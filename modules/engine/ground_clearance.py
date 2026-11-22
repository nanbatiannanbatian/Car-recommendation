#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最小离地间隙（ground_clearance.py）——按入榜配置核验的托底风险事实列 + 受限 safety 修正。

为什么单独做一个模块（2026-11-22，用户提出"离地间隙降低电池磕碰概率应加分"）：
  纯电车电池包平铺底盘，离地间隙比同级燃油SUV低15-20mm，托底磕碰是热失控高频诱因。
  国标 GB38031-2025 新增底部撞击测试（35km/h 撞凸起硬物、电池不起火不爆炸），
  且明确"电池不布置于底盘、离地间隙充足的车型可豁免"——标准层面确认该逻辑成立。

为什么只给受限增量、且不与 CTB/气囊重复计分：
  · 气囊/CTB 是"碰撞时的结构防护"（已被碰撞成绩吸收），离地间隙是"降低磕碰发生概率"，机制不同；
  · 但离地间隙不改变碰撞成绩，只是通过性物理事实，故统一空载口径 ±0.3 小幅修正；
  · 遵守 evidence_gate 单调性门禁：调整后无成绩车 safety 不得高于有成绩车最低分。

约束（与 airbags.py 同构）：
  - 幂等：重复调用先撤回上次写入的 gc_delta，不叠加。
  - 只信 SSOT categories/ground_clearance.json 里逐车核验、带 URL 与引述的条目。
  - unknown（官方未公布）不推断、不扣分，列待补清单。
  - 冲突取低值（已在数据文件裁决），conflict=true 披露。
  - 本模块改 scores.safety，必须在 safety_check 门禁之前调用（pipeline 已按 airbags 位置接入）。

用法：
    from modules.engine import ground_clearance as GC
    cands, audit = GC.apply(cands)
    python3 ground_clearance.py --selftest
    python3 ground_clearance.py --todo
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import paths

HERE = str(paths.ROOT)
DEFAULT_DB = str(paths.cat("ground_clearance.json"))

# 分档阈值（空载最小离地间隙 mm）与受限增量
TH_HIGH = 175          # >=175mm：通过性优势
TH_LOW = 150           # <150mm：托底风险提示
DELTA_HIGH = 0.3
DELTA_LOW = -0.3
DELTA_CAP = 0.3


def load_db(path: str = DEFAULT_DB) -> dict:
    if not os.path.exists(path):
        return {"entries": [], "policy": {}}
    return json.load(open(path, encoding="utf-8"))


def validate(db: dict) -> list[str]:
    errs = []
    seen = set()
    for i, e in enumerate(db.get("entries", [])):
        where = f"entries[{i}]"
        for k in ("model", "trim", "status", "source_type", "url", "quote"):
            if not e.get(k):
                errs.append(f"{where}: 缺 {k}（可核验要求必填）")
        if e.get("model") in seen:
            errs.append(f"{where}: 车型重复 {e.get('model')}")
        seen.add(e.get("model"))
        if e.get("status") not in ("verified", "unknown"):
            errs.append(f"{where}: status 必须是 verified/unknown")
        mm = e.get("clearance_mm")
        if e.get("status") == "verified":
            if not isinstance(mm, (int, float)) or mm <= 0 or mm > 400:
                errs.append(f"{where}: verified 但 clearance_mm 非法 ({mm})")
        else:
            if mm is not None:
                errs.append(f"{where}: unknown 但 clearance_mm 非 null（不得推断）")
        if not str(e.get("url") or "").startswith(("http://", "https://")):
            errs.append(f"{where}: url 不是 http(s) 链接")
    return errs


def delta_for(e: dict) -> tuple[float, str]:
    """算受限增量。返回 (delta, 原因)，原因必须能写进报告逐条核对。"""
    if e.get("status") != "verified" or e.get("clearance_mm") is None:
        return 0.0, "R4 官方未公布离地间隙（unknown）：不推断、不扣分，列待补"
    mm = float(e["clearance_mm"])
    if mm >= TH_HIGH:
        return DELTA_HIGH, f"空载离地间隙 {mm:g}mm ≥ {TH_HIGH}mm：通过性优势 +0.3（仅降低托底概率，不替代护板/CTB）"
    if mm < TH_LOW:
        return DELTA_LOW, f"空载离地间隙 {mm:g}mm < {TH_LOW}mm：托底风险提示 −0.3"
    return 0.0, f"空载离地间隙 {mm:g}mm 处于 {TH_LOW}~{TH_HIGH}mm 常规区间：0"


def _has_official(c: dict) -> bool:
    from modules.engine import crash_systems as CS
    return bool(CS.parse_score(c.get("crash")))


def apply(cands: list[dict], db: dict | None = None,
          monotonic_guard: bool = True) -> tuple[list[dict], list[dict]]:
    """把离地间隙事实写进候选，并对 safety 做受限修正。

    幂等：重复调用先扣掉上次写的 gc_delta。
    单调性保护：先算所有 delta，再检查是否破坏"有实锤车 >= 无实锤车"，
    若破坏则按比例回缩无成绩车的正 delta（只压不抬，保证门禁通过）。
    """
    if db is None:
        db = load_db()
    errs = validate(db)
    if errs:
        raise SystemExit("离地间隙事实库未通过校验：\n  " + "\n  ".join(errs))
    by_model = {e["model"]: e for e in db.get("entries", [])}

    # ① 幂等撤回 + 清标记
    for c in cands:
        prev = float(c.get("gc_delta") or 0)
        if prev:
            sc = c.get("scores") or {}
            if sc.get("safety") is not None:
                sc["safety"] = round(max(0.0, min(10.0,
                                   float(sc["safety"]) - prev)), 2)
        c["gc_delta"] = 0.0
        c.pop("ground_clearance", None)

    # ② 逐车算 delta（先不落分，收集起来做单调性保护）
    plans = []
    for c in cands:
        name = c.get("model")
        e = by_model.get(name)
        if not e:
            c["ground_clearance"] = {
                "status": "unknown", "clearance_mm": None, "trim": None,
                "source": None, "url": None, "source_type": None, "quote": None,
                "conflict": False, "delta": 0.0,
                "reason": "R4 未收录，列待补清单",
            }
            continue
        d, why = delta_for(e)
        d = round(max(-DELTA_CAP, min(DELTA_CAP, d)), 2)
        rec = {
            "status": e.get("status"),
            "clearance_mm": e.get("clearance_mm"),
            "trim": e.get("trim"),
            "source": e.get("source"), "url": e.get("url"),
            "date": e.get("date"), "source_type": e.get("source_type"),
            "quote": e.get("quote"), "note": e.get("note"),
            "conflict": bool(e.get("conflict")),
            "has_official_score": _has_official(c),
            "delta": d, "reason": why,
        }
        c["ground_clearance"] = rec
        plans.append((c, rec, d, why))

    # ③ 单调性保护：有成绩车最低 safety vs 无成绩车最高 safety（含本轮 delta）
    if monotonic_guard:
        proven_after, unproven_after = [], []
        for c in cands:
            sc = c.get("scores") or {}
            s = sc.get("safety")
            if s is None:
                continue
            d = float(c.get("ground_clearance", {}).get("delta") or 0)
            (proven_after if _has_official(c) else unproven_after).append(
                (c, float(s), d))
        if proven_after and unproven_after:
            min_proven = min(v for _, v, _ in proven_after)
            # 无成绩车中，加正分后可能超过有成绩车最低分的，回缩其正 delta
            for c, v, d in unproven_after:
                if d > 0 and v + d > min_proven:
                    allowed = round(max(0.0, min_proven - v), 2)
                    rec = c.get("ground_clearance") or {}
                    rec["delta"] = allowed
                    rec["reason"] += f"；单调性保护：正增量由 {d:+.2f} 回缩至 {allowed:+.2f}（不得高于有成绩车最低分 {min_proven:g}）"
                    # 同步修正 plans 里的 d
                    for i, (pc, pr, pd, pw) in enumerate(plans):
                        if pc is c:
                            plans[i] = (pc, rec, allowed, pw)
                            break

    # ④ 落分 + 审计字段
    audit = []
    for c, rec, d, why in plans:
        if d:
            sc = c.setdefault("scores", {})
            if sc.get("safety") is not None:
                sc["safety"] = round(max(0.0, min(10.0,
                                   float(sc["safety"]) + d)), 2)
            c["gc_delta"] = d
            old = str(c.get("safety_note") or "").strip("｜")
            c["safety_note"] = (old + f"｜离地间隙修正 {d:+.2f}：{why}").strip("｜")
        audit.append({"model": c.get("model"),
                      "clearance_mm": rec.get("clearance_mm"),
                      "status": rec.get("status"),
                      "conflict": rec.get("conflict"),
                      "has_official_score": rec.get("has_official_score"),
                      "delta": d, "reason": why})
    return cands, audit


def coverage(cands: list[dict]) -> dict:
    total = len(cands)
    verified = [c for c in cands
                if (c.get("ground_clearance") or {}).get("status") == "verified"]
    unknown = [c for c in cands
               if (c.get("ground_clearance") or {}).get("status") == "unknown"]
    adjusted = [c for c in cands if float(c.get("gc_delta") or 0)]
    conflicts = [c for c in cands if (c.get("ground_clearance") or {}).get("conflict")]
    return {
        "total": total, "verified": len(verified), "unknown": len(unknown),
        "adjusted": len(adjusted), "conflicts": len(conflicts),
        "todo": [c.get("model") for c in unknown],
        "conflict_models": [c.get("model") for c in conflicts],
    }


def report(cands: list[dict], db: dict | None = None) -> str:
    cov = coverage(cands)
    lines = [
        "### 最小离地间隙（安全维受限修正）",
        "",
        f"覆盖 **{cov['verified']}/{cov['total']}** 款（空载口径）；"
        f"待核 {cov['unknown']} 款不推断不扣分；调整 {cov['adjusted']} 款；"
        f"冲突取低 {cov['conflicts']} 款。",
        "",
        "口径：≥175mm +0.3 / 150~175mm 0 / <150mm −0.3，"
        "只代表降低托底概率，不与气囊/CTB 结构防护重复计分；"
        "有官方碰撞成绩车照常计（底部撞击非现测项），但有单调性保护。",
        "",
    ]
    if cov["todo"]:
        lines.append("待补清单：" + "、".join(cov["todo"]) + "。")
        lines.append("")
    lines.append("| 车型 | 空载离地间隙 | 配置 | delta | 依据 |")
    lines.append("|---|---|---|---|---|")
    for c in cands:
        gc = c.get("ground_clearance") or {}
        if gc.get("status") != "verified":
            continue
        mm = gc.get("clearance_mm")
        d = float(gc.get("delta") if gc.get("delta") is not None else (c.get("gc_delta") or 0))
        flag = " ⚠冲突取低" if gc.get("conflict") else ""
        reason = str(gc.get("reason") or "")
        # 回缩时 reason 末尾带「单调性保护…」，优先显示它，避免只看前半段误以为给了满分
        if "单调性保护" in reason:
            reason = "原档 +0.3 → " + reason.split("单调性保护：", 1)[1]
        lines.append(f"| {c.get('model')} | {mm}mm{flag} | {gc.get('trim') or '-'} | "
                     f"{d:+.2f} | {reason[:72]} |")
    return "\n".join(lines)


# ---------------- selftest ----------------
def selftest() -> bool:
    checks = []

    # 1. 数据文件校验
    db = load_db()
    checks.append(("数据文件校验通过", not validate(db)))
    checks.append(("覆盖20款", len(db["entries"]) == 20))
    checks.append(("2款unknown", sum(1 for e in db["entries"] if e["status"] == "unknown") == 2))

    # 2. 分档逻辑
    checks.append(("≥175 +0.3", delta_for({"status": "verified", "clearance_mm": 197})[0] == 0.3))
    checks.append(("165 0分", delta_for({"status": "verified", "clearance_mm": 165})[0] == 0.0))
    checks.append(("<150 -0.3", delta_for({"status": "verified", "clearance_mm": 140})[0] == -0.3))
    checks.append(("unknown 0分", delta_for({"status": "unknown", "clearance_mm": None})[0] == 0.0))

    # 3. apply 幂等
    cands = [
        {"model": "极氪7X", "scores": {"safety": 8.7}, "crash": "无官方成绩"},
        {"model": "马自达EZ-60", "scores": {"safety": 8.2}, "crash": "无官方成绩"},
        {"model": "小鹏MONA L05", "scores": {"safety": 8.6}, "crash": "无官方成绩"},
    ]
    cands, _ = apply(cands, db)
    s1 = [c["scores"]["safety"] for c in cands]
    cands, _ = apply(cands, db)
    s2 = [c["scores"]["safety"] for c in cands]
    checks.append(("幂等：重复调用不叠加", s1 == s2))
    checks.append(("极氪7X 197mm +0.3", abs(s1[0] - 9.0) < 0.01))
    checks.append(("马自达EZ-60 140mm -0.3", abs(s1[1] - 7.9) < 0.01))
    checks.append(("MONA unknown 不变", abs(s1[2] - 8.6) < 0.01))

    # 4. 单调性保护：无成绩车已很高，不能因+0.3超过有成绩车
    cands2 = [
        {"model": "极氪7X", "scores": {"safety": 8.7}, "crash": "无官方成绩"},
        {"model": "比亚迪宋L EV", "scores": {"safety": 8.6}, "crash": "C-NCAP 90% 五星"},
    ]
    cands2, _ = apply(cands2, db)
    # 宋L 160mm delta=0 → 有成绩车最低8.6；极氪7X 8.7+0.3=9.0 会超出 → 回缩到0
    checks.append(("单调性保护触发", float(cands2[0].get("gc_delta") or 0) == 0.0))

    bad = [n for n, ok in checks if not ok]
    for n, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {n}")
    if bad:
        print(f"selftest FAIL: {len(bad)} 项")
        return False
    print(f"selftest OK: {len(checks)}/{len(checks)}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--todo", action="store_true")
    ap.add_argument("--candidates")
    a = ap.parse_args()
    if a.selftest:
        return 0 if selftest() else 1
    if a.todo:
        db = load_db()
        todo = [e["model"] for e in db["entries"] if e["status"] == "unknown"]
        print("待补离地间隙清单：")
        for m in todo:
            print("  -", m)
        return 0
    if a.candidates:
        cands = json.load(open(a.candidates, encoding="utf-8"))
        items = cands.get("candidates", cands) if isinstance(cands, dict) else cands
        cands, audit = apply(items)
        print(report(cands))
        print(json.dumps(coverage(cands), ensure_ascii=False, indent=2))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
