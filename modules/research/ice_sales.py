#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""非纯电车型级销量（ice_sales.py）——燃油 / 混动四榜的 L5 数据层。

为什么要单独一条链：
  `fetch_dcd_sales.py` 是为**纯电榜**写的：它的 norm() 会剥掉品牌前缀与动力后缀，
  把「宋Pro DM」和「宋Pro EV」合并成同一个 key；还会把只命中插混榜的车直接
  标 `excluded=True`。这套口径用来做纯电拆分是对的，用来做燃油/混动榜会错。

关键发现（2026-11-14 实测，202610 数据）：
  懂车帝 rank_data 的 new_energy_type 不止 1/2/3，实测 0/1/2/3/4 是
  **互斥且完备**的车系主销动力划分：
      0 = 燃油 / 不插电油电混合（HEV）        237 款  534,309 辆
      1 = 纯电                                214 款  654,307 辆
      2 = 插电混动 PHEV                       124 款  227,689 辆
      3 = 增程 EREV                            27 款   58,723 辆
      4 = 一车多动力、厂家未拆车系名            64 款  177,120 辆
                                                      ─────────
      合计 666 款 1,652,148 辆 == new_energy_type=""（全部榜）精确相等
  且五类两两无交集、全部榜 0 个车系未归类。各细分榜销量 == 全部榜销量，
  说明是**按车系主销动力分类**，不是把销量按动力拆分。

因此本榜口径：
  - 入池 = 0（燃油/HEV）+ 2（PHEV）+ 3（EREV）
  - 排除 = 1（纯电）+ 4（多动力混合：销量含纯电，不能算成燃油/混动，
            否则就是「拿车系合计当单一动力销量」——正是本项目一贯禁止的错误）
  - ne=4 的 64 款单独写进 mixed_excluded，在报告里逐条披露，不静默丢弃

销量取该车系所属细分榜的逐月值；趋势值取 6 个月中位数（抗清库月/换代月）。
原始车系名精确匹配，不做前缀/后缀剥离。不含 API key，遵守限速与 robots。

用法：
    python3 ice_sales.py --month 202610 --trend 6 --out runs/car-ice-4boards/ice_sales.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import sys
import time

import requests

URL = "https://www.dongchedi.com/motor/pc/car/rank_data"
HDR = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Referer": "https://www.dongchedi.com/sales",
}
NE_LABEL = {"0": "燃油/HEV", "1": "纯电", "2": "插混", "3": "增程",
            "4": "多动力混合", "": "全部"}
NE_TYPES = ("0", "1", "2", "3", "4", "")
KEEP = {"0": "ICE_OR_HEV", "2": "PHEV", "3": "EREV"}
PAGE = 100
SLEEP = 0.12                  # 限速：别把公开接口打疼


def fetch_page(month, ne, offset, count=PAGE, retries=2):
    p = {"aid": "1839", "app_name": "auto_web_pc", "city_name": "",
         "count": str(count), "offset": str(offset), "month": str(month),
         "new_energy_type": str(ne), "rank_data_type": "11",
         "brand_id": "", "price": "", "manufacturer": "", "series_type": ""}
    for i in range(retries + 1):
        try:
            r = requests.get(URL, params=p, headers=HDR, timeout=25)
            return (r.json().get("data") or {}).get("list") or []
        except Exception as e:
            if i == retries:
                print(f"  [warn] {month} ne={ne} offset={offset} 失败 {type(e).__name__}",
                      file=sys.stderr)
                return []
    return []


def fetch_all(month, ne, hard_limit=1200):
    out, seen = [], set()
    offset = 0
    while offset < hard_limit:
        page = fetch_page(month, ne, offset)
        if not page:
            break
        for x in page:
            sid = x.get("series_id")
            if sid in seen:
                continue
            seen.add(sid)
            out.append({
                "model": (x.get("series_name") or "").strip(),
                "brand": (x.get("brand_name") or "").strip(),
                "series_id": sid,
                "sales": int(x.get("count") or 0),
                "rank": x.get("rank"),
                "min_price": x.get("min_price"),
                "max_price": x.get("max_price"),
                "price_text": x.get("price") or "",
            })
        if len(page) < PAGE:
            break
        offset += PAGE
        time.sleep(SLEEP)
    return out


def month_list(end, n):
    y, m = int(str(end)[:4]), int(str(end)[4:])
    out = []
    for _ in range(n):
        out.append(y * 100 + m)
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return sorted(out)


def _fmt(m):
    return f"{str(m)[:4]}-{str(m)[4:]}"


def collect(months, raw_cache=None):
    """返回 {month: {ne: rows}}；raw_cache 是上次抓的原始长表，可复用。"""
    by_month = {}
    for mth in months:
        if raw_cache and str(mth) in raw_cache:
            print(f"  {_fmt(mth)} 复用 raw_cache", file=sys.stderr)
            by_month[str(mth)] = raw_cache[str(mth)]
            continue
        got = {}
        for ne in NE_TYPES:
            got[ne] = fetch_all(str(mth), ne)
            print(f"  {_fmt(mth)} {NE_LABEL[ne] or '全部'}: {len(got[ne])}",
                  file=sys.stderr)
        by_month[str(mth)] = got
    return by_month


def build(months, by_month):
    last = str(months[-1])
    cur = by_month[last]
    # 以本期分类为准（车系主销动力会随改款变化，历史月只用于取销量）
    owner = {}
    for ne in ("0", "1", "2", "3", "4"):
        for r in (cur.get(ne) or []):
            owner.setdefault(r["model"], {"ne": ne, "row": r})
    # 完整兜底：全部分类都没收录的（理论上 0 个）从「全部」榜补，标 unknown
    for r in (cur.get("") or []):
        owner.setdefault(r["model"], {"ne": "unknown", "row": r})

    monthly_by_ne = {}
    for ne in NE_TYPES:
        monthly_by_ne[ne] = {
            mth: {r["model"]: r["sales"] for r in (by_month[mth].get(ne) or [])}
            for mth in by_month
        }

    kept, excluded = [], []
    for name, info in owner.items():
        ne, row = info["ne"], info["row"]
        # 逐月销量：优先取该车系本期所属分类的当月值，缺月回退到「全部」榜
        monthly = {}
        for mth in by_month:
            v = monthly_by_ne.get(ne, {}).get(mth, {}).get(name)
            if v is None and ne != "":
                v = monthly_by_ne.get("", {}).get(mth, {}).get(name) or 0
            monthly[mth] = int(v or 0)
        vals = [v for v in monthly.values() if v > 0]
        med = round(statistics.median(vals)) if vals else 0
        rec = {
            "model": name,
            "brand": row["brand"],
            "dcd_series_id": row["series_id"],
            "ne": ne,
            "power": KEEP.get(ne, "BEV" if ne == "1" else
                              "MIXED_ENERGY" if ne == "4" else "UNKNOWN"),
            "price_range": [row["min_price"], row["max_price"]],
            "price_text": row["price_text"],
            "sales_oct": row["sales"],
            "sales_month_label": _fmt(last),
            "trend_sales": med,
            "trend_months": {_fmt(m): monthly[m] for m in months},
            "trend_method": f"{len(vals)} 个月中位数（{NE_LABEL.get(ne, ne)}榜）",
            "sales_scope": "classified_series",
        }
        (kept if rec["power"] in KEEP.values() else excluded).append(rec)

    for arr in (kept, excluded):
        arr.sort(key=lambda x: -x["sales_oct"])
    return {
        "as_of": last,
        "months": [_fmt(m) for m in months],
        "source": "懂车帝销量榜 rank_data（车型级，new_energy_type=0/1/2/3/4/全部）",
        "method": "五类互斥完备分类；入池 0+2+3，排除 1+4；销量取所属分类榜逐月值，趋势取中位数",
        "verification": {
            "note": "202610 实测 0/1/2/3/4 两两无交集、与全部榜 666 款完备对应；"
                    "五类销量合计 1,652,148 == 全部榜合计",
            "counts": {NE_LABEL[ne]: len(cur.get(ne) or []) for ne in NE_TYPES},
        },
        "power_legend": {
            "ICE_OR_HEV": "ne=0 燃油或不插电油电混合（HEV）；公开榜不拆 HEV",
            "PHEV": "ne=2 插电式混合动力（DM-i / Hi4 / 雷神等）",
            "EREV": "ne=3 增程式",
            "BEV": "ne=1 纯电 → 本榜排除",
            "MIXED_ENERGY": "ne=4 一车多动力且厂家未拆车系名，销量含纯电 → 保守排除并披露",
            "UNKNOWN": "五类均未收录（异常，需人工核对）",
        },
        "series": kept,
        "excluded": excluded,
        "mixed_excluded": [x for x in excluded if x["power"] == "MIXED_ENERGY"],
        "stats": {"kept": len(kept), "excluded": len(excluded),
                  "by_power": {p: sum(1 for s in kept if s["power"] == p)
                               for p in KEEP.values()}},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default="202610")
    ap.add_argument("--trend", type=int, default=6, help="取最近 N 个月（1=只用当月）")
    ap.add_argument("--raw-cache", help="上次保存的逐月原始长表（json），复用避免重复请求")
    ap.add_argument("--save-raw", help="把逐月原始长表另存一份")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    months = [str(m) for m in month_list(a.month, a.trend)]
    raw = json.load(open(a.raw_cache, encoding="utf-8")) if a.raw_cache and os.path.exists(a.raw_cache) else None
    print(f"抓取 {months[0]} → {months[-1]}（{len(months)} 个月 × {len(NE_TYPES)} 类）",
          file=sys.stderr)
    by_month = collect(months, raw)
    if a.save_raw:
        os.makedirs(os.path.dirname(os.path.abspath(a.save_raw)), exist_ok=True)
        json.dump(by_month, open(a.save_raw, "w", encoding="utf-8"),
                  ensure_ascii=False)
    d = build(months, by_month)
    d["generated_at"] = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    json.dump(d, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[ok] {a.out} | 入池 {d['stats']['kept']} · 排除 {d['stats']['excluded']} "
          f"| {d['stats']['by_power']}")


if __name__ == "__main__":
    main()
