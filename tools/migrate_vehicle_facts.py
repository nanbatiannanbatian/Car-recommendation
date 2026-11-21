#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/migrate_vehicle_facts.py —— 车辆事实单一 JSON 迁移（v11.0.0，一次性）

把九个分散源无损合并成 categories/vehicle_facts.json（唯一可写源）：
  contracts/vehicle_facts_seed.py CELL_SEED/CELL_LEGACY → models[].cell
  categories/fact_library.json      → models[].energy
  categories/vehicle_physics.json   → models[].physics
  categories/airbags.json           → models[].airbags（列表，逐配置）
  categories/trims_manual.json      → models[].trims_manual（列表）
  cncap_scores.json                 → models[].crash_cncap / crash_cncap_no_score
  official_safety_scores.json       → models[].crash_official（口径部分进 domains_meta）
  categories/experience_facts.json  → models[].experience（列表）
  categories/residual_values.json   → models[].residual

无损保证：内存内按合并结果重建全部旧视图文件，与原文件深比较（dict/list 相等）。
列表类源（physics cars / airbags entries / trims_manual entries / experience entries）
靠每条记录携带的 _idx 还原原始顺序。

用法：python3 tools/migrate_vehicle_facts.py [--apply]
默认只演练（报告 + 重建验证）；--apply 才写 categories/vehicle_facts.json。
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)  # tools/ 的上一级 = 项目根
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import paths
from modules.contracts import vehicle_facts_seed as SEED
from modules.contracts.model_names import canon

ROOT = str(paths.ROOT)


def load(p):
    return json.load(open(p, encoding="utf-8"))


def strip_meta(rec):
    return {k: v for k, v in rec.items() if k not in ("_model", "_idx")}


def main() -> int:
    apply = "--apply" in sys.argv

    fl = load(paths.cat("fact_library.json"))
    vp = load(paths.cat("vehicle_physics.json"))
    ab = load(paths.cat("airbags.json"))
    tm = load(paths.cat("trims_manual.json"))
    cn = load(paths.root("cncap_scores.json"))
    off = load(paths.root("official_safety_scores.json"))
    ef = load(paths.cat("experience_facts.json"))
    rv = load(paths.cat("residual_values.json"))
    cf_on_disk = load(paths.gen("cell_facts.json")) if paths.gen("cell_facts.json").exists() \
        else load(paths.cat("cell_facts.json"))

    models: dict[str, dict] = {}
    collisions: list[str] = []

    def add(raw_name: str, domain: str, rec: dict, idx: int):
        k = canon(raw_name)
        m = models.setdefault(k, {"names": []})
        if raw_name not in m["names"]:
            m["names"].append(raw_name)
        if domain in m:
            collisions.append(f"{raw_name}({domain})")
            return
        rec = dict(rec)
        rec["_model"] = raw_name
        rec["_idx"] = idx
        m[domain] = rec

    def add_list(raw_name: str, domain: str, rec: dict, idx: int):
        k = canon(raw_name)
        m = models.setdefault(k, {"names": []})
        if raw_name not in m["names"]:
            m["names"].append(raw_name)
        m.setdefault(domain, []).append({**dict(rec), "_model": raw_name, "_idx": idx})

    # 1) 电芯（契约层种子迁入；seed 优先于 legacy）
    for i, r in enumerate(SEED.CELL_SEED):
        (m, board, trim, price, kwh, brand, tier, source, date, quote) = r
        add(m, "cell", {"board": board, "trim": trim, "price": price, "kwh": kwh,
                        "cell_brand": brand, "tier": tier, "source": source,
                        "date": date, "quote": quote}, i)
    seed_canon = {canon(r[0]) for r in SEED.CELL_SEED}
    legacy_skipped = 0
    for i, r in enumerate(SEED.CELL_LEGACY):
        (m, kwh, brand, source, quote) = r
        if canon(m) in seed_canon:
            legacy_skipped += 1
            continue
        add(m, "cell", {"kwh": kwh, "cell_brand": brand, "source": source, "quote": quote}, i)

    # 2) 能量事实（fact_library 原始条目整体迁入，含其自记的 kwh/cell_brand 副本）
    # 顶层非字典键是文件级元数据（如 updated），不进车型域
    fl_meta = {k: v for k, v in fl.items() if not isinstance(v, dict)}
    for i, (m, v) in enumerate(fl.items()):
        if not isinstance(v, dict):
            continue
        add(m, "energy", v, i)

    # 3) 物理参数
    for i, c in enumerate(vp["cars"]):
        c = dict(c)
        add(c.pop("model"), "physics", c, i)

    # 4) 气囊（逐配置）
    for i, e in enumerate(ab["entries"]):
        e = dict(e)
        add_list(e.pop("model"), "airbags", e, i)

    # 5) 逐配置人工条目
    for i, e in enumerate(tm["entries"]):
        e = dict(e)
        add_list(e.pop("model"), "trims_manual", e, i)

    # 6) C-NCAP 成绩（scores/meta/sources 三结构合入一条）
    for i, (m, pct) in enumerate(cn["scores"].items()):
        rec = {"value": pct}
        meta = cn.get("meta", {}).get(m) or {}
        rec.update(meta)
        src = cn.get("sources", {}).get(m) or {}
        for k in ("overall", "sub", "standard", "note"):
            if k in src:
                rec[k] = src[k]
        if "url" not in rec and "url" in src:
            rec["url"] = src["url"]
        add(m, "crash_cncap", rec, i)
    for i, (m, reason) in enumerate(cn.get("confirmed_no_score", {}).items()):
        add(m, "crash_cncap_no_score", {"reason": reason}, i)

    # 7) 官方安全分映射表人工分（口径部分留 domains_meta）
    for i, (m, v) in enumerate(off["car_scores"].items()):
        add(m, "crash_official", v, i)

    # 8) 体验证据
    for i, e in enumerate(ef["entries"]):
        e = dict(e)
        add_list(e.pop("model"), "experience", e, i)

    # 9) 保值率
    for i, (m, v) in enumerate(rv["values"].items()):
        add(m, "residual", {"value": v}, i)

    domains_meta = {
        "energy": fl_meta,
        "physics": {k: vp[k] for k in ("version", "as_of", "note", "source_policy", "coverage_note")},
        "airbags": {k: ab[k] for k in ("note", "updated", "date_semantics", "fields", "field_labels", "policy")},
        "trims_manual": {k: tm[k] for k in ("scoring_enabled", "out_of_board_examples", "updated", "note")},
        "crash_cncap": {"note": cn.get("note"), "aliases": cn.get("aliases", {}),
                        "provinance": cn.get("provinance")},
        "crash_official": {k: off[k] for k in ("note", "version", "systems",
                                               "structural_evidence_policy", "systems_repository")},
        "experience": {k: ef[k] for k in ("note", "updated", "dimension_scope",
                                          "max_adjust_per_dimension", "source_weights", "coverage_note")},
        "residual": {k: rv[k] for k in ("note", "updated", "source", "source_urls",
                                        "period", "pool_median", "max_adjust", "rule")},
        "cell": {"note": "电芯品牌与电池容量核验表（由 vehicle_facts.json 的 cell 域派生）。"
                          "tier：1=宁德系/弗迪，2=二线或宁德混装或厂商自研，3=新路线（半固态）或未披露。"
                          "本表只补事实，不改候选池快照。",
                 "verified": "2026-11-16"},
    }

    merged = {
        "meta": {
            "version": "11.0.0",
            "as_of": "2026-11-21",
            "note": "车辆硬事实唯一可写源（v11.0.0 单一 JSON consolidation）。"
                    "由九源无损合并：vehicle_facts_seed(CELL_SEED/CELL_LEGACY)/fact_library/"
                    "vehicle_physics/airbags/trims_manual/cncap_scores/official_safety_scores(car_scores)/"
                    "experience_facts/residual_values。唯一写入方=modules/research/vehicle_facts.py 网关"
                    "（schema 校验+原子写+自动备份）；其余同名文件均为只读派生视图，"
                    "由 modules/research/build_fact_views.py 确定性重建。",
            "energy_note": SEED.ENERGY_NOTE,
            "write_policy": "任何模块不得 open(本文件, 'w')；写只能走 vehicle_facts 网关；"
                            "layout_regress 硬拦违规写入。",
            "migrated_from": ["contracts/vehicle_facts_seed.py", "fact_library.json",
                              "vehicle_physics.json", "airbags.json", "trims_manual.json",
                              "cncap_scores.json", "official_safety_scores.json",
                              "experience_facts.json", "residual_values.json"],
        },
        "domains_meta": domains_meta,
        "models": models,
    }

    # ---- 无损验证：内存内重建全部旧视图并深比较 ----
    errs = []

    def check(name, rebuilt, original):
        if rebuilt != original:
            errs.append(name)
            print(f"[ROUNDTRIP FAIL] {name}")
        else:
            print(f"[roundtrip ok] {name}")

    energy_view = {**fl_meta}
    energy_view.update({r["_model"]: strip_meta(r) for r in
                        (m["energy"] for m in models.values() if "energy" in m)})
    check("fact_library.json", energy_view, fl)

    phys_cars = sorted((r for m in models.values() if "physics" in m for r in [m["physics"]]),
                       key=lambda r: r["_idx"])
    phys_view = dict(domains_meta["physics"])
    phys_view["cars"] = [{"model": r["_model"], **strip_meta(r)} for r in phys_cars]
    check("vehicle_physics.json", phys_view, vp)

    ab_view = dict(domains_meta["airbags"])
    ab_entries = [r for m in models.values() for r in m.get("airbags", [])]
    ab_entries.sort(key=lambda r: r["_idx"])
    ab_view["entries"] = [strip_meta(r) for r in ab_entries]
    check("airbags.json", ab_view, ab)

    tm_view = dict(domains_meta["trims_manual"])
    tm_entries = [r for m in models.values() for r in m.get("trims_manual", [])]
    tm_entries.sort(key=lambda r: r["_idx"])
    tm_view["entries"] = [strip_meta(r) for r in tm_entries]
    check("trims_manual.json", tm_view, tm)

    cn_scores, cn_meta, cn_src, cn_nos = {}, {}, {}, {}
    for m in models.values():
        if "crash_cncap" in m:
            r = m["crash_cncap"]
            cn_scores[r["_model"]] = r["value"]
            cn_meta[r["_model"]] = {k: r[k] for k in ("source", "url", "date", "year") if k in r}
            cn_src[r["_model"]] = {k: r[k] for k in ("overall", "sub", "standard", "url", "note") if k in r}
        if "crash_cncap_no_score" in m:
            cn_nos[m["crash_cncap_no_score"]["_model"]] = m["crash_cncap_no_score"]["reason"]
    cn_view = {"note": domains_meta["crash_cncap"]["note"], "scores": cn_scores,
               "meta": cn_meta, "confirmed_no_score": cn_nos,
               "provinance": domains_meta["crash_cncap"]["provinance"],
               "aliases": domains_meta["crash_cncap"]["aliases"], "sources": cn_src}
    check("cncap_scores.json", cn_view, cn)

    off_view = dict(domains_meta["crash_official"])
    off_view["car_scores"] = {m["crash_official"]["_model"]: strip_meta(m["crash_official"])
                              for m in models.values() if "crash_official" in m}
    check("official_safety_scores.json", off_view, off)

    ef_view = dict(domains_meta["experience"])
    ef_entries = [r for m in models.values() for r in m.get("experience", [])]
    ef_entries.sort(key=lambda r: r["_idx"])
    ef_view["entries"] = [strip_meta(r) for r in ef_entries]
    check("experience_facts.json", ef_view, ef)

    rv_view = dict(domains_meta["residual"])
    rv_view["values"] = {m["residual"]["_model"]: m["residual"]["value"]
                         for m in models.values() if "residual" in m}
    check("residual_values.json", rv_view, rv)

    cell_entries = []
    for m in models.values():
        c = m.get("cell")
        if c and c.get("tier") is not None and "board" in c:  # seed 条目（有 tier/board）
            cell_entries.append(c)
    cell_entries.sort(key=lambda r: r["_idx"])
    cf_view = {"note": domains_meta["cell"]["note"], "verified": domains_meta["cell"]["verified"],
               "count": len(cell_entries),
               "entries": [{"model": r["_model"], "board": r["board"], "trim": r["trim"],
                            "price": r["price"], "battery_kwh": r["kwh"],
                            "cell_brand": r["cell_brand"], "cell_tier": r["tier"],
                            "source_url": r["source"], "verified": r["date"],
                            "quote": r["quote"]} for r in cell_entries]}
    check("cell_facts.json", cf_view, cf_on_disk)

    # ---- 报告 ----
    print(f"\n=== 迁移报告 ===")
    print(f"models(canon): {len(models)}")
    for d in ("cell", "energy", "physics", "airbags", "trims_manual",
              "crash_cncap", "crash_cncap_no_score", "crash_official", "experience", "residual"):
        n = sum(1 for m in models.values() if d in m)
        print(f"  {d}: {n}")
    print(f"legacy 被 seed 覆盖跳过: {legacy_skipped}")
    print(f"同域冲突: {len(collisions)} {collisions[:5]}")
    multi = {k: m["names"] for k, m in models.items() if len(m["names"]) > 1}
    print(f"多名车型(canon 归一命中): {len(multi)}")
    for k, v in list(multi.items())[:10]:
        print(f"  {v}")
    if errs:
        print(f"[FAIL] 视图重建不一致: {errs} —— 迁移中止，不写文件")
        return 1
    if collisions:
        print("[FAIL] 存在同域冲突，中止")
        return 1

    if apply:
        out = paths.cat("vehicle_facts.json")
        json.dump(merged, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"\n[apply] 已写入 {out}")
    else:
        print("\n[dry-run] 全部无损验证通过。加 --apply 写入 categories/vehicle_facts.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
