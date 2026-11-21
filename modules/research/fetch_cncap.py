#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C-NCAP 官网自动抓取（fetch_cncap.py）——把「半年人工汇总」变自动。

推翻旧结论的实测（2026-11-14）：
  旧结论说官网「客户端渲染且无公开 API，无法批量抓取」。实测发现官网首页的
  「最新测评结果」swiper 是**服务端渲染**：每张卡片带车型名、年份、综合得分率、
  乘员保护/VRU保护/主动安全三个分项，以及详情页链接（/evaluation/{id}）。
  requests+lxml 直接可解析，零反爬。

本模块只做三件事：
  1. 解析首页 swiper → 最新批次 C-NCAP 结果；
  2. 与 cncap_scores.json 对照（新增/更新/冲突三分）；
  3. --apply 时合并入库：官网为权威源，冲突时以官网值覆盖，meta 记来源与日期。

边界（如实）：
  · 首页 swiper 只显示**最新一批**结果（当前 17 款）；历史批次仍需人工从
    「测评通告」与汇总表补（存量 57 款不受影响）；
  · 得分率是「综合得分率(%)」，规程版本以官网标注年份为准，与 2024+ 口径一致；
  · 星级不在卡片里，需要详情页——暂不抓（映射只用得分率，够用）。

用法：
    python3 fetch_cncap.py                # 对照预览（不动文件）
    python3 fetch_cncap.py --apply        # 合并进 cncap_scores.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

import requests
from lxml import html as LH

import paths

HERE = str(paths.ROOT)
SCORES_PATH = os.path.join(HERE, "cncap_scores.json")
HOME = "https://www.c-ncap.org.cn/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131 Safari/537.36")


def fetch_latest() -> list[dict]:
    s = requests.Session()
    s.trust_env = False
    s.headers.update({"User-Agent": UA})
    r = s.get(HOME, timeout=25)
    r.raise_for_status()
    doc = LH.fromstring(r.content)
    slides = doc.xpath("//*[contains(@class,'cncap-swiper') and "
                       "contains(@class,'swiper-slide')]")
    rows = []
    for sl in slides:
        nm = [x.strip() for x in sl.xpath(
            ".//*[contains(@class,'name') or contains(@class,'title')]/text()")
            if x.strip()]
        if not nm:
            continue
        txt = [x.strip() for x in sl.xpath(".//p//text()") if x.strip()]
        # 结构：名称, 年份, 综合得分率, x%, 乘员保护, x%, VRU保护, x%, 主动安全, x%
        def num_after(label):
            try:
                i = txt.index(label)
                return float(txt[i + 1].rstrip("%"))
            except (ValueError, IndexError):
                return None
        link = sl.xpath(".//a/@href")
        rows.append({
            "name": nm[0], "year": (txt[1] if len(txt) > 1 and txt[1].isdigit() else None),
            "overall": num_after("综合得分率"),
            "occupant": num_after("乘员保护"),
            "vru": num_after("VRU保护"),
            "active": num_after("主动安全"),
            "url": ("https://www.c-ncap.org.cn" + link[0]) if link else HOME,
        })
    return [r for r in rows if r["overall"] is not None]


def diff(rows: list[dict], store: dict, meta: dict) -> dict:
    add, upd, same, conflict = [], [], [], []
    for r in rows:
        k = r["name"]
        cur = store.get(k)
        if cur is None:
            add.append(r)
        elif abs(float(cur) - r["overall"]) < 0.05:
            same.append(r)
        else:
            conflict.append({"row": r, "old": cur})
        # meta 是否已标官网来源
        m = meta.get(k) or {}
        if m.get("source") != "cncap_official_website":
            upd.append(r)
    return {"add": add, "upd": upd, "same": same, "conflict": conflict}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="合并进 cncap_scores.json")
    ap.add_argument("--scores", default=SCORES_PATH)
    a = ap.parse_args()

    rows = fetch_latest()
    print(f"[抓取] 官网最新批次 {len(rows)} 款")
    store = json.load(open(a.scores, encoding="utf-8"))
    d = diff(rows, store.get("scores") or {}, store.get("meta") or {})
    print(f"[对照] 新增 {len(d['add'])} · 更新来源 {len(d['upd'])} · "
          f"一致 {len(d['same'])} · 数值冲突 {len(d['conflict'])}")
    for c in d["conflict"]:
        print(f"  ⚠️ {c['row']['name']}: 官网 {c['row']['overall']} vs 本表 {c['old']}")
    for r in d["add"]:
        print(f"  + {r['name']} {r['overall']}%（{r['year']} 年规程）")

    if not a.apply:
        print("\n[dry-run] 未写文件；加 --apply 合并。")
        return

    today = dt.date.today().isoformat()
    store.setdefault("scores", {})
    store.setdefault("meta", {})
    store.setdefault("provinance", {})
    for r in d["add"] + d["upd"] + d["same"]:
        store["scores"][r["name"]] = r["overall"]
        store["meta"][r["name"]] = {"source": "cncap_official_website",
                                    "url": r["url"], "date": today,
                                    "year": r["year"]}
    store["provinance"]["官网自动抓取"] = {
        "url": HOME, "mode": "首页 swiper 服务端渲染解析", "date": today,
        "note": "每批最新结果自动入库；更早批次仍见汇总表"}
    store["note"] = ("C-NCAP 综合得分率（%）。来源：官网首页自动抓取（每批最新）+ "
                     "测评结果汇总（2024 年至今存量）。2026-11 起抓取自动化。")
    json.dump(store, open(a.scores, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"[ok] cncap_scores.json 合并完成，总款数 {len(store['scores'])}")


if __name__ == "__main__":
    main()
