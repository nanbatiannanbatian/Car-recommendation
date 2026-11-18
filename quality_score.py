#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""质量与品控维度（quality_score.py）——只服务综合打分榜，不改事实榜。

口径：
  · 评分对象：车型级（车质网/PPH/召回通常不按具体 trim 拆分）。
  · 数据源：车质网 90 天投诉聚合、汽车之家 PPH、候选池可选 recall_events。
  · 缺失处理：样本不足/无数据 = 中性 8.0 + 低置信，不按 0 分、不按差评。
  · 防双计：原 7 维权重统一缩到 90%，质量与品控只占 10%；
    三电/车机已有的口碑信息不再额外加第二次分。
"""
from __future__ import annotations

import math

SCORING_ENABLED = True
BASE = 8.0
LOW = 3.0
HIGH = 9.5

HIGH_TAGS = {"转向", "制动", "刹车", "电池", "失灵", "失效", "无法启动", "无法充电"}
MID_TAGS = {"充电", "车机", "黑屏", "续航", "悬挂", "变速箱", "生锈", "爆漆"}


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _complaint_penalty(v: dict) -> tuple[float, str]:
    count = int(v.get("count") or 0)
    if count < 5:
        return 0.0, f"车质网 {count} 条，样本<5，不扣分"
    per = v.get("per_10k")
    if per is None:
        return 0.0, f"车质网 {count} 条，销量缺，密度不可算，不扣分"
    per = float(per)
    density = min(2.6, 2.2 * math.log1p(per) / math.log1p(50.0))
    top_tags = [str(t) for t, _ in (v.get("top_tags") or [])]
    hi = sum(1 for t in top_tags if t in HIGH_TAGS)
    mid = sum(1 for t in top_tags if t in MID_TAGS)
    severe = min(1.2, hi * 0.4 + mid * 0.15)
    total = density + severe
    tag_txt = "、".join(top_tags) or "无高频标签"
    return total, (f"车质网 90 天 {count} 条 / {per:g}/万台；"
                   f"密度扣分 {density:.2f}，问题性质扣分 {severe:.2f}（{tag_txt}）")


def _pph_adjust(c: dict) -> tuple[float, str]:
    if not c.get("ah_pph_usable"):
        return 0.0, "PPH 样本不足，中性处理"
    pph = float(c.get("ah_pph") or 0)
    adj = _clamp((100.0 - pph) / 100.0, -1.5, 0.6)
    n = int(c.get("ah_review_users") or 0)
    return adj, f"汽车之家 PPH {pph:g}（口碑样本 {n}），调整 {adj:+.2f}"


def _recall_penalty(c: dict) -> tuple[float, str]:
    events = c.get("recall_events") or []
    if not events:
        return 0.0, "本期无结构化召回事件，不扣分"
    pen = 0.0
    for e in events:
        sev = str(e.get("severity") or "normal").lower()
        pen += 1.5 if sev in ("high", "严重", "高危") else 0.5
    pen = min(3.0, pen)
    return -pen, f"召回 {len(events)} 次，扣分 -{pen:g}"


def score_one(c: dict, complaint: dict | None) -> dict:
    comp_pen, comp_note = _complaint_penalty(complaint or {})
    pph_adj, pph_note = _pph_adjust(c)
    rec_adj, rec_note = _recall_penalty(c)
    raw = BASE - comp_pen + pph_adj + rec_adj
    score = round(_clamp(raw, LOW, HIGH), 2)

    has_complaint = int((complaint or {}).get("count") or 0) >= 5
    has_pph = bool(c.get("ah_pph_usable"))
    if has_complaint and has_pph:
        conf = "high"
    elif has_complaint or has_pph:
        conf = "medium"
    else:
        conf = "low"
    if c.get("lifecycle", {}).get("status") == "new_launch" and conf != "high":
        conf = "low"

    detail = {
        "score": score,
        "confidence": conf,
        "complaint": {
            "count": int((complaint or {}).get("count") or 0),
            "per_10k": (complaint or {}).get("per_10k"),
            "top_tags": (complaint or {}).get("top_tags") or [],
        },
        "pph": c.get("ah_pph") if has_pph else None,
        "pph_usable": has_pph,
        "recall_events": c.get("recall_events") or [],
        "note": "；".join([comp_note, pph_note, rec_note]),
    }
    return detail


def apply(cands: list[dict], complaints: dict | None = None) -> tuple[list[dict], list[dict]]:
    """把 scores.quality 写入候选池；同一输入重复执行结果一致。"""
    models = (complaints or {}).get("models") or {}
    audit = []
    for c in cands:
        d = score_one(c, models.get(c.get("model")))
        c.setdefault("scores", {})["quality"] = d["score"]
        c["quality_detail"] = d
        audit.append({"model": c.get("model"), **d})
    return cands, audit


def coverage(cands: list[dict]) -> dict:
    out = {"total": len(cands), "high": 0, "medium": 0, "low": 0,
           "complaint_ge5": 0, "pph_usable": 0, "recall": 0}
    for c in cands:
        d = c.get("quality_detail") or {}
        out[d.get("confidence", "low")] = out.get(d.get("confidence", "low"), 0) + 1
        out["complaint_ge5"] += int(int((d.get("complaint") or {}).get("count") or 0) >= 5)
        out["pph_usable"] += int(bool(d.get("pph_usable")))
        out["recall"] += int(bool(d.get("recall_events")))
    return out


def report(cands: list[dict]) -> str:
    cov = coverage(cands)
    lines = [
        "# 质量与品控（综合打分榜新增第 8 维）",
        "",
        "> 本维度只进综合打分榜；配置级事实榜不改名次。车型级、10 分制，基准权重 **10%**，"
        "原 7 维按 0.9 等比缩放，避免把同一份三电/车机口碑算第二遍。",
        "",
        "口径：车质网 90 天投诉（按编号去重，密度=投诉/万台，样本<5 不扣分）"
        "+ 汽车之家 PPH（样本≥30 才生效）+ 可选的官方召回事件。"
        "无数据 = 中性 8.0 + 低置信，不按 0 分，也不判为质量差。",
        "",
        f"> 覆盖：{cov['total']} 款；车质网投诉≥5 条 {cov['complaint_ge5']} 款；"
        f"PPH 可用 {cov['pph_usable']} 款；召回事件 {cov['recall']} 款；"
        f"置信 high/medium/low = {cov['high']}/{cov['medium']}/{cov['low']}。",
        "",
        "| 车型 | 品控分 | 置信 | 依据 |",
        "|---|---:|---|---|",
    ]
    for c in sorted(cands, key=lambda x: -float(((x.get("quality_detail") or {}).get("score")) or 0)):
        d = c.get("quality_detail") or {}
        lines.append(f"| {c.get('model')} | {d.get('score', '—')} | "
                     f"{d.get('confidence', '—')} | {d.get('note', '—')} |")
    lines += [
        "",
        "> 边界：座椅后钢板、单条拆车视频、网帖个例、门店服务态度主观感受均不进分；"
        "召回不等于质量差，仅在有结构化公告且明确批次时按严重度有限扣分。",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    import json
    import os

    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--complaints", help="fetch_complaints 聚合 JSON")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    data = json.load(open(a.candidates, encoding="utf-8"))
    cands = data.get("candidates", data)
    cdata = json.load(open(a.complaints, encoding="utf-8")) if a.complaints and os.path.exists(a.complaints) else None
    cands, _ = apply(cands, cdata)
    data["candidates"] = cands
    json.dump(data, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[ok] {a.out}")
