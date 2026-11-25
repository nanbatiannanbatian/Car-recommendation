#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""销量榜全车型索引（sales_index.py）——查排行榜的第一入口。

为什么要它（2026-11-25 用户要求）：
  用户问「排行榜/某车卖得怎样/某价位有哪些车」时，最权威的入口不是打分榜，而是
  **最近一个已发布月份的销量榜全车型表**：它决定这个月到底有哪些车在卖、卖多少、
  是什么能源类型。此前每次都要重新联网翻懂车帝四张榜，既慢又不可复现。

规则（用户口述，已代码化）：
  1. 每次先探测「最近一个**已发布**的销量月份」——不是简单取上个月：懂车帝接口
     对未发布月份会**静默回退**到最近可用月份，所以必须核对返回的 months 元信息，
     不能拿非空响应当成目标月。
  2. 本地缓存命中就直接查（`data/gen/sales_index/<YYYYMM>.json`），不再联网。
  3. 没命中才抓四张榜（纯电/插混/增程/全部），落盘后返回。
  4. 过期数据删除：只保留最近 KEEP_MONTHS 个月（默认 3），更早的月份文件删除。

数据形态（每月一个 JSON + 一个 index.json）：
  {month, month_label, generated_at, source, counts{all,bev,phev,erev},
   models:[{name, brand, series_id, energy, energy_label, energy_basis,
            rank_all, rank_scope, sales_all, sales_bev, sales_phev, sales_erev,
            price_min, price_max}],
   by_energy:{BEV:n,...}}

能源类型判定优先级：
  ① 该月纯电/插混/增程榜命中 → BEV/PHEV/EREV（榜单口径，最硬）；
  ② 候选池人工标注（categories/pool_membership.json 的 tags.power）→ BEV/PHEV/EREV/HEV/ICE；
  ③ 只在「全部榜」出现且池里没有 → FUEL_OR_HEV（燃油/油混，榜单未细分，不硬猜）。

用法：
    python -m modules.board.sales_index                 # 确保缓存 + 打印概况
    python -m modules.board.sales_index --ensure        # 只确保最新月缓存
    python -m modules.board.sales_index --query 小米     # 查车型（自动确保缓存）
    python -m modules.board.sales_index --query 元up --energy BEV
    python -m modules.board.sales_index --month 202609  # 指定月份（缓存优先）
    python -m modules.board.sales_index --refresh       # 强制重抓
    python -m modules.board.sales_index --prune         # 只做过期清理
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys

import paths

CACHE_DIR = paths.GEN_DIR / "sales_index"
INDEX_NAME = "index.json"
KEEP_MONTHS = 3

ENERGY_LABEL = {
    "BEV": "纯电",
    "PHEV": "插混",
    "EREV": "增程",
    "HEV": "油电混动",
    "ICE": "燃油",
    "FUEL_OR_HEV": "燃油/油混（未细分）",
    "UNKNOWN": "未知",
}
ENERGY_ALIAS = {
    "纯电": "BEV", "bev": "BEV", "ev": "BEV", "电动": "BEV",
    "插混": "PHEV", "phev": "PHEV", "dm": "PHEV", "dm-i": "PHEV",
    "增程": "EREV", "erev": "EREV", "增程式": "EREV",
    "油混": "HEV", "hev": "HEV", "油电混合": "HEV",
    "燃油": "ICE", "ice": "ICE", "汽油": "ICE",
}


class SalesIndexError(RuntimeError):
    pass


# --------------------------------------------------------------------------- 基础
def _prev_month(y: int, m: int) -> tuple[int, int]:
    return (y - 1, 12) if m == 1 else (y, m - 1)


def _month_label(ym: int) -> str:
    return f"{ym // 100}年{ym % 100:02d}月"


def _atomic_write(path, obj) -> None:
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def load_index() -> dict:
    p = CACHE_DIR / INDEX_NAME
    if not p.exists():
        return {"latest": None, "updated": None, "months": []}
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {"latest": None, "updated": None, "months": []}


def load_month(month: int) -> dict | None:
    p = CACHE_DIR / f"{int(month)}.json"
    if not p.exists():
        return None
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        return None
    return d if d.get("models") else None


# --------------------------------------------------------------------------- 探测
def remote_available_months() -> list[int]:
    """读懂车帝接口返回的 months 元信息（真实已发布月份，不是请求参数）。"""
    from modules.research import fetch_dcd_sales as D
    today = dt.date.today()
    y, m = _prev_month(today.year, today.month)
    rows, mm = D.fetch_page(str(y * 100 + m), "", 0)
    months = []
    for x in mm or []:
        try:
            months.append(int(x.get("month")))
        except Exception:
            continue
    return sorted(months, reverse=True)


def latest_month(refresh: bool = False, verbose: bool = True) -> tuple[int, str]:
    """最近一个已发布月份。联网失败时回退本地最大月。返回 (月份, 来源说明)。"""
    idx = load_index()
    local = idx.get("latest")
    if not refresh:
        try:
            months = remote_available_months()
            if months:
                newest = months[0]
                note = f"接口确认最新已发布月份 {newest}"
                if local and newest <= int(local):
                    return int(local), f"本地缓存已是最新（{local}）"
                return newest, note
        except Exception as e:
            if local:
                if verbose:
                    print(f"  [warn] 联网探测失败（{type(e).__name__}），回退本地 {local}")
                return int(local), f"联网失败，回退本地 {local}"
            raise SalesIndexError(f"销量月份探测失败且无本地缓存：{type(e).__name__}: {e}")
    months = remote_available_months()
    if not months:
        raise SalesIndexError("接口未返回任何可用月份")
    return months[0], f"接口确认最新已发布月份 {months[0]}"


# --------------------------------------------------------------------------- 抓取
def _pool_energy() -> dict[str, str]:
    """候选池人工标注的能源类型（按 norm 名索引）。"""
    p = paths.cat("pool_membership.json")
    try:
        d = json.load(open(str(p), encoding="utf-8"))
    except Exception:
        return {}
    from modules.research.fetch_dcd_sales import norm as _norm
    out = {}
    for c in d.get("cars") or []:
        pw = str((c.get("tags") or {}).get("power") or "").upper()
        if not pw:
            continue
        key = _norm(c.get("model") or "")
        if key:
            out[key] = pw
    return out


def build_month(month: int, verbose: bool = True) -> dict:
    """抓四张榜并合成一个月度索引。目标月未发布 → 抛错（绝不缓存错月数据）。"""
    from modules.research import fetch_dcd_sales as D

    scopes = (("1", "bev"), ("2", "phev"), ("3", "erev"), ("", "all"))
    raw = {}
    meta_months = None
    for ne, tag in scopes:
        rows, mm = D.fetch_all(str(month), ne)
        if meta_months is None and mm:
            meta_months = mm
        raw[tag] = rows
        if verbose:
            print(f"  抓取 {_month_label(month)} {tag}: {len(rows)} 款")
    available = []
    for x in meta_months or []:
        try:
            available.append(int(x.get("month")))
        except Exception:
            pass
    if not raw["all"]:
        raise SalesIndexError(f"{month} 全部榜为空（可能未发布）")
    if available and month not in available:
        raise SalesIndexError(
            f"{month} 尚未发布，接口实际返回的是 {max(available)}（可用月份 {available[:6]}）")

    pool = _pool_energy()
    models: dict[str, dict] = {}

    def rec(row: dict) -> dict:
        sid = row.get("series_id")
        name = row.get("series_name") or ""
        key = f"id:{sid}" if sid else f"nm:{D.norm(name)}"
        if key not in models:
            models[key] = {
                "name": name, "brand": row.get("brand") or "", "series_id": sid,
                "energy": None, "energy_basis": None,
                "rank_all": None, "rank_scope": {}, "sales_all": 0,
                "sales_bev": 0, "sales_phev": 0, "sales_erev": 0,
                "price_min": row.get("min_price"), "price_max": row.get("max_price"),
                "_norm": D.norm(name),
            }
        return models[key]

    for row in raw["all"]:
        r = rec(row)
        r["rank_all"] = row.get("rank")
        r["sales_all"] = int(row.get("sales") or 0)
        if r["price_min"] is None:
            r["price_min"] = row.get("min_price")
        if r["price_max"] is None:
            r["price_max"] = row.get("max_price")
    for tag, energy in (("bev", "BEV"), ("phev", "PHEV"), ("erev", "EREV")):
        for row in raw[tag]:
            r = rec(row)
            r["energy"] = r["energy"] or energy
            r["energy_basis"] = r["energy_basis"] or f"榜单口径（{ENERGY_LABEL[energy]}榜）"
            r["rank_scope"][energy] = row.get("rank")
            r[f"sales_{tag}"] = int(row.get("sales") or 0)

    for r in models.values():
        if not r["energy"]:
            pw = pool.get(r["_norm"])
            if pw in ENERGY_LABEL:
                r["energy"], r["energy_basis"] = pw, "候选池人工标注"
        if not r["energy"]:
            r["energy"], r["energy_basis"] = "FUEL_OR_HEV", "仅全部榜命中、榜单未细分"
        r["energy_label"] = ENERGY_LABEL.get(r["energy"], r["energy"])

    out_models = []
    for r in sorted(models.values(), key=lambda x: (x["rank_all"] is None,
                                                    x["rank_all"] or 10**9)):
        out_models.append({
            "name": r["name"], "brand": r["brand"], "series_id": r["series_id"],
            "energy": r["energy"], "energy_label": r["energy_label"],
            "energy_basis": r["energy_basis"],
            "rank_all": r["rank_all"], "rank_scope": r["rank_scope"],
            "sales_all": r["sales_all"], "sales_bev": r["sales_bev"],
            "sales_phev": r["sales_phev"], "sales_erev": r["sales_erev"],
            "price_min": r["price_min"], "price_max": r["price_max"],
        })

    by_energy = {}
    for m in out_models:
        by_energy[m["energy"]] = by_energy.get(m["energy"], 0) + 1

    return {
        "month": month,
        "month_label": _month_label(month),
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source": {
            "name": "懂车帝销量榜（车系级单月）",
            "api": "https://www.dongchedi.com/motor/rank",
            "scopes": ["纯电", "插混", "增程", "全部"],
            "note": "全部榜=含燃油；纯电/插混/增程榜用于判定能源类型与分口径销量。",
        },
        "counts": {"all": len(raw["all"]), "bev": len(raw["bev"]),
                   "phev": len(raw["phev"]), "erev": len(raw["erev"])},
        "by_energy": by_energy,
        "models": out_models,
    }


# --------------------------------------------------------------------------- 缓存
def prune(keep: int = KEEP_MONTHS, verbose: bool = True) -> list[str]:
    """删除最近 keep 个月之外的月份文件。"""
    idx = load_index()
    months = [int(m["month"]) for m in (idx.get("months") or []) if m.get("month")]
    months = sorted(set(months), reverse=True)[:keep]
    removed = []
    for p in sorted(CACHE_DIR.glob("*.json")):
        if p.name == INDEX_NAME:
            continue
        m = re.match(r"(\d{6})\.json$", p.name)
        if m and int(m.group(1)) not in months:
            p.unlink()
            removed.append(p.name)
            if verbose:
                print(f"  [prune] 删除过期缓存 {p.name}")
    return removed


def _refresh_index(keep: int = KEEP_MONTHS) -> dict:
    entries = []
    for p in sorted(CACHE_DIR.glob("*.json")):
        m = re.match(r"(\d{6})\.json$", p.name)
        if not m:
            continue
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if not d.get("models"):
            continue
        entries.append({"month": int(m.group(1)),
                        "month_label": d.get("month_label"),
                        "count": len(d["models"]),
                        "by_energy": d.get("by_energy") or {},
                        "generated_at": d.get("generated_at")})
    entries = sorted(entries, key=lambda x: x["month"], reverse=True)[:keep]
    idx = {"latest": entries[0]["month"] if entries else None,
           "updated": dt.datetime.now().isoformat(timespec="seconds"),
           "keep_months": keep, "months": entries}
    _atomic_write(CACHE_DIR / INDEX_NAME, idx)
    return idx


def ensure(month: int | None = None, refresh: bool = False,
           keep: int = KEEP_MONTHS, verbose: bool = True) -> tuple[dict, bool]:
    """确保目标月缓存存在。返回 (数据, 是否来自缓存)。"""
    paths.ensure_dir(CACHE_DIR)
    if month is None and not refresh:
        idx = load_index()
        if idx.get("latest") and load_month(int(idx["latest"])):
            latest_local = int(idx["latest"])
            today = dt.date.today()
            py, pm = _prev_month(today.year, today.month)
            prev_ym = py * 100 + pm
            # 上个月就是理论上限：本地已有上个月的数据，就不可能再更新，直接命中、不联网。
            if latest_local >= prev_ym:
                if verbose:
                    print(f"  [cache] 命中本地缓存 {latest_local}（已是最新可发布月份）")
                return load_month(latest_local), True
            # 本地落后于上个月，才需要联网确认有没有新月份
            try:
                probe, note = latest_month(refresh=False, verbose=verbose)
            except Exception:
                probe, note = latest_local, "联网失败，使用本地最新"
            if probe <= latest_local:
                if verbose:
                    print(f"  [cache] 命中本地缓存 {latest_local}（{note}）")
                return load_month(latest_local), True
            month = probe
    if month is None:
        month, note = latest_month(refresh=refresh, verbose=verbose)
        if verbose:
            print(f"  [probe] {note}")
    cached = load_month(int(month))
    if cached and not refresh:
        if verbose:
            print(f"  [cache] 命中 {month} 月缓存（{len(cached['models'])} 款）")
        return cached, True
    if verbose:
        print(f"  [fetch] 抓取 {month} 月四张榜…")
    data = build_month(int(month), verbose=verbose)
    _atomic_write(CACHE_DIR / f"{int(month)}.json", data)
    _refresh_index(keep=keep)
    prune(keep=keep, verbose=verbose)
    return data, False


# --------------------------------------------------------------------------- 查询
def query(kw: str = "", month: int | None = None, energy: str | None = None,
          limit: int = 20, refresh: bool = False) -> dict:
    data, hit = ensure(month=month, refresh=refresh)
    e = None
    if energy:
        e = ENERGY_ALIAS.get(str(energy).strip().lower()) or ENERGY_ALIAS.get(str(energy).strip())
        if not e:
            raise SalesIndexError(f"无法识别的能源类型：{energy}（可用：纯电/插混/增程/油混/燃油）")
    kw_l = (kw or "").strip().lower()
    rows = []
    for m in data["models"]:
        if e and m["energy"] != e:
            continue
        if kw_l and kw_l not in (m["name"] + " " + m["brand"]).lower():
            continue
        rows.append(m)
    return {"month": data["month"], "month_label": data["month_label"],
            "from_cache": hit, "total": len(rows), "rows": rows[:limit]}


def stats(month: int | None = None, refresh: bool = False) -> dict:
    data, hit = ensure(month=month, refresh=refresh)
    total_sales = sum(int(m.get("sales_all") or 0) for m in data["models"])
    return {"month": data["month"], "month_label": data["month_label"],
            "from_cache": hit, "models": len(data["models"]),
            "total_sales": total_sales, "counts": data.get("counts"),
            "by_energy": data.get("by_energy"), "generated_at": data.get("generated_at")}


def export_csv(month: int | None = None, path: str | None = None,
               refresh: bool = False) -> str:
    """把索引导出为 Excel 可直接打开的 CSV（UTF-8 with BOM）。"""
    import csv

    data, _ = ensure(month=month, refresh=refresh, verbose=False)
    out = path or str(paths.RUNS_DIR / f"销量索引_{data['month']}.csv")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    cols = ["全部榜名次", "车型", "品牌", "能源类型", "能源判定依据", "月销(全部榜)",
            "纯电榜销量", "插混榜销量", "增程榜销量", "指导价下限(万)", "指导价上限(万)"]
    with open(out, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for m in data["models"]:
            w.writerow([m.get("rank_all") or "", m["name"], m["brand"], m["energy_label"],
                        m.get("energy_basis") or "", m.get("sales_all") or 0,
                        m.get("sales_bev") or 0, m.get("sales_phev") or 0,
                        m.get("sales_erev") or 0, m.get("price_min") or "",
                        m.get("price_max") or ""])
    return out


# --------------------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(description="销量榜全车型索引（查榜第一入口）")
    ap.add_argument("--ensure", action="store_true", help="确保最新月缓存")
    ap.add_argument("--month", type=int, help="指定月份 YYYYMM（缓存优先）")
    ap.add_argument("--refresh", action="store_true", help="强制重抓当月")
    ap.add_argument("--query", metavar="KW", help="按车型/品牌关键词查")
    ap.add_argument("--energy", help="能源类型过滤：纯电/插混/增程/油混/燃油")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--prune", action="store_true", help="只做过期清理")
    ap.add_argument("--export", nargs="?", const="", metavar="PATH",
                    help="导出 CSV（默认 runs/销量索引_<YYYYMM>.csv）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args(argv)
    try:
        if a.export is not None:
            print(export_csv(month=a.month, path=a.export or None, refresh=a.refresh))
            return 0
        if a.prune:
            print(json.dumps({"removed": prune()}, ensure_ascii=False))
            return 0
        if a.query:
            r = query(a.query, month=a.month, energy=a.energy, limit=a.limit,
                      refresh=a.refresh)
            if a.json:
                print(json.dumps(r, ensure_ascii=False, indent=1))
            else:
                print(f"{r['month_label']}（{r['month']}）{r['total']} 款命中"
                      + ("，缓存" if r["from_cache"] else "，新抓"))
                for m in r["rows"]:
                    print(f"  #{m['rank_all'] or '-':>3} {m['name']:<16} {m['energy_label']:<10}"
                          f" 月销 {m['sales_all']:>7}  {m['brand']}")
            return 0
        s = stats(month=a.month, refresh=a.refresh)
        if a.json:
            print(json.dumps(s, ensure_ascii=False, indent=1))
        else:
            print(f"[销量索引] {s['month_label']}（{s['month']}）"
                  f"{'（缓存）' if s['from_cache'] else '（新抓）'}：{s['models']} 款，"
                  f"合计 {s['total_sales']} 辆")
            print("  能源分布：" + "、".join(
                f"{ENERGY_LABEL.get(k, k)} {v}" for k, v in sorted(s["by_energy"].items())))
            print(f"  缓存目录：{CACHE_DIR}")
        return 0
    except SalesIndexError as e:
        print(f"[sales_index] {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
