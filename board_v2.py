# -*- coding: utf-8 -*-
"""统一榜单引擎 v2：打分榜唯一主榜 + 事实列参考(不计分) + 价位段子榜。

与旧链路的关系
--------------
旧链路里「事实榜」(kWh/万元、电耗单独排序) 和「打分榜」是两套并列产物，用户容易混。
v2 把它们收敛成一套：

  1. 主榜 = 打分榜（8 维加权），这是唯一权威名次来源。
  2. 事实指标（kWh、kWh/万元、电耗、续航、电芯、800V、销量）作为「参考列」贴在主榜每行，
     明确标注「参考·不计分」——它们帮助理解，但绝不参与加权。
  3. 价位段子榜 = 把同一份主榜分数按预算分组的展示视图。它复用主榜分数，
     不重新打分、不改权重。回答「我这个预算买哪款最好」。

价位差异怎么处理（用户三选一的决策）
------------------------------------
不做「价格相差太多就不排名」的硬排除。理由：
  - 同级别（官方二级分类）已经控制了「车有多大、什么用途」这个最大混淆变量；
  - 价格对竞争力的影响由「性价比」维度(weight≈0.117)吸收——贵车性价比低但产品力维度占优，
    便宜车反之，混排本身就是有意义的综合比较；
  - 硬排除会丢掉「多花 5 万值不值」的信息，且价位边界会造成临界车跳榜的假精确。
所以：主榜全价位混排，价格高的性价比分低但依然参与排名；再附加价位段子榜给预算敏感用户。

分段汇总总榜（v8.3，默认展示模板）
------------------------------------
用户规则：排行榜如果没有价位段，就分几个价位段，每段取前几名，最后把这几段汇总成总榜。
`segmented_summary()` 实现：按价位段分组 → 每段按主榜分数取前 per_band 名 →
汇总后按主榜分数降序，形成一张「各价位段都有代表」的总榜。分数仍来自主榜，不重新打分。
与价位段子榜的区别：子榜是全量分组视图；分段汇总总榜是各段前几名拼成的 Top N 总榜。

CLI
---
  python3 board_v2.py --candidates <带scores的候选池.json> \
      --config categories/car_ev_1218_quality.json \
      --policy categories/rank_v2_policy.json \
      --facts <事实参数.json, 可选> --out <目录>

  python3 board_v2.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))

try:
    import leaderboard as LB
except ImportError:  # pragma: no cover
    LB = None


# ---------------------------------------------------------------- 事实参考列
def fact_columns(c: dict, facts: dict[str, dict], policy: dict) -> dict:
    """抽取一台车的事实参考列。返回 {label: value}；不返回任何参与打分的字段。"""
    f = facts.get(c.get("model"), {}) or {}
    price = c.get("price_wan") or c.get("trim", {}).get("price") if isinstance(c.get("trim"), dict) \
        else c.get("price_wan")
    price = _num(price)
    kwh = _num(f.get("kwh") or c.get("kwh"))
    out: dict[str, Any] = {}
    by_key = {col["key"]: col for col in policy["fact_reference_columns"]}
    for col in policy["fact_reference_columns"]:
        k, label = col["key"], col["label"]
        if k == "price":
            v = price
        elif k == "kwh":
            v = kwh
        elif k == "kwh_per_wan":
            v = round(kwh / price, 2) if (kwh and price) else None
        elif k == "consumption":
            v = _num(f.get("consumption") or c.get("consumption"))
        elif k == "cltc":
            v = _num(f.get("cltc") or c.get("cltc"))
        elif k == "cell_brand":
            v = f.get("cell_brand") or c.get("cell_brand")
        elif k == "is_800v":
            v = f.get("is_800v", c.get("is_800v"))
        elif k == "trend_sales":
            v = c.get("trend_sales")
        else:
            v = f.get(k)
        if isinstance(v, bool):
            v = "是" if v else "否"
        out[label] = "—" if v is None else v
    return out


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 主榜（打分榜）
def main_board(cands: list[dict], cfg: dict, weights: list[float] | None = None) -> list[dict]:
    """唯一权威名次来源。直接复用 skill 的 score_rows，不重写打分逻辑。"""
    if LB is None:
        raise RuntimeError("leaderboard.py 未找到，无法打分")
    rows = LB.score_rows(cands, cfg, weights)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


# ---------------------------------------------------------------- 价位段子榜
def price_of(c: dict) -> float | None:
    if isinstance(c.get("trim"), dict):
        p = _num(c["trim"].get("price"))
        if p:
            return p
    return _num(c.get("price_wan"))


def assign_bands(cands: list[dict], policy: dict) -> dict[str, list[str]]:
    """把车型分到价位段。返回 {段名: [model,...]}。"""
    bands = policy["price_bands"]
    grouped: dict[str, list[str]] = {}
    if bands.get("mode") == "quantile":
        prices = sorted((price_of(c), c["model"]) for c in cands if price_of(c) is not None)
        n = len(prices)
        breaks = bands.get("quantile_breaks", [0.34, 0.67])
        labels = bands.get("quantile_labels", [f"段{i+1}" for i in range(len(breaks) + 1)])
        for i, (_, m) in enumerate(prices):
            frac = i / max(n - 1, 1)
            idx = sum(frac > b for b in breaks)
            grouped.setdefault(labels[idx], []).append(m)
    else:
        bounds = bands.get("boundaries", [])
        labels = bands.get("labels", [])
        names = labels if len(labels) == len(bounds) + 1 else \
            [f"段{i+1}" for i in range(len(bounds) + 1)]
        # 预置键，保证子榜按价位从低到高展示，不受候选顺序影响
        grouped = {name: [] for name in names}
        for c in cands:
            p = price_of(c)
            if p is None:
                continue
            idx = sum(p > b for b in bounds)
            grouped.setdefault(names[idx], []).append(c["model"])
    return grouped


def band_subboards(rows: list[dict], cands: list[dict], policy: dict) -> list[dict]:
    """价位段子榜：按主榜分数排序的分组视图，分数来自主榜（不重算）。"""
    by_model = {c["model"]: c for c in cands}
    score_by_model = {r["model"]: r["total"] for r in rows}
    grouped = assign_bands(cands, policy)
    out = []
    for label, models in grouped.items():
        ranked = sorted(models, key=lambda m: -score_by_model.get(m, -1e9))
        out.append({
            "band": label,
            "items": [{"model": m, "score": score_by_model.get(m),
                       "price": price_of(by_model[m])} for m in ranked],
        })
    return out


# ---------------------------------------------------------------- 分段汇总总榜
def segmented_summary(rows: list[dict], cands: list[dict], policy: dict,
                      per_band: int = 5) -> dict:
    """分段汇总总榜：每个价位段取分数前 per_band 名，汇总成一张总榜。

    - 回答「排行榜没有价位段就分段、每段取前几、汇总成总榜」。
    - 价值：保证每个价位段都有车进总榜，避免高端车凭产品力垄断前列；
      这是全价位主榜（纯拼综合分）之外的「带分段配额的总榜」视角。
    - 纪律：分数全部来自主榜 rows，不重新打分、不改权重。
    """
    by_model = {c["model"]: c for c in cands}
    score_by_model = {r["model"]: r["total"] for r in rows}
    grouped = assign_bands(cands, policy)
    summary: list[dict] = []
    bands_meta: list[dict] = []
    for label, models in grouped.items():
        ranked = sorted(models, key=lambda m: -score_by_model.get(m, -1e9))
        top = ranked[:per_band]
        bands_meta.append({"band": label, "n_total": len(models), "n_taken": len(top)})
        for rank_in_band, m in enumerate(top, 1):
            c = by_model[m]
            summary.append({
                "model": m,
                "score": score_by_model.get(m),
                "price": price_of(c),
                "band": label,
                "rank_in_band": rank_in_band,
                "tier": c.get("tier", ""),
            })
    # 汇总后按主榜分数降序（分数是唯一权威来源）
    summary.sort(key=lambda x: -(x["score"] if x["score"] is not None else -1e9))
    for i, item in enumerate(summary, 1):
        item["summary_rank"] = i
    return {"per_band": per_band, "bands": bands_meta, "items": summary}


# ---------------------------------------------------------------- 组装
def build(cands: list[dict], cfg: dict, policy: dict,
          facts: dict[str, dict] | None = None,
          weights: list[float] | None = None,
          per_band: int = 5) -> dict:
    facts = facts or {}
    rows = main_board(cands, cfg, weights)
    by_model = {c["model"]: c for c in cands}
    for r in rows:
        r["fact_ref"] = fact_columns(by_model[r["model"]], facts, policy)
        # 档位优先取候选自带 tier，其次取 set_tiers() 注入的全局表
        r["tier"] = by_model[r["model"]].get("tier") or _tier_of(r["model"])
    bands = band_subboards(rows, cands, policy)
    return {
        "meta": {
            "title": policy.get("title", "统一榜单"),
            "primary": "8维加权打分榜（唯一权威名次）",
            "fact_cols_scored": False,
            "band_reuses_score": True,
            "n": len(rows),
        },
        "main_board": rows,
        "band_subboards": bands,
        "segmented_summary": segmented_summary(rows, cands, policy, per_band),
    }


TIER: dict[str, str] = {}  # build_with_tier 之前可用 set_tiers 注入


def set_tiers(tiers: dict[str, list[str]]):
    TIER.clear()
    for t, members in tiers.items():
        for m in members:
            TIER[m] = t


def _tier_of(model: str) -> str:
    return TIER.get(model, "")


# ---------------------------------------------------------------- CLI
def _load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def cli(argv=None):
    ap = argparse.ArgumentParser(description="统一榜单 v2：打分主榜 + 事实参考列 + 价位段子榜")
    ap.add_argument("--candidates", help="带 scores 的候选池 json")
    ap.add_argument("--config", default=os.path.join(HERE, "categories", "car_ev_1218_quality.json"))
    ap.add_argument("--policy", default=os.path.join(HERE, "categories", "rank_v2_policy.json"))
    ap.add_argument("--facts", help="事实参数 json（按 model 索引），用于参考列")
    ap.add_argument("--per-band", type=int, default=5, help="分段汇总总榜每段取前几名（默认5）")
    ap.add_argument("--out", help="输出目录，写 board_v2_data.json / board_v2.md")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        run_selftest()
        return

    raw = _load(a.candidates)
    cands = raw.get("candidates", raw)
    cfg = _load(a.config)
    policy = _load(a.policy)
    facts = _load(a.facts) if a.facts else {}
    data = build(cands, cfg, policy, facts, per_band=a.per_band)
    md = render_md(data)
    if a.out:
        os.makedirs(a.out, exist_ok=True)
        json.dump(data, open(os.path.join(a.out, "board_v2_data.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        open(os.path.join(a.out, "board_v2.md"), "w", encoding="utf-8").write(md)
        print("[ok] board_v2_data.json / board_v2.md ->", a.out)
    else:
        print(md)


def render_md(data: dict) -> str:
    L = [f"# {data['meta']['title']}", "",
         "> 名次来源：**8维加权打分榜（唯一主榜）**。kWh/万元、电耗等为『参考·不计分』列。", ""]
    L += ["## 全价位打分主榜", "", "| # | 车型 | 加权分 | 档位 | 事实参考列（不计分） |",
          "|---|---|---|---|---|"]
    for r in data["main_board"]:
        ref = " · ".join(f"{k} {v}" for k, v in r["fact_ref"].items() if v != "—")
        L.append(f"| {r['rank']} | {r['model']} | {r['total']:.1f} | {r.get('tier','')} | {ref} |")
    ss = data.get("segmented_summary", {})
    if ss:
        L += ["", f"## 分段汇总总榜（各价位段前 {ss.get('per_band', 5)} 名 → 汇总成总榜）", "",
              "先按价位段各取前几名，再按主榜分数汇成一榜；分数全部来自主榜，不重新打分。", ""]
        L += ["| # | 车型 | 加权分 | 价位段 | 段内名次 |", "|---|---|---|---|---|"]
        for it in ss["items"]:
            L.append(f"| {it['summary_rank']} | {it['model']} | {it['score']:.1f} | {it['band']} | {it['rank_in_band']} |")
    L += ["", "## 价位段子榜（复用主榜分数，不重新打分）", ""]
    for b in data["band_subboards"]:
        L += [f"### {b['band']}", ""]
        for i, it in enumerate(b["items"], 1):
            L.append(f"{i}. {it['model']} — {it['score']:.1f} 分（{it['price']}万）")
        L.append("")
    return "\n".join(L)


def run_selftest():
    assert LB is not None, "leaderboard 不可用"
    cfg = _load(os.path.join(HERE, "categories", "car_ev_1218_quality.json"))
    pol = _load(os.path.join(HERE, "categories", "rank_v2_policy.json"))
    cands = [
        {"model": "廉价车", "cls": "中型SUV", "power": "BEV", "price_wan": 15.0,
         "trend_sales": 5000, "scores": {"powertrain": 7, "safety": 7, "adas": 7, "space": 7,
                                          "value": 9, "control": 7, "cabin": 7, "quality": 7}},
        {"model": "贵价车", "cls": "中型SUV", "power": "BEV", "price_wan": 35.0,
         "trend_sales": 3000, "scores": {"powertrain": 9, "safety": 9, "adas": 9, "space": 9,
                                          "value": 5, "control": 9, "cabin": 9, "quality": 8}},
        {"model": "中价车", "cls": "中型SUV", "power": "BEV", "price_wan": 24.0,
         "trend_sales": 6000, "scores": {"powertrain": 8, "safety": 8, "adas": 8, "space": 8,
                                          "value": 8, "control": 8, "cabin": 8, "quality": 8}},
    ]
    facts = {"廉价车": {"kwh": 80, "consumption": 14, "cltc": 600, "cell_brand": "弗迪", "is_800v": True},
             "贵价车": {"kwh": 100, "consumption": 16, "cltc": 700, "cell_brand": "宁德", "is_800v": False},
             "中价车": {"kwh": 85, "consumption": 13, "cltc": 650, "cell_brand": "宁德", "is_800v": True}}
    data = build(cands, cfg, pol, facts)
    # 1) 主榜按分数降序
    tots = [r["total"] for r in data["main_board"]]
    assert tots == sorted(tots, reverse=True), "主榜未按分数降序"
    # 2) 事实列不计分：改 kwh 不改变名次
    facts2 = json.loads(json.dumps(facts)); facts2["廉价车"]["kwh"] = 999
    d2 = build(cands, cfg, pol, facts2)
    assert [r["model"] for r in d2["main_board"]] == [r["model"] for r in data["main_board"]], \
        "事实列影响了名次（不应计分）"
    # 3) kWh/万元 是参考计算
    cheap_ref = data["main_board"][-1]["fact_ref"]  # 廉价车分最低(性价比高但产品力弱)
    cheap_row = [r for r in data["main_board"] if r["model"] == "廉价车"][0]
    assert cheap_row["fact_ref"]["kWh/万元"] == 5.33, cheap_row["fact_ref"]
    # 4) 价位段子榜复用同一份分数
    all_scores = {r["model"]: r["total"] for r in data["main_board"]}
    for b in data["band_subboards"]:
        for it in b["items"]:
            assert it["score"] == all_scores[it["model"]], "子榜分数与主榜不一致"
    # 5) 贵价车性价比分低但仍进主榜
    assert any(r["model"] == "贵价车" for r in data["main_board"]), "贵价车被硬排除"
    # 6) 分段汇总总榜：每段不超过 per_band、汇总按分数降序、分数来自主榜、各段都有代表
    ss = data["segmented_summary"]
    assert ss["per_band"] == 5
    assert all(b["n_taken"] <= 5 for b in ss["bands"]), "分段取数超过 per_band"
    ss_scores = [it["score"] for it in ss["items"]]
    assert ss_scores == sorted(ss_scores, reverse=True), "分段汇总总榜未按分数降序"
    assert all(it["score"] == all_scores[it["model"]] for it in ss["items"]), "汇总分数与主榜不一致"
    assert len({it["band"] for it in ss["items"]}) == len(ss["bands"]), "有价位段没有代表进总榜"
    print("[selftest OK] 主榜降序 / 事实列不计分 / 参考列计算 / 子榜复用分数 / 贵车不被硬排除 / 分段汇总总榜")


if __name__ == "__main__":
    cli()
