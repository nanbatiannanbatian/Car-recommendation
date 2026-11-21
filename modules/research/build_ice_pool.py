#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""燃油 / 混动四榜候选池构建（build_ice_pool.py）。

数据链（全部公开、免登录、可核验，不破解签名、不模拟登录态）：
  1. 汽车之家按品牌字母的静态车系库 grade/carhtml/A-Z.html
       → 全库「车系名 → autohome series_id + 官方指导价区间」
  2. 汽车之家车系页 https://www.autohome.com.cn/<sid>/ 的 SSR JSON
       → "levelName"（厂商申报的**官方二级分类**）、energytype、fctName
     实测速腾页："levelId":3,"levelName":"紧凑型车","energytype":0
     这是与纯电榜 align_specs / auto_levels 完全一致的级别口径，不用轴距推定。
  3. OpenCLI `autohome score <sid>` → 口碑 7 维 + overall + PPH + 样本数
     （口碑属 L4 自选择样本，只作事实与代理分输入，不冒充官方实测）
  4. ice_sales.py 的 6 个月销量趋势

纪律：
  - 名称只做精确归一化匹配 + 明确定义的动力后缀变体；宁可漏配，不可错配。
    未命中的车系标 level=UNKNOWN 并**拒绝入池**（与 levels.json 的规矩一致）。
  - 官方级别不在四个目标级别内的，不入池。
  - energgytype 与懂车帝 ne 分类冲突时，只披露不擅改。
  - 全部结果落盘缓存，支持断点续跑；不写任何密钥。

用法：
    python3 build_ice_pool.py --sales ice_sales.json --out ice_pool.json \
        --cache-dir runs/car-ice-4boards/cache --top 220
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import string
import sys
import time

import requests

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)
from modules.research import fetch_specs as FS

HDR = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
}
GRADE_URL = "https://www.autohome.com.cn/grade/carhtml/{}.html"
SERIES_URL = "https://www.autohome.com.cn/{}/"
CLASSES = ("紧凑型车", "紧凑型SUV", "中型车", "中型SUV")
LEVEL_WORDS = ("微型车", "小型车", "紧凑型车", "中型车", "中大型车", "大型车",
               "小型SUV", "紧凑型SUV", "中型SUV", "中大型SUV", "大型SUV",
               "小型MPV", "紧凑型MPV", "中型MPV", "中大型MPV", "大型MPV", "皮卡")
SLEEP = 0.10


def norm(s):
    return re.sub(r"[\s\-_·（）()＋+／/]", "", str(s or "")).lower()


# 懂车帝把动力写进车系名，汽车之家常写「新能源」/「插电混动」；
# 只在变体**精确存在**于全库索引时才采纳，并记录变体类型供审计。
POWER_SUFFIX = ("PHEV", "phev", "DM-i", "DMi", "DM", "增程", "纯电", "EV", "ev",
                "Hi4-T", "EM-P", "EM-i")
POWER_REPLACE = ("新能源", "插电混动", "DM-i", "DM", "PHEV", "增程", "纯电")


def name_variants(name):
    out = [name]
    base = name
    for suf in POWER_SUFFIX:
        if base.endswith(suf) and len(base) - len(suf) >= 2:
            base = base[: -len(suf)].strip()
            break
    if base != name:
        out.append(base)
        for r in POWER_REPLACE:
            out.append(base + r)
        out.append(base + "新能源")
    seen, uniq = set(), []
    for v in out:
        if v and v not in seen:
            seen.add(v)
            uniq.append(v)
    return uniq


def build_index(cache_dir):
    """A-Z 静态页 → {车系名: {sid, price_min, price_max}}。"""
    path = os.path.join(cache_dir, "ah_grade_index.json")
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    idx = {}
    for ch in string.ascii_uppercase:
        try:
            r = requests.get(GRADE_URL.format(ch), headers=HDR, timeout=25)
            r.encoding = r.apparent_encoding
            blocks = re.findall(r'<li id="s(\d+)">(.*?)</li>', r.text, re.S)
            for sid, blk in blocks:
                m = re.search(r"<h4><a[^>]*>(.*?)</a>", blk, re.S)
                if not m:
                    continue
                nm = re.sub(r"<[^>]+>", "", m.group(1)).strip()
                pm = re.search(r"指导价：.*?>([\d.]+)-([\d.]+)万</a>", blk, re.S)
                rec = {"sid": sid, "price_min": float(pm.group(1)) if pm else None,
                       "price_max": float(pm.group(2)) if pm else None}
                idx.setdefault(nm, rec)
            print(f"  grade/{ch}: 累计 {len(idx)} 车系", file=sys.stderr)
        except Exception as e:
            print(f"  [warn] grade/{ch}: {type(e).__name__}", file=sys.stderr)
        time.sleep(SLEEP)
    os.makedirs(cache_dir, exist_ok=True)
    json.dump(idx, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return idx


def parse_series_page(sid):
    """车系页 SSR → levelName / energytype / fctName。"""
    r = requests.get(SERIES_URL.format(sid), headers=HDR, timeout=25)
    r.encoding = r.apparent_encoding
    t = r.text
    m = re.search(r'"levelId":(\d+),"levelName":"([^"]+)"', t)
    level = m.group(2) if m else ""
    if not level:
        # 兜底：可见文本里的级别标签（如「在售 … <span>紧凑型车</span>」）
        hit = re.search(r"<span>([^\"]*?(?:车|SUV|MPV|皮卡))</span>", t)
        lv = [w for w in LEVEL_WORDS if w in t]
        level = (hit.group(1) if hit and hit.group(1) in LEVEL_WORDS else
                 (lv[0] if len(lv) == 1 else ""))
    en = re.search(r'"energytype":(\d+)', t)
    fc = re.search(r'"fctName":"([^"]+)"', t)
    return {"level": level,
            "energytype": int(en.group(1)) if en else None,
            "fct_name": fc.group(1) if fc else None,
            "source": SERIES_URL.format(sid)}


def load_json(path, default):
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception:
            pass
    return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sales", required=True, help="ice_sales.py 的输出")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache-dir", required=True)
    ap.add_argument("--top", type=int, default=220,
                    help="只处理销量前 N 款（控制请求量）；0 = 全量，不按销量截断")
    ap.add_argument("--with-koubei", action="store_true", help="同时抓口碑（较慢）")
    a = ap.parse_args()

    os.makedirs(a.cache_dir, exist_ok=True)
    sales = json.load(open(a.sales, encoding="utf-8"))
    rows = sales["series"][:a.top] if a.top > 0 else list(sales["series"])
    print(f"[漏斗] top={a.top or 'ALL'} · 销量池 {len(sales['series'])} 款 → 待处理 {len(rows)} 款",
          file=sys.stderr)
    idx = build_index(a.cache_dir)
    print(f"全库索引 {len(idx)} 车系；待处理 {len(rows)} 款", file=sys.stderr)
    nidx = {norm(k): (k, v) for k, v in idx.items()}

    # ---- 名称匹配 ----
    matched, unmatched = [], []
    for x in rows:
        n = norm(x["model"])
        hit = nidx.get(n)
        how = "exact"
        if not hit:
            for v in name_variants(x["model"]):
                hit = nidx.get(norm(v))
                if hit:
                    how = f"variant:{v}"
                    break
        if hit:
            matched.append((x, hit[0], hit[1], how))
        else:
            unmatched.append({"model": x["model"], "brand": x["brand"],
                              "sales_oct": x["sales_oct"], "power": x["power"]})
    print(f"名称匹配 {len(matched)}/{len(rows)}；未匹配 {len(unmatched)}", file=sys.stderr)

    # ---- 级别（带缓存、断点续跑）----
    lv_path = os.path.join(a.cache_dir, "ah_levels.json")
    levels = load_json(lv_path, {})
    for i, (x, ah_name, info, how) in enumerate(matched):
        sid = info["sid"]
        if sid in levels:
            continue
        try:
            levels[sid] = parse_series_page(sid)
        except Exception as e:
            levels[sid] = {"level": "", "error": type(e).__name__}
        if (i + 1) % 25 == 0:
            json.dump(levels, open(lv_path, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            print(f"  级别进度 {i+1}/{len(matched)}", file=sys.stderr)
        time.sleep(SLEEP)
    json.dump(levels, open(lv_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # ---- 口碑（可选）----
    kb_path = os.path.join(a.cache_dir, "ah_koubei.json")
    koubei = load_json(kb_path, {})
    targets = [(x, ah_name, info, how) for x, ah_name, info, how in matched
               if (levels.get(info["sid"]) or {}).get("level") in CLASSES]
    if a.with_koubei:
        for i, (x, ah_name, info, how) in enumerate(targets):
            sid = info["sid"]
            if sid in koubei:
                continue
            d, err = FS.score_series(sid)
            koubei[sid] = d or {"error": err}
            if (i + 1) % 25 == 0:
                json.dump(koubei, open(kb_path, "w", encoding="utf-8"),
                          ensure_ascii=False, indent=1)
                print(f"  口碑进度 {i+1}/{len(targets)}", file=sys.stderr)
        json.dump(koubei, open(kb_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)

    # ---- 组装 ----
    pool, rejected = [], []
    for x, ah_name, info, how in matched:
        lv = levels.get(info["sid"]) or {}
        kb = koubei.get(info["sid"]) or {}
        rec = {
            "model": x["model"], "brand": x["brand"],
            "power": x["power"], "ne": x["ne"],
            "dcd_series_id": x["dcd_series_id"], "ah_series_id": info["sid"],
            "ah_name": ah_name, "name_match": how,
            "cls": lv.get("level") or "UNKNOWN",
            "cls_source": f"autohome 车系页 levelName（{lv.get('source') or '—'}）",
            "energytype": lv.get("energytype"), "fct_name": lv.get("fct_name"),
            "price_guide": ([info["price_min"], info["price_max"]]
                            if info["price_min"] is not None else None),
            "price_source": "汽车之家 grade/carhtml 厂商指导价",
            "dcd_price_range": x.get("price_range"),
            "sales_oct": x["sales_oct"], "trend_sales": x["trend_sales"],
            "trend_months": x.get("trend_months"), "trend_method": x.get("trend_method"),
            "sales_scope": x.get("sales_scope"),
            "koubei_overall": kb.get("overall"), "koubei_dims": kb.get("dims") or {},
            "koubei_users": kb.get("review_users") or 0,
            "pph": kb.get("pph"), "pph_usable": kb.get("pph_usable") or False,
        }
        if rec["cls"] in CLASSES:
            pool.append(rec)
        else:
            rejected.append({"model": rec["model"], "cls": rec["cls"],
                             "sales_oct": rec["sales_oct"],
                             "reason": "级别不在四榜白名单或未取到级别（拒绝入池，不猜）"})

    out = {
        "as_of": sales["as_of"], "months": sales["months"],
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sales_source": sales["source"], "level_source": "autohome grade/carhtml + 车系页 levelName",
        "classes": list(CLASSES),
        "method_note": "名称精确/受控变体匹配；级别取官方二级分类；查不到一律拒绝入池",
        "stats": {"sales_pool": len(sales["series"]), "processed": len(rows),
                  "name_matched": len(matched), "name_unmatched": len(unmatched),
                  "in_class": len(pool), "rejected": len(rejected),
                  "by_cls": {c: sum(1 for r in pool if r["cls"] == c) for c in CLASSES},
                  "by_power": {p: sum(1 for r in pool if r["power"] == p)
                               for p in ("ICE_OR_HEV", "PHEV", "EREV")}},
        "pool": pool, "name_unmatched": unmatched, "rejected": rejected,
    }
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[ok] {a.out} | 入池 {len(pool)} | {out['stats']['by_cls']} | "
          f"{out['stats']['by_power']} | 未匹配 {len(unmatched)} | 拒绝 {len(rejected)}")


if __name__ == "__main__":
    main()
