#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""候选发现（discover.py）——把「新上市/被漏掉的车系」自动找出来。

为什么需要这个：
  建池一直是手工的。极狐阿尔法T7（中大型 SUV、10 月 8,131 台、上市两个月）就是这样被漏掉的——
  不是规则排除它，而是没人想起来加。这种错误不会报错、不会告警，只会让榜单少一台车。

做法：
  1 拉懂车帝四个口径（纯电 / 插混 / 增程 / 全部）的全量榜单
  2 用「价格在带内 + 销量过阈值 + 有纯电供给」三个条件筛出可能属于目标细分的车系
  3 与现有候选池做差集 → 输出「池外待确认」清单
  4 对每个车系标注：动力类型、价格、近 3 月销量、是否新出现（可用来判断上市时间）

它只负责「找出来」，不负责「判定级别」——官方二级分类没有可批量调用的接口，
所以清单里级别一列留空，由人确认后再入池。宁可让人看一眼，也不要自动猜级别。

用法：
    python3 discover.py --candidates runs/x/candidates.json
    python3 discover.py --candidates runs/x/candidates.json --month 202610 --min-sales 500
    python3 discover.py --candidates runs/x/candidates.json --out discover.md
"""
import argparse
import json
import os
import statistics
import sys

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)
from modules.research import fetch_dcd_sales as D


def month_list(end, n):
    y, m = int(str(end)[:4]), int(str(end)[4:])
    out = []
    for _ in range(n):
        out.append(y * 100 + m)
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return sorted(out)


def fmt_m(m):
    return f"{str(m)[:4]}-{str(m)[4:]}"


def collect(months):
    """四口径 × 逐月 → {base_key: {name, brand, bev:{m:n}, phev:{}, erev:{}, all:{}}}"""
    series = {}
    for mth in months:
        for ne, bucket in (("1", "bev"), ("2", "phev"), ("3", "erev"), ("", "all")):
            rows, _ = D.fetch_all(str(mth), ne)
            for r in rows:
                k = D.norm(r["series_name"])
                rec = series.setdefault(k, {
                    "name": r["series_name"], "brand": r["brand"],
                    "bev": {}, "phev": {}, "erev": {}, "all": {},
                    "min_price": r.get("min_price"), "max_price": r.get("max_price")})
                rec[bucket][mth] = r.get("sales") or 0
                if r.get("min_price") is not None:
                    rec["min_price"] = r["min_price"]
                if r.get("max_price") is not None:
                    rec["max_price"] = r["max_price"]
            print(f"  {fmt_m(mth)} {D.NE_LABEL[ne] or '全部'}: {len(rows)}", file=sys.stderr)
    return series


def power_kind(rec):
    """按在哪些榜出现判断动力供给。"""
    has = [k for k in ("bev", "phev", "erev") if any(v > 0 for v in rec[k].values())]
    if has == ["bev"]:
        return "纯电"
    if "bev" in has:
        return "纯电+" + "/".join(x for x in has if x != "bev")
    if has:
        return "/".join(has) + "（无纯电）"
    return "仅全部榜（未拆动力）"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True, help="现有候选池 json")
    ap.add_argument("--month", default="202610")
    ap.add_argument("--lookback", type=int, default=6)
    ap.add_argument("--band", default="9,18", help="价格筛选区间（万元），默认 9,18")
    ap.add_argument("--min-sales", type=int, default=1000,
                    help="近 3 月中位销量下限；低于此值不列入清单（新车可放宽）")
    ap.add_argument("--all-only-min", type=int, default=2000,
                    help="「仅在全部榜」车系的销量下限：这类多为燃油车，门槛要高得多")
    ap.add_argument("--out", help="输出 md")
    ap.add_argument("--json", help="输出结构化 json（供 admit.py 消费）")
    a = ap.parse_args()

    lo, hi = [float(x) for x in a.band.split(",")]
    months = month_list(a.month, a.lookback)
    print(f"抓取 {fmt_m(months[0])} → {fmt_m(months[-1])}（{len(months)} 个月 × 4 口径）",
          file=sys.stderr)
    series = collect(months)

    pool = {D.norm(c["model"]) for c in json.load(
        open(a.candidates, encoding="utf-8"))["candidates"]}

    rows = []
    for k, rec in series.items():
        pk = power_kind(rec)
        if "无纯电" in pk:
            continue                                  # 主销插混/增程，不属于纯电榜
        # 价格闸门：车系最低价必须落在筛选带内
        pmin = rec.get("min_price")
        if pmin is not None and not (lo <= pmin <= hi):
            continue
        # 销量闸门：近 3 个月的中位（用最大口径，避免纯电榜漏收）
        recent = [m for m in months[-3:]]
        vals = []
        for m in recent:
            v = rec["bev"].get(m) or rec["all"].get(m) or 0
            if v:
                vals.append(v)
        med = round(statistics.median(vals)) if vals else 0
        if med < a.min_sales:
            continue
        # 是否新出现：只在最近 2 个月有数
        appear = [m for m in months if any(rec[b].get(m) for b in ("bev", "all"))]
        newish = len(appear) <= 2

        bev_seen = any(v > 0 for v in rec["bev"].values())
        rows.append({
            "key": k, "name": rec["name"], "brand": rec["brand"], "bev_seen": bev_seen,
            "in_pool": k in pool, "price": (pmin, rec.get("max_price")),
            "sales": med, "power": pk, "newish": newish,
            "first_month": fmt_m(appear[0]) if appear else "—",
            "trend": {fmt_m(m): (rec["bev"].get(m) or rec["all"].get(m) or 0)
                      for m in months},
        })

    # 主表：在纯电榜出现过的（动力确定是纯电）
    # 副表：只在「全部」榜出现的（可能是一车多动力，也可能是纯燃油）——
    #      燃油车在这一层占比极高，所以门槛必须单独抬高，否则清单会被朗逸、凯美瑞淹掉。
    outside = sorted([r for r in rows if not r["in_pool"] and r["bev_seen"]],
                     key=lambda r: -r["sales"])
    ambiguous = sorted([r for r in rows if not r["in_pool"] and not r["bev_seen"]
                        and r["sales"] >= a.all_only_min], key=lambda r: -r["sales"])
    inside = [r for r in rows if r["in_pool"]]

    L = ["# 候选发现：池外车系核查清单", ""]
    L.append(f"> 口径：懂车帝 {fmt_m(months[0])}–{fmt_m(months[-1])} 逐月四榜全量；"
             f"筛选条件＝最低价 {lo:g}–{hi:g} 万 + 有纯电供给 + 近 3 月中位销量 ≥{a.min_sales:,}。")
    L.append(f"> 现有候选池 **{len(pool)}** 款；主体筛出 **{len(rows)}** 款，"
             f"其中「确认纯电」的池外车系 **{len(outside)}** 款，"
             f"「动力待确认」的池外车系 **{len(ambiguous)}** 款。")
    L.append("")
    L.append("**级别一列刻意留空**：官方二级分类没有可批量调用的接口，"
             "自动猜级别会重演「用轴距自定义」的错误。请按汽车之家归级后填入 `cls` 再入池。")
    L.append("")
    if outside:
        L.append("## 池外待确认")
        L.append("")
        L.append("| 车系 | 品牌 | 指导价(万) | 近3月中位销量 | 动力 | 首次出现 | 级别(待填) |")
        L.append("|---|---|---|---|---|---|---|")
        for r in outside:
            p = f"{r['price'][0]:g}–{r['price'][1]:g}" if r["price"][0] is not None else "—"
            tag = " 🆕" if r["newish"] else ""
            L.append(f"| **{r['name']}**{tag} | {r['brand']} | {p} | {r['sales']:,} | "
                     f"{r['power']} | {r['first_month']} |  |")
        L.append("")
        L.append("> 🆕 = 只在最近 2 个月出现，多为新上市车型，**优先核查**。")
        L.append("")

    if ambiguous:
        L.append("## 池外 · 动力待确认")
        L.append("")
        L.append("这些车系没进纯电榜、只在「全部」榜出现，可能是「一车多动力」"
                 "（纯电+增程/插混共用销量），**也可能是纯燃油车**。逐条确认动力后再决定是否入池：")
        L.append("")
        L.append("| 车系 | 品牌 | 指导价(万) | 近3月中位销量 | 首次出现 | 说明 |")
        L.append("|---|---|---|---|---|---|")
        for r in ambiguous:
            p = f"{r['price'][0]:g}–{r['price'][1]:g}" if r["price"][0] is not None else "—"
            note = "纯电榜无此车系，需查是否有一车多动力" if not r["newish"] else "🆕 新出现，需查动力"
            L.append(f"| {r['name']} | {r['brand']} | {p} | {r['sales']:,} | "
                     f"{r['first_month']} | {note} |")
        L.append("")
    else:
        L.append("## 池外待确认\n\n无。池子覆盖了本次筛选出的全部车系。\n")

    L.append(f"## 池内（已覆盖，{len(inside)} 款）")
    L.append("")
    L.append("| 车系 | 指导价(万) | 近3月中位销量 | 动力 |")
    L.append("|---|---|---|---|")
    for r in sorted(inside, key=lambda x: -x["sales"]):
        p = f"{r['price'][0]:g}–{r['price'][1]:g}" if r["price"][0] is not None else "—"
        L.append(f"| {r['name']} | {p} | {r['sales']:,} | {r['power']} |")
    L.append("")
    L.append("## 怎么用这份清单")
    L.append("")
    L.append("1. 池外车系逐个查汽车之家官方二级分类；只有落在"
             "「中型车 / 中型SUV / 中大型车 / 中大型SUV」且纯电主销的才入池。")
    L.append("2. 入池需同时补：`cls`、`power`、官方指导价、评分配置、七维分数、`lifecycle`。")
    L.append("3. 同价位但级别不符的（如紧凑型）不要为了凑数放进来——"
             "这正是上一版把深蓝 S05 误放进中型榜的原因。")

    if a.json:
        # v9.7.43：导出全量联网榜（含池内车），供 universe_build 独立生成权威清单。
        # 此前只导出 outside（池外），导致 universe 只能从候选池抄=循环论证。
        all_rows = [{"name": r["name"], "power": r["power"],
                     "price_min": (r["price"][0] if r["price"] else None),
                     "price_max": (r["price"][1] if r["price"] else None),
                     "sales": r["sales"], "in_pool": r["in_pool"]}
                    for r in (inside + outside + ambiguous)]
        payload = {"month": str(a.month), "band": [lo, hi], "min_sales": a.min_sales,
                   "all": all_rows,
                   "outside": [{"name": r["name"], "power": r["power"],
                                "price_min": r["price"][0], "price_max": r["price"][1],
                                "sales": r["sales"], "newish": r["newish"],
                                "first_month": r["first_month"]} for r in outside],
                   "ambiguous": [{"name": r["name"], "power": r["power"],
                                  "price_min": r["price"][0], "sales": r["sales"]}
                                 for r in ambiguous]}
        json.dump(payload, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"[ok] {a.json}", file=sys.stderr)

    txt = "\n".join(L)
    print(txt)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(txt + "\n")
        print(f"\n[ok] {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
