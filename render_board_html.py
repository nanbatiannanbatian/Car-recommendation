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
import re
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
/* v9.7.14 本榜导航：目录卡片 + 锚点跳转 */
.navbox{background:#fff;border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:20px 0 8px}
.navbox h3{margin:0 0 12px;font-size:15px;color:var(--ink)}
.navgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:9px}
.navgrid a{display:block;text-decoration:none;color:inherit;border:1px solid var(--line);
border-radius:8px;padding:9px 11px;transition:border-color .15s,background .15s}
.navgrid a:hover{border-color:var(--accent2);background:#f3f8fa}
.navgrid a b{display:block;font-size:13.5px;color:var(--accent2)}
.navgrid a span{display:block;font-size:12px;color:var(--ink3);line-height:1.5;margin-top:2px}
.topbar{text-align:right;font-size:12px;margin-top:8px}
.topbar a{color:var(--ink3);text-decoration:none}
h2{scroll-margin-top:18px}
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

    # ---- v9.7.14 本榜导航：从 BLOCKS 单一来源生成，只列本次实际渲染的区块
    NAV_BY_TITLE = {
        "打分主榜": "main_board", "逐配置对比": "trim_compare", "总分构成": "score_breakdown",
        "三档分级": "tiers", "价位段子榜": "band_subboards", "分段汇总总榜": "segmented_summary",
        "纯产品力总榜": "product_power", "体验证据修正": "experience_adjust",
        "逐车取证依据": "evidence_notes", "生命周期提示": "lifecycle",
        "落榜与待核名单": "dropped", "方法与边界": "method_boundary", "复现说明": "reproduce",
        "三榜并列": "three_board", "稳健性": "robustness", "审查边界": "audit_boundary",
    }
    nav_items = []
    for b in active:
        bid = b["id"]
        if bid in ("lede", "fact_coverage"):
            continue
        title_b = b["title"].replace("（下拉）", "").replace("（维度明细）", "")
        anchor = NAV_BY_TITLE.get(title_b, bid)
        desc = re.sub(r"多段全价位榜才有；|（下拉）|（维度明细）", "", b.get("desc") or "").strip()
        nav_items.append(f'<a href="#sec-{anchor}"><b>{e(title_b)}</b><span>{e(desc)}</span></a>')
    if nav_items:
        H.append('<div class="navbox" id="top"><h3>本榜导航 · 点击直达各部分</h3>'
                 '<div class="navgrid">' + "".join(nav_items) + '</div>'
                 '<div class="topbar"><a href="#top">↑ 回到顶部</a></div></div>')

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

    # ---- 打分主榜（v9.6.0：单表多列，含评分配置/指导价/月销/证据/周期/kWh万元/电池来源）
    if "main_board" in ids:
        grades = BV.fact_grades(rows)
        H.append("<h2>打分主榜 <span>按每车最高配置分排序（锚定到车系分量纲）· 事实列不参与排序</span></h2>")
        H.append('<div class="rule">v9.7.1：每款车取其<b>最高分配置</b>参与排名，逐配置分经标准差锚定到车系分量纲'
                 '（买车买的是具体配置）。无配置数据的车沿用原车系分。'
                 '点下方「逐配置对比」可展开看每款车的全部配置。</div>')
        H.append('<div class="rule">按<b>评分配置</b>逐车评分（配置不同分不同）；指导价/月销/kWh·万元/'
                 '电池来源为事实列不计分。kWh·万元 <b style="color:#1a7f37">▲优</b>（本池前25%）/'
                 '<b style="color:#b42318">▼弱</b>（后25%）；月销 <i>≤</i> 为车系合计上界。'
                 '证据 A=官方碰撞成绩 / B=口碑样本够 / C=新上市或样本不足。</div>')
        H.append('<div class="rule">配置下拉为<b>交互预览</b>：切换后主榜名次与分数实时重排，'
                 '但<b>档位、三榜并列、稳健性、审查边界按最高配置口径固定不变</b>（权威结论不随预览改变）。</div>')
        tb_main = meta.get("trim_board") or {}
        H.append('<table id="mainBoard"><thead><tr>' + "".join(f"<th>{e(BV.MAIN_COL_LABELS[k])}</th>"
                 for k in BV.MAIN_BOARD_COLUMNS) + "</tr></thead><tbody>")
        for r in rows:
            cand = r
            gmap = grades.get(r["model"], {})
            td = tb_main.get(r["model"]) or {}
            H.append(f"<tr data-model='{e(r['model'])}' data-base-total='{r.get('total')}'>")
            for ci, k in enumerate(BV.MAIN_BOARD_COLUMNS):
                val = BV.main_cell(k, r, cand, gmap.get("kWh/万元", ""))
                if k == "rank":
                    H.append(f"<td class='c-rank'>{rank_badge(r.get('rank'))}</td>")
                elif k == "model":
                    sel = ""
                    if td.get("trims"):
                        opts = "".join(
                            f'<option value="{i}"'
                            f'{" selected" if t.get("name")==r.get("best_trim") else ""}>'
                            f'{e(t.get("name"))}（¥{e(t.get("price"))}）</option>'
                            for i, t in enumerate(td["trims"]))
                        sel = (f'<br><select class="trimsel" data-model="{e(r["model"])}" '
                               f'style="font-size:11px;max-width:160px">{opts}</select>')
                    H.append(f"<td class='name'>{e(val)}{sel}</td>")
                elif k == "total":
                    H.append(f"<td class='c-total'><span class='score'>{e(val)}</span></td>")
                elif k == "tier":
                    H.append(f"<td>{tier_badge(r.get('tier'))}</td>")   # 档位固定，不参与联动
                elif k == "evidence":
                    cls = {"A": "delta-up", "C": "delta-down"}.get(val, "")
                    H.append(f"<td class='{cls}'>{e(val)}</td>")
                elif k == "kwh_per_wan":
                    g = gmap.get("kWh/万元", "")
                    H.append(f'<td class="c-kwh" style="color:{BV.grade_color(g) if g else "inherit"}">{e(val)}</td>')
                elif k == "trim":
                    H.append(f"<td class='ref c-trim'>{e(val)}</td>")
                elif k == "price":
                    H.append(f"<td class='c-price'>{e(val)}</td>")
                elif k == "cell_brand":
                    H.append(f"<td class='ref c-cell'>{e(val)}</td>")
                elif k == "sales":
                    H.append(f"<td>{e(val)}</td>")   # 月销不随配置变
                elif k == "verdict":
                    v = BV.car_verdict(r, r)
                    # v9.7.12：营运/网约车为主——醒目前置标识，让私人购车者一眼看出场景不同
                    flag = '<b style="color:#b42318">⚠ 营运车为主</b><br>' if v.get("fleet") else ""
                    H.append(f"<td class='ref c-verdict' style='font-size:12px;line-height:1.5'>"
                             f"{flag}"
                             f"<b style='color:#1a7f37'>优</b> {'、'.join(e(x) for x in v['pros'])}<br>"
                             f"<b style='color:#b42318'>短</b> {'、'.join(e(x) for x in v['cons'])}<br>"
                             f"适合：{e(v['audience'])}｜<b>{e(v['advice'])}</b></td>")
                else:
                    H.append(f"<td>{e(val)}</td>")
            H.append("</tr>")
        H.append("</tbody></table>")
    # ---- 逐配置下拉对比（v9.7.0）
    if "trim_compare" in ids:
        tb = meta.get("trim_board") or {}
        if tb:
            H.append('<h2>逐配置对比 <span>点车型展开 · 同车不同配置的相对差别</span></h2>')
            H.append('<div class="rule">每个配置按 spec_score 规则单独算分，<b>★＝该车最高分配置，也是主榜用来排名的配置</b>。'
                     '看对比请盯「三电」列和最后一列「得分理由」——例如 MG07 845版靠 800V+1.0、12min快充+1.4、'
                     '一线电芯+0.7、845km+0.8 把三电从 7.3 拉到 9.9。'
                     '注意：配置表缺悬架/算力等参数，逐配置分有插补维度，只作同车配置相对对比。</div>')
            for r in rows:
                td = tb.get(r["model"])
                if not td or not td.get("trims"):
                    continue
                best = td.get("best_trim")
                H.append(f'<details style="margin:4px 0;border:1px solid var(--line);border-radius:8px;padding:8px 12px">'
                         f'<summary style="cursor:pointer"><b>{e(r["model"])}</b> '
                         f'（{e(best)} · {td["n_trims"]} 配置，最高 {e(td.get("best_total"))}）</summary>')
                H.append('<table style="margin-top:8px"><thead><tr><th>配置</th><th>指导价(万)</th>'
                         '<th>电量kWh</th><th>电芯</th><th>电压/快充</th><th>三电</th><th>智驾</th>'
                         '<th>性价比</th><th>配置总分</th><th>插补</th>'
                         '<th style="text-align:left">三电得分理由 / 关键差异</th></tr></thead><tbody>')
                for t in td["trims"]:
                    sc = t.get("scores") or {}
                    flag = " ★" if t.get("name") == best else ""
                    why = "、".join((t.get("reasons") or {}).get("powertrain") or []) or "—"
                    note = t.get("note") or ""
                    H.append(f"<tr><td class='name'>{e(t.get('name'))}{flag}</td>"
                             f"<td>{e(t.get('price'))}</td><td>{e(t.get('battery_kwh'))}</td>"
                             f"<td class='ref'>{e(t.get('cell_supplier'))}</td>"
                             f"<td class='ref'>{e(t.get('charge'))}</td>"
                             f"<td>{num(sc.get('powertrain'))}</td><td>{num(sc.get('adas'))}</td>"
                             f"<td>{num(sc.get('value'))}</td>"
                             f"<td><span class='score'>{e(t.get('total'))}</span></td>"
                             f"<td style='color:#b8860b'>{e(t.get('n_imputed'))}维</td>"
                             f"<td class='ref' style='font-size:11px'>{e(why)}"
                             f"{'<br><i>'+e(note)+'</i>' if note else ''}</td></tr>")
                H.append('</tbody></table>')
                H.append(f'<div style="font-size:11px;color:#7b8590">来源：{e(td.get("source"))} '
                         f'· 核验 {e(td.get("date"))}</div></details>')

    # ---- 总分构成
    if "score_breakdown" in ids:
        dims = meta.get("dimensions") or []
        H.append("<h2>总分构成 <span>8 维原始分 · 加权分＝Σ(维分×权重)×10 + adjust</span></h2>")
        H.append('<table id="scoreBoard"><thead><tr><th>#</th><th>车型</th><th>评分配置</th><th>指导价(万)</th>')
        for d in dims:
            H.append(f"<th>{e(d.get('label'))}<br><small>{float(d.get('weight',0))*100:.1f}%</small></th>")
        H.append("<th>加权分</th></tr></thead><tbody>")
        tb = meta.get("trim_board") or {}
        for r in rows:
            sc = r.get("scores") or {}
            td = tb.get(r["model"]) or {}
            # 配置下拉：与主榜联动（class=trimsel 共用同一处理函数）
            sel = ""
            if td.get("trims"):
                opts = "".join(
                    f'<option value="{i}"'
                    f'{" selected" if t.get("name")==r.get("best_trim") else ""}>'
                    f'{e(t.get("name"))}（¥{e(t.get("price"))}）</option>'
                    for i, t in enumerate(td["trims"]))
                sel = (f'<br><select class="trimsel" data-model="{e(r["model"])}" '
                       f'style="font-size:11px;max-width:150px">{opts}</select>')
            H.append(f"<tr data-model='{e(r['model'])}'><td>{rank_badge(r.get('rank'))}</td>"
                     f"<td class='name'>{e(r.get('model'))}{sel}</td>"
                     f"<td class='ref'>{e(BV.main_cell('trim', r, r))}</td>"
                     f"<td>{e(BV.main_cell('price', r, r))}</td>")
            for d in dims:
                v = sc.get(d["key"])
                cls = "delta-up" if isinstance(v, (int, float)) and v >= 9 else (
                    "delta-down" if isinstance(v, (int, float)) and v < 8 else "")
                H.append(f"<td class='{cls} dimcell' data-dim='{d['key']}'>"
                         f"{num(v) if isinstance(v,(int,float)) else '—'}</td>")
            H.append(f"<td class='totalcell'><span class='score'>{num(r.get('total'))}</span></td></tr>")
        H.append("</tbody></table>")
        # 配置切换联动脚本：三表联动（主榜/总分构成/产品力），事件委托绑定
        if tb:
            s2 = json.dumps(tb, ensure_ascii=False)
            js = r"""<script>
var TRIMDATA=__DATA__;
function trimCellVals(model,idx){
  var t=TRIMDATA[model].trims[idx],sc=t.scores||{},charge=String(t.charge||''),
      cell=String(t.cell_supplier||'—'),kwh=t.battery_kwh,price=t.price;
  var mv=charge.match(/(900|800|400)\s*V/);
  return {name:t.name,price:price,total:t.total,power:t.power_score,scores:sc,verdict:t.verdict||'',
    cell:cell+(mv?' · '+mv[1]+'V':''),
    kwh:(kwh&&price)?(kwh/price).toFixed(2):'—'};
}
function applyTrim(model,idx){
  var v=trimCellVals(model,idx);
  var q='[data-model="'+model+'"]';
  document.querySelectorAll('#mainBoard tr'+q+',#scoreBoard tr'+q).forEach(function(row){
    var c=function(k){return row.querySelector('.'+k);};
    if(c('c-total'))c('c-total').innerHTML='<span class="score">'+v.total+'</span>';
    if(c('c-trim'))c('c-trim').textContent=v.name;
    if(c('c-price'))c('c-price').textContent=v.price;
    if(c('c-cell'))c('c-cell').textContent=v.cell;
    if(c('c-kwh'))c('c-kwh').textContent=v.kwh;
    if(c('c-verdict')&&v.verdict){var flag=v.verdict.indexOf('营运')>=0?'<b style="color:#b42318">⚠ 营运车为主</b><br>':'';c('c-verdict').innerHTML=flag+v.verdict.replace(/优：/,'<b style="color:#1a7f37">优</b> ').replace(/｜短：/,'<br><b style="color:#b42318">短</b> ').replace(/｜适合：/,'<br>适合：').replace(/｜(建议|暂缓|谨慎|落榜)/,'<br><b>$1</b>');}
    row.querySelectorAll('.dimcell').forEach(function(d){
      var k=d.getAttribute('data-dim');
      if(v.scores[k]!=null)d.textContent=Number(v.scores[k]).toFixed(1);});
    if(c('totalcell'))c('totalcell').innerHTML='<span class="score">'+v.total+'</span>';
  });
  var sb=document.getElementById('scoreBoard');
  if(sb){var sr=sb.querySelector('tr'+q);if(sr)sr.children[2].textContent=v.name+' ¥'+v.price;}
  // 产品力榜：更新产品力分与价格
  var pb=document.getElementById('powerBoard');
  if(pb){var pr=pb.querySelector('tr'+q);
    if(pr){
      if(pr.querySelector('.c-power'))pr.querySelector('.c-power').innerHTML='<span class="score">'+v.power+'</span>';
      if(pr.querySelector('.c-ppprice'))pr.querySelector('.c-ppprice').textContent=v.price;
    }
  }
  sortMain();
  sortPower();
}
function mainRankOf(model){
  var r=document.querySelector('#mainBoard tr[data-model="'+model+'"] .c-rank');
  if(!r)return null;
  var m=String(r.textContent).match(/\d+/);
  return m?parseInt(m[0]):null;
}
function sortMain(){
  var tb=document.querySelector('#mainBoard tbody');if(!tb)return;
  var rs=Array.prototype.slice.call(tb.querySelectorAll('tr'));
  rs.sort(function(a,b){
    var ae=a.querySelector('.c-total'),be=b.querySelector('.c-total');
    var av=ae?parseFloat(ae.textContent)||0:0,bv=be?parseFloat(be.textContent)||0:0;
    return bv-av;});
  rs.forEach(function(r,i){tb.appendChild(r);
    var rc=r.querySelector('.c-rank');if(rc)rc.innerHTML='<span class="rank">'+(i+1)+'</span>';});
}
function sortPower(){
  var tb=document.querySelector('#powerBoard tbody');if(!tb)return;
  var rs=Array.prototype.slice.call(tb.querySelectorAll('tr'));
  rs.sort(function(a,b){
    var ae=a.querySelector('.c-power'),be=b.querySelector('.c-power');
    var av=ae?parseFloat(ae.textContent)||0:0,bv=be?parseFloat(be.textContent)||0:0;
    return bv-av;});
  rs.forEach(function(r,i){tb.appendChild(r);
    var pr=r.querySelector('.c-prank');if(pr)pr.textContent=i+1;
    var mr=mainRankOf(r.getAttribute('data-model'));
    if(mr!=null){
      var mc=r.querySelector('.c-mainrank');if(mc)mc.textContent='#'+mr;
      var d=mr-(i+1),dc=r.querySelector('.c-delta');
      if(dc){dc.textContent=(d>0?'+':'')+d;
        dc.className='c-delta '+(d>0?'delta-up':d<0?'delta-down':'');}
    }
  });
}
document.addEventListener('change',function(ev){
  var sel=ev.target;
  if(!sel.classList||!sel.classList.contains('trimsel'))return;
  var m=sel.getAttribute('data-model'),idx=parseInt(sel.value);
  applyTrim(m,idx);
  document.querySelectorAll('.trimsel[data-model="'+m+'"]').forEach(function(o){o.value=idx;});
});
</script>""".replace('__DATA__',s2)
            H.append(js)

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
        tbpp = meta.get("trim_board") or {}
        H.append('<div class="rule">下拉框切换配置后，本榜产品力分（剔除性价比的 7 维）实时重算并重排；'
                 '与主榜/总分构成联动。rank_delta 仍按主榜当前名次计算（正=性价比拖累，负=靠性价比抬升）。</div>')
        H.append('<table id="powerBoard"><thead><tr><th>#</th><th>车型</th><th>产品力分</th>'
                 '<th>主榜名次</th><th>rank_delta</th><th>价格</th></tr></thead><tbody>')
        for it in pp.get("items") or []:
            d = it.get("rank_delta")
            dcls = "delta-up" if isinstance(d, int) and d > 0 else ("delta-down" if isinstance(d, int) and d < 0 else "")
            mdl = it.get("model")
            td = tbpp.get(mdl) or {}
            sel = ""
            if td.get("trims"):
                best = td.get("best_trim")
                opts = "".join(
                    f'<option value="{i}"'
                    f'{" selected" if t.get("name")==best else ""}>'
                    f'{e(t.get("name"))}</option>'
                    for i, t in enumerate(td["trims"]))
                sel = (f'<br><select class="trimsel" data-model="{e(mdl)}" '
                       f'style="font-size:11px;max-width:150px">{opts}</select>')
            H.append(f"<tr data-model='{e(mdl)}'><td class='c-prank'>{it.get('power_rank')}</td>"
                     f"<td class='name'>{e(mdl)}{sel}</td>"
                     f"<td class='c-power'><span class='score'>{num(it.get('power_score'), 2)}</span></td>"
                     f"<td class='c-mainrank'>#{e(it.get('main_rank'))}</td>"
                     f"<td class='{dcls} c-delta'>"
                     f"{f'{d:+d}' if isinstance(d,int) else '—'}</td>"
                     f"<td class='c-ppprice'>{e(it.get('price'))}</td></tr>")
        H.append("</tbody></table>")

    # ---- 体验证据修正（v9.3.1，用户确认展示）
    if "experience_adjust" in ids:
        ea = meta.get("experience_adjust") or {}
        H.append("<h2>体验证据修正 <span>智驾/操控从硬件口径按可核验体验证据调整 · 不碰其它维</span></h2>")
        H.append('<div class="rule">有证据的车才调整（来源分级加权、每维封顶 ±1.5）；'
                 '无证据的车维持硬件口径、不插补。点来源链接可逐条复核。</div>')
        H.append("<table><thead><tr><th style='text-align:left'>车型</th><th>维度</th>"
                 "<th>前 → 后</th><th>调整</th><th style='text-align:left'>依据来源（可点开）</th>"
                 "</tr></thead><tbody>")
        for model, info in ea.items():
            for dim, det in (info.get("dimensions") or {}).items():
                delta = det.get("delta", 0)
                if not delta:
                    continue
                dcls = "delta-up" if isinstance(delta, (int, float)) and delta > 0 else "delta-down"
                dtxt = f"{delta:+.2f}" if isinstance(delta, (int, float)) else "—"
                srcs = det.get("evidence") or []
                links = " · ".join(
                    f'<a href="{e(s.get("url"))}" target="_blank" rel="noopener">'
                    f'{e(s.get("source_type"))} {e(s.get("date"))}</a>' if s.get("url")
                    else f'{e(s.get("source_type"))} {e(s.get("date"))}'
                    for s in srcs)
                claim = " ".join(str(s.get("claim") or "")[:90] for s in srcs[:1])
                dlabel = BV.experience_dim_label(dim)
                H.append(f"<tr><td class='name'>{e(model)}</td><td>{dlabel}</td>"
                         f"<td>{num(det.get('before'))} → {num(det.get('after'))}</td>"
                         f"<td class='{dcls}'>{dtxt}</td>"
                         f"<td class='ref'>{links}<br><span style='color:#7b8590'>{e(claim)}</span></td></tr>")
        H.append("</tbody></table>")

    # ---- 逐车取证依据（历史有、v8.x 漏掉，v9.0 补回）
    if "evidence_notes" in ids:
        H.append("<h2>逐车取证依据 <span>取证要点 / 证据等级 / 销量口径 · 可逐条质疑</span></h2>")
        H.append("<table><thead><tr><th style='text-align:left'>车型</th><th>证据</th>"
                 "<th style='text-align:left'>取证要点与事实</th></tr></thead><tbody>")
        grades = BV.fact_grades(rows)
        for r in rows:
            note = r.get("note") or "—"
            fr = r.get("fact_ref") or {}
            gmap = grades.get(r["model"], {})
            extras = []
            for k in BV.DETAIL_FACT_FIELDS:     # 折中：主榜移出的字段在这里补齐
                v = fr.get(k)
                if v in (None, "—", ""):
                    continue
                txt = f"{v}万" if k == "指导价" else str(v)
                g = gmap.get(k, "")
                if g == "上界":
                    extras.append(f'<i style="color:{BV.grade_color(g)}">{e(k)} {e(txt)}（上界）</i>')
                else:
                    extras.append(f"{e(k)} {e(txt)}")
            detail = e(note) + (f"<br><span style='color:#7b8590'>{' · '.join(extras)}</span>"
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

    # ---- 三榜并列
    if "three_board" in ids:
        tb = meta["three_board"]
        H.append("<h2>三榜并列 <span>销量序 / 加权序 / BT 共识序 · 换视角看名次</span></h2>")
        H.append('<div class="rule">销量序＝市场认不认；加权序＝产品维度综合分；'
                 'BT 共识序＝10 种算法两两胜负后的 Bradley-Terry 共识（非平均名次）。'
                 '「销量↔加权」差值绝对值 ≥3 加粗：正=被销量低估，负=卖得好但产品分不出彩。</div>')
        H.append("<table><thead><tr><th>#</th><th>车型</th><th>趋势月销</th><th>销量序</th>"
                 "<th>加权序</th><th>BT共识序</th><th>销量↔加权</th></tr></thead><tbody>")
        for i, r in enumerate(tb.get("rows") or [], 1):
            d = r.get("delta")
            cls = "delta-up" if isinstance(d, int) and d >= 3 else (
                "delta-down" if isinstance(d, int) and d <= -3 else "")
            txt = f"{d:+d}" if isinstance(d, int) else "—"
            H.append(f"<tr><td>{rank_badge(i)}</td><td class='name'>{e(r.get('model'))}</td>"
                     f"<td>{e(r.get('sales'))}</td><td>{e(r.get('sales_rank'))}</td>"
                     f"<td>{e(r.get('weighted_rank'))}</td><td>{e(r.get('bt_rank'))}</td>"
                     f"<td class='{cls}'>{txt}</td></tr>")
        H.append("</tbody></table>")
        H.append(f'<div style="font-size:12px;color:#7b8590;margin-top:6px">'
                 f'三榜前十的三方交集：<b>{e(tb.get("overlap"))}/10</b>。BT 共识前5：'
                 f'{e(" → ".join(tb.get("bt_top") or []))}</div>')

    # ---- 稳健性
    if "robustness" in ids:
        rb = meta["robustness"]
        h2 = f"{float(rb.get('kendall_mean') or 0):.3f}"
        H.append(f"<h2>稳健性 <span>名次到底硬不硬 · Kendall τ={e(h2)}</span></h2>")
        H.append('<div class="rule">换 10 种聚合 × 多套权重，看名次会不会变。'
                 'τ 0.8–0.9＝大体一致但前几名有方法敏感性；'
                 '「稳定」只说明算法层面一致，不代表输入证据可信。</div>')
        H.append("<table><thead><tr><th>车型</th><th>算法进前三</th><th>判定</th></tr></thead><tbody>")
        pa = rb.get("per_alt") or {}
        for r in rows[:10]:
            a = pa.get(r.get("model")) or {}
            if a.get("hits") is not None:
                H.append(f"<tr><td class='name'>{e(r.get('model'))}</td>"
                         f"<td>{e(a.get('hits'))}/{e(rb.get('n_methods'))}</td>"
                         f"<td>{e(a.get('verdict'))}</td></tr>")
        H.append("</tbody></table>")

    # ---- 审查边界
    if "audit_boundary" in ids:
        ab = meta["audit_boundary"]
        H.append("<h2>审查边界 <span>必须披露的已知缺口</span></h2>")
        H.append(f'<div class="warn"><b>{e(ab.get("verdict"))}</b></div>')
        for title, key, color in [("🟠 结论边界（修不了，写进结论）", "boundary", "#c8442e"),
                                  ("🟡 提示级（须在榜上披露）", "warn", "#b8860b"),
                                  ("⚪ 说明级", "info", "#7b8590")]:
            items = ab.get(key) or []
            if not items:
                continue
            H.append(f'<div class="card" style="background:#fff;border:1px solid var(--line);'
                     f'border-radius:12px;padding:12px 16px;margin:8px 0">'
                     f'<b style="color:{color}">{title}</b><ul>')
            for it in items:
                H.append(f"<li>{e(it if isinstance(it, str) else json.dumps(it, ensure_ascii=False))}</li>")
            H.append("</ul></div>")

    H.append(f"""<div class="foot">可信购车榜单引擎 · 展示区块注册表 board_view.py（{len(active)} 项已渲染）<br>
主榜调用 leaderboard.score_rows，事实列不计分、子榜复用分数均由 selftest 验证。</div>
</div></body></html>""")
    # v9.7.14：给每个 h2 加锚点 id（按标题文字匹配 BLOCKS，不依赖渲染顺序）
    def _add_anchor(m):
        inner = m.group(1)
        first = re.sub(r"<[^>]+>", "", inner).strip().split()[0] if inner.strip() else ""
        bid = NAV_BY_TITLE.get(first, "")
        return f'<h2 id="sec-{bid}">' + inner + '</h2>' if bid else m.group(0)
    H = [re.sub(r"<h2>(.*?)</h2>", _add_anchor, blk) if isinstance(blk, str) else blk for blk in H]
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
