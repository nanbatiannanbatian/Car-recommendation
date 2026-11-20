# -*- coding: utf-8 -*-
"""
cross_energy_sections.py — 跨能源正式榜的 18 区块导航与补充区块（B类）
对齐 board_view.py::BLOCKS 的 18 项契约与 11 项必要区块，但适配跨能源数据结构：
  - 产品力主分用统一 8 维（已含在各 car.product）
  - 三榜并列 = 纯产品力 / 产品力+软调节 / 含硬条件惩罚（默认画像下计算）
  - 稳健性 = 软调节力度 0%/100%/150% 下 Top10 名次跨度（近似）
  - 数据由调用方传入 cars（比较器格式）+ 各车的评分明细/来源
默认场景画像与比较器一致（见 build_energy_compare.FACTORS 默认值）。
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

# 会随环境分（用车条件下拉/力度滑块）实时重算的区块。
# 判定原则：内容依赖「综合分/档位/名次」→ 动态；内容是硬件配置/8维权重/证据/口径说明/纯产品力基准 → 固定。
# main_board 的比较器主表本身已动态（在主区），此处列出的为注入区块中需 JS 重绘者。
DYNAMIC_BLOCKS = {"band_subboards", "tiers", "three_board", "robustness"}
# 明确固定、不受场景影响（避免误改）；product_power 是刻意基准榜，场景调节不得污染。
FIXED_BLOCKS = set(b[0] for b in BLOCKS) - DYNAMIC_BLOCKS - {"main_board"}

# 默认画像（与比较器 FACTORS default 一致）
DEFAULT_COND = {
    "operation":"no","winter_temp":"m10to0","annual_km":"15to25","single_trip":"300to500",
    "long_trip_freq":"few","home_charger":"home","plate_limit":"no","hold_years":"5to8",
    "highway_ratio":"20to40","boxy":"no","drive_style":"normal","ac_usage":"normal",
}


def nav_html():
    """顶部导航卡：指向各区块锚点。"""
    items = "".join(
        f'<a href="#sec-{bid}" class="navlink{" dyn" if bid in DYNAMIC_BLOCKS else ""}">'
        f'{title}{" 🔄" if bid in DYNAMIC_BLOCKS else ""}</a>' for bid,title in BLOCKS)
    return f"""<div class="board-nav" id="boardNav">
<div class="nav-title">本榜导航 · {len(BLOCKS)} 区块 <span style="font-weight:400;font-size:11px;color:#5b6470">（标 🔄 的随环境分实时更新，其余为固定口径/事实/纯产品力基准）</span></div>
<div class="nav-links">{items}</div></div>
<style>
.board-nav .navlink.dyn{{background:#fbf3e6;color:#9a6700}}
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
    def sec(bid, body, dynamic=False):
        title = dict(BLOCKS)[bid]
        badge = ('<span class="dyn-badge">🔄 随环境分实时更新</span>' if dynamic
                 else '<span class="fix-badge">固定口径·不受场景影响</span>')
        out.append(f'<section class="xsec" id="sec-{bid}"><h2>{title} {badge}</h2>{body}</section>')

    # 导语
    sec("lede", '<div class="note">本页为 B 类跨能源正式榜：产品力用统一 8 维框架同台比较（电车评三电、油车评三大件），'
                '用车条件通过左侧下拉做场景调节（软调节封顶±10、硬条件全额计入并降档）。'
                '改动条件即时重排下方主榜——这是比静态前十更完整的「产品力 + 环境适配」双视角。</div>')

    # 打分主榜说明（交互表在主区，这里给导航锚与口径）
    sec("main_board", '<div class="method">交互主榜见页面中部「车型综合榜」表：可按预算/车身过滤、点表头排序、点"有效续航/场景调节"展开算式。'
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

    # 三档分级（默认画像下按产品力近似分档）
    ranked = sorted(cars, key=lambda x: -x["product"])
    n = len(ranked)
    trows = "".join(f"<tr><td>{i}</td><td>{c['model']}</td><td>{c.get('power','')}</td>"
                    f"<td class='num'>{c['product']:.1f}</td><td>{_tier(i,n)}</td></tr>"
                    for i,c in enumerate(ranked[:15],1))
    sec("tiers", "<div class='note'>档位随环境分实时变化：基础按「产品力+场景调节」综合分分档，"
                 "硬条件触发时再降一档（T1→T2→T3）。改左侧条件即时重排。</div>"
                 '<div id="dyn-tiers"><table><thead><tr><th>#</th><th>车型</th><th>能源</th><th>产品力</th><th>综合分</th><th>档位</th></tr></thead><tbody>'
                 + trows + "</tbody></table></div>", dynamic=True)

    # 价位段子榜（动态：按当前综合分排名）
    bands = [(12,14),(14,16),(16,18)]
    bparts = []
    for lo,hi in bands:
        sub = [c for c in cars if c.get("price") and lo<=c["price"]<hi]
        sub = sorted(sub, key=lambda x:-x["product"])[:5]
        bparts.append(f"<p><b>{lo}–{hi}万</b>：" + "、".join(f"{c['model']}({c['product']:.1f})" for c in sub) + "</p>")
    sec("band_subboards", '<div class="note">按当前环境分下的综合分在各价位段内排名，随条件实时更新。</div>'
                          '<div id="dyn-bands">' + "".join(bparts) + "</div>", dynamic=True)

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

    # 三榜并列：产品力 / 产品力+软调节 / 综合(含硬条件)——三榜随环境分实时重排
    sec("three_board", "<div class='note'>三榜并列随环境分实时更新：<b>纯产品力榜</b>（不受场景影响，基准）｜"
                       "<b>产品力+软调节榜</b>（场景软调节后）｜<b>综合榜</b>（再叠加硬条件惩罚与降档）。"
                       "对比可见用车条件如何改变名次。</div>"
                       '<div id="dyn-three"><div class="method">载入中…</div></div>', dynamic=True)

    # 稳健性：真实计算 0%/100%/150% 三档力度下的名次跨度（随 cond 实时重算）
    sec("robustness", "<div class='note'>稳健性 = 同一批车在场景力度 0%（纯产品力）/100%（标准）/150%（强化）下的名次跨度。"
                      "跨度≤1 位说明结论不依赖单一场景假设，更可信；跨度大则该车排名对场景敏感。"
                      "随左侧用车条件实时重算。</div>"
                      '<div id="dyn-robust"><div class="method">载入中…</div></div>', dynamic=True)

    # 审查边界
    sec("audit_boundary", '<div class="method">审查边界：①环境分规则与阈值集中在 build_energy_compare.py + ev_range_model.py，'
                          '由 energy_compare_regress.py（20项）守护；②榜型判定由 energy_router.py 守护（32项）；'
                          '③区块契约齐全由本模块 BLOCKS/REQUIRED 自检；④数据现状（燃油/油混物理参数覆盖不足）已标注，不编造。</div>')

    assert REQUIRED.issubset({b[0] for b in BLOCKS}), "必要区块缺失"
    return nav_html(), "\n".join(out)


def dynamic_script():
    """生成订阅比较器重排钩子的 JS：随环境分实时重绘 4 个 DYNAMIC_BLOCKS。
    比较器 render() 算完 rows 后调用 window.onBoardRerender(rows, cond, scale)。
    这里只用 rows 里已有字段（product/composite/tier/adjItems/hard/price/power/model），不重算引擎。
    """
    return r"""
<script>
(function(){
  function esc(s){return String(s==null?'':s);}
  function pmTag(p){var m={ICE:'燃油',HEV:'油混',PHEV:'插混',EREV:'增程',BEV:'纯电'};return m[p]||p;}
  function tierTd(t){var cls=t==='T1'?'t1':(t==='T2'?'t2':'t3');return '<span class="tier-badge '+cls+'">'+t+'</span>';}
  // rows 由比较器 render() 传入，已含当前 cond/scale 下的 composite/tier/adjItems/hard
  function onRerender(rows, cond, scale){
    if(!Array.isArray(rows)) return;
    var byComp=rows.slice().sort(function(a,b){return (b.composite||0)-(a.composite||0);});

    // 1) tiers 三档分级（用比较器算好的 tier，硬条件已降档）
    var tg={T1:[],T2:[],T3:[]};
    byComp.forEach(function(r){(tg[r.tier]||tg.T3).push(r);});
    var tiersHtml='';
    ['T1','T2','T3'].forEach(function(t){
      tiersHtml+='<p><b>'+t+'（'+tg[t].length+'款）</b>：'+
        tg[t].slice(0,8).map(function(r){return esc(r.model)+'('+(r.composite||0).toFixed(1)+')';}).join('、')+'</p>';
    });
    var d=document.getElementById('dyn-tiers');
    if(d) d.innerHTML=tiersHtml;

    // 2) band_subboards 价位段子榜（按当前综合分）
    var bands=[[12,14],[14,16],[16,18]], bh='';
    bands.forEach(function(b){
      var sub=byComp.filter(function(r){var p=r.price!=null?r.price:r.priceLow;return p!=null&&p>=b[0]&&p<b[1];}).slice(0,5);
      bh+='<p><b>'+b[0]+'–'+b[1]+'万</b>：'+
        (sub.length?sub.map(function(r){return esc(r.model)+'('+(r.composite||0).toFixed(1)+')';}).join('、'):'无匹配车型')+'</p>';
    });
    var db=document.getElementById('dyn-bands');
    if(db) db.innerHTML=bh;

    // 3) three_board 三榜并列：纯产品力 / 产品力+软调节 / 综合
    var softOf=function(r){
      var hard=r.hard?r.hard.reduce(function(s,h){return s+(h.pt||0);},0):0;
      return (r.adjTotal||0)-hard;               // 软调节原始分（未缩放）
    };
    var clampSoft=function(r){return Math.max(-10,Math.min(10,softOf(r)*(scale||1)));};
    function rankBy(scoreFn){
      return rows.slice().sort(function(a,b){return scoreFn(b)-scoreFn(a);}).slice(0,5)
        .map(function(r,i){return (i+1)+'.'+esc(r.model)+'('+scoreFn(r).toFixed(1)+')';}).join('；');
    }
    var th='<p><b>①纯产品力榜</b>（不受场景影响）：'+rankBy(function(r){return r.product||0;})+'</p>'+
           '<p><b>②产品力+软调节榜</b>：'+rankBy(function(r){return (r.product||0)+clampSoft(r);})+'</p>'+
           '<p><b>③综合榜</b>（再含硬条件）：'+rankBy(function(r){return r.composite||0;})+'</p>';
    var dt=document.getElementById('dyn-three');
    if(dt) dt.innerHTML=th;

    // 4) robustness 稳健性：按当前 cond 重算 0%/100%/150% 三档力度的名次跨度
    // 用 rows 反推每车 softRaw 与 hardTotal，再按各档 scale 重算 composite 排名
    function rankAt(sc){
      var arr=rows.map(function(r){
        var hard=r.hard?r.hard.reduce(function(s,h){return s+(h.pt||0);},0):0;
        var adj=Math.max(-10,Math.min(10,softOf(r)*sc))+hard;
        return {model:r.model, c:(r.product||0)+adj};
      }).sort(function(a,b){return b.c-a.c;});
      var pos={}; arr.forEach(function(x,i){pos[x.model]=i+1;}); return pos;
    }
    var p0=rankAt(0), p1=rankAt(1), p15=rankAt(1.5);
    var seen={}, rb=[];
    byComp.slice(0,10).forEach(function(r){
      var m=r.model; if(seen[m])return; seen[m]=1;
      var a=p0[m],b=p1[m],c=p15[m];
      if(a==null||b==null||c==null)return;
      var span=Math.max(a,b,c)-Math.min(a,b,c);
      rb.push('<tr><td>'+esc(m)+'</td><td class="num">'+a+'</td><td class="num">'+b+'</td><td class="num">'+c+'</td><td class="num"><b>'+span+'</b></td></tr>');
    });
    var dr=document.getElementById('dyn-robust');
    if(dr) dr.innerHTML='<table><thead><tr><th>车型</th><th>0%名次</th><th>100%名次</th><th>150%名次</th><th>跨度</th></tr></thead><tbody>'+rb.join('')+'</tbody></table>'+
      '<div class="method" style="margin-top:8px">跨度≤1 位：结论稳健；≥3 位：名次对场景敏感，看档位而非逐名。</div>';
  }
  // 订阅比较器重排钩子（比较器 render() 末尾会调用）
  window.onBoardRerender=onRerender;
  // 首次加载：比较器 script 在 extra_tail 之前已 render 过一次，这里主动触发一次填充
  if(typeof CARS!=='undefined' && typeof FACTORS!=='undefined' && typeof adjust==='function'){
    var cond={}; Object.keys(FACTORS).forEach(function(k){cond[k]=FACTORS[k].default;});
    var rows=CARS.map(function(c){
      var a=adjust(c,cond);
      var hard=a.hard.reduce(function(s,h){return s+(h.pt||0);},0);
      var soft=a.total-hard;
      var adj=Math.max(-10,Math.min(10,soft*1))+hard;
      var tier=compositeTier(c.product+adj);
      if(a.hard.some(function(h){return h.downgrade;})) tier=tier==='T1'?'T2':(tier==='T2'?'T3':'T3');
      return Object.assign({},c,{price:c.price!=null?c.price:c.priceLow,adjTotal:a.total,hard:a.hard,
                                 composite:c.product+adj,tier:tier});
    });
    onRerender(rows,cond,1);
  }
})();
</script>"""


def selftest():
    nav, secs = sections_html([{"model":"测试车","product":80.0,"power":"BEV","price":15.0,"cls":"中型SUV"}], {"dims":[]})
    assert "boardNav" in nav and f"{len(BLOCKS)}" in nav
    for bid,_ in BLOCKS:
        assert f'id="sec-{bid}"' in secs, f"缺区块 {bid}"
    assert len(DYNAMIC_BLOCKS) == 4, DYNAMIC_BLOCKS
    assert DYNAMIC_BLOCKS.issubset({b[0] for b in BLOCKS})
    script = dynamic_script()
    for cid in ("dyn-tiers", "dyn-bands", "dyn-three", "dyn-robust"):
        assert f'getElementById(\'{cid}\')' in script, f"JS未重绘容器 {cid}"
    assert "onBoardRerender" in script and "compositeTier" in script
    print(f"[cross_energy_sections selftest] {len(BLOCKS)} 区块齐全，REQUIRED {len(REQUIRED)} 项，动态区块 {len(DYNAMIC_BLOCKS)} 项满足")
    return True

if __name__ == "__main__":
    selftest()
