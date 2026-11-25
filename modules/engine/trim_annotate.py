#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐配置人工口径标注（trim_annotate.py）——统一量纲的配置分生成。

方法论（与六配置榜 compare_data.json 一致）：
  base_scores = 候选池人工标注的车系级 8 维分（13 款全有、同口径）
  + trim_deltas = 按该配置相对车系评分配置的客观差异做加减（带置信度）
  final = Σ(base+delta) × 权重 × 10（百分制，与车系分同量纲）

为什么不是 spec_score 规则从零算：
  spec_score 缺轴距/悬架/芯片等，6 维插补、系统性偏低约 15 分，与人工分不同量纲，
  混排会让名次失真。本模块以人工 base 为锚，只调整配置真正改变的维度。

delta 规则（只调有客观证据的维）：
  - 800V/900V（+0.5~1.0 三电，high）：配置表 charge 字段明确
  - 续航差（每 +100km 三电 +0.3，medium）：cltc_km 对比
  - 大电池（每 +10kWh 三电 +0.2，medium）：battery_kwh 对比
  - 激光雷达有无（智驾 +0.8，high）：key_configs
  - 价格差（性价比按 kWh/万元 相对变化，medium）
  每个 delta 带 conf（high/medium/low），整体置信取最低。

用法：
    from modules.engine.trim_annotate import annotate_all
    data = annotate_all(cands, cfg)   # {车型: {trims:[{name,scores,total,conf,deltas}]}}
    python3 trim_annotate.py --selftest
"""
from __future__ import annotations
import argparse
import json
import os
import re

import paths

HERE = str(paths.ROOT)
TRIMS = str(paths.cat("trims.json"))
W = {"powertrain": 0.18, "safety": 0.162, "adas": 0.135, "space": 0.126,
     "value": 0.117, "control": 0.09, "cabin": 0.09, "quality": 0.1}

# v10.15.9：页面逐配置表走本模块（人工基准 + delta），不走 spec_score.score_trim。
# 旧实现 clamp(0,10) 让 65/427 个配置并列三电 10.0（极氪7X 9.0+1.0、海狮06EV
# 9.2+0.8…），顶配失去区分度。改用 spec_score.soft_ceiling 的**同一函数**：
# 8.0 以下不动、8.0 以上单调压缩、渐近 9.5，给"参数满分≠体验满分"留余量。
ANNOTATED_PT_CEILING = 9.5
ANNOTATED_PT_KNEE = 8.0


def load_trims(path: str | None = None) -> dict[str, dict]:
    """读逐配置事实。**统一走 trims_store**（人工库 + 自动库合并读视图）。

    2026-11-22：原来这里直接 json.load(trims.json)，而人工条目在自动库里
    另有一份副本 → 读到哪份取决于文件状态，同一事实两个读法。
    现在人工与自动在存储层互斥，合流只在 trims_store 一处发生。
    path 参数保留：非空时读指定文件（测试/临时排查用）。
    """
    if path:
        if not os.path.exists(path):
            return {}
        d = json.load(open(path, encoding="utf-8"))
        return {e["model"]: e for e in d.get("entries", []) if e.get("model")}
    from modules.data.trims_store import load as _load
    return _load()


def weighted_total(scores: dict, weights: dict | None = None) -> float:
    """按当前品类配置权重求和。

    2026-11-22 根因修复：原来固定用模块级 W（旧 18%/13.5% 口径），
    给纯电轿车/中型SUV切到 25%/8% 后，逐配置分仍按旧权重，
    与判档 with_config_scores / 主榜分不同源（BUG-051 实测 MG07 差 0.5）。
    权重唯一事实源 = 出榜 --category 的 dimensions。
    """
    w = weights or W
    return round(sum(float(scores.get(k, 0)) * v for k, v in w.items()) * 10, 1)


def charge_minutes(t: dict) -> tuple[float | None, str]:
    """从配置 charge 文本提取"等效 10-80%（70%区间）"快充分钟数。

    口径陷阱：文本混用 10-80%(70%区间) / 30-80%(50%) / 10-70%(60%)，
    直接比数字会虚高误导。按区间宽度线性折算到 70% 再比较。
    返回 (等效分钟, 口径说明)；解析不出返回 (None, "")。
    """
    c = str(t.get("charge") or "")
    # v10.24.0 根因修复：单位既可能是英文 min，也可能是中文「分钟」。
    # 此前正则只认 min，导致 22 条配置（海豹06GT 二代闪充 30-80% 9分钟、
    # 极氪007GT 10.5分钟、蔚来ET5 36分钟等）解析为 None → 快充项不进分。
    # 这不是"数据缺失"，是解析器漏识单位——同款车把 9min 写成 9分钟就凭空少一项分。
    # 口径不变：仍按区间宽度线性折算到 70%，只把单位识别统一。
    _UNIT = r"(?:min|分钟|minute)"
    m = re.search(r"(\d{2})\s*[-–]\s*(\d{2})%?\s*(?:约|大约)?\s*(\d+(?:\.\d+)?)\s*" + _UNIT, c)
    if not m:
        m2 = re.search(r"(\d+(?:\.\d+)?)\s*" + _UNIT, c)
        if m2:
            return float(m2.group(1)), "未标区间(按原文)"
        return None, ""
    lo, hi, mins = int(m.group(1)), int(m.group(2)), float(m.group(3))
    span = hi - lo
    if span <= 0:
        return None, ""
    equiv = round(mins / span * 70, 1)
    return equiv, f"{lo}-{hi}% {mins:g}min→等效10-80% {equiv:g}min"


def deltas_for(trim: dict, ref: dict) -> list[dict]:
    """该配置相对参考配置的客观差异 → 维度调整（带置信度）。"""
    out = []
    # 电压：v10.25.0 统一走 voltage_v 数值字段 + charge 文本双来源。
    # 此前只读 charge 文本，而 trims_store 已把纯电压文本（'900V'）归一到 voltage_v
    # 并清空 charge，故这里必须补上 voltage_v，否则同车系高压平台 delta 丢失。
    def _c800(t):
        if isinstance(t.get("voltage_v"), (int, float)) and t["voltage_v"] >= 800:
            return True
        c = str(t.get("charge") or "")
        return ("800V" in c or "900V" in c)
    c800 = _c800
    if c800(trim) and not c800(ref):
        out.append({"dim": "powertrain", "delta": 0.8, "conf": "high",
                    "why": "800V高压平台（配置表明确）"})
    # v9.7.31：快充能力 delta——只比"等效10-80%分钟数"，不按品牌/电池代际。
    # 同车系内闪充配置 vs 慢充配置才加分（如二代海鸥高配闪充 vs 低配非闪充）。
    # 全系皆闪充的车，其优势应在人工基准分体现，不由本 delta 重复加。
    ct, cwhy = charge_minutes(trim)
    cr, _ = charge_minutes(ref)
    if ct is not None and cr is not None and (cr - ct) >= 5:
        # 按"比参考配置快多少"分档（相对差），不按绝对分钟数——
        # 全系慢充的车系内部快一点也该体现；闪充车系差距更大则加更多。
        if cr - ct >= 15:
            d = 0.5
        elif cr - ct >= 8:
            d = 0.3
        else:
            d = 0.15
        out.append({"dim": "powertrain", "delta": d, "conf": "medium",
                    "why": f"快充{cwhy}（较参考快{cr-ct:g}min）+{d}"})
    # 续航
    r1, r0 = trim.get("cltc_km"), ref.get("cltc_km")
    if isinstance(r1, (int, float)) and isinstance(r0, (int, float)):
        d = (r1 - r0) / 100 * 0.3
        if abs(d) >= 0.15:
            out.append({"dim": "powertrain", "delta": round(d, 2),
                        "conf": "medium", "why": f"CLTC {r0}→{r1}km"})
    # 电池容量
    k1, k0 = trim.get("battery_kwh"), ref.get("battery_kwh")
    if isinstance(k1, (int, float)) and isinstance(k0, (int, float)):
        d = (k1 - k0) / 10 * 0.2
        if abs(d) >= 0.15:
            out.append({"dim": "powertrain", "delta": round(d, 2),
                        "conf": "medium", "why": f"电量 {k0}→{k1}kWh"})
    # 激光雷达
    # BUG-021：auto_trims 自动补录的 key_configs 常为空，但配置名里有「激光雷达」。
    # 只看 key_configs 会把激光雷达版和非激光版判成一样，配置分失真。
    # v10.16.2：有些车型的配置名**不含**「激光雷达」但硬件确实有（如乐道L60 Max+/Ultra+
    # 用神玑NX9031+1颗激光雷达，配置名只写 Max+/Ultra+）。现在优先读配置级结构化字段
    # adas_lidar（0/1/true/false），没有该字段再退回名称/key_configs 文本判断，
    # 避免「有硬件但名字没写」被判成低配。
    def lidar(t):
        v = t.get("adas_lidar")
        if v is not None:
            if isinstance(v, str):
                return v.strip().lower() in ("1", "true", "yes", "有", "标配")
            return bool(v)
        return ("激光雷达" in str(t.get("name") or "")) or \
               any("激光雷达" in str(x) for x in (t.get("key_configs") or []))
    if lidar(trim) and not lidar(ref):
        out.append({"dim": "adas", "delta": 0.8, "conf": "high", "why": "新增激光雷达"})
    elif lidar(ref) and not lidar(trim):
        out.append({"dim": "adas", "delta": -0.8, "conf": "high", "why": "无激光雷达"})
    # 性价比：按 kWh/万元 相对变化
    p1, p0 = trim.get("price"), ref.get("price")
    if p1 and p0 and k1 and k0:
        v1, v0 = k1 / p1, k0 / p0
        d = max(-1.0, min(1.0, (v1 - v0) / max(v0, 1) * 2))
        if abs(d) >= 0.15:
            out.append({"dim": "value", "delta": round(d, 2),
                        "conf": "medium", "why": f"kWh/万元 {v0:.2f}→{v1:.2f}"})
    return out


CONF_ORDER = {"low": 0, "medium": 1, "high": 2}


# 配置档次/功能词表（用于定位具体配置）。
# 数字相同但档次不同的配置必须靠这些词区分：602 越享型 vs 602 智享型 vs 602 智尊型。
_TRIM_TOKENS = (
    "激光", "ultra", "max", "plus", "pro", "智行", "旗舰",
    "智享", "越享", "智尊", "尊享", "舒享", "畅享", "越界", "卓越", "领先",
    "旗舰版", "标准版", "入门版", "长续航", "后驱", "四驱", "双电机",
)


def find_ref(name: str, trims: list[dict]) -> dict | None:
    """模糊匹配评分配置（v9.7.4：35款里34款精确匹配失败，必须模糊匹配）。

    scored_trim 常写成「560 Ultra 激光版」，配置表实际是「纯电版 560Ultra激光版」；
    用归一化（去空格/去年份/去"纯电版"前缀）+ 关键数字串双重判定。
    """
    def norm(s: str) -> str:
        """归一化配置名（用于精确/模糊匹配）。

        2026-11-22 修根因（数字粘连 bug，42/71 款配置错配）：
          原实现把空格**全部删除**，于是
            「MONA L05 2027款 纯电 635 Max」→ 「monal05635max」
          数字与型号粘在一起，数字正则抓到的是 0563/5635 而不是 635，
          匹配必然失败 → 落到「按价格就近选取」的兜底 → 拿到错配置
          （MONA L05 635 Max 被配成 535 Max 58.2kWh，整车三电/续航全错）。

          现在：分隔符统一替换成 "."（保留边界，不粘连），年份删除时
          也连同其两侧分隔符一起删；数字串用带前后边界的 3-4 位数字正则提取。
        """
        import re
        s = str(s or "")
        for junk in ("纯电版", "纯电", "款", "版"):
            s = s.replace(junk, ".")
        # 分隔符统一（空格/全角空格/连字符/下划线 → "."）
        s = re.sub(r"[\s\-_\u3000]+", ".", s)
        # 年份连同两侧分隔符删掉（避免留下孤立点）
        s = re.sub(r"\.?20\d{2}\.?", ".", s)
        s = re.sub(r"\.{2,}", ".", s).strip(".")
        return s.lower()

    target = norm(name)
    if not target:
        return None
    # 1) 归一化后完全相等
    for t in trims:
        if norm(t.get("name")) == target:
            return t
    # 2) 关键数字串（如 560/610/670）+ 关键配置词（激光/ultra/max）交集
    #    数字串用 (?<!\d)\d{3,4}(?!\d) 加边界——否则「L05635」里的 0563 会被误当型号
    import re
    num_re = re.compile(r"(?<!\d)\d{3,4}(?!\d)")
    nums = set(num_re.findall(target))
    # 配置词表：数字相同但档次不同（602 越享型 vs 602 智享型）要能区分
    words = {w for w in _TRIM_TOKENS if w in target}
    best, score = None, 0
    for t in trims:
        tn = norm(t.get("name"))
        tnums = set(num_re.findall(tn))
        twords = {w for w in _TRIM_TOKENS if w in tn}
        # 数字命中权重高于词命中：型号数字（635/602）比「Max/激光」更能定位配置
        sc = len(nums & tnums) * 3 + len(words & twords)
        if sc > score:
            best, score = t, sc
    # 数字命中才是硬信号：只有词命中（无数字交集）不足以定位配置
    if best is not None and not (nums & set(num_re.findall(norm(best.get("name"))))):
        if not (words & {w for w in _TRIM_TOKENS if w in norm(best.get("name"))}):
            return None
    return best if score >= 2 else None


def resolve_score_ref(c: dict, entry: dict) -> tuple[dict | None, str]:
    """评分配置（打分基准 trim）的**唯一解析入口**——v10.23 根因修正 2026-11-22。

    【为什么要它】此前两个消费者（rule_adjust._spec_of / annotate_all）各自解析，
    且都盲信 trims 库人工维护的 scored_trim_ref。但 scored_trim_ref 是**独立副本**，
    会与候选池评分配置漂移：秦MAX 候选池 trim=「EV 630 超越型」价 13.29 万，
    配置表里精确名是「EV 630km 超越型」，人工 ref 却写成「EV 530km 领先型」
    （10.99 万）——三电取参全落到低配 52.87kWh/100kW，三电分被系统性低估。

    【判据】价格是主榜定价、value 维、价位闸门、页面展示共用的硬数据，比人工
    ref 副本可靠。因此优先级：
      ① 候选池 trim.price 在 trims 里**精确命中唯一配置** → 采用（价格锚）
      ② 命中多个（同价不同配置）→ 用候选池配置名在命中的集合里 find_ref 消歧
      ③ 价格缺失/未命中 → 回退 scored_trim_ref 精确名 → 再回退候选池名
    任何一条路都写明 basis，供审计。缺价缺名才返回 None（不编造）。
    """
    trims = entry.get("trims") or []
    if not trims:
        return None, "无配置可选"
    # 只用**候选池显式给出的 trim.price**做锚。不能用 price_of()——
    # 后者在 trim.price 缺失时回退到 price_guide 下限（车系最低价），
    # 会把「带内主力配置」这类占位名车锚到最低配，破坏人工代表配置（银河A7/极狐贝塔S3）。
    ctrim = c.get("trim") or {}
    tp = ctrim.get("price")
    tp = float(tp) if isinstance(tp, (int, float)) else None
    # ① 价格精确命中
    hits = [t for t in trims if tp is not None
            and abs((t.get("price") if t.get("price") is not None
                     else t.get("price_wan") or 0) - tp) < 0.005]
    if len(hits) == 1:
        return hits[0], f"候选池指导价{tp:g}万精确命中唯一配置"
    if len(hits) > 1:
        # ② 同价多配置：用候选池配置名在命中集合内消歧
        cname = (c.get("trim") or {}).get("name") or ""
        if cname and not is_placeholder_ref(cname):
            r = find_ref(cname, hits)
            if r:
                return r, f"指导价{tp:g}万命中{len(hits)}款，按配置名消歧"
    # ③ 价格锚不可用时：候选池评分配置名（主榜展示/定价的配置，权威）模糊匹配优先，
    #    再退人工 ref 精确名 / scored_trim 简称。
    #    注意：有显式 trim.price 的车已在 ①② 被价格锚解析，不会走到这里——
    #    海狮06EV（605 入门版 price=15.99）由价格锚命中 605领航 15.99，
    #    不会被候选池简称「605 入门版」模糊配到 605领航Pro（v10.15 防的正是这个）。
    cname = (c.get("trim") or {}).get("name")
    if cname and not is_placeholder_ref(cname):
        r = find_ref(cname, trims)
        if r:
            return r, f"价格未命中，按候选池配置名匹配「{cname}」"
    for cand_name in (entry.get("scored_trim_ref"), entry.get("scored_trim")):
        if not cand_name or is_placeholder_ref(cand_name):
            continue
        r = next((t for t in trims if str(t.get("name")) == str(cand_name)), None)
        if r:
            return r, f"价格与候选池名均未命中，回退人工基准名「{cand_name}」"
        r = find_ref(cand_name, trims)
        if r:
            return r, f"回退人工基准名模糊匹配「{cand_name}」"
    return None, "价格与配置名均未命中，不编造"


def is_placeholder_ref(name: str) -> bool:
    """占位基准名（非真实配置名）——v9.7.35：这类值必须自动解析，不许静默 fallback 全车 low。"""
    s = str(name or "")
    return (not s or s in ("—", "-", "null", "None", "unknown")
            or any(k in s for k in ("待核", "待配置", "待核验", "主力配置", "待定")))


def resolve_ref(name: str, trims: list[dict], cands_for_model: list[dict] | None = None) -> tuple[dict | None, bool, str]:
    """解析评分配置基准 → (ref, is_fallback, basis)。

    v9.7.35（BUG-043 代码级根治）：占位名/匹配失败时**主动选代表配置**并写明依据，
    不再静默 fallback 给全车打 low（那会导致主榜低置信刷屏、失去区分度）。
    优先级：① 真实名精确/模糊匹配 → ② 候选池 trim 价格最近 → ③ 车系价格中位数配置。
    只有真的无法解析（trims 为空）才返回 None。
    """
    if not is_placeholder_ref(name):
        ref = find_ref(name, trims)
        if ref:
            return ref, False, "精确/模糊匹配 scored_trim"
    # 占位或匹配失败：选代表配置
    if not trims:
        return None, True, "无配置可选"
    # 价格一律走契约层解析（price_guide/price_wan/price 文本），
    # 否则遇到 price="24.98万" 就是 str - float 的 TypeError。
    from modules.contracts.candidate_schema import price_of
    price_hint = None
    for c in (cands_for_model or []):
        pg = c.get("price_guide") or []
        if isinstance(pg, list) and len(pg) == 2:
            price_hint = (pg[0] + pg[1]) / 2
            break
        p = price_of(c)
        if p:
            price_hint = p
            break
    if price_hint:
        ref = min(trims, key=lambda t: abs((t.get("price") or 0) - price_hint))
        basis = f"占位基准，按指导价中位{price_hint:g}万就近选取"
    else:
        prices = sorted(t.get("price") or 0 for t in trims if t.get("price"))
        med = prices[len(prices) // 2] if prices else 0
        ref = min(trims, key=lambda t: abs((t.get("price") or 0) - med))
        basis = f"占位基准，按车系配置价中位{med:g}万选取"
    return ref, True, basis


def annotate_all(cands: list[dict], cfg: dict) -> dict:
    """给每个车的所有配置生成人工口径分（base_scores + delta）。"""
    tdb = load_trims()
    # 权重随 --category 走，不再用模块级旧权重（页面/判档/主榜同一口径）。
    # v10.74 根因修复：旧版 `if d["key"] in W` 用模块级 8 维常量做白名单，
    # 新增的 reliability/holding_cost 被过滤掉 → 逐配置总分只按 8 维求和（权重和 0.80），
    # 页面「加权分」凭空掉 15+ 分（Model Y 84.8 → 67.0）。权重唯一源是 cfg.dimensions。
    Wc = {d["key"]: float(d["weight"]) for d in (cfg.get("dimensions") or [])
          if d.get("key") and isinstance(d.get("weight"), (int, float))}
    if not Wc:
        Wc = dict(W)
    out = {}
    for c in cands:
        model = c["model"]
        entry = tdb.get(model)
        if not entry or not entry.get("trims"):
            continue
        base = dict(c.get("scores") or {})
        if not base:
            continue
        # v10.23 根因修正：评分配置解析统一走 resolve_score_ref——
        # 候选池指导价精确锚定优先，避免 trims 人工 ref 副本漂移（秦MAX 曾把
        # 630超越型 13.29万 错配到 530领先型）。ref 只在价格未唯一命中时回退。
        _same_model = [c for c in cands if c.get("model") == model]
        ref, ref_basis = resolve_score_ref(c, entry)
        ref_fallback = False
        if ref is None:
            # 价格/config 名都没命中：沿用旧的代表配置兜底，不静默打 low。
            ref_name = entry.get("scored_trim") or ""
            ref, ref_fallback, ref_basis = resolve_ref(ref_name, entry["trims"], _same_model)
        ref_name_real = ref.get("name") if ref else None
        trims = []
        for t in entry["trims"]:
            # v9.7.4：匹配不到参考配置时不下 delta（宁可不加分，不在错基准上加分）
            ds = deltas_for(t, ref) if (ref and t is not ref) else []
            sc = dict(base)
            for d in ds:
                sc[d["dim"]] = round(max(0, min(10, sc.get(d["dim"], 8) + d["delta"])), 2)
            # 三电软上限：与 spec_score 共用同一函数，消除逐配置 10.0 满分并列。
            # 只压三电，不动其他维；仅记录被压幅度，不改 delta 本身（delta 仍是客观差异）。
            from modules.engine import spec_score as _SS
            _pt_raw = sc.get("powertrain")
            _pt_cap = None
            if isinstance(_pt_raw, (int, float)):
                _pt_soft = round(_SS.soft_ceiling(float(_pt_raw),
                                                  ANNOTATED_PT_CEILING,
                                                  ANNOTATED_PT_KNEE), 2)
                if _pt_soft < _pt_raw:
                    _pt_cap = {"dim": "powertrain", "before": round(float(_pt_raw), 2),
                               "after": _pt_soft, "ceiling": ANNOTATED_PT_CEILING,
                               "knee": ANNOTATED_PT_KNEE}
                    sc["powertrain"] = _pt_soft
            conf = min((d["conf"] for d in ds), key=lambda x: CONF_ORDER[x]) if ds else "high"
            # v10.17.0：逐配置 value 与车系级同口径——用价格残差法重算，杜绝
            # 「车系级 value 已被 value_residual 改成残差分，逐配置却仍按 kWh/万元
            # 加 delta，再被 with_config_scores 覆盖回去」的口径分叉（曾致宋Ultra
            # 跨榜 value 9.5 vs 8.6）。配置价 t.price、产品力用本配置七维（剔 value）。
            _vinfo = c.get("value_residual") or {}
            _bmean = _vinfo.get("bin_mean_ppw")
            if isinstance(_bmean, (int, float)) and t.get("price") and t["price"] > 0:
                from modules.engine import value_residual as _VR
                _pp_w = {k: Wc[k] for k in _VR.PROD_DIMS if k in Wc}
                # 产品力取入池稳定基准（与车系级 value_residual 同口径），不用逐配置 sc：
                # sc 的其他维 delta 会因 ref 选择受子池影响而漂移，用它算 ppw 会让同车
                # 跨榜 value 不一致（深蓝S 9.5 vs 7.6）。价格效率只需「车系产品力/配置价」。
                _pf_base = c.get("_value_pf_base") or sc
                _pk = [k for k in _pp_w if isinstance(_pf_base.get(k), (int, float))]
                if len(_pk) == len(_pp_w):
                    _pf = sum(_pf_base[k] * _pp_w[k] for k in _pk) / sum(_pp_w.values())
                    _ppw = _pf / float(t["price"])
                    _vv = _VR.value_for(_ppw, _bmean, _vinfo.get("residual_delta") or 0.0)
                    if _vv is not None:
                        sc["value"] = _vv
            # v9.7.35：占位基准已由 resolve_ref 主动解析成真实代表配置，不再全车降 low
            # （旧逻辑把所有配置打 low→主榜低置信刷屏=BUG-043）。基准偏差只在 ref_basis 说明，
            # 不污染每个配置的置信度。只有基准完全无法解析（ref=None）才降置信。
            if ref is None:
                conf = "low"
            # 产品力分：剔除 value 的 7 维按原权重归一化×10（与 product_power_board 同口径）
            pp_keys = [k for k in Wc if k != "value"]
            pp_tw = sum(Wc[k] for k in pp_keys)
            pp_score = round(sum(sc.get(k, 0) * Wc[k] for k in pp_keys) / pp_tw * 10, 2) if pp_tw else None
            trims.append({
                "name": t.get("name"), "price": t.get("price"),
                "battery_kwh": t.get("battery_kwh"), "cell_supplier": t.get("cell_supplier"),
                "charge": t.get("charge"), "cltc_km": t.get("cltc_km"),
                # v10.11.0：三电与补能事实透传（页面「三电与补能」区块用）
                "voltage_v": t.get("voltage_v"), "consumption": t.get("consumption"),
                "charge_power_kw": t.get("charge_power_kw"), "swap": t.get("swap"),
                "scores": sc, "total": weighted_total(sc, Wc), "power_score": pp_score,
                "confidence": conf, "deltas": ds,
                "score_cap": _pt_cap,
                "note": t.get("note", ""), "score_source": "annotated",
            })
        valid = [t for t in trims if t.get("total") is not None]
        best = max(valid, key=lambda t: t["total"]) if valid else None
        out[model] = {
            "scored_trim": ref_name_real,
            "ref_basis": ref_basis,
            "ref_fallback": ref_fallback,
            "n_trims": len(trims), "trims": trims,
            "best_trim": best["name"] if best else None,
            "best_total": best["total"] if best else None,
            "best_confidence": best.get("confidence") if best else None,
            "best_source": "annotated",
            "source": entry.get("url", ""), "date": entry.get("date", ""),
        }
    return out


def selftest() -> int:
    bad = []
    tdb = load_trims()
    if len(tdb) < 10:
        bad.append("配置库不足")
    cands = [{"model": "MG 07 纯电", "scores": {
        "powertrain": 8.0, "safety": 8.2, "adas": 8.6, "space": 8.0,
        "value": 9.4, "control": 8.8, "cabin": 8.6, "quality": 8.0}}]
    cfg = {"dimensions": [{"key": k, "weight": v} for k, v in W.items()]}
    out = annotate_all(cands, cfg)
    d = out.get("MG 07 纯电", {})
    t845 = next((t for t in d.get("trims", []) if "845" in str(t.get("name"))), None)
    t610 = next((t for t in d.get("trims", []) if "610 激光" in str(t.get("name"))), None)
    if not (t845 and t610):
        bad.append("MG07 845/610配置缺失")
    else:
        if t845["scores"]["powertrain"] <= t610["scores"]["powertrain"]:
            bad.append(f"845三电应高于610: {t845['scores']['powertrain']} vs {t610['scores']['powertrain']}")
        if t845["total"] <= t610["total"]:
            bad.append("845总分应高于610")
    # v10.15.9：人工基准再高（9.8）也不许逐配置三电顶到 10.0；低分配置不动。
    hot_base = {"powertrain": 9.8, "safety": 8.2, "adas": 8.6, "space": 8.0,
                "value": 9.4, "control": 8.8, "cabin": 8.6, "quality": 8.0}
    hot = annotate_all([{"model": "MG 07 纯电", "scores": hot_base}], cfg)
    hot_vals = [t["scores"]["powertrain"] for t in (hot.get("MG 07 纯电") or {}).get("trims", [])]
    if not hot_vals or max(hot_vals) >= ANNOTATED_PT_CEILING:
        bad.append(f"人工口径三电仍顶格: max={max(hot_vals) if hot_vals else None}")
    from modules.engine import spec_score as _SS
    if abs(_SS.soft_ceiling(7.5, ANNOTATED_PT_CEILING, ANNOTATED_PT_KNEE) - 7.5) > 1e-9:
        bad.append("软上限不应改动 knee 以下的分数")
    if bad:
        print("[selftest FAIL]", bad)
        return 1
    print(f"[selftest OK] 配置库 {len(tdb)} 款；MG07 845版人工口径分 > 610版；delta带置信度")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="逐配置人工口径标注")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    raise SystemExit(selftest() if a.selftest else 0)
