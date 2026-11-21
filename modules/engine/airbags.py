#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""被动安全气囊配置（airbags.py）——把「有没有气囊」变成可核验的事实列，而不是主分。

为什么单独做一个模块：
  侧气囊/头部气帘/前排中央（远端）气囊确实是重要的被动安全配置——
  NHTSA 对 7.3 万例致命事故的回归给出「侧气帘＋躯干气囊让近侧碰撞死亡率
  下降约 30%」，Euro NCAP 2020 起把 far-side 纳入评分，C-NCAP 2024 版乘员保护
  里「侧面远端乘员保护」8 分、「保压气帘性能要求」3 分。
  但**它们已经被官方成绩吸收了**：气囊是拿分手段，得分率是结果。
  再按「有几个气囊」加分，等于同一份保护效果算两遍，还会把榜单变成数气囊。

所以这个模块只做三件事：
  1 把按 trim 核验的三态配置（true / false / null）写进候选，报告出事实列；
  2 只对**没有官方碰撞成绩**的车，给 scores.safety 一个受限增量（|Δ| ≤ 0.3），
     补的是「无成绩车只能中位插补」造成的安全维常数化，不是给配置发奖；
  3 暴露待补清单：没核验的一律 null，不判劣、不造差异。

为什么不走 thirdparty.adjust：
  adjust 只平移加权总分，MCDA 矩阵里 safety 维仍是常数，权重依旧失效。
  要真正让 safety 权重生效，必须改 scores.safety。这是 v5.8 落地时相对初版方案的修正。

证据层级：官方发布 > 逐项参数配置表 > 厂商宣传口径。「N 气囊」是营销口径，
不映射到布尔字段（见 categories/airbags.json 里 MG 07 与海豹06EV 两个反例）。

用法：
    python3 airbags.py --validate
    python3 airbags.py --todo --candidates cands.json
    python3 airbags.py --report --candidates cands.json --out airbags.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import paths

HERE = str(paths.ROOT)
DEFAULT_DB = str(paths.cat("airbags.json"))
DEFAULT_CNCAP = os.path.join(HERE, "cncap_scores.json")

FIELDS = ("airbag_front_side", "airbag_curtain", "airbag_center")
LABELS = {"airbag_front_side": "前排侧气囊", "airbag_curtain": "头部气帘",
          "airbag_center": "前排中央（远端）气囊"}

# A3/A4：基本盘不加分，明确缺失 −0.3，中央（远端）气囊 +0.2；A5：合计封顶 ±0.3
DELTA_MISSING_BASE = -0.3
DELTA_CENTER = 0.2
DELTA_CAP = 0.3


def load_db(path: str = DEFAULT_DB) -> dict:
    if not os.path.exists(path):
        return {"entries": []}
    return json.load(open(path, encoding="utf-8"))


def validate(db: dict) -> list[str]:
    errs = []
    seen = set()
    for i, e in enumerate(db.get("entries", [])):
        where = f"entries[{i}]"
        for k in ("model", "trim", "source", "url", "date", "source_type", "quote"):
            if not e.get(k):
                errs.append(f"{where}: 缺 {k}（R1 可核验要求必填）")
        if e.get("model") in seen:
            errs.append(f"{where}: 车型重复 {e.get('model')}")
        seen.add(e.get("model"))
        if not str(e.get("url") or "").startswith(("http://", "https://")):
            errs.append(f"{where}: url 不是 http(s) 链接")
        for f in FIELDS:
            if f not in e:
                errs.append(f"{where}: 缺字段 {f}（三态：true / false / null，缺证据必须显式写 null）")
            elif e[f] not in (True, False, None):
                errs.append(f"{where}: {f} 必须是 true / false / null，收到 {e[f]!r}")
        if e.get("trim_scope") not in ("all_trims", "specific_trim", "series_partial", "series_default"):
            errs.append(f"{where}: trim_scope 取值非法")
    return errs


def official_names(path: str = DEFAULT_CNCAP) -> set[str]:
    """有官方 C-NCAP 成绩的车型名。

    v5.9 起月度链路已接 sync_cncap.py，候选池会写 safety_basis=cncap。
    但成绩表仍是唯一权威源：两者取「或」，任何一边命中即视为有成绩，
    避免链路某月对齐失败时把有成绩车误当成无成绩车、多发一次配置分。
    """
    if not os.path.exists(path):
        return set()
    try:
        d = json.load(open(path, encoding="utf-8"))
        return set((d.get("scores") or {}).keys())
    except Exception:
        return set()


def has_official_score(c: dict, table: set[str]) -> bool:
    return (c.get("model") in table
            or str(c.get("safety_basis") or "") == "cncap")


def delta_for(e: dict, has_official: bool) -> tuple[float, str]:
    """算受限增量。返回 (delta, 原因)，原因必须能写进报告被逐条核对。"""
    if has_official:
        return 0.0, "A1 已有官方碰撞成绩：配置只展示，不重复计分"
    side, cur = e.get("airbag_front_side"), e.get("airbag_curtain")
    if side is None or cur is None:
        return 0.0, "A2 前排侧气囊/头部气帘未核验（null）：不给分也不扣分"
    if side is False or cur is False:
        return DELTA_MISSING_BASE, "A3 明确缺前排侧气囊或头部气帘：−0.3（提示级硬伤）"
    if e.get("airbag_center") is True:
        return DELTA_CENTER, "A4 有前排中央（远端）气囊且无官方成绩：+0.2"
    return 0.0, "A3 侧气囊+气帘为基本盘，不加分；中央气囊未核验或无"


def apply(cands: list[dict], db: dict | None = None,
          cncap_path: str = DEFAULT_CNCAP) -> tuple[list[dict], list[dict]]:
    """把配置事实写进候选，并对无成绩车做受限 safety 修正。

    幂等：重复调用不会叠加 delta（先扣掉上次写的 airbag_delta）。
    """
    if db is None:
        db = load_db()
    errs = validate(db)
    if errs:
        raise SystemExit("气囊配置库未通过校验：\n  " + "\n  ".join(errs))
    table = official_names(cncap_path)
    by_model = {e["model"]: e for e in db.get("entries", [])}
    audit: list[dict] = []

    for c in cands:
        name = c.get("model")
        e = by_model.get(name)
        # 幂等：先撤回上一次本模块写进去的分
        prev = float(c.get("airbag_delta") or 0)
        if prev:
            sc = c.get("scores") or {}
            if sc.get("safety") is not None:
                sc["safety"] = round(max(0.0, min(10.0,
                                   float(sc["safety"]) - prev)), 2)
        c["airbag_delta"] = 0.0
        c.pop("airbags", None)

        if not e:
            c["airbags"] = {"status": "unknown", "trim": None,
                            "airbag_front_side": None, "airbag_curtain": None,
                            "airbag_center": None, "source": None, "url": None,
                            "date": None, "delta": 0.0,
                            "reason": "A2 未核验，列进待补清单"}
            continue

        off = has_official_score(c, table)
        d, why = delta_for(e, off)
        d = round(max(-DELTA_CAP, min(DELTA_CAP, d)), 2)
        rec = {
            "status": "verified",
            "trim": e.get("trim"),
            "trim_scope": e.get("trim_scope"),
            "airbag_front_side": e.get("airbag_front_side"),
            "airbag_curtain": e.get("airbag_curtain"),
            "airbag_center": e.get("airbag_center"),
            "source": e.get("source"), "url": e.get("url"),
            "date": e.get("date"), "source_type": e.get("source_type"),
            "quote": e.get("quote"), "note": e.get("note"),
            "conflict": bool(e.get("conflict")),
            "has_official_score": off,
            # 防御性检查：正常链路下 sync_cncap 已把有成绩车的 crash 改成
            # "C-NCAP xx%"，不会触发。触发说明该月对齐失败或成绩表与候选池不同步。
            "crash_text_conflict": bool(
                off and any(k in str(c.get("crash") or "")
                            for k in ("无官方成绩", "无成绩", "已抽车未测"))),
            "delta": d, "reason": why,
        }
        c["airbags"] = rec
        if d:
            sc = c.setdefault("scores", {})
            if sc.get("safety") is not None:
                sc["safety"] = round(max(0.0, min(10.0,
                                   float(sc["safety"]) + d)), 2)
            c["airbag_delta"] = d
            c["safety_confidence"] = "low"
            old = str(c.get("safety_note") or "").strip("｜")
            c["safety_note"] = (old + f"｜气囊配置受限修正 {d:+.2f}：{why}").strip("｜")
        elif off:
            old = str(c.get("safety_note") or "").strip("｜")
            c["safety_note"] = (old + "｜气囊配置已核验，但已有官方成绩，按 A1 不叠加").strip("｜")
        audit.append({"model": name, **{k: rec[k] for k in
                     ("trim", "airbag_front_side", "airbag_curtain",
                      "airbag_center", "has_official_score", "delta", "reason")}})
    return cands, audit


def coverage(cands: list[dict]) -> dict:
    verified = [c for c in cands if (c.get("airbags") or {}).get("status") == "verified"]
    todo = [c["model"] for c in cands
            if (c.get("airbags") or {}).get("status") != "verified"]
    adjusted = [c for c in cands if float(c.get("airbag_delta") or 0)]
    return {"total": len(cands), "verified": len(verified), "todo": todo,
            "adjusted": [c["model"] for c in adjusted],
            "coverage_pct": round(100.0 * len(verified) / len(cands), 1) if cands else 0.0}


def cell(v) -> str:
    return "有" if v is True else "无" if v is False else "—"


def short_tags(c: dict) -> str:
    a = c.get("airbags") or {}
    if a.get("status") != "verified":
        return "—"
    return "/".join(cell(a.get(f)) for f in FIELDS)


def report(cands: list[dict], db: dict | None = None) -> str:
    if db is None:
        db = load_db()
    cov = coverage(cands)
    L = ["# 被动安全配置（事实列，不计入加权）", "",
         "> 口径：按**带内推荐配置 trim** 核验，三态为「有 / 无 / —（未核验）」。"
         "「—」不等于没有，只是没查到可点开的依据。",
         "> 侧气囊与头部气帘是这一价位的基本盘，不给正分；"
         "前排中央（远端）气囊对应 C-NCAP 2024「侧面远端乘员保护 8 分」与 Euro NCAP far-side，"
         "但**有官方碰撞成绩的车一律不叠加配置分**——成绩已包含这些项目的实测结果。", ""]
    L.append(f"- 配置核验覆盖 **{cov['verified']}/{cov['total']}** 款"
             f"（{cov['coverage_pct']:.0f}%）；触发受限安全分修正 **{len(cov['adjusted'])}** 款。")
    if cov["adjusted"]:
        L.append(f"- 触发修正：{'、'.join(cov['adjusted'])}（|Δ| ≤ {DELTA_CAP:g}，"
                 "仅作用于无官方成绩车的 `scores.safety`，`safety_confidence=low`）。")
    L.append("")
    L.append("| 车型 | 评分配置 | 前排侧气囊 | 头部气帘 | 中央(远端)气囊 | 官方成绩 | Δ安全分 | 依据 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for c in cands:
        a = c.get("airbags") or {}
        if a.get("status") != "verified":
            continue
        trim = a.get("trim") or "—"
        src = (f"[{a.get('source_type')} · {a.get('date')}]({a.get('url')})"
               if a.get("url") else "—")
        off = "有" if a.get("has_official_score") else "无"
        d = f"{a.get('delta'):+.2f}" if a.get("delta") else "0"
        flag = " ⚠口径冲突" if a.get("conflict") else ""
        L.append(f"| {c['model']} | {trim} | {cell(a.get('airbag_front_side'))} | "
                 f"{cell(a.get('airbag_curtain'))} | {cell(a.get('airbag_center'))}{flag} | "
                 f"{off} | {d} | {src} |")
    conflicts = [c["model"] for c in cands
                 if (c.get("airbags") or {}).get("crash_text_conflict")]
    if conflicts:
        L.append("> ⚠ **口径冲突（本模块不改分，只披露）**："
                 + "、".join(conflicts)
                 + " 在 `cncap_scores.json` 里已有官方成绩，但候选池 `crash` 文本仍写「无官方成绩」。"
                 "常见原因是该月 `sync_cncap.py` 对齐失败或成绩表与候选池不同步。"
                 "本模块判「有无成绩」以成绩表为准（所以这些车不叠加配置分），"
                 "但它们的**人工安全分与官方得分率尚未对齐**，不要用本表反推安全分；"
                 "重跑月度链路或单独执行 `sync_cncap.py --fill-mode blend` 即可修复。")
        L.append("")
    L.append("### 逐条依据（可点开复核 / 可撤销）")
    L.append("")
    for c in cands:
        a = c.get("airbags") or {}
        if a.get("status") != "verified":
            continue
        L.append(f"- **{c['model']}**（{a.get('trim')}，{a.get('trim_scope')}）："
                 f"{a.get('quote')}｜{a.get('note') or ''} "
                 f"→ {a.get('reason')}｜[来源]({a.get('url')})")
    L.append("")
    if cov["todo"]:
        L.append("### 待补清单（未核验，不参与任何加减分）")
        L.append("")
        L.append(f"共 {len(cov['todo'])} 款：{'、'.join(cov['todo'])}。")
        L.append("")
        L.append("> 这些车**不是不安全，也不是没有气囊**，只是没有按 trim 核到可点开的配置页。"
                 "补法：在 `categories/airbags.json` 增加条目，url 必须能点开看到逐项配置；"
                 "「全系标配」可填 `trim_scope=all_trims`，按版本分档的填 `series_partial` 并把"
                 "不确定的字段写 null。")
    L.append("")
    L.append("### 规则边界")
    L.append("")
    for r in (db.get("policy") or {}).get("rules", []):
        L.append(f"- {r}")
    L.append("")
    L.append("> **「N 气囊」不进打分**：各厂数法不同（有的把气帘算 2 个、有的把远端气囊算进总数），"
             "本项目只认逐项列明的配置字段。库里 MG 07 的「全系 6 气囊」与海豹06EV 的"
             "「7 vs 11 两源冲突」就是两个刻意保留的反例。")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--candidates", help="候选池 json，用于 --todo / --report")
    ap.add_argument("--todo", action="store_true", help="只打印待补清单")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()

    db = load_db(a.db)
    errs = validate(db)
    if a.validate or errs:
        print(f"{len(db.get('entries', []))} 条；"
              + ("校验通过" if not errs else "校验失败：\n  " + "\n  ".join(errs)))
        if errs:
            sys.exit(1)
        if not a.candidates:
            return
    if not a.candidates:
        for e in db.get("entries", []):
            print(f"{e['model']:16s} | {e['trim']:14s} | "
                  + "/".join(cell(e.get(f)) for f in FIELDS)
                  + f" | {e.get('source_type')}")
        return

    d = json.load(open(a.candidates, encoding="utf-8"))
    cands = d.get("candidates", d)
    cands, _ = apply(cands, db)
    if a.todo:
        cov = coverage(cands)
        print(f"覆盖 {cov['verified']}/{cov['total']}（{cov['coverage_pct']:.0f}%）")
        print(f"触发修正：{'、'.join(cov['adjusted']) or '无'}")
        print(f"待补 {len(cov['todo'])} 款：{'、'.join(cov['todo'])}")
        return
    txt = report(cands, db)
    print(txt)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(txt + "\n")
        print(f"\n[ok] {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
