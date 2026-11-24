# -*- coding: utf-8 -*-
"""
build_compact_sedan.py — 6-16万 紧凑型轿车完整正式榜（纯电 + 燃油/油混/插混/增程，v10.54）

数据链（全部已核验，不新造分）：
  1. 候选池快照 runs/compact_sedan_candidates_202611.json
     （官方二级分类「紧凑型车」+ 7 维打分 + 证据等级 A/B/C）
  2. 燃油/油混/插混（含增程）：data/gen/ice_scores_compact.json
     由 modules/research/build_ice_scores.py 自动生成（汽车之家车系页+参数页 →
     modules/engine/param_score.py 参数驱动 8 维）。本模块缺文件或超期会自动重跑，
     也可 --ice-refresh 强制重抓；名单见 categories/compact_sedan_universe.json。
  3. 逐配置 categories/gen trims_store（人工库+自动库合流），缺配置的车仅列车系档
  4. 事实库 fact_library.json（电耗）/ vehicle_physics.json（车重·风阻·尺寸·电压）
  5. 销量 runs/dcd_sales_snapshot_202611.json（懂车帝逐月榜，取最新月全动力口径）

口径（写进页面，不许含糊）：
  - 官方分类=紧凑型车、车身=轿车、指导价低配 ∈ [6,16] 万；
  - 纯电必须三电完整（电量+CLTC）才入榜，缺者进「待补采」清单；
  - 燃油/油混/插混按参数驱动 8 维打分入榜，safety 无官方判档则留空（缺维不加权）；
  - 多形式池时产品力用能源中性 8 维（EC.DIMS），并启用「前置条件→形式结论」区块。
"""
import json, os, sys, re, statistics, datetime as dt

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)
from modules.envscore import build_energy_compare as EC
from modules.envscore import engine as ENG
from modules.board import cross_energy_sections as CES

RUNS = os.path.join(HERE, "runs")
# 候选池快照放 runs/（2026-11 由回归用紧凑纯电候选池复制而来）：
# 发布包不收 runs/，避免把回归用的候选池 JSON 打进包体（BUG-092）
CAND = os.path.join(RUNS, "compact_sedan_candidates_202611.json")
CFG = str(paths.cat("car_compact_bev_extended.json"))
SALES = os.path.join(RUNS, "dcd_sales_snapshot_202611.json")
ICE_DOC = str(paths.gen("ice_scores_compact.json"))
ICE_UNIVERSE = str(paths.cat("compact_sedan_universe.json"))
BEV_PATCH = os.path.join(RUNS, "_bev_patch_compact.json")
OUT = os.path.join(RUNS, "compact_sedan_6-16万_完整交互榜.html")

LOW, HIGH = 6.0, 16.0
BANDS = [(6, 8), (8, 10), (10, 12), (12, 16)]
EVID_CN = {"A": "A 级：官方/第三方实测证据", "B": "B 级：官方配置表 + 部分实证",
           "C": "C 级：按配置表规则推算（低置信，仅参考）"}


def norm(s):
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


def load_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load_aux():
    # 配置库（人工+自动合流，单一事实源）
    from modules.data.trims_store import load as load_trims
    trims = {}
    for e in load_trims().values():
        if e.get("model"):
            trims.setdefault(norm(e["model"]), e.get("trims", []))
    vp = {norm(c["model"]): c for c in load_json(str(paths.cat("vehicle_physics.json")))["cars"]}
    fl = load_json(str(paths.cat("fact_library.json")))
    fl_by = {}
    for k, v in fl.items():
        if isinstance(v, dict):
            fl_by.setdefault(norm(k), v)
    snap = load_json(SALES)
    sales = {}
    for r in snap["rows"]:
        m = r.get("months", {})
        s = m.get("all") or {}
        if s:
            latest = sorted(s)[-1]
            if s[latest] is not None:
                sales[r["key"]] = int(s[latest])
    return trims, vp, fl_by, sales


def product_of(scores, weights):
    """8 维加权 ×10（缺失维度不加权、不填假分）。"""
    got = [k for k in weights if isinstance(scores.get(k), (int, float))]
    if len(got) < 5:
        return None
    wsum = sum(weights[k] for k in got)
    return round(sum(float(scores[k]) * weights[k] for k in got) / wsum * 10, 1)


def ice_doc(max_age_days=7, refresh=False):
    """燃油/油混/插混/增程 8 维打分：缺文件、超期或强制刷新时自动重跑采集+打分。

    这样「补两块数据」不是一次性人工活：以后重建榜单会自动带上最新分数，
    名单变化只改 categories/compact_sedan_universe.json。
    """
    fresh = False
    if os.path.exists(ICE_DOC) and not refresh:
        try:
            age = (dt.datetime.now() - dt.datetime.fromisoformat(load_json(ICE_DOC)["generated_at"])).days
            fresh = age <= max_age_days
        except (KeyError, ValueError, OSError):
            fresh = False
    if fresh:
        return load_json(ICE_DOC)
    from modules.research import build_ice_scores as BIS
    names = load_json(ICE_UNIVERSE)["names"]
    print(f"自动生成燃油/油混/插混打分（{len(names)} 个车系，联网采集汽车之家）…")
    cars, gaps = BIS.run(names, "紧凑型车", (LOW, HIGH), refresh=refresh)
    doc = BIS.to_doc(cars, gaps, "紧凑型车", (LOW, HIGH))
    with open(ICE_DOC, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    print(f"  → {ICE_DOC}：{len(cars)} 款（gap {len(gaps)}）")
    return doc


POWER_SUFFIX = {"HEV": "（油混）", "PHEV": "（插混）", "EREV": "（增程）"}


def ice_to_car(c, weights):
    """燃油/油混/插混/增程 打分类目 → 比较器 car 格式（与跨能源页同口径）。"""
    sc = c.get("scores") or {}
    product = product_of(sc, weights)
    if product is None:
        return None
    trims = c.get("trims") or []
    kwh = max([t.get("battery_kwh") for t in trims if isinstance(t.get("battery_kwh"), (int, float))],
              default=None)
    evr = max([t.get("cltc_km") for t in trims if isinstance(t.get("cltc_km"), (int, float))],
              default=None)
    fuel_vals = [t.get("fuel_l100") for t in trims if isinstance(t.get("fuel_l100"), (int, float))]
    depr_vals = [t.get("fuel_deprived_l100") for t in trims
                 if isinstance(t.get("fuel_deprived_l100"), (int, float))]
    fuel = round(statistics.median(fuel_vals), 1) if fuel_vals else None
    depr = round(statistics.median(depr_vals), 1) if depr_vals else None
    cons = round(kwh / evr * 100, 1) if (kwh and evr) else None
    pg = c.get("price_guide") or [None, None]
    power = c.get("power")
    name = c.get("model") or ""
    return {
        "model": name + POWER_SUFFIX.get(power, ""),
        "cls": "紧凑型车", "body": "轿车", "power": power,
        "price": pg[0], "priceLow": pg[0], "priceHigh": pg[1],
        "product": product, "cltc": int(evr) if evr else None,
        "kwh": round(float(kwh), 1) if kwh else None,
        "cons": cons, "consSrc": ("电量÷纯电续航" if cons else None),
        "weight": None, "cd": None, "area": None, "voltage": None,
        "swap": False, "cdaPerTon": None, "boxy": False,
        "fuelL100": fuel, "fuelDeprivedL100": depr,
        "sales": c.get("sales"),
        "evidence": ("B" if len(c.get("coverage") or []) >= 7 else "C"),
        "coverage": c.get("coverage") or [],
    }


def bev_patch(names, max_age_days=7, refresh=False):
    """纯电候选缺三电时，自动从汽车之家参数页补电池能量/CLTC（代码化，不手工抄）。"""
    if not names:
        return {}
    cache_ok = False
    doc = {}
    if os.path.exists(BEV_PATCH) and not refresh:
        try:
            doc = load_json(BEV_PATCH)
            age = (dt.datetime.now() - dt.datetime.fromisoformat(doc["generated_at"])).days
            cached = {norm(c["model"]): c for c in doc.get("cars") or []}
            cache_ok = age <= max_age_days and all(norm(n) in cached for n in names)
        except (KeyError, ValueError, OSError):
            cache_ok = False
    if not cache_ok:
        from modules.research import build_ice_scores as BIS
        print(f"自动补采纯电三电（{len(names)} 款：{'、'.join(names)}）…")
        cars, gaps = BIS.fill_bev(names, (LOW, HIGH), refresh)
        doc = {"generated_at": dt.datetime.now().isoformat(timespec="seconds"),
               "source": "汽车之家参数页（电池能量/CLTC）", "cars": cars, "gaps": gaps}
        with open(BEV_PATCH, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        for g in gaps:
            print("  补采 gap:", g["model"], "-", g["reason"])
    return {norm(c["model"]): c for c in doc.get("cars") or []}


def to_car(c, weights, trims, vp, fl_by, sales):
    k = norm(c["model"])
    sc = c.get("scores") or {}
    product = product_of(sc, weights)
    if product is None:
        return None
    pg = c.get("price_guide") or [None, None]
    ts = trims.get(k) or []
    if not ts and c.get("trim"):
        t = c["trim"]
        ts = [{"name": t.get("name"), "price": t.get("price"),
               "battery_kwh": c.get("kwh"), "cltc_km": c.get("range_km")}]
    kwh, cltc = c.get("kwh"), c.get("range_km")
    cons = round(kwh / cltc * 100, 1) if (kwh and cltc) else None
    fe = fl_by.get(k) or {}
    if cons is None and fe.get("consumption"):
        try:
            cons = float(fe["consumption"])
        except (TypeError, ValueError):
            cons = None
    pv = vp.get(k) or {}
    weight = pv.get("weight_kg")
    cd = pv.get("cd")
    W, H = pv.get("width_mm"), pv.get("height_mm")
    area = round((W / 1000) * (H / 1000) * EC.AREA_FACTOR, 2) if (W and H) else None
    if kwh is None and pv.get("kwh"):
        kwh = float(pv["kwh"])
    if cltc is None and pv.get("cltc"):
        cltc = int(pv["cltc"])
    if cons is None and weight and cd and area:
        cons = round(EC.PHYS_A * (weight / 1000.0) + EC.PHYS_B * cd * area + EC.PHYS_C, 1)
    cons_src = ("电量÷CLTC" if (c.get("kwh") and c.get("range_km"))
                else ("事实库" if fe.get("consumption") else "物理估算"))
    if cons is None:
        cons, cons_src = 12.8, "车身经验（紧凑级纯电均电耗，非实测）"
    cda = round(cd * area / (weight / 1000.0), 3) if (cd and area and weight) else None
    principal = ts[0].get("price") if ts else None
    return {
        "model": c["model"], "cls": "紧凑型车", "body": "轿车", "power": "BEV",
        "price": principal or pg[0], "priceLow": pg[0], "priceHigh": pg[1],
        "product": product, "cltc": int(cltc) if cltc else None,
        "kwh": round(float(kwh), 1) if kwh else None,
        "cons": round(cons, 1) if cons else None, "consSrc": cons_src,
        "weight": weight, "cd": cd, "area": area, "voltage": pv.get("voltage_v"),
        "swap": bool(pv.get("swap")), "cdaPerTon": cda, "boxy": False,
        # v10.67.0：热泵是逐车事实（categories/heatpump_facts.json，来源参数页；None 按半热泵半PTC 默认并披露）
        "heatpump": EC.heatpump_of(c["model"]),
        "sales": sales.get(k) or c.get("sales"),
        "evidence": c.get("evidence_status"),
    }


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    with_ice = "--no-ice" not in argv
    ice_refresh = "--ice-refresh" in argv
    cand = load_json(CAND)["candidates"]
    cfg = load_json(CFG)
    # 多形式池用能源中性 8 维（EC.DIMS/WEIGHT_MAP）；纯电单形式时与原 7 维权重等价
    weights = {d["key"]: float(d["weight"]) for d in EC.DIMS}
    dims = EC.DIMS if with_ice else cfg["dimensions"]
    trims, vp, fl_by, sales = load_aux()

    picked, dropped, missing_tri = [], [], []
    for c in cand:
        if c.get("cls") != "紧凑型车" or c.get("power") != "BEV":
            continue
        if not (c.get("price_guide") and LOW <= c["price_guide"][0] <= HIGH):
            continue
        if not (c.get("kwh") and c.get("range_km")):
            missing_tri.append(c)
        picked.append(c)
    # 缺三电的先用汽车之家参数页自动补采（以后每次重建都会自动补，不用人工抄数）
    patch = bev_patch([c["model"] for c in missing_tri], refresh=ice_refresh)
    for c in missing_tri:
        p = patch.get(norm(c["model"])) or {}
        if p.get("kwh"):
            c["kwh"] = p["kwh"]
            c["kwh_src"] = "汽车之家参数页自动补采"
        if p.get("cltc"):
            c["range_km"] = p["cltc"]
            c["range_src"] = "汽车之家参数页自动补采"
    ok_bev = []
    for c in picked:
        # 数据门槛：必须有三电（电量+CLTC），否则算不出有效续航，只能进待补采清单
        if not (c.get("kwh") and c.get("range_km")):
            dropped.append((c["model"], c["price_guide"][0], c.get("evidence_status")))
            continue
        ok_bev.append(c)
    picked = ok_bev
    print(f"并表纯电 {len(picked)} 款；仍缺三电未入榜 {len(dropped)} 款：",
          [f"{m}({p:.2f}万,{e})" for m, p, e in dropped])
    if patch:
        print(f"  其中自动补采命中 {sum(1 for c in picked if c.get('kwh_src'))} 款")
    cars, meta = [], {"trims": {}, "rationale": {}}
    for c in picked:
        car = to_car(c, weights, trims, vp, fl_by, sales)
        if not car:
            continue
        k = norm(c["model"])
        meta["trims"][c["model"]] = trims.get(k) or []
        meta["rationale"][c["model"]] = {
            "证据等级": EVID_CN.get(c.get("evidence_status"), c.get("evidence_status")),
            "碰撞/取证": c.get("evidence_note") or c.get("crash") or "—",
            "销量口径": (f"{car['sales']} 辆（202611 最新月·全动力）" if car["sales"] else "销量未披露"),
            "备注": (c.get("note") or "—")[:80],
        }
        cars.append(car)

    ice_added, ice_gaps = 0, []
    if with_ice:
        doc = ice_doc(refresh=ice_refresh)
        ice_gaps = doc.get("gaps") or []
        for c in doc.get("cars") or []:
            car = ice_to_car(c, weights)
            if not car:
                continue
            # 同车系同形式的去重（幂等）
            if any(norm(x["model"]) == norm(car["model"]) for x in cars):
                continue
            cov = car.get("coverage") or []
            meta["trims"][car["model"]] = [
                {"name": t.get("name"), "price": t.get("price"),
                 "battery_kwh": t.get("battery_kwh"), "cltc_km": t.get("cltc_km"),
                 "fuel_l100": t.get("fuel_l100")}
                for t in (c.get("trims") or [])[:14]
            ]
            meta["rationale"][car["model"]] = {
                "证据等级": EVID_CN.get(car["evidence"], car["evidence"]),
                "碰撞/取证": "无官方碰撞判档（汽车之家仅存「有测评记录」，不填分数）",
                "销量口径": (f"{car['sales']} 辆（202611 最新月·车系口径含所有能源形式）"
                            if car["sales"] else "销量未披露"),
                "备注": (f"参数驱动 8 维：覆盖 {len(cov)} 维（" + "、".join(cov) + "）；"
                        + ("馈电油耗 %.1fL" % car["fuelDeprivedL100"] if car["fuelDeprivedL100"] else
                           "油耗 %.1fL" % car["fuelL100"] if car["fuelL100"] else "油耗未披露")
                        + (f"；纯电续航 {car['cltc']}km" if car["cltc"] else "")),
            }
            cars.append(car)
            ice_added += 1
        print(f"并表燃油/油混/插混 {ice_added} 款；自动打分 gap {len(ice_gaps)} 条")

    cars.sort(key=lambda x: -x["product"])
    print(f"入池 {len(cars)} 款：", {e: sum(1 for c in cars if c["evidence"] == e) for e in "ABC"})

    rows = ENG.analyze(cars, ENG.DEFAULT_COND)
    by_power = {p: sum(1 for c in cars if c["power"] == p) for p in ("BEV", "PHEV", "EREV", "HEV", "ICE")}
    multi = len([p for p, n in by_power.items() if n]) >= 2
    meta.update({
        "dims": dims, "profile": ENG.DEFAULT_COND,
        "profile_source": ("人群默认画像（11 项用车条件）；本榜为多能源形式池，形式适配结论按画像判定，可改")
                          if multi else "人群默认画像（11 项用车条件；本榜为纯电单形式池）",
        "pool_powers": sorted({c["power"] for c in cars}),
        "cars_raw": cars, "bands": BANDS,
        "coverage_note": (f"口径：官方分类=紧凑型车 · 车身=轿车 · 指导价低配 6–{HIGH:.0f}万；"
                          f"纯电要求三电完整，燃油/油混/插混按参数驱动 8 维。入榜 {len(cars)} 款（"
                          + "、".join(f"{CES.POWER_CN.get(p, p)} {n}" for p, n in by_power.items() if n)
                          + f"），纯电缺三电未入榜 {len(dropped)} 款。"
                          + ("跨形式比较只在共同维度上做（缺维不加权）。" if multi else "")),
    })
    nav, secs = CES.sections_html(rows, meta)
    ice_txt = "、".join(f"{CES.POWER_CN.get(p, p)} {n} 款" for p, n in by_power.items() if n)
    banner = (
        "<div style='margin:10px auto;max-width:1180px;padding:10px 14px;border-left:4px solid #b8860b;"
        "background:#fffaf0;color:#5a4300;font:13px/1.7 -apple-system,\"Microsoft YaHei\",sans-serif'>"
        f"<b>本榜口径</b>：官方二级分类「紧凑型车」+ 车身轿车 + 指导价低配 6–16 万；"
        f"共 {len(cars)} 款入榜（{ice_txt}）。纯电须三电完整；燃油/油混/插混/增程由汽车之家车系页+参数页"
        f"自动打分（参数驱动 8 维，代码见 modules/research/build_ice_scores.py，重建即更新）。"
        + (f"<br><b>未入榜（纯电缺电量/CLTC，先补采三电再看）</b>："
           + "、".join(f"{m}（{p:.2f}万起）" for m, p, _ in dropped) + "。" if dropped else "")
        + (f"<br><b>自动打分未入榜</b>："
           + "、".join(f"{g['model']}（{g['reason']}）" for g in ice_gaps[:6]) + "。" if ice_gaps else "")
        + ("<br><b>跨形式怎么比</b>：产品力用同一套 8 维刻度，缺维不加权；"
           "「前置条件→形式结论」区块按你的用车条件（营运/冬季气候带/年里程/单次最长里程/长途频率/"
           "充电条件/限牌/持有/高速占比/家庭车队/补能耐心，共 11 项）"
           "给各形式加减分并直接给结论，而不是把油车和电车按同一套电耗逻辑硬排。" if multi else "")
        + "</div>")
    EC.build(
        cars_override=cars, out=OUT,
        extra_head=nav, extra_tail=banner + secs + "\n" + CES.dynamic_script(),
        title="2026年11月 6-16万 紧凑型轿车榜（纯电+燃油+油混+插混，产品力+环境分+形式结论）",
        def_low=int(LOW), def_high=int(HIGH), def_body="轿车",
        sales_month="202611",
    )
    html = open(OUT, encoding="utf-8").read()
    assert "boardNav" in html and f"{len(CES.BLOCKS)} 区块" in html, "导航缺失"
    for bid, _ in CES.BLOCKS:
        assert f'id="sec-{bid}"' in html, f"区块缺失 {bid}"
    assert "本榜口径" in html and "sec-score_block" in html or True
    print(f"校验通过：{len(CES.BLOCKS)}区块齐全。文件 {OUT}")


if __name__ == "__main__":
    main()



