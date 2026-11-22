#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""指导价 vs 官方优惠价 双口径对照（price_dual_view.py）——纯展示+敏感性，不改主分。

背景（2026-11-22 用户要求）：
  用户问「有官方价格，很多车型有官方优惠，是否要算官方优惠，用指导价还是优惠价好」。
  结论（用户拍板「双口径对照+敏感性」）：指导价继续做主分口径（可复算、跨榜同分、
  不随时间漂移），优惠价只做对照展示，让用户看到「被指导价高估的性价比」。

为什么优惠价不进主分（根因，非权宜）：
  ① 高频变动：优惠有截止日（10/31、11/15、11/30、12/15…），按月换政策；
  ② 口径混杂：金融贴息/保险补贴/置换/选装基金/BaaS 非直接车价，无法可比；
  ③ 覆盖不全：15/20 有优惠但仅 6 款有效直接现金价，其余过期或非现金权益；
  ④ 破坏可复算：今天和明天算出的分会不同，跨榜锚点、回归全失效。

实现：对每款有直接现金优惠价的车，用 value_residual 的同一套纯函数
  （product_strength / delta_for / value_for）把价格换成优惠价重算 value，
  得到「优惠后 value」；与指导价 value 对比。只读 scores，绝不写回。

用法：
  from modules.engine import price_dual_view as PDV
  report = PDV.build(cands, weights, fixed_bins=anchor.get("value_bins"))
  # report["rows"] = [{model, guide_price, promo_price, value_guide, value_promo, delta, ...}]
"""
from __future__ import annotations

import json
import os

import paths

from modules.engine import value_residual as _VR

HERE = str(paths.ROOT)
LIB = str(paths.cat("official_promotions.json"))


def load_library(path: str | None = None) -> dict:
    p = path or LIB
    if not os.path.exists(p):
        return {}
    return json.load(open(p, encoding="utf-8"))


def _key(model: str) -> str:
    from modules.contracts.model_names import canon
    return canon(model)


def promo_for(model: str, lib: dict | None = None) -> dict | None:
    lib = lib if lib is not None else load_library()
    for m, rec in (lib.get("direct_price_promo") or {}).items():
        if _key(m) == _key(model):
            return rec
    return None


def _price_of(cand: dict) -> float | None:
    p = cand.get("price_guide")
    if isinstance(p, (list, tuple)) and p:
        return float(p[0])
    if isinstance(p, (int, float)):
        return float(p)
    return None


def build(cands: list[dict], weights: dict | None = None,
          fixed_bins: dict | None = None, lib: dict | None = None) -> dict:
    """生成双口径对照报告。只读 scores，不写回任何分数/字段。"""
    lib = lib if lib is not None else load_library()
    # 复用全榜价格档锚点（子榜从快照取，保证与指导价主分同一把尺子）
    fit = _VR.fit_bins(cands, weights)
    bins = dict(fit["bins"]) if not fixed_bins else dict(fixed_bins.get("bins") or {})
    pool_mean = fit["pool_mean"] if not fixed_bins else fixed_bins.get("pool_mean")

    rows = []
    for c in cands:
        model = c.get("model", "")
        promo = promo_for(model, lib)
        if not promo:
            continue
        # 指导价基准优先用官方库 guide_price：候选池 price_guide 对部分车存的已是
        # 限时价（如 MONA L05 候选池 13.98 实为限时价、厂商指导价 14.48），
        # 直接拿它当指导价会让 guide==promo、对照失真。回退才用候选池。
        guide = promo.get("guide_price") if isinstance(promo.get("guide_price"), (int, float)) \
            and promo.get("guide_price") > 0 else _price_of(c)
        promo_price = promo.get("effective_price")
        # 状态过滤：needs_value / 无有效数值 不参与数值对照（只列展示）
        if guide is None or not isinstance(promo_price, (int, float)) or promo_price <= 0:
            rows.append({"model": model, "status": promo.get("status"),
                         "guide_price": guide, "promo_price": promo_price,
                         "type": promo.get("type"), "valid_until": promo.get("valid_until"),
                         "note": promo.get("detail"), "source_url": promo.get("source_url"),
                         "value_guide": None, "value_promo": None, "delta": None})
            continue
        pf = _VR.product_strength(c, weights)
        if pf is None:
            continue
        b = _VR.price_bin(guide)
        anchor = bins.get(b) or pool_mean
        if not anchor or anchor <= 0:
            continue
        # 保值率微调与主分一致（同一车同一 rdelta，抵消掉，只看价格差的影响）
        from modules.engine import experience_score as ES
        rdelta, _ = ES.residual_adjust(model)
        ppw_guide = pf / guide
        ppw_promo = pf / promo_price
        v_guide = _VR.value_for(ppw_guide, anchor, rdelta)
        v_promo = _VR.value_for(ppw_promo, anchor, rdelta)
        # 主分实际值（可能已被锚点/规则改过）——以候选池 scores.value 为准做对照
        score_guide = (c.get("scores") or {}).get("value")
        rows.append({
            "model": model, "status": promo.get("status"), "bin": b,
            "guide_price": guide, "promo_price": promo_price,
            "amount_wan": promo.get("amount_wan"), "type": promo.get("type"),
            "valid_until": promo.get("valid_until"),
            "value_guide": v_guide, "value_promo": v_promo,
            "delta": round(v_promo - v_guide, 2) if (v_promo is not None and v_guide is not None) else None,
            "score_guide": score_guide,
            "note": promo.get("detail"), "source_url": promo.get("source_url"),
        })

    numeric = [r for r in rows if r.get("delta") is not None]
    active = [r for r in rows if r.get("status") == "active"]
    return {
        "status": "applied" if rows else "skipped",
        "n": len(cands), "with_promo": len(rows),
        "active": len(active), "numeric_compare": len(numeric),
        "price_basis": lib.get("price_basis"),
        "method": "指导价主分不变；用同一价格档锚点+价值残差公式，把价格换成官方直接现金优惠价重算 value 作对照；金融/置换/选装/BaaS 非车价权益不折算",
        "rows": rows,
        "summary": {
            "uplift_count": sum(1 for r in numeric if (r.get("delta") or 0) > 0),
            "max_uplift": max((r.get("delta") or 0) for r in numeric) if numeric else 0,
            "avg_uplift": round(sum((r.get("delta") or 0) for r in numeric) / len(numeric), 2) if numeric else 0,
            "expired_count": sum(1 for r in rows if r.get("status") == "expired"),
            "key_insight": lib.get("summary", {}).get("key_insight"),
        },
    }


def summarize(report: dict) -> dict:
    out = {k: report.get(k) for k in ("status", "n", "with_promo", "active",
                                      "numeric_compare", "price_basis", "method", "summary")}
    out["rows"] = report.get("rows") or []   # 供页面双口径展示区块读取（只读展示）
    return out


def selftest() -> int:
    lib = load_library()
    assert promo_for("特斯拉 Model Y", lib)["amount_wan"] == 0.4, "Model Y 优惠匹配"
    assert promo_for("特斯拉Model Y", lib)["amount_wan"] == 0.4, "无空格写法也应匹配"
    assert promo_for("丰田bZ5", lib)["status"] == "needs_value", "bZ5 无金额应标 needs_value"
    assert promo_for("腾势N7", lib) is None, "腾势N7 无直接现金优惠"
    # 只读验证：build 不改 scores
    w = {"powertrain": 0.25, "safety": 0.177, "adas": 0.08, "space": 0.131,
         "control": 0.075, "cabin": 0.07, "quality": 0.095, "value": 0.122}
    cands = [
        {"model": "特斯拉 Model Y", "price_guide": [26.35],
         "scores": {"powertrain": 7.3, "safety": 8.5, "adas": 7.0, "space": 8.0,
                    "control": 8.2, "cabin": 8.0, "quality": 8.2, "value": 7.0}},
        {"model": "小鹏G6", "price_guide": [17.68],
         "scores": {"powertrain": 7.0, "safety": 8.0, "adas": 7.0, "space": 7.5,
                    "control": 7.5, "cabin": 7.5, "quality": 7.5, "value": 7.0}},
    ]
    before = [dict(c["scores"]) for c in cands]
    # 传固定锚点，避免 2 车玩具数据每档<2 款回退 pool_mean 导致 delta 被 round 抹平
    snap = {"bins": {">25万": 0.30, "15-20万": 0.40}, "pool_mean": 0.35}
    rep = build(cands, w, fixed_bins=snap, lib=lib)
    assert [c["scores"] for c in cands] == before, "双口径对照不得修改 scores"
    tesla = next(r for r in rep["rows"] if "Model" in r["model"])
    assert tesla["promo_price"] == 25.95 and tesla["delta"] is not None, "Model Y 对照应有数值"
    assert tesla["delta"] > 0, "优惠价更低 → 性价比分应上升"
    # 指导价基准应取官方库 guide_price(26.35)，不是候选池 price_guide
    assert tesla["guide_price"] == 26.35
    return 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        rc = selftest()
        print("[price_dual_view selftest] OK" if rc == 0 else "FAIL")
        raise SystemExit(rc)
