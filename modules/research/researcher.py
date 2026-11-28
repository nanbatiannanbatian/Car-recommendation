#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
研究层 fallback（research-collector 的纯脚本通道）
================================================

⚠️ 主研究通道不是本脚本，而是 **WorkBuddy 自带的实时联网能力**
（domain_search / WebSearch / WebFetch / knowledge_search）。其中 **domain_search**
是多源权威检索的首选，尤其适合「研究型 / 跨源求证」问题（排行榜、横评、参数对比、
时效性数据）。这些工具是 WorkBuddy 内置的、经过优化的多源检索能力，远强于本脚本
手搓的 urllib + Bing 正则解析，且在中国大陆网络下更稳定、更不易被反爬降权。

推荐用法：在 WorkBuddy 对话中触发 research-collector（或 omni-debate 的「研究流程」）时说
    「用 omni-debate 研究：20万纯电SUV 推荐」
本 agent（WorkBuddy）会先用 domain_search / WebFetch 联网检索、抓关键页、直接整理成
research.json。本引擎已移除辩论/orchestrator，全程无 LLM 排序。
额外的 OmniRoute 调用。详见 research-collector 的 SKILL.md「五步协议」。

本脚本仅作为「无 WorkBuddy 对话、纯脚本 / CI / 批量」场景的可选 fallback：
把搜索引擎结果直接构造成 research.json。**默认不调模型抽取**，避免重复消耗
OmniRoute（抽取应由 agent 在检索上下文里一次性完成）。如需脚本内也做结构化
结构化抽取走主路径（agent + domain_search/WebFetch），本脚本仅产出链接列表级 research.json。

契约：research.json 的格式见本引擎 SKILL.md「统一证据采集」章节。
依赖：仅标准库（--extract 为旧辩论引擎遗留，本包已移除，见下方提示）。
用法：
  python3 researcher.py --query "20万 纯电SUV 推荐" --out runs/run1/research.json
  python3 researcher.py --query "..." --backend baidu            # 链接列表级 research.json
  python3 researcher.py --list-sites                               # 列出 17 个站点级解析器
  python3 researcher.py --list-topics                              # 列出垂类画像与证据轨道
  python3 researcher.py --plan-sources --query "20万买SUV 看C-NCAP" --out runs/plan.json  # 判断门：先规划找哪些资料
  python3 researcher.py --query "纯电SUV" --site smzdm          # 单站点级解析（什么值得买）
  python3 researcher.py --query "纯电SUV" --sites smzdm,bilibili,jd   # 多站点并联检索
  python3 researcher.py --query "纯电SUV" --reviews             # 购物平台评价挖掘（负面证据）
  python3 researcher.py --detect-ads runs/run1/research.json --inplace    # 广告/软广识别
  python3 researcher.py --score-authors runs/run1/research.json --inplace # 资料员评分（含广告降权）

注：v2.0 起每次 run_research 自动附带 source_plan / ad_summary，并给每条来源补
content_nature / ad_score / effective_author_score / evidence_track。
站点级解析器对强反爬/登录墙站点（小红书/抖音/淘宝/天猫/拼多多）会回退到搜索引擎
`site:` 检索；能直抓的（什么值得买/京东/B站）尝试解析其搜索/列表页。真实高质量来源
仍推荐用 agent 路径（domain_search / WebFetch，见 SKILL.md「站点级解析指南」）。
"""
import argparse, json, os, sys, time, re, html
import urllib.request, urllib.error, urllib.parse

import paths

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "config.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


# ----------------------------------------------------------------------------
# 搜索引擎（仅 fallback；脆弱、不保证效果，主通道请用 WorkBuddy domain_search）
# ----------------------------------------------------------------------------
def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def _clean(text):
    text = re.sub(r"(?is)<script.*?</script>", " ", text)
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def search_bing(q, n=10):
    url = "https://www.bing.com/search?q=" + urllib.parse.quote(q) + "&count=" + str(n)
    try:
        data = _get(url)
    except Exception:
        return []
    out = []
    for b in re.findall(r'<li class="b_algo"[^>]*>(.*?)</li>', data, re.S):
        tm = re.search(r'<h2>(?:<a[^>]*href="([^"]+)"[^>]*>)?(.*?)</h2>', b, re.S)
        if not tm:
            continue
        href = tm.group(1) or ""
        title = _clean(tm.group(2))
        pm = re.search(r'<p[^>]*>(.*?)</p>', b, re.S)
        snip = _clean(pm.group(1)) if pm else ""
        if title and href:
            out.append({"title": title, "url": href, "snippet": snip})
    return out[:n]


def search_baidu(q, n=10):
    url = "https://www.baidu.com/s?wd=" + urllib.parse.quote(q)
    try:
        data = _get(url)
    except Exception:
        return []
    out = []
    for block in re.findall(r'<div[^>]*class="[^"]*result[^"]*"[^>]*>(.*?)</div>', data, re.S):
        am = re.search(r'<h3[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not am:
            continue
        href = am.group(1)
        title = _clean(am.group(2))
        snip = _clean(re.search(r'class="[^"]*content-right_8Zs40[^"]*"[^>]*>(.*?)</span>', block, re.S).group(1)
                        if re.search(r'class="[^"]*content-right', block) else "")
        if title and href:
            out.append({"title": title, "url": href, "snippet": snip})
    if not out:
        for am in re.finditer(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', data, re.S):
            href, title = am.group(1), _clean(am.group(2))
            if len(title) > 6 and "baidu" not in href:
                out.append({"title": title, "url": href, "snippet": ""})
    return out[:n]


def search_ddg(q, n=10):
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(q)
    try:
        data = _get(url)
    except Exception:
        return []
    out = []
    for b in re.findall(r'<div class="result[^"]*"[^>]*>(.*?)</div>', data, re.S):
        am = re.search(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', b, re.S)
        if not am:
            continue
        href = am.group(1)
        title = _clean(am.group(2))
        snip = _clean(re.search(r'class="result__snippet"[^>]*>(.*?)</a>', b, re.S).group(1)
                        if re.search(r'class="result__snippet"', b) else "")
        if title and href:
            out.append({"title": title, "url": href, "snippet": snip})
    return out[:n]


BACKENDS = {"bing": search_bing, "baidu": search_baidu, "ddg": search_ddg}


def search(query, backend, n):
    fn = BACKENDS.get(backend, search_bing)
    results = fn(query, n)
    if not results and backend != "bing":
        results = search_bing(query, n)  # 任一后端失败，回退 Bing
    return results


def fetch_page(url, timeout):
    try:
        return _clean(_get(url, timeout))[:6000]
    except Exception:
        return None


# ----------------------------------------------------------------------------
# 站点级解析（小红书 / B站 / 什么值得买 / 淘宝 / 天猫 / 拼多多 / 京东）
# ----------------------------------------------------------------------------
# 这些平台的页面结构、反爬、登录墙各不相同，通用网页抽取（WebFetch/模型抽取）
# 经常拿不到正文或拿到脏数据。这里为每种平台提供「站点级解析器」：
#   - 能直抓的（什么值得买/京东/B站）尝试解析其搜索/列表页（best-effort，失败自动回退）；
#   - 强反爬/需登录的（小红书/淘宝/天猫/拼多多）直接走搜索引擎 `site:` 检索兜底。
# 一律优雅降级：原生解析失败就用 `site:` 检索，再失败就返回空（不报错）。
# 真实高质量来源仍推荐用 agent 路径（domain_search / WebFetch，见 SKILL.md 站点级解析指南）。
SITES_FALLBACK = {
    "xiaohongshu": {"label": "小红书", "domain": "xiaohongshu.com", "parser": "site_query", "default_type": "forum", "evidence_role": "reputation"},
    "douyin":      {"label": "抖音",   "domain": "douyin.com",      "parser": "site_query", "default_type": "forum", "evidence_role": "reputation"},
    "bilibili":    {"label": "B站",    "domain": "bilibili.com",    "parser": "bilibili_html", "default_type": "review", "evidence_role": "review"},
    "zhihu":       {"label": "知乎",   "domain": "zhihu.com",       "parser": "site_query", "default_type": "forum", "evidence_role": "analysis"},
    "smzdm":       {"label": "什么值得买", "domain": "smzdm.com",   "parser": "smzdm_html", "default_type": "ranking", "evidence_role": "pricing"},
    "taobao":      {"label": "淘宝",   "domain": "taobao.com",      "parser": "site_query", "default_type": "listing", "evidence_role": "pricing"},
    "tmall":       {"label": "天猫",   "domain": "tmall.com",       "parser": "site_query", "default_type": "listing", "evidence_role": "pricing"},
    "pinduoduo":   {"label": "拼多多", "domain": "pinduoduo.com",   "parser": "site_query", "default_type": "listing", "evidence_role": "pricing"},
    "jd":          {"label": "京东",   "domain": "jd.com",          "parser": "jd_html", "default_type": "listing", "evidence_role": "pricing"},
    "dongchedi":   {"label": "懂车帝", "domain": "dongchedi.com",   "parser": "site_query", "default_type": "review", "evidence_role": "review"},
    "autohome":    {"label": "汽车之家", "domain": "autohome.com.cn", "parser": "site_query", "default_type": "spec", "evidence_role": "spec"},
    "yiche":       {"label": "易车",   "domain": "yiche.com",       "parser": "site_query", "default_type": "review", "evidence_role": "review"},
    "12365auto":   {"label": "车质网", "domain": "12365auto.com",   "parser": "site_query", "default_type": "complaint", "evidence_role": "complaint"},
    "heimao":      {"label": "黑猫投诉", "domain": "tousu.sina.com.cn", "parser": "site_query", "default_type": "complaint", "evidence_role": "complaint"},
    "cncap":       {"label": "C-NCAP", "domain": "c-ncap.org.cn",   "parser": "site_query", "default_type": "safety", "evidence_role": "safety"},
    "ciasi":       {"label": "中保研", "domain": "ciasi.org.cn",    "parser": "site_query", "default_type": "safety", "evidence_role": "safety"},
    "samr_recall": {"label": "缺陷产品召回", "domain": "samr.gov.cn", "parser": "site_query", "default_type": "recall", "evidence_role": "recall"},
}


def load_sites(cfg):
    return cfg.get("sites") or SITES_FALLBACK


def search_via_site(query, domain, n=10, backend="bing"):
    """用搜索引擎的 `site:` 语法把检索限定到某平台域名（强反爬/登录墙站点的通用兜底）。"""
    return search(f"{query} site:{domain}", backend, n)


def parse_smzdm_html(q, n=10):
    """什么值得买搜索页：列表页可抓，正则取 标题/URL/好价。失败返回 None 触发回退。"""
    url = "https://search.smzdm.com/?s=" + urllib.parse.quote(q)
    try:
        data = _get(url, timeout=12)
    except Exception:
        return None
    out = []
    for m in re.finditer(r'<a[^>]+href="(https?://(?:[a-z]+\.)?smzdm\.com/[^"]+)"[^>]*>(.*?)</a>', data, re.S):
        href, title = m.group(1), _clean(m.group(2))
        if len(title) < 4:
            continue
        if any(skip in href for skip in ("/search", "/user/", "/zhiyou/", "/helper")):
            continue
        price = None
        pm = re.search(r"([¥￥]\s?\d[\d.]*|好价[：: ]*([\d.]+))", title)
        if pm:
            price = pm.group(0)
        out.append({"title": title, "url": href, "snippet": "", "metrics": {"price": price} if price else {}})
    return out[:n] if out else None


def parse_bilibili_html(q, n=10):
    """B站搜索页：结果嵌在 <script> JSON 里，正则抓 标题+BV 链接（best-effort）。"""
    url = "https://search.bilibili.com/all?keyword=" + urllib.parse.quote(q)
    try:
        data = _get(url, timeout=12)
    except Exception:
        return None
    out = []
    for m in re.finditer(r'"title":"(.*?)".*?"arcurl":"(https?://www\.bilibili\.com/video/[A-Za-z0-9]+)"', data, re.S):
        title = _clean(m.group(1))
        if title:
            out.append({"title": title, "url": m.group(2), "snippet": "", "metrics": {}})
    if not out:
        for m in re.finditer(r'<a[^>]+href="(https?://www\.bilibili\.com/video/[^"]+)"[^>]*>(.*?)</a>', data, re.S):
            title = _clean(m.group(2))
            if len(title) > 4:
                out.append({"title": title, "url": m.group(1), "snippet": "", "metrics": {}})
    return out[:n] if out else None


def parse_jd_html(q, n=10):
    """京东搜索页：列表页可 site 检索，正则取商品链接/标题/价格（best-effort）。"""
    url = "https://search.jd.com/Search?keyword=" + urllib.parse.quote(q)
    try:
        data = _get(url, timeout=12)
    except Exception:
        return None
    out = []
    for m in re.finditer(r'<a[^>]+href="(https?://item\.jd\.com/\d+\.html)"[^>]*>(.*?)</a>', data, re.S):
        title = _clean(m.group(2))
        if len(title) < 4:
            continue
        price = None
        pm = re.search(r"([¥￥]\s?\d[\d.]*|京东价[：: ]*(\d+))", title)
        if pm:
            price = pm.group(0)
        out.append({"title": title, "url": m.group(1), "snippet": "", "metrics": {"price": price} if price else {}})
    return out[:n] if out else None


def run_site_search(site_key, query, cfg, n):
    """对某平台跑站点级解析：先试原生解析器，失败回退 site: 检索；每条结果打上站点元数据。"""
    sites = load_sites(cfg)
    meta = sites.get(site_key)
    if not meta:
        return []
    label = meta.get("label", site_key)
    domain = meta.get("domain", "")
    parser = meta.get("parser", "site_query")
    default_type = meta.get("default_type", "unknown")
    results = None
    if parser == "smzdm_html":
        results = parse_smzdm_html(query, n)
    elif parser == "bilibili_html":
        results = parse_bilibili_html(query, n)
    elif parser == "jd_html":
        results = parse_jd_html(query, n)
    # xiaohongshu_html / site_query 一律走 site: 检索兜底
    if not results:
        results = search_via_site(query, domain, n, cfg.get("research", {}).get("backend", "bing"))
    if not results:
        return []
    tagged = []
    for r in results:
        rr = dict(r)
        rr["_site"] = site_key
        rr["_publisher"] = label
        rr["_type"] = default_type
        tagged.append(rr)
    return tagged[:n]


def enrich_from_url(url, sites):
    """根据 URL 域名自动匹配已注册站点，返回 (publisher, type, site_key)；不匹配返回全 None。"""
    if not url:
        return None, None, None
    for key, m in sites.items():
        dom = m.get("domain", "")
        if dom and dom in url:
            return m.get("label", key), m.get("default_type", "unknown"), key
    return None, None, None


def detect_site(results, sites):
    """通用检索结果里，按 URL 域名自动补 publisher/type/site（不覆盖已显式标注的）。"""
    for r in results:
        if r.get("_site") or r.get("_publisher"):
            continue
        pub, t, sk = enrich_from_url(r.get("url", ""), sites)
        if pub:
            r["_publisher"] = pub
            r["_type"] = t
            r["_site"] = sk
    return results


# ----------------------------------------------------------------------------
# 博主 / 资料员可信度评分（测评类 / 排名类问题专用）
# ----------------------------------------------------------------------------
# 思路：排名/横评问题若只信单一来源，极易被软广/幸存者偏差带偏。更客观的做法是
# 收集多个博主的测评，并给「资料员」（发布者）打分：粉丝量级 + 是否认证 + 平台权重，
# 得到一个 0-100 的 author_score。该分进入 research.json 契约，辩论/质证轮与裁判
# 可信度表都可直接引用，来源质量更直观、可追溯。
def _parse_followers(s):
    """把 '120万' / '1.2w' / '5000' / '3.2亿' 等解析成整数；失败返回 None。"""
    if not s:
        return None
    s = str(s).strip().replace(",", "").replace("，", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(万|w|W|千|k|K|亿)?", s)
    if not m:
        return None
    v = float(m.group(1))
    unit = m.group(2)
    if unit in ("万", "w", "W"):
        v *= 1e4
    elif unit in ("千", "k", "K"):
        v *= 1e3
    elif unit in ("亿",):
        v *= 1e8
    return int(v)


def score_author(author, platform=None):
    """按博主元数据计算可信度分(0-100)。透明启发式：粉丝量级为主，认证加成，平台微调。

    规则（可调）：未知粉丝给中性底分 30；粉丝分档 25/45/62/76/88/95；
    认证 +6（封顶 100）；B站测评号 +3，抖音/小红书/快手等泛娱乐带货号 -3（封顶/保底）。
    返回 int；author 为空时返回 None。
    """
    if not author:
        return None
    meta = author if isinstance(author, dict) else {}
    num = meta.get("followers_num")
    if num is None:
        num = _parse_followers(meta.get("followers", ""))
    if num is None:
        base = 30
    elif num < 1e4:
        base = 25
    elif num < 1e5:
        base = 45
    elif num < 5e5:
        base = 62
    elif num < 1e6:
        base = 76
    elif num < 5e6:
        base = 88
    else:
        base = 95
    if meta.get("verified"):
        base = min(100, base + 6)
    p = (platform or meta.get("platform") or "").lower()
    if p in ("bilibili",):
        base = min(100, base + 3)
    elif p in ("douyin", "xiaohongshu", "kuaishou"):
        base = max(0, base - 3)
    return int(base)


def author_score_label(score):
    if score is None:
        return "未知"
    if score >= 85:
        return "高"
    if score >= 60:
        return "中"
    return "低"


def summarize_author_scores(research):
    """从 research.json 汇总『博主/资料员可信度排行』：按 author_score 降序。"""
    rows = []
    for s in research.get("sources", []):
        a = s.get("author") or {}
        sc = s.get("author_score")
        if sc is None and a:
            sc = score_author(a, s.get("site"))
        if a or sc is not None:
            rows.append({
                "source_id": s.get("id"),
                "author": a.get("name") or a.get("handle") or (s.get("publisher") or "?"),
                "platform": s.get("site") or a.get("platform") or "",
                "followers": a.get("followers") or (a.get("followers_num") if a.get("followers_num") is not None else ""),
                "verified": bool(a.get("verified")),
                "author_score": sc,
                # 标签按「广告降权后的有效分」给，避免 85 分但判 sponsored 时仍显示「高」
                "author_score_label": author_score_label(s.get("effective_author_score", sc)),
                "content_nature": s.get("content_nature", ""),
                "ad_score": s.get("ad_score"),
                "ad_risk": s.get("ad_risk", ""),
                "effective_author_score": s.get("effective_author_score", sc),
                "title": s.get("title", ""),
            })
    rows.sort(key=lambda x: (x.get("author_score") or 0), reverse=True)
    return rows


# ----------------------------------------------------------------------------
# 广告 / 软广识别：区分「真测评」与「恰饭/通稿/挂车」
# ----------------------------------------------------------------------------
# 抖音/B站/知乎/小红书的内容里商业化比例很高。不能把「粉丝多」等同于「可信」：
# 一条 200 万粉博主的商单，对购买决策的价值可能远低于一条无明显商业信号的自费长测。
# 因此这里把广告风险做成独立维度：命中显式披露即判 sponsored；其余按商业引导、
# 通稿水军特征加权，真测评反证（自费/长期使用/有缺点/实测数据）减分。
# 负面证据轨道（投诉/召回/安全/用户差评）不套用软广逻辑，单独标注为硬证据。
HARD_EVIDENCE_TYPES = {"complaint", "safety", "recall", "user_review"}


def detect_ad_risk(source, cfg):
    """判断单条来源是否为广告/软广。返回结构化结果；未启用时返回 None。

    返回：{ad_score, ad_risk, content_nature, ad_signals, organic_signals,
           author_score_penalty, evidence_track, note}
    """
    ad = cfg.get("ad_detection", {})
    if not ad.get("enabled", True):
        return None
    stype = source.get("type") or "unknown"
    site_meta = (cfg.get("sites", {}).get(source.get("site") or "") or {})
    role = site_meta.get("evidence_role") or ""

    # 硬证据轨道：投诉/召回/安全测试/真实用户评价，抗广告污染，不按软广降权
    if stype in HARD_EVIDENCE_TYPES:
        return {
            "ad_score": 0, "ad_risk": "low",
            "content_nature": "ugc" if stype == "user_review" else "editorial",
            "ad_signals": [], "organic_signals": [],
            "author_score_penalty": 0,
            "evidence_track": role or stype,
            "note": "硬证据轨道（负面/监管/真实购买者反馈），不参与软广加权；注意样本偏差与个例。",
        }

    w = ad.get("weights", {})
    text = " ".join(str(source.get(k) or "") for k in ("title", "summary", "snippet")).lower()
    # 真测评反证里的否定短语（如「无购买链接」）本身包含正向商业词「购买链接」，
    # 会误命中商业信号。扫描商业/通稿类关键词前，先把这些否定短语从待检文本中剔除。
    scan_text = text
    for kw in ad.get("organic_keywords", []):
        low = str(kw).lower()
        if low.startswith("无") and low in scan_text:
            scan_text = scan_text.replace(low, " ")
    cats = (("disclosure", "disclosure_keywords"), ("commercial", "commercial_keywords"),
            ("astroturf", "astroturf_keywords"), ("organic", "organic_keywords"))
    matched = {}
    for cat, key in cats:
        target = text if cat == "organic" else scan_text
        matched[cat] = [kw for kw in ad.get(key, []) if str(kw).lower() in target]

    score = (min(len(matched["disclosure"]), 2) * w.get("disclosure", 45)
             + min(len(matched["commercial"]), 2) * w.get("commercial", 25)
             + min(len(matched["astroturf"]), 2) * w.get("astroturf", 20)
             + min(len(matched["organic"]), 2) * w.get("organic_bonus", -15))
    pub = str(source.get("publisher") or "").lower()
    is_official = (stype == "official"
                   or any(x in pub for x in ("官方", "旗舰店", "品牌", "厂商")))
    if is_official:
        score += w.get("brand_official_penalty", 40)
    score = max(0, min(100, int(score)))

    hi = ad.get("high_risk_score", 60)
    mid = ad.get("medium_risk_score", 30)
    explicit = ad.get("explicit_disclosure_score", 85)
    if matched["disclosure"] or score >= explicit:
        nature, score = "sponsored", max(score, explicit)
    elif is_official:
        nature = "brand_official"
    elif score >= hi:
        nature = "sponsored"
    elif score >= mid:
        nature = "uncertain"
    elif matched["organic"]:
        nature = "organic_review"
    else:
        # 信号不足时不能默认当真测评——显式标 uncertain，提醒质证轮核验
        nature = "uncertain"
    risk = "high" if score >= hi else "medium" if score >= mid else "low"
    penalty = (ad.get("author_score_penalty", {}) or {}).get(risk, 0)
    if is_official:
        penalty = max(penalty, 15)

    signals = matched["disclosure"] + matched["commercial"] + matched["astroturf"]
    note = "启发式判定，非事实裁决。"
    if nature == "sponsored":
        note += "存在显式/高度疑似商业推广，结论需在质证轮按利益相关降权。"
    elif nature == "brand_official":
        note += "品牌/官方内容有利益关系，仅建议用于参数核对，不作为独立评测。"
    elif nature == "uncertain":
        note += "广告信号不足，建议用 WebFetch 核原文或标记为待核验，勿默认当独立测评。"
    return {"ad_score": score, "ad_risk": risk, "content_nature": nature,
            "ad_signals": signals, "organic_signals": matched["organic"],
            "author_score_penalty": penalty, "evidence_track": role or stype,
            "note": note}


def summarize_ad_risk(sources, cfg):
    """汇总整份 research.json 的广告污染程度，供质证轮与裁判一眼看清。"""
    counts, sponsored, organic, hard = {}, [], [], []
    scores = []
    for s in sources:
        n = s.get("content_nature") or "uncertain"
        counts[n] = counts.get(n, 0) + 1
        sid = s.get("id")
        if n == "sponsored":
            sponsored.append(sid)
        elif n == "organic_review":
            organic.append(sid)
        if s.get("evidence_track") in ("complaint", "recall", "safety") or s.get("type") in HARD_EVIDENCE_TYPES:
            hard.append(sid)
        if s.get("ad_score") is not None:
            scores.append(s["ad_score"])
    total = len(sources) or 1
    ratio = len(sponsored) / total
    if ratio >= 0.5:
        verdict = "⚠️ 过半来源疑似商业推广，正面结论可信度需显著下调，优先采信负面硬证据轨道"
    elif ratio >= 0.25:
        verdict = "⚠️ 存在明显软广比例，质证轮须逐条核验利益相关并按 effective_author_score 降权"
    else:
        verdict = "广告污染比例较低；仍建议核对 uncertain 来源原文"
    return {"counts": counts, "sponsored_ids": sponsored, "organic_review_ids": organic,
            "hard_evidence_ids": hard, "sponsored_ratio": round(ratio, 3),
            "avg_ad_score": round(sum(scores) / len(scores), 1) if scores else None,
            "verdict": verdict}


def effective_author_score(author_score, ad_result):
    """把广告风险折进资料员评分：高/中风险分别扣 25/10 分（可配置）。"""
    if author_score is None:
        return None
    if not ad_result:
        return author_score
    return max(0, int(author_score) - int(ad_result.get("author_score_penalty", 0)))


def apply_ad_detection(sources, cfg):
    """批量给 sources 补 content_nature / ad_score / ad_signals / effective_author_score。"""
    for s in sources:
        # agent 已明确标注过内容性质与分数时，尊重其判断，只补有效分
        if s.get("content_nature") and s.get("ad_score") is not None:
            s["effective_author_score"] = effective_author_score(
                s.get("author_score"),
                {"author_score_penalty": (cfg.get("ad_detection", {}).get("author_score_penalty", {}) or {}).get(s.get("ad_risk", "low"), 0)})
            continue
        res = detect_ad_risk(s, cfg)
        if not res:
            continue
        s["content_nature"] = res["content_nature"]
        s["ad_score"] = res["ad_score"]
        s["ad_risk"] = res["ad_risk"]
        s["ad_signals"] = res["ad_signals"]
        s["organic_signals"] = res["organic_signals"]
        s["evidence_track"] = res["evidence_track"]
        s["effective_author_score"] = effective_author_score(s.get("author_score"), res)
    return sources


# ----------------------------------------------------------------------------
# 购物平台「用户评价」挖掘（差评/追评/好评率/差评主题）
# ----------------------------------------------------------------------------
def build_review_queries(query, cfg):
    """按 review_mining.query_templates 生成评价检索式。"""
    tpls = cfg.get("review_mining", {}).get("query_templates", [])
    return [t.replace("{q}", query) for t in tpls]


def extract_negative_topics(text, cfg):
    """从评价摘要里抽取差评主题关键词（best-effort）。"""
    kws = cfg.get("review_mining", {}).get("negative_keywords", [])
    return [k for k in kws if k in (text or "")]


def run_review_search(query, cfg, site_keys=None, n=8, backend=None):
    """对购物/垂类平台做「评价向」检索：差评、追评、投诉，产出 user_review 来源。

    注意：真实评价正文大多需登录，本函数只做检索式拼接 + site: 检索，
    结果一律标注「搜索摘要、未验证原文」；agent 主路径应用 WebFetch/domain_search 核原文。
    """
    rc = cfg.get("research", {})
    backend = backend or rc.get("backend", "bing")
    sites_reg = load_sites(cfg)
    keys = site_keys or cfg.get("review_sites", ["jd", "tmall", "taobao"])
    queries = build_review_queries(query, cfg)
    raw = []
    for sk in keys:
        meta = sites_reg.get(sk)
        if not meta:
            continue
        for q in queries[:2]:  # 每站最多 2 条检索式，控制调用量
            hits = search_via_site(q, meta.get("domain", ""), max(2, n // max(1, len(keys))), backend)
            for h in hits:
                h = dict(h)
                h["_site"] = sk
                h["_publisher"] = meta.get("label", sk)
                h["_type"] = "user_review"
                h["metrics"] = dict(h.get("metrics") or {})
                h["metrics"]["negative_topics"] = extract_negative_topics(h.get("snippet", ""), cfg)
                h["metrics"]["sample_note"] = "搜索摘要、未验证原文；可能含刷评/好评返现"
                raw.append(h)
        if len(raw) >= n:
            break
    return raw[:n]


def summarize_review_signals(sources, cfg):
    """汇总评价信号：多少个评价向来源、高频差评主题、样本量提示。"""
    rm = cfg.get("review_mining", {})
    reviews = [s for s in sources if s.get("type") == "user_review"]
    freq = {}
    for s in reviews:
        for t in (s.get("metrics") or {}).get("negative_topics", []) or []:
            freq[t] = freq.get(t, 0) + 1
    topics = sorted(freq.items(), key=lambda x: -x[1])
    return {
        "review_source_count": len(reviews),
        "negative_topics": [{"topic": t, "mentions": c} for t, c in topics],
        "min_reviews_for_signal": rm.get("min_reviews_for_signal", 50),
        "note": rm.get("note", ""),
        "verdict": ("评价样本不足，结论需谨慎" if len(reviews) < 3
                    else "已有多条评价向来源，可将高频差评主题作为负面证据"),
    }


# ----------------------------------------------------------------------------
# 资料源智能规划：静态垂类画像 + 动态平台发现 + 判断门
# ----------------------------------------------------------------------------
def match_topic_profile(question, cfg):
    """按关键词命中数匹配垂类画像；无命中回退 general。返回 (key, profile, hits)。"""
    q = (question or "").lower()
    best_key, best_profile, best_hits = "general", cfg.get("topic_profiles", {}).get("general", {}), []
    for key, prof in (cfg.get("topic_profiles", {}) or {}).items():
        if key == "general":
            continue
        hits = [k for k in prof.get("keywords", []) if str(k).lower() in q]
        if len(hits) > len(best_hits):
            best_key, best_profile, best_hits = key, prof, hits
    return best_key, best_profile, best_hits


def plan_sources(question, cfg, discovered_sites=None):
    """产出 source_plan：先判断「该找哪些资料」再检索。

    三层混合：① 静态垂类画像给出证据轨道；② 未识别领域/需扩展时产出动态发现查询
    （由 agent 用 domain_search 执行，脚本不自行判定平台可信度）；③ 判断门要求
    先过目计划再抓，且强制正/负双轨平衡。
    """
    sp = cfg.get("source_planning", {}) or {}
    sites_reg = load_sites(cfg)
    key, prof, hits = match_topic_profile(question, cfg)
    tracks = []
    for t in prof.get("evidence_tracks", []):
        track = dict(t)
        track["site_labels"] = [sites_reg.get(s, {}).get("label", s) for s in t.get("sites", [])]
        track["ad_risk"] = [sites_reg.get(s, {}).get("ad_risk", "未知") for s in t.get("sites", [])]
        tracks.append(track)

    roles = {t.get("role") for t in tracks}
    positive = bool(roles & {"review", "spec", "ranking", "pricing"})
    negative = bool(roles & {"complaint", "recall", "safety", "user_review"})
    balance = "ok" if (positive and negative) else "missing_negative" if positive else "missing_positive"

    discovery = []
    if sp.get("dynamic_discovery", True):
        topic = prof.get("label", question)
        discovery = [t.replace("{topic}", str(topic)) for t in sp.get("discovery_queries", [])]

    flat_sites = []
    for t in tracks:
        for s in t.get("sites", []):
            if s not in flat_sites and s in sites_reg:
                flat_sites.append(s)
    if discovered_sites:
        for s in discovered_sites:
            if s not in flat_sites:
                flat_sites.append(s)

    return {
        "question": question,
        "topic": key,
        "topic_label": prof.get("label", ""),
        "matched_keywords": hits,
        "match_confidence": "high" if len(hits) >= 2 else "medium" if len(hits) == 1 else "low",
        "mode": sp.get("mode", "hybrid"),
        "evidence_tracks": tracks,
        "recommended_sites": flat_sites,
        "must_check": prof.get("must_check", []),
        "ad_risk_note": prof.get("ad_risk_note", ""),
        "avoid": prof.get("avoid", []),
        "dynamic_discovery_queries": discovery,
        "dynamic_discovery_note": sp.get("discovery_note", ""),
        "evidence_balance": balance,
        "balance_rule": sp.get("balance_rule", ""),
        "require_plan_review": sp.get("require_plan_review", True),
        "plan_review_note": sp.get("plan_review_note", ""),
        "min_independent_sources": sp.get("min_independent_sources", 3),
        "review_queries": build_review_queries(question, cfg) if cfg.get("review_mining", {}).get("enabled") else [],
        "discovered_sites": discovered_sites or [],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def render_plan_md(plan):
    lines = ["# 资料源计划（source_plan）", "",
             f"**问题：** {plan['question']}", "",
             f"**识别领域：** {plan['topic_label']}（{plan['topic']}，置信度 {plan['match_confidence']}，"
             f"命中关键词：{', '.join(plan['matched_keywords']) or '无'}）", "",
             "## 证据轨道（先判断找什么，再决定去哪找）", "",
             "| 角色 | 平台 | 理由 | 广告风险 |", "|---|---|---|---|"]
    for t in plan["evidence_tracks"]:
        lines.append(f"| {t.get('role','')} | {', '.join(t.get('site_labels', []))} | "
                     f"{t.get('why','')} | {'；'.join(t.get('ad_risk', []))} |")
    lines += ["", f"**正/负证据平衡：** `{plan['evidence_balance']}`", "",
              "## 必查项", ""]
    lines += [f"- {x}" for x in plan["must_check"]] or ["- （无）"]
    lines += ["", "## 应当规避", ""]
    lines += [f"- {x}" for x in plan["avoid"]] or ["- （无）"]
    lines += ["", "## 广告风险提示", "", plan["ad_risk_note"] or "（无）", "",
              "## 动态平台发现（陌生领域 / 画像过期时先用 domain_search 跑）", ""]
    lines += [f"- {q}" for q in plan["dynamic_discovery_queries"]] or ["- （已关闭）"]
    if plan["dynamic_discovery_note"]:
        lines += ["", "> " + plan["dynamic_discovery_note"]]
    lines += ["", "## 购物平台评价检索式", ""]
    lines += [f"- {q}" for q in plan["review_queries"]] or ["- （未启用）"]
    lines += ["", "## 判断门", "",
              f"- 计划需先过目再执行：`{plan['require_plan_review']}`",
              f"- 最少独立来源数：{plan['min_independent_sources']}", ""]
    if plan["plan_review_note"]:
        lines += ["> " + plan["plan_review_note"], ""]
    if plan["evidence_balance"] != "ok":
        lines += ["> ⚠️ " + plan["balance_rule"], ""]
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# 构造来源（不调模型，避免重复消耗 OmniRoute）
# ----------------------------------------------------------------------------
def build_sources_from_results(results):
    """直接把（可能已带站点/作者元数据的）结果列表构造成 source 数组（字段尽量填，缺的给默认值）。"""
    out = []
    for i, r in enumerate(results):
        author = r.get("author") or {}
        site_tag = r.get("site") or r.get("_site")
        sc = r.get("author_score")
        if sc is None and author:
            sc = score_author(author, site_tag)
        out.append({
            "id": "s" + str(i + 1),
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "publisher": r.get("publisher") or r.get("_publisher", ""),
            "type": r.get("type") or r.get("_type", "unknown"),
            "site": site_tag,
            "author": author,
            "author_score": sc,
            "published": r.get("published"),
            "summary": (r.get("snippet", "") or "")[:120],
            "metrics": r.get("metrics", {}) or {},
            "candidates": r.get("candidates", []),
            "credibility_pre": r.get("credibility_pre", "unknown"),
        })
    return out


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def run_research(query, cfg, backend=None, max_sources=None, out_path=None,
                 do_extract=False, site=None, sites=None, list_sites=False,
                 review_mode=False):
    rc = cfg.get("research", {})
    backend = backend or rc.get("backend", "bing")
    max_sources = max_sources or rc.get("max_sources", 8)
    fetch_timeout = rc.get("fetch_timeout_s", 15)
    base = cfg.get("base_url")
    sites_reg = load_sites(cfg)

    if list_sites:
        print("[research][sites] 已注册站点级解析器：", file=sys.stderr)
        for k, m in sites_reg.items():
            print(f"  {k:12s} {m.get('label',''):8s} parser={m.get('parser',''):14s} "
                  f"type={m.get('default_type',''):8s} anti_bot={m.get('anti_bot','')}", file=sys.stderr)
        return None

    site_keys = []
    if sites:
        site_keys = [s.strip() for s in sites.split(",") if s.strip()]
    elif site:
        site_keys = [site]

    raw_results = []
    site_mode = None
    if site_keys:
        site_mode = site_keys
        print(f"[research][site] 站点级检索：{site_keys} 查询={query!r}", file=sys.stderr)
        for sk in site_keys:
            if sk not in sites_reg:
                print(f"[research][site] 未知站点 {sk}，跳过（用 --list-sites 查看）", file=sys.stderr)
                continue
            res = run_site_search(sk, query, cfg, max_sources)
            print(f"[research][site] {sk} 得到 {len(res)} 条", file=sys.stderr)
            raw_results.extend(res)
        raw_results = raw_results[:max_sources]
    else:
        # 默认通用检索 + 域名自动打标（命中已注册站点则补 publisher/type）
        print(f"[research][fallback] 后端={backend} 查询={query!r} extract={do_extract}", file=sys.stderr)
        raw_results = search(query, backend, max_sources * 2)
        print(f"[research][fallback] 检索到 {len(raw_results)} 条候选链接", file=sys.stderr)
        if rc.get("site_enrich", True):
            raw_results = detect_site(raw_results, sites_reg)

    # 评价向检索：叠加「差评/追评/投诉」来源，补齐负面证据轨道
    review_signals = None
    if review_mode:
        print(f"[research][reviews] 购物平台评价挖掘：{query!r}", file=sys.stderr)
        rev = run_review_search(query, cfg, site_keys=site_keys or None,
                                n=max_sources, backend=backend)
        print(f"[research][reviews] 得到 {len(rev)} 条评价向来源", file=sys.stderr)
        raw_results = (raw_results + rev)[:max_sources * 2]
        site_mode = site_mode or ["reviews"]

    # 默认：直接构造（不调模型，不消耗 OmniRoute）
    sources = build_sources_from_results(raw_results)
    # 广告识别：补 content_nature / ad_score / effective_author_score
    sources = apply_ad_detection(sources, cfg)
    ad_summary = summarize_ad_risk(sources, cfg)
    if review_mode:
        review_signals = summarize_review_signals(sources, cfg)

    # v9.7.38：辩论引擎已移除，--extract 旧模型抽取通道删除。
    # 结构化抽取改走主路径（agent + domain_search/WebFetch 直接整理 research.json）。
    if do_extract:
        print("[research] --extract 已废弃（辩论引擎移除）；请走主路径结构化抽取，"
              "本脚本仅产出链接列表级 research.json。", file=sys.stderr)

    note = ("由研究层 fallback 脚本抓取（站点级解析/通用检索/评价挖掘 + 广告识别，未做结构化抽取）；"
            "如需高质量来源，推荐改用 WorkBuddy 对话触发 research-collector / omni-debate，"
            "由 agent 用 domain_search / WebFetch 联网检索并整理。"
            + (f" 本次站点模式：{site_mode}。" if site_mode else ""))
    out = {
        "query": query,
        "backend": backend if not do_extract else (backend + "+extract"),
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "site_mode": site_mode,
        "note": note,
        "source_plan": plan_sources(query, cfg),
        "ad_summary": ad_summary,
        "review_signals": review_signals,
        "sources": sources,
    }
    if out_path:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"[research][fallback] 已写出 {out_path}", file=sys.stderr)
    return out


def render_ad_report_md(research):
    sm = research.get("ad_summary") or summarize_ad_risk(research.get("sources", []), {})
    lines = ["# 广告 / 软广识别报告", "",
             f"**查询：** {research.get('query', '')}", "",
             f"**结论：** {sm.get('verdict','')}", "",
             f"- 疑似商业推广占比：{sm.get('sponsored_ratio')}（{len(sm.get('sponsored_ids', []))} 条）",
             f"- 平均广告风险分：{sm.get('avg_ad_score')}",
             f"- 内容性质分布：{sm.get('counts', {})}",
             f"- 硬证据轨道来源：{', '.join(sm.get('hard_evidence_ids', [])) or '（无）'}", "",
             "| 来源 | 平台 | 内容性质 | 广告分 | 风险 | 资料员分→有效分 | 命中信号 | 备注 |",
             "|---|---|---|---|---|---|---|---|"]
    for s in research.get("sources", []):
        eff = s.get("effective_author_score", s.get("author_score"))
        base = s.get("author_score")
        sc = f"{base}→{eff}" if base is not None else "—"
        lines.append(f"| {s.get('id','')} | {s.get('publisher') or s.get('site','')} | "
                     f"{s.get('content_nature','')} | {s.get('ad_score','')} | {s.get('ad_risk','')} | "
                     f"{sc} | {'、'.join(s.get('ad_signals', [])[:4]) or '—'} | "
                     f"{str(s.get('title',''))[:40]} |")
    lines += ["", "> 判定为启发式，非事实裁决。`uncertain` 请用 WebFetch 核原文；"
              "`sponsored`/`brand_official` 在质证轮与裁判评分表中须按利益相关降权。", ""]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(
        description="research-collector 研究层（fallback）：资料源规划 → 抓取真实来源 → 广告识别 → research.json。"
                    "主通道请用 WorkBuddy 对话触发（agent 用 domain_search 联网检索）。")
    ap.add_argument("--query", help="检索查询 / 待规划的问题；与 --score-authors 等文件模式互斥")
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--out", help="输出路径（research.json / source_plan.json / 报告文件）")
    ap.add_argument("--backend", help="搜索引擎：bing / baidu / ddg（仅通用检索 fallback）")
    ap.add_argument("--max-sources", type=int, help="最多保留的来源条数")
    ap.add_argument("--site", help="站点级检索：单一平台 key（见 --list-sites）")
    ap.add_argument("--sites", help="站点级检索：多个平台 key 逗号分隔")
    ap.add_argument("--list-sites", action="store_true", help="列出已注册的站点级解析器后退出")
    ap.add_argument("--list-topics", action="store_true", help="列出内置垂类画像与证据轨道后退出")
    ap.add_argument("--plan-sources", action="store_true",
                    help="判断门：只产出 source_plan（该找哪些资料/平台/查询式），不抓来源")
    ap.add_argument("--discovered-sites", help="动态平台发现得到的站点 key，逗号分隔，补入计划")
    ap.add_argument("--reviews", action="store_true", help="购物平台评价挖掘：差评/追评/投诉（负面证据轨道）")
    ap.add_argument("--detect-ads", help="对已有 research.json 跑广告识别，产出报告（可配 --inplace 写回）")
    ap.add_argument("--score-authors", help="对已有 research.json 的博主/资料员打分（含广告降权）并输出排行")
    ap.add_argument("--inplace", action="store_true", help="把打分/广告识别结果写回原 research.json")
    ap.add_argument("--extract", action="store_true",
                    help="已废弃（辩论引擎移除）；结构化抽取走主路径 agent+domain_search")
    args = ap.parse_args()

    cfg = json.load(open(args.config, encoding="utf-8"))

    # 模式 0：列出垂类画像
    if args.list_topics:
        print("[research][topics] 内置垂类画像：", file=sys.stderr)
        for k, p in (cfg.get("topic_profiles", {}) or {}).items():
            roles = ",".join(t.get("role", "") for t in p.get("evidence_tracks", []))
            print(f"  {k:12s} {p.get('label',''):12s} 轨道={roles}", file=sys.stderr)
        return

    # 模式 1：判断门 —— 先规划「找哪些资料」，不联网抓
    if args.plan_sources:
        q = args.query
        if not q:
            sys.exit("--plan-sources 需要 --query \"<问题>\"")
        discovered = [x.strip() for x in (args.discovered_sites or "").split(",") if x.strip()]
        plan = plan_sources(q, cfg, discovered_sites=discovered or None)
        out_path = args.out or os.path.join(HERE, "runs", "source_plan.json")
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        md_path = os.path.splitext(out_path)[0] + ".md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(render_plan_md(plan))
        print(f"[research][plan] 已写出资料源计划 {out_path} 与 {md_path}", file=sys.stderr)
        print(f"[research][plan] 领域={plan['topic_label']} 置信度={plan['match_confidence']} "
              f"证据平衡={plan['evidence_balance']}", file=sys.stderr)
        print(render_plan_md(plan), file=sys.stderr)
        return

    # 模式 2：对已有 research.json 跑广告识别
    if args.detect_ads:
        path = args.detect_ads
        if not os.path.exists(path):
            sys.exit("未找到 research 文件: " + path)
        research = json.load(open(path, encoding="utf-8"))
        # 先补算资料员原始分，报告里的「原分→有效分」才完整
        for s in research.get("sources", []):
            a = s.get("author") or {}
            if s.get("author_score") is None and a:
                s["author_score"] = score_author(a, s.get("site"))
        research["sources"] = apply_ad_detection(research.get("sources", []), cfg)
        research["ad_summary"] = summarize_ad_risk(research["sources"], cfg)
        out_json = args.out or (os.path.splitext(path)[0] + "_ad_report.json")
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump({"query": research.get("query"), "ad_summary": research["ad_summary"],
                       "sources": research["sources"]}, f, ensure_ascii=False, indent=2)
        md_path = os.path.splitext(out_json)[0] + ".md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(render_ad_report_md(research))
        print(f"[research][ads] 已写出广告识别报告 {md_path}", file=sys.stderr)
        print("[research][ads] " + research["ad_summary"]["verdict"], file=sys.stderr)
        if args.inplace:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(research, f, ensure_ascii=False, indent=2)
            print(f"[research][ads] 已写回广告识别字段到 {path}", file=sys.stderr)
        return

    # 模式 3：对已有 research.json 做博主/资料员打分（先做广告降权，再排行）
    if args.score_authors:
        path = args.score_authors
        if not os.path.exists(path):
            sys.exit("未找到 research 文件: " + path)
        research = json.load(open(path, encoding="utf-8"))
        for s in research.get("sources", []):
            a = s.get("author") or {}
            if s.get("author_score") is None and a:
                s["author_score"] = score_author(a, s.get("site"))
        research["sources"] = apply_ad_detection(research["sources"], cfg)
        rows = summarize_author_scores(research)
        out_path = args.out or (os.path.splitext(path)[0] + "_author_scores.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"query": research.get("query"),
                       "ad_summary": summarize_ad_risk(research["sources"], cfg),
                       "ranking": rows}, f, ensure_ascii=False, indent=2)
        print(f"[research][author-score] 已写出博主可信度排行 {out_path}", file=sys.stderr)
        print(f"\n{'排名':<4}{'来源':<6}{'博主':<18}{'平台':<10}{'粉丝':<12}{'认证':<6}"
              f"{'评分':<6}{'有效分':<8}{'性质':<16}可信度", file=sys.stderr)
        for i, r in enumerate(rows, 1):
            print(f"{i:<4}{r['source_id']:<6}{str(r['author'])[:16]:<18}{str(r['platform'])[:8]:<10}"
                  f"{str(r['followers'])[:10]:<12}{('是' if r['verified'] else '否'):<6}"
                  f"{str(r['author_score']):<6}{str(r['effective_author_score']):<8}"
                  f"{str(r['content_nature']):<16}{r['author_score_label']}", file=sys.stderr)
        if args.inplace:
            research["ad_summary"] = summarize_ad_risk(research["sources"], cfg)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(research, f, ensure_ascii=False, indent=2)
            print(f"[research][author-score] 已写回评分与广告识别字段到 {path}", file=sys.stderr)
        return

    if args.list_sites:
        run_research("", cfg, list_sites=True)
        return

    if not args.query:
        sys.exit("请提供 --query（检索）或 --plan-sources / --detect-ads / --score-authors / --list-sites")

    run_research(args.query, cfg, backend=args.backend,
                 max_sources=args.max_sources, out_path=args.out, do_extract=args.extract,
                 site=args.site, sites=args.sites, list_sites=False,
                 review_mode=args.reviews)


if __name__ == "__main__":
    main()
