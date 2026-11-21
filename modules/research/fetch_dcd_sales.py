#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""车型级销量（fetch_dcd_sales.py）——按纯电 / 插混 / 增程拆分。

为什么要这条链：
  乘联会公开 API（data.cpcadata.com/api/chartlist）只有 6 个图表类型（charttype 1–6），
  全部是「总体/厂商/大类/级别/国别/新能源」聚合，**没有任何车型级接口**；
  实测 charttype 7–15 全部 404。车型级数据在乘联会月度报告与付费库里，没有公开接口。
  而懂车帝销量榜的公开 JSON 接口给的是**车型级 + 动力类型可拆分 + 支持按月回溯**，
  正好补上 L5 证据层最缺的那块。

接口（公开、免登录）：
  https://www.dongchedi.com/motor/pc/car/rank_data

实测参数含义（用已知插混车型名交叉验证过）：
  month            202610 = 单月；500 = 近半年；1000 = 近一年
  new_energy_type  1=纯电  2=插混  3=增程  ""=全部（含燃油）
  rank_data_type   11=单月销量  1/13=累计口径  2=另一口径（近半年）
  count/offset     分页，单页上限实测 100 可用

输出：
  dcd_sales.csv            车型 × 动力类型 × 销量的长表
  candidates_dcd.json      把销量回填到候选池（dcd_bev / dcd_phev / dcd_all）

用法：
    python3 fetch_dcd_sales.py --month 202610
    python3 fetch_dcd_sales.py --month 202610 --candidates ../v3/candidates_v5_aligned.json
"""

import argparse
import csv
import json
import os
import re
import sys

import requests

URL = "https://www.dongchedi.com/motor/pc/car/rank_data"
HDR = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Referer": "https://www.dongchedi.com/sales",
}
NE_LABEL = {"1": "纯电", "2": "插混", "3": "增程", "": "全部"}
PAGE = 100


def fetch_page(month, ne, offset, count=PAGE, rank_type="11", retries=2):
    p = {"aid": "1839", "app_name": "auto_web_pc", "city_name": "",
         "count": str(count), "offset": str(offset), "month": str(month),
         "new_energy_type": str(ne), "rank_data_type": str(rank_type),
         "brand_id": "", "price": "", "manufacturer": "", "series_type": ""}
    for i in range(retries + 1):
        try:
            r = requests.get(URL, params=p, headers=HDR, timeout=25)
            d = r.json()
            data = d.get("data") or {}
            return data.get("list") or [], data.get("sells_rank_month") or []
        except Exception as e:
            if i == retries:
                print(f"  [warn] ne={ne} offset={offset} 失败：{type(e).__name__}", file=sys.stderr)
                return [], []
    return [], []


def fetch_all(month, ne, hard_limit=1200):
    """翻页取全量。返回 (rows, months_meta)。"""
    out, seen, months = [], set(), []
    offset = 0
    while offset < hard_limit:
        page, mm = fetch_page(month, ne, offset)
        if mm and not months:
            months = mm
        if not page:
            break
        for x in page:
            sid = x.get("series_id")
            if sid in seen:
                continue
            seen.add(sid)
            out.append({
                "series_id": sid,
                "series_name": x.get("series_name") or "",
                "brand": x.get("brand_name") or "",
                "sales": x.get("count"),
                "rank": x.get("rank"),
                "min_price": x.get("min_price"),
                "max_price": x.get("max_price"),
            })
        if len(page) < PAGE:
            break
        offset += PAGE
    return out, months


POWER_TOKENS = ("纯电", "插混", "增程", "油电混合", "PHEV", "phev", "DM-i", "DM", "EM-i",
                "EM", "EV", "ev", "Hi4-T", "iDD", "柴油", "汽油")


def norm(s):
    """归一化 + 剥离动力后缀，用于把「MG 07 纯电」和榜上的「MG 07 EV」对上。

    榜上把纯电/插混拆成独立车系名（MG 07 EV / MG 07 PHEV、海豹06EV / 海豹06DM），
    所以必须先剥后缀再按动力类型取值，否则会被当成两个不同车系。
    """
    t = str(s or "")
    for ch in (" ", "　", "·", "-", "_", "/", "（", "）", "(", ")", "＋", "+"):
        t = t.replace(ch, "")
    # 长前缀必须排在短前缀之前：「东风风行」要在「东风」之前剥，
    # 否则「东风风行星海V6」只剩「风行星海V6」，与榜上「星海V6」对不上。
    for pre in ("东风风行", "长安启源", "东风奕派", "奇瑞风云", "吉利银河",
                "上汽大众", "一汽大众", "上汽通用", "广汽丰田", "东风日产",
                "大众", "长安", "一汽", "上汽", "东风", "广汽", "北汽", "奇瑞",
                "吉利", "比亚迪", "日产", "丰田", "别克"):
        if t.startswith(pre) and len(t) - len(pre) >= 3:
            t = t[len(pre):]
            break
    changed = True
    while changed:
        changed = False
        for tok in POWER_TOKENS:
            if t.endswith(tok) and len(t) - len(tok) >= 3:
                t = t[: -len(tok)]
                changed = True
    return t.lower()


def month_list(end, n):
    """从 end（如 202610）往前取 n 个月，返回升序月份整数。"""
    y, m = int(str(end)[:4]), int(str(end)[4:])
    out = []
    for _ in range(n):
        out.append(y * 100 + m)
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return sorted(out)


def _fmt_m(m):
    return f"{str(m)[:4]}-{str(m)[4:]}"


def _trend_pick(c, series_by_month, med, tag):
    """定趋势值：稳态车用中位数，新车爬坡期取中位与最新月的较大值。

    为什么：中位数是为「抗清库月与换代替换月」设计的，但它对**正在上量**的新车会严重低估。
    实测极狐阿尔法T7 三个月的车系合计是 6 / 2,332 / 8,131（8 月预售、9 月半月、10 月首个完整月），
    中位数取到 2,332，只有实际水平的三分之一。反过来深蓝L06 换代首月 4,487 低于稳态 5,758，
    这时中位数才是对的。所以取两者较大值：上量时用最新月，换代回落时用中位数。
    """
    lc = str((c.get("lifecycle") or {}).get("status") or "").lower()
    if lc != "new_launch" or len(series_by_month) < 2:
        return med, f"{tag}{len(series_by_month)}个月中位数"
    latest_key = max(series_by_month)
    latest = series_by_month[latest_key]
    if latest > med:
        return latest, f"{tag}新车爬坡，取最新月 {latest_key}（中位 {med:,} 已低估）"
    return med, f"{tag}{len(series_by_month)}个月中位数（换代回落，最新月低于稳态）"


def run_trend(a, months):
    """逐月抓纯电/插混，按中位数定 trend_sales。

    为什么不用 month=500：它返回的是**6 个月累计**（星愿 238,532），
    除以 6 得到均值，会被新车首月与清库月同时拉偏。逐月中位数对异常月免疫。
    """
    import statistics
    print(f"逐月抓取 {months[0]} → {months[-1]}（{len(months)} 个月）", file=sys.stderr)
    # 「全部」榜必须一起抓，否则会静默漏掉一整车系——实测：
    # 懂车帝的纯电/插混/增程三个细分榜只收「独立动力车系」（MG 07 EV / 海豹06EV 这种
    # 厂家把动力写进车系名的）；一车多动力的车系（深蓝L06、零跑C10/C11、EZ-6/EZ-60、
    # 启源Q06、小鹏MONA L03/L05、极狐阿尔法T5/T7）在三个细分榜里**一个都不出现**，
    # 只在「全部」榜里存在。只抓 1/2 会导致这 13 款匹配不到、trend 停在旧值。
    series = {}   # base_key -> {"name","brand","sid","bev":{m:n},"phev":{m:n},"all":{m:n}}
    for mth in months:
        for ne in ("1", "2", ""):
            rows, _ = fetch_all(str(mth), ne)
            bucket = {"1": "bev", "2": "phev", "": "all"}[ne]
            for r in rows:
                k = norm(r["series_name"])
                rec = series.setdefault(k, {"name": r["series_name"], "brand": r["brand"],
                                            "sid": r["series_id"],
                                            "bev": {}, "phev": {}, "all": {}})
                rec[bucket][mth] = r["sales"] or 0
            print(f"  {_fmt_m(mth)} {NE_LABEL[ne] or '全部'}: {len(rows)}", file=sys.stderr)

    d = json.load(open(a.candidates, encoding="utf-8"))
    cands = d.get("candidates", d)
    print(f"\n## 半年趋势月销（{len(months)} 个月中位数）\n")
    print("| 候选 | 纯电中位 | 逐月 | 插混中位 | 判定 |")
    print("|---|---|---|---|---|")
    hit = 0
    for c in cands:
        k = norm(c["model"])
        rec = series.get(k)
        if not rec:
            print(f"| {c['model']} | — | 未匹配 | — | 保留原值 |")
            continue
        bev = rec["bev"]
        phev = rec["phev"]
        allv = rec["all"]
        vals = [v for v in bev.values() if v > 0]
        if vals:
            med0 = round(statistics.median(vals))
            med, how = _trend_pick(c, {m: bev[m] for m in bev if bev[m] > 0}, med0, "纯电")
            c["trend_sales"] = med
            c["trend_method"] = how
            c["trend_months"] = {_fmt_m(m): bev[m] for m in sorted(bev)}
            c["power"] = "BEV"
            c["sales_scope"] = "pure_bev"
            flag = "纯电" + ("（新车，仅 %d 个月）" % len(vals) if len(vals) < len(months) else "")
            print(f"| {c['model']} | **{med:,}** | {'/'.join(str(bev[m]) for m in sorted(bev))} | "
                  f"{'—' if not phev else round(statistics.median([v for v in phev.values() if v>0]) or 0)} | {flag} |")
            hit += 1
        elif phev:
            med = round(statistics.median([v for v in phev.values() if v > 0]) or 0)
            c["trend_sales"] = med
            c["power"] = "PHEV"
            c["excluded"] = True
            c["exclude_reason"] = "动力闸门：逐月销量仅出现在插混榜"
            print(f"| {c['model']} | — | — | {med:,} | 移出（PHEV） |")
            hit += 1
        elif any(v > 0 for v in allv.values()):
            # 兜底：只在「全部」榜出现 = 一车多动力、厂家未拆车系名。
            # 合计值**是纯电销量的上界**（含同车系的插混/增程），不能当纯电值用。
            av = [v for v in allv.values() if v > 0]
            med0 = round(statistics.median(av))
            med, how = _trend_pick(c, {m: allv[m] for m in allv if allv[m] > 0},
                                   med0, "车系合计 ")
            c["trend_sales"] = med
            c["trend_method"] = how + "（非纯电）"
            c["trend_months"] = {_fmt_m(m): allv[m] for m in sorted(allv)}
            c["sales_scope"] = "series_total"
            c["sales_upper_bound"] = med
            c["sales_note"] = ("懂车帝三个动力细分榜均未收录该车系（一车多动力、未拆车系名），"
                               "此处为车系合计，纯电实际 ≤ 该值")
            print(f"| {c['model']} | — | — | — | 车系合计 **{med:,}（上限）** |")
            hit += 1
        else:
            print(f"| {c['model']} | — | 无销量 | — | 保留原值 |")
    out = os.path.join(os.path.dirname(a.candidates), "candidates_trend.json")
    d["trend_report"] = {"months": [_fmt_m(m) for m in months], "method": "median",
                         "matched": hit,
                         "source": "懂车帝销量榜逐月 rank_data（new_energy_type=1/2 + 全部兜底）",
                         "scope_legend": {"pure_bev": "纯电榜直接命中",
                                          "series_total": "三个动力细分榜均未收录，取车系合计（纯电上界）",
                                          "phev": "仅插混榜命中，已移出"}}
    json.dump(d, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n命中 {hit}/{len(cands)}；写入 {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default="202610", help="202610 / 500(近半年) / 1000(近一年)")
    ap.add_argument("--candidates", help="候选池 json，用于回填销量")
    ap.add_argument("--out", default="dcd_sales.csv")
    ap.add_argument("--skip-all", action="store_true", help="不抓「全部」口径（省一半请求）")
    ap.add_argument("--trend", type=int, metavar="N",
                    help="取最近 N 个月逐月抓取，算中位数作为 trend_sales（新车首月/清库月不误杀）")
    a = ap.parse_args()

    # ---- 趋势模式：逐月抓，算中位数 ----
    if a.trend:
        return run_trend(a, [str(m) for m in month_list(a.month, a.trend)])

    types = ["1", "2", "3"] + ([] if a.skip_all else [""])
    data, months = {}, []
    for ne in types:
        rows, mm = fetch_all(a.month, ne)
        if mm and not months:
            months = mm
        data[ne] = rows
        print(f"  {NE_LABEL[ne]:3s}: {len(rows)} 个车系", file=sys.stderr)

    if months:
        label = next((m["text"] for m in months if str(m["month"]) == str(a.month)), a.month)
    else:
        label = a.month

    # 长表
    with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["月份", "动力类型", "排名", "车系ID", "车系", "品牌", "销量", "起价", "顶价"])
        for ne in types:
            for r in data[ne]:
                w.writerow([label, NE_LABEL[ne], r["rank"], r["series_id"],
                            r["series_name"], r["brand"], r["sales"],
                            r["min_price"], r["max_price"]])
    print(f"[ok] {a.out}（{label}）", file=sys.stderr)

    # 汇总报告
    print(f"\n## 车型级销量（{label}，懂车帝销量榜）\n")
    for ne in ("1", "2", "3"):
        rows = data.get(ne) or []
        if not rows:
            continue
        rows = sorted(rows, key=lambda x: -(x["sales"] or 0))
        print(f"### {NE_LABEL[ne]} TOP 5")
        print("| # | 车型 | 品牌 | 销量 | 指导价 |")
        print("|---|---|---|---|---|")
        for r in rows[:5]:
            pr = (f"{r['min_price']}–{r['max_price']}万"
                  if r["min_price"] and r["max_price"] else "—")
            print(f"| {r['rank']} | {r['series_name']} | {r['brand']} | {r['sales']:,} | {pr} |")
        print()

    # 回填候选
    if a.candidates and os.path.exists(a.candidates):
        d = json.load(open(a.candidates, encoding="utf-8"))
        cands = d.get("candidates", d)
        idx = {}
        for ne in types:
            for r in data[ne]:
                idx.setdefault(norm(r["series_name"]), {}).setdefault(ne, r)
        hit = 0
        print("### 候选池销量回填")
        print("| 候选 | 纯电 | 插混 | 增程 | 全部 | 匹配到的车系 |")
        print("|---|---|---|---|---|---|")
        for c in cands:
            key = norm(c["model"])
            # 只认基础名完全一致。曾经放宽到「长度差≤2」，结果「风云A9」错配到
            # 「风云A9L」（一款完全不同的车）——宁可漏配，不可错配。
            m = idx.get(key)
            matched = None
            if not m:
                print(f"| {c['model']} | — | — | — | — | 未匹配 |")
                continue
            hit += 1
            g = lambda t: (m.get(t, {}).get("sales") or 0)
            c["dcd_bev_sales"] = g("1") or None
            c["dcd_phev_sales"] = g("2") or None
            c["dcd_erev_sales"] = g("3") or None
            c["dcd_all_sales"] = g("") or None
            c["dcd_series_id"] = (m.get("1") or m.get("2") or m.get("") or {}).get("series_id")
            c["dcd_month"] = label
            print(f"| {c['model']} | {g('1') or '—'} | {g('2') or '—'} | {g('3') or '—'} | "
                  f"{g('') or '—'} | {matched or c['model']} |")
        out_json = os.path.join(os.path.dirname(a.candidates), "candidates_dcd.json")
        d["dcd_sales_report"] = {"month": label, "matched": hit,
                                 "source": "懂车帝销量榜 rank_data（车型级，按动力拆分）"}
        json.dump(d, open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\n命中 {hit}/{len(cands)} 款；已写入 {out_json}")


if __name__ == "__main__":
    main()
