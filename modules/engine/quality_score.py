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

# v10.19.0（用户 2026-11-22 拍板）：纯软件 OTA 修复的召回暂不纳入质量扣分。
# 根因而非补丁：旧逻辑按「召回次数 × 严重度」机械累加、触顶 -3，会把
# 「软件缺陷经 OTA 无损修复」等同于「硬件安全隐患」，与本项目「召回不等于质量差」
# 的既定边界冲突（Model Y 4 起软件召回曾一次扣满 3 分）。改为按处置方式(remedy)
# 区分，任何车型的新召回只要标 ota 即自动跳过，不按车型名硬编码。
# 恢复口径只需把此开关改 False——一处开关、全链路生效。
OTA_RECALL_EXCLUDED = True

# 纯软件 OTA 的特征词；出现线下物理作业词即判为非纯 OTA（仍需扣分）。
_OTA_WORDS = ("OTA", "ota", "远程升级", "软件升级")
_PHYSICAL_WORDS = ("加贴", "粘贴", "标签", "标识", "更换", "换件", "加装",
                   "返厂", "线下", "检修", "维修", "拧紧", "重装")


def is_ota_recall(event: dict) -> bool:
    """判定单条召回是否为「纯软件 OTA 修复」（暂不纳入扣分）。

    这是 OTA 判定的唯一真相源，供扣分与审计共用：
      · 结构化字段 remedy 优先：'ota' → True；'physical'/'mixed'/... → False；
      · remedy 缺失时文本兜底：措施段含 OTA 词且不含任何线下物理作业词 → True。
    只检查「措施：」之后的文本，避免缺陷描述里的词误伤判定。
    """
    remedy = str(event.get("remedy") or "").strip().lower()
    if remedy == "ota":
        return True
    if remedy in ("physical", "mixed", "hardware", "line", "offline", "物理", "线下", "混合"):
        return False
    reason = str(event.get("reason") or "")
    if not any(w in reason for w in _OTA_WORDS):
        return False
    measures = reason.split("措施：")[-1] if "措施：" in reason else reason
    return not any(w in measures for w in _PHYSICAL_WORDS)


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
    density = min(2.2, 2.0 * math.log1p(per) / math.log1p(50.0))  # v9.7.0 密度封顶2.6→2.2
    top_tags = [str(t) for t, _ in (v.get("top_tags") or [])]
    hi = sum(1 for t in top_tags if t in HIGH_TAGS)
    mid = sum(1 for t in top_tags if t in MID_TAGS)
    severe = min(0.8, hi * 0.3 + mid * 0.1)  # v9.7.0 收紧：1.2→0.8
    total = density + severe
    tag_txt = "、".join(top_tags) or "无高频标签"
    return total, (f"车质网 90 天 {count} 条 / {per:g}/万台；"
                   f"密度扣分 {density:.2f}，问题性质扣分 {severe:.2f}（{tag_txt}）")


def _complaint_pph_penalty(c: dict) -> tuple[float, str]:
    """车质网投诉销量比（万分之）→ 质量扣分（v10.62 根因修复）。

    背景：quality_score 文档写明数据源含"车质网投诉聚合"，但实现只支持
    "90天投诉明细"形态（complaints.json 的 count/per_10k）。网查到的车质网
    PPH 比值（13 款车有，如唐L 643.8/万、小米YU7 21.2/万）无处可放，导致
    这些车全拿中性 8.0——用户质疑"质量分全 8 不合理"。
    修复：与 _complaint_penalty 共用同一密度公式（log 压缩、封顶 2.2），
    两种形态数据按"有明细用明细、无明细用 PPH 比值"优先级取，不双计。
    PPH 是车系级（增程/纯电可能共用），故同样标注口径，保守处理。
    """
    pph = c.get("complaint_pph")
    if not isinstance(pph, (int, float)) or pph <= 0:
        return 0.0, "无车质网投诉销量比数据"
    src = str(c.get("complaint_pph_source") or "车系级")
    density = min(2.2, 2.0 * math.log1p(float(pph)) / math.log1p(50.0))
    return density, (f"车质网投诉销量比 {float(pph):g}/万辆（{src}，按车系口径保守）；"
                     f"密度扣分 {density:.2f}")


def _pph_adjust(c: dict) -> tuple[float, str]:
    if not c.get("ah_pph_usable"):
        return 0.0, "PPH 样本不足，中性处理"
    pph = float(c.get("ah_pph") or 0)
    raw = _clamp((100.0 - pph) / 100.0, -1.5, 1.0)  # v9.7.0 正向空间0.6→1.0
    n = int(c.get("ah_review_users") or 0)
    # v10.16.2（用户举证：乐道L60 PPH样本仅44却按满额扣0.27）：
    # 样本≥30 就满额生效会让小样本噪声直接变成扣分，而样本不足的车反而拿中性8.0，
    # 形成「有数据者被罚、无数据者中性」的结构性不对称。按 n/(n+50) 收缩，
    # 样本越大越接近原始幅度，44样本只按约47%计入，消除小样本过度惩罚。
    shrink = n / (n + 50.0) if n > 0 else 0.0
    adj = raw * shrink
    return adj, (f"汽车之家 PPH {pph:g}（口碑样本 {n}，按 n/(n+50) 收缩 "
                 f"{shrink:.2f}），调整 {adj:+.2f}")


def _recall_penalty(c: dict) -> tuple[float, str]:
    events = c.get("recall_events") or []
    if not events:
        return 0.0, "本期无结构化召回事件，不扣分"
    # v10.19.0：纯软件 OTA 召回按用户口径暂不纳入扣分；物理/混合仍按严重度扣。
    # 判定统一走 is_ota_recall（结构化 remedy 优先、文本兜底），不按车型名硬编码。
    counted, ota_skipped = [], []
    for e in events:
        if OTA_RECALL_EXCLUDED and is_ota_recall(e):
            ota_skipped.append(e)
        else:
            counted.append(e)
    pen = 0.0
    for e in counted:
        sev = str(e.get("severity") or "normal").lower()
        pen += 1.5 if sev in ("high", "严重", "高危") else 0.5
    pen = min(3.0, pen)
    note = f"召回 {len(counted)} 次计入"
    if counted:
        note += f"，扣分 -{pen:g}"
    else:
        note += "，不扣分"
    if ota_skipped:
        note += f"（另有 {len(ota_skipped)} 起纯软件OTA按口径不纳入）"
    return -pen, note


def score_one(c: dict, complaint: dict | None) -> dict:
    comp_pen, comp_note = _complaint_penalty(complaint or {})
    # v10.62：投诉明细优先；无明细时用网查车质网PPH比值（同一密度公式，不双计）
    has_detail = int((complaint or {}).get("count") or 0) >= 5 and (complaint or {}).get("per_10k") is not None
    if not has_detail and c.get("complaint_pph"):
        comp_pen, comp_note = _complaint_pph_penalty(c)
    pph_adj, pph_note = _pph_adjust(c)
    rec_adj, rec_note = _recall_penalty(c)
    raw = BASE - comp_pen + pph_adj + rec_adj
    score = round(_clamp(raw, LOW, HIGH), 2)

    has_complaint = int((complaint or {}).get("count") or 0) >= 5
    has_pph = bool(c.get("ah_pph_usable"))
    has_web_pph = bool(c.get("complaint_pph"))  # v10.62：网查PPH也算有据，提置信
    if has_complaint and has_pph:
        conf = "high"
    elif has_complaint or has_pph or has_web_pph:
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
        "complaint_pph": c.get("complaint_pph"),  # v10.62 车质网PPH(网查)
        "complaint_pph_source": c.get("complaint_pph_source"),
        "recall_events": c.get("recall_events") or [],
        # v10.19.0：审计用——有几起纯软件OTA召回按口径不纳入（单一真相源判定）
        "recall_ota_excluded": sum(1 for e in (c.get("recall_events") or [])
                                   if OTA_RECALL_EXCLUDED and is_ota_recall(e)),
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
        "> **读分须注意（v9.6.2 披露）**：① 投诉数已按销量归一化（密度=投诉/万台、对数压缩），"
        "不是「卖得多扣分多」；② 但分母是**单月趋势销量**、投诉是 **90 天累计**，口径不完全对齐，"
        "密度会被高估（保守方向），不作精确质量排序；③ 投诉有自选择偏差（沉默的大多数不投诉），"
        "「无投诉」只代表**没证据**（低置信），不等于质量好——见置信列；"
        "④ v9.7.0 温和平衡：投诉密度封顶 2.6→2.2、性质扣分上限 1.2→0.8；PPH 正向空间 0.6→1.0，让口碑有实质对冲但不反转（刷口碑仍难盖过高投诉）；"
        "⑤ v10.16.2 样本收缩：PPH 调整按 n/(n+50) 缩放，样本不足的车保持中性 8.0 不因「有数据」反被重罚——"
        "小样本不等于质量差，置信列与样本数必须一起读；"
        "⑥ v10.19.0 OTA 口径：召回按处置方式区分，**纯软件 OTA 修复（remedy=ota）暂不纳入扣分**，"
        "线下物理/混合措施（加贴标识、换件等）仍按严重度扣。判定统一走 is_ota_recall（结构化字段优先、文本兜底），"
        "不按车型硬编码；开关 OTA_RECALL_EXCLUDED 一处可切换。",
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
