#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""anchor_pool.py —— 榜内相对锚点的固定快照（v10.16.0）

问题（2026-11-22，用户对比全价位榜与 12-18 万子榜发现）：
  同一款车在两个榜分数不同（小鹏MONA L05 83.8 vs 86.9，差 +3.1）。根因是四个
  「榜内相对锚点」模块各自从**当前候选池**算中位/回归，子池样本一变锚点就漂：
    · evidence_gate     —— 有证据车中位、min_proven=3 门槛、safety 最低锚
    · space_consistency —— 尺寸不小于本车的 space 中位、得房率正常车中位
    · powertrain_efficiency —— 车重~电耗回归斜率与中位残差
    · range_achievement —— 高速达成率榜内中位
  这些是统计锚，不是车的固有属性。子榜换的是「展示哪几款车」，不该换尺子。

方案：从全价位榜产物 summary.json 读取已算好的固定锚点（单一事实源 = 全价位产物，
  不落第二份可写快照），子榜把锚点传给四个模块；模块用固定锚点算调整，只写当前榜
  cands。全价位榜自身不读快照（anchor=None），行为完全不变（历史可比）。

快照结构（从 summary.json 提取）：
  {
    "evidence_cap": {"safety": {"capped":8.0,"anchor":"零跑C11","n":12},
                     "adas": {"capped":8.54,"anchor":"...","n":8}, ...},
    "range_median": 0.499,
    "efficiency": {"slope":0.006572,"intercept":-0.053,"median_residual":-0.063},
    "space_tol": 0.4
  }

契约：
  load(category, override=None) -> dict | None
    · 配置 categories/<category>.json 的 anchor_pool.path 指向全价位 summary.json
    · 无配置/文件缺失/字段不全返回 None（回退旧行为，向后兼容）
  selftest 验证提取正确性与隔离性。

用法：
  python3 -m modules.engine.anchor_pool --selftest
  python3 -m modules.engine.anchor_pool --category car_midsuv_bev
"""
from __future__ import annotations

import argparse
import json
import os
import statistics

import paths


# 证据封顶：各维度 cap 值 + 锚点来源。safety 用 min、其余用 median，与 DIM_POLICY 一致。
_CAP_DIMS = ("safety", "adas", "cabin", "control", "quality")


def extract(summary: dict) -> dict | None:
    """从全价位榜 summary.json 提取固定锚点。字段不全返回 None。"""
    if not isinstance(summary, dict):
        return None
    snap: dict = {"source_n": summary.get("in") if isinstance(summary.get("in"), int) else None,
                  "evidence_cap": {}, "range_median": None,
                  "efficiency": None, "space_tol": None, "space_caps": {},
                  "rule_medians": {}}

    # 证据封顶：dimension_cap(非 safety) + safety_cap
    dcaps = summary.get("dimension_cap") or []
    by_dim: dict[str, list] = {}
    for a in dcaps:
        if isinstance(a, dict) and a.get("dim") in _CAP_DIMS:
            by_dim.setdefault(a["dim"], []).append(a)
    scaps = summary.get("safety_cap") or []
    for a in scaps:
        if isinstance(a, dict) and a.get("dim") == "safety":
            by_dim.setdefault("safety", []).append(a)
    for dim in _CAP_DIMS:
        rows = by_dim.get(dim) or []
        caps = {r.get("capped") for r in rows if isinstance(r.get("capped"), (int, float))}
        if len(caps) == 1:
            cap = caps.pop()
            anchors = {r.get("anchor") for r in rows}
            snap["evidence_cap"][dim] = {
                "capped": cap,
                "anchor": next(iter(anchors)) if len(anchors) == 1 else "全价位榜锚点",
                "n": len(rows),
            }
    if not snap["evidence_cap"]:
        return None

    # 续航达成率中位
    rch = (summary.get("range_achievement") or {}).get("detail") or {}
    meds = {v.get("median") for v in rch.values()
            if isinstance(v, dict) and isinstance(v.get("median"), (int, float))}
    if len(meds) == 1:
        snap["range_median"] = meds.pop()

    # 电耗效率回归：斜率/截距/中位残差（同一次回归全车共享）
    eff = (summary.get("powertrain_efficiency") or {}).get("detail") or {}
    regs, mres = set(), set()
    for v in eff.values():
        if not isinstance(v, dict):
            continue
        r = v.get("regression") or {}
        if isinstance(r.get("slope"), (int, float)) and isinstance(r.get("intercept"), (int, float)):
            regs.add((r["slope"], r["intercept"]))
        if isinstance(v.get("median_residual"), (int, float)):
            mres.add(v["median_residual"])
    if len(regs) == 1 and len(mres) == 1:
        slope, intercept = regs.pop()
        snap["efficiency"] = {"slope": slope, "intercept": intercept,
                              "median_residual": mres.pop()}

    # 空间校准：容忍带 + 全价位榜每款车实际封顶值（按车复用，子榜不重算 peer 中位）
    sp = summary.get("space_calibration") or {}
    if isinstance(sp.get("tol"), (int, float)):
        snap["space_tol"] = sp["tol"]
    for row in (sp.get("capped") or []):
        if not isinstance(row, dict) or not row.get("model"):
            continue
        if isinstance(row.get("to"), (int, float)):
            snap["space_caps"][row["model"]] = {
                "capped": row["to"], "anchor": row.get("anchor"),
                "basis": row.get("basis"), "prev": row.get("prev"),
            }

    # 规则增量逐项中位锚（rule_adjust）：全价位 detail 每项 median 跨车一致，
    # 提取为固定值；子榜不再按本池车重算 → space 等维度同车同分。
    ra = (summary.get("rule_adjust") or {}).get("detail") or {}
    by_item: dict[str, dict[str, set]] = {}
    for _model, dims in ra.items():
        if not isinstance(dims, dict):
            continue
        for dim, info in dims.items():
            if not isinstance(info, dict):
                continue
            for item, iv in (info.get("items") or {}).items():
                med = (iv or {}).get("median")
                if isinstance(med, (int, float)):
                    by_item.setdefault(dim, {}).setdefault(item, set()).add(med)
    for dim, items in by_item.items():
        fixed = {item: vals.pop() for item, vals in items.items() if len(vals) == 1}
        if fixed:
            snap["rule_medians"][dim] = fixed
    return snap


def load(category: str, override: str | None = None) -> dict | None:
    """从配置读取全价位 summary.json 路径，提取固定锚点。"""
    cfg_path = str(paths.cat(f"{category}.json"))
    cfg = {}
    if os.path.exists(cfg_path):
        try:
            cfg = json.load(open(cfg_path, encoding="utf-8")) or {}
        except (OSError, ValueError):
            cfg = {}
    node = (cfg.get("anchor_pool") or {}) if isinstance(cfg, dict) else {}
    rel = override or node.get("path")
    if not rel:
        return None
    p = rel if os.path.isabs(rel) else str(paths.ROOT / rel)
    if not os.path.exists(p):
        return None
    try:
        summary = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return extract(summary)


def selftest() -> int:
    s = load("car_midsuv_bev")
    assert s is not None, "全价位锚点快照加载失败"
    assert "safety" in s["evidence_cap"] and "adas" in s["evidence_cap"], "证据封顶锚缺失"
    assert abs(s["evidence_cap"]["adas"]["capped"] - 8.54) < 1e-9, "adas 锚点值错误"
    assert s["range_median"] is not None and abs(s["range_median"] - 0.499) < 1e-9, "达成率中位错误"
    assert s["efficiency"] and abs(s["efficiency"]["slope"] - 0.006572) < 1e-9, "回归斜率错误"
    assert s["space_tol"] == 0.4, "空间容忍带错误"
    # 缺字段/畸形输入回退
    assert extract({}) is None, "空摘要应回退 None"
    assert extract({"dimension_cap": []}) is None, "无封顶锚应回退 None"
    # 单车型多 capped 值冲突 → 该维不纳入（防脏快照）
    dirty = {"dimension_cap": [{"dim": "adas", "capped": 8.5, "anchor": "a"},
                               {"dim": "adas", "capped": 9.0, "anchor": "b"}]}
    e = extract(dirty)
    assert e is None or "adas" not in e["evidence_cap"], "cap 冲突应剔除该维"
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--category", default="car_midsuv_bev")
    a = ap.parse_args()
    if a.selftest:
        rc = selftest()
        print("[anchor_pool selftest] OK" if rc == 0 else f"[anchor_pool selftest] FAIL({rc})")
        raise SystemExit(rc)
    print(json.dumps(load(a.category), ensure_ascii=False, indent=2))
