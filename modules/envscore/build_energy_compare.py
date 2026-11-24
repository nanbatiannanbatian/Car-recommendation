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

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)
# 物理模型单一事实源：从 ev_range_model import，杜绝两处系数漂移（不打补丁原则）
from modules.physics import ev_range_model as ERM
# v10.52.0：用车条件因素表/分组/默认画像/硬条件+TCO 常量统一从 engine.py import
# （规则单一事实源；engine.js 是浏览器交互镜像，由等价回归守护）
from modules.envscore.engine import (FACTORS, FACTOR_GROUPS, DEFAULT_COND, TCO,                                     CORE_FACTORS, ANNUAL_KM_MID, tco_js,                                     breakeven_km_range, breakeven_sensitivity)
from modules.envscore.cost_params import BASIS as COST_BASIS
MP = str(paths.cat("master_pool.json"))
TR = str(paths.cat("trims.json"))
FL = str(paths.cat("fact_library.json"))  # 真实电耗/电量
VP = str(paths.cat("vehicle_physics.json"))  # 物理参数库(采集)
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

# 用车条件因素表（13 项）/ 分组 / 默认画像：单一事实源在 modules/envscore/engine.py，
# 本页不再持有副本（防两套口径漂移）。调节分值在页面 JS 的 computeAdjust/adjust 中
# 按具体车型计算，规则与 engine.py 一一对应（env_equivalence_regress 双向等价守护）。


def norm(s):
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


# 生成物（master_pool/trims/fact_library/vehicle_physics）不随发布包，新装包首跑前需重建。
# 缺文件时给可执行的下一步，而不是抛裸 FileNotFoundError 让人猜。
_REBUILD_HINT = {
    "master_pool.json": "python3 master_pool.py --intake --month <YYYYMM> --apply  # 或从总池快照恢复",
    "trims.json": "python3 auto_trims.py --apply  # 逐配置阶梯自动补录",
    "fact_library.json": "python3 facts_sync.py --apply  # 原始库→事实库同步",
    "vehicle_physics.json": "python3 sync_to_repo.py 之后由采集脚本重建（或在源码目录复制 data/gen/vehicle_physics.json）",
}


def _load_gen(path: str):
    try:
        return json.load(open(path, encoding="utf-8"))
    except FileNotFoundError as e:
        name = os.path.basename(path)
        hint = _REBUILD_HINT.get(name, "见 MODULES.md「数据落盘」")
        raise SystemExit(
            f"[缺数据] {path}\n"
            f"  这个 JSON 是生成物，不随发布包（data/gen/ 打包排除）。\n"
            f"  重建方式：{hint}"
        ) from e


def load_cars():
    mp = _load_gen(MP)
    # 2026-11-22：走统一入口（人工库 + 自动库合流）
    from modules.data.trims_store import load_db as _load_trims_db
    tr = _load_trims_db()
    fl = _load_gen(FL)  # 真实电耗库
    vpdoc = _load_gen(VP)  # 物理参数库(采集)
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
            "body": body_label(t, c),
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


# 车身形式归一（v10.56.0）：「方盒子造型」不再作为用车条件，改由车型自身承担：
#   ① 官方车身类型（三厢/两厢/SUV/MPV）直接映射；
#   ② 只有拿到客观字段才升级子类——offroad 标记/非承载结构 → 越野车；
#      boxy 标记（方盒子车系，如风阻 Cd≥0.33 且车高/车长≥0.36 的 SUV）→ 方盒子SUV。
#   拿不到就停在「SUV」，不靠外形主观猜（项目纪律：不编造）。
BODY_ALIAS = {"三厢车": "轿车", "两厢车": "轿车", "轿车": "轿车", "SUV": "SUV", "MPV": "MPV", "皮卡": "皮卡"}


def body_label(t, c):
    explicit = (t.get("body_form") or c.get("body_form") or "").strip()
    if explicit:
        return BODY_ALIAS.get(explicit, explicit)
    base = BODY_ALIAS.get((t.get("body") or "").strip(), (t.get("body") or "").strip())
    if base == "SUV":
        if c.get("offroad") or c.get("unibody") is False:
            return "越野车"
        if c.get("boxy"):
            return "方盒子SUV"
    return base


ENGINE_JS_PATH = paths.MODULES / "envscore" / "engine.js"


def engine_js() -> str:
    """读取环境分/续航引擎（engine.js 是唯一事实源）。

    缺文件直接报错，不回退到模板里的旧副本——静默回退会让「单一事实源」名存实亡。
    """
    if not ENGINE_JS_PATH.exists():
        raise FileNotFoundError(f"缺少环境分引擎：{ENGINE_JS_PATH}")
    return ENGINE_JS_PATH.read_text(encoding="utf-8")


def _be_txt(ber, key):
    """平衡里程数值的人话形态（方法区用）：12000 → 1.2 万 km/年。"""
    if not ber or ber.get(key) is None:
        return "—"
    v = ber[key]
    return ("%.1f 万" % (v / 10000.0)) if v >= 9000 else ("%.0f" % v)


def _sens_txt(sens):
    """敏感性 top2 的人话形态。"""
    if not sens:
        return "—"
    out = []
    for s in sens[:2]:
        out.append("%s（%s~%s km/年）" % (s["cn"], _be_txt(s, "low"), _be_txt(s, "high")))
    return "、".join(out)


def build(cars_override=None, out=None, extra_head="", extra_tail="", title=None,
          def_low=12, def_high=14, def_body="轿车", sales_month=None):
    """生成交互比较器页面。默认无参数=原行为（整池67款，向后兼容）。
    cars_override: 外部注入的车列表（含补采分），用于特定榜单。
    extra_head/extra_tail: 注入到 <body> 前 / </body> 前的额外HTML（正式榜导航/区块）。
    def_low/def_high/def_body: 预算与车身的默认过滤值——注入特定榜单时必须传该榜口径，
      否则会沿用"12–14万轿车"示例默认值，导致 SUV 榜默认空表（已出过此 bug）。
    """
    cars = cars_override if cars_override is not None else load_cars()
    html = HTML_TEMPLATE
    # 平衡里程与其敏感性：页面方法区展示（开发期算子，随包只发布结果）
    _def = dict(DEFAULT_COND)
    ber = breakeven_km_range(cars, _def)
    sens = breakeven_sensitivity(cars, _def)
    if title:
        html = html.replace("<title>选车适配调节器 · 产品力主榜 + 用车条件修正</title>",
                            f"<title>{title}</title>")
    # 车身形式选项从车型池实际取值派生（v10.56.0）：池里有 MPV / 越野车 / 方盒子SUV 时自动出现，
    # 不再硬编码「轿车/SUV」，避免出现选中即空表的死选项。
    bodies = [b for b in dict.fromkeys((c.get("body") or "") for c in cars) if b]
    if def_body not in bodies:
        def_body = bodies[0] if bodies else "ALL"
    body_opts = "".join(
        f'<option value="{v}"{" selected" if v == def_body else ""}>{v}</option>'
        for v in bodies) + f'<option value="ALL"{" selected" if def_body == "ALL" else ""}>全部</option>'
    reps = {
        "__DEF_LOW__": str(def_low),
        "__DEF_HIGH__": str(def_high),
        "__DEF_BODY__": def_body,
        "__DEF_FILTER_HINT__": f"默认 {def_low}–{def_high} 万 · {'全部' if def_body == 'ALL' else def_body}",
        "__BODY_OPTIONS__": body_opts,
        "__EXTRA_HEAD__": extra_head,
        "__EXTRA_TAIL__": extra_tail,
        "__CARS_JSON__": js(cars),
        "__FACTORS_JSON__": js(FACTORS),
        "__FACTOR_GROUPS_JSON__": js(FACTOR_GROUPS),
        "__DIMS_JSON__": js(DIMS),
        "__WEIGHTMAP_JSON__": js(WEIGHT_MAP),
        "__POWERS_JSON__": js(POWERS),
        "__POWERMETA_JSON__": js(POWER_META),
        "__ENGINE_JS__": engine_js(),
        "__PHYS_JSON__": js({"a":PHYS_A,"b":PHYS_B,"c":PHYS_C,"areaFactor":AREA_FACTOR,
                             "medRatio":BASE_CDA_PER_TON,"hwyBase":HWY_BASE_MULT,"cityBase":CITY_BASE_MULT,
                             "temp":TEMP_CONS,"drive":DRIVE_MULT,"ac":AC_MULT,
                             "acSeason":ERM.AC_BASE,
                             "volt":VOLTAGE_MULT,"swapAdv":SWAP_ADVANTAGE,
                              # v10.56.0：地形/露天冬季条件已删，对应乘子不再注入（JS 侧已无引用）
                              # v10.52.0：TCO 常量注入（JS tco() 与 engine.py 同源；键名 camelCase 由 engine.tco_js 派生）
                              "tco": tco_js(), "annualKmMid": ANNUAL_KM_MID,
                             "tcoBasis":COST_BASIS + "；购置税2026-2027新能源减半5%（减税额上限1.5万）、燃油/油混10%；"
                                        "以旧换新补贴因资格各异未计入；TCO 只披露不入分"}),
        "__N_CARS__": str(len(cars)),
        "__SALES_MONTH__": str(sales_month or "见逐车销量标注"),
        "__N_CLTC__": str(sum(1 for c in cars if c["cltc"])),
        "__N_PHYS__": str(sum(1 for c in cars if c.get("weight") and c.get("cd"))),
        "__BE_KM__": _be_txt(ber, "km"),
        "__BE_LOW__": _be_txt(ber, "low"),
        "__BE_HIGH__": _be_txt(ber, "high"),
        "__SENS__": _sens_txt(sens),
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
.adv-conds{margin-top:14px;border:1px solid #e6e3dc;border-radius:8px;padding:8px 10px;background:#fcfbf8}
.adv-conds summary{cursor:pointer;font-size:12px;color:#5b6470;font-weight:600;padding:2px 0}
.adv-conds summary:hover{color:#1f6f8b}
.adv-conds[open] summary{margin-bottom:6px;border-bottom:1px solid #e6e3dc;padding-bottom:6px}
.grp-head{margin:14px 0 8px;padding:7px 10px;background:#faf9f6;border-left:3px solid #1f6f8b;border-radius:5px}
.grp-head b{font-size:12.5px;color:#15181c}
.grp-head span{display:block;font-size:10.5px;color:#5b6470;line-height:1.45;margin-top:3px}
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
.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;margin-top:10px}
.form-card{border:1px solid var(--line);border-radius:10px;padding:10px 12px;background:#fcfbf8}
.form-card.top{border-color:#1f6f8b;background:#eef7fa;box-shadow:0 0 0 1px #1f6f8b inset}
.form-card .fc-head{display:flex;align-items:center;gap:6px;font-weight:700;font-size:13.5px}
.form-card .fc-avg{font-size:22px;font-weight:700;color:var(--accent2);margin:4px 0 2px}
.form-card .fc-sub{font-size:11px;color:var(--sub);line-height:1.5}
.form-card .fc-badge{font-size:10px;padding:1px 6px;border-radius:4px;background:#e7f4ea;color:#1a7f37}
.form-card .fc-badge.mid{background:#fbf3e6;color:#9a6700}
.form-card .fc-badge.bad{background:#fbe9e7;color:#b42318}
.diag{margin-top:12px;font-size:12px;line-height:1.7}
.diag .d-item{padding:3px 0;border-bottom:1px dashed var(--line)}
.diag .warn{color:#b42318}.diag .info{color:#5b6470}
@media(max-width:1000px){.layout{grid-template-columns:1fr}.cond-panel{position:static}.two{grid-template-columns:1fr}.mform{grid-template-columns:1fr 1fr}}
</style>
</head>
<body>
__EXTRA_HEAD__
<div class="wrap">
<header>
  <h1>选车适配<em>调节器</em> · 产品力主榜 + 用车条件修正</h1>
  <div class="sub">车好不好，主要看 8 维产品力分（<b>统一维度：电车评三电、油车评三大件，同一把尺子</b>）；你的用车条件只做加减分修正。例如"单次要开 500km、这车有效续航仅 400km"，就在续航不足项扣分。改动条件即时重排车型。</div>
  <div class="note"><b>核心公式：</b>车型综合分 = 产品力主分（统一 8 维加权，75–90 分）+ 场景调节（软调节封顶±10）+ <b>硬条件惩罚（全额计入、降档、标注）</b>。第 1 维"动力总成"对电车=三电、油车=三大件、混动=两者，共用 18% 权重。<b>有效续航用物理模型</b>：基准电耗（真实数据优先，缺失用 4.48×车重+5.88×CdA 回归估算）× 工况倍率（风阻大的车高速更费）× 温度 × 暖风 × 电压平台，电量÷场景电耗×100。物理参数（整备质量/风阻/电压/换电）采集自汽车之家参数页。<b>换电车型</b>在无桩/极寒/短持有场景有优势。点续航或调节分数可看完整算式。<b>前置条件</b>：跨能源混合比较必须先选定 11 项用车条件画像（缺省=人群默认画像，页面顶部画像卡明示），定稿榜=该画像下的快照；页面顶部「前置条件 → 能源形式适配结论」先给出「你这种条件适合哪种能源形式」，并做画像自检（矛盾组合会提示）。所有数据内嵌本页，离线可用。</div>
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
        <input type="number" id="pLow" value="__DEF_LOW__" step="0.5" min="0">
        <input type="number" id="pHigh" value="__DEF_HIGH__" step="0.5" min="0">
      </div>
      <div class="fh">__DEF_FILTER_HINT__</div>
    </div>
    <div class="fg">
      <label>车身类型</label>
      <select id="bodyFilter">__BODY_OPTIONS__</select>
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
    <div class="panel" style="margin-bottom:20px">
      <div class="section-title"><span class="bar"></span>前置条件 → 能源形式适配结论</div>
      <div class="summary-box" id="formNarr"></div>
      <div class="form-grid" id="formGrid"></div>
      <div class="diag" id="profileDiag"></div>
    </div>

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
            <th data-k="tco" class="num">持有成本8年▾</th>
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
          <p><b>打分逻辑：</b>综合分 = 产品力主分 + 软调节（封顶±10）+ 硬条件惩罚（全额、不封顶）。产品力用统一 8 维框架（动力总成18%/安全16.2%/智驾13.5%/空间12.6%/性价比11.7%/质量品控10%/操控9%/车机座舱9%）×10。硬条件触发时扣大分并<b>降一档</b>（T1→T2→T3）、车型旁标红警示：无桩纯电(−15)、极寒+低价纯电(−12)、短持有纯电(−8)、长途续航不足(−10)、限牌油车(−8)、营运油车(−10)、<b>限牌京沪·插混增程无绿牌优势(−8：北京插混增程按油车管理需燃油指标、上海插混无免费绿牌)</b>。<b>纯电优先原则</b>：无硬条件冲突的纯电车型 +0.5——「纯电尽量纯电，条件不符再扣分」的代码化。<b>换电车型</b>相反：无桩/极寒/短持有/长途场景给正分（据网搜共识：3分钟补能、电池恒温、车电分离保值）。</p>
          <p><b>物理续航模型（透明可核）：</b>基准电耗 = 事实库真实电耗，缺失用回归 <code>4.48×车重(t) + 5.88×Cd×迎风面积 + 0.61</code> 估算（39款拟合、MAE 0.69）。迎风面积≈宽×高×0.84。<code>场景电耗 = 基准 × 工况倍率 × 温度(仅电池) × 暖风基础 × 电压</code>，<code>有效续航 = 电量 ÷ 场景电耗 ×100</code>。工况倍率：市区/高速按高速占比加权，高速基准按每车 <b>CdA/吨</b> 偏离中位数差异化——<b>车身形状的影响由车型自身风阻承担</b>（方盒子车 Cd 高、自然更费），不再需要用户勾选"我买的是方盒子"。温度&gt;0℃1.00/−10℃1.05/−20℃1.12/极寒1.20；电压800V·SiC约0.975（影响小，主要影响充电速度）。实测校验高速达成率约51–63%（懂车帝实测55%吻合）。<b>山区/高原不在模型内</b>：路线级爬坡差异（丘陵 +10~20%、高山 +25~40%，每爬升 100m 约 0.5kWh）请自行上浮预期，页面不假装能算准。</p>
        </div>
        <div>
          <p><b>其余调节项：</b>长途频率高→纯电减分、油/混动加分；无家充→纯电大幅减分、插混/增程减分、油车加分（<b>单位/公司可充电</b>单独一档：白天补能、成本接近家充，不按「无固定充电」处罚）；营运→纯电加分；限牌→新能源加分（京沪细分：插混/增程同罚）；常年高速→增程明显减分、纯电续航打折；短持有→纯电减分；年里程：2.5万以上纯电加分，<b>5000km 以下纯电 −2.0</b>（低于盈亏平衡点约 1.5 万km/年）、5000~1万 −1.0；<b>补能耐心不足+长途→纯电明显减分、可油可电加分</b>；<b>低里程无桩纯电→追加减分（TCO 固定成本摊薄慢）</b>。插混/增程因有油箱兜底，不扣"里程不足"分，但增程高速油耗高会体现为减分。</p>
          <p><b>条件表 11 项（已删清"问得多、结论不变"的项）：</b>
          核心 9 项——营运 / 充电条件 / <b>冬季气候带</b>（按常驻城市选：南方·江淮·华北·东北，括号内温度只用于续航）/ 年里程（含 5000km 以下档）/ 单次最长里程 / 长途频率 / 限牌城市 / 持有年限 / 高速占比；
          进阶 2 项——<b>家庭车辆结构</b>（增购第二辆且已有油车：纯电 +2.0，第二辆再买燃油 −1.0，J.D. Power 2025 增购占新能源购买 28.1%）、<b>补能耐心</b>。
          已删 9 项与理由（逐项扰动实测，见 references/用车条件因素库.md）：夏季气温 / 驾驶风格 / 空调强度 / 停放环境 / 驻车用电 / 露营外放电 / 电价档（单项最大影响 ≤2 分或只改 TCO 展示）；<b>地形</b>（无硬条件、路线级平均算不准，改为上文"自行上浮预期"）；<b>方盒子造型</b>（改成车型自身风阻 + 车身形式筛选，不再让用户替车型回答风阻问题）。</p>
          <p><b>持有成本 TCO（披露，不入分）——参数是「费率带」，不是报价：</b>8 年合计 = 能源（逐车电耗/油耗事实 × 画像年里程 × 充电条件；油价 8.36 元/L，各省发改委 2026-11-12 调价区间 8.26~8.39；家充谷电 0.308 元/度，浙江居民充电设施谷段口径；公充 1.5、单位 0.75）+ 保费（<b>固定部分 1800 + 车价×1.8%</b>，再乘能源系数（纯电/插混 1.35、增程 1.30、油混 1.05、燃油 1.0）与 8 年续保均值 0.88；精算协会口径：同级纯电保费溢价 15%~21%，10~20 万主流电车每年贵 1200~2000 元）+ 保养（纯电 500/插混 1200/油混 1000/燃油 2000 元/年，取自 4S 实测账本中位）+ 购置税（2026-2027 新能源减半 5%、减税额上限 1.5 万；燃油/油混 10%）+ 残值损失（<b>级别基准 + 能源修正</b>：轿车 48.3%/SUV 50.25%/MPV 55.45%，纯电 −2.4pt、插混·增程 −3.0pt、油混 +1.0pt；流通协会+精真估 2026）+ <b>轮胎按里程折损</b>（寿命 纯电 4 万km/套、插混 5 万、油混·燃油 6 万；整套价按轮毂尺寸档 16~20 寸 = 1600~5000 元，按车价档映射）+ <b>非事故易损与电子件</b>（纯电 1200/插混 1000/油混·燃油 900 元/年，含大灯总成、雷达、车机等偶发项）+ 电池风险金（纯电 2500、插混·增程 800 元/年，中保研电池零整比口径）。<b>事故维修费不计入</b>：事故由保险吸收、出险又推高次年保费，再单列即双计。</p>
          <p><b>平衡里程怎么读：</b>摘要区给的是「家充纯电 vs 池内最优燃油/油混」8 年 TCO 相等的年里程——本池约 <b>__BE_KM__ km/年</b>，非能源成本 ±30% 得区间 <b>__BE_LOW__~__BE_HIGH__ km/年</b>。低于它纯电省不回固定成本差（车价差、购置税、保费、残值），高于它才越开越省，灰区内取决于你自己那台车的报价。<b>对平衡点影响最大的参数依次是 __SENS__</b>——说明它不是被轮胎、残值那些小项决定的，主要由能源价差与保费差决定。参数取值、来源机构与日期属<b>开发期估算结果，随包发布</b>；原始抓取资料不入包，改口径只需改参数重新打包，公式自动跟着走。</p>
          <p><b>默认值依据：</b>年里程 1.5–2.5 万（成本平衡点约 1.5 万km/年、区间 1.1~2.0 万，家充谷电口径，按参数带重算）；冬季气候带默认江淮（长江流域）；单次 300–500km、一年 1–5 次；有家充；不限牌；持有 5–8 年；高速 20–40%；非营运；可接受补能等待；家庭唯一用车。为人群常见画像，可按实际修改。</p>
          <p><b>销量口径（事实列，不进分）：</b>判档用<b>最近 6 个月逐月中位数</b>（中位抗清库/换代跳水，新车爬坡取「中位 vs 最新月」较大值）；页面逐车显示的是<b>单月车系口径</b>（含该车系所有能源形式），数据月 <b>__SALES_MONTH__</b>（月初未出数时回退到上上月）。两个口径不同，不可混读——单月高≠稳态强，单月中位低也不代表清库。</p>
          <p><b>数据边界：</b>候选池 __N_CARS__ 款车（其中 __N_CLTC__ 款有续航数据、__N_PHYS__ 款有完整物理参数）。能源形式覆盖 BEV/PHEV/EREV/HEV/ICE 五种——纯电/插混为<b>官方核验</b>逐配置数据；燃油/油混/增程的 8 维分与逐配置来自<b>人工补采</b>（每维附信源 URL，见 runs/ 下 scores batch）与汽车之家结构化提取；<b>无电量数据的车</b>不编造续航、不参与续航调节（纯电显示「数据待核」），缺物理参数的车用回归估算电耗并标注来源。物理参数（整备质量/风阻Cd/长宽高/电压/换电）由汽车之家参数页采集，来源逐车可查（见 vehicle_physics.json）。TCO 为披露测算非定价承诺，实际花费因驾驶习惯/地区电价/保险折扣而异。</p>
        </div>
      </div>
    </div>
  </div>
</div>
</div>

<script>
const CARS=__CARS_JSON__;
const FACTORS=__FACTORS_JSON__;
const FACTOR_GROUPS=__FACTOR_GROUPS_JSON__;
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
  // v9.7.51：核心能源决策组默认平铺；其余组收进"高级选项"折叠区（默认收起）。
  // 聚焦 8 项直接影响油电选择的条件；能耗微调/特殊场景是物理模型输入或小众场景，保留但不占主视野。
  const groups=FACTOR_GROUPS||[];
  const grouped=new Set();
  function renderGroup(g,container){
    const hd=document.createElement('div'); hd.className='grp-head';
    hd.innerHTML='<b>'+g[0]+'</b><span>'+g[2]+'</span>';
    container.appendChild(hd);
    g[1].forEach(function(fid){
      const f=FACTORS[fid]; if(!f)return; grouped.add(fid);
      container.appendChild(makeCond(fid,f));
    });
  }
  // 第一组=核心能源决策：默认展开
  if(groups.length) renderGroup(groups[0],box);
  // 其余组=高级选项：默认折叠
  if(groups.length>1){
    const adv=document.createElement('details'); adv.className='adv-conds'; adv.id='advConds';
    const nRest=groups.slice(1).reduce(function(s,g){return s+g[1].length;},0);
    adv.innerHTML='<summary>高级选项 · 能耗微调与特殊场景（'+nRest+' 项，一般用户可忽略）</summary>';
    groups.slice(1).forEach(function(g){renderGroup(g,adv);});
    box.appendChild(adv);
  }
  // 未在分组中的项放到"其他"避免遗漏（默认可见）
  const rest=[];
  for(const [fid,f] of Object.entries(FACTORS)) if(!grouped.has(fid)) rest.push(makeCond(fid,f));
  if(rest.length){
    const hd=document.createElement('div'); hd.className='grp-head';
    hd.innerHTML='<b>其他</b><span>未归入上述分组的条件</span>';
    box.appendChild(hd); rest.forEach(function(d){box.appendChild(d);});
  }
}
function makeCond(fid,f){
  const d=document.createElement('div'); d.className='fg'; d.dataset=Object.assign({},d.dataset,{fid:fid});
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
  return d;
}
function readCond(){const c={};for(const fid of Object.keys(FACTORS))c[fid]=document.getElementById('c_'+fid).value;return c;}
// TCO 明细格式化（与 engine.py tco() 同口径）
function tcoHtml(t){
  if(!t) return '无价格数据，不测算';
  const wan=x=>(x/10000).toFixed(2)+'万';
  return '能源×8年 '+wan(t.energy8y)+'（'+t.unitEnergy.toFixed(3)+'元/km × '+t.annualKm+'km × 8）<br>'+
    '保费×8年 '+wan(t.ins8y)+'（车价×费率×能源系数：约 '+(t.insAnnual||0).toLocaleString()+' 元/年，含续保递减）<br>'+
    '保养×8年 '+wan(t.maint8y)+'<br>购置税 '+wan(t.purchaseTax)+'（2026-2027新能源减半5%/燃油油混10%，减税上限1.5万）<br>'+
    '轮胎×8年 '+wan(t.tire8y)+'（'+(t.tireSize||'')+'寸整套 '+(t.tirePrice4||0)+' 元，按里程折损；电车胎更贵、磨得更快）<br>'+
    '易损与电子件×8年 '+wan(t.wear8y)+'（不含保养、不含事故——事故由保费吸收，不重复计）<br>'+
    '残值损失(3年口径近似) '+wan(t.residualLoss)+'（该车 '+(t.residualRate!=null?(t.residualRate*100).toFixed(1)+'%':'—')+'：级别基准 + 能源修正）<br>'+
    '电池风险金×8年 '+wan(t.batteryRisk8y)+'<br>'+
    '<b>8年合计 '+wan(t.tco8y)+' ≈ '+(t.tcoPerKm*8).toFixed(2)+'元/km（含购车残值口径）</b><br>'+
    '<span style="color:#8a929b">'+t.basis+'</span>';
}
// 逐条件判定徽章：非"适配"的判定上钻（最多3枚）
function verdictChips(v){
  const LV={适配:['#e7f4ea','#1a7f37'],谨慎:['#fbf3e6','#9a6700'],不建议:['#fbe9e7','#b42318']};
  const out=[];
  for(const [fid,val] of Object.entries(v||{})){
    if(!val||val[0]==='适配') continue;
    const lc=LV[val[0]]||LV['谨慎'];
    out.push('<span title="'+fid+'：'+(val[1]||'')+'" style="display:inline-block;background:'+lc[0]+';color:'+lc[1]+';border-radius:4px;font-size:9.5px;padding:0 5px;margin-left:5px">'+val[0]+'·'+({operation:'营运',winter_temp:'气候带',annual_km:'年里程',single_trip:'单次里程',long_trip_freq:'长途频率',home_charger:'充电',plate_limit:'限牌',hold_years:'持有',highway_ratio:'高速',charge_patience:'补能耐心',fleet:'车队'})[fid]+'</span>');
    if(out.length>=3) break;
  }
  return out.join('');
}

// 前置条件 → 能源形式适配结论（v10.53.0）：形式均分 + 画像自检，回答「我这种条件适合哪种能源」
function renderForm(rows,cond){
  const forms=recommendForm(rows,cond);
  const narr=document.getElementById('formNarr');
  if(narr) narr.innerHTML='<b>结论：</b>'+formNarrative(forms,cond)+
    '<div class="formula">形式均分 = 池内该能源车型综合分均值（产品力 + 场景调节）；仅作形式层面参考，逐车结论仍以下方主榜为准</div>';
  const grid=document.getElementById('formGrid');
  if(grid){
    grid.innerHTML=forms.map(function(f){
      const cardCls=f.label==='最适配'?'form-card top':'form-card';
      const bCls=f.label==='不建议'?'fc-badge bad':(f.label==='最适配'?'fc-badge':'fc-badge mid');
      const col=PM[f.power]?PM[f.power].color:'#888';
      return '<div class="'+cardCls+'"><div class="fc-head"><span class="pill" style="background:'+col+'">'+
        (PM[f.power]?PM[f.power].short:f.power)+'</span><span class="'+bCls+'">'+f.label+'</span>'+
        '<span style="margin-left:auto;font-size:10.5px;color:#5b6470">均分</span></div>'+
        '<div class="fc-avg">'+f.avg.toFixed(1)+'</div>'+
        '<div class="fc-sub">池内 '+f.n+' 款 · 场景调节均值 '+(f.avg_adj>0?'+':'')+f.avg_adj.toFixed(2)+
        (f.hard?' · <span style="color:#b42318">'+f.hard+' 款硬条件</span>':' · 无硬条件')+
        '<br>最强一款：'+f.best+'</div></div>';
    }).join('');
  }
  const diag=document.getElementById('profileDiag');
  if(diag){
    const ws=checkProfile(cond);
    diag.innerHTML=ws.length
      ? '<b>画像自检（'+ws.length+' 条）：</b>'+ws.map(function(w){
          return '<div class="d-item '+(w.level==='warn'?'warn':'info')+'">'+(w.level==='warn'?'⚠ ':'· ')+w.text+'</div>';}).join('')
      : '<b>画像自检：</b><span style="color:#1a7f37">条件自洽，未发现矛盾组合</span>';
  }
}

__ENGINE_JS__

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
    const tc=tco(c,cond);
    // 档位：基础按综合分，触发硬条件降一档
    let tier=compositeTier(c.product+adj);
    if(hardDown) tier=tier==='T1'?'T2':(tier==='T2'?'T3':'T3');
    return {...c, eff:rc?rc.range:null, rc, adjTotal:a.total, adj, capped, adjItems:a.items,
            hard:a.hard, tier, composite:c.product+adj, tco:tc, verdicts:a.verdicts};
  });

  const k=carSort.k;
  rows.sort((x,y)=>{
    let a=x[k],b=y[k];
    if(a==null)a=-1;if(b==null)b=-1;
    if(typeof a==='string')return carSort.dir*a.localeCompare(b,'zh');
    return carSort.dir*(a-b);
  });
  rows.forEach((r,i)=>r.rank=i+1);
  // v10.53.0：形式聚合结论 + 画像自检（随条件实时更新，属推演态）
  renderForm(rows,cond);
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
    const be=breakevenKm(CARS,cond);
    const ber=(typeof breakevenKmRange==='function')?breakevenKmRange(CARS,cond):null;
    if(be!=null) document.getElementById('summary').innerHTML+='<div class="formula">家充纯电 vs 池内最优燃油/油混：8年TCO盈亏平衡约 <b>'+be+' km/年</b>'
      +(ber&&ber.low!=null?'（非能源成本估算区间下 '+ber.low+'~'+ber.high+' km/年）':'')
      +'（低于此里程油车/油混更省，以上纯电更省）</div>';
  }else{
    document.getElementById('summary').innerHTML='当前过滤条件下无匹配车型，请放宽预算区间或车身类型。';
  }

  document.getElementById('carCount').textContent='（'+rows.length+' 款符合条件）';
  const tb=document.getElementById('carBody'); tb.innerHTML='';
  if(!rows.length) tb.innerHTML='<tr><td colspan="14" style="text-align:center;color:#888;padding:24px">无匹配车型</td></tr>';
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
    // 车型名旁的警示/换电徽章 + 逐条件判定徽章（非适配项，最多3枚）
    const badges=(r.swap?'<span style="display:inline-block;background:#e7f4ea;color:#1a7f37;border-radius:4px;font-size:9.5px;padding:0 5px;margin-left:5px">换电</span>':'')+
      (r.hard.some(h=>h.downgrade)?'<span style="display:inline-block;background:#fbe9e7;color:#b42318;border-radius:4px;font-size:9.5px;padding:0 5px;margin-left:5px">⚠硬条件不符</span>':'')+
      verdictChips(r.verdicts);
    const tierCls=r.tier==='T1'?'t1':(r.tier==='T2'?'t2':'t3');
    tr.innerHTML=
      '<td>'+r.rank+'</td><td><b>'+r.model+'</b>'+badges+'</td>'+
      '<td><span class="tier-badge '+tierCls+'">'+r.tier+'</span></td><td>'+r.cls+'</td>'+
      '<td><span class="pill" style="background:'+PM[r.power].color+'">'+PM[r.power].short+'</span></td>'+
      '<td class="num">'+(r.price!=null?r.price:(r.priceLow+'–'+r.priceHigh))+'</td>'+
      '<td class="num">'+(r.cltc||'—')+'</td>'+
      '<td class="num">'+(r.cons?r.cons:'—')+'</td>'+
      '<td class="num">'+(r.eff!=null&&r.rc?
        '<details class="adj"><summary>'+r.eff+' km</summary><div class="adjbox"><b>续航估算（'+r.rc.method+'）</b><br>'+
        r.rc.steps.map(s=>'· '+s).join('<br>')+'</div></details>' :
        (r.power==='BEV'?'<span title="无电量数据，未编造续航">数据待核</span>':'<span title="有油箱兜底，不计算纯电续航">—</span>'))+'</td>'+
      '<td class="num">'+r.product.toFixed(1)+'</td>'+
      '<td class="num"><details class="adj"><summary class="'+adjCls+'">'+adjTxt+'</summary>'+
        '<div class="adjbox">调节明细（软 '+(r.adjTotal>0?'+':'')+r.adjTotal.toFixed(1)+'；硬条件不缩放）<br>'+detail+cap+hardHtml+'</div></details></td>'+
      '<td class="num"><details class="adj"><summary>'+(r.tco?(r.tco.tco8y/10000).toFixed(1)+'万':'—')+'</summary>'+
        '<div class="adjbox"><b>8年持有成本测算（仅披露不入分）</b><br>'+tcoHtml(r.tco)+'</div></details></td>'+
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
  document.getElementById('pLow').value=__DEF_LOW__;
  document.getElementById('pHigh').value=__DEF_HIGH__;
  document.getElementById('bodyFilter').value='__DEF_BODY__';
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


