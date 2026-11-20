#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
evidence_consistency.py —— 分数证据文本与结构化字段一致性门禁（v9.7.43）

背景：007GT 的 safety 从 7.2 改到 8.8、basis 改 euro_ncap 后，
score_evidence.safety 文本却还写着"无官方碰撞成绩-0.5（降权不判劣）"——
分数改了、证据说明没同步。这类"文本与字段脱节"会让报告和审计自相矛盾。

规则（通用，不只针对某一句）：
1. 证据文本声明"无官方碰撞/无成绩/降权" 但 safety_basis 有权威体系 → 矛盾
2. 证据文本声明具体成绩（NCAP/C-IASI/五星/得分率）但 safety_basis 为空 → 矛盾
3. 证据文本提到的体系与 safety_basis 体系不一致（如文本E-NCAP、basis=ciasi）→ 矛盾

这些是"分数证据"层的一致性，不参与算法计算，但影响报告/审计可信度。
检测出矛盾 → 出榜拦截，要求修文本或修字段（不许只改展示绕过）。

用法：
  python evidence_consistency.py --candidates cands.json
  python evidence_consistency.py --selftest
"""
import argparse, json, os, re
import crash_systems as CS

# 体系定义统一从 crash_systems 读（唯一真相源，加体系只改那一处）
OFFICIAL_BASIS = CS.all_codes()
BASIS_SYSTEM = {code: CS.family(code) for code in CS.all_codes()}


def _safety_text(c):
    se = c.get("score_evidence") or {}
    if not isinstance(se, dict):
        return ""
    return " ".join(str(x) for x in (se.get("safety") or []))


def check(cands):
    issues, checked = [], 0
    for c in cands:
        text = _safety_text(c)
        if not text:
            continue
        checked += 1
        model = c.get("model")
        basis = str(c.get("safety_basis") or "").lower()
        has_basis = CS.is_official(basis)
        # 规则1：文本说无成绩/降权，但有权威basis
        says_no = bool(re.search(r"无官方碰撞|无官方成绩|无成绩|降权|未查到.*成绩", text))
        if says_no and has_basis:
            issues.append({"model": model, "rule": "text_says_no_but_has_basis",
                           "text": text[:80], "basis": basis,
                           "fix": f"{model} 证据文本说'无官方碰撞'但 safety_basis={basis}；按实际成绩改文本"})
        # 规则2：文本说有具体成绩，但 basis 空。
        # 排除否定语境："无官方C-NCAP/C-IASI"虽含NCAP但说的是没有成绩。
        _yes_clean = re.sub(r"无官方[^，。；\s]*(?:NCAP|CIASI|中保研)[^，。；\s]*", "", text, flags=re.I)
        says_yes = bool(re.search(r"(?i)(ncap|ciasi|五星|5星|得分率|折算\d)", _yes_clean))
        if says_yes and not has_basis:
            issues.append({"model": model, "rule": "text_says_yes_but_no_basis",
                           "text": text[:80], "basis": basis or "(空)",
                           "fix": f"{model} 证据文本写了碰撞成绩但 safety_basis 空；补 basis 或改文本"})
        # 规则3：文本「主体系」(最先出现的)与 basis 体系不符才报。
        # 佐证体系(如"C-IASI AGGG + E-NCAP佐证")不算矛盾——以先出现的主体系为准。
        if has_basis and says_yes and not says_no:
            _m = re.search(r"(?i)(c[\s-]?iasi|中保研|e?[\s-]?ncap)", text)
            if _m:
                first = re.sub(r"[\s-]", "", _m.group(1)).lower()
                txt_sys = {"ciasi"} if ("ciasi" in first or "中保研" in first) else {"ncap"}
                want = CS.family(basis)
                if want and txt_sys != {want}:
                    issues.append({"model": model, "rule": "system_mismatch",
                                   "text": text[:80], "basis": basis,
                                   "fix": f"{model} 证据文本主体系{txt_sys}与 safety_basis={basis}({want})不符；按实际成绩统一"})
    return {"checked": checked, "issues": issues, "passed": not issues}


def selftest():
    cases = [
        ({"model": "正常有成绩", "safety_basis": "cncap",
          "score_evidence": {"safety": ["C-NCAP 92.3%五星+1.4"]}}, True),
        ({"model": "正常无成绩", "safety_basis": None,
          "score_evidence": {"safety": ["无官方碰撞成绩-0.5（降权不判劣）"]}}, True),
        ({"model": "有成绩却写无", "safety_basis": "euro_ncap",
          "score_evidence": {"safety": ["无官方碰撞成绩-0.5（降权不判劣）"]}}, False),
        ({"model": "写成绩却无basis", "safety_basis": None,
          "score_evidence": {"safety": ["E-NCAP折算92.0%+0.6"]}}, False),
        ({"model": "体系不符", "safety_basis": "ciasi",
          "score_evidence": {"safety": ["E-NCAP折算92.0%+0.6"]}}, False),
        # 误报防护：主体系一致+佐证体系不同 → 不算矛盾（ET5场景）
        ({"model": "主体系一致带佐证", "safety_basis": "ciasi",
          "score_evidence": {"safety": ["C-IASI AGGG按C-IASI体系映射", "E-NCAP五星(佐证不叠加)"]}}, True),
        # 误报防护：否定语境含NCAP字样 → 不算"有成绩"（Model3场景）
        ({"model": "否定语境", "safety_basis": None,
          "score_evidence": {"safety": ["无官方C-NCAP/C-IASI成绩-0.5（降权不判劣）", "配置+口碑人工评估"]}}, True),
    ]
    for c, want in cases:
        got = check([c])["passed"]
        assert got == want, f"{c['model']}: {got} != {want}"
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="分数证据文本一致性门禁")
    ap.add_argument("--candidates")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(selftest())
    if a.candidates:
        d = json.load(open(a.candidates, encoding="utf-8"))
        r = check(d.get("candidates", d))
        print(f"[证据文本一致性] 检查 {r['checked']} 款 / 矛盾 {len(r['issues'])}")
        for x in r["issues"]:
            print(f"  - [{x['rule']}] {x['model']}: {x['text']} (basis={x['basis']})")
        raise SystemExit(0 if r["passed"] else 1)
