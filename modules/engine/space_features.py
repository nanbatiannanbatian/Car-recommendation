# -*- coding: utf-8 -*-
"""space_features.py —— 空间体验要素结构化提取（唯一入口，两类分开）

背景（2026-11-21 用户问「空间是否要考虑前备箱大小、下沉后备箱、储物空间、真实空间表现」）：
  查实候选池 evidence.space_evidence 里其实**记着**这些要素，但系统里没有任何地方用：
    · vehicle_physics.json 没有任何细分字段（连轴距都 0% 覆盖）；
    · experience_facts.json 的 dimension_scope 只有四维，space 根本不在其中；
    · baseline 的 space 是随包人工总分，没有子项。
  → 属于「采了但没用」：得房率 64% 的腾势N7 space=8.5，得房率 85.6% 的小鹏G6 却只有 8.0。

  本模块把这些要素从 space_evidence 文本里结构化出来，严格分两类：
    · REAL（真实空间表现）：得房率 / 后排腿部 / 后排膝部 / 前后排间距 / 车内纵向空间
      —— 决定「乘坐空间」的实际体验，交给 space_consistency 做一致性校验。
    · STORAGE（储物便利）：前备箱 / 后备箱 / 放倒 / 下沉储物 / 隐藏储物
      —— 是 BEV 相对油车的真实增量，但和「乘坐空间」不是一回事，
         只做披露，**不混进 space 分**（避免把储物空间当乘坐空间加分）。

纪律：纯函数、只提取不打分、每条带原文与置信度、待人工核实。
"""
from __future__ import annotations

import re

# 类别常量（外部消费方按此判断「进校验」还是「仅披露」）
CAT_REAL = "real"           # 真实空间表现 → 参与一致性校验
CAT_STORAGE = "storage"     # 储物便利 → 仅披露，不进分

# (key, label, 正则, 单位, 类别)
_PATTERNS = [
    ("efficiency", "得房率",     r"得房率(?:约)?\s*(\d+(?:\.\d+)?)\s*%", "%", CAT_REAL),
    ("leg_room",   "后排腿部空间", r"后排腿部(?:空间)?\s*(\d+)\s*mm", "mm", CAT_REAL),
    ("knee_room",  "后排膝部空间", r"后排膝部(?:空间)?\s*(\d+)\s*mm", "mm", CAT_REAL),
    ("row_gap",    "前后排间距",   r"前后排间距\s*(\d+)\s*mm", "mm", CAT_REAL),
    ("longitudinal", "车内纵向空间", r"车内纵向空间\s*(\d+)\s*mm", "mm", CAT_REAL),
    ("frunk",      "前备箱",     r"前备[箱厢]\s*(\d+(?:\.\d+)?)(?:\s*[-~]\s*\d+(?:\.\d+)?)?\s*[Ll升]", "L", CAT_STORAGE),
    ("trunk",      "后备箱",     r"后备箱(?:约)?\s*(\d+(?:\.\d+)?)(?:\s*[-~]\s*\d+(?:\.\d+)?)?\s*[Ll升]", "L", CAT_STORAGE),
    ("trunk_fold", "后备箱放倒",  r"放倒(?:后)?\s*(?:扩展至|可达)?\s*(\d+)\s*[Ll升]", "L", CAT_STORAGE),
    ("under_floor", "下沉储物",   r"(\d+)\s*[Ll]\s*下沉储物", "L", CAT_STORAGE),
    ("hidden",     "隐藏储物",   r"隐藏储物\s*(\d+)\s*[Ll]", "L", CAT_STORAGE),
]


def _confidence(text: str) -> str:
    """从原文括号标注推断置信度：软文/厂商口径低置信 → low，厂商标定 → medium，否则 high。"""
    if "低置信" in text or "软文口径低置信" in text or "宣传口径" in text:
        return "low"
    if "软文口径" in text or "厂商标定" in text or "厂商口径" in text:
        return "medium"
    return "high"


def extract_space_features(c) -> list[dict]:
    """从候选的 evidence.space_evidence 提取结构化空间要素。

    Returns list[{key,label,value,unit,category,confidence,raw}]，无则 []。
    不自动入库、不打分；每条保留原文片段供人工核实。
    """
    ev = c.get("evidence") if isinstance(c, dict) else None
    text = (ev.get("space_evidence") or "") if isinstance(ev, dict) else ""
    if not text:
        return []
    out = []
    for key, label, pat, unit, cat in _PATTERNS:
        m = re.search(pat, text)
        if not m:
            continue
        # 原文片段：命中前后各截 40 字，便于核实口径
        s, e = max(0, m.start() - 40), min(len(text), m.end() + 40)
        out.append({
            "key": key, "label": label,
            "value": float(m.group(1)) if "." in m.group(1) else int(m.group(1)),
            "unit": unit, "category": cat,
            "confidence": _confidence(text[s:e]),
            "raw": text[s:e].strip(),
        })
    return out


def efficiency_of(c) -> float | None:
    """便捷读取得房率（space_consistency 的真实性信号用）；无数据返回 None。"""
    for f in extract_space_features(c):
        if f["key"] == "efficiency":
            return float(f["value"])
    return None


def by_category(features: list[dict]) -> dict:
    """按 REAL / STORAGE 两类分组，供报告披露与校验分流。"""
    return {
        CAT_REAL: [f for f in features if f["category"] == CAT_REAL],
        CAT_STORAGE: [f for f in features if f["category"] == CAT_STORAGE],
    }


def summarize(per_car: dict) -> dict:
    """汇总进 summary 的审计字段：per_car = {model: features}。"""
    real_n = storage_n = 0
    low_conf = []
    for model, feats in per_car.items():
        grouped = by_category(feats)
        real_n += len(grouped[CAT_REAL])
        storage_n += len(grouped[CAT_STORAGE])
        for f in feats:
            if f["confidence"] == "low":
                low_conf.append(f"{model}:{f['key']}")
    return {
        "cars_with_features": len(per_car),
        "real_count": real_n,
        "storage_count": storage_n,
        "low_confidence": low_conf,
        "note": "real=真实空间表现(参与space一致性校验); storage=储物便利(仅披露,不进分)",
    }


def selftest() -> int:
    c = {"evidence": {"space_evidence": (
        "轴距2940mm、车长4860mm；得房率约64%（厂商口径）。"
        "后排腿部1317mm、后排膝部空间160mm、前后排间距967mm。"
        "后备箱500-1325L+前备箱90L（汽车之家config、官网一致）。"
        "另有139L下沉储物与隐藏储物670L。"
    )}}
    f = extract_space_features(c)
    got = {x["key"]: x["value"] for x in f}
    assert got.get("efficiency") == 64, got
    assert got.get("leg_room") == 1317 and got.get("knee_room") == 160, got
    assert got.get("row_gap") == 967, got
    assert got.get("frunk") == 90 and got.get("trunk") == 500, got
    assert got.get("under_floor") == 139 and got.get("hidden") == 670, got
    g = by_category(f)
    assert all(x["category"] == CAT_REAL for x in g[CAT_REAL]), "真实表现归类错"
    assert all(x["category"] == CAT_STORAGE for x in g[CAT_STORAGE]), "储物归类错"
    assert efficiency_of(c) == 64
    # 低置信识别
    c2 = {"evidence": {"space_evidence": "得房率约85.6%（厂商标定，软文口径低置信）"}}
    assert extract_space_features(c2)[0]["confidence"] == "low"
    # 无 space_evidence → 空
    assert extract_space_features({"model": "无"}) == []
    # 不误伤：百分比快充不是得房率
    assert extract_space_features({"evidence": {"space_evidence": "充电10-80%用时22分钟"}}) == []
    # 统计
    s = summarize({"甲": f})
    assert s["real_count"] == len(g[CAT_REAL]) and s["storage_count"] == len(g[CAT_STORAGE]), s
    return 0


if __name__ == "__main__":
    import sys
    import paths
    sys.path.insert(0, str(paths.ROOT))
    print("space_features selftest:", "PASS" if selftest() == 0 else "FAIL")
