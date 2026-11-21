#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原始库 → 事实库 全字段映射同步（facts_sync.py）v9.7.28。

用户要求：不要"原始库有数据、事实库没有"。原始库 all_data.json 有 19 个字段，
但事实库 fact_library.json 只映射了 5 个（kwh/consumption/cell_brand/cltc/source），
sales/life/life_note/trim/来源URL/证据引文等全部丢失。

本工具：
  1) 把原始库每个字段按映射表同步进事实库（不覆盖事实库里更权威的手工值）；
  2) 报告"原始有但事实库无"（已修复）与"原始无"（需另行网查核实）的字段；
  3) --check 只检查不同步，--apply 执行同步，--report 出字段覆盖报告。

用法：
  python3 facts_sync.py --report
  python3 facts_sync.py --check [--src <all_data.json>] [--facts <fact_library.json>]
  python3 facts_sync.py --apply
  python3 facts_sync.py --selftest
"""
from __future__ import annotations
import argparse
import json
import os
import sys

import paths

HERE = str(paths.ROOT)
# 原始库位置：优先环境变量，其次 skill 内随包副本，最后相对工作区约定路径
DEFAULT_SRC = (os.environ.get("CAR_ALL_DATA")
               or str(paths.cat("source_all_data.json"))
               or "")
DEFAULT_FACTS = str(paths.cat("fact_library.json"))

# 原始字段 → 事实库字段。None = 原样保留在 _source 子对象（证据/来源，不进主字段）
FIELD_MAP = {
    "kwh": "kwh",
    "consumption": "consumption",
    "cell": "cell_brand",
    "sales": "trend_sales",
    "life": "life_status",
    "life_note": "life_note",
    "note": "note",
    "trim": "scored_trim",
    "consumption_std": "consumption_std",
    "kwh_per_wan": "kwh_per_wan",
    "price": "price",
}
# 证据/来源字段，全部收进 _source 子对象（可核验，不参与打分）
SOURCE_FIELDS = ["price_src", "cons_src", "cons_quote", "cons_trim", "sales_src", "flags"]
# 原始库里没有、但榜单需要的字段 → 已逐一网查核实结论（2026-11-20）
# 格式: 字段: (结论, 依据, 处理)
NOT_IN_SOURCE_DETAIL = {
    "charge/voltage": ("有但漏采（已修 v9.7.27）", "汽车之家/易车参数页有高压平台(V)/快充时间(min)",
                       "已网查补110/110配置，写进 trims.json"),
    "crash": ("真没有（这批车）", "ice_crash.json 187款中这批紧凑纯电0命中；MG4仅Euro NCAP不在C-NCAP/C-IASI库",
              "按规则标缺失不记0分，有官方成绩再补"),
    "airbags": ("另有专门库", "airbags.json 38款，2026-10覆盖25/39(64%)", "不依赖原始库，专门库核验"),
    "adas_hw": ("另有专门库", "thirdparty_tests.json + 配置表 key_configs", "按配置级硬件字段处理"),
}
NOT_IN_SOURCE = list(NOT_IN_SOURCE_DETAIL)


def load(p):
    if not p or not os.path.exists(p):
        raise SystemExit(
            "[facts_sync] 找不到原始库。请设置环境变量 CAR_ALL_DATA 指向 all_data.json，"
            "或把副本放到 categories/source_all_data.json。")
    return json.load(open(p, encoding="utf-8"))


def sync(src_path=DEFAULT_SRC, facts_path=DEFAULT_FACTS, apply=False):
    src = load(src_path)
    rows = src.get("rows", {})
    facts = load(facts_path) if os.path.exists(facts_path) else {}
    changed = []
    for model, r in rows.items():
        f = facts.setdefault(model, {}) if apply else facts.get(model, {})
        if f is None:
            continue
        for sf, tf in FIELD_MAP.items():
            v = r.get(sf)
            if v in (None, "", [], {}):
                continue
            # 不覆盖事实库已有的非默认值（手工核验值更权威）
            if tf in f and f[tf] not in (None, "", "—"):
                continue
            if apply:
                f[tf] = v
            changed.append((model, sf, tf))
        # 来源/证据子对象
        sv = {k: r[k] for k in SOURCE_FIELDS if r.get(k) not in (None, "", [], {})}
        if sv:
            src_obj = f.get("_source", {}) if not apply else f.setdefault("_source", {})
            for k, v in sv.items():
                if apply:
                    src_obj.setdefault(k, v)
                elif src_obj.get(k) in (None, "", [], {}):
                    changed.append((model, k, f"_source.{k}"))
    if apply:
        facts["_meta"] = {
            "synced_from": os.path.basename(src_path),
            "sync_tool": "facts_sync.py",
            "field_map": FIELD_MAP,
            "source_fields": SOURCE_FIELDS,
            "note": "本段由原始库自动同步；手工核验值优先，不被覆盖。",
        }
        json.dump(facts, open(facts_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return changed, rows, facts


def report(src_path=DEFAULT_SRC, facts_path=DEFAULT_FACTS):
    src = load(src_path)
    rows = src.get("rows", {})
    facts = load(facts_path) if os.path.exists(facts_path) else {}
    all_src_fields = set()
    for r in rows.values():
        all_src_fields |= set(r.keys())
    mapped = set(FIELD_MAP) | set(SOURCE_FIELDS)
    print("=== 原始库 → 事实库 字段映射报告 ===")
    print(f"原始库 {len(rows)} 款 / {len(all_src_fields)} 字段；事实库 {len(facts)-1} 款车型")
    print("\n已映射字段（原始→事实）:")
    for sf, tf in FIELD_MAP.items():
        n = sum(1 for r in rows.values() if r.get(sf) not in (None, "", [], {}))
        print(f"  {sf:20s} → {tf:20s} 原始覆盖 {n}/73")
    print("\n来源/证据字段（收进 _source）:")
    for sf in SOURCE_FIELDS:
        n = sum(1 for r in rows.values() if r.get(sf) not in (None, "", [], {}))
        print(f"  {sf:20s} 原始覆盖 {n}/73")
    unmapped = sorted(all_src_fields - mapped)
    print("\n原始库未映射字段:", unmapped or "无")
    print("\n原始库没有的字段——逐一核实结论:")
    for f, (conclusion, basis, action) in NOT_IN_SOURCE_DETAIL.items():
        print(f"  {f:16s} {conclusion}")
        print(f"    依据: {basis}")
        print(f"    处理: {action}")
    # 检查事实库当前是否已同步
    synced = "_meta" in facts and facts["_meta"].get("sync_tool") == "facts_sync.py"
    print(f"\n事实库已执行同步: {'是' if synced else '否（运行 --apply）'}")
    return synced


def selftest():
    changed, rows, facts = sync(apply=False)
    assert rows and len(rows) > 50, "原始库读取失败"
    assert "cell" in FIELD_MAP and FIELD_MAP["cell"] == "cell_brand"
    assert "charge/voltage" in NOT_IN_SOURCE_DETAIL, "电压核实结论缺失"
    print(f"[selftest OK] 映射表 {len(FIELD_MAP)} 字段，原始库 {len(rows)} 款")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="原始库→事实库字段映射同步")
    ap.add_argument("--src", default=DEFAULT_SRC)
    ap.add_argument("--facts", default=DEFAULT_FACTS)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(selftest())
    if a.report:
        report(a.src, a.facts)
    elif a.apply:
        changed, _, _ = sync(a.src, a.facts, apply=True)
        print(f"[apply] 同步 {len(changed)} 个字段值到 {a.facts}")
    elif a.check:
        changed, _, _ = sync(a.src, a.facts, apply=False)
        if changed:
            print(f"[check] 有 {len(changed)} 个字段未同步（原始有、事实库无）")
            for m, sf, tf in changed[:15]:
                print(f"  {m}: {sf} → {tf}")
            raise SystemExit(1)
        print("[check] 全部字段已同步")
    else:
        ap.error("指定 --report / --check / --apply / --selftest 之一")
