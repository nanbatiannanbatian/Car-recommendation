#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""榜单页面展示的唯一事实源 —— 区块注册表 + 公共渲染辅助。

为什么需要这个文件（v9.0 架构收敛）：
历史漂亮页面是各 run 目录的一次性 build_*.py 生成的，写死车型和文案、没进发布包；
把渲染逻辑固化进 skill 后，v8.3.5→v8.3.9 连续几轮「改一处、漏一处」：
事实列空了、HTML 有产品力榜 md 没有、跳过后连说明都占版面……
根因是展示逻辑散在 board_v2.py(render_md) 与 render_board_html.py(render) 两处，各改各的。

从 v9.0 起：**所有页面区块在这里注册一次**，markdown / html 两个渲染器都遍历同一份 BLOCKS：
  - 每个区块有 id / 标题 / 适用条件 / 数据来源 / 渲染器前缀
  - 新增区块只改这里 + 各渲染器一个分支；删区块必须显式改注册表（selftest 会拦「悄悄少渲染」）
  - BLOCK_IDS 是契约：selftest 断言历史必要区块一个不少

纪律：
  - 展示层只读数据，不改 scores/total/adjust，不重新打分
  - 缺数据如实标「—」并披露覆盖，不填估计值
  - 两版渲染器（md/html）展示同一组区块，不允许一边有一边没有
"""
from __future__ import annotations

# ---------------------------------------------------------------- 区块注册表
# id, 标题, 适用条件 key, 说明（供 selftest / 文档）
BLOCKS = [
    {"id": "lede", "title": "导语与规则", "when": "always",
     "desc": "主榜唯一权威、事实列不计分、产品力榜并列辅助"},
    {"id": "fact_coverage", "title": "事实列覆盖披露", "when": "has_fact_cols",
     "desc": "哪些事实字段有数据、哪些「—」未采集，不以估计值填充"},
    {"id": "main_board", "title": "打分主榜", "when": "always",
     "desc": "8维加权总分唯一排序；行内事实参考列不计分"},
    {"id": "score_breakdown", "title": "总分构成（维度明细）", "when": "has_dimensions",
     "desc": "每车8维原始分+权重，让用户看穿总分怎么来的"},
    {"id": "tiers", "title": "三档分级", "when": "has_tiers",
     "desc": "能不能买/要不要取舍/现在该不该看——与名次两套逻辑"},
    {"id": "band_subboards", "title": "价位段子榜", "when": "multi_band",
     "desc": "多段全价位榜才有；同分数按预算分组，不重新打分"},
    {"id": "segmented_summary", "title": "分段汇总总榜", "when": "multi_band",
     "desc": "多段全价位榜才有；各段前N按主榜分数汇总"},
    {"id": "product_power", "title": "纯产品力总榜", "when": "has_product_power",
     "desc": "辅助榜，剔除性价比、全池混排，不改主榜名次"},
    {"id": "evidence_notes", "title": "逐车取证依据", "when": "has_notes",
     "desc": "每款车的取证要点/证据等级/销量口径，点开可复核"},
    {"id": "lifecycle", "title": "生命周期提示", "when": "has_lifecycle",
     "desc": "换代/清库/新车爬坡——销量下滑不等于车不行"},
    {"id": "dropped", "title": "落榜与待核名单", "when": "has_dropped",
     "desc": "过闸未过、价格待核、跨级别提醒——落榜≠车不好"},
    {"id": "method_boundary", "title": "方法与边界", "when": "always",
     "desc": "打分公式/稳健性怎么读/闸门规则/为什么这样排除"},
    {"id": "reproduce", "title": "复现说明", "when": "always",
     "desc": "同输入必得同输出、无模型调用、数据来源与命令"},
]

BLOCK_IDS = [b["id"] for b in BLOCKS]

# 契约：历史页面里「必要且数据可支撑」的区块，任何版本不得悄悄少渲染。
REQUIRED_BLOCK_IDS = [
    "main_board", "score_breakdown", "tiers", "product_power",
    "evidence_notes", "lifecycle", "method_boundary", "reproduce",
]


def active_blocks(data: dict, summary: dict | None = None) -> list[dict]:
    """按当前数据判定哪些区块有内容。两版渲染器都用这个函数决定渲染集合。"""
    meta = data.get("meta") or {}
    rows = data.get("main_board") or []
    has_fact_cols = any(
        any((r.get("fact_ref") or {}).get(c.get("label")) not in (None, "—", "")
            for r in rows)
        for c in meta.get("fact_reference_columns") or []
    )
    lacks_fact_cols = any(
        not any((r.get("fact_ref") or {}).get(c.get("label")) not in (None, "—", "")
                for r in rows)
        for c in meta.get("fact_reference_columns") or []
    )
    tiers = (summary or {}).get("tiers") or {}
    # summary 结构：{"label": {"T1":[..],"T2":[..],"T3":[..]}} 或直接内层
    if tiers and all(k in tiers for k in ("T1", "T2", "T3")):
        has_tiers = any(tiers.get(k) for k in ("T1", "T2", "T3"))
    elif tiers:
        first = next(iter(tiers.values()), {})
        has_tiers = isinstance(first, dict) and any(first.get(k) for k in ("T1", "T2", "T3"))
    else:
        has_tiers = any((r.get("tier") or "") for r in rows)
    multi_band = len(data.get("band_subboards") or []) >= 2

    flags = {
        "always": True,
        "has_fact_cols": has_fact_cols or lacks_fact_cols,  # 有任何列就披露覆盖
        "has_dimensions": bool(meta.get("dimensions")),
        "has_tiers": has_tiers,
        "multi_band": multi_band,
        "has_product_power": bool((data.get("product_power_board") or {}).get("items")),
        "has_notes": _has_evidence_notes(data, summary),
        "has_lifecycle": _has_lifecycle(data, summary),
        "has_dropped": _has_dropped(summary),
    }
    return [b for b in BLOCKS if flags.get(b["when"], False)]


def _has_evidence_notes(data: dict, summary: dict | None) -> bool:
    for r in data.get("main_board") or []:
        if r.get("note"):
            return True
    # 从 candidate 注入的 note/证据在 summary 里可能有 evidence 字段
    return bool((summary or {}).get("quality") or (summary or {}).get("airbags"))


def _has_lifecycle(data: dict, summary: dict | None) -> bool:
    if (data.get("meta") or {}).get("lifecycle"):
        return True
    return bool((summary or {}).get("lifecycle"))


def _has_dropped(summary: dict | None) -> bool:
    if not summary:
        return False
    for board in (summary.get("boards") or {}).values():
        if isinstance(board, dict) and (board.get("dropped") or 0) > 0:
            return True
    return bool(summary.get("dropped"))


# ---------------------------------------------------------------- 公共辅助
def fact_coverage(meta: dict, rows: list[dict]) -> tuple[list[str], list[str]]:
    """返回 (有数据的字段标签, 「—」的字段标签)。"""
    cols = meta.get("fact_reference_columns") or []
    have, lack = [], []
    for c in cols:
        lab = c.get("label")
        if any((r.get("fact_ref") or {}).get(lab) not in (None, "—", "") for r in rows):
            have.append(lab)
        else:
            lack.append(lab)
    return have, lack


def dimension_lines(meta: dict) -> list[str]:
    """维度表头，如 ['三电(18.0%)', ...]。"""
    return [f"{d['label']}({float(d['weight']) * 100:.1f}%)"
            for d in meta.get("dimensions") or []]


def method_boundary_text() -> list[str]:
    """方法与边界的固定披露文案（两版共用，避免各写一份漂移）。"""
    return [
        "打分公式：加权分 = Σ(8维原始分 × 权重) × 10 + adjust；adjust 仅来自第三方实测证据且带封顶。",
        "稳健性：10 种 MCDA × 6 套权重，同档组＝名次区间重叠，换算法/权重会换位；"
        "τ 均值低时不要按逐名排序选车。",
        "三档是「能不能买」、主榜名次是「谁更强」，两套逻辑；证据 C 的名次只说明参数表好看。",
        "事实列（kWh/电耗/续航/电芯/销量）只展示不计分，改这些数不动名次。",
        "没有官方碰撞成绩的车不记 0 分、只标缺失；未核验的气囊不判劣、只列待补。",
        "入选只认官方二级分类 + 官方指导价，不用轴距推定级别、不拿促销价入榜。",
    ]


def load_fact_library(explicit_path: str | None = None) -> dict:
    """加载事实参考库：显式 --facts 优先，否则自动用随包事实库。

    v9.0.1 修复「漏喂事实库」bug：此前出榜只从 master_pool 导候选，
    候选里只有 cell_brand/trend_sales，kWh/电耗/CLTC 全空，页面误报「未采集」。
    事实数据其实在 car_all_202610/all_data.json（带易车来源），已提炼为
    categories/fact_library.json 随包发布。本函数让两版出榜链路默认就能拿到。
    """
    import os
    import json as _json
    path = explicit_path
    if not path or not os.path.exists(path):
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(here, "categories", "fact_library.json")
    if not os.path.exists(path):
        return {}
    try:
        return _json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def lifecycle_line(model: str, info) -> str:
    """生命周期条目 → 一句可读中文（两版共用，避免露原始 JSON）。"""
    if isinstance(info, str):
        return f"{model}：{info}"
    if not isinstance(info, dict):
        return f"{model}：{info}"
    status = {
        "new_launch": "新车爬坡期", "facelift_pending": "改款在即（现款清库）",
        "discontinued": "已停产", "clearance": "清库",
        "stable": "在售稳定", "sales_collapse": "销量下滑待确认",
    }.get(str(info.get("status") or ""), str(info.get("status") or "状态未知"))
    parts = [f"{model}：{status}"]
    if info.get("date"):
        parts.append(f"日期 {info['date']}")
    if info.get("note"):
        parts.append(str(info["note"]))
    return "｜".join(parts)


def reproduce_text(meta: dict) -> list[str]:
    return [
        "本页由 board_v2_data.json 渲染，全程无模型调用，同输入必得同输出。",
        "主榜调用 leaderboard.score_rows；事实列不计分、子榜复用主榜分数均由 selftest 验证。",
        "展示区块注册表见 board_view.py；渲染改动只需改该文件的 BLOCKS 与各渲染器分支。",
    ]


def selftest() -> int:
    """契约自检：必要区块一个不能少，两版渲染器都按注册表渲染。"""
    bad = []
    for rid in REQUIRED_BLOCK_IDS:
        if rid not in BLOCK_IDS:
            bad.append(f"缺必要区块 {rid}")
    if len(set(BLOCK_IDS)) != len(BLOCK_IDS):
        bad.append("BLOCKS 有重复 id")
    # 用最小数据验证 active_blocks 基本判定
    data = {"meta": {"dimensions": [{"key": "powertrain", "label": "三电", "weight": .5}],
                     "fact_reference_columns": [{"key": "price", "label": "指导价"}]},
            "main_board": [{"model": "A", "total": 80, "note": "取证说明",
                            "fact_ref": {"指导价": 13.0}, "tier": "T1"}],
            "product_power_board": {"items": [{"model": "A"}]}}
    act = {b["id"] for b in active_blocks(data, None)}
    for rid in ("main_board", "score_breakdown", "product_power", "evidence_notes",
                "method_boundary", "reproduce"):
        if rid not in act:
            bad.append(f"最小数据下缺区块 {rid}")
    if "band_subboards" in act:
        bad.append("单段榜不应出价位段子榜")
    # v9.0.1：事实库自动加载必须有数据（防「漏喂」回归）
    lib = load_fact_library()
    if len(lib) < 50 or not any(v.get("kwh") for v in lib.values()):
        bad.append(f"事实库自动加载异常（{len(lib)} 款）")
    if bad:
        print("[selftest FAIL]", bad)
        return 1
    print(f"[selftest OK] 区块注册表 {len(BLOCK_IDS)} 项，必要契约 {len(REQUIRED_BLOCK_IDS)} 项齐全")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
