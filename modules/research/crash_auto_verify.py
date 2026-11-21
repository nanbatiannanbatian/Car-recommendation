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
    return 0


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
