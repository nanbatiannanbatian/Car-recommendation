#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pool_split.py —— 候选池拆分/合成（2026-11-22 新增，v10.14.0）

为什么做这个（单一数据源方案 P3）：
  categories/master_pool.json 原来既是**人工维护层**（谁入池/别名/标签/
  人工基准分/评分配置/生命周期/取证说明）又是**事实聚合层**（facts 里的电芯、
  销量；scores 里的算出来的分；candidate 里的价格区间与成绩），一个文件里
  两种性质的东西混在一起，导致：
    · 人工改一个字段，build 一跑就被覆盖（或反过来，build 写回把人工值冲掉）；
    · 「入池」这个人工决定和「这车现在多少分」这个派生事实没法分开审计。

  拆法（**一个 owner + 一条写路径**，粒度是事实域）：
    categories/pool_membership.json   人工层（唯一可写，随包）
        谁入池 / 别名 / 标签（品牌·级别·能源·车身）/ 人工基准分 / 评分配置 /
        生命周期 / 取证说明 / 评分配置引用
    data/gen/master_pool.json         生成层（只读派生，可重建，不随包）
        facts（电芯·销量）/ scores（算出来的分）/ candidate 聚合 / 指纹 / 审计

  合成规则（唯一口径）：
    · 人工层给「骨架与人工判断」，生成层给「算出来的数」；
    · 同一字段两处都有 → **人工层胜出**（人工核验优先），生成层只补空；
    · 生成层的分 = 人工基准分 + 规则增量 + 电耗效率 + 体验证据（由 build 算），
      它写进生成层，不回写人工层（人工层只存基准分）。

用法：
    python3 -m modules.board.pool_split --split     # 拆：master_pool → membership + 生成物
    python3 -m modules.board.pool_split --compose    # 合成：membership + 事实 → 生成物
    python3 -m modules.board.pool_split --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import paths

POOL = str(paths.cat("master_pool.json"))
MEMBERSHIP = str(paths.CAT_DIR / "pool_membership.json")
GENERATED = str(paths.GEN_DIR / "master_pool.json")

# 人工层保留的字段（人工判断与骨架）
MANUAL_FIELDS = (
    "model", "aliases", "tags", "scores", "scored_trim_ref",
    "lifecycle", "evidence_note", "excluded", "exclude_reason",
    # adjust = 人工调整额（人工判断，如「人工下修 -4 分」）——属人工层。
    # 漏了它，拆分后人工调整会被生成层的 None 覆盖，等于人工调整凭空消失。
    "adjust",
)
# 生成层独占的字段（派生事实）
GENERATED_FIELDS = ("facts", "candidate", "searchable", "completeness",
                     "pool_status", "provenance", "score_fingerprint")


def _read(p: str) -> dict:
    if not p or not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _write(p: str, d: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def split(pool_path: str = POOL, dry_run: bool = False) -> dict:
    """把现有 master_pool 拆成人工层 + 生成层。"""
    pool = _read(pool_path)
    cars = pool.get("cars") or []
    mem_cars, gen_cars = [], []
    for c in cars:
        m = {k: c.get(k) for k in MANUAL_FIELDS if k in c}
        m["key"] = c.get("key")
        # 人工层不存派生字段
        for k in GENERATED_FIELDS:
            m.pop(k, None)
        mem_cars.append(m)
        g = {k: c.get(k) for k in GENERATED_FIELDS if k in c}
        g["model"] = c.get("model")
        g["key"] = c.get("key")
        gen_cars.append(g)
    mem = {
        "note": ("候选池**人工层**（唯一可写，随包）——谁入池/别名/标签/人工基准分/"
                 "评分配置/生命周期/取证说明。派生事实（facts/scores/candidate）"
                 "在 data/gen/master_pool.json，由 build 合成，不在这里。\n\n"
                 "2026-11-22 拆分自原 categories/master_pool.json。原文件里人工判断"
                 "与派生事实混存，导致人工改字段会被 build 覆盖、build 写回又冲掉人工值。"),
        "version": "1.0.0",
        "updated": "2026-11-22",
        "meta": pool.get("meta") or {},
        "cars": mem_cars,
    }
    gen = {
        "note": ("候选池**生成层**（只读派生，可重建，不随包）——由 "
                 "categories/pool_membership.json（人工层）+ 各事实库合成。"
                 "不要手改：手改会在下一次 build 时被覆盖。"),
        "version": "1.0.0",
        "updated": "2026-11-22",
        "meta": pool.get("meta") or {},
        "cars": gen_cars,
    }
    if not dry_run:
        _write(MEMBERSHIP, mem)
        _write(GENERATED, gen)
    return {"membership": len(mem_cars), "generated": len(gen_cars),
            "membership_path": MEMBERSHIP, "generated_path": GENERATED}


def compose(membership_path: str = MEMBERSHIP, generated_path: str = GENERATED,
             out_path: str = POOL, dry_run: bool = False) -> dict:
    """把人工层 + 生成层合成回候选池视图（下游读这个）。

    合成规则：人工层胜出（同名字段人工优先），生成层补空。
    """
    mem = _read(membership_path)
    gen = _read(generated_path)
    mem_by = {c.get("model"): c for c in (mem.get("cars") or [])}
    gen_by = {c.get("model"): c for c in (gen.get("cars") or [])}
    merged = []
    for model, m in mem_by.items():
        g = gen_by.get(model) or {}
        row = dict(g)
        for k, v in m.items():
            if k == "key":
                continue
            # 空值判定要区分「没有值」与「值为空/零」：
            #   aliases=[] 是有意义的空列表；adjust=0.0 是有意义的零调整额
            #   （调整了 0 分 ≠ 没调整）。只有 None/"" 才算「人工层没这个字段」。
            if v is None or v == "":
                continue
            row[k] = v                      # 人工层胜出
        row["model"] = model
        row.setdefault("key", m.get("key"))
        merged.append(row)
    # 生成层里有、人工层没有的（理论上不该有，但保留不丢）
    for model, g in gen_by.items():
        if model not in mem_by:
            merged.append(g)
    out = {"meta": mem.get("meta") or gen.get("meta") or {},
           "cars": merged}
    if not dry_run:
        _write(out_path, out)
    return {"cars": len(merged), "out_path": out_path}


def selftest() -> int:
    tmp_m = os.path.join(paths.ROOT, "runs", "_ps_mem.json")
    tmp_g = os.path.join(paths.ROOT, "runs", "_ps_gen.json")
    tmp_o = os.path.join(paths.ROOT, "runs", "_ps_out.json")
    os.makedirs(os.path.dirname(tmp_m), exist_ok=True)
    mem = {"cars": [
        {"key": "a", "model": "车A", "aliases": ["A"], "tags": {"cls": "中型车"},
         "scores": {"control": 8.0}},
        {"key": "b", "model": "车B", "scores": {"control": 9.0}},
    ]}
    gen = {"cars": [
        {"key": "a", "model": "车A", "facts": {"cell_brand": "宁德时代"},
         "scores": {"control": 7.5}, "candidate": {"price_guide": [10, 12]}},
        {"key": "b", "model": "车B", "facts": {"cell_brand": "弗迪"},
         "scores": {"control": 8.8}},
    ]}
    _write(tmp_m, mem); _write(tmp_g, gen)
    try:
        r = compose(tmp_m, tmp_g, tmp_o, dry_run=False)
        out = _read(tmp_o)
        by = {c["model"]: c for c in out["cars"]}
        assert r["cars"] == 2, f"合成车数不对: {r}"
        # 人工层胜出：scores 取人工值
        assert by["车A"]["scores"]["control"] == 8.0, \
            f"人工基准分被生成层覆盖: {by['车A']['scores']}"
        # 生成层补人工层没有的字段
        assert by["车A"]["facts"]["cell_brand"] == "宁德时代", "生成层字段丢失"
        assert by["车A"]["candidate"]["price_guide"] == [10, 12], "生成层 candidate 丢失"
        assert by["车A"]["tags"]["cls"] == "中型车", "人工 tags 丢失"
        # 生成层多出来的车不丢
        _write(tmp_g, {"cars": gen["cars"] + [
            {"key": "c", "model": "车C", "facts": {"cell_brand": "亿纬"}}]})
        out2 = _read(tmp_o)
        compose(tmp_m, tmp_g, tmp_o, dry_run=False)
        by2 = {c["model"]: c for c in _read(tmp_o)["cars"]}
        assert "车C" in by2, "生成层独有的车被丢弃"
    finally:
        for p in (tmp_m, tmp_g, tmp_o):
            try:
                os.remove(p)
            except OSError:
                pass
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", action="store_true", help="拆：master_pool → 人工层 + 生成层")
    ap.add_argument("--compose", action="store_true", help="合成：人工层 + 生成层 → master_pool")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        try:
            selftest()
            print("pool_split selftest OK")
            return 0
        except AssertionError as e:
            print(f"[FAIL] {e}")
            return 1
    if a.split:
        r = split(dry_run=a.dry_run)
        print(f"[split] 人工层 {r['membership']} 条 → {r['membership_path']}；"
              f"生成层 {r['generated']} 条 → {r['generated_path']}")
        if a.dry_run:
            print("（dry-run，未写盘）")
        return 0
    if a.compose:
        r = compose(dry_run=a.dry_run)
        print(f"[compose] 合成 {r['cars']} 款 → {r['out_path']}")
        if a.dry_run:
            print("（dry-run，未写盘）")
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
