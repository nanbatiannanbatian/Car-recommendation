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
  v10.49 起保值率不再混进 value，改为独立事实展示（residual_values.json 单一源）。

  v10.47 曾定性为「质价比」：每万元买到的产品力，全池一把尺子、不分价位档。
  但 v10.72 实测出反例：本池最低价的小鹏 G6（17.68 万）value 直接 9.0、排全榜第 2，
  而它的空间/品控/安全都在中下游——「便宜」本身被当成了优点，越像清库降价分越高。
  同时把 11 万级与 28 万级强行放在一把尺子上比「每万元」，跨档不可比。

  v10.73（用户授权）改回「同价位段残差」：先按评分配置价分档，再与本档中位比。
  落地为：
    · 产品力 pf = 其余七维（剔除 value 自身，避免重复计分）按权重归一的加权分；
    · 每万元产品力 ppw = pf / 评分配置价（v10.45 根因修复：旧版用 price_guide[0]
      区间最低价，与产品力的评分配置口径错配，Model Y 26.35后驱价配28.85长续航
      产品力 → value 虚高 9.54，note 却写"性价比三车最低"）；
    · value = NEUTRAL + gain × (ppw / 本档 ppw 中位 − 1)，clamp 到 ±cap；
      基准 8.0 中性，高于本档中位加分、低于扣分；档内 <3 款回退全池中位（审计标
      anchor_scope=pool_fallback）。
  这与 J.D. Power「同级组均值距离」的原意一致：同级=同价位段，而不是全市场。
  口径边界（页面须写明）：档内仍会奖励「同档更便宜且产品力不减」，这是性价比的
  本义，不是低价偏置；跨档不可比，要看绝对产品力请用纯产品力榜。

跨榜一致：锚点属榜内统计量，子榜必须从全价位榜固定快照取（anchor_pool 的
  value_bins.bins/pool_mean），否则子池中位漂移会让同车 value 变分。全榜自身本池现算。

契约：
  product_strength(cand, weights=None) -> float | None
  fit_bins(cands, weights=None) -> {"bins": {label: ppw_median}, "detail": [...]}
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
# 产品力（剔除 value 自身；v10.74 起含 reliability，但**不含 holding_cost**——
# 保值率是持有成本维度，不是产品力；把它塞进 pf 会让同一件事在性价比和持有成本各算一次）
PROD_DIMS = ("powertrain", "safety", "reliability", "adas", "space", "control", "cabin", "quality")
DEFAULT_W = {"powertrain": 0.21, "safety": 0.18, "reliability": 0.12, "adas": 0.05, "space": 0.11,
             "control": 0.04, "cabin": 0.03, "quality": 0.06}

# 固定价格档边界（万元）——写死为模块常量，跨榜一致，不随子池漂移。
BIN_EDGES = (15.0, 20.0, 25.0)
BIN_LABELS = ("≤15万", "15-20万", "20-25万", ">25万")

NEUTRAL = 8.0   # 与 J.D. Power「同级均值=80分」对齐（10 分制 8.0）
# v10.73 口径变更（用户 2026-11-25：「小鹏G6 性价比 9.0 全榜第 2 高，是不是不合理」→ 授权按建议改）
#
# 旧口径（v10.47→v10.72）：全池统一锚 —— value = 8 + 3×(ppw/全池均 − 1)，分母是价格。
# 后果（本池实测）：G6 是全榜最低价（17.68 万），ppw 0.479 对全池均 0.44 → 直接顶到 9.0，
# 而它的空间/品控/安全都在中下游。等于把"便宜"本身当成性价比优点 ——
# 越像清库降价，分越高；同时把 11 万级和 28 万级放在同一把尺子上比"每万元"。
#
# 新口径：同价位档残差 —— 先按评分配置价分档（≤15/15-20/20-25/>25 万，边界写死跨榜一致），
# value = 8 + GAIN×(ppw/本档 ppw 中位 − 1)。语义变成"同一个价位里，谁给的产品力更多"，
# 低价不再自动加分（价低但产品力也低的，ppw 不会高）；跨档不可比（也不再被贵车档均值拖抬）。
# 档内不足 MIN_PEERS 款时回退全池均（并在 basis 里写明 band→pool 回退，可审计）。
GAIN = 3.0      # 档内 ppw 相对档中位偏离 ≈30% → ±0.9 分（跨月稳定，不触顶）
CAP = 1.5       # 单维调整封顶，与 experience 单维 ±1.5 一致
MIN_PEERS = 3   # 档内少于 3 款不设该档中位，回退全池（2 款时中位就是两点中点，噪声太大）


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
    # v10.74：入池快照拍摄时还没有 reliability（那时质量/可靠性维还没算），
    # 逐维回退到当前 scores——否则「缺任一维返回 None」会让全池 value 全部跳过。
    s = cand.get("_value_pf_base") or {}
    cur = cand.get("scores") or {}
    vals = {d: (s.get(d) if isinstance(s.get(d), (int, float)) else cur.get(d)) for d in w}
    have = [d for d in w if isinstance(vals[d], (int, float))]
    # v10.74：允许缺 1–2 维（如 value 计算时点早于 reliability 落分），按已有维归一；
    # 缺得更多才判"产品力不可算"（不猜、不插补）。
    if len(have) < max(4, len(w) - 2):
        return None
    tw = sum(w[d] for d in have)
    return round(sum(float(vals[d]) * w[d] for d in have) / tw, 6)


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
    """从本池拟合各价格档的 ppw 中位（v10.73 起锚点=档中位，不再用均值）。

    中位而非均值：档内只有 3~8 款，均值会被单款极端值（如清库价低配）拽走；
    中位对离群稳健，也就不会被"最便宜那款"定义整档标准。
    """
    by_bin: dict[str, list[float]] = {}
    detail = []
    for c in cands:
        b, pf, ppw = _ppw(c, weights, price_map)
        if b and ppw is not None:
            by_bin.setdefault(b, []).append(ppw)
        detail.append({"model": c.get("model"), "bin": b, "pf": pf, "ppw": ppw})
    bins, bins_n = {}, {}
    for label in BIN_LABELS:
        vals = by_bin.get(label) or []
        bins_n[label] = len(vals)
        if len(vals) >= MIN_PEERS:
            bins[label] = round(statistics.median(vals), 6)
    all_ppw = [d["ppw"] for d in detail if d["ppw"] is not None]
    return {"bins": bins, "bins_n": bins_n,
            "pool_mean": round(statistics.median(all_ppw), 6) if all_ppw else None,
            "pool_mean_ppw": round(statistics.mean(all_ppw), 6) if all_ppw else None,
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
        # v10.73 根因修复：锚点从"全池一个均"改回"同价位档中位"。
        # 旧口径把便宜本身当优点：G6 是全榜最低价，ppw 对全池均 +8% 就够到 9.0，
        # 与它中下游的空间/品控/安全不对称；跨档比"每万元"也等于用贵车的价格当惩罚。
        # 现口径：同一价位段内比产品力/价格的残差，价低但产品力同样低的不会占便宜。
        anchor = bins.get(b) or pool_mean
        scope = "band" if bins.get(b) else "pool_fallback"
        if not anchor or anchor <= 0:
            continue
        scores = c.setdefault("scores", {})
        before = scores.get("value")
        # v10.49 根因修复：保值率不再混进"质价比"这一格。
        # 用户看到 value 列以为是纯质价比，实为"质价比+保值微调"合成——
        # 极狐T5 质价比 delta(0.667)>海狮06(0.434) 却因保值-0.31 最终分更低，用户误读。
        # J.D. Power 的 resale 是独立 20% 维度，不该塞进单一 value 格。
        # 保值率改为独立事实展示（residual_values.json 单一源），不再加分。
        delta = delta_for(ppw, anchor)
        final = value_for(ppw, anchor)  # 不传 residual_delta
        scores["value"] = final
        _used_price = (price_map or {}).get(c.get("model")) or (
            c.get("price_guide")[0] if isinstance(c.get("price_guide"), (list, tuple))
            and c.get("price_guide") else c.get("price_guide"))
        c["value_residual"] = {
            "status": "applied", "bin": b, "pf": round(pf, 3), "ppw": ppw,
            "price_used": _used_price, "price_basis": "scoring_trim" if (price_map or {}).get(c.get("model")) else "range_min",
            "bin_mean_ppw": round(anchor, 6), "anchor_scope": scope,
            "band_n": (fit.get("bins_n") or {}).get(b) if not fixed_bins else None,
            "pool_anchor_ppw": pool_mean, "before": before, "after": final,
            "delta": round(delta, 3),  # v10.49：保值率已剔除，residual_delta 不再进 value
            "source": "fixed_anchor" if fixed_bins else "pool_fit",
            "basis": (f"同价位段残差（不含保值）：本档 {b} 每万元产品力 {ppw:.3f} / 本档中位 "
                      f"{anchor:.3f}→{delta:+.2f}"
                      + ("" if scope == "band" else "（本档样本不足，回退全池中位）")
                      + f"；中性 {NEUTRAL:g}"),
        }
        applied.append({"model": c.get("model"), "bin": b, "before": before,
                        "after": final, "delta": round(delta, 3)})
    return {"status": "applied" if applied else "skipped", "n": len(cands),
            "covered": len(applied), "bins": bins, "pool_mean": pool_mean,
            "source": "fixed_anchor" if fixed_bins else "pool_fit",
            "method": (f"同价位段残差：每万元产品力（分母=评分配置价）相对『本价位档中位』的距离"
                       f"（gain={GAIN:g}, cap=±{CAP:g}, 中性={NEUTRAL:g}；档界 ≤15/15-20/20-25/>25 万，"
                       f"档内 <{MIN_PEERS} 款回退全池中位）"),
            "applied": applied}


def delta_for(ppw: float | None, bin_mean: float | None) -> float | None:
    """唯一口径：每万元产品力相对本档均值的残差（clamp ±CAP）。apply 的审计
    delta 与 value_for 都调它，杜绝两份公式漂移导致反向注入检不出。"""
    if ppw is None or not bin_mean or bin_mean <= 0:
        return None
    return max(-CAP, min(CAP, (ppw / bin_mean - 1.0) * GAIN))


def value_for(ppw: float | None, bin_mean: float | None,
              residual_delta: float = 0.0) -> float | None:
    """纯函数：给定每万元产品力 + 全池均值 → 纯质价比分（两处共用的唯一口径）。

    逐配置链路（trim_annotate）与车系链路（apply）都调它，杜绝口径分叉。
    v10.49：residual_delta 参数保留仅为签名兼容（旧调用方仍传），但不再计入——
    保值率是独立事实，不混进"质价比"这一格。
    """
    delta = delta_for(ppw, bin_mean)
    if delta is None:
        return None
    return round(max(0.0, min(10.0, NEUTRAL + delta)), 2)


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
    # 3a) v10.73 口径：同价位档残差——每档用自己的中位做锚，跨档不可比。
    # 构造 3 款 ≤15万（价 10，pf 8 → ppw 0.8）+ 3 款 >25万（价 30，pf 8 → ppw 0.2667）：
    # 各自档内都在中位上 → 全部 ≈ 8.0，低价不再自动加分（旧口径下便宜档会被抬到 9.5）。
    _cheap = [{"model": f"便宜{i}", "price_guide": [10.0], "scores": dict(base["scores"])}
              for i in range(3)]
    _pricey = [{"model": f"贵{i}", "price_guide": [30.0], "scores": dict(base["scores"])}
               for i in range(3)]
    _rr = apply(_cheap + _pricey, w)
    assert _rr["covered"] == 6
    _anch = {c["model"]: (c["value_residual"]["bin_mean_ppw"], c["value_residual"]["anchor_scope"])
             for c in _cheap + _pricey}
    assert _anch["便宜0"][1] == "band" and _anch["贵0"][1] == "band", f"档内≥3款应用本档锚: {_anch}"
    assert abs(_anch["便宜0"][0] - 0.8) < 1e-6 and abs(_anch["贵0"][0] - 0.2667) < 1e-3, \
        f"各档锚应互不相同: {_anch}"
    for c in _cheap + _pricey:
        assert abs(c["scores"]["value"] - NEUTRAL) <= 0.02, \
            f"档内处于中位应得中性 {NEUTRAL}，低价不得自动加分: {c['model']}={c['scores']['value']}"
    # 3a-2) 档内更低价的低产品力车不得占便宜：价 9 但 pf 7.2（ppw 同为 0.8）
    _c_cheap_low = {"model": "便宜低配", "price_guide": [9.0],
                    "scores": {k: (7.2 if k in PROD_DIMS else v) for k, v in base["scores"].items()}}
    _r_low = apply([_c_cheap_low], w, fixed_bins=_rr)
    assert abs(_c_cheap_low["scores"]["value"] - NEUTRAL) <= 0.05, \
        f"价低但产品力同样低不得加分: {_c_cheap_low['scores']['value']}"
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
    # 3c) v10.49 根因：保值率不得混进 value——value_for 传 residual_delta 也应忽略
    _pp = 0.5
    _v_plain = value_for(_pp, 0.45)
    _v_res = value_for(_pp, 0.45, residual_delta=0.31)
    assert _v_plain == _v_res, "保值率不得改变质价比分（参数仅为签名兼容）"
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
