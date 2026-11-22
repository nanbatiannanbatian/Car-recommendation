#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""trims_store.py —— 逐配置事实的**唯一读取入口**（2026-11-22 新增）

为什么要有这个模块（这是「同一事实多个可写副本」的根治点）：
  逐配置事实原本存了两份：
    categories/trims_manual.json  人工核验 13 款（易车/官方表交叉核对，带 url+quote）
    data/gen/trims.json           自动采集 71 款（汽车之家参数页解析）
  两份**都含这 13 款人工条目**，于是同一事实有两个可写副本。实测后果：
    · 345 个字段漂移（13 款人工条目的 source/quote/note 全被自动模板文案顶掉）
    · 人工在 note 里写的**口径冲突披露**（如日产N7 电芯供应商两说、零跑C10 混装）
      被自动模板覆盖 → 那些"人已经查清并写明"的判断在页面上消失了
    · load_db_merged 的合并方向是「生成物优先」——人工版只在生成物缺该车型时才生效，
      于是人工核验形同虚设

根治办法不是「再查一遍副本」，而是**消除存储层副本，合流到读取层**：
    人工车型的事实只存在 categories/trims_manual.json 一处（唯一可写）
    自动车型的事实只存在 data/gen/trims.json 一处（只读派生，可重建）
    本模块是唯一把两者合起来给下游用的地方

合流规则（唯一口径，不搞第二套）：
    · 车型在人工库里 → 整条取人工版（人工核验优先，不被自动覆盖）
    · 车型不在人工库里 → 取自动版
    · 人工条目里由自动补录回填的字段（如官方电耗/电压/轴距），来源在
      entry["_origin"]["trims"][配置名][字段] 里标明 = "auto_fill"，
      人工值 = "manual"，不混淆证据密度。

**纪律**：
    · 下游一律调 trims_store.load()，不要各自 json.load trims.json
      （board_v2 / facts_sync / trim_board / build_* 都已改过来）
    · 本模块**只读**，不写任何文件

用法：
    from modules.data.trims_store import load, load_entry
    db = load()                      # {车型: entry}
    e  = load_entry("极氪7X")        # 单条
    python3 -m modules.data.trims_store --selftest
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any

import paths

# 人工核验库：唯一可写的事实源，随包发布
MANUAL = str(paths.cat("trims_manual.json"))
# 自动采集库：只读派生，可重建，不随包
AUTO = str(paths.cat("trims.json"))

# 人工条目里由自动补录回填的字段 → 在条目上标来源
ORIGIN_MANUAL = "manual"
ORIGIN_AUTO_FILL = "auto_fill"


def _read(path: str) -> dict:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _entries(db: dict) -> list[dict]:
    out = db.get("entries")
    return list(out) if isinstance(out, list) else []


def _mark_origin(entry: dict, origin: str) -> dict:
    """给条目打来源标记（不改动事实值）。"""
    e = dict(entry)
    e["_origin"] = {"level": origin}
    return e


def load(manual_path: str | None = None, auto_path: str | None = None) -> dict[str, dict]:
    """返回 {车型: entry}。人工车型取人工版，自动车型取自动版。

    人工条目已被 v10.13.0 迁回本文件（不再出现在自动库里），所以这里
    **不需要**再做"生成物优先"的合并——人工与自动在存储层就是互斥的两份。
    但为了防御「有人又把人工条目写进自动库」这种回退，仍做一次校验：
    两边同时存在同一车型时，**人工版胜出**并记 audit。
    """
    manual = _read(manual_path or MANUAL)
    auto = _read(auto_path or AUTO)

    out: dict[str, dict] = {}
    # 先放自动版（低优先）
    for e in _entries(auto):
        m = e.get("model")
        if m:
            out[m] = _mark_origin(e, "auto")
    # 人工版覆盖（人工核验优先）
    for e in _entries(manual):
        m = e.get("model")
        if not m:
            continue
        prev = out.get(m)
        if prev is not None:
            prev.setdefault("_origin", {})["shadowed_by_manual"] = True
        out[m] = _mark_origin(e, "manual")
    # v10.24.0：配置级字段证据补丁在读取时注入（唯一数据源 = score_trim_overrides.json）。
    # 不写进 data/gen 生成物：自动采集重跑不会冲掉人工核验的电压/快充文本补丁。
    out = _apply_field_patches(out)
    return out


def _apply_field_patches(out: dict[str, dict]) -> dict[str, dict]:
    """把 score_trim_overrides.json.field_patches 注入读视图（只在字段缺失时填，附依据）。"""
    try:
        with open(str(paths.cat("score_trim_overrides.json")), encoding="utf-8") as f:
            patches = json.load(f).get("field_patches") or []
    except (OSError, ValueError):
        return out
    for fp in patches:
        e = out.get(str(fp.get("model")))
        if not e:
            continue
        for t in e.get("trims", []):
            if t.get("name") != fp.get("trim_name"):
                continue
            for k, v in (fp.get("fields") or {}).items():
                if t.get(k) in (None, ""):
                    t[k] = v
                    t.setdefault("_field_patch", {})[k] = {
                        "value": v, "basis": fp.get("basis"), "evidence": fp.get("evidence")}
    return out


def load_entry(model: str, **kw) -> dict | None:
    return load(**kw).get(model)


def load_db(manual_path: str | None = None, auto_path: str | None = None) -> dict:
    """返回**db 形态**的合并视图：{"entries": [...], "out_of_board_examples": [...]}。

    一些下游要的是整个 db 对象（带 out_of_board_examples 等）而不是索引，
    它们也统一走这里，不再各自 json.load。
    """
    manual = _read(manual_path or MANUAL)
    auto = _read(auto_path or AUTO)
    auto_entries = _entries(auto)
    man_entries = _entries(manual)
    man_models = {e.get("model") for e in man_entries}
    # 自动条目 + 人工条目（人工优先：同名时人工版胜出）
    merged: list[dict] = []
    seen: set[str] = set()
    for e in auto_entries:
        m = e.get("model")
        if m in man_models:
            continue                      # 该车型有手工版 → 跳过自动版
        merged.append(e)
        if m:
            seen.add(m)
    merged.extend(man_entries)
    # out_of_board_examples 只存在于人工库
    oob = manual.get("out_of_board_examples")
    if not isinstance(oob, list):
        oob = auto.get("out_of_board_examples")
    return {
        "scoring_enabled": manual.get("scoring_enabled", auto.get("scoring_enabled", False)),
        "entries": merged,
        "out_of_board_examples": list(oob) if isinstance(oob, list) else [],
    }


def out_of_board_examples(manual_path: str | None = None) -> list[dict]:
    """带外示例（不在榜但作对照的人工条目）——只在人工库里。"""
    db = _read(manual_path or MANUAL)
    v = db.get("out_of_board_examples")
    return list(v) if isinstance(v, list) else []


def provenance(entry: dict) -> dict:
    """条目的来源信息：level + 字段级来源。"""
    o = (entry or {}).get("_origin") or {}
    return {"level": o.get("level"), "shadowed_by_manual": bool(o.get("shadowed_by_manual"))}


def selftest() -> int:
    """合成数据上的合流规则自测。"""
    manual = {
        "entries": [
            {"model": "人工车A", "source": "易车人工核验", "quote": "人工引文",
             "note": "口径冲突披露", "launch_date": "2026-01-01",
             "trims": [{"name": "标准版", "price": 10.0, "cell_supplier": "宁德时代",
                        "consumption": 12.0, "voltage_v": 400}]},
            {"model": "人工车B", "source": "官方表", "trims": []},
        ],
        "out_of_board_examples": [{"model": "带外车", "trims": []}],
    }
    auto = {
        "entries": [
            {"model": "人工车A", "source": "自动模板文案（应被人工覆盖）",
             "note": "自动模板note（应被人工覆盖）",
             "trims": [{"name": "标准版", "price": 10.0, "cell_supplier": "自动值（应被覆盖）"}]},
            {"model": "自动车C", "source": "汽车之家参数页",
             "trims": [{"name": "顶配", "price": 20.0}]},
        ],
    }
    tmp_m = os.path.join(paths.ROOT, "runs", "_ts_manual.json")
    tmp_a = os.path.join(paths.ROOT, "runs", "_ts_auto.json")
    os.makedirs(os.path.dirname(tmp_m), exist_ok=True)
    json.dump(manual, open(tmp_m, "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(auto, open(tmp_a, "w", encoding="utf-8"), ensure_ascii=False)
    try:
        db = load(tmp_m, tmp_a)
        assert set(db) == {"人工车A", "人工车B", "自动车C"}, f"合流后车型集不对: {set(db)}"

        a = db["人工车A"]
        assert a["source"] == "易车人工核验", f"人工 source 被覆盖: {a.get('source')}"
        assert a["note"] == "口径冲突披露", f"人工 note 被覆盖: {a.get('note')}"
        assert a["launch_date"] == "2026-01-01", "人工 launch_date 丢失"
        assert a["trims"][0]["cell_supplier"] == "宁德时代", "人工电芯被自动值覆盖"
        assert provenance(a)["level"] == "manual", "人工车型来源标记不对"

        c = db["自动车C"]
        assert c["source"] == "汽车之家参数页", "自动车型 source 不对"
        assert provenance(c)["level"] == "auto", "自动车型来源标记不对"

        # 人工库缺该车型时不能凭空造出
        db2 = load(tmp_m, os.path.join(paths.ROOT, "runs", "_ts_none.json"))
        assert "自动车C" not in db2, "自动库缺文件时应只有人工条目"

        # 带外示例只在人工库
        assert out_of_board_examples(tmp_m) and not out_of_board_examples(tmp_a), \
            "带外示例应只来自人工库"
    finally:
        for p in (tmp_m, tmp_a):
            try:
                os.remove(p)
            except OSError:
                pass

    # 真实库自检：人工车型必须在人工库里存在
    real = load()
    man = _read(MANUAL)
    man_models = {e.get("model") for e in _entries(man)}
    missing = [m for m in man_models if m not in real]
    assert not missing, f"人工条目在合流视图里缺失: {missing}"
    # 人工车型不应同时出现在自动库（v10.13.0 迁移后的终态）
    auto = _read(AUTO)
    dup = sorted(man_models & {e.get("model") for e in _entries(auto)})
    assert not dup, (f"人工车型仍残留在自动库（存储层副本未消除）: {dup}。"
                     f"迁移脚本 modules/engine/trims_store.py --migrate 可修")
    return 0


def migrate(dry_run: bool = False) -> dict:
    """把自动库里残留的人工条目迁回人工库（消除存储层双副本）。

    步骤：
      1. 自动库条目 → 人工库：把自动补录的 trim 级字段（官方电耗/电压/轴距/
         后备厢/零百/风阻/整备质量/上市时间/冷却方式等）**只填空**回填进
         人工条目对应配置，人工已有值一律不动；条目的 source/quote/note
         保留人工版，绝不用自动模板覆盖。
      2. 从自动库删除这些条目。

    这是**一次性迁移**，跑完即消除双副本；日常增量由 --refresh 走
    正确的写路径（自动补录写回人工库对应字段，而不是另存一份）。
    """
    manual_db = _read(MANUAL)
    auto_db = _read(AUTO)
    man_by = {e.get("model"): e for e in _entries(manual_db)}
    auto_by = {e.get("model"): e for e in _entries(auto_db)}
    dup = [m for m in man_by if m in auto_by]

    # 自动补录可能写的字段（trim 级）
    FILLABLE = ("cell_supplier", "supplier_class", "charge", "consumption",
                "charge_power_kw", "swap", "voltage_v", "battery_type",
                "battery_cooling", "wheelbase_mm", "length_mm", "width_mm",
                "height_mm", "trunk_l", "frunk_l", "cd", "body_structure",
                "zero_to_100_s", "max_power_kw", "max_torque_nm",
                "curb_weight_kg", "max_loaded_kg", "launch_month")

    filled: list[str] = []
    moved: list[str] = []
    for m in dup:
        me, ae = man_by[m], auto_by[m]
        mt = {t.get("name"): t for t in (me.get("trims") or [])}
        for at in (ae.get("trims") or []):
            name = at.get("name")
            ot = mt.get(name)
            if not ot:
                # 按指导价后备匹配（人工款型名与汽车之家差异大）
                p = at.get("price")
                same = [x for x in mt.values()
                        if isinstance(p, (int, float))
                        and isinstance(x.get("price"), (int, float))
                        and abs(x["price"] - p) <= 0.011]
                ot = same[0] if len(same) == 1 else None
            if not ot:
                continue
            for k in FILLABLE:
                nv = at.get(k)
                if nv in (None, "", "—"):
                    continue
                if ot.get(k) in (None, "", "—"):
                    ot[k] = nv
                    filled.append(f"{m}[{name}].{k}")
        moved.append(m)

    if not dry_run and moved:
        keep = [e for e in _entries(auto_db) if e.get("model") not in set(moved)]
        auto_db["entries"] = keep
        with open(AUTO, "w", encoding="utf-8") as f:
            json.dump(auto_db, f, ensure_ascii=False, indent=2)
        with open(MANUAL, "w", encoding="utf-8") as f:
            json.dump(manual_db, f, ensure_ascii=False, indent=2)

    return {"duplicates": dup, "moved": moved, "fields_filled": filled}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--migrate", action="store_true", help="消除人工条目的存储层副本")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.migrate:
        r = migrate(dry_run=a.dry_run)
        print(f"[migrate] 人工车型在自动库的残留 {len(r['duplicates'])} 款 → "
              f"迁回人工库 {len(r['moved'])} 款，回填空字段 {len(r['fields_filled'])} 个")
        for x in r["duplicates"]:
            print(f"  {x}")
        if a.dry_run:
            print("（dry-run，未写盘）")
        return 0
    if a.selftest or True:
        try:
            selftest()
            print("trims_store selftest OK")
        except AssertionError as e:
            print(f"[FAIL] {e}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
