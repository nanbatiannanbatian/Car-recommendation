# -*- coding: utf-8 -*-
"""
energy_router.py — 出榜「能源形式/榜型」自动判定（单一事实源）

为什么要有这个模块（2026-11-20 用户强制固化）：
  以前「判 A 类还是 B 类、选哪种能源」只写在 SKILL.md 的自然语言里，靠执行者记得，
  结果每次出榜都要用 AskUserQuestion 问用户「选哪种能源口径」，且问法/默认不一致。
  用户明确要求：「自动准确选择什么能源形式，固定下来，不让用户选择。」
  按项目元原则，规则必须代码化 + 接入出榜链路 + 回归，只写文档等于会忘。

判定规则（确定性，永不问用户）：
  1. 显式单一能源关键词（纯电/BEV、纯油/燃油/油车/ICE、油混/HEV、插混/PHEV、增程/EREV）→ A 类单能源。
  2. 显式跨能源词（跨能源/不同能源/混排/全能源/不限能源/油电一起…）→ B 类。
  3. 显式 >=2 种能源词（如「油车和电车一起比」）→ B 类，按枚举能源排。
  4. 「新能源」→ B 类 [BEV,PHEV,EREV]（国家新能源口径，不含纯燃油/油混）。
  5. 完全没提能源（只说车型级别/价位，如「12-18万中型SUV前十」）
     → **默认 B 类跨能源全 5 种（ICE/HEV/PHEV/EREV/BEV，含油混）**，不问用户。
  6. 任何情况 needs_user_choice 恒为 False —— 禁止再用交互问题问用户选能源。

关键词歧义处理：「插电混动」含「混动」但不能判成油混 → 强关键词先按 PHEV 匹配，
裸「混动」只在没有任何强能源词命中时才兜底为 HEV。
"""
import sys, json

ALL_POWERS = ["ICE", "HEV", "PHEV", "EREV", "BEV"]
POWER_CN = {"ICE":"燃油", "HEV":"油混", "PHEV":"插混", "EREV":"增程", "BEV":"纯电"}

# 强关键词（有序，避免歧义）。裸「混动」「新能源」不在此表，单独兜底处理。
STRONG_KW = {
    "PHEV": ["插电式混合动力", "插电混动", "插电", "插混", "phev"],
    "EREV": ["增程式", "增程", "erev"],
    "HEV":  ["油电混动", "不插电混动", "油混", "双擎", "轻混", "48v", "hev"],
    "BEV":  ["纯电动", "纯电", "bev", "电动车", "电车", "电池车"],
    "ICE":  ["纯油", "燃油", "汽油", "油车", "ice", "自然吸气", "涡轮增压"],
}
CROSS_KW = ["跨能源", "不同能源", "多种能源", "混排", "全能源", "不限能源",
            "各种能源", "油电一起", "能源不限", "混着比较", "混着比",
            "放在一起比", "放一起比", "放一起", "一起比"]
NEW_ENERGY_KW = ["新能源"]


def norm_query(s):
    return str(s or "").lower().replace(" ", "")


def extract_energies(q):
    """返回 (能源集合, 命中来源)。强关键词优先；无强命中时裸混动/新能源兜底。"""
    q = norm_query(q)
    energies = set()
    hits = []
    for p in ALL_POWERS:  # 遍历顺序即优先级，集合本身无序但命中集合稳定
        for kw in STRONG_KW[p]:
            if kw in q:
                energies.add(p)
                hits.append(f"{kw}→{p}")
                break
    source = "强关键词"
    if not energies:
        if any(k in q for k in NEW_ENERGY_KW):
            energies = {"BEV", "PHEV", "EREV"}
            source = "新能源口径(纯电+插混+增程)"
        elif "混动" in q:
            energies = {"HEV"}
            source = "裸混动词兜底→油混"
    return energies, source, hits


def classify(query):
    """对用户提问文本判定榜型与入池能源。确定性，永不返回 needs_user_choice=True。"""
    q = norm_query(query)
    cross = any(k in q for k in CROSS_KW)
    energies, source, hits = extract_energies(q)

    if energies and not cross and len(energies) == 1:
        board_type = "A"
        powers = [p for p in ALL_POWERS if p in energies]
        reason = f"显式单一能源（{source}：{','.join(hits) or source}）→ A 类同能源榜"
    elif cross or len(energies) >= 2:
        board_type = "B"
        if energies:
            powers = [p for p in ALL_POWERS if p in energies]
            reason = f"显式跨能源/多能源（{source}）→ B 类跨能源榜"
        else:
            powers = ALL_POWERS[:]
            reason = "显式跨能源词但未点名具体能源 → B 类跨能源全 5 种（含油混）"
    else:
        board_type = "B"
        powers = ALL_POWERS[:]
        reason = "未指定能源（只给车型级别/价位）→ 默认 B 类跨能源全 5 种（含油混），不问用户"

    # powers 统一按 ALL_POWERS 顺序输出，杜绝集合无序导致的结果漂移
    powers = [p for p in ALL_POWERS if p in set(powers)]

    return {
        "board_type": board_type,
        "powers": powers,
        "energy_explicit": bool(energies) or cross,
        "needs_user_choice": False,   # 恒定：禁止交互问用户
        "reason": reason,
        "source": source,
        "matched": hits,
        "query": str(query or ""),
    }


def powers_cn(powers):
    return "、".join(POWER_CN.get(p, p) for p in powers)


def selftest():
    cases = [
        ("15-20万纯电轿车榜",        "A", ["BEV"]),
        ("燃油中型车前十",            "A", ["ICE"]),
        ("来一份中型纯油轿车排行",    "A", ["ICE"]),  # 纯油=纯燃油，不能落 B 类跨能源
        ("纯油车SUV前十",            "A", ["ICE"]),
        ("插混SUV排行",              "A", ["PHEV"]),
        ("增程SUV推荐",              "A", ["EREV"]),
        ("油混中型SUV前十",          "A", ["HEV"]),
        ("双擎SUV哪个好",            "A", ["HEV"]),
        ("插电混动中型SUV排行",      "A", ["PHEV"]),   # 不能被误判成油混
        ("混动中型SUV推荐",          "A", ["HEV"]),   # 裸混动兜底
        ("12-18万中型SUV前十",       "B", ALL_POWERS),  # 核心：未提能源→全类型含油混
        ("2026年9月中型轿车前十",    "B", ALL_POWERS),
        ("不同能源中型SUV比较",      "B", ALL_POWERS),
        ("跨能源中型SUV打分",        "B", ALL_POWERS),
        ("油车和电车放在一起比",      "B", ["ICE", "BEV"]),
        ("新能源中型SUV推荐",        "B", ["PHEV", "EREV", "BEV"]),  # 顺序按 ALL_POWERS
    ]
    ok = 0
    for q, bt, powers in cases:
        r = classify(q)
        assert r["board_type"] == bt, (q, r)
        assert r["powers"] == powers, (q, r["powers"], powers)
        assert r["needs_user_choice"] is False, (q, r)
        ok += 1
    print(f"[energy_router selftest] {ok}/{len(cases)} PASS")
    return ok


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        selftest()
    elif len(sys.argv) > 1:
        print(json.dumps(classify(" ".join(sys.argv[1:])), ensure_ascii=False, indent=2))
    else:
        selftest()
        print("\n用法: python energy_router.py \"<用户提问>\"  |  --selftest")
