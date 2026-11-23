#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""value_residual.py —— 性价比（value）价格残差法（v10.17.0）

问题（2026-11-22，用户问「性价比这一列怎么算、是否合适、业界怎么做」）：
  旧 value 分是人工裁决分（候选池 scores.value 人工填），依据主要是 kWh/万元 +
  价格印象；代码里的 spec_score.score_value 同榜比价公式是死代码，没接主链
  （score_audit 自己标 wired:partial）。三个硬伤：
    ① 名实不符：叫「性价比」，实为「电池密度」，把产品力窄化成 kWh；
    ② 不可复算：人工分没法用公式回归验证；
    ③ 贵=低分的粗糙判断无法解释「差价到底买没买到额外产品力」。

方法（用户确认参考 J.D. Power Ratings Methodology）：
  J.D. Power 不设单一「性价比」维度，而是先按价格档 + 尺寸分组，再取「车型属性
  与同级组均值的距离」，由质量可靠/驾驶体验/保值率/经销商体验合成。本榜尺寸已固定
  （中型纯电 SUV），故只按价格档分组；经销商体验全缺、保值率仅 3/20 款有数据，
  不作合成因子，保值率仅作有数据车的既有微调（experience.residual_adjust）。

  v10.47 定性为「质价比」：每万元买到的产品力，全池一把尺子、不分价位档。
  用户指出分档=不同车不同标准，且本池实测 pf 与价格无关（≤17万 pf=8.17 /
  >22万 pf=8.03；Hedonic 回归系数 b=-0.0118≈0，残差法退化为纯产品力排名、失效），
  故放弃分档与 Hedonic，改用全池统一锚。

  落地为：
    · 产品力 pf = 其余七维（剔除 value 自身，避免重复计分）按权重归一的加权分；
    · 每万元产品力 ppw = pf / 评分配置价（v10.45 根因修复：旧版用 price_guide[0]
      区间最低价，与产品力的评分配置口径错配，Model Y 26.35后驱价配28.85长续航
      产品力 → value 虚高 9.54，note 却写"性价比三车最低"）；
    · value = NEUTRAL + gain × (ppw / 全池 ppw 均值 − 1)，clamp 到 ±cap；
      基准 8.0 中性，高者加分、低者扣分。
  即：全池统一标准——单位价格买到的产品力高于全池均值 → 加分，低于 → 扣分。
  因 pf 几乎不随价格涨，质价比天然偏向低价车（便宜车买到的每万元产品力更高），
  这是口径的必然结果、不是 bug，页面与报告必须如实向用户写明。

跨榜一致：全池 ppw 均值属榜内统计锚，子榜必须从全价位榜固定快照取（anchor_pool
  的 value_bins.pool_mean），否则子池均值漂移会让同车 value 变分。全榜自身本池现算。

契约：
  product_strength(cand, weights=None) -> float | None
  fit_bins(cands, weights=None) -> {"bins": {label: ppw_mean}, "detail": [...]}
  apply(cands, weights=None, fixed_bins=None) -> report
  selftest() -> int
"""
from __future__ import annotations

import argparse
import json
import os
import statistics

import paths

HERE = str(paths.ROOT)

# 七维产品力（剔除 value 自身）：与 categories/<category>.json 的 dimensions 同源，
# 缺省值仅用于 selftest / 外部未传权重时；pipeline 必须传 cfg 权重。
PROD_DIMS = ("powertrain", "safety", "adas", "space", "control", "cabin", "quality")
DEFAULT_W = {"powertrain": 0.25, "safety": 0.177, "adas": 0.08, "space": 0.131,
             "control": 0.075, "cabin": 0.07, "quality": 0.095}

# 固定价格档边界（万元）——写死为模块常量，跨榜一致，不随子池漂移。
BIN_EDGES = (15.0, 20.0, 25.0)
BIN_LABELS = ("≤15万", "15-20万", "20-25万", ">25万")

NEUTRAL = 8.0   # 与 J.D. Power「同级均值=80分」对齐（10 分制 8.0）
GAIN = 12.0     # ppw 相对档均偏离 10% → ±1.2 分
CAP = 1.5       # 单维调整封顶，与 experience 单维 ±1.5 一致
MIN_PEERS = 2   # 档内少于 2 款不设该档均值，回退全池均值


def _weights(weights: dict | None) -> dict:
    w = weights or DEFAULT_W
    return {d: float(w.get(d, 0)) for d in PROD_DIMS if float(w.get(d, 0)) > 0}


def price_bin(price: float | None) -> str | None:
    if price is None or not isinstance(price, (int, float)) or price <= 0:
        return None
    for edge, label in zip(BIN_EDGES, BIN_LABELS[:3]):
        if price < edge:
            return label
    return BIN_LABELS[3]


def product_strength(cand: dict, weights: dict | None = None) -> float | None:
    """七维产品力（剔除 value 自身）按权重归一加权；缺任一维返回 None 不插补。"""
    w = _weights(weights)
    # 优先用入池时的原始分快照（pipeline 写入），避免中途规则增量/联网回填让 pf 漂移；
    # 无快照时回退当前 scores（selftest / 外部直调）。
    s = cand.get("_value_pf_base") or cand.get("scores") or {}
    vals = {d: s.get(d) for d in w}
    if any(not isinstance(v, (int, float)) for v in vals.values()):
        return None
    tw = sum(w.values())
    return round(sum(float(vals[d]) * w[d] for d in w) / tw, 6)


def _ppw(cand: dict, weights: dict | None = None,
         price_map: dict | None = None) -> tuple[str | None, float | None, float | None]:
    """每万元产品力。价格口径 v10.45 根因修复：

    旧版取 price_guide[0]（区间最低价），但产品力 pf 反映主榜评分配置水平
    （如 Model Y 主榜用 28.85 长续航，最低价却是 26.35 后驱）→ 分子分母不同配置，
    ppw 虚高把 Model Y 的 value 抬到 9.54（note 却写"性价比三车最低"）。
    现优先用 price_map[model]（pipeline 从 trim_annotate 的评分配置价传入），
    与产品力同一配置；无映射时回退 price_guide[0]（兼容 selftest/外部直调）。
    """
    score_price = (price_map or {}).get(cand.get("model"))
    price = (float(score_price) if isinstance(score_price, (int, float)) and score_price > 0 else None)
    if price is None:
        pg = cand.get("price_guide")
        price = float(pg[0]) if isinstance(pg, (list, tuple)) and pg else (
            float(pg) if isinstance(pg, (int, float)) else None)
    pf = product_strength(cand, weights)
    if pf is None or price is None or price <= 0:
        return None, pf, None
    return price_bin(price), pf, round(pf / price, 6)


def fit_bins(cands: list[dict], weights: dict | None = None,
             price_map: dict | None = None) -> dict:
    """从本池拟合各价格档 ppw 均值（全价位榜用；子榜改用 fixed_bins 快照）。"""
    by_bin: dict[str, list[float]] = {}
    detail = []
    for c in cands:
        b, pf, ppw = _ppw(c, weights, price_map)
        if b and ppw is not None:
            by_bin.setdefault(b, []).append(ppw)
        detail.append({"model": c.get("model"), "bin": b, "pf": pf, "ppw": ppw})
    bins = {}
    for label in BIN_LABELS:
        vals = by_bin.get(label) or []
        if len(vals) >= MIN_PEERS:
            bins[label] = round(statistics.mean(vals), 6)
    all_ppw = [d["ppw"] for d in detail if d["ppw"] is not None]
    return {"bins": bins, "pool_mean": round(statistics.mean(all_ppw), 6) if all_ppw else None,
            "detail": detail}


def apply(cands: list[dict], weights: dict | None = None,
          fixed_bins: dict | None = None, price_map: dict | None = None) -> dict:
    """原地覆盖 scores.value 为价格残差分；返回审计 report。

    fixed_bins: 全价位榜快照 {"bins": {...}, "pool_mean": float}，给定时跳过本池拟合
      （子榜用），保证同车 value 跨榜一致。
    price_map: {model: 评分配置价}（v10.45 根因修复），让 ppw 分母与产品力同配置；
      pipeline 从 trim_annotate.best_trim 传入，子榜用锚点快照时也要传同一份。
    """
    fit = fit_bins(cands, weights, price_map)
    bins = dict(fit["bins"]) if not fixed_bins else dict(fixed_bins.get("bins") or {})
    pool_mean = fit["pool_mean"] if not fixed_bins else fixed_bins.get("pool_mean")
    applied = []
    for c in cands:
        b, pf, ppw = _ppw(c, weights, price_map)
        if b is None or ppw is None:
            continue
        # v10.47 根因修复：统一质价比——全池一个锚（pool_mean），不再按价位档分档。
        # 理由：本池七维产品力 pf 与价格几乎无关（实测 ≤17万 pf=8.17 / >22万 pf=8.03，
        # Hedonic 回归系数 b=-0.0118≈0），分档只会让"贵车档均值低→贵车被抬分"，
        # 且同一辆车落在不同档 get 不同 value = 不同标准。统一锚 = 每万元产品力
        # 对全池均值的距离，全池一把尺子，Model Y(28.85万, ppw0.272)→6.5 触底。
        anchor = pool_mean
        if not anchor or anchor <= 0:
            continue
        scores = c.setdefault("scores", {})
        before = scores.get("value")
        # 保值率仍作有数据车的既有微调（J.D. Power resale 因子，覆盖不足照实披露）。
        # 直接调单一真相源 residual_values.json，不读 experience 运行时字段，避免顺序耦合。
        from modules.engine import experience_score as ES
        rdelta, rev = ES.residual_adjust(c.get("model", ""))
        delta = delta_for(ppw, anchor)
        final = value_for(ppw, anchor, rdelta)
        scores["value"] = final
        _used_price = (price_map or {}).get(c.get("model")) or (
            c.get("price_guide")[0] if isinstance(c.get("price_guide"), (list, tuple))
            and c.get("price_guide") else c.get("price_guide"))
        c["value_residual"] = {
            "status": "applied", "bin": b, "pf": round(pf, 3), "ppw": ppw,
            "price_used": _used_price, "price_basis": "scoring_trim" if (price_map or {}).get(c.get("model")) else "range_min",
            "bin_mean_ppw": round(anchor, 6), "anchor_scope": "pool", "before": before, "after": final,
            "delta": round(delta, 3), "residual_delta": rdelta,
            "residual_rate": (rev or {}).get("rate"),
            "source": "fixed_anchor" if fixed_bins else "pool_fit",
            "basis": (f"质价比（全池统一）：每万元产品力 {ppw:.3f} / 全池均 {anchor:.3f}"
                      f" → {delta:+.2f}；中性 {NEUTRAL:g}"
                      + (f"；保值率 {rev['rate']:g}% 微调 {rdelta:+.2f}" if rev else "")),
        }
        applied.append({"model": c.get("model"), "bin": b, "before": before,
                        "after": final, "delta": round(delta, 3)})
    return {"status": "applied" if applied else "skipped", "n": len(cands),
            "covered": len(applied), "bins": bins, "pool_mean": pool_mean,
            "source": "fixed_anchor" if fixed_bins else "pool_fit",
            "method": (f"质价比法（全池统一）：每万元产品力（分母=评分配置价）相对全池均值的距离"
                       f"（gain={GAIN:g}, cap=±{CAP:g}, 中性={NEUTRAL:g}，不分价位档）"),
            "applied": applied}


def delta_for(ppw: float | None, bin_mean: float | None) -> float | None:
    """唯一口径：每万元产品力相对本档均值的残差（clamp ±CAP）。apply 的审计
    delta 与 value_for 都调它，杜绝两份公式漂移导致反向注入检不出。"""
    if ppw is None or not bin_mean or bin_mean <= 0:
        return None
    return max(-CAP, min(CAP, (ppw / bin_mean - 1.0) * GAIN))


def value_for(ppw: float | None, bin_mean: float | None,
              residual_delta: float = 0.0) -> float | None:
    """纯函数：给定每万元产品力 + 本档均值 → value 分（两处共用的唯一口径）。

    逐配置链路（trim_annotate）与车系链路（apply）都调它，杜绝「车系用残差、
    逐配置仍用 kWh/万元」的口径分叉——后者曾让 with_config_scores 用 best_trim
    覆盖回旧 value，造成同车跨榜 value 差 0.9（宋Ultra 9.5 vs 8.6）。
    """
    delta = delta_for(ppw, bin_mean)
    if delta is None:
        return None
    return round(max(0.0, min(10.0, NEUTRAL + delta + (residual_delta or 0.0))), 2)


def summarize(report: dict) -> dict:
    return {"status": report.get("status"), "covered": report.get("covered"),
            "n": report.get("n"), "bins": report.get("bins"),
            "pool_mean": report.get("pool_mean"), "method": report.get("method"),
            "source": report.get("source"), "applied": report.get("applied") or []}


def selftest() -> int:
    w = DEFAULT_W
    # 1) 档边界
    assert price_bin(14.99) == "≤15万" and price_bin(15.0) == "15-20万"
    assert price_bin(20.0) == "20-25万" and price_bin(25.01) == ">25万"
    assert price_bin(None) is None
    # 2) 产品力剔除 value：给同一车不同 value 分，pf 不变（证明不重复计分）
    base = {"scores": {"powertrain": 8, "safety": 8, "adas": 8, "space": 8,
                       "control": 8, "cabin": 8, "quality": 8, "value": 5}}
    pf1 = product_strength(base, w)
    base2 = json.loads(json.dumps(base)); base2["scores"]["value"] = 10
    assert pf1 == product_strength(base2, w) == 8.0, "产品力不应受 value 自身影响"
    assert product_strength({"scores": {}}, w) is None, "缺维应返回 None 不插补"
    # 3) 同档内：每万元产品力高者 value 更高（单调）
    c_hi = {"model": "高档值", "price_guide": [14.0],
            "scores": {**base["scores"], "powertrain": 9}}
    c_lo = {"model": "低档值", "price_guide": [14.0],
            "scores": {**base["scores"], "powertrain": 7}}
    r = apply([c_hi, c_lo], w)
    assert r["covered"] == 2
    assert c_hi["scores"]["value"] > c_lo["scores"]["value"], "同档高产品力应价值分更高"
    # 3a) v10.47 根因：全池统一锚——不同价位档的车共用同一个 pool_mean，
    # 不再"高档均值低→贵车被抬分"。构造跨档两车：贵车 ppw 更低应 value 更低。
    c_cheap = {"model": "便宜车", "price_guide": [10.0],
               "scores": {**base["scores"], "powertrain": 9}}
    c_pricey = {"model": "贵车", "price_guide": [30.0],
                "scores": dict(base["scores"])}  # pf 更低 + 价更高 → ppw 必更低
    _rr = apply([c_cheap, c_pricey], w)
    assert _rr["covered"] == 2
    _anchor_used = {c["model"]: c["value_residual"]["bin_mean_ppw"]
                    for c in (c_cheap, c_pricey)}
    assert _anchor_used["便宜车"] == _anchor_used["贵车"], \
        f"全池统一锚必须同值: {_anchor_used}"
    assert all(c["value_residual"]["anchor_scope"] == "pool" for c in (c_cheap, c_pricey))
    assert c_cheap["scores"]["value"] > c_pricey["scores"]["value"], \
        "统一锚下每万元产品力高者（便宜车）value 应更高"
    # 3b) v10.45 根因：price_map（评分配置价）必须覆盖 price_guide[0]（区间最低价）。
    # Model Y 类：区间最低价配车系产品力 → ppw 虚高。给配置价后 ppw 必须按配置价算，
    # 且低配价、高配价两车 ppw 不同（证明 price_map 真生效、不是摆设）。
    _c = {"model": "测试车", "price_guide": [26.35, 36.9], "scores": dict(base["scores"])}
    _b0, _pf0, _ppw0 = _ppw(_c, w)
    _b1, _pf1, _ppw1 = _ppw(_c, w, {"测试车": 28.85})
    assert _ppw1 < _ppw0, "配置价(28.85)的ppw应低于区间最低价(26.35)"
    assert abs(_ppw1 - round(_pf0 / 28.85, 6)) < 1e-6, "price_map 分母应为配置价"
    assert abs(_ppw0 - round(_pf0 / 26.35, 6)) < 1e-6, "无price_map应回退区间最低价"
    _fixed = {"bins": {_b0: 0.30}, "pool_mean": 0.30}  # 同锚点才可比 value
    _c1 = json.loads(json.dumps(_c))
    _r0 = apply([_c1], w, fixed_bins=_fixed)
    _c2 = json.loads(json.dumps(_c))
    _r1 = apply([_c2], w, fixed_bins=_fixed, price_map={"测试车": 28.85})
    assert _c2["value_residual"]["price_basis"] == "scoring_trim", "审计应记录配置价口径"
    assert _r1["applied"][0]["after"] < _r0["applied"][0]["after"], "同锚点下配置价value应低于最低价value"
    # 4) 固定锚点：same cands 用不同子池拟合 vs 固定快照 → 同车同分
    snap = {"bins": r["bins"], "pool_mean": r["pool_mean"]}
    c_hi2 = json.loads(json.dumps(c_hi)); c_lo2 = json.loads(json.dumps(c_lo))
    apply([c_hi2, c_lo2], w, fixed_bins=snap)
    assert c_hi2["scores"]["value"] == c_hi["scores"]["value"], "固定锚点下同车应同分"
    assert c_lo2["scores"]["value"] == c_lo["scores"]["value"], "固定锚点下同车应同分"
    # 5) 反向注入：把档均改成极小值 → delta 触顶 cap（证明公式真在读锚而非硬编码）
    inj = {"bins": {k: 0.001 for k in r["bins"]}, "pool_mean": 0.001}
    c_inj = json.loads(json.dumps(c_hi))
    apply([c_inj], w, fixed_bins=inj)
    assert abs(c_inj["scores"]["value"] - round(NEUTRAL + CAP, 2)) < 0.05, "反向注入未触顶"
    # 6) 覆盖 clamp 下界
    inj_lo = {"bins": {k: 1000.0 for k in r["bins"]}, "pool_mean": 1000.0}
    c_inj2 = json.loads(json.dumps(c_hi))
    apply([c_inj2], w, fixed_bins=inj_lo)
    assert abs(c_inj2["scores"]["value"] - round(NEUTRAL - CAP, 2)) < 0.05, "反向注入未触底"
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--category", default="car_midsuv_bev")
    a = ap.parse_args()
    if a.selftest:
        rc = selftest()
        print("[value_residual selftest] OK" if rc == 0 else f"[value_residual selftest] FAIL({rc})")
        raise SystemExit(rc)
    cfg = json.load(open(str(paths.cat(f"{a.category}.json")), encoding="utf-8"))
    w = {x["key"]: x["weight"] for x in cfg.get("dimensions", [])}
    pool = json.load(open(os.path.join(HERE, "runs", "midsuv_bev_pool_202611_final.json"), encoding="utf-8"))
    cands = pool.get("candidates", pool)
    print(json.dumps(summarize(fit_bins(cands, w)), ensure_ascii=False, indent=2))
