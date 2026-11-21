#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 C-NCAP 成绩同步进候选池（sync_cncap.py）。

为什么需要单独一步：
  安全维原本是七维里唯一「必须靠官方成绩」的维度，而成绩散在官网（客户端渲染、
  无公开 API）、公众号汇总表、媒体报道三处，只能半自动累积成 cncap_scores.json。
  这一步把成绩映射成分数并写回候选池，让正式榜单也能用上真实成绩。

一个关键的评分原则：
  **没成绩不等于不合格。** 有成绩的车按实测得分率映射；没有成绩的车**不惩罚**，
  取本榜「有成绩车型的中位数」作为插补值，只把 `safety_confidence` 标为 low。
  否则会变成「谁没被测谁吃亏」，而没被测往往是上市晚、销量小，不是车不安全。

  一个容易被忽略的副作用（v5.9 修复）：
  **纯中位插补会让 safety 维趋同、18% 权重实际失效。** 39 款里只有 8 款有成绩，
  同榜有成绩车型 <3 时回退全池中位，结果 31 款全部落到一个数上，sd 从 0.35–0.44
  掉到接近 0，MCDA 矩阵里这一维变成常数。SKILL.md 里记的「safety 落 8.7、sd 0.00」
  正是这个坑。所以默认改成 blend：无成绩车 = α·原人工分 + (1−α)·中位插补，
  既保留相对序、又仍受中位约束，且一律标 low。α 可调，三种模式都可回退对照。

用法：
    python3 sync_cncap.py --candidates cands.json --scores cncap_scores.json
    python3 sync_cncap.py --candidates cands.json --fill-mode median   # 旧行为（纯中位）
    python3 sync_cncap.py --candidates cands.json --dry-run
"""
import argparse
import json
import os
import statistics
import sys


def safety_from_pct(pct):
    """综合得分率 → 0–10 分。**统一走契约层**（原实现自带一套 cap 10/floor 6，
    与 official_safety_scores.json 的 cap 9.3 不一致，95%+ 高分段会给出不同分）。
    保留本函数名只为向后兼容，逻辑不再本地维护。
    """
    from modules.contracts.safety_score import score_from_pct
    return score_from_pct(pct)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--scores", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "cncap_scores.json"))
    ap.add_argument("--out", help="输出路径（默认覆盖输入）")
    ap.add_argument("--fill-mode", choices=("median", "blend", "manual"), default="blend",
                    help="无成绩车的安全分口径：median=纯中位插补（该维趋同、权重失效，仅作对照）；"
                         "blend=原人工分与中位各半（默认）；manual=保留人工分，只覆盖有成绩车")
    ap.add_argument("--blend-alpha", type=float, default=0.5,
                    help="blend 模式下原人工分的权重（0=纯中位，1=纯人工）")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    alpha = max(0.0, min(1.0, a.blend_alpha))

    sc = json.load(open(a.scores, encoding="utf-8"))
    table = sc["scores"]
    no_score = sc.get("confirmed_no_score") or {}
    d = json.load(open(a.candidates, encoding="utf-8"))
    cands = d["candidates"]

    # 先按榜收集「有成绩车型」的安全分，用来插补无成绩的。
    # 同榜样本 <3 时回退到全池中位——否则「榜里只有一款有成绩」会让其余车
    # 全部被插补成那一款的分（中大型SUV 榜实测只有 NX8 一款有成绩，就是这个坑）。
    by_cls, all_peers = {}, []
    for c in cands:
        pct = table.get(c["model"])
        if pct is not None:
            v = safety_from_pct(pct)
            by_cls.setdefault(str(c.get("cls")), []).append(v)
            all_peers.append(v)
    pool_med = round(statistics.median(all_peers), 1) if all_peers else None

    hit, miss, fixed = 0, [], []
    for c in cands:
        name = c["model"]
        pct = table.get(name)
        if pct is not None:
            old = (c.get("scores") or {}).get("safety")
            c.setdefault("scores", {})["safety"] = safety_from_pct(pct)
            c["cncap_pct"] = float(pct)
            c["crash"] = f"C-NCAP {pct:g}%"
            c["safety_basis"] = "cncap"
            c["safety_confidence"] = "high"
            hit += 1
            if old is not None and abs(float(old) - safety_from_pct(pct)) >= 0.3:
                fixed.append((name, old, safety_from_pct(pct), pct))
        else:
            peers = by_cls.get(str(c.get("cls"))) or []
            # 没有成绩：不降分，只降置信度。样本足够用同榜中位，否则用全池中位。
            if len(peers) >= 3:
                fill, basis = round(statistics.median(peers), 1), "同榜中位"
            else:
                fill, basis = pool_med, "全池中位（同榜有成绩车型 <3）"
            manual = (c.get("scores") or {}).get("safety")
            written, mode = None, a.fill_mode
            if mode == "manual":
                basis += "；按 --fill-mode manual 保留原人工分"
            elif mode == "median" or fill is None:
                written = fill
            elif manual is not None:
                written = round(alpha * float(manual) + (1 - alpha) * fill, 1)
                basis += f"；blend α={alpha:g}·人工分 {manual} + {1-alpha:g}·中位 {fill}"
            else:
                written = fill
            if written is not None:
                c.setdefault("scores", {})["safety"] = written
            if name in no_score:
                c["crash"] = "官方未测（已核实）"
                c["safety_basis"] = "confirmed_none"
            else:
                c["crash"] = c.get("crash") or "无官方成绩"
                c["safety_basis"] = ("imputed" if mode == "median" else
                                     "manual_unverified" if mode == "manual" else
                                     "imputed_blend")
            c["safety_confidence"] = "low"
            c["safety_note"] = (f"{basis}；无成绩不代表不安全，仅代表尚未被第三方验证；"
                                f"最终安全分 {written if written is not None else manual}")
            miss.append(name)

    print(f"C-NCAP 命中 {hit}/{len(cands)}；无成绩 {len(miss)}；"
          f"fill-mode={a.fill_mode}"
          + (f" α={alpha:g}" if a.fill_mode == "blend" else ""))
    if fixed:
        print("\n安全分被显著修正的（旧 → 新）：")
        for n, o, v, p in fixed:
            print(f"  {n:16s} {o} → {v}（C-NCAP {p:g}%）")
    if miss:
        print(f"\n无成绩、按同榜中位插补：{'、'.join(miss[:10])}"
              f"{' 等' if len(miss) > 10 else ''}")

    if not a.dry_run:
        out = a.out or a.candidates
        json.dump(d, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\n[ok] {out}")


if __name__ == "__main__":
    main()
