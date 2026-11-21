#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ev_range_model.py —— 电动车实际续航物理估算模型（skill 单一事实源，v1.0）

用途（2026-11-20 用户要求沉淀）：
  别人问"某车实际能跑多少公里"时，结合物理计算 + 网上真实数据，给出一个合理续航；
  缺少实际续航/电耗数据的车型，用物理回归估算，不编造。

设计原则（遵守 skill 铁律：不许打补丁、改根因、回归测试）：
  - 本模块是物理模型的唯一事实源；页面生成器 build_energy_compare.py 从这里 import 常量，
    不再各自写一套系数（防止两处漂移）。
  - 每个系数都给出网搜证据来源，可被质疑、可被替换。
  - 真实达成率数据表 REAL_WORLD 用于校准/回归，模型输出与实测对照。

物理骨架：
  场景电耗 = 基准电耗 × 工况倍率 × 温度 × 驾驶 × 空调 × 胎压 × 载重 × 电压效率
  有效续航 = 电量(kWh) ÷ 场景电耗 × 100

  基准电耗 = 真实电耗(事实库) 或 物理回归(4.48×车重t + 5.88×CdA + 0.61)估算
  风阻功率 ∝ Cd×迎风面积×v²；高速段风阻占比剧增 → 用每车 CdA/吨 差异化高速倍率。

重要修正（v1.0 vs 旧 v5）：
  旧模型把"暖风"塞进温度系数(极寒1.6)又叠加空调系数 → 双重计算、冬季高估衰减。
  新模型拆分：温度只管"电池活性衰减"(物理，无空调约-10%~-20%)，
  暖风单独由空调档位负责(可关)，避免重复扣。依据：中国汽车报实测"-20℃无空调仅减10%"。

运行：
  python3 ev_range_model.py --selftest     # 模型自检 + 真实数据对照
  python3 ev_range_model.py --demo          # 演示几款车的估算
"""
from __future__ import annotations
import argparse
import json
import os

import paths

HERE = str(paths.ROOT)
# ============ 1. 基准电耗回归（缺真实电耗时用）============
# 39款有真实电耗+完整物理参数的车拟合，MAE 0.69 kWh/100km
REG_A, REG_B, REG_C = 4.48, 5.88, 0.61      # 电耗 = A×车重(t) + B×Cd×迎风面积 + C
AREA_FACTOR = 0.84                           # 迎风面积 ≈ 宽×高×0.84
MED_CDA_PER_TON = 0.314                      # 候选池 CdA/吨 中位数

# ============ 2. 工况倍率（速度）============
# 经验值(用户实测) + 懂车帝高速达成率校准：
#   市区(25km/h,含空调温和) 基准1.10；高速120km/h 基准1.70；CdA/吨偏离中位时差异化
CITY_BASE_MULT = 1.10
# 高速基准1.65：由懂车帝21车实测高速达成率均值~60%反推(1/0.604≈1.66)，
# 再结合用户经验值(轿车19-20/SUV21-22kWh)微调。旧版1.70偏高、达成率系统性低6个点。
HWY_BASE_MULT = 1.65
HWY_CDA_WEIGHT = 0.45        # CdA/吨 偏离中位数对高速倍率的影响权重

# ============ 3. 温度：仅电池活性（不含空调/暖风）============
# 依据：汽车之家2025冬测 + 中国汽车报"-20℃无空调仅减10%" → 纯电池效应温和
TEMP_BATTERY = {
    "above0": 1.00,     # >0℃  无衰减
    "m10to0": 1.05,     # -10~0℃ 约5%（低温活性下降）
    "m20tom10": 1.12,   # -20~-10℃ 约12%
    "underm20": 1.20,   # <-20℃ 约20%（极寒，电池本身，不含暖风）
}

# ============ 4. 空调/暖风（独立于温度，避免双重计算）============
# 依据：冬季暖风占总能耗20%+，热泵比PTC省50%；夏季制冷约10%
# 这里按"季节温度基线"给空调系数，用户档位在此基线上微调
AC_BASE = {"above0": 1.08, "m10to0": 1.12, "m20tom10": 1.18, "underm20": 1.22}  # 正常开空调的季节基线
AC_LEVEL = {"off": 0.90, "normal": 1.00, "heavy": 1.12}  # 用户档位相对基线

# ============ 5. 驾驶风格 ============
# 急加速/急刹比匀速能耗高~40%；ECO省8-12%
DRIVE_MULT = {"mild": 0.90, "normal": 1.00, "aggressive": 1.18}

# ============ 6. 轮胎 + 胎压（用户问的重点）============
# 依据(网搜多源一致)：
#   - 胎压每低0.1bar，滚阻+1.5%、续航损失1-2%
#   - 电车原厂推荐冷胎普遍 2.6-2.9bar(轿车) / 2.8-3.1bar(SUV)，高于油车2.5bar
#   - 低滚阻轮胎可提升续航5-8%
# 建模式取舍：默认按"厂家推荐胎压"(中性)，用户若知道实际胎压低再扣；
# 轮胎类型影响5-8%但逐车数据缺失(仅9款有文本)，故作为可选修正、默认中性不编造。
TIRE_PRESSURE_MULT = {
    "recommended": 1.00,   # 按原厂推荐（默认，中性）
    "low_01": 1.015,       # 低0.1bar（续航-1.5%）
    "low_03": 1.045,       # 低0.3bar（续航-4.5%）
    "low_05": 1.075,       # 低0.5bar（续航-7.5%）
}
RECOMMENDED_PSI_BAR = {"轿车": (2.6, 2.9), "SUV": (2.8, 3.1)}  # 参考，实际以B柱铭牌为准
TIRE_TYPE_MULT = {
    "unknown": 1.00,        # 未知（默认，不编造）
    "standard": 1.00,       # 普通胎
    "low_rolling": 0.95,    # 低滚阻胎（续航+5%，谨慎取值）
}

# ============ 7. 载重 ============
# 每增加100kg续航减少约5%；满载(多300-400kg)少30-50km
LOAD_MULT = {"light": 0.98, "normal": 1.00, "full": 1.06}

# ============ 8. 电压平台/碳化硅（影响小，主要影响充电速度）============
VOLTAGE_MULT = {400: 1.00, 600: 0.99, 800: 0.975, 900: 0.97}

# ============ 9. 电池预热/热泵（北方关键配置）============
# 热泵比PTC省约50%暖风电；电池预热使低温达成率+10-15%
THERMAL_BONUS = {"heatpump_preheat": 0.94, "none": 1.00}  # 冬季有热泵+预热时电耗×0.94

# ============ 真实续航达成率数据（网搜，用于校准/回归）============
# 达成率 = 实测续航 / CLTC。来源：懂车帝/汽车之家/易车/中国汽车报 2025-2026 实测
REAL_WORLD = [
    # (场景, 来源, 车型, 达成率)
    ("city_mild",   "2025-26实测排名", "小米YU7后驱", 0.939),
    ("city_mild",   "2025-26实测排名", "Model Y长续航", 0.904),
    ("city_mild",   "2025-26实测排名", "小米SU7后驱", 0.865),
    ("highway_120", "懂车帝21车高环", "Model Y后驱", 0.617),
    ("highway_120", "懂车帝21车高环", "小米YU7", 0.598),
    ("highway_120", "2025-26实测排名", "小鹏G6(800V)", 0.650),
    ("highway_120", "懂车帝21车高环", "小米SU7后驱", 0.549),
    ("winter",      "懂车帝牙克石冬测", "小鹏P7 Ultra四驱", 0.539),
    ("winter",      "2026冬测", "比亚迪汉EV", 0.691),
    ("winter",      "2026冬测", "小米SU7 Max", 0.51),
    ("winter",      "2026冬测", "极氪001千里版", 0.496),
]

# 场景键 → 模型参数映射（用于把 REAL_WORLD 的场景转成模型输入）
SCENE_MAP = {
    "city_mild":   dict(highway_ratio="under20", winter_temp="above0", drive_style="mild",   ac_usage="normal", load="normal"),
    "highway_120": dict(highway_ratio="over60",  winter_temp="above0", drive_style="normal", ac_usage="normal", load="normal"),
    "winter":      dict(highway_ratio="20to40",  winter_temp="underm20",  drive_style="normal", ac_usage="heavy",   load="normal", thermal="heatpump_preheat"),
}


def highway_mult(cda_per_ton=None, boxy=False, cd=None, area=None, weight=None):
    """高速倍率：按 CdA/吨 偏离中位数差异化；方盒子风阻大额外修正。"""
    r = cda_per_ton
    if boxy:
        if cd is not None and area is not None and weight:
            r = (cd * 1.15 * area) / (weight / 1000.0)
        else:
            r = MED_CDA_PER_TON * 1.5
    if r is None:
        r = MED_CDA_PER_TON
    m = HWY_BASE_MULT * (1 + HWY_CDA_WEIGHT * (r / MED_CDA_PER_TON - 1))
    return max(1.35, min(2.35, m))


def base_consumption(cons_real=None, weight=None, cd=None, area=None, body="轿车"):
    """基准电耗：真实优先，缺失用物理回归，再缺失用车身经验。返回(值, 来源)。"""
    if cons_real:
        try: return float(cons_real), "事实库真实电耗"
        except: pass
    if weight and cd and area:
        v = REG_A * (weight / 1000.0) + REG_B * cd * area + REG_C
        return round(v, 1), "物理回归估算"
    return (13.0 if body == "SUV" else 11.5), "车身经验估计"


def estimate(vehicle: dict, scenario: dict) -> dict:
    """
    估算实际续航。
      vehicle: {kwh, cons, weight, cd, area, voltage, cda_per_ton, swap, body, ...}
      scenario: {highway_ratio, winter_temp, drive_style, ac_usage,
                 tire_pressure, tire_type, load, thermal, boxy}
    返回 {range_km, scene_cons, steps[], method}
    """
    hw = scenario.get("highway_ratio", "20to40")
    hw_weight = {"under20": 0.0, "20to40": 0.25, "40to60": 0.7, "over60": 1.0}[hw]
    hm = highway_mult(vehicle.get("cda_per_ton"), scenario.get("boxy", False),
                      vehicle.get("cd"), vehicle.get("area"), vehicle.get("weight"))
    scene_m = CITY_BASE_MULT * (1 - hw_weight) + hm * hw_weight
    tmp = TEMP_BATTERY[scenario.get("winter_temp", "m10to0")]
    season = scenario.get("winter_temp", "m10to0")
    ac = AC_BASE[season] * AC_LEVEL[scenario.get("ac_usage", "normal")]
    drive = DRIVE_MULT[scenario.get("drive_style", "normal")]
    tire = TIRE_PRESSURE_MULT[scenario.get("tire_pressure", "recommended")]
    tire_type = TIRE_TYPE_MULT[scenario.get("tire_type", "unknown")]
    load = LOAD_MULT[scenario.get("load", "normal")]
    thermal = THERMAL_BONUS[scenario.get("thermal", "none")]
    volt = 1.0
    if vehicle.get("voltage"):
        vs = sorted(VOLTAGE_MULT)
        best = min(vs, key=lambda v: abs(v - vehicle["voltage"]))
        volt = VOLTAGE_MULT[best]

    cons, src = base_consumption(vehicle.get("cons"), vehicle.get("weight"),
                                 vehicle.get("cd"), vehicle.get("area"),
                                 vehicle.get("body", "轿车"))
    scene_cons = cons * scene_m * tmp * ac * drive * tire * tire_type * load * thermal * volt

    steps = [
        f"基准电耗 {cons} kWh/100km（{src}）",
        f"× 工况倍率 {scene_m:.2f}（高速占比{hw}，含CdA差异化）",
        f"× 温度 {tmp}（仅电池活性，不含暖风）",
        f"× 空调 {ac:.2f}（{season}季节基线×{scenario.get('ac_usage','normal')}档）",
        f"× 驾驶 {drive} × 胎压 {tire} × 轮胎 {tire_type} × 载重 {load} × 热管理 {thermal} × 电压 {volt}",
        f"= 场景电耗 {scene_cons:.1f} kWh/100km",
    ]
    kwh = vehicle.get("kwh")
    if not kwh:
        return {"range_km": None, "scene_cons": round(scene_cons, 1), "method": src,
                "steps": steps + ["无电量数据 → 不估算续航（不编造）"]}
    rng = round(kwh / scene_cons * 100)
    steps.append(f"电量 {kwh} kWh ÷ 场景电耗 ×100 = 有效续航 {rng} km")
    # 达成率（若给CLTC）
    ach = None
    if vehicle.get("cltc"):
        ach = round(rng / vehicle["cltc"], 3)
        steps.append(f"相对CLTC {vehicle['cltc']}km 达成率 {ach*100:.0f}%")
    return {"range_km": rng, "scene_cons": round(scene_cons, 1),
            "method": src, "achievement": ach, "steps": steps}


def validate_real():
    """用真实达成率数据校验模型：模型对每种场景给出中位车估算达成率，与实测对照。"""
    # 用一辆典型车（无电压/热泵按各场景需要）跑模型，取达成率
    typical = {"cons": 12.0, "kwh": 65, "cltc": 560, "cda_per_ton": MED_CDA_PER_TON,
               "body": "轿车", "voltage": 800}
    out = []
    for scene, params in SCENE_MAP.items():
        r = estimate(typical, params)
        model_ach = r["achievement"]
        reals = [x[3] for x in REAL_WORLD if x[0] == scene]
        real_mid = sum(reals) / len(reals) if reals else None
        delta = round((model_ach - real_mid) * 100, 1) if real_mid else None
        out.append({"scene": scene, "model_ach": model_ach,
                    "real_min": round(min(reals), 3) if reals else None,
                    "real_max": round(max(reals), 3) if reals else None,
                    "real_mean": round(real_mid, 3) if real_mid else None,
                    "delta_pct_point": delta})
    return out


def selftest():
    print("=== ev_range_model 自检 ===")
    # 1) 温度不含暖风：极寒电池系数应温和（<=1.25）
    assert TEMP_BATTERY["underm20"] <= 1.25, "温度系数过高（可能又把暖风算进去了）"
    # 2) 空调基线随温度递增（冬季暖风更费）
    assert AC_BASE["above0"] < AC_BASE["underm20"], "空调基线未体现冬季暖风更费"
    # 3) 胎压每低0.1bar约+1.5%电耗
    assert abs((TIRE_PRESSURE_MULT["low_01"] - 1) - 0.015) < 1e-6
    # 4) 回归公式刻度合理（中位车电耗11-15）
    v, _ = base_consumption(None, 1800, 0.23, 2.4)
    assert 10 <= v <= 15, f"回归电耗异常: {v}"
    # 5) 真实数据对照
    val = validate_real()
    for row in val:
        print("  ", row)
    # 模型达成率应在实测区间附近（±12个百分点内）
    for row in val:
        lo = row["real_min"] * 100 - 12
        hi = row["real_max"] * 100 + 12
        m = row["model_ach"] * 100
        assert lo <= m <= hi, f"{row['scene']} 模型达成率{m}% 偏离实测[{lo:.0f},{hi:.0f}]"
    print("  模型 vs 实测：全部场景在合理区间内")
    print("SELFTEST OK")


def demo():
    cars = [
        ("深蓝L06", {"model": "深蓝L06", "cons": 11.5, "kwh": 56.1, "cltc": 560,
                     "cda_per_ton": 0.305, "body": "轿车", "voltage": None}),
        ("蔚来ES6(换电)", {"model": "蔚来ES6", "cons": 15.3, "kwh": 100, "cltc": 650,
                          "cda_per_ton": 0.305, "body": "SUV", "voltage": None}),
    ]
    scenes = {
        "市区常温": dict(highway_ratio="under20", winter_temp="above0", drive_style="mild", ac_usage="normal"),
        "高速120": dict(highway_ratio="over60", winter_temp="above0", drive_style="normal", ac_usage="normal"),
        "东北冬季": dict(highway_ratio="20to40", winter_temp="underm20", drive_style="normal",
                         ac_usage="normal", thermal="heatpump_preheat"),
    }
    for name, car in cars:
        print(f"\n=== {name} ===")
        for sname, sc in scenes.items():
            r = estimate(car, sc)
            print(f"  {sname}: 有效续航 {r['range_km']}km, 达成率 {r.get('achievement')}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="电动车实际续航物理模型")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
    elif a.demo:
        demo()
    else:
        selftest()
