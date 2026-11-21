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
import re
import statistics
import sys

DIMS = ["adas", "powertrain", "space", "control", "safety", "value", "cabin"]

# ---------------------------------------------------------------- 电芯能力分级
# 2026-11-22：电芯加分不再用硬编码三品牌白名单（宁德/弗迪/比亚迪 = 一线 +0.7，
# 其余一律二线 +0.2）。白名单把亿纬锂能这类头部企业系统性低估——
# 亿纬 2025 年圆柱电池出货量全国第一、大圆柱累计装车超 9 万台、宝马新世代首发
# 供应商；而宁德在大圆柱路线上布局相对滞后。「圆柱产线比宁德强」在圆柱路线上成立，
# 不能被一个静态白名单判死。
#
# 改法：判「电芯企业能力档位」+「该条技术路线上的档位」，取两者更高。
# 依据与来源全在 categories/cell_tier.json（可核验、可质疑）。
_CELL_TIER_CACHE: dict | None = None

TIER_ORDER = {"unknown": 0, "tier3": 1, "tier2": 2, "tier1": 3}


def _load_cell_tier() -> dict:
    global _CELL_TIER_CACHE
    if _CELL_TIER_CACHE is None:
        import paths
        p = str(paths.cat("cell_tier.json"))
        try:
            with open(p, encoding="utf-8") as f:
                _CELL_TIER_CACHE = json.load(f)
        except (OSError, ValueError):
            _CELL_TIER_CACHE = {"company_tier": {}, "route_rules": {},
                                "tier_points": {}, "form_hints": {}, "alias": {}}
    return _CELL_TIER_CACHE


def _norm_cell(s: str) -> str:
    return str(s or "").replace("（", "(").replace("）", ")").strip()


def cell_tier_of(cell_brand: str, route_hint: str | None = None) -> dict:
    """按「电芯企业能力 + 技术路线能力」判档位。

    cell_brand : 电芯品牌原文（可能是混装多家的组合，如「国轩高科/亿纬动力/中创新航」）
    route_hint : 已知的电池形态/体系线索（如「大圆柱」「磷酸铁锂」），可为空

    返回 {tier, points, text, brand, route}：
      tier   tier1 / tier2 / tier3 / unknown
      points 加分值（unknown = 0，且该车在该项不参与打分）
      text   展示文本（写明按哪条依据判的，可复核）
    """
    lib = _load_cell_tier()
    raw = _norm_cell(cell_brand)
    if not raw or "未披露" in raw or "待核" in raw:
        return {"tier": "unknown", "points": 0.0, "text": "", "brand": "", "route": ""}

    alias = lib.get("alias") or {}
    company = lib.get("company_tier") or {}
    # 混装（multi_batch）时取**各品牌中最高档**——混装不等于差，
    # 但也不能因为其中一家是头部就把整车当头部（取最高而非平均，见下）
    names = [n.strip() for n in re.split(r"[/、,，;；+＋\s]+", raw) if n.strip()]
    best_tier = "unknown"
    best_name = ""
    for n in names:
        key = n
        for a, target in alias.items():
            if a in n:
                key = target
                break
        rec = company.get(key)
        if not rec:
            # 别名兜底：品牌名里含已知企业名
            for cname in company:
                if cname in n:
                    rec = company[cname]
                    key = cname
                    break
        if not rec:
            continue
        t = rec.get("tier") or "unknown"
        if TIER_ORDER.get(t, 0) > TIER_ORDER.get(best_tier, 0):
            best_tier, best_name = t, key
    if best_tier == "unknown":
        # 有品牌但不在能力表里（小厂/新势力自研）→ unknown：不加分也不扣分
        return {"tier": "unknown", "points": 0.0,
                "text": f"电芯「{raw}」不在能力分级表内，不加分不扣分",
                "brand": raw, "route": ""}

    # 技术路线判定：显式 hint 优先，否则从品牌原文里的形态词推断
    route = ""
    rr = lib.get("route_rules") or {}
    hints = lib.get("form_hints") or {}
    probe = f"{route_hint or ''} {raw}"
    for word, r in hints.items():
        if word in probe and r in rr:
            route = r
            break
    # 路线档位：该路线第一梯队里有没有本车实际用的品牌
    route_tier = "unknown"
    if route:
        first = set((rr.get(route) or {}).get("tier1_brands") or [])
        for n in names:
            key = n
            for a, target in alias.items():
                if a in n:
                    key = target
                    break
            if key in first or n in first:
                route_tier = "tier1"
                break
    final = best_tier
    basis = f"{best_name} 企业档位 {best_tier}"
    if route and route_tier == "tier1" and TIER_ORDER[route_tier] > TIER_ORDER[best_tier]:
        final = route_tier
        basis = f"{best_name} 在「{route}」路线为头部（路线档位优先）"
    elif route:
        basis += f"（{route} 路线）"
    pts = (lib.get("tier_points") or {}).get(final, 0.0)
    label = {"tier1": "头部", "tier2": "主流", "tier3": "追赶"}.get(final, "")
    return {"tier": final, "points": float(pts),
            "text": f"{label}电芯 {basis} +{pts:g}",
            "brand": best_name, "route": route}


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
    ct = cell_tier_of(cell, s.get("cell_route"))
    if ct["tier"] != "unknown":
        parts.append(("电芯", ct["points"], ct["text"]))
    rg = _n(s.get("range_km"))
    if rg is not None:
        if rg >= 700:
            parts.append(("续航", 0.8, f"{rg:g}km +0.8"))
        elif rg >= 600:
            parts.append(("续航", 0.5, f"{rg:g}km +0.5"))
        elif rg < 500:
            parts.append(("续航", -0.5, f"{rg:g}km −0.5"))
    # 电池冷却方式（2026-11-22 拍板接入）：直冷是当前快充高倍率的主流方案，
    # 液冷是主流且够用，风冷基本退出。业内看快充能力必看冷却方式。
    cool = str(s.get("battery_cooling") or "")
    if "直冷" in cool:
        parts.append(("冷却", 0.3, "直冷 +0.3"))
    elif "液冷" in cool:
        parts.append(("冷却", 0.2, "液冷 +0.2"))
    elif cool:
        parts.append(("冷却", -0.2, f"{cool} −0.2"))
    return parts


def score_powertrain(s):
    """三电：补能速度 > 电池容量 > 电芯品牌。"""
    base, why = 6.0, []
    for _name, v, desc in powertrain_parts(s):
        base += v
        why.append(desc)
    return base, why


def control_parts(s):
    """操控各加减项的分解（规则唯一出处，见 powertrain_parts 的同口径说明）。

    2026-11-22 重大口径修正（用户举证：宝马 i3 用弹簧悬架，操控却比很多空气悬架车好）：
      原规则把「悬架形式」当**操控好坏**的证据——前双叉臂 +1.0、后多连杆 +0.5、
      后扭力梁 −0.6。问题是**悬架形式是结构配置，不是操控结论**：
        · 悬架形式好 ≠ 调校好（同一副双叉臂可以调成很软也可以很硬）
        · 悬架形式普通 ≠ 操控差（i3 前双球节弹簧减振支柱 + 后五连杆，
          麋鹿 75.7km/h；iX3 麋鹿 71km/h，与燃油 X3 仅差 1km/h，转向虚位 0.05rad）
      所以悬架形式**不再加分**，只保留「明显短板」的单向约束。

    改后的分工：
      · **悬架形式 = 结构上限约束**：只对明确短板减分（后扭力梁 −0.3，减半），
        好结构不再加分——这是「不因没查到就判差，也不因配置好就判好」；
      · **实测证据 = 操控维主证据**：麋鹿/侧倾/制动等可核验实测走
        experience_facts（independent_test 权重最高），由 experience_score 调整；
      · **驱动形式与零百 = 硬件口径**：保留（后驱/四驱、零百是明确的动力特性）。
    """
    parts = []
    fs = str(s.get("susp_front") or "")
    # 前悬架：好结构不加分（调校才是结论）；只在明显短板时提示
    if "扭力梁" in fs:
        parts.append(("前悬架", -0.3, "前扭力梁 −0.3（结构短板，非操控结论）"))
    rs = str(s.get("susp_rear") or "")
    if "扭力梁" in rs:
        # 后扭力梁是真实短板（后轴侧倾与循迹确实受影响），但只减半：
        # 调校得当仍可救回来，不能仅凭结构判差
        parts.append(("后悬架", -0.3, "后扭力梁 −0.3（结构短板，非操控结论）"))
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
    """操控：实测证据为主（走 experience_facts），硬件口径只给参考。

    诚实边界：**悬架形式不构成绩控结论**——同一副悬架调校差异巨大，
    更好的结构不等于更好的操控（宝马 i3/iX3 用弹簧悬架，麋鹿成绩与同级
    空气悬架车相当甚至更好）。操控维的加分依据是**可核验实测**。
    """
    base, why = 6.8, []
    for _name, v, desc in control_parts(s):
        base += v
        why.append(desc)
    return base, why


def space_parts(s):
    """空间维规则项分解（规则唯一出处）。

    **业内惯例 + 大众认知的拍板（2026-11-22）**：
    汽车之家/懂车帝的参数对比与第三方横评，都把「空间」拆成两块各自给分：
      ① **乘坐空间**——轴距（客观尺寸）+ 得房率/后排腿部（真实表现）
      ② **装载空间**——后备厢/前备箱容积
    家用 SUV/MPV 买家两者都看：「后排能坐几个人」和「能装几个行李箱」是两个
    不同的实用诉求，**都属于空间**。所以这里把轴距与后备厢容积作为**两个独立子项**
    分别进 space 维：
      · 不用容积去推断乘坐空间（那是错误因果——溜背造型容积大但乘坐空间一般）；
      · 也不把二者混成一个分，页面同时披露，读者自己区分；
      · 容积项幅度刻意小于轴距项：容积差异对"空间体验"的影响弱于轴距，
        且容积常由造型（溜背/掀背/尾门开口）决定，判据更软。

    轴距区间沿用 v9.x 起的标定；装载区间按家用车装载能力的常见分档：
      ≥800L 大型装载能力 / 600–800 充裕 / 450–600 够用 / <350 局促。
    """
    parts = []
    wb = _n(s.get("wheelbase_mm"))
    if wb is not None:
        if wb >= 3000:
            parts.append(("轴距", 1.6, f"轴距 {wb:g} +1.6"))
        elif wb >= 2900:
            parts.append(("轴距", 1.2, f"轴距 {wb:g} +1.2"))
        elif wb >= 2800:
            parts.append(("轴距", 0.6, f"轴距 {wb:g} +0.6"))
        elif wb < 2700:
            parts.append(("轴距", -1.0, f"轴距 {wb:g} −1.0"))
    tr = _n(s.get("trunk_l"))
    if tr is not None:
        if tr >= 800:
            parts.append(("装载", 0.5, f"后备厢 {tr:g}L +0.5"))
        elif tr >= 600:
            parts.append(("装载", 0.3, f"后备厢 {tr:g}L +0.3"))
        elif tr >= 450:
            parts.append(("装载", 0.1, f"后备厢 {tr:g}L +0.1"))
        elif tr < 350:
            parts.append(("装载", -0.3, f"后备厢 {tr:g}L −0.3"))
    seats = _n(s.get("seats"))
    if seats is not None and seats >= 6:
        parts.append(("座位", 0.4, "六座 +0.4"))
    return parts


def score_space(s):
    """空间：轴距（乘坐）+ 后备厢容积（装载）双判，座位数加成。"""
    base, why = 6.4, []
    for _name, v, desc in space_parts(s):
        base += v
        why.append(desc)
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


def selftest() -> int:
    """锁死两条口径（2026-11-22）：
      ① 电芯按「企业能力 × 技术路线」分级，不是品牌白名单；
      ② 悬架形式不加分（结构约束），好结构与普通结构同分。
    """
    # ① 电芯分级
    t1 = cell_tier_of("宁德时代")
    t2 = cell_tier_of("亿纬锂能", "大圆柱")
    t3 = cell_tier_of("苏州清陶")            # 不在表内 → unknown
    t4 = cell_tier_of("国轩高科/亿纬动力/中创新航")   # 混装取最高
    assert t1["tier"] == "tier1", f"宁德应 tier1: {t1}"
    assert t2["tier"] == "tier1", f"亿纬(圆柱)应 tier1: {t2}"
    assert t3["tier"] == "unknown", f"表外电芯应 unknown: {t3}"
    assert t3["points"] == 0.0, "表外电芯不应加分"
    assert t4["tier"] == "tier1", f"混装取最高应 tier1: {t4}"
    # 路线优先：非圆柱的亿纬仍是 tier1（企业档位），但文本要写明路线
    assert "圆柱" in t2["text"] or "亿纬" in t2["text"], f"路线信息应写进依据: {t2['text']}"
    # ② 悬架不加分：好结构 vs 普通结构，规则分必须相同
    good = control_parts({"susp_front": "前双叉臂", "susp_rear": "后五连杆",
                          "drive": "后置后驱", "zero_to_100_s": 5.1})
    normal = control_parts({"susp_front": "前麦弗逊", "susp_rear": "后多连杆",
                            "drive": "后置后驱", "zero_to_100_s": 5.1})
    assert sum(v for _, v, _ in good) == sum(v for _, v, _ in normal), \
        "悬架形式不应影响操控规则分（结构约束，不是操控结论）"
    # 真短板仍减分（减半）
    weak = control_parts({"susp_rear": "后扭力梁", "drive": "前置前驱"})
    w = sum(v for _, v, _ in weak)
    assert w < 0, "后扭力梁是真实短板，应减分"
    assert w >= -0.5, f"后扭力梁减分应减半（-0.3），实际 {w}"
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--specs", help="配置参数 json：{车型: {参数}}")
    ap.add_argument("--candidates")
    ap.add_argument("--selftest-only", dest="_selftest_only", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--only", help="只比对这些车型（逗号分隔）")
    a = ap.parse_args()
    if a.selftest:
        try:
            selftest()
            print("spec_score selftest OK")
            return 0
        except AssertionError as e:
            print(f"[FAIL] {e}")
            return 1

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
