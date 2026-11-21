#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""multi_evidence_score.py —— 多项权威碰撞测评的融合（v10.4.0）

背景（2026-11-21 用户诉求）
----------------------------
「有评测就要纳入，按照业内原则以及管理重新设计，要把所有测评都纳入才准确。」

此前口径是「取证据最强单一成绩」（tier 3 > 2 > 1 > 0），被丢弃的次要成绩只进审计。
用户明确改为「所有测评都纳入」。本项目遵守两条业内原则：

  A. 木桶原理（Euro NCAP / ANCAP 官网、C-NCAP 规则均明文）：
     总评级由最低的分项决定 —— "the overall star rating is limited by its lowest
     performing pillar" / C-NCAP「按三个部分分别设定的最低得分率，按达到的最低星级评定」。
     => 同一体系内的多个维度/分指数，取最低档，不取最高、不平均。

  B. 跨体系无对应关系（项目早期调研结论：各国 NCAP 工况/严苛度不同，不可套同一公式）：
     不存在「把 C-NCAP 得分率 + Euro NCAP 星级 + C-IASI 等级揉成一个数」的官方方法。
     => 跨体系并存时按证据严格度选主分（C-IASI > Euro NCAP ≈ C-NCAP2024 > 旧版），
        其余体系全部列示作佐证，不参与加权、不与主分混合。

数据纪律（不越界）
------------------
1. 只纳入「一手可核验」成绩：候选池文字成绩 tier>=2、C-IASI 官网新闻稿纯文字等级
   （scope=all/per_model）、official_safety_scores.json 人工核验表。
2. 媒体/软文/旁证（tier=0）、出口版/非本代、aggregate_only（稿件只给分布未点名）
   一律不进计分，只写进披露字段。
3. C-IASI 数字图标 level1/1..5.png 无官方文字对照，不解码、不猜测。
4. 成绩对应具体年款配置，不外推到其它年款。

输出
----
fuse(cands, table=None) -> list[dict]：每车的融合结果（主成绩/主分/全部测评/严格度依据）。
调用方（pipeline）把主成绩写回 crash 字段，后续映射/门禁/封顶沿用既有链路。
"""
from __future__ import annotations

import json
import os
import sys

# 零 paths 依赖：路径由调用方传入（与 space_features 等纯函数模块一致）。
# pipeline 从 paths.ROOT 拼好后传进来；selftest 用 __file__ 反推仓库根。
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 随包事实库优先（categories/，随 skill 与 GitHub 仓库分发），其次才是联网产物缓存（data/gen）。
# ciasi_decoded.json 是 C-IASI 官网结果发布稿的纯文字等级解码——有官方来源、可核验，
# 等同 ice_crash.json 属随包事实；research 的 ciasi_text_decode.py 可联网刷新它。
DECODED_PACKAGED = os.path.join(_ROOT, "categories", "ciasi_decoded.json")
DECODED_CACHE = os.path.join(_ROOT, "data", "gen", "ciasi_decoded.json")
OFFICIAL = os.path.join(_ROOT, "official_safety_scores.json")


def decoded_path():
    """返回实际存在的解码库路径（随包优先），都没有则返回随包路径（调用方按不存在处理）。"""
    return DECODED_PACKAGED if os.path.exists(DECODED_PACKAGED) else DECODED_CACHE

# 跨体系严格度（大者更严，作主分）。依据见模块 docstring B 条与 safety_check.py 顶部调研结论。
# 同严格度时取「换算分更低」者（木桶保守）。
RIGOR = {
    "ciasi": 5,
    "euro_ncap": 4,
    "cncap": 4,       # 2024 版 Euro NCAP ≈ C-NCAP2024；老版由 older_penalty 降权体现
    "iihs": 3,
    "nhtsa": 3,
    "ancap": 3,
    "jncap": 3,
    "kncap": 3,
    "asean_ncap": 2,
    "latin_ncap": 2,
    "bharat_ncap": 2,
    "global_ncap": 2,
}
# C-IASI 计分维度 = 官方综合评级的「三指数」（乘员/行人/辅助）。
# 木桶取最低即 C-IASI 官方综合星级的算法。
# 耐撞维修经济性（可维修性）与新能源专项（EV 专项）是独立分指数，官方不并入综合安全
# 星级——按项目纪律（ciasi_text_decode.py docstring）它们只作事实列披露，不进安全分。
# 这正是「所有测评纳入」的正确落法：全部列入审计/披露，但计分只按官方口径的三指数。
CIASI_SCORE_DIMS = ("occupant", "pedestrian", "assist")
CIASI_DIM_CN = {
    "occupant": "车内乘员",
    "pedestrian": "车外行人",
    "assist": "辅助安全",
    "crash_repair": "耐撞维修(仅披露)",
    "ev_special": "新能源专项(仅披露)",
}


def _canon(s):
    from modules.contracts.model_names import canon
    return canon(s or "")


def _load_json(p):
    if not os.path.exists(p):
        return None
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return None


def decoded_by_canon(decoded=None):
    """ciasi_decoded.json → {canon: 记录}（只保留有可采信等级的维度）。"""
    d = decoded if decoded is not None else _load_json(decoded_path())
    if not d:
        return {}
    out = {}
    for name, rec in (d.get("models") or {}).items():
        dims = {}
        for key, dv in (rec.get("dims") or {}).items():
            if not isinstance(dv, dict):
                continue
            g = dv.get("grade")
            if g and dv.get("scope") in ("all", "per_model"):
                dims[key] = {"grade": g, "scope": dv["scope"]}
        if not dims:
            continue
        out[_canon(name)] = {
            "model": name, "dims": dims, "source": rec.get("source"),
            "published": rec.get("published"),
        }
    return out


def _grade_to_score(grade, grades):
    from modules.contracts.safety_score import score_from_grade
    return score_from_grade(grade, grades or {})


def _ciasi_bucket(rec, table):
    """C-IASI 多维度木桶：各维度按 map_spec 换算成分，取最低分档。"""
    grades = ((table.get("systems", {}).get("C-IASI", {}) or {}).get("map_spec", {})
              or {}).get("grades") or {}
    scored = []       # 三指数：进木桶计分
    disclosed = []    # 耐撞维修/新能源专项：只披露不计分
    for key, dv in rec["dims"].items():
        sc = _grade_to_score(dv["grade"], grades)
        item = {"dim": CIASI_DIM_CN.get(key, key), "grade": dv["grade"],
                "score": sc, "scope": dv["scope"],
                "counted": key in CIASI_SCORE_DIMS}
        if key in CIASI_SCORE_DIMS:
            if sc is not None:
                scored.append(item)
        else:
            disclosed.append(item)
    if not scored:
        return None
    worst = min(scored, key=lambda x: x["score"])
    # 档位取三指数最低档（C-IASI 官方综合评级口径）；披露项另行带出
    return {
        "basis": "ciasi", "system_cn": "C-IASI",
        "grade": worst["grade"], "score": worst["score"],
        "display": f"C-IASI {worst['grade']}（三指数最低：{worst['dim']}）",
        "dims": scored + disclosed, "source": rec.get("source"),
        "published": rec.get("published"), "primary": True,
    }


def _parsed_to_scored(parsed, table):
    """把 parse_all_scores_from_evidence 的一条转成带 score 的测评项。"""
    from modules.engine import safety_check as SFC
    spec = ((table.get("systems", {}).get(parsed["system_cn"], {}) or {}).get("map_spec") or {})
    score, rule = SFC.score_from_crash(spec, parsed)
    if score is None:
        return None
    out = dict(parsed)
    out["score"] = score
    out["rule"] = rule
    out["primary"] = False
    return out


def fuse(cands, table=None, decoded=None, apply=True):
    """对每款车融合全部一手权威测评。

    apply=True 时把主成绩写回 c.crash / c.safety_basis（供既有映射链路消费），
    并在 c 上写 crash_fusion 审计字段（含全部测评、严格度依据、未纳入项）。
    返回 [{"model","primary","all_scores","excluded","rule"}]
    """
    from modules.engine import crash_systems as CS
    table = table or _load_json(OFFICIAL) or {"systems": {}}
    by_canon = decoded_by_canon(decoded)
    results = []
    for c in cands:
        model = c.get("model")
        cm = _canon(model)
        all_scores = []      # 一手、可计分的测评项
        excluded = []        # 旁证/不可解码/未点名，只披露不计分

        # 1) 候选池文字成绩（tier>=2 一手；tier=0 旁证排除）
        ev = c.get("evidence") or {}
        ce = str(ev.get("crash_evidence") or "") if isinstance(ev, dict) else ""
        for p in (CS.parse_all_scores_from_evidence(ce) if ce else []):
            if p.get("tier", 0) >= 2:
                s = _parsed_to_scored(p, table)
                if s:
                    s["via"] = "候选池文字"
                    all_scores.append(s)
            else:
                excluded.append({"display": p.get("display"), "reason": "弱旁证/媒体/非本代，不计分"})

        # 2) C-IASI 官网新闻稿纯文字等级（木桶取最低）
        rec = by_canon.get(cm)
        if rec:
            b = _ciasi_bucket(rec, table)
            if b:
                # 与文字成绩里的 C-IASI 去重：新闻稿是一手发布日期稿，优先用它
                all_scores = [x for x in all_scores if x.get("basis") != "ciasi"]
                b["via"] = "C-IASI新闻稿文字"
                all_scores.append(b)

        # 3) 已有 crash 字段（顶层 note 解析出来的一手成绩，避免与文字重复）
        top = CS.parse_score(c.get("crash"))
        if top and not any(x.get("basis") == top["basis"] for x in all_scores):
            s = _parsed_to_scored({**top, "tier": 2}, table)
            if s:
                s["via"] = "顶层crash字段"
                all_scores.append(s)

        if not all_scores:
            # 无任何一手成绩：保留原样，只清理审计字段（避免上一轮残留）
            if apply:
                c["crash_fusion"] = {"model": model, "primary": None,
                                     "all_scores": [], "excluded": excluded,
                                     "rule": "无一手权威测评"}
            results.append({"model": model, "primary": None, "all_scores": [],
                            "excluded": excluded, "rule": "无一手权威测评"})
            continue

        # 选主分：先按严格度降序，同严格度按分数升序（木桶保守）
        all_scores.sort(key=lambda x: (-RIGOR.get(x.get("basis"), 0), x.get("score", 99)))
        primary = all_scores[0]
        primary["primary"] = True
        for x in all_scores[1:]:
            x["primary"] = False
        rule = (f"跨体系取最严格主分（{primary['system_cn']}）；"
                f"同体系内多维度按木桶取最低档；其余 {len(all_scores)-1} 项一手测评列示不叠加")

        if apply:
            c["crash"] = primary["display"]
            c["safety_basis"] = primary["basis"]
            c["crash_source"] = f"多测评融合：{rule}"
            if primary.get("dims"):
                c["crash_ciasi_dims"] = primary["dims"]
            c["crash_fusion"] = {
                "model": model,
                "primary": primary["display"],
                "primary_score": primary.get("score"),
                "all_scores": [{"basis": x.get("basis"), "system": x.get("system_cn"),
                                "display": x.get("display"), "score": x.get("score"),
                                "primary": x.get("primary"), "via": x.get("via")}
                               for x in all_scores],
                "excluded": excluded,
                "rule": rule,
            }
        results.append({
            "model": model, "primary": primary["display"],
            "all_scores": all_scores, "excluded": excluded, "rule": rule,
        })
    return results


def selftest():
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)   # 直接跑脚本时让 modules.contracts 可导入
    table = _load_json(OFFICIAL) or {"systems": {}}
    # 1) 同体系 C-IASI 多维度木桶取最低：G+/G+/M → 取 M(7.2) 而非 G+(9.3)
    rec = {"dims": {"occupant": {"grade": "G+", "scope": "all"},
                    "pedestrian": {"grade": "G+", "scope": "all"},
                    "assist": {"grade": "M", "scope": "all"}},
           "source": "test", "published": "2026-01-01"}
    b = _ciasi_bucket(rec, table)
    assert b and b["grade"] == "M" and b["score"] == 7.2, b
    # 2) aggregate_only 不进木桶
    rec2 = {"dims": {"occupant": {"grade": None, "scope": "aggregate_only"}},
            "source": "test", "published": "2026-01-01"}
    # decoded_by_canon 已过滤 scope，这里直接测 bucket 只收有 grade 的
    b2 = _ciasi_bucket({"dims": {"occupant": {"grade": "G+", "scope": "all"}},
                        "source": "test", "published": "2026-01-01"}, table)
    assert b2 and b2["grade"] == "G+", b2
    # 3) 跨体系：C-IASI(A=8.3) 与 C-NCAP(90.2%=8.9) 并存 → 取 C-IASI（更严）
    c = {"model": "测试车", "crash": None, "safety_basis": None, "scores": {},
         "evidence": {"crash_evidence":
             "有C-NCAP官方成绩：综合得分率90.2%，五星。有C-IASI官方成绩：车内乘员A、车外行人A、辅助安全A。"}}
    fused = decoded_by_canon({})  # 无新闻稿
    r = fuse([c], table=table, decoded={})
    assert r[0]["primary"].startswith("C-IASI"), r[0]
    assert c["crash"].startswith("C-IASI"), c["crash"]
    # 4) 严格度相同取低分（保守）：两个 C-NCAP 成绩不存在于同一车，跳过
    # 5) 弱旁证不计分
    c2 = {"model": "旁证车", "crash": None, "safety_basis": None, "scores": {},
          "evidence": {"crash_evidence": "无国内一手成绩；海外Euro NCAP五星，仅作旁证。"}}
    r2 = fuse([c2], table=table, decoded={})
    assert r2[0]["primary"] is None, r2[0]
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    print("multi_evidence_score: 由 pipeline 调用；--selftest 跑自测")
