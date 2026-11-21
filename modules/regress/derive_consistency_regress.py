#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""derive_consistency_regress.py —— 派生一致性回归（2026-11-22 新增，P4）

为什么需要这一条：
  前面几轮把「同一事实多个可写副本」逐个治掉了（人工条目 vs 自动条目、
  三套碰撞成绩回流、master_pool 人工层 vs 生成层）。但**治完要有验证手段**，
  否则下次谁在某个环节加回一个副本，又是「改了不知道有没有生效」。

  本回归做的是**重跑合成/回流 → 逐字段比对产物 vs 源**：
    ① 候选池生成物 vs 人工层（pool_membership）：人工字段必须原样保留
    ② 候选池 crash/safety_basis vs 安全成绩 SSOT：必须与唯一事实源一致
    ③ note 自由文本成绩不得进 crash 列（用户 2026-11-22 拍板降级）
    ④ 价格以 trims.json 官方指导价为准（用户拍板）
    ⑤ 逐配置合流视图 vs 人工库 + 自动库：不得丢车型、不得丢人工证据链

  **0 差异才算过。** 有差异 → 报出具体车型与字段。

用法：
    python3 -m modules.regress.derive_consistency_regress
"""
from __future__ import annotations

import copy
import json
import os
import sys

import paths

HERE = str(paths.ROOT)


def _read(p: str) -> dict:
    if not p or not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def check_pool_layers() -> list[str]:
    """① 候选池生成物 vs 人工层。"""
    bad: list[str] = []
    mem = _read(str(paths.cat("pool_membership.json")))
    gen = _read(str(paths.gen("master_pool.json")))
    mem_by = {c.get("model"): c for c in (mem.get("cars") or [])}
    gen_by = {c.get("model"): c for c in (gen.get("cars") or [])}
    if not mem_by:
        return ["人工层 pool_membership.json 为空或缺失"]
    if not gen_by:
        return ["生成层 master_pool.json 为空或缺失"]
    for m, mc in mem_by.items():
        gc = gen_by.get(m)
        if not gc:
            bad.append(f"{m}: 生成层缺该车型")
            continue
        for k, v in mc.items():
            if k == "key":
                continue
            if v is None or v == "":
                continue
            if gc.get(k) != v:
                bad.append(f"{m}.{k}: 生成层 {gc.get(k)!r} != 人工层 {v!r}")
    # 反向：生成层不应有「人工层没有的车型」（那是不受管的游离车）
    for m in gen_by:
        if m not in mem_by:
            bad.append(f"{m}: 生成层有、人工层无（游离车，无人负责）")
    return bad


def check_safety_scores() -> list[str]:
    """②③ 碰撞成绩 vs SSOT + note 文本降级。"""
    bad: list[str] = []
    from modules.engine import sync_safety_scores as SSS
    from modules.engine import crash_systems as CS
    ssot = SSS.load_ssot()
    idx = SSS._index(ssot)
    pool_path = os.path.join(HERE, "runs", "midsuv_bev_pool.json")
    pool = _read(pool_path)
    if not pool:
        return ["候选池快照缺失，无法校验碰撞成绩"]
    cands = copy.deepcopy(pool.get("candidates") or [])
    filled, _unres = SSS.sync(cands)
    for c in cands:
        m = c.get("model")
        rec = SSS._lookup(idx, CS.canon(m) if hasattr(CS, "canon") else m)
        if not rec:
            continue
        p = rec["primary"]
        if c.get("crash") != p.get("display"):
            bad.append(f"{m}.crash: {c.get('crash')!r} != SSOT {p.get('display')!r}")
        if c.get("safety_basis") != p.get("basis"):
            bad.append(f"{m}.safety_basis: {c.get('safety_basis')!r} != SSOT {p.get('basis')!r}")
    # ③ note 里的自由文本成绩不进 crash 列：
    #    判定标准 —— crash 的值必须来自 SSOT，而不是从 note 文本解析出来的
    ssot_displays = {r.get("display") for r in
                     (ssot.get("entries") or []) for r in (r.get("scores") or [])}
    for c in cands:
        cr = str(c.get("crash") or "")
        if not cr or cr in ssot_displays:
            continue
        # 不在 SSOT 里 → 只能来自文本抽取，属于违规残留
        if "C-NCAP" in cr or "C-IASI" in cr or "Euro" in cr or "NCAP" in cr:
            bad.append(f"{m_note(c)}: crash={cr!r} 不在 SSOT 里（疑似 note 文本抽取残留）")
    return bad


def m_note(c: dict) -> str:
    return str(c.get("model") or "?")


def check_price_official() -> list[str]:
    """④ 价格以官方价为准。"""
    bad: list[str] = []
    from modules.engine import facts_sync as FS
    pool = _read(os.path.join(HERE, "runs", "midsuv_bev_pool.json"))
    if not pool:
        return ["候选池快照缺失，无法校验价格"]
    cands = copy.deepcopy(pool.get("candidates") or [])
    before = {c.get("model"): list(c.get("price_guide") or []) for c in cands}
    FS.backfill_price_guide_from_trims(cands)
    from modules.data.trims_store import load as _load
    trims = _load()
    for c in cands:
        m = c.get("model")
        e = trims.get(m)
        if not e:
            continue
        prices = [x.get("price") for x in (e.get("trims") or [])
                  if isinstance(x.get("price"), (int, float))]
        if not prices:
            continue
        want = [min(prices), max(prices)]
        got = list(c.get("price_guide") or [])
        if got != want:
            bad.append(f"{m}.price_guide: {got} != 官方价 {want}")
    return bad


def check_trims_merge() -> list[str]:
    """⑤ 逐配置合流视图：车型齐全 + 人工证据链完好。"""
    bad: list[str] = []
    from modules.data.trims_store import load as _load
    manual = _read(str(paths.cat("trims_manual.json")))
    auto = _read(str(paths.cat("trims.json")))
    db = _load()
    man_models = {e.get("model") for e in (manual.get("entries") or [])}
    auto_models = {e.get("model") for e in (auto.get("entries") or [])}
    for m in man_models | auto_models:
        if m not in db:
            bad.append(f"{m}: 合流视图缺该车型")
    overlap = man_models & auto_models
    if overlap:
        bad.append(f"存储层双副本：{len(overlap)} 款同时在人工库与自动库: {sorted(overlap)[:5]}")
    for e in (manual.get("entries") or []):
        m = e.get("model")
        got = db.get(m)
        if not got:
            continue
        for k in ("source", "quote", "note", "launch_date"):
            if got.get(k) != e.get(k):
                bad.append(f"{m}.{k}: 合流后 {got.get(k)!r} != 人工原值 {e.get(k)!r}")
    return bad


def main() -> int:
    checks = [
        ("候选池生成物 vs 人工层", check_pool_layers),
        ("碰撞成绩 vs 安全成绩 SSOT + note 降级", check_safety_scores),
        ("价格以官方指导价为准", check_price_official),
        ("逐配置合流视图 vs 人工库+自动库", check_trims_merge),
    ]
    total_bad: list[str] = []
    passed = 0
    for name, fn in checks:
        try:
            bad = fn()
        except Exception as e:
            bad = [f"{type(e).__name__}: {e}"]
        if bad:
            print(f"[FAIL] {name}")
            for b in bad[:10]:
                print(f"       {b}")
            if len(bad) > 10:
                print(f"       …另有 {len(bad) - 10} 处")
        else:
            print(f"[PASS] {name}")
            passed += 1
        total_bad += bad
    print(f"\n结果: {passed}/{len(checks)} 通过")
    if total_bad:
        print(f"派生一致性差异 {len(total_bad)} 处 —— 派生物与源不一致，必须先修再出榜")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
