#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""质量与品控维度（quality_score.py）——只服务综合打分榜，不改事实榜。

口径：
  · 评分对象：车型级（车质网/PPH/召回通常不按具体 trim 拆分）。
  · 数据源：车质网 90 天投诉聚合、汽车之家 PPH、候选池可选 recall_events。
  · 缺失处理：样本不足/无数据 = 中性 8.0 + 低置信，不按 0 分、不按差评。
  · 防双计：原 7 维权重统一缩到 90%，质量与品控只占 10%；
    三电/车机已有的口碑信息不再额外加第二次分。
"""
from __future__ import annotations

import math

SCORING_ENABLED = True
BASE = 8.0
LOW = 3.0
HIGH = 9.5

PPH_BASELINE = 151.0  # 可用样本百车故障数中位（两侧榜单22+20款实测分布）
PPH_SLOPE = 0.008      # 每高于/低于中位1个PPH扣分/加分（保守，质量仅占10%权重）

# v10.74.1 起，旧的 HIGH_TAGS/MID_TAGS 两档白名单被下方 SEV_TIERS 四档取代
# （旧口径只数 top4 标签、两档系数 0.3/0.1，全池品控挤在 7.5~8.0 分不出一档）。
# 保留名字不改，是防止外部脚本 import 时报错；实际计算只读 SEV_TIERS。
HIGH_TAGS = {"转向", "制动", "刹车", "电池", "失灵", "失效", "无法启动", "无法充电"}
MID_TAGS = {"充电", "车机", "黑屏", "续航", "悬挂", "变速箱", "生锈", "爆漆"}

# v10.74.1（用户：「用更细的严重度分级」）：旧实现只有 high/mid 两档、
# 每条 top4 标签 ×0.3/×0.1、封顶 0.8 —— 结果全池品控挤在 7.5~8.0（多车并列），
# 分不出「偶尔异响」和「制动失灵」。改为四档 + 三因子：
#   ① 档位权重（每条投诉的问题性质）：安全 > 功能失效 > 耐久劣化 > 体验感知
#   ② 最坏档加成：只要出现安全类，整维再 +0.1 档位分（一次制动失灵不能被平均掉）
#   ③ 频次放大：标签出现越多越接近满额（log 压缩，避免单条极端标签扣满）
# 只改严重度口径，不动 density（频度归可靠性）、不动召回。
SEV_TIERS = {
    "safety":     {"w": 1.00, "tags": {"制动", "刹车", "转向", "跑偏", "失灵", "失效", "自燃",
                                       "起火", "失控", "无法启动", "动力中断", "趴窝", "断轴"}},
    "function":   {"w": 0.70, "tags": {"电池", "充电", "无法充电", "续航", "变速箱",
                                       "悬挂", "减震", "漏油", "报警", "无法挂挡"}},
    "durability": {"w": 0.35, "tags": {"生锈", "爆漆", "漆面", "车门", "异味", "抖动",
                                       "顿挫", "空调", "故障"}},
    "experience": {"w": 0.12, "tags": {"异响", "卡顿", "死机", "黑屏", "车机", "灵敏度"}},
}
SEV_CAP = 1.5          # 严重度扣分上限（旧 0.8 太浅，安全类无法拉开）
SEV_WORST_BONUS = 0.10 # 出现安全类再加的档位分
SEV_TAG_EXPAND = 10.0  # 标签出现到这个量级视为「性质覆盖充分」，频次放大到 1.0

# v10.74.2（用户：「继续做」= 同部件重复故障归并）：
# 标签是从投诉描述里按关键词命中的，一条「刹车失灵」会同时命中「刹车」和「失灵」，
# 被算成两次故障；同一部件 3 条投诉又被 3 次线性累加。两种都是「同一物理故障重复计分」。
# 归并规则：先把标签映射到**部件**，一个部件只出一档权重；部件内次数用
# 次数递减函数 eff = 1 + 0.5·ln(1+occ−1)（第 2 次只加 ~35%，第 4 次 ~69%，封顶 1.8），
# 且 occ 不超过该车投诉总条数（一条投诉不可能产生两条同部件故障）。
def _tag_tier(tag: str) -> tuple[str, float]:
    for name, d in SEV_TIERS.items():
        if tag in d["tags"]:
            return name, float(d["w"])
    return "experience", float(SEV_TIERS["experience"]["w"])


COMPONENT_OF = {
    # 制动系统：刹车/制动/失灵/失效（描述里「刹车失灵」是一条投诉）
    "制动": {"制动", "刹车", "失灵", "失效", "失控"},
    # 转向系统
    "转向": {"转向", "跑偏"},
    # 动力电池与热失控
    "电池": {"电池", "自燃", "起火"},
    # 补能
    "补能": {"充电", "无法充电"},
    # 续航/能耗
    "续航": {"续航"},
    # 动力总成与启动
    "动力总成": {"动力中断", "趴窝", "无法启动", "无法挂挡", "变速箱"},
    # 悬架/行驶系
    "行驶系": {"悬挂", "减震", "断轴", "抖动"},
    # 车身与漆面
    "车身": {"生锈", "爆漆", "漆面", "车门"},
    # 座舱电子
    "座舱电子": {"车机", "黑屏", "死机", "卡顿", "灵敏度"},
    # 空调
    "空调": {"空调"},
    # 异响（整车 NVH，单独一类，不与具体部件合并，避免一条异响被算成制动）
    "异响": {"异响"},
    # 其他
    "其他": {"异味", "顿挫", "漏油", "报警", "故障"},
}

# v10.74.3：部件级归并只解决「同部件重复」，解决不了**上下游因果**——
# 「电池充不进电」导致的「无法启动」是同一个故障链，却被算成电池 + 动力总成两个部件；
# 「悬挂/断轴」导致的「跑偏」同理。规则：下游部件存在且上游部件也存在时，
# 下游并入上游（保留两者中更严重的档位），只按一条链计一次。
CAUSAL_PAIRS = {
    "动力总成": ("电池", "补能"),   # 电池/充电故障 → 无法启动/动力中断/趴窝
    "转向":     ("行驶系",),        # 悬架/断轴 → 跑偏
    # v10.74.3：能量链——电池故障 / 充不进电 / 续航缩水是同一个储能系统的问题，
    # 分开算会把一次「电池不行了」拆成电池 + 补能 + 续航三条。
    "补能":     ("电池",),
    "续航":     ("电池", "补能"),
}


def component_of(tag: str) -> str:
    for comp, tags in COMPONENT_OF.items():
        if tag in tags:
            return comp
    return "其他"


def _merge_components(tags: list[tuple[str, int]], total: int | None = None) -> dict:
    """把 [(标签, 次数)] 归并成 {部件: {tier_w, occ_eff, tags}}，做重复故障去重。"""
    comps: dict[str, dict] = {}
    for t, n in tags:
        tag = str(t)
        comp = component_of(tag)
        name, w = _tag_tier(tag)
        c = max(1, int(n or 1))
        d = comps.setdefault(comp, {"w": 0.0, "occ": 0, "tags": [], "tier": name})
        d["occ"] += c
        d["tags"].append((tag, c))
        if w > d["w"]:
            d["w"] = w
            d["tier"] = name
    for comp, d in comps.items():
        occ = d["occ"]
        # 一条投诉不可能在同一部件上贡献多次故障：上限 = 投诉总数
        if total is not None and total > 0:
            occ = min(occ, int(total))
        d["occ_eff"] = round(1.0 + 0.5 * math.log1p(max(0, occ - 1)), 3) if occ > 1 else 1.0
        d["occ_eff"] = min(1.8, d["occ_eff"])
    # 上下游因果链合并（同一故障链只算一次）
    for down, ups in CAUSAL_PAIRS.items():
        if down not in comps:
            continue
        present = [u for u in ups if u in comps]
        if not present:
            continue
        # 并入最严重的上游部件（档位高的优先，其次次数多的）
        up = max(present, key=lambda u: (comps[u]["w"], comps[u]["occ"]))
        d, u = comps[down], comps[up]
        u["occ"] = u["occ"] + d["occ"]
        if total is not None and total > 0:
            u["occ"] = min(u["occ"], int(total))
        u["tags"] += d["tags"]
        if d["w"] > u["w"]:
            u["w"], u["tier"] = d["w"], d["tier"]
        u.setdefault("chains", []).append(down)
        u["occ_eff"] = min(1.8, round(1.0 + 0.5 * math.log1p(max(0, u["occ"] - 1)), 3)
                           if u["occ"] > 1 else 1.0)
        del comps[down]
    return comps


def severity_breakdown(tags: list[tuple[str, int]], total: int | None = None) -> dict:
    """把标签谱折成四档加权严重度（0..SEV_CAP），并给出可解释明细。

    输入 [(标签, 出现次数)]（tags_all 全量，缺则退 top_tags）；
    total = 该车投诉条数，用于给「同部件次数」设上限（防一条投诉命中多个关键词被算多次）。
    """
    tiers = {"safety": 0, "function": 0, "durability": 0, "experience": 0}
    for t, n in tags:
        name, _w = _tag_tier(str(t))
        tiers[name] += max(1, int(n or 1))
    comps = _merge_components(tags, total)
    if not comps:
        return {"score": 0.0, "tiers": tiers, "avg_w": 0.0, "expand": 0.0,
                "idx": 0.0, "components": {}, "note": "无标签，不扣严重度"}
    wsum = esum = 0.0
    worst = 0.0
    for d in comps.values():
        wsum += d["w"] * d["occ_eff"]
        esum += d["occ_eff"]
        worst = max(worst, d["w"])
    avg_w = wsum / esum if esum else 0.0
    idx = min(1.0, 0.55 * worst + 0.45 * avg_w
              + (SEV_WORST_BONUS if any(d["tier"] == "safety" for d in comps.values()) else 0.0))
    expand = min(1.0, math.log1p(esum) / math.log1p(SEV_TAG_EXPAND))
    sev = min(SEV_CAP, SEV_CAP * idx * expand)
    chains = [f"{p}并入{c}" for c, d in comps.items() for p in d.get("chains", [])]
    note = ("安全类×%d / 功能失效×%d / 耐久劣化×%d / 体验×%d；部件 %d 个（同部件已归并%s）→ "
            "档位分 %.2f（最坏档 %.1f、加权均值 %.2f）×频次 %.2f = 严重度 %.2f"
            % (tiers["safety"], tiers["function"], tiers["durability"], tiers["experience"],
               len(comps), ("；因果链 " + "、".join(chains)) if chains else "",
               idx, worst, avg_w, expand, sev))
    return {"score": round(sev, 3), "tiers": tiers, "components": comps,
            "chains": chains,
            "avg_w": round(avg_w, 3), "expand": round(expand, 3),
            "idx": round(idx, 3), "note": note}

# v10.19.0（用户 2026-11-22 拍板）：纯软件 OTA 修复的召回暂不纳入质量扣分。
# 根因而非补丁：旧逻辑按「召回次数 × 严重度」机械累加、触顶 -3，会把
# 「软件缺陷经 OTA 无损修复」等同于「硬件安全隐患」，与本项目「召回不等于质量差」
# 的既定边界冲突（Model Y 4 起软件召回曾一次扣满 3 分）。改为按处置方式(remedy)
# 区分，任何车型的新召回只要标 ota 即自动跳过，不按车型名硬编码。
# 恢复口径只需把此开关改 False——一处开关、全链路生效。
OTA_RECALL_EXCLUDED = True

# 纯软件 OTA 的特征词；出现线下物理作业词即判为非纯 OTA（仍需扣分）。
_OTA_WORDS = ("OTA", "ota", "远程升级", "软件升级")
_PHYSICAL_WORDS = ("加贴", "粘贴", "标签", "标识", "更换", "换件", "加装",
                   "返厂", "线下", "检修", "维修", "拧紧", "重装")


def is_ota_recall(event: dict) -> bool:
    """判定单条召回是否为「纯软件 OTA 修复」（暂不纳入扣分）。

    这是 OTA 判定的唯一真相源，供扣分与审计共用：
      · 结构化字段 remedy 优先：'ota' → True；'physical'/'mixed'/... → False；
      · remedy 缺失时文本兜底：措施段含 OTA 词且不含任何线下物理作业词 → True。
    只检查「措施：」之后的文本，避免缺陷描述里的词误伤判定。
    """
    remedy = str(event.get("remedy") or "").strip().lower()
    if remedy == "ota":
        return True
    if remedy in ("physical", "mixed", "hardware", "line", "offline", "物理", "线下", "混合"):
        return False
    reason = str(event.get("reason") or "")
    if not any(w in reason for w in _OTA_WORDS):
        return False
    measures = reason.split("措施：")[-1] if "措施：" in reason else reason
    return not any(w in measures for w in _PHYSICAL_WORDS)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _complaint_penalty(v: dict) -> tuple[float, str]:
    count = int(v.get("count") or 0)
    if count < 5:
        return 0.0, f"车质网 {count} 条，样本<5，不扣分"
    per = v.get("per_10k")
    if per is None:
        return 0.0, f"车质网 {count} 条，销量缺，密度不可算，不扣分"
    per = float(per)
    density = min(2.2, 2.0 * math.log1p(per) / math.log1p(50.0))  # v9.7.0 密度封顶2.6→2.2
    tags = (v.get("tags_all") or v.get("top_tags") or [])
    sev = severity_breakdown([(str(t), n) for t, n in tags], total=count)
    severe = float(sev["score"])
    total = density + severe
    tag_txt = "、".join(str(t) for t, _ in (v.get("top_tags") or [])) or "无高频标签"
    return total, (f"车质网 90 天 {count} 条 / {per:g}/万台；"
                   f"密度扣分 {density:.2f}，问题性质 {sev['note']}（高频标签 {tag_txt}）")


def _complaint_pph_penalty(c: dict) -> tuple[float, str]:
    """车质网投诉销量比（万分之）→ 质量扣分（v10.62 根因修复）。

    背景：quality_score 文档写明数据源含"车质网投诉聚合"，但实现只支持
    "90天投诉明细"形态（complaints.json 的 count/per_10k）。网查到的车质网
    PPH 比值（13 款车有，如唐L 643.8/万、小米YU7 21.2/万）无处可放，导致
    这些车全拿中性 8.0——用户质疑"质量分全 8 不合理"。
    修复：与 _complaint_penalty 共用同一密度公式（log 压缩、封顶 2.2），
    两种形态数据按"有明细用明细、无明细用 PPH 比值"优先级取，不双计。
    PPH 是车系级（增程/纯电可能共用），故同样标注口径，保守处理。
    """
    pph = c.get("complaint_pph")
    if not isinstance(pph, (int, float)) or pph <= 0:
        return 0.0, "无车质网投诉销量比数据"
    src = str(c.get("complaint_pph_source") or "车系级")
    density = min(2.2, 2.0 * math.log1p(float(pph)) / math.log1p(50.0))
    return density, (f"车质网投诉销量比 {float(pph):g}/万辆（{src}，按车系口径保守）；"
                     f"密度扣分 {density:.2f}")


def _pph_adjust(c: dict) -> tuple[float, str]:
    if not c.get("ah_pph_usable"):
        return 0.0, "PPH 样本不足，中性处理"
    pph = float(c.get("ah_pph") or 0)
    # v10.72 根因修复：PPH = 百车故障数（越高越差），旧公式 (100-pph)/100
    # 把"每百车56个故障"算成 +0.24 加分——基准100完全错位（百车故障100已是
    # 极差水平），且 PPH=0（零故障）反而因样本不足拿不到分。
    # 新公式按项目既定口径 references/燃油混动打分口径.md 的
    # "9.9 − 0.0135×PPH" 方向：以可用样本 PPH 中位 151 为零点，
    # 低于中位加分、高于中位扣分；斜率取 0.008（比 0.0135 保守，
    # 因本模块 BASE=8.0 且质量只占10%权重），范围 [-1.5, +0.8]。
    raw = _clamp((PPH_BASELINE - pph) * PPH_SLOPE, -1.5, 0.8)
    n = int(c.get("ah_review_users") or 0)
    # v10.16.2（用户举证：乐道L60 PPH样本仅44却按满额扣0.27）：
    # 样本≥30 就满额生效会让小样本噪声直接变成扣分，而样本不足的车反而拿中性8.0，
    # 形成「有数据者被罚、无数据者中性」的结构性不对称。按 n/(n+50) 收缩，
    # 样本越大越接近原始幅度，44样本只按约47%计入，消除小样本过度惩罚。
    shrink = n / (n + 50.0) if n > 0 else 0.0
    adj = raw * shrink
    return adj, (f"汽车之家 PPH {pph:g}（百车故障数，中位基准 {PPH_BASELINE:g}，样本 {n} "
                 f"按 n/(n+50) 收缩 {shrink:.2f}），调整 {adj:+.2f}")


def _recall_penalty(c: dict) -> tuple[float, str]:
    events = c.get("recall_events") or []
    if not events:
        return 0.0, "本期无结构化召回事件，不扣分"
    # v10.19.0：纯软件 OTA 召回按用户口径暂不纳入扣分；物理/混合仍按严重度扣。
    # 判定统一走 is_ota_recall（结构化 remedy 优先、文本兜底），不按车型名硬编码。
    counted, ota_skipped = [], []
    for e in events:
        if OTA_RECALL_EXCLUDED and is_ota_recall(e):
            ota_skipped.append(e)
        else:
            counted.append(e)
    pen = 0.0
    for e in counted:
        sev = str(e.get("severity") or "normal").lower()
        pen += 1.5 if sev in ("high", "严重", "高危") else 0.5
    pen = min(3.0, pen)
    note = f"召回 {len(counted)} 次计入"
    if counted:
        note += f"，扣分 -{pen:g}"
    else:
        note += "，不扣分"
    if ota_skipped:
        note += f"（另有 {len(ota_skipped)} 起纯软件OTA按口径不纳入）"
    return -pen, note


def components(c: dict, complaint: dict | None) -> dict:
    """把「质量与品控」拆成可分别归属的分量（v10.74，用户要求拆可靠性与质量两维）。

    分量与归属：
      density   投诉/PPH 故障**频度**扣分（0..2.2）           → 可靠性
      pph_adj   汽车之家 PPH 相对中位的调整（−1.5..+0.8）      → 可靠性
      severe    问题**性质**四档加权扣分（0..1.5）           → 质量与品控
      recall_adj 召回处置扣分（物理召回重、纯 OTA 不纳入）      → 质量与品控

    两条原则：
      ① 只切不重算：score_one 仍等于 BASE − density − severe + pph_adj + recall_adj，
         历史分与历史回归不变；
      ② 不双计：频度只进可靠性、严重度只进质量，召回归严重度（召回是处置不是频发）。
    """
    comp_pen, comp_note = _complaint_penalty(complaint or {})
    has_detail = int((complaint or {}).get("count") or 0) >= 5 and (complaint or {}).get("per_10k") is not None
    if not has_detail and c.get("complaint_pph"):
        comp_pen, comp_note = _complaint_pph_penalty(c)
    # comp_pen = density + severe；拆回两项
    density, severe = 0.0, 0.0
    sev_bd = {"score": 0.0, "tiers": {}, "note": ""}
    if (complaint or {}).get("count", 0) >= 5 and (complaint or {}).get("per_10k") is not None:
        per = float((complaint or {}).get("per_10k"))
        density = min(2.2, 2.0 * math.log1p(per) / math.log1p(50.0))
        tags = ((complaint or {}).get("tags_all")
                or (complaint or {}).get("top_tags") or [])
        sev_bd = severity_breakdown([(str(t), n) for t, n in tags],
                                    total=int((complaint or {}).get("count") or 0))
        severe = float(sev_bd["score"])
    elif c.get("complaint_pph"):
        density = min(2.2, 2.0 * math.log1p(float(c["complaint_pph"])) / math.log1p(50.0))
        severe = 0.0
    pph_adj, pph_note = _pph_adjust(c)
    rec_adj, rec_note = _recall_penalty(c)
    has_freq = bool(complaint and (complaint.get("count") or 0) >= 5) or bool(c.get("complaint_pph")) \
        or bool(c.get("ah_pph_usable"))
    freq_note = "；".join([x for x in (comp_note if density else "", pph_note) if x])
    _sev_txt = sev_bd.get("note") or ""
    sev_note = "；".join([x for x in (_sev_txt if severe else "", rec_note) if x])
    return {"density": density, "severe": severe, "pph_adj": pph_adj,
            "recall_adj": rec_adj, "has_freq_signal": has_freq,
            "freq_note": freq_note,
            "sev_tiers": sev_bd.get("tiers") or {},
            "sev_note": sev_note,
            "detail_note": "；".join([comp_note, pph_note, rec_note])}


def score_one(c: dict, complaint: dict | None) -> dict:
    comp = components(c, complaint)
    raw = BASE - comp["density"] - comp["severe"] + comp["pph_adj"] + comp["recall_adj"]
    score = round(_clamp(raw, LOW, HIGH), 2)

    has_complaint = int((complaint or {}).get("count") or 0) >= 5
    has_pph = bool(c.get("ah_pph_usable"))
    has_web_pph = bool(c.get("complaint_pph"))  # v10.62：网查PPH也算有据，提置信
    if has_complaint and has_pph:
        conf = "high"
    elif has_complaint or has_pph or has_web_pph:
        conf = "medium"
    else:
        conf = "low"
    if c.get("lifecycle", {}).get("status") == "new_launch" and conf != "high":
        conf = "low"

    detail = {
        "score": score,
        "confidence": conf,
        "complaint": {
            "count": int((complaint or {}).get("count") or 0),
            "per_10k": (complaint or {}).get("per_10k"),
            "top_tags": (complaint or {}).get("top_tags") or [],
        },
        "pph": c.get("ah_pph") if has_pph else None,
        "pph_usable": has_pph,
        "complaint_pph": c.get("complaint_pph"),  # v10.62 车质网PPH(网查)
        "complaint_pph_source": c.get("complaint_pph_source"),
        "recall_events": c.get("recall_events") or [],
        # v10.19.0：审计用——有几起纯软件OTA召回按口径不纳入（单一真相源判定）
        "recall_ota_excluded": sum(1 for e in (c.get("recall_events") or [])
                                   if OTA_RECALL_EXCLUDED and is_ota_recall(e)),
        "note": comp["detail_note"],
        # v10.74：分量拆给两个维度（可靠性=频度、质量=严重度），页面 hover 分别引用
        "components": {"density": round(comp["density"], 3), "severe": round(comp["severe"], 3),
                       "pph_adj": round(comp["pph_adj"], 3), "recall_adj": round(comp["recall_adj"], 3)},
        "freq_note": comp["freq_note"], "sev_note": comp["sev_note"],
        # v10.74.1：四档严重度的逐车明细（安全/功能/耐久/体验各几条），供页面与审计解释
        "severity": {"score": round(comp["severe"], 3), "tiers": comp.get("sev_tiers") or {},
                     "note": comp["sev_note"]},
    }
    return detail


def apply(cands: list[dict], complaints: dict | None = None) -> tuple[list[dict], list[dict]]:
    """把 scores.quality 写入候选池；同一输入重复执行结果一致。"""
    models = (complaints or {}).get("models") or {}
    audit = []
    for c in cands:
        d = score_one(c, models.get(c.get("model")))
        c.setdefault("scores", {})["quality"] = d["score"]
        c["quality_detail"] = d
        audit.append({"model": c.get("model"), **d})
    return cands, audit


def coverage(cands: list[dict]) -> dict:
    out = {"total": len(cands), "high": 0, "medium": 0, "low": 0,
           "complaint_ge5": 0, "pph_usable": 0, "recall": 0}
    for c in cands:
        d = c.get("quality_detail") or {}
        out[d.get("confidence", "low")] = out.get(d.get("confidence", "low"), 0) + 1
        out["complaint_ge5"] += int(int((d.get("complaint") or {}).get("count") or 0) >= 5)
        out["pph_usable"] += int(bool(d.get("pph_usable")))
        out["recall"] += int(bool(d.get("recall_events")))
    return out


def report(cands: list[dict]) -> str:
    cov = coverage(cands)
    lines = [
        "# 质量与品控（严重度维，频度归可靠性）",
        "",
        "> 本维度只进综合打分榜；配置级事实榜不改名次。车型级、10 分制，当前权重见品类配置 **6%**"
        "（v10.74 拆维后：频度归「可靠性」，本维只吃问题性质的严重度）。",
        "",
        "口径：车质网 90 天投诉（按编号去重，密度=投诉/万台，样本<5 不扣分）"
        "+ 汽车之家 PPH（样本≥30 才生效）+ 可选的官方召回事件。"
        "无数据 = 中性 8.0 + 低置信，不按 0 分，也不判为质量差。",
        "",
        "> **读分须注意（v9.6.2 披露）**：① 投诉数已按销量归一化（密度=投诉/万台、对数压缩），"
        "不是「卖得多扣分多」；② 但分母是**单月趋势销量**、投诉是 **90 天累计**，口径不完全对齐，"
        "密度会被高估（保守方向），不作精确质量排序；③ 投诉有自选择偏差（沉默的大多数不投诉），"
        "「无投诉」只代表**没证据**（低置信），不等于质量好——见置信列；"
        "④ v9.7.0 温和平衡：投诉密度封顶 2.6→2.2；v10.74.1 问题性质改为四档"
        "（安全 1.0 / 功能失效 0.7 / 耐久劣化 0.35 / 体验 0.12）+ 最坏档加成 + 频次放大，"
        "上限 1.5；v10.74.2 再加部件级归并——同一物理故障只算一次（「刹车失灵」命中两个标签"
        "仍算一条；同部件多次投诉按 1+0.5·ln 递减、次数不超投诉条数）；"
        "PPH 正向空间 0.6→1.0，让口碑有实质对冲但不反转（刷口碑仍难盖过高投诉）；"
        "⑤ v10.16.2 样本收缩：PPH 调整按 n/(n+50) 缩放，样本不足的车保持中性 8.0 不因「有数据」反被重罚——"
        "小样本不等于质量差，置信列与样本数必须一起读；"
        "⑥ v10.19.0 OTA 口径：召回按处置方式区分，**纯软件 OTA 修复（remedy=ota）暂不纳入扣分**，"
        "线下物理/混合措施（加贴标识、换件等）仍按严重度扣。判定统一走 is_ota_recall（结构化字段优先、文本兜底），"
        "不按车型硬编码；开关 OTA_RECALL_EXCLUDED 一处可切换。",
        "",
        f"> 覆盖：{cov['total']} 款；车质网投诉≥5 条 {cov['complaint_ge5']} 款；"
        f"PPH 可用 {cov['pph_usable']} 款；召回事件 {cov['recall']} 款；"
        f"置信 high/medium/low = {cov['high']}/{cov['medium']}/{cov['low']}。",
        "",
        "| 车型 | 品控分 | 置信 | 依据 |",
        "|---|---:|---|---|",
    ]
    for c in sorted(cands, key=lambda x: -float(((x.get("quality_detail") or {}).get("score")) or 0)):
        d = c.get("quality_detail") or {}
        lines.append(f"| {c.get('model')} | {d.get('score', '—')} | "
                     f"{d.get('confidence', '—')} | {d.get('note', '—')} |")
    lines += [
        "",
        "> 边界：座椅后钢板、单条拆车视频、网帖个例、门店服务态度主观感受均不进分；"
        "召回不等于质量差，仅在有结构化公告且明确批次时按严重度有限扣分。",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    import json
    import os

    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--complaints", help="fetch_complaints 聚合 JSON")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    data = json.load(open(a.candidates, encoding="utf-8"))
    cands = data.get("candidates", data)
    cdata = json.load(open(a.complaints, encoding="utf-8")) if a.complaints and os.path.exists(a.complaints) else None
    cands, _ = apply(cands, cdata)
    data["candidates"] = cands
    json.dump(data, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[ok] {a.out}")
