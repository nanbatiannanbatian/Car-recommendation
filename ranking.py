#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ranking.py —— 榜单口径协议（ranking protocol），omni-debate v4.1 配套模块
=====================================================================
解决的问题（都来自真实翻车）：
  1. 候选池用自行发明的轴距规则入榜，而不是官方级别        → 闸门 gate_class
  2. 拿车系合并销量当纯电销量、拿入门版定义整车            → 闸门 gate_power / require_trim
  3. 拿限时价、补贴价、租电价当入榜价格                    → 闸门 gate_price（只认指导价）
  4. 用单月销量评判新车与改款清库车                        → trend_sales()
  5. 无碰撞成绩却给安全高分                                → evidence_grade + 三态
  6. 模型把池外车型排进榜、或整表不提配置                   → audit()

纯标准库，可单独当 CLI 用：
  python3 ranking.py --selftest
  python3 ranking.py --audit final_answer.md --candidates candidates.json
  python3 ranking.py --gate candidates.json
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys

# ---------------------------------------------------------------- 默认闸门
DEFAULT_GATES = {
    "classes": ["中型车", "中型SUV"],          # 官方二级分类白名单
    "power_main": ["BEV"],                     # 主销动力必须是纯电
    "price_band": [12.0, 18.0],                # 万元，含端点
    "require_guide_price": True,               # 只认官方指导价
}

# 电芯品牌基线分（0–10）。注意：这是「体系可信度」而非电芯单体性能排名。
CELL_BASE = {
    "宁德时代": 8.6, "catl": 8.6,
    "弗迪": 8.6, "比亚迪": 8.6, "finDreams": 8.6, "刀片": 8.6,
    "中创新航": 7.2, "calb": 7.2,
    "国轩高科": 7.0, "国轩": 7.0, "gotion": 7.0,
    "亿纬动力": 6.8, "亿纬": 6.8, "eve": 6.8,
    "欣旺达": 6.8, "sunwoda": 6.8,
    "蜂巢能源": 7.0, "蜂巢": 7.0,
    "清陶": 7.0, "卫蓝": 6.8,
    "中航锂电": 7.0,
}

_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}


def _norm(s) -> str:
    return re.sub(r"[\s·（）()\-—_/]+", "", str(s or "")).lower()


def _num(v):
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?\d+(?:\.\d+)?", str(v or ""))
    return float(m.group()) if m else None


# ---------------------------------------------------------------- 趋势销量
def trend_sales(history, window: int = 6, method: str = "trimmed_mean",
                exclude_flags=("clearance", "launch_first")) -> dict:
    """把月度销量序列折成一个「趋势月销」。

    history: [{"month":"2026-08","units":1867,"flag":"clearance"}, ...]
    规则：
      - 带 exclude_flags 的月份剔除（清库、上市首月产能爬坡）
      - 取最近 window 个月
      - trimmed_mean：>=4 个点去掉最大最小再平均；否则算术平均
      - median：中位数（对单月冲量更稳）
    返回 {"value": float|None, "used": [...], "dropped": [...]}
    """
    rows, dropped = [], []
    for h in history or []:
        flag = (h.get("flag") or "").lower()
        u = _num(h.get("units"))
        if u is None:
            continue
        if flag in set(exclude_flags):
            dropped.append({**h, "reason": f"flag={flag}"})
            continue
        rows.append({**h, "units": u})
    rows.sort(key=lambda x: str(x.get("month", "")))
    rows = rows[-window:]
    if not rows:
        return {"value": None, "used": [], "dropped": dropped}
    vals = [r["units"] for r in rows]
    if method == "median":
        v = statistics.median(vals)
    elif len(vals) >= 4:
        v = (sum(vals) - max(vals) - min(vals)) / (len(vals) - 2)
    else:
        v = sum(vals) / len(vals)
    return {"value": round(v, 0), "used": rows, "dropped": dropped}


# ---------------------------------------------------------------- 三道闸门
def apply_gates(c, gates: dict | None = None) -> dict:
    """对单个候选跑三闸门。返回 {pass: bool, failed: [code], notes: [str]}"""
    g = {**DEFAULT_GATES, **(gates or {})}
    failed, notes = [], []

    # 官方二级分类常带注解（如「中型SUV（大五座）」），用包含匹配而非全等
    cls = str(c.get("cls") or c.get("class") or "").strip()
    if not cls:
        failed.append("class_unknown")
        notes.append("未标官方二级分类：不得用轴距自行推定级别")
    elif not any(gc in cls for gc in g["classes"]):
        failed.append("class_out")
        notes.append(f"官方定级「{cls}」不在白名单 {g['classes']}")

    # 生命周期闸门：停产车不是「排名靠后」，而是「已经不能买」。
    # 留在池里只会让整榜被判不可交付，正确做法是直接落榜并写明原因。
    lc = c.get("lifecycle") or {}
    if str(lc.get("status") or "").lower() in ("discontinued", "stopped"):
        failed.append("lifecycle_out")
        notes.append(f"已停产（{lc.get('date') or '官方停止排产'}）"
                     f"{'，替代车型 ' + lc['successor'] if lc.get('successor') else ''}"
                     f"：{'；'.join([lc.get('note')] if lc.get('note') else [])}"
                     "——残值、配件、OTA 断供，不参与在售榜排序")

    power = str(c.get("power") or "BEV").upper().replace("纯电", "BEV")
    if power not in [p.upper() for p in g["power_main"]]:
        if "BEV" in power and ("PHEV" in power or "EREV" in power or "增程" in power):
            notes.append("车系含插混/增程：销量必须标纯电拆分口径")
        else:
            failed.append("power_out")
            notes.append(f"主销动力 {power} 非纯电")

    lo, hi = g["price_band"]
    trim = c.get("trim") or {}
    prices = []
    tp = _num(trim.get("price"))
    if tp is not None:
        prices.append(tp)
    rng = c.get("price_guide")
    if isinstance(rng, (list, tuple)) and len(rng) == 2:
        prices += [_num(rng[0]), _num(rng[1])]
    elif rng is not None:
        p = _num(rng)
        if p is not None:
            prices.append(p)
    prices = [p for p in prices if p is not None]
    if not prices:
        failed.append("price_unknown")
        notes.append("未取得官方指导价：促销价/限时价不能替代")
    elif g.get("price_basis") == "trim" and tp is not None:
        # trim 模式：以「带内推荐配置」的指导价为准。
        # 只看车系有没有低价车是不够的——若推荐配置本身超预算，读者按这个价买不到。
        if tp <= hi:
            pass
        elif min(prices) <= hi:
            notes.append(f"推荐配置 {tp:g} 万超 {hi:g} 万预算：带内需选 "
                         f"{min(prices):g} 万起的最低配置，评分配置与可买配置不一致")
        else:
            failed.append("price_out")
            notes.append(f"官方指导价 {min(prices)}–{max(prices)} 不落 {lo}–{hi} 万")
    elif not any(lo <= p <= hi for p in prices):
        failed.append("price_out")
        notes.append(f"官方指导价 {min(prices)}–{max(prices)} 不落 {lo}–{hi} 万")
    if c.get("price_promo"):
        notes.append(f"促销口径「{c['price_promo']}」仅展示，不参与判定")

    if g.get("require_guide_price") is False:
        notes.append("配置未强制要求指导价，结论可复核性下降")

    return {"pass": not failed, "failed": failed, "notes": notes}


# ---------------------------------------------------------------- 电芯/电池
def battery_score(c: dict) -> dict:
    """三电稳定性里的电池子分（0–10），并给出加减分理由。

    判的不是「电芯品牌排行榜」，而是四件事：
      ① 供应商是否确定（同款车多供应商混装 = 用户抽盲盒）
      ② 电芯体系（LFP 稳、三元能量密度高但热失控裕度小）
      ③ Pack/BMS 是否自研（热管理与均衡策略的主动权）
      ④ 质保条款是否写死衰减阈值（比「终身质保」四个字有用）
    """
    raw = str(c.get("cell_brand") or "").strip()
    key = _norm(raw)
    base, hit = 6.0, None
    for k, v in CELL_BASE.items():
        if _norm(k) and _norm(k) in key:
            if v > base:
                base, hit = v, k
    if hit is None and raw:
        base = 6.4
    score = base
    reasons = [f"电芯 {raw or '未披露'} → 基线 {base:.1f}"]

    if c.get("cell_multi_source"):
        score -= 1.3
        reasons.append("同款多供应商混装 −1.3（同车不同批次电芯不同）")
    if _norm(c.get("bms_owner")) in ("self", "自研", "oem"):
        score += 0.7
        reasons.append("BMS 自研 +0.7")
    if any(t in str(c.get("pack_tech") or "") for t in ("CTB", "CTC", "刀片", "cell to body")):
        score += 0.4
        reasons.append("Pack 与车身/电芯一体化 +0.4")

    w = c.get("battery_warranty") or {}
    if _num(w.get("soh_threshold")) is not None:
        score += 0.6
        reasons.append(f"质保写入衰减阈值（<{w['soh_threshold']}% 换） +0.6")
    if w.get("replace_only"):
        score += 0.3
        reasons.append("只换不修 +0.3")
    if w.get("lifetime"):
        score += 0.4
        reasons.append("三电终身 +0.4")
    if w.get("first_owner_only"):
        score -= 0.4
        reasons.append("终身权益仅限首任 −0.4（转手即失效）")

    score = max(0.0, min(10.0, score))
    return {"score": round(score, 2), "cell": raw or None, "reasons": reasons}


def cell_risk_notes(c: dict) -> list[str]:
    """给裁判/审计用的风险提示语。"""
    out = []
    if c.get("cell_multi_source"):
        out.append("该车型同款多电芯供应商混装，交付批次不可选，三电一致性不可预期")
    if c.get("cell_brand") and not c.get("bms_owner"):
        out.append("未披露 BMS 归属，热管理与均衡策略无法判断")
    w = c.get("battery_warranty") or {}
    if w.get("lifetime") and not _num(w.get("soh_threshold")):
        out.append("「终身质保」未写衰减阈值，实际可执行性存疑")
    return out


# ---------------------------------------------------------------- 提示词协议
def build_protocol(gates: dict | None = None, trend: dict | None = None) -> str:
    g = {**DEFAULT_GATES, **(gates or {})}
    t = {"window": 6, "method": "trimmed_mean", **(trend or {})}
    cls = " / ".join(g["classes"])
    return f"""【榜单口径协议 ranking_protocol（优先级高于模型直觉）】
A. 三道闸门，先筛后排，不合格者不进榜：
   A1 级别闸门：以厂商/易车等「官方二级分类」为准，白名单 = {cls}。
      禁止用轴距、车长自行推定级别；官方定为紧凑型/中大型的一律出局并写入排除清单。
   A2 动力闸门：主销动力必须是纯电。车系含插混/增程的，销量必须标「车系合并」或「纯电拆分」，
      不得把合并销量当纯电销量。
   A3 价格闸门：只认「官方指导价」至少一档落入 {g['price_band'][0]}–{g['price_band'][1]} 万。
      限时价、补贴价、置换价、电池租用车身价（BaaS）只做展示，不参与入榜判定。
B. 销量用「趋势月销」而不是单月：
   B1 窗口 {t['window']} 个月，方法 {t['method']}；上市首月（产能爬坡）与改款清库月必须剔除并注明。
   B2 不得用「月销 N 台」的原始单月值给新车或清库车定性；无历史时写「无历史可参考」，不得判劣。
C. 评分对象是「带内推荐配置」，不是车系：
   C1 每个名次必须写明所评配置名与该配置官方指导价（如「610 激光雷达版 12.69 万」）。
   C2 禁止用入门版（减配智驾/电池/悬架）代表整车的产品力；反之也不得用顶配代表入门体验。
D. 证据分级与置信度：
   D1 安全维必须写三态：有官方碰撞成绩（C-NCAP/C-IASI，注明年份与规程版本）/ 已抽车未测 / 无成绩。
      无成绩不得给高分，也不得判为不安全，应降低该维置信度。
   D2 每条负向结论尽量落到可查编号（车质网投诉编号、召回公告号、监管批次）。
   D3 每行标置信度 A（官方价＋官方碰撞＋多源销量）/ B（缺一项）/ C（价格或参数仍待核）。
E. 电池判「体系确定性」而不是品牌崇拜：
   E1 电芯品牌只是基线分；同款多供应商混装要扣分（用户无法选择批次）。
   E2 BMS 是否自研、Pack 是否 CTB/刀片一体化，权重高于电芯品牌本身。
   E3 质保看条款：写了衰减阈值（如 <77.5% 换）才算硬承诺，「终身质保」未写阈值视同软承诺；
      仅限首任车主的终身权益要打折。
F. 允许留空：可核实候选不足 10 款时，宁可少于 10 款并说明，也不得用跨级车、停售车、类别泛称凑数。"""


# ---------------------------------------------------------------- 越界审计
PROMO_KW = ("限时", "补贴价", "置换价", "租电", "BaaS", "权益价", "预售价", "裸车价")
TRIM_KW = ("版", "款", "配置")
MONTH_RE = re.compile(r"(?<!\d)(\d{1,2})\s*月")
TREND_KW = ("趋势", "月均", "稳态", "近3月", "近三月", "上市以来", "均值")


def _split_row(line: str) -> list[str]:
    cells = [x.strip() for x in line.strip().strip("|").split("|")]
    return cells


def _match_pool(name: str, pool: list[dict]):
    n = _norm(name)
    if not n:
        return None
    for c in pool:
        cands = [c.get("model", "")] + list(c.get("aliases") or [])
        for cn in cands:
            k = _norm(cn)
            if not k:
                continue
            if k == n or (len(k) >= 3 and (k in n or n in k)):
                return c
    return None


def audit(final_md: str, candidates: dict | list, gates: dict | None = None) -> dict:
    """对最终答案里的「车型榜表格」做机械审计。

    只解析表头含 车型/车系/型号/名次 的 markdown 表，避免把「分歧表」「冲突表」误判成榜单。
    对缺 cls / price_guide / trim 字段的旧候选，跳过闸门检查并计入 gate_skipped（向后兼容）。
    """
    pool = candidates.get("candidates", []) if isinstance(candidates, dict) else (candidates or [])
    out_of_pool, no_trim, promo, single_month, gate_fail = [], [], [], [], []
    gate_skipped = 0
    rows = []
    in_model_table = False
    header_txt = ""
    MODEL_HDR = ("车型", "车系", "型号", "名次")
    BAD_CHARS = "，。；：、（）()“”"

    for line in (final_md or "").splitlines():
        st = line.strip()
        if not st.startswith("|"):
            in_model_table = False
            header_txt = ""
            continue
        if set(st) <= set("|-: "):   # 分隔行（| : - 空格）
            continue
        cells = _split_row(st)
        if not in_model_table:
            # 表头判定：出现「车型/车系/型号/名次」才认为这是榜单表
            if any(h in st for h in MODEL_HDR):
                in_model_table = True
                header_txt = st
            continue
        if len(cells) < 3:
            continue
        raw_name = re.sub(r"[*`]", "", cells[1]).strip()
        # 去掉车型名后的括注（「零跑C01（跨级候补）」→「零跑C01」），但保留可能是论证句的长文本
        name = re.split(r"[（(]", raw_name)[0].strip() or raw_name
        if not name or name in ("车型", "车系", "型号", "—", "-"):
            continue
        if len(name) > 20 or any(ch in name for ch in BAD_CHARS):
            continue  # 明显不是车型名（论证句、说明文字）
        c = _match_pool(name, pool)
        rows.append(name)

        if not c:
            out_of_pool.append(name)
            continue

        row_txt = line
        if not any(k in row_txt for k in TRIM_KW):
            no_trim.append(name)
        if any(k in row_txt for k in PROMO_KW) and "指导价" not in row_txt:
            promo.append(name)
        # 出现「单月」或「N月」且没有任何趋势措辞 → 判定为单月定性
        # 注意：列头写「趋势月销（10月单月）」也算违规，不能因为出现了「趋势」二字就放行
        TREND_STRICT = ("月均", "稳态", "上市以来", "均值", "近3月", "近三月", "trimmed")
        ctx = row_txt + " " + header_txt   # 列头也参与判断（「趋势月销（10月单月）」等于自认单月）
        if ("单月" in ctx or MONTH_RE.search(ctx)) and not any(k in ctx for k in TREND_STRICT):
            single_month.append(name)

        has_gate_fields = any(c.get(k) for k in ("cls", "class", "price_guide", "trim"))
        if not has_gate_fields:
            gate_skipped += 1
            continue
        gr = apply_gates(c, gates)
        if not gr["pass"]:
            gate_fail.append({"model": name, "failed": gr["failed"], "notes": gr["notes"]})

    total = len(rows)
    bad = len(set(out_of_pool)) + len(no_trim) + len(promo) + len(single_month) + len(gate_fail)
    lines = ["# Ranking 审计（榜单口径合规性）", ""]
    lines.append(f"- 榜单表内识别到车型行：**{total}**")
    lines.append(f"- 池外车型（严禁引入）：**{len(set(out_of_pool))}**")
    lines.append(f"- 未写评分配置：**{len(no_trim)}**")
    lines.append(f"- 疑似用促销价入榜：**{len(promo)}**")
    lines.append(f"- 疑似用单月销量定性：**{len(single_month)}**")
    lines.append(f"- 候选本身不过闸门：**{len(gate_fail)}**")
    if gate_skipped:
        lines.append(f"- 缺闸门字段而跳过检查的候选：**{gate_skipped}**（旧格式候选池，建议补 cls/price_guide/trim）")
    if out_of_pool:
        lines += ["", "## 池外车型（模型幻觉 / 自行扩池）", ""]
        for n in dict.fromkeys(out_of_pool):
            lines.append(f"- **{n}**：不在候选池，违反「排序只能从候选池取」")
    if gate_fail:
        lines += ["", "## 不过闸门却被排进榜", ""]
        for g in gate_fail:
            lines.append(f"- **{g['model']}**：{'、'.join(g['failed'])}；{'；'.join(g['notes'])}")
    if no_trim:
        lines += ["", "## 未写评分配置（可能是拿入门版/车系给结论）", ""]
        lines += [f"- {n}" for n in no_trim]
    if promo:
        lines += ["", "## 促销价风险（需核对是否为官方指导价）", ""]
        lines += [f"- {n}" for n in promo]
    if single_month:
        lines += ["", "## 单月销量定性风险（新车/清库车需用趋势值）", ""]
        lines += [f"- {n}" for n in single_month]
    if bad == 0:
        lines += ["", "> 未发现结构性违规。仍需人工抽查数字与来源编号。"]
    lines += ["", "> 审计只查结构（越界/口径/配置/价格性质），不判定产品好坏；不替代 grounding 与人工复核。"]

    return {
        "rows": total,
        "out_of_pool": list(dict.fromkeys(out_of_pool)),
        "no_trim": no_trim,
        "promo_price": promo,
        "single_month": single_month,
        "gate_fail": gate_fail,
        "gate_skipped": gate_skipped,
        "violations": bad,
        "report": "\n".join(lines),
    }



# ---------------------------------------------------------------- 成对比较排名
# 借鉴 LMArena / arena-rank：用 Bradley-Terry MLE 聚合两两胜负，替代「拍权重再求和」。
# 好处：不需要先验权重；置信区间可算；对单个评分者的尺度差异免疫。
def pairwise_rank(matches, iters: int = 500, tol: float = 1e-10,
                  base: float = 1000.0, scale: float = 400.0,
                  prior: float = 1.0) -> dict:
    """matches: [{"a": "车A", "b": "车B", "winner": "a"|"b"|"tie"}, ...]

    返回 {"ratings": {name: elo分}, "rank": [(name, elo, wins, losses)], "n_matches": int}
    BT 的 MM 迭代：p_i = W_i / Σ_j n_ij/(p_i+p_j)。平局按各 0.5 胜处理。
    """
    names, W, N = [], {}, {}
    for m in matches or []:
        a, b, w = m.get("a"), m.get("b"), str(m.get("winner", "")).lower()
        if not a or not b or a == b:
            continue
        try:
            wgt = float(m.get("weight", 1.0) or 1.0)
        except (TypeError, ValueError):
            wgt = 1.0
        if wgt <= 0:
            continue
        for x in (a, b):
            if x not in W:
                names.append(x)
                W[x], N[x] = 0.0, {}
        N[a][b] = N[a].get(b, 0) + wgt
        N[b][a] = N[b].get(a, 0) + wgt
        if w in ("a", "1", "left"):
            W[a] += wgt
        elif w in ("b", "2", "right"):
            W[b] += wgt
        elif w in ("tie", "draw", "0.5", "平"):
            W[a] += 0.5 * wgt
            W[b] += 0.5 * wgt
        else:
            raise ValueError(f"winner 取值非法：{m.get('winner')}")
    if not names:
        return {"ratings": {}, "rank": [], "n_matches": 0}

    # 拉普拉斯式先验：给每个对手加一个虚拟「均势」对手，避免全胜/全负时 MLE 发散
    # （不加上这一步，样本少时 BT 分数会被推到 1800 vs 500 这种荒谬区间）
    if prior > 0:
        for n in names:
            W[n] += prior * 0.5
            N[n]["__prior__"] = N[n].get("__prior__", 0.0) + prior

    p = {n: 1.0 for n in names}
    p["__prior__"] = 1.0
    for _ in range(iters):
        delta = 0.0
        new = {}
        for i in names:
            denom = 0.0
            for j, nij in N[i].items():
                pj = 1.0 if j == "__prior__" else p[j]
                denom += nij / (p[i] + pj)
            new[i] = (W[i] / denom) if denom > 0 else 1e-12
        # 归一化到几何均值 1，避免尺度漂移
        gm = 1.0
        for v in new.values():
            gm *= max(v, 1e-12)
        gm = gm ** (1.0 / len(new))
        for k in new:
            new[k] = max(new[k] / gm, 1e-12)
            delta = max(delta, abs(new[k] - p[k]))
        p = new
        if delta < tol:
            break

    ratings = {k: base + scale * (0.0 if abs(v - 1) < 1e-12 else __import__("math").log10(v))
               for k, v in p.items()}
    rows = []
    for n in names:
        wins = W[n]
        games = sum(N[n].values())
        rows.append((n, round(ratings[n], 1), wins, games - wins))
    rows.sort(key=lambda x: -x[1])
    return {"ratings": ratings, "rank": rows, "n_matches": len(matches or [])}


def consensus_from_rankings(rankings) -> dict:
    """把多份「有序榜单」转成两两胜负，再跑 BT —— 比平均名次更稳。

    rankings: [["车A","车B","车C"], ["车B","车A","车C"], ...]
    """
    matches = []
    for r in rankings or []:
        seq = [str(x) for x in r]
        for i in range(len(seq)):
            for j in range(i + 1, len(seq)):
                matches.append({"a": seq[i], "b": seq[j], "winner": "a"})
    return pairwise_rank(matches)


# ---------------------------------------------------------------- 并列档
def assign_tiers(items, gap: float = 25.0, key=None) -> list[dict]:
    """把有序条目按分数相近度合并成「并列档」（单链接聚类）。

    items: 已按想要的顺序排好的 list；每项是 dict 或 (name, score)。
    key:   从 item 取分数的函数，默认取 item["score"] 或二元组第二项。
    gap:   相邻两项分差 <= gap 视为同档（沿链条传递：A-B、B-C 同档则 A-C 同档）。

    返回新 list（不改原序），每项附加 "tier"（从 1 起）与 "tier_rank"（竞赛名次 1,1,3 式）。
    目的：分数在噪声范围内的相邻名次不硬排，标并列——避免「第 4 名 vs 第 5 名」
    这种证据强度接近却被 listwise 裁判硬分高下的假精确。
    """
    def _score(it):
        if key:
            return float(key(it))
        if isinstance(it, dict):
            return float(it.get("score", it.get("total", 0)) or 0)
        return float(it[1])

    def _name(it):
        if isinstance(it, dict):
            return it.get("model") or it.get("name") or ""
        return str(it[0])

    out = [dict(it) if isinstance(it, dict) else {"model": _name(it), "score": _score(it)}
           for it in items]
    for i, it in enumerate(out):
        it.setdefault("score", _score(items[i]))
        it["tier"] = 1
    # 单链接：相邻分差 <= gap 合档（沿已排序序列扫描）
    for i in range(1, len(out)):
        if abs(out[i]["score"] - out[i - 1]["score"]) <= gap:
            out[i]["tier"] = out[i - 1]["tier"]
        else:
            out[i]["tier"] = out[i - 1]["tier"] + 1
    # 竞赛名次：同档取第一个的名次，其后跳号（1,1,3 而非 1,1,2）
    tier_first_rank, next_rank = {}, 1
    for i, it in enumerate(out):
        t = it["tier"]
        if t not in tier_first_rank:
            tier_first_rank[t] = next_rank
        it["tier_rank"] = tier_first_rank[t]
        next_rank = tier_first_rank[t] + sum(1 for x in out[:i + 1] if x["tier"] == t)
    return out


def render_tier_table(rows: list[dict], name_key: str = "model",
                      score_key: str = "score", extra_cols: tuple = ()) -> str:
    """把 assign_tiers 的结果渲染成带并列标记的 markdown 表。"""
    lines = ["| 名次 | " + name_key + " | " + score_key
             + "".join(" | " + c for c in extra_cols) + " |",
             "|" + "---|" * (3 + len(extra_cols))]
    tier_counts = {}
    for r in rows:
        tier_counts[r["tier"]] = tier_counts.get(r["tier"], 0) + 1
    for r in rows:
        mark = "=" if tier_counts[r["tier"]] > 1 else ""
        rank_cell = f"{r['tier_rank']}{mark}"
        cells = f"| {rank_cell} | {r.get(name_key, '')} | {r.get(score_key, '')}"
        for c in extra_cols:
            cells += f" | {r.get(c, '')} |"
        lines.append(cells + " |")
    n_ties = sum(1 for t, n in tier_counts.items() if n > 1)
    if n_ties:
        lines += ["", f"> 共 {n_ties} 个并列档（「=」标记），同档内名次无显著差异，不作强弱解读。"]
    return "\n".join(lines)

# ---------------------------------------------------------------- CLI
def _selftest():
    ok = True

    pool = {"candidates": [
        {"model": "深蓝L06", "cls": "中型车", "power": "BEV", "price_guide": [11.79, 16.49],
         "trim": {"name": "560 Ultra 激光版", "price": 12.99}, "cell_brand": "宁德时代",
         "cell_multi_source": False, "bms_owner": "self", "pack_tech": "CTV",
         "battery_warranty": {"soh_threshold": 70, "lifetime": True, "first_owner_only": True}},
        {"model": "深蓝S05", "cls": "紧凑型SUV", "power": "BEV", "price_guide": [11.99, 17.99]},
        {"model": "零跑C10", "cls": "中型SUV", "power": "BEV", "price_guide": [12.28, 14.28],
         "cell_brand": "国轩高科/亿纬动力/中创新航", "cell_multi_source": True},
    ]}

    g1 = apply_gates(pool["candidates"][0])
    g2 = apply_gates(pool["candidates"][1])
    g3 = apply_gates(pool["candidates"][2])
    for name, res, want in (("L06", g1, True), ("S05", g2, False), ("C10", g3, True)):
        got = res["pass"]
        print(f"  gate {name}: pass={got} (expect {want}) {res['failed']}")
        ok &= (got == want)

    t = trend_sales([
        {"month": "2026-08", "units": 1867, "flag": "clearance"},
        {"month": "2026-06", "units": 2363},
        {"month": "2026-07", "units": 2201},
        {"month": "2026-04", "units": 2718},
    ], window=3)
    print(f"  trend: {t['value']} dropped={len(t['dropped'])}")
    ok &= (t["value"] is not None and 2000 <= t["value"] <= 2600)

    b1 = battery_score(pool["candidates"][0])
    b2 = battery_score(pool["candidates"][2])
    print(f"  cell CATL={b1['score']} vs mixed={b2['score']}")
    ok &= (b1["score"] > b2["score"])

    md = """| # | 车型 | 价格 | 销量 |
|---|---|---|---|
| 1 | 深蓝L06 | 12.99 | 6,500 |
| 2 | 零跑C01 | 13.98 | 10月 8,000 |
| 3 | 零跑C10 | 限时 12.98 | 10,802 |
"""
    a = audit(md, pool)
    print(f"  audit: out_of_pool={a['out_of_pool']} no_trim={a['no_trim']} promo={a['promo_price']} single={a['single_month']}")
    ok &= ("零跑C01" in a["out_of_pool"])
    ok &= ("零跑C10" in a["promo_price"])

    # 成对比较：3 份榜单里 A 恒第一、B/C 互有胜负，共识应稳定为 A > (B≈C)
    cons = consensus_from_rankings([
        ["A", "B", "C"], ["A", "C", "B"], ["A", "B", "C"],
    ])
    order = [r[0] for r in cons["rank"]]
    print(f"  pairwise: {[(r[0], r[1]) for r in cons['rank']]}")
    ok &= order[0] == "A"

    # 加权 BT：换序复核确认的胜（权重 1）压过单方先验（权重 0.5）
    wres = pairwise_rank([
        {"a": "X", "b": "Y", "winner": "a", "weight": 1.0},   # 复核确认 X 胜
        {"a": "X", "b": "Y", "winner": "b", "weight": 0.5},   # 原榜序先验 Y 胜（弱）
    ])
    print(f"  weighted BT: {[(r[0], r[1]) for r in wres['rank']]}")
    ok &= wres["rank"][0][0] == "X"

    # 并列档：分差在 gap 内并档，竞赛名次 1,1,3
    tiers = assign_tiers(
        [{"model": "A", "score": 90}, {"model": "B", "score": 88.5},
         {"model": "C", "score": 82}, {"model": "D", "score": 81}],
        gap=2.0)
    print("  tiers:", [(t["model"], t["tier"], t["tier_rank"]) for t in tiers])
    ok &= tiers[0]["tier"] == tiers[1]["tier"] == 1
    ok &= tiers[0]["tier_rank"] == 1 and tiers[1]["tier_rank"] == 1
    ok &= tiers[2]["tier_rank"] == 3 and tiers[3]["tier"] == 2 + 0  # C 与 D 分差 1 <= 2 → 同档
    ok &= tiers[2]["tier"] == tiers[3]["tier"]
    ok &= tiers[2]["tier_rank"] == tiers[3]["tier_rank"] == 3

    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--audit", help="待审计的最终答案 markdown")
    ap.add_argument("--candidates", help="候选池 json")
    ap.add_argument("--gate", help="只跑闸门，打印通过/淘汰")
    ap.add_argument("--protocol", action="store_true", help="打印提示词协议")
    ap.add_argument("--pairwise", help="成对比较 json：[{a,b,winner}] 或 [[名次...],[名次...]]")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(_selftest())
    if a.protocol:
        print(build_protocol())
        return
    if a.gate:
        pool = json.load(open(a.gate, encoding="utf-8"))
        items = pool.get("candidates", pool)
        for c in items:
            r = apply_gates(c)
            print(("✓" if r["pass"] else "✗"), c.get("model"), r["failed"], "|", "；".join(r["notes"]))
        return
    if a.pairwise:
        data = json.load(open(a.pairwise, encoding="utf-8"))
        res = consensus_from_rankings(data) if data and isinstance(data[0], list) else pairwise_rank(data)
        for n, elo, w, l in res["rank"]:
            print(f"{elo:8.1f}  {n}  ({w:.1f}胜 / {l:.1f}负)")
        return
    if a.audit:
        md = open(a.audit, encoding="utf-8").read()
        pool = json.load(open(a.candidates, encoding="utf-8")) if a.candidates else {"candidates": []}
        res = audit(md, pool)
        print(res["report"])
        return
    ap.print_help()


if __name__ == "__main__":
    main()
