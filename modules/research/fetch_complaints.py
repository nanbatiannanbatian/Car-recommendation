#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""车质网投诉抓取（fetch_complaints.py）——L2 证据层的自动化。

来源与致谢：
  端点与解析逻辑借用自开源项目 car_crawler（MIT）的 12365auto 爬虫
  （spiders/reverso_context.py 的 ReversoContextSpider3），按本项目架构改造：
  feapder+MySQL → requests+lxml+JSONL，无 Chrome/MySQL/Redis 依赖。
  原仓库的 aqsiqauto.com（中国汽车质量网）数据源已实测下线（http/https 均超时），
  只保留车质网（12365auto.com）这一条活的数据源。

设计决定（为什么不是全量爬）：
  车质网 zlts 列表按时间倒序，每页 30 条。全站每天约几百条，抓全站没有意义——
  我们只关心候选池 39 款车的相对投诉密度。所以：
  · 每次抓最新 N 页（默认 60 页 ≈ 近 2-3 天），按**投诉编号去重**追加进
    runs/_complaints/complaints.jsonl——月积月累形成时间序列，回测有据可查；
  · 聚合窗口用「缓存内最近 90 天」，口径写死在代码里；
  · 匹配候选池用车名归一（filings.find_in），匹配不上的如实报覆盖率，不猜。

用法：
    python3 fetch_complaints.py                      # 增量抓 60 页 + 聚合
    python3 fetch_complaints.py --pages 200          # 首次建库多抓些
    python3 fetch_complaints.py --json ... --md ...  # 输出位置
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import sys
import time

import requests
# v10.62：lxml 只在联网抓取 parse_page 用，聚合(aggregate)不需要。
# 顶层 import 会让"离线聚合缓存"因缺 lxml 直接崩——抓取依赖不该阻断纯本地分析。

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)
from modules.research import filings as F

BASE = "https://www.12365auto.com/zlts/0-0-0-0-0-0_0-0-0-0-0-0-0-{page}.shtml"
CACHE = os.path.join(HERE, "runs", "_complaints", "complaints.jsonl")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
WINDOW_DAYS = 90

# 高频问题关键词（从描述里抽典型问题；车质网列表页没有单独的 tags 列）
TAGS = ["异响", "漏油", "顿挫", "死机", "黑屏", "卡顿", "无法启动", "抖动",
        "续航", "充电", "无法充电", "刹车", "制动", "转向", "变速箱",
        "电池", "空调", "车门", "漆面", "爆漆", "减震", "悬挂", "车机",
        "灵敏度", "失灵", "失效", "故障", "报警", "异味", "生锈"]


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.headers.update({"User-Agent": UA,
                      "Referer": "https://www.12365auto.com/"})
    return s


def parse_page(page: int) -> list[dict]:
    from lxml import html as LH  # 仅联网抓取需要；离线聚合不触发
    s = _session()
    r = s.get(BASE.format(page=page), timeout=30)
    if r.status_code != 200:
        return []
    doc = LH.fromstring(r.content)
    rows = doc.xpath("//*[@id='content']/div[4]/div[2]/table/tr")
    out = []
    for tr in rows[1:]:
        tds = [t.strip() for t in tr.xpath("./td//text()")]
        # 结构：编号 / 品牌 / 车系 / 车型 / 问题简述 / 日期 / 状态
        if len(tds) < 7:
            continue
        no, brand, series, car_name, desc, date, status = tds[:7]
        if not re.fullmatch(r"\d{5,8}", no):
            continue
        out.append({
            "no": no, "brand": brand, "series": series, "car_name": car_name,
            "desc": desc, "date": date, "status": status,
            "src": "12365auto",
        })
    return out


def fetch_incremental(pages: int, cache: str = CACHE) -> tuple[int, int]:
    """抓最新 pages 页，按编号去重追加进 JSONL。返回 (新增, 读取总数)。"""
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    seen = set()
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            for line in f:
                try:
                    seen.add(json.loads(line)["no"])
                except Exception:
                    pass
    before = len(seen)
    added = 0
    with open(cache, "a", encoding="utf-8") as f:
        s = _session()
        empty_streak = 0
        for p in range(1, pages + 1):
            try:
                rows = parse_page(p)
            except Exception:
                rows = []
            if not rows:
                empty_streak += 1
                if empty_streak >= 3:
                    break          # 连续 3 页空 = 到头了，不硬翻
                time.sleep(0.5)
                continue
            empty_streak = 0
            for r in rows:
                if r["no"] in seen:
                    continue
                seen.add(r["no"])
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                added += 1
            time.sleep(0.4)        # 克制频率，别给对方压力
    return added, before + added


def load_cache(cache: str = CACHE) -> list[dict]:
    if not os.path.exists(cache):
        return []
    out = []
    with open(cache, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def aggregate(rows: list[dict], cands: list[dict]) -> dict:
    """窗口内按候选池聚合。返回 {model: {count, per_10k, dates, tags, samples}}。"""
    pool = {F.norm_name(c.get("model", "")): c.get("model") for c in cands}
    dcut = (dt.date.today() - dt.timedelta(days=WINDOW_DAYS)).isoformat()
    buckets = collections.defaultdict(lambda: {"count": 0, "dates": [],
                                               "tags": collections.Counter(),
                                               "samples": []})
    unmatched = collections.Counter()
    for r in rows:
        if (r.get("date") or "") < dcut:
            continue
        # 车系名优先，其次品牌+车型
        key = F.find_in(pool, r.get("series") or "")
        if not key and r.get("car_name"):
            key = F.find_in(pool, r["car_name"])
        if not key:
            unmatched[r.get("series") or "?"] += 1
            continue
        model = pool[key]
        b = buckets[model]
        b["count"] += 1
        b["dates"].append(r.get("date"))
        for t in TAGS:
            if t in (r.get("desc") or ""):
                b["tags"][t] += 1
        if len(b["samples"]) < 3:
            b["samples"].append({"date": r.get("date"), "desc": r.get("desc")})
    sales = {c.get("model"): float(c.get("trend_sales") or 0) for c in cands}
    out = {}
    for m, b in buckets.items():
        per10k = (b["count"] / sales[m] * 10000) if sales.get(m) else None
        out[m] = {"count": b["count"],
                  "per_10k": round(per10k, 1) if per10k is not None else None,
                  "sales_basis": sales.get(m),
                  "first": min(b["dates"]) if b["dates"] else None,
                  "last": max(b["dates"]) if b["dates"] else None,
                  # v10.74.1：top_tags 只留 4 条够展示，但严重度分级需要完整标签谱
                  # （安全类/功能失效/耐久劣化/体验各有不同权重），故额外保留全量。
                  "top_tags": b["tags"].most_common(4),
                  "tags_all": b["tags"].most_common(),
                  "samples": b["samples"]}
    dates = sorted(r["date"] for r in rows if r.get("date"))
    return {"window_days": WINDOW_DAYS, "rows_in_window":
            sum(1 for r in rows if (r.get("date") or "") >= dcut),
            "cache_cover": [dates[0], dates[-1]] if dates else ["?", "?"],
            "unmatched_top": unmatched.most_common(10), "models": out}


def render(agg: dict, cands: list[dict]) -> str:
    cover = agg.get("cache_cover") or ["?", "?"]
    L_ = [f"# 车质网投诉反馈（近 {agg['window_days']} 天窗口）", "",
          f"> 缓存窗口内投诉 {agg['rows_in_window']} 条，缓存实际覆盖 "
          f"{cover[0]} → {cover[1]}（随月度任务累积）；来源 12365auto.com"
          "（爬取逻辑借用 car_crawler，MIT）。", "",
          "⚠️ **投诉量没有分母就没有意义**：只有结合销量（投诉/万台）才可比；"
          "样本太少的车型（<5 条）不参与排名。本表只做参考证据，"
          "**不改变三档判定**。", "",
          "| 车型 | 近窗口投诉 | 投诉密度(/万台) | 高频问题 | 最新投诉 |",
          "|---|---|---|---|---|"]
    models = {k: v for k, v in agg["models"].items()
              if v["count"] >= 5}
    for m, v in sorted(models.items(), key=lambda kv: -(kv[1]["per_10k"] or 0)):
        tags = "、".join(f"{t}×{n}" for t, n in v["top_tags"]) or "—"
        L_.append(f"| {m} | {v['count']} | "
                  f"{v['per_10k'] if v['per_10k'] is not None else '销量缺'} | "
                  f"{tags} | {v['last']} |")
    few = {k: v for k, v in agg["models"].items() if v["count"] < 5}
    if few:
        L_ += ["", f"样本不足（<5 条，不参与比较）："
               f"{'、'.join(f'{k}({v["count"]})' for k, v in sorted(few.items()))}"]
    L_ += ["", f"> 候选池匹配覆盖：{len(agg['models'])} 款命中；"
               f"窗口内未匹配投诉 top10：{agg['unmatched_top'][:5] or '无'}。"
               "未匹配多为候选池外车型（艾瑞泽8、海鸥等非四榜口径）。"]
    return "\n".join(L_)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", type=int, default=60)
    ap.add_argument("--json", help="聚合输出 JSON")
    ap.add_argument("--md", help="聚合输出 Markdown")
    ap.add_argument("--cache", default=CACHE)
    ap.add_argument("--pool", default=str(paths.cat("baseline_car_ev.json")))
    ap.add_argument("--skip-fetch", action="store_true",
                    help="只用已有缓存聚合（离线）")
    a = ap.parse_args()

    if not a.skip_fetch:
        added, total = fetch_incremental(a.pages, a.cache)
        print(f"[抓取] 新增 {added} 条，缓存总量 {total}")
    rows = load_cache(a.cache)
    print(f"[缓存] {len(rows)} 条")
    cands = json.load(open(a.pool, encoding="utf-8"))["candidates"]
    agg = aggregate(rows, cands)
    print(f"[聚合] 命中 {len(agg['models'])} 款，"
          f"窗口 {agg['window_days']} 天内 {agg['rows_in_window']} 条")
    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)) or ".", exist_ok=True)
        json.dump(agg, open(a.json, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"[ok] {a.json}")
    md = render(agg, cands)
    if a.md:
        os.makedirs(os.path.dirname(os.path.abspath(a.md)) or ".", exist_ok=True)
        open(a.md, "w", encoding="utf-8").write(md + "\n")
        print(f"[ok] {a.md}")
    else:
        print(md)


if __name__ == "__main__":
    main()
