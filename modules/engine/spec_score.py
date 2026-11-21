#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配置驱动打分（spec_score.py）——把「看参数给个分」变成「按规则算个分」。

为什么要它：
  现在的七维分数是人看着配置表给的。同一台车，换个人会给出不同的分，
  而且无法反驳——「你说智驾 8.4，凭什么是 8.4？」这个问题没法回答。
  配置驱动打分把每维拆成可核对的加减项：有激光雷达 +2，没有 +0，
  800V +1.0，400V +0，谁都能按同一张表复算出同一个数。

设计原则：
  1 每一维 = 基线 + 若干加减项，每项都写明触发条件与分值
  2 参数缺失不是 0 分，而是**该维标记插补**并降置信度——缺数据不等于差
  3 输出「算出来的分」与「人工给的分」并列，差异超过 0.5 的必须解释

它的局限必须写在报告里：
  参数只能覆盖到「能查到的部分」。轴距、悬架形式、芯片型号这类信息
  在公开页面上并不总是齐全，缺的部分只能插补，插补的维度不构成结论。

用法：
    python3 spec_score.py --specs specs.json --candidates cands.json
    python3 spec_score.py --specs specs.json --candidates cands.json --out cmp.md
"""
import argparse
import json
import os
import statistics
import sys

DIMS = ["adas", "powertrain", "space", "control", "safety", "value", "cabin"]


def _n(x, default=None):
    try:
        return float(x) if x is not None else default
    except (TypeError, ValueError):
        return default


def score_adas(s):
    """智驾：硬件决定上限，软件决定可用性。"""
    base, why = 5.0, []
    lidar = s.get("adas_lidar")
    if lidar is True or (isinstance(lidar, (int, float)) and lidar >= 1):
        base += 1.6
        why.append("激光雷达 +1.6")
    tops = _n(s.get("adas_tops"))
    if tops is not None:
        if tops >= 400:
            base += 2.0
            why.append(f"{tops:g}TOPS +2.0")
        elif tops >= 200:
            base += 1.5
            why.append(f"{tops:g}TOPS +1.5")
        elif tops >= 100:
            base += 0.9
            why.append(f"{tops:g}TOPS +0.9")
        elif tops >= 40:
            base += 0.4
            why.append(f"{tops:g}TOPS +0.4")
        else:
            why.append(f"{tops:g}TOPS +0")
    if s.get("adas_city_noa") is True:
        base += 1.2
        why.append("城区 NOA +1.2")
    if s.get("adas_highway_noa") is True:
        base += 0.4
        why.append("高速 NOA +0.4")
    return base, why


def powertrain_parts(s):
    """三电各加减项的**分解**（规则唯一出处，score_powertrain 与 rule_adjust 共用）。

    返回 [(项名, 分值, 展示文本)]。**参数缺失的项不出现**——这样 rule_adjust
    可以逐项做「该项有数据的车」的中位锚点：缺某一项的车在该项上既不加分
    也不扣分（缺数据 ≠ 差），不会因为没采到电压就被判三电弱。
    """
    parts = []
    if s.get("platform_800v") is True:
        parts.append(("高压平台", 1.0, "800V +1.0"))
    t = _n(s.get("charge_10_80_min"))
    if t is not None:
        if t <= 12:
            parts.append(("快充", 1.4, f"10–80% {t:g}min +1.4"))
        elif t <= 18:
            parts.append(("快充", 1.1, f"10–80% {t:g}min +1.1"))
        elif t <= 25:
            parts.append(("快充", 0.6, f"10–80% {t:g}min +0.6"))
        elif t <= 35:
            parts.append(("快充", 0.1, f"10–80% {t:g}min +0.1"))
        else:
            parts.append(("快充", -0.4, f"10–80% {t:g}min −0.4"))
    cell = str(s.get("cell_brand") or "")
    if any(k in cell for k in ("宁德", "弗迪", "比亚迪")):
        parts.append(("电芯", 0.7, "一线电芯 +0.7"))
    elif cell and "未披露" not in cell:
        parts.append(("电芯", 0.2, "二线电芯 +0.2"))
    rg = _n(s.get("range_km"))
    if rg is not None:
        if rg >= 700:
            parts.append(("续航", 0.8, f"{rg:g}km +0.8"))
        elif rg >= 600:
            parts.append(("续航", 0.5, f"{rg:g}km +0.5"))
        elif rg < 500:
            parts.append(("续航", -0.5, f"{rg:g}km −0.5"))
    return parts


def score_powertrain(s):
    """三电：补能速度 > 电池容量 > 电芯品牌。"""
    base, why = 6.0, []
    for _name, v, desc in powertrain_parts(s):
        base += v
        why.append(desc)
    return base, why


def control_parts(s):
    """操控各加减项的分解（规则唯一出处，见 powertrain_parts 的同口径说明）。"""
    parts = []
    fs = str(s.get("susp_front") or "")
    if "双叉臂" in fs or "双横臂" in fs:
        parts.append(("前悬架", 1.0, "前双叉臂 +1.0"))
    elif "麦弗逊" in fs:
        parts.append(("前悬架", 0.0, "前麦弗逊 +0"))
    rs = str(s.get("susp_rear") or "")
    if any(k in rs for k in ("五连杆", "多连杆")):
        parts.append(("后悬架", 0.5, "后多连杆 +0.5"))
    elif "扭力梁" in rs:
        parts.append(("后悬架", -0.6, "后扭力梁 −0.6"))
    dr = str(s.get("drive") or "")
    if "四驱" in dr:
        parts.append(("驱动", 0.7, "四驱 +0.7"))
    elif "后驱" in dr:
        parts.append(("驱动", 0.4, "后驱 +0.4"))
    z = _n(s.get("zero_to_100_s"))
    if z is not None:
        if z <= 5:
            parts.append(("零百", 0.8, f"零百 {z:g}s +0.8"))
        elif z <= 7:
            parts.append(("零百", 0.3, f"零百 {z:g}s +0.3"))
        elif z > 9:
            parts.append(("零百", -0.5, f"零百 {z:g}s −0.5"))
    return parts


def score_control(s):
    """操控：悬架形式与驱动形式是硬信息，零百是软信息。"""
    base, why = 6.8, []
    for _name, v, desc in control_parts(s):
        base += v
        why.append(desc)
    return base, why


def score_space(s):
    """空间：轴距与车长双判，座位数加成。"""
    base, why = 6.4, []
    wb = _n(s.get("wheelbase_mm"))
    if wb is not None:
        if wb >= 3000:
            base += 1.6
            why.append(f"轴距 {wb:g} +1.6")
        elif wb >= 2900:
            base += 1.2
            why.append(f"轴距 {wb:g} +1.2")
        elif wb >= 2800:
            base += 0.6
            why.append(f"轴距 {wb:g} +0.6")
        elif wb < 2700:
            base -= 1.0
            why.append(f"轴距 {wb:g} −1.0")
    ln = _n(s.get("length_mm"))
    if ln is not None:
        if ln >= 4950:
            base += 0.9
            why.append(f"车长 {ln:g} +0.9")
        elif ln >= 4800:
            base += 0.5
            why.append(f"车长 {ln:g} +0.5")
        elif ln < 4600:
            base -= 0.7
            why.append(f"车长 {ln:g} −0.7")
    if _n(s.get("seats"), 5) >= 6:
        base += 0.4
        why.append("六座 +0.4")
    return base, why


def score_control(s):
    """操控：悬架形式与驱动形式是硬信息，零百是软信息。"""
    base, why = 6.8, []
    for _name, v, desc in control_parts(s):
        base += v
        why.append(desc)
    return base, why


def score_safety(s):
    """安全：有没有官方碰撞成绩是分水岭，没有就降权而不是判劣。"""
    base, why = 7.0, []
    nc = _n(s.get("cncap_pct"))
    if nc is not None:
        if nc >= 90:
            base += 1.6
            why.append(f"C-NCAP {nc:g}% +1.6")
        elif nc >= 85:
            base += 1.0
            why.append(f"C-NCAP {nc:g}% +1.0")
        else:
            base += 0.4
            why.append(f"C-NCAP {nc:g}% +0.4")
    else:
        base -= 0.7
        why.append("无官方碰撞成绩 −0.7（降权，不判劣）")
    if s.get("cell_to_body") is True:
        base += 0.5
        why.append("电池车身一体化 +0.5")
    hs = _n(s.get("high_strength_pct"))
    if hs is not None and hs >= 70:
        base += 0.4
        why.append(f"高强钢 {hs:g}% +0.4")
    return base, why


def score_cabin(s):
    """车机：芯片是一票否决项，屏幕与生态是加分项。"""
    base, why = 7.0, []
    chip = str(s.get("cabin_chip") or "")
    if "8295" in chip:
        base += 1.4
        why.append("8295 +1.4")
    elif "8155" in chip:
        base += 0.8
        why.append("8155 +0.8")
    elif "8775" in chip:
        base += 1.2
        why.append("8775 +1.2")
    inch = _n(s.get("screen_inch"))
    if inch is not None and inch >= 15:
        base += 0.4
        why.append(f"{inch:g}″ 屏 +0.4")
    if s.get("llm_voice") is True:
        base += 0.3
        why.append("语音大模型 +0.3")
    return base, why


def score_value(s, price, peer_prices):
    """性价比：同榜价格中位数 vs 本车价。只在这一维用相对位置，因为它本质是相对的。"""
    base, why = 6.5, []
    if price is None or not peer_prices:
        return None, ["缺价格，无法计算"]
    med = statistics.median(peer_prices)
    if med <= 0:
        return None, ["同榜价格中位数异常"]
    delta = (med - price) / med
    base += max(-1.8, min(2.2, delta * 4.0))
    why.append(f"同榜中位 {med:g} 万，本车 {price:g} 万（{delta:+.0%}，{base - 6.5:+.1f}）")
    return base, why


def score_car(spec, price, peer_prices):
    out, reasons, imputed = {}, {}, []
    for dim, fn in (("adas", score_adas), ("powertrain", score_powertrain),
                    ("space", score_space), ("control", score_control),
                    ("safety", score_safety), ("cabin", score_cabin)):
        v, why = fn(spec)
        out[dim], reasons[dim] = round(max(0.0, min(10.0, v)), 1), why
    v, why = score_value(spec, price, peer_prices)
    if v is None:
        imputed.append("value")
        out["value"] = None
        reasons["value"] = why
    else:
        out["value"] = round(max(0.0, min(10.0, v)), 1)
        reasons["value"] = why
    # 参数缺失统计：某一维完全没有加减项命中，说明该维参数基本没填
    for dim, why in reasons.items():
        hits = [w for w in why if "+" in w or any(k in w for k in ("−", "无官方"))]
        if len(hits) <= 1 and dim != "safety":
            imputed.append(dim)
    return out, reasons, sorted(set(imputed))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--specs", required=True, help="配置参数 json：{车型: {参数}}")
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--out")
    ap.add_argument("--only", help="只比对这些车型（逗号分隔）")
    a = ap.parse_args()

    specs = json.load(open(a.specs, encoding="utf-8"))
    cands = json.load(open(a.candidates, encoding="utf-8"))["candidates"]
    only = {x.strip() for x in (a.only or "").split(",") if x.strip()}
    by = {c["model"]: c for c in cands}

    # value 必须按「同榜/同级别」比价：拿中大型车去和全池（含大量中型车）中位数比，
    # 会让整个榜的车都被判成「贵」，这是口径错误而不是结论。
    from collections import defaultdict
    by_cls = defaultdict(list)
    for c in cands:
        tp = (c.get("trim") or {}).get("price")
        if tp:
            by_cls[str(c.get("cls"))].append(float(tp))
    prices = sorted({p for v in by_cls.values() for p in v})   # 兜底用

    rows = []
    for model, spec in specs.items():
        c = by.get(model)
        if not c:
            continue
        if only and model not in only:
            continue
        price = (c.get("trim") or {}).get("price")
        peer = by_cls.get(str(c.get("cls"))) or prices
        comp, reasons, imp = score_car(spec, float(price) if price else None, peer)
        manual = c.get("scores") or {}
        diffs = {d: (round(comp[d] - float(manual[d]), 1)
                     if comp.get(d) is not None and manual.get(d) is not None else None)
                 for d in DIMS}
        big = {d: v for d, v in diffs.items() if v is not None and abs(v) >= 0.5}
        rows.append({"model": model, "comp": comp, "manual": manual,
                     "diffs": diffs, "big": big, "imputed": imp, "reasons": reasons})

    L = ["# 配置驱动打分 vs 人工打分", ""]
    L.append("> 左侧是按固定规则从配置参数算出来的分，右侧是原来人工给的分。")
    L.append("> 规则写在 `spec_score.py` 里，每一维是「基线 + 加减项」，可以逐条复核。")
    L.append("")
    L.append(f"- 本次比对 **{len(rows)}** 款；参数齐全足以计算的有 "
             f"**{sum(1 for r in rows if not r['imputed'])}** 款。")
    L.append("")
    L.append("| 车型 | " + " | ".join(DIMS) + " | 差异≥0.5 的维度 |")
    L.append("|" + "---|" * (len(DIMS) + 2))
    for r in sorted(rows, key=lambda x: -len(x["big"])):
        cells = []
        for d in DIMS:
            cv, mv = r["comp"].get(d), r["manual"].get(d)
            if cv is None:
                cells.append("—")
            else:
                star = "*" if d in r["imputed"] else ""
                mark = "**" if mv is not None and abs(cv - float(mv)) >= 0.5 else ""
                cells.append(f"{mark}{cv:g}{star}{mark}")
        bigs = "、".join(f"{d}{v:+.1f}" for d, v in r["big"].items()) or "—"
        L.append(f"| {r['model']} | " + " | ".join(cells) + f" | {bigs} |")
    L.append("")
    L.append("> 加粗＝配置分与人工分差 ≥0.5；`*`＝该维参数不足，分数接近基线，**不构成结论**。"
             "差得多不代表谁错，要看下面逐条依据。")
    L.append("")

    detailed = [r for r in rows if r["big"] or r["imputed"]]
    if detailed:
        L.append("## 逐款依据与差异解释")
        L.append("")
        for r in detailed:
            L.append(f"### {r['model']}")
            L.append("")
            for d, v in r["big"].items():
                cv, mv = r["comp"][d], r["manual"].get(d)
                L.append(f"- **{d}**：配置算 {cv:g}，人工 {mv}，差 {v:+.1f}。"
                         f"加减项：{'；'.join(r['reasons'][d])}")
            if r["imputed"]:
                L.append(f"- **参数不足**：{'、'.join(r['imputed'])} "
                         f"（这几维的加减项几乎没命中，分数接近基线，不构成结论）")
            L.append("")

    # 维度有效性：某一维若在本榜是常数，它在加权里等于没有权重
    L.append("## 维度有效性检查（这一节比分数本身重要）")
    L.append("")
    L.append("| 维度 | 均值 | 标准差 | 有效样本 | 判定 |")
    L.append("|---|---|---|---|---|")
    dead = []
    for d in DIMS:
        vals = [r["comp"][d] for r in rows if r["comp"].get(d) is not None]
        if len(vals) < 3:
            L.append(f"| {d} | — | — | {len(vals)} | 样本不足 |")
            continue
        mu = sum(vals) / len(vals)
        sd = statistics.pstdev(vals)
        if sd < 0.15:
            verdict = "**无区分度：该维在本榜是常数，权重实际失效**"
            dead.append((d, mu, sd))
        elif sd < 0.35:
            verdict = "区分度弱"
        else:
            verdict = "有效"
        L.append(f"| {d} | {mu:.1f} | {sd:.2f} | {len(vals)} | {verdict} |")
    L.append("")
    if dead:
        L.append("**这一栏是本次比对最重要的产出**：")
        for d, mu, sd in dead:
            L.append(f"- `{d}` 全部落在 {mu:.1f}（标准差 {sd:.2f}）。"
                     f"它在本榜没有任何区分力，配置里给它的权重实际上被平均分给所有人。"
                     f"要么补数据让它有区分度，要么把这份权重让给别的维度。")
        L.append("")
        L.append("这不是打分规则的问题，是**数据缺失导致权重失效**——"
                 "和「这项配置不重要」完全是两件事，但榜单上看不出区别。")

    L.append("## 这套方法的边界")
    L.append("")
    L.append("1. **能配置化的只有「写进配置表的东西」**：激光雷达、800V、轴距、芯片型号。"
             "底盘调校、NVH、座椅舒适性、售后体验无法从参数推出，仍然只能人工判断。")
    L.append("2. **参数覆盖面是瓶颈**。这份 specs 里轴距、悬架形式、芯片型号大量缺失，"
             "缺失维度只能留在基线附近——`imputed` 列就是用来暴露这件事的，"
             "不能让「没填参数」变成「分数低」。")
    L.append("3. **value 维天然是相对的**：它按同榜价格中位数算，换一个榜就会变。"
             "所以它不能跨榜比较。")
    L.append("4. 建议的用法不是「用配置分替换人工分」，而是**用配置分给人工分做体检**："
             "差 0.5 以上就去看依据，确认是谁偏了。差得多的地方，往往就是人工打分里说不清的部分。")

    txt = "\n".join(L)
    print(txt)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(txt + "\n")
        print(f"\n[ok] {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
