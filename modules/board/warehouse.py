#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地数据仓库（warehouse.py）——把「出一期榜」变成「查一次库」。

存储引擎选型：**sqlite3（Python 标准库），不是 DuckDB。**
  原本计划用 DuckDB，实测 `pip install duckdb` 要下 15.4MB 的 wheel，
  在这台机器的网络下两轮安装（各 5–9 分钟）都没完成。
  而我们的体量是 39 款车 × 6 个月 ≈ 1,500 行，sqlite 的查询延迟完全不是瓶颈，
  六张表、JOIN、窗口函数都支持。为 1,500 行数据引入一个装不上的依赖不划算。
  如果哪天要接全国上险量（千万行级），再换 DuckDB 或 ClickHouse——
  那时 SQL 已经写好了，换引擎只改连接串。

为什么需要：
  现在每期榜是一个 JSON + 一个 HTML，历史散在目录里。想回答「上期第 3 名这期第几」
  「这台车连续几期在 T3」只能手工翻文件。而且每次改口径都要重跑整条 Python 链，
  没法用 SQL 直接试「如果价格上限改 15.5 万会怎样」。

  把这些搬进 DuckDB：六张表，出榜变成一条 SQL，回测变成一条 JOIN。

表结构：
  dim_series       车系维度（级别、动力、生命周期）
  dim_weight       维度权重（一份配置一行，便于同时存多套口径）
  fact_sales       月度销量（区分纯电口径与车系合计）
  fact_price       指导价（带评分配置）
  fact_score       七维分数（区分人工分与配置分）
  lifecycle_event  生命周期事件（停产 / 改款 / 爬坡，带日期与依据）

用法：
    python3 warehouse.py --build --candidates cands.json --db car_ev.duckdb
    python3 warehouse.py --rank --db car_ev.duckdb --cls 中大型SUV --band 18
    python3 warehouse.py --reconcile --db car_ev.duckdb
    python3 warehouse.py --trend --db car_ev.duckdb      # 各车系逐月销量
"""
import argparse
import json
import os
import sys

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)

TABLES = ["dim_series", "dim_weight", "fact_sales", "fact_price",
          "fact_score", "lifecycle_event"]

SCHEMA = """
CREATE TABLE dim_series(
  model TEXT PRIMARY KEY, cls TEXT, brand TEXT, power TEXT,
  lifecycle_status TEXT, lifecycle_date TEXT,
  evidence TEXT, sales_scope TEXT, price_band TEXT,
  adjust REAL DEFAULT 0);
CREATE TABLE dim_weight(dim TEXT PRIMARY KEY, weight REAL, scope TEXT);
CREATE TABLE fact_sales(month TEXT, model TEXT, scope TEXT, units INTEGER);
CREATE TABLE fact_price(model TEXT, trim_name TEXT, guide_price REAL, note TEXT);
CREATE TABLE fact_score(model TEXT, dim TEXT, score REAL, source TEXT);
CREATE TABLE lifecycle_event(model TEXT, status TEXT, event_date TEXT,
  note TEXT, successor TEXT);
CREATE INDEX idx_sales ON fact_sales(model, month);
CREATE INDEX idx_score ON fact_score(model, dim);
"""

# 备案 / 预测 / 第三方证据 / 各期真实名次——四张「能力沉淀」表。
# 与正式六表分开建（IF NOT EXISTS），ingest 不动正式表，--build 也不清掉它们。
CAP_SCHEMA = """
CREATE TABLE IF NOT EXISTS dim_filing(
  batch INTEGER, name TEXT, product_name TEXT, enterprise TEXT, brand TEXT,
  power TEXT, model_codes TEXT, range_km REAL, batt_kwh REAL, kerb_kg REAL,
  len_m REAL, cls_guess TEXT, cls_how TEXT, body TEXT, status TEXT,
  price_lo REAL, price_hi REAL, launch TEXT, tech TEXT,
  notes_conf TEXT, src TEXT, source_url TEXT, batch_date TEXT,
  PRIMARY KEY(batch, name, product_name));
CREATE TABLE IF NOT EXISTS fact_forecast(
  batch INTEGER, board TEXT, name TEXT, p_top10 REAL, p_t1 REAL,
  rank_p10 INTEGER, rank_p50 INTEGER, rank_p90 INTEGER, proxy REAL,
  price_lo REAL, price_hi REAL, price_state TEXT, evidence TEXT,
  generated TEXT, PRIMARY KEY(batch, board, name));
CREATE TABLE IF NOT EXISTS fact_thirdparty(
  model TEXT, kind TEXT, dim TEXT, platform TEXT, date TEXT,
  source_type TEXT, direction TEXT, claim TEXT, delta REAL, applied REAL,
  url TEXT, PRIMARY KEY(model, kind, url));
CREATE TABLE IF NOT EXISTS fact_board(
  month INTEGER, board TEXT, model TEXT, rank INTEGER, total REAL,
  PRIMARY KEY(month, board, model));
CREATE TABLE IF NOT EXISTS fact_complaints(
  month INTEGER, model TEXT, count INTEGER, per_10k REAL,
  sales_basis REAL, window_days INTEGER, top_tags TEXT,
  PRIMARY KEY(month, model));
CREATE INDEX IF NOT EXISTS idx_filing_name ON dim_filing(name);
CREATE INDEX IF NOT EXISTS idx_forecast_name ON fact_forecast(name);
CREATE INDEX IF NOT EXISTS idx_complaints_model ON fact_complaints(model);
"""

CAP_TABLES = ["dim_filing", "fact_forecast", "fact_thirdparty",
              "fact_board", "fact_complaints"]


def connect(db, read_only=False):
    import sqlite3
    uri = f"file:{db}?mode=ro" if read_only else db
    con = sqlite3.connect(uri, uri=read_only)
    con.row_factory = sqlite3.Row
    return con


def build(db, candidates, cfg_path):
    from modules.engine import leaderboard as L
    cfg = L.load_category(cfg_path) if cfg_path else L.load_category("car_ev")
    cands = json.load(open(candidates, encoding="utf-8"))["candidates"]

    # --build 会删库重建正式六表；能力四表（dim_filing 等）是跨期累积的，
    # 删之前先搬到内存，建完再放回去——否则每月 build 一次就清一次历史。
    old_cap = {}
    if os.path.exists(db):
        try:
            con0 = connect(db, read_only=True)
            for t in CAP_TABLES:
                try:
                    cols = [r[1] for r in con0.execute(f"PRAGMA table_info({t})")]
                    old_cap[t] = (cols, con0.execute(f"SELECT * FROM {t}").fetchall()
                                  if cols else [])
                except Exception:
                    pass
            con0.close()
        except Exception:
            old_cap = {}
        os.remove(db)
    con = connect(db)
    con.executescript(SCHEMA)
    con.executescript(CAP_SCHEMA)
    for t, (cols, rows) in old_cap.items():
        if rows:
            ph = ",".join(["?"] * len(cols))
            con.executemany(f"INSERT OR REPLACE INTO {t} VALUES ({ph})",
                            [tuple(r) for r in rows])

    for c in cands:
        lc = c.get("lifecycle") or {}
        con.execute("INSERT INTO dim_series VALUES (?,?,?,?,?,?,?,?,?,?)", [
            c["model"], c.get("cls"), c.get("brand") or "", c.get("power") or "BEV",
            lc.get("status") or "active", lc.get("date") or "",
            c.get("evidence_status") or "", c.get("sales_scope") or "",
            c.get("price") or "", float(c.get("adjust") or 0)])
        trim = c.get("trim") or {}
        con.execute("INSERT INTO fact_price VALUES (?,?,?,?)", [
            c["model"], trim.get("name") or "", trim.get("price"),
            c.get("sales_note") or ""])
        for dim, v in (c.get("scores") or {}).items():
            if v is not None:
                con.execute("INSERT INTO fact_score VALUES (?,?,?,?)",
                            [c["model"], dim, float(v), "manual"])
        for m, v in (c.get("trend_months") or {}).items():
            con.execute("INSERT INTO fact_sales VALUES (?,?,?,?)",
                        [m, c["model"], c.get("sales_scope") or "unknown", int(v or 0)])
        if lc.get("status") and lc["status"] != "active":
            con.execute("INSERT INTO lifecycle_event VALUES (?,?,?,?,?)", [
                c["model"], lc["status"], lc.get("date") or "",
                lc.get("note") or "", lc.get("successor") or ""])

    for d in cfg["dimensions"]:
        con.execute("INSERT INTO dim_weight VALUES (?,?,?)",
                    [d["key"], float(d["weight"]), "base"])

    con.commit()
    n = {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES}
    con.close()
    print(f"[ok] {db}")
    for k, v in n.items():
        print(f"  {k:16s} {v:>6} 行")


def rank(db, cls, band, min_sales=0, top=10):
    """纯 SQL 出榜：级别 + 动力 + 价格 + 停产 四个闸门都在 WHERE 里。"""
    con = connect(db, read_only=True)
    sql = """
    WITH gated AS (
      SELECT s.model, s.cls, s.power, p.guide_price, p.trim_name,
             s.lifecycle_status, COALESCE(s.adjust,0) AS adjust,
             (SELECT units FROM fact_sales fs WHERE fs.model=s.model
              ORDER BY month DESC LIMIT 1) AS latest_sales
      FROM dim_series s JOIN fact_price p USING(model)
      WHERE s.cls LIKE ? AND s.power='BEV'
        AND p.guide_price IS NOT NULL AND p.guide_price <= ?
        AND COALESCE(s.lifecycle_status,'active') <> 'discontinued'
    ),
    scored AS (
      SELECT g.*, ROUND(SUM(f.score*w.weight)*10 + g.adjust, 1) AS total
      FROM gated g
      JOIN fact_score f USING(model)
      JOIN dim_weight w ON w.dim=f.dim
      GROUP BY g.model, g.cls, g.power, g.guide_price, g.trim_name,
               g.lifecycle_status, g.latest_sales, g.adjust
    )
    SELECT model, trim_name, guide_price, latest_sales, total, lifecycle_status
    FROM scored WHERE COALESCE(latest_sales,0) >= ?
    ORDER BY total DESC LIMIT ?
    """
    rows = con.execute(sql, [f"%{cls}%", band, min_sales, top]).fetchall()
    con.close()
    print(f"## {cls}（≤{band:g} 万）· SQL 出榜")
    print()
    print("| # | 车型 | 配置 | 指导价 | 最新月销 | 加权分 | 周期 |")
    print("|---|---|---|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        print(f"| {i} | {r[0]} | {r[1] or '—'} | {r[2]:g} | "
              f"{r[3] if r[3] is not None else '—'} | **{r[4]:.1f}** | {r[5]} |")
    return rows


def reconcile(db, candidates):
    """SQL 出榜 vs Python 出榜，逐款对账。差异 >0.05 说明有一边算错了。"""
    from modules.engine import leaderboard as L
    cfg = L.load_category("car_ev")
    cands = json.load(open(candidates, encoding="utf-8"))["candidates"]
    con = connect(db, read_only=True)

    bad, checked = [], 0
    for board in L.board_items(cfg):
        sub = L.filter_board(cands, board)
        passed = [c for c in sub if str((c.get("lifecycle") or {}).get("status")) != "discontinued"]
        if not passed:
            continue
        py = {r["model"]: r["total"] for r in L.score_rows(passed, cfg)}
        rows = con.execute("""
        SELECT f.model, ROUND(SUM(f.score*w.weight)*10
                              + COALESCE((SELECT adjust FROM dim_series d
                                          WHERE d.model=f.model),0), 1)
        FROM fact_score f JOIN dim_weight w ON w.dim=f.dim
        GROUP BY 1""").fetchall()
        sql = dict(rows)
        for m, v in py.items():
            checked += 1
            if m not in sql:
                bad.append((board.get("label"), m, v, None))
            elif abs(sql[m] - v) > 0.05:
                bad.append((board.get("label"), m, v, sql[m]))
    con.close()

    print(f"## SQL 与 Python 对账（{checked} 款次）")
    print()
    if not bad:
        print("**完全一致**：同一份分数与权重，两条路径算出同一个加权分。"
              "说明出榜逻辑可以搬到 SQL 而不失真。")
    else:
        print("| 榜单 | 车型 | Python | SQL | 差 |")
        print("|---|---|---|---|---|")
        for lb, m, p, s in bad:
            print(f"| {lb} | {m} | {p:.1f} | {s if s is not None else '缺'} | "
                  f"{'' if s is None else f'{s-p:+.2f}'} |")
    return bad


# ---------------------------------------------------------------- 能力沉淀表
def _brand_of(name, enterprise=""):
    from modules.board import forecast as FC
    return FC.brand_of(name or "", enterprise or "")


def ingest_filings(db, filings_path):
    """备案 → dim_filing（含 notes 字段）。幂等：INSERT OR REPLACE。"""
    fl = json.load(open(filings_path, encoding="utf-8"))
    con = connect(db)
    con.executescript(CAP_SCHEMA)
    n = 0
    for it in fl.get("items") or []:
        gd = it.get("cls_guess") or {}
        nt = it.get("notes") or {}
        pe = it.get("price_expect") if isinstance(it.get("price_expect"), list) else None
        con.execute("INSERT OR REPLACE INTO dim_filing VALUES (%s)"
                    % ",".join(["?"] * 23), [
            fl.get("batch"), it.get("name"), it.get("product_name"),
            it.get("enterprise"), _brand_of(it.get("name"), it.get("enterprise")),
            it.get("power"), ",".join(it.get("model_codes") or []),
            it.get("range_km"), it.get("batt_kwh"), it.get("kerb_kg"),
            it.get("len_m") or gd.get("len_m"), gd.get("cls"), gd.get("how"),
            it.get("body") or gd.get("body"), it.get("status"),
            pe[0] if pe else None, pe[1] if pe else None,
            nt.get("launch"), nt.get("tech"), nt.get("conf"),
            nt.get("src"), it.get("source_url") or fl.get("source_url"),
            fl.get("date")])
        n += 1
    con.commit()
    tot = con.execute("SELECT count(*) FROM dim_filing").fetchone()[0]
    con.close()
    print(f"[ok] dim_filing +{n} 行（第{fl.get('batch')}批），累计 {tot}")


def ingest_forecast(db, forecast_path):
    """预测 → fact_forecast。主键(批次,榜,车名)，同批重跑覆盖。"""
    fj = json.load(open(forecast_path, encoding="utf-8"))
    con = connect(db)
    con.executescript(CAP_SCHEMA)
    n = 0
    for board, rows in (fj.get("boards") or {}).items():
        for r in rows:
            rk = r.get("rank") or {}
            pe = r.get("price_expect") or [None, None]
            con.execute("INSERT OR REPLACE INTO fact_forecast VALUES (%s)"
                        % ",".join(["?"] * 14), [
                fj.get("batch"), board, r.get("name"),
                r.get("p_top10"), r.get("p_t1"),
                rk.get("p10"), rk.get("p50"), rk.get("p90"),
                r.get("proxy"), pe[0], pe[1], r.get("price_state"),
                r.get("evidence"), fj.get("generated")])
            n += 1
    con.commit()
    tot = con.execute("SELECT count(*) FROM fact_forecast").fetchone()[0]
    con.close()
    print(f"[ok] fact_forecast +{n} 行，累计 {tot}")


def ingest_thirdparty(db, candidates_path, category="car_ev"):
    """第三方证据 → fact_thirdparty（记录实际生效的 delta 与封顶后 applied）。"""
    from modules.engine import thirdparty as T
    from modules.engine import leaderboard as L
    cfg = L.load_category(category)
    cands = json.load(open(candidates_path, encoding="utf-8"))["candidates"]
    cands, audit = T.apply(cands)
    con = connect(db)
    con.executescript(CAP_SCHEMA)
    n = 0
    for a in audit:
        if a.get("kind") == "APPLY":
            continue
        con.execute("INSERT OR REPLACE INTO fact_thirdparty VALUES (%s)"
                    % ",".join(["?"] * 11), [
            a.get("model"), a.get("kind"), a.get("dim"), a.get("platform"),
            a.get("date"), a.get("source_type"), a.get("direction") or "0",
            a.get("claim"), a.get("delta"), None, a.get("url")])
        n += 1
    # 封顶后合计写进每款车最后一条（applied 列）
    for c in cands:
        if c.get("_tp_applied"):
            con.execute("UPDATE fact_thirdparty SET applied=? "
                        "WHERE rowid IN (SELECT rowid FROM fact_thirdparty "
                        "WHERE model=? ORDER BY date DESC LIMIT 1)",
                        [c["_tp_applied"], c.get("model")])
    con.commit()
    tot = con.execute("SELECT count(*) FROM fact_thirdparty").fetchone()[0]
    con.close()
    print(f"[ok] fact_thirdparty +{n} 行，累计 {tot}")


def ingest_board(db, month, candidates_path, category="car_ev"):
    """把某期真实名次快照存进 fact_board，供回测。口径=当期 filter_board+闸门+score。"""
    from modules.engine import leaderboard as L
    from modules.engine import ranking
    cfg = L.load_category(category)
    cands = json.load(open(candidates_path, encoding="utf-8"))["candidates"]
    con = connect(db)
    con.executescript(CAP_SCHEMA)
    n = 0
    for board in L.board_items(cfg):
        sub = L.filter_board(cands, board)
        gates = ((board.get("gates") or {})
                 or (cfg.get("ranking") or {}).get("gates") or cfg.get("gates") or {})
        passed = []
        for c in sub:
            try:
                r = ranking.apply_gates(c, gates)
            except Exception:
                passed.append(c)
                continue
            if r["pass"]:
                passed.append(c)
        for i, row in enumerate(L.score_rows(passed, cfg), 1):
            con.execute("INSERT OR REPLACE INTO fact_board VALUES (?,?,?,?,?)",
                        [month, board.get("label"), row["model"], i, row["total"]])
            n += 1
    con.commit()
    tot = con.execute("SELECT count(*) FROM fact_board").fetchone()[0]
    con.close()
    print(f"[ok] fact_board {month} +{n} 行次，累计 {tot}")


def ingest_complaints(db, complaints_path, month):
    """投诉聚合 → fact_complaints（月度快照，随时间累积成趋势）。"""
    cj = json.load(open(complaints_path, encoding="utf-8"))
    con = connect(db)
    con.executescript(CAP_SCHEMA)
    n = 0
    for m, v in (cj.get("models") or {}).items():
        con.execute("INSERT OR REPLACE INTO fact_complaints VALUES (?,?,?,?,?,?,?)",
                    [month, m, v.get("count"), v.get("per_10k"),
                     v.get("sales_basis"), cj.get("window_days"),
                     "、".join(f"{t}×{c}" for t, c in v.get("top_tags") or [])])
        n += 1
    con.commit()
    tot = con.execute("SELECT count(*) FROM fact_complaints").fetchone()[0]
    con.close()
    print(f"[ok] fact_complaints {month} +{n} 行，累计 {tot}")


def backtest(db, upto=None):
    """预测 vs 真实榜：把每条预测和它之后各期的真实名次对上。

    命中定义（写死在这里，防止回测时临时改口径）：
      · Top10 命中 = 真实名次 ≤10（预测 p_top10 ≥0.5 视为「预测会进」）
      · 名次命中 = 真实名次落在预测区间 [p10, p90] 内
      · 带外判定 = 真实未出现在该榜（p_top10 <0.5 视为「预测不会进」）
    """
    con = connect(db, read_only=True)
    rows = con.execute("""
      SELECT f.batch, f.board, f.name, f.p_top10, f.p_t1,
             f.rank_p10, f.rank_p50, f.rank_p90, f.price_state,
             b.month, b.rank, b.total
      FROM fact_forecast f
      LEFT JOIN fact_board b
        ON b.model = f.name AND b.board = f.board
       AND CAST(b.month AS INTEGER) >= CAST(f.batch / 100 AS INTEGER)
      ORDER BY f.batch, f.board, f.name, b.month""").fetchall()
    con.close()
    if not rows:
        print("没有可回测的数据（fact_forecast / fact_board 至少要有一期）")
        return
    print("## 预测回测（备案预测 vs 上市后真实名次）\n")
    print("| 批次 | 榜 | 车型 | P(Top10) | 预测名次 | 真实(月) | 结论 |")
    print("|---|---|---|---|---|---|---|")
    hit10 = hit_rank = checked = 0
    for r in rows:
        pred_in = (r["p_top10"] or 0) >= 0.5
        if r["month"] is None:
            verdict = "未上市/未入池，待回测"
            rk_txt = "—"
        else:
            checked += 1
            actual_in = (r["rank"] or 999) <= 10
            if pred_in == actual_in:
                hit10 += 1
            in_range = (r["rank_p10"] or 1) <= (r["rank"] or 999) <= (r["rank_p90"] or 999)
            if in_range:
                hit_rank += 1
            verdict = ("✓ 判对" if pred_in == actual_in else "✗ 进/不进判反") + \
                      (f"；名次{'✓' if in_range else '✗'}" if actual_in else "")
            rk_txt = f"第{r['rank']}名"
        print(f"| {r['batch']} | {r['board']} | {r['name']} | "
              f"{(r['p_top10'] or 0)*100:.0f}% | "
              f"{r['rank_p50'] if r['rank_p50'] else '带外'} | "
              f"{rk_txt}（{r['month']}） | {verdict} |")
    if checked:
        print(f"\n**已回测 {checked} 条**：进/不进判对 {hit10}（{hit10/checked*100:.0f}%），"
              f"名次落区间 {hit_rank}（{hit_rank/checked*100:.0f}%）。"
              "样本还小，这两个数是给模型调参看的，不是给读者看的。")


def trend(db, months=6):
    con = connect(db, read_only=True)
    raw = con.execute(
        "SELECT model, month, units FROM fact_sales ORDER BY model, month").fetchall()
    agg = {}
    for r in raw:
        agg.setdefault(r["model"], []).append(r["units"])
    rows = [(m, None, us) for m, us in agg.items() if len(us) >= 3]
    rows.sort(key=lambda x: -max(x[2]))
    print("## 逐月销量（仓库里存了时序，才谈得上回测）")
    print()
    print("| 车型 | 逐月 | 峰值 | 最新 | 变化 |")
    print("|---|---|---|---|---|")
    for m, ms, us in rows[:20]:
        peak, last = max(us), us[-1]
        print(f"| {m} | {' → '.join(str(u) for u in us)} | {peak:,} | {last:,} | "
              f"{(last/peak-1)*100:+.0f}% |")
    con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(HERE, "car_ev.duckdb"))
    ap.add_argument("--candidates")
    ap.add_argument("--category", default="car_ev")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--rank", action="store_true")
    ap.add_argument("--reconcile", action="store_true")
    ap.add_argument("--trend", action="store_true")
    ap.add_argument("--ingest-filings", help="filings json → dim_filing")
    ap.add_argument("--ingest-forecast", help="forecast json → fact_forecast")
    ap.add_argument("--ingest-thirdparty", action="store_true",
                    help="thirdparty 证据 → fact_thirdparty（需 --candidates）")
    ap.add_argument("--ingest-board", type=int, metavar="YYYYMM",
                    help="某期真实名次快照 → fact_board（需 --candidates）")
    ap.add_argument("--backtest", action="store_true",
                    help="预测 vs 真实名次（fact_forecast JOIN fact_board）")
    ap.add_argument("--ingest-complaints", help="投诉聚合 json → fact_complaints")
    ap.add_argument("--month", type=int, help="YYYYMM（--ingest-complaints 用）")
    ap.add_argument("--cls", default="中型车")
    ap.add_argument("--band", type=float, default=16.0)
    ap.add_argument("--min-sales", type=int, default=0)
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args()

    if a.build:
        if not a.candidates:
            sys.exit("--build 需要 --candidates")
        build(a.db, a.candidates, a.category)
    if a.rank:
        rank(a.db, a.cls, a.band, a.min_sales, a.top)
    if a.reconcile:
        reconcile(a.db, a.candidates or "")
    if a.trend:
        trend(a.db)
    if a.ingest_filings:
        ingest_filings(a.db, a.ingest_filings)
    if a.ingest_forecast:
        ingest_forecast(a.db, a.ingest_forecast)
    if a.ingest_thirdparty:
        if not a.candidates:
            sys.exit("--ingest-thirdparty 需要 --candidates（算实际生效分）")
        ingest_thirdparty(a.db, a.candidates, a.category)
    if a.ingest_board:
        if not a.candidates:
            sys.exit("--ingest-board 需要 --candidates（重算当期名次）")
        ingest_board(a.db, a.ingest_board, a.candidates, a.category)
    if a.ingest_complaints:
        if not a.month:
            sys.exit("--ingest-complaints 需要 --month YYYYMM")
        ingest_complaints(a.db, a.ingest_complaints, a.month)
    if a.backtest:
        backtest(a.db)
    if not any((a.build, a.rank, a.reconcile, a.trend, a.ingest_filings,
                a.ingest_forecast, a.ingest_thirdparty, a.ingest_board,
                a.backtest, a.ingest_complaints)):
        ap.print_help()


if __name__ == "__main__":
    main()
