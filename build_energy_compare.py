# -*- coding: utf-8 -*-
"""
build_energy_compare.py
生成「选车适配调节器」自包含 HTML 页面（产品力主榜 + 用车条件场景调节）。

【打分逻辑 v2 —— 2026-11-20 用户确认重构】
  车型综合分 = 产品力主分(现有8维加权, 75~90) + 场景调节分(用车条件, 封顶约±10)
  排名以综合分降序。用车条件只对具体车型做加减分，不再单独算"能源形式适配榜"。
  核心示例：单次需500km、某纯电有效续航仅400km → 续航不足项扣分。

数据源（只读，不修改）：
  - categories/master_pool.json  车型 / 级别 / 车身 / 能源 / 价格 / 8维产品力分
  - categories/trims.json        逐配置续航(cltc_km) / 电量 / 电芯

物理续航模型（v5，透明展示于页面）：
  基准电耗 = 事实库真实电耗，缺失时用物理回归(4.48×车重t + 5.88×Cd×迎风面积 + 0.61)估算
  场景电耗 = 基准电耗 × 工况倍率(高速按每车CdA/吨差异化) × 温度 × 驾驶风格 × 空调 × 电压平台
  有效续航 = 电量 ÷ 场景电耗 × 100；仅纯电(BEV)计算，油车/插混/增程有油箱兜底。
  物理参数(整备质量/风阻Cd/尺寸/电压/换电)采集自汽车之家参数页，存于 categories/vehicle_physics.json。
  硬条件(无桩纯电/极寒低价纯电/短持有/长途续航不足等)大扣分+降档+标注，换电车型区别对待。

遵守数据铁律：不为凑齐能源形式而编造车型；车型榜只展示候选池实际存在的车，
并在页面显式说明"燃油/油混/增程当前无逐配置数据入池"；无续航数据的车不编造续航、不参与续航调节。
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# 物理模型单一事实源：从 ev_range_model import，杜绝两处系数漂移（不打补丁原则）
import ev_range_model as ERM
MP = os.path.join(HERE, "categories", "master_pool.json")
TR = os.path.join(HERE, "categories", "trims.json")
FL = os.path.join(HERE, "categories", "fact_library.json")  # 真实电耗/电量
VP = os.path.join(HERE, "categories", "vehicle_physics.json")  # 物理参数库(采集)
OUT = os.path.join(HERE, "energy_compare.html")

# ===== 物理续航模型：全部从 ev_range_model 单一事实源 import =====
# 严禁在本文件重定义这些系数——两处漂移正是"打补丁"的典型症状。
PHYS_A, PHYS_B, PHYS_C = ERM.REG_A, ERM.REG_B, ERM.REG_C
AREA_FACTOR = ERM.AREA_FACTOR
BASE_CDA_PER_TON = ERM.MED_CDA_PER_TON
HWY_BASE_MULT = ERM.HWY_BASE_MULT
CITY_BASE_MULT = ERM.CITY_BASE_MULT
# 兼容旧变量名（页面 JS 用 temp/drive/ac 键名）：温度只管电池，空调按季节另算
TEMP_CONS = ERM.TEMP_BATTERY          # 仅电池活性，不含暖风（修正旧版双重计算）
DRIVE_MULT = ERM.DRIVE_MULT
AC_MULT = ERM.AC_LEVEL                # 用户档位；季节基线由 acSeason 注入页面
VOLTAGE_MULT = ERM.VOLTAGE_MULT

# 换电场景优势（据网搜共识）：无家充/北方极寒/短持有/长途
SWAP_ADVANTAGE = {
    "no_charger": {"gain": 2.5, "reason": "换电3分钟补能、接近加油，无家充痛点大幅缓解"},
    "short_hold": {"gain": 1.5, "reason": "车电分离、无电池衰减与残值焦虑，二手更保值"},
    "cold": {"gain": 1.5, "reason": "换电站电池恒温15-30℃，冬季补能不挨冻、不受低温影响"},
    "longtrip": {"gain": 1.0, "reason": "高速换电网络补能快（依赖站点覆盖）"},
}

# 统一的 8 维产品力框架（能源中性）：powertrain 由"三电"改为"动力总成"
# 每维对不同能源形式评价不同内容，但共用同一套 0-10 分刻度 × 权重 ×10 = 百分制
DIMS = [
    {"key": "powertrain", "label": "动力总成", "weight": 0.18,
     "by_power": {"BEV": "三电：电池/电机/电控", "PHEV": "三电+发动机直驱", "EREV": "电机驱动+增程器发电",
                  "HEV": "三大件：发动机+变速箱+底盘", "ICE": "三大件：发动机+变速箱+底盘"}},
    {"key": "safety",     "label": "安全",     "weight": 0.162,
     "by_power": {"BEV": "碰撞+主动安全", "PHEV": "碰撞+主动安全", "EREV": "碰撞+主动安全",
                  "HEV": "碰撞+主动安全", "ICE": "碰撞+主动安全"}},
    {"key": "adas",       "label": "智驾",     "weight": 0.135,
     "by_power": {"BEV": "辅助驾驶硬件与功能", "PHEV": "辅助驾驶硬件与功能", "EREV": "辅助驾驶硬件与功能",
                  "HEV": "辅助驾驶硬件与功能", "ICE": "辅助驾驶硬件与功能"}},
    {"key": "space",      "label": "空间",     "weight": 0.126,
     "by_power": {"BEV": "乘坐+储物", "PHEV": "乘坐+储物", "EREV": "乘坐+储物",
                  "HEV": "乘坐+储物", "ICE": "乘坐+储物"}},
    {"key": "value",      "label": "性价比",   "weight": 0.117,
     "by_power": {"BEV": "配置/价格", "PHEV": "配置/价格", "EREV": "配置/价格",
                  "HEV": "配置/价格", "ICE": "配置/价格"}},
    {"key": "quality",    "label": "质量品控", "weight": 0.10,
     "by_power": {"BEV": "品控/投诉", "PHEV": "品控/投诉", "EREV": "品控/投诉",
                  "HEV": "可靠性/投诉", "ICE": "可靠性/投诉"}},
    {"key": "control",    "label": "操控",     "weight": 0.09,
     "by_power": {"BEV": "底盘+转向", "PHEV": "底盘+转向", "EREV": "底盘+转向",
                  "HEV": "底盘+转向", "ICE": "三大件之底盘调校"}},
    {"key": "cabin",      "label": "车机座舱", "weight": 0.09,
     "by_power": {"BEV": "车机+座舱", "PHEV": "车机+座舱", "EREV": "车机+座舱",
                  "HEV": "座舱", "ICE": "座舱"}},
]
WEIGHT_MAP = {d["key"]: d["weight"] for d in DIMS}

POWERS = ["ICE", "HEV", "PHEV", "EREV", "BEV"]
POWER_META = {
    "ICE":  {"name": "燃油车",   "short": "燃油", "color": "#8a6d3b", "desc": "纯汽油/柴油发动机，补能最快"},
    "HEV":  {"name": "油电混动", "short": "油混", "color": "#5a8a6a", "desc": "不插电，发动机为主、电机省油"},
    "PHEV": {"name": "插电混动", "short": "插混", "color": "#c8442e", "desc": "可插电、发动机可直驱，油电双修"},
    "EREV": {"name": "增程式",   "short": "增程", "color": "#b07d10", "desc": "电机驱动、发动机只发电，高速油耗高"},
    "BEV":  {"name": "纯电动",   "short": "纯电", "color": "#1f6f8b", "desc": "只用电，家充成本最低，冬季/高速续航打折"},
}

# 用车条件下拉框定义（此处只承载文案与默认值；调节分值在页面 JS 的 computeAdjust 中按具体车型计算）
FACTORS = {
    "operation": {
        "label": "是否营运", "default": "no",
        "desc": "营运车辆（网约/出租）以纯电为主，运营成本是核心",
        "options": [
            {"v": "no",  "label": "非营运（家用为主）", "hint": "按家用场景综合权衡"},
            {"v": "yes", "label": "营运（网约/出租）",  "hint": "高频行驶+成本敏感，纯电车型加分、燃油/油混减分"},
        ],
    },
    "winter_temp": {
        "label": "冬季最低气温", "default": "m10to0",
        "desc": "用于计算纯电有效续航：−10℃约衰减20–45%，−20℃可达40–60%",
        "options": [
            {"v": "above0",  "label": "0℃以上（华南）",        "hint": "温度系数0.95，纯电几乎无冬季衰减"},
            {"v": "m10to0",  "label": "-10~0℃（江淮/长三角）", "hint": "温度系数0.85（默认）"},
            {"v": "m20tom10","label": "-20~-10℃（华北/山东）", "hint": "温度系数0.68，纯电衰减明显"},
            {"v": "underm20","label": "-20℃以下（东北/内蒙）", "hint": "温度系数0.50，纯电续航可能腰斩"},
        ],
    },
    "annual_km": {
        "label": "年行驶里程", "default": "15to25",
        "desc": "成本平衡点约0.83–1.5万公里：低于此油车省心，高于此电车省钱",
        "options": [
            {"v": "under10", "label": "1万公里以下",     "hint": "跑得少，纯电小幅减分、油车加分"},
            {"v": "10to15",  "label": "1万~1.5万公里",   "hint": "接近平衡点，不调节"},
            {"v": "15to25",  "label": "1.5万~2.5万公里", "hint": "超过平衡点（默认）"},
            {"v": "over25",  "label": "2.5万公里以上",   "hint": "高里程，纯电省钱优势加分"},
        ],
    },
    "single_trip": {
        "label": "单次最长连续里程", "default": "300to500",
        "desc": "核心调节项：需求里程超过纯电有效续航时，按缺口扣分",
        "options": [
            {"v": "under150", "label": "150km 以下",   "hint": "需求约100km，纯电普遍胜任"},
            {"v": "150to300", "label": "150~300km",    "hint": "需求约220km"},
            {"v": "300to500", "label": "300~500km",    "hint": "需求约400km（默认）"},
            {"v": "over500",  "label": "500km 以上",   "hint": "需求约600km，纯电续航不足将明显扣分"},
        ],
    },
    "long_trip_freq": {
        "label": "长途频率（该里程一年几次）", "default": "few",
        "desc": "长途次数多时，纯电补能时间成本高，可油可电更灵活",
        "options": [
            {"v": "rare",    "label": "几乎没有",        "hint": "纯电小幅加分"},
            {"v": "few",     "label": "一年 1~5 次",     "hint": "偶发长途（默认）"},
            {"v": "monthly", "label": "一年 6~20 次",    "hint": "较频繁，纯电减分、油/混动加分"},
            {"v": "weekly",  "label": "每月都有/更频繁", "hint": "高频长途，纯电明显减分"},
        ],
    },
    "home_charger": {
        "label": "充电条件", "default": "home",
        "desc": "家充是电车经济性的前提；无稳定充电时插混长期亏电油耗会恶化",
        "options": [
            {"v": "home",   "label": "有家用充电桩",       "hint": "纯电/插混/增程加分（默认）"},
            {"v": "public", "label": "无家充但有稳定公充", "hint": "不额外调节"},
            {"v": "none",   "label": "完全没有固定充电",   "hint": "纯电大幅减分，插混/增程减分，油车/油混加分"},
        ],
    },
    "plate_limit": {
        "label": "是否限牌城市", "default": "no",
        "desc": "限牌城市新能源可直接上牌，燃油/油混需摇号或拍牌",
        "options": [
            {"v": "no",  "label": "不限牌", "hint": "不调节（默认）"},
            {"v": "yes", "label": "限牌城市", "hint": "新能源加分、燃油/油混减分"},
        ],
    },
    "hold_years": {
        "label": "计划持有年限", "default": "5to8",
        "desc": "持有越久，残值影响越摊薄，电车的能源节省越明显",
        "options": [
            {"v": "under3", "label": "3 年内换车", "hint": "纯电折旧快，减分；油车加分"},
            {"v": "3to5",   "label": "3~5 年",     "hint": "不调节"},
            {"v": "5to8",   "label": "5~8 年",     "hint": "默认"},
            {"v": "over8",  "label": "8 年以上",   "hint": "纯电加分"},
        ],
    },
    "highway_ratio": {
        "label": "高速工况占比", "default": "20to40",
        "desc": "增程高速油耗最高（油→电→轮二次损耗）；插混直驱高速效率更优。高速还会降低纯电有效续航",
        "options": [
            {"v": "under20", "label": "20% 以下（市区为主）", "hint": "高速系数1.0，纯电小幅加分"},
            {"v": "20to40",  "label": "20%~40%",             "hint": "高速系数0.92（默认）"},
            {"v": "40to60",  "label": "40%~60%",             "hint": "高速系数0.82，增程减分"},
            {"v": "over60",  "label": "60% 以上（常年高速）", "hint": "高速系数0.72，增程明显减分、纯电续航打折"},
        ],
    },
    "boxy": {
        "label": "方盒子/风味越野造型", "default": "no",
        "desc": "如钛7这类承载式方盒子：车重风阻大，高速能耗显著上升",
        "options": [
            {"v": "no",  "label": "普通造型", "hint": "按实际风阻参数计算（默认）"},
            {"v": "yes", "label": "看重方盒子/风味越野", "hint": "按方盒子修正能耗，叠加高频高速时混动意义打折"},
        ],
    },
    "drive_style": {
        "label": "驾驶风格", "default": "normal",
        "desc": "暴力驾驶（急加速/高速）显著增加能耗，CLTC理想工况未计入",
        "options": [
            {"v": "mild",       "label": "温和（省电驾驶）",  "hint": "能耗 ×0.92"},
            {"v": "normal",     "label": "正常",             "hint": "能耗 ×1.0（默认）"},
            {"v": "aggressive", "label": "暴力（经常急加速）", "hint": "能耗 ×1.15"},
        ],
    },
    "ac_usage": {
        "label": "空调使用", "default": "normal",
        "desc": "CLTC理想工况空调负载低；实际开空调（尤其冬夏）会增加能耗",
        "options": [
            {"v": "off",    "label": "很少开空调",   "hint": "能耗 ×0.97"},
            {"v": "normal", "label": "正常开空调",   "hint": "能耗 ×1.0（默认）"},
            {"v": "heavy",  "label": "空调常开/开大风", "hint": "能耗 ×1.10"},
        ],
    },
}


def norm(s):
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


def load_cars():
    mp = json.load(open(MP, encoding="utf-8"))
    tr = json.load(open(TR, encoding="utf-8"))
    fl = json.load(open(FL, encoding="utf-8"))  # 真实电耗库
    vpdoc = json.load(open(VP, encoding="utf-8"))  # 物理参数库(采集)
    wm = WEIGHT_MAP  # 统一维度框架（数值与原榜单权重一致）
    trim_by_model = {}
    # 事实库按归一化车名建索引
    fl_by_model = {}
    for k, v in fl.items():
        if isinstance(v, dict):
            fl_by_model.setdefault(norm(k), v)
    # 物理参数库索引
    vp_by_model = {norm(x["model"]): x for x in vpdoc["cars"]}
    for e in tr["entries"]:
        m = e.get("model")
        if m:
            trim_by_model.setdefault(norm(m), []).append(e)

    cars = []
    for c in mp["cars"]:
        t = c.get("tags") or {}
        sc = c.get("scores") or {}
        power = t.get("power")
        if power not in ("BEV", "PHEV", "EREV", "HEV", "ICE"):
            continue
        product = sum(float(sc.get(k, 0)) * float(wm[k]) for k in wm) * 10.0
        cltc_vals, kwh_vals, cells = [], [], []
        for e in trim_by_model.get(norm(c["model"]), []):
            for trm in e.get("trims", []):
                if trm.get("cltc_km"):
                    try: cltc_vals.append(float(trm["cltc_km"]))
                    except: pass
                if trm.get("battery_kwh"):
                    try: kwh_vals.append(float(trm["battery_kwh"]))
                    except: pass
                if trm.get("cell_supplier"):
                    cells.append(trm["cell_supplier"])
        cand = c.get("candidate") or {}
        guide = cand.get("price_guide") or []
        # 真实电耗（事实库优先，配置库电量兜底）
        fe = fl_by_model.get(norm(c["model"]), {})
        cons = None
        if fe.get("consumption"):
            try: cons = float(fe["consumption"])
            except: pass
        kwh = None
        if fe.get("kwh"):
            try: kwh = float(fe["kwh"])
            except: pass
        if kwh is None and kwh_vals:
            kwh = max(kwh_vals)
        cltc = None
        if fe.get("cltc"):
            try: cltc = int(float(fe["cltc"]))
            except: pass
        if cltc is None and cltc_vals:
            cltc = int(max(cltc_vals))
        # 物理参数（采集库）
        pv = vp_by_model.get(norm(c["model"]), {})
        # 电量/CLTC 兜底：事实库/配置库缺失时用二次采集的补采数据
        if kwh is None and pv.get("kwh"):
            try: kwh = float(pv["kwh"])
            except: pass
        if cltc is None and pv.get("cltc"):
            try: cltc = int(float(pv["cltc"]))
            except: pass
        weight = pv.get("weight_kg") or None
        cd = pv.get("cd") or None
        L, W, H = pv.get("length_mm"), pv.get("width_mm"), pv.get("height_mm")
        area = round((W/1000)*(H/1000)*AREA_FACTOR, 2) if (W and H) else None
        voltage = pv.get("voltage_v") or None
        swap = pv.get("swap") if pv.get("swap") is not None else None
        # 判定换电（采集库/事实库文本兜底）
        cell_txt = str((c.get("facts") or {}).get("cell_brand") or "")
        if swap is None:
            swap = True if ("换电" in cell_txt) else None
        # 基准电耗：真实优先，缺失用物理回归估算，再缺失用车身经验
        cons_src = "事实库" if cons else None
        if cons is None and weight and cd and area:
            cons = PHYS_A*(weight/1000.0) + PHYS_B*cd*area + PHYS_C
            cons = round(cons, 1)
            cons_src = "物理估算"
        if cons is None:
            cons = 13.0 if t.get("body") == "SUV" else 11.5
            cons_src = "车身经验"
        # CdA/吨比（用于高速倍率差异化）
        cda_per_ton = round(cd*area/(weight/1000.0), 3) if (cd and area and weight) else None
        cars.append({
            "model": c["model"],
            "brand": t.get("brand", ""),
            "cls": t.get("cls", ""),
            "body": t.get("body", ""),
            "power": power,
            "price": t.get("price_wan"),
            "priceLow": guide[0] if len(guide) > 0 else None,
            "priceHigh": guide[1] if len(guide) > 1 else None,
            "product": round(product, 1),
            "cltc": cltc,
            "kwh": round(kwh, 1) if kwh else None,
            "cons": round(cons, 1),
            "consSrc": cons_src,
            "weight": weight, "cd": cd, "area": area,
            "voltage": voltage, "swap": swap, "cdaPerTon": cda_per_ton,
            "cell": (c.get("facts") or {}).get("cell_brand") or (cells[0] if cells else None),
            "sales": (c.get("facts") or {}).get("trend_sales"),
        })
    cars.sort(key=lambda x: -x["product"])
    return cars


def js(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def build(cars_override=None, out=None, extra_head="", extra_tail="", title=None):
    """生成交互比较器页面。默认无参数=原行为（整池67款，向后兼容）。
    cars_override: 外部注入的车列表（含补采分），用于特定榜单。
    extra_head/extra_tail: 注入到 <body> 前 / </body> 前的额外HTML（正式榜导航/区块）。
    """
    cars = cars_override if cars_override is not None else load_cars()
    html = HTML_TEMPLATE
    if title:
        html = html.replace("<title>选车适配调节器 · 产品力主榜 + 用车条件修正</title>",
                            f"<title>{title}</title>")
    reps = {
        "__EXTRA_HEAD__": extra_head,
        "__EXTRA_TAIL__": extra_tail,
        "__CARS_JSON__": js(cars),
        "__FACTORS_JSON__": js(FACTORS),
        "__DIMS_JSON__": js(DIMS),
        "__WEIGHTMAP_JSON__": js(WEIGHT_MAP),
        "__POWERS_JSON__": js(POWERS),
        "__POWERMETA_JSON__": js(POWER_META),
        "__PHYS_JSON__": js({"a":PHYS_A,"b":PHYS_B,"c":PHYS_C,"areaFactor":AREA_FACTOR,
                             "medRatio":BASE_CDA_PER_TON,"hwyBase":HWY_BASE_MULT,"cityBase":CITY_BASE_MULT,
                             "temp":TEMP_CONS,"drive":DRIVE_MULT,"ac":AC_MULT,
                             "acSeason":ERM.AC_BASE,
                             "volt":VOLTAGE_MULT,"swapAdv":SWAP_ADVANTAGE}),
        "__N_CARS__": str(len(cars)),
        "__N_CLTC__": str(sum(1 for c in cars if c["cltc"])),
        "__N_PHYS__": str(sum(1 for c in cars if c.get("weight") and c.get("cd"))),
    }
    for k, v in reps.items():
        html = html.replace(k, v)
    dest = out or OUT
    with open(dest, "w", encoding="utf-8") as f:
        f.write(html)
    print("wrote:", dest, len(html), "chars,", len(cars), "cars")
    from collections import Counter
    print("power dist:", dict(Counter(c["power"] for c in cars)))
    print("with consumption:", sum(1 for c in cars if c["cons"]), "/ with kwh:", sum(1 for c in cars if c["kwh"]))
    print("with physics(weight+cd):", sum(1 for c in cars if c.get("weight") and c.get("cd")))
    print("swap cars:", [c["model"] for c in cars if c.get("swap")])


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>选车适配调节器 · 产品力主榜 + 用车条件修正</title>
<style>
:root{
  --bg:#f7f6f3; --card:#ffffff; --ink:#15181c; --sub:#5b6470; --line:#e6e3dc;
  --accent:#c8442e; --accent2:#1f6f8b; --ok:#1a7f37; --warn:#b07d10; --bad:#b42318;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:"PingFang SC","Microsoft YaHei","Noto Sans SC",system-ui,sans-serif;
  font-size:14px;line-height:1.65;-webkit-font-smoothing:antialiased}
.wrap{max-width:1320px;margin:0 auto;padding:28px 22px 60px}
header h1{margin:0 0 6px;font-size:26px;letter-spacing:.5px}
header h1 em{color:var(--accent);font-style:normal}
header .sub{color:var(--sub);font-size:14px;max-width:980px}
.note{margin-top:14px;background:#eef4f7;border-left:4px solid var(--accent2);
  padding:10px 14px;border-radius:6px;color:#274455;font-size:12.5px}
.note.warn{background:#fbf3e6;border-left-color:var(--warn);color:#7a5710}
.note b{color:#123}
.layout{display:grid;grid-template-columns:340px 1fr;gap:20px;margin-top:20px;align-items:start}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:18px;box-shadow:0 1px 3px rgba(0,0,0,.03)}
.panel h2{margin:0 0 4px;font-size:17px}
.panel .hint{color:var(--sub);font-size:12px;margin:0 0 14px}
.cond-panel{position:sticky;top:20px}
.fg{margin-bottom:13px}
.fg label{display:block;font-weight:600;font-size:13px;margin-bottom:5px}
.fg label .w{float:right;color:var(--sub);font-weight:400;font-size:11px}
.fg select,.fg input{width:100%;padding:8px 10px;border:1px solid #cfd4da;border-radius:7px;
  background:#fff;color:var(--ink);font-size:13px;font-family:inherit}
.fg .fh{font-size:11px;color:var(--sub);margin-top:4px;line-height:1.4}
.fg-row{display:flex;gap:10px}
.fg-row .fg{flex:1}
.def-tag{display:inline-block;background:#eef4f7;color:var(--accent2);border-radius:4px;
  font-size:10px;padding:0 5px;margin-left:5px;font-weight:400}
.btn{width:100%;padding:10px;background:var(--accent);color:#fff;border:0;border-radius:8px;
  font-size:14px;font-weight:600;cursor:pointer;margin-top:4px}
.btn.ghost{background:#fff;color:var(--accent);border:1px solid var(--accent);margin-top:8px}
.section-title{font-size:15px;font-weight:700;margin:2px 0 12px;display:flex;align-items:center;gap:8px}
.section-title .bar{width:4px;height:16px;background:var(--accent);border-radius:2px}
.summary-box{background:#fff;border:1px solid var(--line);border-left:4px solid var(--accent);
  border-radius:8px;padding:12px 14px;font-size:13px}
.summary-box b{color:var(--accent)}
.summary-box .formula{display:inline-block;background:#f7f5f1;border-radius:5px;padding:2px 8px;
  font-size:11.5px;color:var(--sub);margin-top:6px}
table{width:100%;border-collapse:collapse;font-size:12.5px}
th,td{padding:8px 8px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--sub);font-weight:600;font-size:11.5px;background:#faf9f6;position:sticky;top:0;cursor:pointer;z-index:2}
th.sorted{color:var(--accent)}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.pill{display:inline-block;padding:1px 7px;border-radius:10px;color:#fff;font-size:10.5px}
.tbl-scroll{max-height:600px;overflow:auto;border:1px solid var(--line);border-radius:10px;background:#fff}
.cf-note{font-size:11.5px;color:var(--sub);margin:0 0 10px}
.adj-pos{color:var(--ok);font-weight:600} .adj-neg{color:var(--bad);font-weight:600} .adj-zero{color:#999}
details.adj{display:inline}
details.adj summary{cursor:pointer;outline:none;text-decoration:underline dotted;color:var(--accent2)}
.adjbox{font-size:11px;color:var(--sub);background:#faf9f6;border:1px solid var(--line);border-radius:6px;
  padding:8px 10px;margin-top:6px;white-space:normal;min-width:230px;line-height:1.7}
.adjbox .r{display:flex;justify-content:space-between;gap:14px;padding:1px 0}
.adjbox .cap{color:var(--warn);border-top:1px dashed var(--line);margin-top:4px;padding-top:4px}
.method{margin-top:22px;background:#fff;border:1px solid var(--line);border-radius:12px;padding:18px;font-size:12px;color:var(--sub)}
.method h2{color:var(--ink);font-size:15px;margin:0 0 10px}
.method code{background:#f4f2ee;padding:1px 5px;border-radius:4px;font-size:11px}
.method .kv{display:inline-block;background:#f7f5f1;border-radius:5px;padding:1px 7px;margin:1px 3px 1px 0;font-size:11px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.dim-table{width:100%;border-collapse:collapse;font-size:11.5px;margin-top:8px}
.dim-table th,.dim-table td{padding:6px 8px;border-bottom:1px solid var(--line);white-space:normal;position:static;cursor:default}
.dim-table th{background:#faf9f6}
.dim-table .bev{color:#1f6f8b}.dim-table .oil{color:#8a6d3b}
.manual-panel{margin-top:20px}
.mform{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:10px}
.mform .mf label{display:block;font-size:11px;font-weight:600;color:var(--sub);margin-bottom:3px}
.mform .mf input,.mform .mf select{width:100%;padding:6px 8px;border:1px solid #cfd4da;border-radius:6px;font-size:12px;font-family:inherit}
.mform .mf .dimrow{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin-top:8px}
.mform .mf .dimrow .d label{font-size:10px;color:var(--sub)}
.mform .mf .dimrow .d input{padding:4px 6px}
.manual-list{margin-top:12px}
.manual-list table{font-size:12px}
.tier-badge{display:inline-block;border-radius:4px;font-size:10px;padding:1px 6px;font-weight:600}
.tier-badge.t1{background:#e7f4ea;color:#1a7f37}
.tier-badge.t2{background:#fbf3e6;color:#9a6700}
.tier-badge.t3{background:#fbe9e7;color:#b42318}
@media(max-width:1000px){.layout{grid-template-columns:1fr}.cond-panel{position:static}.two{grid-template-columns:1fr}.mform{grid-template-columns:1fr 1fr}}
</style>
</head>
<body>
__EXTRA_HEAD__
<div class="wrap">
<header>
  <h1>选车适配<em>调节器</em> · 产品力主榜 + 用车条件修正</h1>
  <div class="sub">车好不好，主要看 8 维产品力分（<b>统一维度：电车评三电、油车评三大件，同一把尺子</b>）；你的用车条件只做加减分修正。例如"单次要开 500km、这车有效续航仅 400km"，就在续航不足项扣分。改动条件即时重排车型。</div>
  <div class="note"><b>核心公式：</b>车型综合分 = 产品力主分（统一 8 维加权，75–90 分）+ 场景调节（软调节封顶±10）+ <b>硬条件惩罚（全额计入、降档、标注）</b>。第 1 维"动力总成"对电车=三电、油车=三大件、混动=两者，共用 18% 权重。<b>有效续航用物理模型</b>：基准电耗（真实数据优先，缺失用 4.48×车重+5.88×CdA 回归估算）× 工况倍率（风阻大的车高速更费）× 温度 × 驾驶风格 × 空调 × 电压平台，电量÷场景电耗×100。物理参数（整备质量/风阻/电压/换电）采集自汽车之家参数页。<b>换电车型</b>在无桩/极寒/短持有场景有优势。点续航或调节分数可看完整算式。所有数据内嵌本页，离线可用。</div>
  <div class="note warn" id="dataNote"></div>
</header>

<div class="layout">
  <div class="panel cond-panel">
    <h2>用车条件</h2>
    <p class="hint">改动任意一项即时重排；带"默认"标记的是人群常用值。</p>
    <div id="conds"></div>
    <div class="fg" style="margin-top:16px;border-top:1px solid var(--line);padding-top:14px">
      <label>车型过滤 · 预算区间（万元）</label>
      <div class="fg-row">
        <input type="number" id="pLow" value="12" step="0.5" min="0">
        <input type="number" id="pHigh" value="14" step="0.5" min="0">
      </div>
      <div class="fh">默认 12–14 万（示例：12–14 万轿车）</div>
    </div>
    <div class="fg">
      <label>车身类型</label>
      <select id="bodyFilter">
        <option value="轿车" selected>轿车</option>
        <option value="SUV">SUV</option>
        <option value="ALL">全部</option>
      </select>
    </div>
    <div class="fg">
      <label>场景调节力度 <span id="wLabel">100%</span></label>
      <input type="range" id="wEnergy" min="0" max="150" value="100" style="padding:0">
      <div class="fh">仅缩放软调节。0%=只看产品力；100%=标准（默认）；150%=软调节影响更强。硬条件惩罚不受此滑块影响、始终全额计入。</div>
    </div>
    <button class="btn" onclick="render()">重新计算</button>
    <button class="btn ghost" onclick="resetAll()">恢复默认条件</button>
  </div>

  <div>
    <div class="panel">
      <div class="section-title"><span class="bar"></span>车型综合榜 <span style="font-size:11px;color:var(--sub);font-weight:400" id="carCount"></span></div>
      <div class="summary-box" id="summary"></div>
      <p class="cf-note" style="margin:12px 0 8px">点"场景调节"列的分数可展开该车逐项加减分；点表头可排序。<b>综合分 = 产品力 + 场景调节</b>。</p>
      <div class="tbl-scroll">
        <table id="carTable">
          <thead><tr>
            <th data-k="rank">#</th><th data-k="model">车型</th><th data-k="tier">档位</th><th data-k="cls">级别</th>
            <th data-k="power">能源</th><th data-k="price" class="num">价格(万)</th>
            <th data-k="cltc" class="num">CLTC(km)</th><th data-k="cons" class="num">电耗</th><th data-k="eff" class="num">有效续航▾</th>
            <th data-k="product" class="num">产品力</th><th data-k="adj" class="num">场景调节</th>
            <th data-k="composite" class="num sorted">综合分</th>
          </tr></thead>
          <tbody id="carBody"></tbody>
        </table>
      </div>
    </div>

    <div class="panel" style="margin-top:20px">
      <div class="section-title"><span class="bar"></span>统一打分维度（电车三电 / 油车三大件，同一把尺子）</div>
      <p class="cf-note">8 维框架对能源中性：各维度在所有能源形式上用同一套 0–10 分刻度 × 权重 ×10 = 百分制产品力分。<b>第一维"动力总成"</b>是统一的关键——电车评三电、油车评三大件、混动两者兼顾，共用 18% 权重，从而可同台比较。</p>
      <table class="dim-table"><thead><tr><th>统一维度</th><th>权重</th><th>电车怎么评</th><th>油车怎么评</th></tr></thead>
        <tbody id="dimBody"></tbody></table>
    </div>

    <div class="method">
      <h2>方法、默认值与数据边界</h2>
      <div class="two">
        <div>
          <p><b>打分逻辑：</b>综合分 = 产品力主分 + 软调节（封顶±10）+ 硬条件惩罚（全额、不封顶）。产品力用统一 8 维框架（动力总成18%/安全16.2%/智驾13.5%/空间12.6%/性价比11.7%/质量品控10%/操控9%/车机座舱9%）×10。硬条件触发时扣大分并<b>降一档</b>（T1→T2→T3）、车型旁标红警示：无桩纯电(−15)、极寒+低价纯电(−12)、短持有纯电(−8)、长途续航不足(−10)、限牌油车(−8)、营运油车(−10)。<b>换电车型</b>相反：无桩/极寒/短持有/长途场景给正分（据网搜共识：3分钟补能、电池恒温、车电分离保值）。</p>
          <p><b>物理续航模型（透明可核）：</b>基准电耗 = 事实库真实电耗（51款），缺失用回归 <code>4.48×车重(t) + 5.88×Cd×迎风面积 + 0.61</code> 估算（39款拟合、MAE 0.69）。迎风面积≈宽×高×0.84。<code>场景电耗 = 基准 × 工况倍率 × 温度 × 驾驶 × 空调 × 电压</code>，<code>有效续航 = 电量 ÷ 场景电耗 ×100</code>。工况倍率：市区1.04、高速基准1.70并按每车 <b>CdA/吨</b> 偏离中位数(0.314)差异化（风阻大高速更费，解释方盒子效应）；温度&gt;0℃1.0/−10℃1.08/−20℃1.28/极寒1.6；驾驶温和0.92/暴力1.15；空调0.97–1.10；电压800V·SiC约0.975（影响小，主要影响充电速度）。经验值：轿车市区12/高速19.5、SUV市区13/高速21.5、方盒子高速24 kWh/100km，实测校验高速达成率约51–63%（懂车帝实测55%吻合）。</p>
        </div>
        <div>
          <p><b>其余调节项：</b>长途频率高→纯电减分、油/混动加分；无家充→纯电大幅减分、插混/增程减分、油车加分；营运→纯电加分；限牌→新能源加分；常年高速→增程明显减分、纯电续航打折；方盒子叠加高频高速→增程/纯电/插混减分；短持有→纯电减分；高里程→纯电加分。插混/增程因有油箱兜底，不扣"里程不足"分，但增程高速油耗高会体现为减分。</p>
          <p><b>默认值依据：</b>年里程 1.5–2.5 万（超过成本平衡点 0.83–1.5 万）；冬季 −10~0℃；单次 300–500km、一年 1–5 次；有家充；不限牌；持有 5–8 年；高速 20–40%；非营运。为人群常见画像，可按实际修改。</p>
          <p><b>数据边界：</b>候选池 __N_CARS__ 款车（其中 __N_CLTC__ 款有续航数据、__N_PHYS__ 款有完整物理参数），当前仅收录纯电(BEV)与插混(PHEV)的<b>官方核验</b>逐配置数据；燃油/油混/增程无数据入池——这是数据现状而非"更差"，不编造车型。物理参数（整备质量/风阻Cd/长宽高/电压/换电）由汽车之家参数页采集，来源逐车可查（见 vehicle_physics.json），采集不到的字段留空、用回归估算并在算式里标注。无电量数据的车不估算续航。</p>
        </div>
      </div>
    </div>
  </div>
</div>
</div>

<script>
const CARS=__CARS_JSON__;
const FACTORS=__FACTORS_JSON__;
const DIMS=__DIMS_JSON__;
const WM=__WEIGHTMAP_JSON__;
const PHYS=__PHYS_JSON__;
const POWERS=__POWERS_JSON__;
const PM=__POWERMETA_JSON__;
const POOL_POWERS=[...new Set(CARS.map(c=>c.power))];

(function(){
  const missing=POWERS.filter(p=>!POOL_POWERS.includes(p));
  const nm=missing.map(p=>PM[p].name).join('、');
  document.getElementById('dataNote').innerHTML='候选池现状：共 '+CARS.length+' 款车，有逐配置数据的能源形式为 <b>'+
    POOL_POWERS.map(p=>PM[p].name).join('、')+'</b>；'+
    (missing.length? '<b>'+nm+'</b> 暂无车型数据入池（不编造车型）。':'');
})();

// ===== 统一维度说明表 =====
(function(){
  const tb=document.getElementById('dimBody');
  tb.innerHTML=DIMS.map(d=>{
    const bev=d.by_power.BEV, oil=d.by_power.ICE;
    return '<tr><td><b>'+d.label+'</b>'+(d.key==='powertrain'?' <span style="color:#c8442e;font-size:10px">统一关键维</span>':'')+'</td>'+
      '<td>'+Math.round(d.weight*100)+'%</td>'+
      '<td class="bev">'+bev+'</td><td class="oil">'+oil+'</td></tr>';
  }).join('');
})();

function buildConds(){
  const box=document.getElementById('conds'); box.innerHTML='';
  for(const [fid,f] of Object.entries(FACTORS)){
    const d=document.createElement('div'); d.className='fg';
    const defOpt=f.options.find(o=>o.v===f.default);
    d.innerHTML='<label>'+f.label+'<span class="w"><span class="def-tag">默认</span></span></label>'+
      '<select id="c_'+fid+'">'+f.options.map(o=>'<option value="'+o.v+'"'+(o.v===f.default?' selected':'')+'>'+o.label+'</option>').join('')+'</select>'+
      '<div class="fh">'+f.desc+'<br><span style="color:#8a929b">当前：'+defOpt.hint+'</span></div>';
    d.querySelector('select').addEventListener('change',()=>{
      const sel=d.querySelector('select');
      const o=f.options.find(o=>o.v===sel.value);
      d.querySelector('.fh span').textContent='当前：'+o.hint;
      render();
    });
    box.appendChild(d);
  }
}
function readCond(){const c={};for(const fid of Object.keys(FACTORS))c[fid]=document.getElementById('c_'+fid).value;return c;}

// ===== 物理续航模型 v5 =====
// 基准电耗：真实电耗优先，缺失用物理回归(重量+CdA)估算 —— 已在 load_cars 算好 car.cons
// 工况倍率：高速按每车 CdA/吨 差异化（风阻大的高速更费电，解释方盒子效应）
// 再加入：温度、驾驶风格、空调、电压平台/碳化硅修正
const DEMAND={under150:100,'150to300':220,'300to500':400,over500:600};
// 档位：按当前榜内综合分相对位置分 T1/T2/T3（硬条件触发时在 render 里再降一档）
function compositeTier(score){ return score>=82?'T1':(score>=72?'T2':'T3'); }

function hwyMult(car,cond){
  // 高速倍率基准1.70，按 CdA/吨 偏离中位数修正；方盒子额外加成
  let r=car.cdaPerTon;
  if(cond.boxy==='yes'){
    // 方盒子：风阻按同级典型方盒子 Cd≈0.32、迎风面积偏大约20% 修正
    if(car.cd!=null) r=(car.cd*1.15*(car.area||2.6)*1.0)/((car.weight||1800)/1000);
    else r=PHYS.medRatio*1.5;
  }
  if(r==null) r=PHYS.medRatio;
  let m=PHYS.hwyBase*(1+0.45*(r/PHYS.medRatio-1));
  return Math.max(1.35,Math.min(2.35,m));
}

function rangeCalc(car,cond){
  if(car.power!=='BEV') return null;
  const hwWeight={under20:0.0,'20to40':0.25,'40to60':0.7,over60:1.0}[cond.highway_ratio];
  const hwM=hwyMult(car,cond);
  // 市区/高速倍率按高速占比加权
  const sceneM=PHYS.cityBase*(1-hwWeight)+hwM*hwWeight;
  const tempM=PHYS.temp[cond.winter_temp];  // 仅电池活性，不含暖风
  const driveM=PHYS.drive[cond.drive_style]||1.0;
  // 空调=季节基线×用户档位（温度与暖风拆分，避免旧版双重计算）
  const acM=(PHYS.acSeason[cond.winter_temp]||1.0)*(PHYS.ac[cond.ac_usage]||1.0);
  // 电压平台修正：800V+SiC 转换效率略高(影响小，主要影响充电速度)
  let voltM=1.0;
  if(car.voltage){
    const vs=Object.keys(PHYS.volt).map(Number).sort((a,b)=>a-b);
    let best=vs[0]; for(const v of vs) if(Math.abs(v-car.voltage)<Math.abs(best-car.voltage)) best=v;
    voltM=PHYS.volt[best];
  }
  const sceneCons=car.cons*sceneM*tempM*driveM*acM*voltM;
  if(!car.kwh){
    return {range:null, method:'无电量数据', cls:car.body,
      steps:['该车型无电池电量数据，无法估算续航（不编造）']};
  }
  const rng=Math.round(car.kwh/sceneCons*100);
  const steps=[
    '基准电耗 '+car.cons+' kWh/100km（'+car.consSrc+'）',
  ];
  if(car.consSrc==='物理估算'){
    steps[0]='物理回归估算电耗 '+car.cons+'（4.48×车重t + 5.88×CdA + 0.61）';
    steps.push('  车重 '+(car.weight||'—')+'kg · Cd '+(car.cd||'—')+' · 迎风面积 '+(car.area||'—')+'m²');
  }
  steps.push('× 工况倍率 '+sceneM.toFixed(2)+(hwWeight>=0.5?'（偏高速，CdA/吨 '+(car.cdaPerTon||'—')+'）':'（偏市区）'));
  steps.push('× 温度(仅电池) '+tempM+' × 驾驶 '+driveM+' × 空调 '+acM.toFixed(2)+'（季节基线'+PHYS.acSeason[cond.winter_temp]+'×档位'+(PHYS.ac[cond.ac_usage]||1)+'） × 电压系数 '+voltM+(car.voltage?'（'+car.voltage+'V平台）':''));
  steps.push('= 场景电耗 '+sceneCons.toFixed(1)+' kWh/100km');
  steps.push('电量 '+car.kwh+' kWh ÷ 场景电耗 ×100 = 有效续航 '+rng+' km');
  return {range:rng, method:car.consSrc==='事实库'?'物理模型(真实电耗基准)':'物理模型(估算电耗基准)',
          cls:car.boxy==='yes'?'方盒子':car.body, sceneCons:Math.round(sceneCons*10)/10, steps};
}
function effRange(car,cond){ const r=rangeCalc(car,cond); return r?r.range:null; }

// 场景调节：对具体车型计算，返回各项明细与合计（未缩放）
function adjust(car,cond){
  const items=[]; let total=0;
  const hard=[]; // 硬条件：{label,pt,reason,downgrade}
  const add=(label,pt)=>{ if(pt){items.push({label,pt}); total+=pt;} };
  const addHard=(label,pt,reason,downgrade)=>{ if(pt){hard.push({label,pt,reason,downgrade:!!downgrade}); total+=pt;} };
  const P=car.power;
  const isNew=(P==='BEV'||P==='PHEV'||P==='EREV');
  const isSwap=car.swap===true;

  // ===== 硬条件（大扣分 + 降档 + 标注）=====
  // H1: 无固定充电 + 纯电（非换电）→ 大扣分，换电车型缓解
  if(cond.home_charger==='none' && P==='BEV'){
    if(isSwap){
      addHard('无家充·换电缓解', PHYS.swapAdv.no_charger.gain, PHYS.swapAdv.no_charger.reason, false);
    }else{
      addHard('无充电桩·纯电不推荐', -15, '无固定充电，纯电日常补能困难、体验大幅打折；换电车型不受此限', true);
    }
  }
  // H2: 东北极寒 + 低价纯电（≤15万）→ 续航低+充电慢，谨慎；换电/大电池缓解
  if(cond.winter_temp==='underm20' && P==='BEV' && car.price!=null && car.price<=15){
    if(isSwap){
      addHard('极寒·换电缓解', PHYS.swapAdv.cold.gain, PHYS.swapAdv.cold.reason, false);
    }else{
      addHard('极寒+低价纯电·谨慎', -12, '-20℃以下低价纯电续航常腰斩、充电变慢，北方选购需谨慎；换电或大电池车型更稳', true);
    }
  }else if(cond.winter_temp==='underm20' && P==='BEV' && isSwap){
    addHard('极寒·换电优势', PHYS.swapAdv.cold.gain, PHYS.swapAdv.cold.reason, false);
  }
  // H3: 短持有(2-3年) + 纯电 → 折旧快；换电车型(车电分离)合适
  if(cond.hold_years==='under3' && P==='BEV'){
    if(isSwap){
      addHard('短持有·换电合适', PHYS.swapAdv.short_hold.gain, PHYS.swapAdv.short_hold.reason, false);
    }else{
      addHard('短持有·纯电折旧快', -8, '2-3年换车，纯电折旧快、残值损失大；换电车型车电分离更合适', true);
    }
  }
  // H4: 长途高频 + 纯电续航明显不足 → 大扣分
  if((cond.long_trip_freq==='weekly'||cond.long_trip_freq==='monthly') && P==='BEV'){
    const eff=effRange(car,cond), d=DEMAND[cond.single_trip];
    if(eff!=null && eff < d*0.75){
      if(isSwap) addHard('长途+换电缓解', PHYS.swapAdv.longtrip.gain, PHYS.swapAdv.longtrip.reason, false);
      else addHard('长途续航不足·不推荐', -10, '长途频繁且单次里程远超有效续航，纯电补能时间成本高', true);
    }
  }
  // H5: 限牌 + 燃油/油混 → 上牌困难
  if(cond.plate_limit==='yes' && (P==='ICE'||P==='HEV')){
    addHard('限牌城市·上牌难', -8, '限牌城市燃油/油混需摇号拍牌，新能源可直接上牌', true);
  }
  // H6: 营运 + 燃油/油混 → 成本劣势
  if(cond.operation==='yes' && (P==='ICE'||P==='HEV')){
    addHard('营运·能耗成本高', -10, '营运高频，燃油/油混能耗成本远高于纯电', true);
  }

  // ===== 常规软调节 =====
  // 1) 续航匹配（仅纯电）
  const eff=effRange(car,cond), d=DEMAND[cond.single_trip];
  if(P==='BEV'){
    if(eff!=null){
      const gap=(d-eff)/d;
      let pt = gap<=0.10?0 : gap<=0.25?-2 : gap<=0.40?-3 : gap<=0.60?-4.5 : -6;
      if(eff>=d*1.3) pt=1;
      add('续航匹配(需'+d+'/有效'+eff+'km)',pt);
    }else{
      items.push({label:'续航匹配：无续航数据，不参与',pt:0,na:true});
    }
  }
  // 2) 长途频率（软）
  if(cond.long_trip_freq==='monthly'||cond.long_trip_freq==='weekly'){
    add('长途频率高', P==='BEV'?-1.5 : (P==='ICE'||P==='HEV')?1.5 : 0);
  }else if(cond.long_trip_freq==='rare'){ add('几乎无长途', P==='BEV'?1:0); }
  // 3) 充电条件（软，硬条件H1已覆盖纯电无桩）
  if(cond.home_charger==='home') add('有家充', P==='BEV'?1.5 : (P==='PHEV'||P==='EREV')?1 : 0);
  if(cond.home_charger==='none' && P!=='BEV') add('无固定充电', (P==='PHEV'||P==='EREV')?-1.5 : 1);
  // 4) 营运（软）
  if(cond.operation==='yes') add('营运', P==='BEV'?2 : 0);
  // 5) 高速占比（软）
  if(cond.highway_ratio==='over60') add('常年高速', P==='EREV'?-2 : P==='BEV'?-1 : 1);
  else if(cond.highway_ratio==='40to60') add('高速较多', P==='EREV'?-1:0);
  else if(cond.highway_ratio==='under20') add('市区为主', P==='BEV'?1:0);
  // 6) 方盒子 + 高频高速
  if(cond.boxy==='yes'&&cond.highway_ratio==='over60') add('方盒子+高频高速', P==='EREV'?-1.5 : P==='BEV'?-1.5 : P==='PHEV'?-1:0);
  // 7) 年里程
  if(cond.annual_km==='over25') add('高里程', P==='BEV'?1.5 : P==='ICE'?-1:0);
  else if(cond.annual_km==='under10') add('低里程', P==='BEV'?-1 : ((P==='ICE'||P==='HEV')?1:0));

  return {items,total,hard};
}

let carSort={k:'composite',dir:-1};
function render(){
  const cond=readCond();
  const scale=(parseFloat(document.getElementById('wEnergy').value)||0)/100;
  document.getElementById('wLabel').textContent=Math.round(scale*100)+'%';
  const low=parseFloat(document.getElementById('pLow').value)||0;
  const high=parseFloat(document.getElementById('pHigh').value)||999;
  const body=document.getElementById('bodyFilter').value;

  let rows=CARS.filter(c=>{
    const p=c.price!=null?c.price:(c.priceLow!=null?c.priceLow:null);
    if(p==null) return true;
    if(p<low||p>high) return false;
    if(body!=='ALL'&&c.body!==body) return false;
    return true;
  }).map(c=>{
    const a=adjust(c,cond);
    // 软调节按力度缩放并封顶±10；硬条件不缩放、不封顶，全额计入
    const soft=a.total-a.hard.reduce((s,h)=>s+h.pt,0);
    const hardTotal=a.hard.reduce((s,h)=>s+h.pt,0);
    const adjSoft=Math.max(-10,Math.min(10,soft*scale));
    const adj=adjSoft+hardTotal;
    const capped=Math.abs(soft*scale)>10;
    const hardDown=a.hard.some(h=>h.downgrade);
    const rc=rangeCalc(c,cond);
    // 档位：基础按综合分，触发硬条件降一档
    let tier=compositeTier(c.product+adj);
    if(hardDown) tier=tier==='T1'?'T2':(tier==='T2'?'T3':'T3');
    return {...c, eff:rc?rc.range:null, rc, adjTotal:a.total, adj, capped, adjItems:a.items,
            hard:a.hard, tier, composite:c.product+adj};
  });

  const k=carSort.k;
  rows.sort((x,y)=>{
    let a=x[k],b=y[k];
    if(a==null)a=-1;if(b==null)b=-1;
    if(typeof a==='string')return carSort.dir*a.localeCompare(b,'zh');
    return carSort.dir*(a-b);
  });
  rows.forEach((r,i)=>r.rank=i+1);
  // 可选扩展钩子：外部注入的正式榜区块可订阅重排结果，随条件实时重绘。默认无订阅，行为不变。
  if(typeof window!=='undefined' && typeof window.onBoardRerender==='function'){
    try{ window.onBoardRerender(rows, cond, scale); }catch(e){ console.error('onBoardRerender',e); }
  }

  // 摘要
  if(rows.length){
    const t=rows.slice().sort((a,b)=>b.composite-a.composite)[0];
    document.getElementById('summary').innerHTML=
      '当前条件下，<b>'+t.model+'</b> 综合分居首（'+t.composite.toFixed(1)+'）= 产品力 '+t.product.toFixed(1)+
      ' + 场景调节 '+(t.adj>=0?'+':'')+t.adj.toFixed(1)+'。'+
      '<div class="formula">综合分 = 产品力主分 + 软调节(封顶±10，力度'+Math.round(scale*100)+'%) + 硬条件惩罚(全额·降档)</div>';
  }else{
    document.getElementById('summary').innerHTML='当前过滤条件下无匹配车型，请放宽预算区间或车身类型。';
  }

  document.getElementById('carCount').textContent='（'+rows.length+' 款符合条件）';
  const tb=document.getElementById('carBody'); tb.innerHTML='';
  if(!rows.length) tb.innerHTML='<tr><td colspan="12" style="text-align:center;color:#888;padding:24px">无匹配车型</td></tr>';
  rows.forEach(r=>{
    const tr=document.createElement('tr');
    const adjCls=r.adj>0?'adj-pos':(r.adj<0?'adj-neg':'adj-zero');
    const adjTxt=(r.adj>0?'+':'')+r.adj.toFixed(1);
    const detail=r.adjItems.length?r.adjItems.map(it=>
        '<div class="r"><span>'+it.label+'</span><span class="'+(it.pt>0?'adj-pos':it.pt<0?'adj-neg':'')+'">'+
        (it.pt>0?'+':'')+(it.pt||0)+'</span></div>').join('')
        :'<div class="r">未触发调节项</div>';
    const cap=r.capped?'<div class="cap">注：软调节已封顶至 ±10</div>':'';
    const hardHtml=r.hard.length?('<div class="cap" style="color:#b42318"><b>硬条件：</b>'+
      r.hard.map(h=>'· '+h.label+' ('+(h.pt>0?'+':'')+h.pt+')：'+h.reason+(h.downgrade?' [已降档]':'')).join('<br>')+'</div>'):'';
    // 车型名旁的警示/换电徽章
    const badges=(r.swap?'<span style="display:inline-block;background:#e7f4ea;color:#1a7f37;border-radius:4px;font-size:9.5px;padding:0 5px;margin-left:5px">换电</span>':'')+
      (r.hard.some(h=>h.downgrade)?'<span style="display:inline-block;background:#fbe9e7;color:#b42318;border-radius:4px;font-size:9.5px;padding:0 5px;margin-left:5px">⚠硬条件不符</span>':'');
    const tierCls=r.tier==='T1'?'t1':(r.tier==='T2'?'t2':'t3');
    tr.innerHTML=
      '<td>'+r.rank+'</td><td><b>'+r.model+'</b>'+badges+'</td>'+
      '<td><span class="tier-badge '+tierCls+'">'+r.tier+'</span></td><td>'+r.cls+'</td>'+
      '<td><span class="pill" style="background:'+PM[r.power].color+'">'+PM[r.power].short+'</span></td>'+
      '<td class="num">'+(r.price!=null?r.price:(r.priceLow+'–'+r.priceHigh))+'</td>'+
      '<td class="num">'+(r.cltc||'—')+'</td>'+
      '<td class="num">'+(r.cons?r.cons:'—')+'</td>'+
      '<td class="num">'+(r.rc?
        '<details class="adj"><summary>'+r.eff+' km</summary><div class="adjbox"><b>续航估算（'+r.rc.method+'）</b><br>'+
        r.rc.steps.map(s=>'· '+s).join('<br>')+'</div></details>' : '—')+'</td>'+
      '<td class="num">'+r.product.toFixed(1)+'</td>'+
      '<td class="num"><details class="adj"><summary class="'+adjCls+'">'+adjTxt+'</summary>'+
        '<div class="adjbox">调节明细（软 '+(r.adjTotal>0?'+':'')+r.adjTotal.toFixed(1)+'；硬条件不缩放）<br>'+detail+cap+hardHtml+'</div></details></td>'+
      '<td class="num"><b>'+r.composite.toFixed(1)+'</b></td>';
    tb.appendChild(tr);
  });
}

document.querySelectorAll('#carTable th').forEach(th=>{
  th.addEventListener('click',()=>{
    const kk=th.dataset.k;
    carSort.dir=(carSort.k===kk)?-carSort.dir:(kk==='model'||kk==='cls'||kk==='power'?1:-1);
    carSort.k=kk;
    document.querySelectorAll('#carTable th').forEach(x=>x.classList.remove('sorted'));
    th.classList.add('sorted'); render();
  });
});
document.getElementById('wEnergy').addEventListener('input',render);
['pLow','pHigh'].forEach(id=>document.getElementById(id).addEventListener('input',render));
document.getElementById('bodyFilter').addEventListener('change',render);

function resetAll(){
  for(const [fid,f] of Object.entries(FACTORS)){
    const s=document.getElementById('c_'+fid); s.value=f.default;
    const o=f.options.find(o=>o.v===f.default);
    s.parentElement.querySelector('.fh span').textContent='当前：'+o.hint;
  }
  document.getElementById('pLow').value=12;
  document.getElementById('pHigh').value=14;
  document.getElementById('bodyFilter').value='轿车';
  document.getElementById('wEnergy').value=100;
  carSort={k:'composite',dir:-1};
  document.querySelectorAll('#carTable th').forEach(x=>x.classList.remove('sorted'));
  document.querySelector('#carTable th[data-k="composite"]').classList.add('sorted');
  render();
}
buildConds();
render();
</script>
__EXTRA_TAIL__
</body>
</html>
"""


if __name__ == "__main__":
    build()
