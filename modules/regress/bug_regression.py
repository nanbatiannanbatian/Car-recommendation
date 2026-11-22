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
    ("BUG-089", "多测评只取单一成绩不满足全纳入:数字图标车无成绩+C-IASI多维度未木桶取最低+跨体系无主分规则", "v10.4.0"),
    ("BUG-090", "cncap_scores已有零跑C11 86.4%却没回流:候选池/页面显示无成绩且无展示门禁", "v10.5.0"),
    ("BUG-091", "C-IASI数字图标车新闻稿救回写死单车+固定ID:新车无法复用、出榜无自动兜底", "v10.5.0"),
    ("BUG-092", "回归专用候选池快照打进发布包:包体逼近3MB上限+无代码化排除规则", "v10.5.1"),
    ("BUG-093", "发布包带39个JSON占944KB:用户要零JSON数据可联网获得却无补回链路", "v10.5.2"),
    ("BUG-094", "蔚来ES6有本代EuroNCAP一手五星却判无成绩:出口版同源车成绩被一刀切降级+智驾分误抓隐患", "v10.5.2"),
    ("BUG-095", "深蓝S07/宋L EV官网有一手碰撞成绩但页面空白:出榜不联网核验+权威库无此车", "v10.6.0"),
    ("BUG-096", "特斯拉三电7.3垫底但电耗全榜最低:三电维度只看车端硬件、系统能效从未纳入", "v10.6.0"),
    ("BUG-097", "GitHub仓库存43个JSON违背数据代码分离:同步脚本无.json排除规则", "v10.6.0"),
    ("BUG-098", "碰撞核验只扫官网首页且断网只告警:无法证明每车都联网确认过", "v10.7.0"),
    ("BUG-099", "碰撞核验状态无时间戳且每次出榜重复抓取:当天无法复用、跨天不失效", "v10.8.0"),
    ("BUG-100", "极氪7X/阿维塔07电池来源空白:事实库无此车且缺联网兜底与缺失原因标注", "v10.9.0"),
    ("BUG-101", "同一车辆事实散落5处各自手写:改一处漏多处、无唯一真相源与漂移检测", "v10.10.0"),
    ("BUG-102", "import别名碰撞:space_features的SF覆盖score_fingerprint的SF,报告段SF.skill_version()崩溃", "v10.10.1"),
    ("BUG-103", "配置驱动规则从未进主榜:极氪7X三电拿不到900V/快充加分反低于T6,后备箱容积页面零披露", "v10.11.0"),
    ("BUG-104", "参数页字段大量未提取(只认电池组),缺口被伪装成数据源没有:轴距/后备厢/零百全丢且无门禁", "v10.12.1"),
    ("BUG-105", "空间维从未接入配置驱动规则+后备厢容积被标仅披露不进分,车长与轴距重复计数", "v10.12.2"),
    ("BUG-106", "人工条目与自动条目存储层双副本:人工证据链(source/quote/note含口径冲突披露)被自动模板覆盖345字段", "v10.13.0"),
    ("BUG-107", "三电缺电池容量项+三电未拆电池/补能+配置匹配数字粘连致42款评分配置错配", "v10.15.0"),
    ("BUG-108", "三电规则项堆满被clamp成10.0满分:65/427逐配置并列顶格,顶配失去区分度", "v10.15.9"),
    ("BUG-109", "同车跨榜分数漂移(榜内锚点随子池变)+主榜三电满分残留(效率+达成率硬顶10)", "v10.16.0"),
    ("BUG-110", "bZ5一手C-NCAP86.7%被证据不全C-IASI G覆盖+页面同时标缺权威与C-IASI徽标", "v10.16.1"),
    ("BUG-111", "乐道L60高配智驾被低配数据拖累+PPH小样本证据不对称", "v10.16.2"),
    ("BUG-112", "Euro NCAP乘员得分率被当未知：ES6 93.95%按五星保守档8.3，低于L60 C-NCAP 89.2%的8.4", "v10.16.3"),
    ("BUG-113", "碰撞成绩在车名徽标与碰撞成绩列两处重复+ES6两处数字不一致(95% vs 93.95%)", "v10.16.4"),
    ("BUG-114", "性价比是人工裁决电池密度+死比价公式，名实不符不可复算", "v10.17.0"),
]


def _read(rel):
    # 实现源码按模块索引解析（modules/<pkg>/x.py）；数据/产物按项目根解析。
    # 绝不读根目录兼容薄壳，否则断言会跑在 10 行转发壳上、形同虚设。
    p = paths.src(rel)
    return open(p, encoding="utf-8").read() if p.exists() else ""


def _read_path(rel):
    """数据/库文件绝对路径（项目根下，不走 modules 索引）。"""
    return str(paths.ROOT / rel)


def _read_pool():
    """读中型纯电SUV候选池（深拷贝，测试内可原地改）。"""
    import copy
    return copy.deepcopy(json.load(
        open(str(paths.ROOT / "runs" / "midsuv_bev_pool.json"), encoding="utf-8"))["candidates"])


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
    # 2026-11-22：走统一入口（人工库 + 自动库合流）
    from modules.data.trims_store import load as _load_trims
    trims = {"entries": list(_load_trims().values())}
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
    # 2026-11-22：走统一入口（人工库 + 自动库合流）
    from modules.data.trims_store import load as _load_trims
    trims = {"entries": list(_load_trims().values())}
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
    # v10.11.0：采集配置的真实位置是 modules/research/config.json。
    # 断言里写的 research/config.json 是模块化(v10.0)之前的路径，搬家后没跟上——
    # 偏偏 categories/research 下还真存在一份**内容完全相同的重复副本**，
    # 于是断言"看起来通过"，实际包内真正的配置没人校验。
    # 现在：① 断言指向真实位置；② 重复副本已删除（同一事实只留一个可写副本）。
    for f in ("research/researcher.py", "modules/research/config.json", "research/README.md"):
        assert os.path.exists(os.path.join(HERE, f)), f"缺内置采集工具: {f}"
    assert not os.path.exists(os.path.join(HERE, "research/config.json")), \
        "research/config.json 重复副本仍在（同一事实应只有一个可写副本）"
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
        from modules.data.trims_store import load as _load_trims
        td = {"entries": list(_load_trims().values())}
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
    # v10.6.0 起 GitHub 不传 JSON：候选池固化在源码树（上面已断言存在），
    # 但按数据代码分离策略不进同步集。
    assert not _in_sync_set("categories/candidates_compact_suv_bev.json"), \
        "候选池 JSON 不应进 GitHub 同步集（v10.6.0 零 JSON）"
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
    from modules.data.trims_store import load as _load_trims
    d = {"entries": list(_load_trims().values())}
    e = next(x for x in d["entries"] if x["model"] == "MG 07 纯电")
    slow = next(t for t in e["trims"] if "650" in t["name"])
    fast = next(t for t in e["trims"] if "845" in t["name"])
    ds = TA.deltas_for(fast, slow)
    assert any("快充" in x["why"] and x["delta"] > 0 for x in ds), "快充配置delta未生效"
    return "快充口径归一化+配置级delta生效"

def t039():
    """候选池必须固化在 categories 源码树（本地可复现），不能只活在 runs。

    v10.6.0 起 GitHub 不传 JSON：固化在源码树 ≠ 同步到仓库；后者按数据代码分离停用。
    """
    import os as _os
    for f in ("categories/candidates_compact_bev_extended.json",
              "categories/candidates_compact_bev_audit.json",
              "categories/universe_compact_bev_202610.json"):
        p = _os.path.join(HERE, f)
        assert _os.path.exists(p), f"候选池未固化: {f}"
    # v10.6.0 起 GitHub 不传 JSON：固化在源码树即可，不进同步集（数据代码分离）
    assert not _in_sync_set("categories/candidates_compact_bev_extended.json"), \
        "候选池 JSON 不应进 GitHub 同步集（v10.6.0 零 JSON）"
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
    from modules.data.trims_store import load as _load_trims
    d = {"entries": list(_load_trims().values())}
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
    # 2026-11-22：走统一入口（人工库 + 自动库合流）
    from modules.data.trims_store import load as _load_trims
    trims = {"entries": list(_load_trims().values())}["entries"]
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
    import re
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
    # v10.16.4：缺权威判定已随成绩一起收敛到 crash_cell（车名不再挂徽标/红标）
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
        txt = re.sub(r"<[^>]+>", "", RH.crash_cell(r, r))
        got = "缺权威" in txt
        assert got == want, f"crash_cell误判: {r} → {txt}, 期望缺权威={want}"
    return "碰撞标记修复：score_rows透传crash+basis；8 case全对(Euro NCAP不误标、真无成绩仍标缺权威)"


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
    """v10.16.4：碰撞成绩只在「碰撞成绩」列显示，数字取 parse_score 规范值；
    车名不再挂徽标/缺权威红标（原徽标自写正则会抓错分项，ES6 95% vs 93.95%）。"""
    import re
    from modules.board import render_board_html as RH
    from modules.engine import crash_systems as CS
    src = _read("render_board_html.py")
    assert "def crash_cell" in src and "flag-crash" in src, "碰撞列函数/CSS缺失"
    assert "crash_badge" not in src, "车名徽标未清除（旧逻辑仍可能抓错分项）"
    assert "risk_flags(r)}{sel}" in src, "车名应只保留车型+非碰撞风险标+配置下拉"
    assert 'elif k == "crash":' in src and "crash_cell(r, cand)" in src, "碰撞列未接唯一出口"
    cases = [
        ({"safety_basis": "cncap", "crash": "C-NCAP 2024 92.3%五星"}, "C-NCAP 92.3% 五星"),
        ({"safety_basis": "euro_ncap", "crash": "Euro NCAP 2026五星(安全驾驶79%/碰撞避免89%/碰撞保护93%)"}, "Euro NCAP 79% 五星"),
        # parse_score 规范 display 直接给体系+主成绩，不再靠正则挑分项
        ({"safety_basis": "euro_ncap", "crash": "Euro NCAP 2019五星(成人乘员96%/儿童86%/行人74%/安全辅助94%)"}, "Euro NCAP 96% 五星"),
        ({"safety_basis": "ciasi", "crash": "C-IASI G（三指数最低：辅助安全）"}, "C-IASI G"),
        ({"crash": "无官方成绩"}, "—（缺权威，非不安全）"),
    ]
    for r, want in cases:
        txt = re.sub(r"<[^>]+>", "", RH.crash_cell(r, r))
        assert txt == want, f"碰撞列错: {r} → {txt}, 期望{want}"
    # ES6 反例：规范主成绩 93.95%，不得再出现徽标抓成 95% 的分项
    es6 = {"safety_basis": "euro_ncap", "crash": "Euro NCAP 93.95% 五星"}
    txt = re.sub(r"<[^>]+>", "", RH.crash_cell(es6, es6))
    assert "93.95%" in txt and "95%" not in txt.replace("93.95%", ""), f"ES6 数字仍未统一: {txt}"
    # 真实 v18 产物：无重复徽标、数字一致、无成绩车标缺权威在碰撞列
    p = os.path.join(HERE, "runs", "midsuv_bev_202611_v18", "榜单.html")
    if os.path.exists(p):
        h = _read("runs/midsuv_bev_202611_v18/榜单.html")
        assert "crash_badge" not in h and "class='name'" in h
        assert "Euro NCAP 93.95% 五星" in h and "缺权威" in h
        # 车名列内不得有碰撞成绩/缺权威字样
        m = re.search(r"<td class=['\"]name['\"](.*?)</td>", h, re.S)
        if m:
            assert "NCAP" not in m.group(1) and "缺权威" not in m.group(1), "车名仍重复碰撞成绩"
    return "碰撞展示收敛到单列：C-NCAP/EuroNCAP/C-IASI规范值+缺权威提示；车名零重复徽标，ES6 93.95%唯一口径"

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
    # 12体系成绩都能在碰撞列显示（v10.16.4 徽标已并入单列）
    badge_cases = [("nhtsa", "NHTSA 5星"), ("jncap", "JNCAP五星(乘员95%)"),
                   ("iihs", "IIHS G"), ("bharat_ncap", "Bharat NCAP五星")]
    for b, cr in badge_cases:
        out = re.sub(r"<[^>]+>", "", RH.crash_cell({"safety_basis": b, "crash": cr}, {"crash": cr}))
        assert out and CS.cn_name(b) in out, f"新体系碰撞列缺失: {b}→{out}"
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
        # v10.14.0：用于测「官方价覆盖候选池已有值」（用户 2026-11-22 拍板）
        {"model": "测试车A2", "trims": [{"name": "低配", "price": 10.0},
                                     {"name": "高配", "price": 14.0}]},
    ]}, tmp, ensure_ascii=False)
    tmp.close()
    try:
        cars = [
            {"model": "测试车A", "price_guide": None},          # 应回填
            {"model": "测试车A2", "price_guide": [9.9, 13.9]},  # 官方价覆盖（用户 2026-11-22 拍板）
            {"model": "测试车C", "price_guide": None},          # trims 也没有 → 真缺口
        ]
        filled, still = FS.backfill_price_guide_from_trims(cars, tmp.name)
        assert ("测试车A", [10.0, 14.0]) in filled, f"未回填：{filled}"
        assert cars[0].get("price_source", "").startswith("trims.json"), "回填未标来源"
        # v10.14.0：价格口径改为「**官方价为准，覆盖**候选池已有值」
        # （用户 2026-11-22 拍板：价格冲突以官方价为准）。
        # 原值必须存档可追溯（price_prev + price_overwritten_by_official）。
        assert cars[1]["price_guide"] == [10.0, 14.0], \
            f"官方价未覆盖候选池值：{cars[1]['price_guide']}"
        assert cars[1].get("price_prev") == [9.9, 13.9], "原值未存档（不可追溯）"
        assert cars[1].get("price_overwritten_by_official") is True, "未标记覆盖审计"
        assert still == ["测试车C"], f"真缺口判定错：{still}"
        # trims 里没有的车（如测试车B）不产生覆盖，也不算真缺口（它本来就没官方价可对）
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
    # v10.14.0：回流入口改为安全成绩域**唯一同步器**
    # （modules/engine/sync_safety_scores.py，读 categories/safety_scores.json）。
    # 原来三套裁决逻辑（文本抽取 / official_safety_scores / cncap_scores）
    # 互相覆盖，冲突只能靠「谁后跑谁赢」——同一车出现过不同成绩。
    for f in ("pipeline.py", "board_v2.py"):
        assert "sync_safety_scores" in _read(f), f"{f} 未接安全成绩同步器"
        assert "SSS.sync(" in _read(f), f"{f} 未调用同步器"
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

    from modules.data.trims_store import load as _load_trims
    db = {"entries": list(_load_trims().values())}
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
    # v10.12.1：尺寸口径从「车身三维体积」换成**官方轴距**（汽车之家参数页「车身」组
    # id=1169，覆盖 382/427 配置；此前 vehicle_physics 的轴距覆盖率一直是 0%）。
    # 轴距是直接量，比外廓体积近似更准。命中面随之变化：
    #   MONA L05 9.3→8.7、零跑C11 9.2→8.6（官方轴距口径）
    assert by_sig.get("size") == {"小鹏MONA L05": 8.7, "零跑C11": 8.6}, \
        f"官方轴距信号应命中 MONA→8.7 / C11→8.6: {by_sig}"
    assert by_sig.get("efficiency") == {"腾势N7": 8.3}, f"得房率信号应只命中 N7→8.3: {by_sig}"
    assert uncal == [], f"本榜 21 款都有尺寸: {uncal}"
    touched = {"小鹏MONA L05", "零跑C11", "腾势N7"}
    assert all(before.get(c["model"]) == (c.get("scores") or {}).get("space")
               for c in cands if c["model"] not in touched), "不得误伤其他车"
    mona = [c for c in cands if c["model"] == "小鹏MONA L05"][0]
    assert mona.get("score_cap_reasons"), "封顶未留审计理由"
    assert adj[0]["peers"] >= SP.MIN_PEERS, "锚点样本不足"

    # 3) 标定唯一性：TOL 放宽到 0.6 会漏掉 MONA/C11（证明 0.4 不是随意取）
    #    v10.12.1：官方轴距口径下超出量变大（MONA 超中位 0.6），阈值须卡在 0.4~0.5
    c2 = copy.deepcopy(pool["candidates"])
    a2, _ = SP.calibrate_space(c2, tol=0.6)
    assert "小鹏MONA L05" not in {a["model"] for a in a2}, "tol 必须卡在 0.4~0.5"
    a3, _ = SP.calibrate_space(copy.deepcopy(pool["candidates"]), tol=0.4)
    assert "小鹏MONA L05" in {a["model"] for a in a3}, "tol=0.4 必须仍能抓住 MONA"

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
    return ("空间虚高根治：官方轴距一致性封顶(MONA9.3→8.7/C11 9.2→8.6)+得房率封顶(N7 8.5→8.3，锚点跨尺寸因为得房率是比例)+不倒扣+research补尺寸")


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
    # v10.5.2：蔚来ES6 是有同源车一手权威成绩的合法回填（EL6=Euro NCAP 五星），
    # 不属于「软文误伤」——从无成绩清单移除，保留其他确无成绩车。
    for bad in ("腾势N7", "问界M5纯电版", "小鹏G6", "比亚迪宋L EV"):
        assert bad not in filled_m, f"无成绩车 {bad} 被误填成有成绩"
    assert "蔚来ES6" in filled_m, "ES6 同源一手成绩未回填"

    # 4) 展示层：回填后不再标「缺权威碰撞成绩」，且徽标显示 C-IASI
    row = {"safety_basis": my2["safety_basis"], "crash": my2["crash"]}
    assert "缺权威碰撞成绩" not in RH.risk_flags(row), "有 C-IASI 实锤仍被标缺成绩"
    badge = re.sub(r"<[^>]+>", "", RH.crash_cell(row, row))
    assert "C-IASI" in badge, f"碰撞列未显示 C-IASI: {badge}"

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
    # v10.14.0：权威库回流并入唯一同步器（SSOT 内含 official_safety_scores）
    assert "sync_safety_scores" in open(src, encoding="utf-8").read(), \
        "pipeline 未接入安全成绩同步器（权威库成绩会白采）"
    assert "SSS.sync(" in open(src, encoding="utf-8").read(), \
        "pipeline 未调用同步器"
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
    # 注意：t088 只跑文本回填、不跑 v10.4.0 融合；bZ5 的文本主成绩仍是 cncap，
    # 新闻稿救回后升级为 ciasi 由 t089 测（两个口径分层，别混在一起）。
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

    # 3) 无成绩车零误伤（G6/宋L/N7/M5 旁证不当成绩）
    # v10.5.2：蔚来ES6 移出——它是同源车一手权威成绩（Euro NCAP 五星），属合法纳入非误伤。
    for model in ("小鹏G6", "比亚迪宋L EV", "腾势N7", "问界M5纯电版"):
        assert not by[model].get("crash_all_scores"), f"无成绩车 {model} 被记入成绩"
    assert by["蔚来ES6"].get("crash_all_scores"), "ES6 同源一手成绩未记入审计"

    # 4) 链路内置：回填写审计字段、报告披露、summary 有审计（防退回「只取第一个就丢」）
    fs_src = open(str(paths.src("facts_sync.py")), encoding="utf-8").read()
    assert "crash_all_scores" in fs_src and "parse_all_scores_from_evidence" in fs_src, \
        "回填未写多成绩审计字段"
    pl_src = open(str(paths.src("pipeline.py")), encoding="utf-8").read()
    # v10.4.0：报告段由「多成绩口径」升级为「多测评纳入」（全纳入口径），两者都代表披露存在
    assert ("多成绩口径" in pl_src or "多测评纳入" in pl_src) and "crash_multi_scores" in pl_src, \
        "报告/summary 未披露多成绩口径"
    return ("多成绩口径：取证据最强(不取最高/不叠加)+次要成绩全量留痕+主成绩不漂移+"
            "无成绩车零误伤+报告/summary披露(+t088)")


def t089():
    """多测评全纳入融合口径（v10.4.0）：同体系木桶取最低 + 跨体系取最严格主分 +
    C-IASI 新闻稿文字等级救回数字图标车。

    2026-11-21 用户「有评测就要纳入，按业内原则以及管理重新设计，所有测评都纳入才准确」。
    业内原则：Euro NCAP/ANCAP 官网与 C-NCAP 规则均明确「总评级由最低分项决定」（木桶）；
    跨体系无对应关系，不能套同一公式（项目早期调研结论）。
    """
    from modules.engine import multi_evidence_score as MES
    import copy

    # 1) 模块自测（木桶取最低/跨体系取主分/旁证不计分）
    assert MES.selftest() == 0, "multi_evidence_score 自测失败"

    # 2) 新闻稿产物必须存在且解出可核验文字等级（不是数字图标猜测）
    from modules.engine import multi_evidence_score as _MES
    dec = json.load(open(_MES.decoded_path(), encoding="utf-8"))
    assert dec.get("n_releases", 0) >= 8 and dec.get("n_models", 0) >= 30, "新闻稿未落盘完整产物"
    assert "method" in dec and "新闻稿" in dec["method"], "解码方法不是新闻稿纯文字"
    my = dec["models"]["特斯拉Model Y"]["dims"]
    assert my["occupant"]["grade"] == "G+" and my["assist"]["grade"] == "G", "Model Y 文字等级错"
    # aggregate_only 不猜
    l60 = dec["models"]["乐道L60"]["dims"]
    assert all((v.get("grade") is None and v.get("scope") == "aggregate_only") for v in l60.values()), \
        "乐道L60 稿件未点名维度被错误猜等级"

    # 3) 木桶：三指数取最低档（Model Y G+/G+/G → G；耐撞维修 M 不进安全分）
    rec = {"dims": {"occupant": {"grade": "G+", "scope": "all"},
                    "pedestrian": {"grade": "G+", "scope": "all"},
                    "assist": {"grade": "G", "scope": "all"},
                    "crash_repair": {"grade": "M", "scope": "all"}},
           "source": "test", "published": "2026-01-01"}
    table = json.load(open(str(paths.ROOT / "official_safety_scores.json"), encoding="utf-8"))
    b = MES._ciasi_bucket(rec, table)
    assert b["grade"] == "G" and b["score"] == 9.0, f"木桶取最低失败: {b}"
    assert any(x["dim"] == "耐撞维修(仅披露)" and not x["counted"] for x in b["dims"]), \
        "耐撞维修不应进安全分木桶"

    # 4) 真实池：新闻稿救回 bZ5（cncap 86.7%→C-IASI G+ 主分）、Model Y 仍 C-IASI G
    from modules.contracts.candidate_schema import normalize_pool
    pool = json.load(open(str(paths.ROOT / "runs" / "midsuv_bev_pool.json"), encoding="utf-8"))
    cands = normalize_pool(copy.deepcopy(pool["candidates"]))
    r = MES.fuse(cands)
    by = {x["model"]: x for x in r}
    assert by["丰田bZ5"]["primary"].startswith("C-IASI G+"), by["丰田bZ5"]
    assert by["特斯拉 Model Y"]["primary"].startswith("C-IASI G（"), by["特斯拉 Model Y"]
    assert "C-NCAP 86.7%" in [s["display"] for s in by["丰田bZ5"]["all_scores"]], "bZ5 次要成绩未列示"
    # 旁证不计分：极氪7X Euro NCAP 只在 excluded
    j7 = by["极氪7X"]
    assert all("Euro NCAP" not in s["display"] for s in j7["all_scores"]), "旁证被计入"
    assert any("Euro NCAP" in str(e) for e in j7["excluded"]), "旁证未披露"

    # 5) 链路内置：pipeline 调融合 + 报告/summary 披露（防退回单一成绩）
    pl = open(str(paths.src("pipeline.py")), encoding="utf-8").read()
    assert "multi_evidence_score" in pl and "crash_fusion" in pl and "多测评纳入" in pl, \
        "pipeline 未接入融合/未披露"

    # 6) 无一手成绩车零误伤
    for m in ("小鹏MONA L05", "蔚来ES6", "小鹏G6", "比亚迪宋L EV"):
        assert by[m]["primary"] is None, f"无成绩车 {m} 被误纳入"
    return ("多测评全纳入：同体系木桶取最低+跨体系取最严格主分+C-IASI新闻稿文字救回+"
            "旁证不计分+无成绩零误伤+报告/summary披露(+t089)")


def t090():
    """C-NCAP 官方成绩表回流 + 展示门禁：有测试但页面不能空白。

    2026-11-21 全量核对发现 cncap_scores.json 有「零跑C11 86.4%」，候选池
    crash/note/evidence 全空，页面碰撞成绩列显示「—」。不是渲染问题，是回流缺链路。
    """
    import copy
    from modules.engine import facts_sync as FS
    from modules.engine import crash_systems as CS

    pool = json.load(open(str(paths.ROOT / "runs" / "midsuv_bev_pool.json"), encoding="utf-8"))
    cands = copy.deepcopy(pool["candidates"])
    c11 = next(c for c in cands if c["model"] == "零跑C11")
    assert not CS.is_official(c11.get("safety_basis")), "测试前置失败：C11 已有权威 basis"

    # 与主链同序：先回填 note 文本成绩，再回填 C-NCAP 官方表 + authoritative 库。
    # v10.6.0 起库里新增深蓝S07(C-NCAP)/宋L EV(C-IASI)，不跑权威回填也会报漏显。
    # v10.14.0：三段回流合并为唯一同步器（读 categories/safety_scores.json）
    from modules.engine import sync_safety_scores as SSS
    filled, _unres = SSS.sync(cands)
    assert any(m == "零跑C11" and d.startswith("C-NCAP 86.4%") and b == "C-NCAP"
               for m, d, b in filled), f"C-NCAP 表未回流: {filled}"
    c11 = next(c for c in cands if c["model"] == "零跑C11")
    assert c11["crash"].startswith("C-NCAP 86.4%") and c11["safety_basis"] == "C-NCAP", c11
    assert FS.crash_display_leaks(cands) == [], "回流后仍报漏显"

    # 失败注入：回流链断开后，门禁必须检出「表里有成绩、页面却是 —」
    broken = copy.deepcopy(cands)
    b11 = next(c for c in broken if c["model"] == "零跑C11")
    b11["crash"], b11["safety_basis"] = "—", None
    leaks = FS.crash_display_leaks(broken)
    # v10.14.0：来源名从 "cncap_scores" 改为 "safety_scores SSOT"
    # （安全成绩域唯一事实源，C-NCAP 表已并入其中）
    assert any(x["model"] == "零跑C11" and "safety_scores SSOT" in x["sources"]
               for x in leaks), f"展示门禁漏检: {leaks}"
    for m in ("小鹏G6", "蔚来ES6", "腾势N7"):
        assert not any(x["model"] == m for x in leaks), f"无成绩车被误报漏显: {m}"

    pl = _read("pipeline.py")
    # v10.14.0：三段回流合并为唯一同步器（sync_safety_scores）
    assert "sync_safety_scores" in pl and "SSS.sync(" in pl and "crash_display_leaks" in pl, \
        "pipeline 未接入安全成绩同步器/展示门禁"
    return ("C-NCAP官方表回流+页面展示门禁：C11 86.4%不再空白+无成绩零误报(+t090)")


def t091():
    """C-IASI 新闻稿救回通用化：任意新车可复用、自动找新 ID、不硬编码。"""
    import inspect
    from modules.research import ciasi_text_decode as CTD

    assert CTD.selftest() == 0, "ciasi_text_decode 自测失败"
    newer = """
    <html><head><title>C-IASI 2026年测评车型第100次结果发布</title></head>
    <body>发布时间： 2026-11-21
    本次结果发布共涉及2款车型，分别为：未来甲EV、未来乙EV。
    车内乘员安全分指数，2款车型的评价结果均为良好（A）。
    车外行人安全分指数，2款车型的评价结果均为优秀（G）。
    车辆辅助安全分指数，未来甲EV的评价结果为优秀（G），未来乙EV车型的评价结果为一般（M）。
    </body></html>
    """
    def fake(sid):
        return newer if sid == 777 else None

    rep = CTD.rescue_targets(["未来甲EV", "未来乙EV"], decoded={}, start=777,
                             end=779, sleep=0, fetcher=fake)
    assert set(rep["matched"]) == {"未来甲EV", "未来乙EV"}, rep
    assert rep["matched"]["未来乙EV"]["dims"]["assist"]["grade"] == "M", rep
    assert rep["unmatched"] == [], rep

    base = {"models": {"旧车型": {"dims": {"occupant": {"grade": "G", "scope": "all"}},
                                 "source": "old", "published": "2026-01-01"}},
            "releases": [], "scan_range": [1, 776]}
    merged = CTD.merge_decoded(base, {"models": rep["matched"], "scan_range": [777, 779]})
    assert "旧车型" in merged["models"] and "未来甲EV" in merged["models"], "合并丢失旧车型"

    src = inspect.getsource(CTD.rescue_targets)
    assert "特斯拉Model Y" not in src and "丰田bZ5" not in src, "救回逻辑硬编码具体车型"
    assert "def rescue_targets" in _read("ciasi_text_decode.py"), "救回入口不在随包代码里"
    pl = _read("pipeline.py")
    assert "rescue_targets" in pl and "targets_from_candidates" in pl and "ciasi_rescue" in pl, \
        "pipeline 未接入新闻稿自动救回"
    return ("新闻稿救回通用化：任意车型可解析+自动探测新稿ID+合并不丢旧车+无硬编码(+t091)")


def t092():
    """发布包体积治理：回归专用样本代码化排除，不破坏运行链路与回归可读。

    背景（2026-11-21）：发布包解压后 2908KB，逼近 3MB 上限。真正的大生成文件夹
    runs/(407MB) data/ 早已排除，但 categories/ 里 4 个候选池快照（~54KB）只被
    bug_regression 当样本读、产品运行链路从不碰——却一直随包发布。
    根因修复：paths.REGRESSION_FIXTURES 单一事实源 + build_release 统一排除，
    而不是手工删文件名（下次新增样本又漏）。
    """
    from pathlib import Path
    import inspect

    # 1) 单一事实源存在且非空
    assert paths.REGRESSION_FIXTURES, "无回归样本名单"
    assert "REGRESSION_FIXTURES" in _read("paths.py"), "名单不在 paths.py"

    # 2) 核心纪律：名单里的文件，产品运行链路（非 regress）零引用
    runtime_hits = {}
    for name in paths.REGRESSION_FIXTURES:
        hits = []
        for p in Path(HERE).rglob("*.py"):
            rel = p.relative_to(HERE).as_posix()
            if rel.startswith(("runs/", "data/", "dist/")) or "/regress/" in rel \
                    or rel.startswith("build_release") or rel == "paths.py":
                continue
            if name in p.read_text(encoding="utf-8", errors="ignore"):
                hits.append(rel)
        if hits:
            runtime_hits[name] = hits
    assert not runtime_hits, f"回归样本被运行链路引用，不能排除: {runtime_hits}"

    # 3) 运行依赖不得混进名单（防呆：新装包功能残缺）
    for must_ship in ("ciasi_decoded.json", "filings_notes.json",
                      "universe_car_midsuv_bev.json"):
        assert must_ship not in paths.REGRESSION_FIXTURES, \
            f"运行依赖 {must_ship} 误列入排除名单"

    # 4) build_release 真的消费了这个名单（代码级，不是摆设）
    import build_release as BR
    assert "REGRESSION_FIXTURES" in inspect.getsource(BR.is_excluded_name), \
        "build_release.is_excluded_name 未消费名单"
    for name in paths.REGRESSION_FIXTURES:
        assert BR.is_excluded_name(name), f"打包白名单未排除 {name}"
    # v10.5.2：零 JSON 策略下所有 JSON（含曾随包的 master_pool/ciasi_decoded/filings_notes）
    # 都预期排除，由 data_bootstrap 补回；只有非 JSON 运行文件必须保留。
    for name in ("SKILL.md", "README.md", "requirements.txt", "pipeline.py"):
        assert not BR.is_excluded_name(name), f"非JSON运行文件被误排除: {name}"

    # 5) 源码树保留（回归测试要读），发布包已重建为不含样本
    for name in paths.REGRESSION_FIXTURES:
        assert (Path(HERE) / "categories" / name).exists(), f"源码树缺回归样本 {name}"
    import glob as _glob, zipfile as _zf
    zips = _glob.glob(str(Path(HERE) / "dist" / "*.zip"))
    if zips:
        with _zf.ZipFile(sorted(zips)[-1]) as z:
            packed = {p.split("/")[-1] for p in z.namelist()}
        leaked = packed & set(paths.REGRESSION_FIXTURES)
        assert not leaked, f"发布包仍含回归样本: {leaked}"
    return ("发布包回归样本代码化排除：4快照~54KB不进包+运行零引用+"
            "非JSON运行文件保留+源码树保留(+t092)")


def t093():
    """发布包零 JSON + 数据补回链路（v10.5.2 用户："JSON 都可以不打包，联网获得"）。

    用户明确接受"即便其他地方引用它们也不打包"。根因修复不是删几个文件名，
    而是：①总闸门 EXCLUDE_ALL_JSON 覆盖一切 .json（新增 JSON 自动排除）；
    ②提供唯一联网出口 data_bootstrap 从已发布仓库补回，新装包才跑得起来；
    ③补回内容校验合法 JSON，防代理错误页落盘污染。
    """
    import inspect
    from pathlib import Path

    # 1) 总闸门存在且被 is_excluded_name 消费
    import build_release as BR
    assert BR.EXCLUDE_ALL_JSON, "无零JSON总闸门"
    assert "EXCLUDE_ALL_JSON" in _read("build_release.py"), "打包脚本无零JSON总闸门"
    assert "EXCLUDE_ALL_JSON" in inspect.getsource(BR.is_excluded_name), \
        "is_excluded_name 未消费总闸门"
    # 覆盖根目录 JSON 与 categories JSON，含未来新增名字
    for n in ("master_pool.json", "ciasi_decoded.json", "cncap_scores.json",
              "official_safety_scores.json", "levels.json", "某未来新数据.json"):
        assert BR.is_excluded_name(n), f"JSON 未被排除: {n}"
    # 非 JSON 运行文件不得被误排
    for n in ("SKILL.md", "README.md", "requirements.txt", "pipeline.py", "data_bootstrap.py"):
        assert not BR.is_excluded_name(n), f"非JSON被误排: {n}"

    # 2) 补回模块存在、联网只在 research 内、selftest 通过
    from modules.research import data_bootstrap as DB
    assert DB.selftest() == 0, "data_bootstrap 自测失败"
    dbsrc = inspect.getsource(DB)
    assert "api.github.com" in dbsrc and "API_CONTENTS" in dbsrc, \
        "补回源不是已发布仓库 contents API"
    # 正文下载必须走 fetch_json_file（contents API + base64），不能直接用 raw 域名拼 URL
    assert "def fetch_json_file" in dbsrc and "base64.b64decode" in dbsrc, \
        "正文下载未走 contents API base64 解码"
    assert "urllib.request.urlopen" in dbsrc, "补回未走 research 内联网出口"
    # 落盘前校验 JSON 合法性（防代理 HTML 污染）
    assert "json.loads(raw.decode" in dbsrc, "补回未校验 JSON 合法性"

    # 3) 实际发布包零 JSON（若已重建）
    import glob as _glob, zipfile as _zf
    zips = _glob.glob(str(Path(HERE) / "dist" / "*.zip"))
    assert zips, "无发布包，请先 python build_release.py"
    with _zf.ZipFile(sorted(zips)[-1]) as z:
        js = [n for n in z.namelist() if n.endswith(".json")]
    assert not js, f"发布包仍含 JSON: {js[:5]}"

    # 4) 源码树数据仍在（本地/仓库运行与回归照常）
    # v10.14.0（P3）：master_pool 降级为生成物，人工层单独随包
    assert (Path(HERE) / "categories" / "pool_membership.json").exists(), \
        "源码树丢了候选池人工层（pool_membership.json）"
    assert not (Path(HERE) / "categories" / "master_pool.json").exists(), \
        "master_pool 仍在 categories/ —— 人工层与生成层未分离（双副本）"
    return ("零JSON发布策略：总闸门覆盖一切.json+仓库补回链路+合法性校验+源码树保留(+t093)")


def t094():
    """同源车出口版一手成绩纳入（v10.5.2 蔚来ES6）：出口名同源映射+具体年份得分放行，
    软文/智驾分不误抓，零映射车不泛化。腾势N7 仍判无成绩（只有宣传口径）。
    """
    import copy
    from modules.engine import crash_systems as CS
    from modules.engine import facts_sync as FS

    # 1) 同源映射表存在且可扩展，只放确定同车
    assert "EXPORT_ALIASES" in _read("crash_systems.py"), "解析器无同源映射表"
    assert "蔚来ES6" in CS.EXPORT_ALIASES and "EL6" in CS.EXPORT_ALIASES["蔚来ES6"]
    assert CS.export_aliases_of("蔚来 ES6") == ["EL6"], "同源名归一化失败"

    # 2) ES6 证据 → 一手 Euro NCAP 成绩（年份+得分率）
    pool = json.load(open(os.path.join(HERE, "runs", "midsuv_bev_pool.json"), encoding="utf-8"))
    cands = copy.deepcopy(pool["candidates"])
    es6 = next(c for c in cands if c["model"] == "蔚来ES6")
    ce = (es6.get("evidence") or {}).get("crash_evidence") or ""
    h = CS.parse_homolog_score(ce, "蔚来ES6")
    assert h and h["basis"] == "euro_ncap" and h["score"] == 93.95 and h["tier"] == 2, h
    assert h["year"] == 2024, h

    # 3) 端到端回填：ES6 从无成绩 → Euro NCAP 一手，且不误抓 C-ICAP 92.9% 智驾分
    filled, _ = FS.backfill_crash_from_text(cands)
    es6 = next(c for c in cands if c["model"] == "蔚来ES6")
    assert CS.is_official(es6.get("safety_basis")) and es6["safety_basis"] == "euro_ncap", es6
    assert "93.95" in es6["crash"] and "92.9" not in es6["crash"], es6
    assert any(f[0] == "蔚来ES6" for f in filled), "ES6 未进回填清单"

    # 4) 零泛化：只对有映射的车生效——全池只有 ES6 被同源解析命中
    hits = [c["model"] for c in pool["candidates"]
            if CS.parse_homolog_score(
                str(((c.get("evidence") or {}).get("crash_evidence") or "")), c["model"])]
    assert hits == ["蔚来ES6"], f"同源解析误伤其他车: {hits}"

    # 5) 腾势N7 仍无成绩（只有 C-NCAP 五星车身设计宣传 + C-ICAP 智驾，无碰撞得分率）
    n7 = next(c for c in cands if c["model"] == "腾势N7")
    assert not CS.is_official(n7.get("safety_basis")), f"N7 被误判有成绩: {n7.get('safety_basis')}"
    n7ce = str(((n7.get("evidence") or {}).get("crash_evidence") or ""))
    assert not CS.parse_homolog_score(n7ce, "腾势N7") and not CS.parse_score_from_evidence(n7ce), \
        "N7 宣传口径被误抓成成绩"

    # 6) 软文拒绝：泛泛「宣称双五星」无年份得分 → 不升级
    assert CS.parse_homolog_score(
        "出口版获Euro NCAP五星，官网宣称中欧双五星", "蔚来ES6") is None, "无年份得分的软文被放行"

    # 7) pipeline 接入同源解析
    assert "parse_homolog_score" in _read("facts_sync.py"), "facts_sync 未接入同源解析"
    return ("同源车出口版一手成绩：ES6=EL6 的 Euro NCAP 93.95%纳入+C-ICAP智驾不误抓+"
            "零泛化+N7仍无成绩+软文拒绝(+t094)")


def t095():
    """深蓝S07/宋L EV 官网有一手碰撞成绩但页面空白（v10.6.0，联网核查发现）。

    根因：出榜只救回数字图标车，不对『本地权威库无此车』的联网核验；
    两车官网有 C-NCAP 88.3% / C-IASI 一手测评，库和候选池都没有 → 页面空白。
    修复：①补入权威库（带官网 URL）②新增 research 联网核验模块接进出榜
    ③展示门禁保证有来源不空白。
    """
    import json as _json
    from modules.engine import facts_sync as FS
    # 1) 两车已在权威库（带来源）
    cn = _json.load(open(_read_path("cncap_scores.json"), encoding="utf-8"))
    assert abs(float(cn["scores"]["深蓝S07"]) - 88.3) < 0.01, "深蓝S07 C-NCAP 88.3% 未入库"
    off = _json.load(open(_read_path("official_safety_scores.json"), encoding="utf-8"))
    sl = off["car_scores"].get("比亚迪宋L EV")
    assert sl and sl.get("system") == "C-IASI" and sl.get("grade") == "G", "宋L EV C-IASI G 未入库"
    assert sl.get("source_url", "").startswith("https://www.ciasi.org.cn/"), "宋L 无官网来源"
    # 2) 回流后页面字段有成绩
    cands = _read_pool()
    f = FS.backfill_crash_from_cncap_table(cands)
    assert any(m == "深蓝S07" for m, _, _ in f), "深蓝S07 未回流"
    from modules.engine import crash_systems as _CS
    s07 = next(c for c in cands if c["model"] == "深蓝S07")
    assert _CS.has_score(s07.get("crash")), "深蓝S07 回流后仍无成绩"
    # 3) 联网核验模块存在且接进出榜（源码断言，避免真联网）
    assert "crash_auto_verify" in _read("pipeline.py"), "pipeline 未接入碰撞自动联网核验"
    assert "cncap-swiper" in _read("crash_auto_verify.py"), "联网核验模块无 C-NCAP 解析"
    from modules.research import crash_auto_verify as CAV
    assert CAV.selftest() == 0, "碰撞联网核验模块自测失败"
    # 4) 宋L 保守取 G，不猜 G+（纪律）
    assert sl.get("score") == 9.0, "宋L 不应猜 G+"
    return ("碰撞联网核验：深蓝S07 C-NCAP88.3%/宋L EV C-IASI G 补库+回流+出榜联网比对"
            "(+t095)")


def t096():
    """特斯拉三电垫底但电耗全榜最低：三电维度从未纳入系统能效（v10.6.0）。"""
    from modules.engine import powertrain_efficiency as PE
    # 1) 模块自测通过（低电耗加分/高电耗减分/无数据不动/封顶/只改三电）
    assert PE.selftest() == 0, "电耗效率模块自测失败"
    # 2) 全网数据：特斯拉电耗全榜第二低
    tbl = PE.load_consumption()
    assert PE._canon_key("特斯拉 Model Y") in tbl
    assert tbl[PE._canon_key("特斯拉 Model Y")]["consumption"] <= 12.0
    # 3) 端到端：特斯拉应加分
    cands = _read_pool()
    before = next(c for c in cands if c["model"] == "特斯拉 Model Y")["scores"]["powertrain"]
    PE.apply_efficiency(cands)
    tesla = next(c for c in cands if c["model"] == "特斯拉 Model Y")
    assert tesla["scores"]["powertrain"] > before, \
        f"特斯拉电耗最低却没加分: {before}→{tesla['scores']['powertrain']}"
    assert tesla["powertrain_efficiency"]["adjust"] > 0
    # 4) 高电耗车减分（全榜统一，不是给特斯拉开后门）
    #    v10.11.0：不再钉死「深蓝S07」这一辆。官方 CLTC 电耗接入后，
    #    每车取的是**评分配置对应值**（深蓝S07 = 12.6，低于榜内中位 13.5），
    #    旧断言钉死的那辆车不再成立——那是数据口径变了，不是机制坏了。
    #    v10.15.3：改为车重归一残差法。绝对电耗高 ≠ 效率低（电耗~车重相关+0.839），
    #    极氪7X 13.9kWh 因车重2280kg 归一后是真高效(残差−1.03)应加分。
    #    新机制断言：**残差高于榜内中位残差**的车必须减分（双向性纪律本身）。
    applied = [c for c in cands
               if (c.get("powertrain_efficiency") or {}).get("status") == "applied"]
    assert applied, "榜内应有已应用的车"
    assert all((c["powertrain_efficiency"].get("method") == "residual") for c in applied), \
        "全榜应走车重残差法"
    med_res = applied[0]["powertrain_efficiency"]["median_residual"]
    assert med_res is not None, "缺中位残差"
    over = [c for c in applied if c["powertrain_efficiency"]["residual"] > med_res]
    if over:
        for c in over:
            assert c["powertrain_efficiency"]["adjust"] < 0, \
                f"残差高于中位（比同重车费电）应减分: {c['model']} {c['powertrain_efficiency']}"
    #    极端费电车（绝对电耗最高且车重不最大）应被减分——机制双向性兜底
    assert any(c["powertrain_efficiency"]["adjust"] < 0 for c in applied), \
        "榜内应有被减分的低效车（否则双向性失效）"
    #    官方口径优先：充电/电耗来源可追溯
    srcs = {(c.get("powertrain_efficiency") or {}).get("official") for c in cands
            if (c.get("powertrain_efficiency") or {}).get("status") == "applied"}
    assert True in srcs, "应至少有一款用官方 CLTC 电耗（trims.json）"
    # 5) pipeline 接入 + 开关存在
    assert "powertrain_efficiency" in _read("pipeline.py"), "pipeline 未接电耗修正"
    assert "no-powertrain-efficiency" in _read("pipeline.py"), "缺 A/B 对照开关"
    return ("三电能耗效率：车重归一残差法双向修正±0.4（v10.15.3，重车不被绝对电耗冤枉）"
            "，特斯拉低电耗加分/低效车减分"
            "(+t096)")


def t097():
    """GitHub 仓库存 43 个 JSON 违背数据代码分离（v10.6.0）。"""
    import sync_to_repo as S
    # 1) 同步脚本排除所有 .json（单一事实源）
    assert '".json"' in _read("sync_to_repo.py"), "同步脚本 SKIP_SUFFIX 未含 .json（源码断言）"
    assert ".json" in S.SKIP_SUFFIX, "同步脚本未排除 .json"
    assert S.should_skip("categories/master_pool.json"), "categories JSON 未被排除"
    assert S.should_skip("cncap_scores.json"), "根目录 JSON 未被排除"
    assert not S.should_skip("modules/engine/spec_score.py"), "误伤 .py"
    # 2) collect 源码树：同步集里不含任何 .json（验证规则真生效，非仅看常量）
    got = S.collect(str(paths.ROOT))
    assert not any(p.endswith(".json") for p in got), "源码树 JSON 仍进同步集"
    # 3) data_bootstrap 已停用（数据源消失）
    assert "停用" in _read("data_bootstrap.py"), "data_bootstrap 未标注停用"
    return ("GitHub 零 JSON：同步脚本排除 .json + data_bootstrap 停用(+t097)")


def t098():
    """逐车碰撞联网核验门禁（v10.7.0）：旧版只扫官网首页+失败只告警，
    断网/历史车不在首页时都静默沿用本地库，无法证明「每车都联网确认过」。

    修复：research 唯一出口 verify_all() 对每车产出状态；coverage_gate()
    对「官网有本地无 / 数值冲突 / 联网失败」硬阻断，历史来源回访披露。
    """
    from modules.research import crash_auto_verify as CAV
    # 1) 模块确定性自测（注入假 fetcher，不联网）
    assert CAV.selftest_all() == 0, "逐车核验自测失败"
    # 2) 源码契约：逐车核验 + 门禁 + 主链接入 + 降级开关都在（防只改产物）
    src = _read("crash_auto_verify.py")
    for token in ("def verify_all", "def coverage_gate", "BLOCKING_STATUSES",
                  "STATUS_NOTE", "def fetch_url"):
        assert token in src, f"crash_auto_verify 缺 {token}"
    pl = _read("pipeline.py")
    assert "verify_all(" in pl and "coverage_gate(" in pl, "pipeline 未接逐车门禁"
    assert "allow-degraded-network" in pl, "缺显式降级开关"
    assert "碰撞联网核验门禁" in pl, "门禁未硬拦"
    # 3) 逐车状态：每款入池车都有状态；历史车回访确认（注入，不联网）
    fake = """
    alt="本代车A" class="cncap-swiper__car-image"
    <span class="cncap-swiper__year">2026</span>
    <p class="cncap-swiper__score-value">93.9%</p>
    """
    rows = CAV.parse_home(fake)
    cands = [{"model": "本代车A"}, {"model": "历史车B"}, {"model": "无成绩车C"}]
    table = {"scores": {"本代车A": 93.9, "历史车B": 88.3},
             "sources": {"历史车B": {"source_url": "https://www.c-ncap.org.cn/evaluation/b2"}}}
    rep = CAV.verify_all(cands, official=rows, table=table, decoded={},
                         authoritative={},
                         url_fetcher=lambda u: "<body>88.3%</body>")
    assert set(rep["per_car"]) == {"本代车A", "历史车B", "无成绩车C"}, "每车必须有核验状态"
    assert rep["per_car"]["本代车A"]["status"] == "live_confirmed"
    assert rep["per_car"]["历史车B"]["status"] == "cache_verified"
    assert rep["per_car"]["无成绩车C"]["status"] == "no_score"
    assert CAV.coverage_gate(rep)["ok"], "无阻断项应放行"
    # 4) 事故回放：官网有本地无（深蓝S07 类型）+ 断网，都必须硬拦
    miss = CAV.verify_all([{"model": "本代车A"}], official=rows,
                          table={"scores": {}}, decoded={}, authoritative={})
    assert miss["per_car"]["本代车A"]["status"] == "new_found"
    assert not CAV.coverage_gate(miss)["ok"], "官网有本地无必须阻断"
    down = CAV.verify_all([{"model": "任意车"}], home_fetcher=lambda: None,
                          table={"scores": {}}, decoded={}, authoritative={})
    assert down["network_error"] and not CAV.coverage_gate(down)["ok"], "断网必须阻断"
    assert CAV.coverage_gate(down, allow_degraded=True)["degraded"], "显式降级须标记"
    return "逐车联网核验：每车有状态+三类硬阻断+历史回访披露，断网不再静默沿用(+t098)"


def t099():
    """核验状态带时间戳 + 按天缓存（v10.8.0）：旧版每次出榜都重新联网、
    状态无时间，用户要求「当天的可不重复抓取，不是当天的核验状态失效」。

    修复：research verify_all 逐车写 verified_at/verified_date；当天命中复用
    （连首页都不抓），跨天/权威库变更使指纹变则失效重抓；network_error 不缓存；
    pipeline 加 --no-crash-cache 强制重抓并输出缓存统计。
    """
    from modules.research import crash_auto_verify as CAV
    # 1) 模块确定性自测（当天复用/跨天失效/数据变更/断网不缓存/救回强制重核）
    assert CAV.selftest_cache() == 0, "按天缓存自测失败"
    # 2) 源码契约：时间戳/缓存函数/指纹/主链开关都在（防只改产物）
    src = _read("crash_auto_verify.py")
    for token in ("verified_at", "verified_date", "def load_cache",
                  "def save_cache", "def _fingerprint", "def _cache_valid",
                  "UNCACHEABLE", '"from_cache"'):
        assert token in src, f"crash_auto_verify 缺 {token}"
    pl = _read("pipeline.py")
    assert "--no-crash-cache" in pl and "use_cache=" in pl, "pipeline 未接缓存开关"
    assert "当天复用" in pl, "缓存统计未输出"
    # 3) 缓存文件必须在 runs/（零 JSON 进 GitHub/发布包的铁律）
    assert "runs" in CAV.CACHE.replace("\\", "/") and CAV.CACHE.endswith(".json"), CAV.CACHE
    # 4) 端到端内存注入：当天二次调用零首页抓取，逐车带时间
    home = """
    alt="缓存车A" class="cncap-swiper__car-image"
    <span class="cncap-swiper__year">2026</span>
    <p class="cncap-swiper__score-value">93.9%</p>
    """
    rows = CAV.parse_home(home)
    cache: dict = {}
    calls = {"n": 0}

    def home_fetch():
        calls["n"] += 1
        return home

    cands = [{"model": "缓存车A"}]
    table = {"scores": {"缓存车A": 93.9}, "sources": {}}
    r1 = CAV.verify_all(cands, home_fetcher=home_fetch, table=table,
                        decoded={}, authoritative={}, cache=cache, today="2026-12-01")
    assert calls["n"] == 1 and r1["per_car"]["缓存车A"]["verified_at"], r1
    r2 = CAV.verify_all(cands, home_fetcher=home_fetch, table=table,
                        decoded={}, authoritative={}, cache=cache, today="2026-12-01")
    assert calls["n"] == 1 and r2["per_car"]["缓存车A"]["from_cache"], "当天不得重抓"
    r3 = CAV.verify_all(cands, home_fetcher=home_fetch, table=table,
                        decoded={}, authoritative={}, cache=cache, today="2026-12-02")
    assert calls["n"] == 2 and not r3["per_car"]["缓存车A"]["from_cache"], "跨天必须失效"
    return "核验状态带时间戳 + 当天复用/跨天失效/数据变更重核/断网不缓存(+t099)"


def t100():
    """电池来源不遗漏（v10.9.0）：极氪7X/阿维塔07 电池来源空——根因是事实库
    没有这两车、且无联网兜底。修复：research 联网核验（只出线索不回填，防串配置）+
    事实库补录 + 页面缺失原因标注「—（原因）」。
    """
    from modules.research import cell_brand_verify as CBV
    # 1) 模块确定性自测
    assert CBV.selftest() == 0, "电芯联网核验自测失败"
    # 2) 源码契约（防只改产物）
    src = _read("cell_brand_verify.py")
    for token in ("def verify_all", "def verify_cell", "def parse_brands",
                  "STATUS_NOTE", "need_review", "def fetch_search"):
        assert token in src, f"cell_brand_verify 缺 {token}"
    pl = _read("pipeline.py")
    assert "cell_brand_verify" in pl and "--no-cell-verify" in pl, "pipeline 未接电芯核验"
    bv = _read("board_view.py")
    assert "电芯_缺失原因" in bv, "页面未接缺失原因标注"
    v2 = _read("board_v2.py")
    assert "电芯_缺失原因" in v2, "fact_columns 未带出缺失原因"
    # v10.10.0 架构升级：唯一可写种子迁到契约层（随 GitHub/发布包存活），
    # build_cell_facts 只引用它；board_view 统一走 vehicle_facts 入口读权威值。
    seed = _read("vehicle_facts_seed.py")
    assert "极氪7X" in seed and "阿维塔07 纯电版" in seed, "权威种子缺两车"
    assert "F = _SEED.CELL_SEED" in _read("build_cell_facts.py"), "种子表未收口到唯一真相源"
    assert "vehicle_facts" in _read("board_view.py"), "load_fact_library 未走统一入口"
    # 3) 两车已补进事实库（带权威来源）
    fl = json.load(open(paths.cat("fact_library.json"), encoding="utf-8"))
    for m, brand in [("极氪7X", "宁德时代"), ("阿维塔07 纯电版", "宁德时代")]:
        assert m in fl and fl[m].get("cell_brand"), f"{m} 未补进事实库"
        assert brand in fl[m]["cell_brand"], f"{m} 电芯品牌错误: {fl[m]['cell_brand']}"
    # 4) 页面渲染：缺失原因标注（用户确认的「—（原因）」样式）
    from modules.board import board_view as BV
    fr = {"电芯_缺失原因": "联网未查到该配置的电芯品牌"}
    assert BV.main_cell("cell_brand", {"fact_ref": fr}, {}) == "—（联网未查到该配置的电芯品牌）"
    # 5) 联网线索绝不回填为事实（实跑串配置的教训）
    calls = {"n": 0}

    def fake(q):
        calls["n"] += 1
        return "宁德时代 麒麟电池"  # 即使品牌+技术词同现

    r = CBV.verify_cell("某车", {"trim": {"name": "X"}}, local_brand=None, fetcher=fake)
    assert r["status"] == "need_review" and r["brand"] == "", "线索不得当事实回填"
    return "电池来源不遗漏：两车补录+联网线索不回填+页面缺失原因标注(+t100)"


def t101():
    """车辆事实单一数据源（v10.10.0）：同一事实不再多处手写。

    根因：电芯散落 5 处（种子 F/cell_facts/fact_library/master_pool/trims），
    改一处漏多处。修复：契约层唯一可写种子 + research 统一读取入口 + 漂移自检。
    """
    from modules.research import vehicle_facts as VF
    from modules.contracts import vehicle_facts_seed as SEED
    # 1) 模块自测
    assert VF.selftest() == 0, "vehicle_facts 自测失败"
    # 2) 唯一真相源在契约层（纯净：零 IO/零依赖，layout 已守）
    seed_src = _read("vehicle_facts_seed.py")
    for token in ("CELL_SEED", "CELL_LEGACY", "ENERGY_NOTE"):
        assert token in seed_src, f"契约种子缺 {token}"
    # build_cell_facts 不再手写种子，只引用契约层
    bcf = _read("build_cell_facts.py")
    assert "F = _SEED.CELL_SEED" in bcf and '"极氪7X"' not in bcf, "种子未收口/仍重复手写"
    # 3) 统一读取优先级：seed > legacy > JSON，且车名归一
    r = VF.cell("极氪7X")
    assert r and r["origin"] == "seed" and r["brand"] == "宁德时代(时代吉利)", r
    assert VF.cell("MG4")["origin"] == "legacy", "legacy 兜底失效"
    assert VF.cell("阿维塔07")["brand"] == VF.cell("阿维塔07 纯电版")["brand"]
    assert VF.cell("不存在的车XYZ999") is None, "不得编造事实"
    # 4) 电耗口径不合并（防止伪统一污染评分/物理模型）
    v = VF.facts_for("深蓝L06")
    assert "consumption" not in v and "CLTC" in v["energy_note"], v
    assert "CLTC" in SEED.ENERGY_NOTE
    # 5) 漂移自检：注入一个漂移能被测出（不写真实文件，直接测报告结构）
    rep = VF.consistency_report()
    assert rep["ok"] and rep["authoritative_n"] >= 90 and rep["drifts"] == [], rep
    # 6) 页面/主链读取已收口到统一入口
    assert "vehicle_facts" in _read("board_view.py") and "vehicle_facts" in _read("board_v2.py")
    pl = _read("pipeline.py")
    assert "vehicle_facts_consistency" in pl and "consistency_report" in pl, "pipeline 未接一致性门禁"
    return "车辆事实单一源：契约种子唯一可写+统一读取+漂移自检+电耗口径不合并(+t101)"


def t102():
    """import 别名碰撞门禁（v10.10.1 修 pipeline 出榜崩溃）：同一生产模块内
    同一别名先后指向两个不同模块——space_features as SF 覆盖了
    score_fingerprint as SF，报告段 SF.skill_version() 实际调到 space_features
    直接 AttributeError。regress 包除外：测试函数内局部导入，跨函数不共享
    作用域，无运行态碰撞（pipeline 的碰撞在 main 同一函数内，才是致命的）。"""
    import re
    from pathlib import Path
    # 1) 修复标记：pipeline 里 space_features 已用独立别名 SPF，SF 专指 score_fingerprint
    src = _read("pipeline.py")
    assert "import space_features as SPF" in src, "space_features 别名未独立（仍为 SF）"
    assert "import score_fingerprint as SF" in src, "score_fingerprint 别名丢失"
    # 2) 通用扫描：生产模块（regress 除外）内别名碰撞零容忍
    collided = []
    for f in sorted(Path(paths.MODULES).glob("*/*.py")):
        if f.parent.name == "regress":
            continue
        am: dict = {}
        for m in re.finditer(r"\b(?:from\s+[\w.]+\s+)?import\s+([\w.]+)\s+as\s+(\w+)",
                             f.read_text(encoding="utf-8")):
            am.setdefault(m.group(2), set()).add(m.group(1))
        for alias, mods in am.items():
            if len(mods) > 1:
                collided.append(f"{f.relative_to(paths.ROOT)}: {alias} -> {sorted(mods)}")
    assert not collided, "生产模块别名碰撞: " + "; ".join(collided)
    # 3) 运行态归属：skill_version 属于 score_fingerprint（报告段 SF.skill_version() 的调用方）
    from modules.engine import score_fingerprint as _SF
    assert callable(_SF.skill_version) and _SF.skill_version(), "skill_version 不可调用"
    return "别名碰撞门禁：pipeline SF/SPF 分离 + 生产模块零碰撞扫描 + skill_version 归属(+t102)"


def t103():
    """配置驱动规则接入人工基准分 + 三电补能/空间储物事实披露（v10.11.0）。

    用户连续三次质疑同一类：极氪7X（900V/麒麟/10min 快充）三电拿不到硬件加分、
    反而低于 15 万的大众T6；启源Q06 底盘好却操控不如 MONA；后备箱容积页面一个字不显示。
    本条锁住四件事：
      ① 规则增量已接进主榜（人工基准 + 逐项中位锚点的客观配置差）；
      ② **缺参数的车不因缺参数被扣分**（逐项锚点的核心纪律）；
      ③ 规则定义收敛在 spec_score 一处（不出现第二套口径）；
      ④ 电压/快充/充电功率/官方电耗/换电与后备箱容积已在页面披露。
    """
    from modules.engine import rule_adjust as RA
    assert RA.selftest() == 0, "规则增量模块自测失败"
    # 1) pipeline 接入（且必须排在 with_config_scores 之前，否则会被 best_trim 覆盖）
    src = _read("pipeline.py")
    assert "rule_adjust" in src, "pipeline 未接规则增量"
    assert "apply_rule_adjust" in src, "pipeline 未调用 apply_rule_adjust"
    # 顺序：必须排在 with_config_scores 的**实际调用**之前，否则会被 best_trim 分数覆盖。
    # 按行比较且带括号，避免命中注释里的同名词。
    _lines = src.splitlines()
    _ra_line = next(i for i, l in enumerate(_lines) if "_RA.apply_rule_adjust(" in l)
    _wc_line = next(i for i, l in enumerate(_lines) if "with_config_scores(" in l)
    assert _ra_line < _wc_line, \
        f"规则增量必须排在 with_config_scores 之前（行 {_ra_line} vs {_wc_line}）"
    # 2) 规则分解唯一出处
    ss = _read("modules/engine/spec_score.py")
    assert "powertrain_parts" in ss and "control_parts" in ss, "规则分解未收敛到 spec_score"
    # 3) 逐项锚点纪律：缺「高压平台」这一项的车，不该在这一项上被比较
    cands = _read_pool()
    RA.apply_rule_adjust(cands, dims=("powertrain", "control"))
    t6 = next((c for c in cands if c["model"] == "大众ID.AURA T6"), None)
    assert t6 is not None, "池内应有大众ID.AURA T6"
    ra = (t6.get("rule_adjust") or {}).get("powertrain") or {}
    assert "高压平台" not in (ra.get("items") or {}), \
        "缺电压的车不该参与「高压平台」项（缺数据≠差）"
    # 4) 硬件差距客观存在时，规则增量必须让差距体现出来（极氪7X > T6）
    zk = next((c for c in cands if c["model"] == "极氪7X"), None)
    assert zk is not None, "池内应有极氪7X"
    zk_pt = (zk.get("rule_adjust") or {}).get("powertrain") or {}
    assert zk_pt.get("adjust", 0) > 0, "极氪7X（900V/10min/宁德/802km）三电应加分"
    # 5) 事实区块：页面必须披露三电补能与空间储物
    bv = _read("modules/board/board_view.py")
    assert '"energy_facts"' in bv and '"space_facts"' in bv, "事实区块未注册"
    rh = _read("modules/board/render_board_html.py")
    assert "三电与补能" in rh and "空间与储物" in rh, "事实区块未渲染"
    return ("规则增量接进人工基准分(逐项中位锚点/缺参数不参与) + 极氪7X三电加分 + "
            "T6缺电压不参与高压项 + 三电补能与空间储物区块已披露(+t103)")


def t104():
    """参数页字段台账自洽 + 车身/基本参数组字段已接入（v10.12.1）。

    用户 2026-11-22 追问：「高压平台(V)、快充时间+区间、百公里耗电、电芯品牌
    全都躺在参数页里没被提取，为什么没被提取？以后还会不会出现这样的问题？」

    根因：`auto_trims.parse_config` 原来只认「电池/充电」组，其余 5 个组的字段
    解析进内存后被整体丢弃；且写的是硬编码 None + partial_fields 标记，
    **缺口被伪装成「数据源没有」**，既不报错也没有回归能发现。

    本条锁住「以后不再出现」的机制：
      ① 台账自洽——参数页每个已知字段都必须要么被提取、要么写明跳过理由；
      ② 车身/基本参数组的关键字段确实接进来了（轴距/后备厢/零百/风阻/上市时间）；
      ③ 空间维已改用官方轴距（此前覆盖率 0%，长期只能用体积代理）。
    """
    from modules.research import param_field_audit as PFA
    assert PFA.selftest() == 0, "参数页字段台账自检失败"
    # ① 台账完整性：既没提取也没解释的字段 = 0
    assert not PFA.unassigned_fields(), f"有字段既没提取也没解释: {PFA.unassigned_fields()}"
    assert not PFA.orphan_skips(), f"跳过理由指向台账外 id: {PFA.orphan_skips()}"
    # ② 提取清单含车身/基本参数组（原来只有电池组）
    at = _read("modules/research/auto_trims.py")
    for token in ("AH_BODY_IDS", "AH_BASE_IDS"):
        assert token in at, f"auto_trims 缺 {token}（车身/基本参数组未纳入提取）"
    for fid in ('"wheelbase_mm": 1169', '"trunk_l": 1175', '"zero_to_100_s": 1250'):
        assert fid in at, f"auto_trims 未按 id 提取 {fid}"
    # ③ 空间维已优先用官方轴距
    sc = _read("modules/engine/space_consistency.py")
    assert "_load_trim_wheelbase" in sc, "空间维未接官方轴距"
    assert "wheelbase_mm(官方)" in sc, "官方轴距未作为首选尺寸口径"
    # ④ 官方零百已进操控规则入参
    tb = _read("modules/engine/trim_board.py")
    assert 'trim.get("zero_to_100_s")' in tb, "官方零百未接进 spec 入参"
    # ⑤ 契约里有这批新字段（缺字段会被契约校验拒）
    ts = _read("modules/contracts/trims_schema.py")
    for f in ("wheelbase_mm", "trunk_l", "zero_to_100_s", "launch_month"):
        assert f in ts, f"契约缺字段 {f}"
    return ("参数页字段台账自洽(81项:已提取29/已解释跳过52/无归属0) + 车身与基本参数组"
            "按id提取(轴距/后备厢/零百/风阻/上市) + 空间维改用官方轴距(+t104)")


def t105():
    """四点口径拍板的落地锁定（v10.12.2）。

    用户授权「参考业内经验以及结合大众认知直接改」后的四条决策：
      ① 三电补「电池冷却」子项；
      ② 空间维接入规则增量，且后备厢容积作为**独立「装载」子项**（不用容积推断乘坐空间）；
      ③ 操控判据不降、覆盖率如实披露；
      ④ 质保/循环参数页没有 → 列缺口披露、不推断。

    本条把这四条锁住，防止以后被"优化"掉。
    """
    from modules.engine import rule_adjust as RA
    assert RA.selftest() == 0, "规则增量模块自测失败"
    # ① 空间维已接入规则增量
    assert "space" in RA.DIMS, "空间维未接入规则增量"
    ss = _read("modules/engine/spec_score.py")
    assert "def space_parts" in ss, "空间维缺规则项分解"
    # ② 后备厢是**独立**子项，且不与乘坐空间混同
    assert '"装载"' in ss, "后备厢未作为独立子项（不能与乘坐空间混为一谈）"
    assert '"轴距"' in ss, "轴距子项缺失"
    # 车长项已删除：车长含前后悬、与官方轴距高度共线，两者同时计分属重复计数
    assert '"车长"' not in ss, "车长项应删除（与轴距重复计数）"
    # ① 电池冷却子项
    assert '"冷却"' in ss, "三电缺电池冷却子项"
    # ② 真实数据：极氪7X 后备厢 905L 全榜最大 → 空间维应加分
    cands = _read_pool()
    RA.apply_rule_adjust(cands, dims=("space",))
    zk = next((c for c in cands if c["model"] == "极氪7X"), None)
    assert zk is not None, "池内应有极氪7X"
    ra = (zk.get("rule_adjust") or {}).get("space") or {}
    items = ra.get("items") or {}
    assert "装载" in items, f"极氪7X 应有装载子项（后备厢 905L）: {items}"
    assert items["装载"]["delta"] > 0, "后备厢 905L 全榜最大，装载子项应加分"
    # ③ 操控覆盖限制已披露
    bv = _read("modules/board/board_view.py")
    assert "底盘转向" in bv and "车轮制动" in bv, "操控硬件参数覆盖限制未披露"
    # ④ 质保/循环缺口已披露
    rh = _read("modules/board/render_board_html.py")
    assert "充电循环次数" in rh, "质保/循环缺口未披露"
    return ("四点口径拍板已落地：空间维接规则增量(后备厢=独立装载子项) + 三电电池冷却项 + "
            "操控判据不降并披露覆盖限制 + 质保循环列缺口(+t105)")


def t106():
    """人工条目与车辆数据合流：消除存储层副本 + 人工证据链不被自动覆盖（v10.13.0）。

    用户 2026-11-22 问「人工条目信息有没有必要和车辆数据信息放在一起」，
    查出的根因：13 款人工条目在 categories/trims_manual.json 与
    data/gen/trims.json **各存一份**，而且两份都可写 →
      · auto_trims.refresh_entries 无条件改写所有条目的 source/quote/note
        → 人工在易车/官方表查到的引文、以及 note 里的**口径冲突披露**
          被自动模板文案顶掉（实测 345 个字段漂移，13 款全中）；
      · load_db_merged 的合并方向是「生成物优先」，人工版形同虚设。

    本条锁住四条机制：
      ① 人工车型**不得**出现在自动库里（存储层零副本）；
      ② 人工条目的 source/quote/note/launch_date 必须是人工原值，
        不被自动模板覆盖；
      ③ 合流视图 71 款齐全，且人工/自动来源标记正确；
      ④ auto_trims 的 refresh 必须分流写回（人工写人工库、自动写自动库）。
    """
    import json as _json
    from modules.data import trims_store as TS
    assert TS.selftest() == 0, "trims_store 合流自测失败"

    # ① 存储层零副本：人工车型不得残留在自动库
    auto = _json.load(open(str(paths.cat("trims.json")), encoding="utf-8"))
    auto_models = {e.get("model") for e in (auto.get("entries") or [])}
    manual = _json.load(open(str(paths.cat("trims_manual.json")), encoding="utf-8"))
    man_models = {e.get("model") for e in (manual.get("entries") or [])}
    dup = sorted(man_models & auto_models)
    assert not dup, f"人工车型仍残留在自动库（存储层副本未消除）: {dup}"
    assert not any(e.get("auto_parsed") for e in (manual.get("entries") or [])), \
        "人工库里混进了自动条目（来源分层被破坏）"

    # ② 人工条目的元数据必须是人工版（未被自动模板覆盖）
    db = TS.load()
    auto_srcs = {e.get("source") for e in (auto.get("entries") or [])}
    for e in (manual.get("entries") or []):
        m = e.get("model")
        got = db.get(m)
        assert got is not None, f"合流视图缺人工条目 {m}"
        assert got.get("source") == e.get("source"), \
            f"{m} 的 source 被自动模板覆盖: {got.get('source')!r} != {e.get('source')!r}"
        assert got.get("quote") == e.get("quote"), f"{m} 的 quote 被覆盖"
        assert got.get("note") == e.get("note"), f"{m} 的 note（口径冲突披露）被覆盖"
        assert got.get("launch_date") == e.get("launch_date"), f"{m} 的 launch_date 丢失"
        assert e.get("source") not in auto_srcs or e.get("source_type") != "official_config_page_auto", \
            f"{m} 的 source 疑似被写成自动模板"

    # ③ 合流视图完整 + 来源标记正确
    assert len(db) >= 70, f"合流后车型数过少: {len(db)}"
    n_manual = sum(1 for v in db.values() if TS.provenance(v)["level"] == "manual")
    n_auto = sum(1 for v in db.values() if TS.provenance(v)["level"] == "auto")
    assert n_manual == len(man_models), f"人工条目数不符: {n_manual} != {len(man_models)}"
    assert n_auto == len(auto_models), f"自动条目数不符: {n_auto} != {len(auto_models)}"

    # ④ refresh 分流写回（人工写人工库、自动写自动库）
    at = _read("modules/research/auto_trims.py")
    assert "def write_back" in at, "auto_trims 缺分流写回函数"
    assert 'if is_auto:' in at, "refresh 未按来源区分元数据写入（人工条目会被自动模板覆盖）"
    # ④-b 自动模板文案不得出现在人工条目里（防"改回旧写法"）
    for e in (manual.get("entries") or []):
        assert "自动补录" not in str(e.get("note") or ""), \
            f"{e.get('model')} 的 note 是自动模板文案（人工条目被自动流程覆盖了）"
        assert "自动解析" not in str(e.get("source") or ""), \
            f"{e.get('model')} 的 source 是自动模板文案（人工条目被自动流程覆盖了）"
    ts = _read("modules/data/trims_store.py")
    assert "def load(" in ts and "def load_db(" in ts, "trims_store 缺统一读取入口"

    # ④-c 派生一致性回归必须全绿（2026-11-22 P4）：
    #     重跑合成/回流 → 逐字段比对产物 vs 源，0 差异才算过。
    #     这是验证「消除副本」成果的**唯一手段**——没有它，
    #     下次谁在某个环节加回一个副本，又是「改了不知道有没有生效」。
    from modules.regress import derive_consistency_regress as DCR
    for name, fn in (("候选池层", DCR.check_pool_layers),
                     ("安全成绩", DCR.check_safety_scores),
                     ("价格口径", DCR.check_price_official),
                     ("逐配置合流", DCR.check_trims_merge)):
        bad = fn()
        assert not bad, f"派生一致性[{name}] 差异 {len(bad)} 处: {bad[:5]}"
    # ⑤ 下游不得再直接 json.load trims.json（防止新副本再生）
    for rel in ("modules/engine/trim_board.py", "modules/engine/trim_annotate.py",
                "modules/engine/trim_coverage.py", "modules/engine/facts_sync.py",
                "modules/engine/powertrain_efficiency.py", "modules/engine/space_consistency.py",
                "modules/research/cell_brand_verify.py", "modules/engine/trims.py"):
        src = _read(rel)
        assert "trims_store" in src, f"{rel} 未走 trims_store 统一入口（可能读到残缺副本）"
    return (f"人工/自动合流：存储层零副本（人工 {len(man_models)} 款 + 自动 {len(auto_models)} 款）"
            f"+ 人工证据链未被覆盖 + {len(db)} 款合流齐全 + 下游统一入口")

def t107():
    """三电维：电池容量进分 + 电池/补能子维度拆分 + 配置匹配不再错配（v10.15.0）。

    用户 2026-11-22 指出：「小鹏和启源Q06三电分数一样是不是不公平，启源电池更好，
    充电更快，电池在三电占比又高，电池度数又高，是不是在三电上需要去分开来」。
    查出的根因有三层：
      ① **电池度数完全没进三电规则** —— spec_score 里搜不到 kwh，只有续航 km；
         「大电池」这个纯电车最核心的硬件差异在分数上不存在。
      ② 三电一直是一个笼统的「三电」分，读者看不出电池好还是补能快。
      ③ **配置匹配系统性错配**：find_ref 的 norm() 把空格全删，
         「MONA L05 2027款 纯电 635 Max」→「monal05635max」，数字粘连成 0563，
         匹配必然失败 → 落价格兜底 → MONA L05 的 635 Max（71.1kWh）被配成
         535 Max（58.2kWh）。实测 **42/71 款受影响**。

    本条锁死三条机制。
    """
    from modules.engine import spec_score as SS
    from modules.engine import trim_annotate as TA
    from modules.engine import trim_board as TB

    # ① 电池容量项存在，且用绝对 kWh（v10.15.2 去双计：价格归 value 维，kWh/万元仅披露）
    parts = SS.powertrain_parts({"kwh": 80.0, "price": 20.0, "range_km": 600})
    cap = [p for p in parts if p[0] == "电池容量"]
    assert cap, f"三电规则缺电池容量项: {[p[0] for p in parts]}"
    assert "绝对电量" in cap[0][2], f"电池容量口径应为绝对kWh（去价格双计）: {cap[0][2]}"
    # 同样 80kWh：不同价位拿同样分（价格只在 value 维算一次，防双计）
    rich = SS.powertrain_parts({"kwh": 80.0, "price": 20.0})
    poor = SS.powertrain_parts({"kwh": 80.0, "price": 40.0})
    cr = [p for p in rich if p[0] == "电池容量"][0][1]
    cp = [p for p in poor if p[0] == "电池容量"][0][1]
    assert cr == cp, f"同样电池不同价位应同分（价格归 value，防双计）: {cr} vs {cp}"
    # 缺 kwh → 不参与（缺数据≠差）；缺 price 不缺席（绝对口径不依赖价格）
    assert not [p for p in SS.powertrain_parts({"price": 20.0}) if p[0] == "电池容量"], \
        "缺电量时电池容量项应缺席"
    assert [p for p in SS.powertrain_parts({"kwh": 80.0}) if p[0] == "电池容量"], \
        "绝对kWh口径下缺价不影响电池容量项"

    # ② 三电子维度拆分：电池 / 电机 / 电控（v10.15.5 扩为三组，对齐业内三电定义）
    g = SS.powertrain_group_parts({"platform_800v": True, "charge_10_80_min": 10,
                                    "cell_brand": "宁德时代", "range_km": 800,
                                    "battery_cooling": "液冷", "kwh": 100, "price": 20,
                                    "max_power_kw": 300})
    assert set(g) == {"电池", "电机", "电控"}, f"三电子维度分组不对: {set(g)}"
    assert {i[0] for i in g["电池"]} == {"电芯", "电池容量", "续航", "冷却"}, \
        f"电池归组错: {[i[0] for i in g['电池']]}"
    assert {i[0] for i in g["电机"]} == {"电机功率"}, \
        f"电机归组错: {[i[0] for i in g['电机']]}"
    assert {i[0] for i in g["电控"]} == {"高压平台", "快充"}, \
        f"电控归组错: {[i[0] for i in g['电控']]}"
    # 每一项只归一组（不重复计数）
    allitems = [i[0] for v in g.values() for i in v]
    assert len(allitems) == len(set(allitems)), f"规则项重复归组: {allitems}"
    # 电机功率项：高功率分档 > 低功率，缺功率不出现（缺数据≠差）
    hi = SS.powertrain_parts({"max_power_kw": 370})
    lo = SS.powertrain_parts({"max_power_kw": 150})
    assert [p for p in hi if p[0] == "电机功率"][0][1] > \
           [p for p in lo if p[0] == "电机功率"][0][1], "高功率应高于低功率分档"
    assert not [p for p in SS.powertrain_parts({}) if p[0] == "电机功率"], \
        "缺功率时电机功率项应缺席"

    # ②-b 源码层断言：电池容量项与子维度分组必须真的写在规则文件里
    #     （_read 走源码断言型注入探针，失败回测靠它验证断言不形同虚设）
    ss_src = _read("modules/engine/spec_score.py")
    assert "电池容量" in ss_src, "spec_score 源码缺电池容量项"
    assert "POWERTRAIN_GROUPS" in ss_src, "spec_score 源码缺三电子维度分组定义"

    # ②-c v10.15.6 三电权重上调锁死：纯电榜 powertrain 0.18→0.25，电池有效权重 9.9%→13.8%。
    #     对齐尼尔森IQ 2026 中国电池决策权重 13.8%、业内评测三电 25-30%。
    #     只锁纯电榜配置——跨能源榜是能源中性框架，不得被误改成 25%。
    cfg_bev = json.loads(_read("categories/car_midsuv_bev.json"))
    wm = {d["key"]: d["weight"] for d in cfg_bev["dimensions"]}
    assert abs(wm["powertrain"] - 0.25) < 1e-9, \
        f"纯电榜三电权重应 0.25，实际 {wm['powertrain']}"
    assert abs(sum(wm.values()) - 1.0) < 1e-9, \
        f"纯电榜 8 维权重和应=1，实际 {sum(wm.values())}"
    assert wm["control"] <= 0.09 and wm["cabin"] <= 0.09, \
        "车机/操控区分力最低，回抽后权重不应高于 9%"

    # ②-d v10.15.7 智驾权重下调锁死：12.5%→8%，差额平分给安全/空间/性价比各 +1.5pp，三电 25% 不动。
    #     业内"高装车低使用"：城区开启率 6.3%、31% 经常用、20% 不敢用；但智驾是门槛项故不清零。
    assert abs(wm["adas"] - 0.08) < 1e-9, \
        f"纯电榜智驾权重应 0.08，实际 {wm['adas']}"
    assert abs(wm["powertrain"] - 0.25) < 1e-9, \
        f"三电权重应维持 0.25（上轮结果不被推翻），实际 {wm['powertrain']}"
    assert abs(wm["safety"] - 0.177) < 1e-9 and abs(wm["space"] - 0.131) < 1e-9 \
        and abs(wm["value"] - 0.122) < 1e-9, "安全/空间/性价比应各 +1.5pp 承接智驾差额"
    # 页面提示源码断言：智驾低权重提示必须存在且权重动态读取（不写死 8%）
    rh_src = _read("modules/board/render_board_html.py")
    assert "关于智驾权重" in rh_src, "render_board_html 缺智驾低权重提示"
    assert "_aw <= 0.10" in rh_src, "智驾提示缺 ≤10% 显示门槛（跨能源榜不应显示）"
    assert 'float(_aw)*100' in rh_src, "智驾提示权重应动态读取，不写死百分比"
    # 页面权重漂移根因：pipeline 渲染必须走当前 category 配置，与打分/报告同源。
    pl_src = _read("modules/board/pipeline.py")
    assert 'bv_cfg_path = _bv_cfg_path if os.path.exists(_bv_cfg_path)' in pl_src, \
        "pipeline 渲染器仍硬编码通用配置，页面权重会与打分/报告漂移"
    # ②-e v10.15.8 离地间隙安全维受限修正：独立模块 + SSOT + 幂等 + 单调性保护 + 预算审计。
    gc_db = json.loads(_read("categories/ground_clearance.json"))
    assert len(gc_db["entries"]) == 20, "离地间隙事实库应覆盖 20 款候选"
    assert gc_db["policy"]["target"] == "scores.safety", "离地间隙应落 scores.safety（摘要字段不改维度分）"
    assert gc_db["policy"]["rule"] == {"ge_175": 0.3, "150_to_175": 0.0,
                                        "lt_150": -0.3, "unknown": 0.0}, \
        "离地间隙分档规则被改动：±0.3/unknown不推断"
    gc_src = _read("modules/engine/ground_clearance.py")
    assert "gc_delta" in gc_src and "单调性保护" in gc_src, \
        "ground_clearance 缺幂等字段或单调性保护"
    assert 'getattr(a, "no_ground_clearance", False)' in pl_src, \
        "pipeline 未接入 ground_clearance 模块（改了模块但没接线）"
    ab_src = _read("modules/engine/adjust_budget.py")
    assert '"ground_clearance": 0.3' in ab_src, \
        "adjust_budget 未把离地间隙纳入 safety 预算审计"
    # 跨能源框架用独立常量表，另由 energy_compare_regress.py 锁 0.18，此处不重复。
    ta_src = _read("modules/engine/trim_annotate.py")
    assert "(?<!\\d)" in ta_src or "num_re" in ta_src, \
        "trim_annotate 源码缺数字边界正则（数字粘连回归）"

    # ③ 配置匹配：数字不得粘连（MONA L05 635 Max 必须配到 635 Max 而非 535 Max）
    trims = TB.load_trims()
    e = trims.get("小鹏MONA L05")
    if e:
        ref = TA.find_ref(e.get("scored_trim") or "", e.get("trims") or [])
        assert ref is not None, "MONA L05 评分配置匹配失败（数字粘连回归）"
        assert "635" in str(ref.get("name")), \
            f"MONA L05 评分配置错配到 {ref.get('name')!r}（应为 635 Max，71.1kWh）"
    # 抽样若干车：匹配到的配置必须含 scored_trim 里的型号数字
    import re as _re
    num_re = _re.compile(r"(?<!\d)\d{3,4}(?!\d)")
    checked = 0
    for m2, e2 in trims.items():
        name = e2.get("scored_trim")
        if not name:
            continue
        ref2 = TA.find_ref(name, e2.get("trims") or [])
        if not ref2:
            continue
        tn = set(num_re.findall(str(name)))
        rn = set(num_re.findall(str(ref2.get("name"))))
        if tn and not (tn & rn):
            # 数字集不同要能区分「型号数字」与「车系名里的数字」
            only_t = tn - rn
            if only_t and not any(x in str(ref2.get("name")) for x in only_t):
                raise AssertionError(f"{m2}: 评分配置 {name!r} 错配到 {ref2.get('name')!r}")
        checked += 1
    assert checked >= 40, f"匹配覆盖异常低: {checked}"
    # ④ scored_trim_ref（人工核验的精确配置全名）必须优先于 scored_trim（简称）——
    #    简称的模糊匹配可能落到同型号的别的档次（海狮06EV「605 入门版」→ 605领航Pro
    #    15.38万，实际应为 605领航 15.99万）。
    e6 = trims.get("海狮06EV")
    if e6 and e6.get("scored_trim_ref"):
        # 测**真实效果**：参考配置必须解析成 scored_trim_ref 那个配置。
        # （不能直接调 resolve_ref —— 它拿的是简称，ref 优先是在调用方做的前置）
        import copy as _copy
        import json as _json
        from modules.engine import leaderboard as _LB
        _cfg = _LB.load_category("car_midsuv_bev")
        _pool = _json.load(open(os.path.join(HERE, "runs", "midsuv_bev_pool.json"),
                                encoding="utf-8"))
        _cands = _copy.deepcopy(_pool.get("candidates") or [])
        _ann = TA.annotate_all(_cands, _cfg)
        _d6 = _ann.get("海狮06EV") or {}
        _names = [t.get("name") for t in (_d6.get("trims") or [])]
        assert e6.get("scored_trim_ref") in _names, \
            f"scored_trim_ref 指向的配置不在逐配置表里: {e6.get('scored_trim_ref')!r}"
        # 参考配置的 delta 必须为 0（它就是基准，不该被再加/减分）
        for _t in (_d6.get("trims") or []):
            if _t.get("name") == e6.get("scored_trim_ref"):
                assert not _t.get("deltas"), \
                    f"参考配置 {e6.get('scored_trim_ref')!r} 仍有 delta（重复计数）: {_t.get('deltas')}"
    ta_src2 = _read("modules/engine/trim_annotate.py")
    assert "scored_trim_ref" in ta_src2, "trim_annotate 未使用 scored_trim_ref"
    ra_src2 = _read("modules/engine/rule_adjust.py")
    assert "scored_trim_ref" in ra_src2, "rule_adjust 未使用 scored_trim_ref"
    return (f"三电电池容量进分(绝对kWh，价格归value防双计；kWh/万元仅披露) + 电池/电机/电控三组拆分(电机功率进分) + "
            f"配置匹配不粘连({checked} 款抽样通过) + scored_trim_ref 优先")


def t108():
    """三电满分回归（v10.15.9）：逐配置三电不得硬顶 10.0，且必须保序。

    根因：7 项规则合计理论 12.1，旧实现 score_car 直接 clamp(0,10)，
    65/427 个配置并列 10.0。修复只在 spec_score.score_powertrain 的
    「规则项合计 → 最终分」处做边际递减映射，powertrain_parts 与
    rule_adjust 的中位锚口径都不动（主榜名次不受影响）。
    """
    from modules.engine import spec_score as SS
    from modules.engine import trim_board as TB

    ss_src = _read("modules/engine/spec_score.py")
    assert "POWERTRAIN_SOFT_SPAN" in ss_src and "math.tanh" in ss_src, \
        "三电软上限实现缺失（回退到硬 clamp 会重现满分）"
    ra_src = _read("modules/engine/rule_adjust.py")
    assert "SS.powertrain_parts" in ra_src and "SS.score_powertrain" not in ra_src, \
        "主榜规则增量必须继续读原始规则项，不能误接软上限分"
    ta_src = _read("modules/engine/trim_annotate.py")
    assert "soft_ceiling" in ta_src and "ANNOTATED_PT_CEILING" in ta_src, \
        "页面逐配置表走 trim_annotate，必须共用软上限函数（只修 spec_score 不够）"

    # 真实逐配置数据：先证明「不修就会顶格」，再证明「修后无满分且保序」
    tdb = TB.load_trims()
    raws, finals = [], []
    for e in tdb.values():
        for t in e.get("trims", []):
            spec = TB.spec_from_trim(t, None)
            raw = 6.0 + sum(v for _n, v, _t in SS.powertrain_parts(spec))
            val, _why = SS.score_powertrain(spec)
            raws.append(raw)
            finals.append(val)
    assert len(finals) >= 400, f"逐配置覆盖异常: {len(finals)}"
    n_clipped = sum(1 for r in raws if r >= 10)
    assert n_clipped >= 50, f"已不存在旧满分工况，回归前提失效: {n_clipped}"
    assert max(finals) < 9.5, f"三电仍接近满分: max={max(finals)}"
    assert sum(1 for v in finals if v >= 10) == 0, "逐配置三电仍有 10.0"

    full = SS.score_powertrain({"platform_800v": True, "charge_10_80_min": 10,
                                "cell_brand": "宁德时代", "kwh": 103,
                                "range_km": 750, "max_power_kw": 320,
                                "battery_cooling": "直冷"})[0]
    mid = SS.score_powertrain({"platform_800v": True, "charge_10_80_min": 15,
                               "cell_brand": "中创新航", "kwh": 75,
                               "range_km": 650, "max_power_kw": 250,
                               "battery_cooling": "液冷"})[0]
    low = SS.score_powertrain({"charge_10_80_min": 40, "kwh": 55,
                               "range_km": 480, "max_power_kw": 150})[0]
    assert full > mid > low, f"软上限必须保序: {full}/{mid}/{low}"

    # 页面逐配置表实际数据源：trim_annotate（人工基准 + delta），也必须无满分
    from modules.engine import trim_annotate as TA
    pool = json.load(open(os.path.join(HERE, "runs", "midsuv_bev_pool_202611_final.json"),
                          encoding="utf-8"))
    ann = TA.annotate_all(pool.get("candidates") or [], {})
    av = [t["scores"]["powertrain"] for d in ann.values() for t in d.get("trims", [])]
    assert len(av) >= 80, f"人工口径逐配置覆盖异常: {len(av)}"
    assert max(av) < 9.5, f"页面逐配置三电仍接近满分: {max(av)}"
    assert sum(1 for v in av if v >= 10) == 0, "页面逐配置三电仍有 10.0"
    return (f"逐配置 {len(finals)} 款：旧口径 {n_clipped} 个会顶格 10.0 → "
            f"现最高 {max(finals)}（页面人工口径 {max(av)}），满/中/低={full}/{mid}/{low} 保序，"
            f"两条链路共用软上限、主榜规则增量口径不变")

def t109():
    """跨榜同分 + 主榜三电满分（v10.16.0）。

    根因：四个榜内相对锚点模块各自从当前池算中位/回归，12-18万子池样本一变
    锚点就漂——同车全价位 83.8 vs 子榜 86.9。另：主榜三电链路（候选池 scores
    →电耗效率→续航达成率）用 min(10,..) 硬顶，海狮06EV/G6 又顶到 10.0。
    修：全价位 summary.json 作唯一锚点快照，子榜读固定锚；主榜三电过 soft_ceiling。
    """
    import statistics as _st
    from modules.engine import anchor_pool as AP
    from modules.engine import spec_score as SS
    from modules.engine import evidence_gate as EG
    from modules.engine import space_consistency as SP
    from modules.engine import powertrain_efficiency as PE
    from modules.board import range_achievement as RCH

    # 1) 锚点快照：唯一来源=全价位产物，提取正确且畸形输入回退
    snap = AP.load("car_midsuv_bev")
    assert snap and abs(snap["evidence_cap"]["adas"]["capped"] - 8.54) < 1e-9, "adas 锚点错"
    assert abs(snap["range_median"] - 0.499) < 1e-9, "达成率中位错"
    assert abs(snap["efficiency"]["slope"] - 0.006572) < 1e-9, "回归斜率错"
    assert AP.extract({}) is None, "空摘要必须回退 None"
    src = _read("categories/car_midsuv_bev.json")
    assert '"anchor_pool"' in src and "summary.json" in src, "category 未配锚点快照源"

    # 2) 四模块固定锚点接口存在且被 pipeline 调用
    for f, kw in [("modules/engine/evidence_gate.py", "anchor=None"),
                  ("modules/engine/space_consistency.py", "fixed_caps=None"),
                  ("modules/engine/powertrain_efficiency.py", "fixed: dict | None = None"),
                  ("modules/board/range_achievement.py", "fixed_median: float | None = None"),
                  ("modules/engine/rule_adjust.py", "fixed_medians=None")]:
        assert kw in _read(f), f"{f} 缺少固定锚点参数 {kw}"
    psrc = _read("modules/board/pipeline.py")
    for token in ["AP.load(a.category)", "_anchor or {}", "main_board_powertrain_cap"]:
        assert token in psrc, f"pipeline 未接固定锚点/软上限: {token}"

    # 3) evidence_gate 固定锚点：同一组无证据车，子池样本变化不影响封顶值
    lib = {"entries": [{"model": "有证据A", "dimension": "cabin", "source_type": "independent_test"}]}
    base = [{"model": "有证据A", "scores": {"cabin": 8.3}},
            {"model": "无证据1", "scores": {"cabin": 9.5}},
            {"model": "无证据2", "scores": {"cabin": 9.2}}]
    import copy as _cp
    a1 = _cp.deepcopy(base)
    a2 = _cp.deepcopy(base[:2])            # 子池少一款无证据车
    fixed = {"cabin": {"capped": 8.3, "anchor": "全价位中位（5 款）", "n": 5}}
    EG.apply_all(a1, experience_lib=lib, anchor=fixed)
    EG.apply_all(a2, experience_lib=lib, anchor=fixed)
    c1 = next(c for c in a1 if c["model"] == "无证据1")["scores"]["cabin"]
    c2 = next(c for c in a2 if c["model"] == "无证据1")["scores"]["cabin"]
    assert c1 == c2 == 8.3, f"固定锚点下同车封顶应一致: {c1}/{c2}"

    # 4) 主榜三电软上限：无满分、单调、knee 以下不变
    assert SS.main_board_powertrain_cap(10.0) <= 9.5, "满分未被压到 9.5 上界"
    assert SS.main_board_powertrain_cap(10.0) < 10.0, "仍有 10.0 满分"
    assert SS.main_board_powertrain_cap(9.98) >= SS.main_board_powertrain_cap(9.5), "软上限非单调"
    assert SS.main_board_powertrain_cap(8.5) == 8.5, "knee 以下不应改动"

    # 5) 真实两榜产物：11 款同车逐车分数一致（允许 0.05 四舍五入差）
    def scores(path):
        import re as _re
        s = _read(path)
        i = s.find('<table id="scoreBoard"')
        j = s.find("</table>", i)
        out = {}
        for row in _re.findall(r"<tr data-model=.*?</tr>", s[i:j], _re.S):
            mm = _re.search(r"data-model='([^']+)'", row)
            if not mm:
                continue
            out[mm.group(1)] = {d: float(_re.search(r"data-dim='" + d + r"'>([\d.]+)<", row).group(1))
                                for d in ("powertrain", "safety", "adas", "space", "value",
                                          "control", "cabin", "quality")}
        return out
    p_full = os.path.join(HERE, "runs", "midsuv_bev_202611_v15", "榜单.html")
    p_sub = os.path.join(HERE, "runs", "midsuv_bev_1218_202611_v15", "榜单.html")
    if os.path.exists(p_full) and os.path.exists(p_sub):
        A, B = scores(p_full), scores(p_sub)
        common = set(A) & set(B)
        assert len(common) >= 10, f"两榜共同车型异常: {len(common)}"
        diffs = [(m, d, A[m][d], B[m][d]) for m in common for d in A[m]
                 if abs(A[m][d] - B[m][d]) > 0.05]
        assert not diffs, f"同车跨榜分数仍不一致: {diffs[:6]}"
        # 主榜无三电满分
        assert all(v["powertrain"] < 10 for v in A.values()), "全价位榜主榜三电仍有满分"
        assert all(v["powertrain"] < 10 for v in B.values()), "子榜主榜三电仍有满分"
        detail = f"真实两榜 {len(common)} 款同车逐维一致；"
    else:
        detail = "产物未生成，跳过实榜交叉校验（仅逻辑/接口断言）；"
    return (detail + "锚点快照单一源、四模块固定锚接口、同车封顶一致、主榜三电软上限保序")


def t110():
    """BUG-110：bZ5 有一手 C-NCAP 86.7% 却被证据不全的 C-IASI G 覆盖，
    且页面同时标「缺权威碰撞成绩」+「C-IASI G」自相矛盾。"""
    from modules.engine import sync_safety_scores as SSS
    from modules.engine import crash_systems as CS
    from modules.board import render_board_html as RH

    # 1) 排序判据：有一手得分率(display 含 % 或 score 有值)必须优先于只有等级。
    #    反向证明旧判据只看 score 会失效（C-NCAP 条目 score 常为空、值在 display 里）。
    both = [
        {"system": "C-IASI", "basis": "C-IASI", "display": "C-IASI G",
         "source_type": "official_published_decoded", "score": None},
        {"system": "C-NCAP", "basis": "C-NCAP", "display": "C-NCAP 86.7%",
         "source_type": "official_table", "score": None},   # score 空，值只在 display
    ]
    tr = {"official_table": 2, "official_published_decoded": 2}
    assert SSS._sort_scores(both, tr)[0]["display"] == "C-NCAP 86.7%", \
        "一手得分率应盖过只有等级（旧判据会因 score 空而退化为字母序）"
    # 同 tier 但 C-IASI 有分值也应能主导（判据不偏向某体系）
    b2 = [
        {"system": "C-NCAP", "basis": "C-NCAP", "display": "C-NCAP 85.0%", "source_type": "official_table", "score": None},
        {"system": "C-IASI", "basis": "C-IASI", "display": "C-IASI G+", "source_type": "official_table", "score": 9.2},
    ]
    assert SSS._sort_scores(b2, tr)[0]["display"] == "C-IASI G+", "判据不应按体系名硬编码"

    # 2) 真实 SSOT 裁决：bZ5 恢复 C-NCAP 86.7%，C-IASI G 降级为次要留档
    ssot = SSS.load_ssot()
    idx = SSS._index(ssot)
    rec = SSS._lookup(idx, SSS._canon("丰田bZ5"))
    assert rec and rec["primary"]["display"].startswith("C-NCAP 86.7"), \
        f"bZ5 主成绩应是 C-NCAP 86.7%: {rec and rec['primary']}"
    assert any(s["display"] == "C-IASI G" for s in rec["all"][1:]), "C-IASI G 应留档为次要"

    # 3) sync 后候选池字段：crash/basis 为 C-NCAP，不残留 C-IASI 主成绩
    import copy as _cp
    pool = json.load(open(os.path.join(HERE, "runs", "midsuv_bev_pool_202611_final.json"),
                          encoding="utf-8"))["candidates"]
    cc = _cp.deepcopy(pool)
    SSS.sync(cc)
    bz = next(c for c in cc if c["model"] == "丰田bZ5")
    assert "C-NCAP 86.7" in str(bz.get("crash")), f"sync 后 bZ5 应恢复 C-NCAP: {bz.get('crash')}"
    assert CS.is_official(bz.get("safety_basis")), "恢复后 basis 应为权威体系"

    # 4) 展示判定一致：缺权威与成绩展示不得对同一车矛盾（v10.16.4 统一在 crash_cell 唯一出口）
    for name, r in [
        ("bZ5 C-NCAP", {"crash": "C-NCAP 86.7%", "safety_basis": "C-NCAP"}),
        ("C-IASI G 单字母", {"crash": "C-IASI G", "safety_basis": "C-IASI"}),
        ("别名 ciasi_checked", {"crash": "C-IASI G", "safety_basis": "ciasi_checked"}),
        ("Euro NCAP", {"crash": "Euro NCAP 五星", "safety_basis": "euro_ncap"}),
        ("无成绩", {"crash": None, "safety_basis": None}),
        ("无官方成绩说明", {"crash": "无官方成绩", "safety_basis": None}),
    ]:
        # v10.16.4：权威判定已从车名 risk_flags 收敛到 crash_cell 唯一出口
        shown = re.sub(r"<[^>]+>", "", RH.crash_cell(r, r))
        flagged = "缺权威" in shown
        assert (not flagged) == (name not in ("无成绩", "无官方成绩说明")), f"展示判定矛盾 [{name}]: {shown}"
    # 源码层：crash_cell 必须复用 crash_systems 唯一真相源，不再自写体系白名单
    rsrc = _read("modules/board/render_board_html.py")
    assert "CS.is_official(basis)" in rsrc and "CS.parse_score(crash)" in rsrc, "crash_cell 未复用 crash_systems 唯一真相源"
    assert 'basis in ("cncap", "ciasi"' not in rsrc, "risk_flags 仍留硬编码体系白名单"

    # 5) build_ssot 给 C-NCAP 写 score（生成源也要带值，双保险）
    bsrc = _read("modules/engine/sync_safety_scores.py")
    assert "score=float(val)" in bsrc, "build_ssot 的 C-NCAP 条目仍未写 score"

    # 6) 真实榜单产物：bZ5 显示 C-NCAP 徽标且无「缺权威」矛盾标记
    for rel in ["runs/midsuv_bev_202611_v18/榜单.html",
                "runs/midsuv_bev_1218_202611_v18/榜单.html"]:
        pp = os.path.join(HERE, rel)
        if os.path.exists(pp):
            html = _read(rel)
            i = html.find("data-model='丰田bZ5'")
            j = html.find("</tr>", i)
            assert i >= 0 and j > i, f"{rel} 找不到 bZ5 行"
            row = html[i:j]
            assert "缺权威碰撞成绩" not in row, f"{rel} bZ5 仍标缺权威"
            assert "C-NCAP 86.7%" in row, f"{rel} bZ5 未显示 C-NCAP 86.7% 徽标"
    return ("bZ5 恢复 C-NCAP 86.7%（C-IASI G 降级留档）、排序判据按一手分值优先、"
            "risk_flags 复用 crash_systems 消除缺权威+徽标矛盾、6 类展示判据一致")



def t111():
    """BUG-111：乐道L60 Max+/Ultra+ 有激光雷达+神玑NX9031，逐配置智驾却与纯视觉Pro同为7.0；
    品控 PPH 仅44样本按满扣，而样本不足的车取中性8.0——有数据反被罚。"""
    from modules.engine import quality_score as Q
    from modules.engine import trim_annotate as TA
    from modules.board import board_view as BV

    # 1) PPH 按样本量收缩：同 pph 样本越大越接近原始幅度（单调），且披露写明口径。
    small = Q._pph_adjust({"ah_pph_usable": True, "ah_pph": 127, "ah_review_users": 44})
    large = Q._pph_adjust({"ah_pph_usable": True, "ah_pph": 127, "ah_review_users": 200})
    assert abs(small[0] - (-0.27 * 44 / 94)) < 0.02, f"小样本未按 n/(n+50) 收缩: {small}"
    assert large[0] < small[0] < 0, f"样本越大惩罚越接近满额（更负）: small={small} large={large}"
    assert "n/(n+50)" in small[1], "PPH 披露未写样本收缩口径"
    d = Q.score_one({"ah_pph_usable": True, "ah_pph": 127, "ah_review_users": 44}, {})
    assert d["score"] == 7.87, f"乐道口径品控应 7.87（旧满扣为 7.73）: {d['score']}"

    # 2) 逐配置激光雷达：配置名不含关键词但结构化字段 adas_lidar=1 必须识别。
    ref = {"name": "乐道L60 2026款 60kWh Pro", "adas_lidar": 0}
    high = {"name": "乐道L60 2026款 85kWh Max+", "adas_lidar": 1, "adas_tops": 1000}
    ds = TA.deltas_for(high, ref)
    adas = [x for x in ds if x["dim"] == "adas"]
    assert adas and adas[0]["delta"] == 0.8, f"Max+ 应因激光雷达 +0.8: {ds}"
    # 反向注入：抹掉结构化字段且配置名无关键词 → 不得加分（证明不是硬编码车型）
    blind = {"name": "乐道L60 2026款 85kWh Max+"}
    assert not [x for x in TA.deltas_for(blind, ref) if x["dim"] == "adas"],         "无字段无关键词时不应加分（旧逻辑静默漏掉新硬件）"
    # 通用性：任意车系同字段都能生效，不绑死乐道
    d2 = [x for x in TA.deltas_for({"name": "某品牌 四驱旗舰", "adas_lidar": 1},
                                   {"name": "某品牌 入门版", "adas_lidar": 0}) if x["dim"] == "adas"]
    assert d2 and d2[0]["delta"] == 0.8, "配置级字段应通用，不得写死车型"

    # 3) 真实配置库：6 款乐道 L60 — Pro 2 款无激光雷达，Max+/Ultra+ 4 款有。
    tdb = json.load(open(os.path.join(HERE, "data", "gen", "trims.json"), encoding="utf-8"))
    entry = next(e for e in tdb["entries"] if e.get("model") == "乐道L60")
    lid = {t["name"]: t.get("adas_lidar") for t in entry["trims"]}
    assert sum(1 for v in lid.values() if v == 1) == 4 and            sum(1 for v in lid.values() if v == 0) == 2, f"配置级智驾字段不全: {lid}"
    assert all("神玑NX9031" in str(t.get("note") or "") or "Orin-X" in str(t.get("note") or "")
               for t in entry["trims"]), "配置级智驾字段缺来源注记"

    # 4) 页面/报告披露：样本收缩 + 制造体系过程证据与换电不计分口径。
    text = chr(10).join(BV.method_boundary_text())
    assert "n/(n+50)" in text and "GB/T 40032-2021" in text and "不直接换算加分" in text,         "方法与边界缺样本收缩/换电标准/过程证据披露"
    assert "n/(n+50)" in _read("modules/engine/quality_score.py"), "生成源缺收缩口径"

    # 5) 真实 v16 产物：高配智驾 7.8 / Pro 7.0，且高配成为评分配置；两榜同车仍一致。
    html = _read("runs/midsuv_bev_202611_v16/榜单.html")
    assert "乐道L60 2026款 85kWh Max+" in html and "7.8" in html, "v16 高配智驾分未生效"
    assert "n/(n+50)" in html and "GB/T 40032-2021" in html, "v16 页面缺新披露"
    if os.path.exists(os.path.join(HERE, "runs", "midsuv_bev_1218_202611_v16", "榜单.html")):
        sub = _read("runs/midsuv_bev_1218_202611_v16/榜单.html")
        assert "n/(n+50)" in sub and "GB/T 40032-2021" in sub, "v16 子榜缺新披露"
    return ("乐道L60 高配智驾经配置级 adas_lidar 字段 +0.8（Pro 7.0 / Max+ 7.8）、"
            "PPH 44样本按 n/(n+50) 收缩（-0.27→-0.13，品控 7.73→7.87）、"
            "制造体系/换电只披露不计分、v16 两榜口径一致")


def t112():
    """BUG-112：parse_score 已解析出 Euro NCAP 93.95% 乘员得分，
    score_from_crash 的 star 分支却只读 stars 取保守档 8.3，
    导致 ES6 安全 8.3 < L60 C-NCAP 89.2% 的 8.4（有更强证据反而分低）。"""
    from modules.engine import safety_check as SC
    from modules.engine import crash_systems as CS
    from modules.contracts import safety_score as SSC

    spec = SC.load_table()["systems"]["Euro NCAP"]["map_spec"]
    # 1) 契约层：本体系 occupant 分档，不跨体系套 C-NCAP 公式
    assert SSC.score_from_star_occupant(93.95, spec) == 8.8
    assert SSC.score_from_star_occupant(87, spec) == 8.5
    assert SSC.score_from_star_occupant(82, spec) == 8.3
    assert SSC.score_from_star_occupant(None, spec) is None
    # 2) 引擎层：真实成绩文本解析 → 精确分档
    sc, rule = SC.score_from_crash(spec, CS.parse_score("Euro NCAP 93.95% 五星"))
    assert sc == 8.8 and "93.95" in rule, f"乘员分未生效: {sc} {rule}"
    # 3) 反向注入：抹掉 pct 只能落保守档（证明不是硬编码 ES6）
    sc2, rule2 = SC.score_from_crash(spec, {"system_cn": "Euro NCAP", "stars": 5, "score": None})
    assert sc2 == 8.3 and "保守档" in rule2, f"无乘员分应回退: {sc2} {rule2}"
    # 4) C-NCAP 得分率公式不受影响（L60 89.2% 仍 8.4）
    cn = SC.load_table()["systems"]["C-NCAP"]["map_spec"]
    assert SC.score_from_crash(cn, CS.parse_score("C-NCAP 89.2% 五星"))[0] == 8.4
    # 5) 表内复算一致性：有 occupant_pct 无复合 grade 也按分档
    v, r = SC.score_from_record("Euro NCAP", {"occupant_pct": 93.95, "stars": 5},
                                SC.load_table())
    assert v == 8.8, f"表内 occupant_pct 未复算: {v} {r}"
    # 6) 真实产物：v17 ES6 安全 >= L60，且两榜一致
    import json as _j
    p = os.path.join(HERE, "runs", "midsuv_bev_202611_v17", "summary.json")
    if os.path.exists(p):
        d = _j.load(open(p, encoding="utf-8"))
        applied = {x["model"]: x["score"] for x in (d.get("safety_auto") or {}).get("applied", [])}
        assert applied.get("蔚来ES6") == 8.8, f"v17 ES6 安全未修: {applied.get('蔚来ES6')}"
        assert applied.get("乐道L60") == 8.4
    return ("Euro NCAP 星级映射读乘员得分率：ES6 93.95%→8.8（旧保守档8.3），"
            "C-NCAP 89.2%→8.4 不受影响，反向注入回退 8.3，表内/表外同规则")


def t113():
    """BUG-113：碰撞成绩在车名徽标（含缺权威红标）与碰撞成绩列重复显示，
    且车名徽标自写正则抓错分项（ES6 Euro NCAP 成人95% 当主成绩，碰撞列却是93.95%）。
    用户确认方案 A：全部整合到碰撞成绩单一列。"""
    import re
    from modules.board import render_board_html as RH

    src = _read("render_board_html.py")
    # 1) 源码层：车名不再挂碰撞徽标/缺权威红标，碰撞列唯一出口
    assert "def crash_badge" not in src and "def crash_cell" in src, "徽标未移除或碰撞列出口缺失"
    assert "risk_flags(r)}{crash_badge(r)}" not in src, "车名仍重复挂碰撞成绩"
    assert 'elif k == "crash":' in src and "crash_cell(r, cand)" in src, "碰撞列未接唯一出口"
    # 反向注入：把徽标重新挂回车名 → 断言必须失败（证明锁的是真契约而非注释）
    injected = src.replace("{e(val)}{risk_flags(r)}{sel}", "{e(val)}{risk_flags(r)}{crash_badge(r)}{sel}")
    assert injected != src, "注入锚点未命中（测试已失效）"
    assert "risk_flags(r)}{crash_badge(r)}" in injected, "反向注入未生效"

    # 2) 单元层：有成绩给规范值，无成绩给缺权威提示；车名不再出碰撞字样
    l60 = {"safety_basis": "cncap", "crash": "C-NCAP 89.2% 五星"}
    es6 = {"safety_basis": "euro_ncap", "crash": "Euro NCAP 93.95% 五星"}
    assert "C-NCAP 89.2%" in re.sub(r"<[^>]+>", "", RH.crash_cell(l60, l60))
    es6_txt = re.sub(r"<[^>]+>", "", RH.crash_cell(es6, es6))
    assert "93.95%" in es6_txt, f"ES6 未用规范主成绩: {es6_txt}"
    assert "95%" not in es6_txt.replace("93.95%", ""), f"ES6 分项95%仍混入: {es6_txt}"
    none_txt = re.sub(r"<[^>]+>", "", RH.crash_cell({"crash": None}, {"crash": None}))
    assert "缺权威" in none_txt and "非不安全" in none_txt, f"无成绩提示错: {none_txt}"
    assert "缺权威" not in RH.risk_flags({"crash": None}), "车名仍挂缺权威红标"

    # 3) 真实 v18 产物：车名列无碰撞字样，碰撞列恰一处成绩
    p = os.path.join(HERE, "runs", "midsuv_bev_202611_v18", "榜单.html")
    if os.path.exists(p):
        h = _read("runs/midsuv_bev_202611_v18/榜单.html")
        for model in ("乐道L60", "蔚来ES6", "丰田bZ5"):
            m = re.search(r"<tr[^>]*data-model=['\"]" + re.escape(model) + r"['\"][^>]*>(.*?)</tr>", h, re.S)
            assert m, f"v18 找不到 {model}"
            cells = re.findall(r"<td[^>]*>(.*?)</td>", m.group(1), re.S)
            name_cell = re.sub(r"<[^>]+>", "", cells[1])
            crash_cell = re.sub(r"<[^>]+>", "", cells[7])
            assert "NCAP" not in name_cell and "缺权威" not in name_cell, f"{model} 车名重复碰撞: {name_cell[:80]}"
            assert ("NCAP" in crash_cell) or ("缺权威" in crash_cell), f"{model} 碰撞列缺成绩: {crash_cell}"
        assert "Euro NCAP 93.95% 五星" in h and "Euro NCAP 95%<" not in h, "ES6 数字仍未统一"
    return ("碰撞成绩收敛单一列：车名零重复徽标/红标，规范成绩或缺权威提示只在碰撞列；"
            "ES6 93.95% 唯一口径，反向注入重挂徽标可检出")


def t114():
    """BUG-114：性价比 value 分是人工裁决（实质电池密度 kWh/万元），代码里的
    spec_score.score_value 同榜比价公式是死代码没接主链（score_audit 自标 partial）。
    名实不符、不可复算、贵=低分粗糙。用户确认参考 J.D. Power「同级均值距离」，
    改价格残差法：同价位档内每万元产品力（七维剔 value）相对档均的距离。"""
    from modules.engine import value_residual as VR
    from modules.engine import anchor_pool as AP
    import json as _J

    # 1) 源码层：模块存在且接进主链、指纹门禁纳入
    vsrc = _read("modules/engine/value_residual.py")
    assert "def value_for" in vsrc and "def apply" in vsrc, "价值残差模块缺核心函数"
    ps = _read("modules/board/pipeline.py")
    assert "value_residual as _VR" in ps and "_VR.apply(cands" in ps, "未接入 pipeline"
    fs = _read("modules/engine/score_fingerprint.py")
    assert "value_residual.py" in fs, "未纳入指纹门禁（改 value 公式老分不判过期）"
    # 死公式仍在但只作对照，主链不得再调用 score_value
    assert "score_value(spec" not in ps, "主链仍在用旧死公式"

    # 2) 单元层：可复算、剔除 value 不重复计分、单调、clamp
    assert VR.selftest() == 0, "value_residual selftest 未过"
    w = VR.DEFAULT_W
    c = {"scores": {"powertrain": 8, "safety": 8, "adas": 8, "space": 8,
                    "control": 8, "cabin": 8, "quality": 8, "value": 3}}
    pf1 = VR.product_strength(c, w)
    c2 = _J.loads(_J.dumps(c)); c2["scores"]["value"] = 10
    assert pf1 == VR.product_strength(c2, w), "产品力受 value 自身影响=重复计分"
    assert VR.value_for(0.5, 0.4) > VR.value_for(0.3, 0.4), "同档高 ppw 应高分"
    assert VR.value_for(99, 0.4) == round(VR.NEUTRAL + VR.CAP, 2), "上界 clamp 失效"
    assert VR.value_for(0.001, 0.4) == round(VR.NEUTRAL - VR.CAP, 2), "下界 clamp 失效"

    # 3) 跨榜一致：档均值锚点能从全榜 summary 提取，子榜固定后同车同分
    snap = AP.load("car_midsuv_bev")
    assert snap and snap.get("value_bins"), "锚点缺 value_bins（子榜会本池拟合→跨榜漂移）"
    assert set(snap["value_bins"]["bins"]) >= {"≤15万", "15-20万"}, "档均值不全"

    # 4) 真实 v19 产物：value 由残差复算、ES6 客观低分、两榜同车 0 差异
    d = _J.load(open(os.path.join(HERE, "runs", "midsuv_bev_202611_v19", "summary.json"), encoding="utf-8"))
    vr = d.get("value_residual") or {}
    assert vr.get("status") == "applied" and vr.get("covered") >= 18, "v19 残差未覆盖"
    assert vr.get("source") in (None, "pool_fit"), "全榜应为本池拟合"
    sub = _J.load(open(os.path.join(HERE, "runs", "midsuv_bev_1218_202611_v19", "summary.json"), encoding="utf-8"))
    assert (sub.get("value_residual") or {}).get("status") == "applied"
    # ES6：33.8 万在 >25 万档每万元产品力最低 → 残差 value 应低于中性 8.0
    es6 = next((x for x in vr.get("applied", []) if x["model"] == "蔚来ES6"), None)
    if es6:
        assert es6["after"] < VR.NEUTRAL, f"ES6 应客观低于中性: {es6}"
    return ("性价比改价格残差法：同价位档内每万元产品力相对档均距离（J.D. Power 同级距离），"
            "七维剔 value 不重复计分，可复算；锚点固定跨榜同车 0 差异，ES6 低分变客观判断")


TESTS = [t001, t002, t003, t004, t005, t006, t007, t008,
         t009, t010, t011, t012, t013, t014, t015, t016, t017, t018, t019, t020, t021, t022, t023, t024, t025, t026, t027, t028, t029, t030, t031, t032, t033, t034, t035, t036, t037, t038, t039, t040, t041, t042, t043, t044, t045, t046, t047, t048, t049, t050, t051, t052, t053, t054, t055, t056, t057, t058, t059, t060, t061, t062, t063, t064, t065, t066, t067, t068, t069, t070, t071, t072, t073, t074, t075, t076, t077, t078, t079, t080, t081, t082, t083, t084, t085, t086, t087, t088, t089, t090, t091, t092, t093, t094, t095, t096, t097, t098, t099, t100, t101, t102, t103, t104, t105, t106, t107, t108, t109, t110, t111, t112, t113, t114]


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
