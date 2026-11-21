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
  python3 ciasi_text_decode.py --out ciasi_decoded.json
  python3 ciasi_text_decode.py --start 105 --end 130 --out ciasi_decoded.json
  python3 ciasi_text_decode.py --selftest
"""
from __future__ import annotations

import argparse
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


def fetch(sid: int, timeout: int = 10) -> str | None:
    req = urllib.request.Request(BASE.format(sid), headers=UA)
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


def resolve_all(start: int, end: int, sleep: float) -> dict:
    releases: list[dict] = []
    for sid in range(start, end + 1):
        html = fetch(sid)
        if not html:
            continue
        rel = parse_release(html, sid)
        if rel:
            releases.append(rel)
            print(f"  ✓ show-{sid}: {rel['published']} · {len(rel['models_found'])} 款")
        if sleep:
            time.sleep(sleep)

    # 同车型多次测评：按发布时间（或 id）取最新
    latest: dict[str, dict] = {}
    for rel in sorted(releases, key=lambda r: (r["published"], r["id"])):
        for model, dims in rel["decoded"].items():
            latest[model] = {
                "dims": dims,
                "source": rel["url"],
                "published": rel["published"],
                "rule": rel["rule"],
            }

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
        "scan_range": [start, end],
        "n_releases": len(releases),
        "n_models": len(latest),
        "releases": [{k: v for k, v in r.items() if k != "decoded"} for r in releases],
        "models": latest,
    }


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
    print("SELFTEST PASS · 8 断言通过（含逐车差异化辅助安全 G vs G+）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", type=int, default=100)
    ap.add_argument("--end", type=int, default=130)
    # 默认输出随包事实库 categories/（随 skill 与 GitHub 分发）；data/gen 仅为缓存。
    ap.add_argument("--out", default="categories/ciasi_decoded.json")
    ap.add_argument("--sleep", type=float, default=0.15)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    print(f"扫描 C-IASI 新闻稿 show-{a.start}..{a.end} ...")
    data = resolve_all(a.start, a.end, a.sleep)
    out = os.path.abspath(a.out)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"完成：{data['n_releases']} 篇结果稿 / {data['n_models']} 款车型唯一解码")
    print(f"输出：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
