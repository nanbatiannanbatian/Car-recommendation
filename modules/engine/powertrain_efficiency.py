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

【v10.15.3 车重归一化根因修复】（用户质疑「电耗高就给三电低分不合理，
  要和电机马力结合，大电池双电机四驱车电耗高是物理必然」）：
  实证：本榜 20 款 电耗~车重 Pearson +0.839（强）、电耗~最大功率仅 +0.205（弱）、
  电耗~零百 −0.097（无关）→ 真正解释电耗的是**车重**，不是马力/电机数。
  用绝对电耗中位法 = 系统性惩罚重车：蔚来ES6 16.1 中 15.30 是车重必然（回归式
  电耗=0.0066×车重kg−0.05），仅残差 +0.80 是自身效率，却被按绝对电耗扣 −0.35；
  反观极氪7X/阿维塔07 这类高功率车实际残差为负（真高效）却被误扣。
  改为：榜内对 (车重→电耗) 做一元线性回归，只按**残差**（实际−车重预测）调整，
  残差>0 比同重车费电→减分，残差<0→加分，榜内残差中位为锚，±MAX_ADJUST 封顶。
  注意：不按马力/功率归一化（相关性弱会引噪音）；不给四驱/双电机直接加分
  （结构化覆盖 0/20、电机多≠操控好、四驱更费电会与电耗扣除逻辑打架）。

纪律（红线）：
  · 只调 scores.powertrain 一维；safety/space/value/adas/control/cabin/quality 一律不碰。
  · 基准 = 本榜「车重→电耗」回归的残差中位（动态，随池变化），
    高于残差中位减分、低于加分，±MAX_ADJUST 封顶；
    无电耗或无车重的车不插补、不调整，只标注。
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


def load_consumption(path: str | None = None, trims_path: str | None = None) -> dict[str, dict]:
    """返回 {canon(车型): {consumption, source_url, note, official}}。

    数据源与优先级（2026-11-21）：
      ① **trims.json 的官方电耗**（汽车之家参数页「百公里耗(kWh/100km)」，
         取评分配置对应值）——官方 CLTC 口径，与 fact_library 同口径，覆盖 369/427 配置；
      ② `categories/vehicle_physics.json` 的人工采集值——兜底。

    口径纪律（沿用 v10.10.0「电耗口径不合并」）：
      两个来源口径**不完全可比**（人工采集取的是参数页首配，可能是增程版/不同配置）。
      因此：①优先并标记 `official=True`；②只在该车没有官方值时兜底，标记 official=False；
      **中位锚点优先只用 official=True 的车**（官方口径自洽），
      官方样本不足 3 款才退回全量混合，并在审计里如实标注用了哪一档。
      绝不把两个口径合成一个数再当事实用。
    """
    p = path or PHYSICS
    out: dict[str, dict] = {}
    # ② 人工采集（兜底）
    if os.path.exists(p):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        for row in d.get("cars") or []:
            v = row.get("consumption")
            if not isinstance(v, (int, float)) or v <= 0:
                continue
            out[_canon_key(row.get("model"))] = {
                "consumption": float(v),
                "source_url": row.get("source_url", ""),
                "note": row.get("note", ""),
                "official": False,
                "origin": "vehicle_physics.json 人工采集",
            }
    # ① 官方电耗（优先，覆盖上面的人工值）
    if trims_path:
        try:
            td = json.load(open(trims_path, encoding="utf-8")) if os.path.exists(trims_path) else {}
        except (OSError, ValueError):
            td = {}
        td_entries = td.get("entries") or []
    else:
        # 2026-11-22：走统一入口（人工库 + 自动库合流）
        from modules.data.trims_store import load as _load
        td_entries = list(_load().values())
    if td_entries:
        for e in td_entries:
            m = e.get("model")
            trims = e.get("trims") or []
            if not m or not trims:
                continue
            ref = e.get("scored_trim_ref")
            pick = next((t for t in trims if ref and t.get("name") == ref), None)
            if pick is None:
                # 无评分配置锚点时取「有电耗且价格最高」的配置（最接近车系主力）
                cand = [t for t in trims
                        if isinstance(t.get("consumption"), (int, float)) and t.get("consumption")]
                pick = max(cand, key=lambda t: t.get("price") or 0) if cand else None
            if not pick:
                continue
            v = pick.get("consumption")
            if not isinstance(v, (int, float)) or v <= 0:
                continue
            out[_canon_key(m)] = {
                "consumption": float(v),
                "source_url": e.get("url", ""),
                "note": f"汽车之家参数页「百公里耗(kWh/100km)」· {pick.get('name')}",
                "official": True,
                "origin": "trims.json 官方电耗",
            }
    return out


def load_weight(path: str | None = None, trims_path: str | None = None) -> dict[str, float]:
    """返回 {canon(车型): 整备质量kg}，与电耗同源优先级（trims 官方优先，physics 兜底）。

    v10.15.3：车重是电耗归一化的分母，必须与电耗取**同一配置口径**，
    否则会出现「电耗取高配、车重取低配」的错配。trims 的 curb_weight_kg 与
    consumption 来自同一配置行，优先；vehicle_physics.json 的 weight_kg 兜底。
    """
    out: dict[str, float] = {}
    p = path or PHYSICS
    if os.path.exists(p):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            d = {}
        for row in d.get("cars") or []:
            w = row.get("weight_kg")
            if isinstance(w, (int, float)) and w > 0:
                out[_canon_key(row.get("model"))] = float(w)
    # trims 优先覆盖（同一配置口径）
    if trims_path:
        try:
            td = json.load(open(trims_path, encoding="utf-8")) if os.path.exists(trims_path) else {}
        except (OSError, ValueError):
            td = {}
        entries = td.get("entries") or []
    else:
        from modules.data.trims_store import load as _load
        entries = list(_load().values())
    for e in entries:
        m, trims = e.get("model"), e.get("trims") or []
        if not m or not trims:
            continue
        ref = e.get("scored_trim_ref")
        pick = next((t for t in trims if ref and t.get("name") == ref), None)
        if pick is None:
            cand = [t for t in trims
                    if isinstance(t.get("curb_weight_kg"), (int, float)) and t.get("curb_weight_kg")]
            pick = max(cand, key=lambda t: t.get("price") or 0) if cand else None
        if not pick:
            continue
        w = pick.get("curb_weight_kg")
        if isinstance(w, (int, float)) and w > 0:
            out[_canon_key(m)] = float(w)
    return out


def _norm_table(table: dict[str, dict]) -> dict[str, dict]:
    """外部传入的表 key 可能未经 canon（如 selftest 直构），统一归一化。"""
    return {_canon_key(k): v for k, v in table.items()}


def median(cands: list[dict], table: dict[str, dict]) -> float | None:
    """榜内电耗中位（锚点基准）。

    口径分层（2026-11-21）：**优先只用官方电耗**（trims.json 的参数页值，CLTC 口径自洽）；
    官方样本 < 3 款才退回全量混合（官方 + 人工采集），并由调用方在审计里标注。
    """
    tbl = _norm_table(table)
    vals, official = [], []
    for c in cands:
        k = _canon_key(c.get("model"))
        if k not in tbl:
            continue
        vals.append(tbl[k]["consumption"])
        if tbl[k].get("official"):
            official.append(tbl[k]["consumption"])
    if not vals:
        return None
    if len(official) >= 3:
        return statistics.median(official)
    return statistics.median(vals)


def adjust_for(consumption: float, mid: float) -> tuple[float, str]:
    """电耗相对中位的线性修正，±MAX_ADJUST 封顶。

    mid±3 kWh/100km 映射到 ±MAX_ADJUST；区间外封顶。
    例：中位 13.9，特斯拉 11.9 → 差 -2.0 → +0.27；深蓝S07 17.4 → 差 +3.5 → -0.4。
    """
    span = 3.0
    delta = mid - consumption          # 电耗低于中位 → 正
    adj = max(-MAX_ADJUST, min(MAX_ADJUST, delta / span * MAX_ADJUST))
    return round(adj, 2), f"电耗 {consumption:g} vs 榜内中位 {mid:g} → {adj:+.2f}"


# ── v10.15.3：车重归一化的电耗效率（残差法） ──────────────────────────
RESIDUAL_SPAN = 2.0   # 残差每偏离榜内中位 2 kWh → ±MAX_ADJUST（沿用 ±0.4 预算）

def regression_slope(weights: list[float], cons: list[float]) -> tuple[float, float]:
    """一元线性回归 电耗 = slope×车重 + intercept。样本<3 或车重无方差则退化。"""
    n = len(weights)
    if n < 3:
        return 0.0, (sum(cons) / n if n else 0.0)
    mw, mc = sum(weights) / n, sum(cons) / n
    den = sum((w - mw) ** 2 for w in weights)
    if den <= 1e-9:                       # 车重全相同 → 无法归一，退回绝对电耗法
        return 0.0, mc
    slope = sum((w - mw) * (c - mc) for w, c in zip(weights, cons)) / den
    return slope, mc - slope * mw


def residual_adjust(residual: float, median_residual: float) -> tuple[float, str]:
    """残差相对榜内中位线性修正，±MAX_ADJUST 封顶。

    residual = 实际电耗 − 车重预测电耗；>0 表示比同重车费电（三电效率真低）。
    """
    delta = median_residual - residual   # 残差低于中位（更省）→ 正
    adj = max(-MAX_ADJUST, min(MAX_ADJUST, delta / RESIDUAL_SPAN * MAX_ADJUST))
    return round(adj, 2), f"车重归一残差 {residual:+.2f} vs 榜内中位 {median_residual:+.2f} → {adj:+.2f}"


def apply_efficiency(cands: list[dict], table: dict[str, dict] | None = None,
                     physics_path: str | None = None,
                     weight_table: dict[str, float] | None = None,
                     fixed: dict | None = None) -> list[dict]:
    """原地调整 cands 的 scores.powertrain，写审计字段。返回 cands。

    v10.15.3：改为车重归一化残差法。仅对同时有电耗+车重的车做榜内回归；
    缺任一项的车不插补、不调整。回归退化（车重全同/样本<3）时退回绝对电耗中位法。

    v10.16.0 fixed：可选全价位固定锚点 {slope, intercept, median_residual}。给定后
      回归参数不再从本池算，子榜与全价位榜共用同一条车重~电耗基准线 → 同车同分；
      车辆自身的残差仍用本车电耗/车重算，不随池变。
    """
    raw = table if table is not None else load_consumption(physics_path)
    tbl = _norm_table(raw)
    wraw = weight_table if weight_table is not None else load_weight(physics_path)
    wtbl = {_canon_key(k): float(v) for k, v in wraw.items() if isinstance(v, (int, float)) and v > 0}

    # 收集同时有电耗+车重的样本做回归
    paired: list[tuple[str, float, float]] = []   # (key, weight, consumption)
    for c in cands:
        key = _canon_key(c.get("model"))
        rec, w = tbl.get(key), wtbl.get(key)
        if rec and w and isinstance(rec.get("consumption"), (int, float)):
            paired.append((key, w, float(rec["consumption"])))

    use_fixed = bool(fixed) and all(isinstance(fixed.get(k), (int, float))
                                    for k in ("slope", "intercept", "median_residual"))
    if use_fixed:
        slope, intercept = float(fixed["slope"]), float(fixed["intercept"])
    else:
        slope, intercept = regression_slope([p[1] for p in paired], [p[2] for p in paired])
    residuals = {key: cons - (slope * w + intercept) for key, w, cons in paired}
    use_residual = slope != 0.0 and bool(residuals)
    med_res = float(fixed["median_residual"]) if use_fixed else (
        statistics.median(residuals.values()) if use_residual else 0.0)
    mid_cons = None   # 退化兜底时再算

    for c in cands:
        key = _canon_key(c.get("model"))
        rec = tbl.get(key)
        w = wtbl.get(key)
        scores = c.setdefault("scores", {})
        before = scores.get(DIM)
        if not rec or not w or not isinstance(before, (int, float)):
            c["powertrain_efficiency"] = {
                "status": "no_data" if (not rec or not w) else "no_score",
                "consumption": (rec or {}).get("consumption"),
                "weight": w,
                "adjust": 0.0, "before": before, "after": before,
                "method": "residual" if use_residual else "absolute",
            }
            continue
        if use_residual and key in residuals:
            adj, why = residual_adjust(residuals[key], med_res)
            method = "residual"
        else:
            if mid_cons is None:
                mid_cons = median(cands, tbl)
            if mid_cons is None:
                adj, why, method = 0.0, "无榜内基准", "none"
            else:
                adj, why = adjust_for(rec["consumption"], mid_cons)
                method = "absolute_fallback"
        after = round(float(before) + adj, 2)
        scores[DIM] = after
        c["powertrain_efficiency"] = {
            "status": "applied" if method != "none" else "no_data",
            "consumption": rec["consumption"],
            "weight": w,
            "adjust": adj,
            "before": before, "after": after, "why": why,
            "method": method,
            "regression": {"slope": round(slope, 6), "intercept": round(intercept, 3)} if use_residual else None,
            "residual": round(residuals[key], 3) if (use_residual and key in residuals) else None,
            "median_residual": round(med_res, 3) if use_residual else None,
            "source_url": rec.get("source_url", ""),
            "origin": rec.get("origin", ""),
            "official": bool(rec.get("official")),
        }
    return cands


def selftest() -> int:
    # v10.15.3：带车重样本，锁死「车重归一，重车不被绝对电耗冤枉」
    phys = {
        "特斯拉ModelY": {"consumption": 11.9, "source_url": "u1", "note": ""},
        "乐道L60": {"consumption": 11.9, "source_url": "u2", "note": ""},
        "小鹏G6": {"consumption": 12.5, "source_url": "u3", "note": ""},
        "极氪7X": {"consumption": 13.9, "source_url": "u4", "note": ""},
        "蔚来ES6": {"consumption": 16.1, "source_url": "u5", "note": ""},
        "深蓝S07": {"consumption": 17.4, "source_url": "u6", "note": ""},
    }
    # 车重：蔚来/极氪是重车(2300)，ModelY/乐道是轻车(1900)
    weights = {
        "特斯拉ModelY": 1921.0, "乐道L60": 1885.0, "小鹏G6": 2030.0,
        "极氪7X": 2280.0, "蔚来ES6": 2336.0, "深蓝S07": 1965.0,
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
    apply_efficiency(cands, table=phys, weight_table=weights)
    by = {c["model"]: c for c in cands}
    # 方法标记为残差法
    assert by["蔚来ES6"]["powertrain_efficiency"]["method"] == "residual", \
        f"应使用车重残差法: {by['蔚来ES6']['powertrain_efficiency']}"
    assert by["蔚来ES6"]["powertrain_efficiency"]["regression"]["slope"] > 0, \
        "回归斜率应为正（车重越大电耗越高）"
    # 核心回归1：蔚来重车绝对电耗最高(16.1)，残差法必须比绝对电耗中位法(−0.35级别)扣得更轻
    #   ——小样本中位受深蓝S07极端低效拉低，这里只验证「不比绝对法更狠」的机制，不锁死绝对值
    nio_adj = by["蔚来ES6"]["powertrain_efficiency"]["adjust"]
    abs_mid = median(cands, phys)
    nio_abs = adjust_for(16.1, abs_mid)[0]
    assert nio_adj >= nio_abs - 1e-9, \
        f"重车残差法不得比绝对电耗法扣更狠: 残差{nio_adj} vs 绝对{nio_abs}"
    # 核心回归2：极氪7X是高功率重车，残差法应不再被冤枉扣分（残差为负→不扣/加分）
    zeekr_adj = by["极氪7X"]["powertrain_efficiency"]["adjust"]
    assert zeekr_adj >= 0, f"极氪7X车重归一后应不再被冤枉扣分，实际 {zeekr_adj}"
    # 核心回归3：同电耗不同车重 → 重车调整 > 轻车调整（重车不被惩罚更重）
    light, heavy = {"consumption": 14.0, "source_url": "", "note": ""}, {"consumption": 14.0, "source_url": "", "note": ""}
    t3 = {"轻车": light, "中车A": {"consumption": 12.0, "source_url": "", "note": ""},
          "重车": heavy, "中车B": {"consumption": 13.0, "source_url": "", "note": ""}}
    w3 = {"轻车": 1900.0, "中车A": 2000.0, "重车": 2300.0, "中车B": 2100.0}
    c3 = [{"model": "轻车", "scores": {DIM: 8.0}}, {"model": "中车A", "scores": {DIM: 8.0}},
          {"model": "重车", "scores": {DIM: 8.0}}, {"model": "中车B", "scores": {DIM: 8.0}}]
    apply_efficiency(c3, table=t3, weight_table=w3)
    b3 = {c["model"]: c for c in c3}
    # 同电耗14.0：轻车(1900)比回归线高很多→费电扣多；重车(2300)符合预期→扣少
    assert b3["重车"]["powertrain_efficiency"]["adjust"] > b3["轻车"]["powertrain_efficiency"]["adjust"], \
        f"同电耗重车扣分应更少: 重{b3['重车']['powertrain_efficiency']['adjust']} vs 轻{b3['轻车']['powertrain_efficiency']['adjust']}"
    # 无数据车不调整
    assert by["无数据车X"]["scores"][DIM] == 8.0, "无数据车不得被调整"
    assert by["无数据车X"]["powertrain_efficiency"]["status"] == "no_data"
    # 封顶校验：残差法同样 ±0.4
    ext = [{"model": "低效X", "scores": {DIM: 7.0}}, {"model": "基准A", "scores": {DIM: 7.0}},
           {"model": "基准B", "scores": {DIM: 7.0}}]
    t2 = {"低效X": {"consumption": 20.0, "source_url": "", "note": ""},
          "基准A": {"consumption": 12.0, "source_url": "", "note": ""},
          "基准B": {"consumption": 13.0, "source_url": "", "note": ""}}
    w2 = {"低效X": 2000.0, "基准A": 2000.0, "基准B": 2000.0}
    apply_efficiency(ext, table=t2, weight_table=w2)
    assert abs(ext[0]["powertrain_efficiency"]["adjust"]) <= 0.4 + 1e-9, "调整必须封顶±0.4"
    # 车重全同 → 回归退化 → 退回绝对电耗法（不崩、不报残差）
    deg = [{"model": "D1", "scores": {DIM: 8.0}}, {"model": "D2", "scores": {DIM: 8.0}}]
    td = {"D1": {"consumption": 11.0, "source_url": "", "note": ""},
          "D2": {"consumption": 14.0, "source_url": "", "note": ""}}
    wd = {"D1": 2000.0, "D2": 2000.0}
    apply_efficiency(deg, table=td, weight_table=wd)
    assert all(c["powertrain_efficiency"]["method"] == "absolute_fallback" for c in deg), \
        "车重无方差应退回绝对电耗法"
    # 只改 powertrain 一维
    c0 = {"model": "特斯拉ModelY", "scores": {DIM: 7.3, "safety": 9.0, "adas": 7.0}}
    apply_efficiency([c0], table=phys, weight_table=weights)
    assert c0["scores"]["safety"] == 9.0 and c0["scores"]["adas"] == 7.0
    print("SELFTEST PASS · 电耗效率车重残差法（重车不冤枉/高效车识别/同电耗重车占优/无数据不动/封顶±0.4/退化兜底/只改三电）")
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
