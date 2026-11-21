# -*- coding: utf-8 -*-
"""safety_score.py —— 碰撞成绩 → 0-10 分的唯一计算入口（契约层）

为什么单独拎出来（2026-11-21）：
  「得分率 → 分数」这个换算原来有两份实现：
    · engine/safety_check.py 按 official_safety_scores.json 的 map_spec（base 7.0 / ref 80 /
      slope 0.155 / cap 9.3）
    · research/sync_cncap.py 自己写了个 safety_from_pct（cap 10.0、floor 6.0）
  两者在 95%+ 的高分段会给出不同结果（9.3 vs 9.65），且 research 不能反向依赖 engine
  （依赖只能向下），于是只能各写一套——这正是「同一事实两套算法」的温床。

  放到契约层：两侧都向下依赖它，公式只有一份。engine 的 map_spec 仍提供参数
  （不同体系可配置），但算法本身在这里。

约束：零依赖、零 IO，不 import 任何本项目其他包。
"""
from __future__ import annotations


def score_from_pct(pct, base=7.0, ref=80.0, slope=0.155, cap=9.3, floor=0.0,
                   older_penalty=0.0, older_before=0, year=None) -> float | None:
    """综合得分率 → 0-10 分。

    标定依据（C-NCAP 2024 版公开门槛）：80% → 7.0，90% → 8.55，cap 9.3。
    斜率 0.155/百分点，刚过五星约 7.5，五星+ 约 8.9。

    older_penalty：2021 及更早规程较松，成绩降权（默认 0.2，由 map_spec 传入）。
    """
    if pct is None:
        return None
    sc = base + (float(pct) - ref) * slope
    sc = min(sc, cap)
    if year and older_before and int(year) <= int(older_before):
        sc -= older_penalty
    return round(max(floor, min(10.0, sc)), 1)


def score_from_grade(grade, grades: dict) -> float | None:
    """C-IASI / IIHS 等级 → 分数。grades 来自 map_spec。"""
    if not grade or not grades:
        return None
    return grades.get(str(grade))


def score_from_stars(stars, spec: dict) -> float | None:
    """NCAP 星级 → 分数（无乘员保护分项时取保守档）。"""
    key = {5: "five", 4: "four", 3: "three"}.get(int(stars) if stars else 0)
    return spec.get(key) if key else None


def selftest() -> None:
    assert score_from_pct(90.7) == 8.7
    assert score_from_pct(92.7) == 9.0
    assert score_from_pct(100) == 9.3, "cap 必须是 9.3"
    assert score_from_pct(86.7) == 8.0
    # 老规程降权：2019 版 92% → 8.9 - 0.2 = 8.7
    assert score_from_pct(92, year=2019, older_before=2021, older_penalty=0.2) == 8.7
    assert score_from_pct(None) is None
    assert score_from_grade("G+", {"G+": 9.3}) == 9.3
    assert score_from_stars(5, {"five": 8.3}) == 8.3
    print("safety_score selftest OK")


if __name__ == "__main__":
    selftest()
