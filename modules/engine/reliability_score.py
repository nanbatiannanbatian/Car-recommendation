#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""reliability_score.py —— 可靠性维度（v10.74.0，用户要求拆维）

为什么要拆（2026-11-25 用户直接指示）：
  旧「质量与品控」一格混装了两种性质完全不同的证据：
    ① 故障**频度**：多少台车出多少问题（车质网投诉密度、汽车之家 PPH）；
    ② 故障**严重度**：出的是什么问题、召回怎么处置（安全类标签、物理/OTA）。
  混在一格有两个后果：权重只能给 9.5% 一刀切（频度这种最该重的信号被稀释）；
  拆开后又必须防双计——所以这里做的是**分量切分**，不是同一份数据再算一遍。

口径（与 quality_score 的 components 共用同一批原始字段，只取频度分量）：
  reliability = 8.0 − 投诉密度扣分 + PPH 相对中位调整
  其中：密度来自车质网 90 天投诉 per_10k（或网查 complaint_pph，二选一不双计）；
        PPH 调整来自汽车之家百车故障数（中位 151，低故障加、高故障扣，样本不足不调）。
  召回**不计入可靠性**（召回是"出问题后的处置"，属质量严重度），避免同一事件算两遍。
  缺数据 → 返回 None，交给 evidence_gate 统一中位插补（不按 0 分、不按差评）。

契约：
  score_one(c, complaint) -> {"score": float|None, "components": {...}, "note": str}
  apply(cands, complaints) -> (cands, audit)
  selftest() -> 0
"""
from __future__ import annotations

BASE = 8.0
LOW = 3.0
HIGH = 9.5


def score_one(c: dict, complaint: dict | None = None) -> dict:
    from modules.engine import quality_score as QS
    comp = QS.components(c, complaint or {})
    density = float(comp.get("density") or 0.0)
    pph_adj = float(comp.get("pph_adj") or 0.0)
    has_signal = bool(comp.get("has_freq_signal"))
    raw = BASE - density + pph_adj
    score = round(max(LOW, min(HIGH, raw)), 2) if has_signal else None
    note = comp.get("freq_note") or ""
    return {"score": score, "components": {"density": round(density, 3),
                                           "pph_adj": round(pph_adj, 3)},
            "confidence": comp.get("confidence"), "note": note}


def apply(cands: list[dict], complaints: dict | None = None):
    """把 scores.reliability 写入候选池；返回 (cands, audit)。"""
    models = (complaints or {}).get("models") or {}
    audit = []
    for c in cands:
        d = score_one(c, models.get(c.get("model")))
        if d["score"] is not None:
            c.setdefault("scores", {})["reliability"] = d["score"]
        c["reliability_detail"] = d
        audit.append({"model": c.get("model"), **d})
    return cands, audit


def coverage(cands: list[dict]) -> dict:
    return {"total": len(cands),
            "covered": sum(1 for c in cands
                           if isinstance((c.get("scores") or {}).get("reliability"), (int, float)))}


def selftest() -> int:
    # 高频度 → 低分；零信号 → None（交插补）
    hi = {"complaint_pph": 300.0}
    lo = {"complaint_pph": 30.0}
    assert score_one(hi)["score"] < score_one(lo)["score"], "频度越高可靠性应越低"
    assert score_one({})["score"] is None, "无信号应返回 None 交插补"
    # PPH 低故障应加分；召回不进可靠性（同一车加召回后分不变）
    base = {"ah_pph": 100.0, "ah_pph_usable": True, "ah_review_users": 300}
    with_recall = dict(base, recall_events=[{"severity": "high", "remedy": "physical"}])
    assert score_one(base)["score"] == score_one(with_recall)["score"], \
        "召回不得计入可靠性（属质量严重度，防止同一事件算两遍）"
    assert score_one(base)["score"] > 8.0, "低 PPH（151 基准以下）应高于中性"
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(selftest())
