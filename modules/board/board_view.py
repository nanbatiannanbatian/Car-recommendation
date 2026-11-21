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
import json
import os
import paths

# ---------------------------------------------------------------- 区块注册表
# id, 标题, 适用条件 key, 说明（供 selftest / 文档）
BLOCKS = [
    {"id": "lede", "title": "导语与规则", "when": "always",
     "desc": "主榜唯一权威、事实列不计分、产品力榜并列辅助"},
    {"id": "fact_coverage", "title": "事实列覆盖披露", "when": "has_fact_cols",
     "desc": "哪些事实字段有数据、哪些「—」未采集，不以估计值填充"},
    {"id": "main_board", "title": "打分主榜", "when": "always",
     "desc": "8维加权总分唯一排序；行内事实参考列不计分"},
    {"id": "trim_compare", "title": "逐配置对比（下拉）", "when": "has_trim_board",
     "desc": "每车展开看各配置单独算分；标注插补维度，只作同车配置相对对比"},
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
    {"id": "experience_adjust", "title": "体验证据修正", "when": "has_experience",
     "desc": "智驾/操控按可核验体验证据调整，列明来源与调整量；无证据车仅硬件口径"},
    {"id": "energy_facts", "title": "三电与补能", "when": "has_energy_facts",
     "desc": "逐车电量/电压/快充/充电功率/官方电耗/电芯/换电；事实列不计分"},
    {"id": "space_facts", "title": "空间与储物", "when": "has_space_facts",
     "desc": "轴距尺寸与后备箱容积；后备箱大不等于乘坐空间好"},
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
    {"id": "three_board", "title": "三榜并列", "when": "has_three_board",
     "desc": "销量序/加权序/BT共识序三视角对照，差值≥3加粗"},
    {"id": "robustness", "title": "稳健性", "when": "has_robustness",
     "desc": "Kendall τ/算法命中/权重敏感性——名次到底硬不硬"},
    {"id": "audit_boundary", "title": "审查边界", "when": "has_audit",
     "desc": "结论边界/提示级/说明级——必须披露的已知缺口"},
]

# v9.6.0：主榜单表多列（参考用户上传的优秀榜单，去掉气囊列）
# 每项 (表头, 取值函数)；取值函数 (row, by_model, grades) -> 字符串
MAIN_BOARD_COLUMNS = [
    "rank", "model", "trim", "price", "sales", "total", "tier",
    # 2026-11-21：主榜直接展示权威碰撞成绩（用户要求「有成绩的要看得见」）。
    # 展示的是 crash 字段解析出的体系+成绩；「在测」= 已抽车未出成绩；「—」= 无成绩。
    "crash",
    "evidence", "period", "kwh_per_wan", "cell_brand", "verdict",
]


BLOCK_IDS = [b["id"] for b in BLOCKS]

# 契约：历史页面里「必要且数据可支撑」的区块，任何版本不得悄悄少渲染。
REQUIRED_BLOCK_IDS = [
    "main_board", "score_breakdown", "tiers", "product_power",
    "evidence_notes", "lifecycle", "method_boundary", "reproduce",
    "three_board", "robustness", "audit_boundary",   # v9.6.0 三个分析区块
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
        "has_experience": bool((meta.get("experience_adjust") or {})),
        "has_energy_facts": bool(data.get("energy_facts")),
        "has_space_facts": bool(data.get("space_facts")),
        "has_trim_board": bool(meta.get("trim_board")),
        "has_three_board": bool((meta.get("three_board") or {}).get("rows")),
        "has_robustness": bool((meta.get("robustness") or {}).get("kendall_mean")),
        "has_audit": bool((meta.get("audit_boundary") or {}).get("verdict")),
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


# ---- 事实列三分法着色（v9.1.0，用户确认后实施）-------------------------
# 第一类：有明确优劣 + 口径一致 → 按本池分位着色（优绿/中灰/劣红 + 文字标记）
# 第二类：事实标签、无优劣序 → 不着色，只把「未采集/未核验」标灰
# 第三类：趋势月销 → ≤上界的车系合计值标灰斜体，不参与分位比较；指导价不着色
RANKED_FACT_FIELDS = {"电耗": "low", "kWh/万元": "high"}   # label -> 方向
PLAIN_FACT_FIELDS = {"电芯", "CLTC", "电池", "800V", "指导价"}

# v9.2.0 折中布局：主榜参考列只留 3 项（带色/事实标签），其余移到逐车取证表。
# 这样主榜一行 ~35 字符而不是 80+，两表都不挤。
MAIN_FACT_FIELDS = ["kWh/万元", "电耗", "电芯"]                    # 主榜参考列，按此顺序
DETAIL_FACT_FIELDS = ["指导价", "电池", "CLTC", "趋势月销", "800V"]  # 逐车取证表补全


def fact_grades(rows: list[dict]) -> dict[str, dict[str, str]]:
    """返回 {model: {字段label: '优|中|弱|未采集|上界|—'}}。纯数据，不改任何分数。

    - 可比较字段（电耗/kWh·万元）：按本池有值样本的分位数分档，
      前 25% 优、后 25% 弱、中间 中；样本 <4 款时不分档，全标「中」避免假精确。
    - 电量/kWh 本身不按优劣着色（大电量不等于好，车重和效率不同）。
    """
    out: dict[str, dict[str, str]] = {r["model"]: {} for r in rows}
    for label, direction in RANKED_FACT_FIELDS.items():
        vals = []
        for r in rows:
            v = (r.get("fact_ref") or {}).get(label)
            if isinstance(v, (int, float)):
                vals.append(float(v))
        if len(vals) < 4:
            for r in rows:
                v = (r.get("fact_ref") or {}).get(label)
                out[r["model"]][label] = "中" if isinstance(v, (int, float)) else "未采集"
            continue
        s = sorted(vals)
        n = len(s)
        # 分位阈值：25% / 75%
        q1 = s[int(0.25 * (n - 1))]
        q3 = s[int(0.75 * (n - 1))]
        for r in rows:
            v = (r.get("fact_ref") or {}).get(label)
            if not isinstance(v, (int, float)):
                out[r["model"]][label] = "未采集"
                continue
            v = float(v)
            if direction == "low":     # 电耗越低越好
                g = "优" if v <= q1 else ("弱" if v >= q3 else "中")
            else:                       # kWh/万元 越高越好
                g = "优" if v >= q3 else ("弱" if v <= q1 else "中")
            out[r["model"]][label] = g
    # 第二类 + 第三类：只标「未采集」，不做优劣
    for r in rows:
        fr = r.get("fact_ref") or {}
        for label in PLAIN_FACT_FIELDS | {"趋势月销"}:
            if label in out[r["model"]]:
                continue
            v = fr.get(label)
            if v in (None, "—", ""):
                out[r["model"]][label] = "未采集"
            elif label == "趋势月销" and isinstance(v, str) and v.strip().startswith(("<=", "≤")):
                out[r["model"]][label] = "上界"   # 车系合计，非纯电值
            else:
                out[r["model"]][label] = "—"      # 有值，不着色
    return out


EXPERIENCE_DIM_LABELS = {
    "adas": "智驾", "control": "操控", "cabin": "车机", "powertrain": "三电/能耗",
    "value": "性价比(保值率)",
}


LIFECYCLE_STATUS_CN = {
    "new_launch": "新车爬坡", "facelift_pending": "改款清库",
    "discontinued": "已停产", "clearance": "清库",
    "stable": "在售", "sales_collapse": "销量待确认",
    # v9.7.13：补页面实测漏网的两个状态（原来原文直出英文）
    "steady": "在售（稳定）", "active": "在售",
    "ramp_up": "爬坡期", "mature": "成熟期", "declining": "销量回落",
    "new": "新车上市", "pending": "待确认", "unknown": "状态未知",
    # v9.7.43：补 on_sale（特斯拉Model 3/比亚迪e7 用此值，此前原文直出英文）
    "on_sale": "在售",
}


def lifecycle_status_cn(status: str) -> str:
    return LIFECYCLE_STATUS_CN.get(str(status or ""), str(status) if status else "—")


def check_lifecycle_coverage(cands: list[dict]) -> dict:
    """v9.7.43：检查所有候选的生命周期状态是否都有中文映射。
    防止再出现 on_sale 这种漏映射、页面直出英文。返回未映射状态→车型列表。
    """
    missing = {}
    for c in cands:
        lc = c.get("lifecycle")
        s = (lc.get("status") if isinstance(lc, dict) else lc) or ""
        s = str(s).strip()
        if s and s not in LIFECYCLE_STATUS_CN:
            missing.setdefault(s, []).append(c.get("model"))
    return {"missing": missing, "passed": not missing}


def car_verdict(row: dict, cand: dict, meta: dict | None = None) -> dict:
    """机械生成单车的评价（优点/短板/适合人群/购买建议）——纯规则、无模型调用、可复现。

    只用已有结构化字段：scores / tier / evidence_status / lifecycle / note / 事实列。
    阈值固定写死，输入相同必得相同输出。不编造主观体验。
    """
    # v9.7.8：评价基于当前配置的分（best_trim_detail），不是车系基础分
    bd = row.get("best_trim_detail") or {}
    sc = bd.get("scores") or row.get("scores") or {}
    fr = dict(row.get("fact_ref") or {})
    # 配置级事实覆盖（电压/快充/电芯/电量），让不同配置评价不同
    if bd:
        if bd.get("price") and bd.get("battery_kwh"):
            fr["kWh/万元"] = round(bd["battery_kwh"] / bd["price"], 2)
        if bd.get("cltc_km"):
            fr["CLTC"] = bd["cltc_km"]
    tier = row.get("tier") or ""
    ev = row.get("evidence_status") or cand.get("evidence_status") or ""
    lc = (row.get("lifecycle") or {}).get("status", "")
    pros, cons = [], []
    # 优点：各维 ≥8.5 给优点标签
    labels = {"powertrain": "三电强", "safety": "安全高", "adas": "智驾好",
              "space": "空间大", "value": "性价比高", "control": "操控好",
              "cabin": "车机流畅", "quality": "品控稳"}
    for k, lab in labels.items():
        v = sc.get(k)
        if isinstance(v, (int, float)) and v >= 8.5:
            pros.append(lab)
        elif isinstance(v, (int, float)) and v < 7.0:
            cons.append(lab.replace("强", "弱").replace("高", "低").replace("好", "弱")
                        .replace("大", "小").replace("流畅", "一般").replace("稳", "弱"))
    if ev == "A":
        pros.append("有官方碰撞成绩")
    # 配置级差异标签（v9.7.8）：从 deltas/charge 提取，不同配置评价不同
    cfg_tags = []
    charge = str(bd.get("charge") or "")
    if "800V" in charge or "900V" in charge:
        cfg_tags.append("高压快充")
    if "5C" in charge or "4C" in charge:
        cfg_tags.append("超充")
    for d in (bd.get("deltas") or []):
        why = str(d.get("why") or "")
        if "激光雷达" in why and d.get("delta", 0) > 0:
            cfg_tags.append("激光雷达")
        if "CLTC" in why and d.get("delta", 0) > 0.4:
            cfg_tags.append("长续航")
        if "电量" in why and d.get("delta", 0) > 0.4:
            cfg_tags.append("大电池")
    # 配置标签优先（让不同配置的评价一眼可辨），维度标签补充，合计不超过4
    dim_pros = [x for x in pros if x not in cfg_tags]
    pros = (cfg_tags[:2] + dim_pros)[:4]
    kpw = fr.get("kWh/万元")
    if isinstance(kpw, (int, float)) and kpw >= 5:
        pros.append("电量/价格比高")
    # 短板：月销上界、缺关键数据
    if str(fr.get("趋势月销", "")).startswith("≤"):
        cons.append("月销仅为车系上界")
    if fr.get("CLTC") in (None, "—", ""):
        cons.append("CLTC未采集")
    if lc == "facelift_pending":
        cons.append("改款在即")
    if lc == "new_launch":
        cons.append("新车爬坡期")
    if not pros:
        pros.append("配置均衡")
    if not cons:
        cons.append("无明显结构化短板")
    # v9.7.12：营运/网约车识别——只读 note 明确关键词，不猜。
    # 有证据才标，让私人购车者一眼看出使用场景不同。
    note_text = str(row.get("note") or cand.get("note") or "")
    fleet_flag = any(k in note_text for k in ("营运", "网约", "B端", "出租", "对公"))
    if fleet_flag:
        cons.insert(0, "营运/网约车为主")
    # v9.7.43：销量口径异常（出口集中/促销透支/清库导致的单月低值，非产品力问题）
    sales_anomaly = any(k in note_text for k in ("销量口径异常", "出口集中", "季度初出口", "促销透支"))
    if sales_anomaly:
        cons.append("当月销量为出口/促销口径性低值，非产品力问题")
    # 适合人群：按最突出的维度机械映射
    top = max((k for k in labels if isinstance(sc.get(k), (int, float))),
              key=lambda k: sc[k], default="")
    audience = {"powertrain": "看重续航快充", "safety": "家庭安全优先", "adas": "常用智驾",
                "space": "大空间家用", "value": "预算敏感", "control": "爱驾驶",
                "cabin": "重车机体验", "quality": "求省心耐用"}.get(top, "综合家用")
    if fleet_flag:
        audience = "营运/网约采购（非私人代步首选）"
    # 建议：三档 + 证据 + 生命周期
    if not tier:
        advice = "落榜/待核（无档位）"
    elif tier == "第一档":
        advice = "建议购买（第一档）"
    elif tier == "第三档":
        advice = "暂缓（第三档，等证据）"
    elif ev == "C":
        advice = "谨慎（证据等级 C：新上市或样本不足）"
    else:
        advice = "按偏好选（第二档）"
    return {"pros": pros[:4], "cons": cons[:4], "audience": audience, "advice": advice, "fleet": fleet_flag}


MAIN_COL_LABELS = {
    "rank": "#", "model": "车型", "trim": "评分配置", "price": "指导价(万)",
    "sales": "趋势月销", "total": "加权分", "tier": "档位", "evidence": "证据",
    # 2026-11-21：原列名"周期"含义不清（用户问「周期-是什么意思」）——
    # 该列装的是生命周期状态（新车爬坡/在售/已停产…），不是"保养周期"。
    # 列名直接写成人话，值已经是中文状态词。
    "period": "生命周期状态",
    "crash": "碰撞成绩", "kwh_per_wan": "kWh/万元", "cell_brand": "电池来源",
    "verdict": "评价（优缺点/适合人群/建议）",
}


def main_cell(key: str, row: dict, cand: dict, grade: str = "") -> str:
    """主榜单表单元格取值（两版渲染器共用，避免各写一份漂移）。"""
    fr = row.get("fact_ref") or {}
    if key == "rank":
        return str(row.get("rank", ""))
    if key == "model":
        return str(row.get("model", ""))
    if key == "trim":
        # v9.7.2：主榜展示排名所用的最高配置（不是带内推荐配置）
        bd = row.get("best_trim_detail")
        if bd:
            return bd.get("name") or "—"
        t = row.get("trim") or cand.get("trim") or {}
        return (t.get("name") if isinstance(t, dict) else str(t)) or "—"
    if key == "price":
        bd = row.get("best_trim_detail")
        if bd and bd.get("price") is not None:
            return str(bd["price"])
        t = row.get("trim") or cand.get("trim") or {}
        p = (t.get("price") if isinstance(t, dict) else None) or cand.get("price")
        return f"{p}" if p not in (None, "") else "—"
    if key == "sales":
        v = fr.get("趋势月销")
        if v in (None, "—", ""):
            v = cand.get("trend_sales")
        return str(v) if v not in (None, "") else "—"
    if key == "total":
        # v9.7.7：去掉「（配置）」标签——主榜标题已写明按最高配置排序，单元格保持纯数字
        return f"{float(row['total']):.1f}" if row.get("total") is not None else "—"
    if key == "tier":
        return str(row.get("tier") or "")
    if key == "crash":
        from modules.engine import crash_systems as _CS
        return _CS.display_short(cand.get("crash"))
    if key == "evidence":
        return str(row.get("evidence_status") or cand.get("evidence_status") or row.get("evidence") or "—")
    if key == "period":
        lc = row.get("lifecycle") or cand.get("lifecycle") or {}
        return lifecycle_status_cn(lc.get("status")) if isinstance(lc, dict) else lifecycle_status_cn(lc)
    if key == "kwh_per_wan":
        v = fr.get("kWh/万元")
        mark = " ▲" if grade == "优" else (" ▼" if grade == "弱" else "")
        if v not in (None, "—", ""):
            return f"{v}{mark}"
        reason = fr.get("kWh/万元_缺失原因")
        return f"—（{reason}）" if reason else "—"
    if key == "verdict":
        v = car_verdict(row, cand)
        flag = '<b style="color:#b42318">⚠ 营运车为主</b>｜' if v.get("fleet") else ""
        return (flag + f"优：{'、'.join(v['pros'])}｜短：{'、'.join(v['cons'])}｜"
                f"适合：{v['audience']}｜{v['advice']}")
    if key == "cell_brand":
        # v9.7.25：电芯来源回退链——先最高配置，再事实库 fact_ref，再 battery_detail。
        # 原 bug：bd 分支里 cell_supplier=null（auto_trims 记 null）时直接返回"—"，
        # 永远走不到事实库回退，导致明明 fact_library 有电芯品牌却显示"未采集"。
        bp = row.get("battery_detail") or {}
        fr_cell = fr.get("电芯")
        bd = row.get("best_trim_detail")
        bd_cell = (bd or {}).get("cell_supplier")
        brand = None
        if bd_cell not in (None, "", "—"):
            brand = str(bd_cell)
        elif fr_cell not in (None, "—", ""):
            brand = str(fr_cell)
        elif bp.get("cell") not in (None, "—", ""):
            brand = str(bp.get("cell"))
        else:
            brand = "未披露"
        # v9.7.29：弗迪电芯标代际+闪充（事实库核验），二代≠都支持闪充，按事实标
        if "弗迪" in brand:
            gen = fr.get("电芯代际")
            flash = fr.get("电芯闪充")
            if gen:
                tag = str(gen)
                if flash and flash != "待核":
                    tag += f"/{flash}"
                brand = f"{brand}[{tag}]"
        # 电压：优先配置 charge，其次 battery_detail
        charge = str((bd or {}).get("charge") or "")
        import re
        mv = re.search(r"(900|800|400)\s*V", charge)
        volt = (mv.group(1) + "V") if mv else bp.get("voltage")
        if volt and volt != "未披露":
            brand = f"{brand} · {volt}"
        else:
            # 无电压时也要把补能时间显示出来——蔚来ES6 这类车参数页没填电压，
            # 但有「10–80% 约 36min」；此前这一段被整段丢掉，页面只剩一句
            # 「联网未查到电芯品牌」，读者以为什么都没采到。
            mc = re.search(r"(\d+)\s*[-–]\s*(\d+)%\s*(?:约|大约)?\s*(\d+)\s*min", charge)
            if mc:
                brand = f"{brand} · 快充{mc.group(1)}–{mc.group(2)}%约{mc.group(3)}min"
        # v10.9.0：查不到电芯时标注原因（用户确认沿用 kWh/万元 的「—（原因）」样式）。
        if brand == "未披露":
            reason = fr.get("电芯_缺失原因")
            return f"—（{reason}）" if reason else "—"
        return brand
    return "—"


def experience_dim_label(dim: str) -> str:
    return EXPERIENCE_DIM_LABELS.get(dim, dim)


def grade_color(g: str) -> str:
    """优绿 / 中灰 / 弱红 / 未采集与上界浅灰 —— 与股市红涨绿跌区分，这里绿=好。"""
    return {"优": "#1a7f37", "弱": "#b42318", "中": "#7b8590",
            "未采集": "#aab0b6", "上界": "#aab0b6"}.get(g, "")


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
        "级别按厂商/易车官方二级分类，存在车身尺寸偏小仍划入本池的案例（如 AION UT 车长 4270mm），"
        "与同级大车身车型比空间时需自行折扣；本榜不按轴距二次重分类。",
        "分数证据类型：全部维度为「官方参数 + 客观差异规则推算」，非实车实测；"
        "无第三方统一实测（冬测续航达成率/充电曲线）数据时如实标注，不伪装成实测。",
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
        here = str(paths.ROOT)
        path = str(paths.cat("fact_library.json"))
    if not os.path.exists(path):
        return {}
    try:
        facts = _json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        facts = {}
    # v10.10.0：电芯事实统一走 vehicle_facts 权威入口（唯一真相源在契约层，随包存活）。
    # 旧实现直接读 cell_facts.json 副本——那份是生成物，新装包没有就静默漏显示。
    # 现在按权威全集补齐 facts 里缺电芯的车；不覆盖 fact_library 已有的值。
    try:
        from modules.research import vehicle_facts as VF
        for e in VF.iter_authoritative_cells():
            m = e["model"]
            slot = facts.setdefault(m, {})
            if not slot.get("cell_brand") and e.get("cell_brand"):
                slot["cell_brand"] = e["cell_brand"]
            if not slot.get("kwh") and e.get("kwh"):
                slot["kwh"] = e["kwh"]
            if not slot.get("scored_trim") and e.get("trim"):
                slot["scored_trim"] = e["trim"]
    except (ImportError, OSError, ValueError):
        pass
    return facts


def lifecycle_line(model: str, info) -> str:
    """生命周期条目 → 一句可读中文（两版共用，避免露原始 JSON）。"""
    if isinstance(info, str):
        return f"{model}：{lifecycle_status_cn(info)}"  # v9.7.13：字符串状态也转中文
    if not isinstance(info, dict):
        return f"{model}：{info}"
    status = LIFECYCLE_STATUS_CN.get(str(info.get("status") or ""),
                                      str(info.get("status") or "状态未知"))
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
    # 体验修正区块：有审计数据时必须渲染
    data_exp = dict(data)
    data_exp["meta"] = dict(data["meta"])
    data_exp["meta"]["experience_adjust"] = {"A车": {"dimensions": {"adas": {"delta": -0.5}}}}
    act_exp = {b["id"] for b in active_blocks(data_exp, None)}
    if "experience_adjust" not in act_exp:
        bad.append("体验审计数据存在但区块未激活")
    # v9.0.1：事实库自动加载必须有数据（防「漏喂」回归）
    lib = load_fact_library()
    if len(lib) < 50 or not any(v.get("kwh") for v in lib.values()):
        bad.append(f"事实库自动加载异常（{len(lib)} 款）")
    # v9.1.0：三分法着色——优/弱分档正确，且着色不改变任何分数
    grows = [
        {"model": f"M{i}", "total": 90 - i, "fact_ref": {
            "电耗": v, "kWh/万元": (20 - v) / 2, "电芯": "宁德时代",
            "趋势月销": "≤3000（车系合计）" if i == 0 else 5000}}
        for i, v in enumerate([10.0, 11.0, 12.0, 13.0, 14.0])
    ]
    g = fact_grades(grows)
    if not (g["M0"]["电耗"] == "优" and g["M4"]["电耗"] == "弱"):
        bad.append(f"电耗分档错误（低=优应成立）: {g['M0']['电耗']}/{g['M4']['电耗']}")
    if not (g["M0"]["kWh/万元"] == "优" and g["M4"]["kWh/万元"] == "弱"):
        bad.append("kWh/万元分档错误（高=优应成立）")
    if g["M0"].get("趋势月销") != "上界":
        bad.append("月销上界未识别")
    if [r["total"] for r in grows] != [90, 89, 88, 87, 86]:
        bad.append("着色改了分数")
    # v9.2.0：折中布局字段拆分——主榜3项 + 取证表5项 = 全部8项，不重不漏
    all_facts = [c["label"] for c in
                 (json.load(open(str(paths.cat("rank_v2_policy.json")), encoding="utf-8"))
                  .get("fact_reference_columns") or [])]
    split = set(MAIN_FACT_FIELDS) | set(DETAIL_FACT_FIELDS)
    if len(split) != len(MAIN_FACT_FIELDS) + len(DETAIL_FACT_FIELDS):
        bad.append("主榜/取证表字段有重复")
    if all_facts and not set(all_facts) <= split:
        bad.append(f"事实字段拆分遗漏: {set(all_facts) - split}")
    # v9.6.0：主榜多列 + 三个分析区块契约
    if len(MAIN_BOARD_COLUMNS) < 10 or "trim" not in MAIN_BOARD_COLUMNS or "cell_brand" not in MAIN_BOARD_COLUMNS:
        bad.append("主榜列清单不完整")
    data3 = dict(data)
    data3["meta"] = dict(data["meta"])
    data3["meta"]["three_board"] = {"rows": [{"model": "A"}]}
    data3["meta"]["robustness"] = {"kendall_mean": 0.85}
    data3["meta"]["audit_boundary"] = {"verdict": "test"}
    act3 = {b["id"] for b in active_blocks(data3, None)}
    for rid in ("three_board", "robustness", "audit_boundary"):
        if rid not in act3:
            bad.append(f"分析区块 {rid} 未激活")
    # v9.6.1：电池来源含电压（按评分配置）、档位中文
    rtest = {"model": "A", "tier": "第一档", "fact_ref": {"电芯": "宁德时代"},
             "battery_detail": {"voltage": "400V", "cell": "宁德时代电芯"}}
    bsrc = main_cell("cell_brand", rtest, rtest)
    if "400V" not in bsrc or "宁德" not in bsrc:
        bad.append(f"电池来源未含电压: {bsrc}")
    if main_cell("tier", rtest, rtest) != "第一档":
        bad.append("档位未显示")
    # v9.7.2：评分配置/价格/电池来源跟随最高配置
    rt = {"model": "A", "trim": {"name": "610版", "price": 12.69},
          "best_trim_detail": {"name": "845版", "price": 14.99,
                               "cell_supplier": "宁德时代", "charge": "800V / 5C，30-80% 约12min"}}
    if main_cell("trim", rt, rt) != "845版" or main_cell("price", rt, rt) != "14.99":
        bad.append("主榜配置/价格未跟随最高配置")
    if "宁德" not in main_cell("cell_brand", rt, rt) or "800V" not in main_cell("cell_brand", rt, rt):
        bad.append("电池来源未跟随最高配置")
    # v9.7.7：加权分纯数字（无「（配置）」标签）
    if main_cell("total", {"total": 85.0}, {}) != "85.0":
        bad.append("加权分非纯数字")
    # v9.7.0：逐配置对比区块
    data4 = dict(data); data4["meta"] = dict(data["meta"])
    data4["meta"]["trim_board"] = {"A车": {"trims": [{"name": "x"}]}}
    if "trim_compare" not in {b["id"] for b in active_blocks(data4, None)}:
        bad.append("逐配置对比区块未激活")
    # v9.6.2：缺失原因 + 评价列机械生成
    rmiss = {"model": "B", "fact_ref": {"kWh/万元": "—", "kWh/万元_缺失原因": "缺配置价"}}
    if main_cell("kwh_per_wan", rmiss, rmiss) != "—（缺配置价）":
        bad.append("kWh万元缺失原因未显示")
    vtest = car_verdict({"model": "C", "scores": {"safety": 9.0, "quality": 5.7},
                         "tier": "第二档", "evidence_status": "A"}, {})
    if not (vtest["pros"] and vtest["cons"] and vtest["advice"]):
        bad.append("评价列生成不完整")
    if bad:
        print("[selftest FAIL]", bad)
        return 1
    print(f"[selftest OK] 区块注册表 {len(BLOCK_IDS)} 项，必要契约 {len(REQUIRED_BLOCK_IDS)} 项齐全")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
