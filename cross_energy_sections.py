# -*- coding: utf-8 -*-
"""
cross_energy_sections.py — 跨能源正式榜的 14 区块导航与补充区块（B类）
对齐 board_view.py::BLOCKS 的 18 项契约与 11 项必要区块，但适配跨能源数据结构。

【设计原则 v9.7.49：定稿榜固定 + 场景推演器分离】
  页面分两层，职责互不污染：
  ① 定稿榜（本模块 14 区块）：全部是「默认画像」下算一次的静态快照——可复现、可截图、可引用。
     稳健性 = 跨 6 个代表性场景（默认/无家充/极寒/长途/营运/限牌）的名次跨度，
     首次加载算一次后固定，不随用户下拉变化——这才配叫"跨场景稳健性"。
  ② 场景推演器（比较器主区的 12 项下拉 + 实时表）：用户探索"我的场景会怎样"，
     只更新自己的推演态标识，绝不改写定稿榜 14 区块。
  两者分离的理由：榜单是"可复现结论"，推演是"个人探索"；同页混时态会破坏可复现性。

环境分引擎仍是 build_energy_compare.py（单一事实源）；本模块只编排展示，不重写引擎。
"""
import json, os

# 18 区块清单（与 board_view.BLOCKS 对齐；跨能源榜用自有内容）
BLOCKS = [
    ("lede",             "导语与规则"),
    ("main_board",       "打分主榜"),
    ("trim_compare",     "逐配置对比"),
    ("score_breakdown",  "总分构成"),
    ("tiers",            "三档分级"),
    ("band_subboards",   "价位段子榜"),
    ("product_power",    "纯产品力总榜"),
    ("evidence_notes",   "逐车取证依据"),
    ("lifecycle",        "生命周期提示"),
    ("method_boundary",  "方法与边界"),
    ("reproduce",        "复现说明"),
    ("three_board",      "三榜并列"),
    ("robustness",       "稳健性"),
    ("audit_boundary",   "审查边界"),
]
REQUIRED = {"main_board","score_breakdown","tiers","product_power","evidence_notes",
            "method_boundary","reproduce","three_board","robustness","audit_boundary"}

# 定稿榜 14 区块全部是默认画像静态快照——不随场景推演器的下拉变化。
# 稳健性虽由 JS 在首次加载时计算（引擎在 JS 侧），但只算一次、不订阅重绘，故仍是定稿快照。
# DYNAMIC_BLOCKS 保留为空集，作为"哪些区块会随下拉变"的显式契约（答案：无）。
DYNAMIC_BLOCKS = set()
FIXED_BLOCKS = set(b[0] for b in BLOCKS)

# 稳健性的 6 个代表性场景（跨这些场景看名次跨度 = 真正的稳健性分析）
ROBUST_SCENARIOS = [
    ("默认画像", {}),
    ("无家充", {"home_charger": "none"}),
    ("北方极寒", {"winter_temp": "underm20"}),
    ("长途高频", {"long_trip_freq": "weekly", "single_trip": "over500"}),
    ("营运", {"operation": "yes"}),
    ("限牌城市", {"plate_limit": "yes"}),
]

# 默认画像（与比较器 FACTORS default 一致）
DEFAULT_COND = {
    "operation":"no","winter_temp":"m10to0","annual_km":"15to25","single_trip":"300to500",
    "long_trip_freq":"few","home_charger":"home","plate_limit":"no","hold_years":"5to8",
    "highway_ratio":"20to40","boxy":"no","drive_style":"normal","ac_usage":"normal",
}


def nav_html():
    """顶部导航卡：指向各区块锚点。"""
    items = "".join(f'<a href="#sec-{bid}" class="navlink">{title}</a>' for bid,title in BLOCKS)
    return f"""<div class="board-nav" id="boardNav">
<div class="nav-title">定稿榜导航 · {len(BLOCKS)} 区块 <span style="font-weight:400;font-size:11px;color:#5b6470">（全部为默认画像下的固定快照，可引用、可截图、不随场景变化）</span></div>
<div class="nav-links">{items}</div>
<div class="nav-split" style="margin-top:12px;padding-top:12px;border-top:1px dashed #e6e3dc;font-size:12.5px;color:#7a5710">
<b>场景推演器（下方交互区）</b>：12 项用车条件 + 实时表，用于探索「我的场景会怎样」，只更新推演结果，<b>不改写上面的定稿榜</b>。当前状态：<span id="sim-state">默认画像</span></div>
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
.xsec .method{{font-size:12px;color:#5b6470;line-height:1.7}}
</style>"""


def _tier(rank, n):
    if rank <= max(1, n//4): return "T1"
    if rank <= max(2, n*3//4): return "T2"
    return "T3"


def sections_html(cars, meta=None):
    """生成补充区块 HTML（18区块中与交互主榜不重复的部分）。
    cars: 比较器格式车列表，需含 product/power/price/cls/model 及评分字段。
    """
    meta = meta or {}
    out = []
    def sec(bid, body):
        title = dict(BLOCKS)[bid]
        badge = '<span style="font-size:11px;font-weight:400;color:#5b6470">· 定稿快照（默认画像）</span>'
        out.append(f'<section class="xsec" id="sec-{bid}"><h2>{title} {badge}</h2>{body}</section>')

    # 导语
    sec("lede", '<div class="note">本页为 B 类跨能源正式榜：产品力用统一 8 维框架同台比较（电车评三电、油车评三大件）。'
                '<b>定稿榜（本页 14 区块）= 默认画像下的固定快照</b>，是唯一可引用、可截图的答案；'
                '<b>场景推演器（页面中部交互表）</b>供你按自己的用车条件探索，改动只影响推演表，不改写定稿榜。'
                '这样既保证榜单可复现，又保留个性化探索能力。</div>')

    # 打分主榜说明（定稿口径；交互表是推演器）
    sec("main_board", '<div class="method">本区块给出口径锚点：<b>定稿前十</b>见下方「纯产品力总榜」与「三榜并列」的默认画像列。'
                      '页面中部「车型综合榜」表是<b>场景推演器</b>：可改条件、点表头排序、点"有效续航/场景调节"展开算式，其结果不回写定稿榜。'
                      '综合分 = 产品力 + 软调节(封顶±10) + 硬条件惩罚(全额·降档)。</div>')

    # 逐配置对比：列出每车在售配置摘要
    trims = meta.get("trims", {})
    rows = []
    for c in sorted(cars, key=lambda x: -x["product"])[:10]:
        ts = trims.get(c["model"], [])[:3]
        tstr = "；".join(f"{t.get('name')} {t.get('price')}万" + (f"/{t.get('cltc_km')}km" if t.get("cltc_km") else "") for t in ts)
        rows.append(f"<tr><td>{c['model']}</td><td>{tstr or '见逐配置库'}</td></tr>")
    sec("trim_compare", "<table><thead><tr><th>车型</th><th>在售配置（最多3款）</th></tr></thead><tbody>"
                        + "".join(rows) + "</tbody></table>")

    # 总分构成：8维权重表（来自比较器 DIMS）
    dims = meta.get("dims", [])
    drows = "".join(f"<tr><td>{d['label']}</td><td class='num'>{int(d['weight']*100)}%</td>"
                    f"<td>{d.get('by_power',{}).get('BEV','')}</td></tr>" for d in dims)
    sec("score_breakdown", "<table><thead><tr><th>维度</th><th>权重</th><th>电动车怎么评</th></tr></thead><tbody>"
                           + drows + "</tbody></table>")

    # 三档分级（默认画像下按产品力分档，静态定稿）
    ranked = sorted(cars, key=lambda x: -x["product"])
    n = len(ranked)
    trows = "".join(f"<tr><td>{i}</td><td>{c['model']}</td><td>{c.get('power','')}</td>"
                    f"<td class='num'>{c['product']:.1f}</td><td>{_tier(i,n)}</td></tr>"
                    for i,c in enumerate(ranked[:15],1))
    sec("tiers", "<div class='note'>默认画像下的产品力分档（T1前25%/T2至75%/T3后25%），静态定稿。"
                 "用户自定义场景下的档位请到页面中部场景推演器查看，两处互不覆盖。</div>"
                 "<table><thead><tr><th>#</th><th>车型</th><th>能源</th><th>产品力</th><th>档位</th></tr></thead><tbody>"
                 + trows + "</tbody></table>")

    # 价位段子榜（默认画像静态，按产品力）
    bands = [(12,14),(14,16),(16,18)]
    bparts = []
    for lo,hi in bands:
        sub = [c for c in cars if c.get("price") and lo<=c["price"]<hi]
        sub = sorted(sub, key=lambda x:-x["product"])[:5]
        bparts.append(f"<p><b>{lo}–{hi}万</b>：" + "、".join(f"{c['model']}({c['product']:.1f})" for c in sub) + "</p>")
    sec("band_subboards", '<div class="note">默认画像下各价位段内按产品力排名，静态定稿（不随场景推演器变化）。</div>'
                          + "".join(bparts))

    # 纯产品力总榜
    prows = "".join(f"<tr><td>{i}</td><td>{c['model']}</td><td>{c.get('power','')}</td><td class='num'>{c['product']:.1f}</td></tr>"
                    for i,c in enumerate(ranked,1))
    sec("product_power", "<table><thead><tr><th>#</th><th>车型</th><th>能源</th><th>产品力</th></tr></thead><tbody>"
                         + prows + "</tbody></table>")

    # 逐车取证依据
    rationale = meta.get("rationale", {})
    erows = []
    for c in cars[:10]:
        rat = rationale.get(c["model"], {})
        brief = "；".join(f"{k}:{str(v)[:40]}" for k,v in list(rat.items())[:3]) or "见补采证据JSON（runs/midsuv_202610_scores_batch*.json）"
        erows.append(f"<tr><td>{c['model']}</td><td>{brief}</td></tr>")
    sec("evidence_notes", "<table><thead><tr><th>车型</th><th>取证摘要</th></tr></thead><tbody>"
                          + "".join(erows) + "</tbody></table>")

    # 生命周期
    sec("lifecycle", '<div class="method">上市状态/换代/清库等生命周期信息见 categories/lifecycle_status.json；'
                     '低销量或新上市车型分数仅作结构性参考，购买前核实保有量与售后。</div>')

    # 方法与边界
    sec("method_boundary", '<div class="method">①级别闸门=官方中型SUV；价位按官方指导价与12–18万有交集（促销/补贴价不计入）。'
                           '②产品力8维证据驱动、查不到给中性7.0不编造。③场景调节按统一画像封顶±10，硬条件全额降档。'
                           '④跨能源比较为结构性参考，分差小于算法噪声时看档位而非逐名。</div>')

    # 复现说明
    sec("reproduce", '<div class="method">复现：① python build_midsuv_full.py 重生成；'
                     '② 数据源 runs/midsuv_202610_candidates.json + scores_batch1/2/3.json + categories/vehicle_physics.json；'
                     '③ 环境分引擎来自 build_energy_compare.py（单一事实源）；④ 回归 python energy_router_regress.py / midsuv_board_regress.py / energy_compare_regress.py。</div>')

    # 三榜并列：默认画像下 产品力/产品力+软调节/综合，首次加载算一次（引擎在JS侧），静态定稿
    sec("three_board", "<div class='note'>默认画像下的三榜并列（静态定稿）：<b>纯产品力榜</b>（基准）｜"
                       "<b>产品力+软调节榜</b>｜<b>综合榜</b>（再叠加硬条件）。"
                       "对比可见默认场景下环境分如何改变名次；其他场景请到推演器探索。</div>"
                       '<div id="fixed-three"><div class="method">计算中…</div></div>')

    # 稳健性：跨6个代表性场景的名次跨度，首次加载算一次、静态定稿（真正的"跨场景稳健性"）
    sec("robustness", "<div class='note'>稳健性 = 每款车在 <b>6 个代表性场景</b>（默认/无家充/北方极寒/长途高频/营运/限牌）下的名次跨度，"
                      "首次加载算一次后固定。跨度≤2 位说明该车在不同用车场景下名次稳定、结论更可信；跨度大则排名对场景敏感，"
                      "应看档位而非逐名。这是<b>对定稿榜的稳健性评估</b>，不随推演器的当前选择变化。</div>"
                      '<div id="fixed-robust"><div class="method">计算中…</div></div>')

    # 审查边界
    sec("audit_boundary", '<div class="method">审查边界：①环境分规则与阈值集中在 build_energy_compare.py + ev_range_model.py，'
                          '由 energy_compare_regress.py（20项）守护；②榜型判定由 energy_router.py 守护（32项）；'
                          '③区块契约齐全由本模块 BLOCKS/REQUIRED 自检；④数据现状（燃油/油混物理参数覆盖不足）已标注，不编造。</div>')

    assert REQUIRED.issubset({b[0] for b in BLOCKS}), "必要区块缺失"
    return nav_html(), "\n".join(out)


def dynamic_script():
    """生成脚本（v9.7.49 定稿/推演分离）：
    ① 首次加载算一次「三榜并列」与「跨6场景稳健性」写入 fixed 容器——静态定稿，不订阅重绘。
    ② 订阅比较器 onBoardRerender，只更新推演态标识 sim-state（默认画像/自定义N项），不碰定稿区块。
    环境分引擎仍是 adjust()/rangeCalc()（比较器单一事实源），这里只编排展示。
    """
    scenarios_js = ",\n".join(
        '{"name":"%s","patch":%s}' % (name, json.dumps(patch, ensure_ascii=False))
        for name, patch in ROBUST_SCENARIOS)
    return r"""
<script>
(function(){
  function esc(s){return String(s==null?'':s);}
  function defaultCond(){var c={};Object.keys(FACTORS).forEach(function(k){c[k]=FACTORS[k].default;});return c;}
  function patchCond(base,patch){var c={};for(var k in base)c[k]=base[k];for(var k in patch)c[k]=patch[k];return c;}
  // 用引擎算某 cond 下所有车的综合分，返回 [{model,product,soft,hard,composite}]
  function calcRows(cond){
    return CARS.map(function(c){
      var a=adjust(c,cond);
      var hard=a.hard.reduce(function(s,h){return s+(h.pt||0);},0);
      var soft=a.total-hard;
      var adj=Math.max(-10,Math.min(10,soft))+hard;   // scale=1 标准
      return {model:c.model,product:c.product||0,soft:soft,hard:hard,composite:c.product+adj};
    });
  }
  function rankMap(rows){
    var sorted=rows.slice().sort(function(a,b){return b.composite-a.composite;});
    var pos={};sorted.forEach(function(r,i){pos[r.model]=i+1;});return pos;
  }
  var ROBUST_SCENARIOS=[__ROBUST_SCENARIOS__];

  // ===== 定稿区（首次加载算一次，静态，不随推演器下拉变化）=====
  function finalizeFixed(){
    var base=defaultCond();
    var rows=calcRows(base);
    // 三榜并列（默认画像）
    function top5(fn){return rows.slice().sort(function(a,b){return fn(b)-fn(a);}).slice(0,5)
      .map(function(r,i){return (i+1)+'.'+esc(r.model)+'('+fn(r).toFixed(1)+')';}).join('；');}
    var th='<p><b>①纯产品力榜</b>（基准）：'+top5(function(r){return r.product;})+'</p>'+
           '<p><b>②产品力+软调节榜</b>：'+top5(function(r){return r.product+Math.max(-10,Math.min(10,r.soft));})+'</p>'+
           '<p><b>③综合榜</b>（含硬条件）：'+top5(function(r){return r.composite;})+'</p>';
    var dt=document.getElementById('fixed-three');if(dt)dt.innerHTML=th;

    // 跨场景稳健性：6场景各算名次，取每车跨度
    var maps=ROBUST_SCENARIOS.map(function(s){return {name:s.name,pos:rankMap(calcRows(patchCond(base,s.patch)))};});
    var byName={};maps.forEach(function(m){byName[m.name]=m.pos;});
    var head='<tr><th>车型</th>'+ROBUST_SCENARIOS.map(function(s){return '<th>'+esc(s.name)+'名次</th>';}).join('')+'<th>跨度</th></tr>';
    var topModels=rows.slice().sort(function(a,b){return b.product-a.product;}).slice(0,10);
    var body=topModels.map(function(r){
      var positions=ROBUST_SCENARIOS.map(function(s){return byName[s.name][r.model];});
      var valid=positions.filter(function(x){return x!=null;});
      var span=valid.length?Math.max.apply(null,valid)-Math.min.apply(null,valid):null;
      return '<tr><td>'+esc(r.model)+'</td>'+positions.map(function(p){return '<td class="num">'+(p==null?'—':p)+'</td>';}).join('')+
             '<td class="num"><b>'+(span==null?'—':span)+'</b></td></tr>';
    }).join('');
    var dr=document.getElementById('fixed-robust');
    if(dr)dr.innerHTML='<table><thead>'+head+'</thead><tbody>'+body+'</tbody></table>'+
      '<div class="method" style="margin-top:8px">跨度≤2 位：跨场景稳健；≥3 位：名次对用车场景敏感，看档位而非逐名。此表为定稿分析，不随推演器变化。</div>';
  }

  // ===== 推演态标识（只更新标识，不改写定稿区块）=====
  function onRerender(rows, cond, scale){
    var el=document.getElementById('sim-state');if(!el)return;
    var base=defaultCond(),changed=0;
    Object.keys(base).forEach(function(k){if(cond&&cond[k]!==base[k])changed++;});
    if(changed===0 && Math.abs((scale||1)-1)<1e-6){el.textContent='默认画像（与定稿榜一致）';el.style.color='#1a7f37';}
    else{el.textContent='自定义 '+changed+' 项 / 力度'+Math.round((scale||1)*100)+'%（推演态·不影响定稿榜）';el.style.color='#b07d10';}
  }
  window.onBoardRerender=onRerender;  // 比较器 render() 末尾调用，仅更新标识
  if(typeof CARS!=='undefined'&&typeof FACTORS!=='undefined'&&typeof adjust==='function') finalizeFixed();
})();
</script>""".replace("__ROBUST_SCENARIOS__", scenarios_js)


def selftest():
    nav, secs = sections_html([{"model":"测试车","product":80.0,"power":"BEV","price":15.0,"cls":"中型SUV"}], {"dims":[]})
    assert "boardNav" in nav and f"{len(BLOCKS)}" in nav
    for bid,_ in BLOCKS:
        assert f'id="sec-{bid}"' in secs, f"缺区块 {bid}"
    assert len(DYNAMIC_BLOCKS) == 0, DYNAMIC_BLOCKS   # 定稿榜不随推演器变化
    assert len(ROBUST_SCENARIOS) == 6
    script = dynamic_script()
    # 定稿区容器由 JS 首次算一次写入（fixed-three/fixed-robust），不订阅重绘
    for cid in ("fixed-three", "fixed-robust"):
        assert f'getElementById(\'{cid}\')' in script, f"JS未填充定稿容器 {cid}"
    assert "finalizeFixed" in script and "ROBUST_SCENARIOS" in script
    # 推演器只更新 sim-state 标识
    assert "sim-state" in script and "onBoardRerender" in script
    # 关键：定稿区块 DOM 里不应再有旧的 dyn-* 容器
    assert 'id="dyn-' not in secs, "仍有随推演器重绘的动态容器"
    print(f"[cross_energy_sections selftest] {len(BLOCKS)} 区块齐全，REQUIRED {len(REQUIRED)} 项，定稿固定/推演分离满足")
    return True

if __name__ == "__main__":
    selftest()
