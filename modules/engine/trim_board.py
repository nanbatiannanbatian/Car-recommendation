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
    from modules.engine.trim_board import build_trim_board
    data = build_trim_board(cands, cfg, main_rows)
    # data = {车型: {scored_trim, best_trim, trims:[{name,price,scores,total,n_imputed,...}]}}
    python3 trim_board.py --selftest
"""
from __future__ import annotations
import argparse
import json
import os
import re

import paths

HERE = str(paths.ROOT)
TRIMS = str(paths.cat("trims.json"))


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
    # 高压平台判定：三个来源任一成立即可——
    #   ① charge 文本里有 800V/900V（auto_trims 拼的、或人工写的）；
    #   ② voltage_v 独立字段 ≥ 800（汽车之家「高压平台(V)」，2026-11-21 新增）；
    #   ③ 调用方已给的值（沿用）。
    # 为什么要独立的 voltage_v：人工条目的 charge 是核验过的表述、不能被自动采集改写；
    # 而「官方没标电压」与「标了是 400V」必须分得清——否则规则打分会把没标的车当弱平台。
    vv = trim.get("voltage_v")
    s["platform_800v"] = (("800V" in charge) or ("900V" in charge)
                          or (isinstance(vv, (int, float)) and vv >= 800)
                          or bool(s.get("platform_800v")))
    # 续航：配置表 cltc_km → spec_score range_km
    if trim.get("cltc_km") is not None:
        s["range_km"] = trim["cltc_km"]
    # 电芯：cell_supplier → cell_brand（一线品牌识别用）
    if trim.get("cell_supplier"):
        s["cell_brand"] = trim["cell_supplier"]
    # 快充时间 → spec_score charge_10_80_min。
    # 口径统一走 trim_annotate.charge_minutes（**唯一口径**）：
    #   它按区间宽度把 10–80%(70%) / 30–80%(50%) / 10–70%(60%) 折算到等效 70% 区间。
    #   此前这里自己写了一套正则，直接取「…80% 后的分钟数」——
    #   启源Q06 的「30–80% 约 9min」被当成 10–80% 的 9min（虚高），
    #   而 trim_annotate 同一份数据折算成 12.6min。两套口径同题不同答 = 事实分叉。
    from modules.engine import trim_annotate as _TA
    _equiv, _why = _TA.charge_minutes(trim)
    if _equiv is not None:
        s["charge_10_80_min"] = _equiv
    # v10.12.1：官方 0-100km/h 加速（汽车之家参数页「基本参数」组 id=1250）。
    # 操控规则的零百项原先只能从各车取证文本正则提取，覆盖极低；
    # 现在有官方值就以官方值为准（官方值优先于媒体/车主口述）。
    if trim.get("zero_to_100_s") is not None:
        s["zero_to_100_s"] = trim["zero_to_100_s"]
    # v10.12.2（拍板接入空间维）：官方轴距 + 后备厢容积 + 电池冷却方式。
    # 轴距是「乘坐空间」的客观代理（比车长准：车长含前后悬，不是乘员舱长度）；
    # 后备厢容积是「装载空间」的客观指标——业内横评与大众购车认知都认它是空间的一部分。
    if trim.get("wheelbase_mm") is not None:
        s["wheelbase_mm"] = trim["wheelbase_mm"]
    if trim.get("trunk_l") is not None:
        s["trunk_l"] = trim["trunk_l"]
    if trim.get("battery_cooling") is not None:
        s["battery_cooling"] = trim["battery_cooling"]
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
    from modules.engine import spec_score as SS
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


def load_verified(path: str | None = None) -> dict[str, dict]:
    """加载人工标注的逐配置分（带置信度），优先于 spec_score 规则分。"""
    p = path or str(paths.cat("trim_verified.json"))
    if not os.path.exists(p):
        return {}
    d = json.load(open(p, encoding="utf-8"))
    out = {}
    for e in d.get("entries", []):
        series = str(e.get("series") or "")
        # 用 series 关联车系（如 "MG 07 纯电"）
        if series:
            out[series] = e
    return out


def build_trim_board(cands: list[dict], cfg: dict, main_rows: list[dict] | None = None) -> dict:
    """给每个车的所有配置打分，标出该车型的最高分配置。不改主榜分数。

    v9.7.3：人工标注配置分（trim_verified.json，带置信度）优先；
    其余配置仍用 spec_score 规则分并标 n_imputed。两套分带 score_source 字段区分。
    """
    tdb = load_trims()
    verified = load_verified()
    peer_prices = [float(c.get("trim", {}).get("price") or c.get("price_wan") or 0)
                   for c in cands if isinstance(c.get("trim"), dict)]
    peer_prices = [p for p in peer_prices if p > 0] or [15.0]
    out = {}
    for c in cands:
        model = c["model"]
        entry = tdb.get(model)
        if not entry or not entry.get("trims"):
            continue
        scored = [score_trim(t, None, cfg, peer_prices) for t in entry["trims"]]
        # 人工标注：把该配置的分替换为标注分（同量纲百分制）
        vf = verified.get(model)
        vf_note = ""
        if vf and vf.get("final_total") is not None:
            vname = str(vf.get("trim") or "")
            for s in scored:
                # 配置名模糊匹配（标注名含年份/电池描述，取关键数字串）
                import re
                nums = set(re.findall(r"\d{3,4}", vname))
                s_nums = set(re.findall(r"\d{3,4}", str(s.get("name") or "")))
                if nums & s_nums or (vf.get("price") is not None and s.get("price") == vf["price"]):
                    s["total"] = vf["final_total"]
                    s["scores"] = dict(vf.get("base_scores") or s["scores"])
                    # 应用 trim_deltas 到对应维度
                    for d in vf.get("trim_deltas", []):
                        if d.get("dim") in s["scores"]:
                            s["scores"][d["dim"]] = d["to"]
                    s["score_source"] = "verified"
                    s["confidence"] = vf.get("confidence")
                    s["n_imputed"] = 0
                    s["verified_note"] = vf.get("source", "")
            vf_note = f"含人工标注配置（{vf.get('confidence')}置信）"
        valid = [s for s in scored if s.get("total") is not None]
        best = max(valid, key=lambda s: s["total"]) if valid else None
        out[model] = {
            "scored_trim": entry.get("scored_trim"),
            "trim_class": entry.get("trim_class"),
            "n_trims": len(scored),
            "trims": scored,
            "best_trim": best["name"] if best else None,
            "best_total": best["total"] if best else None,
            "best_source": best.get("score_source", "spec_score") if best else None,
            "source": entry.get("url", ""),
            "date": entry.get("date", ""),
            "verified_note": vf_note,
        }
    return out


def selftest() -> int:
    bad = []
    tdb = load_trims()
    if len(tdb) < 10:
        bad.append("配置库加载不足")
    cfg_path = str(paths.cat("car_ev_1218_quality.json"))
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
