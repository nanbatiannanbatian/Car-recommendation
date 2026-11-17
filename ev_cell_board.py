#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""电芯优先纯电榜（ev_cell_board.py）——严格按用户三条规则出四榜。

用户规则（原文 → 代码实现）：
  1.「电芯为宁德或者弗迪为佳」
     → 分层而非打分：tier1 = 宁德系（含时代一汽/时代长安/上汽时代等合资）/ 弗迪；
       tier2 = 二线（国轩·中创新航·亿纬·欣旺达·蜂巢·因湃·正力）或宁德混装、厂商自研；
       tier3 = 新路线（半固态/固态）。排序时 tier 是第一顺位。
  2.「相同价位电池度数越高越好」
     → 不跨价位比绝对度数（16万的车天然比12万多10度，那不是在比车），
       而是按 2 万元分桶，桶内比 kWh，取「桶内度数位次」作为第二顺位。
  3.「销量不能太低」
     → 改成**硬门槛**而不是扣分：月销 < 1000 台直接移出推荐区，单列「销量不足区」；
       1000–1500 标「偏低」。理由：扣分只是降几名，低销量车仍会出现在推荐位，
       而用户要的是「不能太低」——这是准入问题，不是权重问题。

与 ev_battery_board.py 的区别（后者保留不删）：
  · 后者把「未披露」电芯一律压到第 3 档 —— 那是拿取证盲区罚车；
    本脚本先读 categories/cell_facts.json（37 款逐车核验过电芯与度数），
    未核验的才记第 3 档并标「未核」。
  · 后者对低销量只扣 2.5 分，大众与众09（69 台）仍排中大型轿车第 5；
    本脚本按门槛处理，它直接进「销量不足区」。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SALES_MIN = 1000          # 进推荐区的月销下限（台/月）
SALES_LOW = 1500          # 低于此值标「偏低」
BUCKET = 2.0              # 同价位分桶宽度（万元）

TIER_NAME = {1: "宁德/弗迪系", 2: "二线或混装", 3: "新路线/未核"}
BOARD_ORDER = ["中型车", "中大型车", "中型SUV", "中大型SUV"]
BOARD_LABEL = {
    "中型车": "中型纯电轿车",
    "中大型车": "中大型纯电轿车",
    "中型SUV": "中型纯电 SUV",
    "中大型SUV": "中大型纯电 SUV",
}
BOARD_CAP = {"中型车": 16, "中型SUV": 16, "中大型车": 18, "中大型SUV": 18}


def price_of(c):
    tr = c.get("trim") or {}
    p = tr.get("price")
    if isinstance(p, (int, float)):
        return float(p)
    for f in ("price_guide", "price_official"):
        v = c.get(f)
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, (list, tuple)) and len(v) == 2:
            try:
                return (float(v[0]) + float(v[1])) / 2
            except Exception:
                pass
    return None


def bucket_of(price):
    lo = int(price // BUCKET) * int(BUCKET)
    return f"{lo}–{lo + int(BUCKET)}万"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--facts", default=os.path.join(HERE, "categories", "cell_facts.json"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    data = json.load(open(a.candidates, encoding="utf-8"))
    cands = data["candidates"] if isinstance(data, dict) else data
    facts = {e["model"]: e for e in json.load(open(a.facts, encoding="utf-8"))["entries"]}

    rows = []
    dropped = []
    for c in cands:
        cls = c.get("cls")
        if cls not in BOARD_ORDER:
            continue
        if str(c.get("power", "")).upper().replace("纯电", "BEV") != "BEV":
            continue
        life = (c.get("lifecycle") or {}).get("status")
        name = c["model"]
        f = facts.get(name)
        price = (f or {}).get("price") or price_of(c)
        if price is None:
            dropped.append({"model": name, "board": cls, "why": "缺可核验指导价，无法判价格带与度数比"})
            continue
        cap = BOARD_CAP[cls]
        if price > cap:
            dropped.append({"model": name, "board": cls, "why": f"评分配置 {price} 万，超出该榜 {cap} 万上限"})
            continue
        if life == "discontinued":
            dropped.append({"model": name, "board": cls,
                            "why": "已停产：" + str((c.get("lifecycle") or {}).get("note", ""))})
            continue
        kwh = (f or {}).get("battery_kwh")
        if kwh is None:
            kwh = None
        brand = (f or {}).get("cell_brand") or c.get("cell_brand")
        tier = (f or {}).get("cell_tier")
        if tier is None:
            # 没核验过的：按候选池品牌粗分，并标 unverified
            b = str(brand or "")
            if "弗迪" in b or "宁德" in b or "时代" in b:
                tier = 1 if "/" not in b else 2
            elif b in ("", "未披露", "None"):
                tier = 3
            else:
                tier = 2
        rows.append({
            "model": name, "board": cls, "price": round(float(price), 2),
            "kwh": kwh, "brand": brand or "未核", "tier": tier,
            "verified": bool(f), "sales": c.get("trend_sales"),
            "trim": (c.get("trim") or {}).get("name"),
            "life": life or "active",
            "life_note": (c.get("lifecycle") or {}).get("note"),
            "sales_note": c.get("trend_note"),
            "evidence": c.get("evidence_status"),
            "crash": c.get("crash"),
            "safety_basis": c.get("safety_basis"),
        })

    boards = defaultdict(list)
    for r in rows:
        boards[r["board"]].append(r)

    out = {}
    for b in BOARD_ORDER:
        lst = boards.get(b, [])
        if not lst:
            continue
        # 同价位桶内度数位次
        by_bucket = defaultdict(list)
        for r in lst:
            r["bucket"] = bucket_of(r["price"])
            by_bucket[r["bucket"]].append(r)
        for bk, arr in by_bucket.items():
            arr2 = sorted(arr, key=lambda x: -(x["kwh"] or 0))
            for i, r in enumerate(arr2, 1):
                r["kwh_rank_in_bucket"] = i
                r["bucket_n"] = len(arr2)
        # 销量门槛
        for r in lst:
            s = r["sales"]
            if s is None:
                r["zone"] = "unknown"
                r["sales_flag"] = "销量未知（待补）"
            elif s < SALES_MIN:
                r["zone"] = "low"
                r["sales_flag"] = f"月销 {s:,}，低于 {SALES_MIN} 门槛"
            else:
                r["zone"] = "main"
                r["sales_flag"] = f"偏低（<{SALES_LOW}）" if s < SALES_LOW else ""
            r["epp"] = round(r["kwh"] / r["price"], 2) if (r["kwh"] and r["price"]) else None
            # 「同价位度数」的度量：kWh/万元。
            # 不用「桶内位次分位」当排序键——独占一个价格桶的车会拿到 1/1 的满分，
            # 那是分桶的伪影，不是度数优势（首版就因此把 54kWh/10.53万 的贝塔S3 顶到中型车第1）。
            # kWh/万元 本身即「每万元买到多少度」，天然只跟同价位比，且跨价位可比。
            n = r.get("bucket_n") or 1
            r["kwh_pct"] = round(1 - (r["kwh_rank_in_bucket"] - 1) / max(n - 1, 1), 3) if n > 1 else None

        main_zone = [r for r in lst if r["zone"] == "main"]
        # 字典序：① 电芯档 ② 同价位度数（kWh/万元）③ 销量（仅作并列时的兜底）
        main_zone.sort(key=lambda r: (r["tier"], -(r["epp"] or 0), -(r["sales"] or 0)))
        for i, r in enumerate(main_zone, 1):
            r["rank"] = i
        low_zone = [r for r in lst if r["zone"] != "main"]
        low_zone.sort(key=lambda r: (r["tier"], -(r["sales"] or 0)))

        out[b] = {"label": BOARD_LABEL[b], "cap": BOARD_CAP[b],
                  "main": main_zone, "low": low_zone, "n": len(lst)}
        print(f"{BOARD_LABEL[b]:16s} 推荐区 {len(main_zone)} · 销量不足区 {len(low_zone)}")
        for r in main_zone:
            print(f"   {r['rank']:2d} {r['model'][:12]:13s} T{r['tier']} {str(r['kwh']):>7s}kWh "
                  f"¥{r['price']:>5.2f} 桶{r['bucket']} {r['kwh_rank_in_bucket']}/{r['bucket_n']} "
                  f"销{r['sales']} {r['brand'][:14]}")
        for r in low_zone:
            print(f"   -- {r['model'][:12]:13s} T{r['tier']} 销{r['sales']} ({r['sales_flag']})")

    # ---- 敏感性：把「厂商自研」并入第 1 档，看名次变动 ----
    SELF = ("得壹", "神盾", "自研")

    def tier_alt(r):
        return 1 if (r["tier"] == 1 or any(k in str(r["brand"]) for k in SELF)) else r["tier"]

    sens = {}
    for b, v in out.items():
        base = [r["model"] for r in v["main"]]
        alt = sorted(v["main"], key=lambda r: (tier_alt(r), -(r["epp"] or 0), -(r["sales"] or 0)))
        sens[v["label"]] = [
            {"model": r["model"], "rank": i, "base_rank": base.index(r["model"]) + 1,
             "delta": base.index(r["model"]) + 1 - i}
            for i, r in enumerate(alt, 1)]

    os.makedirs(a.out, exist_ok=True)
    json.dump(sens, open(os.path.join(a.out, "sensitivity_selfmade.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump({"sales_min": SALES_MIN, "bucket": BUCKET, "boards": out, "dropped": dropped},
              open(os.path.join(a.out, "cell_board.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    if dropped:
        print("\n未入榜：")
        for d in dropped:
            print(f"   {d['model']}（{d['board']}）：{d['why']}")
    print(f"\n[ok] {a.out}/cell_board.json + sensitivity_selfmade.json")


if __name__ == "__main__":
    main()
