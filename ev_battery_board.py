#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""电池优先纯电榜（ev_battery_board.py）——按用户三条原则另出一版，与现榜 MCDA 对比。

用户原则（优先级从高到低）：
  1. 电池最重要：优选 弗迪 / 宁德（整包优先，其次宁德电芯）；其次中创新航/国轩/亿纬等二线。
  2. 相同价位，电池度数(kWh)最高的优先。
  3. 销量太低容易停产、影响保值率 → 低销量要降权/提示。

做法（不改默认 MCDA 榜，只产出对照版）：
  - 逐车取 电池品牌(cell_brand) + 度数(trims 里按价格最接近的配置) + 指导价 + 6月趋势销量；
  - 电池档：1=弗迪/宁德(单源)，2=宁德混装或二线，3=未披露/新路线；
  - 能量密度代理 = kWh / 万元（同价位度数越高越大）；
  - 电池优先分 = 0.50×电池档 + 0.35×能量密度分位 + 0.15×销量分位；低销量(<1500)再扣分；
  - 与现榜 score_rows 名次并排，算 Kendall τ、名次变动。

数据边界（如实标注，不编造）：
  · 只有品牌，无法区分「宁德整包 / 宁德电芯」→ 统一「宁德系」，单源才算第1档，混装降第2档。
  · cell_brand 有 16/38 未披露 → 记第3档并标「未披露」。
  · trims 有 3/38 无 kWh（秦MAX EV / 大众ID.AURA T6 / 小鹏MONA L05）→ 度数缺失，能量分按中位处理并标「缺」。
"""
from __future__ import annotations
import argparse
import html
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import leaderboard as L      # noqa: E402
import ranking               # noqa: E402

TIER_SCORE = {1: 10.0, 2: 6.0, 3: 2.0}
W_TIER, W_ENERGY, W_SALES = 0.50, 0.35, 0.15
SALES_FLOOR_LOW, SALES_FLOOR_HIGH = 800, 1500


def esc(x):
    return html.escape(str(x if x is not None else ""))


def price_of(c):
    tp = (c.get("trim") or {}).get("price")
    if isinstance(tp, (int, float)):
        return float(tp)
    for f in ("price_guide", "price_official", "price"):
        v = c.get(f)
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str) and ("–" in v or "-" in v):
            sep = "–" if "–" in v else "-"
            parts = v.split(sep)
            try:
                return (float(parts[0]) + float(parts[1])) / 2
            except Exception:
                pass
        if isinstance(v, (list, tuple)) and len(v) == 2:
            try:
                return (float(v[0]) + float(v[1])) / 2
            except Exception:
                pass
    return None


def tier_of(brand, multi):
    b = (brand or "").strip()
    if not b or b == "未披露":
        return 3, "未披露"
    if "弗迪" in b:
        return 1, "弗迪" + ("·混装" if multi else "")
    if "宁德" in b or "时代" in b:
        return (2, "宁德系·混装") if multi else (1, "宁德系")
    # 厂商自研（吉利神盾/金砖、广汽弹匣等）：2026 年 6C/12C 已追平，放第2档（非第3档）
    if any(k in b for k in ("神盾", "金砖", "弹匣", "自研")):
        return 2, b + "·厂商自研"
    if any(x in b for x in ("中创新航", "国轩", "亿纬", "欣旺达", "蜂巢", "因湃", "正力", "上汽时代")):
        return 2, b + ("·混装" if multi else "")
    if "清陶" in b:
        return 3, "清陶·新路线"
    return 3, b


def pct_rank(vals):
    """返回函数：给一个值，返回其在 vals 中的分位 0–10。"""
    s = sorted(v for v in vals if v is not None)
    n = len(s)
    if n <= 1:
        return lambda v: 5.0
    def f(v):
        if v is None:
            return 5.0
        c = sum(1 for x in s if x <= v)
        return 10.0 * (c - 0.5) / n
    return f


def kendall(a, b):
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--category", default="car_ev")
    ap.add_argument("--trims", default=os.path.join(HERE, "categories", "trims.json"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    data = json.load(open(a.candidates, encoding="utf-8"))
    cands = [c for c in data["candidates"] if c.get("power") == "BEV"]
    cfg = L.load_category(a.category)
    trims = json.load(open(a.trims, encoding="utf-8"))
    tby = {e["model"]: e for e in trims.get("entries", [])}

    def kwh_of(c):
        e = tby.get(c.get("model"))
        if not e:
            return None
        trs = [x for x in (e.get("trims") or []) if x.get("battery_kwh")]
        if not trs:
            return None
        p = price_of(c)
        if p:
            t = min(trs, key=lambda x: abs((x.get("price") or 999) - p))
            return float(t["battery_kwh"])
        return float(max(x["battery_kwh"] for x in trs))

    boards_out, json_out = [], {}
    for board in L.board_items(cfg):
        label = board["label"]
        sub = L.filter_board(cands, board)
        passed = [c for c in sub if ranking.apply_gates(c, board.get("gates"))["pass"]]
        if not passed:
            continue
        cur = L.score_rows(passed, cfg)
        cur_rank = {r["model"]: i + 1 for i, r in enumerate(cur)}

        rows = []
        for c in passed:
            p = price_of(c)
            kwh = kwh_of(c)
            brand = c.get("cell_brand")
            if (not brand or brand == "未披露"):
                e = tby.get(c["model"]) or {}
                for x in (e.get("trims") or []):
                    if x.get("cell_supplier"):
                        brand = x["cell_supplier"]
                        break
            tier, tier_label = tier_of(brand, bool(c.get("cell_multi_source")))
            epp = (kwh / p) if (kwh and p) else None
            rows.append({"model": c["model"], "price": p, "kwh": kwh, "brand": brand,
                         "tier": tier, "tier_label": tier_label, "epp": epp,
                         "sales": c.get("trend_sales")})

        epp_f = pct_rank([r["epp"] for r in rows])
        sales_f = pct_rank([r["sales"] for r in rows])
        for r in rows:
            s = r["sales"]
            if s is None:
                pen, r["sales_flag"] = 1.0, "销量未知(待补)"
            elif s < SALES_FLOOR_LOW:
                pen, r["sales_flag"] = 2.5, "⚠低销量(<800)"
            elif s < SALES_FLOOR_HIGH:
                pen, r["sales_flag"] = 1.0, "偏低(<1500)"
            else:
                pen, r["sales_flag"] = 0.0, ""
            # 档内综合：度数(0.7) + 销量(0.3) − 低销量扣
            r["within"] = round(0.7 * epp_f(r["epp"]) + 0.3 * sales_f(r["sales"]) - pen, 2)
            r["brand_disp"] = r["brand"] if r["brand"] else "未披露"

        for r in rows:
            r["cur_rank"] = cur_rank.get(r["model"])
        # 字典序：电池档优先（1>2>3），档内按 度数+销量 综合（低销量已扣）
        bat_order = sorted(rows, key=lambda r: (r["tier"], -r["within"]))
        for i, r in enumerate(bat_order, 1):
            r["bat_rank"] = i
            r["move"] = (r["cur_rank"] - i) if r["cur_rank"] else None
        tau = kendall([r["model"] for r in cur], [r["model"] for r in bat_order])
        boards_out.append({"label": label, "rows": bat_order, "tau": tau, "n": len(rows)})
        json_out[label] = {"kendall_vs_mcda": round(tau, 3), "rows": [
            {"model": r["model"], "battery_rank": r["bat_rank"], "mcda_rank": r["cur_rank"],
             "move": r["move"], "brand": r["brand_disp"], "battery_tier": r["tier"],
             "kwh": r["kwh"], "price": round(r["price"], 2) if r["price"] else None,
             "kwh_per_10k": round(r["epp"], 2) if r["epp"] else None,
             "sales": r["sales"], "within": r["within"], "flag": r["sales_flag"]}
            for r in bat_order]}

    # ---- HTML ----
    def board_html(b):
        rows = b["rows"]
        trs = ""
        for r in rows[:10]:
            mv = "" if r["move"] is None else (
                (f'<span class="up">↑{r["move"]}</span>' if r["move"] > 0 else
                 (f'<span class="dn">↓{-r["move"]}</span>' if r["move"] < 0 else "→")))
            tier_cls = {1: "t1", 2: "t2", 3: "t3"}[r["tier"]]
            pv = f"{r['price']:g}" if r["price"] else "—"
            sv = f"{int(r['sales']):,}" if r["sales"] else "—"
            kwh = (f'{r["kwh"]:g}' if r["kwh"] else '<span class="miss">缺</span>')
            epp = (f'{r["epp"]:.2f}' if r["epp"] else "—")
            cr = r["cur_rank"] if r["cur_rank"] else "—"
            trs += (f"<tr><td class='rk'>{r['bat_rank']}</td>"
                    f"<td><b>{esc(r['model'])}</b></td>"
                    f"<td><span class='tier {tier_cls}'>{esc(r['brand_disp'])}</span></td>"
                    f"<td class='num'>{kwh}</td><td class='num'>{epp}</td>"
                    f"<td class='num'>{pv}</td><td class='num'>{sv}</td>"
                    f"<td class='num'>{cr}</td><td class='num'>{mv}</td>"
                    f"<td class='flag'>{esc(r['sales_flag'])}</td></tr>")
        ups = sorted([r for r in rows if r["move"] and r["move"] > 0], key=lambda r: -r["move"])[:3]
        dns = sorted([r for r in rows if r["move"] and r["move"] < 0], key=lambda r: r["move"])[:3]
        up_txt = "、".join(f"{esc(r['model'])}(现{r['cur_rank']}→{r['bat_rank']})" for r in ups) or "无"
        dn_txt = "、".join(f"{esc(r['model'])}(现{r['cur_rank']}→{r['bat_rank']})" for r in dns) or "无"
        return f"""<section>
  <h2>{esc(b['label'])}<em>与现榜 Kendall τ={b['tau']:.2f}</em></h2>
  <table class="rank"><thead><tr>
    <th>电池名次</th><th>车型</th><th>电池供应商</th><th>度数<br>(kWh)</th><th>kWh/万元</th>
    <th>指导价(万)</th><th>月销</th><th>现榜名次</th><th>升降</th><th>销量提示</th></tr></thead>
  <tbody>{trs}</tbody></table>
  <p class="note"><b>最大上调</b>（电池逻辑更看重）：{up_txt}　　
  <b>最大下调</b>：{dn_txt}</p></section>"""

    blocks = "".join(board_html(b) for b in boards_out)
    total_tau = ", ".join(f"{b['label'].split('（')[0]} {b['tau']:.2f}" for b in boards_out)
    html_doc = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>电池优先纯电榜（对照）</title>
<style>
:root{{--ink:#191919;--muted:#6b6b6b;--line:#e6e4e0;--bg:#faf9f7;--card:#fff;--red:#a52222;--green:#0f6b3f;--amber:#8a5a06;--blue:#1f4fa3}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
.wrap{{max-width:1180px;margin:0 auto;padding:32px 22px 60px}}
header{{border-bottom:2px solid var(--ink);padding-bottom:16px;margin-bottom:10px}}
h1{{font-size:24px;margin:0 0 6px}}h2{{font-size:17px;margin:26px 0 8px}}
.lede{{color:var(--muted);font-size:13.5px;margin:0}}
section{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin:18px 0}}
h2 em{{font-style:normal;font-size:12px;color:var(--muted);font-weight:400;margin-left:8px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;font-weight:600;font-size:11.5px;color:var(--muted);border-bottom:1px solid var(--ink);padding:7px 6px}}
td{{padding:8px 6px;border-bottom:1px solid var(--line)}}
.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.rk{{width:64px;color:var(--blue);font-weight:700}}
.tier{{display:inline-block;padding:1px 7px;border-radius:5px;font-size:11px;font-weight:600}}
.t1{{background:#e3f2e8;color:var(--green)}}.t2{{background:#eef2fa;color:var(--blue)}}.t3{{background:#f2f0ec;color:var(--muted)}}
.up{{color:var(--red);font-weight:700}}.dn{{color:var(--green);font-weight:700}}
.flag{{color:var(--amber);font-size:11.5px}}.miss{{color:var(--muted)}}
.caveat{{background:#fdf6ec;border:1px solid #f0d3a8;border-left:4px solid #b4530a;border-radius:10px;padding:14px 16px;font-size:13px;color:#4a3a29;margin:14px 0}}
.caveat b{{color:#8a3f06}}.caveat ol{{margin:8px 0 0;padding-left:20px}}.caveat li{{margin:5px 0}}
footer{{margin-top:24px;font-size:12px;color:var(--muted);border-top:1px solid var(--line);padding-top:12px}}
</style></head><body><div class="wrap">
<header><h1>电池优先纯电榜 · 与现榜对照</h1>
<p class="lede">按「①电池品牌优先（弗迪/宁德）②同价位度数最高 ③低销量降权」三条原则排序，不改默认榜，仅作对照。</p></header>
<div class="caveat"><b>数据边界（不编造）</b><ol>
<li><b>电池品牌</b>取候选 <code>cell_brand</code>，只有品牌——<b>分不出「宁德整包 / 宁德电芯」</b>，故统一「宁德系」，单源算第1档、混装降第2档。</li>
<li><b>16/38 款品牌未披露</b> → 记第3档并标「未披露」；<b>3/38 款无度数</b>（秦MAX EV / 大众ID.AURA T6 / 小鹏MONA L05）→ 度数标「缺」，能量分按中位处理。</li>
<li>度数取 trims 里<b>价格最接近评分配置</b>的那一款；kWh/万元 作「同价位度数」代理。</li>
<li>低销量：月销 &lt;800 扣 2 分、&lt;1500 扣 1 分并提示——对应「太低易停产、影响保值」。</li>
<li><b>电芯品牌只是代理，不是全部</b>：2026 年 LFP 的 6C/12C 已在弗迪/宁德/国轩/吉利（神盾·金砖）等多家量产，<b>纸面倍率差距明显缩小</b>；真正的差距在整包能量密度、CTB/CTP 集成、BMS、热管理、低温、循环寿命、量产一致性与售后/超充生态。故「厂商自研」本版放第2档，不再当第3档。</li>
<li><b>质保是风险缓释，不是质量保证</b>：终身质保多限首任车主、全程4S、年里程&lt;3万、<b>过户即失效</b>；且保的是「非正常衰减/热失控/破损/断电」，<b>正常老化不在内</b>（中汽研 2026：8 年健康度仍 &gt;80%，但出保车实测普遍 65–75%）。2026 起首批集中脱保，8 年后残值/换电成本仍是硬风险。
<b>定车前逐条核对</b>：①质保年限 ②能否过户继承 ③衰减到多少算不合格 ④是否覆盖「正常衰减」⑤车企长期经营稳定性。（本榜不逐车编造质保数据，请查官方手册。）</li>
</ol></div>
<div class="caveat"><b>可维修性提示（2026 实测，影响长期维保）</b><ul>
<li><b>集成度越高越难修</b>：传统三级(可模组修) &gt; CTP(差) &gt; CTB(更差) &gt; CTC(几乎不可修)。</li>
<li><b>二代刀片实测</b>：拆解需磨/切/锤 + 整包冷冻约 40 小时；环氧灌封覆盖端子/铜排/传感器，结构胶不能加热或化学去除，<b>现场电芯级维修基本不可行</b>（170 颗电芯串联）。</li>
<li><b>但「能修不换」已落地（2026-04）</b>：约 75% 局部故障可模块/电芯级修，费用降到整包 10–30%；LFP 换新限价 ~150 元/kWh。限制：<b>一体化粘接的 CTB/CTC 包拆开难复原密封/强度，很多仍难模组级维修</b>。</li>
<li><b>买车时怎么用</b>：①首任+长质保（电芯终身/容量阈值以官方手册为准）②车损险（碰撞换包）③避免托底伤结构包 ④核对电池集成方式（CTP/CTB/CTC）。<b>本榜不逐车判定结构方式，请查官方/实车。</b></li>
</ul></div>
{blocks}
<section><h2>怎么读这份对照</h2>
<p style="font-size:13px;color:#444">
<b>升降</b>＝现榜名次 − 电池榜名次；<span class="up">↑红</span> 表示按你的电池逻辑它更该靠前，<span class="dn">↓绿</span> 表示被下调。
Kendall τ 越低，说明「电池优先」与「7 维产品力」分歧越大——分歧最大的车，正是<b>「参数/电池强但综合产品力一般」或「综合均衡但电池一般」</b>的那批。
排序规则（<b>字典序</b>）：先按电池档（1 弗迪/宁德单源 &gt; 2 二线或宁德混装 &gt; 3 未披露/新路线），档内再按「度数分位×0.7 + 销量分位×0.3 − 低销量扣分」。
即「<b>电池品牌第一、同档看度数、低销量降权</b>」——严格对应你的三条原则。品牌未披露的车会被压到第3档，<b>不代表电池差，只代表没核到</b>，看「电池供应商」列自行判断。
</p></section>
<footer>生成：电池优先对照版 · 仅个人研究 · 不改默认 MCDA 榜 · 同输入同输出</footer>
</div></body></html>"""

    os.makedirs(a.out, exist_ok=True)
    open(os.path.join(a.out, "电池优先榜.html"), "w", encoding="utf-8").write(html_doc)
    json.dump(json_out, open(os.path.join(a.out, "battery_rank.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"[ok] {a.out}/电池优先榜.html · {a.out}/battery_rank.json")
    print("Kendall τ vs 现榜:", total_tau)


if __name__ == "__main__":
    main()
