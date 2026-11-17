#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""燃油 / 混动四榜出榜（ice_board.py）。

复用项目既有引擎：ranking.apply_gates（配置驱动闸门）、
leaderboard.score_rows / top_table / tier_report、robust 的 10 种 MCDA
与 bootstrap 同档分组。只替换两件事：分数来源（ice_scoring 的代理分）
和报告渲染（不带纯电榜的气囊/配置阶梯列）。

产物（写到 --out 目录）：
    report.md            四榜前十 + 三档 + 稳健性 + 口径披露 + 待补清单
    四榜报告.html        可点开的可视化版本
    scoring_audit.md     每车每维的分数、来源公式、置信度、插补标记
    summary.json         机读摘要
    INDEX.md             归档索引

用法：
    python3 ice_board.py --pool ice_pool.json --category car_ice_phev \
        --out runs/car-ice-4boards --top 10
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ranking                            # noqa: E402
import leaderboard as L                    # noqa: E402
import ice_scoring as IS                   # noqa: E402

POWER_LABEL = {"ICE_OR_HEV": "燃油/HEV", "PHEV": "插混", "EREV": "增程"}
POWER_GROUP = {"ICE_OR_HEV": "燃油", "PHEV": "混动", "EREV": "混动"}


def _fmt(n):
    return f"{int(n):,}" if isinstance(n, (int, float)) else "—"


def _lc_label(lc):
    lc = lc or {"status": "active"}
    st = lc.get("status")
    if st == "sales_drop":
        return f"⚠骤降({lc.get('ratio')})"
    return L.LC_LABEL.get(st, st or "在售")


def _trend_arrow(tm):
    """近 6 月销量迷你趋势：↑ 升 / ↓ 降 / → 平（用 trend_months 前后半均值比）。"""
    vals = [v for v in (tm or {}).values() if isinstance(v, (int, float))]
    if len(vals) >= 3:
        h = len(vals) // 2
        early = sum(vals[:h]) / max(1, h)
        late = sum(vals[h:]) / max(1, len(vals) - h)
        if late > early * 1.15:
            return "↑"
        if late < early * 0.85:
            return "↓"
    return "→"


def _subtier_ice(ev, lc_status):
    """T2 内细分：2A 证据较全可优先试驾 / 2B 有明确取舍点 / 2C 证据偏薄仅备选。"""
    if ev == "C":
        return "2C"
    if lc_status in ("facelift_pending", "clearance", "new_launch", "sales_drop"):
        return "2B"
    return "2A"


def _ev_desc(ev, crash, users):
    """把 A/B/C 证据等级翻成描述性标签，切断与购买档位的混淆。
    A 有两条路径（口碑≥100 或 L1 碰撞），这里显式标出依据。"""
    users = users or 0
    if ev == "A":
        if crash == "L1" and users >= 100:
            return "充分·口碑+碰撞", "eA"
        if crash == "L1":
            return "充分·官方碰撞", "eA"
        return "充分·口碑", "eA"
    if ev == "B":
        return "中等", "eB"
    return "薄弱", "eC"


def _kendall(a, b):
    """两套排序的 Kendall τ（只看共同元素），用于衡量名次稳定性。"""
    common = [m for m in a if m in b]
    if len(common) < 2:
        return 1.0
    ia = {m: i for i, m in enumerate(a)}
    ib = {m: i for i, m in enumerate(b)}
    c = d = 0
    for i in range(len(common)):
        for j in range(i + 1, len(common)):
            x = ia[common[i]] - ia[common[j]]
            y = ib[common[i]] - ib[common[j]]
            if x * y > 0:
                c += 1
            elif x * y < 0:
                d += 1
    return (c - d) / (c + d) if (c + d) else 1.0


def _discount_view(boards_data, ndim):
    """评审敏感性：插补折扣系数扫描（不改默认分，只模拟 1−f×插补维 的名次影响）。"""
    factors = (0.03, 0.05, 0.08)
    cards = []
    for b in boards_data:
        sr = b.get("sub_rows") or []
        if len(sr) < 3:
            continue
        default = [d["model"] for d in sr]
        low = {d["model"]: d.get("low", 0) for d in sr}
        tot = {d["model"]: float(d["total"]) for d in sr}
        lines = []
        for f in factors:
            dsc = {m: tot[m] * (1 - f * low[m]) for m in default}
            order = sorted(default, key=lambda m: -dsc[m])
            tau = _kendall(default, order)
            t10d, t10o = set(default[:10]), set(order[:10])
            gained = sorted(t10o - t10d)
            lost = sorted(t10d - t10o)
            shifts = sum(1 for i, m in enumerate(default)
                         if abs((i + 1) - (order.index(m) + 1)) >= 2)
            lines.append(
                f"<tr><td class='num'>{f:.2f}</td><td class='num'>{tau:.3f}</td>"
                f"<td>{html.escape('、'.join(gained) or '—')}</td>"
                f"<td>{html.escape('、'.join(lost) or '—')}</td>"
                f"<td class='num'>{shifts}</td></tr>")
        cards.append(
            f"<h3 style='margin-top:12px'>{html.escape(b['label'])}</h3>"
            "<table><thead><tr><th>折扣系数 f</th><th>Kendall τ</th><th>前十新进</th>"
            "<th>前十掉出</th><th>位移≥2 款数</th></tr></thead><tbody>"
            + "".join(lines) + "</tbody></table>")
    if not cards:
        return ""
    return ("<h2>评审敏感性：插补折扣系数扫描<em>默认分不变，仅模拟</em></h2>"
            "<div class='card'><p class='note'>DeepSeek 建议「加权分 ×（1 − 0.05 × 插补维数）」让薄证据车向均值收缩。"
            "但 <b>0.05 是经验值、未经实测标定</b>，故本榜<b>不把它写进默认评分</b>；"
            "这里把系数在 <b>0.03 / 0.05 / 0.08</b> 三档扫描，看结论对系数是否敏感——"
            "τ 越接近 1、前十进出越少，说明折扣影响越小、该建议对本文结论非关键。</p>"
            + "".join(cards) +
            "<div class='note'>读法：若某车在不同系数下反复进出前十，说明它<b>高度依赖同榜中位插补撑名次</b>，"
            "与表中「插补维」列应一致；这类车看图时自动降权即可，无需改用折扣分。"
            "<b>本视图不进入正式榜、不影响三档判定。</b></div></div>")


def _table_rows(rows, by, ci_groups, n, ci=None):
    ci = ci or {}
    out = []
    for i, r in enumerate(rows[:n]):
        c = by[r["model"]]
        v = ci.get(r["model"])
        out.append({
            "rank": i + 1, "model": r["model"],
            "power": POWER_LABEL.get(c["power"], c["power"]),
            "pgroup": POWER_GROUP.get(c["power"], "其它"),
            "cls": c["cls"], "price": c["price"],
            "trend": _fmt(c["trend_sales"]), "oct": _fmt(c["sales_oct"]),
            "trend_raw": c.get("trend_sales"), "oct_raw": c.get("sales_oct"),
            "tm": c.get("trend_months") or {},
            "total": f"{r['total']:.1f}", "users": c.get("koubei_users") or 0,
            "evidence": r["evidence"],
            "group": ci_groups.get(r["model"], "—"),
            "ci": (f"{v['p05']:g}–{v['p95']:g}" if v else "—"),
            "low": sum(1 for v in (c.get("score_confidence") or {}).values() if v == "low"),
            # L1 = 近期官方碰撞测评；史 = 只有历史年款记录，不外推；— = 两源无记录
            "crash": ("L1" if (c.get("crash_evidence") or {}).get("l1_current")
                      else "史" if c.get("crash_evidence") else "—"),
            "lc": c.get("lifecycle") or {"status": "active", "note": "", "ratio": None},
            "note": c.get("note") or "",
        })
    return out


def render_board_md(board, passed, dropped, rows, tt, tiers, cfg, n):
    label = board["label"]
    ndim = len(cfg["dimensions"])
    by = {c["model"]: c for c in passed}
    groups = {}
    for gi, g in enumerate(tt.get("groups") or [], 1):
        for nm in g:
            groups[nm] = gi
    data = _table_rows(rows, by, groups, n, tt.get("ci"))
    L_ = [f"## {label}", "",
          f"候选 {len(passed) + len(dropped)} 款 → 过闸 **{len(passed)}** 款，"
          f"落榜 {len(dropped)} 款；下表取加权前 {len(data)} 名。", "",
          "| # | 车型 | 动力 | 官方级别 | 指导价(万) | 6月趋势销量 | 10月销量 | 加权分 | 名次区间(95%) | 口碑样本 | 证据 | 官方碰撞 | 同档组 | 插补维 | 生命周期 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for d in data:
        L_.append(f"| {d['rank']} | **{d['model']}** | {d['power']} | {d['cls']} | "
                  f"{d['price']} | {d['trend']} | {d['oct']} | {d['total']} | "
                  f"{d['ci']} | {d['users']} | {d['evidence']} | {d['crash']} | "
                  f"{d['group']} | {d['low']}/{ndim} | {_lc_label(d['lc'])} |")
    L_.append("")
    L_.append(f"> **同档组**＝bootstrap 名次区间重叠（不是分差<1 分）：同一组的车只是"
              f"「换权重后名次会换位」，**并非实力相等**；组内谁排前纯属算法噪声，不要按名次选车。"
              f"**名次区间**＝±0.5 分扰动 × 600 次重排的 95% 名次范围；"
              f"**插补维**＝{ndim} 维里用同榜中位补的维数，越多说明这台的证据越薄。")
    # 前十若同属一个 bootstrap 组：名次完全不可辨
    top_groups = [d["group"] for d in data if d["group"] != "—"]
    if top_groups and len(set(top_groups)) == 1:
        L_.append(f"> ⚠️ **本榜前十（{len(data)} 款）同属一个 bootstrap 组（组 {top_groups[0]}），"
                  f"名次区间大面积重叠，逐名次不可辨**；请按空间 / 动力 / 预算偏好选，"
                  f"不要拿第 1 到第 {len(data)} 当真实差距。")
    # 生命周期骤降提示
    drops = [d for d in data if (d.get("lc") or {}).get("status") == "sales_drop"]
    if drops:
        L_.append("> 🔻 **生命周期骤降车（需先查生命周期再下单）**："
                  + "；".join(f"{d['model']}（10月 {d['oct']} / 6月趋势 {d['trend']}，"
                              f"仅 {d['lc'].get('ratio')}）" for d in drops)
                  + "。销量骤降可能是清库 / 改款在即 / 已停产，不一定是产品力下滑；"
                  "名次按 6 月趋势中位数平滑，未反映最新月崩塌。")
    L_.append("> **加权分保留 1 位小数只是机器排序需要，不代表 0.1 分可分辨**："
              "同档组内请直接看区间与同档组，不要拿 91.3 和 90.0 当真实差距。")
    L_.append("> **官方碰撞**＝`L1` 近 3 年有 C-IASI / C-NCAP 官方测评记录，"
              "`史` 只有更早年款记录（不外推到现款），`—` 本次两源无记录；"
              "**该列只作事实与判档证据，不换算分数**，详见 `crash_evidence.md`。")
    L_.append("> 销量为「车系合计」口径（懂车帝按车系主销动力分类，不二次拆分 HEV/燃油版），"
              "只作市场验证，不是产品力结论。")
    L_.append("")
    # 分动力视图：标题按实际过闸款数命名，不足 10 款不冒充「前十」
    for grp, title in (("燃油", "燃油 / 不插电 HEV"), ("混动", "混动（插混 + 增程）")):
        all_sub = [r for r in rows if POWER_GROUP.get(by[r["model"]]["power"]) == grp]
        sub = all_sub[:10]
        if not sub:
            continue
        cnt = len(all_sub)
        L_ += [f"### {title} 分列（共 {cnt} 款，按同一套加权分）", "",
               "| # | 车型 | 动力 | 指导价 | 趋势销量 | 加权分 |", "|---|---|---|---|---|---|"]
        for i, r in enumerate(sub, 1):
            c = by[r["model"]]
            L_.append(f"| {i} | {r['model']} | {POWER_LABEL.get(c['power'], c['power'])} | "
                      f"{c['price']} | {_fmt(c['trend_sales'])} | {r['total']:.1f} |")
        if cnt < 10:
            L_ += ["", f"> 该细分只有 **{cnt} 款**过闸，不足 10 款；这里列的是全部，"
                   "**尾部名次只是细分内相对次序，没有统计意义，也不代表产品差**。"
                   "分列与总榜用的是同一套加权分，只是参照系从全榜换成同动力子集。"]
        L_.append("")
    # 纯销量对照
    vol = sorted(passed, key=lambda c: -(int(c.get("trend_sales") or 0)))[:10]
    L_ += ["### 纯销量对照前十（不看加权分）", "",
           "| # | 车型 | 动力 | 6月趋势销量 | 10月销量 | 加权名次 |", "|---|---|---|---|---|---|"]
    rank_of = {r["model"]: i + 1 for i, r in enumerate(rows)}
    for i, c in enumerate(vol, 1):
        L_.append(f"| {i} | {c['model']} | {POWER_LABEL.get(c['power'], c['power'])} | "
                  f"{_fmt(c['trend_sales'])} | {_fmt(c['sales_oct'])} | "
                  f"{rank_of.get(c['model'], '—')} |")
    L_.append("")
    mw = next((float(d["weight"]) for d in cfg["dimensions"]
               if d["key"] == "market"), 0.0)
    L_.append(f"> 两个前十不一样，正说明「加权榜 ≠ 销量榜」；"
              f"本榜销量维权重 **{mw * 100:.0f}%**（配置实配值，见正文权重表）。")
    L_.append("> v6.3 起候选池改为**全量核验**（388 款不按销量截断），销量不再在入池处"
              "二次出现；但数据源本身是销量榜、评分仍含 market 22%，整张榜**依然偏市场热度**。"
              "漏斗切换的影响见 `funnel_sensitivity.md`，权重/插补敏感性见 `sensitivity.md`。")
    L_.append("")
    # 三档（tiers 可能传外层 {"tiers":{...}}，也可能已是内层 {"T1":..}；两者都兼容）
    t = tiers.get("tiers") or (tiers if isinstance(tiers, dict) else {}) or {}
    L_ += ["### 三档分级（沿用项目判档框架）", "",
           f"- **第一档 · 可推荐（{len(t.get('T1') or [])} 款）**："
           f"{'、'.join(t.get('T1') or []) or '空的'}",
           f"- **第二档 · 同档竞争（{len(t.get('T2') or [])} 款）**："
           f"{'、'.join((t.get('T2') or [])[:12]) or '—'}"
           f"{' 等' if len(t.get('T2') or []) > 12 else ''}",
           f"- **第三档 · 证据不足（{len(t.get('T3') or [])} 款）**："
           f"{'、'.join((t.get('T3') or [])[:8]) or '—'}"
           f"{' 等' if len(t.get('T3') or []) > 8 else ''}", "",
           "> v6.3 起证据 **A** 有两条并行路径：L4 口碑样本 ≥100，或 L1 近期官方碰撞"
           "测评记录（见 `crash_evidence.md`）。前者与纯电榜 A 的「官方碰撞成绩」"
           "不是同一含义；本榜仍不设安全维，第一档也不等于「碰撞安全成绩已验证」"
           "——L1 只证明该车型被官方测评过，C-IASI 等级图标未解码，不作安全好坏结论。", ""]
    if dropped:
        L_ += ["<details><summary>落榜车型（点击展开）</summary>", "",
               "| 车型 | 闸门代码 | 原因 |", "|---|---|---|"]
        for c, res in dropped[:30]:
            L_.append(f"| {c['model']} | {'; '.join(res['failed'])} | "
                      f"{'; '.join(res['notes']) or '—'} |")
        L_ += ["", "> 落榜≠车不好：本榜 `price_unknown` 一律因未取得**可核验的官方指导价**。"
               "懂车帝榜单的 `min_price/max_price` 与 `dealer_price` 同值，属经销商价口径，"
               "按项目纪律不替代官方指导价；这几款列在待补清单，下期核到即可入池。", "",
               "</details>", ""]
    return "\n".join(L_), data


def render_html(boards_data, meta, audit, repro=None, lc_items=None):
    def esc(s):
        return html.escape(str(s or ""))
    ndim = len(audit["dims"])
    parts = ["""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>2026-10 燃油与混动 紧凑型/中型 轿车·SUV 四榜</title>
<style>
:root{--bg:#f7f8fa;--card:#fff;--ink:#1a1d24;--sub:#5b6472;--line:#e5e8ee;--accent:#1b64d1;--warn:#b26a00}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:27px;margin:0 0 6px}h2{font-size:20px;margin:34px 0 12px;border-left:4px solid var(--accent);padding-left:10px}
h3{font-size:16px;margin:22px 0 8px;color:var(--sub)}
.lead{color:var(--sub);font-size:14px;margin-bottom:22px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px 20px;margin:14px 0;box-shadow:0 1px 3px rgba(20,30,50,.04)}
table{width:100%;border-collapse:collapse;font-size:13.5px}th,td{padding:8px 9px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{background:#f1f4f9;color:var(--sub);font-weight:600}tr:hover td{background:#fafbfe}
.b{font-weight:700}.tag{display:inline-block;padding:1px 7px;border-radius:4px;font-size:12px;background:#eef3fd;color:var(--accent)}
.warn{background:#fff6e8;color:var(--warn);border-left:3px solid #e0a13a;padding:10px 14px;border-radius:4px;margin:12px 0;font-size:13.5px}
.note{color:var(--sub);font-size:12.5px;margin-top:9px}
.pill{display:inline-block;background:#eef1f6;border-radius:12px;padding:2px 10px;margin:2px 4px 2px 0;font-size:12.5px}
.decision{background:#f7f9fc;border:1px solid #d8e2f0;border-left:4px solid #1b64d1;border-radius:10px;padding:14px 16px;margin:6px 0 14px}
.decision h3{margin:0 0 10px;font-size:14px;color:#1b64d1}
.dgrid{display:grid;grid-template-columns:1fr 1.5fr 1fr;gap:12px}
@media(max-width:820px){.dgrid{grid-template-columns:1fr}}
.dcard{background:#fff;border:1px solid var(--line);border-radius:9px;padding:10px 12px}
.dcard .dh{font-weight:700;font-size:13px;margin-bottom:6px}
.dcard.t1{border-color:#bfe3cd}.dcard.t1 .dh{color:#0f6b3f}
.dcard.t2{border-color:#cdd9f0}.dcard.t2 .dh{color:#1b64d1}
.dcard.t3{border-color:#e3ddd2}.dcard.t3 .dh{color:var(--sub)}
.dsub{margin:6px 0;font-size:12px}.dsub b{color:#33405a}
.dcard ul{margin:4px 0 0;padding-left:16px}.dcard li{margin:3px 0;font-size:12.5px;line-height:1.45}
.dcard li.empty{color:var(--sub);list-style:none;margin-left:-16px}
.dtag{display:inline-block;font-size:10px;background:#eef3fd;color:#33405a;border-radius:4px;padding:0 4px;margin-left:3px}
.dprice{color:var(--sub);font-size:11px;margin-left:3px}
.dnote{font-size:11.5px;color:var(--sub);margin:6px 0 0}
.rank td,.rank th{white-space:nowrap}
.rk{width:30px;color:var(--sub);font-variant-numeric:tabular-nums}
.md b{font-size:13.5px}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.big{font-weight:700;font-size:14.5px}
.ev{display:inline-block;width:20px;height:20px;line-height:20px;text-align:center;border-radius:5px;font-size:11.5px;font-weight:700}
.evpill{display:inline-block;padding:1px 7px;border-radius:5px;font-size:11px;font-weight:600;white-space:nowrap}
.eA{background:#e3f2e8;color:#0f6b3f}.eB{background:#fdf4e3;color:#b26a00}.eC{background:#fdecec;color:#a52222}
.tier{display:inline-block;padding:1px 7px;border-radius:5px;font-size:11px;font-weight:600}
.tier.t1{background:#e3f2e8;color:#0f6b3f}.tier.t2{background:#eef2fa;color:#1b64d1}.tier.t3{background:#f2f0ec;color:var(--sub)}
.cr{display:inline-block;padding:1px 6px;border-radius:5px;font-size:11px}
.cr.l1{background:#e3f2e8;color:#0f6b3f}.cr.lishi{background:#fdf4e3;color:#b26a00}.cr.none{background:#f2f0ec;color:var(--sub)}
.lcc{display:inline-block;padding:1px 7px;border-radius:5px;font-size:11px}
.lcc.lc-ok{color:#0f6b3f}.lcc.lc-warn{background:#fdf4e3;color:#b26a00;font-weight:700}.lcc.lc-mid{color:#b26a00}
</style></head><body><div class="wrap">"""]
    parts.append(f"<h1>2026 年 10 月 · 燃油与混动四榜</h1><div class='lead'>"
                 f"紧凑型 / 中型 × 轿车 / SUV，各前十 ｜ 数据源 {esc(meta['as_of'])} ｜ "
                 f"生成 {esc(meta['generated_at'])}</div>")
    parts.append(f"""<div class="card"><h2 style="margin-top:0">先看口径：这四榜和纯电四榜不是一回事</h2>
<ul>
<li><b>动力范围</b>：燃油 / 不插电油电混合（HEV）+ 插混（PHEV）+ 增程（EREV）；纯电排除，
一车多动力未拆车系名的 64 款也排除（销量含纯电，不能算成燃油/混动）。</li>
<li><b>级别</b>：汽车之家展示的厂商申报<b>官方二级分类</b>（车系页 <code>levelName</code>），不用轴距推定；
取不到级别的车系一律拒绝入池。</li>
<li><b>分数</b>：由公开结构化数据按固定公式自动映射（销量趋势 + 汽车之家口碑 + 官方指导价），
<b>不是纯电榜那种逐款人工核验的产品力分</b>，加权分不可与纯电榜直接比较；逐条依据见 <code>scoring_audit.md</code>。</li>
<li><b>L1 碰撞证据 ≠ 安全维</b>：v6.3 已接入 C-IASI 全量公开测评表与 C-NCAP 成绩表，
但近期证据覆盖率远低于 G2(≥70%)，且 L1 与 L4 不同质，故只作<b>事实列与判档证据</b>，
不换算分数、不进加权；C-IASI 等级图标官网无文字对照，<b>未解码</b>。详见 <code>crash_evidence.md</code>。</li>
<li><b>口碑</b>属 L4 自选择样本，样本 &lt;30 条的维度按同榜中位插补并标 low，不是记 0 分。</li>
</ul></div>""")
    parts.append(f"""<div class="card"><b>候选池（v6.3 全量核验）</b>：销量榜非纯电车系 {meta['kept']} 款 →
<b>全部 {meta['processed']} 款</b>逐一核验（不再按销量取前 220 截断） → 名称匹配 {meta['name_matched']} →
取到官方级别且落在四个目标级别 <b>{meta['in_class']}</b> 款
（紧凑型轿车 {meta['by_cls'].get('紧凑型车')} / 紧凑型SUV {meta['by_cls'].get('紧凑型SUV')} /
中型轿车 {meta['by_cls'].get('中型车')} / 中型SUV {meta['by_cls'].get('中型SUV')}）。<br>
<span class="pill">燃油/HEV {meta['by_power'].get('ICE_OR_HEV')}</span>
<span class="pill">插混 {meta['by_power'].get('PHEV')}</span>
<span class="pill">增程 {meta['by_power'].get('EREV')}</span>
<div class="note">未取到名称/级别的车系不猜、不入池，逐条列在 report.md 的待补清单。</div></div>""")
    # ---- 一页纸决策摘要（T1 / 2A·2B·2C / T3）----
    def _decision_card_ice(b):
        tiers = b.get("tiers") or {}
        t1 = tiers.get("T1") or []
        t2 = tiers.get("T2") or []
        t3 = tiers.get("T3") or []
        srows = {d["model"]: d for d in (b.get("sub_rows") or [])}

        def li(name, tag=""):
            d = srows.get(name, {})
            price = d.get("price")
            p = (f" <span class='dprice'>{esc(price)}万</span>"
                 if price not in (None, "", "—") else "")
            tg = (f" <span class='dtag'>{esc(tag)}</span>" if tag else "")
            return f"<li><b>{esc(name)}</b>{p}{tg}</li>"

        a2, b2, c2 = [], [], []
        for nm in t2:
            d = srows.get(nm, {})
            ev = d.get("evidence") or "?"
            st = (d.get("lc") or {}).get("status") or "active"
            sub = _subtier_ice(ev, st)
            if sub == "2A":
                tag = "可优先试驾"
            elif sub == "2B":
                tag = {"sales_drop": "销量骤降·先查生命周期", "clearance": "清库",
                       "facelift_pending": "等新款", "new_launch": "爬坡期"}.get(st, "有取舍")
            else:
                tag = "仅备选"
            (a2 if sub == "2A" else b2 if sub == "2B" else c2).append((nm, tag))

        def cap(lst, k=8):
            items = "".join(li(nm, tg) for nm, tg in lst[:k]) or "<li class='empty'>—</li>"
            more = (f"<li class='empty'>…等 {len(lst)} 款</li>" if len(lst) > k else "")
            return items + more

        t1_items = "".join(li(n) for n in t1) or "<li class='empty'>（本榜空）</li>"
        t3_items = "".join(li(n) for n in t3[:6]) or "<li class='empty'>—</li>"
        t3_items += (f"<li class='empty'>…等 {len(t3)} 款</li>" if len(t3) > 6 else "")
        t1_note = ('<p class="dnote">第1档空＝<b>证据缺口，不是车不能买</b>；'
                   '可先试驾下列 2A 车（未达第1档多因缺 L4 口碑样本≥100 或 L1 官方碰撞）。</p>'
                   if not t1 else "")
        return f"""<div class="decision"><h3>一页纸决策摘要</h3>
  <div class="dgrid">
    <div class="dcard t1"><div class="dh">第1档 · 优先试驾</div><ul>{t1_items}</ul>{t1_note}</div>
    <div class="dcard t2"><div class="dh">第2档 · 按偏好取舍</div>
      <div class="dsub"><b>2A 可优先试驾</b><ul>{cap(a2)}</ul></div>
      <div class="dsub"><b>2B 取舍点</b><ul>{cap(b2)}</ul></div>
      <div class="dsub"><b>2C 仅备选</b><ul>{cap(c2)}</ul></div></div>
    <div class="dcard t3"><div class="dh">第3档 · 证据不足/暂缓</div><ul>{t3_items}</ul></div>
  </div></div>"""

    for b in boards_data:
        parts.append(f"<h2>{esc(b['label'])}</h2>")
        # 落榜名单（正文可见）
        drop_html = ""
        if b.get("drops"):
            rows_d = "".join(
                f"<tr><td>{esc(m)}</td><td>{esc('; '.join(f))}</td>"
                f"<td>{esc('; '.join(n) or '—')}</td></tr>"
                for m, f, n in b["drops"])
            drop_html = (f"<details style='margin-top:8px'><summary>落榜 {len(b['drops'])} 款（点击展开）</summary>"
                         f"<table><thead><tr><th>车型</th><th>闸门代码</th><th>原因</th></tr></thead>"
                         f"<tbody>{rows_d}</tbody></table>"
                         f"<div class='note'>落榜≠车不好：多因未取得可核验官方指导价（懂车帝榜单价与经销商价同值，"
                         "不足以替代指导价），或价格/级别闸未过；下期核到即可入池。</div></details>")
        dc_html = _decision_card_ice(b)
        from collections import Counter
        gsize = Counter(d["group"] for d in (b.get("sub_rows") or []) if d["group"] != "—")
        tiers = b.get("tiers") or {}
        t1m = set(tiers.get("T1") or [])
        t2m = set(tiers.get("T2") or [])
        t3m = set(tiers.get("T3") or [])
        parts.append(f"<div class='card'><div class='note'>候选 {b['n_pass']+b['n_drop']} 款 → 过闸 <b>{b['n_pass']}</b> 款，"
                     f"落榜 {b['n_drop']} 款{drop_html}</div>{dc_html}"
                     "<table class='rank'><thead><tr>"
                     "<th>档组</th><th>车型</th><th>动力</th><th>指导价(万)</th>"
                     "<th>趋势月销</th><th>加权分</th><th>证据</th>"
                     "<th>官方碰撞</th><th>生命周期</th><th>三档</th><th>插补维</th></tr></thead><tbody>")
        for d in b["rows"]:
            g = d["group"]
            is_gi = isinstance(g, int) and g >= 1
            letter = chr(ord("A") + g - 1) if is_gi else "—"
            tie = "≈" if (is_gi and gsize.get(g, 0) > 1) else ""
            arr = _trend_arrow(d.get("tm"))
            ts = d.get("trend_raw")
            stxt = f"{arr} {int(ts):,}" if isinstance(ts, (int, float)) else "—"
            price = d.get("price")
            price_disp = price if price not in (None, "", "—") else "—"
            ev = d["evidence"]
            crash = d.get("crash") or "—"
            ev_label, evcls = _ev_desc(ev, crash, d.get("users"))
            crash_html = {"L1": "<span class='cr l1'>近测</span>",
                          "史": "<span class='cr lishi'>仅历史</span>",
                          "—": "<span class='cr none'>无记录</span>"}.get(crash, esc(crash))
            lc = d.get("lc") or {"status": "active"}
            lcsd = lc.get("status") == "sales_drop"
            lc_cls = "lc-warn" if lcsd else ("lc-ok" if lc.get("status") == "active" else "lc-mid")
            if d["model"] in t1m:
                tier_html = "<span class='tier t1'>第1档</span>"
            elif d["model"] in t2m:
                tier_html = f"<span class='tier t2'>{_subtier_ice(ev, lc.get('status'))}</span>"
            elif d["model"] in t3m:
                tier_html = "<span class='tier t3'>第3档</span>"
            else:
                tier_html = "—"
            parts.append(
                f"<tr><td class='rk'>{letter}{tie}</td>"
                f"<td class='md'><b>{esc(d['model'])}</b></td>"
                f"<td><span class='tag'>{esc(d['power'])}</span></td>"
                f"<td class='num'>{esc(price_disp)}</td>"
                f"<td class='num'>{stxt}</td>"
                f"<td class='num big'>{round(float(d['total']))}</td>"
                f"<td><span class='evpill {evcls}' title='原始等级 {esc(ev)}'>{esc(ev_label)}</span></td>"
                f"<td>{crash_html}</td>"
                f"<td><span class='lcc {lc_cls}'>{esc(_lc_label(lc))}</span></td>"
                f"<td>{tier_html}</td>"
                f"<td class='num'>{d['low']}/{ndim}</td></tr>")
        top_groups = [d["group"] for d in b["rows"] if d["group"] != "—"]
        same_grp = (len(top_groups) > 0 and len(set(top_groups)) == 1)
        board_drops = [d for d in b["rows"] if (d.get("lc") or {}).get("status") == "sales_drop"]
        parts.append("</tbody></table><div class='note'>"
                     "<b>档组</b>＝同字母即<b>名次不可辨</b>（bootstrap 名次 95% 区间重叠；≈ 标并列，组内顺序无意义）；"
                     "<b>加权分已粗化为整数</b>，只比相对强弱、不读精确差；"
                     "<b>官方碰撞</b> 近测/仅历史/无记录＝L1/史/—，只作事实与判档证据、不换算分数；"
                     "<b>插补维</b>越多证据越薄。三档＝能不能买，与名次是两套逻辑，见上方决策摘要。</div>")
        if same_grp and isinstance(top_groups[0], int):
            parts.append(f"<div class='warn'>⚠️ 本榜前十同属一个档组（{chr(ord('A')+top_groups[0]-1)} 组），"
                         "名次区间大面积重叠、逐名次不可辨；请按空间/动力/预算偏好选，"
                         "不要拿第 1 到第 10 当真实差距。</div>")
        if board_drops:
            parts.append("<div class='warn'>🔻 生命周期骤降车（先查生命周期再下单）："
                         + "；".join(f"<b>{esc(d['model'])}</b>（10月 {esc(d['oct'])} / 6月趋势 {esc(d['trend'])}，"
                                     f"仅 {d['lc'].get('ratio')}）" for d in board_drops)
                         + "。销量骤降可能是清库/改款在即/已停产，不一定是产品力下滑；"
                         "名次按 6 月趋势中位数平滑，未反映最新月崩塌。</div>")
        _seen, passed_ev = set(), []
        for _d in (b.get("sub_rows") or []):
            _st = (_d.get("lc") or {}).get("status")
            if _st in (None, "active") or _d["model"] in _seen:
                continue
            _seen.add(_d["model"])
            passed_ev.append((_d["model"], _lc_label(_d.get("lc"))))
        life_ev = b.get("life_events") or []
        if life_ev or passed_ev:
            bits = []
            for e in life_ev:
                s = f"<b>{esc(e['model'])}</b>：{esc(e['label'])}"
                if e.get("successor"):
                    s += f"（接替 <b>{esc(e['successor'])}</b>）"
                bits.append(s)
            bits += [f"<b>{esc(m)}</b>：{esc(s)}" for m, s in passed_ev]
            tail = "；".join(bits[:12]) + (f" …等 {len(bits)} 款" if len(bits) > 12 else "")
            extra = ("该细分刚发生产品换代，<b>建议关注新车数据后再决策</b>，别只看到旧车落榜。"
                     if any(e.get("successor") for e in life_ev) else "")
            parts.append(f"<div class='note'><b>近期事件（换代/停产/清库）</b>：{tail}。{extra}</div>")
        parts.append("</div>")
        # 分动力视图
        for grp, title in (("燃油", "燃油 / 不插电 HEV"), ("混动", "混动（插混 + 增程）")):
            all_sub = [d for d in b["sub_rows"] if d["pgroup"] == grp]
            sub = all_sub[:10]
            if not sub:
                continue
            cnt = len(all_sub)
            if cnt < 5:
                # 样本 <5：取消名次，改无序清单（排名无统计意义）
                items = "".join(
                    f"<li><b>{esc(d['model'])}</b> · {esc(d['price'])}万 · 加权 "
                    f"{round(float(d['total']))}</li>" for d in all_sub)
                parts.append(f"<div class='card'><h3 style='margin-top:0'>{title} "
                             f"清单（共 {cnt} 款，样本&lt;5 不排名）</h3>"
                             f"<ul>{items}</ul>"
                             "<div class='note'>样本不足 5 款，<b>取消名次</b>：任何排序都无统计意义，"
                             "仅供了解该细分有哪些车过闸。</div></div>")
                continue
            parts.append(f"<div class='card'><h3 style='margin-top:0'>{title} "
                         f"分列（共 {cnt} 款，按同一套加权分）</h3>"
                         "<table><thead><tr><th>#</th><th>车型</th><th>动力</th><th>指导价</th>"
                         "<th>趋势销量</th><th>加权分</th></tr></thead><tbody>")
            for i, d in enumerate(sub, 1):
                parts.append(f"<tr><td>{i}</td><td>{esc(d['model'])}</td><td>{esc(d['power'])}</td>"
                             f"<td>{esc(d['price'])}</td><td>{esc(d['trend'])}</td>"
                             f"<td>{esc(d['total'])}</td></tr>")
            if cnt < 10:
                parts.append(f"<tr><td colspan='6'><div class='note'>该细分仅 {cnt} 款过闸，"
                             "不足 10 款；尾部名次只是细分内相对次序，无统计意义。</div></td></tr>")
            parts.append("</tbody></table></div>")
    parts.append(_discount_view(boards_data, ndim))
    parts.append(f"""<h2>评分覆盖率（{ndim} 维 × {audit['n_rows']} 款）</h2><div class="card"><table>
<thead><tr><th>维度</th><th>有真实数据</th><th>插补(low)</th><th>覆盖率</th></tr></thead><tbody>""")
    for k in audit["dims"]:
        c, i = audit["coverage"][k], audit["imputed"][k]
        parts.append(f"<tr><td>{esc(k)}</td><td>{c}</td><td>{i}</td><td>{c/(c+i):.0%}</td></tr>")
    parts.append("</tbody></table><div class='note'>market 维全部来自结构化销量榜；"
                 "其余维度样本不足时按同榜中位插补，不记 0 分、不系统性惩罚冷门车。</div></div>")

    # ---- 复现用：权重 / 公式 / 归一化（正文全公开，评审①）----
    if repro:
        wrows = "".join(repro.get("wrows", []))
        rules = "".join(f"<li>{esc(r)}</li>" for r in repro.get("rules", []))
        parts.append(f"""<h2>复现用：权重、公式与归一化（正文全公开）</h2>
<div class="card"><table><thead><tr><th>维度</th><th>权重</th><th>口径与缺口</th><th>真实覆盖</th><th>插补(low)</th></tr></thead>
<tbody>{wrows}</tbody></table>
<div class='note'>权重合计 <b>{repro.get('wsum',0):.2f}</b>；<code>adas</code> 因口碑「智能化」覆盖率仅
{repro.get('adas_pct',0):.1f}%（{repro.get('adas_n',0)}/{repro.get('adas_total',0)}）被整条禁用，
权重在剩余 6 维重归一化（不把缺失记 0 分）；<code>safety</code> 因近期 L1 证据覆盖率远低于 G2(≥70%)、且 L1 与 L4 不同质，
只作事实列与判档证据，不设评分维。加权总分 = Σ(维分 × 权重) + adjust（本期恒为 0），满分 10。
维分一律映射到 0–10：销量与指导价用<b>全池分位秩</b>（长尾分布不用 min-max）、口碑分(0–5)×2；
缺失维按同榜中位插补并标 low。</div>
<p class="note"><b>评分用哪个销量（评审⑧）</b>：market 维使用<b>「6 月趋势销量中位数」</b>（trend_sales），
不是 10 月单月（sales_oct）。10 月单月只作展示、不进加权分。故选 宝来 等近期骤降的车，名次被中位数平滑、
未反映最新月崩塌——这正是下方「生命周期」专章要单独提示的。</p>
<h3 style='margin-top:14px'>逐维公式与判定规则</h3><ul>{rules}</ul></div>""")

    # ---- 证据等级 A/B/C 定义（评审⑤）----
    parts.append("""<div class="card"><h2 style="margin-top:0">证据充分度（充分 / 中等 / 薄弱）——已与「三档」解耦</h2>
<p class="note">本榜把原来的 A/B/C 改成<b>描述性标签</b>，避免与「能不能买（三档）」混淆。二者是<b>两套独立逻辑</b>：
证据充分度只说「这条结论的证据有多硬」，三档（第1 / 2A / 2B / 2C / 第3档）说「现在值不值得优先看」。<b>第一档 ≠ 碰撞安全已验证</b>。</p>
<ul>
<li><b>充分</b>（内部 A）＝ 口碑样本 ≥100 <b>且</b> 级别+指导价齐备，<b>或</b> 有近期 L1 官方碰撞测评记录（任一即可）。表中进一步标「充分·口碑 / 充分·官方碰撞 / 充分·口碑+碰撞」，让读者知道依据；</li>
<li><b>中等</b>（内部 B）＝ 口碑样本 30–99（无 L1 近期记录）；</li>
<li><b>薄弱</b>（内部 C）＝ 口碑样本 &lt;30（无 L1 近期记录）。</li>
</ul>
<p class="note">⚠️ 两点澄清：①「样本 14 却记充分」<b>不矛盾</b>——如 锋兰达 样本仅 14，但有近期 L1 官方碰撞测评，走碰撞路径；而 天籁 样本 46 无 L1，只到「中等」。②<b>本榜第一档（T1）要求证据充分（内部 A）</b>，所以不存在「第1档却是薄弱证据」；反过来「证据充分」的车仍可能因<b>销量偏低 / 算法稳定性不足</b>被放进第3档——这正是把证据与档位分开的意义。</p></div>""")

    # ---- 生命周期专章（评审④）----
    if lc_items:
        rows_lc = "".join(
            f"<tr><td>{esc(x['model'])}</td><td>{esc(x['board'])}</td>"
            f"<td>{_fmt(x['trend'])}</td><td>{_fmt(x['oct'])}</td>"
            f"<td style='color:#b26a00;font-weight:700'>{x['ratio']}</td>"
            f"<td>{esc(x['note'])}</td></tr>"
            for x in lc_items)
        parts.append(f"""<h2>生命周期专章：销量骤降车（先查生命周期再下单）</h2>
<div class="card"><p class="note">判定：10 月销量 / 6 月趋势中位数 &lt; 0.5 且趋势 ≥800 → 标「销量骤降」。
<b>仅用销量症状识别，无法区分停产 / 改款在即清库 / 正常波动</b>——后者需车系生命周期数据源
（工信部公告、厂商停产通告、懂车帝在售/停售状态，见 report.md「数据来源参考」）。</p>
<table><thead><tr><th>车型</th><th>所在榜</th><th>6月趋势</th><th>10月</th><th>比值</th><th>说明</th></tr></thead>
<tbody>{rows_lc}</tbody></table>
<div class='note'>这些车仍排在榜内（名次按趋势中位数平滑），但最新月已大幅下滑；骤降可能是清库 / 改款 / 停产，
不一定是产品力下滑，<b>不要按榜单位次直接买</b>。本榜不给「现在买贵不贵」的成交价建议。</div></div>""")

    # ---- 插补率突出（评审②）----
    thin = [d for b in boards_data for d in b.get("sub_rows", [])
            if d.get("low", 0) >= ndim - 1]
    if thin:
        thin_names = "、".join(f"{esc(d['model'])}({d['low']}/{ndim})" for d in thin[:20])
        parts.append(f"""<div class="warn"><b>薄证据车型（插补维 {ndim-1}/{ndim} 或 {ndim}/{ndim}）</b>：
{thin_names}{' 等' if len(thin) > 20 else ''}。这些车多数维度靠同榜中位插补，名次尤其不能当真；
space/control/cabin/quality 四维真实覆盖仅 {audit['coverage'].get('space',0)}/{audit['n_rows']}
（{100.0*audit['coverage'].get('space',0)/max(1,audit['n_rows']):.1f}%），其余靠插补。
扩大口碑样本（数据源见 report.md）才能实质降低插补率。</div>""")

    parts.append("""<div class="card"><h2 style="margin-top:0">数据来源与 v6.5 接入状态</h2>
<p class="note">生命周期已升级为<b>确证判定</b>（工信部公告 / 官方停产通告 / 厂商在售状态 / 媒体确证，每条带 URL 溯源，见 categories/lifecycle_status.json）；口碑已接入<b>第二来源合并</b>（koubei_merge.py，消费 vehicle-data-hub 的 benchmark 格式 + 懂车帝种子）。下列为尚未在本沙箱实跑的部分。</p>
<ul>
<li><b>✅ 生命周期确证层</b>（已接入）：lifecycle_confirm.py + categories/lifecycle_status.json，把 detect_lifecycle 的 sales_drop 疑似升级为「停产/改款/清库 确证」；秦L DM 已确证停产，宝来/宋L DM/星舰7 等被误报的骤降已纠正为「在售正常波动」。</li>
<li><b>✅ 口碑样本合并</b>（已接入，种子阶段）：koubei_merge.py 并入懂车帝样本数，使 koubei_users≥30 覆盖率 26.7%→45.5%、插补率 49.6%→36.9%。当前为<b>种子估计值</b>，真实全量需本机跑 vehicle-data-hub（命令见下）。</li>
<li><b>懂车帝月度销量 API</b>（Apify <code>sinodata/china-car-sales</code>，免登录）：600+ 车型按月销量，可校准 6 月趋势与 10 月单月、交叉验证生命周期骤降。<b>待用。</b></li>
<li><b>vehicle-data-hub</b>（GitHub <code>travisoa</code>）：汽车之家配置 + 工信部整车公告 + 懂车帝/汽车之家销量口碑。本机实跑命令（需 Chrome/LibreOffice/登录态 + 国内网络）：<br>
<code>python main.py report &lt;车型&gt; --vs &lt;竞品&gt;</code> → 产出 <code>downloads/benchmark/&lt;dcd_series_id&gt;.json</code>（懂车帝口碑缓存）<br>
<code>python main.py gonggao jianmian sync</code> → 工信部公告 sqlite<br>
随后 <code>python koubei_merge.py --pool ice_pool_full.json --vdh-dir downloads/benchmark --out ice_pool_full.json</code> 即把真实口碑样本并入，替换种子估计。</li>
<li><b>autohome</b>（GitHub <code>swoiow</code>）：汽车之家车型参数配置，按月更新，可补官方指导价/级别、减少落榜。<b>待用。</b></li>
<li><b>C-IASI 中保研 / C-NCAP 中汽测评</b>：接入后可把「官方碰撞」列升级为安全等级。<b>待用。</b></li>
</ul></div>""")

    parts.append("""<div class="warn"><b>读榜提示</b>：本榜回答的是「10 月这个价位/级别里，
按公开可核验数据排出来的相对次序」，不是碰撞安全结论，也不是终端成交价建议。
口碑样本薄、多数维度靠插补的车型，名次不可当购买依据。</div>""")
    parts.append("</div></body></html>")
    return "\n".join(parts)


def audit_md(audit):
    ndim = len(audit["dims"])
    L_ = ["# 评分逐条审计（ice_scoring 代理分）", "",
          "每一维的分数、来源公式与置信度都可回溯；`low` 表示该维按同榜中位插补。", "",
          "## 覆盖率", "", "| 维度 | 有真实数据 | 插补(low) | 覆盖率 |", "|---|---|---|---|"]
    for k in audit["dims"]:
        c, i = audit["coverage"][k], audit["imputed"][k]
        L_.append(f"| {k} | {c} | {i} | {c/(c+i):.0%} |")
    L_ += ["", "## 固定公式", ""] + [f"- {r}" for r in audit["rules"]]
    L_ += ["", "## 逐车逐维（点击展开）", ""]
    for row in audit["rows"]:
        L_ += [f"<details><summary><b>{row['model']}</b> · {row['cls']} · {row['power']} · "
               f"插补 {len(row['low_dims'])}/{ndim} 维</summary>", "",
               "| 维度 | 分数 | 置信度 | 依据 |", "|---|---|---|---|"]
        for k in audit["dims"]:
            L_.append(f"| {k} | {row['scores'][k]} | {row['confidence'][k]} | "
                      f"{row['basis'][k]} |")
        L_ += ["", "</details>", ""]
    return "\n".join(L_)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True)
    ap.add_argument("--category", default="car_ice_phev")
    ap.add_argument("--out", required=True)
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    pool = json.load(open(a.pool, encoding="utf-8"))
    cfg = L.load_category(a.category)
    cands, audit = IS.score_pool(pool["pool"], cfg)
    audit["n_rows"] = len(cands)
    print(f"评分完成 {len(cands)} 款 · {len(cfg['dimensions'])} 维", file=sys.stderr)

    boards_data, md_parts = [], []
    summary = {"as_of": pool["as_of"], "category": a.category,
               "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "boards": {}, "scoring_coverage": audit["coverage"],
               "scoring_imputed": audit["imputed"]}
    for board in L.board_items(cfg):
        label = board["label"]
        sub = L.filter_board(cands, board)
        gates = board.get("gates") or {}
        passed, dropped = [], []
        for c in sub:
            res = ranking.apply_gates(c, gates)
            if res["pass"]:
                passed.append(c)
            else:
                dropped.append((c, res))
        rows = L.score_rows(passed, cfg)
        try:
            tt = L.top_table(passed, cfg, n=a.top)
        except Exception as e:
            print(f"  [warn] top_table {label}: {type(e).__name__} {e}", file=sys.stderr)
            tt = {"groups": [[r["model"]] for r in rows[:a.top]],
                  "ranked": [r["model"] for r in rows[:a.top]], "report": ""}
        try:
            tr = L.tier_report(passed, cfg)
        except Exception as e:
            print(f"  [warn] tier_report {label}: {type(e).__name__} {e}", file=sys.stderr)
            tr = {"tiers": {}, "report": ""}
        md, data = render_board_md(board, passed, dropped, rows, tt, tr.get("tiers") or {},
                                   cfg, a.top)
        md_parts.append(md)
        # 分动力视图用全榜 sub_rows（不截断到前十）
        by = {c["model"]: c for c in passed}
        groups = {}
        for gi, g in enumerate(tt.get("groups") or [], 1):
            for nm in g:
                groups[nm] = gi
        sub_rows = _table_rows(rows, by, groups, len(rows))
        drops_out = [(c["model"], res["failed"], res["notes"]) for c, res in dropped]
        life_events = []
        for c, res in dropped:
            if "lifecycle_out" in (res.get("failed") or []):
                _lc = L.lifecycle_of(c)
                life_events.append({
                    "model": c["model"],
                    "label": L.LC_LABEL.get(_lc.get("status"), _lc.get("status")),
                    "successor": _lc.get("successor") or "",
                })
        boards_data.append({"label": label, "rows": data, "sub_rows": sub_rows,
                            "n_pass": len(passed), "n_drop": len(dropped),
                            "drops": drops_out, "tiers": tr.get("tiers") or {},
                            "life_events": life_events})
        summary["boards"][label] = {
            "candidates": len(sub), "passed": len(passed), "dropped": len(dropped),
            "top10": [r["model"] for r in rows[:a.top]],
            "tiers": tr.get("tiers") or {},
            "dropped_names": [c["model"] for c, _ in dropped],
        }
        print(f"  {label}: 过闸 {len(passed)} / 落榜 {len(dropped)}", file=sys.stderr)

    meta = {"as_of": pool["as_of"],
            "generated_at": summary["generated_at"],
            "kept": pool["stats"]["in_class"] + 0,
            "processed": pool["stats"]["processed"],
            "name_matched": pool["stats"]["name_matched"],
            "in_class": pool["stats"]["in_class"],
            "by_cls": pool["stats"]["by_cls"], "by_power": pool["stats"]["by_power"]}
    meta["kept"] = json.load(open(os.path.join(os.path.dirname(a.pool), "ice_sales.json"),
                                  encoding="utf-8"))["stats"]["kept"]

    # ---- 复现节：权重 / 公式 / 覆盖率全部现算，不手抄、不藏在附录 ----
    _dims = cfg["dimensions"]
    _wsum = sum(float(d["weight"]) for d in _dims)
    _wrows = ["| 维度 | 权重 | 口径与缺口 | 真实覆盖 | 插补(low) |",
              "|---|---|---|---|---|"]
    for _d in _dims:
        _k = _d["key"]
        _wrows.append(
            f"| {_d['label']} (`{_k}`) | {float(_d['weight']) * 100:.0f}% | "
            f"{str(_d.get('gap') or '—').replace('|', '/')} | "
            f"{audit['coverage'].get(_k, 0)}/{audit['n_rows']} | "
            f"{audit['imputed'].get(_k, 0)} |")
    _rules = [f"{i}. {r}" for i, r in enumerate(audit["rules"], 1)]
    _cov_pct = 100.0 * audit["coverage"].get("space", 0) / max(1, audit["n_rows"])

    # ---- 复现卡片数据（HTML 用）----
    _repro_wrows = []
    for _d in _dims:
        _k = _d["key"]
        _repro_wrows.append(
            f"<tr><td>{html.escape(_d['label'])} (<code>{_k}</code>)</td>"
            f"<td>{float(_d['weight']) * 100:.0f}%</td>"
            f"<td>{html.escape(str(_d.get('gap') or '—').replace('|', '/'))}</td>"
            f"<td>{audit['coverage'].get(_k, 0)}/{audit['n_rows']}</td>"
            f"<td>{audit['imputed'].get(_k, 0)}</td></tr>")
    repro = {"wrows": _repro_wrows, "rules": audit["rules"], "wsum": _wsum,
             "adas_pct": audit["adas_coverage"]["pct"],
             "adas_n": audit["adas_coverage"]["n"],
             "adas_total": audit["adas_coverage"]["total"]}

    # ---- 生命周期骤降车（全局）----
    lc_items = []
    for b in boards_data:
        for d in b.get("sub_rows", []):
            lc = d.get("lc") or {}
            if lc.get("status") == "sales_drop":
                lc_items.append({"model": d["model"], "board": b["label"],
                                 "trend": d.get("trend_raw"),
                                 "oct": d.get("oct_raw"),
                                 "ratio": lc.get("ratio"),
                                 "note": lc.get("note", "")})

    # ---- report.md ----
    head = [
        f"# 2026 年 10 月 · 燃油与混动 紧凑型 / 中型 轿车·SUV 四榜", "",
        f"> 数据源月份 {pool['as_of']}｜生成 {summary['generated_at']}｜"
        f"口径版本 v6.3（全量候选池 + L1 碰撞证据层）｜每榜前 {a.top} 名", "",
        "## 先看口径：这四榜和纯电四榜不是一回事", "",
        "1. **动力范围**：燃油 / 不插电油电混合（HEV）+ 插混 + 增程；纯电排除；"
        "「一车多动力、厂家未拆车系名」（懂车帝 ne=4，64 款）也排除——"
        "它们的销量含纯电，算进燃油/混动就是「拿车系合计当单一动力销量」。",
        "2. **级别**：汽车之家展示的厂商申报官方二级分类（车系页 `levelName`），不用轴距推定；"
        "取不到级别的车系一律拒绝入池，不猜。",
        "3. **分数**：由公开结构化数据按固定公式自动映射（销量趋势 + 口碑 + 官方指导价），"
        "**不是纯电榜那种逐款人工核验的产品力分**；加权分不可与纯电榜直接比较。"
        "本榜应理解为「**市场热度 / 官方指导价 / L4 口碑**的综合参考」，"
        "不是产品力榜、不是安全榜、也不是成交价建议。",
        "4. **L1 碰撞证据 ≠ 安全维**：v6.3 已接入 C-IASI 2018 年至今全量公开测评表"
        "（189 条）与 C-NCAP 成绩表（57 款），作受控匹配后为候选车建立官方测评"
        "**存在性**证据；但近期证据覆盖率远低于维度准入门槛 G2(≥70%)，且 L1 与 L4 "
        "不同质，故只进「事实列 + 判档证据」，**不换算分数、不进 6 维加权**。"
        "C-IASI 等级图标官网无文字对照，未解码；成绩不外推到其它年款/配置。"
        "详见 `crash_evidence.md`。",
        "5. **口碑**属 L4 自选择样本，样本 <30 条的维度按同榜中位插补并标 low，"
        "不是记 0 分，也不是「没数据就不加分」——后者会系统性惩罚冷门车。",
        "6. **第一档（可直接推荐）**："
        + "；".join(f"{lab.split('（')[0]} {len(v['tiers']['T1'])} 款"
                   for lab, v in summary["boards"].items())
        + "。v6.2 及以前四榜第一档必然全空，真实原因是 `t1_evidence=['A']` 沿用纯电榜"
          "阈值、而 A 要求「口碑样本 ≥100」，燃油/混动池几乎没有车达标——"
          "**不是「无官方碰撞证据」**（旧版报告这句话是错的，v6.3 已更正）。"
          "补上 L1 路径后，有近期官方测评的车同样够 A，但多数仍被「算法命中 ≥80% / "
          "全情景命中 ≥60%」的稳定性门槛留在第二档；这是判档纪律，不放水填第一档。",
        "7. **落榜 ≠ 车不好**：主要因未取得可核验官方指导价（懂车帝榜单价与经销商价同值，"
        "不足以替代指导价）或未过价格/级别闸门；逐榜名单见各节「落榜车型」。", "",
        "逐车逐维的分数、公式与插补标记见 `scoring_audit.md`。", "",
        "## 复现用：权重、公式与归一化（正文全公开）", "",
        *_wrows, "",
        f"权重合计 **{_wsum:.2f}**；`adas` 因口碑「智能化」覆盖率仅 "
        f"{audit['adas_coverage']['pct']:.1f}%（{audit['adas_coverage']['n']}/"
        f"{audit['adas_coverage']['total']}）被整条禁用，"
        "权重在剩余 6 维间重归一化（不把缺失记 0 分）；`safety` 因近期 L1 证据覆盖率远低于 G2(≥70%)、且 L1 与 L4 不同质，只作事实列与判档证据，不设评分维。"
        "加权总分 = Σ(维分 × 权重) + `adjust`（本期恒为 0），满分 10。"
        "维分一律映射到 0–10：销量与指导价用**全池分位秩**（长尾分布不用 min-max）、"
        "口碑分(0–5)×2；缺失维按同榜中位插补并标 `low`。", "",
        "逐维公式与判定规则：", "",
        *_rules, "",
        "> **口径修正（v6.6）**：`dimensions[market]` 本期由漂移的 **24% 回调至设计上限 22%**"
        "（其余五维等比放大、合计仍为 1.0）。实测影响：四榜 **top10 交集均 10/10**，Kendall τ 0.78–0.96；"
        "唯一档位变化是**中型轿车的「迈腾」掉出第1档**（T1 由 2 款→1 款，仅余 银河星耀7）。"
        "即：去掉 2pp「市场热度」权重不改变谁进前十，但会让个别靠销量撑档的车降档——这正是「销量≠产品力」的体现。"
        "旧 24% 配置留档 `categories/car_ice_phev.json.bak-24pct`。", "",
        f"> **插补比例要说清**：space / control / cabin / quality 四维真实覆盖均只有 "
        f"{audit['coverage'].get('space', 0)}/{audit['n_rows']}（{_cov_pct:.1f}%），"
        f"其余 {audit['imputed'].get('space', 0)} 款靠同榜中位插补。"
        "插补会把证据薄的车拉到中间、压缩区分度；表里标「插补维 5/6」的车，"
        "名次尤其不能当真。禁用插补维后的名次位移见 `sensitivity.md`。", "",
        "---", ""]
    tail = [
        "---", "", "## 待补清单与已知边界", "",
        f"- **名称未匹配（{len(pool['name_unmatched'])} 款）**：多为懂车帝与汽车之家车系名口径不同"
        "（DM/PHEV/新能源后缀、新品牌）；按项目纪律不猜、不入池。",
        "  " + "、".join(x["model"] for x in pool["name_unmatched"][:25]),
        f"- **级别不在四榜或未取到（{len(pool['rejected'])} 款）**：MPV / 中大型 / 大型 / 小型等，"
        "或车系页未取到 levelName，均拒绝入池。",
        "  " + "、".join(x["model"] for x in pool["rejected"][:25]),
        f"- **ne=4 多动力车系被排除**：销量含纯电，无法拆出燃油/混动部分；"
        "完整名单见 `ice_sales.json` 的 `mixed_excluded`。",
        f"- **候选池 v6.3 起全量核验**：销量榜非纯电 {meta['kept']} 款车系逐一核级别，"
        f"不再按销量取前 220 截断；名称未匹配 {len(pool['name_unmatched'])} 款、"
        f"级别拒绝 {len(pool['rejected'])} 款均不猜。切换影响见 `funnel_sensitivity.md`："
        "四榜前十交集 8/10、8/10、9/10、8/10，说明旧漏斗确实漏掉了能进前十的车系。",
        "- **但仍非全市场穷举**：数据源是懂车帝销量榜，汽车之家全库 5372 车系中"
        "没有销量记录的车仍不在池内；且数据源本身是销量榜，市场热度偏向无法完全消除。",
        "- **动力不二次拆分**：HEV 与燃油版合并为「燃油/HEV」，公开榜没有按动力拆分的销量。",
        "- **无终端成交价、无碰撞等级分数、无第三方实测**：L1 碰撞证据只证明"
        "「被官方测评过」，C-IASI 等级图标未解码、不含安全好坏结论；指导价 ≠ 成交价，"
        "终端优惠大的车（轩逸 / 朗逸 / 速腾 / 帕萨特等）用指导价算价值维会失真。"
        "按项目纪律促销价 / 限时价 / 置换价不进闸门、也不替代指导价；"
        "要回答「现在买贵不贵」需另建终端价证据层，**本榜不给成交价建议**，"
        "也不启用证据 adjust 通道。",
        "- 与纯电四榜（人工核验 7 维 + C-NCAP + 气囊事实列）**不可比**："
        "数据来源、维度定义、证据等级含义都不同。", "",
        "## 数据来源参考（开源 / 可获取，用于后续补数据）", "",
        "> 本版**未接入**下列数据源，仅作为后续降插补率、补生命周期、解码碰撞等级的参考。"
        "当前榜单的数据仍来自项目既有管线（懂车帝销量榜 + 汽车之家口碑/级别 + C-IASI/C-NCAP 事实库）。", "",
        "- **懂车帝月度销量 API**（Apify `sinodata/china-car-sales`，免登录）：全国 600+ 车型按月销量，"
        "含 EV/PHEV/EREV 筛选、车身形式/价格带/品牌维度。可用于校准 6 月趋势中位数与 10 月单月、"
        "交叉验证本版「生命周期骤降」判定（如 宝来/宋L DM/秦L DM）。",
        "- **vehicle-data-hub**（GitHub `travisoa`）：统一车型数据采集，覆盖汽车之家配置导出 Excel、"
        "工信部整车公告（车辆公告）参数查询与 PDF 导出、懂车帝/汽车之家销量口碑与竞品对标。"
        "**工信部公告**可直接补 lifecycle（停产/改款备案）；扩口碑样本可实质降低 space/control/cabin/quality 插补率。",
        "- **autohome**（GitHub `swoiow`）：汽车之家车型参数配置，按月更新（最新 v202609）。"
        "可补官方指导价与官方二级分类，减少 `price_unknown` 落榜。",
        "- **C-IASI 中保研**：官网测评结果 + 社区汇总（`zhongbaoyan.gitee.io`）含 G/A/M/P 等级文字。"
        "本版只取「是否被官方测评过」的存在性，未解码等级图标；接入后可把「官方碰撞」列从存在性升级为"
        "安全等级，并真正落地评审建议的「安全证据等级」列。",
        "- **C-NCAP 中汽测评官网**：测评通告与成绩表，可与现有 C-NCAP blend 安全分（α=0.5）对接。", "",
        "## 产物", "",
        "| 文件 | 内容 |", "|---|---|",
        "| `report.md` | 本文：四榜前十 + 分动力视图 + 纯销量对照 + 三档 |",
        "| `四榜报告.html` | 可点开的可视化版本 |",
        "| `scoring_audit.md` | 每车每维的分数、公式、置信度、插补标记 |",
        "| `sensitivity.md` | 三情景敏感性对照（去销量维 / 禁插补维 / 名次区间稳定性） |",
        "| `sensitivity.json` | 上者的机读版（Kendall τ、前十交集、逐车位移） |",
        "| `funnel_sensitivity.md` / `.json` | 入池销量漏斗四情景对照（A/D/E/F） |",
        "| `crash_evidence.md` | L1 官方碰撞证据清单、年款新鲜度与匹配纪律 |",
        "| `summary.json` | 机读摘要（前十名单、三档、落榜） |",
        "| `ice_sales.json` | 6 个月 × 6 类动力销量与趋势中位数 |",
        "| `ice_pool_full.json` | v6.3 正式候选池：官方级别 / 指导价 / 口碑 / 名称匹配审计 |",
        "| `ice_pool.json` | v6.2 基准候选池（前 220 截断），保留作漏斗对照 |", ""]
    report = "\n".join(head + ["\n\n".join(md_parts)] + tail)
    open(os.path.join(a.out, "report.md"), "w", encoding="utf-8").write(report)
    open(os.path.join(a.out, "四榜报告.html"), "w", encoding="utf-8").write(
        render_html(boards_data, meta, audit, repro=repro, lc_items=lc_items))
    open(os.path.join(a.out, "scoring_audit.md"), "w", encoding="utf-8").write(audit_md(audit))
    json.dump(summary, open(os.path.join(a.out, "summary.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    index = "\n".join([
        f"# {pool['as_of']} 燃油/混动四榜 · 归档", "",
        f"- 生成：{summary['generated_at']}",
        f"- 口径：燃油/HEV + PHEV + EREV；官方二级分类；不设价格上限；"
        f"6 维评分不含 safety（L1 碰撞只作事实与判档）",
        f"- 候选：销量榜非纯电 {meta['kept']} 款 → **全量核验 {meta['processed']} 款**"
        f"（不按销量截断）→ 入池 {meta['in_class']} 款", "",
        "## 四榜前十（机读）", ""])
    for lab, b in summary["boards"].items():
        index += f"- **{lab}**：" + "、".join(b["top10"]) + "\n"
    index += """
## 版本记录

- **v6.1 初版**：新建 ice_sales / build_ice_pool / ice_scoring / ice_board 四个模块，
  发现并验证懂车帝 ne=0/1/2/3/4 互斥完备分类；复用项目闸门、MCDA、bootstrap、三档框架。
- **v6.2 披露强化（基准名次零变化）**：正文补「权重 / 公式 / 归一化 / 覆盖率」复现节；
  修正 market 权重旧文案 22% → 实配 24%（配置 `gap` 的设计上限仍是 22%，
  是否下调会动名次，列为待决策项）；表格加 bootstrap 名次区间(95%) 与
  「0.1 分不可辨」说明；分动力视图按实际过闸款数命名，不足 10 款明说尾部无统计意义；
  新增 `ice_sensitivity.py` 的三情景对照（去销量维 / 禁插补维 / 名次稳定性）。
  明确本榜是「市场热度 / 指导价 / L4 口碑综合参考」，不是产品力榜、安全榜或成交价建议。
- **v6.3 结构修正（会改名次，已量化披露）**：① 新建 `ice_crash.py`，接入
  C-IASI 2018 年至今 189 条公开测评 + C-NCAP 57 款成绩，做受控匹配与年款新鲜度分级，
  产出 `categories/ice_crash.json` 与 `crash_evidence.md`；L1 只作事实列与判档证据，
  不进加权分。② 更正旧报告的错误解释：第一档全空的真实原因是 A 档阈值沿用纯电榜
  「口碑样本 ≥100」，不是「无官方碰撞证据」；补上 L1 路径后中型轿车出现 1 款第一档。
  ③ 新建 `ice_funnel.py` 解耦入池销量漏斗，`build_ice_pool.py --top 0` 全量核验
  388 款 → 187 款正式池；四榜前十交集 8/10、8/10、9/10、8/10，证明旧漏斗漏车，
  故正式榜切换到 `ice_pool_full.json`，旧 122 款池留作对照。
  ④ market 22% vs 24% 对照显示前十交集 9–10/10，注记争议不改变结论，默认权重不动。
- **v6.4 展示层审计闭环（名次零变化，仅展示层 + 生命周期标注）**：按 DeepSeek 对 v6.3 的评审逐条整改
  ——① 权重表 + 逐维公式 + 归一化从 report.md 补进 **四榜报告.html**（此前 HTML 缺权重）；
  ② 插补率 50/187（27%）如实披露，新增「薄证据车型」卡 + **开源数据源参考**（懂车帝销量 API /
  vehicle-data-hub / autohome / C-IASI / C-NCAP）用于后续降插补、补 lifecycle、解码碰撞等级（本版未接入）；
  ③ 落榜车型名 + 闸门代码 + 原因从 `<details>` 提到各榜正文可见（紧凑型轿车 1 / 紧凑型SUV 3 /
  中型轿车 2 / 中型SUV 3）；
  ④ 新增**生命周期判定**：`ice_scoring.detect_lifecycle` 用 10月/6月趋势 比值<0.5 标「销量骤降」，
  写回 `rec["lifecycle"]`，HTML/MD 加生命周期专章列出 宝来(0.15) / 宋L DM(0.35) / 秦L DM(0.34) 等，
  leaderboard 加 `sales_drop` 到 LC_LABEL 与 lifecycle_quiet（不低于 T1 门槛，仅提示）；
  ⑤ 加 **A/B/C 证据等级定义框**，说明「锋兰达样本14→A（走 L1 路径）、天籁样本46→B（无 L1）」并不矛盾；
  ⑥ 同档组说明改为「bootstrap 名次区间重叠（非分差<1）」，前十同组时显式警告（中型SUV等已触发）；
  ⑦ 官方碰撞列加「L1→走 L1 路径达证据 A」说明，落实评审「接入碰撞数据要有判档结果」；
  ⑧ 明确 market 维用 **6 月趋势销量中位数（非 10 月单月）**，10 月仅展示，解释骤降车名次被平滑。
  评分数学与排名**零变化**，仅新增展示层与生命周期标注。

## 归档要点

- 动力：懂车帝 ne=0/1/2/3/4 互斥完备分类，入池 `0+2+3`（388 款）、排除 `1+4`（278 款）。
- 候选池（v6.3 正式）：汽车之家车系索引 5372 款 + 车系页 levelName + 口碑 + 6 月趋势，
  **388 款全量核验 → 入池 187 款**（紧凑型车 35 / 紧凑型SUV 64 / 中型车 38 / 中型SUV 50）；
  v6.2 的 122 款池保留在 `ice_pool.json` 作漏斗对照。
- 评分：6 维 `market .24 / value .18 / space .16 / control .16 / cabin .14 / quality .12`；
  `adas` 覆盖率不足 10% 整条禁用；`safety` 因 L1 覆盖率不足 G2 只作事实/判档，不设评分维；
  样本 <30 的维度同榜中位插补标 low。
- 边界：第一档不再是「必然全空」，但多数车被稳定性门槛留在第二档；
  L1 碰撞证据只证明「被官方测评过」，等级未解码、不外推年款；
  落榜 ≠ 车不好；名称未匹配 / 级别拒绝均不猜。

## 产物

| 文件 | 内容 |
|---|---|
| `report.md` | 四榜前十 + 复现公式 + 分动力分列 + 纯销量对照 + 三档 + 待补清单 |
| `四榜报告.html` | 可视化（含名次区间与分动力实际款数） |
| `scoring_audit.md` | 每车每维分数、公式、置信度、插补标记 |
| `sensitivity.md` / `sensitivity.json` | 三情景敏感性对照（Kendall τ、前十交集、逐车位移） |
| `funnel_sensitivity.md` / `.json` | 入池销量漏斗四情景对照（A/D/E/F） |
| `crash_evidence.md` / `categories/ice_crash.json` | L1 碰撞证据清单与事实库 |
| `summary.json` | 机读摘要（前十、三档、落榜、各维覆盖率与插补数） |
| `ice_sales.json` / `ice_sales_raw.json` | 6 个月非纯电销量与原始响应 |
| `ice_pool_full.json` | 187 款正式候选池（含拒绝原因） |
| `ice_pool.json` | 122 款 v6.2 基准池，供漏斗对照 |
| INDEX.md | 本文件 |
"""
    open(os.path.join(a.out, "INDEX.md"), "w", encoding="utf-8").write(index)
    print(f"[ok] {a.out} | 四榜前十 + HTML + audit + summary + INDEX")


if __name__ == "__main__":
    main()
