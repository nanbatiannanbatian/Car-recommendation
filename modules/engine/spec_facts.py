#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""官网配置事实库（spec_facts.py）——把细分维度变成可核验的事实，而不是加分项。

提出这个问题的场景：用户列了轮胎、制动距离、维修经济性、售后态度、保养经济性、
底盘用料、试驾感受、麋鹿成绩、防爆胎、电耗、空间、NVH、智驾、主动安全、流畅度、
马力等十几项，提议「成绩好的加一点分，没数据或不确定不加分」，直觉上认为细分会更准。

**结论是不做**，而且这不是保守，是有具体失效模式的：

1 缺数据不是随机的（MNAR）。新势力、高曝光车型披露的参数与实测远多于冷门车型，
  「有数据就加分」实际在给营销曝光度打分。
2 厂商选择性披露：只公布最优条件下好看的数字，缺的往往是不好看的。只加不减
  会系统性奖励会宣传的品牌。
3 大半已被现有 7 维吸收（powertrain/safety/adas/space/value/control/cabin），
  再加一次是双重计数。
4 覆盖率 10–30% 的维度，方差几乎全由「有没有数据」决定；十几个这样的维度叠加，
  MCDA 权重会被稀疏噪声劫持，榜单变成「谁被报道得多谁赢」。
5 制动/麋鹿跨媒体测试条件不统一；试驾、售后、NVH、流畅度无标准化口径。

替代方案（本模块把它做成可执行的，而不是一句建议）：
  · 分层：existing_core / candidate_core / thirdparty_cap（双向封顶）/ fact_only /
    preference_slider / do_not_collect，见 categories/spec_facts.json 的 16 行决策表。
  · 维度级准入门槛 G1–G4：只有「官方标准化口径 + 覆盖率 ≥70% + 可核验来源」
    同时满足，才允许讨论入主分；不达标就整条维度禁用，而不是把缺失记 0 分。
  · 主观项交还给用户做偏好滑杆，不写进默认权重。

红线：**SCORING_ENABLED=False**。apply() 只写 c['spec_facts']，并在返回前对
apply 前后的 scores 做深比较断言——任何试图让本库改分的代码都会直接抛错。
这条断言是刻意的：事实库的公信力来自「它不可能改排名」。

用法：
    python3 spec_facts.py --validate
    python3 spec_facts.py --admission --candidates cands.json
    python3 spec_facts.py --report --candidates cands.json --out spec_facts.md
    python3 spec_facts.py --todo --candidates cands.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import paths

HERE = str(paths.ROOT)
DEFAULT_DB = str(paths.cat("spec_facts.json"))

SCORING_ENABLED = False          # 红线：永不改分
ADMISSION_THRESHOLD = 0.70       # G2：维度覆盖率门槛

CHANNEL_LABELS = {
    "existing_core": "已吸收·不加分",
    "candidate_core": "有官方口径·待准入",
    "thirdparty_cap": "封顶通道·双向",
    "fact_only": "仅事实列",
    "preference_slider": "用户偏好滑杆",
    "do_not_collect": "不采集",
}

# 概念维度 → 事实库字段（用于算覆盖率）。None 表示目前没有结构化采集，覆盖率=0。
LINK = {
    "tire_spec": "tire_spec", "brake_distance": None, "repair_cost": None,
    "service_attitude": None, "maintenance_cost": "maintenance_interval",
    "chassis_material": "chassis_material", "test_drive_feel": None,
    "moose_test": None, "runflat_tire": "runflat_tire",
    "energy_consumption": None, "space": None, "nvh": "nvh_glass",
    "adas_capability": "adas_hw_note", "active_safety": None,
    "system_fluency": None, "horsepower": None,
}

FIELDS = ("tire_spec", "brake_type", "chassis_material", "nvh_glass",
          "runflat_tire", "adas_hw_note", "maintenance_interval")


def load_db(path: str = DEFAULT_DB) -> dict:
    if not os.path.exists(path):
        return {"dimensions": [], "entries": []}
    return json.load(open(path, encoding="utf-8"))


def validate(db: dict) -> list[str]:
    errs = []
    if db.get("scoring_enabled") is not False:
        errs.append("scoring_enabled 必须为 false（事实库不得产生分数）")
    seen = set()
    for i, e in enumerate(db.get("entries", [])):
        w = f"entries[{i}]"
        for k in ("model", "trim", "source", "url", "date", "source_type", "quote"):
            if not e.get(k):
                errs.append(f"{w}: 缺 {k}（可核验要求必填）")
        if e.get("model") in seen:
            errs.append(f"{w}: 车型重复 {e.get('model')}")
        seen.add(e.get("model"))
        if not str(e.get("url") or "").startswith(("http://", "https://")):
            errs.append(f"{w}: url 不是 http(s) 链接")
        if e.get("trim_scope") not in ("all_trims", "specific_trim",
                                       "series_partial", "series_default"):
            errs.append(f"{w}: trim_scope 取值非法")
        for f in FIELDS:
            if f not in e:
                errs.append(f"{w}: 缺字段 {f}（无证据必须显式写 null）")
    keys = {d.get("key") for d in db.get("dimensions", [])}
    for k in LINK:
        if k not in keys:
            errs.append(f"dimensions 缺决策项 {k}")
    for d in db.get("dimensions", []):
        if d.get("channel") not in CHANNEL_LABELS:
            errs.append(f"dimensions[{d.get('key')}]: channel 非法 {d.get('channel')}")
    return errs


def apply(cands: list[dict], db: dict | None = None) -> tuple[list[dict], list[dict]]:
    """只写事实，绝不改分。返回 (cands, 已匹配条目)。

    幂等：重复调用覆盖 c['spec_facts']，不叠加。
    """
    if SCORING_ENABLED:
        raise RuntimeError("spec_facts 的红线被破坏：scoring_enabled 必须恒为 False")
    if db is None:
        db = load_db()
    errs = validate(db)
    if errs:
        raise SystemExit("配置事实库未通过校验：\n  " + "\n  ".join(errs))

    # 防御性快照：事实库不得以任何间接方式改动 scores / total / 名次相关字段。
    before = json.dumps([{k: c.get(k) for k in
                          ("scores", "total", "adjust", "airbag_delta")}
                         for c in cands], sort_keys=True, ensure_ascii=False)

    by_model = {e["model"]: e for e in db.get("entries", [])}
    matched = []
    for c in cands:
        name = c.get("model")
        e = by_model.get(name)
        if not e:
            c["spec_facts"] = {"status": "unknown", "fields": {f: None for f in FIELDS}}
            continue
        rec = {
            "status": "verified", "trim": e.get("trim"),
            "trim_scope": e.get("trim_scope"),
            "source": e.get("source"), "url": e.get("url"),
            "date": e.get("date"), "source_type": e.get("source_type"),
            "quote": e.get("quote"), "note": e.get("note"),
            "fields": {f: e.get(f) for f in FIELDS},
        }
        c["spec_facts"] = rec
        matched.append({"model": name, **{f: e.get(f) for f in FIELDS}})

    after = json.dumps([{k: c.get(k) for k in
                         ("scores", "total", "adjust", "airbag_delta")}
                        for c in cands], sort_keys=True, ensure_ascii=False)
    if before != after:
        raise AssertionError("spec_facts.apply 改动了评分字段——违反 SCORING_ENABLED=False 红线")
    return cands, matched


def coverage(cands: list[dict]) -> dict:
    """按事实字段统计在候选池的覆盖率（G2 的输入）。"""
    n = len(cands) or 1
    out = {}
    for f in FIELDS:
        k = sum(1 for c in cands
                if (c.get("spec_facts") or {}).get("fields", {}).get(f)
                not in (None, ""))
        out[f] = {"n": k, "total": len(cands), "pct": round(100.0 * k / n, 1)}
    verified = sum(1 for c in cands
                   if (c.get("spec_facts") or {}).get("status") == "verified")
    todo = [c["model"] for c in cands
            if (c.get("spec_facts") or {}).get("status") != "verified"]
    return {"fields": out, "verified": verified, "total": len(cands),
            "todo": todo, "coverage_pct": round(100.0 * verified / n, 1)}


def admission(cands: list[dict], db: dict | None = None) -> list[dict]:
    """维度级准入评估：只有 candidate_core / thirdparty_cap 需要过 G2。"""
    if db is None:
        db = load_db()
    cov = coverage(cands)
    rows = []
    for d in db.get("dimensions", []):
        ch, key = d.get("channel"), d.get("key")
        f = LINK.get(key)
        pct = cov["fields"].get(f, {}).get("pct", 0.0) if f else 0.0
        need = ch in ("candidate_core", "thirdparty_cap")
        rows.append({
            "key": key, "label": d.get("label"), "channel": ch,
            "existing_dim": d.get("existing_dim"),
            "standard_source": d.get("standard_source"),
            "coverage_pct": pct, "needs_gate": need,
            "admitted": (not need) or (pct >= ADMISSION_THRESHOLD * 100),
            "verdict": ("不需准入" if not need else
                        "达标·可评估入主分" if pct >= ADMISSION_THRESHOLD * 100 else
                        f"未达标·整条维度禁用（<{ADMISSION_THRESHOLD:.0%}）"),
            "reason": d.get("reason"),
        })
    return rows


def decision_table(rows: list[dict]) -> str:
    L = ["| 细分维度 | 处置 | 现有维度 | 结构化覆盖率 | 准入判定 | 理由 |",
         "|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['label']} | {CHANNEL_LABELS.get(r['channel'], r['channel'])} "
                 f"| {r['existing_dim'] or '—'} "
                 f"| {r['coverage_pct']:.0f}% | {r['verdict']} | {r['reason']} |")
    return "\n".join(L)


def summary_md(cands: list[dict], db: dict | None = None) -> str:
    """给主报告用的精简节（主报告已经很长，这里只放结论与决策表）。"""
    if db is None:
        db = load_db()
    cov, rows = coverage(cands), admission(cands, db)
    cnt: dict[str, int] = {}
    for d in db.get("dimensions", []):
        cnt[d["channel"]] = cnt.get(d["channel"], 0) + 1
    L = ["# 细分维度与官网配置事实（不加权）", "",
         "> **红线：`spec_facts.py` 的 `SCORING_ENABLED=False`**，apply 前后对 "
         "`scores/total/adjust` 做深比较断言；事实库在代码层面不可能改排名。", "",
         f"16 项细分维度分层结果：" + "、".join(
             f"{CHANNEL_LABELS[k]} {v} 项" for k, v in cnt.items()) + "。",
         "", decision_table(rows), "",
         f"已按 trim 采集官网/逐项参数事实 **{cov['verified']}/{cov['total']}** 款"
         f"（{cov['coverage_pct']:.0f}%）；所有 `candidate_core` / `thirdparty_cap` "
         f"维度当前覆盖率均低于 {ADMISSION_THRESHOLD:.0%}，按 G2 **整条禁用**，"
         "因此本期没有任何新增加分项。", "",
         "### 为什么不做「有数据就加一点分」", ""]
    for i, w in enumerate((db.get("policy") or {}).get("why_no_selective_bonus", []), 1):
        L.append(f"{i}. {w}")
    L += ["", "> 想看逐条依据与官网链接：`python3 spec_facts.py --report "
          "--candidates <候选池> --out spec_facts.md`；"
          "想让主观项影响自己的排序：用 `--sliders` 偏好滑杆，不写进默认榜。", ""]
    return "\n".join(L)


def report(cands: list[dict], db: dict | None = None) -> str:
    if db is None:
        db = load_db()
    cov, rows = coverage(cands), admission(cands, db)
    L = [summary_md(cands, db), "---", "",
         "## 准入门槛 G1–G4", ""]
    for g in (db.get("policy") or {}).get("admission_gates", []):
        L.append(f"- {g}")
    L += ["", "## 已采集的配置事实（按 trim，只展示）", "",
          "| 车型 | 评分配置 | 轮胎 | 制动 | 底盘/车身用料 | 隔音玻璃 | 智驾硬件 | 来源 |",
          "|---|---|---|---|---|---|---|---|"]
    for c in cands:
        s = c.get("spec_facts") or {}
        if s.get("status") != "verified":
            continue
        f = s.get("fields") or {}
        src = (f"[{s.get('source_type')} · {s.get('date')}]({s.get('url')})"
               if s.get("url") else "—")
        def v(x):
            return "—" if x in (None, "") else str(x).replace("|", "/")
        L.append(f"| {c['model']} | {v(s.get('trim'))} | {v(f.get('tire_spec'))} "
                 f"| {v(f.get('brake_type'))} | {v(f.get('chassis_material'))} "
                 f"| {v(f.get('nvh_glass'))} | {v(f.get('adas_hw_note'))} | {src} |")
    L += ["", "### 逐条依据（可点开复核）", ""]
    for c in cands:
        s = c.get("spec_facts") or {}
        if s.get("status") != "verified":
            continue
        L.append(f"- **{c['model']}**（{s.get('trim')}，{s.get('trim_scope')}）："
                 f"{s.get('quote')}｜{s.get('note') or ''} [来源]({s.get('url')})")
    if cov["todo"]:
        L += ["", "### 待补清单（无结构化事实，不参与任何判定）", "",
              f"共 {len(cov['todo'])} 款：{'、'.join(cov['todo'])}。", "",
              "> 未采集 ≠ 配置差。多为新上市/未上市车型，官网逐配置页尚未放出，"
              "或存在两源口径冲突而主动记 null。", ""]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--candidates", help="候选池 json")
    ap.add_argument("--todo", action="store_true")
    ap.add_argument("--admission", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()

    db = load_db(a.db)
    errs = validate(db)
    if a.validate or errs:
        print(f"{len(db.get('entries', []))} 条事实 / {len(db.get('dimensions', []))} 项维度；"
              + ("校验通过" if not errs else "校验失败：\n  " + "\n  ".join(errs)))
        if errs:
            sys.exit(1)
        if not a.candidates:
            return
    if not a.candidates:
        return
    d = json.load(open(a.candidates, encoding="utf-8"))
    cands = d.get("candidates", d)
    cands, _ = apply(cands, db)
    if a.todo:
        cov = coverage(cands)
        print(f"事实采集 {cov['verified']}/{cov['total']}（{cov['coverage_pct']:.0f}%）")
        print(f"待补 {len(cov['todo'])} 款：{'、'.join(cov['todo'])}")
        return
    if a.admission:
        for r in admission(cands, db):
            print(f"  {r['label']:12s} {CHANNEL_LABELS.get(r['channel']):16s} "
                  f"覆盖 {r['coverage_pct']:5.1f}%  {r['verdict']}")
        return
    txt = report(cands, db) if a.report else summary_md(cands, db)
    print(txt)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(txt + "\n")
        print(f"\n[ok] {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
