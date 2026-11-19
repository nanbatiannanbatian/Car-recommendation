#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐配置评分（trim_board.py）——把每个车型的具体配置按规则算 8 维分，供配置下拉对比。

为什么需要（v9.7.0）：
用户指出「总榜本质是配置排行不是车型排行」——MG07 610版(400V/清陶)与845版(800V/宁德)
是两台不同的车，却共用一个车系分。本模块用 spec_score 的规则给每个配置单独算分。

诚实边界（必须随数据展示）：
- trims.json 的配置字段只覆盖电池/电压/电芯/激光雷达等，缺轴距/悬架/芯片/TOPS 等维度，
  spec_score 对缺项给基线分并标 imputed——**插补维度不构成结论**。
- 每个配置分带 `n_imputed`，下拉框展示时必须显示「X 维插补」警告。
- 主榜排序仍用车系级 scores（唯一权威名次不变），配置分只用于下拉对比，**不改主榜名次**。

用法：
    from trim_board import build_trim_board
    data = build_trim_board(cands, cfg, main_rows)
    # data = {车型: {scored_trim, best_trim, trims:[{name,price,scores,total,n_imputed,...}]}}
    python3 trim_board.py --selftest
"""
from __future__ import annotations
import argparse
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
TRIMS = os.path.join(HERE, "categories", "trims.json")


def load_trims(path: str | None = None) -> dict[str, dict]:
    p = path or TRIMS
    if not os.path.exists(p):
        return {}
    d = json.load(open(p, encoding="utf-8"))
    return {e["model"]: e for e in d.get("entries", []) if e.get("model")}


def spec_from_trim(trim: dict, base_spec: dict | None = None) -> dict:
    """把 trims.json 的一个配置 → spec_score 入参。缺项留空让 spec_score 插补。

    v9.7.1 修复：v9.7.0 只映射了 kwh/电压/激光雷达，漏了 cltc_km(→range_km)、
    cell_supplier(→cell_brand)、charge里的10-80分钟(→charge_10_80_min)，
    导致所有配置三电都是基线 6.0、只剩 value 随价格降 → "最高分=最低配"的假象。
    """
    s = dict(base_spec or {})
    s["kwh"] = trim.get("battery_kwh") or s.get("kwh")
    charge = str(trim.get("charge") or "")
    s["platform_800v"] = ("800V" in charge) or ("900V" in charge) or s.get("platform_800v", False)
    # 续航：配置表 cltc_km → spec_score range_km
    if trim.get("cltc_km") is not None:
        s["range_km"] = trim["cltc_km"]
    # 电芯：cell_supplier → cell_brand（一线品牌识别用）
    if trim.get("cell_supplier"):
        s["cell_brand"] = trim["cell_supplier"]
    # 快充时间：从 "30–80% 约 20min" / "10-80% 12min" 抽分钟数
    import re
    m = re.search(r"(\d+)\s*[-–]\s*80%[^0-9]*(\d+)\s*min", charge)
    if m:
        s["charge_10_80_min"] = int(m.group(2))
    else:
        m2 = re.search(r"(\d+)\s*min", charge)
        if m2:
            s["charge_10_80_min"] = int(m2.group(1))
    kcs = [str(x) for x in (trim.get("key_configs") or [])]
    if any("激光雷达" in x for x in kcs):
        s["adas_lidar"] = True
    if any("城区" in x and "NOA" in x.upper() for x in kcs):
        s["adas_city_noa"] = True
    if any("高速" in x and "NOA" in x.upper() for x in kcs):
        s["adas_highway_noa"] = True
    return s


def score_trim(trim: dict, base_spec: dict | None, cfg: dict,
               peer_prices: list[float]) -> dict:
    """给单个配置算 8 维分 + 加权总分。"""
    import spec_score as SS
    spec = spec_from_trim(trim, base_spec)
    price = trim.get("price")
    dims, reasons, imputed = SS.score_car(spec, price, peer_prices)
    # 加权总分（与 leaderboard 口径一致：Σ score×weight×10）
    wmap = {d["key"]: float(d["weight"]) for d in cfg["dimensions"]}
    tw = sum(wmap.get(k, 0) for k in dims)
    total = None
    if all(isinstance(v, (int, float)) for v in dims.values()) and tw:
        total = round(sum(dims[k] * wmap.get(k, 0) for k in dims) / tw * 10, 1)
    return {
        "name": trim.get("name"), "price": price,
        "battery_kwh": trim.get("battery_kwh"), "cell_supplier": trim.get("cell_supplier"),
        "charge": trim.get("charge"), "cltc_km": trim.get("cltc_km"),
        "scores": dims, "total": total, "n_imputed": len(imputed),
        "imputed": imputed, "reasons": reasons,
        "note": trim.get("note", ""),
    }


def build_trim_board(cands: list[dict], cfg: dict, main_rows: list[dict] | None = None) -> dict:
    """给每个车的所有配置打分，标出该车型的最高分配置。不改主榜分数。"""
    tdb = load_trims()
    peer_prices = [float(c.get("trim", {}).get("price") or c.get("price_wan") or 0)
                   for c in cands if isinstance(c.get("trim"), dict)]
    peer_prices = [p for p in peer_prices if p > 0] or [15.0]
    out = {}
    for c in cands:
        model = c["model"]
        entry = tdb.get(model)
        if not entry or not entry.get("trims"):
            continue
        # 车系级公共参数（激光雷达/芯片等从候选池 note 无法可靠提取，留空插补）
        scored = [score_trim(t, None, cfg, peer_prices) for t in entry["trims"]]
        valid = [s for s in scored if s.get("total") is not None]
        best = max(valid, key=lambda s: s["total"]) if valid else None
        out[model] = {
            "scored_trim": entry.get("scored_trim"),
            "trim_class": entry.get("trim_class"),
            "n_trims": len(scored),
            "trims": scored,
            "best_trim": best["name"] if best else None,
            "best_total": best["total"] if best else None,
            "source": entry.get("url", ""),
            "date": entry.get("date", ""),
        }
    return out


def selftest() -> int:
    bad = []
    tdb = load_trims()
    if len(tdb) < 10:
        bad.append("配置库加载不足")
    cfg_path = os.path.join(HERE, "categories", "car_ev_1218_quality.json")
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    # 单车打分：MG07 610版 vs 845版应有不同三电分（400V vs 800V）
    e = tdb.get("MG 07 纯电", {})
    trims = {t["name"]: t for t in e.get("trims", [])}
    t610 = next((v for k, v in trims.items() if "610 激光" in k), None)
    t845 = next((v for k, v in trims.items() if "845" in k), None)
    if not (t610 and t845):
        bad.append("MG07 610/845 配置缺失")
    else:
        s610 = score_trim(t610, None, cfg, [12.69, 14.99])
        s845 = score_trim(t845, None, cfg, [12.69, 14.99])
        p610 = s610["scores"].get("powertrain", 0)
        p845 = s845["scores"].get("powertrain", 0)
        if not (p845 > p610):
            bad.append(f"845版三电分应高于610版: {p610} vs {p845}")
        if s610["name"] == s845["name"]:
            bad.append("两配置未区分")
    if bad:
        print("[selftest FAIL]", bad)
        return 1
    print(f"[selftest OK] 配置库 {len(tdb)} 款；MG07 845版三电分>610版；逐配置打分可用（含插补标注）")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="逐配置评分")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    raise SystemExit(selftest() if a.selftest else 0)
