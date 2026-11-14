#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""燃油 / 混动四榜的可审计代理评分（ice_scoring.py）。

立场先说清楚：这不是纯电四榜那种「逐款人工核验的产品力分」。
本模块把**公开结构化数据**按固定公式映射成 6 维，每条分数都带
`basis`（来源）与 `confidence`（high/medium/low），插补项一律标 low，
并生成 scoring_audit.md 供逐条复核。与纯电榜的加权分不可直接比较。

六维与数据流（缺数据不硬造、不把缺失记 0）：
  market   ← 6 个月销量中位数，全池分位秩映射到 [5.5,10]
  value    ← 0.6·口碑「性价比」+ 0.4·官方指导价相对位置
  space    ← 口碑「空间」
  control  ← 口碑「驾驶感受」
  cabin    ← 口碑「内饰」「外观」均值
  quality  ← 0.6·口碑 overall + 0.4·PPH 反向（仅样本够时）

纪律：
  - 口碑样本 < MIN_USERS 时该维**不做结论**，用同榜中位插补并标 low
    （不是记 0 分，也不是「没数据就不加分」——后者会系统性惩罚冷门车）
  - PPH 样本不足一律作废，不当「零故障」
  - **不设 safety 维**：v6.3 起 L1 官方碰撞测评记录已入库（ice_crash.json），
    但近期证据覆盖率远低于 G2(≥70%)，且 L1 与 L4 不同质，故只作
    **事实列 + 判档证据**：有近期 L1 证据的车可走 L1 路径达到 evidence A，
    但 `scores/total/adjust` 一律不变，前十名次零影响
  - 每条分数可点开回溯到具体来源字段，公式全部写在本文件里
"""
from __future__ import annotations

import json
import math
import os
import statistics

MIN_USERS = 30
HIGH_USERS = 100
SCORE_MIN, SCORE_MAX = 0.0, 10.0
KOU_MIN_USERS = MIN_USERS
CRASH_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "categories", "ice_crash.json")

# 口碑维度 → 本项目维度
DIM_MAP = {
    "value": ["性价比"],
    "space": ["空间"],
    "control": ["驾驶感受"],
    "cabin": ["内饰", "外观"],
    "quality": [],          # 单独用 overall + PPH
}


def clamp(v, lo=SCORE_MIN, hi=SCORE_MAX):
    return max(lo, min(hi, float(v)))


def _kb(c, key):
    """取口碑维度原值（0–5），样本不足返回 None。"""
    if int(c.get("koubei_users") or 0) < KOU_MIN_USERS:
        return None
    d = (c.get("koubei_dims") or {}).get(key)
    return float(d) if isinstance(d, (int, float)) and d > 0 else None


def _kb_avg(c, keys):
    vals = [_kb(c, k) for k in keys]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def load_crash(path=None) -> dict:
    """读 L1 碰撞证据事实库；文件不存在时静默降级（保持向后兼容）。"""
    p = path or CRASH_PATH
    if not os.path.exists(p):
        return {}
    try:
        return (json.load(open(p, encoding="utf-8")) or {}).get("evidence") or {}
    except Exception:                                         # noqa: BLE001
        return {}


def _evidence(c):
    """证据等级：L1 近期官方碰撞证据 与 L4 口碑样本量，两条并行路径。

    v6.3 之前 A 只有「口碑样本 ≥100」一条路，而燃油/混动池里几乎没有车达到，
    导致四榜第一档必然全空——那是把纯电榜阈值直接搬过来的配置错误，
    不是「无官方碰撞证据」的必然结论。现按证据栈纪律补上 L1 路径：
    L1 官方测评（近期）比 L4 自选择口碑更硬，理应同样够 A。
    **只影响判档，不碰任何分数。**
    """
    u = int(c.get("koubei_users") or 0)
    ok_meta = bool(c.get("price_guide")) and c.get("cls") not in ("", "UNKNOWN", None)
    if u >= HIGH_USERS and ok_meta:
        c["_evidence_basis"] = "L4_koubei>=100"
        return "A"
    if c.get("_crash") and ok_meta:
        c["_evidence_basis"] = "L1_recent_official_crash"
        return "A"
    if u >= MIN_USERS:
        c["_evidence_basis"] = "L4_koubei>=30"
        return "B"
    c["_evidence_basis"] = "L4_koubei<30"
    return "C"


def score_pool(pool: list[dict], cfg: dict, crash_db: dict | None = None) -> tuple[list[dict], dict]:
    """给候选池写 scores / score_basis / score_confidence；返回 (cands, audit)。"""
    dims = [d["key"] for d in cfg["dimensions"]]
    audit = {"rows": [], "dims": dims, "MIN_USERS": MIN_USERS,
             "coverage": {}, "imputed": {}, "rules": []}

    # ---- L1 碰撞证据：只写事实字段，绝不参与下面任何分数计算 ----
    crash = load_crash() if crash_db is None else crash_db
    for c in pool:
        e = crash.get(c["model"]) or {}
        c["_crash"] = e if e.get("l1_current") else None
        c["_crash_any"] = e if e.get("has_l1") else None
    audit["crash"] = {
        "db_entries": len(crash),
        "l1_current_in_pool": sum(1 for c in pool if c["_crash"]),
        "l1_any_in_pool": sum(1 for c in pool if c["_crash_any"]),
        "scoring_effect": "none（只影响 evidence_status → 三档；scores/total/adjust 不变）",
    }

    # ---- 分位秩：销量与指导价都是长尾分布，min-max 会把绝大多数车挤到同一端 ----
    # 实测 122 款：趋势销量 min 358 / p05 630 / 中位 2,803 / p95 12,557 / max 14,592；
    # log1p min-max 会让前 20 名全部落在 9.9–10.0，24% 权重实际失效。
    # 改用全池分位秩（并列按序），把市场验证摊到 [5.5,10]，保留相对次序、
    # 不被单一爆款拉偏；绝对销量仍原样展示在表里。
    ordered = sorted(pool, key=lambda c: int(c.get("trend_sales") or 0))
    n = len(ordered)
    sales_pct = {id(c): (i / (n - 1) if n > 1 else 0.5)
                 for i, c in enumerate(ordered)}

    priced = sorted([c for c in pool
                     if c.get("price_guide") and c["price_guide"][0] is not None],
                    key=lambda c: float(c["price_guide"][0]))
    m = len(priced)
    price_pct = {id(c): (i / (m - 1) if m > 1 else 0.5)
                 for i, c in enumerate(priced)}

    for c in pool:
        sc, basis, conf = {}, {}, {}
        # market：Always 有值（销量来自结构化榜单）
        sc["market"] = round(5.5 + 4.5 * sales_pct[id(c)], 1)
        basis["market"] = (f"6 个月销量中位数 {int(c.get('trend_sales') or 0):,}；"
                           f"全池分位秩 {sales_pct[id(c)]:.2f} → 5.5+4.5×秩 "
                           "（长尾分布不用 min-max）")
        conf["market"] = "high" if int(c.get("trend_sales") or 0) >= 2000 else "medium"

        # 口碑映射维
        for key in ("value", "space", "control", "cabin"):
            raw = _kb_avg(c, DIM_MAP[key])
            if raw is not None:
                sc[key] = round(clamp(raw * 2), 1)
                basis[key] = f"汽车之家口碑「{'/'.join(DIM_MAP[key])}」{raw:.2f}/5 ×2（样本 {c['koubei_users']}）"
                conf[key] = "high" if int(c.get("koubei_users") or 0) >= HIGH_USERS else "medium"
            else:
                sc[key] = None
                basis[key] = f"口碑样本 {c.get('koubei_users') or 0} < {MIN_USERS}，该维不做结论"
                conf[key] = "low"

        # value 叠加价格因子（有口碑时 0.6 + 0.4，无口碑只用价格）
        pf = (6.0 + 4.0 * (1.0 - price_pct[id(c)])) if id(c) in price_pct else None
        if sc["value"] is not None and pf is not None:
            sc["value"] = round(0.6 * sc["value"] + 0.4 * pf, 1)
            basis["value"] += f"；×0.6 + 价格因子 {pf:.2f} ×0.4（指导价 {c['price_guide'][0]:g} 万）"
        elif pf is not None:
            sc["value"] = round(pf, 1)
            basis["value"] = (f"口碑样本不足，仅用官方指导价 {c['price_guide'][0]:g} 万的价格因子 "
                              f"{pf:.2f}（全池相对位置）")
            conf["value"] = "low"

        # quality：overall + PPH
        u = int(c.get("koubei_users") or 0)
        overall = float(c.get("koubei_overall") or 0) or None
        pph = c.get("pph") if c.get("pph_usable") else None
        if u >= MIN_USERS and overall:
            q = clamp(overall * 2)
            if pph is not None:
                pph_score = clamp(10.0 - float(pph) * 0.05)
                q = 0.6 * q + 0.4 * pph_score
                basis["quality"] = (f"口碑 overall {overall:.2f}/5 ×2 ×0.6 + "
                                    f"PPH {pph:g} 反向 {pph_score:.2f} ×0.4（样本 {u}）")
            else:
                basis["quality"] = f"口碑 overall {overall:.2f}/5 ×2（样本 {u}；PPH 样本不足已作废）"
            sc["quality"] = round(q, 1)
            conf["quality"] = "high" if u >= HIGH_USERS else "medium"
        else:
            sc["quality"] = None
            basis["quality"] = f"口碑样本 {u} < {MIN_USERS}，该维不做结论"
            conf["quality"] = "low"

        # 先留空，下一轮按同榜中位统一插补（不记 0 分）
        c["_sc"] = sc
        c["_basis"] = basis
        c["_conf"] = conf

    # 第二轮：按同榜（cls）中位插补，同榜不足 3 个则用全池中位
    for k in dims:
        by_cls = {}
        for c in pool:
            v = c["_sc"].get(k)
            if v is not None:
                by_cls.setdefault(c.get("cls"), []).append(v)
        allv = [v for vs in by_cls.values() for v in vs]
        pool_med = round(statistics.median(allv), 1) if allv else 7.0
        n_imp = 0
        for c in pool:
            if c["_sc"].get(k) is not None:
                continue
            peers = by_cls.get(c.get("cls")) or []
            fill = round(statistics.median(peers), 1) if len(peers) >= 3 else pool_med
            c["_sc"][k] = fill
            c["_conf"][k] = "low"
            c["_basis"][k] += (f"；按同榜中位 {fill:g} 插补（同榜有值 {len(peers)} 款）"
                               if len(peers) >= 3 else
                               f"；同榜有值不足 3 款，按全池中位 {fill:g} 插补")
            n_imp += 1
        audit["coverage"][k] = len(pool) - n_imp
        audit["imputed"][k] = n_imp

    # 第三轮：写成引擎字段
    cands = []
    for c in pool:
        pg = c.get("price_guide")
        price_txt = (f"{pg[0]:g}–{pg[1]:g}" if pg and pg[0] is not None
                     and pg[1] is not None else "—")
        ev = _evidence(c)
        notes = []
        if ev == "C":
            notes.append(f"口碑样本 {c.get('koubei_users') or 0} 条，多数维度为插补值")
        if not pg:
            notes.append("未取得官方指导价")
        if c["_crash"]:
            notes.append("有近期 L1 官方碰撞测评记录，证据等级 A 由 L1 路径取得；"
                         "该记录只作事实与判档，不换算分数")
        elif c["_crash_any"]:
            notes.append("仅有历史年款碰撞测评记录，不外推到现款，不作判档证据")
        rec = {
            "model": c["model"], "cls": c["cls"],
            "power": c["power"], "ne": c["ne"],
            "price": price_txt, "price_guide": pg,
            "trend_sales": c["trend_sales"], "sales_oct": c["sales_oct"],
            "sales_scope": "classified_series", "trend_method": c.get("trend_method"),
            "scores": {k: c["_sc"][k] for k in dims},
            "score_basis": {k: c["_basis"][k] for k in dims},
            "score_confidence": {k: c["_conf"][k] for k in dims},
            "evidence_status": ev,
            "evidence_basis": c.get("_evidence_basis"),
            "crash_evidence": c.get("_crash_any"),
            "note": "；".join(notes),
            "lifecycle": {"status": "active"},
            "ah_series_id": c.get("ah_series_id"), "ah_name": c.get("ah_name"),
            "name_match": c.get("name_match"), "energytype": c.get("energytype"),
            "fct_name": c.get("fct_name"), "cls_source": c.get("cls_source"),
            "price_source": c.get("price_source"),
            "koubei_users": c.get("koubei_users"), "koubei_overall": c.get("koubei_overall"),
            "dcd_series_id": c.get("dcd_series_id"),
            "adjust": 0,
        }
        cands.append(rec)
        audit["rows"].append({
            "model": c["model"], "cls": c["cls"], "power": c["power"],
            "scores": rec["scores"], "confidence": rec["score_confidence"],
            "basis": rec["score_basis"],
            "low_dims": [k for k in dims if rec["score_confidence"][k] == "low"],
        })
    n_adas = sum(1 for c in pool
                 if (c.get("koubei_dims") or {}).get("智能化")
                 and int(c.get("koubei_users") or 0) >= MIN_USERS)
    adas_pct = 100.0 * n_adas / max(1, len(pool))
    audit["adas_coverage"] = {"n": n_adas, "total": len(pool),
                              "pct": round(adas_pct, 1)}
    audit["rules"] = [
        "market = 5.5 + 4.5·(6月销量中位数的全池分位秩)；长尾分布不用 min-max，避免前 20 名挤成 9.9",
        "口碑维 = 口碑分(0–5)×2，样本 <30 不采信",
        "value = 0.6·口碑性价比×2 + 0.4·价格因子；价格因子 = 6 + 4·(1−指导价分位秩)；口碑不足时只用价格因子并标 low",
        "cabin = 口碑(内饰+外观)/2 ×2",
        "quality = 0.6·口碑 overall×2 + 0.4·(10 − 0.05·PPH)，PPH 样本 <30 作废",
        "缺失维 = 同榜中位插补（同榜有值 <3 款则全池中位），一律标 low，不记 0 分",
        f"adas 维整条禁用：口碑「智能化」覆盖率仅 {adas_pct:.1f}%"
        f"（{n_adas}/{len(pool)}），按维度级准入门槛 G2 不留失效权重；权重在剩余 6 维重归一化",
        "不设 safety 评分维：v6.3 的 L1 碰撞证据覆盖率远低于 G2(≥70%)，且 L1 与 L4 不同质；"
        "只作事实列与判档证据，scores/total/adjust 一律不变",
        "evidence A 有两条并行路径：L4 口碑样本 ≥100（且级别+指导价齐备），"
        "或 L1 近期官方碰撞测评记录；B = 口碑 30–99，C = <30。"
        "本榜 A/B/C 与纯电榜的 A/B/C 不同义，第一档也不等于碰撞安全已验证",
    ]
    return cands, audit
