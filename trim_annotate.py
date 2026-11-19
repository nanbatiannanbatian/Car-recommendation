#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐配置人工口径标注（trim_annotate.py）——统一量纲的配置分生成。

方法论（与六配置榜 compare_data.json 一致）：
  base_scores = 候选池人工标注的车系级 8 维分（13 款全有、同口径）
  + trim_deltas = 按该配置相对车系评分配置的客观差异做加减（带置信度）
  final = Σ(base+delta) × 权重 × 10（百分制，与车系分同量纲）

为什么不是 spec_score 规则从零算：
  spec_score 缺轴距/悬架/芯片等，6 维插补、系统性偏低约 15 分，与人工分不同量纲，
  混排会让名次失真。本模块以人工 base 为锚，只调整配置真正改变的维度。

delta 规则（只调有客观证据的维）：
  - 800V/900V（+0.5~1.0 三电，high）：配置表 charge 字段明确
  - 续航差（每 +100km 三电 +0.3，medium）：cltc_km 对比
  - 大电池（每 +10kWh 三电 +0.2，medium）：battery_kwh 对比
  - 激光雷达有无（智驾 +0.8，high）：key_configs
  - 价格差（性价比按 kWh/万元 相对变化，medium）
  每个 delta 带 conf（high/medium/low），整体置信取最低。

用法：
    from trim_annotate import annotate_all
    data = annotate_all(cands, cfg)   # {车型: {trims:[{name,scores,total,conf,deltas}]}}
    python3 trim_annotate.py --selftest
"""
from __future__ import annotations
import argparse
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
TRIMS = os.path.join(HERE, "categories", "trims.json")
W = {"powertrain": 0.18, "safety": 0.162, "adas": 0.135, "space": 0.126,
     "value": 0.117, "control": 0.09, "cabin": 0.09, "quality": 0.1}


def load_trims() -> dict[str, dict]:
    p = TRIMS
    if not os.path.exists(p):
        return {}
    d = json.load(open(p, encoding="utf-8"))
    return {e["model"]: e for e in d.get("entries", []) if e.get("model")}


def weighted_total(scores: dict) -> float:
    return round(sum(float(scores.get(k, 0)) * v for k, v in W.items()) * 10, 1)


def deltas_for(trim: dict, ref: dict) -> list[dict]:
    """该配置相对参考配置的客观差异 → 维度调整（带置信度）。"""
    out = []
    # 电压
    c800 = lambda t: ("800V" in str(t.get("charge") or "") or "900V" in str(t.get("charge") or ""))
    if c800(trim) and not c800(ref):
        out.append({"dim": "powertrain", "delta": 0.8, "conf": "high",
                    "why": "800V高压平台（配置表明确）"})
    # 续航
    r1, r0 = trim.get("cltc_km"), ref.get("cltc_km")
    if isinstance(r1, (int, float)) and isinstance(r0, (int, float)):
        d = (r1 - r0) / 100 * 0.3
        if abs(d) >= 0.15:
            out.append({"dim": "powertrain", "delta": round(d, 2),
                        "conf": "medium", "why": f"CLTC {r0}→{r1}km"})
    # 电池容量
    k1, k0 = trim.get("battery_kwh"), ref.get("battery_kwh")
    if isinstance(k1, (int, float)) and isinstance(k0, (int, float)):
        d = (k1 - k0) / 10 * 0.2
        if abs(d) >= 0.15:
            out.append({"dim": "powertrain", "delta": round(d, 2),
                        "conf": "medium", "why": f"电量 {k0}→{k1}kWh"})
    # 激光雷达
    lidar = lambda t: any("激光雷达" in str(x) for x in (t.get("key_configs") or []))
    if lidar(trim) and not lidar(ref):
        out.append({"dim": "adas", "delta": 0.8, "conf": "high", "why": "新增激光雷达"})
    elif lidar(ref) and not lidar(trim):
        out.append({"dim": "adas", "delta": -0.8, "conf": "high", "why": "无激光雷达"})
    # 性价比：按 kWh/万元 相对变化
    p1, p0 = trim.get("price"), ref.get("price")
    if p1 and p0 and k1 and k0:
        v1, v0 = k1 / p1, k0 / p0
        d = max(-1.0, min(1.0, (v1 - v0) / max(v0, 1) * 2))
        if abs(d) >= 0.15:
            out.append({"dim": "value", "delta": round(d, 2),
                        "conf": "medium", "why": f"kWh/万元 {v0:.2f}→{v1:.2f}"})
    return out


CONF_ORDER = {"low": 0, "medium": 1, "high": 2}


def find_ref(name: str, trims: list[dict]) -> dict | None:
    """模糊匹配评分配置（v9.7.4：35款里34款精确匹配失败，必须模糊匹配）。

    scored_trim 常写成「560 Ultra 激光版」，配置表实际是「纯电版 560Ultra激光版」；
    用归一化（去空格/去年份/去"纯电版"前缀）+ 关键数字串双重判定。
    """
    def norm(s: str) -> str:
        s = str(s or "")
        for junk in ("纯电版", "纯电", "款", "版", " ", "　"):
            s = s.replace(junk, "")
        import re
        s = re.sub(r"20\d{2}", "", s)
        return s.lower()

    target = norm(name)
    if not target:
        return None
    # 1) 归一化后完全相等
    for t in trims:
        if norm(t.get("name")) == target:
            return t
    # 2) 关键数字串（如 560/610/670）+ 关键配置词（激光/ultra/max）交集
    import re
    nums = set(re.findall(r"\d{3,4}", target))
    words = {w for w in ("激光", "ultra", "max", "plus", "pro", "智行", "旗舰") if w in target}
    best, score = None, 0
    for t in trims:
        tn = norm(t.get("name"))
        tnums = set(re.findall(r"\d{3,4}", tn))
        twords = {w for w in ("激光", "ultra", "max", "plus", "pro", "智行", "旗舰") if w in tn}
        sc = len(nums & tnums) * 2 + len(words & twords)
        if sc > score:
            best, score = t, sc
    return best if score >= 2 else None


def annotate_all(cands: list[dict], cfg: dict) -> dict:
    """给每个车的所有配置生成人工口径分（base_scores + delta）。"""
    tdb = load_trims()
    out = {}
    for c in cands:
        model = c["model"]
        entry = tdb.get(model)
        if not entry or not entry.get("trims"):
            continue
        base = dict(c.get("scores") or {})
        if not base:
            continue
        ref_name = entry.get("scored_trim") or ""
        ref = find_ref(ref_name, entry["trims"])
        trims = []
        for t in entry["trims"]:
            # v9.7.4：匹配不到参考配置时不下 delta（宁可不加分，不在错基准上加分）
            ds = deltas_for(t, ref) if (ref and t is not ref) else []
            sc = dict(base)
            for d in ds:
                sc[d["dim"]] = round(max(0, min(10, sc.get(d["dim"], 8) + d["delta"])), 2)
            conf = min((d["conf"] for d in ds), key=lambda x: CONF_ORDER[x]) if ds else "high"
            trims.append({
                "name": t.get("name"), "price": t.get("price"),
                "battery_kwh": t.get("battery_kwh"), "cell_supplier": t.get("cell_supplier"),
                "charge": t.get("charge"), "cltc_km": t.get("cltc_km"),
                "scores": sc, "total": weighted_total(sc),
                "confidence": conf, "deltas": ds,
                "note": t.get("note", ""), "score_source": "annotated",
            })
        valid = [t for t in trims if t.get("total") is not None]
        best = max(valid, key=lambda t: t["total"]) if valid else None
        out[model] = {
            "scored_trim": ref_name,
            "n_trims": len(trims), "trims": trims,
            "best_trim": best["name"] if best else None,
            "best_total": best["total"] if best else None,
            "best_confidence": best.get("confidence") if best else None,
            "best_source": "annotated",
            "source": entry.get("url", ""), "date": entry.get("date", ""),
        }
    return out


def selftest() -> int:
    bad = []
    tdb = load_trims()
    if len(tdb) < 10:
        bad.append("配置库不足")
    cands = [{"model": "MG 07 纯电", "scores": {
        "powertrain": 8.0, "safety": 8.2, "adas": 8.6, "space": 8.0,
        "value": 9.4, "control": 8.8, "cabin": 8.6, "quality": 8.0}}]
    cfg = {"dimensions": [{"key": k, "weight": v} for k, v in W.items()]}
    out = annotate_all(cands, cfg)
    d = out.get("MG 07 纯电", {})
    t845 = next((t for t in d.get("trims", []) if "845" in str(t.get("name"))), None)
    t610 = next((t for t in d.get("trims", []) if "610 激光" in str(t.get("name"))), None)
    if not (t845 and t610):
        bad.append("MG07 845/610配置缺失")
    else:
        if t845["scores"]["powertrain"] <= t610["scores"]["powertrain"]:
            bad.append(f"845三电应高于610: {t845['scores']['powertrain']} vs {t610['scores']['powertrain']}")
        if t845["total"] <= t610["total"]:
            bad.append("845总分应高于610")
    if bad:
        print("[selftest FAIL]", bad)
        return 1
    print(f"[selftest OK] 配置库 {len(tdb)} 款；MG07 845版人工口径分 > 610版；delta带置信度")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="逐配置人工口径标注")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    raise SystemExit(selftest() if a.selftest else 0)
