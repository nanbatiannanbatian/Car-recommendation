#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""电池来源（电芯品牌）联网核验（v10.9.0）——「查不到就不许静默留空」。

背景（用户问题）：极氪7X、阿维塔07 的电池来源列为空。根因不是这两款车没数据，
而是它们从没进过任何电芯事实库（fact_library.json / cell_facts.json /
battery_platform.json 都没有条目），fact_columns() 取 cell_brand 落空，页面就空了。
用户要求：①代码层修复确保以后不遗漏；②以后但有车查不到电池信息就联网查；
③还查不到的在电池来源栏标注原因（用户已确认沿用 kWh/万元的「—（原因）」样式）。

设计（遵守项目铁律）：
  · 联网只在 research 内发生（唯一网络出口）；零第三方依赖（urllib + 正则）。
  · **不把搜索摘要当事实**（2026-11-21 实跑教训）：百度摘要会串车型/配置——
    极氪7X 抓到「宁德时代+神行」（神行实为极氪001）、阿维塔07 抓到「中创新航+
    麒麟」（实为宁德时代神行超充）。因此联网**只产出线索 need_review，绝不自动
    confirmed 回填**。事实确认只来自人工核验后回填权威库（走带 kWh/配置名的
    汽车之家/易车配置页），联网负责「不遗漏 + 发现待核线索 + 标注原因」。
  · 逐车状态齐全：ok(库里已有) / need_review(联网线索，需人工核验) /
    unavailable(联网无结果) / net_error。每个非 ok 状态都带 reason，供页面标注。
  · 当天缓存复用、跨天失效（与 crash_auto_verify v10.8.0 同一口径）；
    缓存落 runs/（零 JSON 规则保护，不进 GitHub/发布包）。

用法：
    python3 -m modules.research.cell_brand_verify --selftest
    python3 -m modules.research.cell_brand_verify --candidates runs/midsuv_bev_pool.json
"""
from __future__ import annotations

import argparse
import html as _html
import json
import os
import re
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import datetime

import paths

ROOT = str(paths.ROOT)
CACHE = str(paths.runs("cell_brand_verify_cache.json"))
UA = {"User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120 Mobile Safari/537.36"}
BAIDU = "https://www.baidu.com/s?wd="

# 已知电芯品牌词库（用于从搜索摘要里发现线索；顺序：长的在前，避免「宁德时代」截断合资名）
BRANDS = [
    "宁德时代(时代吉利)", "宁德时代(时代长安)", "宁德时代(时代一汽)",
    "时代吉利", "时代长安", "时代一汽", "宁德时代", "弗迪", "比亚迪刀片",
    "衢州极电", "吉曜通行", "中创新航", "国轩高科", "亿纬锂能", "欣旺达",
    "蜂巢能源", "清陶", "因湃", "得壹能源", "瑞浦兰钧", "正力新能", "太阳神",
]
# 电池技术/配置线索词——用来判断搜索摘要是否与「评分配置」同类，防止串到增程版/别款
TECH_HINTS = ["麒麟", "神行超充", "神行电池", "刀片", "金砖", "半固态", "骁遥", "超混"]

STATUS_NOTE = {
    "ok": "本地事实库/逐配置表已有核验电芯品牌",
    "need_review": "联网发现电芯品牌线索，但未获权威配置页确认（需人工核验后回填）",
    "unavailable": "联网未查到该配置的电芯品牌（多种查询式均未出现品牌词）",
    "net_error": "联网失败，无法核对该车电芯品牌",
}


def _ctx() -> ssl.SSLContext:
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


def fetch_search(query: str, timeout: int = 15) -> str | None:
    """research 唯一网络出口：抓百度搜索结果正文（服务端渲染，零依赖可读）。"""
    url = BAIDU + urllib.parse.quote(query)
    try:
        raw = urllib.request.urlopen(
            urllib.request.Request(url, headers=UA), timeout=timeout,
            context=_ctx()).read()
    except Exception as ex:                                  # noqa: BLE001
        print(f"[cell_verify] 搜索失败：{type(ex).__name__}: {ex}", file=sys.stderr)
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", "replace")
    # 去标签 + 反转义，得到可读正文（摘要在同页面里，无需 JS）
    return _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)))


def _compact(s: str) -> str:
    return re.sub(r"\s+", "", str(s or ""))


def parse_brands(text: str) -> list[str]:
    """从搜索正文里发现电芯品牌线索（按出现次数降序；不去重同义）。"""
    if not text:
        return []
    found: dict[str, int] = {}
    for b in BRANDS:
        n = text.count(b)
        if n:
            found[b] = n
    return [b for b, _ in sorted(found.items(), key=lambda kv: -kv[1])]


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def load_cache(cache_path: str | None = None) -> dict:
    try:
        return json.load(open(cache_path or CACHE, encoding="utf-8")) or {}
    except Exception:                                        # noqa: BLE001
        return {}


def save_cache(cache: dict, cache_path: str | None = None) -> None:
    p = cache_path or CACHE
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        json.dump(cache, open(p, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
    except Exception as ex:                                  # noqa: BLE001
        print(f"[cell_verify] 缓存写入失败（不影响出榜）：{type(ex).__name__}: {ex}",
              file=sys.stderr)


def _queries_for(model: str, cand: dict) -> list[str]:
    """构造多个查询式，逐个试直到命中品牌线索。

    为什么要多式（2026-11-21 实测）：单一问法「车型+配置+电芯品牌+电池供应商」
    在百度摘要里经常一个品牌词都不出现——小鹏G6 / 乐道L60 / 蔚来ES6 / 特斯拉Model Y
    四车实测全部落空，于是页面显示「联网未查到」。换问法命中率明显更高。

    纪律：查询式里**不放品牌候选词**。百度摘要常回显查询词，一旦把
    「宁德时代」写进查询，就会凭空「查」出宁德时代——那是自证，不是证据。
    """
    trim = (cand.get("trim") or {}).get("name") or cand.get("trim_raw") or ""
    kwh = cand.get("battery_kwh") or ""
    parts = [model]
    if trim:
        parts.append(str(trim))
    if kwh:
        parts.append(f"{kwh}kWh")
    head = " ".join(str(p) for p in parts if p)
    return [
        f"{head} 电芯品牌 电池供应商",
        f"{head} 电池 搭载 什么电芯 品牌",
        f"{model} 电芯供应商 电池类型",
    ]


def verify_cell(model: str, cand: dict, *, local_brand: str | None = None,
                fetcher=None, today: str | None = None,
                cache: dict | None = None, cache_path: str | None = None,
                use_cache: bool = False, local_source: str = "fact_library") -> dict:
    """核验一款车的电芯品牌，返回 {status, brand, reason, clue, query, source}。

    local_brand 非空（事实库已有）→ 直接 ok，不联网。
    fetcher 可注入（selftest 用）；默认走 research 百度出口。
    cache 注入时不落盘；use_cache=True 且未注入时读写 runs/ 缓存（当天复用）。
    """
    today = today or _today()
    cache_given = cache is not None
    if cache is None:
        cache = load_cache(cache_path) if use_cache else {}
    cache_on = use_cache or cache_given
    key = _compact(model)

    if local_brand not in (None, "", "—"):
        return {"status": "ok", "brand": str(local_brand), "reason": "",
                "query": "", "source": local_source}

    cached = cache.get(key) if cache_on else None
    if isinstance(cached, dict) and cached.get("verified_date") == today:
        rec = dict(cached["record"])
        rec["from_cache"] = True
        return rec

    fn = fetcher or fetch_search
    queries = _queries_for(model, cand)
    query = queries[0]
    text = None
    brands: list[str] = []
    for q in queries:
        t = fn(q)
        if t is None:
            continue
        text = t
        query = q
        brands = parse_brands(t)
        if brands:
            break
    if text is None:
        rec = {"status": "net_error", "brand": "", "reason": STATUS_NOTE["net_error"],
               "query": query, "source": BAIDU}
        # 联网失败不缓存（与 crash_auto_verify 同口径，别把失败当已确认）
        return rec

    tech = [t for t in TECH_HINTS if t in text]
    # 搜索摘要只作线索，绝不自动回填为事实（实跑会串配置，见模块头注释）。
    # 即使是「品牌+技术词」同现也不采信：人工拿权威配置页核验后才回填事实库。
    if brands:
        clue = brands[0] + (f"（含{tech[0]}线索）" if tech else "")
        rec = {"status": "need_review", "brand": "",
               "clue": clue,
               "reason": f"联网线索「{clue}」未获权威配置页确认，需人工核验后回填",
               "query": query, "source": BAIDU}
    else:
        rec = {"status": "unavailable", "brand": "",
               "reason": "联网未查到该配置的电芯品牌（多种查询式均未出现品牌词）",
               "query": query, "source": BAIDU}
    rec["verified_date"] = today
    rec["verified_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rec["from_cache"] = False
    if cache_on and rec["status"] != "net_error":
        saved = dict(rec)
        saved.pop("from_cache", None)
        cache[key] = {"verified_date": today, "record": saved}
    if use_cache and not cache_given:
        save_cache(cache, cache_path)
    return rec


def load_trim_cells(path: str | None = None) -> dict:
    """从 trims.json 读逐配置电芯：{canon(model): {"by_trim": {配置名: 电芯}, "series": 电芯}}。

    为什么必须读 trims.json（2026-11-21）：
      本模块此前只查车系级事实库（fact_library）+ 百度摘要，**漏掉了 trims.json
      里逐配置的电芯**——而逐配置才是准确口径：极氪7X 的 75kWh 用弗迪第二代金砖、
      103kWh 用宁德麒麟，车系级一个值盖不住。加上这一路之后，
      「联网未查到该配置的电芯品牌」这类占位才有真实数据可回落。
    """
    from modules.contracts.model_names import canon
    p = path or str(paths.cat("trims.json"))
    if not os.path.exists(p):
        return {}
    try:
        d = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, dict] = {}
    for e in d.get("entries", []):
        m = e.get("model")
        if not m:
            continue
        by_trim = {}
        series = ""
        for t in e.get("trims") or []:
            v = (t.get("cell_supplier") or "").strip()
            if not v or v in ("—", "-"):
                continue
            by_trim[str(t.get("name") or "")] = v
            series = series or v
        if by_trim:
            out[canon(m)] = {"by_trim": by_trim, "series": series}
    return out


def _trim_cell(model: str, cand: dict, trim_cells: dict) -> str:
    """从 trims.json 逐配置电芯里取值：先按评分配置名精确匹配，再退车系级。"""
    if not trim_cells:
        return ""
    from modules.contracts.model_names import canon
    rec = trim_cells.get(canon(model))
    if not rec:
        return ""
    want = str((cand.get("trim") or {}).get("name") or cand.get("trim_raw") or "")
    if want:
        for name, val in rec["by_trim"].items():
            if name == want:
                return val
    return rec.get("series") or ""


def verify_all(cands: list[dict], *, facts: dict | None = None,
               fetcher=None, today: str | None = None,
               cache: dict | None = None, cache_path: str | None = None,
               use_cache: bool = False, trim_cells: dict | None = None) -> dict:
    """对全榜逐车核验：事实库有值跳过；空的联网查；产出逐车状态与原因。"""
    facts = facts or {}
    if trim_cells is None:
        trim_cells = load_trim_cells()
    per_car: dict[str, dict] = {}
    for c in cands:
        model = c.get("model") if isinstance(c, dict) else str(c)
        local = (facts.get(model) or {}).get("cell_brand") or (
            c.get("fact_ref") or {}).get("电芯")
        local_source = "fact_library" if local else ""
        if not local:
            local = _trim_cell(model, c if isinstance(c, dict) else {}, trim_cells)
            if local:
                local_source = "trims.json 逐配置电芯"
        rec = verify_cell(model, c if isinstance(c, dict) else {},
                          local_brand=local, fetcher=fetcher, today=today,
                          cache=cache, cache_path=cache_path, use_cache=use_cache,
                          local_source=local_source or "未命中本地事实库")
        per_car[model] = rec
    return {
        "verified_date": today or _today(),
        "per_car": per_car,
        "summary": {s: sum(1 for r in per_car.values() if r["status"] == s)
                    for s in STATUS_NOTE},
    }


def selftest() -> int:
    """确定性自测：注入 fetcher，覆盖 ok/need_review/unavailable/缓存。"""
    cands = [{"model": "库内有值车", "trim": {"name": "X"}},
             {"model": "线索车A", "trim": {"name": "Y"}, "battery_kwh": 80},
             {"model": "仅线索车", "trim": {"name": "Z"}},
             {"model": "查不到车", "trim": {"name": "W"}}]
    facts = {"库内有值车": {"cell_brand": "宁德时代"}}
    calls: dict[str, int] = {"n": 0}

    def fake(query: str) -> str:
        calls["n"] += 1
        if "线索车A" in query:
            return "电芯品牌:宁德时代 麒麟电池 电池能量80kWh"
        if "仅线索车" in query:
            return "搜索结果 提到 弗迪 若干次 但没有配置线索词"
        return "没有任何电芯品牌信息的相关页面 暂无资料"

    rep = verify_all(cands, facts=facts, fetcher=fake, today="2026-11-21")
    p = rep["per_car"]
    # 库里有值不联网；线索车A/仅线索车 1 次查询命中；查不到车把 3 个查询式都试完
    assert p["库内有值车"]["status"] == "ok" and calls["n"] == 5, f"联网次数不对：{calls['n']}"
    # 即使品牌+技术词同现，也只出线索不回填（实跑会串配置，绝不 confirmed）
    assert p["线索车A"]["status"] == "need_review" and "宁德时代" in p["线索车A"]["clue"], p
    assert p["线索车A"]["brand"] == "", "线索不得直接当事实回填"
    assert p["仅线索车"]["status"] == "need_review" and p["仅线索车"]["reason"], p
    assert p["查不到车"]["status"] == "unavailable" and p["查不到车"]["reason"], p
    assert all(r.get("verified_at") or r["status"] == "ok" for r in p.values()), p
    # 当天缓存：注入 cache 后二次核验零新增联网
    cache: dict = {}
    verify_all([cands[1]], facts=facts, fetcher=fake, today="2026-11-21", cache=cache)
    n = calls["n"]
    rep2 = verify_all([cands[1]], facts=facts, fetcher=fake,
                      today="2026-11-21", cache=cache)
    assert calls["n"] == n and rep2["per_car"]["线索车A"]["from_cache"], "当天应复用"
    # 跨天失效
    verify_all([cands[1]], facts=facts, fetcher=fake, today="2026-11-22", cache=cache)
    assert calls["n"] == n + 1, "跨天必须重查"
    print("SELFTEST PASS · 电芯品牌联网核验：ok/need_review/unavailable + 当天缓存 + 线索不回填")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--candidates", default="runs/midsuv_bev_pool.json")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    cands = json.load(open(os.path.join(ROOT, a.candidates), encoding="utf-8"))["candidates"]
    facts = json.load(open(str(paths.cat("fact_library.json")), encoding="utf-8"))
    rep = verify_all(cands, facts=facts)
    print(f"电芯核验 {len(cands)} 款：{rep['summary']}")
    for m, r in rep["per_car"].items():
        if r["status"] != "ok":
            print(f"  {m}: {r['status']} {r.get('brand') or r.get('clue') or ''} — {r['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
