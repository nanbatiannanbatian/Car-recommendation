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
/* v10.41.0 简版/完整版视图切换：简版只显示 core 区块，完整版全显示 */
.viewbar{display:flex;align-items:center;gap:8px;justify-content:flex-end;margin:14px 0 4px}
.viewbar span{font-size:12px;color:var(--ink3);margin-right:2px}
.viewbar button{font:13px/1.4 inherit;padding:6px 13px;border:1px solid var(--line);
border-radius:18px;background:#fff;color:var(--ink2);cursor:pointer;transition:all .15s}
.viewbar button:hover{border-color:var(--accent2)}
.viewbar button.on{background:var(--accent2);border-color:var(--accent2);color:#fff;font-weight:600}
body.view-simple .vblk[data-view="extra"]{display:none}
body.view-simple .navgrid a[data-view="extra"]{display:none}
.view-hint{font-size:12px;color:var(--ink3);margin:2px 0 10px;line-height:1.6}
@media print{.viewbar{display:none}body.view-simple .vblk[data-view="extra"]{display:block}}
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
/* v9.7.16：同档底色分组 + 警示标签 */
tr.tier-T1 td{background:#eef8f0}tr.tier-T2 td{background:#fffdf5}tr.tier-T3 td{background:#fdf3f2}
.trim-missing{display:inline-block;margin-top:2px;font-size:10.5px;color:#8a6d3b;background:#fcf8e3;border:1px solid #f0e0b8;border-radius:4px;padding:0 4px;cursor:help}
.flag{display:inline-block;font-size:11px;font-weight:400;padding:1px 5px;border-radius:4px;margin-left:4px}
.flag-warn{background:#fdf0ee;color:#b42318;border:1px solid #f3c9c3}
.flag-est{background:#fbf3e9;color:#8a5a1f;border:1px solid #edd9bc}
.flag-fleet{background:#fdf0ee;color:#b42318;border:1px solid #d85a30;font-weight:500}
.flag-crash{background:#eaf6ee;color:#17663a;border:1px solid #b7dfc6;font-weight:500}
.flag-lowtier{background:#f2f4f7;color:#475467;border:1px solid #d0d5dd;font-weight:400;cursor:help}
.xboard-tier{display:inline-block;margin-left:5px;font-size:11px;font-weight:400;padding:1px 6px;border-radius:4px;background:#f2f4f7;color:#475467;border:1px solid #d0d5dd;cursor:help}
.stability-tip{background:#fff8e6;border-left:3px solid #BA7517;padding:10px 14px;
border-radius:0 8px 8px 0;font-size:13px;color:#5c4518;margin:14px 0}
.rk{display:inline-flex;width:25px;height:25px;align-items:center;justify-content:center;
border-radius:50%;font-weight:700;background:var(--soft);color:var(--ink2)}
.rk.g1{background:#f6e3c0;color:#8a5a00}.rk.g2{background:#e7eaed;color:#495360}
.rk.g3{background:#f3dfd6;color:#9b4a2f}
.score{font-size:17px;font-weight:700;color:var(--accent2)}
.delta-up{color:#c8442e;font-weight:700}.delta-down{color:#1a7f37;font-weight:700}
.tier{display:inline-block;padding:3px 9px;border-radius:20px;font-size:12px;font-weight:700;color:#fff;background:#5f6b78;white-space:nowrap}
.tier.T1{background:var(--t1)}.tier.T2{background:var(--t2)}.tier.T3{background:var(--t3)}
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
.strong-tag{display:inline-block;margin-left:6px;padding:0 5px;border-radius:3px;background:var(--t1);color:#fff;font-size:11px;font-weight:500;vertical-align:1px}
.strong-main{display:inline-block;margin-left:5px;padding:0 5px;border-radius:3px;background:#b8860b;color:#fff;font-size:10px;font-weight:500;vertical-align:1px;cursor:help}
.foot{margin-top:34px;font-size:12px;color:var(--ink3);border-top:1px solid var(--line);padding-top:16px}
@media(max-width:720px){.wrap{padding:22px 12px 50px}h1{font-size:23px}table{font-size:11.5px}th,td{padding:7px 4px}}
"""


def e(s):
    return html.escape(str(s)) if s is not None else ""


def delta_label(d) -> str:
    """rank_delta 通俗化：正=产品力更强但被性价比拖累，负=靠性价比抬升名次。"""
    if not isinstance(d, int):
        return "—"
    if d > 0:
        return f'<span title="rank_delta {d:+d}">被性价比拖累</span>'
    if d < 0:
        return f'<span title="rank_delta {d:+d}">靠性价比抬升</span>'
    return '<span title="rank_delta 0">无明显影响</span>'


def _kwh_cell(t: dict, car_note: str = "") -> str:
    """v9.7.20：估算/待核的电池值降精度显示（三位小数显得像实测，实为估算）。"""
    v = t.get("battery_kwh")
    if not isinstance(v, (int, float)):
        return "—"
    note = str(t.get("note") or "") + " " + str(car_note or "")
    if "估算" in note or "待核" in note:
        return f'≈{round(v)}<span class="flag flag-est" title="该电量为估算/待核，非官方实测值">估</span>'
    # 未标估算但精度>1位小数也降精度（自动解析不应显示伪精确）
    if isinstance(v, float) and abs(v - round(v, 1)) > 0.001:
        return f'≈{round(v, 1)}'
    return str(v)


def dim_breakdown(r: dict, tb_entry: dict, dims: list) -> str:
    """v9.7.19：维度子项依据折叠面板（默认收起）。数据来自 best_trim_detail.deltas + note，不编造。"""
    bd = r.get("best_trim_detail") or {}
    sc = bd.get("scores") or r.get("scores") or {}
    deltas = bd.get("deltas") or []
    by_dim = {}
    for d in deltas:
        by_dim.setdefault(d.get("dim"), []).append(d)
    parts = []
    for dim in dims:
        k = dim.get("key")
        v = sc.get(k)
        if not isinstance(v, (int, float)):
            continue
        ds = by_dim.get(k) or []
        if ds:
            detail = "；".join(f"{e(str(x.get('why') or ''))} {float(x.get('delta') or 0):+.2f}"
                              for x in ds if isinstance(x, dict))
            kind = "配置差异（客观参数）"
        else:
            detail = "该车系基准分（无配置级差异）"
            kind = "车系基准"
        parts.append(f"<li><b>{e(dim.get('label') or k)} {num(v)}</b>"
                     f" <span style='color:#7b8590'>[{kind}]</span>：{detail}</li>")
    note = str(r.get("note") or "")
    if note:
        parts.append(f"<li style='color:#7b8590'>车系取证：{e(note[:180])}</li>")
    return ("<details style='margin:6px 0'><summary style='cursor:pointer'>"
            f"展开 {e(r.get('model'))} 的维度子项依据（8 维分怎么来的）</summary>"
            "<ul style='font-size:12px;line-height:1.7;margin:8px 0 8px 18px'>"
            + "".join(parts) + "</ul></details>") if parts else ""


def sales_tip(r: dict) -> str:
    """v10.28.0：月销单元格 hover 说明——写出趋势月销的计算公式与本车口径。

    公式（来自 SKILL 出榜口径 + monthly.probe_month）：
      稳态车 = 最近 N 个月（默认 6）逐月销量的中位数（抗清库/换代异常月）；
      新车爬坡 = 「逐月中位 vs 最新月」取较大值（避免爬坡首月低估）；
      sales_scope=series_total 时是车系合计（含其他动力），纯电值 ≤ 该上界。
    只读候选已核验字段，不猜不编造；无 sales_note 也给出通用公式。
    """
    scope = r.get("sales_scope")
    parts = [
        "趋势月销（判档/打分用，非单月）：",
        "最近 6 个月逐月销量取中位数（抗清库/换代异常月）；",
        "新车爬坡取「中位 vs 最新月」较大值。",
    ]
    if scope == "series_total":
        parts.append("本车为车系合计上界（含其他动力），纯电实际销量 ≤ 此值，仅作参考列不参与分位。")
    elif scope == "pure_bev":
        parts.append("本车为纯电单独销量口径。")
    note = str(r.get("sales_note") or "").strip()
    if note:
        parts.append("本车注记：" + note)
    return "".join(parts)


def risk_flags(r: dict) -> str:
    """v9.7.16：主表风险标记——估算/插补、营运车、缺官方碰撞报告。只读事实字段，不编造。"""
    out = []
    note = str(r.get("note") or "")
    if any(k in note for k in ("估算", "待核", "插补")):
        out.append('<span class="flag flag-est" title="该车型部分参数为估算/待核；分数仅用于同车配置相对对比，跨车型横向对比可信度下降">⚠估算待核</span>')
    if any(k in note for k in ("营运", "网约", "B端", "出租", "对公")):
        out.append('<span class="flag flag-fleet" title="营运/网约车为主，属硬风险（第三档）；非私人购车首选">营运为主</span>')
    # v9.7.43：销量口径异常标注（季度初出口集中/促销透支/清库等导致的单月异常低值，非产品力问题）
    if any(k in note for k in ("销量口径异常", "出口集中", "季度初出口", "促销透支")):
        out.append('<span class="flag flag-est" title="该车型当月销量为口径性异常低值（如季度初出口集中、促销透支、清库），非产品力问题，不改档位；见生命周期提示与逐车取证依据">⚠销量口径异常</span>')
    # v10.16.4：碰撞成绩相关信息（含"缺权威"提示）全部收敛到「碰撞成绩」列，
    # 车名只保留估算/营运/销量口径等**非碰撞**风险标记。
    # 原先车名既挂绿色成绩徽标、又挂"缺权威"红标，与碰撞成绩列三处重复；
    # 且徽标自写正则会抓错分项（ES6 把 Euro NCAP 成人95% 当主成绩，碰撞列却是93.95%）。
    return "".join(out)


def crash_cell(r: dict, cand: dict) -> str:
    """碰撞成绩列唯一出口：有成绩给体系+规范成绩，无成绩给"—（缺权威，非不安全）"。

    v10.16.4（用户确认方案 A）：成绩只在这一列显示，数字统一取 crash_systems.parse_score
    的规范 display（与 safety 打分、徽标旧逻辑解耦），避免车名徽标抓错分项。
    权威判定同样复用 crash_systems 唯一真相源，与 safety_check 一致。
    """
    from modules.engine import crash_systems as CS
    crash = str(cand.get("crash") or "")
    parsed = CS.parse_score(crash)
    if parsed:
        return f'<span class="flag flag-crash" title="{e(crash)}">{e(parsed["display"])}</span>'
    basis = str(r.get("safety_basis") or "")
    if CS.is_official(basis) or ("抽车" in crash) or ("在测" in crash) or ("测试中" in crash):
        return f'<span class="flag flag-crash">{e(CS.display_short(crash))}</span>'
    return ('<span class="flag flag-warn" title="无中保研(C-IASI)/中汽研(C-NCAP)/Euro NCAP/IIHS 可查的权威碰撞成绩；'
            '安全分基于配置与口碑，非实测实锤；不等于不安全，但证据等级较低">—（缺权威，非不安全）</span>')


def num(v, digits=1):
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def trim_selector(model, trims, best="", *, show_price=True, width=160) -> str:
    """配置下拉框：有阶梯给 select，没采到给显式「配置待补」标注。

    为什么不能静默（2026-11-21 用户问「大众ID.AURA T6 为什么没有下拉框」）：
      没采到逐配置阶梯时，原来这里什么都不渲染——页面上就只剩一个车名，
      读者无法区分「这车配置没差异」和「我们没采到」，看起来像渲染 bug。
      现在统一给出可识别标注，并被回归 t074 强制检查（每款在榜车必须二选一）。
    """
    if trims:
        opts = "".join(
            f'<option value="{i}"'
            f'{" selected" if t.get("name") == best else ""}>'
            f'{e(t.get("name"))}'
            + (f'（¥{e(t.get("price"))}）' if show_price else "")
            + "</option>"
            for i, t in enumerate(trims))
        return (f'<br><select class="trimsel" data-model="{e(model)}" '
                f'style="font-size:11px;max-width:{width}px">{opts}</select>')
    return ('<br><span class="trim-missing" title="未采集到该车的逐配置阶梯'
            '（trims.json 无条目），已列入待补清单。不影响主榜名次，'
            '也不代表这车没有配置差异">配置待补</span>')


def rank_badge(rank):
    g = "g1" if rank == 1 else ("g2" if rank == 2 else ("g3" if rank == 3 else ""))
    return f'<span class="rk {g}">{rank}</span>'


def tier_badge(t):
    """档位徽标：CSS 颜色类是英文 T1/T2/T3（见 .tier.T1/.T2/.T3），
    此前误用中文当 class（tier 第二档）导致 CSS 匹配不上→白字无背景看不清。
    显示文字保留中文，class 用英文，两者分离。"""
    raw = str(t or "")
    cls = {"第一档": "T1", "第二档": "T2", "第三档": "T3"}.get(raw, "")
    if not cls and raw in ("T1", "T2", "T3"):
        cls = raw  # 兼容英文档位值（selftest/部分数据源）
    if not cls:
        return ""
    label = {"T1": "第一档", "T2": "第二档", "T3": "第三档"}.get(cls, raw)
    return f'<span class="tier {cls}" title="{e(label)}">{e(label)}</span>'


def render(data, summary, title, as_of):
    """HTML 渲染：遍历 board_view.BLOCKS 注册表，缺数据的区块自动不渲染。

    区块清单/顺序/条件唯一来源 = board_view.py；这里只负责每块的 HTML 标记。
    """
    from modules.board import board_view as BV
    meta = data.get("meta", {})
    # v10.27.0：页面版本号动态读 SKILL.md frontmatter，渲染层不写死历史版本号。
    # 单一真相源 = score_fingerprint.skill_version()（与报告"当前出榜 skill vX"同一函数）。
    try:
        from modules.engine.score_fingerprint import skill_version as _skill_version
        _ver = _skill_version()
    except (ImportError, OSError, ValueError):
        _ver = ""
    # v10.24.0：叙述统计（智驾占比说明里的行业数字）从策略配置读取，渲染层不写死。
    _narr = {}
    try:
        import paths as _paths
        with open(str(_paths.cat("rank_v2_policy.json")), encoding="utf-8") as _f:
            _narr = (json.load(_f).get("narrative_stats") or {}).get("adas weight tip") or {}
    except (OSError, ValueError, ImportError):
        _narr = {}
    rows = data.get("main_board", [])
    bands = data.get("band_subboards", [])  # v10.29.0：仅用于跨价位提示判 multi_band，不再渲染子榜
    pp = data.get("product_power_board", {})
    title = title or meta.get("title") or "统一榜单"
    active = BV.active_blocks(data, summary)
    ids = {b["id"] for b in active}

    head = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)} · 可信购车榜单</title><style>{CSS}</style></head><body class="view-simple"><div class="wrap">
<div class="kicker">CAR-RECOMMENDATION · 统一榜单引擎 · 数据月 {e(as_of)}</div>
<h1>{e(title)} <em>可信排行榜</em></h1>
<div class="meta"><span>候选 <b>{len(rows)}</b> 款</span>
<span>主榜口径 <b>{e(meta.get('primary', '8维加权打分榜'))}</b></span>
<span>生成 <b>{datetime.now().strftime('%Y-%m-%d %H:%M')}</b></span></div>"""
    H = [head]

    # ---- v10.41.0 视图切换条（页顶醒目）：简版默认，一键切完整版
    H.append('<div class="viewbar" id="viewbar">'
             '<span>视图：</span>'
             '<button type="button" id="btn-simple" class="on" onclick="setBoardView(\'simple\')">简洁（默认）</button>'
             '<button type="button" id="btn-full" onclick="setBoardView(\'full\')">完整（全部区块）</button>'
             '</div>'
             '<div class="view-hint" id="view-hint">'
             '简洁视图 = 一句话导语 + 打分主榜 + 纯产品力总榜 + 三档分级 + 三电与补能 + 空间与储物；'
             '点「完整」查看逐配置/总分构成/三电/取证/方法边界等全部内容。'
             '</div>')

    # ---- v9.7.14 本榜导航：从 BLOCKS 单一来源生成，只列本次实际渲染的区块
    NAV_BY_TITLE = {
        "打分主榜": "main_board", "逐配置对比": "trim_compare", "总分构成": "score_breakdown",
        "三档分级": "tiers",
        "纯产品力总榜": "product_power", "体验证据修正": "experience_adjust",
        # v10.42：三电/空间进简版，必须给 h2 锚点，否则视图包装边界识别不到、会两版都显示
        "三电与补能": "energy_facts", "空间与储物": "space_facts",
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
        _v = BV.block_view(bid)
        nav_items.append(f'<a href="#sec-{anchor}" data-view="{_v}">'
                         f'<b>{e(title_b)}</b><span>{e(desc)}</span></a>')
    if nav_items:
        H.append('<div class="navbox" id="top"><h3>本榜导航 · 点击直达各部分</h3>'
                 '<div class="navgrid">' + "".join(nav_items) + '</div>'
                 '<div class="topbar"><a href="#top">↑ 回到顶部</a></div></div>')
    # v9.7.20：候选池与证据披露同步到页面（评审反复指出报告里有、页面看不到）
    _pool = (summary or {}).get("pool_disclosure")
    if _pool:
        # v10.24.0：入池口径从 summary 动态读取（pipeline 按品类 gates 生成），
        # 不再写死「汽车之家紧凑型车+纯电动月销榜前10」——在轿车/SUV榜上完全错误。
        _criteria = e(_pool.get("criteria") or "")
        H.append(f'<div class="rule"><b>候选池与证据披露：</b>本池 {e(_pool["total"])} 款，'
                 f'带核验来源 {e(_pool["sourced"])}/{e(_pool["total"])}，'
                 f'有官方碰撞成绩 {e(_pool["crash"])}/{e(_pool["total"])}，'
                 f'证据 A/B/C = {e(_pool["ev_a"])}/{e(_pool["ev_b"])}/{e(_pool["ev_c"])}，'
                 f'网查补采 {e(_pool["extended"])} 款（置信低）。'
                 f'{_criteria}</div>')
        # v10.27.0：页面不再展示「未入池车型及原因」（用户要求剔除展示）；
        # 数据在 summary.pool_excluded 与 report.md 落榜名单中保留，仍可逐条审计。
        H.append('<div class="rule" style="background:#f7f6f3;border-left-color:#888780">'
                 f'<b>榜单定位：</b>这是本池 {len(rows)} 款候选车的<b>可审计横向对比榜</b>，'
                 '<b>不是全市场同级别车型的覆盖榜</b>；未入池车型不代表更差，'
                 '只是不在本期取证样本内。展示的 1–10 名是分数排序，'
                 '名次可信度以报告「同档组」为准，能不能买以「三档」为准。</div>')

    # ---- lede（注册区块，文案统一在 board_view）
    if "lede" in ids:
        H.append('<p class="lede">打分主榜是唯一权威名次来源（8 维加权）；事实指标降级为'
                 '<b>参考列 · 不计分</b>；纯产品力榜为并列辅助视角，不改主榜名次。</p>'
                 '<div class="rule"><b>三条规则：</b>① 主榜＝打分榜，唯一权威名次；'
                 '② kWh、电耗、电芯、月销等＝参考列，<b>只看不计分</b>；'
                 '③ 「不看价格比产品力」看纯产品力榜（全价位混排时价格越高性价比分天然越低，见下方提示）。</div>')

    # ---- 事实列覆盖披露
    if "fact_coverage" in ids:
        H.append('<!--VB:fact_coverage-->')  # v10.41.0 视图包装哨兵（无 h2 的区块需显式标记）
        have, lack = BV.fact_coverage(meta, rows)
        if lack:
            H.append(f'<div class="rule">事实参考列覆盖：本榜有数据的是 <b>{e("、".join(have))}</b>；'
                     f'「—」表示未采集（{e("、".join(lack))}），不以估计值填充。</div>')

    # ---- 打分主榜（v9.6.0：单表多列，含评分配置/指导价/月销/证据/周期/kWh万元/电池来源）
    if "main_board" in ids:
        grades = BV.fact_grades(rows)
        H.append("<h2>打分主榜 <span>按每车最高配置分排序（锚定到车系分量纲）· 事实列不参与排序</span></h2>")
        H.append(f'<div class="rule">v{_ver}：每款车取其<b>最高分配置</b>参与排名，逐配置分经标准差锚定到车系分量纲'
                 '（买车买的是具体配置）。无配置数据的车沿用原车系分。'
                 '点下方「逐配置对比」可展开看每款车的全部配置。</div>')
        H.append('<div class="rule">按<b>评分配置</b>逐车评分（配置不同分不同）；指导价/月销/kWh·万元/'
                 '电池来源为事实列不计分。kWh·万元 <b style="color:#1a7f37">▲优</b>（本池前25%）/'
                 '<b style="color:#b42318">▼弱</b>（后25%）；月销 <i>≤</i> 为车系合计上界。'
                 '证据 A=官方碰撞成绩 / B=口碑样本够 / C=新上市或样本不足。</div>')
        H.append('<div class="rule">配置下拉为<b>交互预览</b>：切换后主榜名次与分数实时重排，'
                 '但<b>档位、三榜并列、稳健性、审查边界按最高配置口径固定不变</b>（权威结论不随预览改变）。</div>')
        _stab = (summary or {}).get("stability") or {}
        _tau = _stab.get("kendall_mean")
        _tau_txt = ''
        if isinstance(_tau, (int, float)):
            _tau_grade = ('高（排序稳定）' if _tau >= 0.9 else
                          '中等（组内名次波动较大）' if _tau >= 0.8 else '较低（逐名排序不可靠）')
            _tau_txt = (f'算法一致性 τ=<b>{_tau:.3f}</b>（越接近1越一致，本次一致性'
                        f'<b>{_tau_grade}</b>；τ&lt;0.9 时请勿严格按数字先后选车）')
        _cross_band_txt = ''
        # v10.15.4：多价位混排时提示「性价比随价格下降≠产品力弱」，引导看纯产品力榜。
        # v10.29.0：价位段子榜/分段汇总已删除，改由 band_subboards 数据本身判 multi_band
        # （>=2 段即全价位混排），不再依赖被删的区块 id。单价位榜不显示，避免噪音。
        # 权重从 meta.dimensions 动态读取，不写死 11.7%——权重调整后文案自动同步。
        if len(bands) >= 2:
            _vdim = next((d for d in (meta.get("dimensions") or [])
                          if d.get("key") == "value"), None)
            _vpct = (f"{float(_vdim.get('weight'))*100:.1f}%"
                     if _vdim and isinstance(_vdim.get("weight"), (int, float)) else "约一成")
            _cross_band_txt = (
                ' <b style="color:#b42318">⚠ 跨价位提示：</b>本榜为全价位混排，含「性价比」维度'
                f'（权重 {_vpct}），价格越高该项天然越低——<b>高价车总分偏低≠产品力更弱</b>；'
                '「不看价格比硬件」请看下方「纯产品力总榜」。')
        H.append('<div class="stability-tip"><b>怎么读这张榜：</b>同档位内各行已用底色分组——'
                 '<b>第二档是「按偏好取舍组」，组内名次会随算法权重变动，请勿严格按数字先后选车</b>，'
                 '结合档位、评价与逐配置对比做决定。' + _tau_txt + '。'
                 '「唯一名次」只在本榜 8 维规则内成立，不是购车唯一结论。' + _cross_band_txt + '</div>')
        # v10.15.7：智驾低权重原因说明（用户："智驾占比太高，高速差别不大、城区不好用、开一年后城区一般不开"。
        # 调查证实"高装车低使用"，但智驾仍是门槛项，故保留非零权重。只在智驾权重≤10%的榜显示，
        # 避免污染跨能源榜(能源中性 13.5%)。权重从 meta.dimensions 动态读取，不写死 8%；
        # 文案客观写两面：为何低（使用率数据）+ 为何不清零（门槛项/保值率/本榜非堆料打分）。
        _adas_tip = ''
        _adim = next((d for d in (meta.get("dimensions") or [])
                      if d.get("key") == "adas"), None)
        _aw = _adim.get("weight") if _adim and isinstance(_adim.get("weight"), (int, float)) else None
        if _aw is not None and _aw <= 0.10:
            _awpct = f"{float(_aw)*100:.1f}%"
            _adas_tip = (
                f'<div class="stability-tip"><b style="color:#b42318">⚠ 关于智驾权重（本榜 {_awpct}）：</b>'
                '智驾权重低于三电/安全，是因为行业「<b>高装车、低使用</b>」：'
                + e(_narr.get("adoption") or "城区 NOA 装车率较高") + '，'
                '但' + e(_narr.get("active") or "实际主动开启率偏低") + '，'
                + e(_narr.get("usage") or "多数车主不常使用") + '；'
                + e(_narr.get("complaint") or "满意度调研中稳定性抱怨占比不低") + '。'
                '<b>权重低≠智驾不重要</b>——它仍是购车门槛项'
                '（' + e(_narr.get("musthave") or "多数用户视城市 NOA 为标配") + '），影响定价与保值率；'
                '本榜智驾分已按「硬件封顶+实测证据」打分，'
                '非配置堆料。重视智驾可重点看下方逐配置里的智驾硬件与证据列。'
                + (f'<br><span style="color:var(--ink3);font-size:12px">统计口径截至 '
                   f'{e(_narr.get("as_of"))}，来源：{e(_narr.get("source_note"))}</span>'
                   if _narr else '') + '</div>')
            H.append(_adas_tip)
        # v9.7.48：连续名次(补二值命中盲区) + 产品力≠市场热度口径说明
        _rm = _stab.get("rank_mean_by_model") or {}
        if _rm:
            _top_rm = sorted(_rm.items(), key=lambda kv: kv[1])[:5]
            _rm_txt = "、".join(f"{e(m)} 平均第{v:g}" for m, v in _top_rm)
            H.append(f'<div class="stability-tip"><b>算法平均名次（连续指标，补「进前三」硬截断的盲区）：</b>'
                     f'{_rm_txt}。平均名次越接近1越稳压；「家族命中0%」可能是"稳稳第5"而非不稳。</div>')
        if _stab.get("scope_note"):
            H.append('<div class="stability-tip" style="border-left-color:#16794a;background:#eef8f1">'
                     '<b>口径边界：</b>' + e(_stab["scope_note"]) + '</div>')
        tb_main = meta.get("trim_board") or {}
        # v9.7.34：低置信标记只在"有区分度"时显示——全high或全low时逐车标没有意义，
        # 全low是系统性数据问题，应在顶部提示修数据而非标每一款。
        _confs = {(tdm.get("best_confidence") or "") for tdm in tb_main.values()}
        _mixed_conf = ("low" in _confs or "低" in _confs) and any(
            c in ("high", "medium", "高", "中") for c in _confs)
        if "low" in _confs and not _mixed_conf and len(tb_main) > 1:
            H.append('<div class="rule" style="background:#fbf3e9">'
                     '<b>置信度提示：</b>本榜全部车型配置基准均为低置信（基准配置名与配置表对不上，'
                     '已用近似配置做基准）。逐车标「低置信」无区分意义，此处统一说明：'
                     '分数仅作同车配置相对参考，跨车型横向对比请谨慎。</div>')
        H.append('<table id="mainBoard"><thead><tr>' + "".join(f"<th>{e(BV.MAIN_COL_LABELS[k])}</th>"
                 for k in BV.MAIN_BOARD_COLUMNS) + "</tr></thead><tbody>")
        # v9.7.43：高分低档车的卡点（pipeline 导出），用于档位列"为何未进一档"标记
        _t1blk = {}
        if summary:
            for _v in (summary.get("t1_blockers") or {}).values():
                if isinstance(_v, dict):
                    _t1blk.update(_v)
        # v9.7.52：强推荐名单+原因，主榜车名后加金标（三档卡片区复用此变量）
        _strong, _strong_reason = set(), {}
        if summary:
            for _k, _v in (summary.get("strong_picks") or {}).items():
                _models = _v if isinstance(_v, list) else []
                _strong.update(_models)
                for _m in _models:
                    _strong_reason[_m] = ((summary.get("strong_pick_reasons") or {}).get(_k) or {}).get(_m, "")
        for r in rows:
            cand = r
            gmap = grades.get(r["model"], {})
            td = tb_main.get(r["model"]) or {}
            tier_cls = {"第一档": "T1", "第二档": "T2", "第三档": "T3"}.get(str(r.get("tier") or ""), "")
            H.append(f"<tr data-model='{e(r['model'])}' data-base-total='{r.get('total')}'"
                     f"{f' class=tier-{tier_cls}' if tier_cls else ''}>")
            for ci, k in enumerate(BV.MAIN_BOARD_COLUMNS):
                val = BV.main_cell(k, r, cand, gmap.get("kWh/万元", ""))
                if k == "rank":
                    H.append(f"<td class='c-rank'>{rank_badge(r.get('rank'))}</td>")
                elif k == "model":
                    sel = trim_selector(r["model"], td.get("trims") or [],
                                        r.get("best_trim"), width=160)
                    # v9.7.53：强推金标移到档位列（车名列不再重复显示）
                    H.append(f"<td class='name'>{e(val)}{risk_flags(r)}{sel}</td>")
                elif k == "total":
                    H.append(f"<td class='c-total'><span class='score'>{e(val)}</span></td>")
                elif k == "crash":
                    H.append(f"<td>{crash_cell(r, cand)}</td>")
                elif k == "tier":
                    _tier_cell = tier_badge(r.get("tier"))
                    _is_strong = r["model"] in _strong
                    if _is_strong:
                        # v9.7.53：强推金标放档位列，hover讲清"为何未进一档+为何仍强推"
                        _why = ("家族命中不够稳压（换算法会换位）" if _strong_reason.get(r["model"]) == "family"
                                else "连续名次稳定、只是排在第5左右")
                        _extra = ""
                        _bi_s = _t1blk.get(r["model"])
                        if _bi_s and (r.get("rank") or 99) <= 10:
                            _extra = "未进第一档因：" + "；".join(_bi_s.get("blockers") or []) + "。"
                        _tip_s = (f"强推荐：有权威碰撞实锤+高分+无硬风险。{_why}。"
                                  f"{_extra}与第一档共同构成可优先看清单——车本身没短板，按你的偏好取舍即可")
                        _tier_cell += (f'<b class="strong-main" title="{e(_tip_s)}">强推</b>')
                    else:
                        # v9.7.43：高分(TOP10)却未进一档的车，档位列加"为何未进一档"标记
                        # （强推车已在上面金标里解释，不重复叠加两个标记）
                        _bi = _t1blk.get(r["model"])
                        if _bi and (r.get("rank") or 99) <= 10:
                            _bl = list(_bi.get("blockers") or [])
                            # 碰撞缺失注明"非不安全"，避免误导
                            _bl = [("无权威碰撞成绩（非不安全，仅证据等级较低）" if "无权威碰撞成绩" in b else b)
                                   for b in _bl]
                            _tip = "；".join(_bl) + "。——不在意这些因素的用户可将其纳入购买考虑，非产品力差。"
                            _tier_cell += (f'<span class="flag flag-lowtier" title="{e(_tip)}">'
                                           f'为何未进一档</span>')
                    H.append(f"<td>{_tier_cell}</td>")   # 档位固定，不参与联动
                elif k == "evidence":
                    cls = {"A": "delta-up", "B": "", "C": "delta-down"}.get(val, "")
                    if val == "C":
                        is_fleet = bool(r.get("tier_risk")) or any(
                            k in str(r.get("note") or "") for k in ("营运", "网约", "B端"))
                        if is_fleet:
                            lab = ('<span class="flag flag-fleet" '
                                   'title="营运/交付硬风险，第三档暂缓">营运风险</span>')
                        else:
                            lab = ('<span class="flag flag-warn" '
                                   'title="证据等级C：新上市或口碑样本不足（非碰撞成绩缺失）；排序稳定但一手实锤有限，不宜对标A类证据">证据不足</span>')
                        H.append(f"<td class='{cls}'>{lab}</td>")
                    else:
                        H.append(f"<td class='{cls}'>{e(val)}</td>")
                elif k == "kwh_per_wan":
                    g = gmap.get("kWh/万元", "")
                    H.append(f'<td class="c-kwh" style="color:{BV.grade_color(g) if g else "inherit"}">{e(val)}</td>')
                elif k == "trim":
                    cf = (td.get("best_confidence") or "")
                    cf_flag = ('<span class="flag flag-est" title="低置信=配置匹配或一手证据不足；分数仅用于同车配置对比，不适合跨车型横向对比">低置信</span>'
                               if _mixed_conf and cf in ("low", "低") else "")
                    H.append(f"<td class='ref c-trim'>{e(val)}{cf_flag}</td>")
                elif k == "price":
                    H.append(f"<td class='c-price'>{e(val)}</td>")
                elif k == "cell_brand":
                    H.append(f"<td class='ref c-cell'>{e(val)}</td>")
                elif k == "sales":
                    # v10.28.0：hover 给出趋势月销计算公式 + 本车口径（纯电/车系合计上界/异常注记）
                    H.append(f'<td title="{e(sales_tip(r))}" style="cursor:help">{e(val)}</td>')
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
            # v10.24.0：示例从本池数据动态取（此前写死具体车型及各项分值，换榜即错、
            # 分值调整后与算法漂移）。取三电最高-最低配置差最大的一款作示例。
            _demo = ""
            for _m, _td in tb.items():
                _ts = (_td or {}).get("trims") or []
                _pw = [float(x.get("scores", {}).get("powertrain") or 0) for x in _ts
                       if x.get("scores", {}).get("powertrain") is not None]
                if len(_pw) >= 2:
                    _gap = max(_pw) - min(_pw)
                    if _gap > 0.05 and (not _demo or _gap > _demo[0]):
                        _demo = (_gap, _m, max(_ts, key=lambda x: float(
                            (x.get("scores") or {}).get("powertrain") or 0)).get("name", ""))
            _demo_txt = (f'例如本池 {e(_demo[1])} 的 {e(_demo[2])} 三电分最高，'
                         '展开可看各项得分理由。') if _demo else '展开可看每个配置的三电分与得分理由。'
            H.append('<div class="rule">每个配置按 spec_score 规则单独算分，<b>★＝该车最高分配置，也是主榜用来排名的配置</b>。'
                     '看对比请盯「三电」列和最后一列「得分理由」——' + _demo_txt +
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
                    why = "、".join((t.get("reasons") or {}).get("powertrain") or []) or "基准配置（无配置级差异）"
                    note = t.get("note") or ""
                    cell_val = str(t.get("cell_supplier") or "未披露")
                    if t.get("cell_inherited"):
                        cell_val += '<span class="flag flag-est" title="车系级电芯，未逐配置核验">车系</span>'
                    charge_val = str(t.get("charge") or "未披露")
                    H.append(f"<tr><td class='name'>{e(t.get('name'))}{flag}</td>"
                             f"<td>{e(t.get('price'))}</td>"
                             f"<td>{_kwh_cell(t, r.get('note'))}</td>"
                             f"<td class='ref'>{cell_val}</td>"
                             f"<td class='ref'>{e(charge_val)}</td>"
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
        H.append(f'<div class="rule"><b>分数证据类型（v{_ver}）：</b>本榜所有维度分均为'
                 '<b>官方参数 + 客观差异规则推算</b>（来源：配置表/碰撞成绩/销量），'
                 '<b>不是实车实测分数</b>；配置间差异由「三电得分理由」逐条列明。'
                 '标注 <span class="flag flag-est">低置信</span> 表示基准配置匹配或证据不足，'
                 '该行分数只作同车配置相对参考。实车实测数据（如冬测续航达成率）目前未纳入打分。</div>')
        H.append('<table id="scoreBoard"><thead><tr><th>#</th><th>车型</th><th>评分配置</th><th>指导价(万)</th>')
        for d in dims:
            H.append(f"<th>{e(d.get('label'))}<br><small>{float(d.get('weight',0))*100:.1f}%</small></th>")
        H.append("<th>加权分</th></tr></thead><tbody>")
        tb = meta.get("trim_board") or {}
        for r in rows:
            sc = r.get("scores") or {}
            td = tb.get(r["model"]) or {}
            # 配置下拉：与主榜联动（class=trimsel 共用同一处理函数）
            sel = trim_selector(r["model"], td.get("trims") or [],
                                r.get("best_trim"), width=150)
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
        # v9.7.19：维度子项依据（折叠，默认收起，不破坏主榜简洁）
        H.append('<div class="rule" style="margin-top:12px"><b>维度子项依据（点开看每维分怎么来的）：</b>'
                 '分数来自官方参数 + 配置差异规则推算，非实车实测。配置级差异逐条列出，'
                 '无差异的维度显示车系基准分。</div>')
        for r in rows:
            bd_html = dim_breakdown(r, (meta.get("trim_board") or {}).get(r["model"]) or {}, dims)
            if bd_html:
                H.append(bd_html)
        # 配置切换联动脚本：三表联动（主榜/总分构成/产品力），事件委托绑定
        if tb:
            s2 = json.dumps(tb, ensure_ascii=False)
            js = r"""<script>
var TRIMDATA=__DATA__;
function trimCellVals(model,idx){
  var t=TRIMDATA[model].trims[idx],sc=t.scores||{},charge=String(t.charge||''),
      cell=String(t.cell_supplier||'—'),kwh=t.battery_kwh,price=t.price;
  var mv=charge.match(/(\d{3,4})\s*V/);
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
      if(dc){dc.innerHTML=d>0?'<span title="rank_delta '+((d>0?'+':'')+d)+'">被性价比拖累</span>':
        d<0?'<span title="rank_delta '+d+'">靠性价比抬升</span>':'<span title="rank_delta 0">无明显影响</span>';
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
        _tcb = (summary or {}).get("tier_crossboard") or {}
        _tcn = {"T1": "第一档", "T2": "第二档", "T3": "第三档"}
        _robust = {}
        for _v in ((summary or {}).get("robust_t1") or {}).values():
            if isinstance(_v, dict):
                _robust.update(_v)
        # v10.20.0：只给跨榜变档的车加「全榜第X档」小标；判档仍以本榜 tiers 为准
        for k in ("T1", "T2", "T3"):
            models = tiers.get(k) or []
            lis = []
            for m in models:
                _nm = e(m)
                # v10.30.0：稳健通道入档的水桶车标「安心首选」（hover 给判据）
                if k == "T1" and m in _robust:
                    _nm += (f'<b class="strong-main" title="{e(_robust[m])}">'
                            '★水桶无短板</b>')
                _g = _tcb.get(m)
                if _g and _g != _tcn[k]:
                    _nm += ('<span class="xboard-tier" title="档位按本榜候选池判定，'
                            '跨榜不可直接比较；跨榜请直接比较总分与八维分">'
                            + f"全榜{e(_g)}</span>")
                # v9.7.52：强推荐名单复用主表循环前已收集的 _strong/_strong_reason
                if k == "T2" and m in _strong:
                    _tip = ('有权威碰撞实锤+高分，家族命中不够稳压（换算法会换位）'
                            if _strong_reason.get(m) == 'family' else
                            '有权威碰撞实锤+高分+连续名次稳定（稳定地排第5左右），只是未在多数算法进前三')
                    _nm += f'<b class="strong-tag" title="{e(_tip)}">强推荐</b>'
                lis.append(f"<li>{_nm}</li>")
            H.append(f'<div class="tier-card {k}"><h3>{names[k]}（{len(models)} 款）</h3>'
                     f'<ul>{"".join(lis) if lis else "<li>—</li>"}</ul></div>')
        H.append("</div>")
        H.append('<div class="rule"><b>跨榜档位口径：</b>上面的「家族命中 / 进前三」'
                 '按本榜候选池判定，不是绝对分数线；同一台车在不同价位榜可能档位不同，'
                 '跨榜请直接比总分与八维分。</div>')
        if _strong:
            H.append('<div class="rule"><b>强推荐说明（与第一档共同构成"可优先看"清单）：</b>'
                     '第二档带「强推荐」的车，有权威碰撞实锤、总分高、无硬风险——'
                     '要么家族命中不够（换算法会换位），要么连续名次稳定只是排在第5左右。'
                     '两类都是"车本身没短板，只是不够稳压"，按你的品牌/充电/保值偏好取舍即可。'
                     '第一档只有几款，不代表只有那几款能买——强推荐同样值得认真看。</div>')
        _es = (summary or {}).get("evidence_soft") or []
        if _es:
            _lines = "；".join(f"{e(x.get('model'))}（{e(x.get('tier'))}）" for x in _es)
            H.append(f'<div class="rule"><b>证据分级提示（v{_ver}）：</b>{_lines} '
                     '一手证据不足（新上市/样本少/缺权威碰撞成绩）——已降低第一档资格，'
                     '<b>但不因此判劣或打入「暂缓」</b>；真正的硬风险（停产/无交付/营运为主）才进第三档。</div>')

    # v10.29.0：价位段子榜 / 分段汇总总榜已按用户要求从页面删除（数据仍保留在
    # board_v2_data.json，跨价位提示仍用 bands 判定 multi_band，不影响打分/三档）。

    # ---- 纯产品力总榜
    if "product_power" in ids:
        H.append("<h2>纯产品力总榜 <span>辅助榜 · 剔除性价比、全池混排 · 不改主榜任何名次</span></h2>")
        H.append('<div class="rule">主榜（8 维含性价比）仍是唯一权威排序；本榜只回答「不看价格谁的产品力更强」。'
                 '<b>与性价比的关系</b>：产品力更强但价格拖累 → 标「被性价比拖累」；靠价格优势上位 → 标「靠性价比抬升」。</div>')
        tbpp = meta.get("trim_board") or {}
        H.append('<div class="rule">下拉框切换配置后，本榜产品力分（剔除性价比的 7 维）实时重算并重排；'
                 '与主榜/总分构成联动。标签按主榜当前名次实时重算，鼠标悬停可看原始数字。</div>')
        H.append('<table id="powerBoard"><thead><tr><th>#</th><th>车型</th><th>产品力分</th>'
                 '<th>主榜名次</th><th>与性价比的关系</th><th>价格</th></tr></thead><tbody>')
        for it in pp.get("items") or []:
            d = it.get("rank_delta")
            dcls = "delta-up" if isinstance(d, int) and d > 0 else ("delta-down" if isinstance(d, int) and d < 0 else "")
            mdl = it.get("model")
            td = tbpp.get(mdl) or {}
            sel = trim_selector(mdl, td.get("trims") or [],
                                td.get("best_trim"), show_price=False, width=150)
            H.append(f"<tr data-model='{e(mdl)}'><td class='c-prank'>{it.get('power_rank')}</td>"
                     f"<td class='name'>{e(mdl)}{sel}</td>"
                     f"<td class='c-power'><span class='score'>{num(it.get('power_score'), 2)}</span></td>"
                     f"<td class='c-mainrank'>#{e(it.get('main_rank'))}</td>"
                     f"<td class='{dcls} c-delta'>"
                     f"{delta_label(d)}</td>"
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

    # ---- 三电与补能事实（v10.11.0：把已采到的一手补能事实摊开给读者）
    if "energy_facts" in ids:
        ef = data.get("energy_facts") or {}

        def _num_unit(v, unit=""):
            return f"{v:g}{unit}" if isinstance(v, (int, float)) else "—"

        H.append("<h2>三电与补能 <span>逐车补能硬件与官方电耗 · 事实列不计分</span></h2>")
        H.append('<div class="rule">电压/快充/最大充电功率/官方电耗取自汽车之家车系参数页'
                 '「电池/充电」组；电芯逐配置取自「电芯品牌」，官方未标时按「电池特有技术」的'
                 '官方产品名映射厂商（刀片→弗迪、麒麟→宁德、金钟罩→中创新航）。'
                 '「未披露」= 官方页面确实没填，<b>不做推断、不插补</b>。'
                 '本页展示的是评分配置那一档。'
                 '<b>缺口披露</b>：电池/整车质保年限与充电循环次数，'
                 '汽车之家参数页<b>不提供该字段</b>（未采集，不推断、不编造）——'
                 '按质保年限横向比较只能人工核验厂商官网。</div>')
        # v10.15.5：两列扩为三列「电池 / 电机 / 电控」——用户诉求「三电=电池+电机+电控，
        # 别只看电池」。电池=电芯+容量+续航+冷却+快充（v10.20.1：快充倍率属电池本体
        # 材料×热管理能力）；电机=峰值功率（硬件规格参考）；电控=高压平台（补能架构）。
        # 三电规则项此前 6 项全电池，现补齐电机项。
        # 列的值来自 rule_adjust 的审计字段（逐项可复算），不是另算一套。
        H.append("<table><thead><tr><th style='text-align:left'>车型</th>"
                 "<th style='text-align:left'>评分配置</th><th>电量</th><th>高压平台</th>"
                 "<th style='text-align:left'>快充</th><th>最大充电功率</th><th>官方电耗</th>"
                 "<th style='text-align:left'>电芯</th><th>换电</th><th>CLTC</th><th>峰值功率</th>"
                 "<th>电池</th><th>电机</th><th>电控</th></tr></thead><tbody>")
        for r in rows:
            f = ef.get(r.get("model")) or {}
            if not f:
                continue
            # 分组来自 energy_facts（rule_adjust 明细已透传到那里），不另找来源
            _g = f.get("groups") or {}
            _groups_txt = {}
            for _gname in ("电池", "电机", "电控"):
                _grp = _g.get(_gname) or {}
                _groups_txt[_gname] = (
                    f"{_grp.get('sum'):+.1f}" if _grp.get("sum") is not None else "—")
            _title_parts = []
            for _gname in ("电池", "电机", "电控"):
                _grp = _g.get(_gname) or {}
                if _grp.get("items"):
                    _title_parts.append(_gname + "：" +
                                        "、".join(i.get("text") or "" for i in _grp["items"]))
            _title = e(" ｜ ".join(_title_parts)) if _title_parts else ""
            H.append(f"<tr><td class='name'>{e(r.get('model'))}</td>"
                     f"<td style='text-align:left'>{e(str(f.get('trim') or '—'))}</td>"
                     f"<td>{_num_unit(f.get('kwh'), ' kWh')}</td>"
                     f"<td>{_num_unit(f.get('voltage_v'), ' V')}</td>"
                     f"<td style='text-align:left'>{e(str(f.get('charge') or '—'))}</td>"
                     f"<td>{_num_unit(f.get('charge_power_kw'), ' kW')}</td>"
                     f"<td>{_num_unit(f.get('consumption'), ' kWh/100km')}</td>"
                     f"<td style='text-align:left'>{e(str(f.get('cell') or '未披露'))}</td>"
                     f"<td>{e(str(f.get('swap') or '—'))}</td>"
                     f"<td>{_num_unit(f.get('cltc_km'), ' km')}</td>"
                     f"<td>{_num_unit(f.get('max_power_kw'), ' kW')}</td>"
                     f"<td title=\'{_title}\'>{_groups_txt['电池']}</td>"
                     f"<td title=\'{_title}\'>{_groups_txt['电机']}</td>"
                     f"<td title=\'{_title}\'>{_groups_txt['电控']}</td></tr>")
        H.append("</tbody></table>")
        H.append("<div class='rule'>三电按业内「<b>电池 / 电机 / 电控</b>」三分展示："
                 "<b>电池</b>=电芯+容量+续航+热管理+快充时间；<b>电机</b>=峰值功率（硬件规格参考，"
                 "按榜内中位相对化、粗分档避免 10kW 噪音）；<b>电控</b>=高压平台（补能架构）。"
                 "<b>快充归电池</b>：快充倍率的瓶颈是电芯材料体系×热管理（均为电池本体属性），"
                 "800V 高压平台只是使能条件，避免把慢充车的电池列抬高。三列是三电规则项的分组求和，<b>只用于披露、不改三电总分算法</b>。"
                 "<b>口径边界</b>：业内更看重的额定功率/效率MAP/最高转速（电机）与 SiC/MCU 效率/"
                 "BMS（电控）本池结构化覆盖 0/20，未进分；电耗效率修正（车重残差）与续航达成率修正"
                 "属「电控+整车系统效率」，作为独立模块计入三电总分，不在上述规则分组内。"
                 "峰值功率只维持约 10s、水分较大，故只作规格参考而非主导项。悬停可看逐项依据。")
        H.append("</div>")

    # ---- 空间与储物事实（v10.11.0）
    if "space_facts" in ids:
        sf = data.get("space_facts") or {}

        def _mm(v):
            return f"{v:g}mm" if isinstance(v, (int, float)) else "—"

        def _l(v):
            return f"{v:g}L" if isinstance(v, (int, float)) else "—"

        H.append("<h2>空间与储物 <span>车身尺寸与官方容积 · 后备箱大≠乘坐空间好</span></h2>")
        H.append('<div class="rule">轴距/长宽高/后备箱/前备箱均取自汽车之家/易车参数页官方字段（稳定可核）。'
                 '<b>不列「下沉储物/得房率/后排腿部/后排膝部」</b>：官方参数页无这些标准字段，'
                 '得房率系厂商自量自宣、无统一计算口径（行业共识），后排腿/膝部仅见口径不一的媒体实测，'
                 '跨车型不可比——按「不编造、不插补」纪律不展示。'
                 '空间维封顶的依据是<b>车身三维体积 + 少数车可得房率</b>；后备箱容积只作旁证，'
                 '<b>容积大不加分</b>——后备箱装东西、乘坐空间坐人，不是同一件事。</div>')
        H.append("<table><thead><tr><th style='text-align:left'>车型</th><th>轴距</th>"
                 "<th>长×宽×高</th><th>后备箱</th><th>前备箱</th></tr></thead><tbody>")
        for r in rows:
            f = sf.get(r.get("model")) or {}
            if not f:
                continue
            _L, _W, _H = f.get("length_mm"), f.get("width_mm"), f.get("height_mm")
            dims = (f"{_L:g}×{_W:g}×{_H:g}mm"
                    if all(isinstance(x, (int, float)) for x in (_L, _W, _H)) else "—")
            H.append(f"<tr><td class='name'>{e(r.get('model'))}</td>"
                     f"<td>{_mm(f.get('wheelbase_mm'))}</td>"
                     f"<td>{e(dims)}</td>"
                     f"<td>{_l(f.get('trunk'))}</td>"
                     f"<td>{_l(f.get('frunk'))}</td></tr>")
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

    H.append('<!--VB:__footer__-->')  # v10.41.0：footer 不参与视图包装
    H.append(f"""<div class="foot">可信购车榜单引擎 · 展示区块注册表 board_view.py（{len(active)} 项已渲染）<br>
主榜调用 leaderboard.score_rows，事实列不计分、子榜复用分数均由 selftest 验证。</div>
</div>
<script>
// v10.41.0 简版/完整版切换：纯前端，不改数据/分数；记住用户选择。
function setBoardView(v){{
  document.body.classList.remove('view-simple','view-full');
  document.body.classList.add('view-'+v);
  var s=document.getElementById('btn-simple'), f=document.getElementById('btn-full');
  if(s) s.className=(v==='simple'?'on':'');
  if(f) f.className=(v==='full'?'on':'');
  var h=document.getElementById('view-hint');
  if(h) h.style.display=(v==='simple'?'':'none');
  try{{localStorage.setItem('car_rank_view',v)}}catch(e){{}}
}}
(function(){{try{{if(localStorage.getItem('car_rank_view')==='full')setBoardView('full')}}catch(e){{}}}})();
</script>
</body></html>""")
    # v9.7.14：给每个 h2 加锚点 id（按标题文字匹配 BLOCKS，不依赖渲染顺序）
    def _add_anchor(m):
        inner = m.group(1)
        first = re.sub(r"<[^>]+>", "", inner).strip().split()[0] if inner.strip() else ""
        bid = NAV_BY_TITLE.get(first, "")
        return f'<h2 id="sec-{bid}">' + inner + '</h2>' if bid else m.group(0)
    H = [re.sub(r"<h2>(.*?)</h2>", _add_anchor, blk) if isinstance(blk, str) else blk for blk in H]
    html = "\n".join(H)
    # v10.41.0：统一包装每个区块为 <div class="vblk" data-view="core|extra">。
    # 边界 = 有 h2 锚点的区块（sec-XXX）+ 无 h2 的哨兵（fact_coverage）；
    # data-view 取自 board_view.block_view() 单一事实源。纯展示层包装，不碰任何数据。
    boundaries = list(re.finditer(r'<!--VB:fact_coverage-->|<h2 id="sec-([A-Za-z_][\w]*)">', html))
    if boundaries:
        out = [html[:boundaries[0].start()]]
        for i, m in enumerate(boundaries):
            end = boundaries[i + 1].start() if i + 1 < len(boundaries) else len(html)
            seg = html[m.start():end]
            seg = re.sub(r'^<!--VB:fact_coverage-->', '', seg)
            bid = m.group(1) or "fact_coverage"
            view = BV.block_view(bid)
            out.append(f'<div class="vblk" data-view="{view}" data-block="{bid}">{seg}</div>')
        html = "".join(out)
    # footer 哨兵不参与包装，但要去掉标记本身
    html = html.replace('<!--VB:__footer__-->', '')
    return html


def selftest():
    """契约自检：注册表必要区块一个不少；多段数据下两版渲染器展示同一组区块。"""
    from modules.board import board_view as BV
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
    # v10.42：带三电/空间数据的扩展 fixture，测这两块的 h2 锚点与 core 视图包装
    _ed = dict(data)
    _ed["energy_facts"] = {"A车": {"battery_kwh": 60, "cell": "CATL", "groups": {}},
                           "B车": {"battery_kwh": 75, "cell": "宁德", "groups": {}}}
    _ed["space_facts"] = {"A车": {"wheelbase": 2900, "length": 4800, "trunk": 500},
                          "B车": {"wheelbase": 2950, "length": 4900, "trunk": 480}}
    _energy_doc = render(_ed, None, "T", "202610")
    # v10.15.4 跨价位提示回归：多段显示 / 单价位不显示 / 权重动态读取（不写死百分比）
    _single = dict(data)
    _single["band_subboards"] = data["band_subboards"][:1]  # 1 段 → multi_band=False
    single_doc = render(_single, None, "T", "202610")
    _w2 = dict(data)
    _w2["meta"] = dict(data["meta"])
    _w2["meta"]["dimensions"] = [{"key": "powertrain", "label": "三电", "weight": .75},
                                 {"key": "value", "label": "性价比", "weight": .25}]
    doc_w2 = render(_w2, None, "T", "202610")
    checks = [
        ("主榜车型", "A车" in doc),
        ("分数", "88.0" in doc),
        ("三档", "第一档" in doc and "第二档" in doc),
        ("维度明细", "总分构成" in doc),
        ("逐车取证", "逐车取证依据" in doc),
        ("方法与边界", "方法与边界" in doc),
        ("复现", "复现说明" in doc),
        ("v10.29.0价位段子榜已删", "价位段子榜" not in doc and "入门段" not in doc),
        ("v10.29.0分段汇总已删", "分段汇总总榜" not in doc),
        ("产品力榜", "纯产品力总榜" in doc and "与性价比的关系" in doc),
        ("事实覆盖披露", "事实参考列覆盖" in doc),
        ("档位徽标class用英文T1/T2/T3", 'class="tier T1"' in doc and 'class="tier T2"' in doc),
        ("档位徽标中文可读", "第一档" in doc and "第二档" in doc),
        ("档位CSS颜色规则存在", ".tier.T1{background:var(--t1)}" in CSS),
        ("跨价位提示·多段显示", "跨价位提示" in doc and "纯产品力总榜" in doc),
        ("跨价位提示·权重动态读取", "40.0%" in doc and "25.0%" in doc_w2 and "40.0%" not in doc_w2),
        ("跨价位提示·单价位不显示", "跨价位提示" not in single_doc),
        # v10.41.0 简版/完整版视图：默认简洁、一键切完整、core/extra 分组来自单一事实源
        ("视图切换条存在", 'id="viewbar"' in doc and 'btn-simple' in doc and 'btn-full' in doc),
        ("视图切换JS存在", "function setBoardView" in doc and "setBoardView('full')" in doc),
        ("视图默认简洁", 'body class="view-simple"' in doc),
        ("视图CSS隐藏extra", 'body.view-simple .vblk[data-view="extra"]{display:none}' in CSS),
        ("视图CSS隐藏extra导航", 'body.view-simple .navgrid a[data-view="extra"]{display:none}' in CSS),
        ("视图core含主榜三档产品力", all(
            f'data-view="core" data-block="{bid}"' in doc
            for bid in ("main_board", "tiers", "product_power"))),
        # v10.42 用户追加：三电与补能/空间与储物进简版（映射函数单一源，实测数据块可能不在小 fixture）
        ("视图core含三电与空间", BV.block_view("energy_facts") == "core"
         and BV.block_view("space_facts") == "core"),
        # core 区块必须有 h2 锚点（否则视图包装边界识别不到、会两版都显示）：
        # 用带三电/空间数据的扩展 fixture 实测锚点 id 与 core 包装
        ("三电空间h2锚点+core包装", all(
            f'id="sec-{bid}"' in _energy_doc and f'data-view="core" data-block="{bid}"' in _energy_doc
            for bid in ("energy_facts", "space_facts"))),
        ("视图提示含三电与空间", "三电与补能 + 空间与储物" in doc),
        ("视图extra不误标核心", not any(
            f'data-view="core" data-block="{bid}"' in doc
            for bid in ("trim_compare", "score_breakdown", "method_boundary"))),
        ("视图包装无哨兵残留", "<!--VB:" not in doc),
    ]
    # v10.15.7 智驾低权重提示回归：权重≤10%显示 / >10%不显示 / 权重动态读取（不写死百分比）
    _a1 = dict(data)
    _a1["meta"] = dict(data["meta"])
    _a1["meta"]["dimensions"] = [{"key": "powertrain", "label": "三电", "weight": .25},
                                  {"key": "value", "label": "性价比", "weight": .122},
                                  {"key": "adas", "label": "智驾", "weight": .08}]
    doc_adas8 = render(_a1, None, "T", "202610")
    _a2 = dict(data)
    _a2["meta"] = dict(data["meta"])
    _a2["meta"]["dimensions"] = [{"key": "powertrain", "label": "三电", "weight": .18},
                                  {"key": "value", "label": "性价比", "weight": .117},
                                  {"key": "adas", "label": "智驾", "weight": .135}]
    doc_adas135 = render(_a2, None, "T", "202610")
    checks += [
        ("智驾提示·≤10%显示", "关于智驾权重" in doc_adas8 and "6.3%" in doc_adas8),
        ("智驾提示·权重动态读取", "8.0%" in doc_adas8),
        ("智驾提示·>10%不显示", "关于智驾权重" not in doc_adas135),
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
    ap.add_argument("--as-of", default=None, help="销量数据月(YYYYMM)，默认自动取上个月")
    ap.add_argument("--selftest", action="store_true", help="结构自检（不依赖真实数据）")
    a = ap.parse_args()

    if a.selftest:
        sys.exit(selftest())
    if not a.data or not a.out:
        ap.error("渲染需要 --data 与 --out（或加 --selftest）")

    _as_of = a.as_of
    if not _as_of:
        try:
            from modules.board import monthly as _MO
            _as_of = _MO.default_sales_month()
        except Exception:
            _as_of = "—"
    data = json.load(open(a.data, encoding="utf-8"))
    summary = json.load(open(a.summary, encoding="utf-8")) if a.summary and os.path.exists(a.summary) else None
    doc = render(data, summary, a.title, _as_of)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    open(a.out, "w", encoding="utf-8").write(doc)
    print(f"[ok] {a.out}（{len(doc)} 字符）")


if __name__ == "__main__":
    main()
