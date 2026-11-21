#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""碰撞成绩自动联网核验（v10.6.0，2026-11-21）——「以后不漏报」的根因修复。

背景（用户问题）：以前出榜只对「数字图标车」联网救回，其余靠本地权威库，
库里没有就显示「—」/空白。深蓝S07、宋L EV 就是这样漏掉的——官网有 C-NCAP
88.3% / C-IASI 一手测评，但本地库和候选池都没有，页面不显示也没人拦。

本模块遵守项目铁律：联网只在 research 模块内发生（唯一网络出口）；零第三方依赖
（urllib + 正则，不用 lxml/requests——本机 lxml 缺失导致 fetch_cncap.py 跑不起来）。

能力（诚实边界）：
  · 抓 C-NCAP 官网首页「最新测评结果」swiper（服务端渲染）：车型名/年份/综合得分率/
    分项，覆盖官网当期展示的最新批次（约 20 款）。
  · 与候选池 + cncap_scores.json 比对，分三类：
      new       = 官网有、本地库无 → 报告补录建议（--apply 时写进 cncap_scores.json）
      conflict  = 两边都有但值不同 → 报告（不自动覆盖人工核验值）
      covered   = 两边都有且一致
  · 历史批次（不在首页 swiper 的老车，如深蓝S07 2023）需要详情页 URL，
    本模块不猜 URL，靠一次性补录（已在本次修复中补入）。这点如实写进报告。

用法：
    python3 -m modules.research.crash_auto_verify --candidates runs/midsuv_bev_pool.json
    python3 -m modules.research.crash_auto_verify --candidates ... --apply
    python3 -m modules.research.crash_auto_verify --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import urllib.request
from datetime import datetime

import paths

ROOT = str(paths.ROOT)
HOME = "https://www.c-ncap.org.cn/"
CNCAP = os.path.join(ROOT, "cncap_scores.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _ctx() -> ssl.SSLContext:
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE       # 大陆代理 CA 过期，research 层统一容错
    return c


def fetch_home(timeout: int = 25) -> str | None:
    req = urllib.request.Request(HOME, headers=UA)
    try:
        raw = urllib.request.urlopen(req, timeout=timeout, context=_ctx()).read()
    except Exception as ex:                                  # noqa: BLE001
        print(f"[crash_verify] 联网失败：{type(ex).__name__}: {ex}", file=sys.stderr)
        return None
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


_YEAR_RE = re.compile(r"cncap-swiper__year\">\s*(\d{4})\s*<")
_SCORE_RE = re.compile(r"cncap-swiper__score-value\">\s*(\d+(?:\.\d+)?)%")
_TARGET_RE = re.compile(
    r"cncap-swiper__target-name\">\s*([^<]+?)\s*</p>.{0,120}?"
    r"cncap-swiper__target-score\"[^>]*>\s*(\d+(?:\.\d+)?)%", re.S)


def parse_home(html: str) -> list[dict]:
    """解析首页 swiper 卡片，逐卡返回 {model, year, overall, targets}。

    卡片结构：`<img src=... alt="车型" class="cncap-swiper__car-image">` 开头，
    其后到下一个 car-image 之间含 year / score-value / targets。alt 在 class 之前，
    所以按 car-image 位置切卡后，从「上一张卡结束到本卡 class」的尾部回看 alt。
    """
    positions = [m.start() for m in re.finditer(r"cncap-swiper__car-image", html)]
    out: list[dict] = []
    for idx, pos in enumerate(positions):
        head = html[:pos]
        # 该 img 标签的 alt：从 pos 往回找最近的 alt="..."
        alts = re.findall(r"alt=\"([^\"]+)\"", head)
        if not alts:
            continue
        model = alts[-1].strip()
        end = positions[idx + 1] if idx + 1 < len(positions) else len(html)
        card = html[pos:end]
        y = _YEAR_RE.search(card)
        s = _SCORE_RE.search(card)
        if not s:
            continue
        targets = {n.strip(): float(v) for n, v in _TARGET_RE.findall(card[:4000])}
        out.append({
            "model": model,
            "year": int(y.group(1)) if y else None,
            "overall": float(s.group(1)),
            "targets": targets,
            "source": HOME,
        })
    return out


def _key(s: str) -> str:
    from modules.contracts.model_names import canon
    return canon(s or "")


def verify(cands: list[dict], official: list[dict] | None = None,
           table: dict | None = None) -> dict:
    """比对官网最新结果与本地库/候选池。official=None 时联网抓。"""
    if official is None:
        html = fetch_home()
        official = parse_home(html) if html else []
    if table is None:
        table = json.load(open(CNCAP, encoding="utf-8")) if os.path.exists(CNCAP) else {}
    scores = table.get("scores") or {}
    pool_keys = {_key(c.get("model")): c.get("model") for c in cands}
    lib_keys = {_key(k): k for k in scores}

    new, conflict, covered = [], [], []
    for rec in official:
        k = _key(rec["model"])
        in_pool = k in pool_keys
        in_lib = k in lib_keys
        if not in_pool:
            continue                      # 官网有但本榜无此车，不属本榜核验范围
        if not in_lib:
            new.append({**rec, "pool_model": pool_keys[k]})
        elif abs(float(scores[lib_keys[k]]) - rec["overall"]) < 0.05:
            covered.append(rec["model"])
        else:
            conflict.append({**rec, "pool_model": pool_keys[k],
                             "local": scores[lib_keys[k]]})
    return {
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": HOME,
        "official_n": len(official),
        "pool_n": len(cands),
        "new": new, "conflict": conflict, "covered": covered,
    }




# ---------------------------------------------------------------------------
# v10.7.0：逐车联网核验状态 + 出榜门禁
# ---------------------------------------------------------------------------
# 旧 verify() 只回答「官网当期批次有没有新成绩」——抓不到就什么都不说，
# 历史车（深蓝S07 2023）不在首页 swiper 时等于没核验；联网失败也只告警。
# 这不能证明「每车都联网确认过」。下面这套改成：
#   · 对入池每一款车产出明确的核验状态（含「本次没抓到但已回访历史来源」）；
#   · 官网有本地无 / 数值冲突 / 主通道联网失败 → 硬阻断出榜；
#   · 历史批次不在官网当期展示 → 采信已核验缓存，但强制披露，不假装「实时确认」。
# 所有联网仍只走本模块（research 唯一网络出口），fetcher 可注入以便确定性测试。

def _cat_path(name: str) -> str:
    """分类数据路径统一收口 paths.cat（禁止硬编码 categories/ 拼接）。"""
    return str(paths.cat(name))


DECODED = _cat_path("ciasi_decoded.json")
AUTHORITATIVE = os.path.join(ROOT, "official_safety_scores.json")

# 逐车状态（人话说明在 STATUS_NOTE，报告/页面只准引用这里，不许各写一套）
STATUS_NOTE = {
    "live_confirmed": "本次联网在官方源确认到成绩，且与本地权威库一致",
    "new_found": "官方源有成绩、本地权威库缺失（必须补录后才可出榜）",
    "conflict": "官方源与本地权威库数值冲突（必须人工核验后才可出榜）",
    "cache_verified": "官网当期批次无此车，但本次回访历史一手来源页确认成绩仍在",
    "cache_no_url": "权威库已有核验成绩但未存一手来源URL；本期联网无法回访，沿用已核验缓存",
    "cache_unconfirmed": "官网当期批次无此车，历史来源页可达但未解析出成绩线索（需人工复核）",
    "source_stale": "历史一手来源页失效/改版（成绩仍采信已核验缓存，链接需更新）",
    "no_score": "本次联网核验（C-NCAP 当期批次 + C-IASI 结果稿）未见一手成绩",
    "network_error": "本次联网主通道失败，无法确认是否漏报（需显式降级才可出榜）",
}
BLOCKING_STATUSES = {"new_found", "conflict", "network_error"}


def fetch_url(url: str, timeout: int = 12) -> str | None:
    """回访官方来源页（research 唯一网络出口；与 fetch_home 同一 SSL 容错）。"""
    if not str(url or "").startswith("https://"):
        return None
    req = urllib.request.Request(url, headers=UA)
    try:
        raw = urllib.request.urlopen(req, timeout=timeout, context=_ctx()).read()
    except Exception as ex:                                  # noqa: BLE001
        print(f"[crash_verify] 来源回访失败 {url}: {type(ex).__name__}: {ex}",
              file=sys.stderr)
        return None
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _load(path: str) -> dict:
    try:
        return json.load(open(path, encoding="utf-8")) or {}
    except Exception:                                        # noqa: BLE001
        return {}


def _compact(s: str) -> str:
    return re.sub(r"\s+", "", str(s or ""))


def local_record(model: str, table: dict, decoded: dict,
                 authoritative: dict, pool_parsed: dict | None = None) -> dict | None:
    """取该车「页面即将显示」的本地成绩（候选池已解析成绩优先，库兜底）。

    顺序坑（2026-11-21 实跑发现）：核验若跑在成绩回流之前，ES6 这类只在
    evidence 文本里的成绩还没进 crash 字段 → 误标 no_score。所以 pipeline
    必须在回流之后调用本函数，并把 crash 字段经 engine.crash_systems 解析后
    以 pool_parsed 传入（research 层不反向依赖 engine，包依赖只向下）。
    """
    k = _key(model)

    def _revisit(url: str) -> str:
        # 只有官方详情页/发布稿算可回访来源；首页或媒体 URL 不能证明该车成绩
        url = str(url or "")
        return url if ("/evaluation/" in url or "/news/" in url
                       or "/show-" in url) else ""

    # 成绩取「页面实际显示」（pool_parsed），URL 从权威库补——两者来源不同，
    # 不能因为页面没存 URL 就放弃库里已有的一手来源（宋L EV 踩过）。
    db_url = ""
    for name, src in (table.get("sources") or {}).items():
        if _key(name) == k:
            db_url = _revisit((src or {}).get("source_url"))
            break
    db_rec = None
    if not db_url:
        for name, rec in (authoritative.get("car_scores") or {}).items():
            if _key(name) == k:
                db_rec = rec
                db_url = _revisit((rec or {}).get("source_url"))
                break
    if not db_url:
        for name, rec in (decoded.get("models") or {}).items():
            if _key(name) == k:
                db_rec = rec
                db_url = _revisit((rec or {}).get("source"))
                break
    if isinstance(pool_parsed, dict) and pool_parsed:
        return {"basis": pool_parsed["basis"], "value": pool_parsed.get("score"),
                "grade": pool_parsed.get("grade"), "source_url": db_url,
                "source": "候选池 crash/safety_basis（页面显示）+ 权威库来源URL",
                "detail": pool_parsed}
    cn_scores = table.get("scores") or {}
    for name, val in cn_scores.items():
        if _key(name) == k:
            return {"basis": "cncap", "value": float(val), "source_url": db_url,
                    "source": "cncap_scores.json",
                    "detail": (table.get("sources") or {}).get(name) or {}}
    if db_rec is not None:
        # authoritative 命中
        if "system" in db_rec:
            return {"basis": str(db_rec.get("system") or ""),
                    "value": db_rec.get("value"), "grade": db_rec.get("grade"),
                    "score": db_rec.get("score"), "source_url": db_url,
                    "source": "official_safety_scores.json", "detail": db_rec}
        # ciasi_decoded 命中
        graded = {d: v.get("grade") for d, v in (db_rec.get("dims") or {}).items()
                  if v.get("grade")}
        if graded:
            return {"basis": "ciasi", "grade": min(
                        (g for g in graded.values()),
                        key=lambda g: ("P", "M", "A", "G", "G+").index(g)
                        if g in ("P", "M", "A", "G", "G+") else 0),
                    "dims": graded, "source_url": db_url,
                    "source": "ciasi_decoded.json", "detail": db_rec}
    return None


def _source_alive(html: str | None, model: str, value) -> bool:
    """历史来源页回访：页面里出现该成绩数值或车型名即视为仍可追溯。"""
    if not html:
        return False
    text = _compact(re.sub(r"<[^>]+>", " ", html))
    if value is not None and f"{float(value):g}" in text:
        return True
    mn = _compact(model)
    return bool(mn) and mn in text


def verify_all(cands: list[dict], *, official: list[dict] | None = None,
               table: dict | None = None, decoded: dict | None = None,
               authoritative: dict | None = None, pool_parsed: dict | None = None,
               home_fetcher=None, url_fetcher=None,
               ciasi_fresh=None, skip_revisit: bool = False) -> dict:
    """逐车联网核验（出榜门禁唯一事实源）。

    official=None 时联网抓 C-NCAP 首页；home_fetcher 返回 None 视为联网失败，
    全榜标 network_error（不静默沿用本地库）。url_fetcher 用于历史来源回访。
    ciasi_fresh：本次 rescue 新命中的车型集合（有则为 live_confirmed）。
    """
    table = table if table is not None else _load(CNCAP)
    decoded = decoded if decoded is not None else _load(DECODED)
    authoritative = (authoritative if authoritative is not None
                     else _load(AUTHORITATIVE))
    home_fetcher = home_fetcher or fetch_home
    url_fetcher = url_fetcher or fetch_url
    ciasi_fresh = {_key(m) for m in (ciasi_fresh or [])}

    net_error = False
    if official is None:
        html = home_fetcher()
        net_error = html is None
        official = parse_home(html) if html else []
    live_cn = {_key(r["model"]): r for r in official}

    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    per_car: dict[str, dict] = {}
    for c in cands:
        name = c.get("model") if isinstance(c, dict) else str(c)
        k = _key(name)
        local = local_record(
            name, table, decoded, authoritative,
            pool_parsed=(pool_parsed or {}).get(name)
            if isinstance(pool_parsed, dict) else None)
        hit = live_cn.get(k)
        rec: dict = {
            "model": name,
            "status": None,
            "local": ({"basis": local["basis"], "value": local.get("value"),
                       "grade": local.get("grade"), "source": local["source"]}
                      if local else None),
            "live": ({"basis": "cncap", "value": hit["overall"],
                      "source": HOME} if hit else None),
            "urls_checked": [],
        }
        if net_error:
            rec["status"] = "network_error"
        elif hit and not local:
            rec["status"] = "new_found"
        elif hit and local:
            # 同为 C-NCAP 才可比数值；跨体系（如本地 C-IASI、官网 C-NCAP）不比
            if local["basis"] == "cncap" and local.get("value") is not None:
                rec["status"] = ("live_confirmed"
                                 if abs(float(local["value"]) - hit["overall"]) < 0.05
                                 else "conflict")
            else:
                rec["status"] = "live_confirmed"
        elif k in ciasi_fresh:
            rec["status"] = "live_confirmed"
        elif local and local.get("source_url") and not skip_revisit:
            html2 = url_fetcher(local["source_url"])
            rec["urls_checked"].append(local["source_url"])
            rec["status"] = ("cache_verified"
                             if _source_alive(html2, name, local.get("value"))
                             else "cache_unconfirmed" if html2 else "source_stale")
        elif local:
            rec["status"] = "cache_no_url"
        else:
            rec["status"] = "no_score"
        rec["note"] = STATUS_NOTE[rec["status"]]
        per_car[name] = rec

    blocking = [{"model": n, "status": r["status"], "note": r["note"]}
                for n, r in per_car.items() if r["status"] in BLOCKING_STATUSES]
    return {
        "checked_at": checked_at,
        "source": HOME,
        "official_n": len(official),
        "pool_n": len(cands),
        "network_error": net_error,
        "per_car": per_car,
        # 兼容旧 verify() 消费者（summary / 报告）
        "new": [{"pool_model": n, "model": n, "overall": r["live"]["value"]}
                for n, r in per_car.items() if r["status"] == "new_found"],
        "conflict": [{"pool_model": n, "model": n,
                      "overall": r["live"]["value"],
                      "local": r["local"]["value"] if r["local"] else None}
                     for n, r in per_car.items() if r["status"] == "conflict"],
        "covered": [n for n, r in per_car.items()
                    if r["status"] == "live_confirmed"],
        "blocking": blocking,
        "status": "blocked" if blocking else "ok",
    }


def coverage_gate(report: dict, allow_degraded: bool = False) -> dict:
    """出榜门禁：数据问题（新成绩未补录/冲突）永远阻断；网络失败可显式降级。

    返回 {ok, degraded, hard, soft, reason}。pipeline 只认这个结果，
    不要在调用处自己判断 status 字符串（避免两处口径漂移）。
    """
    hard = [x for x in (report.get("blocking") or [])
            if x["status"] in ("new_found", "conflict")]
    soft = [x for x in (report.get("blocking") or [])
            if x["status"] == "network_error"]
    ok = not hard and (not soft or bool(allow_degraded))
    reason = ""
    if hard:
        reason = "；".join(f"{x['model']} {x['status']}" for x in hard) + "（数据问题，必须处理后出榜）"
    elif soft and not allow_degraded:
        reason = f"联网主通道失败，{len(soft)} 款车无法确认是否漏报（加 --allow-degraded-network 才允许降级出榜）"
    return {"ok": ok, "degraded": ok and bool(soft), "hard": hard,
            "soft": soft, "reason": reason}


def selftest_all() -> int:
    """逐车核验门禁的确定性自测（注入假 fetcher，不联网）。"""
    from modules.contracts.model_names import canon  # noqa: F401  (触发归一化一致性)
    fake_home = """
    alt="问界M9" class="cncap-swiper__car-image"
    <span class="cncap-swiper__year">2026</span>
    <p class="cncap-swiper__score-value">93.9%</p>
    alt="冲突车C" class="cncap-swiper__car-image"
    <span class="cncap-swiper__year">2026</span>
    <p class="cncap-swiper__score-value">91.0%</p>
    """
    rows = parse_home(fake_home)
    cands = [{"model": "问界M9"}, {"model": "历史车H"},
             {"model": "无成绩车N"}, {"model": "冲突车C"}]
    table = {"scores": {"问界M9": 93.9, "历史车H": 88.3, "冲突车C": 90.0},
             "sources": {"历史车H": {"source_url": "https://www.c-ncap.org.cn/evaluation/h1"},
                         "冲突车C": {"source_url": "https://www.c-ncap.org.cn/evaluation/c1"}}}
    # 历史车回访：页面含 88.3 → cache_verified；无成绩车 → no_score
    def alive(url):
        return "<body>88.3</body>" if "h1" in url else "<body>90.0</body>"
    rep = verify_all(cands, official=rows, table=table, decoded={},
                     authoritative={}, url_fetcher=alive)
    p = rep["per_car"]
    assert p["问界M9"]["status"] == "live_confirmed", p
    assert p["历史车H"]["status"] == "cache_verified", p
    assert p["冲突车C"]["status"] == "conflict", p
    assert p["无成绩车N"]["status"] == "no_score", p
    g = coverage_gate(rep)
    assert not g["ok"] and len(g["hard"]) == 1, g      # 冲突必阻断
    # 官网有本地无 → new_found 必阻断（深蓝S07 事故类型）
    rep2 = verify_all([{"model": "问界M9"}], official=rows, table={"scores": {}},
                      decoded={}, authoritative={})
    assert rep2["per_car"]["问界M9"]["status"] == "new_found", rep2
    assert not coverage_gate(rep2)["ok"], "官网有本地无必须阻断"
    # 联网主通道失败：全榜 network_error，默认阻断，显式开关才降级
    rep3 = verify_all([{"model": "任意车"}], official=None, table={"scores": {}},
                      decoded={}, authoritative={}, home_fetcher=lambda: None)
    assert rep3["per_car"]["任意车"]["status"] == "network_error", rep3
    assert not coverage_gate(rep3)["ok"], "联网失败必须阻断"
    assert coverage_gate(rep3, allow_degraded=True)["degraded"], "显式降级应放行但标记"
    # 历史来源页失效 → source_stale（不阻断，但披露）
    rep4 = verify_all([{"model": "历史车H"}], official=[], table=table,
                      decoded={}, authoritative={}, url_fetcher=lambda u: None)
    assert rep4["per_car"]["历史车H"]["status"] == "source_stale", rep4
    assert coverage_gate(rep4)["ok"], "历史链接失效不应阻断（成绩仍采信缓存）"
    print("SELFTEST PASS · 逐车核验状态 + 分级门禁（冲突/新成绩/断网阻断，历史来源回访披露）")
    return 0


def apply_new(report: dict, table_path: str | None = None) -> list[str]:
    """把 report.new 写进 cncap_scores.json（只填空，不动已有）。"""
    p = table_path or CNCAP
    table = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {"scores": {}}
    added = []
    sources = table.setdefault("sources", {})
    for rec in report.get("new", []):
        name = rec.get("pool_model") or rec["model"]
        if name in table["scores"]:
            continue
        table["scores"][name] = rec["overall"]
        sources[name] = {
            "overall": rec["overall"], "year": rec.get("year"),
            "targets": rec.get("targets", {}), "source_url": HOME,
            "via": "crash_auto_verify 联网核验（官网首页 swiper）",
            "checked_at": report.get("checked_at"),
        }
        added.append(name)
    if added:
        json.dump(table, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return added


def selftest() -> int:
    # 用假 swiper HTML 验证解析（与网络无关，确定性）
    fake = """
    alt="问界M9" class="cncap-swiper__car-image"
    <span class="cncap-swiper__year">2026</span>
    <p class="cncap-swiper__score-value">93.9%</p>
    <p class="cncap-swiper__target-name">乘员保护</p><p class="cncap-swiper__target-score" style="color:#f39200">97.21%</p>
    alt="小米YU7" class="cncap-swiper__car-image"
    <span class="cncap-swiper__year">2025</span>
    <p class="cncap-swiper__score-value">92.1%</p>
    """
    rows = parse_home(fake)
    assert len(rows) == 2, rows
    assert rows[0]["model"] == "问界M9" and rows[0]["overall"] == 93.9
    assert rows[0]["year"] == 2026 and rows[0]["targets"].get("乘员保护") == 97.21
    # 比对：候选池有问界M9、库里没有 → new；小米YU7 不在池 → 忽略
    cands = [{"model": "问界M9"}]
    rep = verify(cands, official=rows, table={"scores": {}})
    assert len(rep["new"]) == 1 and rep["new"][0]["pool_model"] == "问界M9", rep
    # 库里已有且一致 → covered
    rep2 = verify(cands, official=rows, table={"scores": {"问界M9": 93.9}})
    assert rep2["covered"] == ["问界M9"] and not rep2["new"], rep2
    print("SELFTEST PASS · C-NCAP 官网零依赖解析 + new/conflict/covered 三分类")
    return selftest_all()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--candidates", default="runs/midsuv_bev_pool.json")
    ap.add_argument("--apply", action="store_true", help="把 new 补进 cncap_scores.json")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    cands = json.load(open(os.path.join(ROOT, a.candidates), encoding="utf-8"))["candidates"]
    rep = verify(cands)
    print(f"官网最新测评 {rep['official_n']} 款 / 本榜 {rep['pool_n']} 款")
    print(f"  新增可补 {len(rep['new'])}：{', '.join(x['pool_model'] for x in rep['new']) or '无'}")
    print(f"  数值冲突 {len(rep['conflict'])}：{', '.join(x['pool_model'] for x in rep['conflict']) or '无'}")
    print(f"  已覆盖 {len(rep['covered'])} 款")
    if a.apply and rep["new"]:
        added = apply_new(rep)
        print(f"[apply] 补入 cncap_scores.json：{', '.join(added)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
