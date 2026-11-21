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
    "safety":     {"need": "crash",      "cap_to": "min", "min_proven": 3,
                   "why": "官方碰撞成绩（C-NCAP 得分率 / C-IASI 等级）"},
    "adas":       {"need": "experience", "cap_to": "median", "min_proven": 3,
                   "why": "独立实测或车主样本（配置表看不出算法调校）"},
    "cabin":      {"need": "experience", "cap_to": "median", "min_proven": 3,
                   "why": "车机实测/车主反馈（流畅度不是屏幕尺寸）"},
    "control":    {"need": "experience", "cap_to": "median", "min_proven": 3,
                   "why": "麋鹿/底盘实测（悬架形式说明不了调校）"},
    "quality":    {"need": "pph",        "cap_to": "median", "min_proven": 3, "force": True,
                   "why": "PPH/投诉样本（故障率必须靠真实样本，没样本≠没问题）"},
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
    return bool(c.get("ah_pph_usable")) and int(c.get("ah_review_users") or 0) >= PPH_MIN_USERS


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


def apply_all(cands, experience_lib=None, policy=None, only=None) -> list[dict]:
    """按 DIM_POLICY 对所有维度做证据封顶。返回调整明细。

    only：只处理指定维度（调试/A/B 用）。
    返回 [{"model","dim","prev","capped","anchor","rule"}]
    """
    from modules.engine import experience_score as ES
    from modules.contracts.model_names import canon
    policy = policy or DIM_POLICY
    lib = experience_lib if experience_lib is not None else ES.load_library()
    exp = _experience_models(lib)

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
            return False

        proven = [(c["model"], float((c.get("scores") or {})[dim]))
                  for c in cands
                  if has(c) and isinstance((c.get("scores") or {}).get(dim), (int, float))]
        if not proven:
            continue                      # 全池都无证据 → 没有可校准的锚，不封顶
        # 小样本不封顶：2 款车的最低值不是"全池天花板"，是噪声
        if len(proven) < int(rule.get("min_proven") or 1):
            continue

        if cap_to == "min":
            cap = min(v for _, v in proven)
            anchor = min(proven, key=lambda x: x[1])[0]
        else:                             # median
            cap = round(statistics.median([v for _, v in proven]), 2)
            anchor = f"有证据车中位（{len(proven)} 款）"

        force = bool(rule.get("force"))
        for c in cands:
            if has(c):
                continue
            s = (c.get("scores") or {}).get(dim)
            if not isinstance(s, (int, float)):
                continue
            prev = float(s)
            # force=True（quality）：无样本一律标定到中位，不管原值高低——
            # 手填的 8.0 和 5.48 都是「没有依据」，不能因为填得低就吃亏。
            if force:
                if abs(prev - cap) <= 1e-9:
                    continue
            elif prev <= cap + 1e-9:
                continue
            c["scores"][dim] = cap
            c.setdefault("score_cap_reasons", []).append(
                f"{dim} 无{rule['why']}，封顶至 {cap:g}（锚点 {anchor}）")
            out.append({"model": c["model"], "dim": dim, "prev": prev,
                        "capped": cap, "anchor": anchor, "rule": rule["why"],
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
