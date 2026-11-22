#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""调整预算审计（adjust_budget.py）——审计 P1-5：同一维被多模块叠加调整的总量暴露。

背景（2026-11-22 八维因子审计 P1-5）：
  powertrain 一维同时被 rule_adjust(±0.8) + experience(±1.5) +
  powertrain_efficiency(±0.4) + range_achievement(±1.0) 四个模块各按自己的封顶调整；
  adas/cabin/control 被 experience + evidence_gate 两次。每个模块内部都合规，
  但叠加后没有总量约束——读者以为「每维最多±1.5」，实际能到±3.7。

本模块只**统计+披露**，不改分（强行截断会压掉有强一手证据的车，需用户定夺预算值）：
  - 从各模块的审计字段（rule_adjust / experience_adjust / powertrain_efficiency /
    range_achievement / evidence_cap / airbags_adjust）还原每维每车的调整明细；
  - 对比累计绝对调整量与「名义预算」（各模块封顶之和），超预算的车列入 BUDGET_EXCEEDED；
  - 输出 report 供 pipeline 写进 summary.json，页面/报告可披露。

用法：
  from modules.engine.adjust_budget import audit
  r = audit(cands)
  python3 adjust_budget.py --selftest
"""
from __future__ import annotations

import argparse
import sys

# 每维名义预算 = 接入该维的模块封顶之和（用于对照，不是硬约束）
MODULE_CAPS = {
    "powertrain": {"rule_adjust": 0.8, "experience": 1.5, "efficiency": 0.4, "range_achievement": 1.0},
    "control":    {"rule_adjust": 0.8, "experience": 1.5},
    "space":      {"rule_adjust": 0.8, "space_consistency": 0.0},  # 后者只压不抬，不计独立预算
    "adas":       {"experience": 1.5},
    "cabin":      {"experience": 1.5},
    "safety":     {"airbags": 0.3},
    "value":      {"residual": 1.0},
    "quality":    {},
}


def _collect_deltas(c: dict) -> dict:
    """还原每维的逐模块 delta。返回 {dim: {module: delta}}。"""
    out: dict[str, dict] = {}

    def add(dim, mod, val):
        try:
            v = float(val)
        except (TypeError, ValueError):
            return
        if abs(v) < 1e-9:
            return
        out.setdefault(dim, {})[mod] = out.get(dim, {}).get(mod, 0.0) + v

    # rule_adjust: detail[dim] 直接是该维的记录 {adjust, items_delta_sum, ...}。
    # 2026-11-22 修复：旧代码多套一层 item 遍历，把 adjust/before 等标量当 rec，
    # rec.get("delta") 对标量恒失败 → rule_adjust 调整完全没进审计（dim_max_abs 严重低估）。
    # 现按真实结构取 adjust，并兼容 items_delta_sum/delta 及历史两层结构。
    ra = c.get("rule_adjust") or {}
    if isinstance(ra, dict):
        for dim, rec in (ra.get("detail") or {}).items():
            if not isinstance(rec, dict):
                continue
            if "adjust" in rec or "items_delta_sum" in rec or "delta" in rec:
                dv = rec.get("adjust", rec.get("items_delta_sum", rec.get("delta", 0)))
                add(dim, "rule_adjust", dv)
            else:
                # 兼容历史两层结构 detail[dim][item].delta
                for _item, sub in rec.items():
                    if isinstance(sub, dict):
                        add(dim, "rule_adjust", sub.get("delta", sub.get("adjust", 0)))

    # experience_adjust: 生产结构是 ["dimensions"][dim].delta（experience_score.apply_experience 写入）。
    # 2026-11-22 修复：旧代码读 ["detail"] 恒空 → experience 调整完全没进审计。
    # 兼容历史 detail[dim].delta 结构。
    ex = c.get("experience_adjust") or {}
    if isinstance(ex, dict):
        src = ex.get("dimensions") or ex.get("detail") or {}
        if isinstance(src, dict):
            for dim, rec in src.items():
                if isinstance(rec, dict):
                    add(dim, "experience", rec.get("delta", rec.get("adjust", 0)))

    # efficiency / range_achievement: 模块审计字段的 delta
    # 注意 efficiency 用 adjust 字段（不是 delta），range 用 delta——必须都读，否则严重低估。
    ef = c.get("powertrain_efficiency") or {}
    if isinstance(ef, dict):
        dv = ef.get("delta", ef.get("adjust", 0))
        if not dv and ef.get("before") is not None and ef.get("after") is not None:
            try:
                dv = float(ef["after"]) - float(ef["before"])
            except (TypeError, ValueError):
                dv = 0
        add("powertrain", "efficiency", dv)
    rch = c.get("range_achievement") or {}
    if isinstance(rch, dict):
        dv = rch.get("delta", 0)
        if not dv and rch.get("before") is not None and rch.get("after") is not None:
            try:
                dv = float(rch["after"]) - float(rch["before"])
            except (TypeError, ValueError):
                dv = 0
        add("powertrain", "range_achievement", dv)

    # airbags / quality cap 等如有 delta 字段也纳入
    ab = c.get("airbags_adjust") or {}
    if isinstance(ab, dict):
        add("safety", "airbags", ab.get("delta", 0))
    return out


def audit(cands: list[dict]) -> dict:
    """统计每维累计调整，标出超名义预算的车。不改分。"""
    per_car, dim_max = {}, {}
    for c in cands:
        deltas = _collect_deltas(c)
        summary = {}   # 不在遍历 deltas 时写它（否则 dict changed size during iteration）
        for dim, mods in deltas.items():
            total = sum(abs(v) for v in mods.values())
            budget = sum(MODULE_CAPS.get(dim, {}).values())
            summary[dim] = {"modules": {k: round(v, 2) for k, v in mods.items()},
                            "total_abs": round(total, 2), "nominal_budget": budget,
                            "exceeded": total > budget + 1e-9}
            dmax = dim_max.get(dim, 0.0)
            if total > dmax:
                dim_max[dim] = total
        deltas["_summary"] = summary
        per_car[c.get("model")] = deltas
    exceeded = []
    for model, deltas in per_car.items():
        for dim, rec in (deltas.get("_summary") or {}).items():
            if rec["exceeded"]:
                exceeded.append({"model": model, "dim": dim, **rec})
    return {"status": "ok", "checked": len(cands),
            "dim_max_abs": {k: round(v, 2) for k, v in dim_max.items()},
            "exceeded": exceeded,
            "note": "只审计不截断：每模块内部有封顶，但同维多模块叠加无总预算；"
                    "超预算是需人工定夺的口径问题，不自动改分（避免压掉强证据车）"}


def selftest() -> int:
    # 全部用各模块真实生产写入结构（2026-11-22）：杜绝臆测字段名导致审计器恒读 0。
    # 叠加检测：powertrain 四模块各顶格 → 累计 = 名义预算 3.7（等于不超）
    c = {"model": "叠加车",
         "rule_adjust": {"detail": {"powertrain": {"adjust": 0.8, "items_delta_sum": 0.8}}},
         "experience_adjust": {"dimensions": {"powertrain": {"delta": 1.5}}},
         "powertrain_efficiency": {"adjust": 0.4},
         "range_achievement": {"delta": 1.0}}
    r = audit([c])
    pt = r["dim_max_abs"].get("powertrain", 0)
    assert abs(pt - 3.7) < 0.01, f"应累计 3.7，实际 {pt}"
    assert not r["exceeded"], "四模块各顶格=等于预算，不应报超（预算即封顶之和）"
    # 真超预算：experience 异常写入 2.5（超单模块封顶1.5）→ 累计 4.7 > 3.7
    c2 = dict(c)
    c2["experience_adjust"] = {"dimensions": {"powertrain": {"delta": 2.5}}}
    r2 = audit([c2])
    assert any(x["model"] == "叠加车" and x["dim"] == "powertrain" for x in r2["exceeded"]), r2
    # 单模块不超预算
    c3 = {"model": "正常车", "experience_adjust": {"dimensions": {"adas": {"delta": 1.2}}}}
    r3 = audit([c3])
    assert not r3["exceeded"], r3
    # efficiency 字段名是 adjust（不是 delta）——回归锁死，曾因只读 delta 低估累计量
    c4 = {"model": "效率车", "powertrain_efficiency": {"adjust": 0.4}}
    r4 = audit([c4])
    assert abs(r4["dim_max_abs"].get("powertrain", 0) - 0.4) < 0.01, \
        f"efficiency 用 adjust 字段，必须读到 0.4: {r4['dim_max_abs']}"
    # rule_adjust 生产结构：detail[dim] 直接是 {adjust, items_delta_sum,...}，不是 item→rec 两层。
    # 曾因多套一层遍历把标量当 dict，rec.get 恒失败 → rule_adjust 完全没进审计。
    c5 = {"model": "规则车", "rule_adjust": {"detail": {"powertrain": {
        "before": 8.3, "after": 8.8, "adjust": 0.5, "items_delta_sum": 0.5,
        "cap": 0.8, "alpha": 0.5, "items": {"快充": {"delta": 0.5}}}}}}
    r5 = audit([c5])
    assert abs(r5["dim_max_abs"].get("powertrain", 0) - 0.5) < 0.01, \
        f"rule_adjust 生产结构 adjust=0.5 必须读到，不得读 items 内的 delta: {r5['dim_max_abs']}"
    # rule_adjust + efficiency + experience + range 真实叠加（v6 同款字段形态）
    c6 = {"model": "叠加车2",
          "rule_adjust": {"detail": {"powertrain": {"adjust": 0.5, "items_delta_sum": 0.5}}},
          "powertrain_efficiency": {"adjust": 0.4},
          "experience_adjust": {"dimensions": {"powertrain": {"delta": 1.5}}},
          "range_achievement": {"delta": 0.7}}
    r6 = audit([c6])
    assert abs(r6["dim_max_abs"].get("powertrain", 0) - 3.1) < 0.01, \
        f"四模块真实结构应累计 0.5+0.4+1.5+0.7=3.1: {r6['dim_max_abs']}"
    assert not any(x["model"] == "叠加车2" and x["dim"] == "powertrain" for x in r6["exceeded"]), \
        f"3.1 < 3.7 预算，不应报超: {r6['exceeded']}"
    # 不改候选分数
    assert "scores" not in c and "scores" not in c3, "审计不得写分数"
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--candidates")
    a = ap.parse_args()
    if a.selftest:
        try:
            selftest()
            print("adjust_budget selftest OK")
            return 0
        except AssertionError as e:
            print(f"[FAIL] {e}", file=sys.stderr)
            return 1
    if not a.candidates:
        ap.error("需要 --candidates 或 --selftest")
    import json
    d = json.load(open(a.candidates, encoding="utf-8"))
    r = audit(d.get("candidates", d))
    print(json.dumps(r, ensure_ascii=False, indent=2)[:2500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
