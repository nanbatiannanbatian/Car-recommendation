#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""price_model.py —— 持有成本参数的「估算模块」（v10.60.0）

定位：**只估算成本参数，不产生结论**。它把 TCO 里那些原本拍死的常量（保费、轮胎、
保养、易损、残值、油价、电价）变成结构化的「参数带」：

    {low, mid, high, unit, tier, as_of, source}

三方交叉（业内专业口径 / 权威统计 / 大众实价），mid 用于计算，low~high 用于区间与敏感性。
任何一项被质疑，改这里一处即可，engine.tco() / 页面 / 回归全部跟着走。

⛔ 纪律：抓到的原始资料（网页、报价页、帖子）**只落 runs/_price_sources/**，
不入包、不入 git、不打包进 zip；本文件只带「来源摘要」——机构+标题+日期+URL+取值口径。
原始证据留档在本地可复核，发布物只有参数与公式。

⚠️ 这不是「价格采集器」：轮胎/保费的真实成交价高度个性化（规格、渠道、地区、NCD、承保方案），
追精确价必然不可复现。模块输出的是**费率带**，并明确标注哪些是估算、该怎么替换。
"""

# ============================================================ 来源摘要（只带指针，不带原文）

SEARCH_DATE = "2026-11-24"

SOURCES = {
    # 权威（政府/协会/精算）
    "oil": {"tier": "权威", "org": "各省发改委成品油调价（2026-11-12 起执行）",
            "title": "广东省/天津市/山东省汽柴油最高零售价格表",
            "url": "http://drc.gd.gov.cn/ywgg/content/mpost_4979520.html",
            "note": "92# 汽油：广东 8.39、天津 8.36、山东 8.26 元/升；海南 9.48 含特殊附加费，不纳入区间"},
    "elec_home": {"tier": "权威", "org": "浙江省发改委/国网浙江电力（居民自建充电设施）",
                  "title": "居民合表用电峰谷分时价格表",
                  "url": "https://fzggw.zj.gov.cn/art/2023/12/19/art_1229123366_2501768.html",
                  "note": "不满 1kV：电度 0.558，峰 0.588，谷 0.308 元/kWh；1-10kV 谷 0.288"},
    "residual": {"tier": "权威", "org": "中国汽车流通协会 + 精真估",
                 "title": "2026 上半年中国汽车保值率报告（6 月/7 月月报同期口径）",
                 "url": "http://m.jingzhengu.com/information/detail?id=25275",
                 "note": "三年保值率：纯电 45.9%、插混 45.3%（2026-05 月报）；级别基准 紧凑型车 48.30、紧凑型SUV 50.25、MPV 55.45"},
    "ins_ratio": {"tier": "权威", "org": "中国精算师协会 / 中国银保信（经新华网、36氪引述）",
                  "title": "新能源车险赔付与定价：车均风险成本约为燃油车 2.2 倍",
                  "url": "https://www.news.cn/fortune/20260604/fc80cc2d36c145e3abd0f6e5b9923a44/c.html",
                  "note": "同级纯电年保费溢价 15%~21%；10-20 万主流电车每年贵 1200~2000 元，30 万以上差价 3000+"},
    # 行业（媒体实测/账本）
    "maint": {"tier": "行业", "org": "汽车产经网 / 新浪汽车 / 易车（4S 与第三方实测账本）",
              "title": "6 万公里保养成本对照：轩逸约 4000+、朗逸约 5200、秦PLUS EV 约 2300~2400",
              "url": "https://m.autoreport.cn/hao/wenzhang/114525079/",
              "note": "纯电 400~700/年、插混/增程 800~1800、燃油 1500~2500；4S 比第三方贵 30%~50%"},
    "wear": {"tier": "行业", "org": "新浪汽车 / 36氪（维修成本口径）",
             "title": "新能源事故维修均价约为燃油车 1.7 倍；一体化压铸+雷达标定推高单次赔付",
             "url": "https://m.36kr.com/p/3870697532953606",
             "note": "本模块只取「非事故」易损与电子件年预算，事故维修由保费吸收、不重复计"},
    "elec_public": {"tier": "行业", "org": "公共快充价格通行区间（运营商结算口径）",
                    "title": "公共快充 1.2~2.0 元/kWh，含服务费与峰段溢价",
                    "url": "https://www.zj.sgcc.com.cn/m/2c9487058e8492e50190a64aeb6b2f02.html",
                    "note": "按场站/时段差异极大，仅作口径上限参考，需按所在城市替换"},
    "tco_method": {"tier": "业内", "org": "ICCT（国际清洁交通委员会）",
                   "title": "中国电动汽车成本收益评估（2020-2035）/ 商用车 TCO 案例",
                   "url": "https://www.theicct.org.cn/2021/04/14/%e4%b8%ad%e5%9b%bd%e7%94%b5%e5%8a%a8%e6%b1%bd%e8%bd%a6%e6%88%90%e6%9c%ac%e6%94%b6%e7%9b%8a%e8%af%84%e4%bc%b0%ef%bc%882020-2035/",
                   "note": "TCO 口径含购置/能源/维保/保险/税费/残值，并给敏感性区间；不报单点「精确价」"},
    # 大众（电商实价抽样）
    "tire": {"tier": "大众", "org": "淘宝/天猫养车 米其林等品牌实价抽样（2026-11-24）",
             "title": "225/50R18：单条 430~1057 元；16 寸经济胎约 300~450/条",
             "url": "https://mobile-phone.taobao.com/chanpin/db50962f74e0c24ee344118c6207ed33bf1fb9ae4e229afe27fafca7517c302d.html",
             "note": "一套 4 条按尺寸档：16 寸 1600、17 寸 2200、18 寸 3200、19 寸 4000、20 寸 5000；含安装/动平衡"},
}

# ============================================================ 参数带（mid 用于计算，low/high 用于区间）

BAND = 0.20   # 无法给出明确上下限的参数，默认 ±20% 估算带

PARAMS = {
    # —— 能源单价 ——
    "oil":        {"cn": "92# 汽油单价", "unit": "元/升", "low": 8.26, "mid": 8.36, "high": 8.39, "src": ["oil"]},
    "elec_home":  {"cn": "家充谷电", "unit": "元/kWh", "low": 0.288, "mid": 0.308, "high": 0.35, "src": ["elec_home"]},
    "elec_work":  {"cn": "单位/公司充电", "unit": "元/kWh", "low": 0.60, "mid": 0.75, "high": 0.90, "src": ["elec_home"]},
    "elec_public": {"cn": "公共快充", "unit": "元/kWh", "low": 1.20, "mid": 1.50, "high": 2.00, "src": ["elec_public"]},
    # —— 保险：按「固定部分 + 车价 × 费率」× 能源系数 × 8 年续保均值（低车价保费占比天然更高）——
    "ins_fixed":  {"cn": "保费固定部分", "unit": "元/年", "low": 1500, "mid": 1800, "high": 2100, "src": ["ins_ratio"]},
    "ins_rate":   {"cn": "保费车价费率", "unit": "比例", "low": 0.015, "mid": 0.018, "high": 0.021, "src": ["ins_ratio"]},
    "ins_renew":  {"cn": "8 年续保均值系数", "unit": "系数", "low": 0.82, "mid": 0.88, "high": 0.94, "src": ["ins_ratio"]},
    # —— 保养（元/年）——
    "maint":      {"cn": "保养年费", "unit": "元/年", "low": {"BEV": 400, "PHEV": 800, "EREV": 800, "HEV": 900, "ICE": 1500},
                   "mid": {"BEV": 500, "PHEV": 1200, "EREV": 1200, "HEV": 1000, "ICE": 2000},
                   "high": {"BEV": 700, "PHEV": 1800, "EREV": 1800, "HEV": 1200, "ICE": 2500}, "src": ["maint"]},
    # —— 非事故易损/电子件（元/年）——
    "wear":       {"cn": "易损与电子件年预算", "unit": "元/年", "low": {"BEV": 960, "PHEV": 800, "EREV": 800, "HEV": 720, "ICE": 720},
                   "mid": {"BEV": 1200, "PHEV": 1000, "EREV": 1000, "HEV": 900, "ICE": 900},
                   "high": {"BEV": 1440, "PHEV": 1200, "EREV": 1200, "HEV": 1080, "ICE": 1080}, "src": ["wear"]},
    # —— 轮胎：全套 4 条按轮毂尺寸档（元/套）——
    "tire_set":   {"cn": "轮胎整套价", "unit": "元/套",
                   "low": {"16": 1300, "17": 1800, "18": 2600, "19": 3300, "20": 4100},
                   "mid": {"16": 1600, "17": 2200, "18": 3200, "19": 4000, "20": 5000},
                   "high": {"16": 1900, "17": 2600, "18": 3800, "19": 4700, "20": 5900}, "src": ["tire"]},
    "tire_life":  {"cn": "轮胎寿命", "unit": "km/套", "low": {"BEV": 30000, "PHEV": 40000, "EREV": 40000, "HEV": 50000, "ICE": 50000},
                   "mid": {"BEV": 40000, "PHEV": 50000, "EREV": 50000, "HEV": 60000, "ICE": 60000},
                   "high": {"BEV": 50000, "PHEV": 60000, "EREV": 60000, "HEV": 70000, "ICE": 70000}, "src": ["wear"]},
    # —— 残值：级别基准 + 能源修正（pt，百分点）——
    "residual_base": {"cn": "级别三年保值率", "unit": "比例",
                      "low": {"轿车": 0.453, "SUV": 0.472, "MPV": 0.520},
                      "mid": {"轿车": 0.483, "SUV": 0.5025, "MPV": 0.5545},
                      "high": {"轿车": 0.513, "SUV": 0.533, "MPV": 0.589}, "src": ["residual"]},
    "residual_delta": {"cn": "能源形式残值修正", "unit": "pt",
                       "low": {"BEV": -0.054, "PHEV": -0.060, "EREV": -0.060, "HEV": -0.020, "ICE": -0.030},
                       "mid": {"BEV": -0.024, "PHEV": -0.030, "EREV": -0.030, "HEV": 0.010, "ICE": 0.0},
                       "high": {"BEV": 0.006, "PHEV": 0.0, "EREV": 0.0, "HEV": 0.040, "ICE": 0.030}, "src": ["residual"]},
    # —— 电池风险金（元/年，中保研零整比口径）——
    "battery_risk": {"cn": "电池风险金", "unit": "元/年", "low": {"BEV": 1800, "PHEV": 500, "EREV": 500, "HEV": 0, "ICE": 0},
                     "mid": {"BEV": 2500, "PHEV": 800, "EREV": 800, "HEV": 0, "ICE": 0},
                     "high": {"BEV": 3300, "PHEV": 1200, "EREV": 1200, "HEV": 300, "ICE": 200}, "src": ["ins_ratio"]},
}

# 保费能源系数（倍于同价燃油车）——权威口径 1.15~1.21 溢价 → 取 1.35 覆盖「车价相同但电车车损更高」的实测差价
INS_MULT = {"low": {"BEV": 1.30, "PHEV": 1.28, "EREV": 1.22, "HEV": 1.03, "ICE": 1.0},
            "mid": {"BEV": 1.45, "PHEV": 1.45, "EREV": 1.40, "HEV": 1.05, "ICE": 1.0},
            "high": {"BEV": 1.60, "PHEV": 1.58, "EREV": 1.52, "HEV": 1.08, "ICE": 1.0}}

# 轮毂尺寸档 ← 车价档（车价越低轮毂越小；同级电车普遍比油车轮毂大 1~2 寸，靠这一档自然体现）
TIRE_SIZE_BY_PRICE = [(8, "16"), (10, "17"), (25, "18"), (35, "19")]   # (车价上限万元, 尺寸)，其余 → 20

YEARS = 8
TAX_NEV_RATE = 0.05      # 2026-2027 新能源购置税减半
TAX_NEV_CAP = 15000      # 减税额上限
TAX_ICE_RATE = 0.10


def _pick(param_id, level, power=None):
    p = PARAMS[param_id]
    v = p[level]
    if isinstance(v, dict):
        return v.get(power, v.get("ICE")) if power else v
    return v


def band(param_id, level="mid", power=None):
    return _pick(param_id, level, power)


def tire_size(price_wan):
    """车价（万元）→ 轮毂尺寸档。取不到车价按 17 寸（最常见的家用车规格）。"""
    try:
        p = float(price_wan or 0)
    except (TypeError, ValueError):
        p = 0.0
    if p <= 0:
        return "17"
    for cap, size in TIRE_SIZE_BY_PRICE:
        if p <= cap:
            return size
    return "20"


def residual_rate(power, body=None, level="mid"):
    """三年保值率：级别基准 + 能源修正，clamp 到 [0.30, 0.75]。"""
    base = _pick("residual_base", level)
    key = body if body in base else ("SUV" if "SUV" in str(body or "") else "轿车")
    r = base.get(key, base.get("轿车", 0.483)) + _pick("residual_delta", level, power)
    return max(0.30, min(0.75, round(r, 4)))


def rates(power, price_wan, body=None, level="mid", scales=None):
    """给定车的动力与车价 → 该车适用的成本参数（元/年、元/套、比例）。

    scales 用于敏感性分析：{"ins_rate": 1.2, "residual_delta": ...} 会按键缩放（仅对数值项）；
    对比例类（残值）按「偏差点」缩放更合理，故单独用 residual_shift。
    """
    sc = scales or {}
    price = float(price_wan or 0) * 10000.0
    ins_annual = (band("ins_fixed", level) + price * _pick("ins_rate", level)) * INS_MULT[level].get(power, 1.0) * _pick("ins_renew", level)
    size = tire_size(price_wan)
    r = {
        "power": power,
        "ins_annual": ins_annual,
        "maint_annual": _pick("maint", level, power),
        "wear_annual": _pick("wear", level, power),
        "tire_price4": _pick("tire_set", level)[size],
        "tire_size": size,
        "tire_life_km": _pick("tire_life", level, power),
        "residual_rate": residual_rate(power, body, level),
        "battery_risk_annual": _pick("battery_risk", level, power),
    }
    for k, f in sc.items():
        if k == "residual_shift":       # 残值按「百分点」平移，不做乘法
            r["residual_rate"] = max(0.20, min(0.80, round(r["residual_rate"] + f, 4)))
            continue
        if k in r and isinstance(r[k], (int, float)) and not isinstance(f, bool):
            r[k] = r[k] * f
    return r


def provenance():
    """页面/文档用的来源摘要（按参数分组，只带指针与口径，不带原文）。"""
    rows = []
    for pid, p in PARAMS.items():
        rows.append({
            "id": pid, "cn": p["cn"], "unit": p["unit"],
            "low": p["low"], "mid": p["mid"], "high": p["high"],
            "sources": [{"tier": SOURCES[s]["tier"], "org": SOURCES[s]["org"],
                         "as_of": SEARCH_DATE, "url": SOURCES[s]["url"]} for s in p["src"]],
        })
    return rows


def summary_line():
    """一句话口径（写进 basis）：说明这是费率带不是报价，以及关键来源。"""
    return ("成本参数为「费率带（low~high）」而非报价：油价取各省发改委 2026-11-12 调价（92# 8.26~8.39）、"
            "家充谷电取浙江居民充电设施谷段 0.308（0.288~0.35）、保险按车价×费率×能源系数（精算协会："
            "同级纯电保费溢价 15%~21%）、残值用流通协会+精真估三年保值率（级别基准 + 能源修正）、"
            "轮胎按轮毂尺寸档实价抽样（16~20 寸 1600~5000/套）、保养取 4S 实测账本中位；"
            "事故维修不计入（由保费吸收），电池风险金另列。")


def render_python():
    """把参数带与来源摘要生成「发布版常量文件」cost_params.py。

    发布包里**不含本文件**（price_model.py 是开发期计算器），只含它生成的
    cost_params.py —— 即「算出来的结果 + 口径解释」。以后参数变好，重跑
    `python -m modules.envscore.price_model --emit` 再打包即可。
    """
    import json
    payload = {
        "PARAMS": {k: {"cn": v["cn"], "unit": v["unit"], "low": v["low"], "mid": v["mid"], "high": v["high"]}
                   for k, v in PARAMS.items()},
        "INS_MULT": INS_MULT,
        "TIRE_SIZE_BY_PRICE": TIRE_SIZE_BY_PRICE,
        "YEARS": YEARS,
        "TAX_NEV_RATE": TAX_NEV_RATE,
        "TAX_NEV_CAP": TAX_NEV_CAP,
        "TAX_ICE_RATE": TAX_ICE_RATE,
    }
    srcs = [{"id": k, "tier": v["tier"], "org": v["org"], "title": v["title"], "url": v["url"], "note": v["note"]}
            for k, v in SOURCES.items()]
    head = '''# -*- coding: utf-8 -*-
"""cost_params.py —— 持有成本参数的**发布版常量**（开发期由 price_model.py 生成，勿手改）

为什么是这个结构：参数估算（查资料、取实价、定费率带）是开发期的事，不进发布包；
发布包只需要「算出来的结果 + 口径解释」。所以：
  - 本文件 = 参数 mid 值（引擎直接算）+ low/high（页面给区间）+ 来源摘要（机构/日期/口径）；
  - 原始抓取（网页、报价页、帖子）只留本地 runs/_price_sources/，不入包、不入 git；
  - 参数变好时：改 price_model.py → `python -m modules.envscore.price_model --emit` → 重新打包。
回归 `tco_cost_regress.py` 会校验本文件与 price_model 生成结果一致（防止漂移）。
"""
import json as _json  # noqa: F401  (保持手写文件风格一致)
'''
    body = "\n".join([
        "PARAMS = _json.loads(r'''%s''')" % json.dumps(payload["PARAMS"], ensure_ascii=False, indent=2),
        "INS_MULT = _json.loads(r'''%s''')" % json.dumps(payload["INS_MULT"], ensure_ascii=False, indent=2),
        "TIRE_SIZE_BY_PRICE = _json.loads(r'''%s''')" % json.dumps(payload["TIRE_SIZE_BY_PRICE"], ensure_ascii=False),
        "YEARS = %d" % YEARS,
        "TAX_NEV_RATE = %r" % TAX_NEV_RATE,
        "TAX_NEV_CAP = %d" % TAX_NEV_CAP,
        "TAX_ICE_RATE = %r" % TAX_ICE_RATE,
        "SOURCES = _json.loads(r'''%s''')" % json.dumps(srcs, ensure_ascii=False, indent=2),
        'BASIS = %r' % summary_line(),
        'AS_OF = %r' % SEARCH_DATE,
    ])
    return head + "\n" + body + "\n"


def write_cost_params(path=None):
    import os
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "cost_params.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_python())
    return path


if __name__ == "__main__":
    import sys
    if "--emit" in sys.argv:
        print("written:", write_cost_params())
    else:
        import json
        print(json.dumps(provenance(), ensure_ascii=False)[:800])
        for p in ("BEV", "PHEV", "HEV", "ICE"):
            print(p, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in rates(p, 14.0, "轿车").items()})

