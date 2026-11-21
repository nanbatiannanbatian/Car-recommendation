#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vehicle_facts.py —— 车辆客观事实的统一读取入口（research 层，唯一聚合点）。

解决的根因（2026-11-21 用户：「车辆数据多个地方同步不及时，能否只用一个数据源」）：
  同一台车的电芯事实此前在 5 处各自手写——
    modules/contracts/vehicle_facts_seed.py 的 CELL_SEED（权威）
    data/gen/cell_facts.json（由种子生成的派生视图）
    categories/fact_library.json（人工事实库）
    categories/master_pool.json 的 candidate.cell_brand
    data/gen/trims.json 的 cell_supplier
  谁都可能漏改一处，「极氪7X 补 3 处仍显示空」就是这么来的。

统一方案（不合并物理文件——口径不同、零 JSON 铁律、生成物/人工库分类都反对合并）：
  · 唯一可写真相源 = contracts/vehicle_facts_seed.py（纯代码，随 GitHub/发布包存活）。
  · 本模块是唯一读取入口：任何模块要电芯/电量，都调 cell(model) / facts_for(model)，
    不再直接读各自的 JSON 副本。读取优先级：CELL_SEED > CELL_LEGACY > 派生 JSON 兜底。
  · consistency_report() 对照所有派生副本，报告漂移（同一车不同值）——把「不同步」
    从隐性事故变成可门禁的显性检查。

纪律：
  · 电耗不合并：fact_library 与 vehicle_physics 是两个口径（见 ENERGY_NOTE），
    本模块只做披露和自检，不提供合并后的 consumption——避免制造伪统一。
  · 只读：本模块不写任何 JSON；派生视图由各自生成脚本（build_cell_facts.py 等）重建。
  · 零联网：联网取数仍只在 research 的其他模块；本模块只读本地文件与代码常量。

用法：
    from modules.research import vehicle_facts as VF
    rec = VF.cell("极氪7X")          # -> {brand, kwh, tier, price, source, date, quote, origin}
    VF.consistency_report()          # -> {"ok": bool, "drifts": [...]}
    python3 -m modules.research.vehicle_facts --selftest
"""
from __future__ import annotations

import json
import os
import sys

import paths
from modules.contracts.model_names import canon
from modules.contracts import vehicle_facts_seed as SEED

# 权威映射（代码常量，随包存活）
_SEED_BY_KEY = {canon(r[0]): r for r in SEED.CELL_SEED}
_LEGACY_BY_KEY = {canon(r[0]): r for r in SEED.CELL_LEGACY}

# 只读派生副本（本地文件；GitHub 新装可能没有 → 全部缺失也不影响权威读取）
_CELL_FACTS = str(paths.cat("cell_facts.json"))
_FACT_LIBRARY = str(paths.cat("fact_library.json"))


def cell(model: str) -> dict | None:
    """取一台车的权威电芯事实。优先级：核验种子 > 历史兜底 > 派生 JSON。

    返回 {brand, kwh, tier, source, date, quote, origin}；无记录返回 None。
    调用方不应再直接读 fact_library/cell_facts/master_pool 的 cell_brand。
    """
    k = canon(model)
    r = _SEED_BY_KEY.get(k)
    if r:
        return {"brand": r[5], "kwh": r[4], "tier": r[6], "price": r[3],
                "source": r[7], "date": r[8], "quote": r[9], "origin": "seed"}
    r = _LEGACY_BY_KEY.get(k)
    if r:
        return {"brand": r[2], "kwh": r[1], "tier": None, "price": None,
                "source": r[3], "date": "", "quote": r[4], "origin": "legacy"}
    # 派生 JSON 兜底（新装包没有这些文件也无害）
    for path, is_entries in ((_CELL_FACTS, True), (_FACT_LIBRARY, False)):
        rec = _cell_from_json(path, k, is_entries)
        if rec:
            return rec
    return None


def _cell_from_json(path: str, key: str, entries: bool) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        doc = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if entries:
        for e in doc.get("entries") or []:
            if canon(e.get("model")) == key and e.get("cell_brand"):
                return {"brand": e["cell_brand"], "kwh": e.get("battery_kwh"),
                        "tier": e.get("cell_tier"), "price": e.get("price"),
                        "source": e.get("source_url", ""),
                        "date": e.get("verified", ""), "quote": e.get("quote", ""),
                        "origin": "json:cell_facts"}
    else:
        for m, v in doc.items():
            if canon(m) == key and isinstance(v, dict) and v.get("cell_brand"):
                src = v.get("_source") or {}
                return {"brand": v["cell_brand"], "kwh": v.get("kwh"),
                        "tier": None, "price": v.get("price"),
                        "source": src.get("cell_src", ""),
                        "date": src.get("verified", ""), "quote": src.get("cell_quote", ""),
                        "origin": "json:fact_library"}
    return None


def facts_for(model: str) -> dict:
    """统一事实视图：电芯走权威源；电耗只披露口径不合并。

    返回 {cell, energy_note}。不返回合并后的 consumption——那是两个不同事实。
    """
    return {"cell": cell(model), "energy_note": SEED.ENERGY_NOTE}


def iter_authoritative_cells() -> list[dict]:
    """全量权威电芯记录（原始车型名），供补齐事实库/建视图使用。

    seed 优先于 legacy：同名车只出一条（seed 带权威来源）。
    """
    out: dict[str, dict] = {}
    for r in SEED.CELL_LEGACY:            # (model, kwh, brand, source, quote)
        out[canon(r[0])] = {"model": r[0], "kwh": r[1], "cell_brand": r[2],
                            "trim": "", "price": None, "tier": None,
                            "source": r[3], "origin": "legacy"}
    for r in SEED.CELL_SEED:              # (model, board, trim, price, kwh, brand, tier, source, date, quote)
        out[canon(r[0])] = {"model": r[0], "kwh": r[4], "cell_brand": r[5],
                            "trim": r[2], "price": r[3], "tier": r[6],
                            "source": r[7], "origin": "seed"}
    return list(out.values())


def all_cell_brands() -> dict[str, str]:
    """全量权威电芯映射 {canon(model): brand}，供建库/门禁去重使用。"""
    out = {k: r[2] for k, r in _LEGACY_BY_KEY.items()}  # legacy: (model, kwh, brand, source, quote)
    out.update({k: r[5] for k, r in _SEED_BY_KEY.items()})  # seed: (...,brand at 5)，覆盖 legacy
    return out


def consistency_report() -> dict:
    """对照派生副本，报告与权威源漂移的车型。

    漂移定义：同一 canon 车型在权威源与某派生副本都有值但不同。
    只报告，不自动改——修数据必须回到唯一可写源 CELL_SEED。
    """
    authoritative = all_cell_brands()
    drifts: list[dict] = []
    copies = [("cell_facts", _cell_from_entries), ("fact_library", _cell_from_facts_lib)]
    for name, loader in copies:
        for model, brand in loader().items():
            k = canon(model)
            if k in authoritative and brand and authoritative[k] != brand:
                drifts.append({"copy": name, "model": model,
                               "authoritative": authoritative[k], "copy_value": brand})
    return {"ok": not drifts, "authoritative_n": len(authoritative), "drifts": drifts}


def _cell_from_entries() -> dict[str, str]:
    out: dict[str, str] = {}
    if not os.path.exists(_CELL_FACTS):
        return out
    try:
        doc = json.load(open(_CELL_FACTS, encoding="utf-8"))
    except (OSError, ValueError):
        return out
    for e in doc.get("entries") or []:
        if e.get("model") and e.get("cell_brand"):
            out[e["model"]] = e["cell_brand"]
    return out


def _cell_from_facts_lib() -> dict[str, str]:
    out: dict[str, str] = {}
    if not os.path.exists(_FACT_LIBRARY):
        return out
    try:
        doc = json.load(open(_FACT_LIBRARY, encoding="utf-8"))
    except (OSError, ValueError):
        return out
    for m, v in doc.items():
        if isinstance(v, dict) and v.get("cell_brand"):
            out[m] = v["cell_brand"]
    return out


def selftest() -> int:
    # 权威源覆盖：两车都在 seed（v10.9.0 补录），读取命中 seed 而非 JSON
    for name, expect in (("极氪7X", "宁德时代(时代吉利)"),
                         ("阿维塔07 纯电版", "宁德时代"),
                         ("深蓝L06", "宁德时代")):
        rec = cell(name)
        assert rec and rec["brand"] == expect, (name, rec)
        assert rec["origin"] == "seed", (name, rec["origin"])
    # price 字段（v10.10.1：ev_cell_board 度数比需要评分配置指导价）
    assert cell("极氪7X")["price"] == 24.98, cell("极氪7X")
    assert cell("MG4")["price"] is None, "legacy 无独立核验价格"
    # 车名归一化：带空格/纯电后缀命中同一权威
    assert cell("阿维塔07")["brand"] == cell("阿维塔07 纯电版")["brand"]
    # legacy 兜底：seed 外的车仍可查到
    assert cell("MG4")["brand"] == "宁德时代" and cell("MG4")["origin"] == "legacy"
    # 不存在的车返回 None，不编造
    assert cell("不存在的测试车型XYZ") is None
    # 统一视图不提供合并 consumption（口径不同，禁止伪统一）
    v = facts_for("极氪7X")
    assert "consumption" not in v and "CLTC" in v["energy_note"], v
    # 全量映射 seed 覆盖 legacy
    m = all_cell_brands()
    assert len(m) >= 90 and m[canon("极氪7X")] == "宁德时代(时代吉利)"
    # 一致性报告结构（当前派生副本应与权威一致；若不致也必须能被报告出来）
    rep = consistency_report()
    assert isinstance(rep["ok"], bool) and isinstance(rep["drifts"], list), rep
    print("SELFTEST PASS · 车辆事实单一权威入口：seed>legacy>JSON 优先级 / 车名归一 / 电耗口径不合并 / 漂移自检")
    return 0


def main() -> int:
    ap = sys.argv[1:]
    if "--selftest" in ap:
        return selftest()
    if "--report" in ap:
        rep = consistency_report()
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 0 if rep["ok"] else 1
    print("只读聚合模块：--selftest 自测 / --report 漂移报告；无写操作。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
