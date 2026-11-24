# -*- coding: utf-8 -*-
"""engine.py —— 跨能源「环境分 / 用车条件适配」引擎（Python 单一事实源，v10.55.0）

为什么有这个模块（2026-11-24 用户要求优化跨能源比较）：
  原引擎规则只活在 engine.js（浏览器侧），定稿榜 14 区块靠"页面首次加载用 JS
  算一次"——定稿结论不可服务端复现、不可 Python 回归审计，与项目「定稿榜=可复现
  结论」纪律冲突。本次优化：
  ① 规则移植到 Python（本模块）= 规则单一事实源；engine.js 保持浏览器交互实现，
     新增 env_equivalence_regress 双向等价回归（node 实跑 JS vs Python：同一批
     fixtures × 画像，adjust/rangeCalc/tco 必须一致）——防两套口径漂移。
  ② 因素 12→15：新增 summer_temp（夏季高温/暴晒）、charge_patience（途中补能耐心）、
     outdoor_camp（露营/外放电需求）；plate_limit 选项细分（普通限牌/北京/上海——
     北京插混/增程按油车管理需燃油指标、上海插混无免费绿牌，2026 政策）。
  ③ 硬条件 +1：H7 限牌京沪 & 插混/增程 −8（上牌优势丧失，全额+降档+红标）。
  ④ 软调节 +5：纯电优先原则（无硬条件冲突时 BEV +0.5，用户明确「纯电尽量纯电，
     条件不符再扣分」）、夏季高温、补能耐心、露营外放电、低里程无桩纯电（TCO 固定
     成本摊薄慢）。
  ⑤ 新增持有成本测算 TCO（8 年：能源+保险+保养+购置税+残值，**仅披露不入分**——
     事实列不计分纪律）；盈亏平衡里程随画像动态计算。
  ⑥ 新增逐条件适配判定 verdicts（适配/谨慎/不建议 + 理由）——「根据条件告诉用户
     适合什么车」的代码化。
  ⑦ 前置条件门禁：B 类混合池必须带 usage_profile（构建器断言，缺则拒出榜）。

v10.53.0（2026-11-24 第二轮优化，用户「再搜集一些因素」）：
  ⑧ 因素 15→20：新增 fleet（家庭车队结构/是否增购第二辆）、terrain（地形：平原/山区/
     高原）、parking（停放环境：地库/露天）、park_idle（驻车用电：等人/午休）、
     elec_price（家充电价档）；home_charger 增加 work 档（单位/公司可充电——无家充
     但单位能充，纯电照样可行，此前被错当成"无固定充电"直接 −15）。
  ⑨ 物理续航新增两项乘子：地形（山区 +10%、高原 +6%）与露天停放冬季（+5% 场景电耗）
     ——地库 10℃ vs 露天 −12℃ 次日续航实测差约 70km，不能只按环境温度算。
  ⑩ 新增画像自检 check_profile（矛盾组合诊断：营运+8年持有、纯电+无桩+极寒+高频长途等）
     与能源形式聚合结论 recommend_form（按形式算均分/硬条件率/不建议项数 → 推荐哪种形式）。
  ⑪ TCO 增加电费档位（谷电0.35/居民0.55/物业商用0.90）与驻车怠速成本（燃油怠速空调
     1.6-2.5L/h）；盈亏平衡随电价档变化，避免「1.5万公里」结论变成无口径的传说。

v10.55.0（2026-11-24 第三轮，用户「用车条件太多了，哪些不必要的删了」）：
  ⑫ 因素 20→13：删掉 summer_temp / drive_style / ac_usage / parking / park_idle /
     outdoor_camp / elec_price 七项。删除判据是实测量化（45 款五形式池，逐项遍历选项）：
     这些项要么只造成 ≤2 分的同向微调、前十名次基本不变（驾驶风格/空调/夏季高温），
     要么对分数完全无影响只改 TCO 展示（电价档），要么与已保留项语义重复（露天停放≈冬季温度）。
     电价档改固定谷电口径，避免同一套车换个电价档就换一套结论——可复现优先。
  ⑬ 保留 13 项：operation / home_charger / winter_temp / annual_km / single_trip /
     long_trip_freq / plate_limit / hold_years / highway_ratio / boxy / charge_patience /
     fleet / terrain——都有硬条件或能翻转能源形式结论。

v10.56.0（2026-11-24 第四轮，用户逐项提审：
  「年里程加 5000km 以下档 / 限牌去掉 / 地形去掉 / 方盒子合并到车型」）：
  ⑭ 因素 13→11：删 terrain（地形）与 boxy（方盒子）。
     · terrain：无硬条件、单选项最大影响 2.3 分、前十全保留；山区/高原是路线级平均（每爬升100m
       约0.5kWh，丘陵+10~20%、高山+25~40%），用一次条件去近似一条具体路线必然偏，且用户无法
       自评准确——按"问一项就得换一个结论"的判据删掉，改在方法区与续航算式旁提示"自行上浮
       电耗 10~25%"，不再假装能算准。TERRAIN_MULT / ENGINE 常量留存但不再被条件驱动。
     · boxy：本质是车型属性（风阻/车身形式），不是用车条件；让用户替车型回答风阻问题，
       等于把客观参数变成主观勾选。改为：高速倍率只按车型自身 cdaPerTon/cd 算（Cd 高的车自然更费），
       并在 build_energy_compare 的「车身形式」筛选里按数据区分 轿车 / SUV / MPV / 越野车 / 方盒子SUV
       （枚举从池内实际取值派生，池里没有就不显示，避免空选项）。
  ⑮ 年里程新增 under5（5000km/年以下）档：本引擎算出的盈亏平衡点约 4838.7km/年（谷电口径），
     5000km 以下正好落在平衡点左侧，是真实分界而非人为细分。规则：纯电 −2.0、燃油/油混 +2.0，
     且"低里程+无桩"追加减分由 −1 升到 −1.5。
  ⑯ 限牌城市（plate_limit）保留不删——它是 7 条硬条件里唯一能直接判"这种车在当地能不能用"的项：
     H5 燃油/油混 −8、H7 京沪插混/增程 −8，删掉会让限牌城市用户看到油车进前十（结论级错误）。
     实测它触发 33 台硬条件、单项最大 Δ8.0，属"必须问"的一类。

信源（研究代理 2026-11-24 网搜交叉核实，引用细节见 references/用车条件因素库.md）：
  油价 92#≈8.36 元/L（发改委 2026-11 调价口径）；家充谷电≈0.35 元/度、公充≈1.5 元/度、
  单位充电≈0.75 元/度（比亚迪园区 2026-05 改按工商业峰谷价+0.1 元/度服务费）；
  BEV 冬季续航达成率 -10℃约×0.68 / -20℃约×0.50（中汽研 67 款均值衰减 32.7%、
  汽车之家冬测）；PHEV 高速亏电 3.3-5.5L vs EREV 7-9L（媒体实测）；HEV 市区 4.2-5.2L；
  营运车 BEV 渗透率 76%（2026-03，全国网约车）；NEV 保费高 15-20%（精算师协会 2026-03）；
  3 年保值率 BEV 45.1%/PHEV 42.2%/HEV 约 50/ICE 46.07%（流通协会+精真估 2026）；
  购置税 2026-2027 NEV 减半 5%（减税额≤1.5 万封顶）vs ICE/HEV 10%（购置税法）；
  以旧换新 NEV 报废 12%封顶2万/置换 8%封顶1.5万 vs ICE 10%/6%（商务部 2026 细则）；
  山区每爬升 100m 约耗 0.5kWh（1.7t 电车口径）、丘陵路线电耗 +10~20%、高山 +25~40%；
  露天停放冬季续航比地库低约 15~20%（-10~-12℃ 隔夜实测量差约 70km）；
  燃油车怠速开空调 1.5-2.0L 排量约 1.6-2.5L/h（12-22 元/h），HEV 约 0.2-0.3L/h，
  纯电驻车空调约 1-1.5kWh/h；增购用户占新能源购买 28.1%、两车及以上家庭 31.3%
  （J.D. Power 2025）；≥35℃ 暴晒纯电空调+电池散热电耗 +10~15%。

纪律：本模块零 IO、零网络、纯函数；依赖仅 standard library。
"""
from __future__ import annotations

# ============ 1. 用车条件因素表（15 项，单一事实源）============
# build_energy_compare.py 的 FACTORS 与 engine.js 的条件下拉都从本表注入/对齐，
# 回归 env_options 等价测试守每一项。
FACTORS = {
    "operation": {
        "label": "是否营运", "default": "no",
        "desc": "营运车辆（网约/出租）以纯电为主，运营成本是核心",
        "options": [
            {"v": "no",  "label": "非营运（家用为主）", "hint": "按家用场景综合权衡"},
            {"v": "yes", "label": "营运（网约/出租）",  "hint": "高频行驶+成本敏感，纯电加分、燃油/油混减分"},
        ],
    },
    "winter_temp": {
        "label": "冬季气候带（常驻城市）", "default": "m10to0",
        "desc": "先按你常驻城市的气候带选，括号里的温度只用于续航计算：−10℃约衰减20–45%，−20℃可达40–60%",
        "options": [
            {"v": "above0",  "label": "南方（冬季极少≤0℃）", "hint": "系数1.00，纯电几乎无冬季衰减 · 代表城市：广州/深圳/福州/南宁/厦门/海口（含滇南、桂南）"},
            {"v": "m10to0",  "label": "江淮/长江流域（0~-10℃）", "hint": "系数1.05（默认）· 代表城市：上海/杭州/南京/武汉/成都/重庆/长沙；你所在城市极端最低温 -5℃ 左右选这档"},
            {"v": "m20tom10","label": "华北/黄淮（-10~-20℃）", "hint": "系数1.12，纯电衰减明显 · 代表城市：北京/天津/石家庄/济南/郑州/西安/太原（极端 -12℃ 左右）"},
            {"v": "underm20","label": "东北/内蒙/新疆北部（≤-20℃）", "hint": "系数1.20，纯电续航可能腰斩 · 代表城市：哈尔滨/长春/沈阳/呼和浩特/乌鲁木齐/拉萨；辽东半岛沿海（大连约-15℃）按华北档更接近"},
        ],
    },
    "annual_km": {
        "label": "年行驶里程", "default": "15to25",
        "desc": "成本平衡点约 4800km/年（2026 油价+家充谷电口径，见 TCO 区块）：低于此纯电连能源差额都补不回保险溢价与残值差",
        "options": [
            {"v": "under5",  "label": "5000 公里以下（仅周末代步）", "hint": "低于盈亏平衡点（约4800km/年）：纯电省不回保险/残值差，油车或油混更划算"},
            {"v": "under10", "label": "5000~1万公里",     "hint": "跑得少，纯电固定成本摊薄慢、油车/油混加分"},
            {"v": "10to15",  "label": "1万~1.5万公里",   "hint": "刚过平衡点，不调节"},
            {"v": "15to25",  "label": "1.5万~2.5万公里", "hint": "明显超过平衡点（默认）"},
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
        "desc": "家充是电车经济性的前提；但「单位能充」与「无固定充电」是两回事，不能一视同仁",
        "options": [
            {"v": "home",   "label": "有家用充电桩",       "hint": "纯电/插混/增程加分（默认）"},
            {"v": "work",   "label": "无家充·单位/公司可充", "hint": "白天补能、成本接近家充：纯电仍可行，不触发无桩硬条件"},
            {"v": "public", "label": "无家充但有稳定公充", "hint": "纯电谨慎：不额外硬扣分，但成本约家充3-5倍"},
            {"v": "none",   "label": "完全没有固定充电",   "hint": "纯电大幅减分，插混/增程减分，油车/油混加分"},
        ],
    },
    "plate_limit": {
        "label": "限牌城市", "default": "no",
        "desc": "限牌城市新能源可直接上牌；北京插混/增程按油车管理、上海插混无免费绿牌（2026 政策）",
        "options": [
            {"v": "no",       "label": "不限牌",                    "hint": "不调节（默认）"},
            {"v": "yes",      "label": "限牌（广深杭蓉等·插混可绿牌）", "hint": "新能源加分、燃油/油混减分"},
            {"v": "beijing",  "label": "北京（插混/增程按油车管理）",  "hint": "仅纯电绿牌；插混/增程/燃油/油混均扣分"},
            {"v": "shanghai", "label": "上海（插混无免费绿牌）",       "hint": "纯电免费绿牌；插混/增程/燃油/油混均扣分"},
        ],
    },
    "hold_years": {
        "label": "计划持有年限", "default": "5to8",
        "desc": "持有越久，残值影响越摊薄，电车的能源节省越明显",
        "options": [
            {"v": "under3", "label": "3 年内换车", "hint": "纯电折旧快，减分；油车/油混加分"},
            {"v": "3to5",   "label": "3~5 年",     "hint": "不调节"},
            {"v": "5to8",   "label": "5~8 年",     "hint": "默认"},
            {"v": "over8",  "label": "8 年以上",   "hint": "纯电加分（能源节省累积）"},
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
    "charge_patience": {
        "label": "途中补能耐心", "default": "tolerate",
        "desc": "纯电长途需中途充电（800V约10-20分钟、400V约30-40分钟，节假日排队另计）",
        "options": [
            {"v": "tolerate",  "label": "可接受充电等待",       "hint": "纯电长途补能时间成本可接受（默认）"},
            {"v": "reluctant", "label": "不愿等待（补能时间敏感）", "hint": "纯电长途明显减分、可油可电加分"},
        ],
    },
    # ===== v10.53.0 新增（用户「再搜集一些因素」：前置条件要把能源形式放对位置）=====
    "fleet": {
        "label": "家庭车辆结构", "default": "only",
        "desc": "「唯一用车」要扛全部场景；「家里已有油车再增购」则长途由油车兜底，纯电只负责日常——同一价位、同一环境分，结论可能完全不同",
        "options": [
            {"v": "only",   "label": "家里唯一用车（主力）", "hint": "所有场景都要它扛（默认）"},
            {"v": "second", "label": "增购第二辆（已有油车）", "hint": "纯电加分：长途/返乡交给油车，纯电管通勤接娃与周末近郊"},
        ],
    },
}
# 因素分组（页面渲染用；11 项：核心 9 平铺 + 进阶 2 折叠）
FACTOR_GROUPS = [
    ("核心能源决策", ["operation", "home_charger", "winter_temp", "annual_km", "single_trip",
                   "long_trip_freq", "plate_limit", "hold_years", "highway_ratio"],
     "以下 9 项直接决定燃油/混动/纯电谁更合适：营运与高频长途是纯电的硬门槛、家充/单位充电是油电分水岭、"
     "−10℃纯电平均衰减32.7%、年里程决定成本反超点、绿牌通行红利、短持有折旧差"),
    ("进阶条件", ["fleet", "charge_patience"],
     "以下 2 项在特定人群才翻转结论：增购第二辆（长途交给已有油车）、不愿在服务区等充电——"
     "没这些情况就保持默认，不影响结论"),
]

# 默认画像（与 FACTORS default 一致；定稿榜快照口径）
DEFAULT_COND = {k: v["default"] for k, v in FACTORS.items()}

# 逐条件判定矩阵展示用的核心因素（9 列，避免表过宽；其余在调节明细里体现）
CORE_FACTORS = ["operation", "winter_temp", "annual_km", "single_trip", "long_trip_freq",
                "home_charger", "plate_limit", "hold_years", "highway_ratio"]

# ============ 2. 物理续航模型常量（与 modules/physics/ev_range_model.py 对齐）============
DEMAND = {"under150": 100, "150to300": 220, "300to500": 400, "over500": 600}
MED_CDA_PER_TON = 0.314
HWY_BASE_MULT = 1.65
CITY_BASE_MULT = 1.10
HWY_CDA_WEIGHT = 0.45
TEMP_BATTERY = {"above0": 1.00, "m10to0": 1.05, "m20tom10": 1.12, "underm20": 1.20}
AC_BASE = {"above0": 1.08, "m10to0": 1.12, "m20tom10": 1.18, "underm20": 1.22}
AC_LEVEL = {"off": 0.90, "normal": 1.00, "heavy": 1.12}
DRIVE_MULT = {"mild": 0.90, "normal": 1.00, "aggressive": 1.18}
VOLTAGE_MULT = {400: 1.00, 600: 0.99, 800: 0.975, 900: 0.97}
HWY_MULT_CAP = (1.35, 2.35)
# v10.53.0：地形与停放环境对场景电耗的乘子（physical：爬坡重力做功 / 露天静置电池更冷）
TERRAIN_MULT = {"flat": 1.00, "hilly": 1.10, "plateau": 1.06}
# 露天停放按冬季档位加成的场景电耗乘子（地库维持基准；-10℃以下隔夜电池更冷）
PARK_OUTDOOR_MULT = {"above0": 1.00, "m10to0": 1.02, "m20tom10": 1.05, "underm20": 1.05}
OUTDOOR_WINTER_MULT = PARK_OUTDOOR_MULT["m20tom10"]   # 兼容旧引用

# 换电场景优势（据网搜共识）：无家充/北方极寒/短持有/长途
SWAP_ADVANTAGE = {
    "no_charger": {"gain": 2.5, "reason": "换电3分钟补能、接近加油，无家充痛点大幅缓解"},
    "short_hold": {"gain": 1.5, "reason": "车电分离、无电池衰减与残值焦虑，二手更保值"},
    "cold": {"gain": 1.5, "reason": "换电站电池恒温15-30℃，冬季补能不挨冻、不受低温影响"},
    "longtrip": {"gain": 1.0, "reason": "高速换电站补能快（依赖站点覆盖）"},
}

# ============ 3. 持有成本测算（TCO）常量——仅披露不入分 ============
# 信源见模块 docstring；口径：8 年持有，能源按画像年里程，残值用 3 年保值率近似（披露）
TCO = {
    "oil_price": 8.36,          # 元/L，92# 2026-11 发改委调价口径
    "elec_home": 0.35,          # 元/kWh，家充谷电（默认档；画像可选 flat/commercial 覆盖）
    "elec_public": 1.50,        # 元/kWh，公共快充均值
    "elec_work": 0.75,          # 元/kWh，单位/园区充电（工商业峰谷价+服务费，部分企业免费）
    # v10.53.0：家充电价档（「低里程油车更省」的结论随电价变化，必须可调）
    "elec_home_by": {"valley": 0.35, "flat": 0.55, "commercial": 0.90},
    "bev_kwh_per_100km": 16.0,  # 轿车电耗中位（事实库口径）
    "ice_fuel_l": 8.0,          # 燃油 WLTC 综合油耗中位（L/100km）
    "hev_fuel_l": 4.5,          # 油混 WLTC
    "phev_fuel_l": 5.0,         # 插混亏电油耗（直驱架构优秀值）
    "erev_fuel_l": 7.5,         # 增程亏电油耗（高速偏高口径）
    "ins_nev": 5500,            # 新能源车年均保费（家用口径，含电池险）
    "ins_ice": 3500,            # 燃油/油混年均保费
    "maint": {"BEV": 500, "PHEV": 1200, "EREV": 1200, "HEV": 1000, "ICE": 2000},
    "residual3": {"BEV": 0.451, "PHEV": 0.422, "EREV": 0.42, "HEV": 0.50, "ICE": 0.4607},
    # 电池风险准备金（元/年）：中保研第20期纯电电池单件零整比均值49.59%（部分>90%）、
    # 换电池4-15万、规范用车年均衰减1.8-2.3%；8年持有摊薄预期风险。披露项，可质疑可替换。
    "battery_risk": {"BEV": 2500, "PHEV": 800, "EREV": 800, "HEV": 0, "ICE": 0},
    # v10.53.0 驻车用电成本（披露）：每周多次车内开空调等人≈150h/年
    "idle_hours": 150,
    "idle_l_per_h": {"ICE": 2.0, "HEV": 0.3},   # 怠速开空调油耗 L/h（1.5-2.0L 排量实测区间）
    "idle_kwh_per_h": 1.5,                      # 纯电驻车空调电耗 kWh/h
    "tax_nev_rate": 0.05,       # 2026-2027 购置税减半 5%
    "tax_ice_rate": 0.10,       # 购置税法 10%（HEV 不在新能源目录，同油车口径）
    "years": 8,
}
ANNUAL_KM_MID = {"under5": 4000, "under10": 8000, "10to15": 12500, "15to25": 20000, "over25": 30000}

POWERS = ["ICE", "HEV", "PHEV", "EREV", "BEV"]


# ============ 4. 物理续航模型（与 engine.js rangeCalc 同源）============
def hwy_mult(car, cond):
    """高速倍率：基准1.65，按 CdA/吨 偏离中位数修正（车身形状由车型自身风阻承担，不再由「方盒子」条件修正）。"""
    r = car.get("cdaPerTon")
    if r is None:
        r = MED_CDA_PER_TON
    m = HWY_BASE_MULT * (1 + HWY_CDA_WEIGHT * (r / MED_CDA_PER_TON - 1))
    return max(HWY_MULT_CAP[0], min(HWY_MULT_CAP[1], m))


def range_calc(car, cond):
    """纯电有效续航（物理模型）。非纯电返回 None（有油箱兜底，不估算）。"""
    if car.get("power") != "BEV":
        return None
    hw_w = {"under20": 0.0, "20to40": 0.25, "40to60": 0.7, "over60": 1.0}[cond["highway_ratio"]]
    hw_m = hwy_mult(car, cond)
    scene_m = CITY_BASE_MULT * (1 - hw_w) + hw_m * hw_w
    temp_m = TEMP_BATTERY[cond["winter_temp"]]
    ac_m = AC_BASE[cond["winter_temp"]]      # 冬季暖风基础负载（不再按「空调使用强度」调节）
    volt_m = 1.0
    if car.get("voltage"):
        best = min(VOLTAGE_MULT, key=lambda v: abs(v - car["voltage"]))
        volt_m = VOLTAGE_MULT[best]
    terrain_m = 1.0                            # 地形已从条件表移除（见文件头 ⑭），乘子固定 1.0
    scene_cons = car["cons"] * scene_m * temp_m * ac_m * volt_m
    kwh = car.get("kwh")
    if not kwh:
        return {"range": None, "method": "无电量数据",
                "steps": ["该车型无电池电量数据，无法估算续航（不编造）"]}
    rng = round(kwh / scene_cons * 100)
    steps = [
        f"基准电耗 {car['cons']} kWh/100km（{car.get('consSrc','')}）",
        f"× 工况倍率 {scene_m:.2f}" + ("（偏高速）" if hw_w >= 0.5 else "（偏市区）"),
        f"× 温度(仅电池) {temp_m} × 暖风 {ac_m:.2f} × 电压系数 {volt_m}",
    ]
    steps += [
        f"= 场景电耗 {scene_cons:.1f} kWh/100km",
        f"电量 {kwh} kWh ÷ 场景电耗 ×100 = 有效续航 {rng} km",
    ]
    return {"range": rng, "method": car.get("consSrc") == "事实库" and "物理模型(真实电耗基准)" or "物理模型(估算电耗基准)",
            "sceneCons": round(scene_cons, 1), "steps": steps}


def eff_range(car, cond):
    r = range_calc(car, cond)
    return r["range"] if r else None


# ============ 5. 环境分 adjust（软调节+硬条件，与 engine.js 等价）============
def adjust(car, cond):
    """返回 {items, total, hard, verdicts}。
    items: 软调节明细 [{label,pt,na?}]；hard: 硬条件 [{label,pt,reason,downgrade}]；
    total: 软+硬合计（未缩放）；verdicts: 逐条件适配判定。"""
    items, hard = [], []
    total = 0.0

    def add(label, pt):
        nonlocal total
        if pt:
            items.append({"label": label, "pt": pt})
            total += pt

    def add_hard(label, pt, reason, downgrade=False):
        nonlocal total
        hard.append({"label": label, "pt": pt, "reason": reason, "downgrade": bool(downgrade)})
        total += pt

    P = car.get("power")
    is_swap = car.get("swap") is True
    price = car.get("price")

    # ===== 硬条件（大扣分 + 降档 + 红标）=====
    # H1 无固定充电 + 纯电（非换电）
    if cond["home_charger"] == "none" and P == "BEV":
        if is_swap:
            add_hard("无家充·换电缓解", SWAP_ADVANTAGE["no_charger"]["gain"],
                     SWAP_ADVANTAGE["no_charger"]["reason"])
        else:
            add_hard("无充电桩·纯电不推荐", -15,
                     "无固定充电，纯电日常补能困难、体验大幅打折；换电车型不受此限", True)
    # H2 极寒 + 低价纯电（≤15万）
    if cond["winter_temp"] == "underm20" and P == "BEV":
        if is_swap:
            add_hard("极寒·换电缓解", SWAP_ADVANTAGE["cold"]["gain"],
                     SWAP_ADVANTAGE["cold"]["reason"])
        elif price is not None and price <= 15:
            add_hard("极寒+低价纯电·谨慎", -12,
                     "-20℃以下低价纯电续航常腰斩、充电变慢，北方选购需谨慎；换电或大电池车型更稳", True)
        else:
            add_hard("极寒·换电优势", SWAP_ADVANTAGE["cold"]["gain"],
                     SWAP_ADVANTAGE["cold"]["reason"])
    # H3 短持有(2-3年) + 纯电
    if cond["hold_years"] == "under3" and P == "BEV":
        if is_swap:
            add_hard("短持有·换电合适", SWAP_ADVANTAGE["short_hold"]["gain"],
                     SWAP_ADVANTAGE["short_hold"]["reason"])
        else:
            add_hard("短持有·纯电折旧快", -8,
                     "2-3年换车，纯电折旧快、残值损失大；换电车型车电分离更合适", True)
    # H4 长途高频 + 纯电续航明显不足
    if cond["long_trip_freq"] in ("weekly", "monthly") and P == "BEV":
        eff = eff_range(car, cond)
        d = DEMAND[cond["single_trip"]]
        if eff is not None and eff < d * 0.75:
            if is_swap:
                add_hard("长途+换电缓解", SWAP_ADVANTAGE["longtrip"]["gain"],
                         SWAP_ADVANTAGE["longtrip"]["reason"])
            else:
                add_hard("长途续航不足·不推荐", -10,
                         "长途频繁且单次里程远超有效续航，纯电补能时间成本高", True)
    # H5 限牌 + 燃油/油混（上牌难）
    if cond["plate_limit"] in ("yes", "beijing", "shanghai") and P in ("ICE", "HEV"):
        add_hard("限牌城市·上牌难", -8,
                 "限牌城市燃油/油混需摇号拍牌，新能源可直接上牌", True)
    # H6 营运 + 燃油/油混（成本劣势）
    if cond["operation"] == "yes" and P in ("ICE", "HEV"):
        add_hard("营运·能耗成本高", -10,
                 "营运高频，燃油/油混能耗成本远高于纯电", True)
    # H7（v10.52.0 新增）限牌京沪 + 插混/增程：北京插混增程按油车管理需燃油指标、
    # 上海插混无免费绿牌（蓝牌拍卖约9.5万）——上牌优势丧失，与油车同罚。
    if cond["plate_limit"] in ("beijing", "shanghai") and P in ("PHEV", "EREV"):
        city = "北京" if cond["plate_limit"] == "beijing" else "上海"
        add_hard(f"限牌{city}·插混增程无绿牌优势", -8,
                 f"{city}插混/增程不享新能源绿牌（北京按油车管理需燃油指标/上海无免费绿牌），上牌成本与燃油车同", True)

    # ===== 常规软调节 =====
    # 1) 续航匹配（仅纯电）
    eff = eff_range(car, cond)
    d = DEMAND[cond["single_trip"]]
    if P == "BEV":
        if eff is not None:
            gap = (d - eff) / d
            pt = 0 if gap <= 0.10 else (-2 if gap <= 0.25 else (-3 if gap <= 0.40 else (-4.5 if gap <= 0.60 else -6)))
            if eff >= d * 1.3:
                pt = 1
            add(f"续航匹配(需{d}/有效{eff}km)", pt)
        else:
            items.append({"label": "续航匹配：无续航数据，不参与", "pt": 0, "na": True})
    # 2) 长途频率
    if cond["long_trip_freq"] in ("monthly", "weekly"):
        add("长途频率高", -1.5 if P == "BEV" else (1.5 if P in ("ICE", "HEV") else 0))
    elif cond["long_trip_freq"] == "rare":
        add("几乎无长途", 1 if P == "BEV" else 0)
    # 3) 充电条件（软；H1 已覆盖纯电无固定充电的硬条件）
    if cond["home_charger"] == "home":
        add("有家充", 1.5 if P == "BEV" else (1 if P in ("PHEV", "EREV") else 0))
    elif cond["home_charger"] == "work":
        # v10.53.0：单位能充 ≈ 半个家充。此前「无家充」一刀切 −15 把这类用户误杀。
        add("单位可充电", 1.0 if P == "BEV" else (0.8 if P in ("PHEV", "EREV") else 0))
    if cond["home_charger"] == "none" and P != "BEV":
        add("无固定充电", -1.5 if P in ("PHEV", "EREV") else 1)
    # 4) 营运（软）
    if cond["operation"] == "yes":
        add("营运", 2 if P == "BEV" else 0)
    # 5) 高速占比
    if cond["highway_ratio"] == "over60":
        add("常年高速", -2 if P == "EREV" else (-1 if P == "BEV" else 1))
    elif cond["highway_ratio"] == "40to60":
        add("高速较多", -1 if P == "EREV" else 0)
    elif cond["highway_ratio"] == "under20":
        add("市区为主", 1 if P == "BEV" else 0)
    # 6) 年里程
    if cond["annual_km"] == "over25":
        add("高里程", 1.5 if P == "BEV" else (-1 if P == "ICE" else 0))
    elif cond["annual_km"] == "under5":
        add("超低里程", -2.0 if P == "BEV" else (2.0 if P in ("ICE", "HEV") else 0))
    elif cond["annual_km"] == "under10":
        add("低里程", -1 if P == "BEV" else (1 if P in ("ICE", "HEV") else 0))
    # 8) 持有年限（长期持有：电车能源节省累积、残值摊薄）
    if cond["hold_years"] == "over8":
        add("长期持有·电车收益累积", 1.0 if P == "BEV" else (0.3 if P in ("PHEV", "EREV") else (-0.3 if P in ("ICE", "HEV") else 0)))
    # 9)（v10.52.0）纯电优先原则：用户明确「纯电尽量纯电，条件不符再扣分」——
    #    无硬条件冲突的纯电车型给基准偏好加分（披露于方法区）。
    if P == "BEV" and not hard:
        add("纯电优先（无硬条件冲突）", 0.5)
    # 10) 补能耐心：不愿等待 + 纯电 + 长途刚需 → 明显减分；可油可电形式加分
    if cond.get("charge_patience") == "reluctant":
        long_need = cond["long_trip_freq"] in ("monthly", "weekly") or cond["single_trip"] == "over500"
        if P == "BEV":
            add("补能耐心不足+长途需求", -2 if long_need else -1)
        else:
            add("补能时间不敏感", 0.5)
    # 12) 低里程无桩纯电：年固定成本（保险+残值差）摊薄慢，TCO 视角追加
    if cond["annual_km"] in ("under5", "under10") and P == "BEV" and cond["home_charger"] == "none":
        add("低里程无桩纯电·固定成本摊薄慢", -1.5 if cond["annual_km"] == "under5" else -1)
    # ===== v10.53.0 新增软调节（用户「再搜集一些因素」，每项都带信源）=====
    # 13) 家庭车队结构：增购第二辆且已有油车 → 长途/返乡由油车承担，纯电只负责日常
    #     （J.D. Power 2025：增购用户占新能源购买 28.1%，两车及以上家庭 31.3%）
    if cond.get("fleet") == "second":
        add("家庭增购·已有油车兜底",
            2.0 if P == "BEV" else (0.6 if P == "PHEV" else (0.4 if P == "EREV" else (-1.0 if P == "ICE" else 0))))

    return {"items": items, "total": round(total, 2), "hard": hard,
            "verdicts": verdicts(car, cond, eff, d)}


# ============ 6. 逐条件适配判定（适配/谨慎/不建议 + 理由）============
def verdicts(car, cond, eff=None, d=None):
    """逐条件判定。eff/d 由调用方传入（避免重复算续航）。"""
    P = car.get("power")
    price = car.get("price")
    v = {}

    # 营运
    if cond["operation"] == "yes":
        if P in ("BEV", "PHEV", "EREV"):
            v["operation"] = ("适配", "营运高频：纯电/插混/增程能耗成本碾压燃油（网约车新能源渗透率76%）")
        else:
            v["operation"] = ("不建议", "营运高频：燃油/油混能耗成本远高于纯电（硬条件H6 −10）")
    # 冬季气温
    wt = cond["winter_temp"]
    if wt == "underm20":
        if P == "BEV":
            if price is not None and price <= 15:
                v["winter_temp"] = ("不建议", "极寒+低价纯电：续航常腰斩、充电慢（硬条件H2 −12）")
            else:
                v["winter_temp"] = ("谨慎", "极寒纯电续航大幅打折，优先换电/大电池/热泵车型")
        elif P in ("PHEV", "EREV"):
            v["winter_temp"] = ("适配", "发动机余热兜底+加油补能；冬季纯电续航按×0.5~0.7规划")
        else:
            v["winter_temp"] = ("适配", "燃油/油混低温稳定，油耗升高约20-30%")
    elif wt == "m20tom10":
        v["winter_temp"] = ("谨慎" if P == "BEV" else "适配",
                            "纯电：-10℃续航达成约68%（硬条件不触发但需规划）" if P == "BEV"
                            else "有油箱/发动机兜底，影响小")
    # 年里程（TCO 视角）
    ak = cond["annual_km"]
    if ak == "under5":
        if P == "BEV":
            v["annual_km"] = ("不建议", "超低里程（<5000km/年）：低于盈亏平衡点（约4800km/年），纯电省回的能源差额抵不过保险溢价与残值差")
        else:
            v["annual_km"] = ("适配", "超低里程：能源成本占比极小，油车/油混总花费更低")
    elif ak == "under10":
        if P == "BEV":
            v["annual_km"] = ("谨慎" if cond["home_charger"] != "none" else "不建议",
                              "低里程：纯电能源节省无法覆盖保险溢价与残值差（TCO 测算见持有成本区块）")
        else:
            v["annual_km"] = ("适配", "低里程油车/油混省心，能源成本占比低")
    elif ak == "over25":
        v["annual_km"] = ("适配" if P != "ICE" else "谨慎",
                          "高里程：纯电/混动能源成本优势累积" if P != "ICE" else "高里程油费占比高，慎选纯燃油")
    # 单次里程 × 长途频率
    if eff is not None and P == "BEV":
        if cond["long_trip_freq"] in ("monthly", "weekly") and eff < d * 0.75:
            v["single_trip"] = ("不建议", f"长途高频且有效续航{eff}km < 需求{d}km×0.75（硬条件H4 −10）")
        elif eff < d:
            v["single_trip"] = ("谨慎", f"有效续航{eff}km 低于单次需求{d}km，需途中补能")
        else:
            v["single_trip"] = ("适配", f"有效续航{eff}km 覆盖单次需求{d}km")
    elif P == "BEV":
        v["single_trip"] = ("谨慎", "无续航数据不估算，长途需求请人工核实")
    else:
        note = "；增程高速亏电油耗7-9L高于插混直驱5-6L" if P == "EREV" and cond["highway_ratio"] in ("over60", "40to60") else "油箱兜底"
        v["single_trip"] = ("适配", f"非纯电{note}")
    # 充电条件
    hc = cond["home_charger"]
    if hc == "none":
        if P == "BEV":
            v["home_charger"] = ("不建议", "无固定充电，纯电日常补能困难（硬条件H1 −15）")
        elif P in ("PHEV", "EREV"):
            v["home_charger"] = ("谨慎", "无家充插混/增程长期亏电运行，油耗经济性恶化")
        else:
            v["home_charger"] = ("适配", "加油即走，无充电依赖")
    elif hc == "public":
        v["home_charger"] = ("谨慎" if P == "BEV" else "适配",
                             "纯电依赖公充：成本约为家充3-5倍、高峰排队" if P == "BEV" else "不依赖家充")
    elif hc == "work":
        if P in ("BEV", "PHEV", "EREV"):
            v["home_charger"] = ("适配", "单位/公司可充电：白天补能、成本接近家充，不按「无固定充电」处罚")
        else:
            v["home_charger"] = ("适配", "加油即走，不依赖充电")
    # 限牌
    pl = cond["plate_limit"]
    if pl in ("beijing", "shanghai"):
        city = "北京" if pl == "beijing" else "上海"
        if P == "BEV":
            v["plate_limit"] = ("适配", f"{city}纯电享免费绿牌")
        elif P in ("PHEV", "EREV"):
            v["plate_limit"] = ("不建议", f"{city}插混/增程不享绿牌（北京按油车管理/上海无免费绿牌）（硬条件H7 −8）")
        else:
            v["plate_limit"] = ("不建议", f"{city}燃油/油混需摇号或拍牌（硬条件H5 −8）")
    elif pl == "yes":
        if P in ("ICE", "HEV"):
            v["plate_limit"] = ("不建议", "限牌城市燃油/油混需摇号拍牌（硬条件H5 −8）")
        else:
            v["plate_limit"] = ("适配", "新能源直接上牌")
    # 持有年限
    hy = cond["hold_years"]
    if hy == "under3":
        if P == "BEV":
            v["hold_years"] = ("不建议", "短持有纯电折旧快（硬条件H3 −8）；换电车型车电分离除外")
        elif P in ("PHEV", "EREV"):
            v["hold_years"] = ("谨慎", "短持有插混/增程残值中等受压")
        else:
            v["hold_years"] = ("适配", "短持有燃油/油混残值相对稳")
    elif hy == "over8" and P == "BEV":
        v["hold_years"] = ("适配", "长期持有：能源节省累积、残值影响摊薄")
    # 高速占比
    hr = cond["highway_ratio"]
    if hr == "over60":
        if P == "EREV":
            v["highway_ratio"] = ("谨慎", "常年高速：增程亏电油耗7-9L（油→电→轮二次转换），高于插混直驱5-6L")
        elif P == "BEV":
            v["highway_ratio"] = ("谨慎", "常年高速：纯电高速续航达成56-75%，补能时间成本高")
        elif P == "PHEV":
            v["highway_ratio"] = ("适配", "插混高速发动机直驱效率高（亏电3.3-5.5L）")
        else:
            v["highway_ratio"] = ("适配", "燃油/油混高速工况稳定")
    # 方盒子已从条件表移除：车身形状改由车型自身风阻（cd/cdaPerTon）承担，并作为「车身形式」筛选项
    # 补能耐心
    if cond.get("charge_patience") == "reluctant" and P == "BEV":
        long_need = cond["long_trip_freq"] in ("monthly", "weekly") or cond["single_trip"] == "over500"
        v["charge_patience"] = ("不建议" if long_need else "谨慎",
                                "补能时间敏感+纯电途中充电等待（800V约10-20分钟/400V约30-40分钟，节假日排队另计）")
    # 露营/外放电需求已从条件表移除（家用占比低、对能源形式结论无翻转力），判定同步删除
    # ===== v10.53.0 新增判定 =====
    # 家庭车队结构
    if cond.get("fleet") == "second":
        if P == "BEV":
            v["fleet"] = ("适配", "增购第二辆：长途/返乡由已有油车承担，纯电只管日常，分工最省钱")
        elif P == "ICE":
            v["fleet"] = ("谨慎", "第二辆车再买燃油车：日常能源成本无优势，与已有油车功能重叠")
        elif P in ("PHEV", "EREV"):
            v["fleet"] = ("适配", "第二辆可油可电：与油车功能部分重叠，胜在无补能焦虑")
    # 地形已从条件表移除（路线级平均估算不够准，改为方法区提示：山区/高原自行上浮电耗10~25%）
    # 停放环境/驻车用电/电价档已从条件表移除（与冬季气候重复、或只影响 TCO 展示不影响名次），
    # 对应的判定与分项一并删除；TCO 统一按谷电 0.35 元/度口径并在方法区披露。
    return v


# ============ 7. 持有成本测算 TCO（仅披露不入分）============
def home_elec_price(cond):
    """家充每度电价（元/kWh）——固定谷电口径（电价档条件已移除，避免「电价不同结论就翻」的不可复现）。"""
    return TCO["elec_home"]


def unit_energy_cost(car, cond):
    """每公里能源成本（元/km）。逐车事实优先：BEV 用真实电耗 cons、PHEV/EREV 用
    馈电油耗 fuel_deprived_l100、ICE/HEV 用 fuel_l100；缺失才用形式中位兜底（披露）。
    cond 需含 home_charger（home/work/public/none）与 elec_price（v10.53.0）。"""
    charger = cond["home_charger"]
    home_price = home_elec_price(cond)
    P = car.get("power")
    cons = car.get("cons")
    if P == "BEV":
        per100 = cons if cons else TCO["bev_kwh_per_100km"]
        home = per100 * home_price / 100
        pub = per100 * TCO["elec_public"] / 100
        work = per100 * (0.8 * TCO["elec_work"] + 0.2 * TCO["elec_public"]) / 100
        return {"home": home, "work": round(work, 3),
                "public": round(home * 0.3 + pub * 0.7, 3), "none": pub}[charger]
    def fuel_l():
        # 键名与比较器 cars 格式一致（camelCase，同 engine.js）
        for k in ("fuelDeprivedL100", "fuelL100"):
            v = car.get(k)
            if v:
                return float(v)
        return {"PHEV": TCO["phev_fuel_l"], "EREV": TCO["erev_fuel_l"],
                "HEV": TCO["hev_fuel_l"]}.get(P, TCO["ice_fuel_l"])
    ev_price = TCO["elec_work"] if charger == "work" else home_price
    per100 = cons or TCO["bev_kwh_per_100km"]
    if P == "PHEV":
        ev_share = 0.8 if charger == "home" else (0.7 if charger == "work" else (0.5 if charger == "public" else 0.3))
        return round(ev_share * ev_price / 100 * per100
                     + (1 - ev_share) * fuel_l() * TCO["oil_price"] / 100, 3)
    if P == "EREV":
        ev_share = 0.7 if charger == "home" else (0.6 if charger == "work" else (0.5 if charger == "public" else 0.3))
        return round(ev_share * ev_price / 100 * per100
                     + (1 - ev_share) * fuel_l() * TCO["oil_price"] / 100, 3)
    return round(fuel_l() * TCO["oil_price"] / 100, 3)


def idle_cost(car, cond):
    """驻车用电成本（8 年，元）——驻车用电条件已移除，保留函数为兼容旧调用（恒 0）。"""
    return 0


def tco(car, cond):
    """8 年持有成本测算（ disclosed, not scored）。返回明细+合计+口径说明。"""
    P = car.get("power")
    price = car.get("price") or 0.0
    km = ANNUAL_KM_MID[cond["annual_km"]]
    charger = cond["home_charger"]
    unit = unit_energy_cost(car, cond)
    energy_8 = unit * km * TCO["years"]
    ins_8 = (TCO["ins_nev"] if P in ("BEV", "PHEV", "EREV") else TCO["ins_ice"]) * TCO["years"]
    maint_8 = TCO["maint"][P] * TCO["years"]
    is_nev = P in ("BEV", "PHEV", "EREV")
    tax = price / 1.13 * (TCO["tax_nev_rate"] if is_nev else TCO["tax_ice_rate"])
    residual_loss = price * (1 - TCO["residual3"][P])
    battery_risk_8 = TCO["battery_risk"][P] * TCO["years"]
    idle_8 = idle_cost(car, cond)
    total = energy_8 + ins_8 + maint_8 + tax + residual_loss + battery_risk_8 + idle_8
    return {
        "power": P, "price": price, "annual_km": km,
        "unit_energy": round(unit, 3),
        "energy_8y": round(energy_8), "ins_8y": round(ins_8), "maint_8y": round(maint_8),
        "purchase_tax": round(tax), "residual_loss": round(residual_loss),
        "battery_risk_8y": round(battery_risk_8), "idle_8y": round(idle_8),
        "elec_price": home_elec_price(cond),
        "tco_8y": round(total), "tco_per_km": round(total / (km * TCO["years"]), 3),
        "basis": "8年持有；能源按画像年里程与充电条件、家充按谷电0.35元/度统一口径（电价档条件已移除，避免同一套车不同电价得出两套结论）；"
                 "保费/保养为同级中位口径；残值用3年保值率近似（BEV45.1%/PHEV42.2%/EREV42%/HEV50%/ICE46.07%，"
                 "流通协会+精真估2026）；购置税2026-2027新能源减半5%、燃油/油混10%；以旧换新补贴因资格各异未计入",
    }


def tco_js():
    """TCO 常量的 JS 注入形态（camelCase 键）——build_energy_compare 的 __PHYS_JSON__
    与等价回归的 node harness 都从这里取，杜绝两处键名漂移。"""
    return {
        "oilPrice": TCO["oil_price"], "elecHome": TCO["elec_home"], "elecPublic": TCO["elec_public"],
        "elecWork": TCO["elec_work"], "elecHomeBy": TCO["elec_home_by"],
        "bevKwhPer100km": TCO["bev_kwh_per_100km"], "iceFuelL": TCO["ice_fuel_l"],
        "hevFuelL": TCO["hev_fuel_l"], "phevFuelL": TCO["phev_fuel_l"], "erevFuelL": TCO["erev_fuel_l"],
        "insNev": TCO["ins_nev"], "insIce": TCO["ins_ice"], "maint": TCO["maint"],
        "residual3": TCO["residual3"], "batteryRisk": TCO["battery_risk"],
        "idleHours": TCO["idle_hours"], "idleLPerH": TCO["idle_l_per_h"],
        "idleKwhPerH": TCO["idle_kwh_per_h"],
        "taxNevRate": TCO["tax_nev_rate"], "taxIceRate": TCO["tax_ice_rate"], "years": TCO["years"],
    }


def breakeven_km(cars, cond, bev_price=None):
    """盈亏平衡年里程（km/年）：家充纯电 vs 池内最优燃油/油混的 8 年 TCO 相等点。
    TCO(km/年) = 固定项(8年) + 单位能源×km×8 → km = 固定差 / (8×单位差)。"""
    bev = [c for c in cars if c.get("power") == "BEV"]
    ice = [c for c in cars if c.get("power") in ("ICE", "HEV")]
    if not bev or not ice:
        return None
    b = tco(bev[0], {**cond, "annual_km": "15to25", "home_charger": "home"})
    best_ice = min((tco(c, cond) for c in ice), key=lambda t: t["tco_8y"])
    fixed_b = b["tco_8y"] - b["unit_energy"] * ANNUAL_KM_MID["15to25"] * TCO["years"]
    fixed_i = best_ice["tco_8y"] - best_ice["unit_energy"] * ANNUAL_KM_MID["15to25"] * TCO["years"]
    unit_diff = best_ice["unit_energy"] - b["unit_energy"]
    if unit_diff <= 0:
        return None
    return round((fixed_b - fixed_i) / (TCO["years"] * unit_diff), 1)  # km/年


# ============ 7b. 能源形式聚合结论 + 画像自检（v10.53.0）============
FORM_CN = {"ICE": "燃油", "HEV": "油混", "PHEV": "插混", "EREV": "增程", "BEV": "纯电"}
FORM_MIN_N = 3   # 形式均分的最小样本量：少于 3 款不算形式结论（1 台车的分不能代表一种能源）


def recommend_form(rows, cond):
    """能源形式聚合结论：按形式算均分/硬条件车数/不建议项数 → 排序。
    回答「我这种条件适合哪种能源形式」；逐车结论仍以综合榜为准（形式均分受池内车源结构影响）。
    池内某形式车数 < 3 时标「样本不足」，不参与形式推荐（1 台车的均分不能代表一种能源形式）。"""
    agg = {}
    for r in rows:
        p = r.get("power")
        a = agg.setdefault(p, {"power": p, "n": 0, "sum": 0.0, "adj_sum": 0.0,
                               "hard": 0, "bad": 0, "best": None, "best_score": -1e9})
        a["n"] += 1
        a["sum"] += r.get("composite") or 0
        a["adj_sum"] += r.get("adj") or 0
        a["hard"] += 1 if r.get("hard") else 0
        for v in (r.get("verdicts") or {}).values():
            if v and v[0] == "不建议":
                a["bad"] += 1
        if (r.get("composite") or 0) > a["best_score"]:
            a["best"], a["best_score"] = r.get("model"), r.get("composite") or 0
    out = []
    for p, a in agg.items():
        out.append({"power": p, "n": a["n"], "avg": round(a["sum"] / a["n"], 1),
                    "avg_adj": round(a["adj_sum"] / a["n"], 2),
                    "hard": a["hard"], "bad": a["bad"], "best": a["best"]})
    out.sort(key=lambda x: (-x["avg"], x["hard"], -x["n"]))
    eligible = [x for x in out if x["n"] >= FORM_MIN_N]
    base = eligible[0] if eligible else out[0]
    for x in out:
        if x["n"] < FORM_MIN_N:
            x["label"], x["rank"] = "样本不足", None
            continue
        x["rank"] = eligible.index(x) + 1
        if x["power"] == base["power"]:
            x["label"] = "最适配"
        elif x["avg"] >= base["avg"] - 1.5 and x["hard"] <= base["hard"]:
            x["label"] = "可考虑"
        elif x["avg"] >= base["avg"] - 4:
            x["label"] = "次选"
        else:
            x["label"] = "不建议"
    # 展示顺序：有样本的先按名次，样本不足的排后（避免 n=2 的形式霸占第一名造成误读）
    out.sort(key=lambda x: (0 if x["rank"] else 1, x["rank"] or 0, -x["avg"]))
    return out


def form_narrative(forms, cond=None):
    """一句话形式结论（页面/正式榜顶部展示）。"""
    if not forms:
        return "池内无车，无法给出形式结论"
    if len(forms) == 1:
        f0 = forms[0]
        return (f"池内只有{FORM_CN.get(f0['power'], f0['power'])}一种能源形式（{f0['n']} 款），"
                "不存在跨形式取舍，形式适配结论不适用。")
    eligible = [f for f in forms if f["n"] >= FORM_MIN_N] or forms
    top = eligible[0]
    second = eligible[1] if len(eligible) > 1 else None
    txt = (f"本画像下{FORM_CN.get(top['power'], top['power'])}形式最合适"
           f"（均分 {top['avg']:.1f}，池内 {top['n']} 款，最强一款 {top['best']}）")
    if second:
        gap = round(top["avg"] - second["avg"], 1)
        if gap >= 2:
            txt += (f"，比{FORM_CN.get(second['power'], second['power'])}（{second['avg']:.1f}）"
                    f"明显领先 {gap:.1f} 分")
        elif gap > 0:
            txt += (f"，与{FORM_CN.get(second['power'], second['power'])}（{second['avg']:.1f}）"
                    f"只差 {gap:.1f} 分，按具体车型选更靠谱")
        else:
            txt += f"，与{FORM_CN.get(second['power'], second['power'])}基本同级"
    thin = [f for f in forms if f["n"] < FORM_MIN_N]
    if thin:
        txt += "；" + "、".join(f"{FORM_CN.get(f['power'], f['power'])}{f['n']}款" for f in thin) + "样本不足，不作形式结论"
    if top["hard"]:
        txt += f"；但该形式有 {top['hard']} 款触发硬条件（无固定充电/极寒/长途续航等），需逐车核"
    return txt + "。"


def check_profile(cond):
    """画像自检：矛盾/高风险组合提示 [{level, text}]。
    这是「前置条件」的一部分——跨能源比较前先确认画像自洽，否则排名再精细也没有意义。"""
    w = []

    def add(level, text):
        w.append({"level": level, "text": text})

    no_fixed = cond["home_charger"] == "none"
    cold = cond["winter_temp"] in ("m20tom10", "underm20")
    long_need = cond["long_trip_freq"] in ("weekly", "monthly") or cond["single_trip"] == "over500"
    # 营运场景常见冲突
    if cond["operation"] == "yes":
        if long_need:
            add("warn", "营运车（网约/出租）极少单程 500km 以上的高频长途：确认是营运用途还是私家长途")
        if cond["hold_years"] == "over8":
            add("warn", "营运车年里程常在 5 万公里以上，多数城市网约车 8 年强制退出：『8 年以上持有』与营运不匹配")
        if cond["fleet"] == "second":
            add("warn", "营运车是生产工具，通常不是家庭第二辆车：两项条件相互矛盾")
    # 纯电最不利组合
    if no_fixed and cold and long_need:
        add("warn", "无固定充电 + 低温 + 高频长途：纯电在本画像下基本无解（H1/H2/H4 会连续触发），建议直接看插混/油混/燃油")
    elif no_fixed and cold:
        add("info", "无固定充电 + 低温：纯电要靠公充与热泵/电池预热车型硬扛，先确认单位或住处能否装桩")
    elif no_fixed and long_need:
        add("info", "无固定充电 + 高频长途：纯电补能时间成本高，可油可电更稳")
    # 低里程无桩
    if cond["annual_km"] in ("under5", "under10") and no_fixed:
        add("info", "年里程 1 万以下（尤其 5000km 以下）又无固定充电：纯电的能源节省很难覆盖保费溢价与残值差，油车/油混通常更省")
    # 增购第二辆
    if cond["fleet"] == "second" and long_need:
        add("info", "增购第二辆 + 长途高频：长途交给已有油车即可，纯电按日常通勤里程选电池大小就行")
    # 方盒子 + 常年高速（方盒子条件已移除，改由车型风阻承担，此条随之删除）
    # 低温 + 露天（停放环境条件已移除，此条随之删除）
    # 短持有 + 无桩
    if cond["hold_years"] == "under3" and no_fixed:
        add("info", "3 年内换车 + 无固定充电：纯电折旧与补能双重不利，短期持有优先油车/油混")
    # 高里程全短途
    if cond["annual_km"] == "over25" and cond["single_trip"] == "under150" and cond["long_trip_freq"] == "rare":
        add("info", "年里程 2.5 万以上但几乎全是短途：典型城配/网约特征，纯电最合适")
    return w


# ============ 8. 组合分/档位 ============
def composite_tier(score):
    return "T1" if score >= 82 else ("T2" if score >= 72 else "T3")


def normalize_profile(raw):
    """画像归一化：补默认值、校验枚举。返回 (cond, errors)。"""
    cond, errors = {}, []
    for fid, f in FACTORS.items():
        v = (raw or {}).get(fid, f["default"])
        valid = {o["v"] for o in f["options"]}
        if v not in valid:
            errors.append(f"{fid} 非法值 '{v}'（合法：{','.join(sorted(valid))}）")
            v = f["default"]
        cond[fid] = v
    return cond, errors


def analyze(cars, cond):
    """整榜分析：每车 adjust+range+tco+verdicts → 综合分排序。
    cars 需含 product（8 维产品力分）/power/price/kwh/cons/...（ comparator 格式）。"""
    rows = []
    for c in cars:
        a = adjust(c, cond)
        soft = a["total"] - sum(h["pt"] for h in a["hard"])
        adj_soft = max(-10.0, min(10.0, soft))
        adj = round(adj_soft + sum(h["pt"] for h in a["hard"]), 2)
        rc = range_calc(c, cond)
        rows.append({
            "model": c["model"], "power": c.get("power"), "price": c.get("price"),
            "product": c.get("product"), "eff": rc["range"] if rc else None,
            "range_method": rc["method"] if rc else None,
            "adj": adj, "adj_soft": round(adj_soft, 2),
            "hard": a["hard"], "items": a["items"],
            "composite": round((c.get("product") or 0) + adj, 2),
            "verdicts": a["verdicts"], "tco": tco(c, cond),
        })
    rows.sort(key=lambda r: -r["composite"])
    for i, r in enumerate(rows):
        r["rank"] = i + 1
        r["tier"] = composite_tier(r["composite"])
        if r["hard"]:
            r["tier"] = {"T1": "T2", "T2": "T3"}.get(r["tier"], "T3")
    return rows


# ============ 9. selftest ============
def selftest():
    bev = {"model": "测试纯电", "power": "BEV", "price": 14.0, "kwh": 60.0,
           "cons": 14.0, "consSrc": "事实库", "weight": 1800, "cd": 0.24,
           "area": 2.4, "voltage": 800, "swap": False, "cdaPerTon": 0.32,
           "body": "轿车", "product": 82.0}
    ice = {"model": "测试燃油", "power": "ICE", "price": 13.0, "product": 80.0}
    phev = {"model": "测试插混", "power": "PHEV", "price": 13.0, "product": 81.0}
    erev = {"model": "测试增程", "power": "EREV", "price": 14.0, "product": 81.0}
    hev = {"model": "测试油混", "power": "HEV", "price": 13.0, "product": 79.0}

    # ① 默认画像：纯电优先原则生效（无硬条件 BEV +0.5）
    a = adjust(bev, dict(DEFAULT_COND))
    assert any(it["label"] == "纯电优先（无硬条件冲突）" and it["pt"] == 0.5 for it in a["items"]), "纯电优先原则未生效"
    # ② 无家充纯电 → H1 −15 降档
    a2 = adjust(bev, {**DEFAULT_COND, "home_charger": "none"})
    assert any(h["label"] == "无充电桩·纯电不推荐" and h["pt"] == -15 and h["downgrade"] for h in a2["hard"])
    assert a2["verdicts"]["home_charger"][0] == "不建议"
    # ③ 极寒低价纯电 → H2 −12
    a3 = adjust(bev, {**DEFAULT_COND, "winter_temp": "underm20"})
    assert any(h["pt"] == -12 for h in a3["hard"])
    # ④ 京沪限牌插混 → H7 −8（新规则）
    a4 = adjust(phev, {**DEFAULT_COND, "plate_limit": "beijing"})
    assert any(h["label"].startswith("限牌北京") and h["pt"] == -8 and h["downgrade"] for h in a4["hard"])
    assert a4["verdicts"]["plate_limit"][0] == "不建议"
    # ⑤ 京沪纯电不受罚
    a5 = adjust(bev, {**DEFAULT_COND, "plate_limit": "shanghai"})
    assert not a5["hard"], "京沪纯电不应触发硬条件"
    # ⑥ 营运油车 → H6 −10
    a6 = adjust(ice, {**DEFAULT_COND, "operation": "yes"})
    assert any(h["pt"] == -10 for h in a6["hard"])
    # ⑦ 补能耐心+长途
    a8 = adjust(bev, {**DEFAULT_COND, "charge_patience": "reluctant",
                      "long_trip_freq": "monthly", "single_trip": "over500"})
    assert any(it["label"] == "补能耐心不足+长途需求" and it["pt"] == -2 for it in a8["items"])
    assert a8["verdicts"]["charge_patience"][0] == "不建议"
    # ⑧ 方盒子条件已移除：未知键安全忽略（车身形状改由车型风阻承担）
    a9 = adjust(erev, {**DEFAULT_COND, "boxy": "yes", "highway_ratio": "over60"})
    assert not any(it["label"] == "方盒子+高频高速" for it in a9["items"])
    # ⑨ 续航物理模型：纯电有值、非纯电 None
    rc = range_calc(bev, DEFAULT_COND)
    assert rc and rc["range"] and 200 < rc["range"] < 700
    assert range_calc(ice, DEFAULT_COND) is None
    # ⑪ TCO：家充纯电 8 年显著低于燃油；盈亏平衡点落在研究区间 0.6-1.3 万 km
    t_bev = tco(bev, {**DEFAULT_COND, "home_charger": "home"})
    t_ice = tco(ice, DEFAULT_COND)
    assert t_bev["tco_8y"] < t_ice["tco_8y"], "家充纯电 TCO 应低于燃油"
    be = breakeven_km([bev, ice], DEFAULT_COND)
    assert be is not None and 1000 <= be <= 20000, f"盈亏平衡异常: {be}"
    # ⑫ 画像归一化：非法值报错并回退默认
    cond, errs = normalize_profile({"winter_temp": "xxx"})
    assert errs and cond["winter_temp"] == DEFAULT_COND["winter_temp"]
    # ⑬ analyze 排序与降档
    rows = analyze([bev, ice, phev, erev, hev], DEFAULT_COND)
    assert rows[0]["composite"] >= rows[-1]["composite"]
    rows2 = analyze([bev], {**DEFAULT_COND, "home_charger": "none"})
    assert rows2[0]["tier"] == "T3" and rows2[0]["hard"]
    # ⑭ 11 因素齐全 + 分组覆盖（v10.56.0：13→11，删除地形/方盒子，理由见文件头 ⑭）
    assert len(FACTORS) == 11, len(FACTORS)
    grouped = [f for _, ids, _ in FACTOR_GROUPS for f in ids]
    assert sorted(grouped) == sorted(FACTORS.keys()), "因素分组遗漏"
    assert len(FACTOR_GROUPS[0][1]) == 9, "核心能源决策组应为 9 项"
    assert len(FACTOR_GROUPS[1][1]) == 2, "进阶条件组应为 2 项"
    # 删掉的条件不得再被引用（防止删了条件表、规则分支却留着）
    for gone in ("summer_temp", "drive_style", "ac_usage", "elec_price",
                 "outdoor_camp", "park_idle", "parking", "terrain", "boxy"):
        assert gone not in FACTORS, f"{gone} 应已移除"
        for fn in (adjust, range_calc, tco, verdicts):
            fn(bev, {**DEFAULT_COND, gone: "m20tom10"})   # 额外未知键必须被安全忽略
    # ⑮ v10.53.0 新因素：单位充电不再按「无固定充电」处罚
    a10 = adjust(bev, {**DEFAULT_COND, "home_charger": "work"})
    assert not a10["hard"], "单位可充电不应触发无桩硬条件"
    assert any(it["label"] == "单位可充电" and it["pt"] == 1.0 for it in a10["items"])
    assert a10["verdicts"]["home_charger"][0] == "适配"
    # ⑯ 家庭增购：纯电 +2.0、燃油 −1.0
    a11 = adjust(bev, {**DEFAULT_COND, "fleet": "second"})
    assert any(it["label"] == "家庭增购·已有油车兜底" and it["pt"] == 2.0 for it in a11["items"])
    a11b = adjust(ice, {**DEFAULT_COND, "fleet": "second"})
    assert any(it["pt"] == -1.0 for it in a11b["items"])
    # ⑰ 地形条件已移除：未知键安全忽略、续航算式不再出现地形乘子
    probe = range_calc(bev, {**DEFAULT_COND, "terrain": "hilly"})
    assert not any("地形" in s for s in probe["steps"]), "地形乘子应已从算式删除"
    assert probe["range"] == range_calc(bev, DEFAULT_COND)["range"], "地形不应再影响有效续航"
    # ⑱ 形式聚合结论 + 画像自检
    forms = recommend_form(rows, DEFAULT_COND)
    assert forms and forms[0]["avg"] >= forms[-1]["avg"]
    # 每形式仅 1 款 → 全部「样本不足」，不得硬推一个「最适配」
    assert all(f["label"] == "样本不足" for f in forms), "单样本形式应标样本不足"
    # 扩到 3 款纯电后，纯电才成为「最适配」
    rows3 = analyze([bev, {**bev, "model": "测试纯电B", "product": 80.0},
                     {**bev, "model": "测试纯电C", "product": 79.0}, ice], DEFAULT_COND)
    forms3 = recommend_form(rows3, DEFAULT_COND)
    assert any(f["label"] == "最适配" and f["power"] == "BEV" for f in forms3), "3 款纯电应成为最适配"
    assert all(f["label"] == "样本不足" for f in forms3 if f["n"] < FORM_MIN_N), "薄样本未标样本不足"
    assert form_narrative(forms).startswith("本画像下")
    # 单形式池（纯电轿车榜这类）不得编造「最合适」跨形式结论
    assert form_narrative(forms[:1]).endswith("形式适配结论不适用。") and "最合适" not in form_narrative(forms[:1])
    warns = check_profile({**DEFAULT_COND, "home_charger": "none", "winter_temp": "underm20",
                           "long_trip_freq": "weekly", "single_trip": "over500"})
    assert any(w["level"] == "warn" and "无解" in w["text"] for w in warns), "纯电无解组合未诊断"
    assert check_profile(DEFAULT_COND) == [], "默认画像不应有冲突提示"
    print(f"[envscore engine selftest] PASS · 11因素/硬条件H1-H7/纯电优先/形式结论/画像自检/"
          f"TCO盈亏平衡{be}km/年")


if __name__ == "__main__":
    selftest()


