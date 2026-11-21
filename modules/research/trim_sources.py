# -*- coding: utf-8 -*-
"""trim_sources.py —— 逐配置采集的多源调度（可插拔）

为什么要这层（2026-11-21 用户追问「不能多源查吗」）：
  原 auto_trims 是单源单点：查汽车之家索引 → miss 就 skip，整款车没有配置。
  实测暴露：T6/MONA L05 在索引里没有，但汽车之家车系页存在（sid 8584），
  补一条站点检索就都能拿到 4 款配置。说明问题在"寻址方式单一"，不是"数据源没有"。

  所以把「找车系 → 取配置」抽成有序的源链，每次尝试都留痕：
    S1 autohome_index    本地 A-Z 索引（快，覆盖在售）
    S2 autohome_search   站点检索定位 series id（索引漏收新车时兜底）
    S3 content_check     多站内容页核对「这台车确实存在 + 指导价区间」
                         —— 只作证据，不产出结构化配置（防编造）

  为什么 S3 只做存在性：实测易车 API 返回反爬 HTML、懂车帝 config 页 0 字节、
  有驾内嵌数据只有 1 条主推款。要靠浏览器自动化才能拿结构化表，成本与失效风险高。
  与其塞一个"看起来能跑"的假源，不如诚实记录"已确认存在、但无结构化配置表"，
  让待补清单里的原因可追溯。

扩展新源只需实现一个 `_try_xxx(model) -> dict | None` 并注册到 SOURCES。
"""
from __future__ import annotations

import re

import paths

# 源链（顺序即优先级）。kind=structured 的源能产出 trims；existence 只产出证据。
SOURCES = [
    {"id": "autohome_index", "kind": "structured", "desc": "汽车之家 A-Z 车系索引"},
    {"id": "autohome_search", "kind": "structured", "desc": "汽车之家站点检索定位 series id"},
    {"id": "content_check", "kind": "existence", "desc": "多站内容页核对存在性与指导价区间"},
]


def _attempt(attempts, source, ok, detail):
    attempts.append({"source": source, "ok": bool(ok), "detail": str(detail)[:160]})


def _price_range_from_text(text):
    """从内容页摘要里抽指导价区间（仅作文档证据，不进结构化数据）。"""
    m = re.search(r"(\d{1,3}\.\d{1,2})\s*[-–~—]\s*(\d{1,3}\.\d{1,2})\s*万", str(text or ""))
    if m:
        return [float(m.group(1)), float(m.group(2))]
    m = re.search(r"(\d{1,3}\.\d{1,2})\s*万起", str(text or ""))
    if m:
        return [float(m.group(1)), None]
    return None


def collect_trims(model, index, parse_config, find_sid_local, find_sid_remote,
                  content_probe=True):
    """按源链采集一款车的逐配置。

    返回 dict:
      status      "ok" | "missing"
      source      命中的源 id（missing 时为 ""）
      sid/ah_name 命中的车系
      trims       结构化款型列表（missing 时为空）
      attempts    每次尝试的记录（source/ok/detail），写进待补清单
      evidence    {"exists": bool, "price_range": [lo,hi], "hits": [...], "note": str}
    """
    attempts, evidence = [], {"exists": False, "price_range": None, "hits": [], "note": ""}

    # S1 本地索引
    sid, ah_name, how = find_sid_local(model, index)
    if sid:
        _attempt(attempts, "autohome_index", True, f"{how} → sid {sid}")
        trims = _parse(parse_config, sid, attempts, "autohome_index")
        if trims:
            return {"status": "ok", "source": "autohome_index", "sid": sid,
                    "ah_name": ah_name, "trims": trims, "attempts": attempts,
                    "evidence": evidence}
    else:
        _attempt(attempts, "autohome_index", False, "本地索引未命中（可能新车未收录）")

    # S2 站点检索
    sid2, name2, how2 = find_sid_remote(model)
    if sid2:
        _attempt(attempts, "autohome_search", True, f"{how2} → sid {sid2}")
        trims = _parse(parse_config, sid2, attempts, "autohome_search")
        if trims:
            return {"status": "ok", "source": "autohome_search", "sid": sid2,
                    "ah_name": name2, "trims": trims, "attempts": attempts,
                    "evidence": evidence}
    else:
        _attempt(attempts, "autohome_search", False, "站点检索未取到车系页")

    # S3 存在性核对（只出证据）
    if content_probe:
        try:
            from modules.research import researcher as R
            hits = []
            for q in (f"{model} 上市 指导价 配置", f"{model} 报价"):
                rs = R.search(q, backend="bing", n=6) or []
                for r in rs:
                    title = str(r.get("title") or "")
                    if model.replace(" ", "")[:4] in title.replace(" ", ""):
                        hits.append({"title": title[:60], "url": str(r.get("url") or "")[:90]})
                if hits:
                    break
            pr = _price_range_from_text(" ".join(h["title"] for h in hits))
            evidence.update({"exists": bool(hits), "price_range": pr, "hits": hits[:3],
                             "note": "多站内容页确认该车在售" if hits else "内容页也未检索到，可能尚未上市"})
            _attempt(attempts, "content_check", bool(hits),
                     f"内容页 {len(hits)} 条，价格区间 {pr}" if hits else "无匹配内容页")
        except Exception as e:
            _attempt(attempts, "content_check", False, f"{type(e).__name__}: {e}")
            evidence["note"] = "存在性核对失败"

    return {"status": "missing", "source": "", "sid": sid2 or sid, "ah_name": "",
            "trims": [], "attempts": attempts, "evidence": evidence}


def _parse(parse_config, sid, attempts, tag):
    try:
        tl, err = parse_config(sid)
    except Exception as e:
        tl, err = None, f"{type(e).__name__}: {e}"
    if err or not tl or len(tl) < 2:
        _attempt(attempts, tag, False, f"解析失败：{err or f'有效款型不足 2 个（{len(tl) if tl else 0}）'}")
        return None
    return tl


def summarize_attempts(attempts) -> str:
    """把 attempts 压成一句人话，写进待补清单。"""
    if not attempts:
        return "未尝试任何源"
    parts = []
    for a in attempts:
        parts.append(f"{a['source']}{'✓' if a['ok'] else '✗'}({a['detail']})")
    return " → ".join(parts)
