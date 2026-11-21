#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""乘联会（CPCA）月度市场快照（fetch_sales.py）——L5 证据层的可复现取数。

为什么只做市场层、不做车型层：
  AKShare 的 6 个 car_* 接口全部是**厂商/大类/级别聚合**，没有任何「车型级销量」接口。
  因此它可以用来做三件事：
    1. 校验报告里引用的宏观同比数字（例如「10 月轿车零售 −38%」是否真实）；
    2. 给候选池提供大盘背景（这个细分是涨是跌，决定「卖得少」该不该扣分）；
    3. 校验分级结构（A0/A/B/C 占比），确认「中型车」这个闸门口径是否在扩张。
  它**不能**给某款车卖了多少台——那需要乘联会明细表或上险量，AKShare 没有。

数据源（均为乘联会官方发布页）：
  总体市场   http://data.cpcadata.com/TotalMarket
  车型大类   http://data.cpcadata.com/CategoryMarket
  级别结构   http://data.cpcadata.com/SegmentMarket（按 A00/A0/A/B/C 占比）
  新能源     http://data.cpcadata.com/FuelMarket
  厂商排名   http://data.cpcadata.com/ManRank

用法：
    python3 fetch_sales.py --digest                 # 打印同比摘要（默认最新月）
    python3 fetch_sales.py --digest --month 10      # 指定月份
    python3 fetch_sales.py --out market.json        # 落盘全量快照
"""

import argparse
import json
import sys

try:
    import akshare as ak
except Exception as e:                                   # 未装或网络问题
    ak = None
    _ERR = str(e)


MONTHS = list(range(1, 13))


def _rows(df, col="月份"):
    """把 {月份, 2025年, 2026年} 形状的表转成 {月: (去年, 今年)}。"""
    out = {}
    if df is None or getattr(df, "empty", True):
        return out
    for _, r in df.iterrows():
        m = str(r.get(col, "")).strip().rstrip("月")
        if not m.isdigit():
            continue
        try:
            prev = float(r.get("2025年"))
            cur = float(r.get("2026年"))
        except (TypeError, ValueError):
            prev = cur = None
        out[int(m)] = (prev, cur)
    return out


def _pct(cur, prev):
    if cur is None or prev in (None, 0):
        return None
    return (cur / prev - 1) * 100


def snapshot():
    """取全部市场快照。任一接口失败不影响其它，失败项记 None。"""
    if not ak:
        raise RuntimeError("未安装 akshare：python -m pip install akshare -i https://pypi.org/simple")

    def safe(fn, *a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:
            print(f"  [warn] {fn.__name__}{a} 失败：{type(e).__name__} {str(e)[:80]}",
                  file=sys.stderr)
            return None

    snap = {"source": "乘联会 CPCA via AKShare", "units": "万辆（级别结构为 %）",
            "series": {}, "segment": {}, "man_rank": None}

    snap["series"]["总体_零售"] = _rows(safe(ak.car_market_total_cpca, "狭义乘用车", "零售"))
    snap["series"]["总体_批发"] = _rows(safe(ak.car_market_total_cpca, "狭义乘用车", "批发"))
    for cate in ("轿车", "SUV", "MPV"):
        snap["series"][f"{cate}_零售"] = _rows(safe(ak.car_market_cate_cpca, cate, "零售"))
    snap["series"]["新能源_整体"] = _rows(safe(ak.car_market_fuel_cpca, "整体市场"))

    for cate in ("轿车", "SUV"):
        df = safe(ak.car_market_segment_cpca, cate)
        if df is not None and not df.empty:
            snap["segment"][cate] = [
                {**{"月份": str(r["月份"])},
                 **{k: (None if r[k] != r[k] else float(r[k]))
                    for k in ("A00", "A0", "A", "B", "C") if k in r}}
                for _, r in df.iterrows()]

    df = safe(ak.car_market_man_rank_cpca, "狭义乘用车-累计", "批发")
    if df is not None and not df.empty:
        snap["man_rank"] = [
            {"厂商": str(r["厂商"]),
             "去年累计": float(r.get("2025年 1-10月", 0) or 0),
             "今年累计": float(r.get("2026年 1-10月", 0) or 0)}
            for _, r in df.iterrows()]
    return snap


def digest(snap, month=None):
    """打印某月的同比摘要。month 为 None 时取「今年有数据」的最后一月。"""
    tot = snap["series"]["总体_零售"]
    months = [m for m, (p, c) in tot.items() if c is not None]
    if month is None:
        month = max(months) if months else None
    if month is None:
        return "> 未取到总体市场数据（网络或接口变更）。"

    def g(key, m):
        return snap["series"].get(key, {}).get(m)

    t = g("总体_零售", month)
    nev = g("新能源_整体", month)
    lines = [f"## 乘联会 2026-{month:02d} 市场快照（单位：万辆）", ""]
    lines.append("| 口径 | 2026 | 2025 | 同比 |")
    lines.append("|---|---|---|---|")

    def row(label, pair, pct=True):
        if not pair or pair[1] is None:
            lines.append(f"| {label} | — | — | — |")
            return
        prev, cur = pair
        d = _pct(cur, prev)
        lines.append(f"| {label} | {cur:.1f} | {prev:.1f} | "
                     f"{'—' if d is None else f'{d:+.1f}%'} |")

    row("狭义乘用车零售", t)
    for cate in ("轿车", "SUV", "MPV"):
        row(f"{cate}零售", g(f"{cate}_零售", month))
    row("新能源（乘联会口径）", nev)
    if t and nev and t[1] is not None and nev[1] is not None:
        fuel_cur = t[1] - nev[1]
        fuel_prev = t[0] - nev[0]
        lines.append(f"| 燃油（推算＝总体−新能源） | {fuel_cur:.1f} | {fuel_prev:.1f} | "
                     f"{_pct(fuel_cur, fuel_prev):+.1f}% |")
        lines.append("")
        lines.append(f"- 新能源渗透率 **{nev[1]/t[1]*100:.1f}%**（去年同期 {nev[0]/t[0]*100:.1f}%）")

    seg = snap["segment"].get("轿车")
    if seg:
        hit = [x for x in seg if str(month) in x["月份"] or x["月份"].endswith(f"-{month}月")]
        if hit:
            x = hit[-1]
            lines.append(f"- 轿车级别结构（{x['月份']}）：A00 {x.get('A00')}% ｜ A0 {x.get('A0')}% ｜ "
                         f"A {x.get('A')}% ｜ **B {x.get('B')}%** ｜ C {x.get('C')}%")
            lines.append("  → B 级（中型车）占比即本榜单的细分市场；占比变化决定「这个带子是在扩张还是收缩」。")

    if snap.get("man_rank"):
        lines.append("")
        lines.append("厂商累计批发（1–10 月，万辆）前五：")
        lines.append("")
        lines.append("| 厂商 | 2026 | 2025 | 同比 |")
        lines.append("|---|---|---|---|")
        top = sorted(snap["man_rank"], key=lambda x: -x["今年累计"])[:5]
        for r in top:
            d = _pct(r["今年累计"], r["去年累计"])
            lines.append(f"| {r['厂商']} | {r['今年累计']:.1f} | {r['去年累计']:.1f} | "
                         f"{'—' if d is None else f'{d:+.1f}%'} |")

    lines += ["", "> 这些数字只能说明**大盘**，不能说明某款车卖了多少。",
              "> AKShare 的 car_* 接口全部是厂商/大类/级别聚合，没有车型级销量；",
              "> 候选池的 `trend_sales` 仍需乘联会明细表或上险量另行补齐。"]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", type=int, help="指定月份 1-12（默认取今年有数据的最后一月）")
    ap.add_argument("--digest", action="store_true", help="打印同比摘要")
    ap.add_argument("--out", help="把全量快照写入 json")
    a = ap.parse_args()

    if not ak:
        sys.exit(f"未安装 akshare（{_ERR}）。安装：python -m pip install akshare -i https://pypi.org/simple")

    snap = snapshot()
    if a.out:
        json.dump(snap, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"[ok] 已写入 {a.out}", file=sys.stderr)
    if a.digest or not a.out:
        print(digest(snap, a.month))


if __name__ == "__main__":
    main()
