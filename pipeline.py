#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""榜单全链路（pipeline.py）——一条命令出榜，不经过多模型辩论。

为什么把辩论从链路里去掉：
  实测同一个题目（12–18 万纯电中型车）跑了 5 轮双辩手 + 裁判，辩论产出的是
  「分歧清单」，而分歧清单的每一项都能由数据机械算出来，且辩论本身还引入了三类错误：
    · 越界——裁判把候选池外的车型排进榜（零跑C01），又因「口径存疑」剔除了本在池里的车；
    · 无法裁决——辩手没有检索权，两套销量口径互相矛盾时只能互不相让；
    · 不可复现——同一问题两次跑，名次会变，且无法定位是哪一轮引入的。
  现在这些工作分别交给：robust（方法间一致性）、weightaudit（权重来源）、
  review（确定性质证）。三者都是纯函数，同一输入必得同一输出。

链路：
  取数 → 闸门 → 打分 → 稳健性 → 三档 → 反方审查 → 合成报告

用法：
    python3 pipeline.py --category car_ev --candidates cands.json --out runs/x
    python3 pipeline.py --category car_ev --candidates cands.json --sliders "省心=90"
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import leaderboard as L      # noqa: E402
import ranking               # noqa: E402
import thirdparty            # noqa: E402
import airbags               # noqa: E402
import spec_facts            # noqa: E402
import trims                 # noqa: E402
import forecast as FC        # noqa: E402
import quality_score as QS   # noqa: E402


def parse_sliders(s):
    out = {}
    if not s:
        return None
    for part in s.replace("，", ",").split(","):
        if not part.strip():
            continue
        k, v = (part.split("=", 1) if "=" in part else part.split(":", 1))
        out[k.strip()] = float(v)
    return out


def gate_report(cands, cfg, board=None):
    """跑准入闸门，返回 (通过, 落榜, 报告)。

    gates 可以挂在 board 上（四个分榜的价格带上限不同：16 万 / 18 万），
    优先级 board.gates > cfg.ranking.gates > cfg.gates。
    """
    gates = ((board or {}).get("gates")
             or (cfg.get("ranking") or {}).get("gates")
             or cfg.get("gates") or {})
    if not gates:
        return cands, [], "> 未配置 gates，跳过准入检查。"
    ok, bad = [], []
    for c in cands:
        try:
            r = ranking.apply_gates(c, gates)
        except Exception:
            ok.append(c)
            continue
        (ok if r["pass"] else bad).append({**c, "_gate": r})
    band = gates.get("price_band")
    head = f"## 准入闸门（{len(cands)} → {len(ok)} 款）"
    if band:
        head += f" · 价格带 ≤{band[1]:g} 万"
    lines = [head, ""]
    if bad:
        lines += ["| 落榜 | 未过项 | 说明 |", "|---|---|---|"]
        for c in bad:
            lines.append(f"| {c['model']} | {'、'.join(c['_gate']['failed'])} | "
                         f"{'；'.join(c['_gate']['notes']) or '—'} |")
    else:
        lines.append("全部通过。")
    return ok, bad, "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", required=True)
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--sliders", help='优先级滑杆，如 "省心=90,智驾=20"')
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--forecast", help="未上市预测 JSON（filings+forecast 产物）"
                                       "；给出则在每榜后附「未上市预测区」")
    ap.add_argument("--complaints", help="车质网投诉聚合 JSON（fetch_complaints 产物）"
                                         "；给出则报告附「投诉反馈」参考区")
    ap.add_argument("--quality", action="store_true",
                    help="把质量与品控作为第 8 维进综合打分；可与 --complaints 联用")
    ap.add_argument("--no-thirdparty", action="store_true",
                    help="跳过第三方实测/拆车证据（默认启用，走 adjust 封顶通道）")
    ap.add_argument("--airbag-gate", action="store_true",
                    help="把「气囊基线已核验（前排侧气囊+前后排头部气帘齐备）」"
                         "作为第一档硬门槛；未核验的车不降分、只不进第一档")
    ap.add_argument("--crash-gate", action="store_true",
                    help="把「有官方碰撞成绩（C-NCAP/C-IASI）」作为第一档硬门槛；"
                         "只读 safety_basis，不采信人工填写的 evidence_status")
    ap.add_argument("--no-airbags", action="store_true",
                    help="跳过被动安全气囊事实列与无成绩车受限修正（回测对照用）")
    ap.add_argument("--no-spec-facts", action="store_true",
                    help="跳过官网配置事实节（该节本身不改分，仅作回测对照）")
    ap.add_argument("--no-trims", action="store_true",
                    help="跳过具体配置阶梯与带内配置推荐（默认启用，回测对照用）")
    ap.add_argument("--no-html", action="store_true",
                    help="不随报告生成 HTML 榜单页（默认生成，需 board_v2 链路）")
    ap.add_argument("--as-of", default="—", help="HTML 页面数据月标注，如 202610")
    ap.add_argument("--html-title", help="HTML 页面标题")
    a = ap.parse_args()

    cfg = L.load_category(a.category)
    errs = L.validate(cfg)
    if errs:
        sys.exit("配置未通过校验：" + "；".join(errs))
    os.makedirs(a.out, exist_ok=True)

    data = json.load(open(a.candidates, encoding="utf-8"))
    cands = data.get("candidates", data)

    # 第三方实测/拆车证据 → adjust（带封顶）。放在闸门之前：adjust 不影响准入，
    # 只影响名次；审计行进报告，方便逐条质疑。
    tp_audit = []
    if not a.no_thirdparty:
        cands, tp_audit = thirdparty.apply(cands)
    # 被动安全气囊：事实列 + 仅无官方成绩车的受限 safety 修正。
    # 必须放在 thirdparty.apply 之后、闸门之前：它改的是 scores.safety，
    # 会影响后续所有打分 / 稳健性 / 三档，但不能影响准入。
    if not a.no_airbags:
        cands, _ = airbags.apply(cands)
    # 官网配置事实库：只写 c['spec_facts']，SCORING_ENABLED=False 且有前后
    # scores 深比较断言。放在打分前是为了让报告能拿到全量候选，不改任何分数。
    if not a.no_spec_facts:
        cands, _ = spec_facts.apply(cands)
    # 具体配置阶梯：只写 c['trim_ladder']，SCORING_ENABLED=False 且对
    # scores/total/adjust/price/trim 做前后深比较断言。不改「评分配置」原值。
    if not a.no_trims:
        cands, _ = trims.apply(cands)
    custom = parse_sliders(a.sliders)
    wv = L.build_schemes(cfg, custom).get("用户自定义") if custom else None

    parts = [f"# {cfg.get('label')} · 出榜报告", ""]
    parts.append(f"> 来源：`{os.path.basename(a.candidates)}`；"
                 f"链路 取数→闸门→打分→稳健性→三档→审查。**全程无辩论。**")
    # v9.7.17：候选池与证据质量披露自动生成（不再手工写报告，重跑不丢）
    _all = cands
    _src_named = sum(1 for c in _all if c.get("source_ids"))
    _crash_named = sum(1 for c in _all
                       if c.get("crash") and not any(k in str(c.get("crash")) for k in ("无", "未查到", "—")))
    _ev_cnt = {k: sum(1 for c in _all if c.get("evidence_status") == k) for k in ("A", "B", "C")}
    _ext_n = sum(1 for c in _all if c.get("extended") or c.get("score_evidence"))
    parts.append(
        f"> **候选池与证据披露（自动生成）：**本池 {len(_all)} 款，"
        f"带核验来源 {_src_named}/{len(_all)}，有官方碰撞成绩 {_crash_named}/{len(_all)}，"
        f"证据等级 A/B/C = {_ev_cnt['A']}/{_ev_cnt['B']}/{_ev_cnt['C']}，"
        f"其中 {_ext_n} 款为网查补采（置信低）。"
        "入池口径：官方二级分类 + 纯电 + 指导价；未见来源/无碰撞成绩的车只作参数参考，"
        "不构成安全推荐。**没有官方碰撞成绩不记 0 分、只标缺失，降权≠安全。**")
    parts.append(
        "> **分数性质：**全部分数为「官方参数 + 客观差异规则推算」，**非实车实测**；"
        "冬测续航达成率、充电曲线、麋鹿成绩等第三方统一实测未纳入打分。"
        "档位与名次是两套逻辑，同档内逐名排序参考价值有限。")
    if custom:
        parts.append(f"> 用户优先级："
                     + " / ".join(f"{k} {int(v)}" for k, v in custom.items()))
    if tp_audit:
        parts.append(thirdparty.audit_report(tp_audit))
        parts.append("")
    fdata = None
    if a.forecast and os.path.exists(a.forecast):
        fdata = json.load(open(a.forecast, encoding="utf-8"))
        parts.append(f"> 未上市预测：第{fdata.get('batch')}批工信部备案"
                     f"（{fdata.get('date')}）+ 新闻口径，独立分区不计入正式名次。")
        parts.append("")
    cdata = None
    if a.complaints and os.path.exists(a.complaints):
        cdata = json.load(open(a.complaints, encoding="utf-8"))
        parts.append(f"> 投诉反馈：车质网缓存窗口 {cdata.get('window_days')} 天"
                     f"（覆盖 {', '.join(cdata.get('cache_cover') or [])}），"
                     "密度=投诉/万台，仅参考不改档。")
        parts.append("")

    # 1 逐榜：分榜 → 闸门 → 三档 → 审查 → 稳健性 → 权重审计 → 三榜并列
    #    顺序是「先分榜再闸门」，因为四个榜的价格带上限不同（16 万 / 18 万），
    #    gates 必须按榜取，不能全局一刀切。
    summary = {
        "category": a.category, "source": os.path.basename(a.candidates),
        "in": len(cands), "passed": 0, "dropped": 0, "sliders": custom,
        "boards": {}, "tiers": {}, "review": {},
        # v9.7.20：候选池披露同步给页面（评审反复指出报告有、页面看不到）
        "pool_disclosure": {
            "total": len(_all), "sourced": _src_named, "crash": _crash_named,
            "ev_a": _ev_cnt["A"], "ev_b": _ev_cnt["B"], "ev_c": _ev_cnt["C"],
            "extended": _ext_n,
        },
    }
    if a.quality:
        cands, _ = QS.apply(cands, cdata)
        parts += ["---", "", QS.report(cands), ""]
        summary["quality"] = QS.coverage(cands)
    for board in L.board_items(cfg):
        label = board.get("label") or "全部"
        sub = L.filter_board(cands, board)
        if not sub:
            continue
        passed, dropped, greport = gate_report(sub, cfg, board)
        parts += ["---", "", f"# {label}", "",
                  f"> 候选 {len(sub)} 款 → 过闸 {len(passed)} 款", "", greport, ""]
        summary["boards"][label] = {"in": len(sub), "passed": len(passed),
                                    "dropped": len(dropped)}
        summary["passed"] += len(passed)
        summary["dropped"] += len(dropped)
        if not passed:
            parts += ["> **本榜无车通过闸门**：该价格带内没有同时满足"
                      "「官方级别 + 纯电 + 指导价 ≤ 上限」的车型。", ""]
            summary["tiers"][label] = {"T1": [], "T2": [], "T3": []}
            summary["review"][label] = "无车过闸"
            continue
        _band = ((board.get("gates") or {}).get("price_band") or [None, None])[1]
        parts.append(L.top_table(passed, cfg, wv, 10, band=_band)["report"])
        parts.append("")
        _tier = L.tier_report(passed, cfg, wv, label, airbag_gate=a.airbag_gate, crash_gate=a.crash_gate)
        parts.append(_tier["report"])
        parts.append("")
        # v9.7.18：证据 C 软提示（只降第一档资格、不判劣）——写入 summary 供页面展示
        _soft = []
        for _tk in ("T1", "T2", "T3"):
            for _rec in (_tier.get("detail") or {}).get(_tk, []):
                if _rec.get("evidence_soft"):
                    _soft.append({"model": _rec.get("model"), "tier": _tk,
                                  "reason": "；".join(_rec["evidence_soft"])})
        summary.setdefault("evidence_soft", []).extend(_soft)
        parts.append(L.review_report(passed, cfg, wv)["report"])
        parts.append("")
        _rob = L.robust_report(passed, cfg, custom)
        parts.append(_rob["report"])
        # v9.7.16：把 τ 稳健性指标写进 summary，供页面顶部显示（只展示不改分）
        summary["stability"] = {
            "kendall_mean": round(float(_rob.get("kendall_mean") or 0), 3),
            "n_methods": _rob.get("n_methods"), "n_schemes": _rob.get("n_schemes"),
            "note": "10种MCDA×多套权重两两Kendall τ均值；<0.8逐名排序不可靠，≥0.9较稳定",
        }
        parts.append("")
        parts.append(L.weight_audit(passed, cfg, wv)["report"])
        parts.append("")
        parts.append(L.tri_report(L.score_rows(passed, cfg, wv), cfg, passed, 10, wv)["report"])
        parts.append("")
        if fdata:
            fitems = (fdata.get("boards") or {}).get(label) or []
            parts += FC.render_board(label, fitems, len(passed))
        summary["tiers"][label] = L.tier_report(passed, cfg, wv, label, airbag_gate=a.airbag_gate, crash_gate=a.crash_gate)["tiers"]
        summary["review"][label] = L.review_report(passed, cfg, wv)["verdict"]

    # 1b 被动安全配置事实列（全局一节，逐条带来源；不计入加权）
    if not a.no_airbags:
        parts += ["---", "", airbags.report(cands), ""]
        cov = airbags.coverage(cands)
        summary["airbags"] = {
            "verified": cov["verified"], "total": cov["total"],
            "coverage_pct": cov["coverage_pct"],
            "adjusted": cov["adjusted"], "todo_n": len(cov["todo"]),
        }

    # 1c 细分维度决策表 + 官网配置事实（全局一节，不计入加权）
    if not a.no_spec_facts:
        parts += ["---", "", spec_facts.summary_md(cands), ""]
        fcov = spec_facts.coverage(cands)
        gates = spec_facts.admission(cands)
        summary["spec_facts"] = {
            "scoring_enabled": spec_facts.SCORING_ENABLED,
            "verified": fcov["verified"], "total": fcov["total"],
            "coverage_pct": fcov["coverage_pct"], "todo_n": len(fcov["todo"]),
            "dimensions": len(gates),
            "admitted_new_dims": [r["key"] for r in gates
                                  if r["needs_gate"] and r["admitted"]],
            "blocked_dims": [r["key"] for r in gates
                             if r["needs_gate"] and not r["admitted"]],
        }

    # 1d 具体配置阶梯与带内配置推荐（全局一节；完整逐配置表另写 trims.md）
    if not a.no_trims:
        parts += ["---", "", trims.summary_md(cands), ""]
        tcov = trims.coverage(cands)
        summary["trims"] = {
            "scoring_enabled": trims.SCORING_ENABLED,
            "verified": tcov["verified"], "total": tcov["total"],
            "coverage_pct": tcov["coverage_pct"], "todo_n": len(tcov["todo"]),
            "jumps": tcov["jumps"],
            "actionable_models": tcov["actionable_models"],
            "upsell_models": tcov["upsell_models"],
            "price_mismatch_models": tcov["price_mismatch_models"],
        }
        open(os.path.join(a.out, "trims.md"), "w", encoding="utf-8").write(
            trims.report(cands) + "\n")

    # 预测摘要进 summary.json（机读，供月度对比与回测）
    if fdata:
        summary["forecast"] = {
            "batch": fdata.get("batch"), "date": fdata.get("date"),
            "boards": {k: [{"name": r["name"], "p_top10": r["p_top10"],
                            "p_t1": r["p_t1"], "rank": r["rank"]}
                           for r in v]
                       for k, v in (fdata.get("boards") or {}).items()},
        }

    # 投诉反馈区（L2 证据，不改档）：放报告尾部，逐榜列出
    if cdata:
        parts += ["---", "", "# 投诉反馈（车质网 · 参考证据）", "",
                  f"> 窗口 {cdata.get('window_days')} 天｜缓存覆盖 "
                  f"{', '.join(cdata.get('cache_cover') or [])}｜"
                  "密度=投诉/万台。**投诉量没有分母就没有意义**；样本 <5 的车型"
                  "不比较。此区不改三档，只提示「该看哪些车的哪些问题」。", ""]
        models = cdata.get("models") or {}
        rows = [(m, v) for m, v in models.items() if (v.get("count") or 0) >= 5]
        rows.sort(key=lambda kv: -(kv[1].get("per_10k") or 0))
        if rows:
            parts += ["| 车型 | 投诉 | 密度(/万台) | 高频问题 |", "|---|---|---|---|"]
            for m, v in rows:
                tags = "、".join(f"{t}×{n}" for t, n in v.get("top_tags") or []) or "—"
                pk = v.get("per_10k")
                parts.append(f"| {m} | {v['count']} | "
                             f"{pk if pk is not None else '销量缺'} | {tags} |")
            parts.append("")
        # summary 机读
        summary["complaints"] = {
            "window_days": cdata.get("window_days"),
            "cover": cdata.get("cache_cover"),
            "models": {m: {"count": v.get("count"), "per_10k": v.get("per_10k"),
                           "top_tags": v.get("top_tags")}
                       for m, v in models.items()},
        }

    rp = os.path.join(a.out, "report.md")
    open(rp, "w", encoding="utf-8").write("\n".join(parts) + "\n")
    sp = os.path.join(a.out, "summary.json")
    json.dump(summary, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[ok] {rp}")
    print(f"[ok] {sp}")
    for k, v in summary["review"].items():
        print(f"  {k}: {v}")

    # v8.5: 自动出可视化 HTML。用同一份候选 + 同一配置重建 board_v2 数据，
    # 事实列不计分、分段/产品力复用主榜分数——与 board_v2 单独跑完全一致。
    if not a.no_html:
        try:
            import board_v2 as BV
            import render_board_html as RH
            import board_view as BView
            _here = os.path.dirname(os.path.abspath(__file__))
            bv_cfg_path = os.path.join(_here, "categories", "car_ev_1218_quality.json")
            policy_path = os.path.join(_here, "categories", "rank_v2_policy.json")
            if os.path.exists(bv_cfg_path) and os.path.exists(policy_path):
                cfg_v2 = json.load(open(bv_cfg_path, encoding="utf-8"))
                pol = json.load(open(policy_path, encoding="utf-8"))
                facts_v2 = BView.load_fact_library()   # v9.0.1 默认带事实库
                _tiers_v2 = {}
                for tmap in (summary.get("tiers") or {}).values():
                    for tk, label in (("T1", "第一档"), ("T2", "第二档"), ("T3", "第三档")):
                        for nm in (tmap.get(tk) or []):
                            _tiers_v2[nm] = label
                # v9.7.10：页面与报告一致——只把通过闸门的候选（有档位的）传给渲染，
                # 否则被生命周期闸门淘汰的停产车（如秦L EV）会出现在主榜却没档位。
                _passed = set(_tiers_v2.keys())
                cands_for_html = [c for c in cands if c.get("model") in _passed] if _passed else cands
                data_v2 = BV.build(cands_for_html, cfg_v2, pol, facts_v2, tiers=_tiers_v2)
                doc = RH.render(data_v2, summary, a.html_title, a.as_of)
                hp = os.path.join(a.out, "榜单.html")
                open(hp, "w", encoding="utf-8").write(doc)
                print(f"[ok] {hp}")
        except Exception as ex:
            print(f"[warn] HTML 渲染跳过（不影响榜单）：{type(ex).__name__}: {ex}", file=sys.stderr)


if __name__ == "__main__":
    main()
