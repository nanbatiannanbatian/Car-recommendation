#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐配置阶梯自动补录（auto_trims.py）——把 35 款待补车型的配置阶梯建起来。

数据源：汽车之家车系参数页 https://car.autohome.com.cn/config/series/<sid>.html
页面 SSR 里的 `var config = {...}` 是结构化配置矩阵，实测可稳定解析：
  - 款型名（如「N7 2027款 510 Air」）
  - 官方指导价（N7 解析出 11.99/12.99/13.99/14.99/15.69，与人工核验逐项一致）
  - 电池能量(kWh)、CLTC 纯电续航里程(km)、电池类型、电动机总功率(kW)
按 specid 对齐各列，不靠猜；解析不到的字段一律记 null，不推断、不编造。

与人工核验条目的区别（必须显式可查）：
  source_type = "official_config_page_auto"、auto_parsed = true、partial_fields 列明缺项。
  电芯供应商 / 补能曲线 / 上市时长 / 加价跃迁的背景说明不属于参数页字段，
  一律留 null 或空，不能像人工条目那样给「三腔远端气囊」这类叙述。

纪律：SCORING_ENABLED 仍为 False，本脚本只写 categories/trims.json 的事实条目，
不碰候选池、不碰分数、不碰闸门。名称匹配只用精确/受控变体，匹配不到就跳过。

用法：
    python3 auto_trims.py --candidates <候选池> --index <ah_grade_index.json> \
        --db categories/trims.json --out categories/trims.json
    python3 auto_trims.py --dry-run ...   # 只打印将补哪些，不写文件
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time

import requests

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)
# 只依赖数据契约（不 import engine）：写库前自检字段是否合规
from modules.contracts.trims_schema import validate as validate_trims

HDR = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
       "Referer": "https://car.autohome.com.cn/"}
CONFIG_URL = "https://car.autohome.com.cn/config/series/{}.html"
SLEEP = 0.12
MAX_TRIMS = 14
TODAY = dt.date.today().isoformat()


def norm(s):
    return re.sub(r"[\s\-_·（）()＋+／/]", "", str(s or "")).lower()


def clean(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", str(s or ""))).strip()


# 显式别名表：候选池车名带品牌前缀，汽车之家车系索引不带。
# 只列一对一可核验的 6 条，绝不做模糊相似度匹配。
# 未列入的（秦MAX EV / 大众ID.ERA 5S / 大众ID.AURA T6 / 小鹏MONA L05）
# 在汽车之家车系库中查无此车系，或同名前缀指向另一款车（ID.AURA ≠ ID.AURA T6），
# 一律留待补，不硬配、不推断。
ALIASES = {
    "大众与众07": "与众07",
    "大众与众09": "与众09",
    "丰田铂智7": "铂智7",
    "埃安Ray7": "Ray7",
    "东风奕派M8": "奕派M8",
    "东风风行星海V6": "星海V6",
    # 2026-11-21 出纯电中型SUV榜时补：候选池带品牌前缀、汽车之家索引不带。
    # 每条都在 ah_grade_index.json 里核实过车系名存在（不是模糊猜的）。
    "比亚迪宋L EV": "宋L EV",
    "比亚迪海狮07EV": "海狮07 EV",
    "比亚迪海狮06 EV": "海狮06",
    "比亚迪海狮06EV": "海狮06",
    "特斯拉 Model Y": "Model Y",
    "阿维塔07 纯电版": "阿维塔07",
    "问界M5纯电版": "问界M5",
}


def load_db_merged(path):
    """读取 trims 库，并把随包的人工核验条目合并进来（manual 优先）。

    为什么：trims.json 是「可重建生成物」不打包，但里面混着人工核验条目
    （逐配置表来自媒体/官网交叉核对，汽车之家车系库查无，联网也抓不回来）。
    不合并的话，新装包重建 = 人工条目全丢（2026-11-21 用户指出的隐患）。
    现在 categories/trims_manual.json 随包，生成时作为底座叠加自动补录。
    """
    db = None
    if path and os.path.exists(path):
        db = json.load(open(path, encoding="utf-8"))
    if not db:
        db = {"scoring_enabled": False, "entries": [], "out_of_board_examples": []}
    mp = paths.CAT_DIR / "trims_manual.json"
    if mp.exists():
        m = json.load(open(mp, encoding="utf-8"))
        have = {e.get("model") for e in db.get("entries", [])}
        db["entries"] = [e for e in m.get("entries", []) if e.get("model") not in have] + db.get("entries", [])
        if not db.get("out_of_board_examples"):
            db["out_of_board_examples"] = m.get("out_of_board_examples", [])
    return db


def seed_from_manual(out_path):
    """只把人工条目落成基础 trims 库（不联网、不需候选池）。新装包 bootstrap 用。"""
    db = load_db_merged(None)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    json.dump(db, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return len(db.get("entries") or [])


def find_sid_local(model, index):
    nidx = {norm(k): (k, v) for k, v in index.items()}
    # 别名优先：候选池带品牌前缀，索引不带；走显式白名单，不做模糊匹配
    alias = {norm(k): v for k, v in ALIASES.items()}.get(norm(model))
    if alias and norm(alias) in nidx:
        hit = nidx[norm(alias)]
        return hit[1]["sid"], hit[0], f"alias:{alias}"
    hit = nidx.get(norm(model))
    if hit:
        return hit[1]["sid"], hit[0], "exact"
    # 动力后缀变体（与 build_ice_pool 同一套规则）
    base = model
    for suf in ("纯电", "EV", "ev", "PHEV", "DM", "增程"):
        if base.endswith(suf) and len(base) - len(suf) >= 2:
            base = base[: -len(suf)].strip()
            break
    for cand in (base, base + "EV", base + "新能源", base + "纯电"):
        hit = nidx.get(norm(cand))
        if hit:
            return hit[1]["sid"], hit[0], f"variant:{cand}"
    return None, None, None


def find_sid(model, index):
    """本地索引 → 远程检索 的两级寻址（多源调度的兼容入口）。"""
    sid, name, how = find_sid_local(model, index)
    if sid:
        return sid, name, how
    # 索引兜底（2026-11-21）：A-Z 在售索引会漏新车系，但车系页已存在。
    # 只在本地索引全部 miss 时才走远程检索，失败返回 None，不猜。
    return find_sid_remote(model, index)


def find_sid_remote(model, index=None, timeout=25):
    """汽车之家站点搜索兜底：索引（A-Z 在售列表）常漏新车系，但车系页/配置页已存在。

    2026-11-21 根因：大众 ID.AURA T6 上市一个多月，A-Z 索引里没有它（grade 页只列
    "在售"车系且更新滞后），find_sid 直接返回 None → skipped，于是"没有下拉框"。
    实际上汽车之家车系页 (autohome.com.cn/8584) 和 config 页都在，parse_config 能正常
    解析出 4 款配置。也就是说：**不是数据源没有，是我们找车系的方式太单一**。

    策略：站点检索 → 从 URL 提取 series id（autohome.com.cn/<数字>）→ 交给 parse_config。
    只在索引 miss 时调用，不影响正常路径性能；失败一律返回 None，不猜。
    """
    import re as _re
    try:
        from modules.research import researcher as R
    except Exception:
        return None, None, None
    queries = [f"{model} 参数 配置", f"{model} 报价", model]
    seen = set()
    for q in queries:
        try:
            rs = R.search(q, backend="bing", n=8)
        except Exception:
            rs = []
        for r in rs or []:
            url = str(r.get("url") or "")
            m = _re.search(r"autohome\.com\.cn/(\d{3,5})(?:/|$|\?)", url)
            if not m:
                continue
            sid = m.group(1)
            if sid in seen:
                continue
            seen.add(sid)
            title = str(r.get("title") or "").strip()
            return sid, title or model, f"remote_search:{sid}"
    return None, None, None


def parse_config(sid, pure_ev_only=True):
    """解析参数页。pure_ev_only=True 时按「能源类型」列只保留纯电动款型。

    汽车之家把一车多动力车系（海豹06 / 秦L / 零跑C11 等）的 DM-i、增程、
    纯电款型放在同一个 config 矩阵里。纯电四榜的逐配置表若不过滤，会把
    9.68 万的 DM-i 当成纯电入门配置，直接违反「销量/价格必须纯电拆分」纪律。
    """
    r = requests.get(CONFIG_URL.format(sid), headers=HDR, timeout=25)
    r.encoding = r.apparent_encoding
    m = re.search(r"var config\s*=\s*(\{.*?\});\s*(?:var|\n)", r.text, re.S)
    if not m:
        return None, "no config JSON"
    try:
        cfg = json.loads(m.group(1))
    except Exception as e:
        return None, f"config JSON 解析失败 {type(e).__name__}"
    pt = (cfg.get("result") or {}).get("paramtypeitems") or []
    specs = {}
    price_cols = []
    for gi, grp in enumerate(pt):
        for ii, it in enumerate(grp.get("paramitems") or []):
            nm = clean(it.get("name"))
            vals = [clean(v.get("value")) for v in (it.get("valueitems") or [])]
            ids = [v.get("specid") for v in (it.get("valueitems") or [])]
            for sid2, val in zip(ids, vals):
                if sid2 is None:
                    continue
                specs.setdefault(sid2, {})[nm] = val
            # 按 specid 记录能源类型（同一列在基本参数/底盘两组里会重复出现）
            if nm == "能源类型":
                for sid2, val in zip(ids, vals):
                    if sid2 is not None:
                        specs.setdefault(sid2, {})["_energy"] = val
            # 指导价列键名被 CSS 混淆（实测只剩「厂()」），用「全是可解析数字」识别。
            # 页面里数字列很多（车长/功率/续航），先全收集，再取最靠前的一组里
            # 最靠前的一列——基本参数组第 2 项就是官方指导价。
            # 价格列有两道判据（2026-11-21 修）：
            #   a) 列名可信：汽车之家把"厂商指导价"混淆成「厂()」，含「厂」/「指导价」优先
            #   b) 值域兜底：纯数字列不止价格一个（CLTC 续航 620 也是纯数字，会抢到第一位，
            #      导致所有款型 price=620 → 被 3~120 万过滤掉 → 整款车解析出 0 个配置）
            named = nm != "车型" and ("厂" in nm or "指导价" in nm)
            numeric = sum(bool(re.fullmatch(r"\d{1,3}(?:\.\d{1,2})?", v)) for v in vals)
            in_range = sum(1 for v in vals
                           if re.fullmatch(r"\d{1,3}(?:\.\d{1,2})?", v) and 3 <= float(v) <= 120)
            if vals and named:
                price_cols.append((0, gi, ii, nm, list(zip(ids, vals))))
            elif (vals and nm != "车型" and numeric >= max(2, len(vals) * 0.7)
                  and in_range >= max(2, numeric * 0.9)):
                price_cols.append((1, gi, ii, nm, list(zip(ids, vals))))
    if price_cols:
        price_cols.sort(key=lambda x: (x[0], x[1], x[2]))
        for sid2, val in price_cols[0][4]:
            if sid2 is not None:
                specs[sid2]["_price"] = val
    if not specs:
        return None, "空配置矩阵"
    out = []
    for sid2, fields in specs.items():
        name = fields.get("车型") or ""
        p = fields.get("_price")
        if not name or not p or not re.fullmatch(r"\d{1,3}(?:\.\d{1,2})?", str(p)):
            continue
        energy = (fields.get("_energy") or "").strip()
        if energy in ("-", "—"):
            energy = ""
        if pure_ev_only:
            if energy:
                # 官方字段优先：插电混动 / 增程式 / 汽油 一律不进纯电逐配置表
                if "纯电动" not in energy and "纯电" not in energy:
                    continue
            else:
                # 极少数老页面无能源类型列，退到款型名，且要求明确出现 EV/纯电
                up = name.upper()
                if not (("EV" in up or "纯电" in name)
                        and not any(k in name for k in ("DM-i", "DM", "增程", "PHEV"))):
                    continue
                energy = "未标注（按款型名判定）"
        price = float(p)
        if not (3 <= price <= 120):
            continue
        def num(*keys):
            for k, v in fields.items():
                if k.startswith("_") or not any(x in k for x in keys):
                    continue
                mm = re.search(r"-?\d+(?:\.\d+)?", str(v))
                if mm:
                    return float(mm.group(0))
            return None
        kwh = num("电池能量(kWh)", "电池能量")
        cltc = num("CLTC纯电续航", "CLTC")
        motor = num("电动机总功率", "电机总功率", "最大功率")
        btype = next((v for k, v in fields.items()
                      if "电池类型" in k and v and v not in ("-", "—")), None)
        yr = re.search(r"(20\d{2})款", name)
        out.append({
            "name": name, "price": round(price, 2),
            "energy_type": energy or None,
            "battery_kwh": kwh, "battery_type": btype,
            "cell_supplier": None, "supplier_class": "undisclosed",
            "cltc_km": cltc, "charge": None, "motor_kw": motor,
            "key_configs": [], "group": (yr.group(1) + "款") if yr else "在售",
            "note": "自动解析自汽车之家参数页；电芯供应商/补能/质保未取得，记 null",
        })
    # 去重 + 按价格升序
    seen, uniq = set(), []
    for t in sorted(out, key=lambda x: x["price"]):
        if t["name"] in seen:
            continue
        seen.add(t["name"])
        uniq.append(t)
    return uniq[:MAX_TRIMS], None


def band_of(c):
    cls = str(c.get("cls") or "")
    return 18.0 if "中大型" in cls else 16.0


def board_of(c):
    cls = str(c.get("cls") or "")
    if "SUV" in cls:
        return "中大型纯电SUV（≤18万）" if "中大型" in cls else "中型纯电SUV（≤16万）"
    return "中大型纯电轿车（≤18万）" if "中大型" in cls else "中型纯电轿车（≤16万）"


def make_entry(c, sid, ah_name, how, trims_list, pure_ev_only=True):
    from modules.contracts.candidate_schema import trim_of
    tr = trim_of(c) or {}
    target = tr.get("price")
    ref_basis = "latest_group_price_nearest"
    # 参数页常同时列多个年款；先在「全表」按候选池评分配置价格做精确匹配，
    # 因为候选池的评分配置可能属于旧年款。若只在最新年款里找，会把 12.98 万
    # 的评分配置错锚到 15.58 万的新款，凭空造出一个口径冲突。
    groups = sorted({t["group"] for t in trims_list})
    latest = groups[-1] if groups else "在售"
    pool = [t for t in trims_list if t["group"] == latest] or trims_list
    if isinstance(target, (int, float)):
        exact = [t for t in trims_list if abs(t["price"] - float(target)) <= 0.011]
        if exact:
            key = re.sub(r"\s+", "", str(tr.get("name") or ""))
            same = [t for t in exact
                    if key and key in re.sub(r"\s+", "", t["name"])]
            ref = (same or exact)[0]["name"]
            ref_basis = "exact_price" + ("_name_hint" if same else "")
        else:
            ref = min(pool, key=lambda t: abs(t["price"] - float(target)))["name"]
    else:
        band = band_of(c)
        inband = [t for t in pool if t["price"] <= band] or pool
        ref = min(inband, key=lambda t: t["price"])["name"]
        ref_basis = "inband_lowest"
    prices = [t["price"] for t in trims_list]
    kwhs = [t["battery_kwh"] for t in trims_list if t["battery_kwh"]]
    cltcs = [t["cltc_km"] for t in trims_list if t["cltc_km"]]
    quote = (f"汽车之家参数页：{len(trims_list)} 个在售款型，指导价 {min(prices):g}–{max(prices):g} 万"
             + (f"；电池 {min(kwhs):g}–{max(kwhs):g}kWh" if kwhs else "")
             + (f"；CLTC {min(cltcs):.0f}–{max(cltcs):.0f}km" if cltcs else ""))
    return {
        "model": c["model"], "in_board": True, "board": board_of(c),
        "scored_trim": (tr.get("name") or "—"), "launch_date": None,
        "source": "汽车之家车系参数页（自动解析：款型名 / 官方指导价 / 电池能量 / CLTC）",
        "url": CONFIG_URL.format(sid), "date": TODAY,
        "source_type": "official_config_page_auto",
        "quote": quote,
        "note": (f"自动补录，目标车系「{ah_name}」匹配方式 {how}。"
                 + ("已按参数页「能源类型」列只保留纯电动款型，"
                    "排除同车系 DM-i / 增程 / 燃油款型；" if pure_ev_only else "")
                 + "只采信参数页里的结构化字段；电芯供应商、补能速度、上市时长、"
                 "媒体口径的配置亮点均未取得，显式记 null / 空数组，不推断、不加分。"
                 "这是部分事实条目，证据密度低于人工核验条目（source_type 可区分）。"),
        "trim_class": "undisclosed",
        "pure_ev_filtered": bool(pure_ev_only),
        "validation_note": None, "user_claim_check": None,
        "auto_parsed": True,
        "partial_fields": ["cell_supplier", "charge", "launch_date", "key_configs",
                           "supplier_verification"],
        "trims": trims_list,
        "price_band_max": band_of(c),
        "scored_trim_ref": ref,
        "scored_trim_ref_basis": ref_basis,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates")
    ap.add_argument("--index", help="build_ice_pool 缓存的 ah_grade_index.json")
    ap.add_argument("--db", default=str(paths.cat("trims.json")))
    ap.add_argument("--out")
    ap.add_argument("--seed-only", action="store_true",
                    help="只从 categories/trims_manual.json 生成基础库（不联网、不需候选池）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-pure-ev-filter", action="store_true",
                    help="不按能源类型过滤款型（仅用于调试，纯电榜禁止使用）")
    a = ap.parse_args()
    if not getattr(a, "seed_only", False) and not a.candidates:
        ap.error("--candidates 必填（或用 --seed-only）")
    if not getattr(a, "seed_only", False) and not a.index:
        ap.error("--index 必填（或用 --seed-only）")
    if getattr(a, "seed_only", False):
        n = seed_from_manual(a.out or a.db)
        print(f"[seed] 从 trims_manual.json 生成基础库 {n} 条 → {a.out or a.db}")
        return 0
    db = load_db_merged(a.db)
    index = json.load(open(a.index, encoding="utf-8"))
    cands = json.load(open(a.candidates, encoding="utf-8"))["candidates"]
    have = {e["model"] for e in db["entries"]}
    todo = [c for c in cands if c["model"] not in have]
    print(f"待补 {len(todo)} 款；已有 {len(have)} 款", file=sys.stderr)

    from modules.research import trim_sources as TS
    added, skipped = [], []
    for i, c in enumerate(todo):
        # 多源调度：本地索引 → 站点检索 → 存在性核对；每步留痕写进待补清单
        res = TS.collect_trims(c["model"], index, parse_config,
                               find_sid_local=find_sid_local,
                               find_sid_remote=find_sid_remote)
        if res["status"] != "ok":
            why = TS.summarize_attempts(res["attempts"])
            ev = res.get("evidence") or {}
            if ev.get("exists"):
                why += f"；多站内容页确认该车在售，指导价区间 {ev.get('price_range')}"
            skipped.append((c["model"], why))
            time.sleep(SLEEP)
            continue
        sid, ah_name, tl = res["sid"], res["ah_name"], res["trims"]
        how = res["source"]
        if a.no_pure_ev_filter:
            tl2, err = parse_config(sid, pure_ev_only=False)
            if tl2:
                tl = tl2
        # 别名/变体匹配额外过一道价格一致性，防止把同名前缀的另一款车系配进来。
        # 候选池 price_guide 是官方指导价上下界；参数页常只列最新年款，故只比起售价。
        if how != "exact":
            lo = min(t["price"] for t in tl)
            pg = c.get("price_guide")
            if (isinstance(pg, list) and len(pg) == 2 and pg[0] is not None
                    and abs(lo - float(pg[0])) > 3.0):
                skipped.append((c["model"],
                                f"价格一致性不通过（参数页起售 {lo:g} 万 vs 候选池 {pg[0]} 万，"
                                f"匹配方式 {how}）"))
                time.sleep(SLEEP)
                continue
        added.append(make_entry(c, sid, ah_name, how, tl,
                               pure_ev_only=not a.no_pure_ev_filter))
        print(f"  ✓ {c['model']:16s} → {ah_name:14s} {len(tl)} 款 "
              f"{tl[0]['price']:g}–{tl[-1]['price']:g}万", file=sys.stderr)
        time.sleep(SLEEP)

    if not a.dry_run:
        db["entries"] += added
        db["updated"] = TODAY
        db["note"] = (db.get("note", "") +
                      f" {TODAY} 自动补录 {len(added)} 款：来自汽车之家车系参数页的结构化字段"
                      "（source_type=official_config_page_auto，partial_fields 列明缺项，"
                      "证据密度低于人工核验条目，不产生任何分数）。")
        out = a.out or a.db
        json.dump(db, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        errs = validate_trims(db)
        if errs:
            print("[校验失败]", file=sys.stderr)
            for e in errs[:20]:
                print("  ", e, file=sys.stderr)
            sys.exit(1)
        print(f"\n[ok] 新增 {len(added)} 款，跳过 {len(skipped)} 款；"
              f"trims.json 现共 {len(db['entries'])} 款，校验通过 → {out}")
    else:
        print(f"\n[dry-run] 将新增 {len(added)} 款")
    if skipped:
        print("\n跳过清单：", file=sys.stderr)
        for m, why in skipped:
            print(f"  - {m}: {why}", file=sys.stderr)


if __name__ == "__main__":
    main()
