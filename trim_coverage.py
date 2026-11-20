#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""出榜前逐配置覆盖校验（trim_coverage.py）——v9.7.9 铁律的可执行守门。

用户 2026-11-20 明确要求：按车型配置出榜，缺配置数据榜单参考意义大打折扣，
宁愿多花时间搜集也不少搜集。本工具在出榜前强制校验每款车都有 trims[] 逐配置数据。

用法：
    python3 trim_coverage.py --candidates runs/x/candidates.json
    python3 trim_coverage.py --candidates runs/x/candidates.json --strict   # 缺配置则 exit 1
    python3 trim_coverage.py --selftest
"""
from __future__ import annotations
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TRIMS = os.path.join(HERE, "categories", "trims.json")
REQUIRED_FIELDS = ["name", "price", "battery_kwh", "cltc_km"]  # 核心4字段；电芯/快充缺只记 warning


def load_trims(path: str | None = None) -> dict:
    p = path or TRIMS
    if not os.path.exists(p):
        return {}
    d = json.load(open(p, encoding="utf-8"))
    return {e["model"]: e for e in d.get("entries", []) if e.get("model")}


def check(cands_path: str, trims_path: str | None = None) -> dict:
    pool = json.load(open(cands_path, encoding="utf-8"))
    cands = pool.get("candidates", pool) if isinstance(pool, dict) else pool
    tdb = load_trims(trims_path)
    missing, incomplete, ok = [], [], []
    for c in cands:
        m = c.get("model")
        e = tdb.get(m)
        if not e or not e.get("trims"):
            missing.append(m)
            continue
        # 检查配置字段完整度（至少一半配置有核心字段）
        good = sum(1 for t in e["trims"]
                   if all(t.get(f) not in (None, "", "—") for f in REQUIRED_FIELDS))
        if good < max(1, len(e["trims"]) // 2):
            incomplete.append((m, good, len(e["trims"])))
        else:
            ok.append((m, len(e["trims"])))
    return {"total": len(cands), "ok": ok, "missing": missing, "incomplete": incomplete}


def report(r: dict) -> str:
    L = [f"逐配置覆盖校验：{r['total']} 款候选",
         f"  ✓ 完整：{len(r['ok'])} 款",
         f"  ✗ 无配置数据：{len(r['missing'])} 款 —— {', '.join(r['missing']) or '无'}",
         f"  ⚠ 字段不全：{len(r['incomplete'])} 款 —— " +
         (', '.join(f"{m}({g}/{n})" for m, g, n in r['incomplete']) or '无')]
    if r["missing"] or r["incomplete"]:
        L.append("  ⛔ 出榜前必须补齐上述车型配置数据（trims.json），或标注配置待核移出主榜。")
    else:
        L.append("  ✅ 全部车型配置齐全，可出榜。")
    return "\n".join(L)


def selftest() -> int:
    r = check(os.path.join(HERE, "runs", "midsedan_bev_1218_202610", "candidates.json"))
    if not r["missing"] and not r["incomplete"]:
        print("[selftest OK] 覆盖校验可运行")
        return 0
    print("[selftest FAIL]", report(r))
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="出榜前逐配置覆盖校验")
    ap.add_argument("--candidates", help="候选池 json")
    ap.add_argument("--trims", help="配置库 json（默认 categories/trims.json）")
    ap.add_argument("--strict", action="store_true", help="有缺失则 exit 1")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(selftest())
    if not a.candidates:
        ap.error("需要 --candidates")
    r = check(a.candidates, a.trims)
    print(report(r))
    if a.strict and (r["missing"] or r["incomplete"]):
        raise SystemExit(1)
