#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""体验证据评分修正 —— 把 categories/experience_facts.json 的体验证据应用到 adas/control 两维。

为什么需要（v9.3.0）：
原评分公式里 adas/control 只按硬件配置打（激光雷达/算力/悬架/零百），
"看配置单就能算出来"——用户指出这不合理：智驾要看城区可用性、操控要看实测与口碑。
本模块把有来源、可核验的体验证据纳入这两维，无证据的车保持原分并标注"仅硬件口径"。

纪律（红线）：
- 只调 adas / control 两维，其它维、总分公式、权重一律不碰
- 每维调整封顶 ±max_adjust_per_dimension（默认1.5），来源分级加权
- 无证据不插补；同车型多条证据按方向相加后封顶
- 应用后写入 candidate["experience_adjust"] 审计字段（明细+前后分数），便于回溯

用法：
    from experience_score import apply_experience
    cands = apply_experience(cands)   # 原地加 adjust 字段并改 scores
    python3 experience_score.py --selftest
"""
from __future__ import annotations
import argparse
import json
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "categories", "experience_facts.json")
DIMS = ("adas", "control")


def load_library(path: str | None = None) -> dict:
    p = path or LIB
    if not os.path.exists(p):
        return {"entries": [], "source_weights": {}, "max_adjust_per_dimension": 1.5}
    return json.load(open(p, encoding="utf-8"))


def adjustments(model: str, lib: dict) -> dict:
    """返回 {dim: (adjust值, [证据id...])}；按来源权重加总后封顶。"""
    acc = {d: 0.0 for d in DIMS}
    src = defaultdict(list)
    weights = lib.get("source_weights") or {}
    for e in lib.get("entries") or []:
        if e.get("model") != model or e.get("dimension") not in DIMS:
            continue
        w = float(weights.get(e.get("source_type"), 0.35))
        delta = float(e.get("direction", 0)) * float(e.get("weight", 0)) * w
        acc[e["dimension"]] += delta
        src[e["dimension"]].append({
            "kind": e.get("kind"), "source_type": e.get("source_type"),
            "direction": e.get("direction"), "weight": e.get("weight"),
            "delta": round(delta, 3), "url": e.get("url", ""),
            "date": e.get("date", ""), "claim": e.get("claim", "")[:120],
        })
    cap = float(lib.get("max_adjust_per_dimension", 1.5))
    for d in DIMS:
        acc[d] = max(-cap, min(cap, acc[d]))
    return {d: (round(acc[d], 2), src[d]) for d in DIMS}


def apply_experience(cands: list[dict], lib: dict | None = None,
                     enabled: bool = True) -> list[dict]:
    """把体验证据应用到候选（原地改 scores），并写审计字段。"""
    lib = lib if lib is not None else load_library()
    for c in cands:
        model = c.get("model", "")
        adj = adjustments(model, lib) if enabled else {d: (0.0, []) for d in DIMS}
        scores = c.setdefault("scores", {})
        before = {d: scores.get(d) for d in DIMS}
        detail = {}
        for d in DIMS:
            delta, evs = adj[d]
            old = scores.get(d)
            if isinstance(old, (int, float)) and abs(delta) > 1e-9:
                scores[d] = round(max(0.0, min(10.0, float(old) + delta)), 2)
            detail[d] = {
                "before": old, "after": scores.get(d), "delta": delta,
                "evidence": evs,
                "basis": "硬件+体验证据" if evs else "仅硬件口径（无体验证据）",
            }
        c["experience_adjust"] = {
            "enabled": enabled,
            "before": before,
            "dimensions": detail,
            "n_evidence": sum(len(adj[d][1]) for d in DIMS),
        }
    return cands


def selftest() -> int:
    lib = load_library()
    bad = []
    if len(lib.get("entries") or []) < 5:
        bad.append("证据库条目过少")
    # 结构：必填字段 + 来源分级 + 封顶
    for e in lib["entries"]:
        for k in ("model", "dimension", "source_type", "direction", "claim"):
            if k not in e:
                bad.append(f"证据缺 {k}: {e.get('model')}")
        if e["dimension"] not in DIMS:
            bad.append(f"维度越界: {e['dimension']}")
    # 极氪7X 应下修智驾、上修操控（麋鹿75.2高于同级均值）
    a = adjustments("极氪7X", lib)
    if not (a["adas"][0] < 0 and a["control"][0] > 0):
        bad.append(f"极氪7X 调整方向错误: {a}")
    # 无证据车不动分
    a_none = adjustments("不存在车型XYZ", lib)
    if any(v[0] != 0 for v in a_none.values()):
        bad.append("无证据车被调分")
    # 应用测试：只改 adas/control，其它维与总分不动
    c = [{"model": "极氪7X", "scores": {"adas": 9.0, "control": 8.5,
                                        "safety": 8.7, "value": 8.8}, "total": 88.0}]
    apply_experience(c, lib)
    r = c[0]
    if r["scores"]["adas"] >= 9.0 or r["scores"]["control"] <= 8.5:
        bad.append("apply 未生效")
    if r["scores"]["safety"] != 8.7 or r["scores"]["value"] != 8.8 or r["total"] != 88.0:
        bad.append("apply 越界改了其它维/总分")
    if r["experience_adjust"]["dimensions"]["adas"]["basis"] != "硬件+体验证据":
        bad.append("审计字段 basis 错误")
    if bad:
        print("[selftest FAIL]", bad)
        return 1
    print(f"[selftest OK] 证据库 {len(lib['entries'])} 条 / {len({e['model'] for e in lib['entries']})} 款；"
          "极氪7X 智驾下修操控上修；无证据不动；只改两维")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="体验证据评分修正")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--show", metavar="MODEL", help="显示某车型的调整明细")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(selftest())
    if a.show:
        lib = load_library()
        print(json.dumps(adjustments(a.show, lib), ensure_ascii=False, indent=2))
    else:
        lib = load_library()
        print(f"证据库 {len(lib['entries'])} 条 / 更新 {lib.get('updated')}")
