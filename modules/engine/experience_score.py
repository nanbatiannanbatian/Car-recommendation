#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""体验证据评分修正 —— 把 categories/experience_facts.json 的体验证据应用到 adas/control/cabin/powertrain 四维。

为什么需要（v9.3.0）：
原评分公式里 adas/control 只按硬件配置打（激光雷达/算力/悬架/零百），
"看配置单就能算出来"——用户指出这不合理：智驾要看城区可用性、操控要看实测与口碑。
本模块把有来源、可核验的体验证据纳入这两维，无证据的车保持原分并标注"仅硬件口径"。

纪律（红线）：
- 只调 adas/control/cabin/powertrain 四维，safety/space/value/quality 与总分公式一律不碰
- 每维调整封顶 ±max_adjust_per_dimension（默认1.5），来源分级加权
- 无证据不插补；同车型多条证据按方向相加后封顶
- 小数方向(如0.3)=温和调整，整数±1=明确强弱证据
- 拆车证据只作置信度因子、不打分（单条拆车不可推广到整批次）
- 应用后写入 candidate["experience_adjust"] 审计字段（明细+前后分数），便于回溯

用法：
    from modules.engine.experience_score import apply_experience
    cands = apply_experience(cands)   # 原地加 adjust 字段并改 scores
    python3 experience_score.py --selftest
"""
from __future__ import annotations
import argparse
import json
import os
from collections import defaultdict

import paths

HERE = str(paths.ROOT)
LIB = str(paths.cat("experience_facts.json"))
DIMS = ("adas", "control", "cabin", "powertrain")


def load_library(path: str | None = None) -> dict:
    p = path or LIB
    if not os.path.exists(p):
        return {"entries": [], "source_weights": {}, "max_adjust_per_dimension": 1.5}
    return json.load(open(p, encoding="utf-8"))


def adjustments(model: str, lib: dict) -> dict:
    """返回 {dim: (adjust值, [证据id...])}；按来源权重加总后封顶。

    车名一律走 canon 归一化（2026-11-21 修）：库里写「特斯拉Model Y」，候选池写
    「特斯拉 Model Y」，裸名比对会静默漏掉它那份麋鹿 80.9km/h（五车第一）的实测，
    于是有实测的车等于没实测——与 evidence_gate 同一个坑，两处必须同口径。
    """
    from modules.contracts.model_names import canon
    acc = {d: 0.0 for d in DIMS}
    src = defaultdict(list)
    weights = lib.get("source_weights") or {}
    key = canon(model or "")
    for e in lib.get("entries") or []:
        if canon(e.get("model") or "") != key or e.get("dimension") not in DIMS:
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


def teardown_confidence(model: str) -> dict:
    """拆车证据 → 置信度因子（不打分）。

    遵循 skill 红线：单条拆车视频/个例不打分、不推广整批次；
    但拆车能验证配置表没写的真实结构（全铝悬架/CTB/副车架衬套），
    因此只提高该车型硬件项的置信标记，供审计与展示，不进分数。
    """
    path = str(paths.cat("thirdparty_tests.json"))
    if not os.path.exists(path):
        return {"has_teardown": False, "items": [], "note": "证据库缺失"}
    src = json.load(open(path, encoding="utf-8"))
    hits = [e for e in src.get("entries", [])
            if e.get("model") == model and
            e.get("kind") in ("teardown_chassis", "stiffness", "teardown_body")]
    return {
        "has_teardown": bool(hits),
        "items": [{"kind": e.get("kind"), "source_type": e.get("source_type"),
                   "claim": str(e.get("claim", ""))[:100], "url": e.get("url", "")}
                  for e in hits],
        "note": ("有拆车/刚度实证：结构事实置信提高，但不加分、不推广整批次品控"
                 if hits else "无拆车实证"),
    }


def load_residual_values() -> dict:
    """加载三年保值率库（中国汽车流通协会×精真估，可核验来源）。"""
    path = str(paths.cat("residual_values.json"))
    if not os.path.exists(path):
        return {"values": {}, "pool_median": 48.0, "max_adjust": 1.0, "source_urls": []}
    return json.load(open(path, encoding="utf-8"))


def residual_adjust(model: str, rv: dict | None = None) -> tuple[float, dict | None]:
    """保值率 → value 维调整（相对池内中位，封顶 ±max_adjust）。无数据返回 (0,None)。

    2026-11-22 修根因：车型名走 canon 归一化匹配。此前精确匹配让库名
    「零跑C11 纯电版」与候选池「零跑C11」对不上，C11 的保值率微调从未生效
    （与电芯/口碑同类的「精确匹配漏配」坑）。归一化后两种写法都能命中，
    且不误并不同品牌（canon 保留品牌前缀）。
    """
    rv = rv if rv is not None else load_residual_values()
    from modules.contracts.model_names import canon
    key = canon(model)
    rate = None
    matched_name = None
    for name, val in (rv.get("values") or {}).items():
        if canon(name) == key:
            rate, matched_name = val, name
            break
    if rate is None:
        return 0.0, None
    median = float(rv.get("pool_median", 48.0))
    cap = float(rv.get("max_adjust", 1.0))
    delta = max(-cap, min(cap, (float(rate) - median) / 8.0))
    return round(delta, 2), {
        "kind": "residual_value_3y", "source_type": "association_report",
        "date": "2026-06", "url": (rv.get("source_urls") or [""])[0],
        "claim": f"三年保值率 {rate}%（中国汽车流通协会×精真估，纯电三年中位约{median}%）",
        "delta": round(delta, 2), "rate": rate,
    }


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
        # 保值率 → value 维（复用同一审计字段，页面区块自动显示，无需改渲染）
        rdelta, rev = residual_adjust(model) if enabled else (0.0, None)
        v_old = scores.get("value")
        if isinstance(v_old, (int, float)) and rev and abs(rdelta) > 1e-9:
            scores["value"] = round(max(0.0, min(10.0, float(v_old) + rdelta)), 2)
        if rev:
            detail["value"] = {
                "before": v_old, "after": scores.get("value"), "delta": rdelta,
                "evidence": [rev], "basis": "性价比+持有成本(保值率)",
            }
        c["experience_adjust"] = {
            "enabled": enabled,
            "before": before,
            "dimensions": detail,
            "n_evidence": sum(len(adj[d][1]) for d in DIMS) + (1 if rev else 0),
            "teardown_confidence": teardown_confidence(model),
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
    # 四维扩展 + 拆车置信因子
    c2 = [{"model":"极狐阿尔法S5","scores":{"adas":8.0,"control":8.0,"cabin":8.0,"powertrain":8.0},"total":80}]
    apply_experience(c2, lib)
    if c2[0]["scores"]["cabin"] != 8.0:
        bad.append("无车机证据车被调分")
    tc = teardown_confidence("极狐阿尔法S5")
    if not tc["has_teardown"]:
        bad.append("拆车置信因子未识别")
    if teardown_confidence("不存在XYZ")["has_teardown"]:
        bad.append("无拆车车误报置信")
    if c2[0]["experience_adjust"]["dimensions"]["adas"]["before"] != 8.0:
        bad.append("审计 before 错误")
    # 保值率 → value：Model Y 54.2% 应上调，无数据车不动
    rd, rev = residual_adjust("特斯拉 Model Y")
    if not (rd > 0 and rev and rev.get("rate") == 54.2):
        bad.append(f"保值率调整错误: {rd}")
    if residual_adjust("不存在的车")[0] != 0:
        bad.append("无保值率数据车被调分")
    c3 = [{"model":"特斯拉 Model Y","scores":{"value":6.5,"adas":8.0},"total":80}]
    apply_experience(c3, lib)
    if c3[0]["scores"]["value"] <= 6.5:
        bad.append("保值率未作用到 value 维")
    if c3[0]["experience_adjust"]["dimensions"]["value"]["basis"] != "性价比+持有成本(保值率)":
        bad.append("保值率审计 basis 错误")
    # 数据源注册表完整性
    ds_path = str(paths.cat("data_sources.json"))
    if os.path.exists(ds_path):
        ds = json.load(open(ds_path, encoding="utf-8"))
        statuses = {s["status"] for s in ds["sources"]}
        if "integrated" not in statuses or "legal_risk" not in statuses:
            bad.append("数据源注册表缺关键状态")
        if not ds.get("not_integrated_summary"):
            bad.append("数据源注册表缺未接入清单")
    if bad:
        print("[selftest FAIL]", bad)
        return 1
    print(f"[selftest OK] 证据库 {len(lib['entries'])} 条 / {len({e['model'] for e in lib['entries']})} 款 / "
          f"数据源注册表 {len(ds['sources']) if os.path.exists(ds_path) else 0} 渠道；"
          "四维调整+无证据不动+拆车只作置信")
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


# ---------------------------------------------------------------- 体验维度举证封顶
def cap_unproven(cands: list[dict], lib: dict | None = None,
                 dims=("adas", "cabin", "control")) -> list[dict]:
    """体验三维的证据封顶——**薄包装**，实现在 evidence_gate.DIM_POLICY。

    2026-11-21 二次校准：原来的独立实现有三个缺陷（用户质疑「大众 T6 智驾/操控分低」时查实）：
      ① 车名裸比对，库写「特斯拉Model Y」、池写「特斯拉 Model Y」→ 麋鹿 80.9km/h 失联；
      ② 锚点一律用「有证据车最低分」，等于「没测过只能垫底」；
      ③ 有证据车只有 2 款时也照样封顶，拿噪声当全池天花板。
    现在统一由 evidence_gate 处理（canon 归一化 + 分维度锚点 + 小样本不封顶），
    这里只保留函数名，避免「同一件事两套实现」。
    """
    from modules.engine import evidence_gate as EG
    return EG.apply_all(cands, experience_lib=lib, only=dims)
