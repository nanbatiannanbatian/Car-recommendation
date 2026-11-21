#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""销量变点检测（lifecycle_detect.py）——用数据判断停产 / 清库 / 爬坡，而不是靠人搜新闻。

为什么：停产与改款在销量曲线上有不同形状，人能看出来但不会每月都去看。
  · 停产 = 单调下行到接近 0，且峰值在前期
  · 清库 = 短期陡降但仍有量（改款前的正常现象）
  · 爬坡 = 单调上行
判错的代价不对称：把清库当停产会误杀好车，把停产当波动会推荐一台买不到配件的车。

做法（纯标准库，不引入 numpy）：
  1 二分段最优分割（O(n²) 穷举，n≤12 完全够用）：找使两段内平方和之和最小的切点
  2 在切点基础上套规则判据
  3 与人工标注的 lifecycle 对照，冲突处输出核查提示

局限（必须写在报告里）：只有 6 个月数据时，检测只能识别"已经发生"的断崖，
不可能预测"将要发布的新款"。新款信息仍然只能来自新闻或厂商公告。

用法：
    python3 lifecycle_detect.py --candidates runs/x/candidates_trend.json
    python3 lifecycle_detect.py --candidates ... --out detect.md
    python3 lifecycle_detect.py --candidates ... --write   # 把建议写回 lifecycle
"""
import argparse
import json
import os
import statistics
import sys

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)


def _sse(xs):
    if not xs:
        return 0.0
    m = sum(xs) / len(xs)
    return sum((x - m) ** 2 for x in xs)


def best_split(vals):
    """返回 (切点索引, 两段均值, 段间差/整体均值)。切点 k 表示 [0:k] 与 [k:]。

    段间差用相对量（|m1-m2| / 全局均值），便于跨车型比较绝对销量差异。
    """
    n = len(vals)
    if n < 4:
        return None
    total = _sse(vals)
    best, best_k = None, None
    for k in range(2, n - 1):          # 两段至少各 2 个点
        cost = _sse(vals[:k]) + _sse(vals[k:])
        if best is None or cost < best:
            best, best_k = cost, k
    if best_k is None:
        return None
    m1 = sum(vals[:best_k]) / best_k
    m2 = sum(vals[best_k:]) / (n - best_k)
    gm = (sum(vals) / n) or 1.0
    return {"k": best_k, "m1": m1, "m2": m2,
            "jump": (m2 - m1) / gm,          # 相对段间变化
            "fit": 1 - best / total if total else 0.0}   # 分段解释了多少方差


def classify(vals, peak_ratio_low=0.25, drop_strong=-0.45):
    """按曲线形状判周期状态。返回 (status, 依据列表, 置信度 low/mid/high)。"""
    n = len(vals)
    if n < 3:
        return "unknown", ["样本不足 3 个月，无法判断趋势"], "low"
    peak = max(vals)
    last = vals[-1]
    if peak <= 0:
        return "unknown", ["全期为 0，无有效销量"], "low"

    sp = best_split(vals)
    # 连续同向：末尾连续下降/上升的月数
    down = up = 0
    for i in range(n - 1, 0, -1):
        if vals[i] < vals[i - 1] * 0.95:
            down += 1
        else:
            break
    for i in range(n - 1, 0, -1):
        if vals[i] > vals[i - 1] * 1.05:
            up += 1
        else:
            break

    ratio = last / peak
    reasons = []
    if sp:
        reasons.append(f"分段切点在第 {sp['k']+1} 月，段均 {sp['m1']:.0f} → {sp['m2']:.0f}"
                       f"（{sp['jump']:+.0%}），分段解释 {sp['fit']:.0%} 方差")

    # V 型回升：低谷后明显反弹，别误判成清库
    # 峰值必须不是最新月，否则单调上升的新车会被误判成「V 型回升」
    if (n >= 3 and vals[-1] > vals[-2] * 1.5 and vals[-1] >= peak * 0.5
            and vals.index(peak) < n - 1):
        reasons.insert(0, f"最新月 {last:,} 较上月 {vals[-2]:,} 反弹 "
                          f"{last / max(vals[-2], 1) - 1:.0%}，回到峰值 {ratio:.0%}")
        return "rebound", reasons, "mid"

    # 销量崩塌：跌到峰值的 1/4 以下，且峰值不在最后一个月。
    # 注意叫 collapse 而不是 discontinued——数据只能判「卖崩了」，
    # 判不了是退市、清库还是换代（极狐S5 跌到 12% 是清库，秦L 跌到 14% 是停产的）。
    if ratio < peak_ratio_low and vals.index(peak) < n - 1:
        reasons.insert(0, f"最新月仅为峰值的 {ratio:.0%}（{last:,} / {peak:,}）")
        conf = "high" if ratio < 0.15 and down >= 3 else "mid"
        return "collapse?", reasons, conf

    # 断崖：连续 3 个月下降且累计跌幅大
    if down >= 3 and last / max(vals[-4] if n >= 4 else peak, 1) < 0.6:
        reasons.insert(0, f"连续 {down} 个月下降，较 4 月前跌 "
                          f"{1 - last / max(vals[-4] if n >= 4 else peak, 1):.0%}")
        return "cliff?", reasons, "mid"

    # 清库：中期有一次明显下移，但当前仍有峰值的 25% 以上
    rebounded = n >= 2 and vals[-1] > vals[-2] * 1.3
    if sp and sp["jump"] < drop_strong and ratio >= peak_ratio_low and not rebounded:
        reasons.insert(0, f"段间下移 {sp['jump']:.0%}，但仍保有峰值 {ratio:.0%}")
        return "clearance?", reasons, "mid"

    # 爬坡
    # 数据点少时（新车只有 3 个月）要求「连续上升 3 个月」永远不成立，按可用点数放宽
    if up >= min(3, n - 1) and last >= peak * 0.9:
        reasons.insert(0, f"连续 {up} 个月上升，最新月已达峰值 {ratio:.0%}")
        return "ramp_up", reasons, "mid"

    reasons.insert(0, f"最新月为峰值 {ratio:.0%}，无明显段间跳变")
    return "stable", reasons, "low"


def read_series(c):
    """从候选里取逐月序列（按月份升序）。缺失月补 0。"""
    tm = c.get("trend_months") or {}
    if not tm:
        return [], []
    keys = sorted(tm)
    return keys, [float(tm[k] or 0) for k in keys]


CONFLICT = {
    # (数据判定, 人工标注) 视为不冲突。
    # collapse? 与 discontinued / facelift_pending / clearance 都不冲突——
    # 数据只能看出「卖崩了」，崩的原因是退市还是清库，得看外部信息。
    ("stable", "active"), ("ramp_up", "new_launch"), ("ramp_up", "active"),
    ("clearance?", "facelift_pending"), ("clearance?", "clearance"),
    ("collapse?", "discontinued"), ("collapse?", "facelift_pending"),
    ("collapse?", "clearance"), ("cliff?", "facelift_pending"),
    ("cliff?", "clearance"), ("unknown", "active"), ("stable", "new_launch"),
    ("cliff?", "active"), ("clearance?", "active"), ("ramp_up", "facelift_pending"),
    ("rebound", "active"), ("rebound", "new_launch"), ("rebound", "facelift_pending"),
    ("stable", "facelift_pending"), ("stable", "clearance"),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--out")
    ap.add_argument("--write", action="store_true",
                    help="把建议写入 lifecycle.detected（不覆盖人工标注）")
    a = ap.parse_args()

    d = json.load(open(a.candidates, encoding="utf-8"))
    cands = d.get("candidates", d)

    rows, conflicts = [], []
    for c in cands:
        keys, vals = read_series(c)
        if not keys:
            continue
        st, reasons, conf = classify(vals)
        manual = str((c.get("lifecycle") or {}).get("status") or "active").lower()
        conflicted = (st, manual) not in CONFLICT and st != "unknown"
        rows.append({"model": c["model"], "detected": st, "manual": manual,
                     "conf": conf, "reasons": reasons, "vals": vals, "keys": keys,
                     "conflict": conflicted})
        if conflicted:
            conflicts.append(c["model"])
        if a.write:
            c.setdefault("lifecycle", {})["detected"] = st

    L = ["# 销量变点检测：数据怎么说，人工标注怎么说", ""]
    L.append("> 目的：把「停产 / 清库 / 爬坡」从人工搜新闻变成数据判据。"
             "判错的代价不对称——把清库当停产会误杀好车，把停产当波动会推荐一台买不到配件的车。")
    L.append("")
    L.append(f"- 检测 **{len(rows)}** 款，其中 **{len(conflicts)}** 款与人工标注冲突需核查。")
    L.append("")
    L.append("| 车型 | 逐月销量 | 数据判定 | 人工标注 | 置信 | 依据 |")
    L.append("|---|---|---|---|---|---|")

    order = {"collapse?": 0, "cliff?": 1, "clearance?": 2, "rebound": 3,
             "ramp_up": 4, "stable": 5, "unknown": 6}
    for r in sorted(rows, key=lambda x: (not x["conflict"], order.get(x["detected"], 9))):
        mark = " ⚠" if r["conflict"] else ""
        spark = " → ".join(f"{int(v):,}" for v in r["vals"])
        L.append(f"| {r['model']}{mark} | {spark} | **{r['detected']}** | "
                 f"{r['manual']} | {r['conf']} | {'；'.join(r['reasons'][:2])} |")
    L.append("")

    if conflicts:
        L.append("## 需人工核查（数据与标注不一致）")
        L.append("")
        for r in rows:
            if not r["conflict"]:
                continue
            L.append(f"- **{r['model']}**：数据判为 `{r['detected']}`，标注为 `{r['manual']}`。"
                     f"{'；'.join(r['reasons'][:2])}")
            if r["detected"] == "collapse?" and r["manual"] == "active":
                L.append("  → 销量已崩塌但标注为在售：必须查新闻确认是停产、清库还是换代。"
                         "确认停产则改 `discontinued` 并从榜中落榜。")
            elif r["detected"] == "stable" and r["manual"] == "facelift_pending":
                L.append("  → 标注说改款在即但曲线平稳：可能是刚官宣还没开始清库，"
                         "也可能是标注错了。以官方上市时间为准。")
            elif r["detected"] == "ramp_up" and r["manual"] == "facelift_pending":
                L.append("  → 销量在上升却在清库期，两者矛盾，检查是否换代前后销量口径混了（老款+新款合并）。")
        L.append("")

    L.append("## 检测的边界")
    L.append("")
    L.append("- 只有 6 个月数据时，只能识别**已经发生**的断崖，无法预测**将要发布**的新款。"
             "新款信息仍然只能来自新闻或厂商公告——这一点不能靠算法替代。")
    L.append("- **`collapse?` 只表示「销量崩塌」，不表示「停产」**。"
             "同样是跌到峰值 12%–14%，秦L 是停产，极狐S5 是清库——"
             "两者的区别不在数据里，在新闻里。这是本检测最关键的边界。")
    L.append("- 「疑似」状态（带问号）是提示而非结论，必须人工确认后才改 `lifecycle`。")
    L.append("- 逐月销量本身受上牌节奏、促销冲量、产能爬坡影响，单月波动不足以定性；"
             "判据要求连续 3 个月同向才触发。")

    txt = "\n".join(L)
    print(txt)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(txt + "\n")
        print(f"\n[ok] {a.out}", file=sys.stderr)
    if a.write:
        json.dump(d, open(a.candidates, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"[ok] 已写入 lifecycle.detected → {a.candidates}", file=sys.stderr)


if __name__ == "__main__":
    main()
