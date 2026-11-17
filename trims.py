#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""具体配置阶梯与带内配置推荐（trims.py）——列事实、给建议，但不改榜单。

提出的场景：同一车系不同配置的电池容量、电芯供应商、电压平台可能完全不同
（MG 07 低配清陶 67kWh 半固态、高配宁德 91kWh 三元锂；腾势 N8 加 1 万多
24.36kWh），而榜单只按「一个带内推荐配置」打分，用车系级一个字符串概括电池，
掩盖了买车时真正要做的那次选择：买哪个配置。

**结论：该列，而且这是当前最值得补的一层；但不该把它变成加分项。**

具体失效模式：
1 层级错位。榜单回答「买哪款车」，配置推荐回答「这款车买哪个版本」，
   把后者写进加权分，等于用配置性价比替代整车评价。
2 配置性价比是场景函数，不是单调量。同一笔 +1 万换大电池，
   有无家充、南方北方、通勤长途的结论相反。查证中腾势 N8 这一笔被一篇
   媒体称「六款中单位成本最划算」，另一篇明确写「只为续航加 1 万没必要」；
   MG 07 的 610 / 845 也被不同来源分别推荐给不同人群。没有唯一正确答案。
3 MNAR 同样存在：放出逐配置表的是高曝光车型，缺表就吃亏等于给披露完整度打分。
4 「低配二线电池、高配宁德」这个概括不成立：零跑 C10 逐配置表写的是
   「国轩高科/亿纬动力/中创新航」同配置多供应商混装，选配置也规避不了；
   MG 07 是技术路线分层（半固态 vs 三元锂 + 400V vs 800V），不是供应商等级。
   电池包由整车厂集成，公开数据不足以按电芯品牌拆分长期可靠性。

因此本模块做三件事：
  · 按 trim 记录指导价 / 电池容量 / 电芯供应商 / 技术路线 / 上市时长，
    每条带 url + quote + 核验日期；只分类证据形状（single_named /
    multi_batch / trim_split / new_tech_route / conflict），**不给供应商排等级**。
  · 识别「加价-增配跃迁」：单位加价收益 = ΔkWh/万元 或 ΔCLTCkm/万元 超阈值，
    且目标配置仍在价格带内 → 给「可考虑加价配置」及逐条理由。
  · 按场景给分支建议（家充 / 北方 / 长途），不产出唯一最优配置。

红线：**SCORING_ENABLED=False**。apply() 只写 c['trim_ladder']，返回前对
scores/total/adjust/airbag_delta/price/price_guide/trim/cell_brand 做深比较断言——本库在代码层面不可能改排名。
「评分配置」保持候选池原值，本模块不替换、不改 price_basis、不动闸门。

用法：
    python3 trims.py --validate
    python3 trims.py --todo   --candidates cands.json
    python3 trims.py --report --candidates cands.json --out trims.md
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(HERE, "categories", "trims.json")

SCORING_ENABLED = False          # 红线：永不改分
MAX_DELTA_PRICE = 1.5            # J1：加价小升级上限（万元）
MAX_STEP_PRICE = 3.0             # 跨级换技术路线上限（万元），只展示不作「加一点」建议
MIN_KWH_PER_10K = 10.0           # J2：每万元至少 10kWh
MIN_KM_PER_10K = 100.0           # J2：或每万元至少 100km CLTC

SUPPLIER_CLASS_LABELS = {
    "single_named": "单一供应商（逐配置列明）",
    "multi_batch": "同配置多供应商混装（选配置无法规避）",
    "trim_split": "按配置固定分层（选配置可选择）",
    "new_tech_route": "新技术路线（另记上市时长）",
    "conflict": "两源冲突 / 已记 null",
    "undisclosed": "未披露",
}

TRIM_FIELDS = ("name", "price", "battery_kwh", "battery_type", "cell_supplier",
               "supplier_class", "cltc_km", "charge", "motor_kw", "key_configs",
               "group", "note")


def load_db(path: str = DEFAULT_DB) -> dict:
    if not os.path.exists(path):
        return {"entries": [], "out_of_board_examples": []}
    return json.load(open(path, encoding="utf-8"))


def validate(db: dict) -> list[str]:
    errs: list[str] = []
    if db.get("scoring_enabled") is not False:
        errs.append("scoring_enabled 必须为 false（配置阶梯不得产生分数）")
    seen: set[str] = set()

    def check_entry(e: dict, w: str, need_in_board: bool):
        for k in ("model", "source", "url", "date", "source_type", "quote",
                  "note", "trim_class"):
            if not e.get(k):
                errs.append(f"{w}: 缺 {k}（可核验要求必填）")
        if not str(e.get("url") or "").startswith(("http://", "https://")):
            errs.append(f"{w}: url 不是 http(s) 链接")
        if e.get("model") in seen:
            errs.append(f"{w}: 车型重复 {e.get('model')}")
        seen.add(e.get("model"))
        if e.get("trim_class") not in SUPPLIER_CLASS_LABELS:
            errs.append(f"{w}: trim_class 非法 {e.get('trim_class')}")
        trims = e.get("trims") or []
        if len(trims) < 2:
            errs.append(f"{w}: 阶梯至少 2 个配置")
        names = [t.get("name") for t in trims]
        if len(set(names)) != len(names):
            errs.append(f"{w}: 配置名重复")
        for i, t in enumerate(trims):
            tw = f"{w}.trims[{i}]"
            for f in TRIM_FIELDS:
                if f not in t:
                    errs.append(f"{tw}: 缺字段 {f}（无证据必须显式写 null）")
            if not isinstance(t.get("price"), (int, float)):
                errs.append(f"{tw}: price 必须为数值（指导价，禁用促销价）")
            if t.get("battery_kwh") is not None and not isinstance(
                    t.get("battery_kwh"), (int, float)):
                errs.append(f"{tw}: battery_kwh 必须为数值或 null")
            if not t.get("group"):
                errs.append(f"{tw}: 缺 group（跃迁只在同年款内比较，必须显式标年款）")
            if t.get("supplier_class") not in SUPPLIER_CLASS_LABELS:
                errs.append(f"{tw}: supplier_class 非法 {t.get('supplier_class')}")
            if t.get("supplier_class") == "conflict" and t.get("cell_supplier"):
                errs.append(f"{tw}: 标 conflict 时 cell_supplier 必须记 null")
        if need_in_board:
            if e.get("in_board") is not True:
                errs.append(f"{w}: 在榜条目 in_board 必须为 true")
            if not isinstance(e.get("price_band_max"), (int, float)):
                errs.append(f"{w}: 缺 price_band_max（J3 判带内需要）")
            ref = e.get("scored_trim_ref")
            if not ref or ref not in names:
                errs.append(f"{w}: scored_trim_ref 必须指向 trims 里存在的配置名")
        else:
            if e.get("in_board") is not False:
                errs.append(f"{w}: 带外示例 in_board 必须为 false")
            if e.get("scored_trim_ref") is not None:
                errs.append(f"{w}: 带外示例不应有 scored_trim_ref")

    for i, e in enumerate(db.get("entries", [])):
        check_entry(e, f"entries[{i}]", True)
    for i, e in enumerate(db.get("out_of_board_examples", [])):
        check_entry(e, f"out_of_board_examples[{i}]", False)
    return errs


def months_on_market(entry: dict, as_of: str | None = None) -> float | None:
    """上市时长（月）。只作事实标签，不映射质量判断。"""
    s = entry.get("launch_date")
    if not s:
        return None
    try:
        d0 = _dt.date.fromisoformat(s)
        d1 = _dt.date.fromisoformat(as_of or load_db().get("as_of") or
                                    _dt.date.today().isoformat())
        return round((d1 - d0).days / 30.44, 1)
    except ValueError:
        return None


def value_jumps(entry: dict) -> list[dict]:
    """J1–J4：找「加价小、增配多」的配置跃迁，只产事实不改分。"""
    trims = [t for t in (entry.get("trims") or [])
             if isinstance(t.get("price"), (int, float))]
    band = entry.get("price_band_max")
    out: list[dict] = []
    for a in trims:
        ka, ra = a.get("battery_kwh"), a.get("cltc_km")
        cands = []
        for b in trims:
            # J2b：跨年款换代（同价增配）不是「加价跃迁」，不给购买建议
            if b.get("group") != a.get("group"):
                continue
            dp = float(b["price"]) - float(a["price"])
            if dp <= 0 or dp > MAX_STEP_PRICE:
                continue
            dkwh = (float(b["battery_kwh"]) - float(ka)
                    if isinstance(b.get("battery_kwh"), (int, float))
                    and isinstance(ka, (int, float)) else None)
            dkm = (float(b["cltc_km"]) - float(ra)
                   if isinstance(b.get("cltc_km"), (int, float))
                   and isinstance(ra, (int, float)) else None)
            k_per = round(dkwh / dp, 2) if dkwh is not None else None
            km_per = round(dkm / dp, 1) if dkm is not None else None
            if (k_per is not None and k_per >= MIN_KWH_PER_10K) or \
               (km_per is not None and km_per >= MIN_KM_PER_10K):
                cands.append((b["price"], b, dp, dkwh, dkm, k_per, km_per))
        if not cands:
            continue
        _, b, dp, dkwh, dkm, k_per, km_per = min(cands, key=lambda x: x[0])
        in_band = (band is None) or (float(b["price"]) <= float(band))
        kind = "value_jump" if dp <= MAX_DELTA_PRICE else "platform_step"
        out.append({
            "kind": kind,
            "from": a["name"], "to": b["name"],
            "delta_price": round(dp, 2),
            "delta_kwh": None if dkwh is None else round(dkwh, 2),
            "delta_cltc": None if dkm is None else round(dkm, 0),
            "kwh_per_10k": k_per, "km_per_10k": km_per,
            "from_price": a["price"], "to_price": b["price"],
            "supplier_changed": (a.get("cell_supplier") != b.get("cell_supplier")),
            "platform_changed": (a.get("charge") != b.get("charge")),
            "in_band": in_band,
            # 真正的「建议」由 build_ladder 按 R2/R2b 复核（须从评分配置出发、
            # 必须是加价小升级、且目标比评分配置更贵），此处只是候选标记。
            "actionable": False,
            "extra_configs": [x for x in (b.get("key_configs") or [])
                              if x not in (a.get("key_configs") or [])],
        })
    # 去重（同一 from/to 只留一条），按单位收益降序
    uniq = {(j["from"], j["to"]): j for j in out}
    return sorted(uniq.values(),
                  key=lambda j: -(j["kwh_per_10k"] or j["km_per_10k"] or 0))


def build_ladder(entry: dict, cand: dict | None = None) -> dict:
    jumps = value_jumps(entry)
    scored_ref = entry.get("scored_trim_ref")
    ref_trim = next((t for t in entry["trims"] if t["name"] == scored_ref), None)
    cand_trim = (cand or {}).get("trim") or {}
    cand_price = cand_trim.get("price") if isinstance(cand_trim, dict) else None
    mismatch = (ref_trim is not None and isinstance(cand_price, (int, float))
                and abs(float(cand_price) - float(ref_trim["price"])) > 0.01)

    suppliers = {t.get("cell_supplier") for t in entry["trims"]
                 if t.get("cell_supplier")}
    months = months_on_market(entry)

    # R2 / R2b：建议只能从「评分配置」出发，且必须是加价小升级、目标更贵。
    # 其它支线（例如从 510 Max 出发）的跃迁只进事实表，绝不冒充评分配置的加价理由。
    scored_price = (ref_trim or {}).get("price")
    from_scored = [j for j in jumps if j["from"] == scored_ref]
    for j in jumps:
        j["actionable"] = bool(
            entry.get("in_board") and j["in_band"]
            and j["kind"] == "value_jump" and j["from"] == scored_ref
            and isinstance(scored_price, (int, float))
            and float(j["to_price"]) > float(scored_price))
    upsell = next((j for j in from_scored if j["actionable"]), None)
    step = next((j for j in from_scored
                 if j["kind"] == "platform_step" and j["in_band"]), None)

    rec: dict = {"status": "no_actionable_jump", "suggested_trim": None,
                 "reason": "从评分配置出发没有达到 J2 阈值的带内加价小升级，"
                           "维持候选池评分配置；不另推具体配置、也不推降级。"
                           "阶梯中其它支线的加价差异只在事实表展示，不作建议。"}
    branches = [
        "有家充 / 城市通勤为主 → 通常不必为大电池加价，省下的钱是实的",
        "无家充 / 常跑长途 / 依赖公共快充 → 大电池与更高电压平台的收益明显",
        "北方低温 → 电池技术路线与低温衰减比容量数字更重要，需另行查实测",
    ]
    if upsell:
        pick = upsell
        rel = (f"；相对榜单评分配置 {scored_ref or '—'}"
               f"（{scored_price:g} 万）实际需 "
               f"+{float(pick['to_price']) - float(scored_price):.2f} 万")
        rec = {
            "status": "upsell_suggestion",
            "suggested_trim": pick["to"],
            "from_trim": pick["from"],
            "reason": (f"从评分配置「{pick['from']}」（{float(pick['from_price']):g} 万）"
                       f"+{pick['delta_price']:.2f} 万 → "
                       + (f"+{pick['delta_kwh']:g}kWh" if pick["delta_kwh"] is not None else "")
                       + (f" / +{pick['delta_cltc']:.0f}km CLTC" if pick["delta_cltc"] else "")
                       + f"（每万元 {pick['kwh_per_10k']}kWh）" + rel),
            "jump": pick,
            "scenario_branches": branches,
            "disclaimer": "这是购买建议，不是评分：不改加权分、不改同档组、不改三档、不改准入。",
        }
    elif step:
        # 换整条三电路线（MG 07 的 67kWh 半固态 → 91kWh 三元锂 + 800V）
        # 超出「加一点钱」阈值，只陈述事实与场景，不给「建议买」。
        rec = {
            "status": "platform_step_note",
            "suggested_trim": None,
            "reason": (f"从评分配置「{step['from']}」到「{step['to']}」需 "
                       f"+{step['delta_price']:.2f} 万（超过 J1 的 "
                       f"{MAX_DELTA_PRICE:g} 万阈值），换的是整条三电路线："
                       + (f"+{step['delta_kwh']:g}kWh" if step["delta_kwh"] is not None else "")
                       + (f" / +{step['delta_cltc']:.0f}km CLTC" if step["delta_cltc"] else "")
                       + f"，供应商{'变化' if step['supplier_changed'] else '不变'}、"
                       + f"补能平台{'变化' if step['platform_changed'] else '不变'}。"
                       "这是换车级别的取舍，不是「加一点钱的小升级」，故不给单一建议配置。"),
            "jump": step,
            "scenario_branches": branches + [
                "看重长期保有数据 → 注意新技术路线上市时长，缺证据不等于有缺陷",
            ],
            "disclaimer": "仅事实与场景分支，不产出推荐配置，更不改加权分。",
        }
    elif not entry.get("in_board"):
        rec = {"status": "out_of_board", "suggested_trim": None,
               "reason": "不在四榜价格带，仅作跃迁算法演示，绝不参与名次。"}

    return {
        "status": "verified",
        "model": entry["model"], "in_board": bool(entry.get("in_board")),
        "board": entry.get("board"),
        "price_band_max": entry.get("price_band_max"),
        "source": entry.get("source"), "url": entry.get("url"),
        "date": entry.get("date"), "source_type": entry.get("source_type"),
        "quote": entry.get("quote"), "note": entry.get("note"),
        "trim_class": entry.get("trim_class"),
        "supplier_class_label": SUPPLIER_CLASS_LABELS.get(entry.get("trim_class")),
        "launch_date": entry.get("launch_date"),
        "months_on_market": months,
        "validation_note": entry.get("validation_note"),
        "user_claim_check": entry.get("user_claim_check"),
        "media_disagreement": entry.get("media_disagreement"),
        "scored_trim_candidate": (cand_trim.get("name") if isinstance(cand_trim, dict)
                                  else None),
        "scored_trim_ref": scored_ref,
        "scored_trim_price_candidate": cand_price,
        "scored_trim_price_verified": (ref_trim or {}).get("price"),
        "trim_price_mismatch": bool(mismatch),
        "supplier_set": sorted(suppliers),
        "supplier_split": len(suppliers) > 1 or entry.get("trim_class") == "trim_split",
        "n_trims": len(entry["trims"]),
        "trims": entry["trims"],
        "jumps": jumps,
        "recommendation": rec,
    }


def apply(cands: list[dict], db: dict | None = None) -> tuple[list[dict], list[dict]]:
    """只写 c['trim_ladder']，绝不改分。幂等：重复调用覆盖，不叠加。"""
    if SCORING_ENABLED:
        raise RuntimeError("trims 的红线被破坏：scoring_enabled 必须恒为 False")
    if db is None:
        db = load_db()
    errs = validate(db)
    if errs:
        raise SystemExit("配置阶梯库未通过校验：\n  " + "\n  ".join(errs))

    before = json.dumps([{k: c.get(k) for k in
                          ("scores", "total", "adjust", "airbag_delta", "price",
                           "price_guide", "trim", "cell_brand")}
                         for c in cands], sort_keys=True, ensure_ascii=False)

    by_model = {e["model"]: e for e in db.get("entries", [])}
    matched: list[dict] = []
    for c in cands:
        e = by_model.get(c.get("model"))
        if not e:
            c["trim_ladder"] = {
                "status": "unknown", "in_board": None, "trims": [], "jumps": [],
                "recommendation": {"status": "insufficient_data",
                                   "suggested_trim": None,
                                   "reason": "无可核验逐配置表，不推荐具体配置；"
                                             "不用入门版冒充整车（R4）。"},
            }
            continue
        c["trim_ladder"] = build_ladder(e, c)
        matched.append({"model": c["model"], "n_trims": len(e["trims"]),
                        "jumps": len(c["trim_ladder"]["jumps"])})

    after = json.dumps([{k: c.get(k) for k in
                         ("scores", "total", "adjust", "airbag_delta", "price",
                          "price_guide", "trim", "cell_brand")}
                        for c in cands], sort_keys=True, ensure_ascii=False)
    if before != after:
        raise AssertionError("trims.apply 改动了评分/价格字段——违反 SCORING_ENABLED=False 红线")
    return cands, matched


def coverage(cands: list[dict]) -> dict:
    n = len(cands) or 1
    verified = [c for c in cands
                if (c.get("trim_ladder") or {}).get("status") == "verified"]
    todo = [c["model"] for c in cands
            if (c.get("trim_ladder") or {}).get("status") != "verified"]
    jumps = sum(len((c.get("trim_ladder") or {}).get("jumps") or []) for c in verified)
    upsell = [c["model"] for c in verified
              if (c["trim_ladder"]["recommendation"]["status"]
                  == "upsell_suggestion")]
    actionable = len(upsell)
    mism = [c["model"] for c in verified if c["trim_ladder"]["trim_price_mismatch"]]
    return {"verified": len(verified), "total": len(cands),
            "coverage_pct": round(100.0 * len(verified) / n, 1),
            "todo": todo, "jumps": jumps, "actionable_models": actionable,
            "upsell_models": upsell, "price_mismatch_models": mism}


def _f(x) -> str:
    return "—" if x in (None, "") else str(x).replace("|", "/")


def _trim_table(lad: dict) -> str:
    groups = {t.get("group") for t in lad["trims"]}
    multi = len(groups) > 1
    L = ["| 配置" + ("（年款组）" if multi else "") +
         " | 指导价(万) | 电池 | 电芯供应商 | CLTC | 补能 | 电机 | 关键差异 |",
         "|---|---|---|---|---|---|---|---|"]
    for t in lad["trims"]:
        kwh = t.get("battery_kwh")
        bat = (f"{kwh:g}kWh · {_f(t.get('battery_type'))}"
               if isinstance(kwh, (int, float)) else "—")
        kc = "、".join(t.get("key_configs") or []) or (t.get("note") or "—")
        nm = _f(t.get("name")) + (f"<br><sub>{_f(t.get('group'))}</sub>" if multi else "")
        L.append(f"| {nm} | {t['price']:g} | {bat} "
                 f"| {_f(t.get('cell_supplier'))} | {_f(t.get('cltc_km'))} "
                 f"| {_f(t.get('charge'))} | {_f(t.get('motor_kw'))}kW | {_f(kc)} |")
    return "\n".join(L)


def _jump_table(lad: dict) -> str:
    js = lad.get("jumps") or []
    if not js:
        return "无达到 J2 阈值的加价-增配跃迁。"
    L = ["| 类型 | 从 → 到 | Δ价(万) | Δ电(kWh) | Δ续航(km) | 每万元 kWh | 供应商变化 | 带内 |",
         "|---|---|---|---|---|---|---|---|"]
    for j in js:
        kind = "加价小升级" if j["kind"] == "value_jump" else "换技术路线"
        L.append(f"| {kind} | {j['from']} → {j['to']} | +{j['delta_price']:.2f} "
                 f"| {_f(j['delta_kwh'])} | {_f(j['delta_cltc'])} "
                 f"| {_f(j['kwh_per_10k'])} "
                 f"| {'是' if j['supplier_changed'] else '否'} "
                 f"| {'✅' if j['in_band'] else '❌ 带外'} |")
    return "\n".join(L) + "\n\n> 「换技术路线」超出 J1 加价阈值，只陈述事实与场景，不给单一建议配置。"


def summary_md(cands: list[dict], db: dict | None = None) -> str:
    """主报告用的精简节（主报告已很长，这里只放结论 + 汇总表）。"""
    if db is None:
        db = load_db()
    cov = coverage(cands)
    pol = db.get("policy") or {}
    L = ["# 具体配置推荐与配置阶梯（不加权）", "",
         "> **红线：`trims.py` 的 `SCORING_ENABLED=False`**，apply 前后对 "
         "`scores/total/adjust/price/trim` 做深比较断言；本库在代码层面不可能改排名。"
         "「评分配置」保持候选池原值，不替换、不改 `price_basis`、不动闸门。", "",
         "### 为什么「该列配置」但「不该给配置加分」", ""]
    for i, w in enumerate(pol.get("why_fact_not_score", []), 1):
        L.append(f"{i}. {w}")
    L += ["", "### 电芯供应商：只分类证据形状，不排等级", "",
          "> " + (pol.get("supplier_policy") or {}).get("no_tier_ranking", ""), ""]
    for r in (pol.get("supplier_policy") or {}).get("reason", []):
        L.append(f"- {r}")
    L += ["", "### 跃迁规则（可审计）", ""]
    for r in pol.get("value_jump_rules", []):
        L.append(f"- {r}")
    L += ["", "### 本期结果", "",
          f"逐配置核验 **{cov['verified']}/{cov['total']}** 款"
          f"（{cov['coverage_pct']:.0f}%），共识别 **{cov['jumps']}** 条跃迁，"
          f"其中 **{cov['actionable_models']}** 款存在带内加价建议："
          f"{'、'.join(cov['upsell_models']) or '无'}。", "",
          "| 车型 | 评分配置 | 阶梯数 | 证据形状 | 上市(月) | 带内跃迁 | 建议配置 |",
          "|---|---|---|---|---|---|---|"]
    for c in cands:
        lad = c.get("trim_ladder") or {}
        if lad.get("status") != "verified":
            continue
        rec = lad["recommendation"]
        nj = sum(1 for j in lad["jumps"] if j["actionable"])
        L.append(f"| {c['model']} | {_f(lad['scored_trim_candidate'])} "
                 f"| {lad['n_trims']} | {lad['supplier_class_label']} "
                 f"| {_f(lad['months_on_market'])} | {nj} "
                 f"| {_f(rec.get('suggested_trim'))} |")
    platform = [(c["model"], j) for c in cands
                for j in ((c.get("trim_ladder") or {}).get("jumps") or [])
                if j["kind"] == "platform_step"]
    if platform:
        L += ["", f"另有 **{len(platform)}** 条「换整条三电路线」的步骤（超出 "
              f"{MAX_DELTA_PRICE:g} 万加价阈值）："
              + "；".join(f"{m} {j['from']} → {j['to']} 需 +{j['delta_price']:.2f} 万/"
                         f"+{j['delta_kwh']:g}kWh" for m, j in platform)
              + "。这类属于换车级取舍，**只陈述事实与场景分支，不给单一建议配置、不加分**。"]
    if cov["price_mismatch_models"]:
        L += ["", "> ⚠ **评分配置口径冲突**："
              + "、".join(cov["price_mismatch_models"])
              + " 的候选池 `trim.price` 与逐配置表对不上（多为配置名与价格错配）。"
              "本模块不替候选池改价（会动闸门），只披露，下期核对后再修。"]
    L += ["", "> 想看逐配置表、带外示例（腾势 N8 的 +1 万 / +24.36kWh 与媒体分歧）"
          "与来源链接：`python3 trims.py --report --candidates <候选池> --out trims.md`，"
          "或本期 `out/trims.md`。配置选择按场景分支，本报告不给唯一最优配置。", ""]
    return "\n".join(L)


def report(cands: list[dict], db: dict | None = None) -> str:
    if db is None:
        db = load_db()
    cov = coverage(cands)
    L = [summary_md(cands, db), "---", "",
         "## 逐车配置阶梯与购买分支（只展示，不改分）", ""]
    for c in cands:
        lad = c.get("trim_ladder") or {}
        if lad.get("status") != "verified":
            continue
        rec = lad["recommendation"]
        L += [f"### {c['model']} · {_f(lad.get('board'))}", "",
              f"- 评分配置（榜单用的那一个）：**{_f(lad['scored_trim_candidate'])}** "
              f"／候选池记 {_f(lad['scored_trim_price_candidate'])} 万，"
              f"逐配置表核到 {_f(lad['scored_trim_price_verified'])} 万"
              + ("　⚠ **两者不一致，见下方冲突说明**" if lad["trim_price_mismatch"] else ""),
              f"- 证据形状：{lad['supplier_class_label']}；"
              f"供应商集合：{'、'.join(lad['supplier_set']) or '未披露 / 冲突'}；"
              f"上市 {_f(lad['launch_date'])}（{_f(lad['months_on_market'])} 个月）", ""]
        if lad.get("validation_note"):
            L += ["> **待市场验证（事实，不是差评）**：" + lad["validation_note"], ""]
        if lad.get("user_claim_check"):
            L += ["> **线索核验**：" + lad["user_claim_check"], ""]
        if lad.get("media_disagreement"):
            L += ["> **媒体分歧（说明为什么不能进默认权重）**："
                  + lad["media_disagreement"], ""]
        L += [_trim_table(lad), "", "**加价-增配跃迁**", "", _jump_table(lad), "",
              "**带内建议**", "",
              f"- 状态：`{rec['status']}`　建议配置：**{_f(rec.get('suggested_trim'))}**",
              f"- 理由：{rec.get('reason')}"]
        if rec.get("disclaimer"):
            L.append(f"- {rec['disclaimer']}")
        for s in rec.get("scenario_branches") or []:
            L.append(f"- 场景分支：{s}")
        if lad["trim_price_mismatch"]:
            L += ["", "> ⚠ **口径冲突（本模块不改分只披露）**：候选池 `trim.name` "
                  f"=「{_f(lad['scored_trim_candidate'])}」、`trim.price` = "
                  f"{_f(lad['scored_trim_price_candidate'])} 万，但逐配置表中该名称对应 "
                  f"{_f(lad['scored_trim_price_verified'])} 万；"
                  f"{_f(lad['scored_trim_price_candidate'])} 万在表内对应的是另一配置。"
                  "价格会进闸门，所以本模块不擅自替换，留给下期人工核对。"]
        L += ["", f"- 来源：[{_f(lad.get('source_type'))} · {_f(lad.get('date'))}]"
              f"({_f(lad.get('url'))})　{_f(lad.get('source'))}",
              f"- 原文摘录：{_f(lad.get('quote'))}",
              f"- 备注：{_f(lad.get('note'))}", ""]

    oob = db.get("out_of_board_examples") or []
    if oob:
        L += ["---", "", "## 带外示例（不参与四榜名次）", ""]
        for e in oob:
            lad = build_ladder(e)
            L += [f"### {e['model']} · {_f(e.get('board'))}", "",
                  "> " + _f(e.get("note")), ""]
            if e.get("user_claim_check"):
                L += ["> **线索核验**：" + e["user_claim_check"], ""]
            if e.get("media_disagreement"):
                L += ["> **媒体分歧**：" + e["media_disagreement"], ""]
            L += [_trim_table(lad), "", "**加价-增配跃迁**", "",
                  _jump_table(lad), "",
                  f"- 来源：[{_f(e.get('source_type'))} · {_f(e.get('date'))}]"
                  f"({_f(e.get('url'))})", ""]

    if cov["todo"]:
        L += ["---", "", "## 待补清单（无逐配置表，不推荐具体配置）", "",
              f"共 {len(cov['todo'])} 款：{'、'.join(cov['todo'])}。", "",
              "> 未采集 ≠ 配置没差异。多为新上市 / 未上市车型，官网逐配置页尚未放出，"
              "或存在年款与动力混杂、两源冲突而主动不入库。"
              "`python3 trims.py --todo --candidates <候选池>` 可重列。", ""]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--candidates", help="候选池 json")
    ap.add_argument("--todo", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()

    db = load_db(a.db)
    errs = validate(db)
    if a.validate or errs:
        print(f"在榜 {len(db.get('entries', []))} 款 / 带外示例 "
              f"{len(db.get('out_of_board_examples', []))} 款；"
              + ("校验通过" if not errs else "校验失败：\n  " + "\n  ".join(errs)))
        if errs:
            sys.exit(1)
        if not a.candidates:
            return
    if not a.candidates:
        return
    d = json.load(open(a.candidates, encoding="utf-8"))
    cands = d.get("candidates", d)
    cands, _ = apply(cands, db)
    if a.todo:
        cov = coverage(cands)
        print(f"逐配置核验 {cov['verified']}/{cov['total']}"
              f"（{cov['coverage_pct']:.0f}%）；跃迁 {cov['jumps']} 条；"
              f"带内建议 {', '.join(cov['upsell_models']) or '无'}")
        print(f"待补 {len(cov['todo'])} 款：{'、'.join(cov['todo'])}")
        if cov["price_mismatch_models"]:
            print("评分配置价格冲突：" + "、".join(cov["price_mismatch_models"]))
        return
    txt = report(cands, db) if a.report else summary_md(cands, db)
    print(txt)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(txt + "\n")
        print(f"\n[ok] {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
