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
    return bool(_SCORE_RE.search(t))


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
