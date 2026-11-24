# -*- coding: utf-8 -*-
"""build_ice_scores.py —— 燃油/油混/插混/增程（以及缺三电的纯电）自动打分落盘。

一条命令完成：车系名 → 汽车之家事实（ah_series）→ 参数驱动 8 维（param_score）
→ 分数 JSON（board 可直接吃）。带缓存、幂等、可断点续跑。

为什么不做成「一次性补采」
--------------------------
用户 2026-11-24 明确要求：这类分「从代码上就要实现，不要完成一次问一次」。
所以本脚本：
  - 候选名单来自 `categories/compact_sedan_universe.json`（人工维护的曝光车系名单，**只列车名**）；
  - 级别/能源/价格/参数/口碑/PPH 全部来自官方页面解析，**不写死任何分数**；
  - 每次跑都用同一公式重算，公式改了全池同步重算（不是补一次就烂在 JSON 里）；
  - 抓取失败/维数不足的车进 `gaps` 清单（原因写清），不硬凑分。

输出 schema（与 runs/mixed_*_scores_*.json 对齐，便于 build_compact_sedan 合并）
----------------------------------------------------------------------------
{"as_of","source","band","cars":[{model,power,cls,body,price_guide,price_low,price_high,
 sales,scores(8维),product,coverage,evidence,explain,sources,trims,koubei,pph,specs_count}],
 "gaps":[{model,reason}], "stats":{...}}

用法
----
    python3 build_ice_scores.py --universe categories/compact_sedan_universe.json \
        --cls 紧凑型车 --band 6 16 --out runs/compact_sedan_ice_scores_202611.json
    python3 build_ice_scores.py --names 轩逸 朗逸 速腾 --cls 紧凑型车
    python3 build_ice_scores.py --fill-bev --names 领汇e5 AION S --out runs/compact_sedan_bev_patch.json
    python3 build_ice_scores.py --selftest           # 零网络（用缓存夹具）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)

from modules.research import ah_series as AH          # noqa: E402
from modules.engine import param_score as PS          # noqa: E402

RUNS = os.path.join(HERE, "runs")
SALES_SNAP = os.path.join(RUNS, "dcd_sales_snapshot_202611.json")
UNIVERSE = str(paths.cat("compact_sedan_universe.json"))
AS_OF = "202611"
SEDAN_BODY = ("三厢车", "两厢车", "旅行车", "掀背车")  # 掀背（凌渡/UNI-V 类）仍属轿车


def norm(s):
    return re.sub(r"[\s\-_·（）()＋+／/.]", "", str(s or "")).lower()


def load_sales():
    """懂车帝逐月榜 → 车系名归一化 → 最新月全动力销量（车系口径，含所有能源形式）。"""
    out = {}
    if not os.path.exists(SALES_SNAP):
        return out
    with open(SALES_SNAP, encoding="utf-8") as f:
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
    """参数页款型 → board 的 trims[]（只放确定字段；缺的不填）。"""
    ts = []
    for s in sorted(specs, key=lambda x: (x.get("price") or 1e9))[:limit]:
        f = s.get("fields") or {}
        ts.append({
            "name": s.get("name"), "price": s.get("price"),
            "battery_kwh": AH._num(AH.field(s, AH.ID["battery_kwh"])),
            "cltc_km": AH._num(AH.field(s, AH.ID["cltc"])),
            "fuel_l100": AH._num(AH.field(s, AH.ID["fuel_wltc"])),
            "fuel_deprived_l100": AH._num(AH.field(s, AH.ID["fuel_deprived"])),
            "zero100_s": AH._num(AH.field(s, AH.ID["zero100"])),
        })
    return ts


def car_from_series(rec, group, band, sales):
    """一个（车系 × 能源形式）→ board 车条目；不合格返回 (None, 原因)。"""
    pmin, pmax = group.get("price_min"), group.get("price_max")
    if pmin is None:
        return None, "无指导价"
    if not (band[0] <= pmin <= band[1]):
        return None, "指导价低配 %.2f 万在 %s 万带外" % (pmin, band)
    body = group.get("body") or ""
    if not any(b in body for b in SEDAN_BODY):
        return None, "车身结构「%s」不是轿车" % body
    power = group.get("power")
    if power in (None, "UNKNOWN", "FCEV"):
        return None, "能源类型缺失/不支持：%s" % power
    if power == "BEV":
        # 纯电走「三电池 + 纯电 7 维」管线，本管线只负责燃油/油混/插混/增程，避免两套口径混用
        return None, "纯电由三电池评分管线负责，此处不重复打分"

    m = group.get("main") or {}          # 主力款（价格中位）自己的参数，保证功率/车重/价格同车配对
    facts = {
        "power": power, "price_min": pmin,
        "price_ref": m.get("price") or pmin,
        "curb_kg": m.get("curb_kg") or group.get("curb_kg"),
        "system_kw": m.get("system_kw"),
        "max_power_kw": m.get("system_kw") or group.get("engine_kw") or group.get("elec_kw"),
        "engine_ps": group.get("engine_ps"), "elec_ps": group.get("elec_ps"),
        "fuel_wltc": m.get("fuel_wltc") if m else group.get("fuel_wltc"),
        "fuel_deprived": m.get("fuel_deprived") if m else group.get("fuel_deprived"),
        "cltc": m.get("cltc") if m else group.get("cltc"),
        "zero100": m.get("zero100") or group.get("zero100"),
        "wheelbase": m.get("wheelbase") or group.get("wheelbase"),
        "trunk_l": m.get("trunk_l") or group.get("trunk_l"),
        "koubei": rec.get("koubei"), "pph": rec.get("pph"),
    }
    sc = PS.score_facts(facts)
    if not sc:
        return None, "可用维 < %d（参数/口碑不足，证据不足）" % PS.MIN_DIMS
    model = rec.get("official_name") or rec.get("name")
    # v10.58.0：款型按动力形式拆开再取油耗/续航（否则同车系的油混与燃油拿到同一批款型，
    # TCO 里油混的低油耗被抹平）。规则唯一来源 = ah_series.spec_power（能源类型 > 款型名关键词），
    # 不只对这几款车生效——任何车型走同一条规则。
    p_specs, trims_src = AH.specs_for_power(rec.get("specs") or [], power)
    return {
        "model": model, "power": power, "cls": rec.get("level") or "",
        "body": "轿车", "price_guide": [pmin, pmax],
        "price_low": pmin, "price_high": pmax,
        "sales": sales_of(rec.get("name"), model, sales),
        "scores": sc["scores"], "product": sc["product"], "coverage": sc["coverage"],
        "evidence": sc["evidence"], "explain": sc["explain"],
        "trims": trims_of(p_specs, 14),
        "trims_src": trims_src, "trims_n": len(p_specs),
        "koubei": rec.get("koubei"), "pph": rec.get("pph"),
        "specs_count": len(rec.get("specs") or []),
        "sources": {"series": AH.SERIES_URL.format(sid=rec.get("sid")),
                    "config": AH.CONFIG_URL.format(sid=rec.get("sid"))},
        "fetched_at": rec.get("fetched_at"),
    }, None


def run(names, cls, band, refresh=False, universe_names=None):
    sales = load_sales()
    cars, gaps, seen = [], [], set()
    for name in names:
        rec = AH.fetch_series(name, refresh=refresh)
        if not rec.get("ok"):
            gaps.append({"model": name, "reason": rec.get("error") or "fetch 失败"})
            continue
        if cls and rec.get("level") != cls:
            gaps.append({"model": rec.get("official_name") or name,
                         "reason": "官方级别为「%s」，不是「%s」" % (rec.get("level"), cls)})
            continue
        groups = (rec.get("agg") or {}).get("groups") or {}
        for p, g in groups.items():
            car, why = car_from_series(rec, g, band, sales)
            if not car:
                gaps.append({"model": "%s(%s)" % (rec.get("official_name") or name, p), "reason": why})
                continue
            key = (norm(car["model"]), car["power"])
            if key in seen:          # 同车系同能源只留一条（幂等）
                continue
            seen.add(key)
            cars.append(car)
    cars.sort(key=lambda x: -x["product"])
    return cars, gaps


def to_doc(cars, gaps, cls, band, mode="ice"):
    return {
        "as_of": AS_OF,
        "source": "汽车之家车系页(官方级别/能源类型/口碑聚合/PPH) + 参数页(var config) → 参数驱动 8 维",
        "scorer": "modules/engine/param_score.py（绝版锚点，改锚点需同步 selftest）",
        "mode": mode, "cls": cls, "band": list(band),
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "cars": cars, "gaps": gaps,
        "stats": {"n": len(cars), "by_power": {
            p: sum(1 for c in cars if c["power"] == p) for p in ("ICE", "HEV", "PHEV", "EREV", "BEV")},
            "coverage": {c["model"] + "/" + c["power"]: c.get("coverage_n", len(c["coverage"])) for c in cars}},
    }


def fill_bev(names, band=(6, 16), refresh=False):
    """纯电补三电：车系参数页的电池能量/CLTC/指导价 → patch（只补缺，不覆盖已核验值）。"""
    patch, gaps = [], []
    for name in names:
        rec = AH.fetch_series(name, refresh=refresh)
        if not rec.get("ok"):
            gaps.append({"model": name, "reason": rec.get("error") or "fetch 失败"})
            continue
        g = ((rec.get("agg") or {}).get("groups") or {}).get("BEV")
        if not g or not g.get("battery_kwh") or not g.get("cltc"):
            gaps.append({"model": name, "reason": "参数页无电池能量/CLTC（该车系可能已停产或无纯电款）"})
            continue
        patch.append({
            "model": rec.get("official_name") or name, "power": "BEV",
            "kwh": g["battery_kwh"], "cltc": int(g["cltc"]),
            "price_guide": [g.get("price_min"), g.get("price_max")],
            "curb_kg": g.get("curb_kg"), "len": g.get("len"), "wid": g.get("wid"), "hgt": g.get("hgt"),
            "trims": trims_of(AH.specs_for_power(rec.get("specs") or [], "BEV")[0], 14),
            "sources": {"config": AH.CONFIG_URL.format(sid=rec.get("sid"))},
        })
    return patch, gaps


def selftest():
    """零网络：用已缓存的轩逸/速腾事实走完整链路，断言分数可复现。"""
    ok = 0
    for name in ("轩逸", "速腾"):
        sid = AH.resolve_sid(name)
        cache = os.path.join(AH.CACHE_DIR, "%s.json" % sid) if sid else None
        if not cache or not os.path.exists(cache):
            continue
        with open(cache, encoding="utf-8") as f:
            rec = json.load(f)
        assert rec.get("ok"), "%s 缓存不可用" % name
        assert rec.get("level") == "紧凑型车"
        groups = (rec.get("agg") or {}).get("groups") or {}
        car, why = car_from_series(rec, groups.get("ICE") or {}, (6, 16), load_sales())
        assert car, "%s 未产出：%s" % (name, why)
        again, _ = car_from_series(rec, groups.get("ICE") or {}, (6, 16), load_sales())
        assert again["product"] == car["product"], "同一事实必须产出同一分（可复现）"
        assert 40 <= car["product"] <= 95, car["product"]
        assert car["coverage"] and "safety" not in car["scores"], "无碰撞判档不得有安全分"
        ok += 1
    assert ok >= 1, "缺少缓存夹具：先跑一次 --names 轩逸 速腾 再 selftest"
    print("[build_ice_scores selftest] PASS · 车系事实→8维→可复现（%d 款缓存）" % ok)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", help="车系名单 JSON（{\"names\":[...]}）")
    ap.add_argument("--names", nargs="*")
    ap.add_argument("--cls", default="紧凑型车")
    ap.add_argument("--band", nargs=2, type=float, default=[6.0, 16.0])
    ap.add_argument("--out")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--fill-bev", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return
    names = list(a.names or [])
    if a.universe:
        with open(a.universe, encoding="utf-8") as f:
            names = list(json.load(f).get("names") or [])
    if not names:
        ap.error("--universe 或 --names 必给")
    if a.fill_bev:
        patch, gaps = fill_bev(names, tuple(a.band), a.refresh)
        doc = {"as_of": AS_OF, "mode": "bev_patch", "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
               "cars": patch, "gaps": gaps, "source": "汽车之家参数页（电池能量/CLTC）"}
    else:
        cars, gaps = run(names, a.cls, tuple(a.band), a.refresh)
        doc = to_doc(cars, gaps, a.cls, tuple(a.band))
        print("产出 %d 款：%s" % (len(cars), doc["stats"]["by_power"]))
        for g in gaps[:12]:
            print("  gap:", g["model"], "-", g["reason"])
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        print("wrote", a.out)


if __name__ == "__main__":
    main()
