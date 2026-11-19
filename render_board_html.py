#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一榜单 HTML 渲染器 —— 把出榜产物转成可点开的可视化页面。

为什么需要它：pipeline.py / board_v2.py 只出 Markdown；ice_board.py / ev_battery_board.py
自带 HTML，但纯电/燃油主榜一直没有。以前那些漂亮页面是各 run 目录的一次性 build_*.py
生成的、写死车型和文案，没进发布包——安装用户拿不到。本脚本把它固化成通用能力：
同输入必得同输出、无模型调用、不依赖任何第三方包。

用法（在本 skill 根目录执行）：
    python3 render_board_html.py --data runs/x/board_v2_data.json \
        --out runs/x/榜单.html [--summary runs/x/summary.json] \
        [--title "中型纯电轿车 12-18万"] [--as-of 202610]

输入：
  --data      board_v2.py 产出的 board_v2_data.json（必需）
  --summary   pipeline.py 产出的 summary.json（可选，用于三档分级与审查结论）
  --out       输出 html 路径
  --title     页面标题（缺省取 data.meta.title）
  --as-of     数据月（缺省 "—"）

纪律：事实参考列只展示、不计分；分段/产品力榜均复用主榜分数，不重新打分。
"""
import argparse
import html
import json
import os
import sys
from datetime import datetime

CSS = """
:root{--ink:#15181c;--ink2:#4a525c;--ink3:#7b8590;--line:#e4e2dd;--bg:#f7f6f3;--card:#fff;
--accent:#c8442e;--accent2:#1f6f8b;--soft:#f0eee9;--t1:#1a7f37;--t2:#9a6700;--t3:#b42318}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.7 "PingFang SC","Microsoft YaHei","Noto Sans SC",sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:1200px;margin:0 auto;padding:38px 26px 70px}
.kicker{font-size:12.5px;letter-spacing:.18em;color:var(--ink3);text-transform:uppercase}
h1{font-size:30px;line-height:1.3;margin:12px 0 10px}h1 em{font-style:normal;color:var(--accent)}
h2{font-size:20px;margin:38px 0 6px;padding-bottom:9px;border-bottom:2px solid var(--line)}
h2 span{font-size:13px;color:var(--ink3);font-weight:400;margin-left:9px}
.lede{color:var(--ink2);max-width:960px;margin:0}
.rule{background:#eef4f7;border-left:3px solid var(--accent2);padding:12px 16px;
border-radius:0 8px 8px 0;font-size:13.5px;color:#294a58;margin:18px 0}
.rule b{color:#173f52}
.warn{background:#fbf3e9;border-left:3px solid var(--accent);color:#5a4632;
padding:12px 16px;border-radius:0 8px 8px 0;font-size:13.5px;margin:18px 0}
.meta{display:flex;gap:22px;flex-wrap:wrap;margin-top:14px;font-size:13px;color:var(--ink3)}
.meta b{color:var(--ink)}
table{width:100%;border-collapse:collapse;background:var(--card);border-radius:12px;
overflow:hidden;font-size:13px;box-shadow:0 1px 2px rgba(0,0,0,.03);margin-top:12px}
th{background:var(--soft);color:var(--ink2);font-weight:600;text-align:center;padding:11px 8px;
font-size:11.5px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:10px 8px;text-align:center;border-bottom:1px solid #efece6;font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:none}tr:hover td{background:#faf9f6}
td.name{text-align:left;font-weight:600;white-space:nowrap}
.rk{display:inline-flex;width:25px;height:25px;align-items:center;justify-content:center;
border-radius:50%;font-weight:700;background:var(--soft);color:var(--ink2)}
.rk.g1{background:#f6e3c0;color:#8a5a00}.rk.g2{background:#e7eaed;color:#495360}
.rk.g3{background:#f3dfd6;color:#9b4a2f}
.score{font-size:17px;font-weight:700;color:var(--accent2)}
.delta-up{color:#c8442e;font-weight:700}.delta-down{color:#1a7f37;font-weight:700}
.tier{display:inline-block;padding:2px 8px;border-radius:20px;font-size:11px;font-weight:700;color:#fff}
.T1{background:var(--t1)}.T2{background:var(--t2)}.T3{background:var(--t3)}
.ref{color:var(--ink2);font-size:12px;text-align:left;line-height:1.6}
.ref .tag{color:var(--accent);font-weight:600}
.band{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 18px;margin-bottom:12px}
.band h3{margin:0 0 8px;font-size:15px;color:var(--accent2)}
.band ol{margin:0;padding-left:22px;font-size:13.5px}.band li{padding:2px 0}.band li b{color:var(--accent2)}
.tier-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px;margin-top:14px}
.tier-card{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--t2);
border-radius:10px;padding:14px 18px}
.tier-card.T1{border-left-color:var(--t1)}.tier-card.T3{border-left-color:var(--t3)}
.tier-card h3{margin:0 0 8px;font-size:15px}
.tier-card ul{margin:0;padding-left:20px;font-size:13.5px}
.foot{margin-top:34px;font-size:12px;color:var(--ink3);border-top:1px solid var(--line);padding-top:16px}
@media(max-width:720px){.wrap{padding:22px 12px 50px}h1{font-size:23px}table{font-size:11.5px}th,td{padding:7px 4px}}
"""


def e(s):
    return html.escape(str(s)) if s is not None else ""


def num(v, digits=1):
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def rank_badge(rank):
    g = "g1" if rank == 1 else ("g2" if rank == 2 else ("g3" if rank == 3 else ""))
    return f'<span class="rk {g}">{rank}</span>'


def tier_badge(t):
    return f'<span class="tier {e(t)}">{e(t)}</span>' if t else ""


def render(data, summary, title, as_of):
    """HTML 渲染：遍历 board_view.BLOCKS 注册表，缺数据的区块自动不渲染。

    区块清单/顺序/条件唯一来源 = board_view.py；这里只负责每块的 HTML 标记。
    """
    import board_view as BV
    meta = data.get("meta", {})
    rows = data.get("main_board", [])
    bands = data.get("band_subboards", [])
    seg = data.get("segmented_summary", {})
    pp = data.get("product_power_board", {})
    title = title or meta.get("title") or "统一榜单"
    active = BV.active_blocks(data, summary)
    ids = {b["id"] for b in active}

    head = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)} · 可信购车榜单</title><style>{CSS}</style></head><body><div class="wrap">
<div class="kicker">CAR-RECOMMENDATION · 统一榜单引擎 · 数据月 {e(as_of)}</div>
<h1>{e(title)} <em>可信排行榜</em></h1>
<div class="meta"><span>候选 <b>{len(rows)}</b> 款</span>
<span>主榜口径 <b>{e(meta.get('primary', '8维加权打分榜'))}</b></span>
<span>生成 <b>{datetime.now().strftime('%Y-%m-%d %H:%M')}</b></span></div>"""
    H = [head]

    # ---- lede（注册区块，文案统一在 board_view）
    if "lede" in ids:
        H.append('<p class="lede">打分主榜是唯一权威名次来源（8 维加权）；事实指标降级为'
                 '<b>参考列 · 不计分</b>；纯产品力榜为并列辅助视角，不改主榜名次。</p>'
                 '<div class="rule"><b>三条规则：</b>① 主榜＝打分榜，唯一权威名次；'
                 '② kWh、电耗、电芯、月销等＝参考列，<b>只看不计分</b>；'
                 '③ 多段全价位榜额外给价位段子榜与分段汇总；「不看价格比产品力」看纯产品力榜。</div>')

    # ---- 事实列覆盖披露
    if "fact_coverage" in ids:
        have, lack = BV.fact_coverage(meta, rows)
        if lack:
            H.append(f'<div class="rule">事实参考列覆盖：本榜有数据的是 <b>{e("、".join(have))}</b>；'
                     f'「—」表示未采集（{e("、".join(lack))}），不以估计值填充。</div>')

    # ---- 打分主榜
    if "main_board" in ids:
        H.append("<h2>打分主榜 <span>8 维加权总分排序 · 参考列不参与排序</span></h2>")
        H.append("<table><thead><tr><th>#</th><th>车型</th><th>加权分</th><th>档位</th>"
                 "<th>证据</th><th style='text-align:left'>事实参考列 · 不计分</th></tr></thead><tbody>")
        for r in rows:
            ref = " · ".join(f"{k} {v}" for k, v in (r.get("fact_ref") or {}).items()
                             if v not in (None, "—", ""))
            H.append(f"<tr><td>{rank_badge(r.get('rank'))}</td><td class='name'>{e(r.get('model'))}</td>"
                     f"<td><span class='score'>{num(r.get('total'))}</span></td>"
                     f"<td>{tier_badge(r.get('tier'))}</td><td>{e(r.get('evidence'))}</td>"
                     f"<td class='ref'>{e(ref) if ref else '—'}</td></tr>")
        H.append("</tbody></table>")

    # ---- 总分构成
    if "score_breakdown" in ids:
        dims = meta.get("dimensions") or []
        H.append("<h2>总分构成 <span>8 维原始分 · 加权分＝Σ(维分×权重)×10 + adjust</span></h2>")
        H.append('<table><thead><tr><th>#</th><th>车型</th>')
        for d in dims:
            H.append(f"<th>{e(d.get('label'))}<br><small>{float(d.get('weight',0))*100:.1f}%</small></th>")
        H.append("<th>加权分</th></tr></thead><tbody>")
        for r in rows:
            sc = r.get("scores") or {}
            H.append(f"<tr><td>{rank_badge(r.get('rank'))}</td><td class='name'>{e(r.get('model'))}</td>")
            for d in dims:
                v = sc.get(d["key"])
                cls = "delta-up" if isinstance(v, (int, float)) and v >= 9 else (
                    "delta-down" if isinstance(v, (int, float)) and v < 8 else "")
                H.append(f"<td class='{cls}'>{num(v) if isinstance(v,(int,float)) else '—'}</td>")
            H.append(f"<td><span class='score'>{num(r.get('total'))}</span></td></tr>")
        H.append("</tbody></table>")

    # ---- 三档
    if "tiers" in ids:
        tiers = {}
        review = ""
        if summary:
            t = (summary.get("tiers") or {})
            if t and all(k in t for k in ("T1", "T2", "T3")):
                tiers = t
            else:
                first = next(iter(t.values()), {})
                if isinstance(first, dict): tiers = first
            rv = summary.get("review") or {}
            if rv: review = next(iter(rv.values()), "") if isinstance(rv, dict) else str(rv)
        if not tiers:
            derived = {"T1": [], "T2": [], "T3": []}
            for r in rows:
                k = str(r.get("tier") or "").upper()
                if k in derived: derived[k].append(r.get("model"))
            tiers = derived
        H.append("<h2>三档分级 <span>能不能买 / 要不要取舍 / 现在该不该看 —— 与名次是两套逻辑</span></h2>")
        if review: H.append(f'<div class="warn">{e(review)}</div>')
        H.append('<div class="tier-grid">')
        names = {"T1": "第一档 · 可直接买", "T2": "第二档 · 按偏好取舍", "T3": "第三档 · 证据不足/暂缓"}
        for k in ("T1", "T2", "T3"):
            models = tiers.get(k) or []
            H.append(f'<div class="tier-card {k}"><h3>{names[k]}（{len(models)} 款）</h3>'
                     f'<ul>{"".join(f"<li>{e(m)}</li>" for m in models) if models else "<li>—</li>"}</ul></div>')
        H.append("</div>")

    # ---- 价位段子榜 / 分段汇总（多段才有）
    if "band_subboards" in ids:
        H.append("<h2>价位段子榜 <span>同一份分数按预算分组 · 复用主榜 · 不重新打分</span></h2>")
        for b in bands:
            items = b.get("items") or []
            H.append(f"<div class='band'><h3>{e(b.get('band'))}（{len(items)} 款）</h3><ol>")
            for it in items:
                H.append(f"<li><b>{e(it.get('model'))}</b> — {num(it.get('score'))} 分 "
                         f"<span style='color:#7b8590'>({e(it.get('price'))}万)</span></li>")
            H.append("</ol></div>")
    if "segmented_summary" in ids:
        H.append(f"<h2>分段汇总总榜 <span>各段取前 {e(seg.get('per_band'))} → 按主榜分数汇总 · 不重新打分</span></h2>")
        H.append("<table><thead><tr><th>#</th><th>车型</th><th>分数</th><th>来自价位段</th><th>段内名次</th></tr></thead><tbody>")
        for i, it in enumerate(seg.get("items") or [], 1):
            H.append(f"<tr><td>{rank_badge(i)}</td><td class='name'>{e(it.get('model'))}</td>"
                     f"<td>{num(it.get('score'))}</td><td>{e(it.get('band'))}</td>"
                     f"<td>{e(it.get('rank_in_band'))}</td></tr>")
        H.append("</tbody></table>")

    # ---- 纯产品力总榜
    if "product_power" in ids:
        H.append("<h2>纯产品力总榜 <span>辅助榜 · 剔除性价比、全池混排 · 不改主榜任何名次</span></h2>")
        H.append('<div class="rule">主榜（8 维含性价比）仍是唯一权威排序；本榜只回答「不看价格谁的产品力更强」。'
                 '<b>rank_delta</b> 正数＝性价比拖累，负数＝靠性价比抬升了主榜名次。</div>')
        H.append("<table><thead><tr><th>#</th><th>车型</th><th>产品力分</th><th>主榜名次</th>"
                 "<th>rank_delta</th><th>价格</th></tr></thead><tbody>")
        for it in pp.get("items") or []:
            d = it.get("rank_delta")
            dcls = "delta-up" if isinstance(d, int) and d > 0 else ("delta-down" if isinstance(d, int) and d < 0 else "")
            H.append(f"<tr><td>{it.get('power_rank')}</td><td class='name'>{e(it.get('model'))}</td>"
                     f"<td><span class='score'>{num(it.get('power_score'), 2)}</span></td>"
                     f"<td>#{e(it.get('main_rank'))}</td><td class='{dcls}'>"
                     f"{f'{d:+d}' if isinstance(d,int) else '—'}</td><td>{e(it.get('price'))}</td></tr>")
        H.append("</tbody></table>")

    # ---- 逐车取证依据（历史有、v8.x 漏掉，v9.0 补回）
    if "evidence_notes" in ids:
        H.append("<h2>逐车取证依据 <span>取证要点 / 证据等级 / 销量口径 · 可逐条质疑</span></h2>")
        H.append("<table><thead><tr><th style='text-align:left'>车型</th><th>证据</th>"
                 "<th style='text-align:left'>取证要点与事实</th></tr></thead><tbody>")
        for r in rows:
            note = r.get("note") or "—"
            fr = r.get("fact_ref") or {}
            extras = []
            if fr.get("电芯"): extras.append(f"电芯 {fr['电芯']}")
            if fr.get("趋势月销"): extras.append(f"趋势月销 {fr['趋势月销']}")
            if fr.get("指导价"): extras.append(f"指导价 {fr['指导价']}万")
            detail = e(note) + (f"<br><span style='color:#7b8590'>{' · '.join(e(x) for x in extras)}</span>"
                                if extras else "")
            H.append(f"<tr><td class='name'>{e(r.get('model'))}</td><td>{e(r.get('evidence'))}</td>"
                     f"<td class='ref'>{detail}</td></tr>")
        H.append("</tbody></table>")

    # ---- 生命周期（历史有、v8.x 漏掉，v9.0 补回）
    if "lifecycle" in ids:
        life = meta.get("lifecycle") or (summary or {}).get("lifecycle") or {}
        if life:
            H.append("<h2>生命周期提示 <span>换代 / 清库 / 新车爬坡 —— 销量下滑不等于车不行</span></h2>")
            H.append('<div class="card" style="background:#fff;border:1px solid var(--line);'
                     'border-radius:12px;padding:14px 18px"><ul>')
            for model, info in (life.items() if isinstance(life, dict) else []):
                H.append(f"<li>{e(BV.lifecycle_line(model, info))}</li>")
            H.append("</ul></div>")

    # ---- 落榜与待核（历史有、v8.x 漏掉，v9.0 补回）
    if "dropped" in ids:
        H.append("<h2>落榜与待核名单 <span>落榜 ≠ 车不好 · 见 report.md 逐条原因</span></h2>")
        H.append('<div class="card" style="background:#fff;border:1px solid var(--line);'
                 'border-radius:12px;padding:14px 18px"><ul>')
        for lab, b in ((summary or {}).get("boards") or {}).items():
            if isinstance(b, dict) and b.get("dropped"):
                H.append(f"<li>{e(lab)}：过闸 {e(b.get('passed'))} / 落榜 {e(b.get('dropped'))}</li>")
        H.append("</ul></div>")

    # ---- 方法与边界（历史有、v8.x 漏掉，v9.0 补回）
    if "method_boundary" in ids:
        H.append("<h2>方法与边界 <span>必读，避免把名次当结论</span></h2>")
        H.append('<div class="card" style="background:#fff;border:1px solid var(--line);'
                 'border-radius:12px;padding:14px 18px"><ul>')
        for t in BV.method_boundary_text(): H.append(f"<li>{e(t)}</li>")
        H.append("</ul></div>")

    # ---- 复现（历史有、v8.x 漏掉，v9.0 补回）
    if "reproduce" in ids:
        H.append("<h2>复现说明 <span>同输入必得同输出</span></h2>")
        H.append('<div class="card" style="background:#fff;border:1px solid var(--line);'
                 'border-radius:12px;padding:14px 18px"><ul>')
        for t in BV.reproduce_text(meta): H.append(f"<li>{e(t)}</li>")
        H.append("</ul></div>")

    H.append(f"""<div class="foot">可信购车榜单引擎 · 展示区块注册表 board_view.py（{len(active)} 项已渲染）<br>
主榜调用 leaderboard.score_rows，事实列不计分、子榜复用分数均由 selftest 验证。</div>
</div></body></html>""")
    return "\n".join(H)


def selftest():
    """契约自检：注册表必要区块一个不少；多段数据下两版渲染器展示同一组区块。"""
    import board_view as BV
    data = {
        "meta": {"title": "T", "primary": "8维加权打分榜", "fact_cols_scored": False,
                 "dimensions": [{"key": "powertrain", "label": "三电", "weight": .6},
                                {"key": "value", "label": "性价比", "weight": .4}],
                 "fact_reference_columns": [{"key": "price", "label": "指导价"},
                                            {"key": "cell", "label": "电芯"}]},
        "main_board": [
            {"rank": 1, "model": "A车", "total": 88.0, "evidence": "A", "tier": "T1",
             "note": "取证要点", "scores": {"powertrain": 9, "value": 8},
             "fact_ref": {"指导价": 13.0, "电芯": "—"}},
            {"rank": 2, "model": "B车", "total": 80.0, "evidence": "B", "tier": "T2",
             "note": "取证明细", "scores": {"powertrain": 7, "value": 9},
             "fact_ref": {"指导价": 15.0, "电芯": "—"}},
        ],
        # 两段 → registry 判定 multi_band=True，两段榜单都要渲染
        "band_subboards": [{"band": "入门段", "items": [{"model": "A车", "score": 88.0, "price": 13.0}]},
                           {"band": "高端段", "items": [{"model": "B车", "score": 80.0, "price": 25.0}]}],
        "segmented_summary": {"per_band": 10, "items": [{"model": "A车", "score": 88.0,
                                                          "band": "入门段", "rank_in_band": 1}]},
        "product_power_board": {"n": 2, "items": [
            {"model": "B车", "power_score": 85.0, "main_rank": 2, "power_rank": 1,
             "rank_delta": 1, "price": 15.0}]},
    }
    doc = render(data, None, "T", "202610")
    checks = [
        ("主榜车型", "A车" in doc),
        ("分数", "88.0" in doc),
        ("三档", "第一档" in doc and "第二档" in doc),
        ("维度明细", "总分构成" in doc),
        ("逐车取证", "逐车取证依据" in doc),
        ("方法与边界", "方法与边界" in doc),
        ("复现", "复现说明" in doc),
        ("价位段", "入门段" in doc),
        ("分段汇总", "分段汇总总榜" in doc),
        ("产品力榜", "纯产品力总榜" in doc and "rank_delta" in doc),
        ("事实覆盖披露", "事实参考列覆盖" in doc),
    ]
    bad = [n for n, ok in checks if not ok]
    # 注册表契约
    rc = BV.selftest()
    if rc != 0: bad.append("board_view 契约")
    if bad:
        print("[selftest FAIL]", bad)
        return 1
    print(f"[selftest OK] 注册表 {len(BV.BLOCK_IDS)} 区块 / 必要契约 {len(BV.REQUIRED_BLOCK_IDS)} 项 / "
          f"HTML {len(BV.active_blocks(data, None))} 块全渲染")
    return 0


def main():
    ap = argparse.ArgumentParser(description="统一榜单 HTML 渲染器")
    ap.add_argument("--data", help="board_v2_data.json 路径（--selftest 时不需要）")
    ap.add_argument("--summary", help="pipeline.py 的 summary.json（可选，用于三档）")
    ap.add_argument("--out", help="输出 html 路径（--selftest 时不需要）")
    ap.add_argument("--title", help="页面标题（缺省取 data.meta.title）")
    ap.add_argument("--as-of", default="—", help="数据月，如 202610")
    ap.add_argument("--selftest", action="store_true", help="结构自检（不依赖真实数据）")
    a = ap.parse_args()

    if a.selftest:
        sys.exit(selftest())
    if not a.data or not a.out:
        ap.error("渲染需要 --data 与 --out（或加 --selftest）")

    data = json.load(open(a.data, encoding="utf-8"))
    summary = json.load(open(a.summary, encoding="utf-8")) if a.summary and os.path.exists(a.summary) else None
    doc = render(data, summary, a.title, a.as_of)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    open(a.out, "w", encoding="utf-8").write(doc)
    print(f"[ok] {a.out}（{len(doc)} 字符）")


if __name__ == "__main__":
    main()
