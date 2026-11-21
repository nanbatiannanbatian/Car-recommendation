# -*- coding: utf-8 -*-
"""电芯元数据校验/回填：把 cell_facts.json（单一事实源）同步到候选池快照。

背景：cell_facts.json 是 2026-11-16 核验过的电芯品牌表，但其 note 明示
「本表只补事实，不改候选池快照」，导致 baseline/candidates 的 cell_brand
长期停留 null 或旧值。主打分链路读预填 scores、不读 cell_brand，所以不一致
一直潜伏；但配置级比较榜(spec_score)读 cell_brand，会因此漏掉一线电芯加成。

用法：
    python sync_cell_brand.py --check <候选池.json>          # 只报告不一致（默认）
    python sync_cell_brand.py --apply <候选池.json>          # 原地回填并写 .bak
    python sync_cell_brand.py --check <候选池.json> --json out.json

规则：
  - 候选 cell_brand 为空、事实库有值  -> MISSING，回填
  - 两边都有值但不同                  -> CONFLICT，仅报告（不自动覆盖，需人工判）
  - 回填时写 cell_brand_src=cell_facts.json + verified 日期，便于追溯
  - 不改 scores，不动任何分数；只修元数据快照
"""
import argparse
import json
import os
import shutil
import sys
import paths

SKILL = str(paths.ROOT)
FACTS = str(paths.cat("cell_facts.json"))


def load_facts():
    d = json.load(open(FACTS, encoding="utf-8"))
    rows = d.get("entries", [])
    return {f["model"]: f for f in rows if f.get("model")}, d.get("verified", "")


def detect_indent(path):
    """探测 JSON 文件缩进宽度：看第一个键行前导空格数。默认 1。"""
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
        for line in lines[1:6]:
            stripped = line.lstrip(" \t")
            if stripped and stripped != line and stripped[0] in '{["':
                lead = len(line) - len(stripped)
                return lead if lead > 0 else 1
    except OSError:
        pass
    return 1


def candidates_of(raw):
    return raw.get("candidates", raw) if isinstance(raw, dict) else raw


def check(cands, fmap):
    missing, conflict, ok = [], [], 0
    for c in cands:
        m = c.get("model")
        f = fmap.get(m)
        if not f:
            continue
        fb = f.get("cell_brand")
        cb = c.get("cell_brand")
        if cb is None or (isinstance(cb, str) and not cb.strip()):
            if fb:
                missing.append((m, cb, fb))
            else:
                ok += 1
        elif fb and cb != fb:
            conflict.append((m, cb, fb))
        else:
            ok += 1
    return missing, conflict, ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("candidates")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--json", help="把报告另存为 JSON")
    a = ap.parse_args()

    fmap, verified = load_facts()
    raw = json.load(open(a.candidates, encoding="utf-8"))
    cands = candidates_of(raw)
    missing, conflict, ok = check(cands, fmap)

    report = {
        "facts_verified": verified,
        "n_candidates": len(cands),
        "n_facts": len(fmap),
        "missing": [{"model": m, "pool": cb, "fact": fb} for m, cb, fb in missing],
        "conflict": [{"model": m, "pool": cb, "fact": fb} for m, cb, fb in conflict],
        "ok": ok,
    }

    print(f"[{a.candidates}]")
    print(f"候选 {len(cands)} 款 · 事实库 {len(fmap)} 款 · 核验日 {verified}")
    print(f"一致 {ok} · 待回填 {len(missing)} · 冲突 {len(conflict)}")
    if missing:
        print("\n-- 待回填（池为空、事实库有值）--")
        for m, cb, fb in missing:
            print(f"  {m:<16} {cb!r:<8} -> {fb}")
    if conflict:
        print("\n-- 冲突（两边有值不同，需人工判，不自动覆盖）--")
        for m, cb, fb in conflict:
            print(f"  {m:<16} 池={cb!r:<22} 事实={fb!r}")

    if a.json:
        json.dump(report, open(a.json, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"\n报告已存 {a.json}")

    if a.apply and missing:
        bak = a.candidates + ".bak-cell"
        shutil.copy2(a.candidates, bak)
        n = 0
        for c in cands:
            f = fmap.get(c.get("model"))
            if not f or not f.get("cell_brand"):
                continue
            if c.get("cell_brand") is None or (
                    isinstance(c.get("cell_brand"), str) and not c["cell_brand"].strip()):
                c["cell_brand"] = f["cell_brand"]
                c["cell_brand_src"] = {
                    "file": "categories/cell_facts.json",
                    "verified": f.get("verified"),
                    "source_url": f.get("source_url"),
                    "tier": f.get("cell_tier"),
                }
                n += 1
        if isinstance(raw, dict):
            raw["candidates"] = cands
        # 沿用原文件缩进宽度，避免整文件重排制造无谓 diff
        indent = detect_indent(a.candidates)
        with open(a.candidates, "w", encoding="utf-8") as fh:
            json.dump(raw, fh, ensure_ascii=False, indent=indent)
        print(f"\n已回填 {n} 款（indent={indent}），原文件备份 {bak}（只改元数据，未动 scores）")


if __name__ == "__main__":
    main()
