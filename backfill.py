#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""历史回填（backfill.py）——把「出一期榜」升级成「有一个可回溯的数据库」。

两个数据源，两种深度（2026-11 实测边界）：
  1. 懂车帝逐月纯电销量：**2023-10 起真实可用**。2023-09 以前 API 不认月份参数、
     静默回退到最新月——这是最危险的坑：数据看起来完整，其实是同一个月份复制
     59 遍。所以这里用「最新月签名比对」做回退检测，可疑月份直接跳过。
     2022 年没有免费逐车型公开源，不伪造，宁可缺。
  2. 工信部备案：公告栏目能翻到 2022 年（约第 354 批起），附件 Word 纯 Python
     解析（filings.py 同一套）。每批 20-120 行纯电乘用车，含企业/通用名称/
     续航/电量/整备——品牌备案史与「备案→上市」间隔都从这里来。

产出（全部进 warehouse 能力表，build 不会清）：
  fact_sales_history(month, series_id, series, brand, sales, rank, price_lo, price_hi)
  dim_filing（复用现有表；历史批次行的 notes 字段为空）

用法：
    python3 backfill.py --sales --from 202310            # 逐月销量回填
    python3 backfill.py --sales --month 202610           # 只补一个月（月度任务用）
    python3 backfill.py --filings --since 2022-01        # 备案史回填
    python3 backfill.py --brand-stats                    # 品牌史聚合 → fact_brand_stats
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time

import collections

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fetch_dcd_sales as D                     # noqa: E402
import filings as F                             # noqa: E402
import forecast as FC                           # noqa: E402
import warehouse as W                           # noqa: E402


def connect(db):
    import sqlite3
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    return con


def ensure_schema(db):
    con = connect(db)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS fact_sales_history(
      month INTEGER, series_id INTEGER, series TEXT, brand TEXT,
      sales INTEGER, rank INTEGER, price_lo REAL, price_hi REAL,
      PRIMARY KEY(month, series_id));
    CREATE INDEX IF NOT EXISTS idx_fsh_brand ON fact_sales_history(brand, month);
    """)
    con.commit()
    con.close()


# ---------------------------------------------------------------- 销量回填
def month_list(from_ym: str, to_ym: str) -> list[str]:
    y, m = int(from_ym[:4]), int(from_ym[4:])
    out = []
    while True:
        cur = f"{y}{m:02d}"
        out.append(cur)
        if cur >= to_ym:
            break
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def sales_month(db, ym: str, ref_sig, latest_ym: str) -> tuple[int, str]:
    """抓一个月，检测回退，入库。返回 (新增行, 状态)。"""
    rows, _ = D.fetch_all(ym, "1")
    if not rows:
        return 0, "empty"
    sig = (rows[0]["series_name"], rows[0]["sales"], len(rows))
    if ym != latest_ym and sig == ref_sig:
        return 0, "fallback(跳过)"     # API 回退到最新月，数据不可信
    con = connect(db)
    n = 0
    for r in rows:
        con.execute("INSERT OR REPLACE INTO fact_sales_history "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    [int(ym), r["series_id"], r["series_name"], r.get("brand") or "",
                     int(r.get("sales") or 0), r.get("rank"),
                     r.get("min_price"), r.get("max_price")])
        n += 1
    con.commit()
    con.close()
    return n, "ok"


def backfill_sales(db, from_ym: str, to_ym: str):
    ensure_schema(db)
    # 先抓最新月当签名基准（回退月的数据 == 最新月数据）
    latest = to_ym
    ref_rows, _ = D.fetch_all(latest, "1")
    ref_sig = (ref_rows[0]["series_name"], ref_rows[0]["sales"], len(ref_rows))
    con = connect(db)
    tot = con.execute("SELECT count(*) FROM fact_sales_history").fetchone()[0]
    con.close()
    print(f"[基准] 最新月 {latest}：{ref_sig[0]} {ref_sig[1]:,} 台，{ref_sig[2]} 行")
    print(f"[回填] {from_ym} → {to_ym}")
    ok = skip = 0
    for ym in month_list(from_ym, to_ym):
        n, st = sales_month(db, ym, ref_sig, latest)
        if st == "ok":
            ok += 1
            tot += n
            print(f"  {ym}: +{n} 行（累计 {tot:,}）")
        else:
            skip += 1
            print(f"  {ym}: {st}")
        time.sleep(0.6)
    print(f"[ok] 完成：{ok} 个月入库，{skip} 个月跳过（回退/空）")


# ---------------------------------------------------------------- 备案回填
def backfill_filings(db, since: str, cache: str):
    s = F._session()
    items = F.list_batches(s, pages=3)
    # 只取「已公告」且日期 >= since 的批次
    pub = [x for x in items if x["published"]
           and x["date"] >= since]
    print(f"[备案] {len(pub)} 批（{since} 起）")
    con = connect(db)
    con.executescript(W.CAP_SCHEMA)
    con.close()
    total = 0
    for pick in sorted(pub, key=lambda x: x["batch"]):
        try:
            rows, _ = F.fetch_batch_rows(s, pick, cache)
        except Exception as e:
            print(f"  第{pick['batch']}批（{pick['date']}）：失败 {type(e).__name__}")
            continue
        pax = [r for r in rows if r.get("cls_cat") == "乘用车"]
        con = connect(db)
        for r in pax:
            gd = F.guess_class(r["product_name"], r["model_code"],
                               r.get("kerb_kg"), r.get("batt_kwh"))
            names = r.get("names") or [r.get("common_name") or ""]
            con.execute("INSERT OR REPLACE INTO dim_filing VALUES (%s)"
                        % ",".join(["?"] * 23), [
                pick["batch"], names[0], r.get("product_name"),
                r.get("enterprise"), FC.brand_of(names[0], r.get("enterprise")),
                r.get("power"), r.get("model_code"),
                r.get("range_km"), r.get("batt_kwh"), r.get("kerb_kg"),
                gd.get("len_m"), gd.get("cls"), gd.get("how"), gd.get("body"),
                None, None, None, None, None, None, None, None,
                pick["date"]])
        con.commit()
        con.close()
        total += len(pax)
        print(f"  第{pick['batch']}批（{pick['date']}）：乘用车 {len(pax)} 行")
        time.sleep(0.5)
    print(f"[ok] 备案回填完成，累计 {total} 行")


# ---------------------------------------------------------------- 品牌聚合
def brand_stats(db, asof: str):
    con = connect(db)
    rows = con.execute("""
      SELECT brand, month, SUM(sales) AS s, COUNT(*) AS models
      FROM fact_sales_history GROUP BY brand, month
      ORDER BY brand, month""").fetchall()
    filings = con.execute("""
      SELECT brand, batch, batch_date, name FROM dim_filing
      WHERE power='BEV' AND batch_date >= '2022-01-01'""").fetchall()
    hist = con.execute("SELECT DISTINCT month FROM fact_sales_history "
                       "ORDER BY month").fetchall()
    months = [r["month"] for r in hist]
    con.close()

    # 品牌月度销量序列
    bm = {}
    for r in rows:
        bm.setdefault(r["brand"], {})[r["month"]] = r["s"]

    # 备案→上市成功率：名字首次备案后 6 个月内，月销是否达到 300。
    # 品牌口径统一用懂车帝侧（sales 表的 brand）：备案名先匹配到车系，
    # 再取该车的品牌——否则备案侧的归类名（吉利系/北汽系）和懂车帝品牌
    # （吉利银河/极狐）对不上，join 会静默断掉。
    first_filing = {}
    for r in filings:
        k = F.norm_name(r["name"])
        if k and (k not in first_filing or r["batch_date"] < first_filing[k][0]):
            first_filing[k] = (r["batch_date"], r["name"])
    con = connect(db)
    ser = con.execute("SELECT month, series, brand, sales FROM fact_sales_history"
                      ).fetchall()
    con.close()
    by_series = {}
    for r in ser:
        by_series.setdefault(F.norm_name(r["series"]), {})[r["month"]] = r["sales"]
    ser_brand_n = collections.defaultdict(collections.Counter)
    for r in ser:
        ser_brand_n[F.norm_name(r["series"])][r["brand"]] += 1
    ser_brand = {k: c.most_common(1)[0][0] for k, c in ser_brand_n.items()}

    launched = hit = 0
    hit_brands, miss_brands = collections.defaultdict(int), collections.defaultdict(int)
    unmatched_names = []
    for k, (fdate, name) in first_filing.items():
        m = by_series.get(k)
        sbrand = ser_brand.get(k)
        if not m:
            # 宽松：备案名是车系名的子串
            for sk, mv in by_series.items():
                if k and sk and (k in sk or sk in k) and abs(len(k) - len(sk)) <= 2:
                    m = mv
                    sbrand = ser_brand.get(sk)
                    break
        launched += 1
        if not m or not sbrand:
            unmatched_names.append(name)
            miss_brands["(未匹配)"] += 1
            continue
        fm = int(fdate[:4] + fdate[5:7])
        window = [v for mo, v in sorted(m.items()) if fm <= mo <= fm + 5]
        if window and max(window) >= 300:
            hit += 1
            hit_brands[sbrand] += 1
        else:
            miss_brands[sbrand] += 1

    # 月度市场份额（最近 6 个月）
    recent = months[-6:]
    tot_recent = sum(sum(v.get(mo, 0) for mo in recent) for v in bm.values()) or 1
    out_rows = []
    for brand, mv in sorted(bm.items()):
        vol6 = sum(mv.get(mo, 0) for mo in recent)
        share = vol6 / tot_recent
        n_filed = hit_brands.get(brand, 0) + miss_brands.get(brand, 0)
        rate = (hit_brands.get(brand, 0) / n_filed) if n_filed else None
        out_rows.append({"brand": brand, "vol6": vol6, "share": round(share, 4),
                         "filed": n_filed, "hit": hit_brands.get(brand, 0),
                         "hit_rate": round(rate, 3) if rate is not None else None})
    out_rows.sort(key=lambda r: -r["vol6"])

    con = connect(db)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS fact_brand_stats(
      asof TEXT, brand TEXT, vol6 INTEGER, share REAL, filed INTEGER,
      hit INTEGER, hit_rate REAL, PRIMARY KEY(asof, brand));
    """)
    for r in out_rows:
        con.execute("INSERT OR REPLACE INTO fact_brand_stats VALUES (?,?,?,?,?,?,?)",
                    [asof, r["brand"], r["vol6"], r["share"], r["filed"],
                     r["hit"], r["hit_rate"]])
    con.commit()
    con.close()

    print(f"## 品牌历史画像（asof {asof}，销量史 {months[0]}–{months[-1]}，"
          f"{len(months)} 个月）\n")
    print("| 品牌 | 近6月纯电销量 | 份额 | 备案款数 | 6月内月销≥300 | 命中率 |")
    print("|---|---|---|---|---|---|")
    for r in out_rows:
        rate = f"{r['hit_rate']*100:.0f}%" if r["hit_rate"] is not None else "样本少"
        print(f"| {r['brand']} | {r['vol6']:,} | {r['share']*100:.1f}% | "
              f"{r['filed']} | {r['hit']} | {rate} |")
    print(f"\n[ok] fact_brand_stats 写入 {len(out_rows)} 个品牌（asof {asof}）")
    return out_rows


import collections  # noqa: E402  （放在底部避免顶部依赖噪音）


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(
        "C:/Users/海南岛南霸天/WorkBuddy/2026-11-13-15-03-08/runs/car-ev-4boards",
        "car_ev.db"))
    ap.add_argument("--sales", action="store_true")
    ap.add_argument("--filings", action="store_true")
    ap.add_argument("--brand-stats", action="store_true")
    ap.add_argument("--from", default="202310", help="销量回填起始月 YYYYMM")
    ap.add_argument("--month", help="只补某一个月（月度任务增量用）")
    ap.add_argument("--to", default=None, help="截止月（默认最新已发布月份）")
    ap.add_argument("--since", default="2022-01", help="备案回填起始日期")
    ap.add_argument("--asof", default=dt.date.today().strftime("%Y%m"),
                    help="品牌画像快照月份")
    ap.add_argument("--cache", default=os.path.join(HERE, "runs", "_filings"))
    a = ap.parse_args()

    if a.sales:
        to = a.to or a.month or dt.date.today().strftime("%Y%m")
        backfill_sales(a.db, a.month or getattr(a, "from"), to)
    if a.filings:
        backfill_filings(a.db, a.since, a.cache)
    if getattr(a, "brand_stats"):
        brand_stats(a.db, a.asof)
    if not any((a.sales, a.filings, a.brand_stats)):
        ap.print_help()


if __name__ == "__main__":
    main()
