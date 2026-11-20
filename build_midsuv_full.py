# -*- coding: utf-8 -*-
"""
build_midsuv_full.py — 2026-10 12-18万 中型SUV 跨能源【完整正式榜】
在交互环境分引擎(build_energy_compare.py)之上，接入本榜候选池(含18款补采分)，
并注入正式榜的 18 区块导航(cross_energy_sections.py)，产出单文件交互 HTML。

环境分引擎不重写——import build_energy_compare 的常量/公式，保证单一事实源。
本脚本只负责：①构造 cars 数据；②算默认画像三榜；③生成区块；④调 EC.build 注入。
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_energy_compare as EC
import cross_energy_sections as CES
import energy_router as ER

RUNS = os.path.join(HERE, "runs")
OUT = os.path.join(RUNS, "midsuv_202610_12-18万_完整交互榜.html")
QUERY = "2026年10月 12-18万 中型SUV 前十排行榜"


def norm(s): return "".join(ch for ch in str(s).lower() if ch.isalnum())


def load_context():
    cands = json.load(open(os.path.join(RUNS,"midsuv_202610_candidates.json"),encoding="utf-8"))["cars"]
    scores = {}
    for i in (1,2,3):
        d = json.load(open(os.path.join(RUNS,f"midsuv_202610_scores_batch{i}.json"),encoding="utf-8"))
        for c in d["cars"]: scores[norm(c["model"])] = c
    mp = {norm(c["model"]):c for c in json.load(open(os.path.join(HERE,"categories","master_pool.json"),encoding="utf-8"))["cars"]}
    trims = {}
    for e in json.load(open(os.path.join(HERE,"categories","trims.json"),encoding="utf-8"))["entries"]:
        if e.get("model"): trims.setdefault(norm(e["model"]), e.get("trims",[]))
    vp = {norm(c["model"]):c for c in json.load(open(os.path.join(HERE,"categories","vehicle_physics.json"),encoding="utf-8"))["cars"]}
    fl = json.load(open(os.path.join(HERE,"categories","fact_library.json"),encoding="utf-8"))
    fl_by = {}
    for k,v in fl.items():
        if isinstance(v,dict): fl_by.setdefault(norm(k), v)
    return cands, scores, mp, trims, vp, fl_by


def lookup(src, key):
    if key in src: return src[key]
    hits = [v for k,v in src.items() if k.endswith(key) and len(k)>len(key)]
    return hits[0] if len(hits)==1 else None


def build_cars(cands, scores, mp, trims, vp, fl_by):
    route = ER.classify(QUERY)
    allowed = set(route["powers"])
    cars, meta = [], {"trims":{}, "rationale":{}}
    for c in cands:
        if c.get("power") not in allowed: continue
        k = norm(c["model"])
        sup = lookup(scores,k) or {}
        pool = lookup(mp,k) or {}
        sc = sup.get("scores") or pool.get("scores") or {}
        if not all(isinstance(sc.get(x),(int,float)) for x in EC.WEIGHT_MAP): continue
        product = round(sum(float(sc[x])*EC.WEIGHT_MAP[x] for x in EC.WEIGHT_MAP)*10, 1)
        # 逐配置（补采优先，否则配置库）
        ts = sup.get("trims") or trims.get(k, []) or []
        meta["trims"][c["model"]] = ts
        meta["rationale"][c["model"]] = sup.get("rationale") or {}
        # 电量/CLTC
        kwh = cltc = None
        vals_k = [float(t["battery_kwh"]) for t in ts if t.get("battery_kwh")]
        vals_c = [float(t["cltc_km"]) for t in ts if t.get("cltc_km")]
        if vals_k: kwh = max(vals_k)
        if vals_c: cltc = int(max(vals_c))
        fe = fl_by.get(k, {})
        cons = None
        if fe.get("consumption"):
            try: cons = float(fe["consumption"])
            except: pass
        pv = lookup(vp,k) or {}
        if kwh is None and pv.get("kwh"): kwh = float(pv["kwh"])
        if cltc is None and pv.get("cltc"): cltc = int(pv["cltc"])
        weight = pv.get("weight_kg"); cd = pv.get("cd")
        W,H = pv.get("width_mm"), pv.get("height_mm")
        area = round((W/1000)*(H/1000)*EC.AREA_FACTOR,2) if (W and H) else None
        voltage = pv.get("voltage_v")
        swap = pv.get("swap")
        cons_src = "事实库" if cons else None
        if cons is None and weight and cd and area:
            cons = round(EC.PHYS_A*(weight/1000.0)+EC.PHYS_B*cd*area+EC.PHYS_C,1)
            cons_src = "物理估算"
        if cons is None:
            cons = 13.0 if c.get("power")=="BEV" else 8.0
            cons_src = "车身经验"
        cda = round(cd*area/(weight/1000.0),3) if (cd and area and weight) else None
        price = c["price_guide"][0] if c.get("price_guide") else None
        cars.append({
            "model":c["model"], "cls":c["cls"], "body":"SUV", "power":c["power"],
            "price":price, "priceLow":c["price_guide"][0] if c.get("price_guide") else None,
            "priceHigh":c["price_guide"][1] if c.get("price_guide") and len(c["price_guide"])>1 else None,
            "product":product, "cltc":cltc, "kwh":round(kwh,1) if kwh else None,
            "cons":round(cons,1), "consSrc":cons_src, "weight":weight, "cd":cd,
            "area":area, "voltage":voltage, "swap":swap, "cdaPerTon":cda, "boxy":False,
            "sales":c.get("sales_202610"),
        })
    cars.sort(key=lambda x:-x["product"])
    return cars, meta, route


def main():
    cands, scores, mp, trims, vp, fl_by = load_context()
    cars, meta, route = build_cars(cands, scores, mp, trims, vp, fl_by)
    print(f"榜型自动判定: {route['board_type']}类 {ER.powers_cn(route['powers'])}")
    print(f"入池 {len(cars)} 款车")
    nav, secs = CES.sections_html(cars, {
        "dims": EC.DIMS,
        "trims": meta["trims"],
        "rationale": meta["rationale"],
    })
    EC.build(
        cars_override=cars,
        out=OUT,
        extra_head=nav,
        extra_tail=secs + "\n" + CES.dynamic_script(),  # 注入区块 + 定稿/推演分离脚本
        title="2026年10月 12-18万 中型SUV 跨能源正式榜（产品力+环境分）",
        def_low=12, def_high=18, def_body="SUV",  # 本榜口径：12–18万中型SUV（避免默认轿车12-14万导致空表）
    )
    # 校验
    html = open(OUT,encoding="utf-8").read()
    assert "boardNav" in html and "14 区块" in html, "导航缺失"
    for bid,_ in CES.BLOCKS:
        assert f'id="sec-{bid}"' in html, f"区块缺失 {bid}"
    assert "__EXTRA_" not in html, "占位符未替换"
    print(f"校验通过：14区块齐全 + 交互引擎内嵌。文件 {OUT}")


if __name__ == "__main__":
    main()
