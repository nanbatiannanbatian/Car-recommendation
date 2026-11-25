# -*- coding: utf-8 -*-
"""evidence_gate.py —— 维度证据门槛（统一封顶框架）

问题（2026-11-21，用户连续三次抓到同一类）：
  · safety：零碰撞成绩的 MONA L05 拿 8.6，比有 C-NCAP 86.7% 的 bZ5 还高
  · adas/cabin/control：零实测的 MONA 拿 9.4（全场最高），同品牌有实测的 G6 只有 7.6
  · quality：0 个口碑样本的 MONA 拿 8.0，而 64 人样本的零跑C11 只有 4.29
  三次都是同一句话：**没有证据的人，不能被推断成比有证据的人更强。**

  之前修了三次（safety_check.evidence_cap、experience_score.cap_unproven…），
  每修一次就多一个特判函数——下一个维度出问题又要再写一个。这里改成策略表：
  哪些维度「需要什么证据」写在 DIM_POLICY 里，加维度只需加一行。

原则（不是所有维度都封顶）：
  · 纸面可判定的（space 轴距/车长、value 配置价格比、powertrain 三电参数）
    **不封顶**——这些本来就能从硬数据算出来，封顶反而是用"没实测"去惩罚硬指标。
  · 必须实车/官方数据的（safety 碰撞、adas/cabin/control 体验、quality 可靠性）
    无证据则封顶。只封顶、不倒扣（缺数据不判劣，但不能声称更优）。

约束：本模块只做「上限约束」，不改权重、不产生新分。
"""
from __future__ import annotations

import math
import statistics

# 维度证据策略。need 决定"什么算有证据"，cap_to 决定封顶到哪。
#
# 2026-11-21 二次校准（用户质疑「大众 T6 智驾/操控/品控分低是否不客观」后定的）：
#   · cap_to 从"一律最低"改为分维度：
#       safety 用 min —— 碰撞是门槛性质，没成绩不能和有成绩并列；
#       adas/cabin/control/quality 用 median —— 举证责任只要求「不超过有证据车的
#         典型水平」，用最低等于「没测过只能垫底」（T6 麋鹿 80.02km/h 被压到 7.8，
#         而实测 75.2km/h 的极氪7X 是 8.5 —— 这就是最低锚点造成的荒谬）。
#   · min_proven：有证据车不足 3 款时该维不封顶。control 曾只有 2 款有证据，
#     拿它们的噪声当全池天花板，统计上不成立。
#   · force：quality 无样本时「统一置为有样本车中位」，不再看手填值——
#     33 款无样本车里 21 款填 8.0、12 款填 5.48~7.8，同一状态两种尺度，
#     填低的车吃暗亏（T6 填 6.09，比填 8.0 被封到 6.74 的车还低）。
DIM_POLICY = {
    # v10.73 口径变更（用户 2026-11-25：「没有数据要比有数据的低，即使是差评，
    # 是不是有点不合理」「按照你的建议来改」）：
    #
    # 旧口径（v10.63→v10.72）：无证据车封顶 = 有证据车最低 + offset（safety −1.0、
    # quality −0.1）。语义等价于"你比全榜最有证据的差车还差"—— 把"不知道"当负无穷。
    # 实测后果：安全 6 款并列 7.0、品控 7 款并列 5.0（全部无样本车被压在同一条线上，
    # 且低于 PPH 233 的零跑C11 5.1），用户直接指出不合理。
    #
    # 新口径 cap_to="impute"（中位插补 + 不确定性折扣）：
    #   ŝ = median(有证据车) − kappa × stdev(有证据车) / sqrt(n + 1)
    # 语义：缺证据 = "未知"，落在有证据车的典型水平略偏保守一侧（因为不知道，
    # 不敢给到中位），而不是"最差"。与 Consumer Reports 小样本向先验收缩同源，
    # 与 J.D. Power 用 PP100 分层而非判差同源。
    # 保留下来的硬约束：ŝ 一定低于有证据车中位（不得冒充更好），且门槛仍在
    # ——无一级证据的车不得进入该维前 30%（由 ranking 层的 percentile 门槛执行）。
    "safety":     {"need": "crash",      "cap_to": "impute", "impute_kappa": 1.0, "min_proven": 3,
                   "why": "官方碰撞成绩（C-NCAP 得分率 / C-IASI 等级）"},
    "adas":       {"need": "experience", "cap_to": "impute", "impute_kappa": 1.0, "min_proven": 3,
                   "why": "独立实测或车主样本（配置表看不出算法调校）"},
    "cabin":      {"need": "experience", "cap_to": "impute", "impute_kappa": 1.0, "min_proven": 3,
                   "why": "车机实测/车主反馈（流畅度不是屏幕尺寸）"},
    "control":    {"need": "experience", "cap_to": "impute", "impute_kappa": 1.0, "min_proven": 3,
                   "why": "麋鹿/底盘实测（悬架形式说明不了调校）"},
    "quality":    {"need": "pph",        "cap_to": "impute", "impute_kappa": 1.0,
                   "min_proven": 3, "force": True,
                   "why": "PPH/投诉样本（故障率必须靠真实样本，没样本≠没问题）"},
    # v10.74：拆维后的两个新维，同一套「无证据=中位插补」口径，不判劣
    "reliability": {"need": "pph",       "cap_to": "impute", "impute_kappa": 1.0,
                    "min_proven": 3,
                    "why": "故障频度需真实投诉/PPH 样本（没有样本≠故障少）"},
    "holding_cost": {"need": "resale",   "cap_to": "impute", "impute_kappa": 1.0,
                     "min_proven": 3,
                     "why": "三年保值率需流通协会/精真估等同口径数据（C级估值曲线不算）"},
    # ---- 以下为纸面可判定维度，明确不封顶 ----
    "space":      {"need": "params", "cap_to": None,
                   "why": "轴距/车长/容积是硬数据"},
    "value":      {"need": "params", "cap_to": None,
                   "why": "配置与指导价可算"},
    "powertrain": {"need": "params", "cap_to": None,
                   "why": "电池/电机/续航/充电功率是硬参数（续航达成率另计）"},
}

# PPH 可引用的最小样本量（与 fetch_specs / 口碑口径一致）
PPH_MIN_USERS = 30


def _has_crash(c) -> bool:
    from modules.engine import crash_systems as CS
    return bool(CS.parse_score(c.get("crash")))


def _has_pph(c) -> bool:
    if bool(c.get("ah_pph_usable")) and int(c.get("ah_review_users") or 0) >= PPH_MIN_USERS:
        return True
    # v10.72：车质网投诉明细（≥5条且算出每万台密度）也是质量硬证据。
    # 旧版只看 PPH，导致"PPH样本14不足但有11条投诉/2156每万台"的海狮07EV
    # 被当"无证据"force 到中位，反而高于真正有PPH数据的低分车。
    # 投诉明细由 quality_score.apply 写进 c.quality_detail.complaint。
    d = c.get("quality_detail") or {}
    comp = d.get("complaint") or {}
    # 只要 >=5 条投诉就算有证据——密度(per_10k)因销量缺失算不出不影响
    # "有负面数据"这个事实（岚图知音 6 条投诉但销量缺→旧版 gate_has=False
    # 被当无数据 force 到 q25，反而高于 PPH210 的极狐T5）。
    return int(comp.get("count") or 0) >= 5


def _experience_models(lib) -> dict:
    """{canon(车型): {维度: {来源类型}}} —— vendor_claim（厂商宣传）不算依据。

    必须用 canon 归一化（2026-11-21 修）：证据库里写的是「特斯拉Model Y」，
    候选池里是「特斯拉 Model Y」，裸名比对直接失联 → 它那份麋鹿 80.9km/h
    （五车第一）被当成「零实测」，control 被压在 7.8。同一个车系库/池写法
    不统一是老问题，凡跨库比对一律走 model_names.canon，不要各写一套。
    """
    from modules.contracts.model_names import canon
    out: dict[str, dict[str, set]] = {}
    for e in (lib or {}).get("entries", []):
        m, d, st = e.get("model"), e.get("dimension"), e.get("source_type")
        if st and st != "vendor_claim":
            out.setdefault(canon(m or ""), {}).setdefault(d, set()).add(st)
    return out


def apply_all(cands, experience_lib=None, policy=None, only=None, anchor=None) -> list[dict]:
    """按 DIM_POLICY 对所有维度做证据封顶。返回调整明细。

    only：只处理指定维度（调试/A/B 用）。
    anchor：可选固定锚点快照（anchor_pool.load() 的 evidence_cap）。给定后该维
      cap/anchor 直接取快照值，跳过本池 proven 统计——子榜与全价位榜共用同一把尺子，
      同车同分。车自身「有无证据」仍按本车数据判定，不随池变。
    返回 [{"model","dim","prev","capped","anchor","rule"}]
    """
    from modules.engine import experience_score as ES
    from modules.contracts.model_names import canon
    policy = policy or DIM_POLICY
    lib = experience_lib if experience_lib is not None else ES.load_library()
    exp = _experience_models(lib)
    anchor = anchor or {}

    out: list[dict] = []
    for dim, rule in policy.items():
        if only and dim not in only:
            continue
        cap_to = rule.get("cap_to")
        if not cap_to:
            continue                      # 纸面可判定维度，跳过
        need = rule["need"]

        def has(c, _need=need, _dim=dim):
            if _need == "crash":
                return _has_crash(c)
            if _need == "experience":
                return _dim in exp.get(canon(c.get("model") or ""), {})
            if _need == "pph":
                return _has_pph(c)
            if _need == "resale":
                # v10.74：同口径三年保值率（A/B 级）才算证据，C 级估值曲线只展示
                d = c.get("holding_cost_detail") or {}
                return isinstance(d.get("resale"), (int, float)) and bool(d.get("tier")) \
                    and str(d.get("tier")) in ("A", "B")
            return False

        # 固定锚点快照优先：全价位榜算好的 cap 直接用，不再从子池重算
        # v10.72：quality 规则已从 median 改为 q25，旧锚点（median 口径）失效——
        # 若沿用会破"无数据不得高于有负面数据"不变量。quality 强制动态重算，
        # 待全价位榜重出后锚点快照自然更新为新口径。
        fixed = anchor.get(dim)
        if dim == "quality":
            fixed = None
        if fixed and isinstance(fixed.get("capped"), (int, float)):
            cap = float(fixed["capped"])
            anchor_label = fixed.get("anchor") or f"全价位榜锚点（{fixed.get('n', '?')} 款）"
        else:
            proven = [(c["model"], float((c.get("scores") or {})[dim]))
                      for c in cands
                      if has(c) and isinstance((c.get("scores") or {}).get(dim), (int, float))]
            if not proven:
                continue                  # 全池都无证据 → 没有可校准的锚，不封顶
            # 小样本不封顶：2 款车的最低值不是"全池天花板"，是噪声
            if len(proven) < int(rule.get("min_proven") or 1):
                continue
            if cap_to == "min":
                cap = min(v for _, v in proven)
                anchor_label = min(proven, key=lambda x: x[1])[0]
            elif cap_to == "impute":
                # v10.73 中位插补 + 不确定性折扣：ŝ = median − kappa·σ
                # σ 用总体标准差（有证据车就是全集）。
                # 【为什么是 −1σ 而不是 −σ/√n】首版用 median−σ/√(n+1) 折扣过轻
                # （quality 只降 0.35），无证据车被抬到近中位，全榜名次重排（Spearman≈0.5），
                # 等于用"未知"换掉了"有投诉"的信息。−1σ 的语义是"未知落在有证据车
                # 分布的保守一侧"：明显低于中位、明显高于最差（因为最差往往是极值不是典型）。
                vals = [v for _, v in proven]
                med = statistics.median(vals)
                sd = statistics.pstdev(vals) if len(vals) > 1 else 0.0
                k = float(rule.get("impute_kappa") or 1.0)
                cap = round(med - k * sd, 2)
                anchor_label = (f"中位插补 {med:g} − {k:g}×σ{sd:.2f}"
                                f"（有证据 {len(vals)} 款，未知不判劣、不冒充更好）")
            elif cap_to == "q25":
                # v10.72：下四分位——无数据车低于75%有数据车，但不等于最差
                # （min 会把无数据车压到全池最低，违反"缺数据不判劣"；
                #  median 又让无数据车高于有负面数据的车）。
                sv = sorted(v for _, v in proven)
                pos = 0.25 * (len(sv) - 1)
                lo = int(pos)
                hi = min(lo + 1, len(sv) - 1)
                cap = round(sv[lo] + (sv[hi] - sv[lo]) * (pos - lo), 2)
                anchor_label = f"有证据车下四分位（{len(proven)} 款）"
            else:                         # median
                cap = round(statistics.median([v for _, v in proven]), 2)
                anchor_label = f"有证据车中位（{len(proven)} 款）"
            # v10.63：通用封顶偏移（如 safety -1.0 = 无成绩车必须比有成绩最低还低一档）
            _off = float(rule.get("cap_offset") or 0.0)
            if _off:
                cap = round(cap + _off, 2)
                anchor_label += f"{_off:+.1f}（无证据降档）"

        force = bool(rule.get("force"))
        # v10.73：impute 模式的语义是"赋值"（插补到 ŝ），不是"封顶"——
        # 手填过高要压下来、手填过低要抬上来，否则同一"无证据"状态仍两套尺度。
        assign = force or cap_to == "impute"
        for c in cands:
            if has(c):
                continue
            s = (c.get("scores") or {}).get(dim)
            ok = isinstance(s, (int, float))
            # v10.74 根因修复：旧版 `if not isinstance(s, ...) continue` 把「整维没有分」
            # 的车直接跳过，等于"没有频度/保值率信号"的车该维永久缺失——
            # 而 impute 的语义正是给未知车赋值。缺分车现按 ŝ 补上，否则页面该维留空、
            # 权重和只剩 0.80、总分被压低十几分（启源Q06 84.0→67.7）。
            if not ok and not assign:
                continue
            prev = float(s) if ok else None
            # force/assign：无样本一律标定到 ŝ，不管原值高低——
            # 手填的 8.0 和 5.48 都是「没有依据」，不能因为填得低就吃亏。
            if assign:
                if ok and abs(prev - cap) <= 1e-9:
                    continue
            elif prev <= cap + 1e-9:
                continue
            c["scores"][dim] = cap
            # v10.74.4：结构化记录「哪一维是无证据插补/封顶」，供名次带（rank_band）
            # 做不确定性抽样、页面「未验证 x%」徽标披露。别从 score_cap_reasons 文本
            # 里正则解析——文案一改就断，且发布包里的文案是可翻译的。
            c.setdefault("dim_evidence", {})[dim] = {
                "kind": "imputed" if cap_to == "impute" else "capped",
                "cap": cap, "anchor": anchor_label, "why": rule["why"]}
            c.setdefault("score_cap_reasons", []).append(
                (f"{dim} 无{rule['why']}，中位插补至 {cap:g}（{anchor_label}）"
                 if cap_to == "impute" else
                 f"{dim} 无{rule['why']}，封顶至 {cap:g}（锚点 {anchor_label}）"))
            out.append({"model": c["model"], "dim": dim, "prev": prev,
                        "capped": cap, "anchor": anchor_label, "rule": rule["why"],
                        "forced": force})
    return out


def summarize(adjustments) -> dict:
    """按维度汇总，供 pipeline 打印与报告披露。"""
    by_dim: dict[str, list] = {}
    for a in adjustments:
        by_dim.setdefault(a["dim"], []).append(a)
    return {d: {"count": len(v), "cap": v[0]["capped"], "anchor": v[0]["anchor"],
                "models": [x["model"] for x in v]} for d, v in by_dim.items()}


def uncapped_dims(policy=None) -> list[str]:
    """纸面可判定、明确不封顶的维度（报告里要写明，免得被当成漏改）。"""
    policy = policy or DIM_POLICY
    return [d for d, r in policy.items() if not r.get("cap_to")]
