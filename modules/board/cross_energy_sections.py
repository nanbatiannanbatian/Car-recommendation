# -*- coding: utf-8 -*-
"""
cross_energy_sections.py — 跨能源正式榜的 20 区块导航与补充区块（B类，v10.53.0）
对齐 board_view.py 的区块契约思路，但适配跨能源数据结构。

【架构变更 v10.53.0（2026-11-24 跨能源比较第二轮优化）】
  新增 form_fit「能源形式适配结论」区块：先按能源形式聚合（均分/硬条件率/不建议项）
  回答"你这种条件适合哪种能源"（engine.recommend_form + form_narrative），
  再进逐车榜；画像卡同时展示可选选项中文标签与 check_profile 自检告警。
  因素 15→20（家庭车队/地形/停放/驻车用电/电价档，充电条件增 work 档）；
  v10.55.0 因素 20→13（删夏季气温/驾驶风格/空调/停放/驻车用电/露营/电价档，理由见 engine.py 头部），
  定稿快照与推演器仍分离。

【架构变更 v10.52.0（2026-11-24 跨能源比较优化）】
  定稿榜全部**服务端渲染**（envscore/engine.py 计算）：画像卡/综合榜/逐条件判定/
  TCO/推荐结论/三榜并列/稳健性 均为 Python 算好的静态 HTML——定稿结论可复现、
  可回归审计，不再依赖"页面首次加载用 JS 算一次"。
  场景推演器（比较器主区的 11 项下拉 + 实时表）仍是 JS 交互（engine.js 镜像），
  只更新推演态标识 sim-state，绝不改写定稿区块。

设计原则（承 v9.7.49）：
  ① 定稿榜 = 默认/给定画像下的固定快照，可复现、可截图、可引用；
  ② 推演器 = 个人探索，同页分离防时态混乱；
  ③ 前置条件：B 类混合池必须带 usage_profile（构建器断言），画像卡明示来源。

环境分引擎单一事实源 = modules/envscore/engine.py（规则）+ build_energy_compare.py
（页面生成）；本模块只编排展示，不重写引擎。
"""
import json

from modules.envscore import engine as ENG

# 20 区块清单
BLOCKS = [
    ("lede",             "导语与规则"),
    ("profile_card",     "用车条件画像"),
    ("form_fit",         "能源形式适配结论"),
    ("main_board",       "打分主榜"),
    ("condition_verdicts", "逐条件适配判定"),
    ("tco_block",        "持有成本测算"),
    ("recommend",        "推荐结论"),
    ("trim_compare",     "逐配置对比"),
    ("score_breakdown",  "总分构成"),
    ("tiers",            "三档分级"),
    ("band_subboards",   "价位段子榜"),
    ("product_power",    "纯产品力总榜"),
    ("energy_matrix",    "能源形式对比"),
    ("evidence_notes",   "逐车取证依据"),
    ("lifecycle",        "生命周期提示"),
    ("method_boundary",  "方法与边界"),
    ("reproduce",       "复现说明"),
    ("three_board",      "三榜并列"),
    ("robustness",       "稳健性"),
    ("audit_boundary",   "审查边界"),
]
REQUIRED = {"main_board", "profile_card", "form_fit", "condition_verdicts", "tco_block", "recommend",
            "score_breakdown", "tiers", "product_power", "evidence_notes",
            "method_boundary", "reproduce", "three_board", "robustness", "audit_boundary"}

# 定稿榜 20 区块全部是服务端渲染的静态快照——不随推演器下拉变化。
DYNAMIC_BLOCKS = set()
FIXED_BLOCKS = set(b[0] for b in BLOCKS)

# 稳健性的 8 个代表性场景（跨这些场景看名次跨度 = 真正的稳健性分析）
ROBUST_SCENARIOS = [
    ("默认画像", {}),
    ("无家充", {"home_charger": "none"}),
    ("北方极寒", {"winter_temp": "underm20"}),
    ("长途高频", {"long_trip_freq": "weekly", "single_trip": "over500"}),
    ("营运", {"operation": "yes"}),
    ("限牌城市", {"plate_limit": "yes"}),
    ("京沪限牌", {"plate_limit": "beijing"}),
    ("短持有", {"hold_years": "under3"}),
    ("增购第二辆", {"fleet": "second"}),
]

POWER_CN = {"ICE": "燃油", "HEV": "油混", "PHEV": "插混", "EREV": "增程", "BEV": "纯电"}

# 能源形式对比矩阵（事实性结论，信源见 outputs/用车条件因素库）
ENERGY_MATRIX = [
    ("ICE", "补能最快、加油即走；高速工况稳定；保值率中位(46.1%)；购置税10%全额",
     "限牌城市上牌难；营运能耗成本高；油价敏感；智驾/座舱普遍落后同价自主新能源",
     "WLTC油耗7-9L；长途单次>500km 无焦虑；2026购置税约车价8.85%"),
    ("HEV", "市区拥堵最省油(4.2-5.2L)；无需充电、加油即走；保值率最稳(约50%)；无需绿牌",
     "无绿牌（限牌城市同油车）；高速省油优势<1L溢价难回收；智能化普遍弱",
     "WLTC 4.1-4.5L；电池8年/20万公里质保；双擎溢价8000-1.5万，市区为主2-3年回本"),
    ("PHEV", "有家充时日常当纯电开(纯电128-210km)；高速发动机直驱效率高(亏电3.3-5.5L)；可油可电",
     "无家充长期亏电油耗恶化；两套系统都要保养；短持有残值受压(42.2%)",
     "2026购置税减免门槛：WLTC纯电≥100km且馈电油耗低于国标限值70%"),
    ("EREV", "纯电续航长(140-316km)、全程电驱最平顺；综合续航1100km+；有家充时城市成本近纯电",
     "高速亏电油耗高(7-9L，油→电→轮二次转换)；无家充时经济性大幅缩水；增程器NVH",
     "高速占比>40%且无充电条件时慎选；北方冬季发动机余热供热是优势"),
    ("BEV", "能源成本最低(家充约0.05元/km)；智驾/座舱普遍领先；纯电优先原则基准项；换电车型补能近加油",
     "冬季续航腰斩(-20℃约×0.5)；无家充大幅减分；高速达成56-75%；保费高15-20%；短持有残值弱",
     "家充盈亏平衡约0.9-1.3万km/年(2026油价口径)；热泵+电池预热车型冬季衰减更轻"),
]


def nav_html():
    """顶部导航卡：指向各区块锚点。"""
    items = "".join(f'<a href="#sec-{bid}" class="navlink">{title}</a>' for bid, title in BLOCKS)
    return f"""<div class="board-nav" id="boardNav">
<div class="nav-title">定稿榜导航 · {len(BLOCKS)} 区块 <span style="font-weight:400;font-size:11px;color:#5b6470">（全部为给定画像下服务端渲染的固定快照，可引用、可截图、不随推演器变化）</span></div>
<div class="nav-links">{items}</div>
<div class="nav-split" style="margin-top:12px;padding-top:12px;border-top:1px dashed #e6e3dc;font-size:12.5px;color:#7a5710">
<b>场景推演器（页面中部交互表）</b>：15 项用车条件 + 实时表，用于探索「我的场景会怎样」，只更新推演结果，<b>不改写上面的定稿榜</b>。当前状态：<span id="sim-state">默认画像</span></div>
</div>
<style>
.board-nav{{background:#fff;border:1px solid #e6e3dc;border-radius:12px;padding:14px 18px;margin-bottom:20px;box-shadow:0 1px 3px rgba(0,0,0,.03)}}
.board-nav .nav-title{{font-weight:700;font-size:14px;margin-bottom:10px;color:#15181c}}
.board-nav .nav-links{{display:flex;flex-wrap:wrap;gap:8px}}
.board-nav .navlink{{display:inline-block;background:#eef4f7;color:#1f6f8b;text-decoration:none;border-radius:6px;padding:4px 10px;font-size:12px}}
.board-nav .navlink:hover{{background:#dceaf1}}
.xsec{{background:#fff;border:1px solid #e6e3dc;border-radius:12px;padding:18px;margin-top:20px}}
.xsec h2{{font-size:18px;margin:0 0 12px;border-left:4px solid #c8442e;padding-left:10px}}
.xsec table{{width:100%;border-collapse:collapse;font-size:13px}}
.xsec th,.xsec td{{padding:7px 9px;border-bottom:1px solid #e6e3dc;text-align:left}}
.xsec th{{background:#faf9f6;color:#5b6470;font-size:12px}}
.xsec td.num{{text-align:right;font-variant-numeric:tabular-nums}}
.xsec .note{{background:#eef4f7;border-left:4px solid #1f6f8b;padding:8px 12px;border-radius:6px;color:#274455;font-size:12.5px;margin:8px 0}}
.xsec .warn{{background:#fbe9e7;border-left:4px solid #b42318}}
.xsec .method{{font-size:12px;color:#5b6470;line-height:1.7}}
.xsec .ok{{color:#1a7f37;font-weight:600}} .xsec .mid{{color:#9a6700;font-weight:600}} .xsec .bad{{color:#b42318;font-weight:600}}
</style>"""


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _tier(rank, n):
    if rank <= max(1, n // 4):
        return "T1"
    if rank <= max(2, n * 3 // 4):
        return "T2"
    return "T3"


def _vl(v):
    """判定级别 → HTML 色块文本。"""
    if not v:
        return "—"
    lv = v[0]
    cls = {"适配": "ok", "谨慎": "mid", "不建议": "bad"}.get(lv, "")
    return f'<span class="{cls}">{lv}</span>'


def sections_html(rows, meta=None):
    """生成补充区块 HTML（20 区块）。
    rows: engine.analyze() 输出（已按综合分降序，含 rank/product/adj/hard/verdicts/tco/composite/tier/eff）；
    meta: {dims, trims, rationale, profile, profile_source, pool_powers, n_cars}
    """
    meta = meta or {}
    cond = meta.get("profile") or ENG.DEFAULT_COND
    prof_src = meta.get("profile_source", "人群默认画像")
    out = []

    def sec(bid, body):
        title = dict(BLOCKS)[bid]
        badge = '<span style="font-size:11px;font-weight:400;color:#5b6470">· 定稿快照（给定画像·服务端渲染）</span>'
        out.append(f'<section class="xsec" id="sec-{bid}"><h2>{title} {badge}</h2>{body}</section>')

    n = len(rows)
    top = rows[:10]

    # ① 导语
    sec("lede", '<div class="note">本页为 B 类跨能源正式榜：产品力用统一 8 维框架同台比较'
                '（电车评三电、油车评三大件、混动两者兼顾，动力总成共用 18% 权重）。'
                '<b>定稿榜（本页 20 区块）= 给定用车条件画像下的服务端渲染快照</b>，是唯一可引用、可截图的答案；'
                '<b>场景推演器（页面中部交互表）</b>供你按自己的用车条件探索，改动只影响推演表，不改写定稿榜。'
                '综合分 = 产品力 + 软调节(封顶±10) + 硬条件惩罚(全额·降档·红标)；持有成本 TCO 仅披露不入分。</div>')

    # ② 用车条件画像卡（前置条件）
    def _opt_label(fid, v):
        for o in ENG.FACTORS[fid]["options"]:
            if o["v"] == v:
                return o["label"]
        return str(v)
    prows = "".join(
        f"<tr><td>{_esc(ENG.FACTORS[fid]['label'])}</td><td>{_esc(_opt_label(fid, v))}</td></tr>"
        for fid, v in cond.items() if fid in ENG.FACTORS)
    diag = ENG.check_profile(cond)
    diag_html = ("<p style='margin-top:8px'><b>画像自检：</b></p><ul style='margin:0 0 0 18px;font-size:12px'>"
                 + "".join(f"<li style=\"color:{'#b42318' if w['level']=='warn' else '#5b6470'}\">"
                           f"{'⚠ ' if w['level']=='warn' else ''}{_esc(w['text'])}</li>" for w in diag)
                 + "</ul>") if diag else (
                 "<p style='margin-top:8px;font-size:12px;color:#1a7f37'><b>画像自检：</b>条件自洽，未发现矛盾组合。</p>")
    sec("profile_card",
        '<div class="note"><b>前置条件</b>：跨能源混合比较必须先选定用车条件画像——'
        f'本榜定稿快照基于以下画像（来源：<b>{_esc(prof_src)}</b>，共 {len(cond)} 项）。'
        '同一池车在不同画像下排名可能显著不同（见稳健性区块跨场景名次跨度）；'
        '想探索自己的画像，用页面中部场景推演器，不改写本定稿榜。</div>'
        '<table><thead><tr><th>用车条件</th><th>画像取值</th></tr></thead><tbody>'
        + prows + "</tbody></table>" + diag_html)

    # ②b 能源形式适配结论（v10.53.0：先回答「适合哪种能源形式」，再进逐车榜）
    forms = ENG.recommend_form(rows, cond)
    narr = ENG.form_narrative(forms, cond)
    frows = []
    for f in forms:
        fits = {"最适配": "适配", "可考虑": "可观察", "次选": "次选",
                "不建议": "不建议", "样本不足": "样本不足"}.get(f["label"], f["label"])
        mem = f.get("members") or []
        inner = "、".join(f"{m['cn']} {'%.1f' % m['avg']}（{m['n']} 款）" for m in mem) if len(mem) > 1 else "—"
        frows.append(
            f"<tr><td><b>{ENG.form_name(f['power'])}</b></td>"
            f"<td class='num'>{f['rank'] if f.get('rank') else '—'}</td><td class='num'>{f['n']}</td>"
            f"<td class='num'><b>{f['avg']:.1f}</b></td>"
            f"<td class='num'>{f['avg_adj']:+.2f}</td>"
            f"<td style='font-size:11px'>{inner}</td>"
            f"<td class='num'>{f['hard']}</td><td class='num'>{f['bad']}</td>"
            f"<td>{_esc(f['best'])}</td><td>{fits}</td></tr>")
    if len(forms) == 1:
        # 单形式池（如纯电轿车榜）：不存在跨形式比较，别拿 1 行「最适配」糊弄读者
        only = ENG.form_name(forms[0]["power"])
        sec("form_fit",
            f"<div class='note'><b>单形式池</b>：本榜池内只有{only}一种能源形式（{forms[0]['n']} 款），"
            "不存在跨形式取舍，形式适配结论不适用；逐车比较看下方主榜与场景调节。</div>"
            f"<p>池内均分 <b>{forms[0]['avg']:.1f}</b>，最强一款 <b>{_esc(forms[0]['best'])}</b>；"
            "跨能源形式（燃油含油混/插混·增程/纯电）在同一画像下谁更合适，见"
            "<a href='../energy_compare.html'>跨能源交互比较页</a>。</p>")
    else:
        sec("form_fit",
            "<div class='note'><b>先定形式、再选车</b>：同一价位里燃油（含油混）/插混·增程/纯电混在一起时，"
            "先看「你的前置条件更适配哪种能源形式」，再看该形式内的具体车型。"
            "形式均分 = 池内该能源形式车型综合分均值（产品力 + 场景调节），受池内车源结构影响，只作形式层面参考。"
            "<b>形式按 3 个家族给</b>：油混与纯燃油在牌照/购置税/营运口径上完全同待遇（H5 −8、H6 −10 同罚），"
            "条件层面 11 项里 9 项同分，故合并为「燃油（含油混）」；家族内油混 vs 燃油的均分差单列在「其中」列，"
            "不靠拆成两种能源形式来暗示油混是独立品类。"
            "本表为**全池口径**（不叠加价格/车身/能源筛选）；顶部交互面板随筛选实时重算，两者车数不同属正常。</div>"
            f"<p><b>结论：</b>{_esc(narr)}</p>"
            "<div style='overflow-x:auto'><table><thead><tr><th>能源形式</th><th>名次</th><th>池内车数</th>"
            "<th>形式均分</th><th>场景调节均值</th><th>其中（家族内明细）</th><th>硬条件车数</th><th>不建议项</th>"
            "<th>最强一款</th><th>判定</th>"
            "</tr></thead><tbody>" + "".join(frows) + "</tbody></table></div>"
            "<p class='method'>纯电优先原则已内置：无硬条件冲突的纯电 +0.5；但无固定充电（且单位不可充）、极寒、"
            "高频长途、短持有等条件会触发硬条件或软扣分——「尽量纯电」不等于「无条件纯电」。</p>")

    # ③ 打分主榜（定稿综合榜 top10）
    mrows = []
    for r in top:
        hard = "；".join(f"{h['label']}({h['pt']:+.0f})" for h in r["hard"]) or "—"
        mrows.append(
            f"<tr><td class='num'>{r['rank']}</td><td><b>{_esc(r['model'])}</b></td>"
            f"<td>{POWER_CN.get(r['power'], r['power'])}</td>"
            f"<td class='num'>{r['product']:.1f}</td><td class='num'>{r['adj']:+.1f}</td>"
            f"<td class='num'><b>{r['composite']:.1f}</b></td>"
            f"<td>{r['tier']}</td>"
            f"<td class='num'>{r['eff'] if r['eff'] is not None else '—'}</td>"
            f"<td style='font-size:11px'>{_esc(hard)}</td></tr>")
    sec("main_board",
        "<div class='note'>定稿综合榜（给定画像下服务端计算）：综合分 = 产品力 + 场景调节(软封顶±10 + 硬条件全额)。"
        "硬条件触发车已降档并标红（见逐条件判定区块）。完整交互探索见页面中部推演器。</div>"
        "<table><thead><tr><th>#</th><th>车型</th><th>能源</th><th>产品力</th><th>场景调节</th>"
        "<th>综合分</th><th>档位</th><th>有效续航(km)</th><th>硬条件</th></tr></thead><tbody>"
        + "".join(mrows) + "</tbody></table>")

    # ④ 逐条件适配判定矩阵
    vfid = [f for f in ENG.CORE_FACTORS if f in ENG.FACTORS]
    vhead = "".join(f"<th>{_esc(ENG.FACTORS[f]['label'])}</th>" for f in vfid)
    vrows = []
    for r in top:
        cells = "".join(f"<td>{_vl((r['verdicts'] or {}).get(f))}</td>" for f in vfid)
        vrows.append(f"<tr><td><b>{_esc(r['model'])}</b></td><td>{POWER_CN.get(r['power'], '')}</td>{cells}</tr>")
    # 非适配理由摘要
    reasons = []
    for r in top:
        bad = [(f, v) for f, v in (r["verdicts"] or {}).items() if v and v[0] != "适配"]
        if bad:
            txt = "；".join(f"{ENG.FACTORS[f]['label']}→{v[0]}（{v[1]}）" for f, v in bad[:4])
            reasons.append(f"<tr><td><b>{_esc(r['model'])}</b></td><td style='font-size:11.5px'>{_esc(txt)}</td></tr>")
    sec("condition_verdicts",
        "<div class='note'>逐条件适配判定（适配/谨慎/不建议）与调节分同源（engine.py 规则），"
        "是「根据条件告诉用户适合什么车」的逐条依据。判定只针对该条件单项，综合结论见主榜与推荐结论。</div>"
        "<div style='overflow-x:auto'><table><thead><tr><th>车型</th><th>能源</th>" + vhead + "</tr></thead><tbody>"
        + "".join(vrows) + "</tbody></table></div>"
        + ("<p style='margin-top:10px'><b>非适配项理由摘要（top10）：</b></p>"
           "<table><thead><tr><th>车型</th><th>理由</th></tr></thead><tbody>"
           + ("".join(reasons) if reasons else "<tr><td colspan='2'>全部适配</td></tr>") + "</tbody></table>"))

    # ⑤ 持有成本测算 TCO
    trows = []
    for r in top:
        t = r.get("tco") or {}
        trows.append(
            f"<tr><td><b>{_esc(r['model'])}</b></td><td>{POWER_CN.get(r['power'], '')}</td>"
            f"<td class='num'>{t.get('unit_energy', 0):.3f}</td>"
            f"<td class='num'>{(t.get('energy_8y') or 0)/10000:.2f}</td>"
            f"<td class='num'>{(t.get('ins_8y') or 0)/10000:.2f}</td>"
            f"<td class='num'>{(t.get('maint_8y') or 0)/10000:.2f}</td>"
            f"<td class='num'>{(t.get('purchase_tax') or 0)/10000:.2f}</td>"
            f"<td class='num'>{(t.get('residual_loss') or 0)/10000:.2f}</td>"
            f"<td class='num'>{(t.get('battery_risk_8y') or 0)/10000:.2f}</td>"
            f"<td class='num'><b>{(t.get('tco_8y') or 0)/10000:.2f}</b></td></tr>")
    be = ENG.breakeven_km(meta.get("cars_raw") or [], cond)
    be_txt = (f"家充纯电 vs 池内最优燃油/油混：8 年 TCO 盈亏平衡约 <b>{be} km/年</b>"
              if be is not None else "池内能源形式不足，盈亏平衡不适用")
    sec("tco_block",
        "<div class='note'>8 年持有成本测算（<b>仅披露不入分</b>，事实列不计分纪律）。单位能源成本按画像年里程与充电条件；"
        "逐车电耗/油耗事实优先、缺省用形式中位；残值用 3 年保值率近似；购置税 2026-2027 新能源减半 5%、燃油/油混 10%；"
        "以旧换新补贴因资格各异未计入。实际花费因驾驶习惯/地区电价/保险折扣而异。</div>"
        "<table><thead><tr><th>车型</th><th>能源</th><th>能源元/km</th><th>能源8年(万)</th><th>保费8年(万)</th>"
        "<th>保养8年(万)</th><th>购置税(万)</th><th>残值损失(万)</th><th>电池风险金(万)</th><th>8年合计(万)</th></tr></thead><tbody>"
        + "".join(trows) + "</tbody></table>"
        f"<p style='margin-top:8px'>{be_txt}。</p>")

    # ⑥ 推荐结论
    rec = top[0] if top else None
    warn = [r for r in rows if r["hard"]]
    rec_txt = ""
    if rec:
        v = rec.get("verdicts") or {}
        n_fit = sum(1 for f in v if v[f][0] == "适配")
        rec_txt = (
            f"<p>在<b>{_esc(prof_src)}</b>下，本池综合分居首：<b>{_esc(rec['model'])}</b>"
            f"（{POWER_CN.get(rec['power'], '')}，产品力 {rec['product']:.1f} + 场景调节 {rec['adj']:+.1f} "
            f"= 综合分 <b>{rec['composite']:.1f}</b>，{rec['tier']}）。</p>"
            f"<p>逐条件判定：{n_fit}/{len(v)} 项核心条件适配；"
            + ("无硬条件冲突。" if not rec["hard"] else "硬条件：" + "；".join(h["label"] for h in rec["hard"]) + "。")
            + f"8 年持有成本测算约 {(rec.get('tco') or {}).get('tco_8y', 0)/10000:.1f} 万元。</p>")
    warn_txt = "".join(
        f"<tr><td><b>{_esc(r['model'])}</b></td><td>{POWER_CN.get(r['power'], '')}</td>"
        f"<td class='num'>{r['composite']:.1f}</td>"
        f"<td style='font-size:11.5px'>{_esc('；'.join(h['label'] + '(' + str(h['pt']) + ')' for h in r['hard']))}</td></tr>"
        for r in warn[:6])
    sec("recommend",
        (rec_txt or "<p>池内无车</p>")
        + "<p><b>硬条件不符（不推荐/谨慎）清单：</b>以下车触发硬条件惩罚（全额计入+降档+红标），"
          "在本画像下不建议优先选择：</p>"
        + ("<table><thead><tr><th>车型</th><th>能源</th><th>综合分</th><th>硬条件</th></tr></thead><tbody>"
           + (warn_txt if warn_txt else "<tr><td colspan='4'>无</td></tr>") + "</tbody></table>")
        + "<p class='method'>一句话结论规则：纯电优先（无硬条件冲突的纯电 +0.5），但天气太冷/长途高频/无家充等条件不符时"
          "在相应项扣分（硬条件全额+降档）；最终按综合分排序，推荐结论以主榜+本区块联合解读。</p>")

    # ⑦ 逐配置对比
    trims = meta.get("trims") or {}
    trows2 = []
    for r in top:
        ts = trims.get(r["model"], [])[:3]
        tstr = "；".join(
            f"{t.get('name')} {t.get('price')}万"
            + (f"/纯电{t.get('ev_range_km') or t.get('cltc_km')}km" if (t.get("ev_range_km") or t.get("cltc_km")) else "")
            + (f"/馈电{t.get('fuel_deprived_l100')}L" if t.get("fuel_deprived_l100") else "")
            + (f"/油耗{t.get('fuel_l100')}L" if t.get("fuel_l100") else "")
            for t in ts)
        trows2.append(f"<tr><td>{_esc(r['model'])}</td><td>{_esc(tstr) or '见逐配置库'}</td></tr>")
    sec("trim_compare", "<table><thead><tr><th>车型</th><th>在售配置（最多3款）</th></tr></thead><tbody>"
                        + "".join(trows2) + "</tbody></table>")

    # ⑧ 总分构成：8 维权重表
    dims = meta.get("dims") or []
    drows = "".join(f"<tr><td>{d['label']}</td><td class='num'>{int(d['weight']*100)}%</td>"
                    f"<td>{d.get('by_power',{}).get('BEV','')}</td></tr>" for d in dims)
    sec("score_breakdown", "<table><thead><tr><th>维度</th><th>权重</th><th>电车怎么评</th></tr></thead><tbody>"
                           + drows + "</tbody></table>")

    # ⑨ 三档分级（按综合分）
    trows3 = "".join(
        f"<tr><td class='num'>{r['rank']}</td><td>{_esc(r['model'])}</td><td>{POWER_CN.get(r['power'], '')}</td>"
        f"<td class='num'>{r['product']:.1f}</td><td class='num'>{r['adj']:+.1f}</td>"
        f"<td class='num'><b>{r['composite']:.1f}</b></td><td>{r['tier']}</td></tr>"
        for r in top)
    sec("tiers", "<div class='note'>默认画像下按综合分分档（T1前25%/T2至75%/T3后25%），静态定稿；"
                 "硬条件触发车已降档。用户自定义场景下的名次请到页面中部推演器探索。</div>"
                 "<table><thead><tr><th>#</th><th>车型</th><th>能源</th><th>产品力</th><th>场景调节</th>"
                 "<th>综合分</th><th>档位</th></tr></thead><tbody>" + trows3 + "</tbody></table>")

    # ⑩ 价位段子榜（区间由构建器按本榜口径传入，默认 12-18 万）
    bands = meta.get("bands") or [(12, 14), (14, 16), (16, 18)]
    bparts = []
    for lo, hi in bands:
        sub = [r for r in rows if r.get("price") and lo <= r["price"] < hi]
        sub = sorted(sub, key=lambda x: -x["composite"])[:5]
        bparts.append(f"<p><b>{lo}–{hi}万</b>：" + "、".join(
            f"{r['model']}({r['composite']:.1f})" for r in sub) + "</p>")
    sec("band_subboards", "<div class='note'>默认画像下各价位段内按综合分排名，静态定稿（不随推演器变化）。</div>"
                          + "".join(bparts))

    # ⑪ 纯产品力总榜
    prows = "".join(
        f"<tr><td class='num'>{i}</td><td>{_esc(r['model'])}</td><td>{POWER_CN.get(r['power'], '')}</td>"
        f"<td class='num'>{r['product']:.1f}</td></tr>"
        for i, r in enumerate(sorted(rows, key=lambda x: -x["product"]), 1))
    sec("product_power", "<table><thead><tr><th>#</th><th>车型</th><th>能源</th><th>产品力</th></tr></thead><tbody>"
                         + prows + "</tbody></table>")

    # ⑫ 能源形式对比矩阵
    pool_powers = meta.get("pool_powers") or []
    counts = {}
    for r in rows:
        counts[r["power"]] = counts.get(r["power"], 0) + 1
    mrows = []
    for p, fit, unfit, fact in ENERGY_MATRIX:
        reps = "、".join(r["model"] for r in top if r["power"] == p)[:60] or "本池无"
        mrows.append(
            f"<tr><td><b>{POWER_CN.get(p, p)}</b></td><td class='num'>{counts.get(p, 0)}</td>"
            f"<td>{_esc(fit)}</td><td>{_esc(unfit)}</td><td style='font-size:11.5px'>{_esc(fact)}</td>"
            f"<td style='font-size:11.5px'>{_esc(reps)}</td></tr>")
    sec("energy_matrix",
        "<div class='note'>五种能源形式横向对比（适用/不适用/关键事实），事实结论信源见 references/用车条件因素库.md"
        "（研究代理网搜交叉核实）。本池实际收录形式：" + "、".join(POWER_CN.get(p, p) for p in pool_powers) + "。</div>"
        "<div style='overflow-x:auto'><table><thead><tr><th>形式</th><th>本池车数</th><th>适用场景</th>"
        "<th>不适用场景</th><th>关键事实</th><th>本池代表车</th></tr></thead><tbody>"
        + "".join(mrows) + "</tbody></table></div>")

    # ⑬ 逐车取证依据
    rationale = meta.get("rationale") or {}
    erows = []
    for r in top:
        rat = rationale.get(r["model"], {})
        brief = "；".join(f"{k}:{str(v)[:40]}" for k, v in list(rat.items())[:3]) or "见补采证据JSON（runs/ 下 scores batch）"
        erows.append(f"<tr><td>{_esc(r['model'])}</td><td>{_esc(brief)}</td></tr>")
    sec("evidence_notes", "<table><thead><tr><th>车型</th><th>取证摘要</th></tr></thead><tbody>"
                          + "".join(erows) + "</tbody></table>")

    # ⑭ 生命周期
    sec("lifecycle", '<div class="method">上市状态/换代/清库等生命周期信息见 categories/lifecycle_status.json；'
                     '低销量或新上市车型分数仅作结构性参考，购买前核实保有量与售后。</div>')

    # ⑮ 方法与边界
    sec("method_boundary",
        "<div class='method'>①能源形式由 energy_router 自动判定（禁止问用户）；混合池必须带 usage_profile 前置画像。"
        "②产品力 8 维证据驱动、统一刻度（动力总成18%：电车三电/油车三大件/混动两者），查不到给中性 7.0 不编造。"
        "③场景调节 11 项用车条件：软调节封顶±10；硬条件 7 条（无桩纯电−15/极寒低价纯电−12/短持有纯电−8/"
        "长途续航不足−10/限牌油车−8/营运油车−10/限牌京沪插混增程−8）全额计入+降档+红标；纯电优先原则（无硬条件冲突纯电+0.5）。"
        "条件表两轮精简：v10.55.0 删夏季气温/驾驶风格/空调/停放环境/驻车用电/露营/电价档（单项最大影响≤2 分或完全不改分数）；"
        "v10.56.0 再删地形（路线级平均算不准，改为提示自行上浮电耗 10~25%）与方盒子造型（改由车型自身风阻承担，"
        "并在车身形式筛选里区分轿车/SUV/MPV/越野车/方盒子SUV）；"
        "保留进阶条件为家庭车辆结构（增购第二辆纯电+2.0）与补能耐心；家充 TCO 统一按谷电 0.35 元/度口径；"
        "并新增形式聚合结论与画像自检（矛盾组合显式提示）。"
        "④持有成本 TCO 仅披露不入分。⑤跨能源比较为结构性参考，分差小于算法噪声时看档位而非逐名。"
        "⑥燃油/油混/增程逐配置与 8 维分来自人工补采（附信源）与汽车之家结构化提取，缺项显式标注不估算。</div>")

    # ⑯ 复现说明
    sec("reproduce",
        "<div class='method'>复现：①构建器（build_mixed_sedan.py / build_midsuv_full.py）读候选池+补采分→"
        "engine.analyze 服务端计算→EC.build 注入交互页；②规则单一事实源 modules/envscore/engine.py，"
        "engine.js 为浏览器镜像，env_equivalence_regress.py 双向等价回归守护；③画像归一化校验 engine.normalize_profile；"
        "④回归 modules/regress/ 下 energy_compare_regress / midsuv_full_regress / env_equivalence_regress。</div>")

    # ⑰ 三榜并列（服务端计算）
    def top5(keyfn):
        s = sorted(rows, key=keyfn, reverse=True)[:5]
        return "、".join(f"{i+1}.{r['model']}({keyfn(r):.1f})" for i, r in enumerate(s))
    sec("three_board",
        "<div class='note'>默认画像下的三榜并列（静态定稿，服务端计算）："
        f"<b>①纯产品力榜</b>（基准）：{top5(lambda r: r['product'])}；"
        f"<b>②产品力+软调节榜</b>：{top5(lambda r: r['product'] + r['adj_soft'])}；"
        f"<b>③综合榜</b>（含硬条件）：{top5(lambda r: r['composite'])}。"
        "对比可见默认场景下环境分如何改变名次；其他场景请到推演器探索。</div>")

    # ⑱ 稳健性（服务端计算 8 场景名次跨度）
    cars_raw = meta.get("cars_raw") or []
    scen_maps = []
    for name, patch in ROBUST_SCENARIOS:
        c = {**ENG.DEFAULT_COND, **patch}
        rs = ENG.analyze(cars_raw, c) if cars_raw else []
        scen_maps.append((name, {r["model"]: r["rank"] for r in rs}))
    if cars_raw:
        base_names = [r["model"] for r in rows[:10]]
        rrows = []
        for r in rows[:10]:
            positions = []
            for name, m in scen_maps:
                positions.append(m.get(r["model"]))
            valid = [p for p in positions if p is not None]
            span = (max(valid) - min(valid)) if valid else None
            rrows.append(
                f"<tr><td><b>{_esc(r['model'])}</b></td>"
                + "".join(f"<td class='num'>{p if p is not None else '—'}</td>" for p in positions)
                + f"<td class='num'><b>{span if span is not None else '—'}</b></td></tr>")
        robust_html = ("<div style='overflow-x:auto'><table><thead><tr><th>车型</th>"
                       + "".join(f"<th>{_esc(n)}名次</th>" for n, _ in ROBUST_SCENARIOS)
                       + "<th>跨度</th></tr></thead><tbody>" + "".join(rrows) + "</tbody></table></div>"
                       "<div class='method' style='margin-top:8px'>跨度≤2 位：跨场景稳健；≥3 位：名次对用车场景敏感，"
                       "看档位而非逐名。此表为定稿分析（服务端计算），不随推演器变化。</div>")
    else:
        robust_html = "<div class='method'>无车数据</div>"
    sec("robustness",
        f"<div class='note'>稳健性 = 每款车在 <b>{len(ROBUST_SCENARIOS)} 个代表性场景</b>（"
        + "/".join(n for n, _ in ROBUST_SCENARIOS) + "）下的名次跨度，服务端计算后固定。"
        "跨度≤2 位说明该车在不同用车场景下名次稳定、结论更可信；"
        "跨度大则排名对场景敏感，应看档位而非逐名。这是对定稿榜的稳健性评估，不随推演器的当前选择变化。</div>"
        + robust_html)

    # ⑲ 审查边界
    sec("audit_boundary",
        "<div class='method'>审查边界：①环境分规则与阈值集中在 modules/envscore/engine.py（Python 单一事实源）"
        "与 modules/physics/ev_range_model.py（物理续航），engine.js 为浏览器镜像，由 env_equivalence_regress.py "
        "双向等价守护；②因素库信源（寒冷衰减/盈亏平衡/保值率/购置税/营运渗透率/山区电耗/露天停放/怠速油耗等）"
        "见 references/用车条件因素库.md；"
        "③榜型判定由 energy_router.py 守护；④区块契约齐全由本模块 BLOCKS/REQUIRED 自检；"
        "⑤数据现状（燃油/油混物理参数覆盖不足）已标注，不编造。</div>")

    assert REQUIRED.issubset({b[0] for b in BLOCKS}), "必要区块缺失"
    return nav_html(), "\n".join(out)


def dynamic_script():
    """生成脚本（v10.52.0：定稿全部服务端渲染，JS 只做推演态标识）：
    订阅比较器 onBoardRerender，只更新推演态标识 sim-state（默认画像/自定义N项），不碰定稿区块。
    环境分引擎 adjust()/rangeCalc()/tco() 仍是 engine.js（浏览器镜像），与 engine.py 等价回归守护。
    """
    return r"""
<script>
(function(){
  function defaultCond(){var c={};Object.keys(FACTORS).forEach(function(k){c[k]=FACTORS[k].default;});return c;}
  function onRerender(rows, cond, scale){
    var el=document.getElementById('sim-state'); if(!el) return;
    var base=defaultCond(), changed=0;
    Object.keys(base).forEach(function(k){ if(cond && cond[k]!==base[k]) changed++; });
    if(changed===0 && Math.abs((scale||1)-1)<1e-6){
      el.textContent='默认画像（与定稿榜一致）'; el.style.color='#1a7f37';
    }else{
      el.textContent='自定义 '+changed+' 项 / 力度'+Math.round((scale||1)*100)+'%（推演态·不影响定稿榜）';
      el.style.color='#b07d10';
    }
  }
  window.onBoardRerender=onRerender;  // 比较器 render() 末尾调用，仅更新标识
})();
</script>"""


def selftest():
    rows = ENG.analyze([
        {"model": "测试纯电", "power": "BEV", "price": 14.0, "kwh": 60.0, "cons": 14.0,
         "consSrc": "事实库", "weight": 1800, "cd": 0.24, "area": 2.4, "voltage": 800,
         "swap": False, "cdaPerTon": 0.32, "body": "轿车", "product": 82.0},
        {"model": "测试燃油", "power": "ICE", "price": 13.0, "product": 80.0},
    ], ENG.DEFAULT_COND)
    nav, secs = sections_html(rows, {"dims": [], "profile": ENG.DEFAULT_COND,
                                     "profile_source": "selftest", "pool_powers": ["BEV", "ICE"]})
    assert "boardNav" in nav and f"{len(BLOCKS)}" in nav
    for bid, _ in BLOCKS:
        assert f'id="sec-{bid}"' in secs, f"缺区块 {bid}"
    assert len(DYNAMIC_BLOCKS) == 0, DYNAMIC_BLOCKS
    assert len(ROBUST_SCENARIOS) == 11
    assert "sec-form_fit" in secs and "能源形式适配结论" in secs
    # 形式结论必须真的给出排名与「最适配」判定，不能是空壳区块
    assert "形式均分" in secs and "形式最合适" in secs
    script = dynamic_script()
    assert "sim-state" in script and "onBoardRerender" in script
    # 定稿服务端渲染：区块 HTML 里直接有综合分数字，不靠 JS 首次加载计算
    assert "sec-main_board" in secs and "sec-profile_card" in secs
    assert 'id="fixed-three"' not in secs and 'id="fixed-robust"' not in secs, \
        "旧 JS 首算容器残留，定稿应服务端渲染"
    print(f"[cross_energy_sections selftest] {len(BLOCKS)} 区块齐全，REQUIRED {len(REQUIRED)} 项，"
          f"定稿服务端渲染/推演分离满足")
    return True


if __name__ == "__main__":
    selftest()





