#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""燃油/混动四榜「入池销量漏斗」敏感性对照（ice_funnel.py，v6.3）。

v6.2 的敏感性脚本只量化了**评分**里 market 维的影响，报告里也承认：
「销量在入池（前 220 款）与评分（market 维）两处都出现，本对照只能量化第二处」。
本模块把第一处也量化出来。

两个候选池
----------
  A 基准池 ice_pool.json      ：懂车帝非纯电 388 款 → 取销量前 220 款核验 → 入池 122 款
  D 全量池 ice_pool_full.json ：388 款全部核验（--top 0），不按销量截断 → 入池 187 款

情景
----
  A  = 基准池 + 现行权重（正式榜口径）
  D  = 全量池 + 现行权重（只解耦漏斗）
  E  = 全量池 + 去掉 market 维（漏斗与销量评分同时解耦）
  F  = 全量池 + market 权重 22%（把配置注记里的设计上限当真，其余按比例重归一化）

纪律
----
  · 只读，不改写任何正式产物；默认正式榜是否切换由报告与用户决定。
  · 全量池里月销个位数的车同样入池，market 分位秩按**该池**重算，
    所以 D 与 A 的分差不只是「多了几款车」，也包含归一化基准的变化——
    这正是漏斗偏差的一部分，不剔除。
  · 覆盖率、落榜数、名称未匹配数都在报告里如实列出。

用法
----
    python3 ice_funnel.py --pool-base <ice_pool.json> --pool-full <ice_pool_full.json> \
        --category car_ice_phev --out <报告目录>
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)

from modules.engine import ranking
from modules.engine import leaderboard as L
from modules.engine import ice_scoring as IS

SCEN = {
    "A": "基准池（销量前 220 → 122 款）+ 现行权重【v6.2 旧口径，保留作对照】",
    "D": "全量池（388 款全核验 → 187 款）+ 现行权重【v6.3 正式榜口径，只解耦漏斗】",
    "E": "全量池 + 去掉 market 维（漏斗与销量评分同时解耦）",
    "F": "全量池 + market 权重 22%（配置注记的设计上限）",
}


def tau(order_a, order_b):
    ra = {m: i for i, m in enumerate(order_a)}
    rb = {m: i for i, m in enumerate(order_b)}
    common = [m for m in order_a if m in rb]
    pairs = conc = 0
    for i, x in enumerate(common):
        for y in common[i + 1:]:
            pairs += 1
            d = (ra[x] - ra[y]) * (rb[x] - rb[y])
            conc += 1 if d > 0 else (-1 if d < 0 else 0)
    return (conc / pairs) if pairs else 0.0


def rank_for(pool, cfg, weights, board):
    cands, audit = IS.score_pool(list(pool), cfg)
    sub = L.filter_board(cands, board)
    gates = board.get("gates") or {}
    passed = [c for c in sub if ranking.apply_gates(c, gates)["pass"]]
    rows = L.score_rows(passed, cfg, weights)
    rows.sort(key=lambda r: -r["total"])
    return [r["model"] for r in rows], passed, audit


def weights_for(cfg, market=None, drop=False):
    dims = cfg["dimensions"]
    w = []
    for d in dims:
        k = d["key"]
        if drop and k == "market":
            w.append(0.0)
        elif market is not None and k == "market":
            w.append(market)
        else:
            w.append(float(d["weight"]))
    if market is not None and not drop:
        # 其余维按原比例重归一化，使总和仍为 1
        rest = sum(float(d["weight"]) for d in dims if d["key"] != "market")
        scale = (1.0 - market) / rest if rest else 1.0
        w = [market if d["key"] == "market" else float(d["weight"]) * scale
             for d in dims]
    return w


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool-base", required=True)
    ap.add_argument("--pool-full", required=True)
    ap.add_argument("--category", default="car_ice_phev")
    ap.add_argument("--out", required=True)
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    base = json.load(open(a.pool_base, encoding="utf-8"))
    full = json.load(open(a.pool_full, encoding="utf-8"))
    pb, pf = base["pool"], full["pool"]
    cfg = L.load_category(a.category)
    w_none = None
    w_drop = weights_for(cfg, drop=True)
    w22 = weights_for(cfg, market=0.22)

    out = {"generated_at": dt.datetime.now().isoformat(timespec="seconds"),
           "scenarios": SCEN,
           "pools": {
               "A": {"file": os.path.basename(a.pool_base), "n": len(pb),
                     "stats": base.get("stats")},
               "D": {"file": os.path.basename(a.pool_full), "n": len(pf),
                     "stats": full.get("stats")},
           },
           "boards": {}}
    L_ = ["# 燃油/混动四榜 · 入池销量漏斗敏感性对照", "",
          f"> 生成 {out['generated_at']}｜A 为 v6.2 旧口径归档，"
          "**D 为 v6.3 现行正式榜口径**；本对照不改写任何已出榜产物", "",
          f"- **基准池 A（v6.2 旧口径）**：{len(pb)} 款（销量前 220 款核验后入池）",
          f"- **全量池 D**：{len(pf)} 款（388 款全部核验，不按销量截断）",
          f"- 全量池多出的 **{len(pf) - len(pb)} 款**主要是月销偏低的车系；"
          "它们的存在会改变 market 分位秩的归一化基准，所以 D 与 A 的差异"
          "包含「多了车」与「分位重算」两部分，不剔除。", "",
          "## 情景", ""]
    for k, v in SCEN.items():
        L_.append(f"- **{k}** = {v}")
    L_ += ["", "## 结论先看", ""]

    for board in L.board_items(cfg):
        label = board["label"]
        ra, pa, _ = rank_for(pb, cfg, w_none, board)
        rd, pd, _ = rank_for(pf, cfg, w_none, board)
        re, _, _ = rank_for(pf, cfg, w_drop, board)
        rf, _, _ = rank_for(pf, cfg, w22, board)
        ta, td, te, tf = ra[:a.top], rd[:a.top], re[:a.top], rf[:a.top]
        common = [m for m in ra if m in set(rd)]
        tau_ad = tau([m for m in ra if m in set(rd)],
                     [m for m in rd if m in set(ra)])
        posd = {m: i + 1 for i, m in enumerate(rd)}
        new_in = [m for m in td if m not in set(ta)]
        fell_out = [m for m in ta if m not in set(td)]
        shifts = [{"model": m, "A": i, "D": posd.get(m),
                   "d": (posd.get(m) or 0) - i} for i, m in enumerate(ta, 1)]
        rec = {
            "passed_A": len(pa), "passed_D": len(pd),
            "common": len(common),
            "tau_AD": round(tau_ad, 3),
            "overlap_AD": len(set(ta) & set(td)),
            "overlap_DE": len(set(td) & set(te)),
            "overlap_DF": len(set(td) & set(tf)),
            "max_shift_AD": max((abs(s["d"]) for s in shifts if s["D"]), default=0),
            "new_in_top_D": new_in, "fell_out_in_D": fell_out,
            "top_A": ta, "top_D": td, "top_E": te, "top_F": tf,
            "shifts": shifts,
        }
        out["boards"][label] = rec
        L_ += [f"### {label}", "",
               f"- 过闸 {rec['passed_A']} → **{rec['passed_D']}** 款；"
               f"共同车型 {rec['common']} 款，全榜 Kendall τ(A,D) = **{rec['tau_AD']:.3f}**。",
               f"- 前十交集 **{rec['overlap_AD']}/{a.top}**；"
               f"基准前十在 D 中最大位移 **{rec['max_shift_AD']}** 位。"
               f"新进前十：{'、'.join(new_in) or '无'}；掉出：{'、'.join(fell_out) or '无'}。",
               f"- 全量池再去 market 维，前十交集 **{rec['overlap_DE']}/{a.top}**；"
               f"market 由 24% 改 22%，前十交集 **{rec['overlap_DF']}/{a.top}**。", "",
               "| 基准 # | 车型 | A 名次 | D 名次 | 位移 |", "|---|---|---|---|---|"]
        for s in shifts:
            L_.append(f"| {s['A']} | {s['model']} | {s['A']} | {s['D'] or '—'} | "
                      f"{s['d']:+d} |" if s["D"] else
                      f"| {s['A']} | {s['model']} | {s['A']} | 落榜 | — |")
        L_ += ["", f"- **D 前十**：{'、'.join(td)}", "",
               f"- **E 前十（全量池去销量维）**：{'、'.join(te)}", "",
               f"- **F 前十（全量池 market 22%）**：{'、'.join(tf)}", ""]

    L_ += ["## 四榜汇总", "",
           "| 榜单 | 过闸 A→D | τ(A,D) | 前十交集 A∩D | 新进前十 | D∩E 去销量 | D∩F 22% |",
           "|---|---|---|---|---|---|---|"]
    for label, r in out["boards"].items():
        L_.append(f"| {label} | {r['passed_A']}→{r['passed_D']} | {r['tau_AD']:.3f} | "
                  f"{r['overlap_AD']}/10 | {len(r['new_in_top_D'])} | "
                  f"{r['overlap_DE']}/10 | {r['overlap_DF']}/10 |")
    L_ += ["", "## 怎么读", "",
           "- **A∩D 高**：说明「销量前 220」这个截断对前十几乎没有实质影响，"
           "漏斗偏差主要体现在全榜尾部而非头部。",
           "- **A∩D 低 / τ 低**：说明入池截断本身就在决定名次，"
           "此时正式榜更应切到全量池，并同步复核 market 分位归一化。",
           "- **D∩F**：把 `market` 从实配 24% 调到配置注记里写的设计上限 22%"
           "对前十的影响。若交集 10/10，说明该注记争议不改变结论，"
           "不必为了「对齐一句注释」单独改名次。",
           "- 全量池仍不是「全市场穷举」：它只覆盖懂车帝销量榜里出现的 388 款非纯电车系，"
           "汽车之家全库 5372 车系中无销量记录的车仍不在池内。",
           "- 本报告不引入终端成交价与碰撞等级分数，结论不外推到真实性价比或安全。", ""]

    md = "\n".join(L_)
    open(os.path.join(a.out, "funnel_sensitivity.md"), "w",
         encoding="utf-8").write(md)
    json.dump(out, open(os.path.join(a.out, "funnel_sensitivity.json"), "w",
                        encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[ok] {os.path.join(a.out, 'funnel_sensitivity.md')}")
    for label, r in out["boards"].items():
        print(f"  {label}: 过闸 {r['passed_A']}→{r['passed_D']} | "
              f"τ(A,D)={r['tau_AD']:.3f} 交集={r['overlap_AD']}/10 | "
              f"新进={','.join(r['new_in_top_D']) or '无'} | "
              f"D∩F={r['overlap_DF']}/10")


if __name__ == "__main__":
    main()
