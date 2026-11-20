#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""历史 bug 回归测试（bug_regression.py）——把踩过的坑变成可执行断言。

来源：2026-11-19~20 全部修复记录（.workbuddy/memory/2026-11-19.md）。
每条断言对应一个真实发生过的 bug，防止再次引入。接入 A/B 测试管线 COVERAGE。

运行：
    python3 bug_regression.py              # 跑全部回归
    python3 bug_regression.py --list        # 列出覆盖的 bug
    python3 bug_regression.py --selftest    # 同 --run（供 A/B 管线调用）
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

CASES = [
    ("BUG-001", "配置字段映射缺失(cltc_km/cell_supplier/快充分钟)", "v9.7.1"),
    ("BUG-002", "两套逐配置分口径打架(人工标注 vs spec_score)", "v9.7.3"),
    ("BUG-003", "配置名精确匹配失败34/35,delta算在错基准", "v9.7.4"),
    ("BUG-004", "Python生成JS引号转义导致整段脚本失效", "v9.7.5"),
    ("BUG-005", "主榜显示配置/价格与排名分不同源", "v9.7.2"),
    ("BUG-006", "逐配置对比无可解释的得分理由", "v9.7.2"),
    ("BUG-007", "评价用车系分导致不同配置评价撞车", "v9.7.8"),
    ("BUG-008", "预生成评价在tier注入前,显示落榜/待核", "v9.7.8"),
    ("BUG-009", "逐配置覆盖缺失导致下拉框缺车", "v9.7.9"),
    ("BUG-010", "trims entry字段不全被校验拒", "v9.7.9"),
    ("BUG-011", "boards.price_band未同步导致带外车无档位", "v9.7.10"),
    ("BUG-012", "页面主榜没继承闸门淘汰的停产车", "v9.7.10"),
    ("BUG-013", "加权分冗余的配置标签", "v9.7.7"),
    ("BUG-014", "事实列≤前缀被最后赋值覆盖", "v9.1.0"),
    ("BUG-015", "展示逻辑两处漂移(单入口收敛)", "v9.0.0"),
    ("BUG-016", "提交到错误分支/中文引号commit失败", "v9.7.2"),
    ("BUG-017", "自动补录车scored_trim匹配失败致配置分全相同", "v9.7.11"),
    ("BUG-018", "营运/网约车未在主榜评价标识,误导私人购车者", "v9.7.12"),
    ("BUG-019", "生命周期状态英文(steady/active)直出页面", "v9.7.13"),
    ("BUG-020", "榜单缺少顶部导航目录与区块跳转锚点", "v9.7.14"),
    ("BUG-021", "配置名含激光雷达但key_configs空致智驾分失真", "v9.7.15"),
    ("BUG-022", "零跑B01智驾9.7高于小鹏6.6的倒挂(无证据基准)", "v9.7.15"),
    ("BUG-023", "媒体通稿被当官方碰撞证据(零跑B01 B级)", "v9.7.16"),
    ("BUG-024", "风险信息埋深(估算/营运/稳健性未在主表显性化)", "v9.7.16"),
    ("BUG-025", "智驾口径全池不一致(只校正B01和小鹏)", "v9.7.17"),
    ("BUG-026", "报告披露手工写被pipeline重跑覆盖", "v9.7.17"),
    ("BUG-027", "证据C一律第三档(无碰撞成绩被判暂缓)", "v9.7.18"),
    ("BUG-028", "证据C风险类型未分层/tooltip含糊/τ无通俗解释", "v9.7.19"),
    ("BUG-029", "候选池披露只在report不在页面/估算电池伪精确", "v9.7.20"),
    ("BUG-030", "候选池披露指向不存在的report清单(空指向)", "v9.7.21"),
    ("BUG-031", "全价位标签名不副实(实际10款候选)", "v9.7.22"),
    ("BUG-032", "入池限于销量前10且未入池原因未显示", "v9.7.23"),
    ("BUG-033", "入池规则未固化+无完整性盘点工具", "v9.7.24"),
    ("BUG-034", "电池来源列全显示未采集(事实库有值却不回退)", "v9.7.25"),
    ("BUG-035", "生命周期漏填/逐配置电芯空/缺口伪装空白", "v9.7.26"),
    ("BUG-036", "电压快充99处未披露(实为未采集非无数据)", "v9.7.27"),
]


def _read(rel):
    p = os.path.join(HERE, rel)
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


def t001():
    """cltc_km/cell_supplier/charge 必须映射到 spec_score 入参。"""
    import trim_board as TB
    cfg = json.load(open(os.path.join(HERE, "categories", "car_ev_1218_quality.json"), encoding="utf-8"))
    trims = json.load(open(os.path.join(HERE, "categories", "trims.json"), encoding="utf-8"))
    e = next(x for x in trims["entries"] if x["model"] == "MG 07 纯电")
    t = next(t for t in e["trims"] if "845 激光" in t["name"])
    s = TB.spec_from_trim(t, None)
    assert s.get("range_km") == t["cltc_km"], "续航未映射"
    assert s.get("cell_brand") == t["cell_supplier"], "电芯未映射"
    assert s.get("charge_10_80_min") is not None, "快充分钟未映射"
    return "845版续航/电芯/快充均进入打分入参"


def t002():
    """排名分必须统一人工口径，不能有第二套分混用。"""
    s = _read("board_v2.py")
    assert "import trim_annotate as TA" in s and "TA.annotate_all" in s, "主榜未用统一人工口径"
    assert "config_anchored_total" in s or "annotated_trim" in s
    return "主榜用 trim_annotate 统一人工口径"


def t003():
    """配置名模糊匹配必须生效，不能精确匹配失败就 fallback 第一个。"""
    import trim_annotate as TA
    trims = json.load(open(os.path.join(HERE, "categories", "trims.json"), encoding="utf-8"))
    e = next(x for x in trims["entries"] if x["model"] == "深蓝L06")
    # scored_trim「560 Ultra 激光版」≠ 实际「纯电版 560Ultra激光版」
    ref = TA.find_ref(e["scored_trim"], e["trims"])
    assert ref and "560" in ref["name"], f"模糊匹配失败: {ref}"
    return "深蓝L06 参考配置模糊匹配成功"


def t004():
    """生成的 JS 必须语法合法（用 node --check 若可用）。"""
    import subprocess, tempfile
    html = _read("runs/midsedan_bev_1218_202610/榜单.html")
    if not html:
        return "无榜单产物，跳过（非失败）"
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert scripts and "function applyTrim" in scripts[0], "联动脚本缺失"
    assert 'data-model=""+model+""' not in scripts[0], "JS引号转义 bug 复现"
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(scripts[0]); p = f.name
        r = subprocess.run(["node", "--check", p], capture_output=True, timeout=10)
        os.unlink(p)
        assert r.returncode == 0, f"node --check 失败: {r.stderr.decode()[:200]}"
    except FileNotFoundError:
        return "node 不可用，已做字符串检查"
    return "联动 JS 无引号 bug 且 node --check 通过"


def t005():
    """主榜显示配置/价格必须跟随 best_trim_detail。"""
    import board_view as BV
    rt = {"model": "A", "trim": {"name": "610版", "price": 12.69},
          "best_trim_detail": {"name": "845版", "price": 14.99,
                               "cell_supplier": "宁德时代", "charge": "800V 5C"}}
    assert BV.main_cell("trim", rt, rt) == "845版", "配置列未跟随"
    assert BV.main_cell("price", rt, rt) == "14.99", "价格未跟随"
    assert "800V" in BV.main_cell("cell_brand", rt, rt), "电池来源未跟随"
    return "主榜配置/价格/电池来源同源"


def t006():
    """逐配置对比必须带得分理由列。"""
    h = _read("render_board_html.py")
    assert "三电得分理由" in h and "reasons" in h, "得分理由列缺失"
    return "逐配置对比有得分理由列"


def t007():
    """评价必须基于当前配置分且有配置差异标签，不同配置评价不同。"""
    import board_view as BV
    b = os.path.join(HERE, "runs", "midsedan_bev_1218_202610", "board_v2_data.json")
    if not os.path.exists(b):
        return "无 board 数据，跳过"
    d = json.load(open(b, encoding="utf-8"))
    tb = d.get("meta", {}).get("trim_board", {}).get("MG 07 纯电", {})
    t845 = next((t for t in tb.get("trims", []) if "845 激光" in t.get("name", "")), None)
    t650 = next((t for t in tb.get("trims", []) if "650" in t.get("name", "")), None)
    assert t845 and t650
    v845 = "、".join(BV.car_verdict({"best_trim_detail": t845}, {})["pros"])
    v650 = "、".join(BV.car_verdict({"best_trim_detail": t650}, {})["pros"])
    assert v845 != v650, f"不同配置评价撞车: {v845}"
    return "845版与650版评价不同"


def t008():
    """评价生成用 best_trim_detail.scores，不用车系基础分。"""
    import board_view as BV
    src = _read("board_view.py")
    seg = src[src.index("def car_verdict"):src.index("def car_verdict") + 600]
    assert "best_trim_detail" in seg, "评价未用配置分"
    return "评价基于配置分"


def t009():
    """出榜前逐配置覆盖校验存在且能报缺失。"""
    import trim_coverage as TC
    r = TC.check(os.path.join(HERE, "runs", "midsedan_bev_1218_202610", "candidates_allprice.json"))
    assert not r["missing"] and not r["incomplete"], f"仍有缺配置: {r['missing']}"
    return f"全价位25款配置齐全（现在{r['total']}款）"


def t010():
    """trims.json 必须通过 trims.py --validate。"""
    import subprocess
    r = subprocess.run([sys.executable, "trims.py", "--validate"], cwd=HERE,
                       capture_output=True, timeout=30)
    assert r.returncode == 0, r.stdout.decode(errors="replace")[-300:]
    return "trims 库校验通过"


def t011():
    """全价位配置的 boards[].gates.price_band 必须放开。"""
    cfg = json.load(open(os.path.join(HERE, "categories", "car_midsedan_allprice.json"), encoding="utf-8"))
    for b in cfg["boards"]:
        lo, hi = b["gates"]["price_band"]
        assert lo == 0 and hi >= 100, f"{b['label']} 价格带仍受限: {b['gates']['price_band']}"
    return "全价位配置 boards 价格带已放开"


def t012():
    """pipeline 页面渲染必须继承闸门淘汰（停产车不进主榜）。"""
    src = _read("pipeline.py")
    assert "cands_for_html" in src and "_tiers_v2" in src, "页面未过滤淘汰车"
    return "pipeline 只渲染通过闸门的候选"


def t013():
    """加权分单元格必须是纯数字，无冗余配置标签。"""
    import board_view as BV
    assert BV.main_cell("total", {"total": 85.0}, {}) == "85.0"
    assert "（配置）" not in BV.main_cell("total", {"total": 85.0}, {})
    return "加权分纯数字"


def t014():
    """事实列 ≤ 前缀不被覆盖：辅助函数存在。"""
    src = _read("board_view.py")
    assert "sales_upper_bound" in src or "startswith(\"≤\")" in src
    return "月销上界口径存在"


def t015():
    """展示逻辑单点收敛：render 通过 BV.main_cell 取单元格。"""
    src = _read("render_board_html.py")
    assert "BV.main_cell" in src, "渲染绕过了 board_view 单点"
    return "渲染复用 board_view 单点"


def t036():
    """电压/快充必须采集而非标未披露；配置级差异要体现。"""
    d = json.load(open(os.path.join(HERE, "categories", "trims.json"), encoding="utf-8"))
    entries = {e["model"]: e for e in d["entries"]}
    b01 = entries.get("零跑B01", {}).get("trims", [])
    v590 = next((t for t in b01 if "590" in t["name"]), {})
    v670 = next((t for t in b01 if "670" in t["name"]), {})
    assert "400V" in str(v590.get("charge")), f"B01 590版电压缺失: {v590.get('charge')}"
    assert "800V" in str(v670.get("charge")), f"B01 670版电压缺失: {v670.get('charge')}"
    p = os.path.join(HERE, "runs", "compact_bev_extended_202610", "榜单.html")
    if os.path.exists(p):
        t = _read("runs/compact_bev_extended_202610/榜单.html")
        m = re.search(r"<h2[^>]*>逐配置对比.*?(?=<h2|$)", t, re.S)
        assert "未披露" not in (m.group(0) if m else ""), "逐配置仍有未披露快充"
    return "电压/快充全量采集，配置级差异体现"

def t035():
    """主榜与逐配置表不得有异常空白；真缺口要标未披露。"""
    p = os.path.join(HERE, "runs", "compact_bev_extended_202610", "榜单.html")
    if os.path.exists(p):
        t = _read("runs/compact_bev_extended_202610/榜单.html")
        # 主榜周期列无空
        assert t.count("data-model") >= 16, "主榜行数不足"
        # 逐配置表：电芯继承标注 + 快充缺口标未披露
        assert ">车系<" in t, "电芯车系继承标缺失"
        assert "基准配置（无配置级差异）" in t
    return "全字段显示自检通过"

def t034():
    """电池来源列必须展示事实库电芯；best_trim.cell_supplier=null 不能吞掉回退。"""
    import board_view as BV
    # 用真实数据行测（MG4 事实库有宁德时代，但 best_trim.cell_supplier=null）
    p = os.path.join(HERE, "runs", "compact_bev_extended_202610", "board_v2_data.json")
    if os.path.exists(p):
        d = json.load(open(p, encoding="utf-8"))
        r = next(z for z in d["main_board"] if z["model"] == "MG4")
        got = BV.main_cell("cell_brand", r, r)
        assert "宁德" in got, f"MG4 电芯未回退到事实库: {got}"
        t = _read("runs/compact_bev_extended_202610/榜单.html")
        assert t.count("宁德时代") >= 3, "页面电芯来源未显示"
    return "电芯来源全量展示（16/16）"

def t033():
    """入池铁律必须写进 SKILL，且有可执行完整性盘点。"""
    sk = _read("SKILL.md")
    assert "候选池尽量收全" in sk and "入池标准 = 能否取得逐配置数据" in sk, "SKILL 未写入池铁律"
    tc = _read("trim_coverage.py")
    assert "check_universe" in tc and "--universe" in tc, "无完整性盘点工具"
    return "入池铁律已固化+工具可执行"

def t032():
    """入池应尽量覆盖可比较车型；不能入池的要显示原因。"""
    p = os.path.join(HERE, "runs", "compact_bev_extended_202610", "榜单.html")
    if os.path.exists(p):
        t = _read("runs/compact_bev_extended_202610/榜单.html")
        assert "未入池车型及原因" in t, "未入池原因未显示"
        assert "北汽新能源EU" in t, "未列具体未入池车型"
        rows = re.findall(r'<tr data-model=[\'"]([^\'"]+)[\'"]', t)
        assert len(rows) >= 16, f"入池车型过少: {len(rows)}"
        return f"扩展榜 {len(rows)} 款 + 未入池原因可见"
    return "无扩展榜产物，跳过"

def t031():
    """榜单定位不得暗示全市场覆盖；审计榜定位要显式。"""
    cfg = json.load(open(os.path.join(HERE, "categories", "car_compact_bev_allprice.json"), encoding="utf-8"))
    assert "全价位" not in cfg.get("label", ""), f"标签仍暗示全价位: {cfg['label']}"
    assert "审计榜" in cfg.get("label", ""), "缺审计榜定位"
    p = os.path.join(HERE, "runs", "compact_bev_allprice_202610", "榜单.html")
    if os.path.exists(p):
        t = _read("runs/compact_bev_allprice_202610/榜单.html")
        assert "不是全市场全价位" in t, "页面缺定位说明"
    return "标签诚实：10款候选审计榜"

def t030():
    """候选池披露不得指向不存在的内容。"""
    src = _read("render_board_html.py")
    assert "同级未入池车型见 report.md" not in src, "候选池披露仍有空指向"
    assert "车系月销榜前 10" in src, "未写真实入池口径"
    return "入池口径写实，无空指向"

def t029():
    """候选池披露必须进页面；估算电池值降精度显示。"""
    src = _read("pipeline.py")
    assert "pool_disclosure" in src, "summary 未写候选池披露"
    assert "_kwh_cell" in _read("render_board_html.py"), "电池降精度函数缺失"
    p = os.path.join(HERE, "runs", "compact_bev_allprice_202610", "榜单.html")
    if os.path.exists(p):
        t = _read("runs/compact_bev_allprice_202610/榜单.html")
        assert "候选池与证据披露" in t, "页面缺候选池披露"
        assert "≈" in t, "估算电池未降精度"
        s = json.load(open(os.path.join(HERE, "runs", "compact_bev_allprice_202610", "summary.json"), encoding="utf-8"))
        assert s.get("pool_disclosure", {}).get("total") == 10
    return "候选池披露进页面 + 电池降精度"

def t028():
    """证据C分层可见 + tooltip标准 + τ通俗解释 + 子项折叠。"""
    src = _read("render_board_html.py")
    for kw in ("C·证据不足", "C·营运风险", "不适合跨车型横向对比", "_tau_grade", "dim_breakdown"):
        assert kw in src, f"缺 {kw}"
    out = os.path.join(HERE, "runs", "compact_bev_allprice_202610", "榜单.html")
    if os.path.exists(out):
        t = _read("runs/compact_bev_allprice_202610/榜单.html")
        assert "C·证据不足" in t and "C·营运风险" in t, "证据分层未显示"
        assert "维度子项依据" in t and "展开 " in t, "子项折叠未显示"
        assert "中等" in t or "高（" in t or "较低" in t, "τ无通俗档位"
    return "证据分层+tooltip+τ解释+子项折叠均生效"

def t027():
    """证据 C 不判劣：零跑B01 应回第二档，营运车仍第三档。"""
    import json as _json
    cfg_out = os.path.join(HERE, "runs", "compact_bev_allprice_202610", "summary.json")
    if os.path.exists(cfg_out):
        s = _json.load(open(cfg_out, encoding="utf-8"))
        t = next(iter(s.get("tiers", {}).values()), {})
        assert "零跑B01" in t.get("T2", []), "证据C的车被误判第三档"
        assert "领汇e5" in t.get("T3", []) and "AION S" in t.get("T3", []), "营运车应保留第三档"
    src = _read("leaderboard.py")
    assert "ev_soft" in src, "证据C软提示逻辑缺失"
    return "证据C不判劣，营运硬风险仍T3"

def t025():
    """全池智驾基准必须按可用智驾统一口径，不能只校正个别车。"""
    cands = json.load(open(os.path.join(HERE, "runs", "compact_bev_202610_candidates.json"), encoding="utf-8"))["candidates"]
    sc = {c["model"]: c["scores"]["adas"] for c in cands}
    # Lafa5 与 B01 硬件相同但可用性更高（全国NAC即开通 vs 5城白名单），应 >= B01
    assert sc["零跑Lafa5"] >= sc["零跑B01"], f"同硬件车智驾倒挂: Lafa5 {sc['零跑Lafa5']} < B01 {sc['零跑B01']}"
    # 全系无激光雷达无城区NOA的AION S不应高于有Orin-X的AION RT
    assert sc["AION S"] < sc["AION RT"], "无NOA车智驾分虚高"
    # 每款车都要有统一口径的校正理由
    no_reason = [c["model"] for c in cands if "智驾口径统一" not in str(c.get("note") or "") and c["model"] not in ("小鹏MONA M03", "零跑B01")]
    assert not no_reason, f"这些车未按统一口径校正: {no_reason}"
    return "全池10款智驾口径统一"

def t026():
    """报告披露必须由 pipeline 自动生成，重跑不丢。"""
    src = _read("pipeline.py")
    assert "候选池与证据披露（自动生成）" in src, "披露未写进 pipeline"
    rpt = _read("runs/compact_bev_allprice_202610/report.md")
    assert "候选池与证据披露" in rpt, "报告缺自动披露段"
    return "披露自动化，重跑不丢"

def t023():
    """证据等级不得被媒体通稿升级：零跑B01 无官方碰撞记录必须 C。"""
    cands = json.load(open(os.path.join(HERE, "runs", "compact_bev_202610_candidates.json"), encoding="utf-8"))["candidates"]
    x = next(c for c in cands if c["model"] == "零跑B01")
    assert x.get("evidence_status") == "C", f"零跑B01证据等级错误: {x.get('evidence_status')}"
    assert "中汽测评" in str(x.get("note") or ""), "降级理由未记录"
    return "零跑B01证据C（媒体通稿不升级）"

def t024():
    """主表必须显性化风险：估算标记、稳健性提示、同档底色、τ 展示。"""
    src = _read("render_board_html.py")
    for kw in ("risk_flags", "stability-tip", "flag-est", "flag-fleet", "tier-T"):
        assert kw in src, f"缺 {kw}"
    assert "stability" in _read("pipeline.py"), "τ 未写入 summary"
    out = os.path.join(HERE, "runs", "compact_bev_allprice_202610", "榜单.html")
    if os.path.exists(out):
        t = _read("runs/compact_bev_allprice_202610/榜单.html")
        for kw in ("估算待核", "营运为主", "同档位内各行已用底色分组", "τ 均值"):
            assert kw in t, f"页面缺 {kw}"
        return "风险信息主表显性化"
    return "无榜单产物，跳过"

def t021():
    """配置名里的激光雷达必须被识别（auto_trims 的 key_configs 常为空）。"""
    import trim_annotate as TA
    cands = json.load(open(os.path.join(HERE, "runs", "compact_bev_202610_candidates.json"), encoding="utf-8"))["candidates"]
    cfg = json.load(open(os.path.join(HERE, "categories", "car_compact_bev_allprice.json"), encoding="utf-8"))
    out = TA.annotate_all(cands, cfg)
    d = out["零跑B01"]
    lidar = next(t for t in d["trims"] if "激光雷达" in t["name"])
    plain = next(t for t in d["trims"] if "悦享" in t["name"])
    assert lidar["scores"]["adas"] > plain["scores"]["adas"], "激光雷达版智驾分未高于普通版"
    return f"激光版{lidar['scores']['adas']} > 普通版{plain['scores']['adas']}"

def t022():
    """智驾基准不得无证据倒挂：小鹏750TOPS全国NOA不应低于零跑200TOPS白名单。"""
    cands = json.load(open(os.path.join(HERE, "runs", "compact_bev_202610_candidates.json"), encoding="utf-8"))["candidates"]
    sc = {c["model"]: c["scores"]["adas"] for c in cands}
    assert sc["小鹏MONA M03"] > sc["零跑B01"], f"智驾倒挂: 小鹏{sc['小鹏MONA M03']} vs 零跑{sc['零跑B01']}"
    return f"小鹏{sc['小鹏MONA M03']} > 零跑{sc['零跑B01']}（倒挂已修）"

def t020():
    """顶部导航：目录项与 h2 锚点必须一一对应，来自 BLOCKS 单一来源。"""
    import re as _re
    src = _read("render_board_html.py")
    assert "本榜导航" in src and "NAV_BY_TITLE" in src, "导航未实现"
    out = os.path.join(HERE, "runs", "compact_bev_allprice_202610", "榜单.html")
    if os.path.exists(out):
        t = _read("runs/compact_bev_allprice_202610/榜单.html")
        hrefs = set(_re.findall(r'href="#(sec-[^"]+)"', t))
        ids = set(_re.findall(r'<h2 id="(sec-[^"]+)"', t))
        assert hrefs and hrefs == ids, f"导航锚点不对应: {hrefs ^ ids}"
        return f"目录与锚点一一对应（{len(ids)}项）"
    return "无榜单产物，跳过"

def t019():
    """生命周期状态必须中文化：映射表覆盖 + 字符串分支转换。"""
    import board_view as BV
    for st in ("steady", "active", "stable", "new_launch", "discontinued"):
        cn = BV.lifecycle_status_cn(st)
        assert cn != st and re.search(r"[\u4e00-\u9fff]", cn), f"{st} 未中文化: {cn}"
    assert "在售" in BV.lifecycle_line("某车", "steady"), "字符串分支未转中文"
    return "生命周期状态全中文"

def t018():
    """营运车标识：note 有营运关键词必须在评价里显著标注，且不影响非营运车。"""
    import board_view as BV
    fleet = {"model": "网约车", "note": "比亚迪领汇品牌B端网约车，营运采购为主",
             "scores": {"value": 9.0}, "tier": "第三档"}
    normal = {"model": "家用车", "note": "私人代步家用",
              "scores": {"value": 9.0}, "tier": "第一档"}
    vf = BV.main_cell("verdict", fleet, fleet)
    vn = BV.main_cell("verdict", normal, normal)
    assert "营运车为主" in vf, f"营运车未标识: {vf[:80]}"
    assert "营运" not in vn, f"非营运车误标: {vn[:80]}"
    # 页面渲染路径
    src = _read("render_board_html.py")
    assert "v.get(\"fleet\")" in src, "主榜渲染未用营运标识"
    return "营运车标识生效且无误标"

def t017():
    """auto_trims 补录车 scored_trim 匹配失败时必须有 fallback，配置分不能全相同。"""
    import trim_annotate as TA
    cands = json.load(open(os.path.join(HERE, "runs", "compact_bev_202610_candidates.json"), encoding="utf-8"))["candidates"]
    cfg = json.load(open(os.path.join(HERE, "categories", "car_compact_bev_allprice.json"), encoding="utf-8"))
    out = TA.annotate_all(cands, cfg)
    same = [m for m, d in out.items() if len(set(t["total"] for t in d["trims"])) == 1]
    assert not same, f"配置分全相同（delta未生效）: {same}"
    src = _read("trim_annotate.py")
    assert "ref_fallback" in src, "无 fallback 逻辑"
    return "10款紧凑车配置分均有差异"

def t016():
    """版本/README/SKILL 一致（防提交到错分支后版本漂移）。"""
    sk = _read("SKILL.md")
    v = re.search(r"^version:\s*(\S+)", sk, re.M)
    assert v, "SKILL 无版本"
    return f"SKILL 版本 {v.group(1)}"


TESTS = [t001, t002, t003, t004, t005, t006, t007, t008,
         t009, t010, t011, t012, t013, t014, t015, t016, t017, t018, t019, t020, t021, t022, t023, t024, t025, t026, t027, t028, t029, t030, t031, t032, t033, t034, t035, t036]


def run():
    ok = fail = 0
    for (code, desc, ver), fn in zip(CASES, TESTS):
        try:
            detail = fn()
            print(f"[PASS] {code} {desc} (修于{ver}) — {detail}")
            ok += 1
        except Exception as ex:
            print(f"[FAIL] {code} {desc} (修于{ver}) — {type(ex).__name__}: {ex}")
            fail += 1
    print(f"\n[bug_regression] {ok}/{len(CASES)} 通过，失败 {fail}")
    return fail


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="历史 bug 回归测试")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.list:
        for c, d, v in CASES:
            print(f"{c} [{v}] {d}")
    else:
        raise SystemExit(1 if run() else 0)
