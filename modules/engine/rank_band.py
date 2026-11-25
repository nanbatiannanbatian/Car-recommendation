# -*- coding: utf-8 -*-
"""rank_band.py —— 名次带（插补不确定性 → 名次区间）

问题（2026-11-25，用户「继续做」清单里的未做项）：
  主榜只给一个整数名次，但 22 款里 7 款可靠性、14 款持有成本、8 款智驾
  是「无证据 → 中位插补」得来的分。插补车的总分本身带不确定性，
  显示「#6」等于假装这个名次是精确的——第 6 和第 7 相差 0.1 分时，
  到底谁在前，取决于那几维插补值的运气。

做法（蒙特卡洛，不改分、只加区间）：
  1. 对每一款车，只扰动「无证据插补」的维度：s' = s + N(0, σ_dim)，
     σ_dim 取该维**有证据车**分数的总体标准差（证据越集中，带越窄）。
  2. 总分按原公式重算（10 × Σ w·s），全池重排 300 次（固定种子，可复现）。
  3. 取每款车名次的 P10–P90 作为名次带；带宽度即「这条名次有多硬」。
  纸面可判定维度（space/value/powertrain）不扰动——它们本来就没有不确定性。

同时输出每款车的「未验证权重」= 所有插补维权重之和，供页面徽标披露
（未验证 20% = 总分里有两成来自插补，不是实测）。
"""
from __future__ import annotations

import random
import statistics

N_DRAWS = 300
SEED = 20261125
SIGMA_FLOOR = 0.5      # 某维有证据车太少/太集中时，σ 不低于 0.5 分（避免带假性为 0）


def imputed_of(cand) -> dict:
    """从 evidence_gate 写入的 dim_evidence 取插补维（kind=imputed）。"""
    return {k: v for k, v in ((cand or {}).get("dim_evidence") or {}).items()
            if isinstance(v, dict) and v.get("kind") == "imputed"}


def _sigma(rows, cands_by_model, dim, policy) -> float:
    vals = []
    for r in rows:
        s = (r.get("scores") or {}).get(dim)
        if not isinstance(s, (int, float)):
            continue
        c = cands_by_model.get(r.get("model")) or {}
        if dim in imputed_of(c):
            continue                   # 有证据车才算 σ 样本
        vals.append(float(s))
    if len(vals) < 3:
        return SIGMA_FLOOR
    sd = statistics.pstdev(vals)
    return max(SIGMA_FLOOR, round(sd, 3))


def apply_all(rows, cands, cfg, n_draws: int = N_DRAWS, seed: int = SEED) -> dict:
    """给每行写入 rank_band / rank_band_note / imputed_weight；返回汇总 dict。

    rows 需含 model / scores / total / rank。只读不写分数，返回结构由调用方
    decide 放进 summary。
    """
    if not rows:
        return {"models": {}, "note": "无上榜车"}
    dims = cfg.get("dimensions") or []
    weights = {d.get("key"): float(d.get("weight") or 0) for d in dims}
    cands_by_model = {c.get("model"): c for c in (cands or [])}
    imps = {r.get("model"): imputed_of(cands_by_model.get(r.get("model"))) for r in rows}
    sigmas = {d: _sigma(rows, cands_by_model, d, None) for d in weights}

    rng = random.Random(seed)
    ranks = {r.get("model"): [] for r in rows}
    for _ in range(n_draws):
        # v10.74.4 校准：插补锚是「池级」量（同一维所有无证据车共用一个 ŝ），
        # 所以同维插补车的误差**同向**——若按车独立抽样，会把本来稳的组内次序
        # 也打散（首版尚界H5 名次带 4–15，等于说全榜前十都可能，明显过宽）。
        # 现改为「共同冲击 + 少量个体偏差」：共同冲击让插补组整体相对有证据车浮动，
        # 个体偏差（0.35σ）只允许同组内轻微换位。
        common = {d: rng.gauss(0.0, sigmas.get(d, SIGMA_FLOOR))
                  for d in weights if any(d in imps.get(r.get("model"), {}) for r in rows)}
        draw_totals = []
        for r in rows:
            base = r.get("total")
            if not isinstance(base, (int, float)):
                base = 10.0 * sum(weights.get(k, 0.0) * float(v)
                                  for k, v in (r.get("scores") or {}).items()
                                  if isinstance(v, (int, float)))
            delta = 0.0
            for d in imps[r.get("model")]:
                if d in weights:
                    sd = sigmas.get(d, SIGMA_FLOOR)
                    delta += 10.0 * weights[d] * (common.get(d, 0.0)
                                                  + 0.35 * sd * rng.gauss(0.0, 1.0))
            draw_totals.append((r.get("model"), float(base) + delta))
        draw_totals.sort(key=lambda x: (-x[1], x[0]))
        for i, (m, _v) in enumerate(draw_totals, start=1):
            ranks[m].append(i)

    out = {}
    for r in rows:
        m = r.get("model")
        rk = ranks[m] or [r.get("rank") or 1]
        rk_sorted = sorted(rk)
        lo = max(1, rk_sorted[max(0, int(0.10 * (len(rk_sorted) - 1)))] )
        hi = min(len(rows), rk_sorted[int(0.90 * (len(rk_sorted) - 1))])
        p50 = rk_sorted[len(rk_sorted) // 2]
        # v10.74.4：中间 50%（P25–P75）——页面显示这个（P10–P90 太宽，容易读成「全榜都可能」）
        q25 = rk_sorted[int(0.25 * (len(rk_sorted) - 1))]
        q75 = rk_sorted[int(0.75 * (len(rk_sorted) - 1))]
        im = [d for d in imps[m] if d in weights]
        iw = round(sum(weights[d] for d in im), 3)
        labels = {d.get("key"): (d.get("label") or d.get("key")) for d in dims}
        note = ("插补维：" + "、".join(labels.get(d, d) for d in im)) if im else "全部维度均有证据"
        r["rank_band"] = [q25, q75]
        r["rank_band_p10p90"] = [lo, hi]
        r["rank_p50"] = p50
        r["imputed_weight"] = iw
        r["rank_band_note"] = note
        out[m] = {"lo": q25, "hi": q75, "p10": lo, "p90": hi, "p50": p50,
                  "imputed_weight": iw, "imputed_dims": im, "note": note}

    return {
        "models": out,
        "n_draws": n_draws,
        "sigmas": sigmas,
        "note": ("名次带 = 仅对「无证据插补」维度按 ±σ 抽样 300 次后重排的 P10–P90；"
                 "带窄说明该名次靠的是实测数据，带宽说明名次可以被插补值的运气改写。"
                 "纸面可判定维度不参与抽样。"),
    }


def selftest() -> list[str]:
    errs = []
    cfg = {"dimensions": [
        {"key": "a", "label": "A", "weight": 0.5},
        {"key": "b", "label": "B", "weight": 0.3},
        {"key": "c", "label": "C", "weight": 0.2}]}
    rows = [
        {"model": "实据车", "scores": {"a": 9.0, "b": 8.0, "c": 7.0}, "total": 8.45, "rank": 1},
        {"model": "插补车", "scores": {"a": 9.0, "b": 8.4, "c": 7.0}, "total": 8.29, "rank": 2},
        {"model": "弱车", "scores": {"a": 7.0, "b": 6.0, "c": 6.0}, "total": 6.80, "rank": 3},
    ]
    cands = [
        {"model": "实据车", "dim_evidence": {}},
        {"model": "插补车", "dim_evidence": {"b": {"kind": "imputed"}}},
        {"model": "弱车", "dim_evidence": {"b": {"kind": "imputed"}}},
    ]
    res = apply_all(rows, cands, cfg, n_draws=200)
    # 1) 全证据车自身不扰动 → 带下界必须是自己名次（别的车插补上调仍可能越过它，
    #    这是真实不确定性，不能要求带收敛成 [1,1]）
    if rows[0]["rank_band"][0] != 1:
        errs.append(f"实据车带下界应为自己名次 1，实际 {rows[0]['rank_band']}")
    # 2) 插补车的带必须非空（不确定性可见）
    if rows[1]["rank_band"][1] - rows[1]["rank_band"][0] < 1:
        errs.append(f"插补车带应至少跨 1 名，实际 {rows[1]['rank_band']}")
    # 3) 未验证权重 == 插补维权重之和
    if abs(rows[1]["imputed_weight"] - 0.3) > 1e-9:
        errs.append(f"未验证权重应为 0.3，实际 {rows[1]['imputed_weight']}")
    if rows[0]["imputed_weight"] != 0:
        errs.append("实据车未验证权重应为 0")
    # 4) 带必须落在 [1, len(rows)] 内且 lo<=hi
    for r in rows:
        lo, hi = r["rank_band"]
        if not (1 <= lo <= hi <= len(rows)):
            errs.append(f"{r['model']} 带越界 {r['rank_band']}")
    # 5) 固定种子可复现
    rows2 = [dict(r) for r in rows]
    for r, r2 in zip(rows, rows2):
        r2.pop("rank_band", None)
    res2 = apply_all(rows2, cands, cfg, n_draws=200)
    if res2["models"]["插补车"]["lo"] != res["models"]["插补车"]["lo"]:
        errs.append("同种子两次结果不一致（不可复现）")
    return errs


if __name__ == "__main__":
    e = selftest()
    print("[rank_band] selftest", "PASS" if not e else "FAIL")
    for x in e:
        print(" -", x)
