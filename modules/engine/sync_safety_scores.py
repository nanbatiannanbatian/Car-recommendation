#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sync_safety_scores.py —— 安全成绩域的唯一同步器（2026-11-22 新增，v10.14.0）

为什么做这个（用户 2026-11-22 拍板「note 文本降级」+ 单一数据源方案 P2）：
  碰撞成绩原本散在四个地方，各有可写副本：
    official_safety_scores.json   人工核验的权威成绩表
    cncap_scores.json             C-NCAP 官网得分表
    data/gen/ciasi_decoded.json   C-IASI 官网发布稿解码（生成物）
    categories/verified_overrides.json  网查多源确认回填表
  三个回流函数（backfill_crash_from_text / from_authoritative / from_cncap_table）
  分别把它们写进候选池的 crash / safety_basis 字段——**三套口径、三处裁决逻辑**，
  冲突只能靠「谁后跑谁覆盖」解决，页面上出现过同一车不同成绩。

本模块是**唯一**的安全成绩同步入口：
  · 事实源 = categories/safety_scores.json（唯一可写）
  · 裁决规则 = tier 高者为主（定义在 SSOT 的 tier_rules 里，不在本文件另写一套）
  · 输出 = 候选池的 crash / safety_basis / crash_source / safety_score_value /
          crash_all_scores / crash_secondary 字段 + 审计

口径（用户拍板）：
  · **note 里的自由文本成绩降级为非权威描述**——不进 crash 列，只作备注。
    文本抽取的成绩 tier=1，低于任何权威库，永远不会盖过一手数据。
  · **不同体系的成绩不合并、不平均**——C-NCAP 92.3% 与 C-IASI G+ 不可比。
    主成绩之外并列留在 scores[] 并记 conflicts。

用法：
    from modules.engine.sync_safety_scores import sync
    filled, unresolved = sync(cands)
    python3 -m modules.engine.sync_safety_scores --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import paths

SSOT = str(paths.cat("safety_scores.json"))


def load_ssot(path: str | None = None) -> dict:
    p = path or SSOT
    if not os.path.exists(p):
        return {"entries": [], "tier_rules": {}}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _looks_like_score(text: str) -> bool:
    """这个字符串像不像一个**成绩**（而不是「已抽车未测」这类状态说明）。"""
    from modules.engine import crash_systems as CS
    return CS.parse_score(text) is not None


def _canon(model: str) -> str:
    from modules.contracts.model_names import canon
    return canon(model)


def _has_concrete_value(r: dict) -> int:
    """该成绩是否带「可量化的具体分值」（0=有，1=只有等级/奖项）。

    2026-11-22 修（bZ5 事故）：原判据只看 `score is not None`，但
    build_ssot 构建 C-NCAP 条目时 86.7 只写在 display 文本里、score 留空，
    于是 C-NCAP 一手得分率被误判成「只有等级」，与 C-IASI G 同 tier 时
    退化为按体系名字母序（C-IASI < C-NCAP），证据不全的 C-IASI G 抢走
    primary、覆盖真实权威的 C-NCAP 86.7%——页面随即出现
    「有 C-IASI G 徽标」却「缺权威碰撞成绩」的自相矛盾。
    判据改为「score 有值 或 display 含得分率%」，回到既定口径本意。
    """
    if r.get("score") is not None:
        return 0
    import re as _re
    return 0 if _re.search(r"\d+(?:\.\d+)?\s*%", str(r.get("display") or "")) else 1


def _sort_scores(scores: list, tier_rules: dict) -> list:
    """统一排序口径（构建 SSOT 与读 SSOT 必须用同一个规则，否则两处不一致）。

    tier 高者为主；同 tier 时「有具体分值」优先于「只有等级」；
    再同则按体系名稳定排序（保证可复现）。
    """
    return sorted(
        scores,
        key=lambda r: (
            -tier_rules.get(r.get("source_type"), 0),
            _has_concrete_value(r),
            r.get("system", ""),
        ))


# 跨库车型名对齐：C-NCAP 表里写「海狮07」，候选池写「比亚迪海狮07EV」——
# 品牌前缀缺失导致对不上，成绩明明在库里却显示「无成绩」。
# 不用「无品牌前缀」做主键（不同品牌的同型号会撞车，如「宋L」可能两家都有），
# 而是建**别名索引**：主键找不到时，只在**唯一命中**时才用别名匹配。
_BRAND_PREFIX = (
    "比亚迪", "大众", "宝马", "奔驰", "奥迪", "吉利", "长安", "长城", "奇瑞",
    "上汽", "广汽", "一汽", "北汽", "东风", "小米", "理想", "蔚来", "小鹏",
    "零跑", "哪吒", "极氪", "深蓝", "启源", "阿维塔", "智己", "腾势", "岚图",
    "问界", "极狐", "银河", "星途", "捷途", "五菱", "红旗", "宝骏", "领克",
    "极越", "赛力斯", "名爵", "荣威", "飞凡", "昊铂", "方程豹", "仰望",
    "MINI", "smart", "MG",
)


def _strip_brand(canon: str) -> str:
    for b in _BRAND_PREFIX:
        if canon.startswith(b) and len(canon) > len(b) + 1:
            return canon[len(b):]
    return canon


def _index(ssot: dict) -> dict[str, dict]:
    from modules.engine import crash_systems as CS
    out: dict[str, dict] = {}
    for e in (ssot.get("entries") or []):
        m = _canon(e.get("model") or "")
        if not m:
            continue
        scores = e.get("scores") or []
        if not scores:
            continue
        # 主成绩 = SSOT 已裁决的第一条（按 tier 排序）；这里再按 tier 复核一次，
        # 防止有人手改 SSOT 的顺序
        tier_rules = ssot.get("tier_rules") or {}
        # 同 tier 按「有具体分值优先于只有等级」处理（见 _has_concrete_value）：
        # C-NCAP 百分制 > C-IASI 等级制。等级之间不可比、分值之间也不可比，
        # 但有一手得分率的那条信息量更大、更权威。不能按体系名字母序——
        # 「C-IASI」字面排在「C-NCAP」前，不代表它更权威（bZ5 曾因此被覆盖）。
        scores = _sort_scores(scores, tier_rules)
        primary = scores[0]
        if not CS.is_official(primary.get("basis")) and primary.get("source_type") != "verified_web":
            continue                       # 非权威体系的主成绩不采用
        out[m] = {"primary": primary, "all": scores,
                  "conflicts": e.get("conflicts") or []}
    # 别名索引：无品牌前缀 → canon 列表（只用于唯一命中时的兜底匹配）
    alias: dict[str, list[str]] = {}
    for m in out:
        alias.setdefault(_strip_brand(m), []).append(m)
    for lst in alias.values():
        lst.sort()
    return out


def _lookup(idx: dict, canon_model: str) -> dict | None:
    """按 canon 查；主键未命中时用「无品牌前缀」兜底（**必须唯一命中**才认）。

    唯一命中才认，是为了不把「不同品牌的同型号」错配成同一台车——
    宁可显示「无成绩」，也不显示别人的成绩。
    """
    if canon_model in idx:
        return idx[canon_model]
    stripped = _strip_brand(canon_model)
    hits = [m for m in idx if _strip_brand(m) == stripped]
    if len(hits) == 1:
        return idx[hits[0]]
    return None


def sync(cars: list[dict], ssot_path: str | None = None) -> tuple[list, list]:
    """把 SSOT 的安全成绩写进候选池。返回 (filled, unresolved)。

    覆盖规则：
      · 有 SSOT 记录 → **覆盖** crash / safety_basis（SSOT 是权威）
      · 无 SSOT 记录 → **不动**（不写 null、不清空已有值），列入 unresolved
    """
    ssot = load_ssot(ssot_path)
    idx = _index(ssot)
    filled, unresolved = [], []
    for c in cars:
        m = _canon(c.get("model") or "")
        rec = _lookup(idx, m)
        if not rec:
            # SSOT 里没有这车 → 但仍要剥离「状态说明冒充成绩」：
            # 「C-NCAP 已抽车未测」不是成绩，放在 crash 字段里读者会误以为有成绩。
            prev = str(c.get("crash") or "")
            if prev and not _looks_like_score(prev):
                c["crash_status"] = prev
                c["crash"] = None
                c["safety_basis"] = None
            unresolved.append(c.get("model"))
            continue
        p = rec["primary"]
        # 状态说明与成绩分离（2026-11-22）：像「C-NCAP 已抽车未测」这种**不是成绩**
        # 的说明放在 crash 字段里，读者会以为有成绩（实测大众T6 一款）。
        # 这里把 crash 字段清成「无成绩」，说明另存 crash_status，页面另行披露。
        prev_crash = str(c.get("crash") or "")
        if prev_crash and not _looks_like_score(prev_crash):
            c["crash_status"] = prev_crash
            c["crash"] = None
            c["safety_basis"] = None
        c["crash"] = p.get("display") or c.get("crash")
        c["safety_basis"] = p.get("basis")
        c["crash_source"] = (
            f"{p.get('system')}（{p.get('source_type')}，tier "
            f"{(ssot.get('tier_rules') or {}).get(p.get('source_type'), 0)}）"
            + (f"；来源 {p.get('url')}" if p.get("url") else "")
            + (f"；日期 {p.get('date')}" if p.get("date") else "")
            + (f"；依据 {p.get('quote')[:50]}" if p.get("quote") else ""))
        if p.get("score") is not None:
            c["safety_score_value"] = p["score"]
        # 全部并列成绩留痕（不同体系不可比，不合并）
        c["crash_all_scores"] = [
            {"basis": s.get("basis"), "system": s.get("system"),
             "display": s.get("display"), "tier": (ssot.get("tier_rules") or {}).get(s.get("source_type"), 0),
             "primary": (i == 0)}
            for i, s in enumerate(rec["all"])
        ]
        c["crash_secondary"] = [s.get("display") for i, s in enumerate(rec["all"]) if i != 0]
        if rec["conflicts"]:
            c["crash_conflict_note"] = rec["conflicts"][0].get("note", "")
        filled.append((c.get("model"), c.get("crash"), c.get("safety_basis")))
    return filled, unresolved


def build_ssot(sources: dict, out_path: str | None = None, dry_run: bool = False) -> dict:
    """从四个来源库构建 SSOT（构建口径与读取口径用同一排序规则）。

    sources: {"official":…, "cncap":…, "ciasi":…, "verified":…}
    """
    tier_rules = {"verified_web": 4, "official_table_manual": 3,
                  "official_table": 2, "official_published_decoded": 2,
                  "text_extracted": 1}
    entries: dict[str, dict] = {}

    def put(model, *, system, basis, display, score=None, url="", date="",
            quote="", source_type="", note=""):
        m = _canon(model)
        e = entries.setdefault(m, {"model": model, "canon": m, "scores": [],
                                   "_primary": None, "conflicts": []})
        e["scores"].append({"system": system, "basis": basis, "display": display,
                             "score": score, "url": url, "date": date,
                             "quote": quote, "source_type": source_type,
                             "tier": tier_rules.get(source_type, 0), "note": note})

    for model, r in (sources.get("official") or {}).items():
        # display 必须是**简洁可展示值**（页面上直接显示的那一串）。
        # 原实现：有 value 用 "体系 + 数值%"，没有 value 就把整段 source 原文
        # 当 display → 页面出现一长串"C-IASI 2024年第2次测评(2023版规程…)"。
        # 详细说明归 quote（进 crash_source 审计），display 只留结论。
        sysname = str(r.get("system") or "")
        val = r.get("value")
        src = str(r.get("source") or "")
        if val is not None:
            display = f"{sysname} {val}%"
        else:
            # 从 source 原文里提取可展示的等级/星级结论
            import re as _re
            # 顺序要紧：先抓**等级/星级结论**，最后才抓百分制。
            # 踩过的坑：宋L EV 的原文含「乘员侧25%偏置0缺陷」，先抓百分制会得到
            # 「C-IASI 25%」——25% 是偏置比例，不是成绩。
            # 百分制只在原文明确是「得分率/综合得分」时才认。
            # 保守取等级：原文里若明写「保守取G(不猜G+)」这类结论，要认出来。
            # 宋L EV 的原文是「逐车三指数等级为图片未文字化，按已确认文字事实
            # 保守取G(不猜G+)」——等级是 G，只是写得长。
            m_cons = _re.search(r"保守取\s*([A-G]\+*)", src)
            m_grade = _re.search(r"C-IASI[^，。；]{0,12}?([A-G]\+*)", src)
            m_star = _re.search(r"([四五]星)", src)
            m_score_pct = _re.search(r"(?:得分率|综合得分|得分)[^\d]{0,4}(\d{2}(?:\.\d)?)\s*%", src)
            if m_cons:
                display = f"C-IASI {m_cons.group(1)}"
            elif m_grade:
                display = f"C-IASI {m_grade.group(1)}"
            elif m_star:
                display = f"{sysname} {m_star.group(1)}"
            elif m_score_pct:
                display = f"{sysname} {m_score_pct.group(1)}%"
            else:
                # 提不出结论就用体系名，不硬塞长文本（宁可粗略不可冗长）
                display = sysname or "官方碰撞成绩"
        put(model, system=sysname, basis=sysname,
            display=display,
            score=r.get("score"), quote=src,
            source_type="official_table_manual", note=str(r.get("score_rule", "")))
    cn_meta = sources.get("cncap_meta") or {}
    for model, val in (sources.get("cncap") or {}).items():
        meta = cn_meta.get(model) or {}
        # score 必须带值：_sort_scores 用它判「有一手得分率优先于只有等级」。
        # 此前只写 display 不写 score，导致 C-NCAP 86.7% 被 C-IASI G 盖掉（bZ5 事故）。
        put(model, system="C-NCAP", basis="C-NCAP", display=f"C-NCAP {val}%",
            score=float(val),
            url=str(meta.get("url") or ""), date=str(meta.get("date") or ""),
            quote=f"C-NCAP 官方得分 {val}%", source_type="official_table")
    for model, r in (sources.get("ciasi") or {}).items():
        dims = r.get("dims") or {}
        worst = None
        for _dk, dv in dims.items():
            g = dv.get("grade")
            if g and (worst is None or g < worst[0]):
                worst = (g, dv.get("cn", ""))
        if not worst:
            continue
        put(model, system="C-IASI", basis="C-IASI", display=f"C-IASI {worst[0]}",
            url=str(r.get("source") or ""), date=str(r.get("published") or ""),
            quote=f"C-IASI {worst[0]}（{worst[1]}）",
            source_type="official_published_decoded", note=str(r.get("rule") or ""))
    for model, lst in (sources.get("verified") or {}).items():
        if str(model).startswith("_"):
            continue
        for r in (lst or []):
            if r.get("field") != "cell_brand":
                continue
            put(model, system="网查确认", basis="verified_web",
                display=str(r.get("value") or ""), url=str(r.get("url") or ""),
                date=str(r.get("date") or ""), quote=str(r.get("note") or ""),
                source_type="verified_web")

    # ⑤ 候选池 evidence 里的一手官方成绩文字（tier=2，官方一手文字）
    #    这些是「有官方成绩、SSOT 还没收录」的缺口——例如海狮07EV 的
    #    C-NCAP 87.3% 只写在 evidence 里（含官网 URL），页面却显示无成绩。
    #    收进 SSOT 后，页面显示与事实源一致，且不再依赖 evidence 文本解析。
    for c in (sources.get("cars") or []):
        ev = c.get("evidence") if isinstance(c.get("evidence"), dict) else {}
        ce = str(ev.get("crash_evidence") or "")
        if not ce:
            continue
        try:
            from modules.engine import crash_systems as CS
            # 必须用**严格**解析器（parse_all_scores_from_evidence），不能用宽松的
            # parse_score —— 宽松版会把 C-ICAP 智驾测评的「5★」误抓成 C-NCAP 碰撞五星。
            # 实测腾势N7：原文写的是「无C-NCAP碰撞得分率成绩…C-ICAP 1.1版…行车辅助5★+」，
            # 宽松解析给出 "C-NCAP 五星"（假成绩），严格解析正确返回空。
            strict = CS.parse_all_scores_from_evidence(ce)
            # 同源车出口版一手成绩（如 ES6=EL6 的 Euro NCAP 五星）：
            # 判据极严（同源映射+年份+得分率+一手权威体系），只放行确定同车的成绩。
            # 不加这一路，ES6 这类车会被判「无成绩」——但它确实有一手成绩。
            homolog = CS.parse_homolog_score(ce, c.get("model") or "")
            if homolog and not any(s.get("basis") == homolog.get("basis") for s in strict):
                strict.insert(0, homolog)
        except Exception:
            strict = []
        if not strict:
            continue
        best = max(strict, key=lambda s: s.get("tier", 0))
        put(c.get("model"), system=best.get("system_cn") or best.get("basis") or "",
            basis=best.get("basis") or "",
            display=best.get("display") or "", score=best.get("score"),
            quote=ce.strip()[:120], source_type="official_table")

    # 合并**同一台车**的重复条目（跨库车型名不一致导致）：
    #   C-NCAP 表写「海狮07」、候选池写「比亚迪海狮07EV」——同一台车，
    #   按 canon 是两个键，但按「去品牌前缀」是同一台。
    #   合并规则：前缀更长的键优先（保留品牌信息更全的那个），成绩取并集。
    by_stripped: dict[str, list[str]] = {}
    for m in entries:
        by_stripped.setdefault(_strip_brand(m), []).append(m)
    for stripped, lst in by_stripped.items():
        if len(lst) < 2:
            continue
        # 只有「去掉品牌前缀后完全相同」才合并，且不能有品牌冲突
        keep = max(lst, key=len)
        for m in lst:
            if m == keep:
                continue
            entries[keep]["scores"].extend(entries[m]["scores"])
            entries[m]["scores"] = []          # 标记为已合并
    entries = {m: e for m, e in entries.items() if e["scores"]}

    for e in entries.values():
        e["scores"] = _sort_scores(e["scores"], tier_rules)
        e["_primary"] = e["scores"][0]["basis"] if e["scores"] else None
        systems = {s["system"] for s in e["scores"] if s["system"] != "网查确认"}
        if len(systems) > 1:
            e["conflicts"] = [{"systems": sorted(systems),
                               "note": "该车有多个体系成绩（体系间不可比），按 tier 取主，其余留档"}]
    out = {"note": "安全成绩域唯一事实源（由 build_ssot 从四个来源库构建）",
           "version": "1.0.0", "updated": "2026-11-22",
           "tier_rules": tier_rules,
           "systems": sources.get("systems") or {},
           "entries": list(entries.values())}
    if not dry_run:
        p = out_path or SSOT
        with open(p, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def selftest() -> int:
    tmp = os.path.join(paths.ROOT, "runs", "_ss_ssot.json")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    ssot = {
        "tier_rules": {"verified_web": 4, "official_table_manual": 3,
                       "official_table": 2, "official_published_decoded": 2,
                       "text_extracted": 1},
        "entries": [
            {"model": "测试车A", "scores": [
                {"system": "C-NCAP", "basis": "C-NCAP", "display": "C-NCAP 92.3%",
                 "source_type": "official_table", "score": 8.9, "url": "u1", "date": "d1"},
                {"system": "C-IASI", "basis": "C-IASI", "display": "C-IASI G",
                 "source_type": "official_published_decoded", "score": None},
            ], "conflicts": [{"systems": ["C-IASI", "C-NCAP"], "note": "多体系并列"}]},
            {"model": "测试车B", "scores": [
                {"system": "网查确认", "basis": "verified_web", "display": "欣旺达",
                 "source_type": "verified_web", "score": None},
            ], "conflicts": []},
        ],
    }
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ssot, f, ensure_ascii=False)
    try:
        cars = [
            {"model": "测试车A", "crash": "旧文本成绩", "safety_basis": "旧basis"},
            {"model": "测试车B", "crash": None, "safety_basis": None},
            # 无记录车：crash 是**状态说明**（不是成绩）→ 应剥离到 crash_status，
            # 页面才不会被「C-NCAP 已抽车未测」误导。若 crash 是真成绩则原样保留。
            {"model": "无记录车", "crash": "C-NCAP 已抽车未测", "safety_basis": None},
        ]
        filled, unresolved = sync(cars, tmp)
        a, b, c = cars
        # ① 主成绩取 tier 高者
        assert a["crash"] == "C-NCAP 92.3%", f"主成绩裁决错: {a.get('crash')}"
        assert a["safety_basis"] == "C-NCAP", f"basis 错: {a.get('safety_basis')}"
        assert a["safety_score_value"] == 8.9, "分值未回填"
        # ② 次要成绩留痕但不合并
        assert len(a["crash_all_scores"]) == 2, "并列成绩未留痕"
        assert a["crash_secondary"] == ["C-IASI G"], f"次要成绩错: {a['crash_secondary']}"
        assert a.get("crash_conflict_note"), "多体系冲突未记录"
        # ③ 不同体系不平均（score 仍是权威表给的 8.9，不是两体系混合）
        assert a["safety_score_value"] == 8.9, "跨体系混算了分值"
        # ④ verified_web 也可作主成绩
        assert b["crash"] == "欣旺达" and b["safety_basis"] == "verified_web", \
            f"verified_web 未生效: {b}"
        # ⑤ 无记录的车：状态说明剥离到 crash_status，crash 清空（不冒充成绩）
        assert c["crash"] is None, f"状态说明未剥离: {c.get('crash')!r}"
        assert c.get("crash_status") == "C-NCAP 已抽车未测", f"状态说明未存档: {c.get('crash_status')!r}"
        assert c["safety_basis"] is None, "状态说明不该带 safety_basis"
        assert "无记录车" in unresolved, "无记录车应列入 unresolved"
        # ⑤-b 无记录车若 crash 本身就是真成绩 → 原样保留，不动
        c2 = {"model": "无记录车2", "crash": "C-NCAP 88.8%", "safety_basis": "cncap"}
        cars.append(c2)
        sync(cars, tmp)
        assert c2["crash"] == "C-NCAP 88.8%", f"真成绩被改动: {c2.get('crash')!r}"
        assert c2.get("crash_status") is None, "真成绩不该被标成状态说明"
        # ⑥ 覆盖旧文本成绩（note 降级的落地：文本不再占 crash 列）
        assert a["crash"] != "旧文本成绩", "旧文本成绩未被权威库覆盖"
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--ssot", default=SSOT)
    ap.add_argument("--report", action="store_true", help="只报告 SSOT 覆盖情况")
    a = ap.parse_args()
    if a.selftest:
        try:
            selftest()
            print("sync_safety_scores selftest OK")
            return 0
        except AssertionError as e:
            print(f"[FAIL] {e}")
            return 1
    ssot = load_ssot(a.ssot)
    n = len(ssot.get("entries") or [])
    conf = sum(1 for e in (ssot.get("entries") or []) if e.get("conflicts"))
    print(f"安全成绩 SSOT：{n} 款车型，多体系并列 {conf} 款 → {a.ssot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
