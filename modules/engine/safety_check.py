#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
safety_check.py —— 安全分严谨性门禁（v9.7.43）

调研结论（2026-11-20）：
1. 各国NCAP结果无直接对应关系（中国质量万里行/JustChinaCars）——不能跨体系套同一得分率公式。
2. C-IASI最严（自购低配、25%小偏置、保险出资）> Euro NCAP ≈ C-NCAP2024 > 旧版C-NCAP。
3. 扭转刚度/高强度钢/气囊数：无强制标准、厂商白车身宣传口径不可靠、非越大越好（吸能区要形变），
   业内共识是"纸面参数只作弱佐证，最终以碰撞测试为准"——故不入公式。

本模块两道检查：
A. 有官方成绩的车：candidates.scores.safety 必须 == official_safety_scores.json 的映射分（防人工乱填，如007GT 7.2）。
B. 单调性：有官方成绩车的最低 safety 不得低于无成绩车的中位 safety
   （防"有强证据反而分更低"——007GT 7.2 < 无成绩车8.0 的病根）。

用法：
  python safety_check.py --candidates cands.json --check   # 检查（默认）
  python safety_check.py --selftest
"""
import argparse, json, os, statistics

import paths

HERE = str(paths.ROOT)
OFFICIAL = os.path.join(HERE, "official_safety_scores.json")


def load_table():
    return json.load(open(OFFICIAL, encoding="utf-8"))


def check(cands, table=None):
    table = table or load_table()
    car_scores = table.get("car_scores", {})
    by = {c["model"]: c for c in cands}
    issues, checked = [], 0

    # A. 有官方成绩的车，分数必须等于映射表
    official_vals = []
    for model, rec in car_scores.items():
        c = by.get(model)
        if not c:
            continue
        actual = (c.get("scores") or {}).get("safety")
        expected = rec["score"]
        checked += 1
        official_vals.append(float(expected))
        if actual is None or abs(float(actual) - float(expected)) > 0.05:
            issues.append({"type": "mismatch", "model": model,
                           "actual": actual, "expected": expected,
                           "fix": f"按官方成绩映射表把 {model}.scores.safety 改为 {expected}（{rec['source']}）"})
        basis = str(c.get("safety_basis") or "")
        sysname = rec["system"]
        want_basis = {"C-NCAP": "cncap", "C-IASI": "ciasi", "Euro NCAP": "euro_ncap",
                      "ANCAP": "ancap", "IIHS": "iihs"}.get(sysname, "")
        if want_basis and not basis:
            issues.append({"type": "no_basis", "model": model, "actual": basis or "(空)",
                           "expected": want_basis,
                           "fix": f"给 {model} 写 safety_basis={want_basis}"})

    # A2. 池内「有权威碰撞成绩但未进映射表」：只报缺口，不擅自给分。
    # 为什么不像价格那样自动回填：映射表里同样是 C-NCAP、得分率相近的车分数并不一致
    # （90.8%→8.9、90.6%→8.5、91.3%→9.1），说明还叠加了分项/年款口径的人工判断；
    # 用公式硬算会造出与既有表不一致的假分。所以：成绩进字段（事实），分数等人定。
    from modules.engine import crash_systems as _CS
    unmapped = []
    has_score_n = 0
    for c in cands:
        if not _CS.parse_score(c.get("crash")):
            continue
        has_score_n += 1
        if c["model"] not in car_scores:
            unmapped.append({"model": c["model"], "crash": c.get("crash"),
                             "basis": c.get("safety_basis")})

    # B. 单调性：有成绩车最低 >= 无成绩车中位
    no_score_vals = []
    for c in cands:
        if c["model"] in car_scores:
            continue
        s = (c.get("scores") or {}).get("safety")
        if s is not None:
            no_score_vals.append(float(s))
    if official_vals and no_score_vals:
        med_nos = round(statistics.median(no_score_vals), 1)
        min_off = min(official_vals)
        if min_off < med_nos:
            issues.append({"type": "monotonicity", "model": "(全池)",
                           "actual": f"有成绩最低{min_off} < 无成绩中位{med_nos}",
                           "expected": f"有成绩最低>={med_nos}",
                           "fix": "有官方碰撞成绩的车安全分不应低于无成绩车中位——检查是否被误降权"})
    return {"checked": checked, "official_min": min(official_vals) if official_vals else None,
            "no_score_median": round(statistics.median(no_score_vals), 1) if no_score_vals else None,
            "has_score": has_score_n, "mapped": checked, "unmapped": unmapped,
            "issues": issues, "passed": not issues}


def selftest():
    import copy
    cands = [
        {"model": "极氪007GT", "scores": {"safety": 8.8}, "safety_basis": "euro_ncap"},
        {"model": "极氪007", "scores": {"safety": 8.8}, "safety_basis": "euro_ncap"},
        {"model": "MG 07 纯电", "scores": {"safety": 8.2}, "safety_basis": None},
        {"model": "秦MAX EV", "scores": {"safety": 8.2}, "safety_basis": None},
        {"model": "海豹06EV", "scores": {"safety": 8.0}, "safety_basis": None},
    ]
    r = check(cands)
    assert r["passed"], f"正常态应通过: {r['issues']}"
    # 篡改1：把007GT改回7.2（本次bug）→ 必须检出
    fake = copy.deepcopy(cands)
    fake[0]["scores"]["safety"] = 7.2
    r2 = check(fake)
    assert not r2["passed"] and any(x["model"] == "极氪007GT" and x["type"] == "mismatch" for x in r2["issues"]), \
        f"7.2未被检出: {r2['issues']}"
    # 篡改2：有成绩车低于无成绩中位（单调性）→ 必须检出
    fake2 = copy.deepcopy(cands)
    for c in fake2[:2]:
        c["scores"]["safety"] = 7.0
    r3 = check(fake2)
    assert not r3["passed"], "单调性破坏未检出"
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="安全分严谨性门禁")
    ap.add_argument("--candidates")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(selftest())
    if a.candidates:
        d = json.load(open(a.candidates, encoding="utf-8"))
        r = check(d.get("candidates", d))
        print(f"[安全分门禁] 核对有成绩车 {r['checked']} 款 / 有成绩最低 {r['official_min']} / 无成绩中位 {r['no_score_median']} / 问题 {len(r['issues'])}")
        for x in r["issues"]:
            print(f"  - [{x['type']}] {x['model']}: {x['actual']} → 应 {x['expected']}")
        raise SystemExit(0 if r["passed"] else 1)
