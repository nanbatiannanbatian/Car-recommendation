#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chassis_fetch.py —— 底盘事实采集器（悬架形式 / 驱动形式，多源链 + 留痕）

为什么要有这个模块（2026-11-23 用户追问「底盘数据为什么缺，是不是代码问题」）：
  八维里操控维的规则（spec_score.control_parts 消费 susp_front/susp_rear/drive/
  zero_to_100）早就写好，但**底盘事实从未有任何结构化采集通道落地**：
    · 汽车之家 config JSON 的「底盘转向」「车轮制动」两组对**所有能源类型**整组
      为空（2026-11-23 实测：燃油凯美瑞/迈腾、纯电极氪7X/Model Y、增程+纯电同系
      深蓝S07/阿维塔07 底盘组有值数均为 0；旧边界文档「燃油车有值」已失实）。
      底盘表在渲染页由 AJAX 异步加载，SSR 与 config JSON 都不含值。
    · 易车参数页对脚本直连返回腾讯云 TCaptcha WAF 挑战（实测 1745B 验证码页）；
      懂车帝参数/搜索接口反爬（opencli dongchedi specs 实测 no __NEXT_DATA__）；
      有驾 DNS 不可达；官网参数页均为 JS SPA 壳无 SSR 数据表。
    · 合规红线（data_sources.json R4）：不绕反爬。因此易车/懂车帝的**参数表**
      不能作为自动化结构化通道。
  后果：操控维规则增量只吃到 score_audit 从人工取证文本里正则提取的零散字段，
  覆盖率极低且不可复算——「启源Q06 底盘好却不如 MONA L05」这类质疑的根因
  之一就是底盘事实没进结构化层。

本模块的源链（与 trim_sources 同模式，每步留痕 attempts）：
    S1 autohome_config  汽车之家 config JSON 按 id 提取（AH_CHASSIS_IDS）。
                        当前源边界：整组为空 → 如实 null；源恢复即自动生效。
    S2 article_text     媒体/官网文章正文正则提取，**每条事实带 url+quote+date 引文**。
                        两条进入方式：①调用方提供 URL（assistant domain_search 主通道
                        产物，--urls-json）；②S2a 自动发现（v10.51.2 通用化核心：
                        researcher 多引擎搜索 → URL 质量过滤 → 抓取正文 → 提取，
                        多源共识升 high、冲突留 null 并披露——任何车型都走此链，
                        不依赖人工逐车找链接）。
    S3 manual           人工核验事实（--facts-json 输入，同 trims_manual 纪律：
                        只填空不覆盖，随包 curated）。

通用化纪律（v10.51.2，2026-11-24 用户纠正「手动研究 21 台再 apply 只是榜单回填，
不是根治」）：
    · --fill-all：全库缺口车型（含部分缺口）逐个跑全源链、只填空写库——
      现有 80 台 + 未来新车 + 混合榜燃油/混动车，同一链条通用；
    · auto_trims --apply 新车入库后自动对接底盘链（接线在 auto_trims.main，
      失败只披露不影响入库）；
    · --models：非库车型跑链写 facts JSON（--out-json），不写库（无条目可写）；
    · 冲突披露：多源对悬前/悬后取值不一致 → 该字段留 null 并记 conflicts，不猜；
    · 逐车型 attempts/sources/conflicts 留痕 runs/chassis_fill_report_<date>.json。

写库纪律（与 auto_trims.refresh_entries 一致）：
    · 自动条目写 data/gen/trims.json，人工条目写 categories/trims_manual.json
      （存储层互斥，trims_store 合流读取）；
    · **只填空，绝不覆盖**已有值（人工核验 > 文章提取 > 汽车之家空值）；
    · 驱动形式逐配置赋值（后驱/四驱/空悬按配置不同），配置名无驱动关键词时
      显式留 null（缺数据 ≠ 差，不按名猜）；
    · 原子写（临时文件 + 替换）+ 写前自动备份。

用法：
    python3 -m modules.research.chassis_fetch --selftest
    python3 -m modules.research.chassis_fetch --report --candidates <候选池>
    python3 -m modules.research.chassis_fetch --facts-json <已核验事实.json> --apply
    python3 -m modules.research.chassis_fetch --urls-json <{车型:[url,...]}> --apply
    python3 -m modules.research.chassis_fetch --fill-all          # 全库缺口dry-run
    python3 -m modules.research.chassis_fetch --fill-all --apply  # 全库缺口写库
    python3 -m modules.research.chassis_fetch --models 轩逸,朗逸 --out-json <facts.json>
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.request

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)

TODAY = dt.date.today().isoformat()

# ── 文本提取的正则（保守：只提可靠硬参数，匹配不到就 null，不推断）────────────
# 悬架形式关键词（长词在前，避免「多连杆」先命中而漏掉「五连杆」）
SUSP_KEYS = ("双叉臂", "双横臂", "麦弗逊", "麦花臣", "五连杆", "多连杆",
             "扭力梁", "扭转梁", "整体桥", "钢板弹簧", "非独立")
# 驱动形式关键词（长词在前；「四驱」类必须先于裸「四驱」）
DRIVE_KEYS = ("前置四驱", "前置后驱", "前置前驱", "后置后驱", "后置四驱",
              "双电机四驱", "双电机后驱", "全轮驱动", "适时四驱", "全时四驱",
              "分时四驱", "四驱", "后驱", "前驱")
# 别名归一（媒体混用）
SUSP_ALIAS = {"麦花臣": "麦弗逊", "扭转梁": "扭力梁", "双横臂": "双叉臂"}

# 配置名里的驱动关键词 → 系列级驱动声明匹配（逐配置赋值的依据之一）
# 顺序即优先级；「双电机」在前（启源Q06「670 双电机智享型」类配置名无四驱词）
TRIM_DRIVE_TOKENS = (("双电机", ("双电机四驱", "双电机后驱")),
                     ("四驱", ("双电机四驱", "适时四驱", "全时四驱", "四驱")),
                     ("后驱", ("后置后驱", "后驱")),
                     ("前驱", ("前置前驱", "前驱")))

# 驱动形式别名归一（媒体混用；spec_score.control_parts 按「四驱/后驱」子串判分）
DRIVE_ALIAS = {"全轮驱动": "四驱"}

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


# ---------------------------------------------------------------- 文本提取
def _norm_susp(v: str) -> str:
    for k in SUSP_KEYS:
        if k in v:
            return SUSP_ALIAS.get(k, k)
    return v


def extract_from_text(text: str) -> dict:
    """从文章正文提取底盘事实（纯函数，fixture 可测）。

    返回 {susp_front, susp_rear, drive_forms[], quote, confidence}。
    匹配不到 → None/[]，不猜。quote 取首个命中句（引文落库用）。
    """
    t = re.sub(r"\s+", "", str(text or ""))          # 去空白再匹配，防换行截断
    out = {"susp_front": None, "susp_rear": None, "drive_forms": [],
           "quote": "", "confidence": None}

    def _susp(prefix):
        # 完整式：「前悬架采用/为/是…双叉臂…」；紧凑式：「前双叉臂独立悬架」
        pats = [prefix + r"悬(?:架|挂)?(?:类型|形式)?(?:为|采用|使用|是|搭配)?"
                         r"[^，。；;、]{0,14}?(" + "|".join(SUSP_KEYS) + r")",
                prefix + r"(" + "|".join(SUSP_KEYS) + r")"]
        for p in pats:
            m = re.search(p, t)
            if m:
                return _norm_susp(m.group(1))
        return None

    out["susp_front"] = _susp("前")
    out["susp_rear"] = _susp("后")

    # 驱动形式：全量命中后做子串去重——「四驱」是「双电机四驱/前置四驱」的子串，
    # 裸形式必须让位给更长形式（否则逐配置赋值会拿到歧义裸词）。
    seen, forms = set(), []
    for k in DRIVE_KEYS:
        if k in t and k not in seen:
            seen.add(k)
            forms.append(k)
    dedup = []
    for f in forms:
        if any(f != g and f in g for g in forms):
            continue                      # 裸形式被更长形式包含 → 丢弃
        dedup.append(DRIVE_ALIAS.get(f, f))
    out["drive_forms"] = dedup

    # 引文：首个命中的完整句（还原空白原文里取）
    raw = str(text or "")
    for kw in ("前悬架", "前悬", "后悬架", "后悬", "双叉臂", "麦弗逊",
               "多连杆", "五连杆", "扭力梁", "四驱", "后驱", "驱动形式", "驱动方式"):
        i = raw.find(kw)
        if i >= 0:
            seg = raw[max(0, i - 60):i + 80]
            out["quote"] = re.sub(r"\s+", " ", seg).strip()
            break
    n_hits = sum(1 for v in (out["susp_front"], out["susp_rear"]) if v) + \
        (1 if out["drive_forms"] else 0)
    out["confidence"] = ("high" if n_hits >= 2 else
                         "medium" if n_hits == 1 else None)
    return out


# ---------------------------------------------------------------- 抓取
def fetch_text(url: str, timeout: int = 18) -> str | None:
    """抓页面正文（去 script/style/标签）。失败返回 None，不抛。"""
    import ssl
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            html = r.read().decode("utf-8", "replace")
    except Exception:                                  # noqa: BLE001
        return None
    text = re.sub(r"(?is)<script.*?</script>", " ", html)
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    import html as _h
    return re.sub(r"\s+", " ", _h.unescape(text)).strip()


# ---------------------------------------------------------------- S1 汽车之家
def chassis_from_config(cfg: dict) -> dict:
    """汽车之家 config JSON → {specid: {drive_form, susp_front, susp_rear}}。

    按 id 定位（字段名被 CSS 混淆，跨车系稳定）。值当前全空（源边界），
    但解析器必须先在——源恢复即自动生效，不假装采不到也不假装采到。
    """
    from modules.research.param_fields import AH_CHASSIS_IDS
    rev = {v: k for k, v in AH_CHASSIS_IDS.items()}
    out: dict[str, dict] = {}
    for grp in ((cfg.get("result") or {}).get("paramtypeitems") or []):
        for it in (grp.get("paramitems") or []):
            fid = it.get("id")
            key = rev.get(fid)
            if not key:
                continue
            for v in (it.get("valueitems") or []):
                sid2 = v.get("specid")
                if sid2 is None:
                    continue
                val = str(v.get("value") or "").strip()
                val = re.sub(r"<[^>]+>", "", val).strip()
                if val in ("", "-", "—"):
                    continue
                slot = out.setdefault(str(sid2), {})
                if slot.get(key) in (None, ""):
                    slot[key] = val
    return out


def _series_consensus(per_spec: dict, key: str) -> str | None:
    """系列级共识：非空值里去重，取共同值；分歧 → None（不猜）。"""
    vals = {str(v.get(key)).strip() for v in per_spec.values() if v.get(key)}
    vals = {v for v in vals if v and v not in ("-", "—")}
    if len(vals) == 1:
        return vals.pop()
    return None                      # 分歧或全空都记 null（如实，不多数决）


# ---------------------------------------------------------------- S1c 官方配置表
# 官方 SSR 配置表注册表（v10.51.2）：部分品牌官网配置表是 SSR 且含底盘事实
# （实测阿维塔 avatr.com/configuration?car=11、蔚来 nio.cn 配置页可直连抓取）。
# curated 随包维护（同 trims_manual 纪律：人工核验、带 URL、可审计）；
# 新品牌官方配置表发现后追加到此表，collect_model 自动优先走此通道（置信度 high）。
# 这是「主通道人工核验 + 自动化落库」架构在底盘通道的体现——搜索找链接是脆弱
# fallback（实测 bing/baidu 对中文长尾查询质量不稳），官方一手表才是高置信源。
OFFICIAL_CONFIG_SEEDS = {
    # 实测可直连抓取（urllib）+ 正文含底盘事实的官方 SSR 配置表才入此表；
    # 深蓝 deepal.com.cn（len=9 被挡）、蔚来 nio.cn（len=0 被挡）已实测剔除——
    # 注册表纪律：只留验证可用源，新增品牌先 probe 验证再登记。
    # 阿维塔07/06（avatr.com/configuration?car=11|06，SSR 全参数组）、
    # 小鹏MONA M03（xiaopeng.com/m03/configuration.html，SSR，2026-11-24 probe len=5785 通过）
    "阿维塔07 纯电版": "https://www.avatr.com/configuration?car=11",
    "阿维塔07 增程版": "https://www.avatr.com/configuration?car=11",
    "阿维塔06": "https://www.avatr.com/configuration?car=06",
    "小鹏MONA M03": "https://www.xiaopeng.com/m03/configuration.html?fromtoM03",
}


def _try_official(model: str, result: dict, attempts: list) -> None:
    """S1c：官方 SSR 配置表（ curated 注册表，高置信一手）。只填空。"""
    url = OFFICIAL_CONFIG_SEEDS.get(model)
    if not url:
        return
    try:
        text = fetch_text(url)
        ex = extract_from_text(text or "")
    except Exception as ex:                        # noqa: BLE001
        attempts.append({"source": "official_config", "ok": False,
                         "detail": f"{type(ex).__name__}: {str(ex)[:60]}"})
        return
    if not (ex["susp_front"] or ex["susp_rear"] or ex["drive_forms"]):
        attempts.append({"source": "official_config", "ok": False,
                         "detail": "官方配置表正文未提取到底盘事实"})
        return
    for k in ("susp_front", "susp_rear"):
        if not result[k] and ex.get(k):
            result[k] = ex[k]
    for f in ex.get("drive_forms") or []:
        if f not in result["drive_forms"]:
            result["drive_forms"].append(f)
    result.setdefault("conflicts", [])
    result["sources"].append({"url": url, "quote": ex.get("quote") or "",
                              "date": TODAY, "confidence": "high",
                              "via": "official_config"})
    attempts.append({"source": "official_config", "ok": True,
                     "detail": f"官方SSR配置表 悬前={ex['susp_front']} 悬后={ex['susp_rear']} "
                               f"驱动={ex['drive_forms']}"})


# ---------------------------------------------------------------- S2/S2a
def _merge_article_parts(parts: list[dict]) -> dict:
    """多源文章提取合并（纯函数，fixture 可测）。

    规则（v10.51.2 通用化）：
      · susp_front/susp_rear：单源→取其值；多源一致→共识；多源冲突→None 并记
        conflicts（不猜——媒体口徑打架时效验成本高，留 null + 披露）；
      · drive_forms：多源并集（多形式系列是合法事实，如「后驱/四驱两种」）；
      · 置信度：≥2 源命中同字段 → high；单源 → 该源自带 medium/high。
    """
    merged = {"susp_front": None, "susp_rear": None, "drive_forms": [], "conflicts": []}
    for key in ("susp_front", "susp_rear"):
        vals = []
        for p in parts:
            v = p.get(key)
            if v and v not in vals:
                vals.append(v)
        if len(vals) == 1:
            merged[key] = vals[0]
        elif len(vals) > 1:
            merged["conflicts"].append(
                f"{key} 多源冲突：{' / '.join(vals)}（留 null 不猜，需人工核验）")
    for p in parts:
        for f in p.get("drive_forms") or []:
            if f not in merged["drive_forms"]:
                merged["drive_forms"].append(f)
    return merged


def _try_urls(model: str, urls, attempts: list) -> tuple[dict, list[dict]]:
    """文章正文通道：逐个 URL 抓取 + 提取 + 多源共识/冲突披露。"""
    parts = []
    for u in urls or []:
        text = fetch_text(u)
        if not text:
            attempts.append({"source": "article_text", "ok": False,
                             "detail": f"抓取失败/空壳 {str(u)[:80]}"})
            continue
        ex = extract_from_text(text)
        if not (ex["susp_front"] or ex["susp_rear"] or ex["drive_forms"]):
            attempts.append({"source": "article_text", "ok": False,
                             "detail": "正文无底盘事实（文章不涉及悬架/驱动）"})
            continue
        parts.append({"url": u, "susp_front": ex["susp_front"], "susp_rear": ex["susp_rear"],
                      "drive_forms": ex["drive_forms"], "quote": ex.get("quote") or "",
                      "confidence": ex.get("confidence") or "medium"})
        attempts.append({"source": "article_text", "ok": True,
                         "detail": f"提取 悬前={ex['susp_front']} 悬后={ex['susp_rear']} "
                                   f"驱动={'/'.join(ex['drive_forms'][:2])}"})
    merged = _merge_article_parts(parts)
    n_ok = len(parts)
    sources = [{"url": p["url"], "quote": p.get("quote") or "", "date": TODAY,
                "confidence": ("high" if n_ok >= 2 else p.get("confidence") or "medium")}
               for p in parts]
    return merged, sources


# ---------------------------------------------------------------- S2a 自动发现
# URL 质量过滤（抓取预算给可信媒体文章，防首页/百科/视频/经销商/车系页浪费）
_MEDIA_HINT = ("autohome.com.cn", "yiche.com", "bitauto.com", "163.com", "sina.com.cn",
               "sohu.com", "qq.com", "thepaper.cn", "zhihu.com", "cars", "auto")
_EXCLUDE_HINT = ("/brand", "/series/", "index.html", "baike.baidu.com", "/video",
                 "dealer.", "/dealer", "config/series", "/sales")


def _url_quality(u: str) -> bool:
    s = str(u or "")
    if not s.startswith("http"):
        return False
    low = s.lower()
    if any(x in low for x in _EXCLUDE_HINT):
        return False
    # 汽车之家车系页 /<纯数字>/（参数摘要页，非评测正文）排除；
    # 文章页 /<车系id>/<年月>/<文章id>.html 与之区分（路径段数≥3 且以 .html 结尾）
    m = re.match(r"https?://[^/]+/(\d+)/?$", low)
    if m:
        return False
    return any(x in low for x in _MEDIA_HINT)


def discover_article_urls(model: str, search_fn=None, max_urls: int = 5) -> list[str]:
    """搜索发现文章 URL（多查询去重 + 质量过滤）。search_fn 可注入（selftest 离线）。

    查询设计：前悬/后悬/驱动 是参数页式措辞（媒体评测正文高频出现）；
    底盘+悬架形式 兜底换措辞。bing 为主，命中 <2 条质量 URL 时 baidu 兜底
    （researcher 声明搜索为通用 fallback，文章正文提取才是事实通道——
    搜索只负责「找链接」）。
    """
    from modules.research import researcher as R
    fn = search_fn or R.search
    urls, seen = [], set()
    for q in (f"{model} 前悬架 后悬架 驱动",
              f"{model} 底盘 悬架形式 双叉臂 麦弗逊",
              f"{model} 底盘 驱动方式 评测"):
        try:
            rs = fn(q, backend="bing", n=8) or []
        except Exception:                                  # noqa: BLE001
            rs = []
        for r in rs:
            u = str(r.get("url") or "")
            if u and u not in seen and _url_quality(u):
                seen.add(u)
                urls.append(u)
        if len(urls) >= max_urls:
            break
    if len(urls) < 2:
        # bing 命中不足 → baidu 兜底（同查询）
        for q in (f"{model} 前悬架 后悬架", f"{model} 底盘 悬架"):
            try:
                rs = fn(q, backend="baidu", n=8) or []
            except Exception:                              # noqa: BLE001
                rs = []
            for r in rs:
                u = str(r.get("url") or "")
                if u and u not in seen and _url_quality(u):
                    seen.add(u)
                    urls.append(u)
            if len(urls) >= max_urls:
                break
    return urls[:max_urls]


# ---------------------------------------------------------------- 主入口
def collect_model(model: str, ah_sid: str | None = None, urls=(),
                  allow_search: bool = True) -> dict:
    """采集一款车的底盘事实。返回结果 dict（不写库）。"""
    attempts: list[dict] = []
    result = {"model": model, "ah_sid": ah_sid,
              "susp_front": None, "susp_rear": None, "drive_forms": [],
              "sources": [], "attempts": attempts, "conflicts": []}

    # S1c 官方 SSR 配置表（curated 注册表，高置信一手；只填空）
    _try_official(model, result, attempts)

    # S1 汽车之家结构化（当前源边界：底盘组整组为空）
    if ah_sid:
        try:
            from modules.research import auto_trims as AT
            cfg, _err = AT._fetch_config_json(str(ah_sid))
            if cfg is None:
                attempts.append({"source": "autohome_config", "ok": False,
                                 "detail": "config JSON 抓取失败"})
            else:
                per = chassis_from_config(cfg)
                sf = _series_consensus(per, "susp_front")
                sr = _series_consensus(per, "susp_rear")
                df = _series_consensus(per, "drive_form")
                if sf or sr or df:
                    result["susp_front"], result["susp_rear"] = sf, sr
                    if df:
                        result["drive_forms"] = [df]
                    attempts.append({"source": "autohome_config", "ok": True,
                                     "detail": f"结构化提取 悬前={sf} 悬后={sr} 驱动={df}"})
                else:
                    attempts.append({"source": "autohome_config", "ok": False,
                                     "detail": "底盘转向组整组为空（汽车之家源边界，"
                                               "非采集遗漏）"})
        except Exception as ex:                       # noqa: BLE001
            attempts.append({"source": "autohome_config", "ok": False,
                             "detail": f"{type(ex).__name__}: {str(ex)[:80]}"})
    else:
        attempts.append({"source": "autohome_config", "ok": False,
                         "detail": "未提供 ah_sid，跳过"})

    def _absorb(m):
        for k in ("susp_front", "susp_rear"):
            if not result[k] and m.get(k):
                result[k] = m[k]
        for f in m.get("drive_forms") or []:
            if f not in result["drive_forms"]:
                result["drive_forms"].append(f)
        for c in m.get("conflicts") or []:
            if c not in result["conflicts"]:
                result["conflicts"].append(c)

    have = result["susp_front"] or result["susp_rear"] or result["drive_forms"]
    if not have:
        # S2 文章正文（调用方提供 URL = assistant domain_search 主通道产物）
        m2, src = _try_urls(model, urls, attempts)
        _absorb(m2)
        result["sources"] = src
    if not (result["susp_front"] or result["susp_rear"] or result["drive_forms"]):
        # S2a 自动发现（通用化兜底：搜索发现 → 质量过滤 → 抓取 → 提取）
        if allow_search:
            urls_auto = discover_article_urls(model)
            m3, src3 = _try_urls(model, urls_auto, attempts)
            _absorb(m3)
            result["sources"] += src3
    result["resolved"] = {k: bool(result[k]) for k in
                          ("susp_front", "susp_rear")} | \
        {"drive_form": bool(result["drive_forms"])}
    return result


# ---------------------------------------------------------------- 存储层
def _ensure_chassis_keys(db: dict) -> int:
    """所有条目的所有配置补齐底盘三字段键（缺 → null）。返回补齐数。"""
    n = 0
    for e in db.get("entries", []) + db.get("out_of_board_examples", []):
        for t in e.get("trims") or []:
            for k in ("susp_front", "susp_rear", "drive_form"):
                if k not in t:
                    t[k] = None
                    n += 1
    return n


def _assign_drive_per_trim(trim_name: str, drive_forms: list,
                           drive_by_trim: dict | None = None) -> str | None:
    """逐配置驱动赋值（优先级：curated 显式映射 > 系列单一形式 > 配置名关键词）。

    三种依据都不猜（v10.51.0 拍板）：
      ① drive_by_trim：人工核验的逐配置显式映射（子串匹配，最长优先）——覆盖
        「配置名不含驱动词但事实已核验」的车（如乐道L60 Ultra+ 实为四驱）；
      ② 系列只有一种驱动形式（多源一致）→ 全配置赋值（系列级已核验事实，
        如 T6 上市稿「全系单电机后驱」、G6 2026「全系后驱单电机」）；
      ③ 配置名驱动关键词（四驱/后驱/双电机/前驱…）× 系列声明匹配才赋值；
      ④ 以上都不满足 → 显式 null（缺数据≠差，不按名猜）。
    """
    name = str(trim_name or "")
    # 特斯拉口径归一：后轮驱动→后驱、全轮驱动→四驱（与中文媒体口径对齐）
    name_norm = name.replace("后轮驱动", "后驱").replace("全轮驱动", "四驱")
    # ① curated 逐配置映射（最长子串优先，防「智驾」误配「四驱智驾」类短键）
    if drive_by_trim:
        hits = sorted((k for k in drive_by_trim if k and k in name_norm),
                      key=len, reverse=True)
        if hits:
            return drive_by_trim[hits[0]]
    forms = list(drive_forms or [])
    # ② 系列单一形式 → 全配置（已核验的系列级事实，非推断）
    if len(forms) == 1:
        return forms[0]
    if not forms:
        return None
    # ③ 配置名关键词 × 系列声明
    for token, candidates in TRIM_DRIVE_TOKENS:
        if token in name_norm:
            for c in candidates:
                if c in forms:
                    return c
            return None
    return None


def merge_into_db(db: dict, facts: dict[str, dict], mark: str) -> dict:
    """把 {车型: {susp_front, susp_rear, drive_forms, sources}} 合入 trims db。

    纪律：只填空不覆盖（人工核验 > 文章 > 汽车之家空值）；驱动逐配置按名赋值，
    配置名无关键词留 null；来源挂到条目 chassis_sources（刷新不被模板文案抹掉）。
    """
    touched = 0
    for e in db.get("entries", []) + db.get("out_of_board_examples", []):
        m = e.get("model")
        f = facts.get(m)
        if not f:
            continue
        n_fill = 0
        for t in e.get("trims") or []:
            if not t.get("susp_front") and f.get("susp_front"):
                t["susp_front"] = f["susp_front"]
                n_fill += 1
            if not t.get("susp_rear") and f.get("susp_rear"):
                t["susp_rear"] = f["susp_rear"]
                n_fill += 1
            if not t.get("drive_form"):
                dv = _assign_drive_per_trim(t.get("name"), f.get("drive_forms") or [],
                                            f.get("drive_by_trim"))
                if dv:
                    t["drive_form"] = dv
                    n_fill += 1
        if n_fill:
            touched += 1
            srcs = [dict(s, mark=mark) for s in (f.get("sources") or [])]
            if srcs:
                e.setdefault("chassis_sources", [])
                have = {s.get("url") for s in e["chassis_sources"]}
                e["chassis_sources"] += [s for s in srcs if s.get("url") not in have]
    return {"touched": touched}


def _atomic_write(path: str, obj: dict) -> None:
    """原子写（临时文件 + 替换）+ 自动备份。"""
    import shutil
    bak = path + ".bak-" + TODAY
    if os.path.exists(path) and not os.path.exists(bak):
        shutil.copy2(path, bak)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def apply_facts(facts: dict[str, dict], dry_run: bool = True) -> dict:
    """把已核验事实合入两层 trims 库（自动库 + 人工库，都只填空）。

    facts: {车型: {susp_front, susp_rear, drive_forms, sources}}
    """
    report = {"auto": None, "manual": None}
    auto_p = str(paths.gen("trims.json"))
    if os.path.exists(auto_p):
        db = json.load(open(auto_p, encoding="utf-8"))
        _ensure_chassis_keys(db)
        r = merge_into_db(db, facts, "article_text")
        report["auto"] = r
        if not dry_run:
            _atomic_write(auto_p, db)
    man_p = str(paths.cat("trims_manual.json"))
    if os.path.exists(man_p):
        db = json.load(open(man_p, encoding="utf-8"))
        _ensure_chassis_keys(db)
        r = merge_into_db(db, facts, "manual_verified")
        report["manual"] = r
        if not dry_run:
            _atomic_write(man_p, db)
    return report


# ---------------------------------------------------------------- 通用补齐（v10.51.2）
def _entry_sid(entry: dict) -> str | None:
    """从条目 url 解析汽车之家 sid（自动条目 url = config 移动版含 /series/<sid>）。"""
    m = re.search(r"series/(\d+)", str(entry.get("url") or ""))
    return m.group(1) if m else None


def fill_models(models, apply=False, ah_index=None, collect_fn=None) -> dict:
    """对给定车型逐个跑全源链（S1→S2→S2a），只填空写库。返回逐车型报告（留痕可审计）。

    collect_fn 可注入（selftest 离线）；ah_index 提供 sid 兜底（条目 url 无 sid 时）。
    """
    tdb = load_trims_index()
    collect = collect_fn or collect_model
    facts, per_model = {}, {}
    for m in models:
        e = tdb.get(m) or {}
        sid = _entry_sid(e)
        if not sid and ah_index:
            hit = ah_index.get(m)
            sid = hit.get("sid") if isinstance(hit, dict) else None
        r = collect(m, ah_sid=sid)
        per_model[m] = {"resolved": r["resolved"], "attempts": r["attempts"],
                        "sources": r["sources"], "conflicts": r.get("conflicts") or []}
        if r["susp_front"] or r["susp_rear"] or r["drive_forms"]:
            facts[m] = {"susp_front": r["susp_front"], "susp_rear": r["susp_rear"],
                        "drive_forms": r["drive_forms"], "sources": r["sources"],
                        "conflicts": r.get("conflicts") or []}
    rep = apply_facts(facts, dry_run=not apply)
    return {"per_model": per_model, "facts": facts, "filled": rep,
            "n_collected": len(facts)}


def gap_models() -> list[str]:
    """全库底盘缺口车型：三字段任一在系列级未解析（全空或部分缺失都算）。

    人工条目同样在查（人工核验事实优先，但没写就是缺口，补齐链同样适用）。
    """
    tdb = load_trims_index()
    out = []
    for m, e in tdb.items():
        trims = e.get("trims") or []
        if not trims:
            continue
        got = {"susp_front": False, "susp_rear": False, "drive_form": False}
        for t in trims:
            for k in got:
                if t.get(k):
                    got[k] = True
        if not all(got.values()):
            out.append(m)
    return out


def fill_all(apply=False) -> dict:
    """通用补齐：全库缺口车型逐个采集 → 只填空写库（什么车都走同一条链）。"""
    gaps = gap_models()
    rep = fill_models(gaps, apply=apply)
    rep["gap_models"] = gaps
    return rep


def _write_fill_report(rep: dict) -> str:
    """逐车型采集留痕写 runs/（attempts/sources/conflicts 全量，可审计）。"""
    out = os.path.join(HERE, "runs", f"chassis_fill_report_{TODAY}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=1)
    return out


# ---------------------------------------------------------------- 覆盖率
def load_trims_index() -> dict[str, dict]:
    """统一走 trims_store 的合并读视图（人工优先）。"""
    from modules.data import trims_store as TS
    return TS.load()


def coverage(models: list[str]) -> dict:
    """底盘三字段覆盖率（哨兵数据）。缺 = 显式缺口，不是「数据源没有」的伪装。"""
    from modules.data import trims_store as TS
    tdb = load_trims_index()
    per = {}
    for m in models:
        e = tdb.get(m) or {}
        trims = e.get("trims") or []
        st = {"susp_front": False, "susp_rear": False, "drive_form": False,
              "n_trims": len(trims), "origin": TS.provenance(e).get("level")}
        for t in trims:
            if t.get("susp_front"):
                st["susp_front"] = True
            if t.get("susp_rear"):
                st["susp_rear"] = True
            if t.get("drive_form"):
                st["drive_form"] = True
        st["n_fields"] = sum(1 for k in ("susp_front", "susp_rear", "drive_form")
                             if st[k])
        per[m] = st
    n = len(models) or 1
    full = sum(1 for s in per.values() if s["n_fields"] == 3)
    partial = sum(1 for s in per.values() if 0 < s["n_fields"] < 3)
    return {"models": per, "n": len(models), "full": full, "partial": partial,
            "none": len(models) - full - partial,
            "pct": round((full + partial) / n * 100, 1),
            # 显式缺口清单（哨兵输出：缺口可见，不许静默伪装成「数据源没有」）
            "missing": sorted(m for m, s in per.items() if s["n_fields"] == 0),
            "partial_models": sorted(m for m, s in per.items()
                                     if 0 < s["n_fields"] < 3)}


# ---------------------------------------------------------------- 自测/CLI
def selftest() -> int:
    """离线自测：文本提取正/负 fixture + config 解析 + 归并纪律，不联网。"""
    # ① 正 fixture：极氪7X 式正文（多驱动形式同文）
    pos = ("底盘方面，它全系标配前双叉臂、后五连杆独立悬架，还搭载液压衬套。"
           "ZEEKR 7X有双电机四驱和后置后驱两种驱动形式，匹配电动车单速变速箱。"
           "高配车型配备空气悬架系统。")
    ex = extract_from_text(pos)
    assert ex["susp_front"] == "双叉臂", ex
    assert ex["susp_rear"] == "五连杆", ex
    assert "双电机四驱" in ex["drive_forms"] and "后置后驱" in ex["drive_forms"], ex
    assert ex["confidence"] == "high", ex
    # ② 紧凑式「前双叉臂式独立悬架」+ 别名归一（麦花臣→麦弗逊、扭转梁→扭力梁）
    ex2 = extract_from_text("前双叉臂式独立悬挂，后扭力梁，麦花臣前悬不算。")
    assert ex2["susp_front"] == "双叉臂", ex2
    assert ex2["susp_rear"] == "扭力梁", ex2
    # ③ 负 fixture：无底盘事实 → 全 null，不猜
    ex3 = extract_from_text("这车外观很好看，空间大，续航扎实，充电快。")
    assert ex3["susp_front"] is None and ex3["susp_rear"] is None
    assert ex3["drive_forms"] == [] and ex3["confidence"] is None, ex3
    # ④ 提及但无形式（「后悬架调校」）→ 不误判
    ex4 = extract_from_text("后悬架调校偏舒适，前悬滤震不错。")
    assert ex4["susp_front"] is None and ex4["susp_rear"] is None, ex4
    # ⑤ config 解析：id 定位 + 空值如实 null
    cfg = {"result": {"paramtypeitems": [
        {"groupname": "底盘转向", "paramitems": [
            {"id": 1231, "name": "驱动方式", "valueitems": [
                {"specid": "s1", "value": "后置后驱"}, {"specid": "s2", "value": ""}]},
            {"id": 1226, "name": "前悬架类型", "valueitems": [
                {"specid": "s1", "value": "双叉臂"}, {"specid": "s2", "value": "-"}]},
        ]},
        {"groupname": "基本参数", "paramitems": [
            {"id": 1185, "name": "最大功率", "valueitems": [
                {"specid": "s1", "value": "310"}]},
        ]}]}}
    per = chassis_from_config(cfg)
    assert per["s1"]["drive_form"] == "后置后驱", per
    assert per["s1"]["susp_front"] == "双叉臂", per
    assert per.get("s2", {}) == {}, per          # 空值不建条目
    assert _series_consensus(per, "drive_form") == "后置后驱"
    assert _series_consensus({"a": {"drive_form": "后驱"}, "b": {"drive_form": "四驱"}},
                             "drive_form") is None   # 分歧不多数决
    # ⑥ 逐配置驱动赋值：名含关键词 × 系列声明
    assert _assign_drive_per_trim("100kWh 四驱 Ultra", ["后置后驱", "双电机四驱"]) \
        == "双电机四驱"
    assert _assign_drive_per_trim("75kWh 后驱 Max", ["后置后驱", "双电机四驱"]) \
        == "后置后驱"
    # 系列单一形式 → 全配置赋值（T6/G6 全系后驱场景：已核验系列级事实，非猜）
    assert _assign_drive_per_trim("75kWh Max", ["后置后驱"]) == "后置后驱"
    # 多形式且无关键词无映射 → 显式 null（不猜）
    assert _assign_drive_per_trim("75kWh Max", ["后置后驱", "双电机四驱"]) is None
    # ⑦ 合入纪律：只填空不覆盖
    db = {"entries": [{"model": "测试车", "trims": [
        {"name": "后驱版", "susp_front": None, "susp_rear": None, "drive_form": None},
        {"name": "四驱版", "susp_front": "已有值", "susp_rear": None, "drive_form": None}]}]}
    merge_into_db(db, {"测试车": {"susp_front": "双叉臂", "susp_rear": "五连杆",
                                  "drive_forms": ["后置后驱", "双电机四驱"],
                                  "sources": [{"url": "http://x", "quote": "q",
                                               "date": TODAY, "confidence": "high"}]}},
                  "test")
    tr = db["entries"][0]["trims"]
    assert tr[0]["susp_front"] == "双叉臂" and tr[0]["drive_form"] == "后置后驱"
    assert tr[1]["susp_front"] == "已有值", "只填空不覆盖"
    assert tr[1]["drive_form"] == "双电机四驱"
    assert db["entries"][0]["chassis_sources"][0]["mark"] == "test"
    # ⑧ 赋值优先级 v2：curated 显式映射 > 系列单一形式 > 配置名关键词
    assert _assign_drive_per_trim("85kWh Ultra+", ["后置后驱", "双电机四驱"],
                                  {"Ultra+": "双电机四驱"}) == "双电机四驱"
    assert _assign_drive_per_trim("660 Max 激光雷达版", ["后置后驱"]) == "后置后驱"
    assert _assign_drive_per_trim("Model Y 2027款 后轮驱动",
                                  ["后置后驱", "双电机四驱"]) == "后置后驱"
    assert _assign_drive_per_trim("Model Y 2027款 长续航全轮驱动",
                                  ["后置后驱", "双电机四驱"]) == "双电机四驱"
    assert _assign_drive_per_trim("75kWh Max", ["后置后驱", "双电机四驱"]) is None
    assert _assign_drive_per_trim("670 双电机智享型", ["后置后驱", "双电机四驱"]) \
        == "双电机四驱"
    # ⑨ 多源共识/冲突（v10.51.2 通用化：共识升级、冲突留 null+披露、驱动并集）
    parts = [
        {"url": "http://a", "susp_front": "双叉臂", "susp_rear": "五连杆",
         "drive_forms": ["后置后驱"], "quote": "q1", "confidence": "medium"},
        {"url": "http://b", "susp_front": "双叉臂", "susp_rear": "多连杆",
         "drive_forms": ["后置后驱", "双电机四驱"], "quote": "q2", "confidence": "medium"},
    ]
    mg = _merge_article_parts(parts)
    assert mg["susp_front"] == "双叉臂", "两源一致应共识"
    assert mg["susp_rear"] is None and mg["conflicts"], "悬后冲突必须留 null+披露"
    assert sorted(mg["drive_forms"]) == ["双电机四驱", "后置后驱"], "驱动并集"
    # ⑩ URL 质量过滤（首页/百科/视频/经销商页不浪费抓取预算）
    assert _url_quality("https://www.autohome.com.cn/1234/202601/1234567.html")
    assert _url_quality("https://www.yiche.com/article/abc-123.html")
    assert not _url_quality("https://www.autohome.com.cn/brand/")
    assert not _url_quality("https://baike.baidu.com/item/xxx")
    assert not _url_quality("https://example.com/video/123")
    assert not _url_quality("not-a-url")
    # ⑪ 自动发现离线（注入 stub 搜索，不联网）
    def _stub_search(q, backend="bing", n=8):
        return [{"url": "https://www.yiche.com/article/abc-123.html", "title": "t"}]
    assert discover_article_urls("测试车", search_fn=_stub_search) == \
        ["https://www.yiche.com/article/abc-123.html"]
    # ⑫ fill_models 迭代+留痕（stub 采集，不联网；dry-run 不写库）
    def _stub_collect(model, ah_sid=None, urls=(), allow_search=True):
        return {"model": model, "susp_front": "双叉臂", "susp_rear": "五连杆",
                "drive_forms": ["前置前驱"], "sources": [{"url": "http://x", "quote": "q",
                                                          "date": TODAY,
                                                          "confidence": "high"}],
                "attempts": [], "conflicts": [],
                "resolved": {"susp_front": True, "susp_rear": True, "drive_form": True}}
    rep2 = fill_models(["缺口车"], apply=False, collect_fn=_stub_collect)
    assert rep2["n_collected"] == 1 and rep2["facts"]["缺口车"]["susp_front"] == "双叉臂"
    assert rep2["per_model"]["缺口车"]["resolved"]["drive_form"]
    print("SELFTEST PASS · chassis_fetch 文本提取/config解析/逐配置赋值/只填空不覆盖"
          "/多源共识冲突/URL过滤/自动发现/通用补齐")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--facts-json", help="已核验事实 {车型:{susp_front,...}}")
    ap.add_argument("--urls-json", help="{车型:[url,...]} 走文章正文通道")
    ap.add_argument("--models", help="逗号分隔车型列表（非库车型：跑链写 facts JSON，不写库）")
    ap.add_argument("--out-json", help="非库车型采集结果输出路径（配合 --models）")
    ap.add_argument("--fill-all", action="store_true",
                    help="全库缺口车型自动补齐（通用链：搜索发现文章→正文提取→共识/冲突披露）")
    ap.add_argument("--ah-index", help="ah_grade_index.json（取 sid 走 S1）")
    ap.add_argument("--candidates", help="候选池（--report 覆盖率用）")
    ap.add_argument("--apply", action="store_true", help="写库（默认 dry-run）")
    ap.add_argument("--report", action="store_true", help="只出覆盖率报告")
    a = ap.parse_args()

    if a.selftest or not any((a.facts_json, a.urls_json, a.models, a.report, a.fill_all)):
        return selftest()

    if a.report:
        models = []
        if a.candidates and os.path.exists(a.candidates):
            d = json.load(open(a.candidates, encoding="utf-8"))
            models = [c["model"] for c in (d.get("candidates") or d)]
        elif a.models:
            models = [m.strip() for m in a.models.split(",") if m.strip()]
        rep = coverage(models)
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 0

    # --fill-all：全库缺口车型（含部分缺口）逐个跑全源链、只填空写库
    if a.fill_all:
        rep = fill_all(apply=a.apply)
        rep_path = _write_fill_report(rep)
        print(json.dumps({"gap_models": rep["gap_models"], "n_gap": len(rep["gap_models"]),
                          "n_collected": rep["n_collected"], "filled": rep["filled"],
                          "report": rep_path}, ensure_ascii=False, indent=1))
        if not a.apply:
            print("(dry-run，未写库；加 --apply 生效)")
        return 0

    # --models：非库车型（如混合榜燃油/混动车）跑链写 facts JSON，不写库（无条目可写）
    if a.models and not a.urls_json:
        models = [m.strip() for m in a.models.split(",") if m.strip()]
        rep = fill_models(models, apply=False)
        out = a.out_json or os.path.join(HERE, "runs", f"chassis_facts_auto_{TODAY}.json")
        payload = {m: rep["facts"].get(m) for m in models if m in rep["facts"]}
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        _write_fill_report(rep)
        print(f"[ok] 非库车型采集 {rep['n_collected']}/{len(models)} 款 → {out}")
        return 0

    facts: dict[str, dict] = {}
    if a.facts_json:
        facts = json.load(open(a.facts_json, encoding="utf-8"))
    if a.urls_json:
        urls_map = json.load(open(a.urls_json, encoding="utf-8"))
        idx = {}
        if a.ah_index and os.path.exists(a.ah_index):
            idx = json.load(open(a.ah_index, encoding="utf-8"))
        for m, urls in urls_map.items():
            sid = None
            hit = idx.get(m) or {}
            sid = hit.get("sid") if isinstance(hit, dict) else None
            r = collect_model(m, ah_sid=sid, urls=urls)
            facts[m] = {"susp_front": r["susp_front"], "susp_rear": r["susp_rear"],
                        "drive_forms": r["drive_forms"], "sources": r["sources"],
                        "attempts": r["attempts"], "conflicts": r.get("conflicts") or []}
            print(f"[{m}] 悬前={r['susp_front']} 悬后={r['susp_rear']} "
                  f"驱动={r['drive_forms']} attempts={len(r['attempts'])}",
                  file=sys.stderr)
        # v10.51.2：采集结果全量留痕 runs/（非库车型同样可取——混合榜构建器消费；
        # 库内车型走下方 apply_facts 只填空写库）
        dump = os.path.join(HERE, "runs", f"chassis_facts_auto_{TODAY}.json")
        with open(dump, "w", encoding="utf-8") as f:
            json.dump(facts, f, ensure_ascii=False, indent=1)
        print(f"[ok] 采集留痕 → {dump}（{len(facts)} 款）", file=sys.stderr)
    rep = apply_facts(facts, dry_run=not a.apply)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    if not a.apply:
        print("(dry-run，未写库；加 --apply 生效)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
