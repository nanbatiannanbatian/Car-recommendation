# -*- coding: utf-8 -*-
"""
build_large_suv_bev.py — 2026-11 中大型纯电SUV【完整交互榜】（单一能源形式：纯电）

为什么单独建一个榜
------------------
项目原有三张榜是 6-16 万紧凑轿车、12-18 万中型SUV、12-14 万五形式演示榜，
「中大型纯电SUV」既不在价格带内、也不在级别内，混进任何一张榜都会破坏该榜口径。
本榜按同一条闸门链独立建池：官方二级分类=中大型SUV + 车系页有纯电分组。

数据链（不猜级别、不猜价格、不猜分数）
------------------------------------
  1 名单：categories/universe_large_suv_bev.json（只写车名，人工维护曝光清单）
  2 级别/价格/三电/参数：汽车之家车系页 + 参数页（modules/research/ah_series，带缓存）
  3 8 维分：modules/engine/param_score.score_facts 参数驱动（口径与紧凑榜的燃油侧同一函数）
  4 电耗：优先「车重+风阻+迎风面积」物理估算（EC.PHYS_*，项目既有公式），缺风阻才退回「电量÷CLTC」
  5 TCO/环境分：modules/envscore/engine（v10.60 费率带参数）+ 默认画像

诚实边界（必须随页面展示）
--------------------------
  · 安全/智驾两维没有官方碰撞判档与智驾实测 → 留空、不加权，不插补；
  · 分数是参数驱动的 C 级证据（按配置/参数规则推算，低置信，仅参考）；
  · 销量只作事实列，不进评价分（销量是发现器不是评价器）。
"""
import json, os, sys, re

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)
from modules.envscore import build_energy_compare as EC
from modules.envscore import engine as ENG
from modules.board import cross_energy_sections as CES
from modules.research import ah_series as AH
from modules.engine import param_score as PS
from modules.engine import spec_score as SS

RUNS = os.path.join(HERE, "runs")
UNIVERSE = str(paths.cat("universe_large_suv_bev.json"))
SALES = os.path.join(RUNS, "dcd_sales_snapshot_202611.json")
OUT = os.path.join(RUNS, "large_suv_bev_中大型纯电SUV_完整交互榜.html")
# --top N 只出池内前 N（默认全池）。用于"前十/前二十"这类切片，池与口径不变，只改展示范围。
TOP = None
POOL_N = None

CLS, BODY = "中大型SUV", "SUV"
LOW, HIGH = 15.0, 90.0
BANDS = [(15, 20), (20, 25), (25, 30), (30, 40), (40, 90)]
EVID_CN = {"A": "A 级：官方/第三方实测证据", "B": "B 级：官方配置表 + 部分实证",
           "C": "C 级：按配置表规则推算（低置信，仅参考）"}
# 参数页字段：1370 = 风阻系数 Cd（与 ah_series 参数页 pid 一致）
PID_CD = "1370"


def norm(s):
    return re.sub(r"[\s\-_·（）()＋+／/.]", "", str(s or "")).lower()


def load_sales():
    out = {}
    if not os.path.exists(SALES):
        return out
    with open(SALES, encoding="utf-8") as f:
        snap = json.load(f)
    for r in snap.get("rows") or []:
        s = (r.get("months") or {}).get("all") or {}
        if s:
            latest = sorted(s)[-1]
            if s[latest] is not None:
                out[r["key"]] = int(s[latest])
    return out


def sales_of(name, official, sales):
    for k in (norm(official), norm(name)):
        if k in sales:
            return sales[k]
    return None


def trims_of(specs, limit=14):
    ts = []
    for s in sorted(specs, key=lambda x: (x.get("price") or 1e9))[:limit]:
        ts.append({
            "name": s.get("name"), "price": s.get("price"),
            "battery_kwh": AH._num(AH.field(s, AH.ID["battery_kwh"])),
            "cltc_km": AH._num(AH.field(s, AH.ID["cltc"])),
        })
    return ts


def cd_of(specs):
    for s in specs or []:
        v = (s.get("fields") or {}).get(PID_CD)
        try:
            f = float(v)
            if 0.15 <= f <= 0.45:
                return f
        except (TypeError, ValueError):
            continue
    return None


def load_crash():
    """官方碰撞判档（0-10）：official_safety_scores.car_scores 优先，
    C-NCAP 得分率按官方映射 7.0+(pct-80)×0.155 封顶 9.3 兜底。
    两套数据都是仓库既有事实表，不在本模块里新造判档。"""
    out = {}
    try:
        d = json.load(open(os.path.join(HERE, "official_safety_scores.json"), encoding="utf-8"))
        for m, v in (d.get("car_scores") or {}).items():
            if isinstance(v, dict) and isinstance(v.get("score"), (int, float)):
                out[norm(m)] = {"score": float(v["score"]), "src": v.get("source") or v.get("system")}
    except (OSError, ValueError):
        pass
    try:
        d = json.load(open(os.path.join(HERE, "cncap_scores.json"), encoding="utf-8"))
        for m, pct in (d.get("scores") or {}).items():
            k = norm(m)
            if k in out or not isinstance(pct, (int, float)):
                continue
            g = min(9.3, 7.0 + (float(pct) - 80) * 0.155)
            out[k] = {"score": round(g, 2), "src": "C-NCAP %s%%（官方映射）" % pct}
    except (OSError, ValueError):
        pass
    return out


def main(argv=None):
    global TOP, POOL_N, OUT
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--top" in argv:
        TOP = int(argv[argv.index("--top") + 1])
        OUT = os.path.join(RUNS, "large_suv_bev_中大型纯电SUV_TOP%d_完整交互榜.html" % TOP)
    if not os.path.exists(UNIVERSE):
        print("缺少品类清单 %s。\n"
              "本榜与其它固定榜一样依赖开发树里的 JSON 事实库（categories/、data/gen/），"
              "发布包按「零 JSON」铁律不带数据，只带代码与文档；\n"
              "请在完整开发树里运行，或先用 data_bootstrap 补回事实库后再建榜。" % UNIVERSE)
        return 1
    uni = json.load(open(UNIVERSE, encoding="utf-8"))
    sales = load_sales()
    crash = load_crash()
    # v10.67.0：热泵逐车事实由 EC.heatpump_of() 统一提供（categories/heatpump_facts.json，未采到=None）
    cars, gaps, excluded = [], [], []
    for name in uni["names"]:
        try:
            rec = AH.fetch_series(name)
        except Exception as e:                      # 网络/解析异常不吞掉，进 gap
            gaps.append({"model": name, "reason": "fetch 异常：%s" % e})
            continue
        if not rec.get("ok"):
            gaps.append({"model": name, "reason": rec.get("error") or "fetch 失败"})
            continue
        official = rec.get("official_name") or name
        if rec.get("level") != CLS:
            excluded.append((official, rec.get("level"), name))
            continue
        g = ((rec.get("agg") or {}).get("groups") or {}).get("BEV") or {}
        pmin, pmax = g.get("price_min"), g.get("price_max")
        if pmin is None:
            gaps.append({"model": official, "reason": "车系页无纯电款（无 BEV 指导价）"})
            continue
        kwh, cltc = g.get("battery_kwh"), g.get("cltc")
        if kwh is None or cltc is None:
            gaps.append({"model": official, "reason": "参数页缺电池能量/CLTC（三电不完整）"})
            continue
        m = g.get("main") or {}
        bev_specs = AH.specs_for_power(rec.get("specs") or [], "BEV")[0]
        ts = trims_of(bev_specs)
        weight = m.get("curb_kg") or g.get("curb_kg")
        cd = cd_of(bev_specs)
        L, W_, H_ = g.get("len"), g.get("wid"), g.get("hgt")
        area = round((W_ / 1000) * (H_ / 1000) * EC.AREA_FACTOR, 2) if (W_ and H_) else None
        if weight and cd and area:
            cons = round(EC.PHYS_A * (weight / 1000.0) + EC.PHYS_B * cd * area + EC.PHYS_C, 1)
            cons_src = "物理估算（车重%.0fkg·Cd%.3f·迎风%.2f㎡）" % (weight, cd, area)
        else:
            cons = round(float(kwh) / float(cltc) * 100, 1)
            cons_src = "电量÷CLTC（无风阻/尺寸，偏乐观）"

        cg = crash.get(norm(official)) or crash.get(norm(name))
        facts = {"power": "BEV", "price_min": pmin,
                 "price_ref": m.get("price") or pmin,
                 "curb_kg": weight,
                 "max_power_kw": m.get("elec_kw") or g.get("elec_kw"),
                 "elec_kw": m.get("elec_kw") or g.get("elec_kw"),
                 "cltc": cltc, "zero100": m.get("zero100") or g.get("zero100"),
                 "wheelbase": m.get("wheelbase") or g.get("wheelbase"),
                 "trunk_l": m.get("trunk_l") or g.get("trunk_l"),
                 "koubei": rec.get("koubei"), "pph": rec.get("pph")}
        if cg:
            facts["crash_grade"] = cg["score"]
        sc = PS.score_facts(facts)
        if not sc:
            gaps.append({"model": official, "reason": "可用维 < %d（参数/口碑不足）" % PS.MIN_DIMS})
            continue
        scores = dict(sc["scores"])
        # 主榜三电软上限（v10.16.0 规则）：避免多款车并列 9.95 及以上，
        # 单调保序、不硬顶满分；与 modules/board/pipeline.py 同一函数。
        if isinstance(scores.get("powertrain"), (int, float)) and scores["powertrain"] > SS.MAIN_PT_KNEE:
            before = scores["powertrain"]
            scores["powertrain"] = SS.main_board_powertrain_cap(before)
            capped = (before, scores["powertrain"])
        else:
            capped = None
        got = [k for k in EC.WEIGHT_MAP if isinstance(scores.get(k), (int, float))]
        if len(got) < 5:
            gaps.append({"model": official, "reason": "可用维 %d < 5" % len(got)})
            continue
        wsum = sum(EC.WEIGHT_MAP[k] for k in got)
        product = round(sum(float(scores[k]) * EC.WEIGHT_MAP[k] for k in got) / wsum * 10, 1)
        car = {
            "model": official, "cls": CLS, "body": BODY, "power": "BEV",
            "price": pmin, "priceLow": pmin, "priceHigh": pmax,
            "product": product, "scores": scores,
            "cltc": int(cltc), "kwh": round(float(kwh), 1),
            "cons": cons, "consSrc": cons_src,
            "weight": weight, "cd": cd, "area": area,
            "voltage": None, "swap": None,
            "cdaPerTon": round(cd * area / (weight / 1000.0), 3) if (cd and area and weight) else None,
            "boxy": False, "sales": sales_of(name, official, sales),
            # v10.67.0：热泵逐车事实（heatpump_facts.json；None 时冬季按半热泵半PTC 默认并披露）
            "heatpump": EC.heatpump_of(official),
            "evidence": sc["evidence"], "coverage": sc["coverage"],
            "trims": ts, "trims_n": len(bev_specs),
            "sources": {"series": AH.SERIES_URL.format(sid=rec.get("sid")),
                        "config": AH.CONFIG_URL.format(sid=rec.get("sid"))},
            "pt_cap": capped,
            "crash_src": (cg["src"] if cg else None),
        }
        cars.append(car)

    cars.sort(key=lambda x: -x["product"])
    POOL_N = len(cars)
    if TOP:
        cars = cars[:TOP]
    print("入池 %d 款%s；级别不符排除 %d 款；gap %d 条"
          % (POOL_N, ("（按综合分取前 %d 展示）" % TOP) if TOP else "", len(excluded), len(gaps)))
    for e in excluded:
        print("  排除(级别=%s)：%s" % (e[1], e[0]))
    for g_ in gaps:
        print("  gap：%s - %s" % (g_["model"], g_["reason"]))
    print("  能源类型：", {c["power"]: sum(1 for x in cars if x["power"] == c["power"]) for c in cars})
    print("  分数区间 %.1f~%.1f；可用维中位 %d" % (cars[-1]["product"], cars[0]["product"],
                                                sorted(len(x["coverage"]) for x in cars)[len(cars) // 2]))

    rows = ENG.analyze(cars, ENG.DEFAULT_COND)
    meta = {
        "dims": EC.DIMS, "profile": ENG.DEFAULT_COND,
        "profile_source": "人群默认画像（11 项用车条件；本榜为纯电单形式池，形式结论只用于说明纯电适配）",
        "pool_powers": ["BEV"], "cars_raw": cars, "bands": BANDS,
        "trims": {c["model"]: c["trims"] for c in cars},
        "rationale": {c["model"]: {
            "证据等级": EVID_CN.get(c["evidence"], c["evidence"]),
            "碰撞/取证": (c["crash_src"] + "（映射 0-10 分 %.2f）" % crash.get(norm(c["model"]), {}).get("score", 0)) if c["crash_src"] else "无官方碰撞判档（不填分数）",
            "销量口径": ("%s 辆（202611 最新月·车系口径含所有能源形式）" % c["sales"]) if c["sales"] else "销量未披露",
            "备注": ("电耗 %s；可用维 %d/%d（%s）" % (c["consSrc"], len(c["coverage"]), len(EC.WEIGHT_MAP),
                                                  "、".join(c["coverage"]))
                     + ("；三电软上限 %.2f→%.2f" % c["pt_cap"] if c["pt_cap"] else "")),
        } for c in cars},
        "coverage_note": ("口径：官方二级分类=中大型SUV · 车系页含纯电分组 · 指导价低配 %.0f–%.0f 万；"
                          "共 %d 款入榜%s。8 维中安全/智驾无官方判档留空、不加权（缺维不加权、不插补）；"
                          "分数为参数驱动（C 级，仅参考）；销量只作事实列。"
                          % (LOW, HIGH, len(cars),
                             ("（全池 %d 款中的前 %d，池与口径不变）" % (POOL_N, TOP)) if TOP else "")),
    }
    nav, secs = CES.sections_html(rows, meta)
    banner = (
        "<div style='margin:10px auto;max-width:1180px;padding:10px 14px;border-left:4px solid #b8860b;"
        "background:#fffaf0;color:#5a4300;font:13px/1.7 -apple-system,\"Microsoft YaHei\",sans-serif'>"
        "<b>本榜口径</b>：官方二级分类「中大型SUV」+ 车系页有纯电款 + 指导价低配 15–90 万；"
        f"共 {len(cars)} 款入榜（纯电）"
        + (f"（全池 {POOL_N} 款按综合分取前 {TOP}，池与口径不变，完整榜见 large_suv_bev_中大型纯电SUV_完整交互榜.html）" if TOP else "")
        + "。级别由汽车之家官方二级分类逐款核验，"
        "大型SUV（ES8/M8/M9/N9/L90/HL/N8L/9X/S09/LS9）与中型SUV（ES6/L60/G7/7X/知音/宋L/海狮07）不入本口径。"
        "<br><b>分数是参数驱动（C 级）</b>：安全/智驾两维没有官方碰撞判档与实测，留空不加权；"
        "电耗优先按「车重+风阻+迎风面积」物理估算，缺风阻才退回电量÷CLTC（逐车标注）。"
        + (f"<br><b>级别不符已排除</b>：" + "、".join("%s（%s）" % (e[0], e[1]) for e in excluded) + "。" if excluded else "")
        + ("<br><b>按官方级别明确不入本口径</b>："
           + "、".join("%s（%s）" % (x["model"], x["level"]) for x in (uni.get("excluded_verified") or [])) + "。"
           if uni.get("excluded_verified") else "")
        + ("<br><b>平衡里程</b>：本榜全池纯电，没有「纯电 vs 燃油」的池内对象，摘要区平衡里程留 —；"
           "纯电相对燃油的成本平衡口径仍是约 1.5 万 km/年（区间 1.1–2.0 万，见方法区）。")
        + (f"<br><b>未入榜</b>：" + "、".join("%s（%s）" % (g_["model"], g_["reason"]) for g_ in gaps[:6]) + "。" if gaps else "")
        + "</div>")
    EC.build(cars_override=cars, out=OUT, extra_head=nav,
             extra_tail=banner + secs + "\n" + CES.dynamic_script(),
             title="2026年11月 中大型纯电SUV榜（产品力+环境分+TCO，参数驱动）",
             def_low=int(LOW), def_high=int(HIGH), def_body=BODY,
             sales_month="202611")
    html = open(OUT, encoding="utf-8").read()
    assert "boardNav" in html and f"{len(CES.BLOCKS)} 区块" in html, "导航缺失"
    for bid, _ in CES.BLOCKS:
        assert f'id="sec-{bid}"' in html, "区块缺失 %s" % bid
    print("校验通过：%d 区块齐全。文件 %s" % (len(CES.BLOCKS), OUT))


if __name__ == "__main__":
    main()
