#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
leaderboard.py —— 品类无关的排行榜引擎
=====================================
把「取数 → 闸门 → 打分 → 审计 → 出榜」抽成一套固定流程，品类差异全部放进
categories/<id>.json。换品类只换配置，不改代码。

  python3 leaderboard.py --list
  python3 leaderboard.py --validate car_ev
  python3 leaderboard.py --slots coffee_grinder      # 看这个品类缺哪类证据源
  python3 leaderboard.py --render car_ev --candidates ../cands.json

三类内容必须分开，混在一起就会出「拿销量榜当产品榜」这类错：
  1. 候选发现（discovery）：谁有资格参评
  2. 市场验证（sales）：卖得怎么样（只作维度之一/门槛，不作排序主依据）
  3. 产品评价（dimensions）：在候选池内做同口径横评

依赖 ranking.py 的 apply_gates / trend_sales / battery_score / audit。
"""
from __future__ import annotations

import argparse
import json
import statistics
import os
import re
import sys
import lifecycle_confirm as LC  # noqa: E402  生命周期确证层（工信部公告/官方/媒体溯源）

try:
    import ranking
except Exception:  # 同目录下应有 ranking.py
    ranking = None

try:
    import robust
except Exception:  # 同目录下应有 robust.py
    robust = None

BASE = os.path.dirname(os.path.abspath(__file__))
CAT_DIR = os.path.join(BASE, "categories")


# ---------------------------------------------------------------- 配置
def list_categories() -> list[str]:
    if not os.path.isdir(CAT_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(CAT_DIR) if f.endswith(".json"))


def load_category(cid: str) -> dict:
    path = os.path.join(CAT_DIR, cid + ".json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"未找到品类配置：{path}")
    cfg = json.load(open(path, encoding="utf-8"))
    cfg["_id"] = cid
    return cfg


def validate(cfg: dict) -> list[str]:
    """返回问题列表，空列表＝通过。"""
    errs = []
    if not cfg.get("id"):
        errs.append("缺 id")
    dims = cfg.get("dimensions") or []
    if len(dims) < 3:
        errs.append("维度少于 3 个，无法构成横评")
    keys = [d.get("key") for d in dims]
    if len(set(keys)) != len(keys):
        errs.append(f"维度 key 重复：{keys}")
    for d in dims:
        if not d.get("weight"):
            errs.append(f"维度 {d.get('key')} 缺 weight")
        if not d.get("evidence"):
            errs.append(f"维度 {d.get('key')} 缺 evidence（该维靠什么数据打分）")
    total = sum(float(d.get("weight") or 0) for d in dims)
    if abs(total - 1.0) > 0.02:
        errs.append(f"权重合计 {total:.3f}，应≈1.0")
    gates = cfg.get("gates") or {}
    if not gates:
        errs.append("缺 gates（没有准入规则，候选池会被销量榜带偏）")
    for gk in ("class", "power", "price"):
        g = gates.get(gk)
        if g and not g.get("basis"):
            errs.append(f"闸门 {gk} 缺 basis（口径依据：官方分类/指导价/到手价）")
    if not (cfg.get("discovery") or {}).get("sources"):
        errs.append("缺 discovery.sources：候选从哪来没写清楚")
    for name, spec in (cfg.get("sliders") or {}).items():
        for dk in spec.get("dims") or []:
            if dk not in keys:
                errs.append(f"滑杆「{name}」指向不存在的维度 {dk}")
    st = cfg.get("evidence_stack") or []
    if st and len(st) < 3:
        errs.append(f"证据栈只有 {len(st)} 层，至少应覆盖安全/质量/实测三类")
    if not st:
        errs.append("缺 evidence_stack：没有证据分层，出榜时无法自动标缺口")
    return errs


# ---------------------------------------------------------------- 五层证据栈
def evidence_stack_report(cfg: dict, sources: list[dict] | None = None) -> list[str]:
    """五层证据栈：每层能支撑什么 / 不能支撑什么 / 独立性 / 本次有几条来源落在该层。

    这一节是为了让「缺哪一层」在出榜时自动暴露，而不是靠人记得去查。
    """
    stack = cfg.get("evidence_stack") or []
    if not stack:
        return ["## 证据栈", "", "> 该品类尚未配置 evidence_stack。"]
    counts = {L.get("key"): 0 for L in stack}
    if sources:
        for s in sources:
            blob = " ".join(str(s.get(k) or "") for k in
                            ("publisher", "title", "site", "url", "summary"))
            for L in stack:
                toks = L.get("match") or [x.get("name", "") for x in (L.get("sources") or [])]
                if any(t and t.lower() in blob.lower() for t in toks):
                    counts[L["key"]] += 1
                    break

    out = ["## 五层证据栈（每层能支撑什么、不能支撑什么）", ""]
    if sources:
        out.append("| 层 | 覆盖内容 | 独立性 | 本次来源数 | 状态 |")
        out.append("|---|---|---|---|---|")
    else:
        out.append("| 层 | 覆盖内容 | 独立性 |")
        out.append("|---|---|---|")
    for L in stack:
        sup = "、".join(L.get("supports") or []) or "—"
        ind = L.get("independence") or "—"
        if sources:
            n = counts.get(L.get("key"), 0)
            flag = "✓ 有" if n else "⚠ **缺层**"
            out.append(f"| {L.get('key')} {L.get('label')} | {sup} | {ind} | {n} | {flag} |")
        else:
            out.append(f"| {L.get('key')} {L.get('label')} | {sup} | {ind} |")
    out += ["", "### 各源的可采信边界", "",
            "| 来源 | 拿来做什么 | 不能拿它做什么 | 位置 |", "|---|---|---|---|"]
    for L in stack:
        for src in (L.get("sources") or []):
            out.append(f"| {src.get('name')} | {src.get('use') or '—'} | "
                       f"{src.get('limit') or '—'} | {src.get('url') or ''} |")
    if sources:
        missing = [f"{L.get('key')} {L.get('label')}" for L in stack
                   if counts.get(L.get("key"), 0) == 0]
        out += ["", ("**本次缺层：** " + "、".join(missing) + "。缺层意味着对应结论无一手支撑，"
                     "必须在榜上标 evidence_gap，不得用软文补。") if missing else
                "**五层齐备。** 仍需人工抽查数字与来源编号。"]
    return out


# ---------------------------------------------------------------- 证据槽位
def slot_report(cfg: dict, sources: list[dict] | None = None) -> str:
    """每个维度列出：打算用哪些证据源 → 哪几类还是一手源、哪几类只有二手。

    这是跨品类迁移时唯一不能照抄的部分：权重可以抄，证据地图不能抄。
    """
    em = cfg.get("evidence_map") or {}
    lines = [f"# 证据槽位：{cfg.get('label', cfg['_id'])}", ""]
    lines.append(f"- 评价对象：{cfg.get('subject', '—')}")
    lines.append(f"- 价格口径：{(cfg.get('gates') or {}).get('price', {}).get('basis', '—')}")
    sales = cfg.get("sales") or {}
    lines.append(f"- 市场验证：{sales.get('from', '—')}"
                 + (f"（{sales.get('note')}）" if sales.get("note") else ""))
    lines += ["", "| 维度 | 权重 | 一手证据源 | 二手/代理源 | 缺口 |", "|---|---|---|---|---|"]
    for d in cfg.get("dimensions") or []:
        ev = d.get("evidence") or []
        first = [e for e in ev if e in (cfg.get("_first_party") or [])]
        second = [e for e in ev if e not in first]
        gap = d.get("gap") or ("—" if first else "无一手源，只能靠二手横评")
        lines.append(f"| {d.get('label')} | {int(float(d['weight'])*100)}% | "
                     f"{'、'.join(first) or '无'} | {'、'.join(second) or '—'} | {gap} |")
    lines += [""] + evidence_stack_report(cfg, sources)
    if cfg.get("category_caveats"):
        lines += ["", "## 该品类的特有坑", ""]
        lines += [f"- {c}" for c in cfg["category_caveats"]]
    return "\n".join(lines)


# ---------------------------------------------------------------- 打分
def _active_dims(cfg: dict) -> list[dict]:
    """排除 excluded_dims（如一手覆盖太薄的维度），只对有资格的维度打分。"""
    excl = set(cfg.get("excluded_dims") or [])
    return [d for d in cfg["dimensions"] if d.get("key") not in excl]


def score_rows(cands: list[dict], cfg: dict, weights: list[float] | None = None) -> list[dict]:
    """weights 为按 dimensions 顺序的权重向量；缺省用配置里的基准权重。

    excluded_dims（如「做工/噪音一手覆盖仅 15%」）不参与打分，避免拿薄证据维
    的插补分去决定名次；剩余权重重归一化。
    """
    dims_all = cfg["dimensions"]
    dims = _active_dims(cfg)
    excl = set(cfg.get("excluded_dims") or [])
    idx = [i for i, d in enumerate(dims_all) if d.get("key") not in excl]
    if weights and len(weights) == len(dims_all):
        w = {dims_all[i]["key"]: float(weights[i]) for i in idx}
    elif weights and len(weights) == len(dims):
        w = {d["key"]: float(weights[i]) for i, d in enumerate(dims)}
    else:
        w = {d["key"]: float(d["weight"]) for d in dims}
    tot = sum(w.values()) or 1.0
    w = {k: v / tot for k, v in w.items()}   # 排除维度后重归一化
    rows = []
    for c in cands:
        sc = c.get("scores") or {}
        missing = [k for k in w if k not in sc]
        total = sum(float(sc.get(k, 0)) * w[k] for k in w) * 10
        total += float(c.get("adjust") or 0)
        rows.append({
            "model": c.get("model"),
            "price": c.get("price"),
            "total": round(total, 1),
            "missing_dims": missing,
            "evidence": c.get("evidence_status") or c.get("grade") or "?",
            "note": c.get("note") or c.get("weak") or "",
            "scores": sc,
            # v9.7.43：事实字段透传给页面渲染（risk_flags 等）。此前 score_rows 只出
            # model/price/total/evidence/note/scores，导致 render 读不到 crash →
            # 全池误标"缺官方碰撞"（连有 Euro NCAP 五星的 007 都被误标）。
            "crash": c.get("crash"),
            "safety_basis": c.get("safety_basis"),
            "safety_confidence": c.get("safety_confidence"),
        })
    rows.sort(key=lambda r: -r["total"])
    return rows


def board_items(cfg: dict) -> list[dict]:
    """取分榜定义。没有 boards 就当成单榜，保证向后兼容。"""
    return cfg.get("boards") or [{"label": cfg.get("label", "全部")}]


def filter_board(cands: list[dict], board: dict) -> list[dict]:
    """按 board.match 过滤候选（子串匹配，大小写不敏感）。

    注意级别匹配靠子串：「中型车」不会命中「中大型车」（后者不含前者），
    但若哪天加了「车」这种过短的关键词就会互相污染——match 值必须写完整级别。
    """
    match = board.get("match") or {}
    return [c for c in cands
            if all(str(match[k]).lower() in str(c.get(k, "")).lower() for k in match)]


def split_boards(cands: list[dict], cfg: dict) -> list[tuple[str, list[dict]]]:
    """按配置里的 boards 分组；没有 boards 就单榜。返回 (label, 子集)。"""
    return [(b.get("label", "全部"), filter_board(cands, b)) for b in board_items(cfg)]


def render(rows: list[dict], cfg: dict, top: int = 10,
           weights: list[float] | None = None) -> str:
    dims = _active_dims(cfg)
    excl = set(cfg.get("excluded_dims") or [])
    idx = [i for i, d in enumerate(cfg["dimensions"]) if d.get("key") not in excl]
    if weights and len(weights) == len(cfg["dimensions"]):
        eff = [float(weights[i]) for i in idx]
    elif weights and len(weights) == len(dims):
        eff = [float(x) for x in weights]
    else:
        eff = [float(d["weight"]) for d in dims]
    tot = sum(eff) or 1.0
    eff = [x / tot for x in eff]
    head = "| # | " + cfg.get("subject", "对象") + " | " + " | ".join(
        f"{d['label']}({int(eff[i]*100)}%)" for i, d in enumerate(dims)) + " | 加权 | 证据 |"
    sep = "|" + "---|" * (len(dims) + 4)
    out = [head, sep]
    for i, r in enumerate(rows[:top], 1):
        cells = " | ".join(
            ("—" if r["scores"].get(d["key"]) is None else f"{float(r['scores'][d['key']]):.1f}")
            for d in dims)
        out.append(f"| {i} | **{r['model']}** | {cells} | **{r['total']:.1f}** | {r['evidence']} |")
    if excl:
        out += ["", f"> 已排除证据薄的维度不参与打分：{('、'.join(sorted(excl)))}。"
                 "排除维分数仍保留在 scores 里供查阅，但不影响名次。"]
    if any(r["missing_dims"] for r in rows):
        out += ["", "> 有行缺维度分，已按 0 计——这会把「没查」误算成「很差」，出榜前必须补齐。"]
    return "\n".join(out)




# ---------------------------------------------------------------- 证据覆盖
def _norm_name(x) -> str:
    return re.sub(r"[\s·（）()\-—_/]+", "", str(x or "")).lower()


def _mentions(c: dict, s: dict) -> bool:
    """来源正文是否真的提到该候选（含别名）。

    不查这一条，多机型横评会被错配给每一款车/机，覆盖率虚高。
    """
    text = _norm_name(str(s.get("title") or "") + str(s.get("summary") or ""))
    if not text:
        return False
    names = [c.get("model", "")] + list(c.get("aliases") or [])
    for n in names:
        key = _norm_name(re.split(r"[（(]", str(n))[0])
        if len(key) >= 2 and key in text:
            return True
    return False
def _is_independent(s: dict, cfg_indep: dict, dim_key: str | None = None) -> bool:
    """一手证据判定，宁严勿松：厂商稿与软文一票否决。

    1) content_nature=brand_official/sponsored 或 ad_score>=40 → 直接判二手
    2) 发行方在独立媒体白名单 → 一手
    3) 投诉原始记录 / 真实用户评论 → 一手
    4) 独立评测（track=review）且广告分低、且正文出现量化实测词 → 一手
    其余（导购、聚合报告、参数页、论坛转述）一律二手。
    """
    pub = str(s.get("publisher") or "")
    nature = str(s.get("content_nature") or "")
    track = str(s.get("evidence_track") or "")
    try:
        ad = float(s.get("ad_score") or 0)
    except Exception:
        ad = 0.0
    if nature in ("brand_official", "sponsored") or ad >= 40:
        return False
    if any(k in pub for k in (cfg_indep.get("publishers") or [])):
        return True
    if track in ("complaint", "user_review"):
        # 投诉/买家评论只能证明「可感知」的维度，不能证明需要仪器或对照实验的维度
        limits = (cfg_indep.get("track_limits") or {}).get(track)
        return True if (not limits or dim_key in limits) else False
    if track == "review" and ad < 25:
        text = str(s.get("title") or "") + str(s.get("summary") or "")
        return any(k in text for k in ("实测", "拆机", "分贝", "克数", "dB"))
    return False


# 量化实测识别（v4.2）：粒径/残粉克数/分贝等「带数字的测量」是盖得/CR 级证据，
# 比定性「好用/安静」高一档。这是 evidence_grade 体系最需要的高权重输入。
_QUANT_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:g|克|mg|dB|db|分贝|µm|μm|um|微米|mm|毫米|cm|rpm|转/分|W|V|%|秒|ml|mL|kg|℃)"
)
_QUANT_KW = ("粒径", "残粉", "吃粉", "分贝", "称重", "复秤", "实测数据", "均匀度", "细粉率",
             "萃取率", "转速", "扭矩", "刹停", "零百", "电耗", "续航实测", "拆解实测")
_QUANT_VERB = ("实测", "测得", "测量", "称得", "复秤", "仪器", "实验室", "统计得出")


def _has_quant(s: dict) -> bool:
    """来源是否含量化实测数据（数字+单位，且出现在测量语境）。"""
    text = str(s.get("title") or "") + str(s.get("summary") or "")
    if not _QUANT_RE.search(text):
        return False
    return any(k in text for k in _QUANT_KW) or any(v in text for v in _QUANT_VERB)


def _evidence_level(s: dict, cfg_indep: dict, dim_key: str | None = None) -> str:
    """证据层级：量化实测 > 一手 > 二手。量化实测 = 一手证据 且 含量化测量数据。"""
    if not _is_independent(s, cfg_indep, dim_key):
        return "二手"
    return "量化实测" if _has_quant(s) else "一手"


def evidence_coverage(cands: list[dict], cfg: dict, sources: list[dict]) -> dict:
    """每个候选 × 每个维度：量化实测 / 一手 / 二手 / 缺失。

    量化实测 = 独立来源带数字化测量（残粉 0.2g、62dB、粒径分布）——榜单可信度的护城河；
    一手 = 独立机构定性实测、拆机观察、投诉原始记录；
    二手 = 厂商规格页、电商详情页、聚合导购（厂方口径或转述）。
    """
    rules = cfg.get("evidence_rules") or {}
    indep = cfg.get("independence") or {}
    src = {s.get("id"): s for s in (sources or [])}
    rows, dim_stat = [], {d["key"]: {"量化实测": 0, "一手": 0, "二手": 0, "缺失": 0}
                          for d in cfg["dimensions"]}
    for c in cands:
        cells = {}
        for d in cfg["dimensions"]:
            kws = rules.get(d["key"]) or []
            hits = []
            for sid in (c.get("source_ids") or []):
                s = src.get(sid)
                if not s:
                    continue
                if not _mentions(c, s):
                    continue   # 来源未提及该机型 → 不能当它的证据
                text = str(s.get("title") or "") + str(s.get("summary") or "")
                if any(k in text for k in kws):
                    hits.append((sid, _evidence_level(s, indep, d["key"])))
            order = {"量化实测": 3, "一手": 2, "二手": 1}
            lvl = "缺失"
            if hits:
                lvl = max((h[1] for h in hits), key=lambda x: order.get(x, 0))
            cells[d["key"]] = {"level": lvl, "ids": [h[0] for h in hits]}
            dim_stat[d["key"]][lvl] += 1
        n1 = sum(1 for v in cells.values() if v["level"] in ("一手", "量化实测"))
        nq = sum(1 for v in cells.values() if v["level"] == "量化实测")
        # 量化实测升权：有量化实测时 A 档门槛降低一格（4→3），无量化实测维持原门槛
        need_a = 3 if nq >= 1 else 4
        grade = "A" if n1 >= need_a else ("B" if n1 >= 2 else "C")
        rows.append({"model": c.get("model"), "cells": cells, "first_party": n1,
                     "quant": nq, "grade": grade})

    # evidence_gate：第一权重维度必须达到指定证据层级才进主榜（如研磨质量须有一手/量化）
    gate = cfg.get("evidence_gate") or {}
    gated_out = []
    if gate and gate.get("dimension"):
        gd = gate["dimension"]
        req = gate.get("require", "first_party")
        for r in rows:
            lvl = (r["cells"].get(gd) or {}).get("level", "缺失")
            ok = (lvl == "量化实测") if req == "quant" else (lvl in ("一手", "量化实测"))
            if not ok:
                r["gate_fail"] = f"{gd} 证据 {lvl} < {req}"
                gated_out.append(r)

    lines = [f"# 证据覆盖：{cfg.get('label', cfg['_id'])}", "",
             f"- 候选 **{len(rows)}** 款 ｜ 维度 **{len(cfg['dimensions'])}** 个",
             f"- 判定口径：量化实测=独立来源带数字化测量（克数/分贝/粒径等）；"
             f"一手=独立定性实测/拆机/投诉原始记录；二手=厂方口径或转述。", "",
             "| 维度 | 权重 | 量化实测 | 仅一手 | 仅二手 | 缺失 | 一手及以上覆盖率 |",
             "|---|---|---|---|---|---|---|"]
    for d in cfg["dimensions"]:
        st = dim_stat[d["key"]]
        cov = (st["量化实测"] + st["一手"]) / len(rows) if rows else 0
        lines.append(f"| {d['label']} | {int(float(d['weight'])*100)}% | **{st['量化实测']}** | "
                     f"{st['一手']} | {st['二手']} | {st['缺失']} | **{cov:.0%}** |")
    lines += ["", "| 候选 | 量化实测维 | 一手及以上维 | 等级 | 缺证据的维度 |",
              "|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda x: (-x["quant"], -x["first_party"])):
        miss = [d["label"] for d in cfg["dimensions"] if r["cells"][d["key"]]["level"] == "缺失"]
        lines.append(f"| {r['model']} | **{r['quant']}** | {r['first_party']}/{len(cfg['dimensions'])} "
                     f"| {r['grade']} | {'、'.join(miss) or '—'} |")
    if gate and gate.get("dimension"):
        lines += ["", f"## evidence_gate（{gate['dimension']} 需 {gate.get('require', 'first_party')}）", ""]
        if gated_out:
            lines.append("以下候选未过证据闸门，只进观察区不进主榜：")
            lines += [f"- **{r['model']}**：{r['gate_fail']}" for r in gated_out]
        else:
            lines.append("全部候选过闸。")
    n_quant_rows = sum(1 for r in rows if r["quant"] > 0)
    lines += ["", f"> 量化实测覆盖：{n_quant_rows}/{len(rows)} 款至少一维有数字化测量。"
              "无量化实测支撑的维度，结论只能停留在「方向性判断」，"
              "不得写成精确数值对比（如「残粉少 0.3g」）。"]
    return {"rows": rows, "dim_stat": dim_stat, "gated_out": [r["model"] for r in gated_out],
            "report": "\n".join(lines)}


# ---------------------------------------------------------------- 决策矩阵与权重情景
def with_config_scores(cands: list[dict], cfg: dict) -> list[dict]:
    """把候选的 scores 替换为「主榜同款最高配置」的 scores —— 判档与主榜同源。

    v9.7.43 根因修复：此前 tier_report/robust_report 用候选池车系基础分
    （如 MG07 的 610版 84.0），而主榜 board_v2 用最高配置锚定分（845版 88.0），
    造成「主榜第2、档位判定却 0/60 进不了前三」的口径打架。
    本函数用 trim_annotate.annotate_all 现算 best_trim.scores，深拷贝注入，
    不污染原始候选；无配置数据的车保留原 scores（score_basis=series_base）。
    """
    import copy
    import trim_annotate as TA
    out = copy.deepcopy(cands)
    td_all = TA.annotate_all(out, cfg)
    if not td_all:
        return out
    for c in out:
        td = td_all.get(c.get("model")) or {}
        best_name = td.get("best_trim")
        best = next((t for t in td.get("trims", []) if t.get("name") == best_name), None)
        sc = (best or {}).get("scores")
        if sc and all(k in sc for k in (d["key"] for d in cfg.get("dimensions", []))):
            c["scores"] = sc
            c["score_basis"] = "annotated_trim"
            c["scored_trim_used"] = best_name
    return out


def decision_matrix(cands: list[dict], cfg: dict):
    """候选 → (names, matrix, types, imputed)。

    缺维度用**中位数**插补，而不是 0。用 0 等于把「没查」当成「极差」，
    会让稳健性检验得出「这家很差」的假结论；中位数插补至少是中性假设，
    但插补过的候选必须在报告里点名，它的名次不具解释力。
    """
    dims = cfg["dimensions"]
    names, raw, imputed = [], [], {}
    for c in cands:
        sc = c.get("scores") or {}
        row, miss = [], []
        for d in dims:
            v = sc.get(d["key"])
            if v is None:
                miss.append(d["key"])
            row.append(None if v is None else float(v))
        names.append(c.get("model"))
        raw.append(row)
        if miss:
            imputed[c.get("model")] = miss
    for j in range(len(dims)):
        col = sorted(r[j] for r in raw if r[j] is not None)
        med = col[len(col) // 2] if col else 5.0
        for r in raw:
            if r[j] is None:
                r[j] = med
    types = [str(d.get("type") or "benefit") for d in dims]
    return names, raw, types, imputed


def build_schemes(cfg: dict, custom: dict | None = None, probe: float = 85.0):
    """权重情景：基准 / AHP / 每个滑杆单独拉满 / 用户自定义。

    probe 取 85 而不是 100，是为了避免把某组权重压到 0 导致矩阵退化。
    """
    dims = cfg["dimensions"]
    keys = [d["key"] for d in dims]
    base = [float(d["weight"]) for d in dims]
    tot = sum(base) or 1.0
    base = [x / tot for x in base]
    out = {"基准": list(base)}

    ahp_m = cfg.get("ahp_matrix")
    if ahp_m and len(ahp_m) == len(keys) and robust:
        r = robust.ahp_weights(ahp_m)
        out["AHP"] = r["weights"]
        out["_ahp_cr"] = r["cr"]
        out["_ahp_lambda"] = r["lambda_max"]

    specs = cfg.get("sliders") or {}
    for name, spec in specs.items():
        sp = dict(spec)
        sp["_order"] = keys
        for probe_only in (True,):
            vals = {n: (probe if n == name else 50.0) for n in specs}
            out[f"{name}优先"] = robust.tilt_weights(base, vals, {name: sp}, gain=1.0) \
                if robust else list(base)

    if custom:
        vals = {n: float(custom.get(n, 50.0)) for n in specs}
        specs2 = {}
        for n, spec in specs.items():
            sp = dict(spec); sp["_order"] = keys; specs2[n] = sp
        out["用户自定义"] = robust.tilt_weights(base, vals, specs2, gain=1.0) \
            if robust else list(base)
    return out


def _clean_schemes(schemes: dict) -> dict:
    return {k: v for k, v in schemes.items() if not k.startswith("_")}


# ---------------------------------------------------------------- 稳健性
def robust_report(cands: list[dict], cfg: dict, custom: dict | None = None,
                  top_k: int = 3) -> dict:
    if not robust:
        return {"error": "robust.py 未找到，无法做多方法稳健性对比",
                "report": "## 稳健性对比\n\n> robust.py 缺失，跳过。"}
    names, matrix, types, imputed = decision_matrix(cands, cfg)
    schemes = build_schemes(cfg, custom)
    meta = {k: v for k, v in schemes.items() if k.startswith("_")}
    base_key = "用户自定义" if "用户自定义" in schemes else "基准"
    res = robust.run_robust(matrix, schemes[base_key], types, names, top_k=top_k,
                            scheme_weights=_clean_schemes(schemes))
    res["imputed"] = imputed
    res["schemes"] = {k: [round(x, 4) for x in v] for k, v in _clean_schemes(schemes).items()}
    res["ahp_cr"] = meta.get("_ahp_cr")
    # 稳健但证据薄：算法结论稳定，不代表输入可信
    grade = {c.get("model"): (c.get("evidence_status") or c.get("grade") or "?")
             for c in cands}
    thin = []
    for nm, a in res["per_alt"].items():
        if a["method_verdict"] in ("稳定", "较稳") and str(grade.get(nm, "?")).upper() not in ("A",):
            thin.append((nm, grade.get(nm, "?"), a["method_hits"], a["method_n"]))
    if thin:
        res["report"] += (
            "\n\n## 五、稳健但证据薄（算法稳 ≠ 结论可信）\n\n"
            "| 候选 | 证据等级 | 方法命中 | 说明 |\n|---|---|---|---|\n"
            + "\n".join(f"| {nm} | {g} | {h}/{n} | 排序稳定，但一手证据不足，"
                        f"名次不构成推荐 |" for nm, g, h, n in thin)
            + "\n\n> 稳健性只检验「在同一份评分矩阵下换算法会不会变」，"
              "不检验矩阵的输入是否可信。零交付、无实测的车可以在 9 种方法下都排第一，"
              "那只能说明**评分是照着参数表打出来的**，不说明车好。\n")

    extra = []
    if imputed:
        extra += ["", "## 三、插补说明", ""]
        extra.append("以下候选有维度缺分，已用**该维中位数**插补。它们的名次只作参考，"
                     "不构成「好/差」结论：")
        extra.append("")
        for nm, miss in imputed.items():
            extra.append(f"- **{nm}**：缺 {'、'.join(miss)}")
    if res.get("ahp_cr") is not None:
        cr = res["ahp_cr"]
        extra += ["", f"> AHP 判断矩阵一致性比率 CR = **{cr:.3f}**"
                  + ("（<0.1，权重可用）" if cr < 0.1 else "（**≥0.1，判断矩阵自相矛盾，AHP 情景不可采信**）")]
    res["report"] = res["report"] + "\n".join(extra)
    return res


# ---------------------------------------------------------------- 三榜并列
def tri_report(rows: list[dict], cfg: dict, cands: list[dict],
               top: int = 10, weights: list[float] | None = None) -> dict:
    """销量序 / 加权序 / BT 共识序 三榜并列。

    BT 共识不来自「平均名次」，而是把 9 种方法的排序各自转成两两胜负，
    再用 Bradley-Terry 聚合——平均名次会被极端名次带偏，两两胜负不会。
    """
    by_model = {c.get("model"): c for c in cands}
    sales_order = sorted(
        [r for r in rows],
        key=lambda r: -(by_model.get(r["model"], {}).get("trend_sales") or -1))
    w_order = [r["model"] for r in sorted(rows, key=lambda r: -r["total"])]

    bt_order, bt_scores = [], {}
    if robust and ranking:
        names, matrix, types, _ = decision_matrix(cands, cfg)
        w = list(weights) if (weights and len(weights) == len(cfg["dimensions"])) \
            else [float(d["weight"]) for d in cfg["dimensions"]]
        tot = sum(w) or 1.0
        w = [x / tot for x in w]
        rankings = []
        for mkey in robust.METHODS:
            try:
                sc = robust.RUNNERS[mkey](matrix, w, types)
                rk = robust._ranks(sc)
                rankings.append([nm for _, nm in sorted(zip(rk, names))])
            except Exception:
                continue
        if rankings:
            cons = ranking.consensus_from_rankings(rankings)
            bt_order = [r[0] for r in cons["rank"]]
            bt_scores = {r[0]: r[1] for r in cons["rank"]}

    sales_rank = {r["model"]: i for i, r in enumerate(sales_order, 1)}
    w_rank = {m: i for i, m in enumerate(w_order, 1)}
    bt_rank = {m: i for i, m in enumerate(bt_order, 1)} if bt_order else {}

    head = ["| # | 候选 | 趋势月销 | 销量序 | 加权序 | BT共识序 | 销量↔加权 |", "|---|---|---|---|---|---|---|"]
    body = []
    for i, r in enumerate(sorted(rows, key=lambda x: -x["total"])[:top], 1):
        nm = r["model"]
        ts = by_model.get(nm, {}).get("trend_sales")
        d = sales_rank[nm] - w_rank[nm]
        flag = f"+{d}" if d > 0 else str(d)
        if abs(d) >= 3:
            flag = f"**{flag}**"
        body.append(f"| {i} | {nm} | {ts if ts is not None else '—'} | {sales_rank[nm]} | "
                    f"{w_rank[nm]} | {bt_rank.get(nm, '—')} | {flag} |")

    n_common = 0
    if bt_order:
        s_set = {r["model"] for r in sales_order[:top]}
        w_set = set(w_order[:top])
        b_set = set(bt_order[:top])
        n_common = len(s_set & w_set & b_set)
    lines = ["## 三榜并列（销量序 / 加权序 / BT 共识序）", ""]
    lines += head + body
    lines += ["", f"三榜前 {top} 名的三方交集：**{n_common}/{top}**。"]
    if bt_order:
        lines += ["", "BT 共识序前 5：" + " → ".join(
            f"{m}({bt_scores.get(m, 0):.0f})" for m in bt_order[:5])]
    lines += ["", "> 「销量↔加权」为负表示产品分排名低于销量排名（卖得好但维度不出彩），"
                  "为正表示被销量低估。差值 ≥3 已在表中加粗。"]
    return {"sales": [r["model"] for r in sales_order],
            "weighted": w_order, "bt": bt_order, "bt_scores": bt_scores,
            "overlap": n_common, "report": "\n".join(lines)}



# ---------------------------------------------------------------- 权重审计（pyDecision 借）
def weight_audit(cands: list[dict], cfg: dict, weights: list[float] | None = None) -> dict:
    """主观权重 vs 数据隐含权重（熵权 / CRITIC / MEREC / 标准差）。

    「权重是拍的」这个质疑，光靠换算法排序不变回答不了——那只证明算法一致。
    真正能回答它的是：把权重交给数据本身推一遍，看结论是否仍然一致。
    若数据隐含的权重算出的排序与主观权重高度一致，主观权重就不是拍脑袋；
    若差得远，就必须逐维说明「为什么我给的权重与数据的信息量分布不同」。
    """
    if not robust:
        return {"error": "robust.py 缺失", "report": "## 权重审计\n\n> robust.py 缺失，跳过。"}
    names, matrix, types, imputed = decision_matrix(cands, cfg)
    dims = cfg["dimensions"]
    base = list(weights) if (weights and len(weights) == len(dims)) else \
        [float(d["weight"]) for d in dims]
    tot = sum(base) or 1.0
    base = [x / tot for x in base]

    obj = robust.objective_weights(matrix, types)
    obj_mean = [sum(v[j] for v in obj.values()) / len(obj) for j in range(len(dims))]

    sims = {k: robust.weight_similarity(base, v) for k, v in obj.items()}
    sims["客观均值"] = robust.weight_similarity(base, obj_mean)

    def saw_ranks(w):
        sc = robust.m_saw(matrix, w, types)
        return robust._ranks(sc)

    r_base = saw_ranks(base)
    rank_cmp = {k: robust.kendall_tau(r_base, saw_ranks(v)) for k, v in obj.items()}
    rank_cmp["客观均值"] = robust.kendall_tau(r_base, saw_ranks(obj_mean))

    mean_tau = sum(rank_cmp.values()) / len(rank_cmp)
    mean_cos = sum(sims[k]["cosine"] for k in obj) / len(obj)

    L = ["# 权重审计：主观权重 vs 数据隐含权重", ""]
    L.append("> 问题：这套权重是不是拍脑袋？")
    L.append("> 做法：把权重交给数据自己推（不看人怎么想），再比两套排序是否一致。")
    L.append("")
    L.append("## 一、逐维权重对照")
    L.append("")
    L.append("| 维度 | 主观 | 熵权法 | CRITIC | MEREC | 标准差 | 客观均值 | 主观−客观 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for j, d in enumerate(dims):
        diff = base[j] - obj_mean[j]
        L.append(f"| {d['label']} | **{base[j]:.0%}** | {obj['熵权法'][j]:.0%} | "
                 f"{obj['CRITIC'][j]:.0%} | {obj['MEREC'][j]:.0%} | "
                 f"{obj['标准差法'][j]:.0%} | {obj_mean[j]:.0%} | "
                 f"{'+' if diff >= 0 else ''}{diff*100:.0f}pt |")
    L += ["", "## 二、两套权重有多像", "",
          "| 对比 | 余弦相似度 | 权重排序 τ |", "|---|---|---|"]
    for k, v in sims.items():
        L.append(f"| 主观 vs {k} | {v['cosine']:.3f} | {v['tau']:.2f} |")
    L += ["", "## 三、两套权重算出的排序有多像", "",
          "| 排序对比（SAW 同一算法） | Kendall τ |", "|---|---|"]
    for k, v in rank_cmp.items():
        L.append(f"| 主观权重 vs {k}权重 | {v:.3f} |")
    L.append("")
    L.append(f"- 权重余弦均值 **{mean_cos:.3f}**；排序 τ 均值 **{mean_tau:.3f}**")
    if mean_tau >= 0.85:
        verdict = ("**主观权重与数据隐含权重基本一致**：换成纯数据推导的权重，排序几乎不变。"
                   "「权重是拍的」这一质疑在本矩阵上不成立。")
    elif mean_tau >= 0.65:
        verdict = ("**部分一致**：客观权重算出的排序与主观有可见差异，但不是推翻性的。"
                   "需在报告里逐维说明「为什么某一维给得比数据信息量更高/更低」，"
                   "例如安全权重高于熵权，理由是「安全是硬约束，不能用信息量定价」。")
    else:
        verdict = ("**主观权重与数据隐含权重冲突**：换客观权重后排序明显变化。"
                   "这不必然说明主观权重错（数据信息量≠用户效用），但必须逐维给出理由，"
                   "否则排序的解释权就不在方法手里。")
    wtau = sum(sims[k]["tau"] for k in obj) / len(obj)
    if mean_cos >= 0.95 and wtau < 0.7:
        verdict += ("\n\n但要注意一个反差：**余弦相似度很高（{:.3f}）而权重排序 τ 很低（{:.2f}）**。"
                    "这说明各维权重彼此接近、都落在 10%–22% 这个窄带里，"
                    "「哪一维最重要」的顺序对微小扰动极敏感——所以**不要把权重表读成优先级排序**，"
                    "它只表达「大致同等重要，个别维度略高」。".format(mean_cos, wtau))
    L += ["", "## 四、结论", "", verdict, ""]
    L.append("> 注意：客观权重衡量的是「数据里哪一维更能区分候选」，不等于「用户更在意哪一维」。"
             "两者不一致是正常的；被质疑的不是不一致，而是**说不出为什么不一致**。")

    return {"weights": {"subjective": base, **obj, "客观均值": obj_mean},
            "similarity": sims, "rank_agreement": rank_cmp,
            "mean_cos": mean_cos, "mean_tau": mean_tau,
            "verdict": verdict, "report": "\n".join(L)}


# ---------------------------------------------------------------- 单点推荐（voltpilot 借）
def recommend_report(cands: list[dict], cfg: dict, weights: list[float] | None = None,
                     profile: dict | None = None) -> dict:
    """按用户画像给「首选 / 备选 / 不建议」，并机械生成理由。

    理由不是写出来的，是算出来的：
      该车相对全带均值的加权偏离（w × (score − band_mean)）就是它对总分的贡献。
      Top2 正贡献＝强项，Top2 负贡献＝弱项；与亚军的差值分解＝「为什么是它」。
    """
    dims = cfg["dimensions"]
    keys = [d["key"] for d in dims]
    w = list(weights) if (weights and len(weights) == len(dims)) else \
        [float(d["weight"]) for d in dims]
    tot = sum(w) or 1.0
    w = [x / tot for x in w]
    rows = score_rows(cands, cfg, w)
    if not rows:
        return {"report": "## 推荐\n\n> 无候选。"}

    by = {c.get("model"): c for c in cands}
    band_mean = {}
    for d in dims:
        vals = [(r["scores"] or {}).get(d["key"]) for r in rows]
        vals = [float(v) for v in vals if v is not None]
        band_mean[d["key"]] = sum(vals) / len(vals) if vals else 0.0

    def contrib(r):
        out = {}
        for i, d in enumerate(dims):
            v = (r["scores"] or {}).get(d["key"])
            if v is None:
                continue
            out[d["key"]] = w[i] * (float(v) - band_mean[d["key"]])
        return out

    top = rows[0]
    L = ["## 按你的优先级推荐", ""]
    if profile:
        L.append("画像：" + " / ".join(f"{k} {int(float(v))}" for k, v in profile.items()))
        L.append("")
        L.append("| 维度 | 基准权重 | 本次权重 |")
        L.append("|---|---|---|")
        base_w = [float(d["weight"]) for d in dims]
        for i, d in enumerate(dims):
            L.append(f"| {d['label']} | {base_w[i]:.0%} | **{w[i]:.0%}** |")
        L.append("")

    c0 = contrib(top)
    strong = sorted(c0.items(), key=lambda x: -x[1])[:2]
    weak = sorted(c0.items(), key=lambda x: x[1])[:2]
    lab = {d["key"]: d["label"] for d in dims}

    L.append(f"### 首选：**{top['model']}**（{top['total']:.1f} 分，证据 {top['evidence']}）")
    L.append("")
    L.append(f"- **为什么是它**：相对全带均值，强项是 "
             + "、".join(f"{lab[k]}（{float((top['scores'] or {}).get(k, 0)):.1f} vs 均值 "
                        f"{band_mean[k]:.1f}，加权贡献 {v*10:+.1f} 分）" for k, v in strong) + "。")
    L.append(f"- **代价**：弱项是 "
             + "、".join(f"{lab[k]}（{float((top['scores'] or {}).get(k, 0)):.1f} vs 均值 "
                        f"{band_mean[k]:.1f}，{v*10:+.1f} 分）" for k, v in weak)
             + "。你的权重已把这些维度压低/抬高，若你在意它们，结论会变。")
    if len(rows) > 1:
        gap = top["total"] - rows[1]["total"]
        d0, d1 = contrib(top), contrib(rows[1])
        delta = sorted(((k, d0.get(k, 0) - d1.get(k, 0)) for k in d0),
                       key=lambda x: -abs(x[1]))[:2]
        L.append(f"- **与亚军（{rows[1]['model']}）差 {gap:.1f} 分**，主要差在 "
                 + "、".join(f"{lab[k]}（{v*10:+.1f}）" for k, v in delta) + "。")
        # 噪声检查：分差若小于方法间波动，就不能当选型结论
        nm2, mx2, ty2, _ = decision_matrix(cands, cfg)
        _tau = robust.run_robust(mx2, w, ty2, nm2, top_k=3,
                                 scheme_weights={"base": w})["kendall_mean"] if robust else 1.0
        if gap < 1.0:
            L.append(f"  ⚠ **差 {gap:.1f} 分落在算法噪声内**（本榜 10 种方法两两 τ={_tau:.2f}）。"
                     f"首选与亚军实质并列——选谁取决于你在意哪一维，不是看分数。")
    ts = by.get(top["model"], {}).get("trend_sales")
    if ts is not None:
        L.append(f"- **市场验证**：趋势月销 {ts:,}"
                 + ("；月销低于 2,000，残值与配件便利性需另算。" if ts < 2000 else "。"))
    if top.get("note"):
        L.append(f"- **已知短板**：{top['note']}")
    L.append("")

    L.append("### 备选")
    L.append("")
    for r in rows[1:3]:
        c1 = contrib(r)
        edge = sorted(((k, c1.get(k, 0) - c0.get(k, 0)) for k in c1), key=lambda x: -x[1])[:1]
        why = ""
        if edge and edge[0][1] > 0:
            k, v = edge[0]
            why = (f"若你更看重 **{lab[k]}**（它 {float((r['scores'] or {}).get(k, 0)):.1f} vs "
                   f"首选 {float((top['scores'] or {}).get(k, 0)):.1f}），选它；"
                   f"该维加权领先 {v*10:+.1f} 分。")
        L.append(f"- **{r['model']}**（{r['total']:.1f}，证据 {r['evidence']}）：{why or '综合接近，无明显单项优势。'}")
    L.append("")

    warn = []
    for r in rows[:5]:
        g = str(r["evidence"]).upper()
        if g not in ("A",):
            warn.append(f"- **{r['model']}**：证据等级 {r['evidence']}，排序稳但一手证据不足，"
                        f"名次不构成推荐。")
    if warn:
        L += ["### 进在前列但暂不建议", ""] + warn + [""]

    sponsors = cfg.get("sponsor_slots") or []
    hit = [m for m in sponsors if m in [r["model"] for r in rows[:10]]]
    if sponsors:
        L += ["### 赞助位披露", ""]
        if hit:
            L += [f"⚠ **{ '、'.join(hit) }** 是赞助位且落在前 10。赞助只允许出现在独立披露区，"
                  f"不得影响名次；若它的名次因赞助变化，本榜作废。"]
        else:
            L += ["本榜无赞助位进入前 10。"]
        L.append("")
    return {"top": top["model"], "picks": [r["model"] for r in rows[:3]],
            "report": "\n".join(L)}




# ---------------------------------------------------------------- 生命周期
# 销量下滑有两种完全不同的原因，混在一起判会得出错误结论：
#   · 车不行 → 该扣分（真实竞争力下降）
#   · 停产 / 改款在即 / 新车爬坡 → 销量曲线反映的是产品周期，不是竞争力
# 所以先判周期状态，再决定销量能不能当证据。
LC_LABEL = {"active": "在售", "new_launch": "新车爬坡期",
            "facelift_pending": "改款在即（现款清库）",
            "clearance": "清库", "discontinued": "已停产",
            "sales_drop": "销量骤降·需查生命周期"}


def lifecycle_of(c: dict) -> dict:
    # v6.5：优先查确证来源（lifecycle_status.json），回退候选自带标注
    r = LC.resolve(c)
    return {"status": r["status"], "note": r["note"], "date": r["date"],
            "successor": r["successor"], "source": r.get("source", ""),
            "source_type": r.get("source_type", ""), "url": r.get("url", ""),
            "confirmed": r.get("confirmed", False)}


def lifecycle_quiet(c: dict) -> bool:
    """销量暂时低是否属正常周期（不该当负面证据）。"""
    return lifecycle_of(c)["status"] in ("new_launch", "facelift_pending", "clearance", "sales_drop")


# ---------------------------------------------------------------- 三档分级
def tier_rules(cfg: dict) -> dict:
    """判档阈值。写进配置，便于审计与改口径；缺省用下面的保守值。"""
    t = cfg.get("tiering") or {}
    return {
        "top_k": int(t.get("top_k") or 3),
        "t1_min_method_freq": float(t.get("t1_min_method_freq", 0.8)),
        # v9.7.43：家族命中用「票数」而非比例，消除6家族无法整除80%的语义漂移。
        # 6家族中至少5票认可（=83.3%，与原10票制80%≈8票的严格度相当）。
        # 显式 None 才回退比例(兼容旧榜)；缺省给5票。
        "t1_min_family_hits": (None if t.get("t1_min_family_hits") == "ratio"
                               else int(t.get("t1_min_family_hits", 5))),
        "t1_min_joint_freq": float(t.get("t1_min_joint_freq", 0.4)),
        "t1_evidence": t.get("t1_evidence") or ["A"],
        "t1_min_sales": float(t.get("t1_min_sales", 2000)),
        "risk_evidence": t.get("risk_evidence") or ["C"],
        "risk_low_sales": float(t.get("risk_low_sales", 500)),
        # v9.7.41：归一化敏感性判据。第一档要求「换算法、换权重都靠前」，
        # 归一化方式也是「算法选择」的一种——若换个等价的归一化就跳位，说明这个
        # 「稳」是假象。默认 2 位：跨 5 种归一化名次波动 >2 位不进第一档（降第二档）。
        # 只拦第一档，绝不进第三档：名次不稳是算法精度问题，不是产品硬伤。
        "t1_max_norm_swing": int(t.get("t1_max_norm_swing", 2)),
        "norm_audit_enabled": bool(t.get("norm_audit_enabled", True)),
    }


_BASIS_CN = {"cncap": "C-NCAP", "c-ncap": "C-NCAP", "ciasi": "C-IASI", "ciasi_checked": "C-IASI",
             "euro_ncap": "Euro NCAP", "ancap": "ANCAP", "iihs": "IIHS"}


def tier_report(cands: list[dict], cfg: dict, weights: list[float] | None = None,
                label: str | None = None, airbag_gate: bool = False,
                crash_gate: bool = False, norm_audit: bool | None = None) -> dict:
    """按「稳定性 × 证据 × 市场验证」判三档，不排名次。

    为什么不分 1–10 名：名次的可解释性依赖于「分差大于算法噪声」。
    当 10 种算法只给出 7 种互不相同的排序、前几名分差不到 1 分时，
    给第 5 名和第 6 名排序等于制造假精度。三档只回答三个不同的问题：
      第一档：换算法、换权重都靠前 → 不用取舍，直接入围
      第二档：某些偏好下靠前        → 取舍由你的优先级决定，交给滑杆
      第三档：证据不足或有硬风险    → 不是车差，是现在没资格被推荐
    """
    if not robust:
        return {"error": "robust.py 缺失", "report": "## 三档分级\n\n> robust.py 缺失，跳过。"}
    dims = cfg["dimensions"]
    w = list(weights) if (weights and len(weights) == len(dims)) else \
        [float(d["weight"]) for d in dims]
    tot = sum(w) or 1.0
    w = [x / tot for x in w]
    rules = tier_rules(cfg)
    top_k = rules["top_k"]

    names, matrix, types, imputed = decision_matrix(cands, cfg)
    schemes = build_schemes(cfg)
    res = robust.run_robust(matrix, schemes["基准"], types, names, top_k=top_k,
                            scheme_weights=_clean_schemes(schemes))
    per = res["per_alt"]
    scheme_top = res["scheme_top"]
    rows = score_rows(cands, cfg, w)
    by = {c.get("model"): c for c in cands}
    total_score = {r["model"]: r["total"] for r in rows}

    scen_hit = {nm: [s for s, tops in scheme_top.items() if nm in tops] for nm in names}

    # v9.7.41：归一化敏感性审计（借 scikit-criteria 方法学空间思想）。
    # 换算法、换权重都靠前才够第一档；归一化方式也是「算法选择」的一部分——
    # 换个等价归一化就跳位，说明「稳」是假象。只拦第一档，不进第三档。
    # norm_audit=None 时听配置开关；显式 False 可关（如缺维度样本太少的老榜）。
    do_norm = rules["norm_audit_enabled"] if norm_audit is None else norm_audit
    norm_swing = {}   # 车型 → 跨归一化名次跨度（位）
    norm_detail = {}  # 车型 → {ranks, tau_mean}
    if do_norm and hasattr(robust, "normalization_audit"):
        try:
            na = robust.normalization_audit(
                matrix, schemes["基准"], types, names, top_k=top_k)
            for nm in names:
                pa = na["per_alt"][nm]
                norm_swing[nm] = pa["range"]
                norm_detail[nm] = {"ranks": pa["ranks"], "tau_mean": na["tau_mean"]}
        except Exception:
            norm_swing, norm_detail = {}, {}  # 审计失败优雅降级，不阻断出榜

    band_mean = {}
    for d in dims:
        vals = [float((by.get(nm, {}).get("scores") or {}).get(d["key"]))
                for nm in names if (by.get(nm, {}).get("scores") or {}).get(d["key"]) is not None]
        band_mean[d["key"]] = sum(vals) / len(vals) if vals else 0.0
    lab = {d["key"]: d["label"] for d in dims}

    def best_dim(nm):
        sc = (by.get(nm, {}).get("scores") or {})
        devs = [(lab[d["key"]], w[i] * (float(sc[d["key"]]) - band_mean[d["key"]]))
                for i, d in enumerate(dims) if d["key"] in sc]
        if not devs:
            return "—"
        k, v = max(devs, key=lambda x: x[1])
        return f"{k} {v*10:+.1f} 分" if v > 0 else "无相对强项"

    tiers = {"T1": [], "T2": [], "T3": []}
    for nm in names:
        c = by.get(nm, {})
        a = per[nm]
        ev = str(c.get("evidence_status") or c.get("grade") or "?").upper()
        # v9.7.43：证据A重定义——「有权威碰撞成绩」即满足第一档证据要求。
        # 此前 evidence_status 是人工散落字段，没跟碰撞成绩挂钩：007GT有 Euro NCAP
        # 五星(碰撞保护93%同期第一)却因人工标B被挡在T1，导致T1全空、失去推荐意义。
        # 现以 safety_basis（官方体系，含 Euro NCAP/IIHS）为准；非国内体系只提示不阻断。
        OFFICIAL_BASIS = ("cncap", "ciasi", "c-ncap", "ciasi_checked",
                          "euro_ncap", "ancap", "iihs")
        has_crash_evidence = str(c.get("safety_basis") or "").lower() in OFFICIAL_BASIS
        # 无碰撞实锤时一律不认"人工标A"（MG07 曾人工A但crash=无官方成绩）；
        # 证据A = 有权威碰撞成绩，证据等级只降不升。
        ev_for_t1 = "A" if has_crash_evidence else ("B" if ev == "A" else ev)
        crash_basis_note = ""
        if has_crash_evidence and ev not in rules["t1_evidence"]:
            crash_basis_note = (f"碰撞实锤来自 {c.get('safety_basis')}（人工证据等级 {ev}），"
                                f"按官方碰撞成绩认定证据达标")
        sales = c.get("trend_sales")
        lc = lifecycle_of(c)
        quiet = lifecycle_quiet(c)
        risks = []
        if lc["status"] == "discontinued":
            risks.append(f"已停产{'（' + lc['note'] + '）' if lc['note'] else ''}"
                         f"{'，替代车型 ' + lc['successor'] if lc['successor'] else ''}")
        # v9.7.18：证据 C（多为缺官方碰撞成绩）不再一律判第三档。
        # 原逻辑把「没有证据」当成「车有硬伤」，与「降权≠判劣」纪律矛盾——
        # 零跑B01 销量第二/产品力第一，仅因无 C-NCAP 成绩被打成暂缓，明显过严。
        # 新规则：证据 C 只降低第一档资格（下面 t1_evidence 仍要求 A），
        # 并写进 t1_notes 做提示；只有停产/无交付/低销量/人工 tier_risk 等硬风险才进第三档。
        ev_soft = []
        if ev in rules["risk_evidence"]:
            ev_soft.append(f"证据 {ev}：一手证据不足（不进第一档，但不判劣）")
        # v9.7.43：区分「销量数据缺失(None)」与「确认零销量/无交付(0)」——
        # 此前 `sales is None or == 0` 合并处理，导致补录但未查到单月销量的车(如极氪007三厢版,
        # 产品力87.6/10次进前3)被误判「无交付」降T3。数据缺失是证据问题，不是交付失败：
        # 不降T3，但写进 ev_soft 只降低第一档资格，并在报告标注「销量待核」。
        if sales is None:
            ev_soft.append("销量数据缺失（未查到可靠单月销量，非无交付）：不进第一档，但不判劣、不降第三档")
        elif sales == 0:
            if lc["status"] != "new_launch":
                risks.append("无交付 / 零销量")
        elif sales < rules["risk_low_sales"] and not quiet:
            risks.append(f"趋势月销仅 {sales:,}，保有量过低")
        if c.get("tier_risk"):
            risks.append(str(c["tier_risk"]))

        rec = {"model": nm, "evidence": ev, "sales": sales,
               "method_hits": a["method_hits"], "method_n": a["method_n"],
               "method_freq": a["method_freq"],
               # v9.7.43：家族去重命中率（第一档判据用这个，防相关算法重复计数）
               "family_freq": a.get("family_freq"),
               "family_hits": a.get("family_hits"),
               "family_n": a.get("family_n"),
               "family_names": a.get("family_names") or [],
               "joint_hits": a["topk_count"], "joint_freq": a["topk_freq"],
               "scenarios": scen_hit[nm], "risks": risks, "best": best_dim(nm),
               "score": total_score.get(nm), "note": c.get("note") or "",
               "sales_scope": c.get("sales_scope"), "sales_note": c.get("sales_note"),
               "lifecycle": lc,
               "evidence_soft": ev_soft,
               # v9.7.43：判据展示用——有碰撞实锤时显示"达标(体系)"而非原始人工等级，
               # 避免第一档表写"证据B"让人以为规则失效。
               "evidence_t1_label": (f"证据达标（{_BASIS_CN.get(str(c.get('safety_basis') or '').lower(), c.get('safety_basis'))}碰撞实锤）"
                                     if has_crash_evidence else f"证据 {ev}"),
               "norm_swing": norm_swing.get(nm),
               "norm_detail": norm_detail.get(nm),
               "price": c.get("price") or "",
               # v9.7.43：判档已用配置锚定分，评分配置名也必须跟着用同款，
               # 否则第一档表显示610版、分却是845版的（标签与分数不同源）。
               "trim": (c.get("scored_trim_used") or (c.get("trim") or {}).get("name"))}

        blockers = []
        # v9.7.43：第一档用家族去重命中率（同家族算法算1票，防MG07那种6票全抱团虚高）
        _ff = a.get("family_freq")
        _ff_hits = a.get("family_hits")
        _ff_n = a.get("family_n") or 6
        _is_family = _ff is not None
        if not _is_family:
            # 兼容旧数据：无家族字段时回退原算法命中
            _ff, _ff_hits, _ff_n = a["method_freq"], a["method_hits"], a["method_n"]
        # 优先按「票数」判（6家族至少5票），语义明确；无票数配置时回退比例
        _min_hits = rules["t1_min_family_hits"] if _is_family else None
        if _min_hits is not None:
            _pass_hit = _ff_hits >= _min_hits
        else:
            _pass_hit = _ff >= rules["t1_min_method_freq"]
        if not _pass_hit:
            _need = f"{_min_hits}/{_ff_n}票" if _min_hits is not None else f"{rules['t1_min_method_freq']:.0%}"
            blockers.append(f"独立算法家族命中 {_ff_hits}/{_ff_n}={_ff:.0%}<{_need}")
        if a["topk_freq"] < rules["t1_min_joint_freq"]:
            blockers.append(f"全情景命中 {a['topk_freq']:.0%}<{rules['t1_min_joint_freq']:.0%}")
        if ev_for_t1 not in rules["t1_evidence"]:
            blockers.append(f"证据 {ev} 未达 {'/'.join(rules['t1_evidence'])}（无权威碰撞成绩）")
        if airbag_gate:
            # 第1档气囊准入闸——三态，**只拦「明确缺失」，不拦「未核验」**。
            # 为什么不再用 null 拦：null 表示「我们没取到证据」，是数据缺口，
            # 不是产品缺陷。把 null 当 false 用，等于用我方的检索盲区去降一款车的档，
            # 举证责任倒置；而且本价位侧气囊+气帘覆盖率 92%（35/38），
            # 这种闸门几乎没有区分度，真正被它筛掉的只有「没查到的那几台」。
            # airbags.json 的 policy A2 本来就写着「null 不参与修正也不触发硬伤提示」，
            # 这里与之一致。未核验的车不降档，但必须打标进「待补清单」，不许悄悄放行。
            ab = c.get("airbags") or {}
            miss = [lab for k, lab in (("airbag_front_side", "前排侧气囊"),
                                       ("airbag_curtain", "头部气帘"))
                    if ab.get(k) is False]
            if miss:
                blockers.append("带内气囊明确缺失：" + "/".join(miss))
            elif not (ab.get("airbag_front_side") is True
                      and ab.get("airbag_curtain") is True):
                rec["airbag_pending"] = True
                rec.setdefault("t1_notes", []).append(
                    "气囊配置未核验：不因此降档（数据缺口不等于缺失），已列入待补清单")
        if crash_gate:
            # 第一档＝「不用取舍、可以直接去谈价」，被动安全不能只有插补值。
            # 只认官方碰撞成绩（C-NCAP / C-IASI）；safety_basis 由 sync_cncap.py 写成，
            # 是唯一权威源，不采信人工填的 evidence_status（两者曾出现过不一致）。
            # Euro NCAP / ANCAP 同为官方政府背书的独立碰撞测评，中国品牌出口车常只有它。
            # 早前白名单漏了 euro_ncap，导致 MG4 明明有 Euro NCAP 五星却被判「无官方成绩」。
            basis = str(c.get("safety_basis") or "")
            if basis not in ("cncap", "ciasi", "c-ncap", "ciasi_checked",
                             "euro_ncap", "ancap"):
                blockers.append("无官方碰撞成绩（安全分为插补值）")
        if (sales or 0) < rules["t1_min_sales"] and not quiet:
            blockers.append(f"月销 {(sales or 0):,}<{int(rules['t1_min_sales']):,}")
        elif (sales or 0) < rules["t1_min_sales"] and quiet and lc["status"] != "new_launch":
            # 改款/清库期的低销量不阻断第一档，但必须写明原因，否则读者会以为它卖得动
            rec.setdefault("lc_notes", []).append(
                f"月销 {(sales or 0):,} 低于阈值，但处于{LC_LABEL.get(lc['status'], lc['status'])}，"
                f"不作为市场验证失败的证据（改款后需重新评估）")
        # v9.7.41：归一化敏感性——名次跨归一化方式波动过大，不满足「换算法也稳」。
        # 只拦第一档；名次不稳是算法精度问题，不是产品硬伤，绝不进第三档。
        sw = norm_swing.get(nm)
        if sw is not None and sw > rules["t1_max_norm_swing"]:
            blockers.append(f"归一化敏感：跨方式名次波动 {sw} 位 > {rules['t1_max_norm_swing']} 位")
            rec.setdefault("t2_notes", []).append(
                f"名次在 5 种归一化间摆动 {sw} 位（换等价归一化就换位），"
                f"不满足「换算法也稳」，不进第一档；这不是产品缺陷，不降第三档")
        rec["t1_blockers"] = blockers

        if risks:
            tiers["T3"].append(rec)
        elif not blockers:
            if ev_soft:
                rec.setdefault("t1_notes", []).extend(ev_soft)
            # v9.7.43：非国内体系碰撞成绩的含金量提示（不阻断T1，只是披露）
            if crash_basis_note:
                rec.setdefault("t1_notes", []).append(crash_basis_note)
            tiers["T1"].append(rec)
        else:
            if ev_soft:
                rec.setdefault("t2_notes", []).extend(ev_soft)
            # v9.7.43：第二档内"强推荐"标记——有权威碰撞实锤 + 高分 + 无硬风险，
            # 但换算法不够稳（命中未达T1）。回答"车好，只是分数挤团不够稳压"。
            # 不是新档位，只是T2内的可视化提示，不改三档结构。
            _sc = total_score.get(nm) or 0
            rec["strong_pick"] = bool(
                has_crash_evidence and _sc >= 80 and not risks
                and (rec.get("family_freq") or 0) >= 0.5)
            if rec["strong_pick"]:
                rec.setdefault("t2_notes", []).append(
                    f"强推荐候选：总分{_sc:.1f}+{c.get('safety_basis')}碰撞实锤，"
                    f"独立算法家族命中 {rec.get('family_hits')}/{rec.get('family_n')}={rec.get('family_freq', 0):.0%}<80%故未进第一档；车本身无明显短板")
            tiers["T2"].append(rec)

    for k in tiers:
        tiers[k].sort(key=lambda r: (-(r.get("family_freq") or 0), -r["joint_freq"], -(r["score"] or 0)))

    L = [f"## 三档分级：{label or cfg.get('label')}", ""]
    L.append(f"> **不出 1–10 名。** 本榜 {res['n_methods']} 种算法两两 Kendall τ 均值 "
             f"**{res['kendall_mean']:.3f}**，有效独立意见约 "
             f"{_independent_count(res['tau_matrix'])} 组；"
             f"前几名分差落在算法噪声内，逐名排序等于制造假精度。")
    L.append(">")
    L.append("> 三档回答的是三个不同问题：**能不能直接买 / 要不要按偏好取舍 / 现在该不该看它**。")
    L.append("")
    n_pure = sum(1 for c in cands if c.get("sales_scope") == "pure_bev")
    n_total = sum(1 for c in cands if c.get("sales_scope") == "series_total")
    if n_total:
        L.append(f"> **销量口径**：{n_pure} 款取到纯电单独销量；{n_total} 款厂家未拆车系名，"
                 f"只有车系合计（表中写作 **≤X（车系合计）**，是纯电销量的上界，不是纯电值）。")
        L.append("")
    L.append("## 判档规则（先看规则，再看车）")
    L.append("")
    L.append("| 档 | 条件（全部满足） | 含义 |")
    L.append("|---|---|---|")
    ab_cond = "、气囊基线无明确缺失（未核验不拦，只标待补）" if airbag_gate else ""
    ab_cond += "、有官方碰撞成绩（C-NCAP/C-IASI）" if crash_gate else ""
    norm_cond = ""
    if do_norm and norm_swing:
        norm_cond = f"、跨归一化名次波动 ≤{rules['t1_max_norm_swing']} 位"
    _hit_cond = (f"独立算法家族命中 ≥{rules['t1_min_family_hits']}/{len(robust.METHOD_FAMILIES)}票"
                 if rules.get("t1_min_family_hits") is not None
                 else f"独立算法家族命中 ≥{rules['t1_min_method_freq']:.0%}")
    L.append(f"| **第一档** | {_hit_cond}（10算法按原理去重成6家族、同家族只算1票）、"
             f"全情景命中 ≥{rules['t1_min_joint_freq']:.0%}、证据 ∈ {rules['t1_evidence']}、"
             f"趋势月销 ≥{int(rules['t1_min_sales']):,}{ab_cond}{norm_cond} | 换算法换权重都在前列，不用取舍 |")
    L.append("| **第二档** | 不触发第三档风险，但未达第一档 | 强弱项分明，取舍交给滑杆 |")
    L.append(f"| **第三档** | 硬风险：停产 / 无交付 / 月销极低 / 营运为主 "
             f"<{int(rules['risk_low_sales']):,} / 人工标注硬伤 | 不是车差，是现在没资格被推荐 |")
    L.append("")

    def _line(r):
        # 车系合计是纯电销量的**上界**（含同车系的插混/增程），必须用 ≤ 表示，
        # 否则会被读成「纯电卖了这个数」。
        if r["sales"] and r.get("sales_scope") == "series_total":
            stxt = f"月销 ≤{r['sales']:,}（车系合计）"
        elif r["sales"]:
            stxt = f"月销 {r['sales']:,}"
        else:
            stxt = "月销 —"
        bits = [r.get("evidence_t1_label") or f"证据 {r['evidence']}", stxt]
        if r.get("family_hits") is not None:
            bits.append(f"独立家族命中 {r['family_hits']}/{r['family_n']}（{r.get('family_freq', 0):.0%}）")
        bits.append(f"{r['joint_hits']}/{res['n_runs']} 次进前{top_k}（{r['joint_freq']:.0%}）")
        lcs = (r.get("lifecycle") or {}).get("status")
        if lcs and lcs != "active":
            bits.append(LC_LABEL.get(lcs, lcs))
        if r["price"]:
            bits.append(f"{r['price']} 万")
        return " ｜ ".join(bits)

    # 生命周期单列一节：这是「销量下滑」与「车不行」的分界线，必须让读者先看到
    lc_rows = [(r["model"], r["lifecycle"], r["sales"]) for r in
               (tiers["T1"] + tiers["T2"] + tiers["T3"])
               if (r.get("lifecycle") or {}).get("status") not in (None, "", "active")]
    if lc_rows:
        L.append("## 生命周期提示（销量下滑 ≠ 车不行）")
        L.append("")
        L.append("| 车型 | 状态 | 对销量数据的影响 | 说明 |")
        L.append("|---|---|---|---|")
        for nm, lc, sales in lc_rows:
            st = lc.get("status")
            impact = {"discontinued": "销量不再作为竞争力证据；**不建议现在买**（残值/配件/售后）",
                      "facelift_pending": "现款清库，销量下滑属正常；**等新款上市再决策**",
                      "new_launch": "爬坡期，月销低不等于卖不动；等 3–6 个月再看",
                      "clearance": "清库中，价格低但配置代际落后"}.get(st, "—")
            L.append(f"| {nm} | **{LC_LABEL.get(st, st)}**{('（' + lc['date'] + '）') if lc.get('date') else ''} "
                     f"| {impact} | {lc.get('note') or '—'} |")
        L.append("")
        L.append("> 这一节存在的理由：把「销量曲线难看」直接当成「产品差」是最常见的误判。"
                 "停产车的销量是**退出过程**，改款车的销量是**清库过程**，"
                 "两者都不能用来说明竞争力。")
        L.append("")

    L.append(f"## 第一档 · 可推荐（{len(tiers['T1'])} 款）")
    L.append("")
    if tiers["T1"]:
        L.append("| 车型 | 评分配置 | 判据 | 为什么在这一档 |")
        L.append("|---|---|---|---|")
        for r in tiers["T1"]:
            why = (f"{r.get('family_n', 6)} 个独立算法家族中 {r.get('family_hits', 0)} 个进前{top_k}"
                   f"（10算法按原理去重），{res['n_schemes']} 套权重情景下 {len(r['scenarios'])} 套进前{top_k}")
            L.append(f"| **{r['model']}** | {r['trim'] or '—'} | {_line(r)} | {why} |")
    else:
        near = sorted([r for t in ("T2", "T3") for r in tiers[t]],
                      key=lambda r: -r["joint_freq"])[:3]
        L.append("> **空的。** 没有一款同时满足「算法稳 + 证据足 + 卖得动」，"
                 "说明这个细分里这三件事还没落在同一台车上——不是没有好车，是还没被证实。")
        if near:
            L.append("")
            L.append("| 最接近的几款 | 全情景命中 | 卡在哪一条 |")
            L.append("|---|---|---|")
            for r in near:
                L.append(f"| {r['model']} | {r['joint_freq']:.0%} | "
                         f"{'；'.join(r['t1_blockers']) or '—'} |")
    L.append("")

    L.append(f"## 第二档 · 同档竞争（{len(tiers['T2'])} 款，档内不排名次）")
    L.append("")
    # v9.7.41：把「卡在第一档哪一条」显性化——原来第二档表格只看得到 note，
    # 归一化拦下的车读者不知道为什么没进第一档，等于把判据藏在代码里。
    norm_blocked = [r for r in tiers["T2"]
                    if any("归一化敏感" in b for b in r.get("t1_blockers", []))]
    if norm_blocked:
        L.append("> **名次对归一化敏感，未进第一档**（换等价的归一化方式就会换位，"
                 "说明名次落在算法选择噪声内；不降第三档，因为这不是产品问题）：")
        for r in norm_blocked:
            L.append(f"> - **{r['model']}**：跨 5 种归一化名次 "
                     f"{min(r['norm_detail']['ranks'].values()):.0f}–"
                     f"{max(r['norm_detail']['ranks'].values()):.0f} 名。"
                     f"{'；'.join(r.get('t2_notes', []))}")
        L.append("")
    if tiers["T2"]:
        with_trig = [r for r in tiers["T2"] if r["scenarios"]]
        no_trig = [r for r in tiers["T2"] if not r["scenarios"]]
        if with_trig:
            L.append(f"**有明确偏好触发点（{len(with_trig)} 款）**——把你的优先级输入滑杆，它们会进前三：")
            L.append("")
            L.append("| 车型 | 判据 | 相对强项 | 什么偏好下它进前三 | 已知短板 |")
            L.append("|---|---|---|---|---|")
            for r in with_trig:
                L.append(f"| {r['model']} | {_line(r)} | {r['best']} | "
                         f"{'、'.join(r['scenarios'])} | {r['note'] or '—'} |")
            L.append("")
        if no_trig:
            L.append(f"**无偏好触发点（{len(no_trig)} 款）**——任何单一优先级都推不进前三。"
                     "它们没有硬伤，但在本价位没有一项强到能当理由；"
                     "选它们通常是看品牌、渠道、保值或售后，不是因为产品力胜出：")
            L.append("")
            L.append("| 车型 | 判据 | 相对强项 | 已知短板 |")
            L.append("|---|---|---|---|")
            for r in no_trig:
                L.append(f"| {r['model']} | {_line(r)} | {r['best']} | {r['note'] or '—'} |")
    else:
        L.append("> 空。")
    L.append("")

    L.append(f"## 第三档 · 暂不推荐（{len(tiers['T3'])} 款）")
    L.append("")
    if tiers["T3"]:
        L.append("| 车型 | 触发原因 | 说明 |")
        L.append("|---|---|---|")
        for r in tiers["T3"]:
            only_ev = all("证据" in x for x in r["risks"])
            sc_txt = "不低" if (r["score"] or 0) >= 80 else "中下"
            tail = (f"分数本身{sc_txt}，缺的是「凭什么信它」而不是「它好不好」" if only_ev
                    else "先把触发项补齐（碰撞成绩 / 交付量 / 保有量），才具备被推荐的资格")
            L.append(f"| {r['model']} | {'；'.join(r['risks'])} | "
                     f"{r['method_hits']}/{r['method_n']} 次进前{top_k}；{tail} |")
    else:
        L.append("> 空。")
    L.append("")
    L.append("## 使用方式")
    L.append("")
    L.append("- **第一档**：直接进候选清单，不需要看滑杆。")
    L.append("- **第二档**：先调滑杆（`--sliders \"省心=90,智驾=20\"`），再看谁进前三；"
             "档内顺序不是优劣，是「进前三的频率」。")
    L.append("- **第三档**：不是排除，是**等证据**。零交付的车等交付，证据 C 的等碰撞成绩，"
             "月销极低的等保有量。补齐后重新跑，它会自己上移。")
    L.append("")
    L.append("> 判档阈值写在 `categories/car_ev.json` 的 `tiering` 里，改口径不用改代码；"
             "改完重跑，三档会自己变。")

    return {"tiers": {k: [r["model"] for r in v] for k, v in tiers.items()},
            "detail": tiers, "rules": rules, "kendall": res["kendall_mean"],
            "n_runs": res["n_runs"], "n_schemes": res["n_schemes"],
            "n_methods": res["n_methods"], "tau_matrix": res["tau_matrix"],
            "norm_swing": norm_swing, "norm_audit_enabled": bool(norm_swing),
            "report": "\n".join(L)}


def _independent_count(tau_mat, thresh=0.98):
    """τ≥thresh 的方法合并成等价类，数真正独立的意见有几组。"""
    keys = list(tau_mat)
    parent = {k: k for k in keys}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if tau_mat[a][b] >= thresh:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[ra] = rb
    return len({find(k) for k in keys})




def airbag_tags(c: dict) -> str:
    """被动安全配置事实列（侧气囊 / 头部气帘 / 前排中央气囊）。

    只展示不加权。字段由 airbags.apply() 注入；没有跑该模块时显示「—」，
    不把「没查」显示成「没有」。
    """
    a = c.get("airbags") or {}
    if a.get("status") != "verified":
        return "—"
    mark = lambda v: "有" if v is True else "无" if v is False else "—"
    return "/".join(mark(a.get(k)) for k in
                    ("airbag_front_side", "airbag_curtain", "airbag_center"))


# ---------------------------------------------------------------- 加权排名 + 同档切分
def top_table(cands: list[dict], cfg: dict, weights: list[float] | None = None,
              n: int = 10, gap: float = 1.0, band: float | None = None,
              use_ci: bool = True, n_boot: int = 600) -> dict:
    """给出「名次」，但把分差小于 gap 的相邻名次合并成同一档。

    为什么要同时给名次和同档：只给名次是假精确（算法一换就换位），
    只给档位又答不了「前十是哪几款」。两者并列才既可用又不骗人。
    """
    rows = score_rows(cands, cfg, weights)
    by = {c.get("model"): c for c in cands}
    ranked = rows[:n]

    # 同档判定：优先用 bootstrap 名次区间的重叠，而不是拍一个「1 分」阈值。
    # 区间重叠意味着「在这份评分的误差范围内，两者的名次可以互换」，
    # 这比「分差 0.9 算同档、1.1 算不同档」有依据。
    ci = None
    if use_ci and robust:
        dims = cfg["dimensions"]
        wv = list(weights) if (weights and len(weights) == len(dims)) else \
            [float(d["weight"]) for d in dims]
        tot = sum(wv) or 1.0
        wv = [x / tot for x in wv]
        try:
            names, matrix, types, _ = decision_matrix(cands, cfg)
            ci = robust.bootstrap_rank_ci(matrix, wv, types, names, n_boot=n_boot)
        except Exception:
            ci = None

    # 同档切分用「组内公共交集」而不是「相邻两两重叠」：
    # 两两重叠有传递性陷阱——A 挨着 B、B 挨着 C，会把 10 款串成一组。
    # 要求整组共享一个非空交集，等价于「存在一个名次，组内每台车都可能占它」。
    groups, cur = [], [0]
    if ci and ranked and ranked[0]["model"] in ci:
        lo = ci[ranked[0]["model"]]["p05"]
        hi = ci[ranked[0]["model"]]["p95"]
    else:
        lo = hi = -1
    for i in range(1, len(ranked)):
        nm = ranked[i]["model"]
        if ci and nm in ci and lo >= 0:
            nlo, nhi = max(lo, ci[nm]["p05"]), min(hi, ci[nm]["p95"])
            same = nlo <= nhi
            if same:
                lo, hi = nlo, nhi
        else:
            same = (ranked[i - 1]["total"] - ranked[i]["total"]) < gap
        if same:
            cur.append(i)
        else:
            groups.append(cur)
            cur = [i]
            if ci and nm in ci:
                lo, hi = ci[nm]["p05"], ci[nm]["p95"]
    if cur:
        groups.append(cur)
    gid = {}
    for gi, g in enumerate(groups, 1):
        for i in g:
            gid[i] = gi

    L = [f"## 加权排名（前 {len(ranked)} 名）", ""]
    how = ("同档组按 bootstrap 名次区间（±0.5 分扰动 × 600 次重排）是否重叠判定"
           if ci else f"同档组按相邻分差 <{gap:g} 分判定")
    L.append(f"> {how}；组号相同＝名次不可辨。"
             "**同档组只表示名次不可辨，与下一节「第一/二/三档」不是一回事**："
             "同档组第 1 组也可能是证据不足、不该买的车。")
    L.append("")
    L.append("| 名次 | 车型 | 评分配置 | 指导价 | 趋势月销 | 加权分 | 名次区间(95%) | 同档组 | 气囊 侧/帘/中 | 证据 | 周期 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(ranked):
        c = by.get(r["model"], {})
        trim = c.get("trim") or {}
        tname = trim.get("name") if isinstance(trim, dict) else str(trim or "")
        tprice = trim.get("price") if isinstance(trim, dict) else None
        # 旧数据里有些配置名是占位符（「带内主力配置」「入门」），别把它当配置名展示
        if not tname or any(k in tname for k in ("带内", "主力配置")) or len(tname) <= 2:
            tname = "带内最低配（具体配置待核）"
        if tprice:
            price = f"{tprice:g}"
            if band and tprice > band:
                price += f" ⚠超{band:g}万，需选低配"
        else:
            price = c.get("price") or "—"
        ts = c.get("trend_sales")
        if ts and c.get("sales_scope") == "series_total":
            stxt = f"≤{ts:,}"
        elif ts:
            stxt = f"{ts:,}"
        else:
            stxt = "—"
        lc = lifecycle_of(c)
        lcs = "" if lc["status"] == "active" else LC_LABEL.get(lc["status"], lc["status"])
        civ = ""
        if ci and r["model"] in ci:
            civ = f"{ci[r['model']]['p05']:g}–{ci[r['model']]['p95']:g}"
        # v6.0：有逐配置阶梯的车打「配」角标，只作导购提示，不影响名次
        lad = c.get("trim_ladder") or {}
        ladder_mark = " <sup>配</sup>" if lad.get("status") == "verified" else ""
        L.append(f"| {i+1} | **{r['model']}** | {tname or '—'}{ladder_mark} | {price} | {stxt} | "
                 f"{r['total']:.1f} | {civ or '—'} | {gid[i]} | "
                 f"{airbag_tags(c)} | {r['evidence']} | {lcs or '在售'} |")
    L.append("")
    spans = [f"第 {g[0]+1}" + (f"–{g[-1]+1}" if len(g) > 1 else "") +
             f" 名（{len(g)} 款）" for g in groups]
    if len(groups) == len(ranked):
        L.append(f"- 全部 {len(ranked)} 款分差均 ≥{gap:g} 分，名次可用。")
    else:
        L.append(f"- 同档组：**{'；'.join(spans)}**。同组内换权重就会换位，不要按名次选车。")
    L.append("")
    L.append("> 证据 A＝有官方碰撞成绩；B＝上市半年以上、口碑样本够；C＝新上市或样本不足。"
             "C 级车型的名次只说明「参数表打得好看」，不构成推荐。")
    L.append("> 「气囊 侧/帘/中」＝前排侧气囊 / 前后排头部气帘 / 前排中央（远端）气囊，"
             "按带内推荐配置核验的**事实列，不计入加权**；「—」是未核验，不是没有。"
             "有官方碰撞成绩的车不叠加配置分，避免把同一份保护效果算两遍。")
    L.append("> <sup>配</sup>＝该车型有按 trim 核验的配置阶梯（指导价 / 电池容量 / 电芯供应商 / "
             "加价-增配跃迁），见报告尾部「具体配置推荐与配置阶梯」节及 `trims.md`。"
             "**只回答「这款车买哪个配置」，不参与车与车之间的排序**；没有角标＝未采集，"
             "不代表配置没有差异。")
    L.append("")
    return {"groups": [[ranked[i]["model"] for i in g] for g in groups],
            "ranked": [r["model"] for r in ranked], "report": "\n".join(L),
            "ci": ci or {}}


# ---------------------------------------------------------------- 反方审查（替代辩论）
def review_report(cands: list[dict], cfg: dict, weights: list[float] | None = None,
                  gap_threshold: float = 1.0) -> dict:
    """确定性的「反方清单」——用数据自己质证，不需要辩论。

    辩论能做的三件事：挑反例、找口径冲突、指出证据不足。
    这三件事全部可以由已算好的数据机械生成，而且可复现、不越界、不耗时。
    本函数就是那三件事的实现：六类反对意见，每条都带具体数字。
    """
    dims = cfg["dimensions"]
    keys = [d["key"] for d in dims]
    lab = {d["key"]: d["label"] for d in dims}
    w = list(weights) if (weights and len(weights) == len(dims)) else \
        [float(d["weight"]) for d in dims]
    tot = sum(w) or 1.0
    w = [x / tot for x in w]
    rows = score_rows(cands, cfg, w)
    by = {c.get("model"): c for c in cands}
    K = len(rows)

    # 分三类，不要混：blocking=硬约束被违反（必须修）；
    # boundary=结论的边界（修不了，但必须声明）；warn=证据/口径缺口。
    S = {"blocking": [], "boundary": [], "warn": [], "info": []}
    # A 并列判定：分差小于噪声，不能说谁更强
    ties = []
    for i in range(K - 1):
        gap = rows[i]["total"] - rows[i + 1]["total"]
        if gap < gap_threshold:
            ties.append((rows[i]["model"], rows[i + 1]["model"], round(gap, 1)))
    if ties:
        S["boundary"].append(
            f"**{len(ties)} 处相邻名次分差 <{gap_threshold} 分**，不能当作强弱差异："
            + "；".join(f"{a} vs {b}（差 {g}）" for a, b, g in ties[:4])
            + (" 等" if len(ties) > 4 else ""))

    # B 方法分歧：τ<0.8 的算法对
    tau_pairs = []
    if robust:
        names, matrix, types, _ = decision_matrix(cands, cfg)
        schemes = build_schemes(cfg)
        res = robust.run_robust(matrix, schemes["基准"], types, names, top_k=3,
                               scheme_weights=_clean_schemes(schemes))
        tm = res.get("tau_matrix") or {}
        ks = list(tm)
        for i, a in enumerate(ks):
            for b in ks[i + 1:]:
                if tm[a][b] < 0.8:
                    tau_pairs.append((a, b, tm[a][b]))
        tau_pairs.sort(key=lambda x: x[2])
        if tau_pairs:
            S["boundary"].append(
                f"**{len(tau_pairs)} 对算法的排序一致性 τ<0.8**，换算法会换名次："
                + "；".join(f"{robust.METHOD_LABEL.get(a,a)} vs "
                            f"{robust.METHOD_LABEL.get(b,b)}（τ={v:.2f}）"
                            for a, b, v in tau_pairs[:3]))
        # C 权重敏感：哪些情景换掉前三
        base_top = set(res["scheme_top"].get(list(res["scheme_top"])[0], []))
        moved = [(k, v) for k, v in res["scheme_top"].items()
                 if k != list(res["scheme_top"])[0] and set(v) != base_top]
        if moved:
            S["boundary"].append(
                f"**{len(moved)} 套权重情景会改变前三**："
                + "；".join(f"{k} → {'/'.join(v)}" for k, v in moved[:3])
                + "。说明对「你在意什么」敏感，应交给滑杆而非固定排序。")

    # D 证据缺口
    ev_c = [c["model"] for c in cands
            if str(c.get("evidence_status") or c.get("grade") or "").upper() == "C"]
    pph_thin = [c["model"] for c in cands
                if c.get("ah_pph") is not None and not c.get("ah_pph_usable")]
    unsplit = [c["model"] for c in cands if c.get("sales_scope") == "series_total"]
    if ev_c:
        S["warn"].append(f"**{len(ev_c)} 款证据 C**（一手证据不足，已挡在第三档）："
                         f"{'、'.join(ev_c[:6])}")
    if pph_thin:
        S["warn"].append(f"**{len(pph_thin)} 款 PPH 样本不足 30**，其故障率不作结论："
                         f"{'、'.join(pph_thin[:6])}")
    if unsplit:
        S["warn"].append(f"**{len(unsplit)} 款销量未拆分纯电/混动**（厂家未拆车系名），"
                         f"用的是车系合计：{'、'.join(unsplit[:6])}")

    # D2 被动安全配置（v5.6 方案 A）：只披露，不改三档、不改准入。
    #    没跑 airbags.apply 的候选池不带这个字段，此时整节不输出，避免把「没查」写成结论。
    if any("airbags" in c for c in cands):
        ab_missing, ab_conflict, ab_unknown = [], [], []
        for c in cands:
            a = c.get("airbags") or {}
            if a.get("status") != "verified":
                ab_unknown.append(c["model"])
                continue
            if a.get("airbag_front_side") is False or a.get("airbag_curtain") is False:
                ab_missing.append(c["model"])
            if a.get("conflict"):
                ab_conflict.append(c["model"])
        if ab_missing:
            S["warn"].append(
                f"**{len(ab_missing)} 款明确缺前排侧气囊或头部气帘**："
                f"{'、'.join(ab_missing)}。这两项在 C-NCAP 2024 侧碰/柱碰里直接影响乘员保护得分，"
                "已在 safety 维按 A3 扣 0.3（提示级，不阻断第一档）。")
        if ab_conflict:
            S["warn"].append(
                f"**{len(ab_conflict)} 款气囊配置口径冲突**：{'、'.join(ab_conflict)}。"
                "两源说法不一致时记空值，不参与任何加减分；见「被动安全配置」一节的逐条依据。")
        if ab_unknown:
            S["info"].append(
                f"**{len(ab_unknown)} 款气囊配置未核验**（按 trim 未找到可点开的配置页）："
                f"{'、'.join(ab_unknown[:6])}{' 等' if len(ab_unknown) > 6 else ''}。"
                "这些车不参与加减分，也**不构成「不安全」的结论**——缺数据不判劣。")

    # E2 生命周期
    lc_bad = [(c["model"], lifecycle_of(c)) for c in cands
              if lifecycle_of(c)["status"] == "discontinued"]
    lc_wait = [(c["model"], lifecycle_of(c)) for c in cands
               if lifecycle_of(c)["status"] in ("facelift_pending", "clearance")]
    if lc_bad:
        S["blocking"].append(
            f"**{len(lc_bad)} 款已停产**却仍在候选池："
            + "；".join(f"{m}（{l['note'] or '停产'}）" for m, l in lc_bad)
            + "。停产后残值、配件、OTA 都会断供，不应当作「可买选项」参与排序。")
    if lc_wait:
        S["warn"].append(
            f"**{len(lc_wait)} 款改款/清库在即**："
            + "；".join(f"{m}（{LC_LABEL.get(l['status'])}"
                        f"{'，' + l['date'] if l['date'] else ''}）" for m, l in lc_wait)
            + "。销量下滑是清库所致，不能当竞争力下降的证据；反之现在买现款也要接受很快变老款。")

    # E1 维度有效性：某一维若在本榜是常数，它的权重实际失效
    dead = []
    for d in dims:
        vals = [float((c.get("scores") or {})[d["key"]]) for c in cands
                if (c.get("scores") or {}).get(d["key"]) is not None]
        if len(vals) >= 4:
            sd = statistics.pstdev(vals)
            if sd < 0.15:
                dead.append((d["label"], float(d["weight"]), sd, sum(vals) / len(vals)))
    if dead:
        S["warn"].append(
            "**%d 个维度在本榜无区分度**：" % len(dead)
            + "；".join(f"{lb}（权重 {w:.0%}，全部落在 {mu:.1f}，标准差 {sd:.2f}）"
                        for lb, w, sd, mu in dead)
            + "。这些权重实际上被平均分给所有人，等于没有权重——"
              "原因通常是数据缺失（例如全榜都没有碰撞成绩），"
              "而不是这项配置不重要。补数据前，结论应视为「有效维度的加权」。")

    # E 口径冲突
    price_diff, level_diff = [], []
    for c in cands:
        if c.get("price_official") and c.get("price_prev"):
            import re as _re
            a = _re.findall(r"\d+\.?\d*", str(c["price_prev"]))
            b = _re.findall(r"\d+\.?\d*", str(c["price_official"]))
            if a and b and abs(float(a[0]) - float(b[0])) >= 0.1:
                price_diff.append(f"{c['model']}（我方 {a[0]} / 官方 {b[0]}）")
        if c.get("level_conflict"):
            level_diff.append(c["model"])
    if price_diff:
        S["warn"].append(f"**{len(price_diff)} 款指导价两源不一致**："
                         + "；".join(price_diff[:4]))
    if level_diff:
        S["blocking"].append(f"**{len(level_diff)} 款级别冲突**（官方与自判不一致）："
                             f"{'、'.join(level_diff)}")

    # F 硬约束
    phev = [c["model"] for c in cands
            if str(c.get("power", "")).upper() == "PHEV" and not c.get("excluded")]
    promo = [c["model"] for c in cands
             if any(k in str(c.get("price", "")) for k in ("限时", "补贴", "租电"))]
    if phev:
        S["blocking"].append(f"**{len(phev)} 款动力类型不符**却未剔除：{'、'.join(phev)}")
    if promo:
        S["info"].append(f"{len(promo)} 款价格含促销口径（已标「不计入」）："
                         f"{'、'.join(promo[:4])}")

    L = ["# 反方审查（确定性，无辩论）", ""]
    L.append("> 这一节替代原先的多模型辩论。辩论能给的「反例 / 口径冲突 / 证据不足」，")
    L.append("> 这里全部由数据机械生成：可复现、不越界、不消耗模型调用。")
    L.append("")
    tag = {"blocking": "🔴 阻断级（硬约束被违反，必须修）",
           "boundary": "🟠 结论边界（修不了，但必须写进结论里）",
           "warn": "🟡 提示级（须在榜上披露）",
           "info": "⚪ 说明级"}
    total = 0
    for k in ("blocking", "boundary", "warn", "info"):
        if not S[k]:
            continue
        L.append(f"## {tag[k]}")
        L.append("")
        for it in S[k]:
            L.append(f"- {it}")
            total += 1
        L.append("")
    if S["blocking"]:
        verdict = "**不可直接交付**：存在硬约束违规，修完再出榜。"
    elif S["boundary"]:
        verdict = ("**可交付，但结论只能说到「同档」**：无硬约束违规，"
                   "但分差小于算法噪声 / 方法间分歧明显，逐名排序不成立。"
                   "这一条必须写进报告正文，不能只放在附录。")
    elif S["warn"]:
        verdict = "**可交付，附披露**：无阻断项，提示级问题已在榜上标注。"
    else:
        verdict = "**可交付**：未发现结构性反对意见。仍需人工抽查数字与来源。"
    L += ["## 结论", "", verdict, ""]
    return {"blocking": S["blocking"], "boundary": S["boundary"],
            "warn": S["warn"], "info": S["info"],
            "verdict": verdict, "report": "\n".join(L)}


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--validate", help="校验品类配置")
    ap.add_argument("--slots", help="打印证据槽位与缺口")
    ap.add_argument("--render", help="按品类配置给候选打分出榜")
    ap.add_argument("--coverage", help="统计每个维度的证据覆盖（需 --candidates 与 --research）")
    ap.add_argument("--research", help="research.json（含 sources，用于证据覆盖）")
    ap.add_argument("--out", help="把报告写到文件")
    ap.add_argument("--candidates", help="候选池 json（含 scores 字段）")
    ap.add_argument("--robust", help="多方法稳健性对比（9 种聚合 × 权重情景）")
    ap.add_argument("--sliders", help='用户优先级滑杆，如 "省心=80,智驾=60"（0-100，50=不动）')
    ap.add_argument("--weightaudit", help="主观权重 vs 客观权重（熵权/CRITIC/MEREC）")
    ap.add_argument("--recommend", help="按滑杆画像给首选/备选/不建议")
    ap.add_argument("--review", help="确定性反方审查（替代多模型辩论）")
    ap.add_argument("--tier", help="三档分级（不出名次）：稳定×证据×市场验证")
    ap.add_argument("--tri", action="store_true", help="出榜时附「销量序/加权序/BT共识序」三榜并列")
    ap.add_argument("--top", type=int, default=10)
    a = ap.parse_args()

    custom = None
    if a.sliders:
        custom = {}
        for part in a.sliders.replace("，", ",").split(","):
            if not part.strip():
                continue
            if "=" in part:
                k, v = part.split("=", 1)
            elif ":" in part:
                k, v = part.split(":", 1)
            else:
                sys.exit(f"滑杆格式应为 名称=数值，收到：{part}")
            try:
                custom[k.strip()] = float(v)
            except ValueError:
                sys.exit(f"滑杆数值不是数字：{part}")

    if a.list:
        for cid in list_categories():
            cfg = load_category(cid)
            errs = validate(cfg)
            print(f"{cid:18s} {cfg.get('label','')}  "
                  f"[{'OK' if not errs else f'{len(errs)} 处待修'}]")
        return

    if a.validate:
        cfg = load_category(a.validate)
        errs = validate(cfg)
        if errs:
            print(f"✗ {a.validate}")
            for e in errs:
                print("  -", e)
            sys.exit(1)
        print(f"✓ {a.validate}：{len(cfg['dimensions'])} 维、权重合计 "
              f"{sum(float(d['weight']) for d in cfg['dimensions']):.2f}")
        return

    if a.slots:
        cfg = load_category(a.slots)
        srcs = None
        if a.research:
            srcs = json.load(open(a.research, encoding="utf-8")).get("sources")
        rep = slot_report(cfg, srcs)
        if a.out:
            open(a.out, "w", encoding="utf-8").write(rep + "\n")
            print(f"[ok] 已写入 {a.out}", file=sys.stderr)
        print(rep)
        return

    if a.coverage:
        cfg = load_category(a.coverage)
        data = json.load(open(a.candidates, encoding="utf-8"))
        cands = data.get("candidates", data)
        res = json.load(open(a.research, encoding="utf-8"))
        out = evidence_coverage(cands, cfg, res.get("sources"))
        print(out["report"])
        if a.out:
            open(a.out, "w", encoding="utf-8").write(out["report"] + "\n")
            print(f"\n[ok] 已写入 {a.out}", file=sys.stderr)
        return

    if a.weightaudit:
        cfg = load_category(a.weightaudit)
        data = json.load(open(a.candidates, encoding="utf-8"))
        cands = data.get("candidates", data)
        out = [f"# {cfg.get('label')}", ""]
        for label, sub in split_boards(cands, cfg):
            if not sub:
                continue
            if len(cfg.get("boards") or []) > 1:
                out += [f"## {label}（{len(sub)} 款）", ""]
            wv = build_schemes(cfg, custom).get("用户自定义") if custom else None
            out.append(weight_audit(sub, cfg, wv)["report"])
            out.append("")
        rep = "\n".join(out)
        if a.out:
            open(a.out, "w", encoding="utf-8").write(rep + "\n")
            print(f"[ok] 已写入 {a.out}", file=sys.stderr)
        print(rep)
        return

    if a.review:
        cfg = load_category(a.review)
        data = json.load(open(a.candidates, encoding="utf-8"))
        cands = data.get("candidates", data)
        out = []
        for label, sub in split_boards(cands, cfg):
            if not sub:
                continue
            if len(cfg.get("boards") or []) > 1:
                out += [f"> 分榜：{label}（{len(sub)} 款）", ""]
            wv = build_schemes(cfg, custom).get("用户自定义") if custom else None
            out.append(review_report(sub, cfg, wv)["report"])
            out.append("")
        rep = "\n".join(out)
        if a.out:
            open(a.out, "w", encoding="utf-8").write(rep + "\n")
            print(f"[ok] 已写入 {a.out}", file=sys.stderr)
        print(rep)
        return

    if a.recommend:
        cfg = load_category(a.recommend)
        data = json.load(open(a.candidates, encoding="utf-8"))
        cands = data.get("candidates", data)
        out = [f"# {cfg.get('label')}", ""]
        for label, sub in split_boards(cands, cfg):
            if not sub:
                continue
            if len(cfg.get("boards") or []) > 1:
                out += [f"## {label}（{len(sub)} 款）", ""]
            wv = build_schemes(cfg, custom).get("用户自定义") if custom else None
            out.append(recommend_report(sub, cfg, wv, custom)["report"])
            out.append("")
        rep = "\n".join(out)
        if a.out:
            open(a.out, "w", encoding="utf-8").write(rep + "\n")
            print(f"[ok] 已写入 {a.out}", file=sys.stderr)
        print(rep)
        return

    if a.tier:
        cfg = load_category(a.tier)
        errs = validate(cfg)
        if errs:
            sys.exit("配置未通过校验：" + "；".join(errs))
        data = json.load(open(a.candidates, encoding="utf-8"))
        cands = data.get("candidates", data)
        out = []
        for label, sub in split_boards(cands, cfg):
            if not sub:
                continue
            wv = build_schemes(cfg, custom).get("用户自定义") if custom else None
            r = tier_report(sub, cfg, wv)
            if len(cfg.get("boards") or []) > 1:
                out += [f"> 分榜：{label}（{len(sub)} 款）", ""]
            out.append(r["report"])
            out.append("")
        rep = "\n".join(out)
        if a.out:
            open(a.out, "w", encoding="utf-8").write(rep + "\n")
            print(f"[ok] 已写入 {a.out}", file=sys.stderr)
        print(rep)
        return

    if a.robust:
        cfg = load_category(a.robust)
        errs = validate(cfg)
        if errs:
            sys.exit("配置未通过校验：" + "；".join(errs))
        data = json.load(open(a.candidates, encoding="utf-8"))
        cands = data.get("candidates", data)
        out_lines = [f"# {cfg.get('label')}", ""]
        for label, sub in split_boards(cands, cfg):
            if not sub:
                continue
            if len(cfg.get("boards") or []) > 1:
                out_lines += [f"## {label}（{len(sub)} 款）", ""]
            r = robust_report(sub, cfg, custom)
            out_lines.append(r["report"])
            out_lines.append("")
        rep = "\n".join(out_lines)
        if a.out:
            open(a.out, "w", encoding="utf-8").write(rep + "\n")
            print(f"[ok] 已写入 {a.out}", file=sys.stderr)
        print(rep)
        return

    if a.render:
        cfg = load_category(a.render)
        errs = validate(cfg)
        if errs:
            sys.exit("配置未通过校验：" + "；".join(errs))
        data = json.load(open(a.candidates, encoding="utf-8"))
        cands = data.get("candidates", data)
        out = [f"## {cfg.get('label')}", ""]
        wv = None
        if custom:
            wv = build_schemes(cfg, custom).get("用户自定义")
            if wv:
                out.append("> 权重按滑杆调整：")
                out.append("")
                out.append("| 维度 | 基准 | 本次 |")
                out.append("|---|---|---|")
                base = build_schemes(cfg)["基准"]
                for i, d in enumerate(cfg["dimensions"]):
                    out.append(f"| {d['label']} | {base[i]:.0%} | **{wv[i]:.0%}** |")
                out.append("")
        for label, sub in split_boards(cands, cfg):
            if not sub:
                continue
            if len(cfg.get("boards") or []) > 1:
                out += ["", f"### {label}（{len(sub)} 款过闸）", ""]
            rows = score_rows(sub, cfg, wv)
            out.append(render(rows, cfg, a.top, wv))
            if a.tri:
                out += ["", tri_report(rows, cfg, sub, a.top, wv)["report"]]
        rep = "\n".join(out)
        if a.out:
            open(a.out, "w", encoding="utf-8").write(rep + "\n")
            print(f"[ok] 已写入 {a.out}", file=sys.stderr)
        print(rep)
        return

    ap.print_help()


if __name__ == "__main__":
    main()
