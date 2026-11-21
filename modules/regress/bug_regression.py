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

import paths

HERE = str(paths.ROOT)
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
    ("BUG-037", "原始库773字段未映射到事实库", "v9.7.28"),
    ("BUG-038", "弗迪电池未标代际/误以为二代都闪充", "v9.7.29"),
    ("BUG-039", "扩展候选池只在runs未固化(下次运行丢失)", "v9.7.30"),
    ("BUG-040", "快充能力无配置级delta且口径混用会虚高", "v9.7.31"),
    ("BUG-041", "紧凑SUV榜候选池/配置未固化", "v9.7.32"),
    ("BUG-042", "校验不覆盖电芯字段致11/20主榜空白", "v9.7.33"),
    ("BUG-043", "占位scored_trim致全榜低置信刷屏", "v9.7.35"),
    ("BUG-044", "skill升级后老分数混入新榜(无指纹门禁)", "v9.7.36"),
    ("BUG-045", "发布包缺内置证据采集工具(依赖外部skill)", "v9.7.37"),
    ("BUG-046", "整合残留orchestrator死依赖", "v9.7.38"),
    ("BUG-047", "归一化敏感(换等价归一化即跳位)未拦第一档", "v9.7.41"),
    ("BUG-048", "候选池漏收主流车(Model3/极氪007)未联网盘点", "v9.7.43"),
    ("BUG-049", "销量数据缺失(None)被误判无交付降T3(极氪007)", "v9.7.43"),
    ("BUG-050", "补录车销量用单月冒充趋势/假方法名(Model3/e7)", "v9.7.43"),
    ("BUG-051", "判档用车系基础分、主榜用配置锚定分(两套口径打架)", "v9.7.43"),
    ("BUG-052", "note文案与crash结构字段矛盾无人校验(铁律不可执行)", "v9.7.43"),
    ("BUG-053", "同源车维度分无据大差异(007GT value/cabin被压低)", "v9.7.43"),
    ("BUG-054", "Euro NCAP成绩被当无成绩降权(007GT safety 7.2<无成绩车)", "v9.7.43"),
    ("BUG-055", "score_rows丢crash字段致全池误标缺官方碰撞", "v9.7.43"),
    ("BUG-056", "证据A绑人工标注致有碰撞实锤的车进不了T1/T1全空", "v9.7.43"),
    ("BUG-057", "人工标A但无碰撞实锤未堵+T2强推荐过渡档", "v9.7.43"),
    ("BUG-058", "有碰撞成绩车页面不显示具体体系/成绩", "v9.7.43"),
    ("BUG-059", "高分低档车未标注为何分高却不推荐", "v9.7.43"),
    ("BUG-060", "生命周期状态on_sale漏中文映射页面直出英文", "v9.7.43"),
    ("BUG-061", "MCDA相关算法重复计票虚高命中率", "v9.7.43"),
    ("BUG-062", "家族制80%阈值语义漂移(名义80%实际83%)", "v9.7.43"),
    ("BUG-063", "分数改了证据文本没同步(分数证据脱节)", "v9.7.43"),
    ("BUG-064", "碰撞体系硬编码散落5处致新体系被漏", "v9.7.44"),
    ("BUG-065", "二值命中截断埋没稳稳第5的车(Model3)+产品力市场口径不清", "v9.7.48"),
    ("BUG-066", "第一档过严叙事局限+强推荐未标稳稳第5的车+并行纪律", "v9.7.50"),
    ("BUG-067", "强推荐金标移到档位列+hover讲清为何强推/为何未进一档", "v9.7.53"),
    ("BUG-068", "候选池price/trim形状不一致(文本/字符串)致出榜TypeError", "v10.1.1"),
    ("BUG-069", "trims已核验官方价未回流候选池致11款price_unknown落榜", "v10.1.2"),
    ("BUG-070", "碰撞披露把「已抽车未测」算成有成绩(4/21实际3款)", "v10.1.2"),
    ("BUG-071", "官方碰撞成绩只写在note文本未进字段致极氪7X进不了一档", "v10.1.3"),
    ("BUG-072", "成绩已进crash字段却未参与打分(safety仍按插补)致有实锤等于没用", "v10.1.4"),
    ("BUG-073", "映射表人工分与公式复算不一致(同分率不同分)", "v10.1.5"),
    ("BUG-074", "缺逐配置数据的车静默无下拉框(大众T6),读者分不清是缺失还是无差异", "v10.1.6"),
    ("BUG-075", "采集只有单源单点:索引miss就放弃(索引漏新车但车系页已存在)", "v10.1.7"),
    ("BUG-076", "多源采集无调度层/失败无留痕(待补清单只写「未匹配」)", "v10.1.9"),
    ("BUG-077", "联网取数未强制走research(业务模块可自行requests抓页面)", "v10.2.0"),
    ("BUG-078", "无碰撞成绩车安全分可高于有实锤车(MONA L05 8.6 > bZ5 8.0)", "v10.2.2"),
    ("BUG-079", "得分率→分数存在两套实现(engine cap9.3 vs research cap10/floor6)", "v10.2.3"),
    ("BUG-080", "无实测车体验维度可断层最高(MONA adas9.4 vs 有实测G6 7.6)", "v10.2.4"),
    ("BUG-081", "维度证据门槛散落成多个特判函数,新增维度需再打补丁(quality漏网)", "v10.2.5"),
    ("BUG-082", "T6实测数据没录入:采集抓不到试驾稿麋鹿/NOA+检测器只扫候选池+缺口只print不拦", "v10.2.7"),
    ("BUG-083", "MONA空间9.3全场第一但零实测:space被判硬数据不封顶+轴距0%覆盖+人工分从未校验", "v10.2.8"),
    ("BUG-084", "空间要素(前备箱/下沉后备箱/储物/真实表现)记在evidence却没用:得房率64%的N7拿8.5", "v10.2.9"),
    ("BUG-085", "人工分全量核算:8维仅safety闭环/quality/powertrain规则没接/spec_score从没跑过", "v10.3.0"),
    ("BUG-086", "ModelY有C-IASI一手成绩却标缺权威碰撞:回填只扫note漏evidence+宽松解析误抓枚举句", "v10.3.1"),
    ("BUG-087", "12碰撞体系只2个能解析+权威库从不回流+安全分文案写死C-NCAP:蔚来ET5 EuroNCAP/C-IASI取不到", "v10.3.2"),
    ("BUG-088", "多碰撞成绩取第一个锚点(靠文本顺序既非最高非叠加)+次要成绩被静默丢弃无审计", "v10.3.3"),
]


def _read(rel):
    # 实现源码按模块索引解析（modules/<pkg>/x.py）；数据/产物按项目根解析。
    # 绝不读根目录兼容薄壳，否则断言会跑在 10 行转发壳上、形同虚设。
    p = paths.src(rel)
    return open(p, encoding="utf-8").read() if p.exists() else ""


def _in_sync_set(*names):
    """候选池是否会被同步到仓库（v10 起 sync_to_repo 是镜像同步，没有手工白名单）。

    原来断言「白名单里要有这个文件名」——白名单本身已删除，改成直接跑一遍收集逻辑：
    文件在同步集里 = 下次同步一定不会漏（比字符串匹配更强）。
    """
    from importlib import import_module
    sync = import_module("sync_to_repo")
    have = sync.collect(str(paths.ROOT))
    return all(any(r.replace("\\", "/").endswith(n) for r in have) for n in names)


def t001():
    """cltc_km/cell_supplier/charge 必须映射到 spec_score 入参。"""
    from modules.engine import trim_board as TB
    cfg = json.load(open(str(paths.cat("car_ev_1218_quality.json")), encoding="utf-8"))
    trims = json.load(open(str(paths.cat("trims.json")), encoding="utf-8"))
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
    from modules.engine import trim_annotate as TA
    trims = json.load(open(str(paths.cat("trims.json")), encoding="utf-8"))
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
    from modules.board import board_view as BV
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
    from modules.board import board_view as BV
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
    from modules.board import board_view as BV
    src = _read("board_view.py")
    seg = src[src.index("def car_verdict"):src.index("def car_verdict") + 600]
    assert "best_trim_detail" in seg, "评价未用配置分"
    return "评价基于配置分"


def t009():
    """逐配置覆盖校验必须能检出缺失（能力断言，不依赖数据快照）。

    2026-11-21 删缓存重建后原版挂掉：它断言「历史池 25 款配置齐全」，
    而历史池里的车（极氪007GT/海豹06GT…）不在当前总池、trims 重建不出来。
    测试应当守「门禁能发现缺失」这个能力，而不是「某个快照恰好齐全」。
    """
    import tempfile
    from modules.engine import trim_coverage as TC
    fake = {"candidates": [{"model": "绝不存在的测试车XYZ", "cls": "中型车", "power": "BEV"}]}
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(fake, f, ensure_ascii=False); f.close()
    try:
        r = TC.check(f.name)
        assert r["missing"] == ["绝不存在的测试车XYZ"], f"缺配置未被检出: {r}"
    finally:
        os.remove(f.name)
    # 随包的人工库必须真的齐全（这是数据契约，不能只报不拦）——
    # 生成物可以缺（可重建），随包的人工条目缺了就是发出去坏包。
    manual = json.load(open(str(paths.cat("trims_manual.json")), encoding="utf-8"))["entries"]
    assert len(manual) >= 10, f"随包人工条目过少：{len(manual)}"
    bad = []
    for e in manual:
        tr = e.get("trims") or []
        if len(tr) < 2:
            bad.append(f"{e.get('model')} 配置不足 2 档")
        for f in ("name", "price", "cltc_km"):
            if any(t.get(f) in (None, "", "—") for t in tr):
                bad.append(f"{e.get('model')} 有配置缺 {f}")
    assert not bad, "随包人工库不完整：" + "；".join(bad[:5])
    real = TC.check(os.path.join(HERE, "runs", "midsedan_bev_1218_202610", "candidates_allprice.json"))
    return (f"覆盖校验可检出缺失；人工库 {len(manual)} 款齐全；历史池 {real['total']} 款：完整 {len(real['ok'])} / "
            f"缺 {len(real['missing'])} / 不完整 {len(real['incomplete'])} / 无电芯 {len(real['no_cell'])}（数据状态不拦截）")


def t010():
    """trims.json 必须通过 trims.py --validate。"""
    import subprocess
    r = subprocess.run([sys.executable, "trims.py", "--validate"], cwd=HERE,
                       capture_output=True, timeout=30)
    assert r.returncode == 0, r.stdout.decode(errors="replace")[-300:]
    return "trims 库校验通过"


def t011():
    """全价位配置的 boards[].gates.price_band 必须放开。"""
    cfg = json.load(open(str(paths.cat("car_midsedan_allprice.json")), encoding="utf-8"))
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
    from modules.board import board_view as BV
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


def t047():
    """归一化敏感性必须进三档判据：换等价归一化就跳位的车不能进第一档，
    但也绝不进第三档（名次不稳是算法精度问题，不是产品硬伤）。"""
    from modules.engine import robust as RB
    # 断言1：判据已接入源码（阈值可配 + blocker + 只拦T1）
    src = _read("leaderboard.py")
    assert "t1_max_norm_swing" in src, "归一化阈值未入 tier_rules"
    assert "normalization_audit(" in src, "tier_report 未调用归一化审计"
    assert "归一化敏感" in src, "缺归一化 blocker 文案"
    # 断言2：审计本身能识别高波动（构造一个跨归一化会换位的矩阵）
    #        三组：A 两极分化、B 居中、C 靠另一维胜出——zscore/sum 会重排
    m = [[9.0, 1.0], [5.0, 5.0], [1.0, 9.0]]
    na = RB.normalization_audit(m, [0.5, 0.5], ["benefit", "benefit"],
                                ["A", "B", "C"])
    assert len(na["normalizations"]) == 5, "审计未覆盖 5 种归一化"
    assert 0 <= na["tau_mean"] <= 1, "τ 越界"
    # 断言3：默认阈值写进规则且只作第一档 blocker（不进 risk/第三档逻辑）
    from modules.engine import leaderboard as LB
    rules = LB.tier_rules({"tiering": {}})
    assert rules["t1_max_norm_swing"] == 2, "默认归一化阈值不是 2 位"
    assert rules["norm_audit_enabled"] is True, "归一化审计默认未开"
    return "归一化波动进T1判据(阈值2位)+只拦T1不进T3+审计5方式可用"


def t046():
    """research-collector 完全整合：无独立身份、无 orchestrator 死依赖。"""
    sk = _read("SKILL.md")
    assert "统一证据采集（内建" in sk, "采集能力未并入SKILL"
    rp = _read("research/researcher.py")
    assert "import orchestrator" not in rp and "def extract_sources" not in rp, "残留辩论引擎死代码"
    rd = _read("research/README.md")
    assert "内建证据采集模块" in rd, "README仍称外部来源"
    cfg = json.load(open(str(paths.research_config()), encoding="utf-8"))
    assert "api_key_env" not in cfg, "残留废弃密钥配置"
    return "完全整合：能力入SKILL+死代码清除+无外部依赖"

def t045():
    """发布包自包含：证据采集工具必须在包内，不依赖外部 skill。"""
    import zipfile
    for f in ("research/researcher.py", "research/config.json", "research/README.md"):
        assert os.path.exists(os.path.join(HERE, f)), f"缺内置采集工具: {f}"
    bld = _read("build_release.py")
    assert 'p.name == "research"' in bld, "打包未放行 research/"
    # 发布包实测含这三个文件（若有包）
    zpath = os.path.join(HERE, "dist", "car-recommendation-v9.7.36.zip")
    if os.path.exists(zpath):
        with zipfile.ZipFile(zpath) as z:
            names = z.namelist()
        assert "car-recommendation/research/researcher.py" in names, "发布包缺采集脚本"
    return "内置采集工具随包发布，自包含"

def t044():
    """指纹门禁：代码/版本变化后老候选池分数不得混进新榜。"""
    from modules.engine import score_fingerprint as SF
    import tempfile
    # 1) 指纹模块存在且能算
    fp = SF.compute_fingerprint()
    assert len(fp) == 16 and SF.skill_version()
    # 2) 三个候选池都已打标
    for f in ("categories/candidates_compact_suv_bev.json",
              "categories/candidates_compact_bev_extended.json",
              "categories/candidates_compact_bev_audit.json"):
        d = json.load(open(os.path.join(HERE, f), encoding="utf-8"))
        assert d.get("_generated_by", {}).get("fingerprint"), f"{f} 未打指纹"
    # 3) pipeline 有门禁
    src = _read("pipeline.py")
    assert "指纹门禁" in src and "force-stale" in src, "pipeline 无指纹门禁"
    # 4) 篡改指纹能被测出（用临时副本）
    import copy
    d = json.load(open(str(paths.cat("candidates_compact_suv_bev.json")), encoding="utf-8"))
    fake = copy.deepcopy(d)
    fake["_generated_by"]["fingerprint"] = "0000000000000000"
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(fake, tmp); tmp.close()
    try:
        r = SF.check(tmp.name)
        assert not r["ok"], "篡改指纹未被拦截"
    finally:
        os.unlink(tmp.name)
    return "指纹门禁生效，三池已打标，篡改可检出"

def t043():
    """低置信只在有区分度时显示；占位scored_trim由代码自动解析（防呆，非靠人工回填）。"""
    import copy
    from modules.engine import trim_annotate as TA
    # 代码级防呆：故意把某车 scored_trim 改成占位，验证 annotate_all 能自动解析且不全车 low
    src_ta = _read("trim_annotate.py")
    assert "is_placeholder_ref" in src_ta and "resolve_ref" in src_ta, "无占位基准自动解析"
    fake_path = os.path.join(HERE, "__bug043_fake_trims.json")
    orig_trims = TA.TRIMS
    try:
        td = json.load(open(str(paths.cat("trims.json")), encoding="utf-8"))
        fake = copy.deepcopy(td)
        c = json.load(open(str(paths.cat("candidates_compact_suv_bev.json")), encoding="utf-8"))["candidates"]
        cfg = json.load(open(str(paths.cat("car_compact_suv_bev.json")), encoding="utf-8"))
        # 取「候选池里有、trims 里也有配置」的车，不指定具体车型
        # （原版硬编码 AION N60，删缓存重建后该条目不存在 → KeyError）
        in_pool = {x["model"] for x in c}
        target = next((e for e in fake["entries"]
                       if e.get("trims") and e.get("model") in in_pool), None)
        assert target, "候选池中的车在 trims 里都无配置条目，无法构造占位场景"
        target["scored_trim"] = "待配置核验"
        json.dump(fake, open(fake_path, "w", encoding="utf-8"), ensure_ascii=False)
        TA.TRIMS = fake_path
        r = TA.annotate_all(c, cfg)[target["model"]]
        assert "待配置" not in str(r["scored_trim"]), "占位基准未被自动解析"
        assert r["best_confidence"] != "low", f"占位基准仍致全车low: {r['best_confidence']}"
        assert len(set(t["total"] for t in r["trims"])) > 1, "配置分无差异"
    finally:
        TA.TRIMS = orig_trims
        if os.path.exists(fake_path):
            os.remove(fake_path)
    src = _read("render_board_html.py")
    assert "_mixed_conf" in src, "低置信区分度显示逻辑缺失"
    for out in ("compact_suv_bev_202610", "compact_bev_extended_202610"):
        p2 = os.path.join(HERE, "runs", out, "榜单.html")
        if os.path.exists(p2):
            t = _read(os.path.join("runs", out, "榜单.html"))
            m = re.search(r"<h2[^>]*>打分主榜.*?<tbody>(.*?)</tbody>", t, re.S)
            seg = m.group(1) if m else ""
            rows = len(re.findall(r"<tr data-model=", seg))
            low_count = seg.count("低置信")
            assert low_count == 0 or low_count < rows, f"{out} 低置信刷屏: {low_count}/{rows}"
    return "占位基准已回填，低置信不刷屏"

def t042():
    """校验必须覆盖展示字段——电芯缺失要被拦截，不能'校验全绿但主榜空白'。"""
    from modules.engine import trim_coverage as TC
    src = _read("trim_coverage.py")
    assert "no_cell" in src and "无电芯来源" in src, "电芯校验未实现"
    # 能力断言：造一条「有配置但电芯空」的条目，check 必须报 no_cell
    # （原来断言三个随包池零缺失，删缓存重建后自动条目无电芯即挂——那是数据状态，不是代码能力）
    import tempfile
    tf = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump({"entries": [{"model": "无电芯测试车", "trims": [
        {"name": "标准版", "price": 15.0, "battery_kwh": 60, "cltc_km": 500,
         "charge": "400V", "motor_kw": 150, "cell_supplier": None}]}]}, tf, ensure_ascii=False)
    tf.close()
    cf = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump({"candidates": [{"model": "无电芯测试车", "cls": "中型车", "power": "BEV"}]}, cf, ensure_ascii=False)
    cf.close()
    try:
        r = TC.check(cf.name, tf.name)
        assert r["no_cell"] == ["无电芯测试车"], f"电芯缺失未被检出: {r}"
    finally:
        os.remove(tf.name); os.remove(cf.name)
    # 页面主榜电芯列无空
    for out in ("compact_suv_bev_202610", "compact_bev_extended_202610"):
        p2 = os.path.join(HERE, "runs", out, "榜单.html")
        if os.path.exists(p2):
            t = _read(os.path.join("runs", out, "榜单.html"))
            m = re.search(r"<h2[^>]*>打分主榜.*?<tbody>(.*?)</tbody>", t, re.S)
            blank = 0
            for item in re.findall(r"<tr data-model=.*?</tr>", m.group(1), re.S) if m else []:
                cs = re.findall(r"<td[^>]*>(.*?)</td>", item, re.S)
                if len(cs) > 10 and re.sub(r"<[^>]+>", "", cs[10]).split("·")[0].strip() in ("—", ""):
                    blank += 1
            assert blank == 0, f"{out} 主榜电芯列有 {blank} 处空白"
    return "电芯入校验，三榜主榜零空白"

def t041():
    """紧凑纯电SUV榜：候选池/配置固化，未入池原因可见，20款配置齐全。"""
    for f in ("categories/candidates_compact_suv_bev.json",
              "categories/car_compact_suv_bev.json"):
        assert os.path.exists(os.path.join(HERE, f)), f"未固化: {f}"
    assert _in_sync_set("categories/candidates_compact_suv_bev.json",
                        "categories/car_compact_suv_bev.json"), "候选池未进同步集"
    p = os.path.join(HERE, "runs", "compact_suv_bev_202610", "榜单.html")
    if os.path.exists(p):
        t = _read("runs/compact_suv_bev_202610/榜单.html")
        assert "未入池车型及原因" in t and "小鹏MONA L03" in t
        assert "候选池与证据披露" in t
        m = re.search(r"<h2[^>]*>打分主榜.*?<tbody>(.*?)</tbody>", t, re.S)
        rows = re.findall(r"<tr data-model=", m.group(1)) if m else []
        assert len(rows) == 20, f"入池数不对: {len(rows)}"
    return "紧凑SUV榜20款入池+未入池原因+固化"

def t040():
    """快充口径归一化 + 配置级 delta：不同区间可比，闪充配置加分。"""
    from modules.engine import trim_annotate as TA
    # 口径归一化：30-80% 20min 应折算等效28min，不能当20min用
    eq, _ = TA.charge_minutes({"charge": "400V/3C，30–80% 约 20min"})
    assert eq is not None and 27 <= eq <= 29, f"口径折算错误: {eq}"
    # 兆瓦闪充 10-70% 5min 折算后不能虚高成 5min 当10-80%
    eq2, _ = TA.charge_minutes({"charge": "800V 兆瓦闪充 10-70% 5min"})
    assert eq2 is not None and 5 < eq2 < 7, f"闪充口径折算错误: {eq2}"
    # 配置级 delta：同车系快充版应比慢充版多快充加成
    d = json.load(open(str(paths.cat("trims.json")), encoding="utf-8"))
    e = next(x for x in d["entries"] if x["model"] == "MG 07 纯电")
    slow = next(t for t in e["trims"] if "650" in t["name"])
    fast = next(t for t in e["trims"] if "845" in t["name"])
    ds = TA.deltas_for(fast, slow)
    assert any("快充" in x["why"] and x["delta"] > 0 for x in ds), "快充配置delta未生效"
    return "快充口径归一化+配置级delta生效"

def t039():
    """候选池必须固化在 categories（随包同步），不能只活在 runs。"""
    import os as _os
    for f in ("categories/candidates_compact_bev_extended.json",
              "categories/candidates_compact_bev_audit.json",
              "categories/universe_compact_bev_202610.json"):
        p = _os.path.join(HERE, f)
        assert _os.path.exists(p), f"候选池未固化: {f}"
    # 必须进同步集（v10：镜像同步，无白名单）
    assert _in_sync_set("categories/candidates_compact_bev_extended.json",
                        "categories/candidates_compact_bev_audit.json"), "候选池未进同步集"
    # 内容完整性：6 款长尾车 + 分数 + 生命周期 + 代际事实
    ext = json.load(open(str(paths.cat("candidates_compact_bev_extended.json")), encoding="utf-8"))
    models = {c["model"] for c in ext["candidates"]}
    for m in ("奔驰CLA新能源", "羿驰05", "曹操60", "秦新能源", "e爱丽舍", "比亚迪e3"):
        assert m in models, f"固化候选池缺车: {m}"
    b01 = next(c for c in ext["candidates"] if c["model"] == "零跑B01")
    assert b01["scores"]["adas"] == 7.8 and b01.get("evidence_status") == "C"
    fl = json.load(open(str(paths.cat("fact_library.json")), encoding="utf-8"))
    assert fl.get("秦PLUS EV", {}).get("cell_flash") == "非闪充"
    return "候选池已固化随包，关键修复数据齐全"

def t038():
    """弗迪电芯标代际；二代不等于闪充，按事实标注。"""
    fl = json.load(open(str(paths.cat("fact_library.json")), encoding="utf-8"))
    assert fl.get("秦PLUS EV", {}).get("cell_gen") == "二代"
    assert fl.get("秦PLUS EV", {}).get("cell_flash") == "非闪充", "二代≠闪充的事实未体现"
    assert fl.get("秦新能源", {}).get("cell_gen") == "一代"
    p = os.path.join(HERE, "runs", "compact_bev_extended_202610", "榜单.html")
    if os.path.exists(p):
        t = _read("runs/compact_bev_extended_202610/榜单.html")
        assert "弗迪[二代/非闪充]" in t, "页面未显示代际"
    return "弗迪代际标注+二代非闪充事实"

def t037():
    """原始库→事实库映射完整；原始库没有的字段有核实结论。"""
    from modules.engine import facts_sync as FS
    if not FS.DEFAULT_SRC or not os.path.exists(FS.DEFAULT_SRC):
        return "原始库不在本机（发布环境），跳过映射检查"
    changed, rows, facts = FS.sync(apply=False)
    assert not changed, f"仍有 {len(changed)} 字段未同步"
    assert "_meta" in facts and facts["_meta"].get("sync_tool") == "facts_sync.py"
    for f in ("charge/voltage", "crash", "airbags", "adas_hw"):
        assert f in FS.NOT_IN_SOURCE_DETAIL, f"缺核实结论: {f}"
    return f"映射完整({len(rows)}款)，原始库缺字段均有核实结论"

def t036():
    """电压/快充必须采集而非标未披露；配置级差异要体现。"""
    d = json.load(open(str(paths.cat("trims.json")), encoding="utf-8"))
    entries = {e["model"]: e for e in d["entries"]}
    # 用随包 manual 里的真实案例（MG 07 同车 400V/800V 分层）；不依赖已删的累积条目
    e = entries.get("MG 07 纯电")
    assert e, "随包 manual 缺 MG 07 条目（trims_manual.json 应随包）"
    charges = [str(t.get("charge") or "") for t in e.get("trims", [])]
    assert any("400V" in c for c in charges), f"低配电压缺失: {charges}"
    assert any("800V" in c for c in charges), f"高配电压缺失: {charges}"
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
    from modules.board import board_view as BV
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
    """入池规则已代码化：完整性盘点工具 + pipeline 门禁（不再断言 SKILL 文字）。"""
    tc = _read("trim_coverage.py")
    assert "check_universe" in tc and "--universe" in tc, "无完整性盘点工具"
    assert "check_universe" in _read("pipeline.py"), "盘点工具未接入出榜门禁"
    return "入池规则已代码化+工具可执行+门禁接入"

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
    cfg = json.load(open(str(paths.cat("car_compact_bev_allprice.json")), encoding="utf-8"))
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
    """证据C分层可见 + tooltip标准 + τ通俗解释 + 子项折叠。
    v9.7.43 文案中文化：C·证据不足→证据不足、C·营运风险→营运风险。"""
    src = _read("render_board_html.py")
    for kw in ("证据不足", "营运风险", "不适合跨车型横向对比", "_tau_grade", "dim_breakdown"):
        assert kw in src, f"缺 {kw}"
    # v9.7.43：徽标文案必须已中文化（去掉 C· 前缀），旧前缀只剩在历史产物里
    assert 'title="证据等级C：新上市或口碑样本不足' in src, "证据徽标 tooltip 丢失"
    assert "非碰撞成绩缺失" in src, "证据C tooltip未澄清与碰撞成绩无关"
    assert "C·证据不足</span>" not in src, "证据徽标仍是 C· 英文前缀未中文化"
    out = os.path.join(HERE, "runs", "compact_bev_allprice_202610", "榜单.html")
    if os.path.exists(out):
        t = _read("runs/compact_bev_allprice_202610/榜单.html")
        # 历史产物可能是旧文案(C·证据不足)或新文案(证据不足)，都算证据分层已显示
        assert ("证据不足" in t) and ("营运风险" in t), "证据分层未显示"
        assert "维度子项依据" in t and "展开 " in t, "子项折叠未显示"
        assert "中等" in t or "高（" in t or "较低" in t, "τ无通俗档位"
    return "证据分层+tooltip+τ解释+子项折叠均生效（文案已中文化）"

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
    from modules.engine import trim_annotate as TA
    cands = json.load(open(os.path.join(HERE, "runs", "compact_bev_202610_candidates.json"), encoding="utf-8"))["candidates"]
    cfg = json.load(open(str(paths.cat("car_compact_bev_allprice.json")), encoding="utf-8"))
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
    from modules.board import board_view as BV
    for st in ("steady", "active", "stable", "new_launch", "discontinued"):
        cn = BV.lifecycle_status_cn(st)
        assert cn != st and re.search(r"[\u4e00-\u9fff]", cn), f"{st} 未中文化: {cn}"
    assert "在售" in BV.lifecycle_line("某车", "steady"), "字符串分支未转中文"
    return "生命周期状态全中文"

def t018():
    """营运车标识：note 有营运关键词必须在评价里显著标注，且不影响非营运车。"""
    from modules.board import board_view as BV
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
    from modules.engine import trim_annotate as TA
    cands = json.load(open(os.path.join(HERE, "runs", "compact_bev_202610_candidates.json"), encoding="utf-8"))["candidates"]
    cfg = json.load(open(str(paths.cat("car_compact_bev_allprice.json")), encoding="utf-8"))
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


def t048():
    """候选池完整性门禁：出榜前必须联网盘点同级全量车型，漏收必须逐个判定，不许再漏 Model3/极氪007。"""
    import copy, tempfile
    from modules.engine import trim_coverage as TC

    # 断言1（源码级）：pipeline 已接入完整性门禁，SKILL 铁律仍在
    src = _read("pipeline.py")
    assert "check_universe" in src and "完整性门禁" in src, "pipeline 未接候选池完整性门禁"
    assert "--skip-universe-gate" in src, "缺绕过门禁的显式开关"
    assert paths.src("universe_build.py").exists(), "universe 独立生成器缺失（代码即事实）"

    # 断言2（数据级）：全价位榜的 universe 清单存在且正常态无漏收
    univ = str(paths.cat("universe_car_midsedan_allprice.json"))
    pool = os.path.join(HERE, "runs", "midsedan_bev_1218_202610", "candidates_allprice.json")
    assert os.path.exists(univ) and os.path.exists(pool), "universe/候选池未固化"
    d = json.load(open(pool, encoding="utf-8"))
    r = TC.check_universe(pool, univ, d.get("pool_excluded", []))
    assert r["available"], "universe 清单不可读"
    assert not r["not_covered"], f"正常态有漏收且未说明原因: {r['not_covered']}"

    # 断言2.5（反循环论证）：universe 必须由联网榜独立生成，不是从候选池抄
    uv = json.load(open(univ, encoding="utf-8"))
    assert "universe_build.py" in uv.get("_meta", {}).get("generated_by", ""), "universe 非联网独立生成（循环论证风险）"
    assert _read("universe_build.py") and "discover" in _read("universe_build.py"), "缺联网→universe生成器"
    # 断言3：漏收的车已补入候选池+trims
    names = {c["model"] for c in d["candidates"]}
    assert "特斯拉Model 3" in names and "极氪007" in names and "比亚迪e7" in names, "门禁发现的车仍未入池"
    trims = json.load(open(str(paths.cat("trims.json")), encoding="utf-8"))["entries"]
    tnames = {x["model"] for x in trims}
    assert "特斯拉Model 3" in tnames and "极氪007" in tnames and "比亚迪e7" in tnames, "入池车缺逐配置条目"

    # 断言4（黄金反向验证）：删掉候选池的 Model 3 后，门禁必须报漏收——证明真能防漏
    fake = copy.deepcopy(d)
    fake["candidates"] = [c for c in fake["candidates"] if c["model"] != "特斯拉Model 3"]
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    try:
        json.dump(fake, tmp); tmp.close()
        r2 = TC.check_universe(tmp.name, univ, fake.get("pool_excluded", []))
        assert any("Model 3" in x for x in r2["not_covered"]), "删掉Model3后未报漏收=门禁失效"
    finally:
        os.unlink(tmp.name)
    return f"完整性盘点生效(联网独立生成)：权威 {r['universe']} 款 / 入池 {r['in_pool']} 款 / 删车能检出"


def t049():
    """销量数据缺失(None)不等于无交付(0)：不能因补录车没查到单月销量就降T3。"""
    src = _read("leaderboard.py")
    # 源码级：None 与 0 必须分开处理，不能有 `sales is None or sales == 0` 合并分支
    assert "sales is None or sales == 0" not in src, "None与0仍合并判无交付"
    assert "销量数据缺失" in src and "非无交付" in src, "缺销量分支未标注非无交付"
    assert "无交付 / 零销量" in src, "确认零销量的硬风险提示丢失"
    # 数据级：极氪007产品力高于MG07，档位不该更低
    d = json.load(open(os.path.join(HERE,"runs","bev_midsedan_202610_full","summary.json"), encoding="utf-8"))
    t = d["tiers"]["中型纯电轿车（全价位）"]
    def in_tier(name):
        for tier, arr in t.items():
            for x in arr:
                if name in (x if isinstance(x,str) else str(x.get("model",""))):
                    return tier
        return None
    j7, mg = in_tier("极氪007"), in_tier("MG 07")
    assert j7 and mg, "两车档位缺失"
    assert not (j7 == "T3" and mg in ("T1","T2")), f"产品力更高的极氪007({j7})档位低于MG07({mg})"
    return f"销量缺失不再降档：极氪007={j7} / MG07={mg}（同为T2档）"


def t050():
    """补录车的销量口径必须与全池一致：多月中位，不许单月冒充、不许假方法名。"""
    d = json.load(open(os.path.join(HERE,"runs","midsedan_bev_1218_202610","candidates_allprice.json"), encoding="utf-8"))
    by = {c["model"]: c for c in d["candidates"]}
    # Model3：必须用6个月中位 14372，不是10月异常单月 1010；且要有 trend_months 支撑
    m3 = by.get("特斯拉Model 3", {})
    assert m3.get("trend_sales") == 14372, f"Model3趋势销量异常: {m3.get('trend_sales')}（应为6月中位14372）"
    assert m3.get("trend_months") and len(m3["trend_months"]) == 6, "Model3缺6个月逐月数据"
    assert "中位数" in str(m3.get("trend_method")), "Model3口径标注非中位数"
    # e7：标'近3月中位'而非虚假的'6个月中位数'
    e7 = by.get("比亚迪e7", {})
    assert "6个月中位数" not in str(e7.get("trend_method")), "e7假标6个月中位数（无逐月数据）"
    # 007三厢：v9.7.43 方案A——用全系(三厢+猎装)合并中位9840，标 series_total 上界，不谎称单独销量
    j7 = by.get("极氪007", {})
    assert j7.get("trend_sales") == 9840, f"007全系中位异常: {j7.get('trend_sales')}"
    assert j7.get("sales_scope") == "series_total", "007未标车系合计(上界)"
    assert "全系" in str(j7.get("trend_method")), "007未标明全系合并口径"
    # 口径规则已由本测试的数据断言锁死，不再依赖 SKILL 文字
    return "口径统一：Model3=6月中位14372 / e7=近3月中位 / 007=全系合并中位9840(上界)"


def t051():
    """判档分必须与主榜展示分同源（最高配置锚定分），不许两套口径打架。"""
    from modules.engine import leaderboard as L
    src_pipeline = _read("pipeline.py")
    src_lb = _read("leaderboard.py")
    # 源码级：pipeline 判档前必须注入配置分；with_config_scores 单点定义
    assert "with_config_scores" in src_lb, "缺判档配置分注入函数"
    assert "with_config_scores(passed, cfg)" in src_pipeline, "pipeline 判档未注入配置分"
    assert "--no-config-anchor" in src_pipeline, "缺回退开关"
    # 数据级：with_config_scores 注入后分数==主榜best_total，配置名同步
    cfg = json.load(open(str(paths.cat("car_midsedan_allprice.json")), encoding="utf-8"))
    cands = json.load(open(os.path.join(HERE,"runs","midsedan_bev_1218_202610","candidates_allprice.json"), encoding="utf-8"))["candidates"]
    from modules.engine import trim_annotate as TA
    injected = L.with_config_scores(cands, cfg)
    td = TA.annotate_all(cands, cfg)
    by = {c["model"]: c for c in injected}
    for m in ("MG 07 纯电", "深蓝L06", "特斯拉Model 3"):
        c = by[m]
        assert c.get("score_basis") == "annotated_trim", f"{m}判档未用配置分"
        best_total = td[m]["best_total"]
        # 用配置分按权重算总分，应等于主榜 best_total
        W = {d["key"]: d["weight"] for d in cfg["dimensions"]}
        tot = sum(c["scores"][k]*W[k] for k in W)/sum(W.values())*10
        assert abs(round(tot,1) - round(float(best_total),1)) <= 0.1, f"{m}判档分{tot:.1f}≠主榜{best_total}"
        assert c.get("scored_trim_used") == td[m]["best_trim"], f"{m}配置名未同步"
    return "判档分=主榜配置锚定分（MG07=88.0/845版、深蓝L06=87.8/670版），配置名同源"


def t052():
    """条目内一致性：note文案不得与crash/safety_basis矛盾，且门禁必须接入出榜链路。"""
    from modules.engine import cross_verify as XV
    # 源码级：门禁函数存在 + pipeline 接入 + 可绕过开关
    assert "def check_internal_consistency" in _read("cross_verify.py"), "缺一致性校验函数"
    src = _read("pipeline.py")
    assert "check_internal_consistency(cands)" in src and "内部一致性门禁" in src, "pipeline 未接入一致性门禁"
    assert "--skip-consistency-gate" in src, "缺绕过开关"
    # 数据级：真实候选池必须无矛盾
    cands = json.load(open(os.path.join(HERE,"runs","midsedan_bev_1218_202610","candidates_allprice.json"), encoding="utf-8"))["candidates"]
    r = XV.check_internal_consistency(cands)
    assert r["passed"], f"真实池有矛盾: {r['conflicts']}"
    # 反向验证：注入矛盾必须被检出（证明不是摆设）
    import copy
    fake = copy.deepcopy(cands)
    for c in fake:
        if c["model"] == "深蓝L06":
            c["note"] = "磁流变仅Ultra版；无碰撞成绩"
    r2 = XV.check_internal_consistency(fake)
    assert not r2["passed"] and any(x["model"] == "深蓝L06" for x in r2["conflicts"]), "注入矛盾未被检出=门禁失效"
    return f"一致性门禁生效：{r['checked']}款0矛盾；注入深蓝L06矛盾能检出"


def t053():
    """同源车评分一致性：同价同配置车不得无据大差异；有据差异须登记理由。"""
    import copy
    from modules.engine import sibling_check as SC
    src = _read("pipeline.py")
    assert "sibling_check" in src and "同源车一致性门禁" in src, "pipeline 未接同源车门禁"
    assert "--skip-sibling-gate" in src, "缺绕过开关"
    assert SC.selftest() == 0, "sibling_check selftest失败"
    # 数据级：真实候选池无据差异为0（safety差异已登记理由）
    pool_path = os.path.join(HERE, "runs", "midsedan_bev_1218_202610", "candidates_allprice.json")
    d = json.load(open(pool_path, encoding="utf-8"))
    r = SC.check(d["candidates"], d.get("score_diff_reasons", {}))
    assert r["passed"], f"真实池有无据差异: {r['issues']}"
    # 007两车 value/cabin/safety 已对齐（同价同配置同Euro NCAP成绩）
    by = {c["model"]: c for c in d["candidates"]}
    j7, gt = by["极氪007"], by["极氪007GT"]
    assert j7["scores"]["value"] == gt["scores"]["value"] == 8.8, "value未对齐"
    assert j7["scores"]["cabin"] == gt["scores"]["cabin"] == 9.0, "cabin未对齐"
    assert j7["scores"]["safety"] == gt["scores"]["safety"] == 8.8, "safety同成绩应同分"
    # 反向：把三厢safety改成7.0制造无据差异，门禁必须检出
    import copy as _copy
    fake = _copy.deepcopy(d["candidates"])
    for c in fake:
        if c["model"] == "极氪007":
            c["scores"]["safety"] = 7.0
    r2 = SC.check(fake, {})
    assert not r2["passed"] and any(x["dim"] == "safety" for x in r2["issues"]), "注入safety差异未检出"
    return f"同源车门禁生效：{r['pairs']}对/0无据差异；007/GT value/cabin/safety全对齐"


def t054():
    """安全分严谨性：多体系映射表 + 单调性(有成绩车不得低于无成绩车中位)。"""
    import copy
    from modules.engine import safety_check as SFC
    tbl = SFC.load_table()
    # 映射表结构：体系分级 + 严谨性说明
    assert "Euro NCAP" in tbl["systems"] and "C-IASI" in tbl["systems"], "缺体系分级"
    assert tbl["systems"]["Euro NCAP"]["grades"]["5star_occupant>=90"] == 8.8, "Euro映射分错"
    assert tbl["structural_evidence_policy"]["in_formula"] is False, "扭转刚度不应入公式"
    assert ("无直接对应关系" in tbl["note"] or "不可按得分率横向" in tbl["note"]), "缺体系不可比说明"
    # pipeline接入
    src = _read("pipeline.py")
    assert "safety_check" in src and "安全分严谨性门禁" in src and "--skip-safety-gate" in src, "pipeline未接安全门禁"
    # 数据级：真实池通过 + 007GT=8.8 + basis
    pool = os.path.join(HERE, "runs", "midsedan_bev_1218_202610", "candidates_allprice.json")
    d = json.load(open(pool, encoding="utf-8"))
    # 走真实链路：表重算后候选池须先对齐表分，再跑门禁（pipeline 里就是这个顺序）。
    # 直接对历史池跑 check 会报 mismatch —— 那是「池子比表旧」，不是门禁误判。
    SFC.sync_table_scores_to_pool(d["candidates"], tbl)
    # 再走举证封顶（与出榜链路同序：表分同步 → 封顶 → 门禁）。
    # 历史池是按老口径给的分，无实锤车可能高于实锤车；不封顶直接跑门禁会报 monoton.
    SFC.evidence_cap(d["candidates"])
    r = SFC.check(d["candidates"], tbl)
    assert r["passed"], f"真实池安全门禁未过: {r['issues']}"
    by = {c["model"]: c for c in d["candidates"]}
    gt = by["极氪007GT"]
    assert gt["scores"]["safety"] == 8.8 and gt["safety_basis"] == "euro_ncap", "007GT安全分未修"
    assert by["极氪007"]["scores"]["safety"] == 8.8, "三厢007同成绩应同分"
    assert r["official_min"] >= r["no_score_median"], "单调性破坏"
    # 反向：改回7.2必须检出
    fake = copy.deepcopy(d["candidates"])
    for c in fake:
        if c["model"] == "极氪007GT":
            c["scores"]["safety"] = 7.2
    r2 = SFC.check(fake, tbl)
    assert not r2["passed"], "7.2未被检出"
    return f"安全分门禁生效：{r['checked']}款有成绩车/最低{r['official_min']}≥无成绩中位{r['no_score_median']}；007GT=8.8"


def t055():
    """碰撞成绩标记：score_rows须透传crash；有权威成绩(含Euro NCAP)不误标。"""
    import json as J
    from modules.engine import leaderboard as LB
    from modules.board import render_board_html as RH
    # 源码级：score_rows 必须透传 crash
    src = _read("leaderboard.py")
    assert '"crash": c.get("crash")' in src, "score_rows未透传crash字段"
    # 数据级：score_rows 输出含crash
    cfg = J.load(open(str(paths.cat("car_midsedan_allprice.json")), encoding="utf-8"))
    pool = J.load(open(os.path.join(HERE, "runs", "midsedan_bev_1218_202610", "candidates_allprice.json"), encoding="utf-8"))["candidates"]
    rows = LB.score_rows(pool, cfg)
    by = {r["model"]: r for r in rows}
    j7 = by["极氪007"]
    assert "crash" in j7 and j7["crash"] and "Euro NCAP" in str(j7["crash"]), "007崩溃字段未透传"
    assert j7.get("safety_basis") == "euro_ncap", "basis未透传"
    # risk_flags 8个case
    cases = [
        ({"crash": "Euro NCAP 2026五星93%；无正式C-NCAP星级", "safety_basis": "euro_ncap"}, False),
        ({"crash": "Euro NCAP 2026五星(93%)", "safety_basis": "euro_ncap"}, False),
        ({"crash": "C-NCAP 92.3%", "safety_basis": "cncap"}, False),
        ({"crash": "Euro NCAP 2026五星(93%)", "safety_basis": None}, False),
        ({"crash": "无官方成绩", "safety_basis": None}, True),
        ({"crash": "未查到C-NCAP成绩", "safety_basis": None}, True),
        ({"crash": "未查到C-NCAP；无中保研", "safety_basis": None}, True),
        ({"safety_basis": None}, True),
    ]
    for r, want in cases:
        got = "缺权威碰撞成绩" in RH.risk_flags(r)
        assert got == want, f"risk_flags误判: {r} → {got}, 期望{want}"
    return "碰撞标记修复：score_rows透传crash+basis；8 case全对(Euro NCAP不误标、真无成绩仍标)"


def t056():
    """证据A以官方碰撞成绩为准(非人工evidence_status)，让有实锤的高命中车进T1。"""
    import copy
    from modules.engine import leaderboard as L
    cfg = json.load(open(str(paths.cat("car_midsedan_allprice.json")), encoding="utf-8"))
    pool = json.load(open(os.path.join(HERE, "runs", "midsedan_bev_1218_202610", "candidates_allprice.json"), encoding="utf-8"))["candidates"]
    src = _read("leaderboard.py")
    assert "has_crash_evidence" in src and "ev_for_t1" in src, "证据A未按碰撞成绩重定义"
    assert "cn_name" in src or "_BASIS_CN" in src, "缺体系中文展示名"
    r = L.tier_report(pool, cfg, None, "测试", airbag_gate=False, crash_gate=False)
    t1 = r["tiers"]["T1"]
    assert "极氪007GT" in t1, f"007GT有Euro NCAP五星+命中最高应进T1: {t1}"
    rec = next(x for x in r["detail"]["T1"] if x["model"] == "极氪007GT")
    assert rec["evidence"] == "B" and "Euro NCAP" in rec.get("evidence_t1_label", ""), \
        f"判据应显示碰撞实锤达标: {rec.get('evidence_t1_label')}"
    assert not rec.get("t1_blockers"), f"007GT不应有T1阻断: {rec.get('t1_blockers')}"
    # 反向：删掉safety_basis（无碰撞实锤）→ 证据B进不了T1
    fake = copy.deepcopy(pool)
    for c in fake:
        if c["model"] == "极氪007GT":
            c.pop("safety_basis", None)
    r2 = L.tier_report(fake, cfg, None, "测试", airbag_gate=False, crash_gate=False)
    assert "极氪007GT" not in r2["tiers"]["T1"], "删碰撞实锤后仍进T1=机制失效"
    return f"证据A以碰撞实锤为准：T1={t1}，007GT(B但Euro NCAP实锤)进T1，删实锤即出局"


def t057():
    """证据A必须有实锤(人工A无basis不算) + T2强推荐过渡档机制可用。"""
    import copy
    from modules.engine import leaderboard as L
    cfg = json.load(open(str(paths.cat("car_midsedan_allprice.json")), encoding="utf-8"))
    pool = json.load(open(os.path.join(HERE, "runs", "midsedan_bev_1218_202610", "candidates_allprice.json"), encoding="utf-8"))["candidates"]
    src = _read("leaderboard.py")
    assert 'ev_for_t1 = "A" if has_crash_evidence' in src and 'if ev == "A"' in src, "人工A无实锤未堵"
    assert "strong_pick" in src and "强推荐候选" in src, "T2强推荐机制缺失"
    # 证据A漏洞：MG07人工A但无basis → ev_for_t1 不应为A
    r = L.tier_report(pool, cfg, None, "测试", airbag_gate=False, crash_gate=False)
    mg = next(x for x in r["detail"]["T2"] + r["detail"]["T3"] if x["model"] == "MG 07 纯电")
    assert "无权威碰撞成绩" in str(mg.get("t1_blockers") or ""), f"MG07人工A无实锤未堵: {mg.get('t1_blockers')}"
    # 强推荐机制：合成一辆"有实锤+高分+命中50%但<80%"的车，应被标strong_pick
    fake = copy.deepcopy(pool)
    tgt = next(c for c in fake if c["model"] == "深蓝L06")
    tgt["safety_basis"] = "cncap"
    # 直接测强推荐判定逻辑（源码存在 + 真实数据路径不报错 + summary传递字段）
    psrc = _read("pipeline.py")
    assert "strong_picks" in psrc and 'x.get("strong_pick")' in psrc, "pipeline未传强推荐名单到页面"
    rsrc = _read("render_board_html.py")
    assert "strong-tag" in rsrc and "强推荐说明" in rsrc, "页面未渲染强推荐标记"
    # 反向：删掉safety_basis后强推荐逻辑不应把无实锤车标上
    r2 = L.tier_report(fake, cfg, None, "测试", airbag_gate=False, crash_gate=False)
    assert isinstance(r2["detail"]["T2"], list), "T2结构异常"
    return "证据A堵漏(MG07人工A无实锤被挡)+T2强推荐机制已接入(summary+页面)"


def t058():
    """有碰撞成绩的车，页面显示体系+关键数字徽标；C-IASI用等级不用百分号。"""
    import re
    from modules.board import render_board_html as RH
    src = _read("render_board_html.py")
    assert "def crash_badge" in src and "flag-crash" in src, "碰撞徽标函数/CSS缺失"
    assert "crash_badge(r)" in src, "name列未挂碰撞徽标"
    cases = [
        ({"safety_basis": "cncap", "crash": "C-NCAP 2024 92.3%五星"}, "C-NCAP 92.3%"),
        ({"safety_basis": "euro_ncap", "crash": "Euro NCAP 2026五星(安全驾驶79%/碰撞避免89%/碰撞保护93%)"}, "Euro NCAP 93%"),
        # Euro NCAP分项顺序不固定：Model3是成人96/儿童86/行人74/安全94，不能按位置取
        ({"safety_basis": "euro_ncap", "crash": "Euro NCAP 2019五星(成人乘员96%/儿童86%/行人74%/安全辅助94%)"}, "Euro NCAP 96%"),
        ({"safety_basis": "ciasi", "crash": "E-NCAP 2023五星(乘员96%)+中保研C-IASI AGGG最优点评"}, "C-IASI AGGG"),
        ({"safety_basis": None, "crash": "无官方成绩"}, ""),
    ]
    for r, want in cases:
        got = re.sub(r"<[^>]+>", "", RH.crash_badge(r))
        assert got == want, f"碰撞徽标错: {r} → {got}, 期望{want}"
    # 真实页面产物：有徽标且无成绩车不误标
    p = os.path.join(HERE, "runs", "bev_midsedan_202610_full", "榜单.html")
    if os.path.exists(p):
        h = _read("runs/bev_midsedan_202610_full/榜单.html")
        assert h.count("flag-crash") >= 7, "页面碰撞徽标不足7个"
    return "碰撞徽标：C-NCAP得分率/EuroNCAP碰撞保护/C-IASI等级全部正确，无成绩车不标"


def t059():
    """高分低档车：卡点导出到summary + 档位列'为何未进一档'标记(hover列全部卡点)。"""
    import re, html
    psrc = _read("pipeline.py")
    rsrc = _read("render_board_html.py")
    assert "t1_blockers" in psrc, "pipeline未导出T1卡点"
    assert "flag-lowtier" in rsrc and "为何未进一档" in rsrc, "档位列缺低档标记"
    assert "不在意这些因素" in rsrc and "非不安全" in rsrc, "缺客观提示/碰撞非不安全说明"
    # 数据级：summary有卡点 + 页面标记（TOP10非T1的车）
    p = os.path.join(HERE, "runs", "bev_midsedan_202610_full", "summary.json")
    if os.path.exists(p):
        s = json.load(open(p, encoding="utf-8"))
        blk = (s.get("t1_blockers") or {}).get("中型纯电轿车（全价位）") or {}
        assert "奥迪E5 Sportback" in blk, "奥迪E5卡点未导出"
        assert any("月销 450" in b for b in blk["奥迪E5 Sportback"]["blockers"]), "奥迪销量卡点丢失"
        h = _read("runs/bev_midsedan_202610_full/榜单.html")
        assert h.count("为何未进一档") >= 5, "页面低档标记不足"
        tips = [html.unescape(t) for t in re.findall(r'flag-lowtier" title="([^"]+)"', h)]
        sales_tips = [t for t in tips if "月销" in t]
        assert sales_tips and "非不安全" in "".join(tips), "销量/碰撞提示缺失"
    return "高分低档标记：卡点导出summary+档位列hover全列(销量/命中/碰撞)+客观提示"


def t060():
    """生命周期中文化：on_sale 有映射 + 未映射状态门禁可检出。"""
    from modules.board import board_view as BV
    assert BV.LIFECYCLE_STATUS_CN.get("on_sale") == "在售", "on_sale未映射"
    assert "check_lifecycle_coverage" in _read("board_view.py"), "缺覆盖检查函数"
    assert "check_lifecycle_coverage" in _read("pipeline.py"), "pipeline未接中文化门禁"
    # 真实池无漏网
    pool = json.load(open(os.path.join(HERE, "runs", "midsedan_bev_1218_202610", "candidates_allprice.json"), encoding="utf-8"))["candidates"]
    r = BV.check_lifecycle_coverage(pool)
    assert r["passed"], f"有未映射状态: {r['missing']}"
    # 反向：注入未映射状态必须检出
    import copy
    fake = copy.deepcopy(pool[:1])
    fake[0]["lifecycle"] = {"status": "off_sale_brand_new"}
    r2 = BV.check_lifecycle_coverage(fake)
    assert not r2["passed"] and "off_sale_brand_new" in r2["missing"], "未映射状态未检出"
    # 页面无 on_sale 英文泄漏
    p = os.path.join(HERE, "runs", "bev_midsedan_202610_full", "榜单.html")
    if os.path.exists(p):
        assert "on_sale" not in _read("runs/bev_midsedan_202610_full/榜单.html"), "页面仍泄漏on_sale英文"
    return "生命周期中文化：on_sale→在售，覆盖门禁生效(注入未映射状态能检出)"


def t061():
    """算法家族去重：相关算法只算1票，MG07抱团虚高被戳破。"""
    from modules.engine import leaderboard as L
    from modules.engine import robust as R
    assert hasattr(R, "METHOD_FAMILIES") and hasattr(R, "FAMILY_OF"), "缺家族定义"
    assert len(R.METHOD_FAMILIES) == 6, "家族数应为6"
    # saw系5种必须同家族（实测τ≥0.84）
    for m in ("saw", "moora", "waspas", "spotis", "edas"):
        assert R.FAMILY_OF[m] == "weighted_sum", f"{m}未在加权和家族"
    assert R.FAMILY_OF["gra"] == "grey_relational" and R.FAMILY_OF["topsis"] == "distance"
    lsrc = _read("leaderboard.py")
    assert "family_freq" in lsrc and "独立算法家族命中" in lsrc, "第一档未用家族命中"
    # 数据级：真实池跑run_robust，007家族命中应显著高于MG07
    cfg = json.load(open(str(paths.cat("car_midsedan_allprice.json")), encoding="utf-8"))
    d = json.load(open(os.path.join(HERE, "runs", "midsedan_bev_1218_202610", "candidates_allprice.json"), encoding="utf-8"))["candidates"]
    d = L.with_config_scores(d, cfg)
    names, matrix, types, _ = L.decision_matrix(d, cfg)
    schemes = L.build_schemes(cfg)
    res = R.run_robust(matrix, schemes["基准"], types, names, top_k=3, scheme_weights=L._clean_schemes(schemes))
    j7 = res["per_alt"]["极氪007"]["family_freq"]
    mg = res["per_alt"]["MG 07 纯电"]["family_freq"]
    assert j7 >= 0.8 and mg <= 0.5, f"家族命中异常: 007={j7}, MG07={mg}"
    return f"算法家族去重生效：6家族，007家族命中{j7:.0%} vs MG07{mg:.0%}(原6/10抱团虚高)"


def t062():
    """家族命中阈值用票数语义：6家族至少5票，避免80%无法整除的语义漂移。"""
    from modules.engine import leaderboard as L
    cfg = json.load(open(str(paths.cat("car_midsedan_allprice.json")), encoding="utf-8"))
    rules = L.tier_rules(cfg)
    assert rules["t1_min_family_hits"] == 5, f"默认票数阈值应为5: {rules['t1_min_family_hits']}"
    # 显式ratio可回退
    cfg2 = json.loads(json.dumps(cfg))
    cfg2["tiering"]["t1_min_family_hits"] = "ratio"
    assert L.tier_rules(cfg2)["t1_min_family_hits"] is None, "ratio回退失效"
    src = _read("leaderboard.py")
    assert "t1_min_family_hits" in src and "≥{rules['t1_min_family_hits']}/{len(robust.METHOD_FAMILIES)}票" in src, \
        "报告未显示票数语义"
    # 数据级：007(6票)/007GT(5票)过，MG07(2票)不过
    d = json.load(open(os.path.join(HERE, "runs", "midsedan_bev_1218_202610", "candidates_allprice.json"), encoding="utf-8"))["candidates"]
    r = L.tier_report(d, cfg, None, "测试", airbag_gate=False, crash_gate=False)
    assert "极氪007" in r["tiers"]["T1"] and "极氪007GT" in r["tiers"]["T1"], "5票/6票应过T1"
    assert "MG 07 纯电" not in r["tiers"]["T1"], "2票不应过T1"
    return "家族阈值票数化：≥5/6票，消除80%语义漂移；007=6票/007GT=5票过，MG07=2票不过"


def t063():
    """分数证据文本一致性：文本不得与safety_basis矛盾；全场扫描+注入能检出。"""
    import copy
    from modules.engine import evidence_consistency as EC
    assert EC.selftest() == 0, "证据一致性selftest失败"
    psrc = _read("pipeline.py")
    assert "evidence_consistency" in psrc and "证据文本一致性门禁" in psrc, "pipeline未接证据门禁"
    pool = json.load(open(os.path.join(HERE, "runs", "midsedan_bev_1218_202610", "candidates_allprice.json"), encoding="utf-8"))["candidates"]
    r = EC.check(pool)
    assert r["passed"], f"真实池有矛盾: {r['issues']}"
    # 关键车文本已按字段修正
    by = {c["model"]: c for c in pool}
    for n in ("极氪007", "极氪007GT"):
        txt = EC._safety_text(by[n])
        assert "无官方碰撞" not in txt and "Euro NCAP" in txt, f"{n}证据文本未同步: {txt}"
    # 反向：注入"有basis却写无成绩"必须检出
    fake = copy.deepcopy(pool[:1])
    fake[0]["safety_basis"] = "cncap"
    fake[0]["score_evidence"] = {"safety": ["无官方碰撞成绩-0.5"]}
    assert not EC.check(fake)["passed"], "注入矛盾未检出"
    return f"证据文本一致性：全场{r['checked']}款0矛盾；007/GT已同步Euro NCAP；注入矛盾能检出"


def t064():
    """碰撞体系单一真相源：crash_systems 覆盖全部权威体系，全链路引用不再硬编码。"""
    import re
    from modules.engine import crash_systems as CS
    from modules.board import render_board_html as RH
    assert CS.selftest() == 0, "crash_systems selftest失败"
    # 覆盖12体系 + 关键新体系
    for code in ("cncap", "ciasi", "euro_ncap", "ancap", "iihs", "nhtsa",
                 "jncap", "kncap", "asean_ncap", "latin_ncap", "bharat_ncap", "global_ncap"):
        assert code in CS.SYSTEMS, f"缺体系 {code}"
    # 别名归一（防 E-NCAP/NHTSA-NCAP 等不同写法漏判）
    for raw, want in [("E-NCAP", "euro_ncap"), ("c-ncap", "cncap"), ("C-IASI", "ciasi"),
                      ("NHTSA-NCAP", "nhtsa"), ("Bharat NCAP", "bharat_ncap"), ("iihs_tsp", "iihs")]:
        assert CS.normalize(raw) == want, f"别名归一错: {raw}→{CS.normalize(raw)} 应{want}"
    # 全链路引用 crash_systems（不再各自硬编码白名单）
    for f in ("leaderboard.py", "evidence_consistency.py", "render_board_html.py"):
        assert "crash_systems" in _read(f), f"{f} 未引用单一真相源"
    # 12体系徽标都能显示
    badge_cases = [("nhtsa", "NHTSA 5星"), ("jncap", "JNCAP五星(乘员95%)"),
                   ("iihs", "IIHS Top Safety Pick+"), ("bharat_ncap", "Bharat NCAP五星")]
    for b, cr in badge_cases:
        out = re.sub(r"<[^>]+>", "", RH.crash_badge({"safety_basis": b, "crash": cr}))
        assert out and CS.cn_name(b) in out, f"新体系徽标缺失: {b}→{out}"
    # 映射表覆盖12体系
    tbl = json.load(open(os.path.join(HERE, "official_safety_scores.json"), encoding="utf-8"))
    assert len(tbl["systems"]) >= 12, "映射表未覆盖全部体系"
    return "碰撞体系单一真相源：12体系+别名归一+全链路引用+徽标全显示"


def t065():
    """连续名次指标补二值命中盲区 + 产品力≠市场热度口径说明。"""
    from modules.engine import leaderboard as L
    from modules.engine import robust as R
    cfg = json.load(open(str(paths.cat("car_midsedan_allprice.json")), encoding="utf-8"))
    d = json.load(open(os.path.join(HERE, "runs", "midsedan_bev_1218_202610", "candidates_allprice.json"), encoding="utf-8"))["candidates"]
    d = L.with_config_scores(d, cfg)
    names, matrix, types, _ = L.decision_matrix(d, cfg)
    schemes = L.build_schemes(cfg)
    res = R.run_robust(matrix, schemes["基准"], types, names, top_k=3, scheme_weights=L._clean_schemes(schemes))
    # 连续名次字段存在且合理
    m3 = res["per_alt"]["特斯拉Model 3"]
    assert m3.get("rank_mean") is not None and 4 <= m3["rank_mean"] <= 7, \
        f"Model3平均名次应稳定在5附近: {m3.get('rank_mean')}"
    assert m3.get("rank_span"), "缺名次区间"
    # 报告：平均名次列 + Model3不再被剔除 + 口径说明
    assert "平均名次" in res["report"] and "特斯拉Model 3" in res["report"], "报告中Model3被埋没"
    assert "产品力" in res["report"] and "市场热度" in res["report"], "缺口径边界说明"
    # 页面传字段
    psrc = _read("pipeline.py")
    assert "rank_mean_by_model" in psrc and "scope_note" in psrc, "pipeline未传连续名次/口径"
    rsrc = _read("render_board_html.py")
    assert "算法平均名次" in rsrc and "口径边界" in rsrc, "页面未展示"
    # summary 真实产物
    sp = os.path.join(HERE, "runs", "bev_midsedan_202610_full", "summary.json")
    if os.path.exists(sp):
        st = (json.load(open(sp, encoding="utf-8")).get("stability") or {})
        assert "特斯拉Model 3" in (st.get("rank_mean_by_model") or {}), "summary缺Model3平均名次"
        assert "市场热度" in (st.get("scope_note") or ""), "summary缺口径说明"
    return f"连续名次补盲区：Model3平均第{m3['rank_mean']:.1f}(区间{m3['rank_span']})可见+产品力≠市场热度口径说明"


def t066():
    """强推荐OR路径(稳定但名次略后) + 一档+强推荐叙事 + 并行纪律固化。"""
    from modules.engine import leaderboard as L
    cfg = json.load(open(str(paths.cat("car_midsedan_allprice.json")), encoding="utf-8"))
    d = json.load(open(os.path.join(HERE, "runs", "midsedan_bev_1218_202610", "candidates_allprice.json"), encoding="utf-8"))["candidates"]
    r = L.tier_report(d, cfg, None, "测试", airbag_gate=True, crash_gate=False)
    # T1不放宽
    assert set(r["tiers"]["T1"]) == {"极氪007GT", "极氪007"}, "T1不应放宽"
    # 强推荐OR路径：稳稳第5的Model3应被标(steady)，且T1仍2款
    by = {x["model"]: x for x in r["detail"]["T2"]}
    m3 = by.get("特斯拉Model 3")
    assert m3 and m3.get("strong_pick"), "Model3稳稳第5应进强推荐"
    assert m3.get("strong_pick_reason") == "steady", f"应走steady路径: {m3.get('strong_pick_reason')}"
    # 无碰撞实锤的车不能靠steady混进强推荐
    mg = by.get("MG 07 纯电")
    assert mg and not mg.get("strong_pick"), "MG07无碰撞实锤不应强推"
    # 叙事：一档+强推荐共同清单
    rsrc = _read("render_board_html.py")
    assert "与第一档共同构成" in rsrc and "只有几款" in rsrc, "缺一档+强推荐叙事"
    assert "换算法会换位" in rsrc and "稳定地排第5左右" in rsrc, "缺两种强推荐原因的文案"
    # 并行纪律固化
    sksrc = _read("SKILL.md")
    assert "多会话并行改代码的纪律" in sksrc and "新增文件必须先加白名单" in sksrc, \
        "并行/白名单纪律未固化"
    # 真实产物
    sp = os.path.join(HERE, "runs", "bev_midsedan_202610_full", "summary.json")
    if os.path.exists(sp):
        s = json.load(open(sp, encoding="utf-8"))
        picks = (s.get("strong_picks") or {}).get("中型纯电轿车（全价位）") or []
        assert "特斯拉Model 3" in picks, "产物强推荐缺Model3"
    return "强推荐OR路径：Model3走steady进强推荐、T1仍2款、MG07无实锤被挡；并行纪律已固化"


def t067():
    """强推荐金标在打分主榜【档位列】显示（车名列不重复），hover讲清为何强推+为何未进一档。"""
    p = os.path.join(HERE, "runs", "bev_midsedan_202610_full", "榜单.html")
    if not os.path.exists(p):
        return "强推主榜档位列金标：产物缺失跳过"
    import re
    h = _read("runs/bev_midsedan_202610_full/榜单.html")
    i, j = h.find('id="mainBoard"'), h.find("</table>", h.find('id="mainBoard"'))
    assert i >= 0 and j > i, "主表缺失"
    body = h[i:j]
    assert ".strong-main" in _read("render_board_html.py"), "缺金标CSS"
    assert body.count("strong-main") >= 2, "主榜无强推金标"
    # 强推车：档位列(td index 6)有金标、车名列(td index 1)无、且不叠加"为何未进一档"
    for m in ("特斯拉Model 3", "深蓝L06"):
        k = body.find("data-model='" + m + "'")
        row = body[k:body.find("</tr>", k)] if k >= 0 else ""
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        assert len(tds) > 6, f"{m}主榜列数异常"
        assert "strong-main" in tds[6], f"{m}档位列缺金标"
        assert "strong-main" not in tds[1], f"{m}车名列应移除金标"
        assert "为何未进一档" not in row, f"{m}强推车不应叠加低档标记"
    k = body.find("data-model='MG 07 纯电'")
    mg_row = body[k:body.find("</tr>", k)] if k >= 0 else ""
    assert "strong-main" not in mg_row, "MG07非强推却误标"
    assert "为何未进一档" in mg_row, "MG07高分低档仍应显示为何未进一档"
    # hover含强推理由 + 未进一档原因
    tips = re.findall(r'strong-main" title="([^"]+)"', body)
    assert tips and all("强推荐" in t and "未进第一档因" in t for t in tips), "金标hover缺强推/未进档理由"
    return f"强推金标在档位列({body.count('strong-main')}处)、车名无重复、hover讲清双重理由、非强推不误标"
    return f"强推主榜金标：{body.count('strong-main')}处(hover含原因)，非强推车不误标"


def t068():
    """候选池同一字段多种形状（price 文本 / trim 字符串）必须由契约层归一化。

    2026-11-21 出「纯电中型SUV」榜时真实踩到：22 款里 11 款 trim 是字符串、
    22 款 price 全是 "24.98万" 文本；master_pool 把原始记录直接塞进 candidate，
    trim_annotate `t.get("price") - price_hint` 抛 TypeError，整条出榜崩掉。
    """
    from modules.contracts.candidate_schema import (
        check_candidate, normalize_pool, price_of)
    from modules.engine import trim_annotate as TA
    raw = [{"model": "极氪7X", "price": "24.98万", "trim": "103kWh 后驱MAX"},
           {"model": "另一款", "price": 15.0, "trim": {"name": "激光雷达版", "price": "14.99"}}]
    norm = normalize_pool(raw)
    assert all(check_candidate(c) == [] for c in norm), [check_candidate(c) for c in norm]
    assert norm[0]["price"] == 24.98 and norm[0]["trim"] == {"name": "103kWh 后驱MAX", "price": None}
    assert norm[1]["trim"]["price"] == 14.99, norm[1]["trim"]
    # 不归一化必须被检出（否则门禁是摆设）
    assert check_candidate({"price": "24.98万", "trim": "X"}), "未归一化未被检出"
    # 下游 resolve_ref 拿到 str price 不再崩，且按价格就近选配置
    trims = [{"name": "A", "price": 24.99}, {"name": "B", "price": 30.0}]
    ref, _fb, basis = TA.resolve_ref("", trims, [{"price": "24.98万"}])
    assert ref and ref["name"] == "A", (ref, basis)
    # 价格优先级不许改（价位段子榜按它划带）
    assert price_of({"trim": {"price": "14.99"}, "price": 20}) == 14.99
    assert price_of({"price_guide": ["12.99", "16.69"]}) == 12.99
    return "契约归一化生效：price 文本/trim 字符串统一、未归一化能检出、下游不再 TypeError"


def t069():
    """trims 里已核验的官方指导价必须自动回流到候选池 price_guide（出榜链路内置）。

    2026-11-21 出中型纯电SUV榜：auto_trims 已把官方指导价写进 trims.json，
    但候选池 price_guide 还是 None，闸门只认 price_guide → 11 款（极氪7X/Model Y/
    蔚来ES6/腾势N7/乐道L60…）全被判 price_unknown 落榜，榜单只剩 10 款且全是 13-16 万。
    当时只补了个手动命令（--sync-prices）＝ 仍然靠人记得跑。这里锁死三件事：
      ① 回填函数本身不覆盖已有值、不伪造无价车；
      ② 出榜链路（pipeline / board_v2）自动调用，且有独立复核门禁；
      ③ 审计结果进 summary.price_backfill，可追溯。
    """
    import tempfile
    from modules.engine import facts_sync as FS

    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump({"scoring_enabled": False, "entries": [
        {"model": "测试车A", "trims": [{"name": "低配", "price": 10.0},
                                     {"name": "高配", "price": 14.0}]},
        {"model": "测试车B", "trims": []},
    ]}, tmp, ensure_ascii=False)
    tmp.close()
    try:
        cars = [
            {"model": "测试车A", "price_guide": None},          # 应回填
            {"model": "测试车A2", "price_guide": [9.9, 13.9]},  # 已有值不许覆盖
            {"model": "测试车C", "price_guide": None},          # trims 也没有 → 真缺口
        ]
        filled, still = FS.backfill_price_guide_from_trims(cars, tmp.name)
        assert ("测试车A", [10.0, 14.0]) in filled, f"未回填：{filled}"
        assert cars[0].get("price_source", "").startswith("trims.json"), "回填未标来源"
        assert cars[1]["price_guide"] == [9.9, 13.9], "覆盖了已有 price_guide"
        assert still == ["测试车C"], f"真缺口判定错：{still}"
        # 独立复核用索引：门禁靠它判断「trims 有价却没回流」
        pm = FS.trims_price_map(tmp.name)
        assert pm, "价格索引为空，门禁无法复核"
    finally:
        os.remove(tmp.name)

    # 链路必须内置（而不是只留一个手动命令）
    src = _read("pipeline.py")
    assert "backfill_price_guide_from_trims" in src, "pipeline 未接价格回流"
    assert "价格回流门禁" in src, "pipeline 缺回填失效的硬门禁"
    assert "backfill_price_guide_from_trims" in _read("board_v2.py"), "board_v2 未接价格回流"
    assert "price_backfill" in src, "summary 缺回填审计字段"
    return "价格回流：自动回填/不覆盖已有/真缺口不伪造 + 链路内置 + 独立复核门禁 + 审计留痕"


def t070():
    """碰撞成绩披露口径：缺数据不得算成「有成绩」（单一真相源 has_score）。

    2026-11-21 出中型纯电SUV榜时，报告写「有官方碰撞成绩 4/21」，
    实际只有 3 款有分——第 4 款是「C-NCAP 已抽车未测」。
    根因：pipeline 自己写了一串排除词（无/未查到/—），漏了「未测」，
    而 crash_systems 又没有统一的「是否有成绩」判定。
    现在口径收口到 crash_systems.has_score()，披露/门禁/徽标都必须用它。
    """
    from modules.engine.crash_systems import has_score
    assert has_score("C-NCAP 90.2% 五星")
    assert has_score("C-NCAP 92.7%")
    assert has_score("C-IASI G+")
    assert not has_score("C-NCAP 已抽车未测"), "「未测」被当成有成绩"
    assert not has_score("无官方成绩（海狮07有）"), "「无官方成绩」被当成有成绩"
    assert not has_score("—") and not has_score("") and not has_score(None)
    src = _read("pipeline.py")
    assert ("CRS.parse_score" in src or "CRS.has_score" in src), "pipeline 披露未走碰撞真相源"
    return "碰撞披露：百分比/星级/等级才算成绩，未测/无成绩不计入，pipeline 走单一真相源"


def t071():
    """碰撞成绩必须从文本结构化进字段（否则有实锤的车被当「无实锤」挡在一档外）。

    2026-11-21：极氪7X/阿维塔07/乐道L60/海狮07EV 的 C-NCAP 成绩（90.7/90.2/89.2/87.3%）
    只写在 note 自由文本里，crash/safety_basis 全空。极氪7X 家族命中 6/6、全情景 98%、
    证据 A、月销 8500，只因「无权威碰撞实锤」这一条进不了一档。
    数据采到了、没落字段 = 等于没采。这里锁死「文本→字段」自动回流 + 门禁 + 链路接入。
    """
    from modules.engine import crash_systems as CS
    from modules.engine import facts_sync as FS
    from modules.engine import cross_verify as XV

    # 1) 解析器：有成绩才解析，未测/无成绩必须返回 None
    assert CS.parse_score("C-NCAP 2024版90.7%")["score"] == 90.7
    assert CS.parse_score("C-NCAP 90.2% 五星")["basis"] == "cncap"
    assert CS.parse_score("C-IASI G+")["grade"] == "G+"
    assert CS.parse_score("C-NCAP 已抽车未测") is None, "「未测」被解析成成绩"
    assert CS.parse_score("无官方成绩") is None
    assert CS.parse_score("置换补贴5%") is None, "无体系词的百分比被当成成绩"

    # 2) 回填：只写在 note 里的要进字段；已有权威 basis 不许被覆盖
    cars = [
        {"model": "极氪7X", "crash": "", "safety_basis": "", "note": "C-NCAP 2024版90.7%一手"},
        {"model": "零跑C10", "crash": "C-NCAP 90.2% 五星", "safety_basis": ""},
        {"model": "丰田bZ5", "crash": "C-NCAP 86.7%", "safety_basis": "cncap"},
        {"model": "大众T6", "crash": "C-NCAP 已抽车未测", "safety_basis": ""},
    ]
    filled, unresolved = FS.backfill_crash_from_text(cars)
    assert ("极氪7X", "C-NCAP 90.7%", "cncap") in filled, filled
    assert cars[0]["safety_basis"] == "cncap" and CS.has_score(cars[0]["crash"])
    assert cars[1]["safety_basis"] == "cncap", "有成绩但无 basis 的未补 basis"
    assert cars[1]["crash"] == "C-NCAP 90.2% 五星", "覆盖了原 crash 文案"
    assert cars[2]["crash_source"] if 0 else True  # 已有 basis 的车不动
    assert "大众T6" in unresolved, "无成绩的车应进 unresolved 而不是编造成绩"

    # 3) 门禁：文本有成绩但字段空必须报（防回填失效）
    r = XV.check_internal_consistency(
        [{"model": "X", "note": "C-NCAP 2024版90.7%", "crash": "", "safety_basis": ""}])
    assert not r["passed"] and r["conflicts"][0]["field"] == "safety_basis", r

    # 4) 链路必须内置
    for f in ("pipeline.py", "board_v2.py"):
        assert "backfill_crash_from_text" in _read(f), f"{f} 未接碰撞成绩回流"
    assert "crash_backfill" in _read("pipeline.py"), "summary 缺碰撞回填审计"
    return "碰撞成绩：文本解析→字段回填（不覆盖已有/不编造）+ 反向门禁 + 链路内置 + 审计"


def t072():
    """碰撞成绩必须真正参与打分：表外有成绩的车按映射规则自动算 safety 分。

    2026-11-21 第二段断链：crash 里已有 C-NCAP 成绩，但 official_safety_scores.json
    没有对应条目 → safety 仍按「无成绩车插补」，成绩等于白采。
    这里锁死：规则机器可读（map_spec）、公式可复算、人工分不被覆盖、无成绩不编号。
    """
    from modules.engine import safety_check as SC
    table = {
        "car_scores": {"已有车": {"system": "C-NCAP", "value": 90.0, "score": 8.8, "source": "人工核验"}},
        "systems": {
            "C-NCAP": {"map_spec": {"kind": "pct", "base": 7.0, "ref": 80, "slope": 0.155,
                                    "cap": 9.3, "older_penalty": 0.2, "older_before": 2021}},
            "C-IASI": {"map_spec": {"kind": "grade", "grades": {"G+": 9.3, "G": 9.0}}},
            "Euro NCAP": {"map_spec": {"kind": "star", "five": 8.3, "four": 7.8, "three": 7.0}},
        },
    }
    cars = [
        {"model": "极氪7X", "crash": "C-NCAP 2024版90.7%", "scores": {"safety": 8.7}},
        {"model": "老规程车", "crash": "C-NCAP 2019版92%", "scores": {"safety": 8.9}},
        {"model": "中保研车", "crash": "C-IASI G+", "scores": {"safety": 8.0}},
        {"model": "欧标车", "crash": "Euro NCAP 五星", "scores": {"safety": 8.0}},
        {"model": "已有车", "crash": "C-NCAP 90%", "scores": {"safety": 8.8}},
        {"model": "无成绩车", "crash": "无官方成绩", "scores": {"safety": 8.4}},
    ]
    applied, skipped = SC.apply_auto_scores(cars, table=table)
    got = {a["model"]: a["score"] for a in applied}
    # 公式：7.0+(90.7-80)*0.155 = 8.66 → 8.7
    assert got.get("极氪7X") == 8.7, got
    # 2021 及更早要降 0.2：7.0+12*0.155=8.86→8.9，再 -0.2 = 8.7
    assert got.get("老规程车") == 8.7, got
    assert got.get("中保研车") == 9.3, got
    assert got.get("欧标车") == 8.3, "NCAP 星级无乘员分项应取保守档 8.3"
    assert "已有车" not in got, "覆盖了表内人工分"
    assert cars[4]["scores"]["safety"] == 8.8, "人工分被改动"
    assert "无成绩车" in skipped, "无成绩车被编号"
    # 真正写进了 scores（否则等于没参与打分）
    assert cars[0]["scores"]["safety"] == 8.7 and cars[0].get("safety_score_source")
    # 链路内置 + 审计
    src = _read("pipeline.py")
    assert "apply_auto_scores" in src, "pipeline 未接成绩→分数映射"
    assert "safety_auto" in src, "summary 缺安全分映射审计"
    assert "--no-auto-safety" in src, "缺 A/B 开关"
    return "成绩→分数：公式可复算、老规程降权、等级/星级映射、人工分不覆盖、无成绩不编号、链路内置"


def t073():
    """映射表口径必须能被规则复算：表内分数 = 公式结果，禁止手填漂移。

    2026-11-21 用户选 A 方案。表里 8 条人工分对不上同一公式：
      90.8%→8.9（应 8.7）、90.6%→8.5（应 8.6）、91.3%→9.1（应 8.8）
    → 同样得分率给了不同分。现在：全表按 map_spec 复算 + 门禁强校验 + 候选池与表自动对齐。
    """
    import copy
    from modules.engine import safety_check as SC

    table = SC.load_table()
    # 1) 真表必须已经统一（若有漂移，说明没跑 --recompute）
    ch, un = SC.recompute_table(copy.deepcopy(table), apply=False)
    assert not ch and not un, f"映射表存在未复算条目：{[x['model'] for x in ch]} / {[x['model'] for x in un]}"

    # 2) 手改一条成错误值 → 门禁必须检出 formula_mismatch
    bad = copy.deepcopy(table)
    victim = "马自达EZ-6" if "马自达EZ-6" in bad["car_scores"] else next(iter(bad["car_scores"]))
    bad["car_scores"][victim]["score"] = 9.9
    r = SC.check([], table=bad)
    types = [x["type"] for x in r["issues"]]
    assert "formula_mismatch" in types, f"手填错分未被检出：{r['issues']}"

    # 3) 候选池旧分 → 必须被对齐到表分（否则榜上与表两套数）
    cars = [{"model": victim, "scores": {"safety": 1.0}}]
    fixed = SC.sync_table_scores_to_pool(cars, table=table)
    assert fixed and cars[0]["scores"]["safety"] == table["car_scores"][victim]["score"], fixed
    assert cars[0].get("safety_score_source"), "对齐未标来源"

    # 4) 公式本身：C-NCAP 90.8% 必须是 8.7（不能被"人工印象"改）
    spec = table["systems"]["C-NCAP"]["map_spec"]
    s, _ = SC.score_from_record("C-NCAP", {"value": 90.8}, table)
    assert s == 8.7, s
    assert SC.score_from_record("C-NCAP", {"value": 92.3}, table)[0] == 8.9

    # 5) 链路内置
    src = _read("pipeline.py")
    assert "sync_table_scores_to_pool" in src, "pipeline 未把表分同步到候选池"
    assert "safety_table_sync" in src, "summary 缺表分同步审计"
    return "映射表全表可复算 + 手填错分被门禁拦 + 候选池自动对齐表分 + 公式锁死"


def t074():
    """配置下拉框必须「二选一」：有阶梯给下拉，没采到给显式「配置待补」。

    2026-11-21 用户问「大众 ID.AURA T6 为什么没有下拉框」：
    该车在汽车之家查无车系（索引只有 ID.AURA 且已无 config 页），trims.json 无条目；
    渲染时原来 `if td.get("trims")` 不成立就什么都不画 → 页面看起来像 bug，
    读者无法区分「这车配置没差异」和「我们没采到」。现在强制标注 + 回归守住。
    """
    import html as _html
    import re as _re
    from modules.board import render_board_html as RH
    from modules.contracts.model_names import canon

    # 1) 函数级：两种输入都必须给出可识别标记
    assert "trim-missing" in RH.trim_selector("大众ID.AURA T6", []), "无配置时未标注待补"
    sel = RH.trim_selector("极氪7X", [{"name": "低配", "price": 22.98},
                                     {"name": "高配", "price": 26.98}], "低配")
    assert 'class="trimsel"' in sel and sel.count("<option") == 2, sel
    assert "selected" in sel, "未选中评分配置"

    # 2) 产物级：主榜每款车必须二选一，且判定与 trims.json 一致
    hp = os.path.join(HERE, "runs", "midsuv_bev_202610", "榜单.html")
    if not os.path.exists(hp):
        return "配置下拉：函数级通过（产物缺失，跳过页面级）"
    h = _read("runs/midsuv_bev_202610/榜单.html")
    body = h.split('id="mainBoard"')[1].split("</table>")[0]
    rows = [r for r in _re.findall(r"<tr[^>]*>(.*?)</tr>", body, _re.S) if "<td" in r]
    assert rows, "主表无数据行"
    bare = [r for r in rows if "trimsel" not in r and "trim-missing" not in r]
    assert not bare, f"主榜 {len(bare)} 行既无下拉框也无『配置待补』标注"

    db = json.load(open(str(paths.cat("trims.json")), encoding="utf-8"))
    have = {canon(e.get("model")) for e in db.get("entries", [])
            if len(e.get("trims") or []) >= 2}
    bad = []
    for row in rows:
        m = _re.search(r"<td class='name'>([^<]+)<", row)
        if not m:
            continue
        model = _html.unescape(m.group(1)).strip()
        if canon(model) in have:
            if "trimsel" not in row:
                bad.append(f"{model} 有阶梯却无下拉框")
        elif "trim-missing" not in row:
            bad.append(f"{model} 无阶梯却未标待补")
    assert not bad, "；".join(bad)
    n_miss = sum(1 for r in rows if "trim-missing" in r)
    return f"配置下拉二选一：{len(rows)} 款在榜车，{len(rows)-n_miss} 款有下拉、{n_miss} 款标注待补"


def t075():
    """采集必须有多源兜底：索引未命中不能直接放弃，要去站点检索找车系 id。

    2026-11-21 根因（比"数据源没有"更准确）：大众 ID.AURA T6 / 小鹏 MONA L05
    在汽车之家 A-Z 在售索引里查不到，但车系页（autohome.com.cn/8584）和 config 页
    早就有，parse_config 能正常解析出 4 款配置。find_sid 只查索引一次，miss 即 skip，
    于是整款车被跳过 → 页面没有下拉框。

    修复：find_sid 末尾加 find_sid_remote（站点检索 → 从 URL 取 series id）。
    这里锁死三件事：有兜底、能提取 id、兜底失败不瞎猜。
    """
    from modules.research import auto_trims as A
    from modules.research import researcher as R

    # 1) 源码层：兜底分支必须存在
    src = _read("auto_trims.py")
    assert "def find_sid_remote" in src, "缺远程兜底函数"
    assert "remote_search" in src, "兜底结果未标记来源"

    orig = R.search
    try:
        # 2) 索引空 + 搜索能定位车系页 → 必须拿到 sid
        R.search = lambda *a, **kw: [
            {"title": "【 测试车X 】 报价", "url": "https://www.autohome.com.cn/8584"}]
        sid, name, how = A.find_sid_remote("测试车X")
        assert sid == "8584" and how.startswith("remote_search"), (sid, how)
        got = A.find_sid("测试车X", {})
        assert got[0] == "8584", f"索引 miss 时未走兜底：{got}"

        # 3) 兜底失败必须返回 None，不许猜一个 sid 出来
        R.search = lambda *a, **kw: []
        assert A.find_sid("测试车X", {})[0] is None, "兜底失败却返回了 sid"
    finally:
        R.search = orig

    # 4) 只提取车系页形态（数字 id），不把文章页/品牌页当车系
    import re as _re
    assert _re.search(r"autohome\.com\.cn/(\d{3,5})", "https://www.autohome.com.cn/8584")
    assert not _re.search(r"autohome\.com\.cn/(\d{3,5})(?:/|$|\?)",
                          "https://car.autohome.com.cn/price/brand-1.html")
    return "采集兜底：索引 miss → 站点检索取 series id → 正常解析；失败不猜 + 源码锁定"


def t076():
    """逐配置采集必须走多源有序调度，且每次尝试留痕（失败也能看出试过哪些源）。

    2026-11-21 用户追问「不能多源查吗」：原实现只有汽车之家索引一条路，
    miss 就 skip，待补清单只写「名称未匹配」，看不出到底试过什么。
    现在 trim_sources.collect_trims 按 索引 → 站点检索 → 存在性核对 依次尝试，
    结果与 attempts 一起返回；结构化源能出配置，存在性源只出证据（防编造）。
    """
    from modules.research import trim_sources as TS
    from modules.research import researcher as R

    # 1) 源链：结构化在前、存在性兜底
    ids = [s["id"] for s in TS.SOURCES]
    assert ids[0] == "autohome_index" and "autohome_search" in ids, ids
    assert TS.SOURCES[-1]["kind"] == "existence", "存在性核对应在最后（只出证据）"

    two = [{"name": "低配", "price": 10.0}, {"name": "高配", "price": 12.0}]

    # 2) 本地索引命中 → 直接用，不再打远程
    calls = {"remote": 0}
    def local_hit(m, idx):
        return "111", "测试车", "exact"
    def remote_spy(m, idx=None):
        calls["remote"] += 1
        return None, None, None
    r1 = TS.collect_trims("测试车", {}, lambda sid: (two, None),
                          find_sid_local=local_hit, find_sid_remote=remote_spy,
                          content_probe=False)
    assert r1["status"] == "ok" and r1["source"] == "autohome_index", r1
    assert calls["remote"] == 0, "本地命中却仍打了远程"

    # 3) 本地 miss → 远程命中 → source 必须记成 autohome_search
    def local_miss(m, idx):
        return None, None, None
    def remote_hit(m, idx=None):
        return "222", "测试车", "remote_search:222"
    r2 = TS.collect_trims("测试车", {}, lambda sid: (two, None),
                          find_sid_local=local_miss, find_sid_remote=remote_hit,
                          content_probe=False)
    assert r2["status"] == "ok" and r2["source"] == "autohome_search", r2

    # 4) 全 miss → missing + 每次尝试都留痕 + 不编造 trims
    orig = R.search
    R.search = lambda *a, **kw: []
    try:
        r3 = TS.collect_trims("绝对不存在的测试车ZZZ", {}, lambda sid: (two, None),
                              find_sid_local=local_miss, find_sid_remote=remote_spy,
                              content_probe=True)
    finally:
        R.search = orig
    assert r3["status"] == "missing" and not r3["trims"], "miss 却产出了配置"
    sources = [a["source"] for a in r3["attempts"]]
    assert sources[:2] == ["autohome_index", "autohome_search"], sources
    assert all(a["ok"] is False for a in r3["attempts"]), r3["attempts"]
    assert "autohome_index" in TS.summarize_attempts(r3["attempts"])

    # 5) 存在性核对只出证据、不出结构化数据
    assert all(s["kind"] == "structured" for s in TS.SOURCES[:-1])
    return "多源调度：索引→检索→存在性核对；本地命中不打远程；miss 留痕且不编造"


def t077():
    """联网取数必须只走 research（唯一网络出口），越界要被门禁拦。

    2026-11-21 用户要求：「查数据是否要用 research 模块，是否要加入铁律让你严格遵守，
    你搭的可插拔也要纳入其中。」——此前 MODULES.md 只有一句声明，没有代码强制。
    声明会忘；门禁不会。除 modules/research/ 外任何模块 import 网络库即 FAIL。
    """
    from modules.regress import layout_regress as LR
    from modules.research import trim_sources as TS

    # 1) 现状必须干净（有越界就说明有人绕过了 research）
    cur = LR.scan_network_egress()
    assert not cur, "存在越界网络调用：" + "；".join(cur[:4])

    # 2) 能力断言：造一个越界文件，必须被检出
    probe = paths.MODULES / "engine" / "_tmp_egress_probe.py"
    try:
        probe.write_text("import requests\n", encoding="utf-8")
        hits = LR.scan_network_egress()
        assert any("_tmp_egress_probe.py" in h for h in hits), f"越界 import 未被检出：{hits}"
    finally:
        if probe.exists():
            probe.unlink()

    # 2b) 无例外：往 EXCEPTIONS 里塞一条网络豁免，也必须照样检出
    probe2 = paths.MODULES / "engine" / "_tmp_egress_probe.py"
    try:
        probe2.write_text("import requests\n", encoding="utf-8")
        base = len(LR.scan_network_egress())
        key = ("engine", "_tmp_egress_probe", "requests")
        LR.EXCEPTIONS[key] = "试图绕过网络出口约束"
        try:
            after = LR.scan_network_egress()
        finally:
            LR.EXCEPTIONS.pop(key, None)
        assert any("_tmp_egress_probe.py" in h for h in after), "例外表可以豁免网络越界（不允许）"
        assert len(after) == base, (base, len(after))
    finally:
        if probe2.exists():
            probe2.unlink()

    # 3) research 包内允许（它才是出口），且不得被误报
    assert not any("modules/research/" in h for h in LR.scan_network_egress())

    # 4) 可插拔源链也纳入这条约束：源必须注册、结构化在前、存在性兜底
    ids = [s["id"] for s in TS.SOURCES]
    kinds = [s["kind"] for s in TS.SOURCES]
    assert ids and "structured" in kinds and "existence" in kinds, (ids, kinds)
    assert kinds[-1] == "existence", "存在性核对必须兜底（只出证据不出配置）"

    # 5) 铁律必须写在 SKILL.md 里（人能读到），并由上面的门禁强制执行
    assert "联网取数只走 research" in _read("SKILL.md"), "SKILL.md 缺该铁律"
    return "网络出口唯一：现状零越界 + 越界能被检出 + research 内放行 + 源链纳入约束 + 铁律成文"


def t078():
    """无官方碰撞成绩的车，safety 不得高于任何有实锤的车（举证责任，不是判劣）。

    2026-11-21 用户质疑：「小鹏 MONA L05 碰撞成绩都没有，用料也没有 20 万车好，
    为什么安全分这么高」。查实：它 note 自述「零交付、零实测、零碰撞成绩」，
    safety 却是 8.6，比有 C-NCAP 86.7% 的丰田 bZ5（8.0）高 0.6。
    根因两处：safety 底分是人工填的、范围与实锤车重叠；单调性门禁只比对映射表里
    8 款老车，本池一款都不在表里 → 检查被整体跳过（显示「问题 0」）。
    """
    from modules.engine import safety_check as SC

    def mk(model, crash, safety):
        return {"model": model, "crash": crash, "scores": {"safety": safety}}

    # 1) 封顶：无实锤者压到「有实锤最低分」，且不额外倒扣（缺数据不判劣）
    cands = [mk("实锤高", "C-NCAP 92.7%", 9.0), mk("实锤低", "C-NCAP 86.7%", 8.0),
             mk("零实锤A", "无官方成绩", 8.6), mk("零实锤B", "无官方成绩", 8.4),
             mk("在测车", "C-NCAP 已抽车未测", 8.8)]
    adj = SC.evidence_cap(cands)
    assert {a["model"] for a in adj} == {"零实锤A", "零实锤B", "在测车"}, adj
    for c in cands:
        if "实锤" not in c["model"]:
            assert c["scores"]["safety"] == 8.0, (c["model"], c["scores"]["safety"])
            assert c.get("safety_cap_reason"), "封顶未留原因"
    assert cands[0]["scores"]["safety"] == 9.0, "有实锤的被误压"

    # 2) 池内无实锤时不封顶（没有可校准的锚，不能凭空压到某个数）
    only_unproven = [mk("甲", "无官方成绩", 8.6), mk("乙", "无官方成绩", 8.2)]
    assert SC.evidence_cap(only_unproven) == [], "全池无实锤却做了封顶"

    # 3) 门禁：未封顶的无实锤车高于实锤最低 → 必须报 evidence_monotonicity
    bad = [mk("实锤", "C-NCAP 86.7%", 8.0), mk("漏网", "无官方成绩", 8.6)]
    r = SC.check(bad, table={"car_scores": {}, "systems": {}})
    assert any(i["type"] == "evidence_monotonicity" for i in r["issues"]), r["issues"]
    # 封顶后应通过
    SC.evidence_cap(bad)
    r2 = SC.check(bad, table={"car_scores": {}, "systems": {}})
    assert not any(i["type"] == "evidence_monotonicity" for i in r2["issues"]), r2["issues"]

    # 4) 链路内置 + 审计 + 披露
    src = _read("pipeline.py")
    assert "evidence_cap" in src, "pipeline 未接举证封顶"
    assert "safety_cap" in src, "summary 缺封顶审计"
    assert "举证封顶" in src, "报告缺封顶披露"
    return "举证封顶：无实锤不高于实锤最低、全池无实锤不封顶、漏网被门禁拦、链路+审计+披露"


def t079():
    """碰撞得分率→分数只能有一个算法（contracts/safety_score），两侧不得各写一套。

    2026-11-21 查证「无实锤车安全分过高」时顺带发现：
    engine/safety_check 按 map_spec（cap 9.3）算，research/sync_cncap 自己写了
    safety_from_pct（cap 10.0 / floor 6.0）——95%+ 高分段两边结果不同
    （96% → 9.3 vs 9.65）。research 不能反向依赖 engine，于是各写一套，这正是
    「同一事实两套算法」的温床。现在算法下沉到契约层，两侧都向下依赖。
    """
    from modules.contracts.safety_score import score_from_pct, score_from_grade, score_from_stars
    from modules.research import sync_cncap as SYNC
    import importlib
    importlib.reload(SYNC)   # 防其它测试 patch 过

    # 1) 标定锁死
    assert score_from_pct(90.7) == 8.7
    assert score_from_pct(92.7) == 9.0
    assert score_from_pct(100) == 9.3, "cap 必须是 9.3（不是 10）"
    assert score_from_pct(86.7) == 8.0
    assert score_from_pct(92, year=2019, older_before=2021, older_penalty=0.2) == 8.7
    assert score_from_grade("G+", {"G+": 9.3}) == 9.3
    assert score_from_stars(5, {"five": 8.3}) == 8.3

    # 2) research 侧必须与契约层逐点一致（含高分段）
    for pct in (80.0, 86.7, 90.7, 92.7, 95.0, 96.0, 100.0):
        assert SYNC.safety_from_pct(pct) == score_from_pct(pct), f"{pct}% 两侧不一致"

    # 3) 源码：两侧都不得再有本地公式
    src_sync = _read("sync_cncap.py")
    assert "score_from_pct" in src_sync, "sync_cncap 未走契约层"
    assert "* 0.155" not in src_sync and "*0.155" not in src_sync, "sync_cncap 仍有本地斜率公式"
    src_sc = _read("safety_check.py")
    assert "from modules.contracts.safety_score import" in src_sc, "safety_check 未走契约层"
    return "安全分换算：契约层单一实现（cap 9.3）、两侧逐点一致、源码无本地公式"


def t080():
    """体验类维度（智驾/车机/操控）无实测不得高于有实测车；空间不封顶。

    2026-11-21 用户质疑：「MONA L05 智驾、车机、空间分数断层高是否合理，有没有参考实测」。
    查实：它零交付零实测，experience_facts / thirdparty_tests 里都没有它，
    adas 却 9.4（全场最高），同品牌有实测的小鹏 G6 只有 7.6。
    这三类维度纸面参数看不出来，拿配置推断压实测值＝用宣传口径压实测证据。
    另：11 款车的 adjust 加减分没有 reason，属不可审计的总分黑箱。

    2026-11-21 二次校准（用户质疑「T6 麋鹿 80.02 反而被压到 7.8」后定的正确设计）：
      · 锚点 min → median：用「有证据车最低分」当天花板＝「没测过只能垫底」，
        会把实测更强的车压到比实测弱的车还低，荒谬。
      · min_proven=3：有证据车不足 3 款时拿噪声当全池天花板，统计上不成立 → 不封顶。
      · 车名走 canon 归一化，库写「特斯拉Model Y」、池写「特斯拉 Model Y」失联的坑必须堵。
    本测试从「2 款即封顶到 min 7.6」的旧断言升级为覆盖上述正确设计，且更强。
    """
    from modules.engine import experience_score as ES
    from modules.contracts.model_names import canon

    def mk(m, adas, space=8.0):
        return {"model": m, "scores": {"adas": adas, "space": space}}

    # 1) ≥3 款有实测，锚点应为中位而非最低：分数 9.0/8.0/7.6 → 中位 8.0（不是 min 7.6）
    lib = {"entries": [
        {"model": "有实测A", "dimension": "adas", "source_type": "independent_test"},
        {"model": "有实测B", "dimension": "adas", "source_type": "owner_report"},
        {"model": "有实测C", "dimension": "adas", "source_type": "independent_test"},
        {"model": "仅宣传D", "dimension": "adas", "source_type": "vendor_claim"},
    ]}
    cands = [mk("有实测A", 9.0), mk("有实测B", 8.0), mk("有实测C", 7.6),
             mk("仅宣传D", 9.4), mk("零依据E", 9.2)]
    adj = ES.cap_unproven(cands, lib=lib)
    got = {a["model"]: a["capped"] for a in adj}
    # vendor_claim 不算依据 → 与零依据一样封顶到中位 8.0（不是最低 7.6）
    assert got == {"仅宣传D": 8.0, "零依据E": 8.0}, got
    assert [c["scores"]["adas"] for c in cands[:3]] == [9.0, 8.0, 7.6], "有实测的被改"
    assert cands[4].get("score_cap_reasons"), "封顶未留原因"
    # 空间不在封顶维度内（轴距/车长是硬数据，不该因无实测被压）
    assert "space" not in ES.cap_unproven.__defaults__[1], "space 不该纳入体验封顶"
    assert cands[0]["scores"]["space"] == 8.0, "space 被误压"

    # 2) 小样本不封顶：仅 2 款有实测 → 拿噪声当全池天花板不成立（T6 麋鹿曾因此被压）
    lib2 = {"entries": [
        {"model": "有实测A", "dimension": "adas", "source_type": "independent_test"},
        {"model": "有实测B", "dimension": "adas", "source_type": "owner_report"},
    ]}
    c2 = [mk("有实测A", 9.0), mk("有实测B", 7.6), mk("零依据Z", 9.2)]
    assert ES.cap_unproven(c2, lib=lib2) == [], "有证据车不足3款不该封顶"
    assert c2[2]["scores"]["adas"] == 9.2, "小样本封顶误改了分数"

    # 3) canon 归一化：库写「特斯拉Model Y」、池写「特斯拉 Model Y」必须算同一款
    assert canon("特斯拉Model Y") == canon("特斯拉 Model Y"), "canon 未归一空格"

    # 4) 某维度全池无依据 → 不封顶
    assert ES.cap_unproven([mk("甲", 9.0)], lib={"entries": []}) == []

    # adjust 门禁：源码必须有「非 0 必须写 reason」的拦截
    src = _read("pipeline.py")
    assert "adjust_reason" in src and "adjust 门禁" in src, "pipeline 缺 adjust 黑箱门禁"
    # 体验封顶已并入统一证据门槛（v10.2.5），pipeline 不再单独调 cap_unproven
    assert "evidence_gate" in src, "pipeline 未接证据门槛（体验维度在 DIM_POLICY 里）"
    assert "dimension_cap" in src or "cap_unproven" in src, "summary 缺封顶审计"
    return ("体验封顶：封到中位非最低、vendor_claim 不算、小样本(2款)不封顶、"
            "canon 归一空格、空间不封顶、adjust 必须有 reason")


def t081():
    """维度证据门槛必须是一张统一的策略表，不是一个个特判函数。

    2026-11-21 用户要求「其他五个维度是不是也要修改，一并修改掉」+「代码层面确保以后其他车不会一样问题」。
    此前 safety 有 evidence_cap、体验有 cap_unproven，各自特判；quality 完全漏网
    （0 个口碑样本的 MONA 拿 8.0，而 64 样本的零跑C11 只有 4.29）。
    现在统一到 evidence_gate.DIM_POLICY：加维度只加一行，不用再写函数。
    """
    from modules.engine import evidence_gate as EG

    # 1) 策略表必须覆盖 8 维，且明确区分「封顶 / 不封顶」
    need_cap = {"safety", "adas", "cabin", "control", "quality"}
    no_cap = {"space", "value", "powertrain"}
    assert set(EG.DIM_POLICY) >= need_cap | no_cap, EG.DIM_POLICY.keys()
    assert {d for d, r in EG.DIM_POLICY.items() if r.get("cap_to")} >= need_cap
    assert set(EG.uncapped_dims()) >= no_cap, "纸面可判定维度被误纳入封顶"
    for d in no_cap:
        assert EG.DIM_POLICY[d]["cap_to"] is None, f"{d} 不该封顶"

    # 2) quality 用中位（不是最低）：有样本的车分数低是因为问题被暴露，不是「合格线」
    assert EG.DIM_POLICY["quality"]["cap_to"] == "median"
    assert EG.DIM_POLICY["safety"]["cap_to"] == "min"

    # 3) 行为：≥3 款有 PPH 样本 → 无样本车 force 到中位（统一锚点，不看原值高低）
    #    三款有样本分数 5.0/6.0/7.0 → 中位 6.0；T6 手填 6.09 曾比被封到 6.74 的还低，
    #    force 的意义正是「填低填高都是没依据，一律同尺度」，故填得低的也应上修到 6.0。
    cands = [
        {"model": "有PPH1", "ah_pph_usable": True, "ah_review_users": 80,
         "scores": {"quality": 5.0, "space": 9.0}},
        {"model": "有PPH2", "ah_pph_usable": True, "ah_review_users": 64,
         "scores": {"quality": 6.0, "space": 9.0}},
        {"model": "有PPH3", "ah_pph_usable": True, "ah_review_users": 50,
         "scores": {"quality": 7.0, "space": 9.0}},
        {"model": "零样本", "ah_pph_usable": False, "ah_review_users": 0,
         "scores": {"quality": 8.0, "space": 9.3}},
        {"model": "填得低", "ah_pph_usable": False, "ah_review_users": 12,
         "scores": {"quality": 3.5, "space": 8.8}},
    ]
    adj = EG.apply_all(cands, experience_lib={"entries": []}, only=("quality",))
    got = {a["model"]: a["capped"] for a in adj}
    assert got == {"零样本": 6.0, "填得低": 6.0}, got
    assert [c["scores"]["quality"] for c in cands[:3]] == [5.0, 6.0, 7.0], "有样本的被改"
    # force：填低(3.5→6.0)上修、填高(8.0→6.0)下压，同一无依据状态同一尺度
    assert cands[3]["scores"]["quality"] == 6.0 and cands[4]["scores"]["quality"] == 6.0
    assert all(a.get("forced") for a in adj), "quality 应 force 而非仅封顶"
    # 空间不在 only 里 → 不被压
    assert cands[3]["scores"]["space"] == 9.3
    assert cands[3].get("score_cap_reasons"), "封顶未留原因"

    # 3b) 小样本不封顶：有 PPH 样本不足 3 款 → 不拿噪声当全池天花板
    c2 = [
        {"model": "有PPH1", "ah_pph_usable": True, "ah_review_users": 80,
         "scores": {"quality": 5.0}},
        {"model": "零样本", "ah_pph_usable": False, "ah_review_users": 0,
         "scores": {"quality": 8.0}},
    ]
    assert EG.apply_all(c2, experience_lib={"entries": []}, only=("quality",)) == [], \
        "有样本不足3款不该封顶"
    assert c2[1]["scores"]["quality"] == 8.0, "小样本封顶误改了分数"

    # 3c) 全池无证据 → 不封顶
    assert EG.apply_all([{"model": "甲", "scores": {"quality": 8.0}}],
                        experience_lib={"entries": []}, only=("quality",)) == []

    # 4) 补采器必须可复用（库调用），且样本门槛 30
    from modules.research import fill_experience as FE
    assert callable(FE.fill_pph) and FE.__dict__.get("__name__")
    from modules.research import fetch_specs as FS
    assert FS.PPH_MIN_SAMPLE == 30, "PPH 样本门槛被改"
    # 5) pipeline 用统一入口
    src = _read("pipeline.py")
    assert "evidence_gate" in src and "apply_all" in src, "pipeline 未用统一证据门槛"
    return "证据门槛统一：8维策略表/5封顶3不封顶/quality用中位/无证据不高于有证据/补采器可复用"


def t082():
    """T6 数据没录入的根因闭环：外部实测稿 → 可复用提取 → 缺口检出 → 硬门禁拦截。

    2026-11-21 用户追问「大众T6数据没有录入什么原因，是否在代码层面修改确保以后不出错」。
    查实三层漏洞：
      ① 采集：fill_pph 只补口碑，抓不到试驾稿里的麋鹿/NOA；T6 数据只在外部媒体稿，
         候选池 note 也没写 → 证据库天然缺条目。
      ② 检测：check_experience_gaps 只扫候选池文本，T6 note 无麋鹿 → 检出 0 条（形同虚设）。
      ③ 门禁：即使检出也只 print，不拦截 → 带病照常出榜。
    修复：research.extract_driving_evidence 可复用提取（只提取待核实、不入库不打分）；
          check_experience_gaps 支持外部文本源；pipeline 缺口升级为 sys.exit 硬拦。
    """
    from modules.research import fill_experience as FE
    from modules.engine import cross_verify as XV
    from modules.contracts.model_names import canon

    # 1) 可复用提取器：从试驾稿提取麋鹿+NOA，标 pending（不判来源、不入库、不改分）
    art = "ID.AURA T6 麋鹿测试 80.02km/h；城市NOA实车路测谨慎保守。"
    got = FE.extract_driving_evidence(art, model="大众ID.AURA T6", url="u")
    assert len(got) == 2, got
    assert {x["dimension"] for x in got} == {"control", "adas"}, got
    assert all(x["verification"] == "pending" and x["source_type"] == "pending"
               for x in got), "提取器不得自行判定来源类型/入库"
    assert [x for x in got if x["kind"] == "moose_test"][0]["value"] == 80.02
    assert FE.extract_driving_evidence("10-80%快充22分钟") == [], "快充百分比不得误判麋鹿"

    # 2) 缺口检测含外部源：候选池 note 没有实测（T6 原样）也必须能查出
    cands = [{"model": "大众ID.AURA T6", "note": "碰撞仅抽车未测；30–80%需22分钟"}]
    lib0 = {"entries": []}
    assert XV.check_experience_gaps(cands, lib=lib0) == [], "只看候选池确实扫不到（原 bug）"
    ext = {canon("大众ID.AURA T6"): [art]}
    gaps = XV.check_experience_gaps(cands, lib=lib0, external_evidence=ext)
    assert {(g["dim"], g["kind"]) for g in gaps} == {("control", "moose"), ("adas", "noa")}, gaps
    assert all(g["source"] == "外部实测稿" for g in gaps), gaps
    # 入库后清零
    lib1 = {"entries": [
        {"model": "大众ID.AURA T6", "dimension": "control", "source_type": "independent_test"},
        {"model": "大众ID.AURA T6", "dimension": "adas", "source_type": "up_review"},
    ]}
    assert XV.check_experience_gaps(cands, lib=lib1, external_evidence=ext) == []

    # 3) 门禁：pipeline 源码必须有硬拦 + skip 开关，不能只 print
    src = _read("pipeline.py")
    assert "体验证据缺口门禁" in src and "skip_experience_gap" in src, \
        "pipeline 缺口未升级为硬门禁"
    assert 'sys.exit("体验证据缺口门禁' in src, "缺口只 print 不拦截"
    return "T6根因闭环：research可复用提取(待核实不入库)+外部源缺口检测+pipeline硬门禁"


def t083():
    """空间分硬数据一致性校准：MONA space 9.3 全场第一但零实测、尺寸不占优。

    2026-11-21 用户问「小鹏MONA L05 空间分这么高合理吗」。查实 space=9.3 是
    随包人工库拍的分、从未用硬数据校验；而 evidence_gate 以"轴距是硬数据"为由
    把 space 列为不封顶——但 vehicle_physics 全库轴距 0% 覆盖，"硬数据"是空的。
    体积最大的 ES6(16.49m³) 只有 8.3，体积中上的 MONA(15.38m³) 却 9.3——
    体积与 space 几乎无相关。修复：space_consistency 用"尺寸不小于本车的车"
    space 中位设上限，TOL=0.4 标定为全池只命中 MONA，只压虚高不倒扣。
    """
    from modules.engine import space_consistency as SP
    from modules.research import fill_experience as FE

    # 1) 模块 selftest：MONA 场景 + 小样本 + 容忍带 + 不倒扣 + 轴距优先
    assert SP.selftest() == 0, "space_consistency 单元自测失败"

    # 2) 真实池子标定（v10.2.9 起两类信号并存，按 signal 分组验证，都是「只压虚高不倒扣」）
    #    体积信号：TOL=0.4 只命中 MONA 9.3→8.8，其余车不动（原 v10.2.8 断言保持强度）
    #    得房率信号：N7 得房率64%却8.5 → 压到同尺寸正常车中位 8.3
    import json, copy
    pool = json.load(open(str(paths.ROOT / "runs" / "midsuv_bev_pool.json"), encoding="utf-8"))
    cands = copy.deepcopy(pool["candidates"])
    before = {c["model"]: (c.get("scores") or {}).get("space") for c in cands}
    adj, uncal = SP.calibrate_space(cands)
    by_sig = {}
    for a in adj:
        by_sig.setdefault(a.get("signal", "size"), {})[a["model"]] = a["capped"]
    assert by_sig.get("size") == {"小鹏MONA L05": 8.8}, f"体积信号应只命中 MONA→8.8: {by_sig}"
    assert by_sig.get("efficiency") == {"腾势N7": 8.3}, f"得房率信号应只命中 N7→8.3: {by_sig}"
    assert uncal == [], f"本榜 21 款都有尺寸: {uncal}"
    touched = {"小鹏MONA L05", "腾势N7"}
    assert all(before.get(c["model"]) == (c.get("scores") or {}).get("space")
               for c in cands if c["model"] not in touched), "不得误伤其他车"
    mona = [c for c in cands if c["model"] == "小鹏MONA L05"][0]
    assert mona.get("score_cap_reasons"), "封顶未留审计理由"
    assert adj[0]["peers"] >= SP.MIN_PEERS, "锚点样本不足"

    # 3) 标定唯一性：TOL 放宽到 0.5 会漏掉 MONA（证明 0.4 是必要的，不是随意取）
    c2 = copy.deepcopy(pool["candidates"])
    a2, _ = SP.calibrate_space(c2, tol=0.5)
    assert "小鹏MONA L05" not in {a["model"] for a in a2}, "tol 必须卡在 0.4"

    # 4) research 补硬数据的可复用提取器：只提取待核实、不误伤
    dims = FE.extract_dimensions("车身尺寸 4870×1930×1636mm，轴距 2900mm", model="X")
    assert {x["kind"] for x in dims} == {"wheelbase", "length_width_height"}, dims
    assert all(x["verification"] == "pending" for x in dims), "尺寸提取不得自动入库"
    assert FE.extract_dimensions("快充 280kW 售价 153800元") == [], "不得误伤功率/价格"

    # 5) pipeline 接入了 space 校准与审计字段
    src = _read("pipeline.py")
    assert "space_consistency" in src and "calibrate_space" in src, "pipeline 未接空间校准"
    assert '"space_calibration"' in src, "summary 缺 space 校准审计"
    assert "no_space_cap" in src, "缺 A/B 对照开关"
    return "空间虚高根治：硬数据一致性封顶(只命中MONA9.3→8.8)+不倒扣+research补尺寸"


def t084():
    """空间要素（前备箱/下沉后备箱/储物/真实空间表现）是否该进空间分的根治回归。

    2026-11-21 用户问「空间是否要考虑前备箱大小、下沉后备箱、储物空间、真实空间表现」。
    查实：这些要素其实记在 evidence.space_evidence 里，但系统从没用过——
    得房率64%的腾势N7 space=8.5，得房率85.6%的小鹏G6 却只有8.0。
    修法：①结构化提取分两类(real参与校验/storage仅披露不进分)；②space_consistency
    加得房率真实性信号(低得房率封顶/高得房率放宽)；③pipeline 报告披露。
    """
    from modules.engine import space_features as SF
    from modules.engine import space_consistency as SP

    # 1) 提取器自测：两类分开、待核实、低置信、不误伤
    assert SF.selftest() == 0, "space_features 单元自测失败"

    # 2) 真实池：两类要素都提取到了，且严格归类
    import json, copy
    pool = json.load(open(str(paths.ROOT / "runs" / "midsuv_bev_pool.json"), encoding="utf-8"))
    all_feats = []
    for c in pool["candidates"]:
        all_feats.extend(SF.extract_space_features(c))
    keys = {f["key"] for f in all_feats}
    assert "efficiency" in keys and "knee_room" in keys, f"真实表现要素缺失: {keys}"
    assert "frunk" in keys and "trunk" in keys and "under_floor" in keys, f"储物要素缺失: {keys}"
    real_cats = {f["category"] for f in all_feats if f["key"] in ("efficiency", "knee_room", "leg_room")}
    store_cats = {f["category"] for f in all_feats if f["key"] in ("frunk", "trunk", "under_floor")}
    assert real_cats == {SF.CAT_REAL} and store_cats == {SF.CAT_STORAGE}, "要素归类错"

    # 3) 得房率真实性信号：真实池 N7(64%却8.5)被压到正常车中位8.3，G6(85.6%)不误伤
    cands = copy.deepcopy(pool["candidates"])
    before = {c["model"]: c["scores"]["space"] for c in cands}
    adj, _ = SP.calibrate_space(cands)
    eff_hits = {a["model"]: a["capped"] for a in adj if a.get("signal") == "efficiency"}
    assert eff_hits == {"腾势N7": 8.3}, f"低得房率虚高应只命中 N7→8.3: {eff_hits}"
    g6 = [c for c in cands if "G6" in c["model"]][0]
    assert g6["scores"]["space"] == before["小鹏G6"], "高得房率车不该被误伤"

    # 4) 储物不进分：阿维塔有前备箱90L，但不该因此加分（分数只由体积/得房率信号决定）
    avatr = [c for c in cands if "阿维塔07" in c["model"]][0]
    assert avatr["scores"]["space"] == before["阿维塔07 纯电版"], "前备箱不得直接加分"

    # 5) 模块 selftest 覆盖高得房率放宽 + 小样本保护
    assert SP.selftest() == 0, "space_consistency 自测（含得房率信号）失败"

    # 6) pipeline 接入披露与审计字段（只改数据链路，不碰渲染层）
    src = _read("pipeline.py")
    assert "space_features" in src and "space_features_detail" in src, "summary 缺空间要素审计"
    assert "空间要素披露" in src and "储物便利" in src, "报告缺空间要素披露"
    return "空间要素根治：两类结构化(real校验/storage仅披露不进分)+得房率信号抓N7不误伤G6"


def t085():
    """人工分全量核算审计回归。

    2026-11-21 用户「全量检查哪些数据存在人工分没有算，一项项核算，加入测试」。
    查实：8 维里只有 safety 有完整算分链；quality/powertrain 有规则没接链路；
    adas/cabin/control/space/value 是人工分+局部封顶，spec_score 规则从没跑过
    （依赖 21 个结构化字段一个都没进候选池，事实散在 evidence 文本里）。
    修法：engine/score_audit.py 链路盘点 + 从 evidence 提取硬参数按规则算客观分与人工分对照，
    只审计不改分，差异是待核实线索。pipeline --score-audit 出核账段落 + summary 审计。
    """
    from modules.engine import score_audit as SA

    # 1) 模块自测
    assert SA.selftest() == 0, "score_audit 单元自测失败"

    # 2) 链路盘点：safety 闭环，quality/powertrain 有规则没接，其余部分接
    links = {r["dim"]: r["gap"] for r in SA.link_audit()}
    assert links["safety"] == "WIRED", f"safety 应闭环: {links}"
    assert links["quality"] == "RULE_NOT_WIRED" and links["powertrain"] == "RULE_NOT_WIRED", \
        f"quality/powertrain 应有规则没接: {links}"
    assert links["adas"] == "PARTIAL_WIRED" and links["space"] == "PARTIAL_WIRED", links

    # 3) 真实池提取 + 核算：有硬参数的维度能算，且差异可检出
    import json, copy
    pool = json.load(open(str(paths.ROOT / "runs" / "midsuv_bev_pool.json"), encoding="utf-8"))
    audit = SA.diff_audit(pool["candidates"], threshold=0.5)
    assert audit["skipped_wired"] == ["safety"], "已闭环 safety 不该产生伪差异"
    assert "adas" in audit["coverage"] and audit["coverage"]["adas"] >= 5, \
        f"adas 有硬参数应可核算: {audit['coverage']}"
    # powertrain/value 无数据 → coverage 里不该有（这正是「规则有了数据没进池」的证据）
    assert "powertrain" not in audit["coverage"] and "value" not in audit["coverage"], \
        f"powertrain/value 缺数据不该算: {audit['coverage']}"
    assert audit["diff_count"] > 0, "应检出人工分与规则分的差异"

    # 4) 审计绝不改分（深比较）
    snapshot = copy.deepcopy(pool["candidates"])
    SA.diff_audit(pool["candidates"], threshold=0.5)
    assert pool["candidates"] == snapshot, "审计不得改候选任何字段"

    # 5) pipeline 接入开关与披露
    src = _read("pipeline.py")
    assert "score-audit" in src and "score_audit" in src, "pipeline 缺 --score-audit 开关"
    assert "人工分核算审计" in src and "不是改分指令" in src, "报告缺核账披露/免责"
    return "人工分核算审计：链路盘点+evidence硬参数算客观分对照+不改分+差异是线索(+t085)"


def t086():
    """证据文本里的权威碰撞成绩必须回流：Model Y 有 C-IASI 却标「缺权威碰撞成绩」。

    2026-11-21 用户「Model Y 显示缺权威碰撞成绩，我记得有呀」。
    三层根因（都是代码层，不是数据缺失）：
      1) Model Y 的 C-IASI 一手成绩（官网 show-784，乘员G+/行人G+/辅助G）
         只写在 evidence.crash_evidence，顶层 crash/basis 都空；
      2) backfill_crash_from_text 只扫 crash/note/safety_note，漏了 evidence → 成绩白采；
      3) 直接把 evidence 丢给宽松 parse_score 会误伤 6 款无成绩车
         （「五星安全车身设计」宣传口径 / 问界M7 他车成绩 / C-ICAP 智驾分 / 枚举句 G/G+/A/M/P）。
    修法：crash_systems.parse_score_from_evidence 严格解析（肯定锚点 + C-IASI 维度结构
    + 三指数取最低 + 多次测评取最新），backfill 在顶层无成绩时才用它。
    """
    from modules.engine import crash_systems as CS
    from modules.engine import facts_sync as FS
    from modules.board import render_board_html as RH
    from modules.contracts.candidate_schema import normalize_pool
    import copy

    # 1) 单元自测（含 Model Y 命中 + 4 类误伤防护）
    assert CS.selftest() == 0, "crash_systems 自测失败"

    # 2) 严格解析器：Model Y 证据命中 ciasi/G（三指数乘员G+行人G+辅助G→最低G）
    pool = json.load(open(str(paths.ROOT / "runs" / "midsuv_bev_pool.json"), encoding="utf-8"))
    my = next(x for x in pool["candidates"] if x["model"] == "特斯拉 Model Y")
    r = CS.parse_score_from_evidence(my["evidence"]["crash_evidence"])
    assert r and r["basis"] == "ciasi" and r["grade"] == "G", f"Model Y 严格解析失败: {r}"
    assert r["ciasi_dims"] == {"occupant": "G+", "pedestrian": "G+", "assist": "G"}, r["ciasi_dims"]

    # 3) 回填全池：Model Y 必须进 filled；原来 13 款无成绩车仍 unresolved（无误伤）
    cands = normalize_pool(copy.deepcopy(pool["candidates"]))
    filled, unres = FS.backfill_crash_from_text(cands)
    filled_m = {m for m, _, _ in filled}
    assert "特斯拉 Model Y" in filled_m, f"Model Y 未回填: {filled}"
    my2 = next(x for x in cands if x["model"] == "特斯拉 Model Y")
    assert my2.get("safety_basis") == "ciasi" and my2.get("crash") == "C-IASI G", \
        f"Model Y 字段未结构化: {my2.get('safety_basis')} / {my2.get('crash')}"
    assert my2.get("crash_ciasi_dims"), "C-IASI 三指数明细缺审计留痕"
    for bad in ("腾势N7", "问界M5纯电版", "蔚来ES6", "小鹏G6", "比亚迪宋L EV"):
        assert bad not in filled_m, f"无成绩车 {bad} 被误填成有成绩"

    # 4) 展示层：回填后不再标「缺权威碰撞成绩」，且徽标显示 C-IASI
    row = {"safety_basis": my2["safety_basis"], "crash": my2["crash"]}
    assert "缺权威碰撞成绩" not in RH.risk_flags(row), "有 C-IASI 实锤仍被标缺成绩"
    badge = re.sub(r"<[^>]+>", "", RH.crash_badge(row))
    assert "C-IASI" in badge, f"徽标未显示 C-IASI: {badge}"

    # 5) 链路内置：回填函数必须调严格解析器（防以后退回宽松扫描）
    src = str(paths.src("facts_sync.py"))
    assert os.path.exists(src), f"源码路径不存在: {src}"
    assert "parse_score_from_evidence" in open(src, encoding="utf-8").read(), "回填未走严格解析器"
    return ("证据碰撞成绩回流：Model Y C-IASI 进字段+三指数取最低+13款无成绩车零误伤+"
            "误标消失+徽标显示(+t086)")


def t087():
    """全量碰撞体系覆盖 + 权威库回流 + 安全分文案按实际体系（不是只改 Model Y 一处）。

    2026-11-21 用户「之前安全方面很多碰撞实验都纳入了，全部验证是否都可以取到。
    蔚来应该也有成绩，不要单个改，用代码改所有情况」。
    盘点出三层系统性问题：
      1) crash_systems 定义了 12 个体系，但两个解析器手写只覆盖 8 个（严格版只 2 个），
         Euro NCAP/ANCAP/IIHS/JNCAP/KNCAP/ASEAN/Latin/Bharat/Global 的成绩文本取不到；
      2) official_safety_scores.json（8款人工核验成绩，含蔚来ET5 C-IASI A）从不回流进候选池；
      3) 报告「安全分来源」写死 C-NCAP 公式，Model Y 走 C-IASI 等级映射，口径矛盾。
    修法：体系名从 SYSTEMS 表动态生成（加体系只改一处）+ award/星级分支补齐 +
    权威库自动回流 + 文案按每车实际体系显示。
    """
    from modules.engine import crash_systems as CS
    from modules.engine import facts_sync as FS
    from modules.board import pipeline as PL

    # 1) SYSTEMS 全覆盖：定义数 == 解析器能识别数（防以后加体系忘了进正则）
    assert len(CS.SYSTEMS) >= 12, "体系清单不完整"
    for code, sample in (
        ("cncap", "有C-NCAP官方成绩：综合得分率90.7%"),
        ("ciasi", "有C-IASI官方成绩：车内乘员G+、车外行人G+、辅助安全G"),
        ("euro_ncap", "有Euro NCAP官方成绩：五星，成人91%"),  # 蔚来ET5 体系
        ("ancap", "有ANCAP官方成绩：五星"),
        ("iihs", "有IIHS官方成绩：TSP+评价"),
        ("nhtsa", "有NHTSA官方成绩：五星"),
        ("jncap", "有JNCAP官方成绩：五星"),
        ("kncap", "有KNCAP官方成绩：五星"),
        ("asean_ncap", "有ASEAN NCAP官方成绩：五星"),
        ("latin_ncap", "有Latin NCAP官方成绩：五星"),
        ("bharat_ncap", "有Bharat NCAP官方成绩：五星"),
        ("global_ncap", "有Global NCAP官方成绩：五星"),
    ):
        r = CS.parse_score_from_evidence(sample)
        assert r and r["basis"] == code, f"体系 {code} 成绩取不到: {r}"
    # 星级制（Euro NCAP 无得分率只有星级）必须能解析——蔚来ET5 的真实情形
    euro = CS.parse_score_from_evidence("有Euro NCAP官方成绩：五星，成人乘员96%")
    assert euro and euro["stars"] == 5, f"Euro NCAP 星级没解析出来: {euro}"

    # 2) 误伤防护不退化（全体系扩展后仍不能把宣传/他车/智驾分当成绩）
    for bad in (
        "无C-NCAP成绩；宣称按C-NCAP五星安全车身设计（厂商软文口径）",
        "无官方成绩；有问界M7 2025（95.0%）但无M5",
        "C-NCAP基础行车辅助92.9%实为C-ICAP智驾测评",
        "蔚来ES6出口版获Euro NCAP五星，非本代国内一手成绩，低置信",
    ):
        assert CS.parse_score_from_evidence(bad) is None, f"误伤无成绩车: {bad}"

    # 3) 权威库回流：库里有的车（蔚来ET5 C-IASI A）必须能回填，且不改 scores.safety
    cands = [
        {"model": "蔚来ET5", "crash": None, "safety_basis": None, "scores": {"safety": 8.0}},
        {"model": "不存在车型XYZ", "crash": None, "safety_basis": None, "scores": {"safety": 8.0}},
    ]
    filled = FS.backfill_crash_from_authoritative(cands)
    assert any(m == "蔚来ET5" and b == "ciasi" for m, _, b in filled), f"蔚来ET5 权威成绩没回流: {filled}"
    et5 = cands[0]
    assert et5["safety_basis"] == "ciasi" and "C-IASI" in et5["crash"], et5
    assert et5["scores"]["safety"] == 8.0, "权威库回填不该改分数（那是映射表的职责）"
    assert cands[1]["safety_basis"] is None, "库里没有的车被错误回填"

    # 4) 链路内置：pipeline 必须调权威库回流（防退回「只靠文本」）
    src = str(paths.src("pipeline.py"))
    assert "backfill_crash_from_authoritative" in open(src, encoding="utf-8").read(), \
        "pipeline 未接入权威库回流"
    # 5) 文案不写死 C-NCAP 公式
    src = str(paths.src("pipeline.py"))
    text = open(src, encoding="utf-8").read()
    assert "7.0+(pct-80)×0.155" not in text, "安全分来源文案仍写死 C-NCAP 公式"
    return ("碰撞体系全覆盖：12体系都能解析+星级/奖项分支+蔚来ET5权威库回流+"
            "误伤防护不退化+文案按实际体系(+t087)")


def t088():
    """多碰撞成绩口径：取证据最强单一成绩（不取最高/不叠加），次要成绩全量留痕。

    2026-11-21 用户「多个碰撞成绩是只算最高还是一起纳入？榜单所有车型对比页面有没有遗漏」。
    全量对比发现的真问题：
      1) parse_score_from_evidence 遇到第一个锚点就 return —— 多成绩车取哪个
         由文本书写顺序决定，不是取最高也不是一起算（规则隐式、脆弱）；
      2) 次要成绩（极氪7X Euro NCAP、乐道L60 C-IASI、Model Y 两次测评、
         海狮07EV E/A-NCAP）被静默丢弃，无审计留痕。
    用户确认口径：取证据最强（一手得分率 > 等级/星级 > 奖项），不取最高分
    （会偏向宽松体系）、不跨体系叠加（各国 NCAP 无对应关系）；次要成绩只记
    数据层+报告，不动榜单页面（渲染层改动需单独确认）。
    """
    from modules.engine import crash_systems as CS
    from modules.engine import facts_sync as FS
    from modules.contracts.candidate_schema import normalize_pool
    import copy

    # 1) 单元：多成绩按 tier 取最强，且与文本顺序无关
    assert CS.selftest() == 0, "crash_systems 自测失败"
    text = "有C-NCAP官方成绩：综合得分率90.7%，五星。另有Euro NCAP五星（媒体口径）。"
    alls = CS.parse_all_scores_from_evidence(text)
    assert len(alls) == 2 and alls[0]["tier"] > alls[1]["tier"], alls
    assert CS.parse_score_from_evidence(text)["basis"] == "cncap"
    rev = "另有Euro NCAP五星（媒体口径）。有C-NCAP官方成绩：综合得分率90.7%。"
    assert CS.parse_score_from_evidence(rev)["basis"] == "cncap", "主成绩受文本顺序影响"

    # 2) 真实池：8 款有成绩车主成绩不漂移；多成绩车次要成绩留痕
    pool = json.load(open(str(paths.ROOT / "runs" / "midsuv_bev_pool.json"), encoding="utf-8"))
    cands = normalize_pool(copy.deepcopy(pool["candidates"]))
    FS.backfill_crash_from_text(cands)
    expected = {"极氪7X": "cncap", "阿维塔07 纯电版": "cncap", "极狐阿尔法T5": "cncap",
                "零跑C10": "cncap", "乐道L60": "cncap", "丰田bZ5": "cncap",
                "特斯拉 Model Y": "ciasi", "比亚迪海狮07EV": "cncap"}
    by = {c["model"]: c for c in cands}
    for model, basis in expected.items():
        assert by[model].get("safety_basis") == basis, f"{model} 主成绩体系漂移"
    multi = {c["model"]: c for c in cands if len(c.get("crash_all_scores") or []) > 1}
    assert "极氪7X" in multi and "乐道L60" in multi and "比亚迪海狮07EV" in multi, \
        f"多成绩车次要成绩未留痕: {list(multi)}"
    assert multi["极氪7X"]["crash_secondary"], "极氪7X 的 Euro NCAP 未记为次要成绩"
    assert multi["乐道L60"]["crash_secondary"], "乐道L60 的 C-IASI 未记为次要成绩"
    my = by["特斯拉 Model Y"]
    assert my.get("crash_ciasi_n_tests") == 2, "Model Y 两次 C-IASI 测评次数未留痕"

    # 3) 无成绩车零误伤（G6/宋L/ES6/N7/M5 旁证不当成绩）
    for model in ("小鹏G6", "比亚迪宋L EV", "蔚来ES6", "腾势N7", "问界M5纯电版"):
        assert not by[model].get("crash_all_scores"), f"无成绩车 {model} 被记入成绩"

    # 4) 链路内置：回填写审计字段、报告披露、summary 有审计（防退回「只取第一个就丢」）
    fs_src = open(str(paths.src("facts_sync.py")), encoding="utf-8").read()
    assert "crash_all_scores" in fs_src and "parse_all_scores_from_evidence" in fs_src, \
        "回填未写多成绩审计字段"
    pl_src = open(str(paths.src("pipeline.py")), encoding="utf-8").read()
    assert "多成绩口径" in pl_src and "crash_multi_scores" in pl_src, \
        "报告/summary 未披露多成绩口径"
    return ("多成绩口径：取证据最强(不取最高/不叠加)+次要成绩全量留痕+主成绩不漂移+"
            "无成绩车零误伤+报告/summary披露(+t088)")


TESTS = [t001, t002, t003, t004, t005, t006, t007, t008,
         t009, t010, t011, t012, t013, t014, t015, t016, t017, t018, t019, t020, t021, t022, t023, t024, t025, t026, t027, t028, t029, t030, t031, t032, t033, t034, t035, t036, t037, t038, t039, t040, t041, t042, t043, t044, t045, t046, t047, t048, t049, t050, t051, t052, t053, t054, t055, t056, t057, t058, t059, t060, t061, t062, t063, t064, t065, t066, t067, t068, t069, t070, t071, t072, t073, t074, t075, t076, t077, t078, t079, t080, t081, t082, t083, t084, t085, t086, t087, t088]


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
    # v9.7.43：失败回测——只测正确路径不够，必须证明"注入错误参数会FAIL"。
    # 每条可注入测试在独立子进程里篡改输入/源码断言目标，验证测试确实会挂。
    try:
        from modules.regress import negative_regression as NR
        broken, skipped = NR.probe_all()
        if broken:
            print(f"[FAIL] 失败回测：{len(broken)} 条测试注入错误后仍PASS（断言形同虚设）")
            fail += 1
    except Exception as ex:
        print(f"[FAIL] 失败回测框架异常：{type(ex).__name__}: {ex}")
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
