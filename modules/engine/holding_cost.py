#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""holding_cost.py —— 持有成本维度（三年保值率）（v10.74.0，用户要求拆维）

为什么单列（2026-11-25 用户指示：「三年保值率接进打分」）：
  v10.49 曾把保值率从「性价比」里剥掉（保值率是市场认可度结果，不是产品力），
  但剥掉不等于不重要——它直接决定三年后卖掉亏多少，是纯电车最大的隐性成本之一
  （精真估 2026 半年报：同级纯电三年保值率约 45%–55%）。旧做法是"只展示、不计分"，
  用户看不到它影响总分。现在单列一维，权重 8%，与"性价比"分开算：
    性价比 = 同价位段产品力/价格残差（产品力维度）
    持有成本 = 三年保值率相对同级中位的残差（持有维度）
  两者不共用一个数字：保值率不进 pf，也不进 value。

口径：
  · 数据源：categories/residual_values.json（单一真相源，A级流通协会×精真估 / B级汽车之家
    中型SUV纯电专项，三年口径）；C 级（估值曲线/含插混/未满三年/来源冲突）只展示不计分。
  · 分数：8.0 + clamp((三年保值率 − 池中位 48%) / 4, −1.5, +1.5)
    —— 每 4 个百分点 ±1 分，±1.5 封顶；中位写死不随入库车型漂移（跨榜同分）。
  · 缺数据 → None，交给 evidence_gate 统一中位插补（不按 0 分、不按差评）。

契约：
  score_one(c, db) -> {"score": float|None, "resale": float|None, "tier": str, "note": str}
  apply(cands, db=None) -> (cands, audit)
  selftest() -> 0
"""
from __future__ import annotations

import json

import paths

BASE = 8.0
MID = 48.0        # 池中位三年保值率（写死，保证扩库不改既有分数）
PER_PP = 4.0      # 每 4 个百分点 1 分
CAP = 1.5
LOW, HIGH = 5.0, 9.5
SCORING_TIERS = ("A", "B")   # C 级口径不可比，只展示不计分


def load_db() -> dict:
    with open(paths.cat("residual_values.json"), encoding="utf-8") as f:
        return json.load(f)


def _norm(name: str) -> str:
    from modules.contracts.model_names import canon
    return canon(name or "")


def lookup(db: dict, model: str) -> tuple[float | None, str]:
    """按 canon 归一匹配保值率；C 级/display_only 一律返回 (None, 'C') 不计分。"""
    values = db.get("values") or {}
    tiers = db.get("value_tier") or {}
    display_only = {str(x) for x in (db.get("display_only") or [])}
    n = _norm(model)
    for k, v in values.items():
        if _norm(k) == n or k == model:
            tier = str(tiers.get(k) or "B")
            if k in display_only or tier not in SCORING_TIERS:
                return None, tier
            if isinstance(v, (int, float)) and v > 0:
                return float(v), tier
    return None, ""


def score_one(c: dict, db: dict | None = None) -> dict:
    db = db or load_db()
    resale, tier = lookup(db, c.get("model") or "")
    if resale is None:
        return {"score": None, "resale": None, "tier": tier,
                "note": ("三年保值率无同口径数据（C级/未覆盖），交中位插补"
                         if tier == "C" else "无三年保值率数据，交中位插补")}
    delta = max(-CAP, min(CAP, (resale - MID) / PER_PP))
    score = round(max(LOW, min(HIGH, BASE + delta)), 2)
    return {"score": score, "resale": resale, "tier": tier,
            "note": f"三年保值率 {resale:g}%（{tier}级源）对同级中位 {MID:g}% → {delta:+.2f} 分"}


def apply(cands: list[dict], db: dict | None = None):
    db = db or load_db()
    audit = []
    for c in cands:
        d = score_one(c, db)
        if d["score"] is not None:
            c.setdefault("scores", {})["holding_cost"] = d["score"]
        c["holding_cost_detail"] = d
        audit.append({"model": c.get("model"), **d})
    return cands, audit


def coverage(cands: list[dict]) -> dict:
    return {"total": len(cands),
            "covered": sum(1 for c in cands
                           if isinstance((c.get("scores") or {}).get("holding_cost"), (int, float)))}


def selftest() -> int:
    db = {"values": {"甲车": 56.0, "乙车": 44.0, "丙车": 48.0},
          "value_tier": {"甲车": "A", "乙车": "B", "丙车": "A"}, "display_only": []}
    a = score_one({"model": "甲车"}, db)
    b = score_one({"model": "乙车"}, db)
    c = score_one({"model": "丙车"}, db)
    assert a["score"] > c["score"] > b["score"], "保值率高应分高、中位应中性"
    assert abs(c["score"] - BASE) < 1e-9, "中位保值率应恰为中性 8.0"
    assert score_one({"model": "丁车"}, db)["score"] is None, "无数据应 None 交插补"
    # C 级只展示不计分
    db2 = dict(db, display_only=["甲车"])
    assert score_one({"model": "甲车"}, db2)["score"] is None, "C级/仅展示不得进分"
    # 封顶
    db3 = {"values": {"高": 80.0, "低": 10.0}, "value_tier": {"高": "A", "低": "A"}, "display_only": []}
    assert score_one({"model": "高"}, db3)["score"] == BASE + CAP
    assert score_one({"model": "低"}, db3)["score"] == BASE - CAP
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())
