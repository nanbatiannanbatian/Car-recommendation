# -*- coding: utf-8 -*-
"""param_score.py —— 参数驱动的 8 维打分（燃油/油混/插混/增程/纯电同一口径）。

为什么要有
----------
2026-11-24 用户要求：燃油/油混/插混的 8 维打分「从代码上实现，不要完成一次问一次」。
此前这三类只能靠人工补采 batch（`runs/*_scores_batch*.json`），每出一个榜就要重新问人采分。
本模块把打分变成**纯函数**：给定 `ah_series.py` 采到的官方事实（参数矩阵 + 口碑聚合 + PPH），
按固定公式产出 8 维分、维覆盖、证据等级与逐维推导。

8 维与权重（单一事实源 = `modules/envscore/build_energy_compare.py::DIMS`）
----------
动力总成 0.18 / 安全 0.162 / 智驾 0.135 / 空间 0.126 / 性价比 0.117 / 质量品控 0.100 /
操控 0.090 / 车机座舱 0.090

每维的数据来源（谁缺就 null，绝不填假分）
----------------------------------------
  powertrain  比功率(kW/t) + 口碑「驾驶感受」+ 油耗/馈电油耗 + 插混纯电续航
  safety      官方碰撞成绩（C-NCAP/C-IASI 判档）；当前仓库只有「有测评记录」事实、无判档
              → 恒 null；未来 `facts["crash_grade"]` 一填自动生效（这里留了钩子）
  adas        口碑「配置」分（用户对配置含辅助驾驶的评价，L4 自样本代理，非实测）
  space       轴距 + 后备厢 + 口碑「空间」
  value       马力/万元（指导价）+ 口碑「性价比」
  quality     PPH：可靠性 PPH 主、新车 PPH 辅（汽车之家口碑聚合，样本数一并记录）
  control     0-100km/h + 口碑「驾驶感受」
  cabin       口碑「内饰」+ 口碑「配置」

为什么用**绝对锚点**而不是池内百分位：池子一变名次就变，跨榜不可复现。
锚点全部写在 `ANCHORS`，改锚点必须同步改 selftest。

维覆盖与合成
------------
`score_facts()` 返回 `coverage`（可用维数 0-8）。合成 = 可用维加权均值 ×10（分母只算可用维），
所以「缺维不惩罚、也不假装有分」。可用维 <5 → 视为证据不足，返回 None（进待补采，不上市）。
证据等级：可用维 ≥7 且口碑+PPH 齐全 → B；5-6 → C；<5 → None。

用法
----
    python3 param_score.py --selftest
    python3 param_score.py --facts facts.json --out score.json
"""
from __future__ import annotations

import argparse
import json
import sys

import paths

sys.path.insert(0, str(paths.ROOT))

# 统一 8 维权重（与 envscore DIMS 同值；回归 param_score_regress 会比对一致性）
WEIGHTS = {"powertrain": 0.180, "safety": 0.162, "adas": 0.135, "space": 0.126,
           "value": 0.117, "quality": 0.100, "control": 0.090, "cabin": 0.090}
DIM_CN = {"powertrain": "动力总成", "safety": "安全", "adas": "智驾", "space": "空间",
          "value": "性价比", "quality": "质量品控", "control": "操控", "cabin": "车机座舱"}
MIN_DIMS = 5          # 可用维下限：不足则证据不足，返回 None
B_GRADE_DIMS = 7      # ≥7 维且有口碑/PPH → B

# 绝对锚点（单调查表插值；表外按端点截断）——改这里必须同步 selftest
ANCHORS = {
    # 比功率 kW/t → 动力基础分
    "p2w": [(40, 3.8), (50, 4.6), (60, 5.5), (70, 6.3), (80, 7.0), (90, 7.6),
            (105, 8.4), (120, 9.1), (140, 9.6)],
    # 轴距 mm → 空间基础分
    "wheelbase": [(2550, 4.2), (2600, 4.8), (2650, 5.3), (2700, 5.9), (2750, 6.5),
                  (2800, 7.1), (2850, 7.6), (2900, 8.1), (2950, 8.5)],
    # 0-100 km/h → 操控基础分
    "zero100": [(6.5, 9.4), (7, 9.0), (8, 8.3), (9, 7.5), (10, 6.8),
                (11, 6.1), (12, 5.4), (14, 4.6), (16, 4.0)],
    # 马力/万元 → 性价比基础分
    "hp_per_wan": [(6, 2.8), (8, 4.0), (10, 5.2), (12, 6.3), (14, 7.2),
                   (16, 8.0), (18, 8.6), (22, 9.3), (26, 9.7)],
}
# 油耗（WLTC L/100km）→ 动力总成加减分
FUEL_ADJ = [(4.0, 0.75), (4.5, 0.6), (5.5, 0.35), (6.5, 0.10), (7.5, -0.15),
            (9.0, -0.45), (11.0, -0.8), (13.0, -1.1)]
# 馈电油耗（插混/增程）→ 加减分（门槛放宽，电机兜底）
DEPRIVED_ADJ = [(4.0, 0.7), (5.0, 0.5), (6.0, 0.3), (7.0, 0.0), (8.0, -0.3), (10.0, -0.7)]
# PPH → 质量分：可靠性主、新车辅
PPH_BASE, PPH_REL_W, PPH_NEW_W = 9.9, 0.0135, 0.008


def _interp(x, table):
    """单调查表线性插值，表外截断。x 为 None → None。"""
    if x is None:
        return None
    pts = sorted(table)
    if x <= pts[0][0]:
        return pts[0][1]
    if x >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return None


def _clamp(v, lo=0.0, hi=10.0):
    return max(lo, min(hi, v))


def _kb(koubei, name):
    """口碑 5 分制维度 → 数值；缺维/缺车系 → None。"""
    dims = (koubei or {}).get("dims") or {}
    v = dims.get(name)
    return float(v) if isinstance(v, (int, float)) else None


def score_facts(facts):
    """车系事实 → {product, scores{8维}, coverage[可用维], evidence, explain{维:{inputs,formula}}}。

    facts 需含：power, price_min, curb_kg, max_power_kw(或 engine_ps), fuel_wltc,
    fuel_deprived, cltc, zero100, wheelbase, trunk_l, koubei{avg,dims}, pph{reliability,new_car}
    任何字段缺失 → 该维 null，不推断。
    """
    power = (facts.get("power") or "").upper()
    price = facts.get("price_ref") or facts.get("price_min")
    curb = facts.get("curb_kg")
    kw = facts.get("system_kw") if facts.get("system_kw") is not None else facts.get("max_power_kw")
    ps = facts.get("engine_ps") or facts.get("elec_ps")
    if kw is None and ps:
        kw = ps * 0.7355
    if kw is None and facts.get("elec_kw"):
        kw = facts["elec_kw"]
    hp_per_wan = (kw / 0.7355) / price if (price and kw) else None
    p2w = kw / (curb / 1000.0) if (kw and curb) else None
    kb = facts.get("koubei") or {}
    pph = facts.get("pph") or {}

    scores, explain = {}, {}

    # ① 动力总成
    base = _interp(p2w, ANCHORS["p2w"])
    fuel = facts.get("fuel_wltc")
    deprived = facts.get("fuel_deprived")
    adj = 0.0
    parts = []
    if power in ("PHEV", "EREV") and deprived is not None:
        adj += _interp(deprived, DEPRIVED_ADJ) or 0
        parts.append("馈电油耗 %.1fL→%+.2f" % (deprived, _interp(deprived, DEPRIVED_ADJ) or 0))
    elif fuel is not None:
        adj += _interp(fuel, FUEL_ADJ) or 0
        parts.append("WLTC %.1fL→%+.2f" % (fuel, _interp(fuel, FUEL_ADJ) or 0))
    kbp = _kb(kb, "驾驶感受")
    if kbp is not None:
        adj += (kbp - 4.0) * 0.35
        parts.append("口碑驾驶 %.2f→%+.2f" % (kbp, (kbp - 4.0) * 0.35))
    ev = facts.get("cltc")
    if power in ("PHEV", "EREV") and ev:
        bonus = 0.7 if ev >= 150 else (0.5 if ev >= 100 else (0.25 if ev >= 50 else 0))
        adj += bonus
        parts.append("纯电续航 %s→%+.2f" % (ev, bonus))
    if base is not None:
        scores["powertrain"] = _clamp(base + adj)
        explain["powertrain"] = {"inputs": {"p2w_kw_t": round(p2w, 1) if p2w else None,
                                            "kw": round(kw, 1) if kw else None},
                                 "formula": "比功率插值 + " + ("；".join(parts) or "无附加")}

    # ② 安全：官方碰撞判档缺失 → 恒 null（钩子：facts["crash_grade"] 0-10）
    cg = facts.get("crash_grade")
    if isinstance(cg, (int, float)):
        scores["safety"] = _clamp(float(cg))
        explain["safety"] = {"inputs": {"crash_grade": cg}, "formula": "官方碰撞判档"}

    # ③ 智驾：口碑「配置」代理
    kbc = _kb(kb, "配置")
    if kbc is not None:
        scores["adas"] = _clamp(5.0 + (kbc - 4.0) * 2.0)
        explain["adas"] = {"inputs": {"koubei_配置": kbc},
                           "formula": "5.0 + (口碑配置-4)×2（L4 自样本代理，无实测）"}

    # ④ 空间
    wb, trunk, kbs = facts.get("wheelbase"), facts.get("trunk_l"), _kb(kb, "空间")
    sp = _interp(wb, ANCHORS["wheelbase"])
    if sp is not None:
        if trunk:
            sp += 0.3 if trunk >= 550 else (0.15 if trunk >= 480 else (-0.2 if trunk < 420 else 0))
        if kbs is not None:
            sp += (kbs - 4.0) * 1.2
        scores["space"] = _clamp(sp)
        explain["space"] = {"inputs": {"wheelbase": wb, "trunk_l": trunk, "koubei_空间": kbs},
                            "formula": "轴距插值 + 后备厢档 + 口碑空间×1.2"}

    # ⑤ 性价比
    kv = _kb(kb, "性价比")
    v0 = _interp(hp_per_wan, ANCHORS["hp_per_wan"])
    if v0 is not None:
        if kv is not None:
            v0 += (kv - 4.0) * 0.8
        scores["value"] = _clamp(v0)
        explain["value"] = {"inputs": {"hp_per_wan": round(hp_per_wan, 1) if hp_per_wan else None,
                                       "koubei_性价比": kv},
                            "formula": "马力/万元插值 + 口碑性价比×0.8"}

    # ⑥ 质量品控：PPH
    rel, new = pph.get("reliability"), pph.get("new_car")
    if isinstance(rel, (int, float)):
        q = PPH_BASE - PPH_REL_W * float(rel)
        if isinstance(new, (int, float)):
            q -= PPH_NEW_W * float(new)
        scores["quality"] = _clamp(q)
        explain["quality"] = {"inputs": {"可靠性PPH": rel, "新车PPH": new,
                                         "样本数": pph.get("reliability_n")},
                              "formula": "9.9 − 0.0135×可靠性PPH − 0.008×新车PPH"}

    # ⑦ 操控
    z, kbd = facts.get("zero100"), _kb(kb, "驾驶感受")
    c0 = _interp(z, ANCHORS["zero100"])
    if c0 is not None:
        if kbd is not None:
            c0 += (kbd - 4.0) * 0.5
        scores["control"] = _clamp(c0)
        explain["control"] = {"inputs": {"zero100_s": z, "koubei_驾驶": kbd},
                              "formula": "零百插值 + 口碑驾驶×0.5"}

    # ⑧ 车机座舱
    ki, kbc2 = _kb(kb, "内饰"), _kb(kb, "配置")
    if ki is not None or kbc2 is not None:
        cb = 5.5
        ins = {}
        if ki is not None:
            cb += (ki - 4.0) * 1.5
            ins["koubei_内饰"] = ki
        if kbc2 is not None:
            cb += (kbc2 - 4.0) * 1.0
            ins["koubei_配置"] = kbc2
        scores["cabin"] = _clamp(cb)
        explain["cabin"] = {"inputs": ins, "formula": "5.5 + 内饰×1.5 + 配置×1.0（口碑代理）"}

    avail = list(scores)
    wsum = sum(WEIGHTS[k] for k in avail)
    product = round(sum(WEIGHTS[k] * scores[k] for k in avail) / wsum * 10, 1) if wsum else None
    if not avail or product is None or len(avail) < MIN_DIMS:
        return None
    ev = "B" if (len(avail) >= B_GRADE_DIMS and kb.get("average") and pph) else "C"
    return {"product": product, "scores": {k: round(v, 2) for k, v in scores.items()},
            "coverage": avail, "coverage_n": len(avail), "evidence": ev,
            "weights_used": {k: WEIGHTS[k] for k in avail}, "explain": explain}


# ---------------------------------------------------------------- 纯电候选池 7 维 → 统一 8 维
BEV_MAP = {"powertrain": "powertrain", "safety": "safety", "adas": "adas", "space": "space",
           "value": "value", "control": "control", "cabin": "cabin"}


def score_from_dim_scores(dim_scores, label_map=None):
    """已算好的分维（如紧凑纯电候选池的 7 维 三电/安全/智驾/空间/性价比/操控/车机）
    → 统一 8 维口径。缺维按可得维归一，不补假分。"""
    label_map = label_map or {}
    out = {}
    for k, v in (dim_scores or {}).items():
        tgt = BEV_MAP.get(label_map.get(k, k), None)
        if tgt and isinstance(v, (int, float)):
            out[tgt] = _clamp(float(v))
    avail = list(out)
    if len(avail) < MIN_DIMS:
        return None
    wsum = sum(WEIGHTS[k] for k in avail)
    return {"product": round(sum(WEIGHTS[k] * out[k] for k in avail) / wsum * 10, 1),
            "scores": {k: round(v, 2) for k, v in out.items()},
            "coverage": avail, "coverage_n": len(avail),
            "evidence": "B" if len(avail) >= B_GRADE_DIMS else "C",
            "weights_used": {k: WEIGHTS[k] for k in avail}}


def selftest():
    # 1) 权重与 envscore 单一事实源一致（跨层校验放在 regress/param_score_regress，engine 层不许向上依赖）
    # 2) 插值：单调、端点截断、None 透传
    assert _interp(None, ANCHORS["zero100"]) is None
    assert _interp(6.0, ANCHORS["zero100"]) == ANCHORS["zero100"][0][1]
    assert _interp(20.0, ANCHORS["zero100"]) == ANCHORS["zero100"][-1][1]
    a, b = _interp(9, ANCHORS["zero100"]), _interp(10, ANCHORS["zero100"])
    assert a > b > 0
    # 3) 轩逸型事实（2026-11-24 抓取：轴距 2712 / 135Ps / 5.88L / 口碑 4.4x / PPH 171）
    facts = {"power": "ICE", "price_min": 7.99, "curb_kg": 1304, "max_power_kw": 99,
             "fuel_wltc": 5.88, "zero100": 12.1, "wheelbase": 2712, "trunk_l": 572,
             "koubei": {"average": 4.47, "dims": {"空间": 4.66, "驾驶感受": 4.24, "油耗": 4.7,
                                                  "内饰": 4.31, "性价比": 4.63, "配置": 4.12}},
             "pph": {"reliability": 171, "new_car": 139, "reliability_n": 773}}
    r = score_facts(facts)
    assert r and 5 <= r["product"] <= 95, r
    assert "powertrain" in r["scores"] and "space" in r["scores"] and "quality" in r["scores"]
    assert "safety" not in r["scores"], "无碰撞判档时安全维必须缺，不许填"
    assert r["coverage_n"] == 7, r["coverage"]
    # 4) 缺维策略：只有价格/动力 → 维数不足 → None
    thin = {"power": "ICE", "price_min": 10, "max_power_kw": 100, "curb_kg": 1300}
    assert score_facts(thin) is None, "可用维 <5 必须判证据不足"
    # 5) 更强的动力/更短零百 → 动力/操控不降
    strong = dict(facts, max_power_kw=150, zero100=8.0, fuel_wltc=6.2)
    rs = score_facts(strong)
    assert rs["scores"]["powertrain"] > r["scores"]["powertrain"]
    assert rs["scores"]["control"] > r["scores"]["control"]
    # 6) PPH 越差质量分越低
    bad = dict(facts, pph={"reliability": 400, "new_car": 300})
    assert score_facts(bad)["scores"]["quality"] < r["scores"]["quality"]
    # 7) 纯电 7 维映射：缺质量维 → coverage 7，证据 B
    bev = score_from_dim_scores({"powertrain": 7.3, "safety": 8.2, "space": 5.7, "control": 6.2,
                                 "adas": 5.8, "value": 9.59, "cabin": 6.8})
    assert bev and bev["coverage_n"] == 7 and "quality" not in bev["scores"]
    assert score_from_dim_scores({"powertrain": 7.3}) is None, "维数不足应 None"
    print("[param_score selftest] PASS · 8维锚点/缺维不填/PPH单调/纯电映射")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--facts", help="单台事实 JSON（ah_series 的 group + 口碑）")
    ap.add_argument("--out", help="打分结果输出")
    a = ap.parse_args()
    if a.selftest or not a.facts:
        selftest()
        return
    with open(a.facts, encoding="utf-8") as f:
        facts = json.load(f)
    r = score_facts(facts)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
