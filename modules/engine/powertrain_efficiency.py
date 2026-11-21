#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三电维度——电耗效率修正（v10.6.0，2026-11-21）。

为什么加这一维（用户质疑「特斯拉三电 7.3 太低，但电耗全榜最低、超充网络、质保都强」）：
  现有三电分口径是「补能速度 > 电池容量 > 电芯品牌」，车端硬件参数主导；
  但同样 62.5kWh 跑出全榜最低电耗，本身就是三电系统（电控/热管理/能量回收）
  效率的一手体现——这块此前没有进任何评分维度，导致能效强的车被系统性低估。

数据基础（可核验，覆盖率达标）：
  categories/vehicle_physics.json 的 consumption 字段，来源汽车之家/易车参数页，
  全库 51/67、本榜 18/21（86%）有值——高于 spec_facts 维度准入的 70% 覆盖率线。

纪律（红线）：
  · 只调 scores.powertrain 一维；safety/space/value/adas/control/cabin/quality 一律不碰。
  · 基准 = 本榜电耗中位数（动态，随池变化），低于中位加分、高于中位减分，
    ±MAX_ADJUST 封顶；无电耗数据的车不插补、不调整，只标注。
  · 双向调整（不是只加不减），避免「有数据就加分」奖励营销曝光。
  · 超充网络覆盖、三电质保（8年/16万公里）属补能生态/售后保障，非车端三电性能，
    按 spec_facts 纪律只作证据留痕（写 note），不改分。
  · 写入 candidate["powertrain_efficiency"] 审计字段（前后分数+依据），可回溯。

用法：
    from modules.engine.powertrain_efficiency import apply_efficiency
    cands = apply_efficiency(cands)
    python3 -m modules.engine.powertrain_efficiency --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys

import paths

HERE = str(paths.ROOT)
PHYSICS = str(paths.cat("vehicle_physics.json"))
MAX_ADJUST = 0.4          # 封顶：电耗是全榜统一维度，但权重温和，不主导三电分
DIM = "powertrain"


def _canon_key(s: str) -> str:
    from modules.contracts.model_names import canon
    return canon(s or "")


def load_consumption(path: str | None = None) -> dict[str, dict]:
    """返回 {canon(车型): {consumption, source_url, note}}，只保留有值的。"""
    p = path or PHYSICS
    if not os.path.exists(p):
        return {}
    d = json.load(open(p, encoding="utf-8"))
    out: dict[str, dict] = {}
    for row in d.get("cars") or []:
        v = row.get("consumption")
        if not isinstance(v, (int, float)) or v <= 0:
            continue
        out[_canon_key(row.get("model"))] = {
            "consumption": float(v),
            "source_url": row.get("source_url", ""),
            "note": row.get("note", ""),
        }
    return out


def _norm_table(table: dict[str, dict]) -> dict[str, dict]:
    """外部传入的表 key 可能未经 canon（如 selftest 直构），统一归一化。"""
    return {_canon_key(k): v for k, v in table.items()}


def median(cands: list[dict], table: dict[str, dict]) -> float | None:
    tbl = _norm_table(table)
    vals = [tbl[_canon_key(c.get("model"))]["consumption"]
            for c in cands if _canon_key(c.get("model")) in tbl]
    return statistics.median(vals) if vals else None


def adjust_for(consumption: float, mid: float) -> tuple[float, str]:
    """电耗相对中位的线性修正，±MAX_ADJUST 封顶。

    mid±3 kWh/100km 映射到 ±MAX_ADJUST；区间外封顶。
    例：中位 13.9，特斯拉 11.9 → 差 -2.0 → +0.27；深蓝S07 17.4 → 差 +3.5 → -0.4。
    """
    span = 3.0
    delta = mid - consumption          # 电耗低于中位 → 正
    adj = max(-MAX_ADJUST, min(MAX_ADJUST, delta / span * MAX_ADJUST))
    return round(adj, 2), f"电耗 {consumption:g} vs 榜内中位 {mid:g} → {adj:+.2f}"


def apply_efficiency(cands: list[dict], table: dict[str, dict] | None = None,
                     physics_path: str | None = None) -> list[dict]:
    """原地调整 cands 的 scores.powertrain，写审计字段。返回 cands。"""
    raw = table if table is not None else load_consumption(physics_path)
    tbl = _norm_table(raw)
    mid = median(cands, tbl)
    if mid is None:
        return cands
    for c in cands:
        key = _canon_key(c.get("model"))
        rec = tbl.get(key)
        scores = c.setdefault("scores", {})
        before = scores.get(DIM)
        if not rec or not isinstance(before, (int, float)):
            c["powertrain_efficiency"] = {
                "status": "no_data" if not rec else "no_score",
                "consumption": (rec or {}).get("consumption"),
                "adjust": 0.0, "before": before, "after": before,
            }
            continue
        adj, why = adjust_for(rec["consumption"], mid)
        after = round(float(before) + adj, 2)
        scores[DIM] = after
        c["powertrain_efficiency"] = {
            "status": "applied", "consumption": rec["consumption"],
            "median": round(mid, 2), "adjust": adj,
            "before": before, "after": after, "why": why,
            "source_url": rec.get("source_url", ""),
        }
    return cands


def selftest() -> int:
    phys = {
        "特斯拉ModelY": {"consumption": 11.9, "source_url": "u1", "note": ""},
        "乐道L60": {"consumption": 11.9, "source_url": "u2", "note": ""},
        "小鹏G6": {"consumption": 12.5, "source_url": "u3", "note": ""},
        "极氪7X": {"consumption": 13.9, "source_url": "u4", "note": ""},
        "蔚来ES6": {"consumption": 16.1, "source_url": "u5", "note": ""},
        "深蓝S07": {"consumption": 17.4, "source_url": "u6", "note": ""},
    }
    cands = [
        {"model": "特斯拉 Model Y", "scores": {DIM: 7.3}},
        {"model": "乐道L60", "scores": {DIM: 8.0}},
        {"model": "小鹏G6", "scores": {DIM: 8.4}},
        {"model": "极氪7X", "scores": {DIM: 9.0}},
        {"model": "蔚来ES6", "scores": {DIM: 7.6}},
        {"model": "深蓝S07", "scores": {DIM: 7.2}},
        {"model": "无数据车X", "scores": {DIM: 8.0}},
    ]
    apply_efficiency(cands, table=phys)
    by = {c["model"]: c for c in cands}
    # 中位 = median(11.9,11.9,12.5,13.9,16.1,17.4) = (12.5+13.9)/2 = 13.2
    assert by["特斯拉 Model Y"]["scores"][DIM] > 7.3, "低电耗车应加分"
    assert by["深蓝S07"]["scores"][DIM] < 7.2, "高电耗车应减分"
    assert by["极氪7X"]["scores"][DIM] < 9.0, "高于中位应减分"
    assert by["小鹏G6"]["scores"][DIM] > 8.4, "略低于中位应加分"
    assert by["蔚来ES6"]["scores"][DIM] < 7.6, "高于中位应减分"
    assert by["无数据车X"]["scores"][DIM] == 8.0, "无数据车不得被调整"
    assert by["无数据车X"]["powertrain_efficiency"]["status"] == "no_data"
    # 封顶校验：中位13.0，极端低电耗5.0 差8>span3 → 封顶 +0.4
    ext = [
        {"model": "低电耗X", "scores": {DIM: 7.0}},
        {"model": "基准A", "scores": {DIM: 7.0}},
        {"model": "基准B", "scores": {DIM: 7.0}},
    ]
    t2 = {
        "低电耗X": {"consumption": 5.0, "source_url": "", "note": ""},
        "基准A": {"consumption": 13.0, "source_url": "", "note": ""},
        "基准B": {"consumption": 13.0, "source_url": "", "note": ""},
    }
    apply_efficiency(ext, table=t2)
    assert abs(ext[0]["scores"][DIM] - 7.4) < 1e-9, ext[0]["scores"]
    # 只改 powertrain 一维
    c0 = {"model": "特斯拉ModelY", "scores": {DIM: 7.3, "safety": 9.0, "adas": 7.0}}
    apply_efficiency([c0], table=phys)
    assert c0["scores"]["safety"] == 9.0 and c0["scores"]["adas"] == 7.0
    print("SELFTEST PASS · 电耗效率修正（低电耗加分/高电耗减分/无数据不动/封顶±0.4/只改三电）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    print("本模块为纯函数，供 pipeline 调用；用 --selftest 验证。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
