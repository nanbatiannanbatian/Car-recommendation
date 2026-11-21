#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原始库 → 事实库 全字段映射同步（facts_sync.py）v9.7.28。

用户要求：不要"原始库有数据、事实库没有"。原始库 all_data.json 有 19 个字段，
但事实库 fact_library.json 只映射了 5 个（kwh/consumption/cell_brand/cltc/source），
sales/life/life_note/trim/来源URL/证据引文等全部丢失。

本工具：
  1) 把原始库每个字段按映射表同步进事实库（不覆盖事实库里更权威的手工值）；
  2) 报告"原始有但事实库无"（已修复）与"原始无"（需另行网查核实）的字段；
  3) --check 只检查不同步，--apply 执行同步，--report 出字段覆盖报告。

用法：
  python3 facts_sync.py --report
  python3 facts_sync.py --check [--src <all_data.json>] [--facts <fact_library.json>]
  python3 facts_sync.py --apply
  python3 facts_sync.py --selftest
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys

import paths

HERE = str(paths.ROOT)
# 原始库位置：优先环境变量，其次 skill 内随包副本，最后相对工作区约定路径
DEFAULT_SRC = (os.environ.get("CAR_ALL_DATA")
               or str(paths.cat("source_all_data.json"))
               or "")
DEFAULT_FACTS = str(paths.cat("fact_library.json"))

# 原始字段 → 事实库字段。None = 原样保留在 _source 子对象（证据/来源，不进主字段）
FIELD_MAP = {
    "kwh": "kwh",
    "consumption": "consumption",
    "cell": "cell_brand",
    "sales": "trend_sales",
    "life": "life_status",
    "life_note": "life_note",
    "note": "note",
    "trim": "scored_trim",
    "consumption_std": "consumption_std",
    "kwh_per_wan": "kwh_per_wan",
    "price": "price",
}
# 证据/来源字段，全部收进 _source 子对象（可核验，不参与打分）
SOURCE_FIELDS = ["price_src", "cons_src", "cons_quote", "cons_trim", "sales_src", "flags"]
# 原始库里没有、但榜单需要的字段 → 已逐一网查核实结论（2026-11-20）
# 格式: 字段: (结论, 依据, 处理)
NOT_IN_SOURCE_DETAIL = {
    "charge/voltage": ("有但漏采（已修 v9.7.27）", "汽车之家/易车参数页有高压平台(V)/快充时间(min)",
                       "已网查补110/110配置，写进 trims.json"),
    "crash": ("真没有（这批车）", "ice_crash.json 187款中这批紧凑纯电0命中；MG4仅Euro NCAP不在C-NCAP/C-IASI库",
              "按规则标缺失不记0分，有官方成绩再补"),
    "airbags": ("另有专门库", "airbags.json 38款，2026-10覆盖25/39(64%)", "不依赖原始库，专门库核验"),
    "adas_hw": ("另有专门库", "thirdparty_tests.json + 配置表 key_configs", "按配置级硬件字段处理"),
}
NOT_IN_SOURCE = list(NOT_IN_SOURCE_DETAIL)


def load(p):
    if not p or not os.path.exists(p):
        raise SystemExit(
            "[facts_sync] 找不到原始库。请设置环境变量 CAR_ALL_DATA 指向 all_data.json，"
            "或把副本放到 categories/source_all_data.json。")
    return json.load(open(p, encoding="utf-8"))


def sync(src_path=DEFAULT_SRC, facts_path=DEFAULT_FACTS, apply=False):
    src = load(src_path)
    rows = src.get("rows", {})
    facts = load(facts_path) if os.path.exists(facts_path) else {}
    changed = []
    for model, r in rows.items():
        f = facts.setdefault(model, {}) if apply else facts.get(model, {})
        if f is None:
            continue
        for sf, tf in FIELD_MAP.items():
            v = r.get(sf)
            if v in (None, "", [], {}):
                continue
            # 不覆盖事实库已有的非默认值（手工核验值更权威）
            if tf in f and f[tf] not in (None, "", "—"):
                continue
            if apply:
                f[tf] = v
            changed.append((model, sf, tf))
        # 来源/证据子对象
        sv = {k: r[k] for k in SOURCE_FIELDS if r.get(k) not in (None, "", [], {})}
        if sv:
            src_obj = f.get("_source", {}) if not apply else f.setdefault("_source", {})
            for k, v in sv.items():
                if apply:
                    src_obj.setdefault(k, v)
                elif src_obj.get(k) in (None, "", [], {}):
                    changed.append((model, k, f"_source.{k}"))
    if apply:
        facts["_meta"] = {
            "synced_from": os.path.basename(src_path),
            "sync_tool": "facts_sync.py",
            "field_map": FIELD_MAP,
            "source_fields": SOURCE_FIELDS,
            "note": "本段由原始库自动同步；手工核验值优先，不被覆盖。",
        }
        json.dump(facts, open(facts_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return changed, rows, facts


def report(src_path=DEFAULT_SRC, facts_path=DEFAULT_FACTS):
    src = load(src_path)
    rows = src.get("rows", {})
    facts = load(facts_path) if os.path.exists(facts_path) else {}
    all_src_fields = set()
    for r in rows.values():
        all_src_fields |= set(r.keys())
    mapped = set(FIELD_MAP) | set(SOURCE_FIELDS)
    print("=== 原始库 → 事实库 字段映射报告 ===")
    print(f"原始库 {len(rows)} 款 / {len(all_src_fields)} 字段；事实库 {len(facts)-1} 款车型")
    print("\n已映射字段（原始→事实）:")
    for sf, tf in FIELD_MAP.items():
        n = sum(1 for r in rows.values() if r.get(sf) not in (None, "", [], {}))
        print(f"  {sf:20s} → {tf:20s} 原始覆盖 {n}/73")
    print("\n来源/证据字段（收进 _source）:")
    for sf in SOURCE_FIELDS:
        n = sum(1 for r in rows.values() if r.get(sf) not in (None, "", [], {}))
        print(f"  {sf:20s} 原始覆盖 {n}/73")
    unmapped = sorted(all_src_fields - mapped)
    print("\n原始库未映射字段:", unmapped or "无")
    print("\n原始库没有的字段——逐一核实结论:")
    for f, (conclusion, basis, action) in NOT_IN_SOURCE_DETAIL.items():
        print(f"  {f:16s} {conclusion}")
        print(f"    依据: {basis}")
        print(f"    处理: {action}")
    # 检查事实库当前是否已同步
    synced = "_meta" in facts and facts["_meta"].get("sync_tool") == "facts_sync.py"
    print(f"\n事实库已执行同步: {'是' if synced else '否（运行 --apply）'}")
    return synced


def selftest():
    changed, rows, facts = sync(apply=False)
    assert rows and len(rows) > 50, "原始库读取失败"
    assert "cell" in FIELD_MAP and FIELD_MAP["cell"] == "cell_brand"
    assert "charge/voltage" in NOT_IN_SOURCE_DETAIL, "电压核实结论缺失"
    print(f"[selftest OK] 映射表 {len(FIELD_MAP)} 字段，原始库 {len(rows)} 款")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="原始库→事实库字段映射同步")
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--facts", default=DEFAULT_FACTS)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(selftest())
    if a.report:
        report(a.src, a.facts)
    elif a.apply:
        changed, _, _ = sync(a.src, a.facts, apply=True)
        print(f"[apply] 同步 {len(changed)} 个字段值到 {a.facts}")
    elif a.check:
        changed, _, _ = sync(a.src, a.facts, apply=False)
        if changed:
            print(f"[check] 有 {len(changed)} 个字段未同步（原始有、事实库无）")
            for m, sf, tf in changed[:15]:
                print(f"  {m}: {sf} → {tf}")
            raise SystemExit(1)
        print("[check] 全部字段已同步")
    else:
        ap.error("指定 --report / --check / --apply / --selftest 之一")


# ---------------------------------------------------------------- 价格回流
def trims_price_map(trims_path=None) -> dict:
    """{canon(车型): [最低指导价, 最高指导价]} —— 供回填后的独立复核用。"""
    from modules.contracts.model_names import canon
    if trims_path:
        if not os.path.exists(trims_path):
            return {}
        db = json.load(open(trims_path, encoding="utf-8"))
        entries = db.get("entries", [])
    else:
        # 2026-11-22：走统一入口（人工库 + 自动库合流），否则人工车型的官方价
        # 可能读不到（人工条目已不再留在自动库里）。
        from modules.data.trims_store import load as _load
        entries = list(_load().values())
    out = {}
    for e in entries:
        prices = [x.get("price") for x in e.get("trims", [])
                  if isinstance(x.get("price"), (int, float))]
        if prices:
            out[canon(e.get("model") or "")] = [min(prices), max(prices)]
    return out


def backfill_price_guide_from_trims(cars, trims_path=None):
    """把 trims.json 里已核验的官方指导价，回流到候选池的 price_guide。

    为什么必须在链路上（2026-11-21 出中型纯电SUV榜的真实事故）：
      auto_trims 已从汽车之家参数页解析出官方指导价并写进 trims.json，
      但候选池的 price_guide 还是 None；闸门只认 price_guide，于是 11 款车
      （极氪7X / Model Y / 蔚来ES6 / 腾势N7 / 乐道L60 …）全被判 price_unknown
      落榜，榜单只剩 10 款且全是 13-16 万。数据采到了、没回流 = 白采。

    当时的补救是一个手动命令（master_pool --sync-prices），靠人记得跑——
    那就是「规则只存在人的记忆里」。现在改成出榜前自动执行，并保留手动入口。

    规则：
      - 只填空值，不覆盖候选池已有的 price_guide（已有值可能来自更权威的官网逐个核对）
      - 依据是 trims.json 的 price 字段，契约强制为数值且禁用促销价（见 trims_schema）
      - 来源写进 price_source，可追溯

    返回 (filled, still_missing)：
      filled        [(车型, [最低价, 最高价]), ...] 已回填
      still_missing [车型, ...] 仍无价且 trims 也没有 → 真缺口，需补采集（出榜时披露）
    """
    from modules.contracts.model_names import canon

    if trims_path:
        if not os.path.exists(trims_path):
            return [], [c.get("model") for c in cars
                        if not (isinstance(c.get("price_guide"), (list, tuple)) and c.get("price_guide"))]
        db = json.load(open(trims_path, encoding="utf-8"))
        entries = db.get("entries", [])
    else:
        from modules.data.trims_store import load as _load
        entries = list(_load().values())
    by = {canon(e.get("model") or ""): e for e in entries}

    filled, still = [], []
    for c in cars:
        g = c.get("price_guide")
        if isinstance(g, (list, tuple)) and g and g[0]:
            continue
        e = by.get(canon(c.get("model") or ""))
        prices = [x.get("price") for x in (e or {}).get("trims", [])
                  if isinstance(x.get("price"), (int, float))]
        if not prices:
            still.append(c.get("model"))
            continue
        c["price_guide"] = [min(prices), max(prices)]
        c["price_source"] = "trims.json 官方指导价（汽车之家参数页逐配置解析，已核验非促销价）"
        filled.append((c.get("model"), c["price_guide"]))
    return filled, still


# ---------------------------------------------------------------- 碰撞成绩回流
def backfill_crash_from_text(cars):
    """把 crash / note 自由文本里的官方碰撞成绩，结构化进 crash + safety_basis。

    为什么必须在链路上（2026-11-21）：
      极氪7X / 阿维塔07 / 乐道L60 / 海狮07 EV 的 C-NCAP 成绩（90.7% / 90.2% / 89.2% / 87.3%）
      只写在 note 文本里，crash 和 safety_basis 都是空 → 被判「无权威碰撞实锤」。
      极氪7X 家族命中 6/6、全情景 98%、证据 A、月销 8500，只因这一条进不了一档。
      当时只能人工补；现在改成出榜前自动解析（crash_systems.parse_score 是唯一解析器）。

    规则：
      - 已有权威 basis 的车不覆盖（尊重已核验的一手数据）
      - crash 已有具体成绩但 basis 空（如零跑C10）→ 只补 basis，不改文案
      - 解析依据是 crash/note/safety_note 三处文本，来源写进 crash_source
      - 2026-11-21 补：成绩还可能只写在 evidence.crash_evidence（Model Y 即此情形：
        C-IASI 官网 show-784 的一手等级全在证据里，顶层 crash/basis 都空 → 被误标
        「缺权威碰撞成绩」并被封顶）。但 evidence 是长文本，宽松的 parse_score 会把
        「五星安全车身设计」宣传口径 / 他车成绩 / C-ICAP 智驾分误抓成碰撞成绩
        （实测误伤 6 款无成绩车），所以 evidence 必须走严格解析器
        crash_systems.parse_score_from_evidence（只认肯定锚点 + C-IASI 维度结构）。
    返回 (filled, unresolved)：unresolved = 文本里也解析不出成绩的车
    """
    from modules.engine import crash_systems as CS

    filled, unresolved = [], []
    for c in cars:
        if CS.is_official(c.get("safety_basis")):
            continue
        text = " ".join(str(c.get(k) or "") for k in ("crash", "note", "safety_note"))
        parsed = CS.parse_score(text)
        source_tag = "crash/note 文本"
        quote = text.strip()[:60]
        # 多成绩审计（2026-11-21）：evidence 里若有多个体系成绩，全部结构化留痕，
        # 但只取证据最强的一条算分（parse_score_from_evidence 已按 tier 排序）。
        # 被丢弃的次要成绩不参与打分、不改页面，只进审计字段 + 报告披露。
        # 注意：无论主成绩从哪来（note / evidence），只要 evidence 有多成绩就留痕——
        # 极氪7X note 写了 C-NCAP、evidence 另有 Euro NCAP，两个分支都要记。
        ev = c.get("evidence") or {}
        ce = str(ev.get("crash_evidence") or "") if isinstance(ev, dict) else ""
        all_scores = CS.parse_all_scores_from_evidence(ce) if ce else []
        # 同源车出口版一手成绩（v10.5.2）：如蔚来ES6=EL6 的 Euro NCAP 一手五星。
        # 判据极严（同源映射+年份+得分率+一手权威体系），只放行确定同车的成绩，
        # 泛泛的「宣称双五星」「按X标准设计」仍拒绝。零映射的车根本不进此分支。
        # 注意：无论顶层是否已解析到，都要算同源成绩并入审计——否则 note 里带
        # 同源体系名时顶层 parse_score 抓到成绩、却漏写 crash_all_scores（t088 事故）。
        homolog = CS.parse_homolog_score(ce, c.get("model")) if ce else None
        if homolog and not any(s["basis"] == homolog["basis"] for s in all_scores):
            all_scores.insert(0, homolog)
            all_scores.sort(key=lambda s: -s.get("tier", 0))
        if not parsed and all_scores:
            # 顶层三处解析不出 → 用 evidence 里证据最强的一条（严格解析，防误伤）
            parsed = all_scores[0]
            source_tag = ("同源车出口版一手成绩（evidence 严格解析）"
                          if parsed is homolog else "evidence.crash_evidence 严格解析")
            quote = ce.strip()[:60]
        if not parsed:
            unresolved.append(c.get("model"))
            continue
        if not CS.has_score(c.get("crash")):
            c["crash"] = parsed["display"]
        c["safety_basis"] = parsed["basis"]
        c["crash_source"] = (f"由 {source_tag} 自动解析（原文：" +
                             quote + "）；已核验体系 " + parsed["system_cn"])
        if parsed.get("score") is not None:
            c["safety_score_value"] = parsed["score"]
        if parsed.get("ciasi_dims"):
            c["crash_ciasi_dims"] = parsed["ciasi_dims"]
        if parsed.get("ciasi_n_tests"):
            c["crash_ciasi_n_tests"] = parsed["ciasi_n_tests"]
        # 主成绩之外的全部成绩（含被丢弃的次要成绩）留痕
        if all_scores:
            # primary 指向实际采用的成绩：主成绩来自 note 时按其 basis 对齐，
            # 来自 evidence 时就是 tier 最高的首条（避免审计与 crash 字段两套数）。
            primary_basis = parsed["basis"]
            primary_idx = next((i for i, s in enumerate(all_scores)
                                if s["basis"] == primary_basis), 0)
            c["crash_all_scores"] = [
                {"basis": s["basis"], "system": s["system_cn"],
                 "display": (parsed["display"] if i == primary_idx else s["display"]),
                 "tier": s["tier"], "quality": s["source_quality"],
                 "primary": (i == primary_idx),
                 **({"n_tests": s["ciasi_n_tests"]} if s.get("ciasi_n_tests") else {})}
                for i, s in enumerate(all_scores)
            ]
            c["crash_secondary"] = [
                s["display"] for i, s in enumerate(all_scores) if i != primary_idx
            ]
        filled.append((c.get("model"), parsed["display"], parsed["basis"]))
    return filled, unresolved


# ---------------------------------------------------------------- 权威成绩库回流
def backfill_crash_from_authoritative(cars, table_path=None):
    """把 official_safety_scores.json 里已人工核验的成绩回流进候选池字段。

    为什么要有这条（2026-11-21 全量排查）：
      文本回填（backfill_crash_from_text）只能解析「候选池里写了成绩文本」的车。
      但权威成绩库 official_safety_scores.json 有 8 款人工核验的一手成绩
      （如蔚来ET5 C-IASI A、特斯拉Model 3 Euro NCAP 五星、极氪007GT Euro NCAP 五星），
      这条链路此前从不读该库——车只要没在候选池文本里写成绩，即便库里有也白采。
      本次中型SUV池 21 款恰好 0 命中，但机制必须接上：以后任何车入池，库里有成绩
      就自动回流，不再靠人记得手动补（与价格回流同一纪律）。

    规则：
      - 只填 safety_basis 为空的车，不覆盖已核验的候选池值
      - car_scores 的 system 归一到标准 basis；score 是权威分，直接对齐
      - 成绩展示 display 从 grade/value/stars 生成，写进 crash
      - 不碰 scores.safety（那是 safety_check.sync_table_scores_to_pool 的职责）
    返回 filled = [(车型, display, basis), ...]
    """
    from modules.contracts.model_names import canon
    from modules.engine import crash_systems as CS

    tp = table_path or os.path.join(HERE, "official_safety_scores.json")
    if not os.path.exists(tp):
        return []
    table = json.load(open(tp, encoding="utf-8"))
    car_scores = table.get("car_scores", {})
    by = {canon(k): (k, v) for k, v in car_scores.items()}
    filled = []
    for c in cars:
        if CS.is_official(c.get("safety_basis")):
            continue
        rec = by.get(canon(c.get("model") or ""))
        if not rec:
            continue
        model_key, r = rec
        basis = CS.normalize(r.get("system"))
        if not basis:
            continue
        # 构造与 parse_score 同构的事实（不参与打分，只回填事实列）
        grade, value, stars = r.get("grade"), r.get("value"), r.get("stars")
        sys_cn = CS.cn_name(basis)
        if value is not None:
            display = f"{sys_cn} {value:g}%"
        elif stars:
            display = f"{sys_cn} {stars}星"
        elif grade and re.match(r"^[GAMP]\+?$", str(grade)):
            display = f"{sys_cn} {grade}"                    # C-IASI 等级 A/G/...
        elif grade and str(grade).startswith("5star"):
            display = f"{sys_cn} 五星（乘员分项达标）"         # Euro NCAP 星级口径
        elif grade:
            display = f"{sys_cn} {grade}"
        else:
            display = sys_cn
        c["safety_basis"] = basis
        if not CS.has_score(c.get("crash")):
            c["crash"] = display
        c["crash_source"] = (f"权威成绩库 official_safety_scores.json 回流"
                             f"（{model_key}：{r.get('source') or sys_cn}）")
        if value is not None:
            c["safety_score_value"] = value
        filled.append((c.get("model"), display, basis))
    return filled


# ---------------------------------------------------------------- C-NCAP 成绩表回流
def _model_key(s) -> str:
    """精确归一：只去格式符，不去掉「纯电/四驱」等限定词，防止跨动力外推。"""
    return re.sub(r"[\s\-_·（）()＋+／/．.、,，]", "", str(s or "")).lower()


def backfill_crash_from_cncap_table(cars, table_path=None):
    """把 cncap_scores.json 的官方综合得分率回流进候选池（v10.5.0）。

    为什么必须有这条（2026-11-21 全量核对发现）：
      cncap_scores.json 已抓到「零跑C11 86.4%」，但候选池 crash/note/evidence 都没写，
      权威库 official_safety_scores.json 也没有 → 页面碰撞成绩列显示「—」。
      这是「数据源有测试、页面不显示」的典型漏回填，不是渲染问题。

    规则：只填空，不覆盖候选池/权威库已有的更权威成绩；不外推到配置或年款。
    """
    from modules.engine import crash_systems as CS

    tp = table_path or os.path.join(HERE, "cncap_scores.json")
    if not os.path.exists(tp):
        return []
    table = json.load(open(tp, encoding="utf-8"))
    scores = table.get("scores") or {}
    by = {_model_key(k): (k, v) for k, v in scores.items()}
    filled = []
    for c in cars:
        if CS.is_official(c.get("safety_basis")):
            continue
        rec = by.get(_model_key(c.get("model") or ""))
        if not rec:
            continue
        key, value = rec
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        display = f"C-NCAP {value:g}%"
        c["safety_basis"] = "cncap"
        if not CS.has_score(c.get("crash")):
            c["crash"] = display
        c["crash_source"] = (f"C-NCAP 官方成绩表 cncap_scores.json 回流"
                             f"（{key}：综合得分率 {value:g}%）")
        c["safety_score_value"] = value
        filled.append((c.get("model"), display, "cncap"))
    return filled


def crash_display_leaks(cars, decoded=None):
    """出榜前门禁：有可核验成绩来源但 crash 字段仍无成绩 → 页面必然漏显。

    只统计「有具体成绩」的来源（一手文字 / C-IASI 新闻稿文字等级 / 权威库 /
    C-NCAP 官方表）；只有「存在测评记录但图标未解码」的车不误报，避免把
    「查到旧测评但无官方文字等级」硬塞进页面。
    """
    from modules.contracts.model_names import canon
    from modules.engine import crash_systems as CS
    from modules.engine import multi_evidence_score as MES

    dec = MES.decoded_by_canon(decoded)
    off_path = os.path.join(HERE, "official_safety_scores.json")
    off = json.load(open(off_path, encoding="utf-8")) if os.path.exists(off_path) else {}
    off_cars = {canon(k) for k in (off.get("car_scores") or {})}
    cn_path = os.path.join(HERE, "cncap_scores.json")
    cn = json.load(open(cn_path, encoding="utf-8")) if os.path.exists(cn_path) else {}
    cn_scores = {_model_key(k) for k in (cn.get("scores") or {})}

    leaks = []
    for c in cars:
        if CS.parse_score(c.get("crash")):
            continue
        model = c.get("model")
        k, mk = canon(model), _model_key(model)
        sources = []
        ev = c.get("evidence") if isinstance(c.get("evidence"), dict) else {}
        ce = str(ev.get("crash_evidence") or "")
        if any(s.get("tier", 0) >= 2 for s in CS.parse_all_scores_from_evidence(ce)):
            sources.append("候选池一手成绩文字")
        if k in dec and any(v.get("grade") for v in (dec[k].get("dims") or {}).values()):
            sources.append("C-IASI 新闻稿文字等级")
        if k in off_cars:
            sources.append("official_safety_scores")
        if mk in cn_scores:
            sources.append("cncap_scores")
        if sources:
            leaks.append({"model": model, "sources": sources})
    return leaks
