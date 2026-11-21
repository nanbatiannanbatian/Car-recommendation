#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 categories/cell_facts.json —— 电芯品牌与电池容量的核验事实表。

为什么单独建这张表，而不是直接改 candidates_aligned.json：
  1) candidates_aligned.json 是 202610 数据月的历史归档，改它等于篡改快照；
  2) 本表每条都带 url + quote + 核验日期，可回溯；
  3) 表里记的是「评分配置」对应的那一款电池，不是车系最大电池——
     同车系不同配置的电芯供应商与容量可能完全不同（MG 07 610版=清陶67kWh / 845版=宁德91kWh）。

档位定义（严格按用户第一条原则「宁德或弗迪为佳」）：
  1 = 宁德系（含时代一汽/时代长安/上汽时代等合资）/ 弗迪（含刀片）
  2 = 二线（国轩/中创新航/亿纬/欣旺达/蜂巢/正力/因湃等）或宁德混装、厂商自研
  3 = 新路线（半固态/固态）或未披露
"""
import json
import os

import paths

HERE = str(paths.ROOT)
OUT = str(paths.cat("cell_facts.json"))

# model, board, trim, price, kwh, cell_brand, tier, source, date, quote
# v10.10.0：唯一可写真相源已迁到契约层（随 GitHub/发布包存活），本表从那里引用。
# 不要再在这里手写电芯事实——改一处漏三处的旧问题由此根治。
from modules.contracts import vehicle_facts_seed as _SEED

F = _SEED.CELL_SEED


def main():
    entries = []
    for (m, board, trim, price, kwh, brand, tier, url, date, quote) in F:
        entries.append({
            "model": m, "board": board, "trim": trim, "price": price,
            "battery_kwh": kwh, "cell_brand": brand, "cell_tier": tier,
            "source_url": url, "verified": date, "quote": quote,
        })
    doc = {
        "note": ("电芯品牌与电池容量核验表。每条对应「评分配置」那一款电池，"
                 "不是车系最大电池。tier：1=宁德系/弗迪，2=二线或宁德混装或厂商自研，"
                 "3=新路线（半固态）或未披露。本表只补事实，不改候选池快照。"),
        "verified": "2026-11-16",
        "count": len(entries),
        "entries": entries,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(doc, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    from collections import Counter
    c = Counter(e["cell_tier"] for e in entries)
    print(f"[ok] {OUT} · {len(entries)} 条 · tier 分布 {dict(c)}")


if __name__ == "__main__":
    main()
