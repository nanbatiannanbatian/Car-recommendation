#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""crash_systems.py —— 全球权威碰撞测试体系的唯一真相源（v9.7.44）

为什么需要它：体系的 code/中文名/别名/家族/权威性此前散落在5处
（official_safety_scores.json 的 systems、render 徽标、evidence_consistency、
safety_check、leaderboard crash_gate），每加一个体系就要改5处——导致
"用户说一项改一项"、Model3 的 Euro NCAP 被漏。现在统一在此定义，
全链路 import，以后加体系只改这一个文件。

全球体系清单（联网核实 2026-11-20，来源：tyremap NCAP guide / 百度百科 NCAP /
Hindustan Times 各国NCAP综述）：
  NCAP 家族（星级制 0-5，分级按严苛度）：
    Euro NCAP(欧1997,公认最全面最严,影响全球设计), ANCAP(澳新1993,规程同Euro),
    C-NCAP(中2006,对标Euro), JNCAP(日1995), KNCAP(韩1999),
    NHTSA-NCAP(美1978政府), Latin NCAP(拉美2010), ASEAN NCAP(东南亚2011),
    Bharat NCAP(印度2023), Global NCAP(全球协调)
  保险机构（等级制/奖项制）：
    IIHS(美1959,25%小偏置+车顶强度,TSP/TSP+), C-IASI(中2015,对标IIHS,G+/G/A/M/P)

计分统一原则：各体系不可按得分率横向直接比较（年份/规程/选车方式不同），
按"权威体系 + 等级/得分率"映射到统一 0-10 安全分，见 official_safety_scores.json。
"""
import re
from collections import OrderedDict

# code -> 定义。code 即候选池 safety_basis 的标准值。
SYSTEMS = OrderedDict([
    # 中国
    ("cncap",       {"cn": "C-NCAP",      "family": "ncap",     "grade": "star",  "authority": "高(官方/中国场景)", "aliases": ["c-ncap"]}),
    ("ciasi",       {"cn": "C-IASI",      "family": "ciasi",    "grade": "letter","authority": "最高(自购低配/25%小偏置/保险)", "aliases": ["ciasi_checked", "c-iasi"]}),
    # 欧洲/澳新
    ("euro_ncap",   {"cn": "Euro NCAP",   "family": "ncap",     "grade": "star",  "authority": "高(独立匿名购车/全球公认最严)", "aliases": ["e-ncap", "enCAP"]}),
    ("ancap",       {"cn": "ANCAP",       "family": "ncap",     "grade": "star",  "authority": "高(澳新/规程同Euro)", "aliases": ["a-ncap"]}),
    # 美国
    ("iihs",        {"cn": "IIHS",        "family": "iihs",     "grade": "award", "authority": "最高(保险/25%小偏置+车顶强度)", "aliases": ["iihs_tsp", "iihs_tsp_plus"]}),
    ("nhtsa",       {"cn": "NHTSA-NCAP",  "family": "ncap",     "grade": "star",  "authority": "高(美政府/1978创始)", "aliases": ["nhtsa_ncap", "us_ncap", "usncap"]}),
    # 亚太
    ("jncap",       {"cn": "JNCAP",       "family": "ncap",     "grade": "star",  "authority": "高(日本/1995)", "aliases": ["j-ncap"]}),
    ("kncap",       {"cn": "KNCAP",       "family": "ncap",     "grade": "star",  "authority": "高(韩国/1999)", "aliases": ["k-ncap", "korea_ncap"]}),
    ("asean_ncap",  {"cn": "ASEAN NCAP",  "family": "ncap",     "grade": "star",  "authority": "中(东南亚/含摩托车场景)", "aliases": ["asean"]}),
    # 拉美/印度/全球
    ("latin_ncap",  {"cn": "Latin NCAP",  "family": "ncap",     "grade": "star",  "authority": "中(拉美)", "aliases": ["latinncap"]}),
    ("bharat_ncap", {"cn": "Bharat NCAP", "family": "ncap",     "grade": "star",  "authority": "中(印度/2023)", "aliases": ["bharat"]}),
    ("global_ncap", {"cn": "Global NCAP", "family": "ncap",     "grade": "star",  "authority": "中(全球协调/多为新兴市场)", "aliases": ["globalncap"]}),
])

# 别名 -> 标准 code（含小写归一）
ALIAS2CODE = {}
for _code, _d in SYSTEMS.items():
    for _a in [_code] + list(_d["aliases"]):
        # key 与 normalize() 用同一套归一化（去空格/连字符/转小写）
        _k = str(_a).lower().replace(" ", "").replace("-", "").replace("_", "")
        ALIAS2CODE[_k] = _code

# 兼容旧代码的集合/映射
def all_codes():
    return set(SYSTEMS.keys())


def is_official(basis):
    """safety_basis 是否为权威碰撞体系（大小写/别名容错）。"""
    if not basis:
        return False
    return normalize(basis) is not None


def normalize(basis):
    """把任意写法 ('E-NCAP','euro_ncap','C-NCAP') 归一到标准 code，未知返回 None。"""
    if not basis:
        return None
    k = str(basis).lower().replace(" ", "").replace("-", "").replace("_", "")
    return ALIAS2CODE.get(k)


def cn_name(basis):
    """中文展示名。未知返回原值。"""
    code = normalize(basis)
    return SYSTEMS[code]["cn"] if code else str(basis or "")


def family(basis):
    """体系家族：ncap(星级制) / ciasi / iihs。未知返回 None。"""
    code = normalize(basis)
    return SYSTEMS[code]["family"] if code else None


# 「没有成绩」的文本标记：缺数据必须与「有成绩」区分，否则披露会虚报
# （2026-11-21：报告写「有官方碰撞成绩 4/21」，实际 3 款，把「C-NCAP 已抽车未测」算成了有成绩）
_NO_SCORE_MARKERS = ("无官方成绩", "无成绩", "未查到", "未测", "待测", "未公布",
                     "暂无", "不适用", "—", "无数据")
# 「有成绩」的必要特征：百分比 / 星级 / C-IASI 等级字母
_SCORE_RE = re.compile(r"\d+(?:\.\d+)?\s*%|五星|\bG\+?\b|\bA\b|优秀|良好")


def _negated(t: str) -> bool:
    """文本里是否出现「无/未/没 + NCAP/IASI」这种否定表述。

    不加这条会把「无C-NCAP成绩；9min快充(10-97%)」判成有成绩——
    百分比来自快充、体系词来自否定句，两个片段拼出假成绩。
    """
    return bool(re.search(r"(无|未|没|非)\s*C?\s*-?\s*(?:N?CAP|IASI)", t, re.I))


def has_score(crash_text) -> bool:
    """crash 文本里是否有可引用的具体成绩（百分比/星级/等级）。

    单一真相源：披露统计、证据门禁、页面徽标都必须用它，不要各自写关键词表
    ——各写一套的结果就是同一份数据在报告里两个数字。
    """
    t = str(crash_text or "").strip()
    if not t:
        return False
    if any(k in t for k in _NO_SCORE_MARKERS):
        return False
    if _negated(t):
        return False
    # 「第三方称五星但无得分率」「宣传口径」这类不能当权威成绩（宝马i3 曾误报）
    if any(k in t for k in ("无得分率", "无得分", "宣传口径", "传闻")):
        return False
    return bool(_SCORE_RE.search(t))


# ---------------------------------------------------------------- 文本 → 结构化
def _system_pattern():
    """从 SYSTEMS 表动态生成体系名正则——单一真相源。

    为什么（2026-11-21 全量排查）：此前这里和 _ANCHOR_RE 都是手写 8 个体系，
    SYSTEMS 却定义了 12 个，导致 Euro NCAP/ANCAP/IIHS/JNCAP/KNCAP/ASEAN/
    Latin/Bharat/Global 的成绩文本全都解析不出来（实测 10/12 FAIL）。
    蔚来 ET5 在权威库是 Euro NCAP 五星 + C-IASI A，就因为体系没进正则而取不到。
    现在体系名只从 SYSTEMS 生成：以后加体系只改 SYSTEMS 一处，两个解析器自动覆盖。
    """
    names = []
    for code, d in SYSTEMS.items():
        for n in [code, d.get("cn", "")] + list(d.get("aliases") or []):
            # 先归一成纯字母数字串（去掉下划线/连字符/空格），再逐字符插可选分隔符。
            # 关键：不能把 code 里的 "_" 用 re.escape 当字面量，否则 euro_ncap 要求
            # 文本里必须有下划线，匹配不了 "Euro NCAP"（空格）/ "euroncap"（无分隔）。
            bare = re.sub(r"[^A-Za-z0-9]", "", str(n))
            if not bare:
                continue
            names.append(r"\s*[-_]?\s*".join(re.escape(ch) for ch in bare))
    # 长的排前面，避免 "ANCAP" 抢先截走 "Euro NCAP"/"ASEAN NCAP" 里的 NCAP
    names.sort(key=len, reverse=True)
    return "|".join(names)


_TEXT_SYS_RE = re.compile(r"(?P<sys>" + _system_pattern() + r")", re.I)
_PCT_RE = re.compile(r"(\d{2,3}(?:\.\d+)?)\s*%")
_STAR_RE = re.compile(r"([五五四四三三]|[3-5])\s*星")
_GRADE_RE = re.compile(r"(?:^|[^A-Za-z])(G\+{0,2}|A|M|P)(?![A-Za-z])")
_CN_NUM = {"五": 5, "5": 5, "四": 4, "4": 4, "三": 3, "3": 3}


def display_short(crash_text) -> str:
    """主榜展示用短文本：有成绩给「C-NCAP 90.7%」，在测给「在测」，没测给「—」。

    页面与 Markdown 共用这一个函数，避免两处各写一套判断
    （曾出现「有 C-NCAP 徽标」却说「无成绩」）。
    """
    parsed = parse_score(crash_text)
    if parsed:
        return parsed["display"]
    t = str(crash_text or "")
    if ("抽车" in t) or ("在测" in t) or ("测试中" in t):
        return "在测"
    return "—"


def parse_score(text) -> dict | None:
    """从自由文本里解析出「体系 + 成绩」，返回结构化 dict；解析不出返回 None。

    为什么要它：2026-11-21 出中型纯电SUV榜，4 款车（极氪7X 90.7% / 阿维塔07 90.2% /
    乐道L60 89.2% / 海狮07 EV 87.3%）的 C-NCAP 成绩**只写在 note 自由文本里**，
    crash/safety_basis 是空的 → 被判「无权威碰撞实锤」，极氪7X 因此进不了一档。
    这里把「从文本抽成绩」固化成代码，出榜前自动进字段。

    严格性（踩过的坑）：「无C-NCAP成绩；9min快充(10-97%)」曾被解析成「C-NCAP 97%」——
    体系词在否定句、百分比来自快充。所以：
      - 体系词前 3 字若是否定词 → 跳过
      - 只在该体系词**同一分句**（到 ；，。| 换行为止）里找分数/星级/等级

    返回: {"basis","system_cn","score","stars","grade","display"}
    """
    t = str(text or "").strip()
    if not t:
        return None
    for m in _TEXT_SYS_RE.finditer(t):
        pre = t[max(0, m.start() - 3):m.start()]
        if any(k in pre for k in ("无", "未", "没", "非", "暂无")):
            continue
        basis = normalize(m.group("sys"))
        if not basis:
            continue
        clause = re.split(r"[；;，,。|\n]", t[m.end():])[0]
        # 同句里若写明是第三方传言/无得分率/宣传口径，不当作权威成绩
        if any(k in clause for k in ("第三方称", "宣传", "无得分率", "无得分", "传闻", "网传")):
            continue
        grade_kind = SYSTEMS[basis]["grade"]
        score = stars = grade = None
        if grade_kind == "star":
            pm = _PCT_RE.search(clause)
            score = float(pm.group(1)) if pm else None
            sm = _STAR_RE.search(clause)
            stars = _CN_NUM.get(sm.group(1)) if sm else None
            if score is None and stars is None:
                continue
        elif grade_kind in ("letter", "award"):
            gm = _GRADE_RE.search(clause)
            if not gm:
                continue
            grade = gm.group(1)
        else:
            continue
        ym = re.search(r"(20\d{2})\s*版?", clause)
        year = int(ym.group(1)) if ym else None
        sys_cn = SYSTEMS[basis]["cn"]
        if score is not None:
            display = f"{sys_cn} {score:g}%"
            if stars:
                display += " 五星"
        elif stars:
            display = f"{sys_cn} 五星"
        else:
            display = f"{sys_cn} {grade}"
        return {"basis": basis, "system_cn": sys_cn, "score": score,
                "stars": stars, "grade": grade, "year": year, "display": display}
    return None


# ---------------------------------------------------------------- 证据文本 → 结构化（严格）
# 为什么不能只靠 parse_score 扫 evidence（2026-11-21 Model Y 事故）：
#   parse_score 是为「短 crash 文案」（如 "C-NCAP 90.7%"）设计的宽松解析器，
#   直接扫长篇 evidence 会把「宣称C-NCAP五星安全车身设计」（宣传口径）、
#   「问界M7 95.0%」（他车成绩）、「C-NCAP基础行车辅助92.9%」（智驾C-ICAP）
#   误抓成碰撞成绩。实测误伤 6 款无成绩车。所以证据文本必须走严格解析器：
#     1) 只认肯定锚点「有<体系>官方成绩」；
#     2) C-IASI 必须解析出「车内乘员G+/车外行人G+/辅助安全G」维度等级结构；
#     3) 官方口径：三指数取最低档，耐撞维修经济性仅参考不主导；
#     4) 同一车多次测评取最新（与 ciasi_text_decode 纪律一致）。
_ANCHOR_RE = re.compile(
    r"有\s*(?P<sys>" + _system_pattern() + r")\s*"
    r"(?:官方|碰撞|测评)?\s*(?:碰撞)?成绩", re.I)
# 出现这些词说明该句不是「本车碰撞实测成绩」的肯定陈述
_NEG_EVIDENCE = ("无官方", "未取得", "未查到", "未见", "软文", "宣传口径",
                 "厂商口径", "设计标准", "C-ICAP", "辅助驾驶得分")
_CIASI_DIM_PATTERNS = {
    "occupant": re.compile(r"(?:车内)?乘员(?:安全)?\s*(G\+|G|A|M|P)"),
    "pedestrian": re.compile(r"(?:车外)?行人(?:安全)?\s*(G\+|G|A|M|P)"),
    "assist": re.compile(r"(?:车辆)?辅助(?:驾驶)?安全\s*(G\+|G|A|M|P)"),
}
_CIASI_GRADE_ORDER = {"P": 0, "M": 1, "A": 2, "G": 3, "G+": 4}


def parse_ciasi_dimensions(text) -> dict | None:
    """从 C-IASI 多维度等级文本提取（乘员/行人/辅助）三指数，取最低档。

    只认至少 2 个维度的组合（单个 G 可能是「只给G/G+/A/M/P等级」枚举句）。
    多次测评按文本顺序取最后一组完整维度（最新）。返回 None 表示证据不足。
    """
    groups = []
    for seg in re.split(r"[①②③④⑤⑥⑦⑧⑨⑩；;]", str(text or "")):
        dims = {}
        for k, pat in _CIASI_DIM_PATTERNS.items():
            found = pat.search(seg)
            if found:
                dims[k] = found.group(1)
        if len(dims) >= 2:
            groups.append(dims)
    if not groups:
        return None
    latest = groups[-1]
    grade = min(latest.values(), key=lambda g: _CIASI_GRADE_ORDER.get(g, -1))
    return {"grade": grade, "dims": latest, "n_tests": len(groups)}


# ---------------------------------------------------------------- 多成绩：取证据最强单一成绩
# 用户口径（2026-11-21 确认）：一款车有多个碰撞成绩时，取「证据最强」的一条算分，
# 既不取最高分（会系统性偏向测试宽松的体系），也不把多体系分数一起算（各国 NCAP
# 无对应关系、不可跨体系套同一得分率公式）。被丢弃的次要成绩不删，进审计字段。
# 强度阶梯：一手得分率(3) > C-IASI 三指数/NCAP 五星(2) > 奖项(1) > 媒体/软文/旁证(0)。
_WEAK_ANCHOR_RE = re.compile(
    r"(?:另(?:有)?|此外|参考|海外|出口版获|据)\s*(?P<sys>" + _system_pattern() + r")", re.I)
# 厂商「按XX五星标准设计」不是实测成绩，次级收集也要拒绝
_SECONDARY_REJECT = ("安全车身设计", "设计标准", "标准开发", "非第三方实测", "按C-NCAP", "按中保研",
                     "非国内", "仅作弱旁证", "仅作旁证", "非本代", "非本车", "不能当", "非实测")

# ---------------------------------------------------------------- 同源车 / 出口别名
# 为什么要有（2026-11-21 蔚来ES6 事故）：
#   全球车型常有「国内名 + 出口名」双名（蔚来 ES6 = 出口 EL6；比亚迪 元PLUS = ATTO 3…）。
#   同一台车、同一代、同一车身结构，出口版拿到的 Euro NCAP / ANCAP 一手实测成绩，
#   对本国在售的同源车同样成立——这是业内通行做法（官网自己就写「中欧双五星」）。
#   此前解析器把「出口版获<体系>」一律当弱锚点 tier0、把「非本代」整句拒收，
#   导致 ES6 这种有明确年份+得分率的一手成绩被误判「无成绩」。
#   判据（防止误伤）：只有【同源映射命中 + 具体年份 + 具体得分率/星级 + 一手权威体系】
#   才升级为一手；泛泛的「宣称双五星」「按X标准设计」无数字仍一律拒绝。
#   映射只放"确定是同一台车"的别名，不确定的不写（宁可漏，不可错配）。
EXPORT_ALIASES = {
    "蔚来ES6": ["EL6"],          # 蔚来官网英文站：ES6 = EL6 同代，中欧双五星
}

# 一手权威体系：出口版在这些体系的实测成绩可视为同源车一手成绩。
# 不含 IIHS/C-IASI（这些是本国体系，出口版不会去测）；不含弱 NCAP 变体。
_HOMOLOG_AUTHORITATIVE = {"euro_ncap", "ancap", "nhtsa", "jncap", "kncap"}


def export_aliases_of(model) -> list[str]:
    """取车型的出口别名（归一化匹配：忽略空格/连字符/大小写）。"""
    if not model:
        return []
    key = re.sub(r"[\s\-]", "", str(model)).lower()
    for domestic, aliases in EXPORT_ALIASES.items():
        dkey = re.sub(r"[\s\-]", "", domestic).lower()
        if key == dkey or key in dkey or dkey in key:
            return list(aliases)
    return []


def parse_homolog_score(text, model) -> dict | None:
    """识别【同源车出口版一手权威成绩】：有具体年份+得分率/星级的 Euro/ANCAP 等。

     strict 规则（每一条都是防误伤，踩过的坑）：
       - 必须 model 命中 EXPORT_ALIASES（没有映射的车一律不启用，防泛化误伤）
       - 必须同句出现出口别名（如 EL6）或「出口版/海外版」+ 权威体系
       - 必须有一手权威体系（_HOMOLOG_AUTHORITATIVE，排除智驾 C-ICAP/宣传口径）
       - 必须有具体年份(20xx) 且 有得分率(xx.x%)或五星（泛泛"五星"无数字不算）
       - 出现 C-ICAP/智驾/行车辅助/宣传/软文 等词 → 该句整句拒绝（防把智驾分当碰撞）
     返回与 parse_score 同构的 dict，含 tier=2 / source_quality 字段。
    """
    t = str(text or "")
    if not t or not export_aliases_of(model):
        return None
    reject = ("C-ICAP", "智驾", "行车辅助", "领航", "宣传", "软文", "网传", "传闻",
              "安全车身设计", "非实测")
    # 按分句/顿号切：让「具体成绩」与同段后面的软文（如「官网宣称中欧双五星」）分开，
    # 否则「2024出口版获E-NCAP五星93.95%，官网宣称双五星」会因后半句"宣称"被整句拒。
    for seg in re.split(r"[。\n；;，,、—]", t):
        if any(k in seg for k in reject):
            continue
        if not any(k in seg for k in ("出口版", "海外版", "出口", "海外")) \
                and not any(a in seg for a in export_aliases_of(model)):
            continue
        if not re.search(r"20\d{2}", seg):        # 必须具体年份
            continue
        # 找权威体系锚点（复用动态体系正则）
        for m in _TEXT_SYS_RE.finditer(seg):
            basis = normalize(m.group("sys"))
            if basis not in _HOMOLOG_AUTHORITATIVE:
                continue
            pre = seg[max(0, m.start() - 12):m.start()]
            if any(k in pre for k in ("无", "未", "没", "非")):
                continue
            clause = seg[m.end():]
            pm = _PCT_RE.search(clause)
            sm = _STAR_RE.search(clause)
            score = float(pm.group(1)) if pm else None
            stars = _CN_NUM.get(sm.group(1)) if sm else None
            if score is None and stars is None:
                continue
            # 得分必须是合理碰撞得分率区间（40-100），防把 3.9s 加速等误抓
            if score is not None and not (40 <= score <= 100):
                continue
            ym = re.search(r"(20\d{2})", seg)
            sys_cn = SYSTEMS[basis]["cn"]
            display = (f"{sys_cn} {score:g}%" + (" 五星" if stars else "")) if score \
                else f"{sys_cn} 五星"
            return {"basis": basis, "system_cn": sys_cn, "score": score,
                    "stars": stars, "grade": None,
                    "year": int(ym.group(1)) if ym else None,
                    "display": display, "evidence": True,
                    "tier": 2, "source_quality": "同源车出口版一手权威（年份+得分率）"}
    return None


def _parse_one_anchor(t: str, seg: str, m, basis: str) -> dict | None:
    """解析单个体系锚点 → 与 parse_score 同构的 dict；证据不足返回 None。

    IIHS 奖项制 / C-IASI 三指数 / NCAP 星级得分率三分支，沿用严格防误伤规则。
    """
    if SYSTEMS[basis]["grade"] == "award":
        if any(k in seg for k in _NEG_EVIDENCE):
            return None
        am = re.search(r"(TSP\+\+|TSP\+|TSP|Top Safety Pick\+?|优秀\+|优秀|Good)", seg, re.I)
        if not am:
            return None
        award = am.group(1)
        return {"basis": basis, "system_cn": cn_name(basis), "score": None,
                "stars": None, "grade": award, "year": None,
                "display": f"{cn_name(basis)} {award}", "evidence": True}
    if basis == "ciasi":
        # 从该锚点起、到「注意：」前取维度等级，覆盖多次测评
        tail = t[t.find(m.group(0)):].split("注意：")[0]
        dim = parse_ciasi_dimensions(tail)
        if not dim:
            return None
        return {"basis": basis, "system_cn": cn_name(basis), "score": None,
                "stars": None, "grade": dim["grade"], "year": None,
                "display": f"{cn_name(basis)} {dim['grade']}",
                "ciasi_dims": dim["dims"], "ciasi_n_tests": dim["n_tests"],
                "evidence": True}
    if SYSTEMS[basis]["grade"] == "star":
        if any(k in seg for k in _NEG_EVIDENCE):
            return None
        pm = re.search(r"综合得分率\s*(\d{2,3}(?:\.\d+)?)\s*%", seg)
        sm = _STAR_RE.search(seg)
        score = float(pm.group(1)) if pm else None
        stars = _CN_NUM.get(sm.group(1)) if sm else None
        if score is None and stars is None:
            return None
        if score is not None:
            display = f"{cn_name(basis)} {score:g}%"
            if stars:
                display += " 五星"
        else:
            display = f"{cn_name(basis)} 五星"
        return {"basis": basis, "system_cn": cn_name(basis), "score": score,
                "stars": stars, "grade": None, "year": None,
                "display": display, "evidence": True}
    return None


def _score_tier(parsed: dict, seg: str, anchored: bool) -> int:
    """单条成绩的证据强度：3 一手得分率 / 2 等级星级 / 1 奖项 / 0 媒体软文旁证。

    强锚点「有<体系>官方成绩」已由 _NEG_EVIDENCE 排掉软文/宣传/否定口径，
    本身即一手官方声明，直接按成绩类型给 tier——不能再按 seg 里出现的"媒体/
    宣称/出口版"等词降级（那些词可能只是同句 commentary，如乐道L60"宣称首款
    2024版五星"、Model Y"官网/媒体2025年"、海狮07EV"媒体口径"的另一体系）。
    弱锚点（另/参考/海外/出口版获）才是次级旁证，一律 tier 0。
    """
    if not anchored:
        return 0
    if parsed.get("score") is not None:
        return 3
    if parsed.get("ciasi_dims") or parsed.get("stars"):
        return 2
    if parsed.get("grade"):
        return 1
    return 0


def parse_all_scores_from_evidence(text) -> list[dict]:
    """从证据长文本提取【全部】可识别的权威成绩，按证据强度降序。

    与严格解析器共用防误伤规则，但不在第一个锚点就 return：
      - 强锚点「有<体系>官方成绩」→ tier 按证据类型（3/2/1/0）
      - 弱锚点「另/参考/海外/出口版获<体系>」→ tier 0（只作追溯，不参与算分）
      - 同一体系多次测评（Model Y 2021/2025）保留一条、记 ciasi_n_tests
    返回的首条即「证据最强的单一成绩」，是算分唯一依据。
    """
    t = str(text or "")
    found = []
    for seg in re.split(r"[。\n]", t):
        if any(k in seg for k in _SECONDARY_REJECT):
            continue
        handled = set()
        # 强锚点「有<体系>官方成绩」：一手官方，按成绩类型给 tier
        sm = _ANCHOR_RE.search(seg)
        if (sm and not any(k in seg[:sm.start()] for k in ("无", "未", "没", "非"))
                and not any(k in seg for k in _NEG_EVIDENCE)):
            basis = normalize(sm.group("sys"))
            if basis:
                p = _parse_one_anchor(t, seg, sm, basis)
                if p:
                    p["tier"] = _score_tier(p, seg, True)
                    p["source_quality"] = "一手官方" if p["tier"] else "弱(媒体/软文/旁证)"
                    found.append(p)
                    handled.add(basis)
        # 弱锚点「另/参考/海外/出口版获<体系>」：同句可有零到多个，一律 tier 0
        for wm in _WEAK_ANCHOR_RE.finditer(seg):
            if any(k in seg[:wm.start()] for k in ("无", "未", "没", "非")):
                continue
            basis = normalize(wm.group("sys"))
            if not basis or basis in handled:
                continue
            # 弱锚点只在其自身之后取成绩，不能用同句前面强锚点的数字
            # （海狮07EV「C-NCAP 87.3%…另有E-NCAP五星」曾把 87.3% 串给 E-NCAP）
            wseg = seg[wm.start():]
            p = _parse_one_anchor(t, wseg, wm, basis)
            if not p:
                continue
            p["tier"] = 0
            p["source_quality"] = "弱(媒体/软文/旁证)"
            found.append(p)
            handled.add(basis)
    # 同一体系去重：tier 高者优先；C-IASI 多次测评次数并入保留条
    dedup = OrderedDict()
    for p in found:
        b = p["basis"]
        if b not in dedup or p["tier"] > dedup[b]["tier"]:
            dedup[b] = p
        elif p.get("ciasi_n_tests"):
            dedup[b]["ciasi_n_tests"] = max(dedup[b].get("ciasi_n_tests") or 0,
                                            p["ciasi_n_tests"])
    out = list(dedup.values())
    out.sort(key=lambda x: -x["tier"])              # 稳定排序：同级保持文本顺序
    return out


def parse_score_from_evidence(text) -> dict | None:
    """从证据长文本提取【证据最强的单一成绩】；证据不足返回 None（不猜）。

    2026-11-21 修：此前遇第一个锚点就 return，多成绩车取哪个由文本书写顺序决定。
    现在统一走 parse_all_scores_from_evidence 取首条（强度最高）。
    与 parse_score 返回同构（basis/system_cn/score/stars/grade/year/display），
    额外带 tier / source_quality / ciasi_dims / ciasi_n_tests 供审计。
    """
    scores = parse_all_scores_from_evidence(text)
    return scores[0] if scores else None


def selftest():
    assert normalize("euro_ncap") == "euro_ncap"
    assert normalize("E-NCAP") == "euro_ncap"
    assert normalize("c-ncap") == "cncap"
    assert normalize("C-IASI") == "ciasi"
    assert normalize("iihs_tsp") == "iihs"
    assert normalize("nhtsa_ncap") == "nhtsa"
    assert normalize("JNCAP") == "jncap"
    assert normalize("") is None and normalize(None) is None
    assert is_official("Bharat NCAP") and not is_official("宣传口径")
    assert cn_name("e-ncap") == "Euro NCAP"
    assert family("ciasi") == "ciasi" and family("euro_ncap") == "ncap"
    assert len(SYSTEMS) >= 12, "体系清单应覆盖全部权威体系"
    # 严格证据解析（Model Y 事故 + 误伤防护）
    my = ("有C-IASI官方成绩（非C-NCAP得分率）：①2021款——耐撞性与维修经济性P、"
          "车内乘员安全G、车外行人安全G、辅助安全G；②中期改款——耐撞维修M、"
          "车内乘员G+、车外行人G+、辅助安全G、新能源专项G。来源：官网。"
          "注意：C-IASI只给G/G+/A/M/P等级、无百分制得分率。")
    r = parse_score_from_evidence(my)
    assert r and r["basis"] == "ciasi" and r["grade"] == "G", f"Model Y C-IASI三指数应取最低G: {r}"
    assert r["ciasi_dims"] == {"occupant": "G+", "pedestrian": "G+", "assist": "G"}, r
    # 枚举句「只给G/G+/A/M/P等级」单独出现不得当成成绩
    assert parse_ciasi_dimensions("注意：C-IASI只给G/G+/A/M/P等级、无百分制得分率") is None
    # 误伤防护：宣传口径/他车成绩/智驾C-ICAP/否定句都不算成绩
    for bad in (
        "无C-NCAP成绩；宣称按C-NCAP五星安全车身设计（厂商软文口径，非实测）",
        "无官方成绩；首页最新结果中有问界M7 2025（95.0%）但无M5",
        "无官方成绩（本代NT2.0未取得）；C-NCAP基础行车辅助92.9%实为C-ICAP智驾测评",
        "明确无官方碰撞成绩，未参加C-NCAP测试",
    ):
        assert parse_score_from_evidence(bad) is None, f"误伤无成绩车: {bad}"
    # 肯定的 C-NCAP 得分率仍要抓到
    r2 = parse_score_from_evidence(
        "有C-NCAP官方成绩：C-NCAP（2024年版规程），测评年份2025，综合得分率90.7%，五星。来源：中汽测评官网")
    assert r2 and r2["basis"] == "cncap" and r2["score"] == 90.7, r2
    # 12 体系全覆盖（2026-11-21：此前只覆盖 C-NCAP/C-IASI，Euro NCAP 等 10 个取不到）
    # 体系名从 SYSTEMS 动态生成，这里逐体系验证；以后 SYSTEMS 加体系必须同步补一条。
    for code, sample in (
        ("cncap",       "有C-NCAP官方成绩：综合得分率90.7%"),
        ("ciasi",       "有C-IASI官方成绩：车内乘员G+、车外行人G+、辅助安全G"),
        ("euro_ncap",   "有Euro NCAP官方成绩：五星，成人91%"),
        ("ancap",       "有ANCAP官方成绩：五星"),
        ("iihs",        "有IIHS官方成绩：TSP+评价"),
        ("nhtsa",       "有NHTSA官方成绩：五星"),
        ("jncap",       "有JNCAP官方成绩：五星"),
        ("kncap",       "有KNCAP官方成绩：五星"),
        ("asean_ncap",  "有ASEAN NCAP官方成绩：五星"),
        ("latin_ncap",  "有Latin NCAP官方成绩：五星"),
        ("bharat_ncap", "有Bharat NCAP官方成绩：五星"),
        ("global_ncap", "有Global NCAP官方成绩：五星"),
    ):
        hit = parse_score_from_evidence(sample)
        assert hit and hit["basis"] == code, f"体系 {code} 解析失败（正则没覆盖）: {hit}"
    assert len(_TEXT_SYS_RE.findall("有C-NCAP成绩")) == 1
    # 多成绩口径（2026-11-21）：取证据最强单一成绩，不取最高/不按文本顺序
    multi = ("有C-NCAP官方成绩：综合得分率90.7%，五星。另有Euro NCAP五星（媒体口径）。")
    alls = parse_all_scores_from_evidence(multi)
    assert len(alls) == 2, f"多成绩应全部收集: {alls}"
    assert alls[0]["basis"] == "cncap" and alls[0]["tier"] == 3, alls
    assert alls[1]["basis"] == "euro_ncap" and alls[1]["tier"] == 0, alls
    main = parse_score_from_evidence(multi)
    assert main["basis"] == "cncap", f"主成绩应取一手得分率而非文本里先出现的: {main}"
    # 反序也取最强（防「谁写在前面取谁」复发）
    rev = ("另有Euro NCAP五星（媒体口径）。有C-NCAP官方成绩：综合得分率90.7%。")
    assert parse_score_from_evidence(rev)["basis"] == "cncap", "主成绩不应受文本顺序影响"
    # C-IASI 多次测评记次数、取最新一次、三指数最低
    my = ("有C-IASI官方成绩：①车内乘员G、车外行人G、辅助安全G；"
          "②车内乘员G+、车外行人G+、辅助安全G。")
    mr = parse_score_from_evidence(my)
    assert mr["grade"] == "G" and mr["ciasi_n_tests"] == 2, mr
    # 弱旁证不能当成绩（G6/宋L/ES6 踩过的坑）
    for weak in ("可参考海外ANCAP 2024五星——非国内协议，仅作弱旁证",
                 "宣称按C-NCAP五星安全车身设计（厂商口径）",
                 "出口版获Euro NCAP五星，非本代国内一手成绩，低置信"):
        assert parse_score_from_evidence(weak) is None, f"弱旁证误当成绩: {weak}"
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
