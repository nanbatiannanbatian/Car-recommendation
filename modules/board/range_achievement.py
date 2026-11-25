#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""续航达成率调整（range_achievement.py）——P0-1：三电从「标称 CLTC」升级为「标称+达成率」双层。

为什么要它（2026-11-22 八维因子审计 P0-1）：
  三电维现有「续航」规则只吃 CLTC 标称值（≥700km +0.8 / ≥600 +0.5 / <500 −0.5），
  同样 700km、冬季实测打 4 折和 7 折的车同分。业内（C-AHI 2026 高速/低温衰减率各 25%、
  懂车帝高速达成率、汽车之家冬测）已把达成率作为核心量化指标；我们只有零散 5 条
  体验证据（方向还互相矛盾），未制度化。

口径（可复现、不编造）：
  - 用 physics.ev_range_model 从 kwh/电耗/车重/Cd/电压估算**高速120km/h达成率**
    （模型已经懂车帝21车实测校准，MAE已在模块内回归验证）。
  - 按榜内达成率中位数做双向调整：ach 每高于中位 5 个百分点 +0.1，低于 −0.1，
    封顶 ±1.0（对应审计建议 ±0.6~1.0）。公式：clamp((ach-med)/0.05*0.1, ±1.0)。
  - 无物理参数（kwh 缺）不插补、不动分；有实测达成率一手数据的车优先用实测
    （experience_facts 的 highway_range_test），模型值作兜底。
  - 只写 scores.powertrain 与 c["range_achievement"] 审计字段，不碰其他维/总分。

顺序：必须在 powertrain_efficiency 之后、evidence_gate 之前（gate 读最终三电分封顶）。

用法：
  from modules.board.range_achievement import apply
  report = apply(cands)   # 原地改 cands
  python3 range_achievement.py --selftest
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys

import paths

HERE = str(paths.ROOT)
CAP = 1.0          # 单维封顶 ±1.0
STEP = 0.05        # 每 5 个百分点达成率差 → 0.1 分
STEP_PT = 0.1
MIN_PEERS = 3      # 榜内样本 <3 不调整（无统计意义）


def _num(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _physics_map() -> dict:
    """canon(model) → 物理参数条目（categories/vehicle_physics.json）。"""
    from modules.contracts.model_names import canon
    p = str(paths.cat("vehicle_physics.json"))
    try:
        d = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {canon(c.get("model") or ""): c for c in d.get("cars", [])}


def estimate_achievement(c: dict, phys: dict) -> tuple[float | None, str]:
    """估算高速达成率。优先用体验库一手实测，否则物理模型。返回 (ach, 依据)。"""
    from modules.contracts.model_names import canon
    from modules.physics import ev_range_model as ER
    from modules.engine import experience_score as ES

    model = c.get("model", "")
    k = canon(model)

    # ① 一手实测优先（highway_range_test / range_test，independent_test 权重最高）
    lib = ES.load_library()
    for e in lib.get("entries", []):
        if canon(e.get("model") or "") != k:
            continue
        if e.get("dimension") != "powertrain":
            continue
        if e.get("kind") not in ("highway_range_test", "range_test"):
            continue
        if e.get("source_type") not in ("independent_test", "up_review"):
            continue
        claim = str(e.get("claim") or "")
        import re
        m = re.search(r"达成率\s*约?\s*(\d+(?:\.\d+)?)\s*%", claim)
        if m:
            ach = float(m.group(1)) / 100.0
            if 0.3 <= ach <= 1.05:
                return ach, f"一手实测达成率 {ach*100:.0f}%（{e.get('source_type')}：{claim[:40]}）"

    # ② 物理模型兜底
    pv = phys.get(k) or {}
    kwh = _num(c.get("kwh"))
    cltc = _num(pv.get("cltc_km")) or _num(c.get("range_km"))
    # 候选池层常无 kwh/cltc → 走 trims  scored_trim 统一口径（与 rule_adjust 同源）
    if not (kwh and cltc):
        try:
            from modules.engine import trim_annotate as TA, trim_board as TB
            from modules.data.trims_store import load as load_trims
            entry = load_trims().get(c.get("model")) or {}
            trims = entry.get("trims") or []
            ref, _, _ = TA.resolve_ref(entry.get("scored_trim") or "", trims, [c])
            if ref:
                sp = TB.spec_from_trim(ref, {})
                kwh = kwh or _num(sp.get("kwh"))
                cltc = cltc or _num(sp.get("range_km"))
        except Exception:
            pass
    if not kwh:
        det = c.get("scored_trim_detail") or {}
        if isinstance(det, dict):
            kwh = _num(det.get("battery_kwh"))
    w = _num(pv.get("weight_kg"))
    cd = _num(pv.get("cd"))
    cons = _num(pv.get("consumption"))
    vlt = _num(pv.get("voltage_v"))
    if not (kwh and w and cltc):
        return None, "缺 kwh/车重/CLTC，不估算达成率（不插补）"
    area = None
    wd, ht = _num(pv.get("width_mm")), _num(pv.get("height_mm"))
    if wd and ht:
        area = wd * ht / 1e6 * ER.AREA_FACTOR
    vehicle = {"kwh": kwh, "cons": cons, "weight": w, "cd": cd, "area": area,
               "voltage": vlt, "cltc": cltc, "body": "SUV"}
    try:
        r = ER.estimate(vehicle, dict(ER.SCENE_MAP["highway_120"]))
    except Exception:
        return None, "物理模型估算失败（缺参数），不插补"
    ach = r.get("achievement")
    if ach is None or not (0.3 <= ach <= 1.05):
        return None, "达成率估算越界，不采信"
    return ach, f"物理模型高速估算达成率 {ach*100:.0f}%（kwh={kwh:g} w={w:g} cd={cd}）"


def apply(cands: list[dict], fixed_median: float | None = None) -> dict:
    """对达成率做中位锚双向调整。原地改 scores.powertrain，返回审计 report。

    fixed_median：可选固定中位（全价位榜算好）。给定时跳过本池中位统计，
      子榜与全价位榜共用同一锚点 → 同车同分；达成率仍是车自身物理量，不随池变。
    """
    phys = _physics_map()
    ach_map, basis_map, kind_map = {}, {}, {}
    for c in cands:
        ach, basis = estimate_achievement(c, phys)
        if ach is not None:
            ach_map[c["model"]] = ach
            basis_map[c["model"]] = basis
            # v10.73：区分证据强度——independent（独立实测）/ owner（车主口碑）/ model（物理估算）。
            # 三电 9.0+ 闸门只认 independent：车主口碑的达成率反映的是驾驶习惯与路况，
            # 不是统一工况实测，不能据此声称"三电能效第一梯队"。
            if "independent_test" in basis:
                kind_map[c["model"]] = "independent"
            elif "up_review" in basis:
                kind_map[c["model"]] = "owner"
            else:
                kind_map[c["model"]] = "model"
    applied, skipped = [], []
    use_fixed = isinstance(fixed_median, (int, float))
    if use_fixed or len(ach_map) >= MIN_PEERS:
        med = float(fixed_median) if use_fixed else statistics.median(ach_map.values())
        for c in cands:
            m = c.get("model")
            if m not in ach_map:
                c["range_achievement"] = {"status": "no_data", "reason": basis_map.get(m, "无达成率数据")}
                skipped.append(m)
                continue
            ach = ach_map[m]
            delta = max(-CAP, min(CAP, (ach - med) / STEP * STEP_PT))
            delta = round(delta, 2)
            scores = c.setdefault("scores", {})
            old = scores.get("powertrain")
            if isinstance(old, (int, float)) and abs(delta) > 1e-9:
                scores["powertrain"] = round(max(0, min(10, float(old) + delta)), 2)
            c["range_achievement"] = {"status": "applied", "achievement": round(ach, 3),
                                      "median": round(med, 3), "delta": delta,
                                      "before": old, "after": scores.get("powertrain"),
                                      "kind": kind_map.get(m) or "model",
                                      "basis": basis_map[m]}
            applied.append({"model": m, "ach": round(ach, 3), "delta": delta})
    else:
        for c in cands:
            c["range_achievement"] = {"status": "not_enough_peers",
                                      "reason": f"有达成率数据 {len(ach_map)} 款 < {MIN_PEERS}"}
    anchor_kind = "全价位固定中位锚" if use_fixed else "榜内中位锚"
    return {"status": "applied" if (use_fixed or len(ach_map) >= MIN_PEERS) else "skipped",
            "covered": len(ach_map), "applied": applied, "skipped": skipped,
            "method": f"高速达成率{anchor_kind}，每{STEP*100:.0f}个百分点{STEP_PT:+.1f}分，±{CAP}封顶"}


def selftest() -> int:
    """锁死：达成率高于中位加分、低于扣分、无数据不动分、封顶生效。"""
    cands = [
        {"model": "A车", "kwh": 80, "range_km": 600, "scores": {"powertrain": 8.0}},
        {"model": "B车", "kwh": 80, "range_km": 600, "scores": {"powertrain": 8.0}},
        {"model": "C车", "kwh": 80, "range_km": 600, "scores": {"powertrain": 8.0}},
        {"model": "D车", "scores": {"powertrain": 8.0}},  # 无数据
    ]
    # 直接注入 ach 避免依赖物理库（测的是锚点算法本身）
    seen = {}
    orig = estimate_achievement
    try:
        globals()["estimate_achievement"] = lambda c, phys: (
            {"A车": 0.70, "B车": 0.60, "C车": 0.50}.get(c["model"]), "test")
        r = apply(cands)
    finally:
        globals()["estimate_achievement"] = orig
    assert r["covered"] == 3, r
    a = cands[0]["range_achievement"]
    assert a["delta"] > 0 and cands[0]["scores"]["powertrain"] > 8.0, a
    c = cands[2]["range_achievement"]
    assert c["delta"] < 0 and cands[2]["scores"]["powertrain"] < 8.0, c
    assert cands[3]["range_achievement"]["status"] == "no_data"
    assert cands[3]["scores"]["powertrain"] == 8.0, "无数据不能动分"
    # 封顶：极端达成率也不超过 ±1.0
    assert abs(a["delta"]) <= CAP
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--candidates")
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.selftest:
        try:
            selftest()
            print("range_achievement selftest OK")
            return 0
        except AssertionError as e:
            print(f"[FAIL] {e}", file=sys.stderr)
            return 1
    if not a.candidates:
        ap.error("需要 --candidates 或 --selftest")
    d = json.load(open(a.candidates, encoding="utf-8"))
    cands = d.get("candidates", d)
    r = apply(cands)
    print(json.dumps(r, ensure_ascii=False, indent=2)[:2000])
    if a.out:
        json.dump(r, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
