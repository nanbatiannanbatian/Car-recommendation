#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨能源「选能源版」页面（build_energy_form_guide.py）。

用户 2026-11-26 拍板：不同能源形式的页面**删掉打分榜**，只保留
「用车条件 + 能源形式推荐」，具体车型排名请用户去看对应能源的单能源榜。

为什么不再排名：
  跨能源综合分 = 产品力 + 场景调节 + 硬条件，而产品力本身是「统一 8 维」——
  但油车的三电维由三大件代替、插混两者兼顾，分数刻度并不真的等价；
  再把不同能源排成一列，读者会把它当「谁更好」的精确结论。页面的真实价值
  是回答「我这种用法适合哪种能源」，而不是「哪台车第一」。

页面内容（全部服务端渲染，可复现）：
  ① 一句话形式结论（engine.form_narrative）
  ② 用车条件画像（11 项，给定时画像）
  ③ 能源形式适配表（最适配/可考虑/次选/不建议 + 样本数 + 家族内部明细）
  ④ 每个形式的适配理由（硬条件车数、不建议项、代表车型仅作说明、不排名）
  ⑤ 去哪看具体车型排名（各能源单榜入口说明）
  ⑥ 方法与边界（为什么不排名、均分为什么不能当排名）

用法：
    python -m modules.board.build_energy_form_guide [--out 路径]
"""

from __future__ import annotations

import argparse
import html as H
import os

import paths

from modules.envscore import engine as ENG
from modules.board import build_midsuv_full as BF

OUT_DEFAULT = os.path.join(str(paths.RUNS_DIR), "midsuv_202610_选能源版.html")

FORM_ORDER = ["BEV", "PHEV", "EREV", "FUEL", "ICE", "HEV"]


def _narrative(forms: list[dict]) -> str:
    """形式结论，但**不点名最强车型**——选能源版不提供任何跨能源名次线索。"""
    def name(f):
        return ENG.form_name(f["power"])
    best = [f for f in forms if f.get("label") == "最适配"]
    near = [f for f in forms if f.get("label") == "可考虑"]
    thin = [f for f in forms if f.get("label") == "样本不足"]
    if not best:
        return "池内没有样本 ≥3 款的能源形式，暂不给形式推荐。"
    b = best[0]
    s = (f"本画像下最适配 {name(b)}（池内 {b['n']} 款，平均场景调节 {b['avg_adj']:+.1f}，"
         f"硬条件触发 {b['hard']} 款）")
    if near:
        s += "；" + "、".join(f"{name(f)}（{f['avg']:.1f}）" for f in near) +              f" 与它相差不到 1.5 分，可一并考虑"
    s += "。"
    if thin:
        s += "；" + "、".join(name(f) for f in thin) + " 池内样本不足 3 款，本次不作推荐。"
    s += " 结论只说明「这种用法适合哪种能源」，不代表某台车更强；确定形式后去看对应能源的单能源榜。"
    return s


def _esc(x) -> str:
    return H.escape(str(x))


def _cond_rows(cond: dict) -> str:
    rows = []
    for fid, spec in ENG.FACTORS.items():
        v = cond.get(fid)
        label = next((o["label"] for o in spec["options"] if o["v"] == v), str(v))
        rows.append(f"<tr><td>{_esc(spec['label'])}</td><td>{_esc(label)}</td></tr>")
    return "".join(rows)


def _form_table(forms: list[dict]) -> str:
    rows = []
    for f in forms:
        cn = ENG.form_name(f["power"])
        mem = "、".join(f"{m['cn']} {m['avg']:.1f}（{m['n']} 款）" for m in f.get("members") or [])
        rows.append(
            "<tr>"
            f"<td><b>{_esc(cn)}</b></td>"
            f"<td>{_esc(f.get('label') or '—')}</td>"
            f"<td>{f['n']}</td>"
            f"<td>{f['avg']:.1f}</td>"
            f"<td>{f['avg_adj']:+.1f}</td>"
            f"<td>{f['hard']}</td>"
            f"<td>{f['bad']}</td>"
            f"<td style='text-align:left'>{_esc(mem or '—')}</td>"
            "</tr>")
    return "".join(rows)


def build(out: str = OUT_DEFAULT) -> str:
    cands, scores, mp, trims, vp, fl_by = BF.load_context()
    cars, meta, route = BF.build_cars(cands, scores, mp, trims, vp, fl_by)
    cond = ENG.DEFAULT_COND
    rows = ENG.analyze(cars, cond)
    forms = ENG.recommend_form(rows, cond)
    narrative = _narrative(forms)
    diag = ENG.check_profile(cond)

    forms_html = _form_table(forms)
    diag_html = ("<p class='warn'>" + "；".join(_esc(w["text"]) for w in diag) + "</p>") if diag else \
        "<p class='ok'>条件自洽，未发现矛盾组合。</p>"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>12–18万中型SUV · 能源形式选择指南</title>
<style>
:root{{--ink:#1c2430;--sub:#5b6470;--line:#d9dee5;--bg:#f6f7f9;--accent:#1f6f8b;--warn:#b42318}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}}
.wrap{{max-width:1080px;margin:0 auto;padding:32px 20px 64px}}
h1{{font-size:26px;margin:0 0 6px}} h2{{font-size:18px;margin:28px 0 10px;padding-left:10px;border-left:4px solid var(--accent)}}
.sub{{color:var(--sub);font-size:13px}}
.card{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:16px;margin:12px 0}}
table{{width:100%;border-collapse:collapse;font-size:13px}} th,td{{border-bottom:1px solid var(--line);padding:8px;text-align:center}}
th{{background:#eef3f5;font-weight:600}} td:first-child,th:first-child{{text-align:left}}
.lead{{font-size:17px;line-height:1.8}} .ok{{color:#1a7f37}} .warn{{color:var(--warn)}}
.note{{color:var(--sub);font-size:13px}} .pill{{display:inline-block;background:#eef3f5;border-radius:999px;padding:2px 10px;margin:2px}}
ul{{margin:8px 0 0 18px}} li{{margin:4px 0}}
</style></head><body><div class="wrap">
<h1>12–18万 中型SUV · 能源形式选择指南</h1>
<p class="sub">不出跨能源名次 · 只回答「你这种用法适合哪种能源」 · 具体车型请查对应能源的单能源榜</p>

<h2>一句话结论</h2>
<div class="card lead">{_esc(narrative)}</div>

<h2>你的用车条件（11 项）</h2>
<div class="card">
<table><thead><tr><th>用车条件</th><th>取值</th></tr></thead><tbody>{_cond_rows(cond)}</tbody></table>
{diag_html}
<p class="note">这是本页采用的画像（人群默认）。换条件会改变形式结论——把条件告诉助手，可重跑本页。</p>
</div>

<h2>能源形式适配</h2>
<div class="card">
<table><thead><tr><th>能源形式</th><th>结论</th><th>池内车数</th><th>均分(参考)</th>
<th>平均场景调节</th><th>触发硬条件车数</th><th>不建议项数</th><th style="text-align:left">家族内部</th></tr></thead>
<tbody>{forms_html}</tbody></table>
<p class="note">「均分(参考)」是池内该形式车辆综合分的平均值，<b>不是排名</b>：池内车源结构（几款车、什么价位）会直接影响均分；样本 &lt; 3 款标「样本不足」，不参与形式推荐。</p>
</div>

<h2>具体车型去哪看</h2>
<div class="card">
<p>本页故意不列跨能源名次。确定能源形式后，按形式去看<b>单能源打分榜</b>（同能源内 8–9 维加权，口径一致、可比较）：</p>
<p>{"".join(f'<span class="pill">{_esc(x)}</span>' for x in ("纯电榜", "插混榜", "增程榜", "燃油/油混榜"))}</p>
<p class="note">跟助手说「给我 12–18 万纯电 SUV 榜」即可出对应单能源榜；榜单会带逐配置、名次带与插补披露。</p>
</div>

<h2>方法与边界</h2>
<div class="card">
<ul>
<li><b>为什么不排名</b>：跨能源的「产品力」用统一维度刻度，但纯电的三电、燃油的三大件、插混的两者兼顾并不是同一件事；分数可用来判断「适配」，不足以支撑「谁更强」的精确名次。</li>
<li><b>形式结论怎么来</b>：按形式家族汇总池内车辆的综合分、硬条件触发数与不建议项，样本 ≥ 3 款才参与推荐。</li>
<li><b>池内结构限制</b>：某形式入池车辆少或价位偏离，会让均分失真；结论只在当前画像与当前池内成立。</li>
<li><b>可复现</b>：本页为服务端定稿快照，同一输入必得同一输出；数据月 202610。</li>
</ul>
</div>
<p class="sub">生成：modules/board/build_energy_form_guide.py · 画像：人群默认（11 项）· 数据：master_pool + trims + 物理续航模型</p>
</div></body></html>"""

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="跨能源选能源版页面")
    ap.add_argument("--out", default=OUT_DEFAULT)
    a = ap.parse_args(argv)
    p = build(a.out)
    print(f"[ok] {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
