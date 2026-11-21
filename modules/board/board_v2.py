# -*- coding: utf-8 -*-
"""统一榜单引擎 v2：打分榜唯一主榜 + 事实列参考(不计分) + 价位段子榜。

与旧链路的关系
--------------
旧链路里「事实榜」(kWh/万元、电耗单独排序) 和「打分榜」是两套并列产物，用户容易混。
v2 把它们收敛成一套：

  1. 主榜 = 打分榜（8 维加权），这是唯一权威名次来源。
  2. 事实指标（kWh、kWh/万元、电耗、续航、电芯、800V、销量）作为「参考列」贴在主榜每行，
     明确标注「参考·不计分」——它们帮助理解，但绝不参与加权。
  3. 价位段子榜 = 把同一份主榜分数按预算分组的展示视图。它复用主榜分数，
     不重新打分、不改权重。回答「我这个预算买哪款最好」。

价位差异怎么处理（用户三选一的决策）
------------------------------------
不做「价格相差太多就不排名」的硬排除。理由：
  - 同级别（官方二级分类）已经控制了「车有多大、什么用途」这个最大混淆变量；
  - 价格对竞争力的影响由「性价比」维度(weight≈0.117)吸收——贵车性价比低但产品力维度占优，
    便宜车反之，混排本身就是有意义的综合比较；
  - 硬排除会丢掉「多花 5 万值不值」的信息，且价位边界会造成临界车跳榜的假精确。
所以：主榜全价位混排，价格高的性价比分低但依然参与排名；再附加价位段子榜给预算敏感用户。

分段汇总总榜（v8.3，默认展示模板）
------------------------------------
用户规则：排行榜如果没有价位段，就分几个价位段，每段取前几名，最后把这几段汇总成总榜。
`segmented_summary()` 实现：按价位段分组 → 每段按主榜分数取前 per_band 名（默认 10）→
汇总后按主榜分数降序，形成一张「各价位段各有前 10 代表」的总榜。分数仍来自主榜，不重新打分。
用户口径：回答「全价位前 10」时，不是全池混排取前 10（会被高端车垄断），而是入门/主流/高端
各列前 10 再汇总——这样每个价位段的前 10 都看得到，段内选车逻辑各自成立。
与价位段子榜的区别：子榜是全量分组视图；分段汇总总榜是各段前几名拼成的 Top N 总榜。

纯产品力总榜（v8.3.3，并列辅助榜）
------------------------------------
`product_power_board()`：剔除性价比维度（value），其余 7 维按原权重归一化×10，
全池混排、不分价位段（目的正是跨价位直接比产品力）。回答「不看价格谁的产品力更强」。
纪律：这是【辅助榜】，与主榜并列展示，主榜（8维含性价比）仍是唯一权威排序，
本榜绝不改动主榜任何名次；两榜各自的分数、名次、口径都分开标注，避免混淆。
rank_delta = 主榜名次 - 产品力名次；正数=剔除性价比后名次上升（性价比拖累了它），
负数=剔除性价比后名次下降（它靠性价比抬升了主榜名次）。

CLI
---
  python3 board_v2.py --candidates <带scores的候选池.json> \
      --config categories/car_ev_1218_quality.json \
      --policy categories/rank_v2_policy.json \
      --facts <事实参数.json, 可选> --out <目录>

  python3 board_v2.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import paths

HERE = str(paths.ROOT)
try:
    from modules.engine import leaderboard as LB
except ImportError:  # pragma: no cover
    LB = None


# ---------------------------------------------------------------- 事实参考列
def fact_columns(c: dict, facts: dict[str, dict], policy: dict) -> dict:
    """抽取一台车的事实参考列。返回 {label: value}；不返回任何参与打分的字段。"""
    f = facts.get(c.get("model"), {}) or {}
    price = c.get("price_wan") or c.get("trim", {}).get("price") if isinstance(c.get("trim"), dict) \
        else c.get("price_wan")
    price = _num(price)
    kwh = _num(f.get("kwh") or c.get("kwh"))
    out: dict[str, Any] = {}
    by_key = {col["key"]: col for col in policy["fact_reference_columns"]}
    for col in policy["fact_reference_columns"]:
        k, label = col["key"], col["label"]
        if k == "price":
            v = price
        elif k == "kwh":
            v = kwh
        elif k == "kwh_per_wan":
            v = round(kwh / price, 2) if (kwh and price) else None
            # v9.6.2：缺失时写明原因，避免读者误以为"前10才算"
            if v is None:
                out["kWh/万元_缺失原因"] = (
                    "缺电量" if not kwh else ("缺配置价" if not price else "数据缺失"))
        elif k == "consumption":
            v = _num(f.get("consumption") or c.get("consumption"))
        elif k == "cltc":
            v = _num(f.get("cltc") or c.get("cltc"))
        elif k == "cell_brand":
            v = f.get("cell_brand") or c.get("cell_brand")
            # v10.9.0：缺电芯且本次已联网核验 → 带出原因/线索，供页面显示「—（原因）」。
            # 只在库与候选都为空时才写，不覆盖任何已核验事实。
            if v in (None, "", "—"):
                cv = c.get("_cell_verify") or {}
                if cv.get("status") in ("need_review", "unavailable", "net_error"):
                    out["电芯_缺失原因"] = cv.get("reason") or "联网未查到，待核"
        elif k == "is_800v":
            v = f.get("is_800v", c.get("is_800v"))
        elif k == "trend_sales":
            v = c.get("trend_sales")
            # v9.1.0：车系合计是纯电销量的上界，加 ≤ 前缀供三分法区分，不参与分位比较
            if v is not None and c.get("sales_scope") == "series_total":
                v = f"≤{v}"
        else:
            v = f.get(k)
        if isinstance(v, bool):
            v = "是" if v else "否"
        out[label] = "—" if v is None else v
    return out


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 主榜（打分榜）
def main_board(cands: list[dict], cfg: dict, weights: list[float] | None = None) -> list[dict]:
    """唯一权威名次来源。直接复用 skill 的 score_rows，不重写打分逻辑。"""
    if LB is None:
        raise RuntimeError("leaderboard.py 未找到，无法打分")
    rows = LB.score_rows(cands, cfg, weights)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


# ---------------------------------------------------------------- 价位段子榜
def price_of(c: dict) -> float | None:
    if isinstance(c.get("trim"), dict):
        p = _num(c["trim"].get("price"))
        if p:
            return p
    return _num(c.get("price_wan"))


def assign_bands(cands: list[dict], policy: dict) -> dict[str, list[str]]:
    """把车型分到价位段。返回 {段名: [model,...]}。"""
    bands = policy["price_bands"]
    grouped: dict[str, list[str]] = {}
    if bands.get("mode") == "quantile":
        prices = sorted((price_of(c), c["model"]) for c in cands if price_of(c) is not None)
        n = len(prices)
        breaks = bands.get("quantile_breaks", [0.34, 0.67])
        labels = bands.get("quantile_labels", [f"段{i+1}" for i in range(len(breaks) + 1)])
        for i, (_, m) in enumerate(prices):
            frac = i / max(n - 1, 1)
            idx = sum(frac > b for b in breaks)
            grouped.setdefault(labels[idx], []).append(m)
    else:
        bounds = bands.get("boundaries", [])
        labels = bands.get("labels", [])
        names = labels if len(labels) == len(bounds) + 1 else \
            [f"段{i+1}" for i in range(len(bounds) + 1)]
        # 预置键，保证子榜按价位从低到高展示，不受候选顺序影响
        grouped = {name: [] for name in names}
        for c in cands:
            p = price_of(c)
            if p is None:
                continue
            idx = sum(p > b for b in bounds)
            grouped.setdefault(names[idx], []).append(c["model"])
    return grouped


def band_subboards(rows: list[dict], cands: list[dict], policy: dict) -> list[dict]:
    """价位段子榜：按主榜分数排序的分组视图，分数来自主榜（不重算）。

    v8.3.7：提问已给价位段（候选只落一个非空段）时返回空列表——
    此时子榜＝主榜按唯一段原样重列，且价格缺失的车会被丢掉，零信息增量。
    多段全价位榜不受影响，仍按段给分组视图。
    """
    grouped = assign_bands(cands, policy)
    nonempty = [label for label, models in grouped.items() if models]
    if len(nonempty) <= 1:
        return []
    by_model = {c["model"]: c for c in cands}
    score_by_model = {r["model"]: r["total"] for r in rows}
    out = []
    for label, models in grouped.items():
        ranked = sorted(models, key=lambda m: -score_by_model.get(m, -1e9))
        out.append({
            "band": label,
            "items": [{"model": m, "score": score_by_model.get(m),
                       "price": price_of(by_model[m])} for m in ranked],
        })
    return out


# ---------------------------------------------------------------- 分段汇总总榜
def segmented_summary(rows: list[dict], cands: list[dict], policy: dict,
                      per_band: int = 10) -> dict:
    """分段汇总总榜：每个价位段取分数前 per_band 名（默认10），汇总成一张总榜。

    - 回答「排行榜没有价位段就分段、每段取前几、汇总成总榜」。
    - 价值：保证每个价位段都有车进总榜，避免高端车凭产品力垄断前列；
      这是全价位主榜（纯拼综合分）之外的「带分段配额的总榜」视角。
    - 纪律：分数全部来自主榜 rows，不重新打分、不改权重。
    - v8.3.6 起：**非价位段提问（候选只落一个段）时跳过**。此时「各段取前几→汇总」
      等于把主榜原样再列一遍，没有信息增量；用户已给价位段的场景应看主榜+纯产品力榜。
      多段（全价位榜）时规则不变，照常分段取前几汇总。
    """
    by_model = {c["model"]: c for c in cands}
    score_by_model = {r["model"]: r["total"] for r in rows}
    grouped = assign_bands(cands, policy)
    nonempty = [label for label, models in grouped.items() if models]
    if len(nonempty) <= 1:
        return {
            "per_band": per_band,
            "bands": [{"band": label, "n_total": len(grouped[label]), "n_taken": 0}
                      for label in grouped],
            "items": [],
            "skipped": True,
            "reason": ("候选只落入 1 个价位段（提问已给价位段），分段汇总等于重列主榜，"
                       "无信息增量；已跳过。多段全价位榜仍按各段前 N 汇总。"),
        }
    summary: list[dict] = []
    bands_meta: list[dict] = []
    for label, models in grouped.items():
        ranked = sorted(models, key=lambda m: -score_by_model.get(m, -1e9))
        top = ranked[:per_band]
        bands_meta.append({"band": label, "n_total": len(models), "n_taken": len(top)})
        for rank_in_band, m in enumerate(top, 1):
            c = by_model[m]
            summary.append({
                "model": m,
                "score": score_by_model.get(m),
                "price": price_of(c),
                "band": label,
                "rank_in_band": rank_in_band,
                "tier": c.get("tier", ""),
            })
    # 汇总后按主榜分数降序（分数是唯一权威来源）
    summary.sort(key=lambda x: -(x["score"] if x["score"] is not None else -1e9))
    for i, item in enumerate(summary, 1):
        item["summary_rank"] = i
    return {"per_band": per_band, "bands": bands_meta, "items": summary,
            "skipped": False, "reason": ""}


# ---------------------------------------------------------------- 纯产品力总榜（并列辅助榜）
def product_power_board(rows: list[dict], cands: list[dict], cfg: dict,
                        policy: dict | None = None) -> dict:
    """纯产品力总榜：剔除性价比(value)，其余7维按原权重归一化×10，全池混排。

    - 与主榜并列的【辅助榜】，回答「不看价格谁的产品力更强」。
    - 纪律：主榜（8维含性价比）仍是唯一权威排序；本榜不改主榜任何名次、分数、权重。
    - 全池混排、不分价位段——因为目的正是跨价位直接比产品力。
    - rank_delta = 主榜名次 - 产品力名次；正=剔除性价比后名次上升，负=靠性价比抬升。
    """
    W = {d["key"]: d["weight"] for d in cfg["dimensions"]}
    pb_cfg = (policy or {}).get("product_board", {})
    value_key = pb_cfg.get("excluded_dimension", "value")
    keys = [k for k in W if k != value_key]
    tw = sum(W[k] for k in keys)
    if tw <= 0:
        raise RuntimeError("产品力维度权重合计为0，无法归一化")
    by_model = {c["model"]: c for c in cands}
    main_rank = {r["model"]: r["rank"] for r in rows}
    main_score = {r["model"]: r["total"] for r in rows}
    band_of = {}
    if policy:
        for label, models in assign_bands(cands, policy).items():
            for m in models:
                band_of[m] = label
    items = []
    best_detail = {r["model"]: r.get("best_trim_detail") for r in rows}
    for mdl, c in by_model.items():
        # v9.7.6：与主榜同口径——用最高配置的 scores（不再是车系基础分）
        bd = best_detail.get(mdl)
        sc = (bd.get("scores") if bd else None) or c.get("scores") or {}
        if any(k not in sc for k in keys):
            continue  # 维度不全的车不进产品力榜
        pp = sum(sc[k] * W[k] for k in keys) / tw * 10
        items.append({
            "model": mdl,
            "power_score": round(pp, 2),
            "main_score": main_score.get(mdl),
            "main_rank": main_rank.get(mdl),
            "price": (bd.get("price") if bd else None) or price_of(c),
            "band": band_of.get(mdl, ""),
        })
    items.sort(key=lambda x: -x["power_score"])
    for i, it in enumerate(items, 1):
        it["power_rank"] = i
        it["rank_delta"] = (it["main_rank"] - i) if it["main_rank"] is not None else None
    return {
        "excluded_dimension": value_key,
        "note": "辅助榜：剔除性价比/7维归一化×10/全池混排；主榜8维全方面仍是唯一权威排序",
        "n": len(items),
        "items": items,
    }


# ---------------------------------------------------------------- 组装
def analysis_blocks(cands: list[dict], cfg: dict, weights: list[float] | None = None,
                    board_label: str | None = None) -> dict:
    """三榜并列 / 稳健性 / 审查边界的结构化数据（复用 pipeline 同款 gate 逻辑）。"""
    out = {}
    try:
        from modules.engine import leaderboard as LB
        from modules.board import pipeline as PL
        items = LB.board_items(cfg)
        bd = next((b for b in items if not board_label or (b.get("label") or "") == board_label),
                  items[0] if items else {})
        sub = LB.filter_board(cands, bd)
        passed, dropped, _ = PL.gate_report(sub, cfg, bd)
        if not passed:
            return {}
        sc = LB.score_rows(passed, cfg, weights)
        by = {c["model"]: c for c in passed}
        tri = LB.tri_report(sc, cfg, passed, 10, weights)
        s_rank = {m: i for i, m in enumerate(tri["sales"], 1)}
        w_rank = {m: i for i, m in enumerate(tri["weighted"], 1)}
        b_rank = {m: i for i, m in enumerate(tri["bt"], 1)}
        out["three_board"] = {
            "rows": [{"model": r["model"], "sales": by.get(r["model"], {}).get("trend_sales"),
                      "sales_rank": s_rank.get(r["model"]), "weighted_rank": w_rank.get(r["model"]),
                      "bt_rank": b_rank.get(r["model"]),
                      "delta": (s_rank.get(r["model"], 0) - w_rank.get(r["model"], 0))
                               if s_rank.get(r["model"]) and w_rank.get(r["model"]) else None}
                     for r in sc[:10]],
            "overlap": tri["overlap"], "bt_top": tri["bt"][:5],
            "bt_scores": {m: tri["bt_scores"].get(m) for m in tri["bt"][:5]},
        }
        rb = LB.robust_report(passed, cfg, weights)
        out["robustness"] = {
            "kendall_mean": rb.get("kendall_mean"), "n_runs": rb.get("n_runs"),
            "n_methods": rb.get("n_methods"), "n_schemes": rb.get("n_schemes"),
            "per_alt": {m: {"hits": a.get("method_hits"), "verdict": a.get("method_verdict")}
                        for m, a in (rb.get("per_alt") or {}).items()},
            "scheme_top": rb.get("scheme_top"),
        }
        rv = LB.review_report(passed, cfg, weights)
        out["audit_boundary"] = {
            "verdict": rv.get("verdict"), "blocking": rv.get("blocking") or [],
            "boundary": rv.get("boundary") or [], "warn": rv.get("warn") or [],
            "info": rv.get("info") or [],
        }
    except Exception as ex:
        out["_error"] = f"{type(ex).__name__}: {ex}"
    return out


def build(cands: list[dict], cfg: dict, policy: dict,
          facts: dict[str, dict] | None = None,
          weights: list[float] | None = None,
          per_band: int = 10,
          no_experience: bool = False,
          tiers: dict[str, str] | None = None,
          battery_platform: dict | None = None) -> dict:
    facts = facts or {}
    # 候选池形状归一化（契约层）：price 文本转数值、trim 统一 dict
    from modules.contracts.candidate_schema import normalize_pool
    cands = normalize_pool(cands)
    # 价格回流：trims 已核验的官方指导价 → 候选池 price_guide（与 pipeline 同一份实现，
    # 直接调 board_v2 出榜也受保护，不必记得先跑 --sync-prices）
    from modules.engine import facts_sync as FS
    _bf, _bs = FS.backfill_price_guide_from_trims(cands)
    if _bf:
        print(f"[价格回流] {len(_bf)} 款 price_guide 由 trims 官方指导价回填")
    # 碰撞成绩回流：note 里有成绩就结构化进 crash/safety_basis（与 pipeline 同一份实现）
    _cf, _cu = FS.backfill_crash_from_text(cands)
    if _cf:
        print(f"[碰撞回流] {len(_cf)} 款 crash/safety_basis 由文本成绩回填")
    # v9.6.1：电压/封装事实库（按评分配置）；调用方可覆盖
    if battery_platform is None:
        _bp_path = str(paths.cat("battery_platform.json"))
        battery_platform = (_load(_bp_path).get("values") or {}) if os.path.exists(_bp_path) else {}
    # v9.3.0：建池时自动应用体验证据（只调 adas/control）；
    # --no-experience 或 no_experience=True 可关闭（A/B 对照用）
    import copy
    from modules.engine import experience_score as ES
    cands = ES.apply_experience(copy.deepcopy(cands), enabled=not no_experience)
    rows = main_board(cands, cfg, weights)
    # v9.7.3：主榜按「每车最高配置分」评价——统一人工口径（车系base+客观delta）
    # 所有配置分同量纲百分制，直接排名不再锚定；无配置数据的车沿用原车系分。
    from modules.engine import trim_annotate as TA
    trim_data = TA.annotate_all(cands, cfg)
    if trim_data:
        for r in rows:
            td = trim_data.get(r["model"])
            if td and td.get("best_total") is not None:
                r["series_total"] = r["total"]
                r["total"] = round(float(td["best_total"]), 1)
                r["config_anchored_total"] = r["total"]
                r["best_trim"] = td.get("best_trim")
                r["best_trim_raw"] = td["best_total"]
                r["best_total_basis"] = td.get("best_source")
                r["best_confidence"] = td.get("best_confidence")
                r["score_basis"] = "annotated_trim"
                best_detail = next((t for t in td.get("trims", [])
                                    if t.get("name") == r["best_trim"]), None)
                r["best_trim_detail"] = best_detail
        rows.sort(key=lambda r: (-r["total"], r["model"]))
        for i, r in enumerate(rows, 1):
            r["rank"] = i
    by_model = {c["model"]: c for c in cands}
    for r in rows:
        r["fact_ref"] = fact_columns(by_model[r["model"]], facts, policy)
        # v9.7.29：弗迪电芯代际+闪充标记带进 fact_ref（事实库有则显示）
        _fk = facts.get(r["model"]) or {}
        if isinstance(_fk, dict) and _fk.get("cell_gen"):
            r["fact_ref"]["电芯代际"] = _fk["cell_gen"]
            r["fact_ref"]["电芯闪充"] = _fk.get("cell_flash")
        # v9.7.2：kWh/万元、电量、电芯按最高配置重算（与主榜分数/配置同口径）
        bd = r.get("best_trim_detail")
        if bd:
            fr = r["fact_ref"]
            kwh, price = bd.get("battery_kwh"), bd.get("price")
            if kwh is not None:
                fr["电池"] = kwh
            if kwh and price:
                fr["kWh/万元"] = round(float(kwh) / float(price), 2)
                fr.pop("kWh/万元_缺失原因", None)
            if bd.get("cell_supplier"):
                fr["电芯"] = bd["cell_supplier"]
        # 档位优先取候选自带 tier，其次取 set_tiers() 注入的全局表
        r["tier"] = by_model[r["model"]].get("tier") or _tier_of(r["model"])
        # v9.6.0：主榜单表多列所需字段随行带出（页面只读，不改分）
        _c = by_model[r["model"]]
        r["trim"] = _c.get("trim")
        r["evidence_status"] = _c.get("evidence_status")
        r["lifecycle"] = _c.get("lifecycle")
        # v9.7.12：note 随行带出，供 car_verdict 识别营运/网约车（只读证据关键词）
        r["note"] = _c.get("note")
        # v9.6.1：档位（从 summary.tiers 反查）、电压/封装（按评分配置）
        r["tier"] = (tiers or {}).get(r["model"]) or r.get("tier") or ""
        _bp = battery_platform.get(r["model"], {})
        r["battery_detail"] = _bp   # {voltage, cell, pack, trim, src}
    # v9.7.8：给每个配置预生成评价文本（此时 tier/fact_ref 已注入），供下拉切换联动
    if trim_data:
        import copy as _copy
        from modules.board import board_view as _BV
        row_by_model = {r["model"]: r for r in rows}
        for mdl, td in trim_data.items():
            base = row_by_model.get(mdl, {})
            for t in td.get("trims", []):
                vrow = _copy.deepcopy(base)
                vrow["best_trim_detail"] = t
                # v9.7.26：配置级电芯/快充缺失时，继承车系事实库值并显式标注来源，
                # 否则逐配置对比页两列全"—"（用户指出电芯信息本来就有）。
                _fr = base.get("fact_ref") or {}
                if not t.get("cell_supplier") and _fr.get("电芯") not in (None, "—", ""):
                    t["cell_supplier"] = str(_fr["电芯"])
                    t["cell_inherited"] = True
                if not t.get("charge"):
                    _bp = base.get("battery_detail") or {}
                    _volt = _bp.get("voltage")
                    if _volt and _volt != "未披露":
                        t["charge"] = f"{_volt} 平台（车系级）"
                        t["charge_inherited"] = True
                v = _BV.car_verdict(vrow, vrow)
                t["verdict"] = (f"优：{'、'.join(v['pros'])}｜短：{'、'.join(v['cons'])}｜"
                                f"适合：{v['audience']}｜{v['advice']}")
    bands = band_subboards(rows, cands, policy)
    analyses = analysis_blocks(cands, cfg, weights)
    return {
        "meta": {
            "title": policy.get("title", "统一榜单"),
            "primary": "8维加权打分榜（唯一权威名次）",
            "fact_cols_scored": False,
            "band_reuses_score": True,
            "n": len(rows),
            "band_subboards_skipped": not bands,
            "band_subboards_skip_reason": (
                "提问已给价位段，候选只落一个段；子榜＝主榜原样重列且会丢价格缺失车，已跳过。"
                if not bands else ""),
            # v8.3.9：主榜 8 维标签/权重随数据出，供 md/html 渲染维度明细（不硬编码）
            "dimensions": [{"key": d["key"], "label": d["label"],
                            "weight": d["weight"]} for d in cfg["dimensions"]],
            "fact_reference_columns": list(policy.get("fact_reference_columns") or []),
            # v9.3.0：体验修正审计（哪些车被调、依据来源）
            "experience_adjust": {
                c["model"]: c["experience_adjust"] for c in cands
                if c.get("experience_adjust", {}).get("n_evidence", 0) > 0
            },
            "experience_enabled": not no_experience,
            # v9.0：生命周期事实随数据出（历史页面有、v8.x 漏渲染）
            "lifecycle": {c["model"]: c["lifecycle"] for c in cands if c.get("lifecycle")},
            "three_board": analyses.get("three_board", {}),
            "robustness": analyses.get("robustness", {}),
            "audit_boundary": analyses.get("audit_boundary", {}),
            "trim_board": trim_data,
            # v9.7.3：主榜分数口径（统一人工口径逐配置分）
            "total_basis": "annotated_trim" if trim_data else "series",
            "total_basis_note": "主榜分＝该车型最高配置分（车系人工base+客观配置delta，"
                                "同量纲百分制）；series_total 保留车系分对照；"
                                "无配置数据的车沿用车系分；delta带置信度",
        },
        "main_board": rows,
        "band_subboards": bands,
        "segmented_summary": segmented_summary(rows, cands, policy, per_band),
        "product_power_board": product_power_board(rows, cands, cfg, policy),
    }


TIER: dict[str, str] = {}  # build_with_tier 之前可用 set_tiers 注入


def set_tiers(tiers: dict[str, list[str]]):
    TIER.clear()
    for t, members in tiers.items():
        for m in members:
            TIER[m] = t


def _tier_of(model: str) -> str:
    return TIER.get(model, "")


# ---------------------------------------------------------------- CLI
def _load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def cli(argv=None):
    ap = argparse.ArgumentParser(description="统一榜单 v2：打分主榜 + 事实参考列 + 价位段子榜")
    ap.add_argument("--candidates", help="带 scores 的候选池 json")
    ap.add_argument("--config", default=str(paths.cat("car_ev_1218_quality.json")))
    ap.add_argument("--policy", default=str(paths.cat("rank_v2_policy.json")))
    ap.add_argument("--facts", help="事实参数 json（按 model 索引），用于参考列")
    ap.add_argument("--per-band", type=int, default=10, help="分段汇总总榜每段取前几名（默认10）")
    ap.add_argument("--out", help="输出目录，写 board_v2_data.json / board_v2.md / 榜单.html")
    ap.add_argument("--no-html", action="store_true", help="不自动生成 HTML 页面（默认生成）")
    ap.add_argument("--no-experience", action="store_true",
                    help="不应用体验证据修正（只看硬件口径，A/B 对照用）")
    ap.add_argument("--html-title", help="HTML 页面标题（缺省用 data.meta.title）")
    ap.add_argument("--as-of", default=None, help="HTML 页面销量数据月标注(YYYYMM)，默认自动取上个月")
    ap.add_argument("--summary", help="pipeline.py 的 summary.json，给 HTML 提供三档分级")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        run_selftest()
        return

    raw = _load(a.candidates)
    cands = raw.get("candidates", raw)
    cfg = _load(a.config)
    policy = _load(a.policy)
    # v9.0.1：默认加载随包事实库，不再漏喂 kWh/电耗/CLTC；--facts 可覆盖
    from modules.board import board_view as BV
    facts = BV.load_fact_library(a.facts)
    _sum = _load(a.summary) if a.summary and os.path.exists(a.summary) else None
    # v9.6.1：从 summary.tiers 反查每车档位
    _tiers = {}
    for tmap in (_sum or {}).get("tiers", {}).values():
        for tk, label in (("T1", "第一档"), ("T2", "第二档"), ("T3", "第三档")):
            for nm in (tmap.get(tk) or []):
                _tiers[nm] = label
    data = build(cands, cfg, policy, facts, per_band=a.per_band,
                 no_experience=a.no_experience, tiers=_tiers)
    md = render_md(data, _sum)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        json.dump(data, open(os.path.join(a.out, "board_v2_data.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        open(os.path.join(a.out, "board_v2.md"), "w", encoding="utf-8").write(md)
        print("[ok] board_v2_data.json / board_v2.md ->", a.out)
        # v8.5: 自动出可视化 HTML（无模型、同输入同输出；--no-html 可关）
        if not a.no_html:
            try:
                from modules.board import render_board_html as RH
                summary = _load(a.summary) if a.summary and os.path.exists(a.summary) else None
                _as_of = a.as_of
                if not _as_of:
                    try:
                        from modules.board import monthly as _MO
                        _as_of = _MO.default_sales_month()
                    except Exception:
                        _as_of = "—"
                doc = RH.render(data, summary, a.html_title, _as_of)
                hp = os.path.join(a.out, "榜单.html")
                open(hp, "w", encoding="utf-8").write(doc)
                print("[ok] 榜单.html ->", hp)
            except Exception as ex:
                print(f"[warn] HTML 渲染跳过（不影响榜单）：{type(ex).__name__}: {ex}", file=sys.stderr)
    else:
        print(md)


def render_md(data: dict, summary: dict | None = None) -> str:
    """Markdown 渲染：遍历 board_view.BLOCKS 注册表，缺数据的区块自动不渲染。

    所有展示区块的唯一清单在 board_view.py —— 加区块/改顺序只改那边。
    """
    from modules.board import board_view as BV
    if summary is None:
        summary = _load_summary_sidecar()
    active = BV.active_blocks(data, summary)
    ids = {b["id"] for b in active}
    meta = data.get("meta") or {}
    rows = data.get("main_board") or []
    L = [f"# {meta.get('title', '统一榜单')}", ""]

    if "lede" in ids:
        L += ["> 名次来源：**8维加权打分榜（唯一主榜）**。kWh/万元、电耗等为『参考·不计分』列；"
              "纯产品力榜为并列辅助视角，不改主榜名次。", ""]

    if "fact_coverage" in ids:
        have, lack = BV.fact_coverage(meta, rows)
        if lack:
            L += ["> **事实参考列覆盖**：本榜有数据的是 " + "、".join(have) + "；"
                  "「—」表示未采集到该字段（" + "、".join(lack) + "），不以估计值填充。", ""]

    if "main_board" in ids:
        grades = BV.fact_grades(rows)
        labels = [BV.MAIN_COL_LABELS[k] for k in BV.MAIN_BOARD_COLUMNS]
        L += ["## 打分主榜", "",
              "> 按评分配置逐车评分；指导价/月销/kWh·万元/电池来源为事实列不计分。"
              "▲=本池前25%、▼=后25%；月销 ≤ 为车系合计上界。证据 A=官方碰撞/B=口碑样本够/C=新上市。", "",
              "| " + " | ".join(labels) + " |", "|" + "---|" * len(labels)]
        for r in rows:
            gmap = grades.get(r["model"], {})
            cells = [BV.main_cell(k, r, r, gmap.get("kWh/万元", ""))
                     for k in BV.MAIN_BOARD_COLUMNS]
            L.append("| " + " | ".join(cells) + " |")

    if "score_breakdown" in ids:
        dims = meta.get("dimensions") or []
        L += ["", "## 总分构成（维度明细）", "",
              "> 表头括号是该维权重，格内是原始维分（0–10）；加权分 = Σ(维分×权重) × 10 + adjust。", ""]
        L += ["| # | 车型 | 评分配置 | 指导价(万) | " + " | ".join(BV.dimension_lines(meta)) + " | 加权分 |",
              "|---|---|---|---|" + "---|" * (len(dims) + 1)]
        for r in rows:
            sc = r.get("scores") or {}
            cells = [f"{sc[d['key']]:.1f}" if isinstance(sc.get(d['key']), (int, float)) else "—"
                     for d in dims]
            L.append(f"| {r['rank']} | {r['model']} | {BV.main_cell('trim', r, r)} | "
                     f"{BV.main_cell('price', r, r)} | " + " | ".join(cells) + f" | {r['total']:.1f} |")

    if "tiers" in ids:
        t = (summary or {}).get("tiers") or {}
        if t and all(k in t for k in ("T1", "T2", "T3")):
            names = {"T1": "第一档 · 可直接买", "T2": "第二档 · 按偏好取舍", "T3": "第三档 · 证据不足/暂缓"}
            L += ["", "## 三档分级", ""]
            for k in ("T1", "T2", "T3"):
                models = t.get(k) or []
                L.append(f"- **{names[k]}（{len(models)} 款）**：{'、'.join(models) if models else '—'}")
        else:
            by = {"T1": [], "T2": [], "T3": []}
            for r in rows:
                k = str(r.get("tier") or "").upper()
                if k in by: by[k].append(r["model"])
            L += ["", "## 三档分级", ""]
            for k in ("T1", "T2", "T3"):
                L.append(f"- **{k}（{len(by[k])} 款）**：{'、'.join(by[k]) if by[k] else '—'}")

    if "band_subboards" in ids:
        L += ["", "## 价位段子榜（复用主榜分数，不重新打分）", ""]
        for b in data.get("band_subboards") or []:
            L += [f"### {b['band']}", ""]
            for i, it in enumerate(b.get("items") or [], 1):
                L.append(f"{i}. {it['model']} — {it['score']:.1f} 分（{it['price']}万）")
            L.append("")

    if "segmented_summary" in ids:
        ss = data.get("segmented_summary") or {}
        L += ["", f"## 分段汇总总榜（各段前 {ss.get('per_band', 10)} 名 → 汇总）", "",
              "分数全部来自主榜，不重新打分。", "",
              "| # | 车型 | 加权分 | 价位段 | 段内名次 |", "|---|---|---|---|---|"]
        for it in ss.get("items") or []:
            L.append(f"| {it['summary_rank']} | {it['model']} | {it['score']:.1f} | "
                     f"{it['band']} | {it['rank_in_band']} |")

    if "product_power" in ids:
        pp = data.get("product_power_board") or {}
        L += ["", f"## 纯产品力总榜（辅助榜 · 剔除{pp.get('excluded_dimension','value')} · 不改主榜名次）", "",
              "> rank_delta 正＝性价比拖累，负＝靠性价比抬升。", "",
              "| # | 车型 | 产品力分 | 主榜名次 | rank_delta | 价格 |", "|---|---|---|---|---|---|"]
        for it in pp.get("items") or []:
            d = it.get("rank_delta")
            L.append(f"| {it['power_rank']} | {it['model']} | {it['power_score']:.2f} | "
                     f"#{it['main_rank']} | {f'{d:+d}' if isinstance(d,int) else '—'} | {it.get('price','—')} |")

    if "experience_adjust" in ids:
        ea = meta.get("experience_adjust") or {}
        L += ["", "## 体验证据修正（智驾/操控 · 不碰其它维）", "",
              "> 有证据才调整（来源分级加权、每维封顶 ±1.5），无证据维持硬件口径不插补。", "",
              "| 车型 | 维度 | 前→后 | 调整 | 依据 |", "|---|---|---|---|---|"]
        for model, info in ea.items():
            for dim, det in (info.get("dimensions") or {}).items():
                delta = det.get("delta", 0)
                if not delta:
                    continue
                dlabel = BV.experience_dim_label(dim)
                srcs = det.get("evidence") or []
                ref = "; ".join(
                    f"{s.get('source_type')} {s.get('date')} {s.get('url')}" for s in srcs)
                L.append(f"| {model} | {dlabel} | {det.get('before')}→{det.get('after')} | "
                         f"{delta:+.2f} | {ref} |")

    if "evidence_notes" in ids:
        L += ["", "## 逐车取证依据（点开可复核）", "",
              "> 承接主榜参考列之外的完整事实（指导价/电量/CLTC/月销/800V）。", ""]
        for r in rows:
            note = r.get("note") or "—"
            fr = r.get("fact_ref") or {}
            extras = []
            for k in BV.DETAIL_FACT_FIELDS:     # 折中：主榜移出的字段在这里补齐
                v = fr.get(k)
                if v in (None, "—", ""):
                    continue
                extras.append(f"{k} {v}{'万' if k == '指导价' else ''}")
            L.append(f"- **{r['model']}**（证据 {r.get('evidence','?')}）：{note}"
                     + (f"｜{' · '.join(extras)}" if extras else ""))

    if "lifecycle" in ids:
        lc = (summary or {}).get("boards") or {}
        # summary.boards 可能按分榜聚合；尝试找到生命周期条目
        life = meta.get("lifecycle") or (summary or {}).get("lifecycle") or {}
        if life:
            L += ["", "## 生命周期提示（销量下滑 ≠ 车不行）", ""]
            for model, info in (life.items() if isinstance(life, dict) else []):
                L.append("- " + BV.lifecycle_line(model, info))

    if "dropped" in ids:
        L += ["", "## 落榜与待核名单（落榜 ≠ 车不好）", ""]
        for lab, b in ((summary or {}).get("boards") or {}).items():
            if isinstance(b, dict) and b.get("dropped"):
                L.append(f"- {lab}：过闸 {b.get('passed')} / 落榜 {b.get('dropped')}（见 report.md 逐条原因）")
        if (summary or {}).get("dropped"):
            L.append(f"- 合计落榜 {summary['dropped']} 款")

    if "method_boundary" in ids:
        L += ["", "## 方法与边界", ""] + [f"- {t}" for t in BV.method_boundary_text()]

    if "reproduce" in ids:
        L += ["", "## 复现说明", ""] + [f"- {t}" for t in BV.reproduce_text(meta)]

    if "three_board" in ids:
        tb = meta["three_board"]
        L += ["", "## 三榜并列 · 换视角看名次", "",
              "> 销量序＝市场认不认；加权序＝产品维度综合分；BT 共识序＝10 种算法两两胜负后的"
              "Bradley-Terry 共识（非平均名次）。差值 ≥3 为显著分歧。", "",
              "| # | 车型 | 趋势月销 | 销量序 | 加权序 | BT共识序 | 销量↔加权 |",
              "|---|---|---|---|---|---|---|"]
        for i, r in enumerate(tb["rows"], 1):
            d = r.get("delta")
            ds = f"**{d:+d}**" if isinstance(d, int) and abs(d) >= 3 else (f"{d:+d}" if isinstance(d, int) else "—")
            L.append(f"| {i} | {r['model']} | {r.get('sales','—')} | {r.get('sales_rank','—')} | "
                     f"{r.get('weighted_rank','—')} | {r.get('bt_rank','—')} | {ds} |")
        L += ["", f"三榜前十的三方交集：**{tb.get('overlap')}/10**。",
              "BT 共识前5：" + " → ".join(m for m in tb.get("bt_top", []))]

    if "robustness" in ids:
        rb = meta["robustness"]
        L += ["", "## 稳健性 · 名次到底硬不硬", "",
              f"- 10 种聚合 × {rb.get('n_schemes')} 套权重 = {rb.get('n_runs')} 次排序；"
              f"两两 Kendall τ 均值 **{float(rb.get('kendall_mean') or 0):.3f}**"
              "（0.8≤τ<0.9：大体一致但前几名有方法敏感性）。", ""]
        pa = rb.get("per_alt") or {}
        for m in (rows[:5] if rows else []):
            a = pa.get(m["model"]) or {}
            if a.get("hits") is not None:
                L.append(f"- {m['model']}：{a.get('hits')}/{rb.get('n_methods')} 种算法进前三"
                         f"（{a.get('verdict','—')}）")

    if "audit_boundary" in ids:
        ab = meta["audit_boundary"]
        L += ["", "## 审查边界 · 必须披露的已知缺口", "",
              f"> {ab.get('verdict','')}", ""]
        for title, key in [("🟠 结论边界（修不了）", "boundary"),
                           ("🟡 提示级（须在榜上披露）", "warn"),
                           ("⚪ 说明级", "info")]:
            items = ab.get(key) or []
            if items:
                L += [f"### {title}", ""]
                for it in items:
                    L.append(f"- {it if isinstance(it, str) else json.dumps(it, ensure_ascii=False)}")
                L.append("")

    return "\n".join(L)


def _load_summary_sidecar() -> dict | None:
    """render_md 在没有显式 summary 时，尝试读同目录 summary.json（pipeline 产物）。"""
    # 这里不自动读文件——避免 render_md 变成有副作用的函数；
    # board_v2 CLI 会把 summary 显式传进来。保留钩子供调用方覆盖。
    return None


def run_selftest():
    assert LB is not None, "leaderboard 不可用"
    cfg = _load(str(paths.cat("car_ev_1218_quality.json")))
    pol = _load(str(paths.cat("rank_v2_policy.json")))
    cands = [
        {"model": "廉价车", "cls": "中型SUV", "power": "BEV", "price_wan": 15.0,
         "trend_sales": 5000, "scores": {"powertrain": 7, "safety": 7, "adas": 7, "space": 7,
                                          "value": 9, "control": 7, "cabin": 7, "quality": 7}},
        {"model": "贵价车", "cls": "中型SUV", "power": "BEV", "price_wan": 35.0,
         "trend_sales": 3000, "scores": {"powertrain": 9, "safety": 9, "adas": 9, "space": 9,
                                          "value": 5, "control": 9, "cabin": 9, "quality": 8}},
        {"model": "中价车", "cls": "中型SUV", "power": "BEV", "price_wan": 24.0,
         "trend_sales": 6000, "scores": {"powertrain": 8, "safety": 8, "adas": 8, "space": 8,
                                          "value": 8, "control": 8, "cabin": 8, "quality": 8}},
    ]
    facts = {"廉价车": {"kwh": 80, "consumption": 14, "cltc": 600, "cell_brand": "弗迪", "is_800v": True},
             "贵价车": {"kwh": 100, "consumption": 16, "cltc": 700, "cell_brand": "宁德", "is_800v": False},
             "中价车": {"kwh": 85, "consumption": 13, "cltc": 650, "cell_brand": "宁德", "is_800v": True}}
    data = build(cands, cfg, pol, facts)
    # 1) 主榜按分数降序
    tots = [r["total"] for r in data["main_board"]]
    assert tots == sorted(tots, reverse=True), "主榜未按分数降序"
    # 2) 事实列不计分：改 kwh 不改变名次
    facts2 = json.loads(json.dumps(facts)); facts2["廉价车"]["kwh"] = 999
    d2 = build(cands, cfg, pol, facts2)
    assert [r["model"] for r in d2["main_board"]] == [r["model"] for r in data["main_board"]], \
        "事实列影响了名次（不应计分）"
    # 3) kWh/万元 是参考计算
    cheap_ref = data["main_board"][-1]["fact_ref"]  # 廉价车分最低(性价比高但产品力弱)
    cheap_row = [r for r in data["main_board"] if r["model"] == "廉价车"][0]
    assert cheap_row["fact_ref"]["kWh/万元"] == 5.33, cheap_row["fact_ref"]
    # 4) 多段榜：价位段子榜复用同一份分数
    all_scores = {r["model"]: r["total"] for r in data["main_board"]}
    assert data["band_subboards"], "多段榜应保留价位段子榜"
    assert data["meta"]["band_subboards_skipped"] is False
    for b in data["band_subboards"]:
        for it in b["items"]:
            assert it["score"] == all_scores[it["model"]], "子榜分数与主榜不一致"
    # 5) 贵价车性价比分低但仍进主榜
    assert any(r["model"] == "贵价车" for r in data["main_board"]), "贵价车被硬排除"
    # 6) 分段汇总总榜：每段不超过 per_band、汇总按分数降序、分数来自主榜、各段都有代表
    ss = data["segmented_summary"]
    assert ss["per_band"] == 10
    assert all(b["n_taken"] <= 10 for b in ss["bands"]), "分段取数超过 per_band"
    ss_scores = [it["score"] for it in ss["items"]]
    assert ss_scores == sorted(ss_scores, reverse=True), "分段汇总总榜未按分数降序"
    assert all(it["score"] == all_scores[it["model"]] for it in ss["items"]), "汇总分数与主榜不一致"
    assert len({it["band"] for it in ss["items"]}) == len(ss["bands"]), "有价位段没有代表进总榜"
    assert ss.get("skipped") is False, "多段榜不应跳过分段汇总"
    # 6b) v8.3.6：单价位段（提问已给价位段）时跳过分段汇总，但纯产品力榜仍在
    single = [dict(c, price_wan=15.0) for c in cands[:2]]
    d1 = build(single, cfg, pol, facts)
    ss1 = d1["segmented_summary"]
    assert ss1.get("skipped") is True and ss1["items"] == [], "单段榜应跳过分段汇总"
    # 6c) v8.3.7：单段榜价位段子榜也跳过，纯产品力榜仍在
    assert d1["band_subboards"] == [], "单段榜应跳过价位段子榜"
    assert d1["meta"]["band_subboards_skipped"] is True
    assert d1["product_power_board"]["items"], "单段榜仍应保留纯产品力榜"
    md1 = render_md(d1)
    assert "## 分段汇总总榜" not in md1, "单段无数据，分段汇总整块不应渲染"
    assert "## 价位段子榜" not in md1, "单段无数据，价位段子榜整块不应渲染"
    assert "已跳过" not in md1, "单段不应留「已跳过」说明占版面"
    assert "纯产品力总榜" in md1, "单段榜产品力榜应在"
    # 8) v8.3.9：维度明细从 meta 出，含全部 8 维标签与权重
    assert len(data["meta"]["dimensions"]) == 8, "meta 应带 8 维标签/权重"
    assert "总分构成" in render_md(data), "md 应渲染维度明细"
    assert "三电(18.0%)" in render_md(data), "维度表头应带权重"
    # 7) 纯产品力总榜：降序 / 覆盖全池 / main_score 与主榜一致 / 只改 value 不改产品力分
    pp = data["product_power_board"]
    assert pp["excluded_dimension"] == "value"
    pp_scores = [it["power_score"] for it in pp["items"]]
    assert pp_scores == sorted(pp_scores, reverse=True), "产品力榜未按产品力分降序"
    assert pp["n"] == len(cands)
    assert all(it["main_score"] == all_scores[it["model"]] for it in pp["items"]), \
        "产品力榜携带的主榜分与主榜不一致"
    mid = next(c for c in cands if c["model"] == "中价车")
    mid2 = json.loads(json.dumps(mid)); mid2["model"] = "中价车低性价比"; mid2["scores"]["value"] = 2
    pp2 = product_power_board(main_board([mid, mid2], cfg), [mid, mid2], cfg, pol)
    d_by = {it["model"]: it["power_score"] for it in pp2["items"]}
    assert d_by["中价车"] == d_by["中价车低性价比"], "改 value 却改变了产品力分（性价比未真正剔除）"
    print("[selftest OK] 主榜降序 / 事实列不计分 / 参考列计算 / 子榜复用分数 / 贵车不被硬排除 / 分段汇总总榜 / 纯产品力榜(剔除性价比)")


if __name__ == "__main__":
    cli()
