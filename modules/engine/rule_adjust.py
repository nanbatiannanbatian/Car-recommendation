#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rule_adjust.py —— 把配置驱动规则接进人工基准分（唯一入口）

为什么需要这个模块（2026-11-21，用户连续三次质疑同一类）：
  主榜 8 个维度分**全是人工基准分**（master_pool.json 里手写的 0–10 分），
  配置驱动的规则（spec_score 的 800V / 快充 / 电芯 / 悬架 / 驱动 / 零百…）
  只活在「逐配置表」里，主榜一格都不接——`score_audit` 里
  powertrain 标 `wired: False`、control 标 `partial`。后果：

  · 20 多万的极氪 7X（900V / 麒麟 / 10min 快充）拿不到任何硬件加分，
    反而被高速续航实测达成率 55.32% 的体验证据扣 0.7，
    三电 8.3 低于 15 万的大众 T6（8.4）——而两车的硬件差距是客观存在的；
  · 长安启源 Q06 底盘硬件好，操控分却低于小鹏 MONA L05，因为操控分是拍出来的。

为什么不直接用 spec_score 的**绝对分**替换人工分：
  spec_score 的规则从 base 6.0 起算，字段缺失的车只能拿基线，
  会把「没采到参数」变成「分数低」，违反「缺数据 ≠ 差」纪律。
  人工基准分承载的是人的综合判断（质感、标定、主观体验），不该被规则覆盖。

接法（规则增量 · 逐项中位锚点）：
  规则分 R 是绝对分，但**只用来算相对增量**。关键是**逐项**做锚点，
  而不是用总分做锚点：

      对规则里的每一个加减项（如「高压平台」「快充」「电芯」「续航」）：
        vals = {车: 该项分值}                 # **只有该项有数据的车**
        med  = median(vals)                   # 该项在本榜的中位
        adj[车] += (vals[车] − med) × α
      最终 = 人工基准分 + clamp(Σadj, −cap, +cap)

  为什么要逐项（而不是拿总分比）：
    若用总分锚点，**缺参数的车会因为「缺的那一项」而被判弱**——
    实测大众T6（人工条目 charge 没记电压）本该拿 800V 加分，却因缺电压
    被扣 0.38 分；蔚来ES6 被扣满 0.8。这违反「缺数据 ≠ 差」。
    逐项锚点后：缺电压的车在「高压平台」这一项上**既不加分也不扣分**，
    只在它确实有数据的项（快充/电芯/续航）上参与比较。

  其余纪律：
  · 用中位做基准 → 规则只负责区分度，不改全榜平均水位；
  · 每项可比车 < min_peers 就不做该项锚点（样本太少是噪声）；
  · 无效车分毫不动 —— 缺数据 ≠ 差；
  · 双向（可加可减），避免「有数据就加分」奖励营销披露；
  · 全过程写 `c["rule_adjust"][dim]` 审计字段，可逐条复核。

与 trim_annotate 的分工：
  trim_annotate 管**同一车系内不同配置之间**的相对差（best_trim 的 delta）；
  本模块管**不同车系之间**由客观配置参数造成的差异。两者不重叠、不相乘。
"""
from __future__ import annotations

import argparse
import statistics
import sys

import paths

HERE = str(paths.ROOT)

# 接入的维度 → spec_score 的算分函数名
DIMS = ("powertrain", "control")

ALPHA = 0.5        # 单项规则分差 → 增量 的折算系数
CAP = 0.8          # 单维总增量封顶（分）
MIN_PEERS = 3      # 某一项的可比车少于这个数就不做该项锚点（样本太少是噪声）


def _spec_of(c: dict, trims_db: dict) -> dict:
    """组装 spec_score 的入参：evidence 硬参数 + trims 逐配置电池参数。

    复用现成实现，不另写一套映射（同一事实只能有一处口径）：
      · `score_audit.extract_spec_fields` —— 从 evidence 文本提 TOPS/悬架/驱动/零百…
      · `trim_board.spec_from_trim` —— trims 的 charge/cell_supplier/cltc_km → spec 字段
    """
    from modules.engine import score_audit as SA
    from modules.engine import trim_annotate as TA
    from modules.engine import trim_board as TB

    base_spec = SA.extract_spec_fields(c)
    entry = trims_db.get(c.get("model"))
    if not entry or not entry.get("trims"):
        return base_spec
    ref, _fallback, _basis = TA.resolve_ref(
        entry.get("scored_trim") or "", entry["trims"], [c])
    if not ref:
        return base_spec
    return TB.spec_from_trim(ref, base_spec)


def rule_parts(cands: list[dict], dim: str) -> dict:
    """{model: [(项名, 分值, 展示文本)]}——规则项分解（缺项不出现在列表里）。"""
    from modules.engine import spec_score as SS
    from modules.engine import trim_board as TB

    fn = {"powertrain": SS.powertrain_parts, "control": SS.control_parts}.get(dim)
    if fn is None:
        return {}
    trims_db = TB.load_trims()
    out: dict[str, list] = {}
    for c in cands:
        out[c.get("model")] = fn(_spec_of(c, trims_db))
    return out


def apply_rule_adjust(cands: list[dict], dims=DIMS, alpha=ALPHA, cap=CAP,
                       min_peers=MIN_PEERS) -> dict:
    """原地给 cands 的目标维度加规则增量，写审计字段。返回逐维摘要。

    逐项中位锚点：每个规则项各自在「该项有数据的车」里取中位做基准，
    缺该项的车在该项上不参与（既不加分也不扣分）。
    """
    summary: dict[str, dict] = {}
    for dim in dims:
        parts = rule_parts(cands, dim)
        if not parts:
            continue
        item_names = sorted({n for ps in parts.values() for n, _v, _t in ps})
        adj = {m: 0.0 for m in parts}
        detail: dict[str, dict] = {m: {} for m in parts}
        for item in item_names:
            vals = {}
            for m, ps in parts.items():
                for (n, v, t) in ps:
                    if n == item:
                        vals[m] = (v, t)
            if len(vals) < min_peers:
                continue
            med = statistics.median([v for v, _t in vals.values()])
            for m, (v, t) in vals.items():
                d = (v - med) * alpha
                adj[m] += d
                detail[m][item] = {"value": v, "median": round(med, 2),
                                   "delta": round(d, 2), "text": t}
        touched = []
        for c in cands:
            m = c.get("model")
            if m not in adj:
                continue
            total = max(-cap, min(cap, adj[m]))
            if abs(total) < 1e-9:
                continue
            scores = c.setdefault("scores", {})
            before = scores.get(dim)
            if not isinstance(before, (int, float)):
                continue
            after = round(max(0.0, min(10.0, before + total)), 2)
            scores[dim] = after
            c.setdefault("rule_adjust", {})[dim] = {
                "before": before, "after": after,
                "adjust": round(total, 2),
                "items_delta_sum": round(adj[m], 2),
                "cap": cap, "alpha": alpha,
                "items": detail[m],
            }
            touched.append(m)
        summary[dim] = {"status": "applied", "n_items": len(item_names),
                        "touched": touched}
    return summary


def selftest() -> int:
    """单元回归：规则增量必须只作用于参数有效的车，且以中位为基准。"""
    # A 车：900V + 快充 10min + 宁德 + CLTC 800 → 规则分高
    # B 车：无参数 → 规则分基线，不该被调整（缺数据≠差）
    # C 车：400V + 慢充 + 二线电芯 + CLTC 400 → 规则分低 → 应减分
    trims_db = {
        "A车": {"scored_trim": "A顶配", "trims": [
            {"name": "A顶配", "price": 25.0, "battery_kwh": 100, "battery_type": "液态",
             "cell_supplier": "宁德时代", "supplier_class": "single_named", "cltc_km": 800,
             "charge": "900V · 10–80% 约 10min", "motor_kw": 300, "key_configs": [],
             "group": "2026款", "note": "", "consumption": 13.0,
             "charge_power_kw": 400.0, "swap": None}]},
        "B车": {"scored_trim": "B顶配", "trims": [
            {"name": "B顶配", "price": 15.0, "battery_kwh": 60, "battery_type": None,
             "cell_supplier": None, "supplier_class": "undisclosed", "cltc_km": 500,
             "charge": None, "motor_kw": 150, "key_configs": [],
             "group": "2026款", "note": "", "consumption": None,
             "charge_power_kw": None, "swap": None}]},
        "C车": {"scored_trim": "C顶配", "trims": [
            {"name": "C顶配", "price": 12.0, "battery_kwh": 40, "battery_type": "磷酸铁锂",
             "cell_supplier": "二线电芯厂", "supplier_class": "single_named", "cltc_km": 400,
             "charge": "30–80% 约 35min", "motor_kw": 100, "key_configs": [],
             "group": "2026款", "note": "", "consumption": 17.0,
             "charge_power_kw": 60.0, "swap": None}]},
        "D车": {"scored_trim": "D顶配", "trims": [
            {"name": "D顶配", "price": 14.0, "battery_kwh": 60, "battery_type": "磷酸铁锂",
             "cell_supplier": "国轩高科", "supplier_class": "single_named", "cltc_km": 500,
             "charge": "400V · 30–80% 约 25min", "motor_kw": 120, "key_configs": [],
             "group": "2026款", "note": "", "consumption": 15.0,
             "charge_power_kw": 90.0, "swap": None}]},
        "E车": {"scored_trim": "E顶配", "trims": [
            {"name": "E顶配", "price": 18.0, "battery_kwh": 70, "battery_type": "液态",
             "cell_supplier": "亿纬锂能", "supplier_class": "single_named", "cltc_km": 650,
             "charge": "800V · 10–80% 约 20min", "motor_kw": 180, "key_configs": [],
             "group": "2026款", "note": "", "consumption": 14.0,
             "charge_power_kw": 200.0, "swap": None}]},
    }
    import modules.engine.trim_board as _TB_mod
    orig = _TB_mod.load_trims
    _TB_mod.load_trims = lambda *a, **k: trims_db
    try:
        cands = [
            {"model": "A车", "scores": {"powertrain": 9.0, "control": 8.0}},
            {"model": "B车", "scores": {"powertrain": 7.0, "control": 7.0}},
            {"model": "C车", "scores": {"powertrain": 7.5, "control": 7.5}},
        ]
        # 造第 4/5 款让中位有 5 个样本（B 车参数不足不计入有效集）
        for m in ("D车", "E车"):
            cands.append({"model": m, "scores": {"powertrain": 7.5, "control": 7.5}})
        rep = apply_rule_adjust(cands, dims=("powertrain",), min_peers=3)
        by = {c["model"]: c for c in cands}
        # B 车参数不足 → 分毫不动
        assert by["B车"]["scores"]["powertrain"] == 7.0, "参数不足的车不该被调整"
        assert "powertrain" not in by["B车"].get("rule_adjust", {}), "B车不该有审计记录"
        # A 车规则分最高 → 应加分
        assert by["A车"]["scores"]["powertrain"] > 9.0, f"A车应加分: {by['A车']['scores']}"
        # C 车规则分最低 → 应减分（双向）
        assert by["C车"]["scores"]["powertrain"] < 7.5, f"C车应减分: {by['C车']['scores']}"
        # 封顶：增量不超过 CAP
        for c in cands:
            ra = (c.get("rule_adjust") or {}).get("powertrain")
            if ra:
                assert abs(ra["adjust"]) <= CAP + 1e-9, ra
        assert rep["powertrain"]["status"] == "applied", rep
    finally:
        _TB_mod.load_trims = orig
    print("SELFTEST PASS · 规则增量接入：参数不足不动 / 高规则分加分 / 低规则分减分 / 双向封顶")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(selftest())
    print("本模块为纯函数，供 pipeline 调用；用 --selftest 验证。")
