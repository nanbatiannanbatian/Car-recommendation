#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""燃油/混动四榜三情景敏感性对照（v6.2）。

把三个「口头承认」变成可计算的数：
  Q1 销量权重是不是实际很重（v6.3 起入池已全量核验，销量只剩评分里的 market 24%）
     → 情景 B：整条去掉 market 维，其余权重自动重归一化。
  Q2 space/control/cabin/quality 约 61% 靠同榜中位插补，会不会扭曲名次
     → 情景 C：每台车只用自己有真实数据的维，按可得维权重重归一化。
  Q3 前十到底有多稳
     → 全榜 Kendall τ、前十交集、基准前十在各情景下的名次与最大位移。

纪律：只读候选池，不写回、不改 report.md 的正式名次。
情景 C 里每台车使用的维集合不同，是压力测试，不是可发布的排名。

用法：
    python3 ice_sensitivity.py --pool <ice_pool.json> \
        --category car_ice_phev --out <报告目录>
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ranking                            # noqa: E402
import leaderboard as L                   # noqa: E402
import ice_scoring as IS                  # noqa: E402

SCENARIOS = {
    "A": "基准（现行权重，插补照旧）",
    "B": "去掉销量维 market，其余维按原比例重归一化",
    "C": "禁用插补维：每台车只用有真实数据的维并重归一化（压力测试）",
}


def rank_of(rows):
    return [r["model"] for r in rows]


def kendall_tau(order_a, order_b):
    """全榜 Kendall τ（无并列处理，够用于「名次整体变了多少」）。"""
    ra = {m: i for i, m in enumerate(order_a)}
    rb = {m: i for i, m in enumerate(order_b)}
    common = [m for m in order_a if m in rb]
    pairs, conc = 0, 0
    for i, x in enumerate(common):
        for y in common[i + 1:]:
            pairs += 1
            d = (ra[x] - ra[y]) * (rb[x] - rb[y])
            if d > 0:
                conc += 1
            elif d < 0:
                conc -= 1
    return (conc / pairs) if pairs else 0.0


def scenario_c(passed, cfg):
    """只用 conf != low 的维；每车可用维不同，权重按该车可得维重归一化。"""
    dims = cfg["dimensions"]
    w = {d["key"]: float(d["weight"]) for d in dims}
    tot = sum(w.values()) or 1.0
    w = {k: v / tot for k, v in w.items()}
    out = []
    for c in passed:
        sc = c.get("scores") or {}
        cf = c.get("score_confidence") or {}
        used = [(k, wk) for k, wk in w.items() if cf.get(k) != "low" and k in sc]
        sw = sum(wk for _, wk in used)
        val = (sum(sc[k] * wk for k, wk in used) / sw * 10) if sw else 0.0
        out.append({"model": c["model"], "total": round(val, 1),
                    "n_dims": len(used),
                    "used": [k for k, _ in used]})
    out.sort(key=lambda r: -r["total"])
    return out


def low_dims(c):
    return sum(1 for v in (c.get("score_confidence") or {}).values() if v == "low")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True)
    ap.add_argument("--category", default="car_ice_phev")
    ap.add_argument("--out", required=True)
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    pool = json.load(open(a.pool, encoding="utf-8"))["pool"]
    cfg = L.load_category(a.category)
    cands, audit = IS.score_pool(pool, cfg)
    w_nomarket = [0.0 if d["key"] == "market" else float(d["weight"])
                  for d in cfg["dimensions"]]
    mw = next(float(d["weight"]) for d in cfg["dimensions"] if d["key"] == "market")

    boards, lines = {}, []
    lines += ["# 燃油/混动四榜 · 三情景敏感性对照", "",
              f"> 生成 {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}｜"
              f"候选 {len(cands)} 款｜基准 market 权重 {mw * 100:.0f}%｜"
              "只读压力测试，不改写正式名次", "",
              "## 三个情景", ""]
    for k, v in SCENARIOS.items():
        lines.append(f"- **{k}** = {v}")
    lines += ["", "## 结论先看", ""]

    for b in L.board_items(cfg):
        label = b["label"]
        sub = L.filter_board(cands, b)
        passed = [c for c in sub
                  if ranking.apply_gates(c, b.get("gates") or {})["pass"]]
        by = {c["model"]: c for c in passed}
        A = L.score_rows(passed, cfg)
        B = L.score_rows(passed, cfg, w_nomarket)
        C = scenario_c(passed, cfg)
        ra, rb, rc = rank_of(A), rank_of(B), rank_of(C)
        ta, tb, tc = ra[:a.top], rb[:a.top], rc[:a.top]
        posb = {m: i + 1 for i, m in enumerate(rb)}
        posc = {m: i + 1 for i, m in enumerate(rc)}

        moves = []
        for i, m in enumerate(ta, 1):
            db, dc = posb.get(m, 0) - i, posc.get(m, 0) - i
            moves.append({"model": m, "base": i, "B": posb.get(m), "C": posc.get(m),
                          "dB": db, "dC": dc, "low_dims": low_dims(by[m])})
        thin = [m for m in ta if low_dims(by[m]) >= 4]
        dropped_c = [m["model"] for m in moves if m["C"] and m["C"] > a.top]
        rec = {
            "passed": len(passed), "top": a.top,
            "tau_AB": round(kendall_tau(ra, rb), 3),
            "tau_AC": round(kendall_tau(ra, rc), 3),
            "overlap_AB": len(set(ta) & set(tb)),
            "overlap_AC": len(set(ta) & set(tc)),
            "max_shift_B": max((abs(m["dB"]) for m in moves), default=0),
            "max_shift_C": max((abs(m["dC"]) for m in moves), default=0),
            "thin_evidence_in_top": len(thin), "thin_evidence": thin,
            "base_top_dropped_in_C": dropped_c,
            "top_A": ta, "top_B": tb, "top_C": tc, "moves": moves,
        }
        boards[label] = rec
        lines += [f"### {label}", "",
                  f"- 过闸 {len(passed)} 款；去掉销量维后全榜 Kendall τ = **{rec['tau_AB']:.3f}**"
                  f"（1.0＝名次完全不变），前十交集 **{rec['overlap_AB']}/{a.top}**，"
                  f"基准前十最大位移 **{rec['max_shift_B']}** 位。",
                  f"- 禁用插补维后 τ = **{rec['tau_AC']:.3f}**，前十交集 "
                  f"**{rec['overlap_AC']}/{a.top}**，最大位移 **{rec['max_shift_C']}** 位；"
                  f"基准前十中有 **{len(thin)} 款**是「≥4 维靠插补」的薄证据车，"
                  f"其中情景 C 掉出前十的：{'、'.join(dropped_c) or '无'}。", "",
                  "| 基准 # | 车型 | 插补维 | 去销量后名次 | 禁插补后名次 | 位移(B/C) |",
                  "|---|---|---|---|---|---|"]
        for m in moves:
            lines.append(f"| {m['base']} | {m['model']} | {m['low_dims']}/6 | "
                         f"{m['B'] or '—'} | {m['C'] or '—'} | "
                         f"{m['dB']:+d} / {m['dC']:+d} |")
        lines += ["", f"- **情景 B 前十**：{'、'.join(tb)}", "",
                  f"- **情景 C 前十**：{'、'.join(tc)}", ""]

    # 总表
    lines += ["## 四榜汇总", "",
              "| 榜单 | τ(A,B) 去销量 | 前十交集 A∩B | τ(A,C) 禁插补 | 前十交集 A∩C | 基准前十薄证据车 |",
              "|---|---|---|---|---|---|"]
    for label, r in boards.items():
        lines.append(f"| {label} | {r['tau_AB']:.3f} | {r['overlap_AB']}/10 | "
                     f"{r['tau_AC']:.3f} | {r['overlap_AC']}/10 | "
                     f"{r['thin_evidence_in_top']} |")
    lines += ["", "## 怎么读这组数", "",
              "- **交集低 / τ 低 = 名次不稳**，不是算法错了：说明这份榜对权重与证据密度敏感，"
              "购买决策不能按第 1 名和第 9 名之间那点分差来做。",
              "- **情景 B 回答「销量有多重」**：τ 掉得越多，说明榜单越接近市场热度榜。"
              "v6.3 起候选池已全量核验（不按销量截断），入池漏斗的影响由 "
              "`funnel_sensitivity.md` 单独量化；本对照只覆盖评分里的 market 维。",
              "- **情景 C 回答「插补影响多大」**：每台车用的维集合不同，"
              "它衡量的是「证据厚度」而不是产品力，因此只作压力测试，不发布为正式名次。",
              "- 三情景都不引入终端成交价与碰撞**等级分数**（v6.3 的 L1 碰撞记录只作"
              "事实与判档证据、不进加权），因此结论仍不能外推到「真实性价比」或「安全」。", ""]

    md = "\n".join(lines)
    open(os.path.join(a.out, "sensitivity.md"), "w", encoding="utf-8").write(md)
    json.dump({"generated_at": dt.datetime.now().isoformat(timespec="seconds"),
               "scenarios": SCENARIOS, "market_weight": mw,
               "coverage": audit["coverage"], "imputed": audit["imputed"],
               "boards": boards},
              open(os.path.join(a.out, "sensitivity.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"[ok] {os.path.join(a.out, 'sensitivity.md')}")
    for label, r in boards.items():
        print(f"  {label}: τ(B)={r['tau_AB']:.3f} 交集B={r['overlap_AB']}/10 | "
              f"τ(C)={r['tau_AC']:.3f} 交集C={r['overlap_AC']}/10 | "
              f"薄证据前十={r['thin_evidence_in_top']}")


if __name__ == "__main__":
    main()
