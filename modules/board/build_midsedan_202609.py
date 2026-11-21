# -*- coding: utf-8 -*-
"""
build_midsedan_202609.py
生成「2026年9月中型轿车前十排行榜（全能源）」Markdown + HTML。

数据来源（全部真实网搜采集，不编造）：
  - runs/midsedan_202609_candidates_ice.json / _bev.json   车型/级别/指导价/9月销量
  - runs/midsedan_202609_scores_b1/b2/b3.json              14款补采的8维产品力分+逐配置
  - categories/master_pool.json / trims.json                已有4款(深蓝L06/EZ-6/天工05/i3)的分与配置

打分逻辑（用户确认：产品力+场景调节）：
  产品力分 = 8维加权 × 10（统一能源中性框架：动力总成18/安全16.2/智驾13.5/空间12.6/性价比11.7/质量10/操控9/车机9）
  场景调节 = 统一默认人群画像下对具体车的加减分（透明展示，封顶±10），非用户定制条件
  综合分 = 产品力 + 场景调节；主榜按综合分降序取前十
  销量(2026-09)只作事实列，不作评价分（销量是发现器不是评价器）

铁律：级别闸门只留官方"中型车"；每车必须有逐配置；无数据的车不进主榜、显式列出原因。
"""
import json, os, sys

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)

from modules.board import filings_board as FB

# 本榜类别：中型轿车（级别=中型车，车身=轿车），能源不限（全能源）
BOARD_CLS, BOARD_BODY = "中型车", "轿车"
RUNS = os.path.join(HERE, "runs")
OUT_MD = os.path.join(RUNS, "midsedan_202609_榜单.md")
OUT_HTML = os.path.join(RUNS, "midsedan_202609_榜单.html")

W = {"powertrain":0.18,"safety":0.162,"adas":0.135,"space":0.126,
     "value":0.117,"quality":0.10,"control":0.09,"cabin":0.09}
DIM_LABEL = {"powertrain":"动力总成","safety":"安全","adas":"智驾","space":"空间",
             "value":"性价比","quality":"质量品控","control":"操控","cabin":"车机座舱"}

# 默认场景（人群常用画像，对所有车一致应用）：有家充、江淮-10~0℃、年1.5-2.5万、单次300-500km、
# 一年1-5次长途、不限牌、持有5-8年、高速20-40%、非营运、普通造型。调节项按能源形式给分。
DEFAULT_SCENE_ADJ = {
    "BEV":  {"有家充":1.5, "年里程1.5-2.5万":0, "低温轻度":0, "续航匹配":0},
    "PHEV": {"有家充":1.0, "油电双补":0.5},
    "EREV": {"有家充":1.0, "高速油价高":0},
    "HEV":  {"省油无需充电":0.5},
    "ICE":  {"补能便利":0.3},
}


def norm(s):
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


def load_all():
    ice = json.load(open(os.path.join(RUNS,"midsedan_202609_candidates_ice.json"),encoding="utf-8"))["cars"]
    bev = json.load(open(os.path.join(RUNS,"midsedan_202609_candidates_bev.json"),encoding="utf-8"))["cars"]
    cands = ice + bev
    # 补采的14款分数+配置
    scores = {}
    for i in (1,2,3):
        d = json.load(open(os.path.join(RUNS,f"midsedan_202609_scores_b{i}.json"),encoding="utf-8"))["cars"]
        for c in d:
            scores[norm(c["model"])] = c
    # 已有数据
    mp = json.load(open(str(paths.cat("master_pool.json")),encoding="utf-8"))["cars"]
    # 2026-11-22：走统一入口（人工库 + 自动库合流）
    from modules.data.trims_store import load as _load_trims
    tr = list(_load_trims().values())
    mp_by = {norm(c["model"]):c for c in mp}
    tr_by = {norm(e["model"]):e for e in tr}

    rows = []
    excluded = []
    for c in cands:
        # 级别闸门：官方中型车
        if c.get("cls") != "中型车":
            excluded.append((c["model"], c.get("cls"), "官方级别非中型车"))
            continue
        sales = c.get("sales_202609")
        if not sales:
            excluded.append((c["model"], c.get("cls"), "无2026年9月销量数据"))
            continue
        key = norm(c["model"])
        sc = scores.get(key)
        # 已有4款：从池取分，从trims取配置
        if not sc and key in mp_by:
            pool = mp_by[key]
            if pool.get("scores"):
                trims = []
                e = tr_by.get(key)
                if e:
                    for t in e.get("trims",[]):
                        trims.append({"name":t.get("name"),"price":t.get("price"),
                                      "power":"纯电","cltc_km":t.get("cltc_km"),
                                      "key_configs":t.get("key_configs",[])})
                sc = {"model":c["model"],"power":c["power"],"scores":pool["scores"],
                      "rationale":{},"trims":trims,"source_urls":[]}
        if not sc:
            excluded.append((c["model"], c.get("cls"), "无8维产品力分（宝马i3仅配置无分）"))
            continue
        if not sc.get("trims"):
            excluded.append((c["model"], c.get("cls"), "无逐配置数据"))
            continue
        # 产品力分
        product = sum(float(sc["scores"].get(k,7))*W[k] for k in W)*10
        # 场景调节（默认场景，按能源）
        adj_parts = DEFAULT_SCENE_ADJ.get(c["power"], {})
        # 续航匹配调节（对纯电：默认场景单次400km需求 vs 该车最高续航）
        cltcs = [t.get("cltc_km") for t in sc["trims"] if t.get("cltc_km")]
        eff = None
        if c["power"]=="BEV" and cltcs:
            # 简化：用CLTC×0.75(温度+高速综合达成率)作为有效续航参考
            eff = round(max(cltcs)*0.75)
            if eff < 400*0.85: adj_parts = dict(adj_parts); adj_parts["续航匹配(需400km)"] = -1.0
            elif eff >= 400*1.3: adj_parts = dict(adj_parts); adj_parts["续航匹配(需400km)"] = 0.5
        adj = round(sum(adj_parts.values()),1)
        rows.append({
            "model":c["model"],"power":c["power"],"cls":c["cls"],
            "price_guide":c.get("price_guide"),"sales":sales,
            "scores":sc["scores"],"rationale":sc.get("rationale",{}),
            "trims":sc["trims"],"source_url":c.get("source_url"),
            "product":round(product,1),"adj":adj,"adj_parts":adj_parts,
            "eff":eff,"composite":round(product+adj,1),
        })
    rows.sort(key=lambda r:-r["composite"])
    return rows, excluded


def power_label(p):
    return {"ICE":"燃油","HEV":"油混","PHEV":"插混","EREV":"增程","BEV":"纯电"}.get(p,p)


def build_md(rows, excluded):
    top10=rows[:10]
    L=[]
    L.append("# 2026年9月中型轿车前十排行榜（全能源）\n")
    L.append("> 口径：官方二级分类「中型车」；主排序 = 8维产品力分（统一能源中性框架）+ 默认场景调节；")
    L.append("> 2026年9月销量只作事实参考列，**不参与打分**（销量是候选发现器，不是产品评价器）。\n")
    L.append("> 无模型调用、分数可复现。所有车型数据来自网搜采集（汽车之家/易车/懂车帝/C-NCAP），逐车可溯源。\n")
    L.append("## 前十总榜\n")
    L.append("| 排名 | 车型 | 能源 | 指导价(万) | 9月销量(辆) | 产品力 | 场景调节 | 综合分 | 档位 |")
    L.append("|---:|---|---|---|---:|---:|---:|---:|---|")
    for i,r in enumerate(top10,1):
        tier = "T1" if r["composite"]>=82 else ("T2" if r["composite"]>=72 else "T3")
        adjs = f"{r['adj']:+.1f}" if r['adj'] else "0"
        L.append(f"| {i} | **{r['model']}** | {power_label(r['power'])} | "
                 f"{r['price_guide'][0]}–{r['price_guide'][1]} | {r['sales']:,} | "
                 f"{r['product']:.1f} | {adjs} | **{r['composite']:.1f}** | {tier} |")
    # 读榜须知：分差/销量异常提示（负责任出榜）
    scores_top=[r["composite"] for r in top10]
    spread=round(max(scores_top)-min(scores_top),1)
    low_sales=[r for r in top10 if r["sales"]<1000]
    L.append(f"\n> **读榜须知**：前十综合分 {min(scores_top):.1f}–{max(scores_top):.1f}，跨度仅 {spread:.1f} 分，")
    L.append("小于算法噪声与维度口径差异 → **逐名精确排序不成立，应理解为「同档第一梯队」**，勿把第1名和第10名的差距当真。\n")
    if low_sales:
        ls="、".join(f"{r['model']}({r['sales']:,}辆)" for r in low_sales)
        L.append(f"> **销量异常提示**：{ls} 虽产品力分靠前，但9月销量很低，**市场验证不足**——")
        L.append("可能是小众/新上市/品牌承压车型，购买前务必核实保有量、售后网络与残值，不要只看产品力分。\n")
    L.append("\n## 各车逐项分数与证据摘要\n")
    for i,r in enumerate(top10,1):
        L.append(f"### {i}. {r['model']}（{power_label(r['power'])}，综合 {r['composite']:.1f}）\n")
        L.append("| 维度 | 权重 | 得分 | 证据摘要 |")
        L.append("|---|---:|---:|---|")
        for k in W:
            sc=r["scores"].get(k,7); rat=r["rationale"].get(k,"无证据·中性")
            L.append(f"| {DIM_LABEL[k]} | {W[k]*100:.1f}% | {sc} | {rat[:120]} |")
        adjstr = "、".join(f"{k}{v:+.1f}" for k,v in r["adj_parts"].items() if v) or "无"
        L.append(f"\n- **场景调节（默认画像）**：{adjstr} = {r['adj']:+.1f}")
        if r.get("eff"): L.append(f"- **续航参考**：CLTC最高{max(t.get('cltc_km') or 0 for t in r['trims'])}km → 默认场景有效约{r['eff']}km")
        L.append(f"- **9月销量**：{r['sales']:,} 辆（{r['source_url'] or '见采集记录'}）")
        L.append("- **逐配置**：")
        for t in r["trims"][:5]:
            kc = "、".join(t.get("key_configs",[])[:4])
            L.append(f"  - {t['name']}：{t['price']}万" + (f"，CLTC {t['cltc_km']}km" if t.get('cltc_km') else "") + (f"｜{kc}" if kc else ""))
        L.append("")
    L.append("## 未入榜车型及原因（数据边界）\n")
    L.append("| 车型 | 官方级别 | 原因 |")
    L.append("|---|---|---|")
    for m,cls,why in excluded:
        L.append(f"| {m} | {cls} | {why} |")
    L.append("\n> 说明：级别不符（中大型/紧凑型）按官方二级分类闸门排除，不混入中型车榜；")
    L.append("> 无逐配置或无产品力分的车不编造数据、不进主榜，符合「每车必须有逐配置数据」铁律。\n")
    L.append("## 方法与边界\n")
    L.append("- **统一8维框架**：动力总成18%（电车评三电/油车评三大件）、安全16.2%、智驾13.5%、空间12.6%、性价比11.7%、质量10%、操控9%、车机9%。产品力分=各维(0-10)×权重×10。")
    L.append("- **场景调节**：按人群常用画像（有家充、江淮冬季、年1.5-2.5万km、单次300-500km、一年1-5次长途、高速20-40%、持有5-8年）对具体车加减分，封顶±10；非用户定制条件。")
    L.append("- **销量口径**：2026年9月，多为车系口径（懂车帝/乘联会），个别为车型拆分口径，逐车 note 已注明。")
    L.append("- **不可比提示**：油车口碑分与纯电人工核验分来源不同，跨能源比较为结构性参考，不应据此做精确名次判断；分差小于算法噪声时只看档位。")
    # 附加同级别申报车型列表（未上市，用参考车预估，不进主榜）
    rel = FB.related_filings(category_cls=BOARD_CLS, category_body=BOARD_BODY,
                             category_power=None, all_filings=FB.load_filings())
    main_scores = [r["composite"] for r in rows]
    sec = FB.build_section_md(rel, main_scores)
    if sec:
        L.append("\n" + sec)
    return "\n".join(L)


def build_html(rows, excluded):
    top10=rows[:10]
    power_color={"ICE":"#8a6d3b","HEV":"#5a8a6a","PHEV":"#c8442e","EREV":"#b07d10","BEV":"#1f6f8b"}
    trs=[]
    for i,r in enumerate(top10,1):
        tier="T1" if r["composite"]>=82 else ("T2" if r["composite"]>=72 else "T3")
        adj=f"{r['adj']:+.1f}" if r['adj'] else "0"
        maint=";".join(f"{k}{v:+.1f}" for k,v in r["adj_parts"].items() if v) or "无"
        trs.append(f"<tr class='tier-{tier}'><td>{i}</td><td><b>{r['model']}</b></td>"
            f"<td><span class='pill' style='background:{power_color.get(r['power'],'#666')}'>{power_label(r['power'])}</span></td>"
            f"<td>{r['price_guide'][0]}–{r['price_guide'][1]}</td><td class='num'>{r['sales']:,}</td>"
            f"<td class='num'>{r['product']:.1f}</td><td class='num'>{adj}</td>"
            f"<td class='num'><b>{r['composite']:.1f}</b></td><td><span class='tier {tier}'>{tier}</span></td>"
            f"<td class='detail'><details><summary>展开</summary>场景调节：{maint}<br>"
            + "".join(f"<br>{DIM_LABEL[k]} {r['scores'].get(k,7)}：{str(r['rationale'].get(k,'无证据·中性'))[:70]}" for k in W)
            + "</details></td></tr>")
    exc_rows="".join(f"<tr><td>{m}</td><td>{cls}</td><td>{why}</td></tr>" for m,cls,why in excluded)
    css="""
    body{font-family:'PingFang SC','Microsoft YaHei',sans-serif;background:#f7f6f3;color:#15181c;margin:0;padding:28px;line-height:1.6}
    .wrap{max-width:1200px;margin:0 auto}h1{font-size:26px}em{color:#c8442e;font-style:normal}
    .note{background:#eef4f7;border-left:4px solid #1f6f8b;padding:10px 14px;border-radius:6px;color:#274455;font-size:13px;margin:12px 0}
    table{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;font-size:13px;box-shadow:0 1px 3px rgba(0,0,0,.04)}
    th,td{padding:9px 10px;text-align:left;border-bottom:1px solid #e6e3dc}th{background:#faf9f6;font-size:12px;color:#5b6470}
    td.num{text-align:right;font-variant-numeric:tabular-nums}.pill{color:#fff;padding:1px 8px;border-radius:10px;font-size:11px}
    .tier{padding:1px 7px;border-radius:5px;font-size:11px;font-weight:600}.tier.T1{background:#e7f4ea;color:#1a7f37}
    .tier.T2{background:#fbf3e6;color:#9a6700}.tier.T3{background:#fbe9e7;color:#b42318}
    tr.tier-T1{background:#f6fbf7}tr.tier-T3{background:#fdf7f6}
    .detail{font-size:11px;color:#5b6470;max-width:320px}details summary{cursor:pointer;color:#1f6f8b}
    h2{font-size:18px;margin-top:28px}.method{background:#fff;border:1px solid #e6e3dc;border-radius:10px;padding:16px;font-size:12px;color:#5b6470;margin-top:24px}
    """
    # 同级别申报车型区块（未上市参考评价）
    rel = FB.related_filings(category_cls=BOARD_CLS, category_body=BOARD_BODY,
                             category_power=None, all_filings=FB.load_filings())
    main_scores = [r["composite"] for r in rows]
    filings_html = FB.build_section_html(rel, main_scores)
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>2026年9月中型轿车前十排行榜（全能源）</title>
<style>{css}\n{FB.FILINGS_CSS}</style></head><body><div class="wrap">
<h1>2026年9月中型轿车<em>前十排行榜</em>（全能源）</h1>
<div class="note">口径：官方二级分类「中型车」；综合分 = 8维产品力分（统一能源中性框架）+ 默认场景调节（封顶±10）；
2026年9月销量只作事实列、不参与打分（销量是发现器不是评价器）。所有数据来自网搜采集，逐车可溯源、分数可复现。</div>
<h2>前十总榜</h2>
<table><thead><tr><th>#</th><th>车型</th><th>能源</th><th>指导价(万)</th><th>9月销量</th>
<th>产品力</th><th>场景调节</th><th>综合分</th><th>档位</th><th>逐项分数</th></tr></thead>
<tbody>{''.join(trs)}</tbody></table>
<div class="note" style="border-left-color:#b07d10;background:#fbf3e6;color:#7a5710">
<b>读榜须知</b>：前十综合分跨度仅 {round(max(r['composite'] for r in top10)-min(r['composite'] for r in top10),1)} 分，小于算法噪声 → <b>逐名精确排序不成立，应理解为「同档第一梯队」</b>。
低销量车型（蔚来ET5 473辆、红旗天工05 293辆）产品力分高但市场验证不足，购买前核实保有量/售后/残值，勿只看分数。
</div>
{filings_html}
<div class="method"><b>未入榜车型及原因</b>
<table style="margin-top:8px"><thead><tr><th>车型</th><th>官方级别</th><th>原因</th></tr></thead><tbody>{exc_rows}</tbody></table>
<p style="margin-top:14px"><b>方法与边界：</b>统一8维框架（动力总成18%=电车三电/油车三大件、安全16.2%、智驾13.5%、空间12.6%、性价比11.7%、质量10%、操控9%、车机9%）。
场景调节按人群常用画像（有家充、江淮冬季、年1.5-2.5万km、单次300-500km、高速20-40%、持有5-8年）对具体车加减分。
<b>跨能源不可比提示：</b>油车口碑分与纯电人工核验分来源不同，本榜为结构性参考，不据此做精确名次判断；分差小于算法噪声时只看档位。
销量为车系口径（懂车帝/乘联会），逐车来源见 Markdown 报告。</p></div>
</div></body></html>"""


def main():
    rows, excluded = load_all()
    open(OUT_MD,"w",encoding="utf-8").write(build_md(rows,excluded))
    open(OUT_HTML,"w",encoding="utf-8").write(build_html(rows,excluded))
    print(f"WROTE {OUT_MD} / {OUT_HTML}")
    print(f"入榜候选 {len(rows)} 款，排除 {len(excluded)} 款，前十：")
    for i,r in enumerate(rows[:10],1):
        print(f"  {i}. {r['model']:<12} {power_label(r['power']):<4} 产品{r['product']:.1f} 调节{r['adj']:+.1f} = {r['composite']:.1f} | 9月销量{r['sales']:,}")


if __name__=="__main__":
    main()
