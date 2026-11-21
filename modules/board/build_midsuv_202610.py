# -*- coding: utf-8 -*-
"""
build_midsuv_202610.py
生成「2026年10月 12-18万 中型SUV 跨能源前十排行榜（B类）」Markdown + HTML。

榜型：B类跨能源打分榜（用户未限定能源，覆盖燃油/油混/插混/增程/纯电）。
口径与历史 build_midsedan_202609.py 完全一致（同一套8维统一框架+场景调节）：
  产品力 = 8维加权 × 10（powertrain18/safety16.2/adas13.5/space12.6/value11.7/quality10/control9/cabin9）
  场景调节 = 默认人群画像对具体车加减分（透明展示，封顶±10）
  综合分 = 产品力 + 场景调节；主榜按综合分降序取前十
  销量(2026-10)只作事实列，不作评价分（销量是发现器不是评价器）

数据源（本榜新增采集，全部真实网搜）：
  - runs/midsuv_202610_candidates.json      候选池：车型/级别/指导价/10月销量
  - runs/midsuv_202610_scores_batch1/2/3.json  18款补采的8维分+逐配置+证据
  - categories/master_pool.json + trims.json  纯电车型已核验8维分+逐配置

铁律：级别闸门=官方中型SUV；价位闸门=官方指导价与[12,18]万有交集；
      每车必须有逐配置；无数据不进主榜并显式列因。
"""
import json, os, sys

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)

from modules.board import filings_board as FB
from modules.engine import energy_router as ER

BOARD_CLS, BOARD_BODY = "中型SUV", "SUV"
RUNS = os.path.join(HERE, "runs")
OUT_MD = os.path.join(RUNS, "midsuv_202610_12-18万_榜单.md")
OUT_HTML = os.path.join(RUNS, "midsuv_202610_12-18万_榜单.html")
AS_OF = "2026年10月"
# 用户原始提问——由 energy_router 自动判定榜型/能源，不写死、不问用户
USER_QUERY = "中型SUV 12万到18万的前十榜单"
ROUTE = ER.classify(USER_QUERY)
assert ROUTE["board_type"] == "B" and not ROUTE["needs_user_choice"], ROUTE
ALLOWED_POWERS = set(ROUTE["powers"])

# 与 build_energy_compare.py / build_midsedan_202609.py 同源的统一8维权重
W = {"powertrain":0.18,"safety":0.162,"adas":0.135,"space":0.126,
     "value":0.117,"quality":0.10,"control":0.09,"cabin":0.09}
DIM_LABEL = {"powertrain":"动力总成","safety":"安全","adas":"智驾","space":"空间",
             "value":"性价比","quality":"质量品控","control":"操控","cabin":"车机座舱"}

# 默认场景（人群常用画像，对所有车一致应用）：有家充、江淮-10~0℃、年1.5-2.5万km、
# 单次300-500km、一年1-5次长途、不限牌、持有5-8年、高速20-40%、非营运。
DEFAULT_SCENE_ADJ = {
    "BEV":  {"有家充":1.5, "续航匹配":0},   # 续航匹配按有效续航动态计算
    "PHEV": {"有家充":1.0, "油电双补":0.5},
    "EREV": {"有家充":1.0, "油电双补":0.5},
    "HEV":  {"省油无需充电":0.5},
    "ICE":  {"补能便利":0.3},
}

def norm(s): return "".join(ch for ch in str(s).lower() if ch.isalnum())

def lookup(source, key):
    """通用查表：先精确匹配，再按候选名是库内名的后缀匹配（去品牌前缀差异）。
    例：候选 'ID.AURA T6' 与母池 '大众ID.AURA T6' —— 后者以前者规范化名结尾。
    注意只接受『库名以候选名结尾』的单向后缀（品牌前缀在前），避免误匹配。"""
    if key in source: return source[key]
    hits = [v for k,v in source.items() if k.endswith(key) and len(k) > len(key)]
    if len(hits) == 1: return hits[0]
    return {}

def power_label(p): return {"ICE":"燃油","HEV":"油混","PHEV":"插混","EREV":"增程","BEV":"纯电"}.get(p,p)
POWER_COLOR = {"ICE":"#8a6d3b","HEV":"#5a8a6a","PHEV":"#c8442e","EREV":"#b07d10","BEV":"#1f6f8b"}


def load_all():
    cands = json.load(open(os.path.join(RUNS,"midsuv_202610_candidates.json"),encoding="utf-8"))["cars"]
    # 补采分数/配置
    scores = {}
    for i in (1,2,3):
        d = json.load(open(os.path.join(RUNS,f"midsuv_202610_scores_batch{i}.json"),encoding="utf-8"))
        for c in d["cars"]:
            scores[norm(c["model"])] = c
    # 母池已核验分 + 配置库逐配置
    mp = {norm(c["model"]):c for c in json.load(open(str(paths.cat("master_pool.json")),encoding="utf-8"))["cars"]}
    tr_by = {}
    for e in json.load(open(str(paths.cat("trims.json")),encoding="utf-8"))["entries"]:
        if e.get("model"): tr_by.setdefault(norm(e["model"]), e)

    rows, excluded = [], []
    for c in cands:
        # 能源闸门：只保留 energy_router 判定出的能源形式（自动判定，不问用户）
        if c.get("power") not in ALLOWED_POWERS:
            excluded.append((c["model"], f"能源{c.get('power')}不在自动判定范围内"))
            continue
        k = norm(c["model"])
        sup = lookup(scores, k)
        pool = lookup(mp, k)
        # 8维分：补采优先（有scores），否则母池
        sc = sup.get("scores") or pool.get("scores") or {}
        rationale = sup.get("rationale") or {}
        has8 = all(isinstance(sc.get(x),(int,float)) for x in W)
        # 逐配置：补采优先，否则配置库
        trims = sup.get("trims") or []
        if not trims:
            e = lookup(tr_by, k)
            for t in e.get("trims",[]):
                trims.append({"name":t.get("name"),"price":t.get("price"),
                              "cltc_km":t.get("cltc_km"),"key_configs":t.get("key_configs",[]),
                              "battery_kwh":t.get("battery_kwh")})
        # 闸门
        if not has8:
            excluded.append((c["model"], "缺8维产品力分")); continue
        if not trims:
            excluded.append((c["model"], "缺逐配置数据")); continue
        product = sum(float(sc[x])*W[x] for x in W)*10

        # 场景调节（默认画像）
        adj_parts = dict(DEFAULT_SCENE_ADJ.get(c["power"], {}))
        cltcs = [float(t.get("cltc_km")) for t in trims if t.get("cltc_km")]
        eff = None
        if c["power"]=="BEV" and cltcs:
            eff = round(max(cltcs)*0.75)   # CLTC×0.75（温度+高速综合达成率），与历史榜同口径
            if eff < 400*0.85: adj_parts["续航匹配(需400km)"] = -1.0
            elif eff >= 400*1.3: adj_parts["续航匹配(需400km)"] = 0.5
        adj = round(sum(adj_parts.values()),1)
        rows.append({
            "model":c["model"], "power":c["power"], "cls":c["cls"],
            "price_guide":c.get("price_guide"), "sales":c.get("sales_202610"),
            "sales_split":c.get("sales_split"), "note":c.get("note",""),
            "scores":sc, "rationale":rationale, "trims":trims,
            "source_url":c.get("source_url"),
            "product":round(product,1), "adj":adj, "adj_parts":adj_parts,
            "eff":eff, "composite":round(product+adj,1),
        })
    rows.sort(key=lambda r:-r["composite"])
    return rows, excluded


def tier_of(rank, n=10):
    # 与历史榜一致：前三T1、4-7 T2、8-10 T3（仅作阅读分层提示，非产品力硬判）
    if rank<=3: return "T1"
    if rank<=7: return "T2"
    return "T3"


def build_md(rows, excluded):
    top = rows[:10]
    L = ["# 2026年10月 12–18万 中型SUV 跨能源前十排行榜",""]
    L.append(f"> **榜型（自动判定）**：{ROUTE['board_type']}类，入池能源 = {ER.powers_cn(ROUTE['powers'])}。依据：{ROUTE['reason']}（由 `energy_router.py` 自动判定，未询问用户）。")
    L.append(f"> **口径**：官方二级分类「中型SUV」+ 官方指导价与12–18万有交集。")
    L.append(f"> **综合分** = 8维产品力分（统一能源中性框架，加权×10）+ 默认场景调节（封顶±10）。**2026年10月销量只作事实列、不计分**（销量是发现器不是评价器，判档用逐月中位数而非单月）。")
    L.append(f"> **销量双口径**：本表「10月销量」为单月事实列（2026年10月）；销量异常/换代月不影响产品力评分。所有分数可溯源、可复现。")
    L.append("")
    L.append("## 前十总榜")
    L.append("")
    L.append("| 排名 | 车型 | 能源 | 指导价(万) | 10月销量(辆) | 产品力 | 场景调节 | 综合分 | 档位 |")
    L.append("|---|---|---|---|---:|---:|---:|---:|---|")
    for i,r in enumerate(top,1):
        pg = r["price_guide"]
        prices = f"{pg[0]:.2f}–{pg[1]:.2f}" if pg and len(pg)==2 else "—"
        sales = f"{r['sales']:,}" if r["sales"] else "—"
        if r.get("sales_split"): sales += f"<br>({r['sales_split']})"
        tier = tier_of(i)
        L.append(f"| {i} | **{r['model']}** | {power_label(r['power'])} | {prices} | {sales} | {r['product']} | {r['adj']:+.1f} | **{r['composite']}** | {tier} |")
    L.append("")
    L.append("## 逐车评分依据与逐配置")
    L.append("")
    for i,r in enumerate(top,1):
        L.append(f"### {i}. {r['model']}（{power_label(r['power'])}，综合分 {r['composite']}）")
        L.append(f"- 产品力 {r['product']} = " + " + ".join(f"{DIM_LABEL[k]}{r['scores'].get(k,'-'):g}×{W[k]:.3f}" for k in W) + "（×10）")
        parts = "; ".join(f"{k}{v:+.1f}" for k,v in r["adj_parts"].items()) if r["adj_parts"] else "无"
        L.append(f"- 场景调节（默认画像）：{parts} = **{r['adj']:+.1f}**")
        if r.get("eff"): L.append(f"- 有效续航参考：CLTC最高{max(float(t.get('cltc_km')) for t in r['trims'] if t.get('cltc_km')):.0f}km × 0.75 ≈ {r['eff']}km")
        for k in W:
            rat = r["rationale"].get(k,"")
            L.append(f"  - {DIM_LABEL[k]} {r['scores'].get(k,'-')}：{rat[:200] if rat else '无证据·中性'}")
        L.append("- 在售配置：")
        for t in r["trims"][:6]:
            kc = "、".join(t.get("key_configs",[])[:4])
            ev = f" / CLTC {t.get('cltc_km')}km" if t.get("cltc_km") else ""
            L.append(f"  - {t.get('name')}｜{t.get('price')}万{ev}｜{kc}")
        L.append("")
    # 申报车
    rel = FB.related_filings(category_cls=BOARD_CLS, category_body=BOARD_BODY,
                             category_power=None, all_filings=FB.load_filings())
    main_scores = [r["composite"] for r in top]
    if rel:
        L.append("## 待上市申报车型（同级别参考，不进主榜）")
        L.append(FB.build_section_md(rel, main_scores))
        L.append("")
    if excluded:
        L.append("## 未入主榜候选及原因")
        for name,reason in excluded:
            L.append(f"- {name}：{reason}")
        L.append("")
    L.append("## 方法与边界")
    L.append("- 级别闸门只认官方二级分类「中型SUV」；价位按官方指导价与12–18万有交集（促销/补贴/终端优惠价不计入），故排除途观L(18.68万起)、探岳(19.89万起)、Model Y(26.35万起)。")
    L.append("- 产品力8维为证据驱动评分，每维附理由与来源；查不到确切数据给中性7.0并标注，不编造。")
    L.append("- 场景调节按人群常用画像统一应用，非用户定制；封顶±10，产品力仍主导排名。")
    L.append("- 前十综合分跨度若小于算法噪声，应理解为「同档第一梯队」而非逐名精确排序。")
    L.append(f"- 数据截止：销量{AS_OF}（太平洋汽车中型SUV榜）；申报车库更新{FB.freshness_state().get('updated','—')}。")
    return "\n".join(L)


def build_html(rows, excluded):
    top = rows[:10]
    css = """
    body{font-family:'PingFang SC','Microsoft YaHei',sans-serif;background:#f7f6f3;color:#15181c;margin:0;padding:28px;line-height:1.6}
    .wrap{max-width:1200px;margin:0 auto}h1{font-size:26px}em{color:#c8442e;font-style:normal}
    .note{background:#eef4f7;border-left:4px solid #1f6f8b;padding:10px 14px;border-radius:6px;color:#274455;font-size:13px;margin:12px 0}
    table{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;font-size:13px;box-shadow:0 1px 3px rgba(0,0,0,.04)}
    th,td{padding:9px 10px;text-align:left;border-bottom:1px solid #e6e3dc}th{background:#faf9f6;font-size:12px;color:#5b6470}
    td.num{text-align:right;font-variant-numeric:tabular-nums}.pill{color:#fff;padding:1px 8px;border-radius:10px;font-size:11px}
    .tier{padding:1px 7px;border-radius:5px;font-size:11px;font-weight:600}.tier.T1{background:#e7f4ea;color:#1a7f37}
    .tier.T2{background:#fbf3e6;color:#9a6700}.tier.T3{background:#fbe9e7;color:#b42318}
    tr.tier-T1{background:#f6fbf7}tr.tier-T3{background:#fdf7f6}
    .detail{font-size:11px;color:#5b6470;max-width:340px}details summary{cursor:pointer;color:#1f6f8b}
    h2{font-size:18px;margin-top:28px}.method{background:#fff;border:1px solid #e6e3dc;border-radius:10px;padding:16px;font-size:12px;color:#5b6470;margin-top:24px}
    .banner{background:#fbf3e6;border-left:4px solid #b07d10;padding:10px 14px;border-radius:6px;color:#7a5710;font-size:12.5px;margin:12px 0}
    """
    h = [f"<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>",
         "<meta name='viewport' content='width=device-width,initial-scale=1'>",
         "<title>2026年10月 12-18万 中型SUV 跨能源前十排行榜</title>",
         f"<style>{css}\n{FB.FILINGS_CSS}</style></head><body><div class='wrap'>",
         "<h1>2026年10月 12–18万 中型SUV <em>跨能源前十排行榜</em></h1>",
         f"<div class='note'><b>榜型自动判定</b>：{ROUTE['board_type']}类，入池能源 = {ER.powers_cn(ROUTE['powers'])}。"
         f"依据：{ROUTE['reason']}（由 energy_router 自动判定，未询问用户）。</div>",
         "<div class='note'>口径：官方二级分类「中型SUV」+ 官方指导价与12–18万有交集；综合分 = 8维产品力分（统一能源中性框架）+ 默认场景调节（封顶±10）；10月销量只作事实列、不参与打分。</div>",
         "<div class='banner'><b>销量口径说明</b>：本榜为<b>当前状态</b>排名；表内「10月销量」是2026年10月<b>单月事实列</b>，与判档所用的「近6个月逐月中位数」是两套口径，请勿混淆。跨能源榜按统一8维打分+场景调节，销量仅作发现器不计分。</div>",
         "<h2>前十总榜</h2>",
         "<table><thead><tr><th>#</th><th>车型</th><th>能源</th><th>指导价(万)</th><th>10月销量</th><th>产品力</th><th>场景调节</th><th>综合分</th><th>档位</th><th>逐项分数/依据</th></tr></thead><tbody>"]
    for i,r in enumerate(top,1):
        pg=r["price_guide"]; prices=f"{pg[0]:.2f}–{pg[1]:.2f}" if pg and len(pg)==2 else "—"
        sales=f"{r['sales']:,}" if r["sales"] else "—"
        if r.get("sales_split"): sales+=f"<br><span style='font-size:10px;color:#888'>{r['sales_split']}</span>"
        tier=tier_of(i)
        parts="; ".join(f"{k}{v:+.1f}" for k,v in r["adj_parts"].items()) if r["adj_parts"] else "无"
        det=[f"场景调节：{parts} = {r['adj']:+.1f}"]
        for k in W:
            rat=r["rationale"].get(k,"")
            det.append(f"<b>{DIM_LABEL[k]} {r['scores'].get(k,'-')}</b>：{(rat[:180] if rat else '无证据·中性')}")
        det.append("<b>在售配置</b>：" + "；".join(f"{t.get('name')} {t.get('price')}万" + (f"/{t.get('cltc_km')}km" if t.get('cltc_km') else "") for t in r["trims"][:6]))
        h.append(f"<tr class='tier-{tier}'><td>{i}</td><td><b>{r['model']}</b></td>"
                 f"<td><span class='pill' style='background:{POWER_COLOR.get(r['power'],'#666')}'>{power_label(r['power'])}</span></td>"
                 f"<td>{prices}</td><td class='num'>{sales}</td><td class='num'>{r['product']}</td>"
                 f"<td class='num'>{r['adj']:+.1f}</td><td class='num'><b>{r['composite']}</b></td>"
                 f"<td><span class='tier {tier}'>{tier}</span></td>"
                 f"<td class='detail'><details><summary>展开</summary>{'<br>'.join(det)}</details></td></tr>")
    h.append("</tbody></table>")
    span = top[0]["composite"] - top[-1]["composite"] if len(top)>1 else 0
    h.append(f"<div class='note' style='border-left-color:#b07d10;background:#fbf3e6;color:#7a5710'><b>读榜须知</b>：前十综合分跨度 {span:.1f} 分"
             + ("，小于算法噪声 → <b>逐名精确排序不成立，应理解为「同档第一梯队」</b>。" if span<6 else "，排名有区分度。")
             + "低销量车型产品力分可能较高但市场验证不足，购买前核实保有量/售后/残值，勿只看分数。</div>")
    # 申报车
    rel = FB.related_filings(category_cls=BOARD_CLS, category_body=BOARD_BODY,
                             category_power=None, all_filings=FB.load_filings())
    main_scores = [r["composite"] for r in top]
    if rel: h.append(FB.build_section_html(rel, main_scores))
    if excluded:
        h.append("<h2>未入主榜候选及原因</h2><div class='method'>")
        for name,reason in excluded: h.append(f"- {name}：{reason}<br>")
        h.append("</div>")
    h.append("<div class='method'><b>方法与边界</b><br>"
             "- 级别闸门只认官方二级分类「中型SUV」；价位按官方指导价与[12,18]万有交集（促销/补贴/终端优惠价不计入）。<br>"
             "- 产品力8维证据驱动，每维附理由与来源，查不到给中性7.0不编造；场景调节按统一画像封顶±10。<br>"
             f"- 数据截止：销量{AS_OF}（太平洋汽车中型SUV榜）。</div>")
    h.append("</div></body></html>")
    return "\n".join(h)


def main():
    rows, excluded = load_all()
    md = build_md(rows, excluded)
    with open(OUT_MD,"w",encoding="utf-8") as f: f.write(md)
    html = build_html(rows, excluded)
    with open(OUT_HTML,"w",encoding="utf-8") as f: f.write(html)
    print(f"wrote {OUT_MD} ({len(md)} chars)")
    print(f"wrote {OUT_HTML} ({len(html)} chars)")
    print("\n前十：")
    for i,r in enumerate(rows[:10],1):
        print(f"  {i:>2}. {r['model']:<16} {power_label(r['power']):<4} 综合{r['composite']:>5} (产品{r['product']}+调节{r['adj']:+.1f}) 10月销{r['sales']}")
    if excluded:
        print("\n未入榜:", excluded)

if __name__=="__main__":
    main()
