#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""未上市车型上榜预测（forecast.py）——把备案与新闻变成「提前导购」。

只回答三个问题，且必须都能被下一期真实榜单证伪：
  1. 会不会进这榜的 Top10（概率）
  2. 大概落在第几（名次区间，不是单一名次）
  3. 凭什么这么猜（逐项加/减分与来源）

方法论（为什么不是拍脑袋）：
  · 基准位置不是凭空给的，而是把在售榜当成坐标系：拿「同品牌在榜车型的实际得分」
    做锚点，再用备案参数（续航/电池/尺寸/价格）在同榜内的**分位数**做偏移。
  · 概率用 logistic 把分差映射成概率，方差随证据等级变（备案齐全但无价格 → 更宽）。
  · 名次区间用固定随机种子的蒙特卡洛重排（同一输入必得同一输出，可复现）。
  · 价格是闸门，不是打分项：预估价越界就先扣掉大半概率，而不是先上榜再解释。

三条硬约束（写在这里防止后面自己骗自己）：
  · 未上市车**不进正式榜**，单独分区，避免污染真实名次。
  · 无碰撞成绩、无实测、无口碑 = 证据 C，T1 概率上限锁死在 0.35。
  · 备案参数只证明「存在」，不证明「好」；所以规格分位只占偏移的一部分，
    主体仍是品牌在榜表现——这是有偏的，报告里必须写明。

用法：
    python3 forecast.py --filings runs/_filings/filings_411.json \
        --candidates runs/car-ev-4boards/candidates_trend.json \
        --out runs/_filings/forecast_411.json --md runs/_filings/forecast_411.md
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import sys

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)
from modules.engine import leaderboard as L

CONF_SIGMA = {"curated": 2.2, "auto": 3.6}     # 名次扰动标准差
T1_CAP = 0.35                                  # 无官方碰撞成绩时的 T1 概率上限


# ---------------------------------------------------------------- 工具
def pct_rank(v: float, arr: list[float]) -> float:
    """v 在 arr 中的分位（0..1）；arr 为空给 0.5（无信息）。"""
    arr = [x for x in arr if x is not None]
    if not arr or v is None:
        return 0.5
    less = sum(1 for x in arr if x < v)
    eq = sum(1 for x in arr if x == v)
    return (less + 0.5 * eq) / len(arr)


def logistic(x: float) -> float:
    if x > 30:
        return 1.0
    if x < -30:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def linfit(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """最小二乘 y = a + b·x；样本不足或退化时返回 (中位比价, 0)。"""
    pts = [(x, y) for x, y in zip(xs, ys) if x and y]
    if len(pts) >= 3:
        mx = sum(p[0] for p in pts) / len(pts)
        my = sum(p[1] for p in pts) / len(pts)
        sxx = sum((p[0] - mx) ** 2 for p in pts)
        sxy = sum((p[0] - mx) * (p[1] - my) for p in pts)
        if sxx > 1e-6:
            b = sxy / sxx
            return my - b * mx, b
    if pts:
        ratio = statistics.median([y / x for x, y in pts])
        return 0.0, ratio
    return 0.0, 1.2


def price_mid(c: dict) -> float | None:
    p = c.get("price_guide") or []
    if isinstance(p, (list, tuple)) and len(p) == 2 and p[0] and p[1]:
        return (float(p[0]) + float(p[1])) / 2
    t = c.get("trim") or {}
    return float(t["price"]) if t.get("price") else None


# ---------------------------------------------------------------- 核心
def board_stats(cands: list[dict], cfg: dict) -> dict:
    """把在售榜变成坐标系：分数分布 + 品牌锚点 + 价格-电量关系。"""
    rows = L.score_rows(cands, cfg)
    totals = [r["total"] for r in rows]
    kwhs = [c.get("batt_kwh") or (c.get("trim") or {}).get("batt_kwh") for c in cands]
    kwhs = [k for k in kwhs if k]
    prices = [price_mid(c) for c in cands]
    a, b = linfit(kwhs, [p for p in prices if p])
    brand = {}
    for c in cands:
        r = next((x for x in rows if x["model"] == c.get("model")), None)
        if not r:
            continue
        key = (c.get("brand") or brand_of(c.get("model") or "", c.get("enterprise")))
        w = max(float(c.get("trend_sales") or r["total"]), 1.0)
        d = brand.setdefault(key, {"w": 0.0, "s": 0.0, "n": 0,
                                   "best": 0.0, "models": []})
        d["w"] += w
        d["s"] += w * r["total"]
        d["n"] += 1
        d["best"] = max(d["best"], r["total"])
        d["models"].append(c.get("model"))
    for d in brand.values():
        d["avg"] = d["s"] / d["w"] if d["w"] else None
    n = len(totals)
    median = statistics.median(totals) if totals else 78.0
    # 收缩：样本 <3 款的「品牌均分」不可信（可能只是它唯一一款低价车的分），
    # 向全榜中位收缩——n 款样本按 n/3 权重混入中位。
    for d in brand.values():
        if d.get("avg") is not None and d["n"] < 3:
            w = d["n"] / 3.0
            d["avg"] = w * d["avg"] + (1 - w) * median
            d["shrunk"] = True
    return {
        "rows": rows, "n": n,
        "median": median,
        "p25": sorted(totals)[max(0, n // 4 - 1)] if totals else 76.0,
        "p75": sorted(totals)[min(n - 1, 3 * n // 4)] if totals else 82.0,
        "max": max(totals) if totals else 86.0,
        "t10": sorted(totals, reverse=True)[9] if n >= 10 else (
            min(totals) if totals else 74.0),
        "t1": sorted(totals, reverse=True)[2] if n >= 3 else (
            max(totals) if totals else 84.0),
        "ranges": [c.get("range_km") or c.get("_range") or 0 for c in cands],
        "kwhs": kwhs, "prices": prices,
        "lens": [c.get("len_mm") for c in cands],
        "kerbs": [c.get("kerb_kg") for c in cands],
        "fit": (a, b), "brand": brand,
    }


BRANDS = [
    ("理想", ["理想"]),
    ("蔚来", ["蔚来", "乐道", "萤火虫"]),
    ("小鹏", ["小鹏", "MONA"]),
    ("零跑", ["零跑"]),
    ("小米", ["小米"]),
    ("比亚迪", ["比亚迪", "海豹", "海狮", "汉", "秦", "宋", "元", "海豚", "海鸥", "方程豹", "腾势", "仰望"]),
    ("吉利系", ["吉利", "银河", "领克", "极氪", "星愿", "星舰"]),
    ("长安系", ["深蓝", "长安", "启源", "阿维塔"]),
    ("奇瑞系", ["奇瑞", "捷途", "星途", "iCAR", "风云"]),
    ("上汽系", ["上汽", "MG", "荣威", "智己", "飞凡", "大通", "五菱", "宝骏",
                "大众", "ID."]),
    ("广汽系", ["广汽", "AION", "埃安", "传祺", "昊铂"]),
    ("东风系", ["东风", "奕派", "岚图", "风神", "纳米"]),
    ("北汽系", ["北汽", "极狐", "享界", "蓝谷", "麦格纳"]),
    ("长城", ["长城", "欧拉", "ORA", "哈弗", "魏牌", "坦克"]),
    ("合资", ["丰田", "本田", "日产", "别克", "宝马", "奥迪", "奔驰", "马自达",
              "现代", "起亚", "沃尔沃", "福特", "雪佛兰", "标致", "雪铁龙", "特斯拉"]),
]


def brand_of(model: str, enterprise: str | None = None) -> str:
    """先按生产企业判品牌，再按车型名兜底。生产企业比车型名可靠
    （如「V8X」「7X」这类代号名里没有品牌字样）。"""
    ent = enterprise or ""
    for name, keys in BRANDS:
        for k in keys:
            if k in ent:
                return name
    m = (model or "").upper()
    for name, keys in BRANDS:
        for k in keys:
            if k.upper() in m:
                return name
    return enterprise[:4] if enterprise else (model[:2] if model else "未知")


def forecast_one(item: dict, board: dict, st: dict, cfg: dict,
                 n_boot: int = 400) -> dict:
    """单款车的预测：位置 + 概率 + 理由链。"""
    band = ((board.get("gates") or {}).get("price_band") or [0, 999])[1]
    band_lo = ((board.get("gates") or {}).get("price_band") or [0, 999])[0]
    cond = item.get("notes_status") == "curated"
    gd = item.get("cls_guess") or {}
    kwh, rng, kerb = item.get("batt_kwh"), item.get("range_km"), item.get("kerb_kg")
    ln = item.get("len_m") or gd.get("len_m")

    # ---- 1 价格：备案不报价格，这是最大的不确定项
    pe = item.get("price_expect")
    kmax = max(st["kwhs"]) if st["kwhs"] else None
    kerb_max = max([x for x in (st.get("kerbs") or []) if x], default=None)
    too_big = bool((kwh and kmax and kwh > kmax * 1.15)
                   or (kerb and kerb_max and kerb > kerb_max * 1.12))
    if isinstance(pe, list) and len(pe) == 2:
        p_lo, p_hi, p_src = float(pe[0]), float(pe[1]), "新闻/官方预售"
    elif kwh and not too_big:
        a, b = st["fit"]
        mid = a + b * (kwh or 0)
        if mid <= 0:
            mid = (b or 1.2) * kwh
        p_lo, p_hi, p_src = mid * 0.93, mid * 1.07, "电量回归"
    else:
        p_lo = p_hi = None
        p_src = ("参数超出该价格带上限（电量/整备远超在售），回归不适用"
                 if too_big else "无")
    p_mid = (p_lo + p_hi) / 2 if p_lo else None
    if p_hi is None:
        price_state = "likely_out" if too_big else "unknown"
    elif p_hi <= band and p_lo >= band_lo:
        price_state = "in"
    elif p_lo <= band * 1.06 and p_mid <= band:
        price_state = "edge"          # 入门的低配能压进带内
    else:
        price_state = "out"

    # ---- 2 品牌锚点（在榜得分 + 历史画像两级）
    bkey = brand_of(item.get("name") or "", item.get("enterprise"))
    bk = st["brand"].get(bkey)
    hist = (st.get("brand_hist") or {}).get(bkey)
    if bk and bk.get("avg"):
        brand_base = bk["avg"]
        brand_src = (f"{bkey} 在榜 {bk['n']} 款加权均分"
                     + ("（样本<3，已向全榜中位收缩）" if bk.get("shrunk") else ""))
        hit_n = bk["n"]
    else:
        brand_base, brand_src, hit_n = st["median"], f"无 {bkey} 在榜样本，用全榜中位", 0
    # 历史画像（fact_brand_stats）：命中率+体量折成 0-100 的历史锚，
    # 与在榜锚按 35% 混合——在榜分反映「当下产品力」，历史分反映「这家厂
    # 过去报备的车有多少真的卖起来了」。只有历史样本 ≥2 款才参与。
    if hist and (hist.get("filed") or 0) >= 2:
        evo = 55 + 30 * (hist.get("hit_rate") or 0) \
              + 15 * min((hist.get("share") or 0) / 0.15, 1.0)
        brand_base = 0.65 * brand_base + 0.35 * evo
        brand_src += (f"；历史画像（备案{hist['filed']}款，"
                      f"6月内月销≥300 命中率 {hist['hit_rate']*100:.0f}%，"
                      f"近6月纯电份额 {hist['share']*100:.1f}%）按 35% 混入")

    # ---- 3 规格分位（相对本榜在售）
    pr = pct_rank(rng, st["ranges"]) if rng else 0.5
    pb = pct_rank(kwh, st["kwhs"]) if kwh else 0.5
    if ln:
        ps = pct_rank(ln * 1000, [x for x in (st.get("lens") or []) if x])
    elif kerb:
        ps = pct_rank(kerb, [x for x in (st.get("kerbs") or []) if x])
    else:
        ps = 0.5
    # 性价比：每万元买到的电量（越高越划算），再取分位。
    # 带外车不计——拿 26 万的车在 ≤18 万榜里算性价比，只会产出 "-3 分" 的噪声。
    if price_state in ("out", "likely_out"):
        pv, pv_note = 0.5, "价格带外，性价比项不计"
        pv_c = 0.0
    elif p_mid and kwh:
        v_kwh = kwh / p_mid
        v_arr = [k / p for k, p in zip(st["kwhs"], st["prices"]) if k and p]
        pv = pct_rank(v_kwh, v_arr)
        pv_c = round(6 * (pv - 0.5), 1)
        pv_note = f"{v_kwh:.2f} kWh/万元，分位 {pv*100:.0f}%"
    else:
        pv, pv_c, pv_note = 0.5, 0.0, "价格未定，性价比项取中性"

    contrib = [
        ("品牌在榜表现", round(brand_base, 1), brand_src),
        ("续航分位", round(6 * (pr - 0.5), 1), f"{rng:.0f} km，榜内分位 {pr*100:.0f}%"),
        ("电量分位", round(4 * (pb - 0.5), 1), f"{kwh:.1f} kWh，分位 {pb*100:.0f}%"),
        ("尺寸分位", round(3 * (ps - 0.5), 1),
         (f"车长 {ln:.2f} m" if ln else (f"整备 {kerb:.0f} kg" if kerb else "无尺寸")) +
         f"，分位 {ps*100:.0f}%"),
        ("性价比分位", pv_c, pv_note),
    ]
    tech = (item.get("tech") or "")
    tech_bonus = 0.0
    for kw, v in (("800V", 1.5), ("激光雷达", 1.0), ("城市", 1.0), ("换电", 0.5)):
        if kw in tech:
            tech_bonus += v
    tech_bonus = min(tech_bonus, 3.0)
    if tech_bonus:
        contrib.append(("技术点", round(tech_bonus, 1), tech))
    if not cond:
        contrib.append(("证据不足", -2.0, "只有备案参数，无尺寸/价格/上市窗口"))

    proxy = sum(c[1] for c in contrib)

    # ---- 4 概率
    z10 = (proxy - st["t10"]) / 3.2
    p10 = logistic(z10)
    p10 *= {"in": 1.0, "edge": 0.75, "out": 0.22, "unknown": 0.55,
            "likely_out": 0.12}[price_state]
    z1 = (proxy - st["t1"]) / 3.0
    # 带外车不谈 T1——它连榜都进不了，「T1 概率 35%」只会误导
    p1 = None if price_state in ("out", "likely_out") else min(logistic(z1), T1_CAP)

    # ---- 5 名次区间：蒙特卡洛重排（价格带外的车不排名次——排名没有意义）
    if price_state in ("out", "likely_out"):
        rk = None
    else:
        sigma = CONF_SIGMA["curated" if cond else "auto"]
        rnd = random.Random(abs(hash((item.get("name") or "") +
                                     board.get("label", ""))) % (2 ** 31))
        totals = [r["total"] for r in st["rows"]]
        ranks = []
        for _ in range(n_boot):
            p_ = proxy + rnd.gauss(0, sigma)
            ranks.append(1 + sum(1 for t in totals if t > p_))
        ranks.sort()
        rk = {"p10": ranks[int(0.10 * len(ranks))],
              "p50": ranks[len(ranks) // 2],
              "p90": ranks[int(0.90 * len(ranks)) - 1]}
    in_top10 = bool(rk and sum(1 for t in [r["total"] for r in st["rows"]]
                               if t > proxy) < 10)

    return {
        "name": item.get("name"), "enterprise": item.get("enterprise"),
        "board": board.get("label"), "cls": gd.get("cls"), "body": gd.get("body"),
        "cls_how": gd.get("how"), "len_m": ln,
        "range_km": rng, "batt_kwh": kwh, "kerb_kg": kerb,
        "price_expect": [p_lo, p_hi] if p_lo else None, "price_src": p_src,
        "launch": (item.get("notes") or {}).get("launch"),
        "price_state": price_state, "band": band,
        "proxy": round(proxy, 1), "brand_base": round(brand_base, 1),
        "brand_models": (bk or {}).get("models", [])[:3],
        "p_top10": round(p10, 2), "p_t1": (round(p1, 2) if p1 is not None else None),
        "rank": rk, "in_top10_point": in_top10,
        "evidence": "B" if cond else "C",
        "contrib": [(k, v, note) for k, v, note in contrib],
        "notes_status": item.get("notes_status"),
        "batch": item.get("batch"),
    }


def _lens(st):
    return [x for x in (st.get("lens") or []) if x]


def _kerbs(st):
    return [x for x in (st.get("kerbs") or []) if x]


# ---------------------------------------------------------------- 报告
def render_board(label: str, items: list[dict], n_sold: int) -> list[str]:
    if not items:
        return [f"## 未上市预测 · {label}", "", "> 无报备待上市车型。", ""]
    items = sorted(items, key=lambda r: (-r["p_top10"],
                                         (r.get("rank") or {}).get("p50") or 999))
    L_ = [f"## 未上市预测 · {label}", "",
          f"> 对照榜内 {n_sold} 款在售车。预测**不进正式名次**，"
          f"上市首月用真实榜回测。", "",
          "| 车型 | 上市窗口 | 预计名次(10–90%) | 进Top10概率 | T1概率 | 预估价 | 依据 |",
          "|---|---|---|---|---|---|---|"]
    for r in items:
        pe = r["price_expect"]
        ptxt = (f"{pe[0]:.1f}–{pe[1]:.1f} 万" if pe else "未定")
        flag = {"out": " ⚠️超带", "edge": " △压线", "unknown": " ?",
                "likely_out": " ⚠️大概率超带"}.get(r["price_state"], "")
        rk = r.get("rank")
        rk_txt = (f"{rk['p50']}（{rk['p10']}–{rk['p90']}）" if rk
                  else "—（价格带外）")
        p1 = r.get("p_t1")
        p1_txt = "—" if p1 is None else f"{p1*100:.0f}%"
        L_.append(
            f"| **{r['name']}** | {r.get('launch') or '未公布'} | "
            f"{rk_txt} | "
            f"{r['p_top10']*100:.0f}% | {p1_txt} | {ptxt}{flag} | "
            f"代理分 {r['proxy']:.1f}｜证据 {r['evidence']} |")
    L_.append("")
    L_.append("**逐项依据**")
    L_.append("")
    for r in items:
        parts = [f"{k} {v:+.1f}（{note}）" for k, v, note in r["contrib"]]
        L_.append(f"- **{r['name']}**：{'；'.join(parts)}")
    L_.append("")
    L_.append("> 读法：代理分与在售榜同一坐标系（0–100）。名次区间来自代理商 ±σ 的"
              "蒙特卡洛重排；σ 由证据等级决定（有新闻价格/尺寸 2.2，仅备案 3.6）。"
              "价格是闸门项，超带直接砍掉 78% 概率，不再讨论产品力。")
    L_.append("")
    return L_


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--filings", required=True)
    ap.add_argument("--candidates", required=True, help="在售候选池（含 scores）")
    ap.add_argument("--category", default="car_ev")
    ap.add_argument("--out", help="输出 JSON")
    ap.add_argument("--md", help="输出 Markdown")
    ap.add_argument("--specs", default=os.path.join(HERE, "runs", "_filings",
                                                    "specs.json"),
                    help="备案参数库（把在售车的续航/电量/车长补齐，才有分位可比）")
    ap.add_argument("--n-boot", type=int, default=400)
    a = ap.parse_args()

    cfg = L.load_category(a.category)
    cands = json.load(open(a.candidates, encoding="utf-8"))
    cands = cands.get("candidates", cands)

    cover = 0
    if os.path.exists(a.specs):
        from modules.research import filings as F
        lib = json.load(open(a.specs, encoding="utf-8")).get("models") or {}
        for c in cands:
            k = F.find_in(lib, c.get("model") or "")
            if not k:
                continue
            g = lib[k]
            cover += 1
            c.setdefault("batt_kwh", g.get("batt_kwh"))
            c["batt_kwh"] = c.get("batt_kwh") or g.get("batt_kwh")
            c["range_km"] = c.get("range_km") or g.get("range_km")
            c["kerb_kg"] = c.get("kerb_kg") or g.get("kerb_kg")
            if g.get("len_m"):
                c["len_mm"] = c.get("len_mm") or int(round(g["len_m"] * 1000))
        print(f"[备案参数] 在售池补齐 {cover}/{len(cands)} 款"
              f"（缺的用中性分位 0.5，不猜）")
    fl = json.load(open(a.filings, encoding="utf-8"))
    items = fl.get("items") or []

    # 品牌历史画像（backfill.py --brand-stats 产物，可选）
    brand_hist = {}
    try:
        import sqlite3
        dbp = os.environ.get("CAR_EV_DB") or os.path.join(
            os.path.dirname(a.candidates), "car_ev.db")
        if os.path.exists(dbp):
            con = sqlite3.connect(dbp)
            con.row_factory = sqlite3.Row
            asof = con.execute("SELECT MAX(asof) FROM fact_brand_stats"
                               ).fetchone()[0]
            if asof:
                for r in con.execute("SELECT * FROM fact_brand_stats WHERE asof=?",
                                     [asof]):
                    dcd = r["brand"]
                    # DCD 品牌名 → 品牌组（与 brand_of 同一归类），双键都存，
                    # 查找时先组名后原名
                    brand_hist[brand_of(dcd)] = dict(r)
                    brand_hist.setdefault(dcd, dict(r))
            con.close()
            if brand_hist:
                print(f"[品牌历史] 读到 {len(brand_hist)} 个品牌画像（asof {asof}）")
    except Exception as e:
        print(f"[品牌历史] 不可用（{type(e).__name__}），仅用在榜锚点")

    results, skipped = [], []
    for board in L.board_items(cfg):
        sub = L.filter_board(cands, board)
        if not sub:
            continue
        st = board_stats(sub, cfg)
        st["brand_hist"] = brand_hist
        target = board.get("match", {}).get("cls", "")
        for it in items:
            gd = it.get("cls_guess") or {}
            if it.get("status") == "on_sale":
                continue                      # 在售车的改款备案，不是新车
            if gd.get("cls") != target:
                continue
            if (it.get("body") or gd.get("body")) == "MPV":
                skipped.append({**it, "why": "MPV，非 SUV 榜口径"})
                continue
            r = forecast_one(it, board, st, cfg, a.n_boot)
            results.append(r)

    # 归并：同一款只出现在一个榜
    by_board = {}
    for r in results:
        by_board.setdefault(r["board"], []).append(r)

    # 说明「为什么某些已备案的车没出现在任何预测榜里」——口径不符要写清楚
    used = {r["name"] for r in results}
    for it in items:
        if it.get("name") in used:
            continue
        if any(s.get("name") == it.get("name") for s in skipped):
            continue
        gd = it.get("cls_guess") or {}
        if it.get("status") == "on_sale":
            why = "已在候选池（本批备案为改款/新增配置，非新车）"
        else:
            why = (f"级别为 {gd.get('cls') or '未知'}"
                   f"（{'新闻/官方口径' if gd.get('how') == 'news' else '备案推定'}）"
                   f"，不在四榜口径内")
        skipped.append({**it, "why": why})

    out = {"batch": fl.get("batch"), "date": fl.get("date"),
           "generated": fl.get("generated"),
           "boards": {k: sorted(v, key=lambda x: -x["p_top10"])
                      for k, v in by_board.items()},
           "skipped": [{"name": s.get("name"), "why": s.get("why")} for s in skipped]}
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
        json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"[ok] {a.out}")

    L_ = [f"# 未上市车型上榜预测 · 第{fl.get('batch')}批备案", "",
          f"> 备案公告：{fl.get('date')}｜在售对照：`{os.path.basename(a.candidates)}`", "",
          "> 口径：只预测「已报备但未在榜」的纯电乘用车。预测区独立于正式榜，"
          "不参与名次；每款都给出可被下期榜单证伪的概率与名次区间。", ""]
    for board in L.board_items(cfg):
        lab = board.get("label")
        n_sold = len(L.filter_board(cands, board))
        L_ += render_board(lab, by_board.get(lab, []), n_sold)
    if skipped:
        L_ += ["## 已排除（口径不符）", ""]
        for s in skipped:
            L_.append(f"- {s.get('name')}：{s.get('why')}")
        L_.append("")
    if a.md:
        open(a.md, "w", encoding="utf-8").write("\n".join(L_) + "\n")
        print(f"[ok] {a.md}")
    for k, v in out["boards"].items():
        print(f"  {k}: {len(v)} 款待上市")
    if not out["boards"]:
        print("  （本批没有落在四个价格带内的待上市车型）")


if __name__ == "__main__":
    main()
