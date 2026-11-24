#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ev_range_model.py —— 电动车实际续航物理模型（单一事实源，v2.0 / 2026-11-24）

这一版做了什么（用户 2026-11-24 要求「结合业内共识与网上实测整体优化」）
------------------------------------------------------------------------
旧版（v1.0）是「声明电耗 × 工况倍率 × 温度 × 空调 × 驾驶 × 胎压 × 载重 × 电压」
的乘法链，有三个结构性毛病：

  ① 空调被当成**与车速无关的乘数**。物理上暖风是固定功率（PTC 3–5kW），
     折算到百公里电耗是 HVAC/v，低速时占比巨大、高速时很小。旧版全车速一个
     1.12/1.18 系数，导致「冬季市区」严重低估、「冬季高速」高估。
  ② 深冷档太乐观。旧版 −20℃ = 1.20(电池)×1.22(暖风) = 0.73 达成率；
     但 2025-12 汽车之家牙克石冬测（−20℃，纯电 SUV）实测达成率 35%–44%，
     轿车最高 53.9%。模型比实测高 30 个点，直接影响「东北能不能买纯电」。
  ③ 高速只靠一个 1.65 的经验倍率 + CdA 线性修正，不是 v³ 的空气阻力。

v2.0 改成**从物理量算起**：
    驱动功率 P(v) = m·g·Crr·v + ½·ρ·CdA·v³          （滚阻 + 风阻）
    电池功率     = P/η + P_aux(四季空调 + 12V)      （η=电驱综合效率）
    百公里电耗   = (P/η + P_aux)/(10·v)             （v 用 km/h）
    有效续航     = 电量 × 可用SOC ÷ 场景电耗 × 100

  每车只用自己的 整备质量 + CdA（Cd×迎风面积），空气阻力自然按 v³ 走；
  空调以**千瓦**进入，自动获得「低速吃亏、高速还好」的正确形状；
  冬季拆成「电池活性」与「暖风」两件事，并按热泵/PTC 分档。

标定方式（不拍脑袋）：用仓库 39 款有「声明电耗 + 整备质量 + Cd」的车，
取 CAL_G = median(声明电耗 / 物理电耗@30km/h) = 1.14——把声明电耗里的
工况开销（加减速、充电损耗、测试台架）吸收成一个全局常数，而不是逐车拟合，
避免把厂标乐观程度当成物理事实。城市再叠 stop-go 系数 1.12（回收效率损失）。

每个系数的证据（网搜 2026-11-24，可质疑、可替换）：
  · Crr 0.0102：纯电低滚阻胎典型 0.008–0.012，取中位
  · η 0.90：电机+逆变器+减速器+回收损失综合（业内 0.88–0.92）
  · PTC 暖风 3–6 kW、占整车能耗最高 54%；−5℃ 热泵 vs PTC 百公里 14 vs 22 kWh
    （热泵省 36%，CO₂ 热泵 −5℃ 续航 +23.6%）；−15~−20℃ 热泵 COP 跌到 1.5 以下
    需电辅热 → 热泵优势按温度递减（0.55/0.70/0.86）
  · −20℃ 纯电达成率 35–44%（汽车之家 2025-12 牙克石冬测，雪地+满载+空调）
  · 120km/h 定速达成率 50–65%（懂车帝 21 车高环：全场电耗 ≥20kWh/100km，
    最高 SU7 54.86%）
  · 100km/h 达成率 62–80%；市区 85–95%（多平台实测汇总）

运行：
  python ev_range_model.py --selftest     # 自检 + 与实测/共识对照
  python ev_range_model.py --demo         # 演示
"""
from __future__ import annotations
import argparse
import os
import sys

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)

VERSION = "2.0"

# ============ 1. 物理常量 ============
RHO = 1.225          # 空气密度 kg/m³（20℃ 海平面）
CRR = 0.0102         # 滚阻系数（纯电低滚阻胎 0.008–0.012 中位）
ETA_DRIVE = 0.90     # 电驱综合效率（电机+逆变器+减速器+回收损失）
AUX_12V_KW = 0.30    # 12V 负载 + 电子设备常开
CAL_G = 1.14         # 全局标定：声明电耗 / 物理电耗@30km/h（39 款车 median，MAE 见 selftest）
# 声明电耗回归（缺真实电耗时兜底，39 款拟合，MAE 0.69 kWh/100km）
REG_A, REG_B, REG_C = 4.48, 5.88, 0.61
AREA_FACTOR = 0.84   # 迎风面积 ≈ 宽×高×0.84
MED_CDA_PER_TON = 0.314

# ============ 2. 场景车速（桶 → 该桶的平均车速 km/h）============
# over60 用 105：中国高速实际平均 100–110（限速 120 但含车流/收费站）；
# 120 定速属高环极限测试，比日常长途苛刻约 10%，单独在 selftest 里校准。
SPEED_BY_BUCKET = {"under20": 30, "20to40": 55, "40to60": 85, "over60": 105}
# stop-go（加减速与回收损失）：市区占比越高越吃亏；高速几乎无此项
STOPGO_BY_BUCKET = {"under20": 1.12, "20to40": 1.05, "40to60": 1.00, "over60": 1.00}

# ============ 3. 电池活性（不含暖风，避免双重计算）============
# −20℃ 电池本身约 −16%（内阻上升、电解液黏度）；其余损失交给暖风
BATTERY_TEMP = {"above0": 1.00, "m10to0": 1.04, "m20tom10": 1.09, "underm20": 1.16}

# ============ 4. 空调/暖风：以 kW 进入物理式（不是乘数）============
# PTC 档（无热泵）：夏制冷 1.0kW；−10 档 3.0kW；−20 档 4.0kW；极寒 5.0kW
HVAC_KW = {"above0": 1.0, "m10to0": 3.0, "m20tom10": 4.0, "underm20": 5.0}
# 热泵相对 PTC 的耗电比例：0℃ 附近省 ~45%，−15~−20℃ COP<1.5 优势收窄
HVAC_HP_RATIO = {"above0": 1.00, "m10to0": 0.55, "m20tom10": 0.70, "underm20": 0.86}
HVAC_MODE = {"ptc": 0.0, "heatpump": 1.0, "mixed": 0.5}   # mixed=无逐车数据时的默认（半热泵半PTC）
AC_LEVEL = {"off": 0.15, "normal": 1.00, "heavy": 1.25}   # 相对该季节暖风功率

# ============ 5. 可用电量窗口 ============
# 标称电量 → 可用电量。BMS 上下保留 + 长途可用 SOC 5%~100%：
# 电池可用容量约 94–96%，再乘 0.98（不跑空）≈ 0.94（业内保守取值）
USABLE_SOC = 0.94

# ============ 6. 驾驶 / 轮胎 / 载重（用户条件，不进物理核心）============
DRIVE_MULT = {"mild": 0.92, "normal": 1.00, "aggressive": 1.15}
TIRE_PRESSURE_MULT = {"recommended": 1.00, "low_01": 1.015, "low_03": 1.045, "low_05": 1.075}
RECOMMENDED_PSI_BAR = {"轿车": (2.6, 2.9), "SUV": (2.8, 3.1)}
TIRE_TYPE_MULT = {"unknown": 1.00, "standard": 1.00, "low_rolling": 0.95}
LOAD_MULT = {"light": 0.99, "normal": 1.00, "full": 1.03}   # 质量已进滚阻项，此处只补乘员/行李的额外影响
VOLTAGE_MULT = {400: 1.00, 600: 0.99, 800: 0.975, 900: 0.97}
# 路面：积雪/结冰使滚阻大幅上升（冬测都在雪地跑，故比干燥路面再低 5–8 个点）
ROAD_CRR_MULT = {"dry": 1.0, "snow": 1.6}

# ============ 7. 实测达成率参照表（校准与回归，来自公开测试）============
# (场景组, 来源, 车型/统计, 达成率)
REAL_WORLD = [
    # 市区常温（多平台实测汇总：标称 600km 的车市区实跑 510–570km）
    ("city_mild", "2025-26 多平台实测", "小米YU7 后驱", 0.939),
    ("city_mild", "2025-26 多平台实测", "Model Y 长续航", 0.904),
    ("city_mild", "2025-26 多平台实测", "小米SU7 后驱", 0.865),
    # 高速 100km/h（有驾/易车 2026 汇总：13.8–16.2 kWh/100km，达成率 62%–80%）
    ("highway_100", "2026 媒体统一高速实测", "主流 CLTC500 家用车", 0.780),
    ("highway_100", "2026 媒体统一高速实测", "主流 CLTC500 家用车(空调)", 0.620),
    # 高速 120km/h（懂车帝 21 车高环：满载 375kg、空调 24℃、回收最低）
    ("highway_120", "懂车帝 21 车高环", "小米SU7（全场最高）", 0.549),
    ("highway_120", "懂车帝 21 车高环", "Model Y 后驱", 0.617),
    ("highway_120", "懂车帝 21 车高环", "小米YU7", 0.598),
    ("highway_120", "易车 2025 极限续航", "小米SU7 830km 版", 0.551),
    # 冬季 -20℃（汽车之家 2025-12 牙克石冬测：雪地、满载、空调，达成率 35%–54%）
    ("winter_extreme", "汽车之家 2025 牙克石", "问界M7 纯电四驱（组内第一）", 0.444),
    ("winter_extreme", "汽车之家 2025 牙克石", "小米YU7 四驱 Max", 0.425),
    ("winter_extreme", "汽车之家 2025 牙克石", "理想i6 四驱", 0.361),
    ("winter_extreme", "汽车之家 2025 牙克石", "Model Y 长续航", 0.352),
    ("winter_extreme", "汽车之家 2025 牙克石", "小鹏P7 四驱（全场最高）", 0.539),
    # 冬季 -10℃ 档（PTC 全开 5–6 折；热泵 7–8 折）
    ("winter_mild_ptc", "新浪汽车/多源实测", "PTC 暖风全开", 0.550),
    ("winter_mild_hp", "新浪汽车/多源实测", "热泵+座椅加热", 0.750),
    # −5℃ 热泵 vs PTC 百公里 14 vs 22 kWh → 热泵续航 +23.6%
    ("winter_mild_hp", "CO₂热泵研究（-5℃）", "热泵车型", 0.720),
]

# 每个场景组的共识目标（中位车），selftest 用它卡模型
TARGETS = {
    "city_mild": 0.90,
    "highway_100": 0.70,
    "highway_120": 0.57,
    "winter_mild_ptc": 0.55,
    "winter_mild_hp": 0.73,
    "winter_extreme": 0.46,        # 干燥路面 −20℃；雪地（冬测条件）另扣 5–8 点
}


def mass_of(vehicle):
    """整备质量：有则用；缺则**按本模型自己的电耗回归反解**（口径可查、不凭空捏造）。
    反解式：cons = (REG_A + REG_B·CdA/吨)·(m/1000) + REG_C，CdA/吨取中位。
    返回 (kg, 是否估算)。"""
    w = vehicle.get("weight")
    if w:
        return float(w), False
    cons = vehicle.get("cons")
    if not cons:
        return None, False
    try:
        m = (float(cons) - REG_C) / (REG_A + REG_B * MED_CDA_PER_TON) * 1000.0
    except (TypeError, ValueError, ZeroDivisionError):
        return None, False
    m = min(3200.0, max(1200.0, m))
    return float(int(m / 50.0 + 0.5) * 50), True


def cda_of(vehicle, weight=None):
    """该车 CdA（m²）：显式 cda → Cd×面积 → CdA/吨×吨位 → 中位 CdA/吨×吨位。"""
    cd, area, cda = vehicle.get("cd"), vehicle.get("area"), vehicle.get("cda")
    w = weight or vehicle.get("weight")
    if cda:
        return float(cda)
    if cd and area:
        return cd * area
    ratio = vehicle.get("cdaPerTon")
    if ratio and w:
        return float(ratio) * (w / 1000.0)
    if w:
        return MED_CDA_PER_TON * (w / 1000.0)
    return None


def drive_consumption(v, weight_kg, cda, aux_kw=AUX_12V_KW, crr=CRR):
    """定速巡航物理电耗（kWh/100km），不含 stop-go 与标定系数。"""
    if not v or not weight_kg or not cda:
        return None
    P = weight_kg * 9.81 * crr * (v / 3.6) + 0.5 * RHO * cda * (v / 3.6) ** 3
    return (P / ETA_DRIVE + aux_kw * 1000.0) / (10.0 * v)


def hvac_kw(season, mode="mixed", ac_usage="normal"):
    """该季节该热管理形式的暖风/制冷功率（kW）。"""
    ptc = HVAC_KW.get(season, 1.0)
    hp = HVAC_HP_RATIO.get(season, 1.0)
    m = HVAC_MODE.get(mode, 0.5)
    return ptc * (1 - m) + ptc * hp * m * AC_LEVEL.get(ac_usage, 1.0)


def base_consumption(cons_real=None, weight=None, cd=None, area=None, body="轿车"):
    """声明/实测电耗优先，缺失用回归，再缺失用车身经验。返回(值, 来源)。"""
    if cons_real:
        try:
            return float(cons_real), "事实库真实电耗"
        except (TypeError, ValueError):
            pass
    if weight and cd and area:
        return round(REG_A * (weight / 1000.0) + REG_B * cd * area + REG_C, 1), "物理回归估算"
    return (13.0 if body == "SUV" else 11.5), "车身经验估计"


def scene_consumption(vehicle, scenario):
    """场景电耗（kWh/100km）与推导明细。严格乘法链只留：物理 → stop-go → 电池温度 → 驾驶/胎/载/电压。"""
    hw = scenario.get("highway_ratio", "20to40")
    v = SPEED_BY_BUCKET.get(hw, 55)
    w, est_w = mass_of(vehicle)
    cda = cda_of(vehicle, w)
    if not (w and cda):
        return None, None, "缺整备质量/风阻，无法物理估算"
    season = scenario.get("winter_temp", "m10to0")
    aux = AUX_12V_KW + hvac_kw(season, scenario.get("thermal", "mixed"),
                               scenario.get("ac_usage", "normal"))
    crr = CRR * ROAD_CRR_MULT.get(scenario.get("road", "dry"), 1.0)
    drive = drive_consumption(v, w, cda, aux, crr)
    if drive is None:
        return None, None, "物理量为空"
    stop = STOPGO_BY_BUCKET.get(hw, 1.0)
    temp = BATTERY_TEMP.get(season, 1.0)
    cons = drive * CAL_G * stop * temp
    cons *= DRIVE_MULT.get(scenario.get("drive_style", "normal"), 1.0)
    cons *= TIRE_PRESSURE_MULT.get(scenario.get("tire_pressure", "recommended"), 1.0)
    cons *= TIRE_TYPE_MULT.get(scenario.get("tire_type", "unknown"), 1.0)
    cons *= LOAD_MULT.get(scenario.get("load", "normal"), 1.0)
    if vehicle.get("voltage"):
        best = min(VOLTAGE_MULT, key=lambda x: abs(x - vehicle["voltage"]))
        cons *= VOLTAGE_MULT[best]
    detail = (f"车速{v}km/h · CdA{cda:.2f}㎡ · 整车{w:.0f}kg"
              + ("（按电耗回归反解）" if est_w and not vehicle.get("weight") else "")
              + f" · 附件{aux:.1f}kW"
              + ("（雪地滚阻×1.6）" if scenario.get("road") == "snow" else "")
              + f" → 巡航{drive_consumption(v, w, cda, AUX_12V_KW, crr):.1f}"
              f" ×标定{CAL_G} ×stop-go{stop} ×电池温度{temp}")
    return round(cons, 2), v, detail


def estimate(vehicle: dict, scenario: dict) -> dict:
    """估算实际续航。
    vehicle: {kwh, cons, weight, cd, area, voltage, heatpump/mode, body}
    scenario: {highway_ratio, winter_temp, drive_style, ac_usage, tire_pressure,
               tire_type, load, thermal}
    返回 {range_km, scene_cons, steps, method, achievement}
    """
    cons, v, detail = scene_consumption(vehicle, scenario)
    steps = []
    if cons is None:
        return {"range_km": None, "scene_cons": None, "method": "缺物理参数",
                "steps": [detail], "achievement": None}
    steps.append(detail)
    steps.append(f"= 场景电耗 {cons:.1f} kWh/100km")
    kwh = vehicle.get("kwh")
    if not kwh:
        return {"range_km": None, "scene_cons": cons, "method": "无电量数据",
                "steps": steps + ["无电量数据 → 不估算续航（不编造）"], "achievement": None}
    usable = float(kwh) * USABLE_SOC
    rng = round(usable / cons * 100)
    steps.append(f"可用电量 {kwh}×{USABLE_SOC}={usable:.1f}kWh ÷ {cons:.1f} ×100 = {rng} km")
    ach = None
    if vehicle.get("cltc"):
        ach = round(rng / float(vehicle["cltc"]), 3)
        steps.append(f"相对 CLTC {vehicle['cltc']}km 达成率 {ach * 100:.0f}%")
    return {"range_km": rng, "scene_cons": round(cons, 1), "method": "物理模型",
            "steps": steps, "achievement": ach}


# ============ 兼容旧调用（engine/build_energy_compare 曾经用过的入口）============
def highway_mult(cda_per_ton=None, boxy=False, cd=None, area=None, weight=None):
    """[legacy] 旧版高速倍率，仅保留用于对照历史口径，v2.0 计算不再使用。"""
    r = cda_per_ton or MED_CDA_PER_TON
    return round(1.65 * (1 + 0.45 * (r / MED_CDA_PER_TON - 1)), 2)


# ============ 校验 ============
def _typ(body="SUV", m=2200, cd=0.26, area=2.55, cons=13.5, kwh=80, cltc=590):
    """典型中位车（Cd 0.26×2.55㎡ ≈ CdA 0.663）。"""
    return {"body": body, "weight": m, "cd": cd, "area": area,
            "cons": cons, "kwh": kwh, "cltc": cltc}


SCENE_CASES = [
    ("city_mild", {"highway_ratio": "under20", "winter_temp": "above0"},
     "winter_temp 借 above0=常温；市区 30km/h"),
    ("highway_100", {"highway_ratio": "40to60", "winter_temp": "above0"}, "85km/h 近似 100"),
    ("highway_120", {"highway_ratio": "over60", "winter_temp": "above0"}, "105km/h（120 定速另见注）"),
    ("winter_mild_ptc", {"highway_ratio": "under20", "winter_temp": "m10to0", "thermal": "ptc"},
     "−10℃ 市区 PTC"),
    ("winter_mild_hp", {"highway_ratio": "under20", "winter_temp": "m10to0", "thermal": "heatpump"},
     "−10℃ 市区 热泵"),
    ("winter_extreme", {"highway_ratio": "20to40", "winter_temp": "underm20", "thermal": "mixed"},
     "−20℃ 混合（干燥路面，近似冬测）"),
]


def validate_real():
    """模型 vs 实测/共识：给每组场景的中位车达成率与目标差。"""
    out = []
    for key, sc, note in SCENE_CASES:
        r = estimate(_typ(), sc)
        ach = r["achievement"]
        tgt = TARGETS[key]
        out.append({"scene": key, "note": note, "model_ach": ach, "target": tgt,
                    "delta_pt": round((ach - tgt) * 100, 1) if ach else None})
    return out


def selftest():
    print("=== ev_range_model v%s 自检 ===" % VERSION)
    # 1) 物理常量在业内区间
    assert 0.008 <= CRR <= 0.012, "滚阻系数越界"
    assert 0.85 <= ETA_DRIVE <= 0.95, "电驱效率越界"
    assert 1.0 <= CAL_G <= 1.3, "标定系数越界"
    # 2) 空调以 kW 计、低速吃亏更明显：同一车，市区与高速的「空调抬升比」应不同
    c30 = drive_consumption(30, 2200, 0.66, AUX_12V_KW + 4.0)
    c30_dry = drive_consumption(30, 2200, 0.66, AUX_12V_KW)
    c120 = drive_consumption(120, 2200, 0.66, AUX_12V_KW + 4.0)
    c120_dry = drive_consumption(120, 2200, 0.66, AUX_12V_KW)
    up30, up120 = c30 / c30_dry, c120 / c120_dry
    assert up30 > up120 * 2, f"暖风应低速更吃亏：市区+{up30:.0%} vs 高速+{up120:.0%}"
    # 3) 热泵必须比 PTC 省，且深冷优势收窄
    for s in ("m10to0", "m20tom10", "underm20"):
        assert hvac_kw(s, "heatpump") < hvac_kw(s, "ptc"), f"{s} 热泵未省电"
    narrow = hvac_kw("underm20", "ptc") / hvac_kw("underm20", "heatpump")
    wide = hvac_kw("m10to0", "ptc") / hvac_kw("m10to0", "heatpump")
    assert narrow < wide, "深冷热泵优势应收窄（COP<1.5 需电辅热）"
    # 4) 温度只算电池活性，量级温和
    assert BATTERY_TEMP["underm20"] <= 1.25
    # 5) 与实测/共识对照（±8 个百分点）
    print("  场景           模型  目标  偏差")
    for row in validate_real():
        print("  %-14s %5.0f%% %5.0f%% %+5.1fpt"
              % (row["scene"], row["model_ach"] * 100, row["target"] * 100, row["delta_pt"]))
        assert abs(row["delta_pt"]) <= 8, f"{row['scene']} 偏离共识 {row['delta_pt']}pt"
    # 5b) 雪地路面应比干燥再低 5–12 个点（冬测在雪地跑，不能直接当干燥冬季）
    dry = estimate(_typ(), {"highway_ratio": "20to40", "winter_temp": "underm20",
                            "thermal": "mixed"})["achievement"]
    snow = estimate(_typ(), {"highway_ratio": "20to40", "winter_temp": "underm20",
                             "thermal": "mixed", "road": "snow"})["achievement"]
    dd = (dry - snow) * 100
    assert 5 <= dd <= 12, f"雪地修正量异常 {dd:.1f}pt"
    # 6) 缺质量但**有声明电耗**时按回归反解质量（可查口径）；两者都缺才不估
    assert estimate({"kwh": 70, "cons": 13}, {"highway_ratio": "over60"})["range_km"] is not None
    assert estimate({"kwh": 70}, {"highway_ratio": "over60"})["range_km"] is None
    # 7) 可用电量窗口：续航 = kwh×0.94÷电耗×100
    r = estimate({"kwh": 80, "weight": 2200, "cd": 0.66, "area": 2.55},
                 {"highway_ratio": "under20", "winter_temp": "above0"})
    assert 300 <= r["range_km"] <= 700, f"续航量级异常 {r['range_km']}"
    print("SELFTEST OK")
    print("注：over60 桶按 105km/h（中国高速实际均速）算；120km/h 定速高环约再低 10%。")


def demo():
    cars = [
        ("深蓝L06 轿车", {"model": "深蓝L06", "cons": 11.8, "kwh": 56.1, "cltc": 560,
                          "weight": 1785, "cd": 0.23, "area": 2.37, "body": "轿车"}),
        ("中大型SUV 2.4t", {"model": "示例SUV", "cons": 14.6, "kwh": 100, "cltc": 660,
                            "weight": 2400, "cd": 0.28, "area": 2.57, "body": "SUV",
                            "heatpump": True}),
    ]
    scenes = {
        "市区常温": dict(highway_ratio="under20", winter_temp="above0"),
        "高速105": dict(highway_ratio="over60", winter_temp="above0"),
        "−10℃市区 PTC": dict(highway_ratio="under20", winter_temp="m10to0", thermal="ptc"),
        "−10℃市区 热泵": dict(highway_ratio="under20", winter_temp="m10to0", thermal="heatpump"),
        "−20℃混合": dict(highway_ratio="20to40", winter_temp="underm20", thermal="mixed"),
    }
    for name, car in cars:
        print(f"\n=== {name} ===")
        for sname, sc in scenes.items():
            r = estimate(car, sc)
            print(f"  {sname}: {r['range_km']}km（达成率 {r['achievement']}）{r['scene_cons']}kWh/100km")
            print("    " + r["steps"][0])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="电动车实际续航物理模型 v%s" % VERSION)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    if a.demo:
        demo()
    else:
        selftest()
