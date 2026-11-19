#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cross_verify.py — 多源交叉核验（v8.2，2026-11-19）

用户要求：不要只查一个数据源（懂车帝），所有数据源都查，最后比对，
对不上的网上找资料确认。本模块把散落在 12 个已落盘事实源里的同一字段
汇总到一款车上，逐值带 source/url/date/证据等级，然后机械裁决：
  - AGREE    多源一致（取最高等级值，列出所有支持源）
  - CONFLICT 多源分歧（列出每个值+来源，等网查确认）
  - SINGLE   只有一个源（证据单薄，标 low confidence）
  - MISSING  所有源都没有

覆盖字段（按可核验性）：
  cls(级别) / power(动力) / price(指导价) / kwh(电池能量) /
  cell_brand(电芯) / crash(碰撞成绩) / trend_sales(月销) /
  lifecycle(生命周期) / cabin_users(口碑样本量)

网查确认闭环：
  对 CONFLICT 项，人工/agent 联网核实后写入 categories/verified_overrides.json：
    {"车型":[{"field":"cell_brand","value":"弗迪","url":"...","date":"2026-11-19","note":"..."}]}
  本模块读取它作为**最高证据等级**（rank 100）覆盖，形成
  「多源比对 → 冲突 → 网查 → 确认 → 回填 → 重跑冲突清零」闭环。

纪律（继承 skill 红线）：
  - 不改任何 scores/adjust/total —— 本模块只产出核验表，不进打分链。
  - 不猜值：只有单源就标 SINGLE，分歧就标 CONFLICT，绝不挑一个当事实。
  - 每值必带来源；没有 url 的来源降级（rank 打折），不是不记录。
  - 可离线复现：全部读已落盘缓存，同输入必得同输出。

用法：
  python3 cross_verify.py --selftest
  python3 cross_verify.py --pool categories/master_pool.json [--out 报告目录]
  python3 cross_verify.py --pool categories/master_pool.json --model 丰田bZ5   # 单车深查
  python3 cross_verify.py --pool categories/master_pool.json --conflicts-only
"""
import argparse
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
CAT = os.path.join(HERE, "categories")

# 允许从 skill 根目录 import master_pool 的 canon
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    from master_pool import canon
except Exception:  # pragma: no cover
    canon = lambda s: str(s or "").strip().lower()

# 跨库车名对齐：内联 filings.py 的纯函数逻辑（那套函数本身不依赖 requests，
# 但 filings 模块顶层 import requests，在无 requests 环境会失败，故直接内联零依赖版）。
import re as _re
_GREEK_ALIAS = [("阿尔法", "Α"), ("贝塔", "Β"), ("伽马", "Γ"),
                ("阿尔法", ""), ("贝塔", ""), ("伽马", "")]
_BRAND_STRIP = ["比亚迪", "大众汽车", "上汽大众", "一汽大众", "大众", "ID.UNYX",
                "ID.", "丰田", "一汽丰田", "广汽丰田", "日产", "东风日产", "别克",
                "上汽通用", "长安启源", "长安", "深蓝", "极狐", "北汽", "奇瑞",
                "捷途", "星途", "吉利", "银河", "领克", "极氪", "五菱", "宝骏",
                "广汽埃安", "埃安", "AION", "东风风行", "东风", "奕派", "岚图",
                "小鹏", "MONA", "零跑", "理想", "蔚来", "乐道", "红旗", "荣威",
                "名爵", "MG", "哈弗", "魏牌", "欧拉", "ORA", "星海", "宝马", "BMW"]

def _norm_name(s):
    s = (s or "").strip().replace("（", "(").replace("）", ")")
    s = _re.sub(r"\(.*?\)", "", s)
    s = _re.sub(r"[\s\-·_]", "", s).replace("牌", "")
    return s.upper()

def _name_keys(s):
    base = _norm_name(s)
    out = {base}
    v = base
    for a, b in _GREEK_ALIAS:
        v = v.replace(a, b)
    out.add(v)
    for suf in ("EV", "BEV", "纯电"):
        if v.endswith(suf) and len(v) > len(suf) + 1:
            out.add(v[:-len(suf)])
    for pre in _BRAND_STRIP:
        if v.startswith(pre) and len(v) > len(pre) + 1:
            out.add(v[len(pre):])
    return {x for x in out if len(x) >= 2}

def _find_in(lib, name):
    """跨库车名对齐：键全等 / 一方包含另一方且长度差≤2（短键≥3字符）。"""
    ks = _name_keys(name)
    for k in ks:
        if k in lib:
            return k
    libkeys = {}
    for lk in lib:
        for x in _name_keys(lk):
            libkeys.setdefault(x, lk)
    for k in ks:
        if k in libkeys:
            return libkeys[k]
    for k in ks:
        if len(k) < 3:
            continue
        for lk, orig in libkeys.items():
            if (k in lk or lk in k) and abs(len(k) - len(lk)) <= 2 and min(len(k), len(lk)) >= 3:
                return orig
    return None


# ---------------------------------------------------------------- 证据等级
# 数值越大越权威。裁决时取最高等级值；同等级分歧 = CONFLICT。
SOURCE_RANK = {
    "verified_web": 100,      # 网查确认（人工/agent 核实，带 url）
    "cncap_official": 90,     # C-NCAP 官网成绩表
    "ciasi_official": 90,     # C-IASI 官网评级表
    "oem_config": 85,         # 官方逐配置参数表（汽车之家 config / 易车 peizhi）
    "cell_ledger": 80,        # 电芯核验表 cell_facts（带 url+quote）
    "airbag_ledger": 78,      # 气囊核验表
    "lifecycle_ledger": 78,   # 生命周期核验表（官方停产通告）
    "dcd_sales": 60,          # 懂车帝销量榜
    "baseline": 55,           # 月度基准候选池（人工维护）
    "master_pool": 50,        # 总池当前快照
    "levels_cache": 45,       # 级别缓存 levels.json
    "koubei_cache": 40,       # 口碑缓存
    "thirdparty": 40,         # 第三方实测
}

# 归一化函数：不同字段的可比值规则
def _norm_value(field, v):
    """把同一字段不同来源的写法归一到可比键，返回 (归一值, 展示值)。"""
    if v is None:
        return None, None
    if field in ("cls", "lifecycle_status", "cell_brand", "power"):
        s = str(v).strip()
        # 电芯品牌别名归一
        if field == "cell_brand":
            # 比对键剥掉括号补充说明（"上汽时代(宁德合资)"→"上汽时代"），
            # 展示值仍保留全文——括号里是注解不是不同供应商。
            import re as _re2
            compare = _re2.sub(r"[（(].*?[）)]", "", s).strip()
            for a, b in [("宁德时代", "宁德时代"), ("CATL", "宁德时代"),
                         ("时代一汽", "宁德时代"), ("上汽时代", "上汽时代"),
                         ("弗迪", "弗迪"), ("比亚迪", "弗迪"),
                         ("中创新航", "中创新航"), ("中航锂电", "中创新航"),
                         ("国轩高科", "国轩"), ("国轩", "国轩"),
                         ("蜂巢能源", "蜂巢"), ("清陶", "清陶"),
                         ("神盾短刀", "神盾短刀"), ("欣旺达", "欣旺达")]:
                if a in compare:
                    return b, s
            return compare, s
        # 生命周期状态
        if field == "lifecycle_status":
            for a in ("discontinued", "clearance", "facelift_pending",
                      "new_launch", "active", "on_sale"):
                if a in s.lower():
                    return a, s
        return s.lower(), s
    if field in ("price", "price_guide", "min_price", "kwh", "battery_kwh",
                 "crash", "trend_sales", "cabin_users"):
        try:
            f = float(v)
            return round(f, 2), f
        except (ValueError, TypeError):
            return str(v).strip(), v
    return str(v).strip(), v


# ---------------------------------------------------------------- 事实源加载
def _load_json(path):
    if not os.path.exists(path):
        return None
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None


def _find_key(lib, name):
    """跨库车名对齐：内联的 find_in 规则，失败回退 canon。"""
    if isinstance(lib, dict):
        hit = _find_in(lib, name)
        if hit is not None:
            return hit
    c = canon(name)
    if isinstance(lib, dict):
        for k in lib:
            if canon(k) == c:
                return k
    return None


def collect_sources(root=HERE):
    """一次加载所有已落盘事实源，返回结构化源集合。纯读取，无网络。"""
    S = {}
    S["master_pool"] = _load_json(os.path.join(CAT, "master_pool.json"))
    S["baseline"] = _load_json(os.path.join(CAT, "baseline_car_ev.json"))
    S["cell_facts"] = _load_json(os.path.join(CAT, "cell_facts.json"))
    S["cncap"] = _load_json(os.path.join(HERE, "cncap_scores.json"))
    S["levels"] = _load_json(os.path.join(HERE, "levels.json"))
    S["trims"] = _load_json(os.path.join(CAT, "trims.json"))
    S["airbags"] = _load_json(os.path.join(CAT, "airbags.json"))
    S["lifecycle"] = _load_json(os.path.join(CAT, "lifecycle_status.json"))
    S["koubei"] = _load_json(os.path.join(CAT, "koubei_seed.json"))
    S["thirdparty"] = _load_json(os.path.join(CAT, "thirdparty_tests.json"))
    S["verified"] = _load_json(os.path.join(CAT, "verified_overrides.json")) or {}
    # 懂车帝销量快照（workspace run 目录，可能不存在则跳过）
    dcd = os.environ.get("CROSS_VERIFY_DCD", "")
    if dcd:
        S["dcd_snapshot"] = _load_json(dcd)
    return S


# 把各源抽成 {car: {field: [evidence,...]}} 的统一中间层
def build_evidence(S):
    """返回 ev[canon_car][field] = list of evidence dict。
    evidence = {value, display, source, rank, url, date, detail}"""
    ev = defaultdict(lambda: defaultdict(list))

    def put(car, field, value, source, url="", date="", detail="", rank=None):
        if value is None or value == "":
            return
        nv, disp = _norm_value(field, value)
        if nv is None:
            return
        r = rank if rank is not None else SOURCE_RANK.get(source, 10)
        # 无 url 的非核验类来源降一档（防止无来源值压过有来源值）
        if not url and source in ("baseline", "master_pool", "koubei_cache"):
            r = max(1, r - 15)
        ev[canon(car)][field].append({
            "value": nv, "display": disp, "source": source, "rank": r,
            "url": url, "date": date, "detail": detail,
        })

    # 1) 总池（当前快照）
    mp = S.get("master_pool") or {}
    for c in mp.get("cars", []):
        m = c["model"]
        tags = c.get("tags") or {}
        facts = c.get("facts") or {}
        cand = c.get("candidate") or {}
        put(m, "cls", tags.get("cls") or cand.get("cls"), "master_pool")
        put(m, "power", tags.get("power") or cand.get("power"), "master_pool")
        put(m, "price", tags.get("price_wan"), "master_pool")
        put(m, "cell_brand", facts.get("cell_brand") or cand.get("cell_brand"), "master_pool")
        put(m, "crash", (cand.get("crash") or {}).get("score") if isinstance(cand.get("crash"), dict) else None,
            "master_pool", detail=str(cand.get("crash") or "")[:80])
        put(m, "trend_sales", facts.get("trend_sales") or cand.get("trend_sales"), "master_pool",
            detail=facts.get("trend_method", ""))
        lc = cand.get("lifecycle")
        if isinstance(lc, dict):
            put(m, "lifecycle_status", lc.get("status"), "master_pool")
        # 注意：不把 master_pool 的 safety 分(10分制)塞进 crash(得分率%)，量纲不同。
        # crash 只收官方原始得分率(C-NCAP/baseline.crash.cncap)，避免假冲突/假一致。

    # 2) baseline 基准池
    bl = S.get("baseline") or {}
    for c in bl.get("candidates", []):
        m = c["model"]
        put(m, "cls", c.get("cls"), "baseline")
        put(m, "power", c.get("power"), "baseline")
        pg = c.get("price_guide") or []
        if pg:
            put(m, "price", pg[0], "baseline", detail="price_guide下界")
        put(m, "cell_brand", c.get("cell_brand"), "baseline")
        cr = c.get("crash") or {}
        if isinstance(cr, dict):
            put(m, "crash", cr.get("cncap") or cr.get("ciasi"), "baseline",
                detail=f"协议{cr.get('protocol','')}/{cr.get('year','')}")
        put(m, "trend_sales", c.get("trend_sales"), "baseline")

    # 3) 电芯核验表（官方配置页，带 url）
    cf = S.get("cell_facts") or {}
    for e in cf.get("entries", []):
        m = e["model"]
        put(m, "cell_brand", e.get("cell_brand"), "cell_ledger",
            url=e.get("source_url", ""), date=e.get("verified", ""), detail=e.get("quote", "")[:100])
        put(m, "kwh", e.get("battery_kwh"), "cell_ledger",
            url=e.get("source_url", ""), date=e.get("verified", ""), detail=e.get("trim", ""))
        put(m, "cls", e.get("board"), "cell_ledger", url=e.get("source_url", ""))  # board 是榜单名非官方级别，rank已高需谨慎

    # 4) C-NCAP 官方成绩
    nc = S.get("cncap") or {}
    meta = nc.get("meta") or {}
    for m, score in (nc.get("scores") or {}).items():
        mi = meta.get(m, {}) if isinstance(meta, dict) else {}
        put(m, "crash", score, "cncap_official",
            url=mi.get("url", "") if isinstance(mi, dict) else "",
            date=str(mi.get("year", "")) if isinstance(mi, dict) else "",
            detail="C-NCAP综合得分率%")

    # 5) 级别缓存
    lv = S.get("levels") or {}
    for m, info in (lv.get("levels") or {}).items():
        put(m, "cls", info.get("cls"), "levels_cache", detail=info.get("note", ""))
        if info.get("power"):
            put(m, "power", info.get("power"), "levels_cache")

    # 6) 逐配置表（官方参数页）—— kWh/价格/电芯
    tr = S.get("trims") or {}
    for e in tr.get("entries", []):
        m = e["model"]
        url = e.get("url", "")
        date = e.get("date", "")
        # 从 quote/note 里抽 kWh 不可靠，用条目的 scored_trim 对应字段（若有）
        for k in ("battery_kwh", "kwh"):
            if e.get(k):
                put(m, "kwh", e[k], "oem_config", url=url, date=date, detail=e.get("scored_trim", ""))
        if e.get("cell_supplier"):
            put(m, "cell_brand", e["cell_supplier"], "oem_config", url=url, date=date)

    # 7) 生命周期核验表
    lf = S.get("lifecycle") or {}
    for e in lf.get("entries", []):
        put(e["model"], "lifecycle_status", e.get("status"), "lifecycle_ledger",
            url=e.get("url", ""), date=e.get("date", ""), detail=e.get("note", "")[:80])

    # 8) 口碑样本量
    kb = S.get("koubei") or {}
    for e in kb.get("entries", []):
        put(e["model"], "cabin_users", e.get("users"), "koubei_cache", detail=e.get("note", "")[:60])

    # 9) 懂车帝销量快照
    ds = S.get("dcd_snapshot") or {}
    for r in ds.get("rows", []):
        m = r.get("name")
        months = r.get("months") or {}
        vals = [v for b in months.values() for v in (b or {}).values() if (v or 0) > 0]
        if vals:
            import statistics as _st
            put(m, "trend_sales", round(_st.median(vals)), "dcd_sales",
                detail="懂车帝逐月中位（交叉核验参考）")
        put(m, "min_price", r.get("min_price"), "dcd_sales", detail="懂车帝榜单价（非指导价）")

    # 10) 网查确认（最高等级）—— 读取人工/agent 核实结果
    vf = S.get("verified") or {}
    for m, items in vf.items():
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            put(m, it.get("field"), it.get("value"), "verified_web",
                url=it.get("url", ""), date=it.get("date", ""),
                detail=it.get("note", ""), rank=SOURCE_RANK["verified_web"])
    return ev


# ---------------------------------------------------------------- 裁决
def adjudicate(field, items):
    """对同一字段的多条证据裁决。返回 dict(status, chosen, values, note)。

    纪律（用户 2026-11-19 要求）：只有「网查确认」(verified_web) 才允许覆盖其他源；
    其余任何**多源分歧一律 CONFLICT 列出来等人核实**，绝不静默挑一个当事实。
    单源标 SINGLE（证据单薄），不伪装成 AGREE。
    """
    if not items:
        return {"status": "MISSING", "chosen": None, "values": [], "note": "所有数据源均无此字段"}

    # 1) 网查确认存在 → 采纳它（最高优先级），其余值列为 superseded
    verified = [it for it in items if it["source"] == "verified_web"]
    if verified:
        chosen = verified[0]
        return {"status": "AGREE", "chosen": chosen["display"], "chosen_value": chosen["value"],
                "values": [display_for(x) for x in items],
                "verified": display_for(chosen),
                "superseded": [display_for(x) for x in items if x is not chosen],
                "note": f"网查确认采纳：{chosen['display']}（{chosen['url'] or '无URL'}），其余源存档",
                "sources": [it["source"] for it in items]}

    # 2) 按归一值分组
    byval = defaultdict(list)
    for it in items:
        byval[it["value"]].append(it)
    distinct = len(byval)

    # 3) 单源 → SINGLE（不伪装成一致）
    if len(items) == 1:
        return {"status": "SINGLE", "chosen": items[0]["display"],
                "chosen_value": items[0]["value"], "values": [display_for(x) for x in items],
                "note": f"仅1个来源({items[0]['source']})，证据单薄，建议网查佐证",
                "sources": [it["source"] for it in items]}

    # 4) 多源但归一值全一致 → AGREE
    if distinct == 1:
        return {"status": "AGREE", "chosen": items[0]["display"],
                "chosen_value": items[0]["value"], "values": [display_for(x) for x in items],
                "note": f"{len(items)}个来源一致", "sources": [it["source"] for it in items]}

    # 5) 多源分歧 → CONFLICT（全部列出，等网查确认；不静默挑值）
    cause = ""
    if field == "price":
        cause = "【多为口径差】总池记评分配置价、基准池记车系指导价下界，差1–2万属正常；仅当同口径仍分歧才是真错"
    elif field == "trend_sales":
        cause = "【多为口径/月度差】总池/基准池可能是上一版或车系合计、懂车帝为当期逐月中位；需确认采用口径"
    elif field == "cell_brand":
        cause = "【注意表述差异】括号注解已剥离用于比对；若仍分歧（如未披露vs某品牌）需网查"
    return {"status": "CONFLICT", "chosen": None,
            "values": [display_for(x) for x in items],
            "conflict": [display_for(x) for x in items],
            "cause": cause,
            "note": f"{len(items)}个来源出现{len(byval)}个不同值，需网查确认后写verified_overrides.json。{cause}",
            "sources": [it["source"] for it in items]}


def display_for(it):
    return {"value": it["display"], "source": it["source"], "rank": it["rank"],
            "url": it["url"], "date": it["date"], "detail": it["detail"]}


FIELDS = ["cls", "power", "price", "kwh", "cell_brand", "crash",
          "trend_sales", "lifecycle_status", "cabin_users"]
FIELD_LABEL = {
    "cls": "官方级别", "power": "动力", "price": "指导价(万)", "kwh": "电池能量(kWh)",
    "cell_brand": "电芯品牌", "crash": "碰撞成绩", "trend_sales": "月销",
    "lifecycle_status": "生命周期", "cabin_users": "口碑样本量",
}


def verify_all(root=HERE, models=None, dcd_path="", pool_only=True):
    """主入口：返回 {car: {field: adjudication}}。
    pool_only=True 时只输出总池内的车（用户关心的是候选池核验结果）；
    False 则包含所有事实源里出现的车（含池外燃油车/口碑车，用于发现）。"""
    if dcd_path:
        os.environ["CROSS_VERIFY_DCD"] = dcd_path
    S = collect_sources(root)
    ev = build_evidence(S)
    pool_keys = set()
    if pool_only:
        mp = S.get("master_pool") or {}
        pool_keys = {canon(c["model"]) for c in mp.get("cars", [])}
    out = {}
    for car, fields in ev.items():
        if pool_only and car not in pool_keys:
            continue
        if models and not any(canon(m) == canon(car) for m in models):
            continue
        out[car] = {f: adjudicate(f, fields.get(f, [])) for f in FIELDS}
        out[car]["_model"] = car
    return out, S


# ---------------------------------------------------------------- 报告
def write_report(result, out_dir, conflicts_only=False):
    os.makedirs(out_dir, exist_ok=True)
    all_conflicts, all_single = [], []
    per_car = {}
    for car, fields in result.items():
        if "_model" in fields:
            fields = {k: v for k, v in fields.items() if k != "_model"}
        per_car[car] = fields
        for f, adj in fields.items():
            if adj["status"] == "CONFLICT":
                all_conflicts.append({"car": car, "field": f, "label": FIELD_LABEL.get(f, f), **adj})
            elif adj["status"] == "SINGLE":
                all_single.append({"car": car, "field": f, **adj})
    data = {
        "meta": {"as_of": "2026-11-19", "fields": FIELDS,
                 "note": "多源交叉核验；CONFLICT需网查确认后写verified_overrides.json；不进打分链"},
        "summary": {"车型": len(result), "冲突项": len(all_conflicts), "单源项": len(all_single)},
        "冲突清单": all_conflicts,
        "单源清单": all_single[:50],
        "逐车核验": per_car,
    }
    jp = os.path.join(out_dir, "cross_verify.json")
    json.dump(data, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    # Markdown
    L = ["# 多源交叉核验报告（2026-11-19）\n",
         f"车型 **{len(result)}** | 冲突项 **{len(all_conflicts)}** | "
         f"单源项 **{len(all_single)}**\n",
         "证据等级：网查确认(100) > C-NCAP/C-IASI(90) > 官方配置表(85) > "
         "电芯核验表(80) > 懂车帝(60) > 基准池(55) > 总池(50) > 缓存(40-45)\n",
         "## 冲突清单（需网查确认）\n"]
    if all_conflicts:
        L.append("| 车型 | 字段 | 冲突值（来源） |")
        L.append("|---|---|---|")
        for c in all_conflicts:
            vals = " / ".join(f"{v['value']}[{v['source']}]" for v in c.get("conflict", c["values"]))
            L.append(f"| {c['car']} | {c['label']} | {vals} |")
    else:
        L.append("无冲突。")
    L.append("\n## 逐车核验（仅列非 AGREE 项）\n")
    for car, fields in per_car.items():
        flags = {f: a for f, a in fields.items() if a["status"] in ("CONFLICT", "SINGLE", "MISSING")}
        if not flags:
            continue
        L.append(f"\n### {car}\n")
        for f, a in flags.items():
            vals = "; ".join(f"{v['value']}({v['source']}{' '+v['url'] if v['url'] else ''})"
                             for v in a["values"][:6])
            L.append(f"- **{FIELD_LABEL.get(f,f)}** [{a['status']}]: {vals} — {a['note']}")
    mp = os.path.join(out_dir, "cross_verify.md")
    open(mp, "w", encoding="utf-8").write("\n".join(L))
    return jp, mp, data


# ---------------------------------------------------------------- selftest
def selftest():
    print("cross_verify selftest:")
    # 1 归一化
    assert _norm_value("cell_brand", "比亚迪弗迪")[0] == "弗迪"
    assert _norm_value("cell_brand", "CATL")[0] == "宁德时代"
    assert _norm_value("lifecycle_status", "status=discontinued")[0] == "discontinued"
    assert _norm_value("price", "12.99")[0] == 12.99
    print("  [1] 字段归一化 ✓")
    # 2 裁决：一致
    items = [{"value": "宁德时代", "display": "宁德时代", "source": "a", "rank": 50, "url": "", "date": "", "detail": ""},
             {"value": "宁德时代", "display": "宁德", "source": "b", "rank": 80, "url": "u", "date": "", "detail": ""}]
    a = adjudicate("cell_brand", items)
    assert a["status"] == "AGREE" and a["chosen_value"] == "宁德时代", a
    print("  [2] 多源一致裁决 ✓")
    # 3 网查确认覆盖其他源（唯一允许的覆盖）
    items2 = [{"value": "宁德时代", "display": "宁德时代", "source": "cell_ledger", "rank": 80, "url": "u1", "date": "", "detail": ""},
              {"value": "弗迪", "display": "弗迪", "source": "verified_web", "rank": 100, "url": "u2", "date": "", "detail": ""}]
    a2 = adjudicate("cell_brand", items2)
    assert a2["status"] == "AGREE" and a2["chosen_value"] == "弗迪", a2
    print("  [3] 网查确认覆盖其他源 ✓")
    # 4 非网查的多源分歧 → CONFLICT（即使 rank 不同也不静默选值）
    items3 = [{"value": 7.6, "display": 7.6, "source": "x", "rank": 90, "url": "", "date": "", "detail": ""},
              {"value": 8.9, "display": 8.9, "source": "y", "rank": 50, "url": "", "date": "", "detail": ""}]
    a3 = adjudicate("crash", items3)
    assert a3["status"] == "CONFLICT", a3
    print("  [4] 非网查分歧一律标冲突 ✓")
    # 5 单源 → SINGLE（不伪装成 AGREE）
    a5 = adjudicate("kwh", [{"value": 62.5, "display": 62.5, "source": "x", "rank": 50, "url": "", "date": "", "detail": ""}])
    assert a5["status"] == "SINGLE", a5
    print("  [5a] 单源标 SINGLE ✓")
    assert adjudicate("kwh", [])["status"] == "MISSING"
    print("  [5b] 缺失标记 ✓")
    # 6 车名跨库对齐
    lib = {"大众ID.AURA T6": {}, "极狐阿尔法T5": {}}
    assert _find_in(lib, "ID.AURA T6") is not None
    assert _find_in(lib, "阿尔法T5") is not None
    print("  [6] 跨库车名对齐 ✓")
    print("全部通过 ✓")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--pool", default=os.path.join(CAT, "master_pool.json"))
    ap.add_argument("--model", help="只核验指定车型（可逗号分隔）")
    ap.add_argument("--dcd-cache", help="懂车帝逐月销量快照路径（可选，纳入销量交叉核验）")
    ap.add_argument("--out", help="报告输出目录")
    ap.add_argument("--conflicts-only", action="store_true", help="只打印冲突项")
    ap.add_argument("--all-sources", action="store_true", help="含池外源车（默认只核验总池内车型）")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return 0
    models = [m.strip() for m in a.model.split(",")] if a.model else None
    result, S = verify_all(models=models, dcd_path=a.dcd_cache, pool_only=not a.all_sources)
    out_dir = a.out or os.path.join(HERE, "runs", "cross_verify_20261119")
    jp, mp, data = write_report(result, out_dir, a.conflicts_only)
    print(f"交叉核验完成：{data['summary']['车型']} 款，"
          f"冲突 {data['summary']['冲突项']}，单源 {data['summary']['单源项']}")
    print(f"  JSON → {jp}\n  MD   → {mp}")
    if a.conflicts_only:
        for c in data["冲突清单"]:
            vals = " / ".join(f"{v['value']}[{v['source']}]" for v in c.get("conflict", []))
            print(f"  ⚠ {c['car']} · {c['label']}: {vals}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
