# -*- coding: utf-8 -*-
"""
build_mixed_sedan.py — 12-14 万 轿车 跨能源【演示榜】（v10.53.0）

用途：用户原始场景——"我想买 12-14 万区间的轿车"，池里天然掺着燃油/油混/插混/增程/纯电，
必须先给前置条件画像 + 能源形式适配结论，再进逐车打分榜。

数据口径（重要，页面会明示）：
  · 纯电/插混：来自 master_pool（官方核验逐配置）；
  · 燃油/油混/增程：来自 runs/mixed_1214_sedan_scores_202611.json（人工补采 8 维分 + 逐配置事实，每维附信源）；
  · 因此本榜定位为「演示池」，不是正式可引用榜单（补采车无官方销量/完整在售核验）。
  · 环境分/TCO/形式结论全部走 modules/envscore/engine.py（单一事实源），本脚本只组装 cars。

输出：runs/mixed_1214_sedan_12-14万_完整交互榜.html
"""
import json, os, sys, statistics

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)
from modules.envscore import build_energy_compare as EC
from modules.envscore import engine as ENG
from modules.board import cross_energy_sections as CES

RUNS = os.path.join(HERE, "runs")
SUPP = os.path.join(RUNS, "mixed_1214_sedan_scores_202611.json")
OUT = os.path.join(RUNS, "mixed_1214_sedan_12-14万_完整交互榜.html")
LOW, HIGH = 12, 14


def norm(s):
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


def _median(vals):
    vals = [float(v) for v in vals if isinstance(v, (int, float))]
    return round(statistics.median(vals), 2) if vals else None


def supp_to_car(c):
    """补采车 → 比较器 car 格式（8维分/价格/馈电油耗/电量/EV续航）。"""
    trims = c.get("trims") or []
    prices = [t["price"] for t in trims if isinstance(t.get("price"), (int, float))]
    band = [p for p in prices if LOW <= p <= HIGH] or prices
    price = min(band) if band else (c.get("price_guide") or [None])[0]
    sc = c.get("scores") or {}
    product = round(sum(float(sc.get(k, 0)) * float(EC.WEIGHT_MAP[k]) for k in EC.WEIGHT_MAP) * 10, 1)
    P = c["power"]
    kwh = max([t["kwh"] for t in trims if isinstance(t.get("kwh"), (int, float))], default=None)
    evr = max([t["ev_range_km"] for t in trims if isinstance(t.get("ev_range_km"), (int, float))], default=None)
    fuel = _median([t.get("fuel_l100") for t in trims])
    depr = _median([t.get("fuel_deprived_l100") for t in trims])
    # EV 模式电耗：电量 / 纯电续航 ×100（仅插混/增程有电池，用于 TCO 与场景电耗）
    cons = round(kwh / evr * 100, 1) if (kwh and evr) else None
    return {
        "model": c["model"], "cls": "轿车", "body": "轿车", "power": P,
        "price": price, "priceLow": price,
        "priceHigh": (c.get("price_guide") or [None, None])[1],
        "product": product, "cltc": evr, "kwh": kwh, "cons": cons,
        "consSrc": "补采(实测/工信部)" if cons else None,
        "weight": None, "cd": None, "area": None, "voltage": None, "swap": False,
        "cdaPerTon": None, "boxy": False,
        "fuelL100": fuel, "fuelDeprivedL100": depr,
        "sales": None,
    }


def main():
    pool = [c for c in EC.load_cars() if c.get("body") == "轿车"
            and c.get("priceLow") and LOW <= c["priceLow"] <= HIGH]
    doc = json.load(open(SUPP, encoding="utf-8"))
    supp = [supp_to_car(c) for c in doc["cars"]]
    # 同名去重：补采优先（池里的同名车多为占位/待补）
    by = {norm(c["model"]): c for c in supp}
    for c in pool:
        by.setdefault(norm(c["model"]), c)
    # v10.67.0：热泵逐车事实来自 categories/heatpump_facts.json（参数页「热泵空调」逐车核验）；
    # 本页补采车若不在事实表里 → None，冬季续航按「半热泵半PTC」默认并在页面披露
    for c in by.values():
        c["heatpump"] = EC.heatpump_of(c["model"])
    cars = sorted(by.values(), key=lambda x: -(x.get("product") or 0))
    print(f"入池 {len(cars)} 款：", {p: sum(1 for c in cars if c['power'] == p) for p in ('BEV','PHEV','EREV','HEV','ICE')})

    rows = ENG.analyze(cars, ENG.DEFAULT_COND)
    meta = {
        "dims": EC.DIMS,
        "trims": {},
        "rationale": {},
        "profile": ENG.DEFAULT_COND,
        "profile_source": "人群默认画像（11 项用车条件；本页为演示池）",
        "pool_powers": sorted({c["power"] for c in cars}),
        "cars_raw": cars,
        "bands": [(12, 13), (13, 14), (12, 14)],
    }
    nav, secs = CES.sections_html(rows, meta)
    EC.build(
        cars_override=cars, out=OUT,
        extra_head=nav,
        extra_tail=secs + "\n" + CES.dynamic_script(),
        title="12-14万 轿车 跨能源演示榜（产品力+环境分+形式结论）",
        def_low=LOW, def_high=HIGH, def_body="轿车",
        sales_month="202610",
    )
    html = open(OUT, encoding="utf-8").read()
    assert "boardNav" in html and f"{len(CES.BLOCKS)} 区块" in html, "导航缺失"
    for bid, _ in CES.BLOCKS:
        assert f'id="sec-{bid}"' in html, f"区块缺失 {bid}"
    assert "sec-form_fit" in html and "形式最合适" in html
    print(f"校验通过：{len(CES.BLOCKS)}区块齐全 · 形式结论已渲染。文件 {OUT}")


if __name__ == "__main__":
    main()




