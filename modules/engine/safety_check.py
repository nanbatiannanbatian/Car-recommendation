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
    unmapped, auto_mapped = [], []
    has_score_n = 0
    for c in cands:
        if not _CS.parse_score(c.get("crash")):
            continue
        has_score_n += 1
        if c["model"] in car_scores:
            continue
        rec = {"model": c["model"], "crash": c.get("crash"),
               "basis": c.get("safety_basis"),
               "score": (c.get("scores") or {}).get("safety"),
               "source": c.get("safety_score_source") or ""}
        (auto_mapped if rec["source"] else unmapped).append(rec)

    # A3（v10.71 根因修复）：有"星级"但无得分率的车，分数不得高于
    # 池内"有得分率且按公式可复算"车的中位 +0.3。
    # 2026-11-24 事故：尚界H5 crash 只写"C-NCAP五星"（无得分率），
    # 手工 safety 拍到封顶 9.3，恰好压过第1名 0.2 分；而同池有得分率的车
    # 92.7%→9.0、91.2%→9.0、93.5%→9.2，只有 95%→9.3 才到封顶。
    # A 分支只查表内车、A2 只报缺口，导致这类"无得分率却满分"无人拦。
    # 注意：不自动改分（尊重"分数等人定"的历史决定），只把异常暴露成门禁 issue。
    from modules.engine import crash_systems as _CS3
    pct_mapped = []   # 有得分率的车的公式映射分
    star_only = []    # 只有星级无得分率的车
    for c in cands:
        parsed = _CS3.parse_score(c.get("crash"))
        if not parsed:
            continue
        actual = (c.get("scores") or {}).get("safety")
        if actual is None:
            continue
        if parsed.get("score") is not None:
            spec = (table.get("systems") or {}).get(parsed.get("system_cn"), {}).get("map_spec")
            if spec:
                try:
                    want, _ = score_from_crash(spec, parsed)
                    if want is not None:
                        pct_mapped.append(float(want))
                except Exception:
                    pass
        else:
            # 只有星级/等级无得分率：先看 safety 是否等于该体系的公式映射分。
            # 若相等，说明是 apply_auto_scores 按官方等级规则给的（如 C-IASI G→9.0），
            # 属于有据分，不放行检查；只有"手工拍分≠公式分"才需核验。
            spec = (table.get("systems") or {}).get(parsed.get("system_cn"), {}).get("map_spec")
            formula = None
            if spec:
                try:
                    formula, _ = score_from_crash(spec, parsed)
                except Exception:
                    pass
            if formula is not None and abs(float(actual) - float(formula)) <= 0.05:
                continue  # 按官方规则映射，有据
            star_only.append((c.get("model"), float(actual), parsed.get("display") or "",
                              formula))
    if pct_mapped and star_only:
        ceiling = round(statistics.median(pct_mapped) + 0.3, 1)
        for model, actual, disp, formula in star_only:
            if actual > ceiling:
                issues.append({
                    "type": "star_only_overcap", "model": model,
                    "actual": actual, "expected": f"≤{ceiling}",
                    "fix": (f"{model} 的碰撞记录只有星级/等级({disp})无得分率，"
                            f"safety={actual} 非官方公式映射"
                            f"（{formula if formula is not None else '无公式'}），"
                            f"且高于有得分率车映射中位+0.3={ceiling}；"
                            "补齐 crash 的官方得分率并按 map_spec 复算，或下调分数"),
                })

    # C. 表内分数必须等于公式复算（口径统一）。
    # 2026-11-21 用户选 A 方案：表里 8 条人工分与同一公式算出来的不一致
    # （90.8%→8.9 / 90.6%→8.5 / 91.3%→9.1，公式应是 8.7/8.6/8.8），
    # 结果「同样得分率给不同分」。现在任何人改表都必须能被规则复算，否则出榜被拦。
    for model, rec in car_scores.items():
        want, rule = score_from_record(rec.get("system"), rec, table)
        if want is None:
            continue
        if abs(float(rec.get("score") or 0) - float(want)) > 0.05:
            issues.append({"type": "formula_mismatch", "model": model,
                           "actual": rec.get("score"), "expected": want,
                           "fix": f"表内分数与规则复算不一致；运行 "
                                  f"`python3 safety_check.py --recompute --apply` 统一口径（{rule}）"})

    # B. 举证单调性：有实锤车最低 >= 无实锤车最高
    # 2026-11-21 修：原来只比对 official_safety_scores.json（8 款老车），本池一款都不在表里
    # → 检查直接跳过，于是「MONA L05 零实锤却 8.6、比 C-NCAP 86.7% 的 bZ5 还高」没被拦。
    # 现在直接读 cands 的 crash 字段（含自动映射分），并用「最高」而非「中位」——
    # 无实锤的车一辆都不许高于任何有实锤的车。
    from modules.engine import crash_systems as _CS2
    proven_vals, unproven_vals = [], []
    for c in cands:
        s = (c.get("scores") or {}).get("safety")
        if s is None:
            continue
        (proven_vals if _CS2.parse_score(c.get("crash")) else unproven_vals).append(float(s))
    no_score_vals = unproven_vals
    if proven_vals and unproven_vals:
        max_nos = round(max(unproven_vals), 1)
        min_off = min(proven_vals)
        if min_off < max_nos:
            issues.append({"type": "evidence_monotonicity", "model": "(全池)",
                           "actual": f"有实锤最低{min_off} < 无实锤最高{max_nos}",
                           "expected": f"有实锤最低>={max_nos}（无实锤者不得高于任何实锤车）",
                           "fix": "跑 safety_check.evidence_cap() 封顶，或核对有实锤车是否被误降权"})
    return {"checked": checked, "official_min": min(official_vals) if official_vals else None,
            "no_score_median": round(statistics.median(no_score_vals), 1) if no_score_vals else None,
            "has_score": has_score_n, "mapped": checked,
            "auto_mapped": auto_mapped, "unmapped": unmapped,
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
    # BUG-112：Euro NCAP 文本含乘员得分率 93.95% 时必须取本体系 ≥90 档 8.8，
    # 不能因"表外"落保守档 8.3（ES6 曾因此低于 C-NCAP 89.2% 的 L60）。
    from modules.engine import crash_systems as CS
    tab = load_table()
    spec = tab["systems"]["Euro NCAP"]["map_spec"]
    p = CS.parse_score("Euro NCAP 93.95% 五星")
    sc, rule = score_from_crash(spec, p)
    assert sc == 8.8 and "93.95" in rule, f"乘员分档未生效: {sc} {rule}"
    # 反向注入：抹掉 pct 只能取保守档 8.3（证明确实读了分而非硬编码车型）
    sc2, _ = score_from_crash(spec, {"system_cn": "Euro NCAP", "stars": 5, "score": None})
    assert sc2 == 8.3, f"无乘员分应回退保守档: {sc2}"
    # C-NCAP 得分率公式不受影响（L60 89.2% 仍为 8.4）
    cn = tab["systems"]["C-NCAP"]["map_spec"]
    assert score_from_crash(cn, CS.parse_score("C-NCAP 89.2% 五星"))[0] == 8.4
    return 0




# ---------------------------------------------------------------- 成绩 → 分数（自动映射）
def score_from_crash(spec: dict, parsed: dict) -> tuple:
    """按 official_safety_scores.json 的 map_spec 把成绩转成 0-10 安全分。

    返回 (score|None, rule_text)。spec 是该体系的机器可读规则：
      kind=pct   : base + (pct-ref)*slope，cap 封顶；2021 及更早 -older_penalty
      kind=grade : grades[grade]（C-IASI / IIHS）
      kind=star  : five/four/three（NCAP 家族；无乘员分项时取保守档）

    为什么要有：2026-11-21 出中型纯电SUV榜，7 款车的 C-NCAP 成绩已经回流进 crash 字段，
    但 official_safety_scores.json 里没有它们 → safety 分仍按「无成绩车插补」，
    成绩等于没参与打分。这里把「成绩→分数」变成可复算的代码，不再靠人手填表。
    """
    if not spec:
        return None, ""
    kind = spec.get("kind")
    # 换算算法统一在 contracts/safety_score（research 侧也用同一份，避免两套公式）
    from modules.contracts.safety_score import (
        score_from_grade, score_from_pct, score_from_stars, score_from_star_occupant)
    if kind == "pct":
        pct = parsed.get("score")
        if pct is None:
            return None, ""
        sc = score_from_pct(pct, base=spec["base"], ref=spec["ref"], slope=spec["slope"],
                            cap=spec.get("cap", 9.3), floor=spec.get("floor", 0.0),
                            older_penalty=spec.get("older_penalty", 0.0),
                            older_before=spec.get("older_before", 0),
                            year=parsed.get("year"))
        return sc, f"C-NCAP {pct:g}% → {spec['base']}+({pct:g}-{spec['ref']})×{spec['slope']} 封顶{spec.get('cap')}"
    if kind == "grade":
        g = parsed.get("grade")
        sc = score_from_grade(g, spec.get("grades") or {})
        return (sc, f"{parsed.get('system_cn')} {g} → {sc}") if sc is not None else (None, "")
    if kind == "star":
        stars = parsed.get("stars")
        # BUG-112：成绩文本里已知乘员保护得分率时，按【本体系】grades 精确分档，
        # 不能因为"表外"就当作只有星级、一律落保守档（ES6 Euro NCAP 93.95% 被算成 8.3）。
        pct = parsed.get("score")
        if stars == 5 and pct is not None:
            sc = score_from_star_occupant(pct, spec)
            if sc is not None:
                return sc, (f"{parsed.get('system_cn')} 五星·乘员保护{pct:g}% → {sc}"
                            f"（按本体系 occupant 分档，不跨体系套得分率公式）")
        sc = score_from_stars(stars, spec)
        return (sc, f"{parsed.get('system_cn')} {stars}星 → {sc}（无乘员分项取保守档）") if sc is not None else (None, "")
    return None, ""


def apply_auto_scores(cands, table=None, apply=True):
    """表外但有成绩的车 → 自动映射 safety 分（表内人工分不动）。

    返回 (applied, skipped)：
      applied: [{"model","score","prev","rule","crash"}]
      skipped: [车型]（有成绩但规则/分项不足，无法映射）
    """
    from modules.engine import crash_systems as CS
    table = table or load_table()
    car_scores = table.get("car_scores", {})
    systems = table.get("systems", {})
    applied, skipped = [], []
    for c in cands:
        if c["model"] in car_scores:
            continue                      # 表内人工分优先，绝不覆盖
        parsed = CS.parse_score(c.get("crash"))
        if not parsed:
            skipped.append(c["model"])
            continue
        spec = (systems.get(parsed["system_cn"]) or {}).get("map_spec")
        score, rule = score_from_crash(spec, parsed)
        if score is None:
            skipped.append(c["model"])
            continue
        prev = (c.get("scores") or {}).get("safety")
        if apply:
            c.setdefault("scores", {})["safety"] = score
            c["safety_score_source"] = rule
            if prev is not None and abs(float(prev) - score) > 0.05:
                c["safety_score_prev"] = prev
        applied.append({"model": c["model"], "score": score, "prev": prev,
                        "rule": rule, "crash": parsed["display"]})
    return applied, skipped


# ---------------------------------------------------------------- 全表复算（口径统一）
def score_from_record(system: str, rec: dict, table: dict | None = None) -> tuple:
    """按体系规则复算「表内一条记录」的分数。

    优先级：精确 grade（含乘员分项的复合等级）→ 乘员分档/得分率 pct → 星级保守档。
    这是 car_scores 的唯一合法来源：表里的 score 必须能被这条规则复算出来。
    """
    table = table or load_table()
    spec = ((table.get("systems") or {}).get(system) or {}).get("map_spec") or {}
    if not spec:
        return None, ""
    grade = rec.get("grade")
    if grade and spec.get("grades") and grade in spec["grades"]:
        v = spec["grades"][grade]
        return v, f"{system} {grade} → {v}"
    # BUG-112：表内若只写 occupant_pct（没写复合 grade），同样按体系分档复算，
    # 与表外成绩文本走同一条规则，避免"表内按分档、表外落保守档"两套口径。
    if rec.get("occupant_pct") is not None and spec.get("kind") == "star":
        parsed = {"score": rec["occupant_pct"], "stars": rec.get("stars") or 5,
                  "system_cn": system, "grade": None, "year": rec.get("year")}
        return score_from_crash(spec, parsed)
    if rec.get("value") is not None and spec.get("kind") == "pct":
        parsed = {"score": rec["value"], "year": rec.get("year"),
                  "system_cn": system, "stars": rec.get("stars"), "grade": None}
        return score_from_crash(spec, parsed)
    stars = rec.get("stars")
    if stars:
        key = {5: "five", 4: "four", 3: "three"}.get(int(stars))
        v = spec.get(key) if key else None
        if v is not None:
            return v, f"{system} {stars}星 → {v}（未给乘员分项，取保守档）"
    return None, ""


def recompute_table(table: dict | None = None, apply: bool = False) -> tuple:
    """按规则重算 car_scores 全表。返回 (changed, unresolved)。

    为什么（2026-11-21 用户选择 A 方案）：
      表里 8 条是人工填的，同一公式算出来对不上（90.8%→8.9、90.6%→8.5、91.3%→9.1，
      公式应是 8.7/8.6/8.8）。口径不统一会导致「同样得分率给不同分」。
      现在统一按 map_spec 复算，原值存 score_prev 备查。
    apply=True 时原地改写 table（不落盘，由调用方 dump）。
    """
    table = table or load_table()
    car_scores = table.get("car_scores", {})
    changed, unresolved = [], []
    for model, rec in car_scores.items():
        new, rule = score_from_record(rec.get("system"), rec, table)
        if new is None:
            unresolved.append({"model": model, "reason": "规则无法复算（缺 value/grade/stars）"})
            continue
        old = rec.get("score")
        if old is not None and abs(float(old) - float(new)) <= 0.05:
            rec["score_rule"] = rule
            continue
        changed.append({"model": model, "old": old, "new": new, "rule": rule})
        if apply:
            rec["score_prev"] = old
            rec["score"] = new
            rec["score_rule"] = rule
    return changed, unresolved


def sync_table_scores_to_pool(cands, table: dict | None = None) -> list:
    """表内车（有人工/复算分）→ 强制对齐候选池 scores.safety。

    表是权威：表重算后，候选池若还是旧分，会出现「表里 8.7、榜上 8.9」两套数。
    返回调整清单 [{"model","prev","score","rule"}]。
    """
    table = table or load_table()
    car_scores = table.get("car_scores", {})
    out = []
    for c in cands:
        rec = car_scores.get(c["model"])
        if not rec:
            continue
        want = rec.get("score")
        if want is None:
            continue
        cur = (c.get("scores") or {}).get("safety")
        if cur is not None and abs(float(cur) - float(want)) <= 0.05:
            continue
        c.setdefault("scores", {})["safety"] = float(want)
        c["safety_score_source"] = rec.get("score_rule") or f"映射表 {rec.get('system')}"
        if cur is not None:
            c["safety_score_prev"] = cur
        out.append({"model": c["model"], "prev": cur, "score": float(want)})
    return out


# ---------------------------------------------------------------- CLI
def main(argv=None) -> int:
    import argparse
    import json as _json
    ap = argparse.ArgumentParser(description="安全分映射表复算/校验")
    ap.add_argument("--candidates", help="候选池 json：跑安全分严谨性门禁")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--recompute", action="store_true", help="按体系规则复算全表并报告差异")
    ap.add_argument("--apply", action="store_true", help="写入（默认 dry-run）")
    ap.add_argument("--check-table", action="store_true", help="校验表内分数是否都等于公式复算值")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if a.candidates:
        d = _json.load(open(a.candidates, encoding="utf-8"))
        r = check(d.get("candidates", d))
        print(f"[安全分门禁] 池内有成绩 {r.get('has_score')} 款 / 表内人工 {r.get('mapped')} 款 / "
              f"自动映射 {len(r.get('auto_mapped') or [])} 款 / 问题 {len(r['issues'])}")
        for x in r["issues"]:
            print(f"  - [{x['type']}] {x['model']}: {x['actual']} → 应 {x['expected']}")
        return 0 if r["passed"] else 1

    p = str(paths.ROOT / "official_safety_scores.json")
    table = _json.load(open(p, encoding="utf-8"))

    if a.check_table:
        ch, un = recompute_table(table, apply=False)
        print(f"表内 {len(table.get('car_scores', {}))} 条：与公式不一致 {len(ch)} 条 / 无法复算 {len(un)} 条")
        for x in ch:
            print(f"  ✗ {x['model']:<18} 表内 {x['old']} ≠ 复算 {x['new']}  （{x['rule']}）")
        for x in un:
            print(f"  ? {x['model']}: {x['reason']}")
        return 0 if not ch and not un else 1

    ch, un = recompute_table(table, apply=a.apply)
    print(f"复算：{len(ch)} 条需调整 / 无法复算 {len(un)} 条")
    for x in ch:
        print(f"  {x['model']:<18} {x['old']} → {x['new']}  （{x['rule']}）")
    for x in un:
        print(f"  ? {x['model']}: {x['reason']}")
    if a.apply and ch:
        table["version"] = table.get("version", "") + "+recomputed"
        _json.dump(table, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"已写入 {p}")
    elif ch:
        print("dry-run；加 --apply 写入")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# ---------------------------------------------------------------- 举证责任封顶
def evidence_cap(cands):
    """无官方碰撞成绩的车，safety 不得超过「有实锤车里的最低分」。

    2026-11-21 用户质疑「小鹏 MONA L05 碰撞成绩都没有、用料也没 20 万车好，
    为什么安全分这么高」——查实：它 note 自述「零交付、零实测、零碰撞成绩」，
    safety 却是 8.6，比有 C-NCAP 86.7% 的丰田 bZ5（8.0）高 0.6。

    为什么是封顶而不是扣分：
      「没测过」不等于「不安全」（项目原则：缺数据不判劣），
      但也不该被推断成「比测过的更安全」——这是举证责任问题。
      所以只做上限约束：无实锤者不得高于任何一款有实锤的车，不额外倒扣。

    规则：
      - 有实锤 = crash 字段能解析出具体成绩（C-NCAP 得分率 / C-IASI 等级）
      - cap = 这些车的 safety 最低值；池内无实锤车时不封顶（没有可校准的锚）
      - 只在 safety 高于 cap 时下调，并记录 safety_cap_reason 供披露
    返回 [{"model","prev","capped","reason"}]
    """
    from modules.engine import crash_systems as CS
    proven = []
    for c in cands:
        if CS.parse_score(c.get("crash")):
            v = (c.get("scores") or {}).get("safety")
            if v is not None:
                proven.append((c["model"], float(v)))
    if not proven:
        return []
    cap = min(v for _, v in proven)
    anchor = min(proven, key=lambda x: x[1])[0]
    out = []
    for c in cands:
        if CS.parse_score(c.get("crash")):
            continue                      # 有实锤的不封顶
        s = (c.get("scores") or {}).get("safety")
        if s is None or float(s) <= cap + 1e-9:
            continue
        prev = float(s)
        c.setdefault("scores", {})["safety"] = cap
        c["safety_cap_reason"] = (f"无官方碰撞成绩，safety 封顶至 {cap:g}"
                                  f"（不超过有实锤车最低分，锚点 {anchor}）；"
                                  f"原推断值 {prev:g}")
        out.append({"model": c["model"], "prev": prev, "capped": cap, "anchor": anchor})
    return out
