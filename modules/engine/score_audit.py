# -*- coding: utf-8 -*-
"""score_audit.py —— 人工分全量核算审计（唯一入口）

问题（2026-11-21 用户「全量检查哪些数据存在人工分没有算，一项项核算，加入测试」）：
  查实 8 维分数里，只有 safety 有完整计算链（官方映射+表分同步+举证封顶）；
  quality 需 --quality 开关才算、默认出榜是人工分；value/powertrain 基本纯人工；
  adas/cabin/control/space 是人工分 + 封顶/调整，基础分仍人工拍的。
  而 spec_score.py 早就写好了 7 维的「按规则算分」，却从没接进出榜链路——
  原因是它依赖的 21 个结构化字段（轴距/TOPS/800V/芯片/悬架…）候选池一个都没有，
  这些事实全散落在 evidence.*_evidence 文本里，于是规则从设计那天起就没跑过。

  本模块做两件事（都是「审计」，不自动改分——改分是评分口径变更，须人工确认）：
    A. 链路盘点：逐维度给出「有无规则 / 规则依赖字段 / 默认出榜是否算」的机读表，
       标出三类缺口：NO_RULE(无规则) / RULE_NO_DATA(有规则没数据) / RULE_NOT_WIRED(有规则没接链路)。
    B. 逐项核算：从 evidence 文本提取可结构化的硬参数，喂给 spec_score 的同一套规则，
       算出「客观分」与人工分对照，列出差异超阈值的车+维度（DIFF 清单）。

纪律：纯函数、只审计不打分（不改候选 scores）、每条差异带依据原文与算分理由、
低覆盖维度标 imputed 不参与结论。与 space_consistency/evidence_gate 同一套「只报不擅改」原则。
"""
from __future__ import annotations

import re
import statistics

# 8 维链路盘点元数据
# wired = 默认出榜链路上是否有「从客观数据算分」的步骤
# rule  = 是否存在按规则算分的模块
DIM_LINKS = {
    "safety":     {"rule": True,  "wired": True,  "module": "contracts/safety_score.py + engine/safety_check.py",
                   "note": "官方碰撞成绩→分数映射+表分同步+举证封顶，完整闭环"},
    "quality":    {"rule": True,  "wired": False, "module": "engine/quality_score.py",
                   "note": "有 PPH/投诉算分规则，但需 --quality 开关；默认出榜用人工分+举证封顶"},
    "space":      {"rule": True,  "wired": "partial", "module": "engine/space_consistency.py + spec_score.score_space",
                   "note": "有尺寸一致性封顶+得房率信号，但基础分人工；spec_score 规则没接（缺轴距字段）"},
    "adas":       {"rule": True,  "wired": "partial", "module": "engine/experience_score.py + spec_score.score_adas",
                   "note": "有实测证据调整+封顶，基础分人工；TOPS/激光雷达/NOA 规则没接"},
    "cabin":      {"rule": True,  "wired": "partial", "module": "engine/experience_score.py + spec_score.score_cabin",
                   "note": "有实测调整+封顶，基础分人工；8295/屏幕规则没接"},
    "control":    {"rule": True,  "wired": "partial", "module": "engine/experience_score.py + spec_score.score_control",
                   "note": "有麋鹿证据调整+封顶，基础分人工；悬架/驱动/零百规则没接"},
    "powertrain": {"rule": True,  "wired": False, "module": "engine/spec_score.py",
                   "note": "有 800V/快充/电芯/续航算分规则，但依赖字段没进候选池，纯人工分"},
    "value":      {"rule": True,  "wired": "partial", "module": "engine/spec_score.py + experience_score",
                   "note": "有同榜比价规则+保值率调整，但比价规则没接（价格字段有但没喂给 score_value）"},
}

# 从 evidence 文本提取 spec_score 依赖字段的正则（保守：只提可靠硬参数）
_EXTRACT = [
    ("adas_tops",       r"(\d{2,4})\s*TOPS", "adas_evidence", float),
    ("adas_lidar",      r"激光雷达", "adas_evidence", lambda _: True),
    ("adas_city_noa",   r"城市NZP|城区NOA|城市NOA|无图城市", "adas_evidence", lambda _: True),
    ("adas_highway_noa", r"高速NZP|高速NOA", "adas_evidence", lambda _: True),
    ("cabin_chip",      r"(8295\w*|8155|8775)", "cabin_evidence", str),
    ("screen_inch",     r"(\d{1,2}(?:\.\d+)?)\s*英寸中控", "cabin_evidence", float),
    ("wheelbase_mm",    r"轴距\s*(\d{4})mm", "space_evidence", float),
    ("length_mm",       r"车长\s*(\d{4})mm", "space_evidence", float),
    ("seats",           r"(\d)座", "space_evidence", int),
    ("cncap_pct",       r"综合得分率\s*(\d+(?:\.\d+)?)%", "crash_evidence", float),
    ("susp_front",      r"前(双叉臂|双横臂|麦弗逊)", "control_evidence", str),
    ("susp_rear",       r"后(五连杆|多连杆|扭力梁)", "control_evidence", str),
    ("drive",           r"(后置后驱|后驱|四驱|双电机四驱)", "control_evidence", str),
    ("zero_to_100_s",   r"零百\s*(\d+(?:\.\d+)?)s", "control_evidence", float),
]


def extract_spec_fields(c: dict) -> dict:
    """从候选 evidence 文本提取可结构化的硬参数（只提可靠项，供 spec_score 复用）。"""
    ev = c.get("evidence") or {}
    out = {}
    for key, pat, sub, conv in _EXTRACT:
        text = ev.get(sub) or ""
        m = re.search(pat, text)
        if not m:
            continue
        try:
            out[key] = conv(m.group(1) if m.groups() else m.group(0))
        except (ValueError, TypeError):
            continue
    return out


def link_audit() -> list[dict]:
    """链路盘点：逐维度给出缺口分类。"""
    rows = []
    for dim, meta in DIM_LINKS.items():
        if not meta["rule"]:
            gap = "NO_RULE"
        elif not meta["wired"]:
            gap = "RULE_NOT_WIRED"
        elif meta["wired"] == "partial":
            gap = "PARTIAL_WIRED"
        else:
            gap = "WIRED"
        rows.append({"dim": dim, "gap": gap, **meta})
    return rows


def compute_objective(c: dict, peer_prices: list[float] | None = None) -> tuple[dict, dict, list[str]]:
    """用 spec_score 的同一套规则，从 evidence 提取的字段算客观分。

    Returns (scores, reasons, imputed)。不改候选。复用 spec_score 保证与既有规则一致。
    """
    from modules.engine import spec_score as SS
    spec = extract_spec_fields(c)
    price = None
    pg = c.get("price_guide")
    if isinstance(pg, (list, tuple)) and pg:
        price = float(pg[0])
    elif isinstance(pg, (int, float)):
        price = float(pg)
    # value 必须同榜比价；peer_prices 缺省时标 imputed
    scores, reasons, imputed = SS.score_car(spec, price, peer_prices)
    return scores, reasons, imputed


def diff_audit(cands: list[dict], threshold: float = 0.5, skip_wired=True) -> dict:
    """逐车逐维核算：客观分 vs 人工分，列出差异超阈值项。不自动改分。

    skip_wired=True 时跳过「链路已完整闭环」的维度（safety）：它已有更权威的算分规则
    （官方碰撞映射），spec_score 的简化规则会造成伪差异（如无成绩车 −0.7 vs 映射表分），
    拿它当「人工分没算」的证据会误导。链路展示仍保留在 link_audit() 里。
    """
    from collections import defaultdict
    skip_dims = {d for d, m in DIM_LINKS.items() if m["wired"] is True} if skip_wired else set()
    by_cls = defaultdict(list)
    for c in cands:
        pg = c.get("price_guide")
        p = float(pg[0]) if isinstance(pg, (list, tuple)) and pg else (float(pg) if isinstance(pg, (int, float)) else None)
        if p:
            by_cls[c.get("cls") or "全部"].append(p)

    diffs = []
    coverage = defaultdict(int)
    for c in cands:
        peers = by_cls.get(c.get("cls") or "全部") or []
        obj, reasons, imputed = compute_objective(c, peers)
        for dim in ("adas", "powertrain", "space", "control", "safety", "cabin", "value"):
            if dim in skip_dims:
                continue
            if dim in imputed or obj.get(dim) is None:
                continue
            coverage[dim] += 1
            human = (c.get("scores") or {}).get(dim)
            if not isinstance(human, (int, float)):
                continue
            d = round(float(obj[dim]) - float(human), 2)
            if abs(d) >= threshold:
                diffs.append({"model": c.get("model"), "dim": dim, "human": human,
                              "objective": obj[dim], "diff": d,
                              "basis": "；".join(reasons.get(dim, []))[:200]})
    diffs.sort(key=lambda x: -abs(x["diff"]))
    return {"threshold": threshold, "checked": len(cands), "coverage": dict(coverage),
            "skipped_wired": sorted(skip_dims), "diff_count": len(diffs), "diffs": diffs,
            "note": "只审计不自动改分；差异=『按现有规则从evidence算出的客观分』与人工分对照，是待核实线索不是改分指令；"
                    "硬件强≠体验好（零实测车已由evidence_gate封顶），差异须人工核实来源后决定"}


def summarize(audit: dict) -> dict:
    by_dim = {}
    for d in audit["diffs"]:
        by_dim.setdefault(d["dim"], []).append({"model": d["model"], "diff": d["diff"]})
    return {
        "checked": audit["checked"], "coverage": audit["coverage"],
        "diff_count": audit["diff_count"],
        "by_dim": {k: {"count": len(v), "top": sorted(v, key=lambda x: -abs(x["diff"]))[:5]}
                   for k, v in sorted(by_dim.items())},
    }


def selftest() -> int:
    # 字段提取：极氪7X 的硬参数
    c = {"evidence": {
        "adas_evidence": "芯片英伟达Thor-U，算力700 TOPS；1颗禾赛激光雷达；支持无图城市NZP、高速NZP。",
        "cabin_evidence": "车机芯片高通骁龙8295P；16英寸中控屏。",
        "space_evidence": "轴距2925mm，车长4825mm，5座。",
        "crash_evidence": "综合得分率90.7%，五星。",
        "control_evidence": "前双叉臂+后五连杆；后置后驱，零百5.1s。",
    }}
    f = extract_spec_fields(c)
    assert f.get("adas_tops") == 700 and f.get("adas_lidar") is True, f
    assert f.get("adas_city_noa") is True and f.get("adas_highway_noa") is True, f
    assert "8295" in str(f.get("cabin_chip")), f
    assert f.get("wheelbase_mm") == 2925 and f.get("length_mm") == 4825, f
    assert f.get("cncap_pct") == 90.7 and abs(f.get("zero_to_100_s", 0) - 5.1) < 1e-6, f
    assert "双叉臂" in str(f.get("susp_front")) and "五连杆" in str(f.get("susp_rear")), f

    # 链路盘点：分类正确
    links = {r["dim"]: r["gap"] for r in link_audit()}
    assert links["safety"] == "WIRED", links
    assert links["powertrain"] == "RULE_NOT_WIRED", links
    assert links["quality"] == "RULE_NOT_WIRED", links
    assert links["space"] == "PARTIAL_WIRED", links

    # 核算：不改候选分数
    import copy
    c2 = copy.deepcopy(c)
    c2["model"] = "核算车A"
    c2["scores"] = {"adas": 5.0, "powertrain": 6.0, "space": 6.0, "control": 6.0,
                    "safety": 5.0, "cabin": 5.0, "value": 6.0}
    c2["price_guide"] = [20.0]
    snap = copy.deepcopy(c2["scores"])
    audit = diff_audit([c2], threshold=0.5)
    assert c2["scores"] == snap, "审计不得改候选分数"
    # adas 人工5.0，客观分(700TOPS+激光雷达+城市NOA+高速NOA=5+2+1.6+1.2+0.4=10.2→10.0) 差5.0 → 应检出
    adas_diffs = [d for d in audit["diffs"] if d["dim"] == "adas"]
    assert adas_diffs and adas_diffs[0]["diff"] > 0, f"应检出 adas 人工分偏低: {audit['diffs']}"

    # 小样本：threshold 很大 → 无差异
    assert diff_audit([c2], threshold=99)["diff_count"] == 0

    # skip_wired：默认跳过 safety（已用官方映射闭环），不产生伪差异；
    # 显式 skip_wired=False 才纳入（验证这个开关本身有效）
    c3 = {"model": "无碰撞车", "scores": {"safety": 9.0, "adas": 5.0},
          "evidence": {"crash_evidence": "无权威碰撞成绩"}}
    a_skip = diff_audit([c3], threshold=0.5, skip_wired=True)
    assert not any(d["dim"] == "safety" for d in a_skip["diffs"]), "已闭环 safety 不该产生伪差异"
    a_full = diff_audit([c3], threshold=0.5, skip_wired=False)
    assert any(d["dim"] == "safety" for d in a_full["diffs"]), "skip_wired=False 应纳入 safety"
    return 0


if __name__ == "__main__":
    import sys
    import paths
    sys.path.insert(0, str(paths.ROOT))
    print("score_audit selftest:", "PASS" if selftest() == 0 else "FAIL")
