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
_TEXT_SYS_RE = re.compile(
    r"(?P<sys>C\s*-?\s*NCAP|CNCAP|C\s*-?\s*IASI|CIASI|Euro\s*NCAP|E\s*-?\s*NCAP|ANCAP|IIHS|NHTSA)",
    re.I)
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
                "stars": stars, "grade": grade, "display": display}
    return None


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
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
