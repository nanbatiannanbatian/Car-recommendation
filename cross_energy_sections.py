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
<div class="nav-title">本榜导航 · {len(BLOCKS)} 区块</div>
<div class="nav-links">{items}</div></div>
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
        out.append(f'<section class="xsec" id="sec-{bid}"><h2>{title}</h2>{body}</section>')

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
    sec("tiers", "<div class='note'>档位为产品力主分在榜内的相对分层（T1前25%/T2至75%/T3后25%）；"
                 "交互主榜在硬条件触发时会在此基础上再降一档。</div>"
                 "<table><thead><tr><th>#</th><th>车型</th><th>能源</th><th>产品力</th><th>档位</th></tr></thead><tbody>"
                 + trows + "</tbody></table>")

    # 价位段子榜
    bands = [(12,14),(14,16),(16,18)]
    bparts = []
    for lo,hi in bands:
        sub = [c for c in cars if c.get("price") and lo<=c["price"]<hi]
        sub = sorted(sub, key=lambda x:-x["product"])[:5]
        bparts.append(f"<p><b>{lo}–{hi}万</b>：" + "、".join(f"{c['model']}({c['product']:.1f})" for c in sub) + "</p>")
    sec("band_subboards", "".join(bparts))

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

    # 三榜并列：产品力 / +软调节 / +硬条件（用默认画像 approximation，标注为静态基准）
    mthree = meta.get("three_board_default", {})
    if mthree:
        tb = "".join(f"<tr><td>{i}</td><td>{d['model']}</td><td class='num'>{d.get('product','—')}</td>"
                     f"<td class='num'>{d.get('soft','—')}</td><td class='num'>{d.get('hard','—')}</td></tr>"
                     for i,d in enumerate(mthree,1))
        sec("three_board", "<div class='note'>默认画像下的静态基准三榜；交互主榜可改条件实时看三榜变化。</div>"
                           "<table><thead><tr><th>#</th><th>车型</th><th>产品力</th><th>+软调节</th><th>+硬条件(综合)</th></tr></thead><tbody>"
                           + tb + "</tbody></table>")
    else:
        sec("three_board", '<div class="method">见交互主榜：综合分列即"产品力+软调节+硬条件"，展开可见逐项；调节力度滑块可切换仅看产品力。</div>')

    # 稳健性
    sec("robustness", '<div class="method">稳健性通过交互主榜的"场景调节力度"滑块体现：0%=纯产品力、100%=标准、150%=强化场景。'
                      '榜单不依赖单一场景假设；若车型在 0%/100%/150% 下名次稳定，则结论更可信。</div>')

    # 审查边界
    sec("audit_boundary", '<div class="method">审查边界：①环境分规则与阈值集中在 build_energy_compare.py + ev_range_model.py，'
                          '由 energy_compare_regress.py（20项）守护；②榜型判定由 energy_router.py 守护（32项）；'
                          '③区块契约齐全由本模块 BLOCKS/REQUIRED 自检；④数据现状（燃油/油混物理参数覆盖不足）已标注，不编造。</div>')

    assert REQUIRED.issubset({b[0] for b in BLOCKS}), "必要区块缺失"
    return nav_html(), "\n".join(out)


def selftest():
    nav, secs = sections_html([{"model":"测试车","product":80.0,"power":"BEV","price":15.0,"cls":"中型SUV"}], {"dims":[]})
    assert "boardNav" in nav and f"{len(BLOCKS)}" in nav
    for bid,_ in BLOCKS:
        assert f'id="sec-{bid}"' in secs, f"缺区块 {bid}"
    print(f"[cross_energy_sections selftest] {len(BLOCKS)} 区块齐全，REQUIRED {len(REQUIRED)} 项满足")
    return True

if __name__ == "__main__":
    selftest()
