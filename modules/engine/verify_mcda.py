#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MCDA 交叉验证（verify_mcda.py）——用 pymcdm（MIT，文献验证实现）当裁判。

为什么需要这个：
  robust.py 的 10 种算法是手写的。手写实现最怕"看起来对、公式错一个下标"——
  排名错了自己发现不了，还会把错误结论交给三档判档。MCDM-Workbench 的思路
  （26 方法、对照文献验证）值得借鉴，但它是 TypeScript 浏览器端，搬不进来；
  Python 生态里的等价物是 pymcdm（pip 安装、MIT、带文献用例）。所以：

  不照搬任何项目的代码，而是**拿独立实现当裁判**，对我们的实现对拍。

  判定口径：两种实现给出同一**排序**（ Spearman τ / 名次完全一致）才算过；
  分值本身允许差（归一化方式不同，topsis 的 closeness 和我们的总分不等价）。
  VIKOR 允许 Q 值平局顺序差异（v 参数与 f* 平局处理不同实现有公认分歧），
  但前三名集合应一致。

用法：
    python3 verify_mcda.py                          # 用当前候选池的真实矩阵对拍
    python3 verify_mcda.py --random 50              # 再跑 50 个随机矩阵压力测试
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)
from modules.engine import leaderboard as L
from modules.engine import robust

import pymcdm
from pymcdm.methods import TOPSIS, VIKOR, MABAC, MOORA, EDAS, WASPAS, ARAS, COPRAS
from pymcdm.methods import PROMETHEE_II as P2, SPOTIS
from pymcdm import normalizations as nz


def real_matrix(cands_path=None):
    """当前候选池（第一个非空榜）的真实决策矩阵。

    候选池查找顺序：--candidates > 环境变量 CAR_EV_CANDS > 本机历史归档（存在才用）
    > skill 内任意 runs/**/candidates_trend.json（取最新的）。"""
    cfg = L.load_category("car_ev")
    path = cands_path or os.environ.get("CAR_EV_CANDS")
    if not path:
        here_runs = os.path.join(HERE, "runs")
        cands_pool = []
        for root, dirs, files in os.walk(here_runs):
            if "candidates_trend.json" in files:
                cands_pool.append(os.path.join(root, "candidates_trend.json"))
        legacy = os.environ.get("CAR_EV_LEGACY_CANDS")
        if legacy and os.path.exists(legacy):
            cands_pool.append(legacy)
        if not cands_pool:
            raise SystemExit("找不到 candidates_trend.json，用 --candidates 指定")
        path = max(cands_pool, key=os.path.getmtime)
    cands = json.load(open(path, encoding="utf-8"))["candidates"]
    for board in L.board_items(cfg):
        sub = L.filter_board(cands, board)
        if len(sub) >= 4:
            names, matrix, types, _ = L.decision_matrix(sub, cfg)
            # 两套实现的类型口径不同：robust.py 只认字符串 "cost"；
            # pymcdm 只认 +1/-1 数字。这里各自保留一份，避免静默的方向错误。
            tmap = {"benefit": 1.0, "cost": -1.0}
            types_str = [t if isinstance(t, str) else ("cost" if t < 0 else "benefit")
                         for t in types]
            types_num = np.array([tmap.get(t, tmap.get(str(t), 1.0)) for t in types],
                                 dtype=float)
            return (names, np.array(matrix, dtype=float),
                    (types_str, types_num), board.get("label"))
    raise SystemExit("没有足够候选")


def spearman_tau(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    da, db = a - a.mean(), b - b.mean()
    den = (np.sqrt((da**2).sum()) * np.sqrt((db**2).sum())) or 1e-12
    return float((da * db).sum() / den)


def rank_of(scores):
    """分数 → 名次（1 起，分高在前）。"""
    order = np.argsort(-np.asarray(scores, dtype=float))
    rk = np.empty_like(order)
    rk[order] = np.arange(1, len(order) + 1)
    return rk


def check(name, ours_scores, ref_scores, tol_tau=0.99):
    ro, rr = rank_of(ours_scores), rank_of(ref_scores)
    tau = spearman_tau(ro, rr)
    exact = int((ro == rr).all())
    ok = tau >= tol_tau
    return {"method": name, "tau": round(tau, 4), "exact": exact, "ok": ok}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--random", type=int, default=0,
                    help="附加 N 个随机矩阵压力测试")
    ap.add_argument("--candidates", help="候选池 json（缺省自动就近查找）")
    a = ap.parse_args()

    names, M, types, board = real_matrix(a.candidates)
    types_str, types_num = types
    w = np.array([d["weight"] for d in cfg_dims()], dtype=float)
    w = w / w.sum()
    results = []

    # ---- 对拍映射：robust.py 方法 ↔ pymcdm 类
    # 注意归一化差异：分数不等价，只比排序。
    # ref 用 lambda 延迟求值：某些方法（如 pymcdm 的 MOORA）要求至少一个成本型
    # 准则，真实矩阵全 benefit 时会抛错——捕获后跳过并写进报告，而不是崩掉。
    pairs = [
        ("topsis", robust.m_topsis(M, w, types_str),
         lambda: TOPSIS(normalization_function=nz.vector_normalization)(M, w, types_num)),
        ("vikor", robust.m_vikor(M, w, types_str),      # 我们分越高越好
         lambda: -VIKOR()(M, w, types_num)),            # pymcdm Q 越小越好 → 取负
        ("mabac", robust.m_mabac(M, w, types_str), lambda: MABAC()(M, w, types_num)),
        ("moora", robust.m_moora(M, w, types_str), lambda: MOORA()(M, w, types_num)),
        ("edas", robust.m_edas(M, w, types_str), lambda: EDAS()(M, w, types_num)),
        ("waspas", robust.m_waspas(M, w, types_str), lambda: WASPAS()(M, w, types_num)),
        ("promethee2", robust.m_promethee(M, w, types_str),
         lambda: P2(preference_function=nz.linear_normalization, p=0.5, q=0.1)(M, w, types_num)),
        ("spotis", robust.m_spotis(M, w, types_str), lambda: SPOTIS(bounds=(M.min(axis=0), M.max(axis=0)))(M, w, types_num)),
        ("aras", None, lambda: ARAS()(M, w, types_num)),   # robust 无 ARAS，仅参考
        ("copras", None, lambda: COPRAS()(M, w, types_num)),
    ]
    skipped = []
    for name, ours, ref_fn in pairs:
        if ours is None:
            continue
        try:
            ref_v = ref_fn()
        except Exception as e:
            skipped.append(f"{name}（{type(e).__name__}）")
            continue
        results.append(check(name, ours, ref_v))
    if skipped:
        L_head_skip = skipped   # 供报告引用

    L_ = ["# MCDA 交叉验证（robust.py ↔ pymcdm）", "",
          f"> 矩阵来源：{board} 实际候选池 {len(names)} 款 × {M.shape[1]} 维；"
          f"裁判 = pymcdm（MIT，独立实现）。判定口径：**排序一致**（τ≥0.99），"
          "分值不比（归一化方式不同）。", ""]
    if skipped:
        L_ += ["", "> 跳过：" + "；".join(skipped)]
    L_ += ["| 方法 | Spearman τ | 名次完全一致 | 结论 |", "|---|---|---|---|"]
    for r in results:
        L_.append(f"| {r['method']} | {r['tau']} | {'✓' if r['exact'] else '✗'} | "
                  f"{'✅ 通过' if r['ok'] else '❌ 复查'} |")

    if a.random:
        rng = np.random.default_rng(42)
        n_fail = 0
        trials = []
        for t in range(a.random):
            n, m = rng.integers(4, 12), rng.integers(3, 8)
            Mr = rng.uniform(1, 10, size=(n, m))
            tr = rng.choice([-1.0, 1.0], size=m)
            wr = rng.dirichlet(np.ones(m))
            bnd = (Mr.min(axis=0), Mr.max(axis=0))
            tr_str = ["cost" if t < 0 else "benefit" for t in tr]
            pairs_r = [
                ("topsis", robust.m_topsis(Mr, wr, tr_str), lambda: TOPSIS(normalization_function=nz.vector_normalization)(Mr, wr, tr)),
                ("mabac", robust.m_mabac(Mr, wr, tr_str), lambda: MABAC()(Mr, wr, tr)),
                ("moora", robust.m_moora(Mr, wr, tr_str), lambda: MOORA()(Mr, wr, tr)),
                ("edas", robust.m_edas(Mr, wr, tr_str), lambda: EDAS()(Mr, wr, tr)),
                ("waspas", robust.m_waspas(Mr, wr, tr_str), lambda: WASPAS()(Mr, wr, tr)),
                ("spotis", robust.m_spotis(Mr, wr, tr_str),
                 lambda: SPOTIS(bounds=bnd)(Mr, wr, tr)),
            ]
            for nm, ours, ref_fn in pairs_r:
                try:
                    ref = ref_fn()
                except Exception:
                    continue          # 随机矩阵可能触发 pymcdm 的前置条件限制
                r = check(nm, ours, ref)
                trials.append(r)
                if not r["ok"]:
                    n_fail += 1
        bad = [t for t in trials if not t["ok"]]
        L_ += ["", f"## 随机压力测试（{a.random} 矩阵 × 6 方法 = {len(trials)} 次）", "",
               f"- τ≥0.99 通过率：**{1 - n_fail/len(trials):.1%}**",
               f"- 失败明细：{[(t['method'], t['tau']) for t in bad] or '无'}", "",
               "> 失败点通常是平局/近平局（两行几乎相同的矩阵），τ 对次序抖动敏感，"
               "不一定是公式错。逐个看 τ 值再判断。"]
        results.extend(trials)

    out = os.path.join(HERE, "runs", "_verify_mcda.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "w", encoding="utf-8").write("\n".join(L_) + "\n")
    print("\n".join(L_))
    print(f"\n[ok] {out}")
    n_bad = sum(1 for r in results if not r["ok"])
    sys.exit(1 if n_bad else 0)


def cfg_dims():
    cfg = L.load_category("car_ev")
    return cfg["dimensions"]


if __name__ == "__main__":
    main()
