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

import paths

HERE = str(paths.ROOT)
TRIMS = str(paths.cat("trims.json"))
# v9.7.33：电芯提升为必需字段——此前"电芯缺只记warning"导致主榜11/20显示"—"却校验全绿。
# 铁律：能在主榜展示的字段必须先校验有值，不许"校验通过但展示空白"。
REQUIRED_FIELDS = ["name", "price", "battery_kwh", "cltc_km"]
# v9.7.33：电芯单独校验（配置级 或 事实库车系级 有值即可，与展示回退链一致）
CELL_FIELD = "cell_supplier"


def load_trims(path: str | None = None) -> dict[str, dict]:
    """读逐配置事实。**统一走 trims_store**（人工库 + 自动库合并读视图）。

    2026-11-22：原来这里直接 json.load(trims.json)，而人工条目在自动库里
    另有一份副本 → 读到哪份取决于文件状态，同一事实两个读法。
    现在人工与自动在存储层互斥，合流只在 trims_store 一处发生。
    path 参数保留：非空时读指定文件（测试/临时排查用）。
    """
    if path:
        if not os.path.exists(path):
            return {}
        d = json.load(open(path, encoding="utf-8"))
        return {e["model"]: e for e in d.get("entries", []) if e.get("model")}
    from modules.data.trims_store import load as _load
    return _load()


def check(cands_path: str, trims_path: str | None = None) -> dict:
    pool = json.load(open(cands_path, encoding="utf-8"))
    cands = pool.get("candidates", pool) if isinstance(pool, dict) else pool
    tdb = load_trims(trims_path)
    facts_path = os.path.join(os.path.dirname(TRIMS), "fact_library.json")
    facts = json.load(open(facts_path, encoding="utf-8")) if os.path.exists(facts_path) else {}
    missing, incomplete, ok, no_cell = [], [], [], []
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
            continue
        # v9.7.33：电芯必须"配置级 或 车系事实库"至少一处有值（与 main_cell 回退链一致）
        cell_ok = any(t.get(CELL_FIELD) not in (None, "", "—") for t in e["trims"]) or \
                  str((facts.get(m) or {}).get("cell_brand") or "") not in ("", "—")
        if not cell_ok:
            no_cell.append(m)
        else:
            ok.append((m, len(e["trims"])))
    return {"total": len(cands), "ok": ok, "missing": missing,
            "incomplete": incomplete, "no_cell": no_cell}


def report(r: dict) -> str:
    L = [f"逐配置覆盖校验：{r['total']} 款候选",
         f"  ✓ 完整：{len(r['ok'])} 款",
         f"  ✗ 无配置数据：{len(r['missing'])} 款 —— {', '.join(r['missing']) or '无'}",
         f"  ⚠ 字段不全：{len(r['incomplete'])} 款 —— " +
         (', '.join(f"{m}({g}/{n})" for m, g, n in r['incomplete']) or '无')]
    L.append(f"  ✗ 无电芯来源：{len(r.get('no_cell', []))} 款 —— " +
             (', '.join(r.get('no_cell', [])) or '无'))
    if r["missing"] or r["incomplete"] or r.get("no_cell"):
        L.append("  ⛔ 出榜前必须补齐上述车型配置数据（trims.json / fact_library.json），或标注配置待核移出主榜。")
    else:
        L.append("  ✅ 全部车型配置齐全，可出榜。")
    return "\n".join(L)


def _uname(s):
    """车型名归一化：去空格、小写、去品牌前缀+动力后缀，用于 universe 比对。
    销量榜用「MG 07 EV/特斯拉Model 3/ZEEKR 007」，候选池用「MG 07 纯电/特斯拉Model 3/极氪007」——
    精确字符串会误报漏收，必须归一化。"""
    n = "".join(c for c in str(s).lower() if c.isalnum())
    for suf in ("ev", "pureev", "bev", "phev", "dmi", "evphev", "纯电"):
        if n.endswith(suf) and len(n) > len(suf) + 2:
            n = n[:-len(suf)]
            break
    # 品牌前缀归一化：中英文品牌统一去掉（只留车型主体）
    for brand in ("特斯拉", "tesla", "zeekr", "极氪", "byd", "比亚迪", "上汽mg", "mg", "蔚来", "nio"):
        if n.startswith(brand) and len(n) > len(brand) + 2:
            n = n[len(brand):]
            break
    return n


def check_universe(cands_path: str, universe_path: str | None, excluded: list | None = None) -> dict:
    """候选池完整性盘点（v9.7.24）：对照权威车型清单，报出漏收的车型。
    universe json 格式：{"models": ["车型A", ...]} 或纯 list。
    v9.7.43：用归一化名称比对（去空格/EV后缀），避免别名误报漏收。"""
    if not universe_path or not os.path.exists(universe_path):
        return {"available": False}
    u = json.load(open(universe_path, encoding="utf-8"))
    universe = set(u.get("models", []) if isinstance(u, dict) else u)
    pool = json.load(open(cands_path, encoding="utf-8"))
    cands = pool.get("candidates", []) if isinstance(pool, dict) else pool
    inpool = {c.get("model") for c in cands}
    excluded = {x.get("model") for x in (excluded or [])}
    # 归一化集合
    uni_n = {_uname(x): x for x in universe}
    in_n = {_uname(x) for x in inpool}
    exc_n = {_uname(x) for x in excluded}
    missing_n = sorted(set(uni_n) - in_n - exc_n)
    missing = [uni_n[n] for n in missing_n]  # 用 universe 原名报出
    return {"available": True, "universe": len(universe), "in_pool": len(inpool),
            "excluded_with_reason": len(in_n & exc_n) + len(exc_n & set(uni_n)),
            "not_covered": missing}


def report_universe(r: dict) -> str:
    if not r.get("available"):
        return "完整性盘点：未提供 --universe 清单，跳过"
    L = [f"候选池完整性盘点：权威清单 {r['universe']} 款，入池 {r['in_pool']} 款，"
         f"已列明原因未入池 {r['excluded_with_reason']} 款"]
    if r["not_covered"]:
        L.append(f"  ⛔ 未覆盖且未说明原因 {len(r['not_covered'])} 款：{', '.join(r['not_covered'])}")
        L.append("  → 按铁律：能取配置的入池，不能取的写明原因进 pool_excluded[]，不许漏掉。")
    else:
        L.append("  ✅ 权威清单全部覆盖或已说明未入池原因")
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
    ap.add_argument("--universe", help="权威车型清单 json（完整性盘点，对照是否漏收）")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(selftest())
    if not a.candidates:
        ap.error("需要 --candidates")
    r = check(a.candidates, a.trims)
    print(report(r))
    if a.universe:
        pool = json.load(open(a.candidates, encoding="utf-8"))
        ex = pool.get("pool_excluded", []) if isinstance(pool, dict) else []
        ur = check_universe(a.candidates, a.universe, ex)
        print(report_universe(ur))
    if a.strict and (r["missing"] or r["incomplete"]):
        raise SystemExit(1)
