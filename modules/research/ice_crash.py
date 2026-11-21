#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""燃油/混动四榜 L1 官方碰撞证据层（ice_crash.py，v6.3）。

解决什么问题
------------
v6.2 的 `no_safety_dimension` 写「C-NCAP / C-IASI 成绩未入库」，因此不设安全维。
本模块把**公开、免登录、可核验**的官方碰撞测评记录接进来，但**只作 L1 事实与
判档证据，不换算成分数、不进 6 维加权**，理由：

  · 覆盖率远达不到维度级准入门槛 G2（≥70%）。实测 C-NCAP 精确命中 5/122，
    C-IASI 命中数见报告；把 4%~30% 覆盖的安全硬塞进加权分，等于让「有没有
    被抽去测过」决定名次，是选择性披露（MNAR），比不打分更糟。
  · L1 官方碰撞证据与 L4 自选择口碑不是同一种证据，不能混在同一个加权和里。

因此本模块走「事实列 + 证据等级」通道：候选车有 L1 官方测评记录时，
`evidence_status` 可以走 L1 路径达到 A（详见 ice_scoring._evidence），
但 `scores` / `total` / `adjust` 一律不变。

数据来源（全部公开 GET，不破解签名、不模拟登录态、不绕验证码）
--------------------------------------------------------------
  · C-IASI 测评结果列表：https://www.ciasi.org.cn/get_products_list.html
    （列表页 /products/list-492.html 的表单公开调用此接口，返回 2018 年至今
      全量评级表 HTML；逐行含品牌/测评车型/车辆型号/级别/生产厂家/五个分项）
    详情页 /products/show-<id>.html 提供测评年份、规程版本、具体测试配置。
  · C-NCAP：本仓库已核验的 cncap_scores.json（官网首页 swiper SSR + 汇总表）。

等级口径（重要，如实披露）
--------------------------
C-IASI 列表页用 `level1/{1,2,3,4}.png` 数字图标，详情页图例却用
`G+.png / G.png / A.png / M.png / P.png` 字母图标，官网**没有给出两套图标的
文字对照**。因此本模块 **不解码等级**，只保留原始图标文件名 `grades_raw`，
并把「存在官方测评记录」作为唯一事实结论。任何把 1/2/3/4 直接读成 G/A/M/P
的做法都是推断，本项目不做。

用法
----
    python3 ice_crash.py --pool <ice_pool.json> [--pool <ice_pool_full.json>] \
        --out categories/ice_crash.json [--report <crash_evidence.md>] [--fetch]

    --fetch   强制重抓 C-IASI；默认读 cache/ciasi_results.json，缺失时自动抓。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time

import requests

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)

CIASI_LIST = "https://www.ciasi.org.cn/get_products_list.html"
CIASI_PAGE = "https://www.ciasi.org.cn/products/list-492.html"
CIASI_DETAIL = "https://www.ciasi.org.cn/products/show-{}.html"
CNCAP_PATH = os.path.join(HERE, "cncap_scores.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131 Safari/537.36")
SLEEP = 0.10
CURRENT_MIN_YEAR = 2023          # 2026-10 榜单：测评年款 ≥2023 视为现款/近款
GRADE_COLS = ("耐撞性与维修经济性", "车内乘员", "车外行人", "辅助安全", "新能源汽车专项")

# 受控别名：仅当「品牌 + 测评车型」拼接后可确认是同一车系时启用。
# 不做子串模糊匹配；例如「途观L Pro」≠「途观L」、「海狮07」≠「海狮06DM」，一律不配。
ALIAS = {
    "宝马3系": "3系",
    "奥迪A6L": "A6 L",
    "奔驰GLC": "GLC",
    "红旗H5": "H5",
}


def norm(s) -> str:
    return re.sub(r"[\s\-_·（）()＋+／/．.、,，]", "", str(s or "")).lower()


def clean(cell: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", cell or "")).strip()


def icon_of(cell: str):
    m = re.search(r"/assets/img/car/([^\"']+\.png)", cell or "")
    return m.group(1) if m else None


def fetch_cias(sess: requests.Session) -> dict:
    r = sess.get(CIASI_LIST, timeout=30)
    r.raise_for_status()
    r.encoding = r.apparent_encoding
    payload = json.loads(r.text)
    views = payload["data"]["views"]

    # 年份标题按出现位置切段，给每一行分配发布年份
    marks = [(m.start(), int(m.group(1)))
             for m in re.finditer(r'<div class="tableTit">(\d{4})年-评级', views)]
    rows_out = []
    for rm in re.finditer(r"<tr>(.*?)</tr>", views, re.S):
        tds = re.findall(r"<td[^>]*>(.*?)</td>", rm.group(1), re.S)
        if len(tds) < 11:
            continue  # 表头
        year = next((y for pos, y in reversed(marks) if pos < rm.start()), None)
        cells = [clean(x) for x in tds]
        icons = [icon_of(x) for x in tds]
        link = re.search(r"/products/show-(\d+)\.html", rm.group(1))
        rows_out.append({
            "seq": cells[0], "brand": cells[1], "model": cells[2],
            "model_code": cells[3], "cls": cells[4], "manufacturer": cells[5],
            "year": year,
            "grades_raw": {GRADE_COLS[i]: icons[6 + i] for i in range(5)},
            "url": CIASI_DETAIL.format(link.group(1)) if link else CIASI_PAGE,
        })
    return {
        "fetched_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "url": CIASI_LIST, "source_page": CIASI_PAGE,
        "n_rows": len(rows_out),
        "years": sorted({r["year"] for r in rows_out if r["year"]}),
        "rows": rows_out,
    }


def fetch_detail(sess: requests.Session, row: dict) -> dict:
    """只取可核验的年份/规程/具体测试配置，不解析等级图标。"""
    try:
        r = sess.get(row["url"], timeout=20)
        r.encoding = r.apparent_encoding
        t = r.text
        yr = re.search(r"测评年份[：:]\s*(\d{4})年", t)
        rule = re.search(r"规程版本[：:]\s*(\d{4})年", t)
        trim = re.search(r"<th>([^<]{8,80}（[^<]+）)</th>", t)
        return {
            "test_year": int(yr.group(1)) if yr else row.get("year"),
            "rule_version": rule.group(1) if rule else None,
            "test_trim": clean(trim.group(1)) if trim else None,
            "detail_url": row["url"],
        }
    except Exception as e:                                   # noqa: BLE001
        return {"test_year": row.get("year"), "rule_version": None,
                "test_trim": None, "detail_url": row["url"],
                "detail_error": type(e).__name__}


def freshness(r: dict) -> dict:
    """年款新鲜度：详情页明确「20XX款」优先，否则用列表页发布年份兜底。

    实测 55 条命中记录里，详情页能解析出具体配置文本的只有 2 条（页面结构不统一），
    但列表页按「YYYY年-评级」分段给出的**发布年份覆盖 100%**。发布年份与测试年款
    通常相差 ≤1 年，作为「近 3 年内是否被官方测过」的判据足够，但不能冒充
    「该成绩一定适用于 2026 年在售的具体配置」——所以标 medium 置信并如实披露。
    """
    txt = f"{r.get('test_trim') or ''} {r.get('model_code') or ''}"
    m = re.search(r"(20\d{2})款", txt)
    if m:
        yr, basis, conf = int(m.group(1)), "detail_trim_text", "high"
    else:
        yr = r.get("test_year") or r.get("year")
        basis = "publish_year_proxy" if yr else "unclear"
        conf = "medium" if yr else "low"
    return {
        "trim_year": yr,
        "year_basis": basis,
        "year_confidence": conf,
        "is_current": bool(yr and yr >= CURRENT_MIN_YEAR),
    }


def match(pool_names: list[str], ciasi_rows: list[dict], cncap_scores: dict) -> dict:
    pnorm = {norm(n): n for n in pool_names}
    by_model = {}
    for n in pool_names:
        by_model.setdefault(n, {"_ci": [], "_cn": None, "matched_by": []})

    # --- C-IASI：精确车型名 → 品牌+车型拼接 → 白名单别名 ---
    for r in ciasi_rows:
        nm = r["model"]
        key = pnorm.get(norm(nm))
        how = "exact"
        if not key:
            key = pnorm.get(norm((r["brand"] or "") + nm))
            how = "brand+model"
        if not key:
            for pool_name, alias in ALIAS.items():
                if pool_name in pnorm and norm(alias) == norm(nm):
                    key, how = pnorm[pool_name], f"alias:{alias}"
                    break
        if key:
            by_model[key]["_ci"].append({**r, "freshness": freshness(r),
                                         "matched_by": how})

    # --- C-NCAP：只做精确匹配，不做任何变体 ---
    for nm, score in (cncap_scores or {}).items():
        key = pnorm.get(norm(nm))
        if key:
            by_model[key]["_cn"] = {"model": nm, "overall_pct": score,
                                    "matched_by": "exact"}

    out = {}
    for nm, v in by_model.items():
        ci = v["_ci"]
        cn = v["_cn"]
        cur_ci = [r for r in ci if r["freshness"]["is_current"]]
        out[nm] = {
            "has_l1": bool(ci or cn),
            # 只有「详情页明确写出 2023 款及以后」或 C-NCAP 近年成绩，
            # 才能作为判档的 L1 现款证据；更早年款只进事实列。
            "l1_current": bool(cur_ci or cn),
            "l1_historical_only": bool((ci or cn) and not (cur_ci or cn)),
            "sources": (["CIASI"] if ci else []) + (["CNCAP"] if cn else []),
            "cias": ci or None,
            "cncap": cn,
            "matched_by": sorted({x["matched_by"] for x in ci} |
                                 ({cn["matched_by"]} if cn else set())),
            "note": ("C-IASI / C-NCAP 成绩对应具体测试年款与配置，不自动外推到"
                     "同车系其它年款、动力或配置；等级图标未解码，不作分数。"),
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", action="append", required=True,
                    help="ice_pool.json（可重复，合并车型名集合）")
    ap.add_argument("--out", default=str(paths.cat("ice_crash.json")))
    ap.add_argument("--report", help="额外输出 Markdown 证据清单")
    ap.add_argument("--cache-dir", default=os.path.join(HERE, "cache"))
    ap.add_argument("--fetch", action="store_true", help="强制重抓 C-IASI")
    ap.add_argument("--no-detail", action="store_true", help="不抓详情页（更快）")
    a = ap.parse_args()

    os.makedirs(a.cache_dir, exist_ok=True)
    cache_path = os.path.join(a.cache_dir, "ciasi_results.json")
    sess = requests.Session()
    sess.trust_env = False
    sess.headers.update({"User-Agent": UA, "Referer": CIASI_PAGE})

    if a.fetch or not os.path.exists(cache_path):
        print("[抓取] C-IASI 全量测评结果表 …", file=sys.stderr)
        ciasi = fetch_cias(sess)
        json.dump(ciasi, open(cache_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    else:
        ciasi = json.load(open(cache_path, encoding="utf-8"))
    print(f"[C-IASI] {ciasi['n_rows']} 条 · 年份 {ciasi['years']}", file=sys.stderr)

    pool_names = []
    for p in a.pool:
        d = json.load(open(p, encoding="utf-8"))
        pool_names += [c["model"] for c in d["pool"]]
    pool_names = sorted(set(pool_names))
    print(f"[候选池] 合并后 {len(pool_names)} 款", file=sys.stderr)

    cncap = json.load(open(CNCAP_PATH, encoding="utf-8"))
    ev = match(pool_names, ciasi["rows"], cncap.get("scores") or {})

    # 详情页：只对命中的测评记录抓，补测评年份/规程/具体配置，回写缓存后重新匹配
    if not a.no_detail:
        urls = sorted({r["url"] for v in ev.values() for r in (v["cias"] or [])})
        print(f"[详情页] 补齐 {len(urls)} 条测评记录 …", file=sys.stderr)
        details = {}
        for i, url in enumerate(urls, 1):
            row = next((x for x in ciasi["rows"] if x["url"] == url), {"url": url})
            details[url] = fetch_detail(sess, row)
            if i % 10 == 0:
                print(f"  进度 {i}/{len(urls)}", file=sys.stderr)
            time.sleep(SLEEP)
        for r in ciasi["rows"]:
            if r["url"] in details:
                r.update(details[r["url"]])
        json.dump(ciasi, open(cache_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        ev = match(pool_names, ciasi["rows"], cncap.get("scores") or {})

    cn_meta = cncap.get("meta") or {}
    store = {
        "version": "v6.3",
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "purpose": ("L1 官方碰撞测评**存在性**证据；只作事实列与判档证据，"
                    "不换算分数、不进 6 维加权，排名零影响。"),
        "discipline": {
            "SCORING_ENABLED": False,
            "grade_decoded": False,
            "grade_note": ("C-IASI 列表页用 level1/1..4.png 数字图标，详情页图例用 "
                           "G+/G/A/M/P 字母图，官网无文字对照，故不解码等级；"
                           "只把「有官方测评记录」当作事实。"),
            "no_extrapolation": ("成绩对应具体测试年款/配置，不外推到同车系其它"
                                 "年款、动力或配置。"),
            "no_fuzzy_match": ("只做精确归一化、品牌+车型拼接、显式白名单别名三种匹配；"
                               "不做子串模糊匹配，避免途观L Pro→途观L 这类张冠李戴。"),
            "freshness_rule": {
                "current_min_year": CURRENT_MIN_YEAR,
                "note": ("详情页明确写出「20XX款」时用该年份（high 置信）；"
                         "否则用 C-IASI 列表页按「YYYY年-评级」分段给出的发布年份"
                         "作代理（medium 置信，与测试年款通常相差 ≤1 年）。"
                         "年份 ≥2023 或为 C-NCAP 2024 年至今成绩，才认作可支撑判档的"
                         "L1 近期证据；更早年款只进事实列。这是保守阈值，不是换代判定"
                         "——本仓库没有车型换代数据，故不声称「2023+ 一定适用于现款」。"),
            },
        },
        "sources": {
            "CIASI": {
                "list_url": CIASI_LIST, "page_url": CIASI_PAGE,
                "fetched_at": ciasi["fetched_at"], "n_rows": ciasi["n_rows"],
                "years": ciasi["years"],
                "access": "公开 GET，无签名、无登录态；限速 0.1s",
            },
            "CNCAP": {
                "file": "cncap_scores.json",
                "n_scores": len(cncap.get("scores") or {}),
                "note": cncap.get("note"),
                "home": "https://www.c-ncap.org.cn/",
            },
        },
        "cncap_meta": cn_meta,
        "alias_whitelist": ALIAS,
        "pool_size": len(pool_names),
        "coverage": {
            "has_l1": sum(1 for v in ev.values() if v["has_l1"]),
            "l1_current": sum(1 for v in ev.values() if v["l1_current"]),
            "l1_historical_only": sum(1 for v in ev.values()
                                      if v["l1_historical_only"]),
            "cias": sum(1 for v in ev.values() if v["cias"]),
            "cncap": sum(1 for v in ev.values() if v["cncap"]),
            "both": sum(1 for v in ev.values() if v["cias"] and v["cncap"]),
        },
        "evidence": ev,
        "ciasi_rows": ciasi["rows"],
    }
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    json.dump(store, open(a.out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    cv = store["coverage"]
    print(f"[ok] {a.out} | 池 {len(pool_names)} 款 · 有 L1 证据 {cv['has_l1']} 款 "
          f"(现款 {cv['l1_current']} / 仅历史 {cv['l1_historical_only']} · "
          f"C-IASI {cv['cias']} / C-NCAP {cv['cncap']} / 双源 {cv['both']})",
          file=sys.stderr)

    if a.report:
        L = ["# 燃油 / 混动四榜 · L1 官方碰撞证据清单", "",
             f"> 生成 {store['generated_at']} · 候选池 {len(pool_names)} 款 · "
             f"有官方测评记录 **{cv['has_l1']}** 款 "
             f"（C-IASI {cv['cias']} · C-NCAP {cv['cncap']} · 双源 {cv['both']}）；"
             f"其中可作**现款 L1 证据**的 {cv['l1_current']} 款，"
             f"仅历史年款、不外推的 {cv['l1_historical_only']} 款。", "",
             "## 为什么这不做成第 7 个评分维", "",
             f"- 覆盖率 {cv['has_l1']}/{len(pool_names)} = "
             f"**{cv['has_l1'] / max(1, len(pool_names)):.1%}**（近期证据仅 "
             f"{cv['l1_current'] / max(1, len(pool_names)):.1%}），远低于维度级准入"
             "门槛 G2（≥70%）。把「有没有被抽去测过」写进加权分，等于奖励选择性披露。",
             "- L1 官方碰撞证据与 L4 自选择口碑不同质，不混在同一个加权和里。",
             f"- C-IASI 等级图标无官方文字对照，**未解码**；本清单只证明"
             "「该车型存在官方测评记录」，不证明成绩好坏。", "",
             "## 判档用的近期 L1 证据", "",
             "| 车型 | 来源 | 匹配方式 | 测评年款 | 年款依据 | 具体测试配置 | 详情 |",
             "|---|---|---|---|---|---|---|"]
        for nm, v in sorted(ev.items()):
            if not v["l1_current"]:
                continue
            for r in (v["cias"] or []):
                if not r["freshness"]["is_current"]:
                    continue
                f_ = r["freshness"]
                L.append(f"| **{nm}** | C-IASI | {r.get('matched_by')} | "
                         f"{f_['trim_year']} | "
                         f"{f_['year_basis']} / {f_['year_confidence']} | "
                         f"{(r.get('test_trim') or r.get('model_code') or '—')[:56]} | "
                         f"[链接]({r.get('detail_url') or r['url']}) |")
            if v["cncap"]:
                m = cn_meta.get(v["cncap"]["model"]) or {}
                L.append(f"| **{nm}** | C-NCAP | exact | 2024 年至今成绩表 | "
                         f"成绩表口径 / high | 综合得分率 {v['cncap']['overall_pct']}% | "
                         f"[链接]({m.get('url') or 'https://www.c-ncap.org.cn/'}) |")
        if not any(v["l1_current"] for v in ev.values()):
            L.append("| — | — | — | — | — | 本期无满足新鲜度门槛的记录 | — |")
        hist = [(nm, r) for nm, v in ev.items() for r in (v["cias"] or [])
                if not r["freshness"]["is_current"]]
        L += ["", f"## 历史年款记录（{len(hist)} 条，只作事实，不作判档证据）", "",
              "> 这些成绩对应上一代或更早年款，**不外推到 2026 年在售车型**；"
              "列出来是为了让读者知道「查得到旧成绩，但我们没拿它当现款证据」。", ""]
        for nm, r in hist[:25]:
            L.append(f"- {nm}：{r.get('year') or '—'} 年发布 · "
                     f"`{(r.get('test_trim') or r.get('model_code') or '—')[:60]}` · "
                     f"年款判定 {r['freshness']['year_basis']}")
        none = [nm for nm, v in ev.items() if not v["has_l1"]]
        L += ["", f"## 无官方测评记录（{len(none)} 款）", "",
              "> 未命中 = 本次两个公开数据源里没有该车型的可核验记录，"
              "**不等于车不安全**，也不记 0 分、不扣分。", "",
              "、".join(none) if none else "（无）", "",
              "## 匹配纪律", "",
              "- 精确归一化 / 品牌+车型拼接 / 显式白名单别名，三种之外一律不配。",
              "- 明确拒绝的近似：`途观L Pro ≠ 途观L`、`海狮07 ≠ 海狮06DM`、"
              "`红旗HS6 ≠ 红旗HS5`、`长安启源A07 ≠ 长安启源Q07`。",
              "- 未匹配的 C-IASI 记录多为纯电、中大型/大型车或已停产老车，"
              "不在本四榜候选池内。", ""]
        open(a.report, "w", encoding="utf-8").write("\n".join(L))
        print(f"[ok] {a.report}", file=sys.stderr)


if __name__ == "__main__":
    main()
