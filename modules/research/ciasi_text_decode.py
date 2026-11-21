#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C-IASI 官方测评结果「文字稿」解码器（零第三方依赖）。

背景
----
手册长期标注「C-IASI 等级图标未解码」，原因是结果页用图标展示 G/A/M/P。
但 C-IASI 官网的「测评结果发布」新闻稿（/news/show-N.html）是**纯文字**：
  「车内乘员安全分指数，2 款车型的评价结果均为优秀+（G+）。」
  「耐撞性与维修经济性分指数，2 款车型均获得一般（M）评价。」
因此不需要 OCR / XHR 逆向，直接解码新闻稿即可拿到逐车逐维度等级。

额外价值
--------
「耐撞性与维修经济性」分指数（G/A/M/P）正是「可维修性」的官方证据，
可补电池榜/安全列此前的「无官方口径」空白。

纪律（与项目红线一致）
----------------------
1. 只作事实列 / 证据存在性与强度，**不进加权分**（SCORING_ENABLED 不因本脚本改变）。
2. 同车型多次测评以**最新发布**覆盖，并保留 releases 溯源。
3. 「均获得 X」是全车型同等级；逐车不同时按车型名就近匹配等级词。
4. 稿件里的「得分方面获得优秀评价」等细分描述**不**覆盖主分指数等级。
5. 未匹配到车型的等级词一律丢弃并计入 unmatched，不猜、不填充。

用法
----
  python3 ciasi_text_decode.py --out data/gen/ciasi_decoded.json          # 全量刷新（自动向上找新稿）
  python3 ciasi_text_decode.py --end 132 --out data/gen/ciasi_decoded.json
  python3 ciasi_text_decode.py --cars "某车A,某车B" --merge              # 只救指定车，合并进随包事实库
  python3 ciasi_text_decode.py --targets-from runs/midsuv_bev_pool.json --merge
  python3 ciasi_text_decode.py --selftest

出榜链路调用（自动兜底）
------------------------
pipeline 发现候选池里有「C-IASI 数字图标/图标未解码」且随包事实库没有该车的文字等级时，
会调用本模块的 rescue_targets()，联网解码结果发布稿并合并回事实库；已缓存车型不重复联网。
失败只告警、不阻断出榜（继续用已缓存事实），避免网络抖动拖垮本地榜单生成。
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import ssl
import sys
import time
import urllib.request
from datetime import datetime

BASE = "https://www.ciasi.org.cn/news/show-{}.html"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}

# 官方文字等级 → 代码（保留 G+ 与 G 的区别）
GRADE_RE = re.compile(r"(优秀\+|优秀|良好|一般|较差)（([GAMP]\+?)）")
GRADE_CN = {"优秀+": "G+", "优秀": "G", "良好": "A", "一般": "M", "较差": "P"}

# 五个分指数在新闻稿中的键名片段（顺序即输出顺序）
DIMS = [
    ("crash_repair", "耐撞性与维修经济性"),
    ("occupant", "车内乘员安全"),
    ("pedestrian", "车外行人安全"),
    ("assist", "车辆辅助安全"),
    ("ev_special", "新能源汽车专项"),
]

TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
PUBLISH_RE = re.compile(r"发布时间[：:]\s*([0-9]{4}-[0-9]{2}-[0-9]{2})")
# 两种措辞：「分别为：A、B、C」/「包括“A、B、C及D”」
MODELS_RE = re.compile(
    r"共涉及\s*(\d+)\s*款车型[^。]*?(?:分别为[：:]|包括)\s*([^。]+)")
ALL_COUNT_RE = re.compile(r"([0-9]+)\s*款车型")
NOISE_RE = re.compile(r"^(且均为|其中\d*款为|包含\d*款|\d*款为|分别为[：:]|包括)")


def strip_html(s: str) -> str:
    s = re.sub(r"<script.*?</script>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<style.*?</style>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&")
           .replace("&ldquo;", "“").replace("&rdquo;", "”")
           .replace("&quot;", '"').replace("&#39;", "'"))
    return re.sub(r"\s+", " ", s).strip()


def _ssl_ctx():
    """C-IASI 官网证书在部分网络出口（如本地代理）上报 CERTIFICATE_VERIFY_FAILED。
    这里只对本解码器的官网请求放宽证书校验；抓的仅是官方公开文字稿，
    且解码纪律要求「未匹配不猜、只作事实列」，不涉及任何账号/隐私数据传输。
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def fetch_url(url: str, timeout: int = 10) -> str | None:
    """唯一网络出口：所有 C-IASI 新闻稿抓取都走这里（含自动探测新稿 ID）。"""
    req = urllib.request.Request(url, headers=UA)
    try:
        raw = urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()).read()
    except Exception:
        return None
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def fetch(sid: int, timeout: int = 10) -> str | None:
    return fetch_url(BASE.format(sid), timeout)


# 自动探测参数：从已知最大 ID 往上找新稿，连续 MAX_MISSES 个无结果稿才停。
# 之所以不用「新闻列表页」：官网栏目 URL 多次改版（news.html / news/index.html 已 404），
# show-N 详情页稳定；每秒 0.15 次请求对公开官网足够轻，且只在有新目标车时才跑。
AUTO_MAX_SCAN = 400
AUTO_MAX_MISSES = 30


def compact(s: str) -> str:
    """归一化：去所有空白，用于「特斯拉Model Y」vs「特斯拉 Model Y」匹配。"""
    return re.sub(r"\s+", "", s or "")


def find_pos(sentence: str, model: str) -> int:
    i = sentence.find(model)
    if i >= 0:
        return i
    cs, cm = compact(sentence), compact(model)
    i = cs.find(cm)
    if i < 0:
        return -1
    # 映射回原始下标：按紧凑串中的字符位置在原文里顺序推进
    idx = 0
    seen = 0
    for j, ch in enumerate(sentence):
        if not ch.isspace():
            if seen == i:
                return j
            seen += 1
    return -1


def parse_release(html: str, sid: int) -> dict | None:
    """解析单篇结果发布稿；非结果稿返回 None。"""
    m = TITLE_RE.search(html)
    title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    if "结果发布" not in title:
        return None
    text = strip_html(html)
    grades_all = GRADE_RE.findall(text)
    if not grades_all:
        return None

    pm = MODELS_RE.search(text)
    models: list[str] = []
    declared = None
    if pm:
        declared = int(pm.group(1))
        raw = pm.group(2).strip("“”\"' ")
        # 「及」也是连接词（如 尚界H5及方程豹钛7）
        raw = raw.replace("及", "、")
        for x in re.split(r"[、，,]", raw):
            x = x.strip("“”\"' ")
            # 去掉尾部说明：「且均为新能源SUV」「其中1款为新能源车（…）」
            x = re.split(r"\s*(?:且|其中|包含|，)", x)[0].strip()
            # 剔除「2款SUV」「6款轿车」「1款MPV」这类分类计数 token
            if re.fullmatch(r"\d+款(SUV|轿车|MPV|车型|新能源车|燃油车)", x):
                x = ""
            if x and not NOISE_RE.match(x) and len(x) <= 24:
                models.append(x)
    if not models:  # 兜底：从「分别为：/包括」后取到句号
        m2 = re.search(r"(?:分别为[：:]|包括)\s*([^。]+)", text)
        if m2:
            models = [x.strip("“”\"' ") for x in re.split(r"[、，,]及?", m2.group(1))
                      if x.strip("“”\"' ")]
    # 声明数量与解析数量不符时，如实记录、不猜补
    count_mismatch = (declared is not None and len(models) != declared)
    if not models:
        return None

    # 逐维度解析
    result: dict[str, dict] = {}
    unmatched: list[str] = []
    for key, cn in DIMS:
        # 取包含维度名的句子（主等级通常在第一句）
        sentence = ""
        for s in re.split(r"(?<=[。！])", text):
            if cn in s and GRADE_RE.search(s):
                sentence = s
                break
        if not sentence:
            continue
        hits = list(GRADE_RE.finditer(sentence))
        # 「N款均X」=全员同等级；「N款X，M款Y」=只披露分布、未点名车型 → aggregate
        all_n = ALL_COUNT_RE.findall(sentence)
        distinct = {h.group(2) for h in hits}
        universal = ("均" in sentence) and len(distinct) == 1
        aggregate = (len(distinct) > 1 and len(all_n) > 1
                     and not any(find_pos(sentence, m) >= 0 for m in models))
        # 「均为X及以上，其中N款Y」：主等级 X 是下限，逐车细分未点名
        lower_bound = bool(re.search(r"及以上|不低于", sentence)) and len(distinct) > 1

        if aggregate or (lower_bound and not any(find_pos(sentence, m) >= 0 for m in models)):
            for model in models:
                result.setdefault(model, {})[key] = {
                    "grade": None, "cn": None,
                    "scope": "aggregate_only" if aggregate else "lower_bound_only",
                    "published_distribution": [h.group(2) for h in hits],
                    "note": "稿件仅披露等级分布/下限，未点名对应车型——不猜、不套用每车",
                }
            continue

        for model in models:
            if universal:
                result.setdefault(model, {})[key] = {
                    "grade": hits[0].group(2), "cn": hits[0].group(1),
                    "scope": "all",
                }
                continue
            pos = find_pos(sentence, model)
            if pos < 0:
                continue
            nxt = [h for h in hits if h.start() > pos]
            if nxt:
                result.setdefault(model, {})[key] = {
                    "grade": nxt[0].group(2), "cn": nxt[0].group(1),
                    "scope": "per_model",
                }
        if not universal and len(hits) > len(models):
            unmatched.append(cn)

    d = PUBLISH_RE.search(text)
    published = d.group(1) if d else ""
    ver = re.search(r"依据[^。]*?(\d{4}版规程)", text)
    return {
        "id": sid,
        "url": BASE.format(sid),
        "title": title,
        "published": published,
        "rule": ver.group(1) if ver else None,
        "declared_models": declared,
        "count_mismatch": count_mismatch,
        "models_found": models,
        "decoded": result,
        "unmatched_dims": unmatched,
    }


def collect_releases(start: int, end: int | None = None, sleep: float = 0.15,
                     fetcher=fetch, max_misses: int = AUTO_MAX_MISSES,
                     verbose: bool = True) -> tuple[list[dict], list[int]]:
    """扫描 show-N 详情页，返回 (结果发布稿列表, 实际扫描过的 ID 列表)。

    end=None 表示自动向上找新稿：连续 max_misses 个 ID 不是结果发布稿才停。
    这样以后官网新增 show-133/134/… 不需要改代码或手记 ID 范围。
    fetcher 可注入，便于回归测试用假 HTML 验证「新车型也能解析」。
    """
    releases: list[dict] = []
    scanned: list[int] = []
    misses = 0
    sid = start
    hard_cap = (end if end is not None else start + AUTO_MAX_SCAN)
    while sid <= hard_cap and len(scanned) < AUTO_MAX_SCAN:
        html = fetcher(sid)
        scanned.append(sid)
        rel = parse_release(html, sid) if html else None
        if rel:
            releases.append(rel)
            misses = 0
            if verbose:
                print(f"  ✓ show-{sid}: {rel['published']} · {len(rel['models_found'])} 款")
        else:
            misses += 1
        if end is None and misses >= max_misses:
            break
        sid += 1
        if sleep:
            time.sleep(sleep)
    return releases, scanned


def _build_dataset(releases: list[dict], scanned: list[int]) -> dict:
    """结果发布稿列表 → 随包事实库结构（同车型按发布时间/ID 取最新）。"""
    latest: dict[str, dict] = {}
    for rel in sorted(releases, key=lambda r: (r["published"], r["id"])):
        for model, dims in rel["decoded"].items():
            latest[model] = {
                "dims": dims,
                "source": rel["url"],
                "published": rel["published"],
                "rule": rel["rule"],
            }
    rng = [min(scanned), max(scanned)] if scanned else [0, 0]
    return {
        "note": "C-IASI 官方测评结果发布稿文字解码（非图标 OCR）。只作事实列/证据强度，不进加权分。",
        "source_site": "https://www.ciasi.org.cn/",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "method": "新闻稿纯文字 G/G+/A/M/P 解码；同车型取最新发布；未匹配不猜",
        "discipline": [
            "不进加权分（SCORING_ENABLED 不受影响）",
            "「耐撞性与维修经济性」为官方可维修性事实列，非产品力评分",
            "细分工况描述（如碰撞兼容性）不覆盖主分指数等级",
        ],
        "scan_range": rng,
        "n_releases": len(releases),
        "n_models": len(latest),
        "releases": [{k: v for k, v in r.items() if k != "decoded"} for r in releases],
        "models": latest,
    }


def resolve_all(start: int, end: int | None = None, sleep: float = 0.15,
                fetcher=fetch, max_misses: int = AUTO_MAX_MISSES) -> dict:
    """兼容旧入口；默认自动探测新稿，不再依赖手写的 end=130。"""
    releases, scanned = collect_releases(start, end, sleep, fetcher, max_misses)
    return _build_dataset(releases, scanned)


def merge_decoded(base: dict | None, extra: dict | None) -> dict:
    """把新解码结果合并进事实库；同车型按 published 取最新，不丢旧车型。"""
    out = copy.deepcopy(base or {})
    models = dict(out.get("models") or {})
    releases = {r.get("id"): r for r in (out.get("releases") or []) if r.get("id")}
    for name, rec in ((extra or {}).get("models") or {}).items():
        prev = models.get(name)
        if not prev or str(rec.get("published") or "") >= str(prev.get("published") or ""):
            models[name] = rec
    for r in ((extra or {}).get("releases") or []):
        if r.get("id"):
            releases[r["id"]] = r
    out["models"] = models
    out["releases"] = list(releases.values())
    out["n_models"] = len(models)
    out["n_releases"] = len(releases)
    rng = list(out.get("scan_range") or [0, 0])
    xrng = list((extra or {}).get("scan_range") or [0, 0])
    if xrng[0] and xrng[1]:
        out["scan_range"] = [min(rng[0], xrng[0]), max(rng[1], xrng[1])]
    out["merged_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return out


def _key(s: str) -> str:
    from modules.contracts.model_names import canon
    return canon(s or "")


def targets_from_candidates(candidates: list[dict]) -> list[str]:
    """找出候选池里「有 C-IASI 记录但数字图标/等级未文字化」的车。

    只作触发条件，不在这里猜成绩：命中后交给 rescue_targets() 联网解新闻稿。
    """
    out: list[str] = []
    for c in candidates or []:
        ev = c.get("evidence") if isinstance(c.get("evidence"), dict) else {}
        txt = " ".join(str(c.get(k) or "") for k in ("crash", "note", "safety_note"))
        txt += " " + str(ev.get("crash_evidence") or "")
        if not re.search(r"C\s*-?\s*IASI|中保研|level1/|等级图标|数字图标", txt, re.I):
            continue
        if re.search(r"level1/[^\s]+\.png|图标未解码|数字图标|图标等级", txt, re.I):
            out.append(c.get("model"))
            continue
        # 「无C-NCAP/C-IASI」这类否定句不是数字图标车，不能触发联网
        if re.search(r"(?:无|未取得|未查到|本代无|非本代)[^。；]{0,12}C\s*-?\s*IASI|"
                     r"C\s*-?\s*IASI[^。；]{0,8}(?:无成绩|无官方|未测)", txt, re.I):
            continue
        # 有 C-IASI 锚点但文字里没有维度等级 → 需新闻稿救回
        if not re.search(r"(?:乘员|行人|辅助)[^\d]{0,8}[GAMP]\+?", txt, re.I):
            out.append(c.get("model"))
    return sorted({m for m in out if m})


def rescue_targets(targets: list[str], decoded: dict | None = None,
                   out_path: str | None = None, start: int = 100,
                   end: int | None = None, sleep: float = 0.05,
                   fetcher=fetch, max_misses: int = AUTO_MAX_MISSES,
                   force: bool = False) -> dict:
    """通用救回入口：联网解码新闻稿 → 只取指定车型 → 合并回事实库。

    - targets 可以是任意车型列表（不硬编码 Model Y / bZ5）；
    - 已在事实库中的车型默认跳过，避免每次出榜都联网；
    - force=True 时重扫（用于人工刷新）；
    - 返回 matched/unmatched，失败由调用方决定是告警还是阻断。
    """
    base = decoded
    if base is None and out_path and os.path.exists(out_path):
        try:
            base = json.load(open(out_path, encoding="utf-8"))
        except Exception:
            base = None
    have = {_key(k) for k in (base or {}).get("models", {}).keys()}
    want = [m for m in dict.fromkeys(targets or []) if m and (force or _key(m) not in have)]
    report = {"targets": list(targets or []), "pending": want, "matched": {},
              "unmatched": [], "skipped_cached": [m for m in (targets or []) if m not in want]}
    if not want:
        report["status"] = "all_cached"
        return report
    releases, scanned = collect_releases(start, end, sleep, fetcher, max_misses,
                                         verbose=False)
    fresh = _build_dataset(releases, scanned)
    want_keys = {_key(m) for m in want}
    matched: dict[str, dict] = {}
    for name, rec in fresh.get("models", {}).items():
        if _key(name) in want_keys:
            graded = {k: v for k, v in (rec.get("dims") or {}).items()
                      if v.get("grade") and v.get("scope") in ("all", "per_model")}
            if graded:
                matched[name] = {**rec, "dims": graded}
    report["matched"] = matched
    report["unmatched"] = [m for m in want if _key(m) not in {_key(x) for x in matched}]
    report["status"] = "ok" if matched else "no_match"
    report["scanned"] = scanned
    merged = merge_decoded(base, {"models": matched, "releases": [], "scan_range": [start, scanned[-1] if scanned else start]})
    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        json.dump(merged, open(out_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        report["out"] = out_path
    return report


def selftest() -> int:
    sample = """
    <html><head><title>中国保险汽车安全指数（C-IASI）2025年测评车型第六次结果发布-C-IASI新闻</title></head>
    <body>发布时间： 2026-09-29
    本次结果发布共涉及2款车型，分别为：特斯拉Model Y、享界S9T。
    测评结果显示，耐撞性与维修经济性分指数，2款车型均获得一般（M）评价。
    车内乘员安全分指数，2款车型的评价结果均为优秀+（G+）。
    车外行人安全分指数，2款车型的评价结果均为优秀+（G+）。
    车辆辅助安全分指数，特斯拉Model Y的评价结果为优秀（G），享界S9T车型的评价结果为优秀+（G+）。
    新能源汽车专项指数，2款车型均获得优秀（G）评价。
    </body></html>
    """
    r = parse_release(sample, 128)
    assert r is not None, "结果稿识别失败"
    assert set(r["models_found"]) == {"特斯拉Model Y", "享界S9T"}, r["models_found"]
    d = r["decoded"]
    assert d["特斯拉Model Y"]["crash_repair"]["grade"] == "M", d
    assert d["享界S9T"]["occupant"]["grade"] == "G+", d
    assert d["特斯拉Model Y"]["assist"]["grade"] == "G", d
    assert d["享界S9T"]["assist"]["grade"] == "G+", d
    assert d["特斯拉Model Y"]["ev_special"]["grade"] == "G", d

    # 通用救回：任意两款新车 + 自动向上探测新稿 ID（不硬编码具体车型/ID）
    newer = """
    <html><head><title>C-IASI 2026年测评车型第99次结果发布</title></head>
    <body>发布时间： 2026-11-20
    本次结果发布共涉及2款车型，分别为：测试甲EV、测试乙EV。
    车内乘员安全分指数，2款车型的评价结果均为良好（A）。
    车外行人安全分指数，2款车型的评价结果均为优秀（G）。
    车辆辅助安全分指数，测试甲EV的评价结果为优秀（G），测试乙EV车型的评价结果为一般（M）。
    </body></html>
    """
    def fake_fetch(sid):
        return newer if sid == 2 else None

    rels, scanned = collect_releases(1, None, 0, fake_fetch, max_misses=2)
    assert [r["id"] for r in rels] == [2] and scanned[-1] >= 4, (rels, scanned)
    rep = rescue_targets(["测试甲EV", "测试乙EV"], decoded={}, start=1, end=3,
                         sleep=0, fetcher=fake_fetch)
    assert set(rep["matched"]) == {"测试甲EV", "测试乙EV"}, rep
    assert rep["matched"]["测试乙EV"]["dims"]["assist"]["grade"] == "M", rep

    # 数字图标车识别：图标/未解码标记命中；没有文字等级时也要触发救援
    cands = [
        {"model": "未来新车X", "crash": "C-IASI level1/2.png 图标未解码", "evidence": {}},
        {"model": "未来新车Y", "crash": None,
         "evidence": {"crash_evidence": "有C-IASI官方成绩，但页面为数字图标，等级未文字化"}},
        {"model": "无碰撞车Z", "crash": "无官方成绩", "evidence": {}},
        {"model": "无IASI车W", "crash": "无C-NCAP/C-IASI", "evidence": {}},
    ]
    assert set(targets_from_candidates(cands)) == {"未来新车X", "未来新车Y"}, \
        targets_from_candidates(cands)
    print("SELFTEST PASS · 15 断言通过（含任意新车新闻稿救回+自动探测新ID+图标车识别）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", type=int, default=100)
    ap.add_argument("--end", type=int, default=None,
                    help="留空=自动向上探测新稿；给整数=兼容旧固定范围")
    # 默认输出随包事实库 categories/（随 skill 与 GitHub 分发）；data/gen 仅为缓存。
    ap.add_argument("--out", default="data/gen/ciasi_decoded.json")
    ap.add_argument("--sleep", type=float, default=0.15)
    ap.add_argument("--max-misses", type=int, default=AUTO_MAX_MISSES)
    ap.add_argument("--cars", help="只救指定车型，逗号分隔；仍会合并进 --out")
    ap.add_argument("--targets-from", help="候选池 JSON，自动找数字图标车（与 --cars 可叠加）")
    ap.add_argument("--merge", action="store_true", help="与原事实库合并（默认救回模式必合并）")
    ap.add_argument("--force", action="store_true", help="强制重扫已缓存车型")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    targets = [x.strip() for x in (a.cars or "").split(",") if x.strip()]
    if a.targets_from:
        pool = json.load(open(a.targets_from, encoding="utf-8"))
        cands = pool.get("candidates") or pool.get("cars") or pool.get("pool") or []
        targets += targets_from_candidates(cands)
    if targets:
        out = os.path.abspath(a.out)
        rep = rescue_targets(targets, out_path=out, start=a.start, end=a.end,
                             sleep=a.sleep, max_misses=a.max_misses, force=a.force)
        print(f"救回完成：目标 {len(rep['targets'])} 款 / 新扫 {len(rep.get('pending', []))} 款 / "
              f"命中 {len(rep['matched'])} 款 / 未命中 {len(rep['unmatched'])}")
        for name, rec in rep["matched"].items():
            print(f"  ✓ {name}: " + "、".join(
                f"{k}={v['grade']}" for k, v in rec["dims"].items()))
        print(f"输出：{out}")
        return 0

    rng = f"show-{a.start}..{a.end or 'auto'}"
    print(f"扫描 C-IASI 新闻稿 {rng} ...")
    data = resolve_all(a.start, a.end, a.sleep, max_misses=a.max_misses)
    if a.merge and os.path.exists(os.path.abspath(a.out)):
        base = json.load(open(os.path.abspath(a.out), encoding="utf-8"))
        data = merge_decoded(base, data)
    out = os.path.abspath(a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"完成：{data['n_releases']} 篇结果稿 / {data['n_models']} 款车型唯一解码")
    print(f"输出：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
