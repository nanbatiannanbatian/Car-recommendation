#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
negative_regression.py —— 失败回测框架（2026-11-20 用户强制）

用户要求：每条回归必须证明"注入错误参数时测试会 FAIL"，否则只测正确路径的测试是摆设。

两种注入机制（都不永久改文件）：
1. 源码断言型：monkeypatch bug_regression._read，把断言依赖的关键字符串从返回内容抹掉。
   若测试断言该字符串存在，抹掉后必 FAIL → 证明断言有效。
2. 数据断言型：monkeypatch json.load，对目标文件返回篡改对象（删字段/改值）。

NEG_TARGETS：测试名 -> 断言依赖的关键字符串（从该测试真实 assert 提取）。
框架自动跑：注入 → 测试必须 FAIL → 还原。检出率 100% 才算合格。
"""
import contextlib, io, json, os, re, sys

import paths

HERE = str(paths.ROOT)
# 每条测试断言依赖的关键源码字符串（抹掉后该测试应 FAIL）
NEG_TARGETS = {
    "t002": ["import trim_annotate as TA"],
    "t006": ["三电得分理由"],
    "t008": ["best_trim_detail"],
    "t012": ["cands_for_html"],
    # t014 断言是 A or B，两个都要抹掉
    "t014": ["sales_upper_bound", "startswith(\"≤\")"],
    "t015": ["BV.main_cell"],
    "t017": ["ref_fallback"],
    "t018": ["v.get(\"fleet\")"],  # render断言目标（verdict运行态无法用_read抹）
    "t020": ["本榜导航"],
    "t024": ["stability"],
    "t026": ["候选池与证据披露"],
    "t027": ["ev_soft"],
    "t028": ["维度子项依据"],
    "t029": ["pool_disclosure"],
    "t030": ["车系月销榜前 10"],
    "t031": ["不是全市场全价位"],
    "t032": ["未入池车型及原因"],
    "t033": ["check_universe"],
    "t016": ["version:"],  # 抹SKILL版本号断言FAIL
    "t035": ["基准配置（无配置级差异）"],
    "t038": ["弗迪[二代/非闪充]"],
    "t041": ["未入池车型及原因"],
    "t042": ["no_cell"],
    "t043": ["is_placeholder_ref"],
    "t044": ["指纹门禁"],
    "t045": ['p.name == "research"'],
    "t046": ["统一证据采集（内建"],
    "t047": ["t1_max_norm_swing"],
    "t048": ["check_universe"],
    "t049": ["销量数据缺失"],
    "t051": ["with_config_scores"],
    "t052": ["check_internal_consistency"],
    "t053": ["同源车一致性门禁"],
    "t054": ["安全分严谨性门禁"],
    "t055": ['"crash": c.get("crash")'],
    "t056": ["has_crash_evidence"],
    "t057": ["strong_pick"],
    "t058": ["def crash_badge"],
    "t059": ["为何未进一档"],
    "t060": ["_lc = BView.check_lifecycle_coverage(cands)"],
    "t061": ["独立算法家族命中"],
    "t062": ["t1_min_family_hits"],
    "t063": ["证据文本一致性门禁"],
    "t064": ["crash_systems"],
    "t065": ["rank_mean_by_model"],
    "t066": ["稳定地排第5左右"],
    "t067": [".strong-main"],
    # t090：抹掉 C-NCAP 回流/展示门禁断言依赖（回到「表里有成绩、页面空白无人拦」），应 FAIL
    "t090": ["backfill_crash_from_cncap_table"],
    # t091：抹掉新闻稿救回入口定义（回到「救回只存在手动操作」），应 FAIL
    "t091": ["def rescue_targets"],
    # t092：抹掉 paths.py 里的名单定义（回到「无代码化排除规则、样本混进包」），应 FAIL
    "t092": ["REGRESSION_FIXTURES"],
    # t093：抹掉零JSON总闸门常量（回到「JSON又会混进包」），应 FAIL
    "t093": ["EXCLUDE_ALL_JSON"],
    # t094：抹掉同源映射表（回到「出口版同源车成绩被一刀切拒、ES6判无成绩」），应 FAIL
    "t094": ["EXPORT_ALIASES"],
    # t095：抹掉 C-NCAP 官网解析标记（回到「出榜不联网核验碰撞成绩」），应 FAIL
    "t095": ["cncap-swiper"],
    # t096：抹掉电耗修正开关（回到「三电维度无视系统能效」），应 FAIL
    "t096": ["no-powertrain-efficiency"],
    # t097：抹掉同步脚本 .json 排除标记（回到「GitHub 又存 JSON」），应 FAIL
    "t097": ['".json"'],
    # t098：抹掉逐车门禁入口（回到「只扫首页 + 断网静默沿用」），应 FAIL
    "t098": ["def coverage_gate"],
    # t099：抹掉按天缓存命中判定（回到「每次重复抓取、无当天复用」），应 FAIL
    "t099": ["def _cache_valid"],
    # t100：抹掉联网电芯线索发现（回到「查不到就静默留空」），应 FAIL
    "t100": ["def parse_brands"],
}

# 数据断言型：测试名 -> (文件路径, 车型/键, 篡改模式)
NEG_DATA = {
    "t001": ("trims.json", "MG 07 纯电", "delete_cltc"),      # 删trims的cltc_km→续航映射断言FAIL
    "t003": ("trims.json", "深蓝L06", "rename_trim"),
    "t007": ("runs/midsedan_bev_1218_202610/board_v2_data.json", "MG 07 纯电", "same_score_trims"),
    "t011": ("car_midsedan_allprice.json", "price_band", "tighten_band"),
    "t021": ("trims.json", "零跑B01", "remove_lidar_name"),
    "t036": ("trims.json", "MG 07 纯电", "remove_charge"),     # 删charge→电压断言FAIL（用随包manual里的真实案例）
    "t005": ("board_view.main_cell", None, "wrong_trim"),                # 函数patch
    "t013": ("board_view.main_cell", None, "wrong_total"),
    "t019": ("board_view.lifecycle_status_cn", None, "return_english"),
    "t040": ("trim_annotate.charge_minutes", None, "wrong_charge"),
    "t034": ("board_view.main_cell", None, "hide_cell"),
    "t022": ("runs/compact_bev_202610_candidates.json", "小鹏MONA M03", "flatten_adas_all"),
    "t023": ("runs/compact_bev_202610_candidates.json", "零跑B01", "evidence_to_B"),
    "t025": ("runs/compact_bev_202610_candidates.json", "零跑Lafa5", "flatten_adas_all"),
    "t050": ("runs/midsedan_bev_1218_202610/candidates_allprice.json", "特斯拉Model 3", "fake_single_month"),
    # t068：把归一化改为 identity（等于没归一化），「未归一化必须被检出」那条断言应 FAIL
    "t068": ("candidate_schema.normalize_pool", None, "no_normalize"),
    # t069：把回填改成 no-op（等于没回流），「应回填」断言必须 FAIL
    "t069": ("facts_sync.backfill_price_guide_from_trims", None, "no_backfill"),
    # t070：把「是否有成绩」恒判为 True（等于又虚报），负向断言必须 FAIL
    "t070": ("crash_systems.has_score", None, "always_score"),
    # t071：把成绩回流改成 no-op（文本成绩进不了字段），回填断言必须 FAIL
    "t071": ("facts_sync.backfill_crash_from_text", None, "no_crash_backfill"),
    # t072：把成绩→分数映射改成空操作（成绩进不了打分），断言必须 FAIL
    "t072": ("safety_check.apply_auto_scores", None, "no_auto_safety"),
    # t073：让「公式复算」永远返回 None（等于校验失效），手填错分将检不出 → 断言必须 FAIL
    "t073": ("safety_check.score_from_record", None, "no_formula"),
    # t074：让无配置时返回空串（等于又静默），「必须标注待补」断言应 FAIL
    "t074": ("render_board_html.trim_selector", None, "silent_trim"),
    # t075：把远程兜底关掉（回到"索引 miss 就放弃"），断言必须 FAIL
    "t075": ("auto_trims.find_sid_remote", None, "no_remote_sid"),
    # t076：让多源调度永远返回 missing（等于没有调度层），断言必须 FAIL
    "t076": ("trim_sources.collect_trims", None, "always_missing"),
    # t077：让越界扫描永远返回空（等于门禁失效），「越界必须被检出」断言应 FAIL
    "t077": ("layout_regress.scan_network_egress", None, "clean_egress"),
    # t078：关掉举证封顶（回到「零实锤也能 8.6」），断言必须 FAIL
    "t078": ("safety_check.evidence_cap", None, "no_cap"),
    # t079：把换算改成恒返回 1.0（等于标定失效），断言必须 FAIL
    "t079": ("safety_score.score_from_pct", None, "flat_score"),
    # t080：关掉体验封顶（回到「零实测也能 9.4」），断言必须 FAIL
    "t080": ("experience_score.cap_unproven", None, "no_dim_cap"),
    # t081：关掉统一证据门槛（回到「各维度随便给分」），断言必须 FAIL
    "t081": ("evidence_gate.apply_all", None, "no_evidence_gate"),
    # t082：关掉可复用实测提取器（回到「抓不到试驾稿麋鹿/NOA」），断言必须 FAIL
    "t082": ("fill_experience.extract_driving_evidence", None, "no_extract"),
    # t083：关掉空间一致性校准（回到「space 人工拍分无人校验」），断言必须 FAIL
    "t083": ("space_consistency.calibrate_space", None, "no_space_cap"),
    # t084：关掉空间要素提取器（回到「前备箱/得房率记了却没用」），断言必须 FAIL
    "t084": ("space_features.extract_space_features", None, "no_sf_extract"),
    # t085：关掉核算字段提取（回到「evidence 硬参数提不出、规则从没跑」），断言必须 FAIL
    "t085": ("score_audit.extract_spec_fields", None, "no_audit_extract"),
    # t086：关掉严格证据解析（回到「Model Y 的 C-IASI 全在 evidence 里却没人回填」），断言必须 FAIL
    "t086": ("crash_systems.parse_score_from_evidence", None, "no_evidence_crash"),
    # t087：关掉权威库回流（回到「蔚来ET5 等库里有成绩却取不到」），断言必须 FAIL
    "t087": ("facts_sync.backfill_crash_from_authoritative", None, "no_authoritative_fill"),
    # t088：关掉全量成绩收集（回到「只取第一个锚点、次要成绩静默丢弃」），断言必须 FAIL
    "t088": ("crash_systems.parse_all_scores_from_evidence", None, "no_multi_scores"),
    # t089：关掉多测评融合（回到「数字图标车无成绩、C-IASI 不木桶取最低」），断言必须 FAIL
    "t089": ("multi_evidence_score.fuse", None, "no_fusion"),
    # t039：v10.6.0 后改为「固化内容完整性」断言；删掉零跑B01的scores → 断言FAIL
    "t039": ("categories/candidates_compact_bev_extended.json", "零跑B01", "delete_scores"),
}


def _func_probe(name, fn, dotted, mode):
    """函数级探针：patch 被测模块的函数返回错误值，验证断言FAIL。"""
    from modules.regress import bug_regression as BR
    mod_name, func_name = dotted.split(".")
    mod = paths.module(mod_name)
    orig = getattr(mod, func_name)
    wrong = {
        "wrong_trim": lambda *a, **kw: "610版",
        "wrong_total": lambda *a, **kw: "85.0（配置）",
        "return_english": lambda st: st,
        "wrong_charge": lambda *a, **kw: (20, "wrong"),
        "hide_cell": lambda *a, **kw: "未披露电芯",
        "empty_sync": lambda *a, **kw: {},   # 同步集清空 → 「候选池必须进同步集」应 FAIL
        "no_normalize": lambda cands=None, *a, **kw: list(cands or []),  # 不归一化原样返回
        "no_backfill": lambda *a, **kw: ([], []),                        # 不回流，什么都不填
        "always_score": lambda *a, **kw: True,                            # 虚报：什么都算有成绩
        "no_crash_backfill": lambda *a, **kw: ([], []),                    # 不回流成绩
        "no_auto_safety": lambda *a, **kw: ([], []),                       # 不映射安全分
        "no_formula": lambda *a, **kw: (None, ""),                         # 公式失效（校验形同虚设）
        "silent_trim": lambda *a, **kw: "",                                # 静默：无配置时不标注
        "no_remote_sid": lambda *a, **kw: (None, None, None),              # 不做远程兜底
        "clean_egress": lambda *a, **kw: [],                               # 越界扫描恒为空
        "no_cap": lambda *a, **kw: [],                                     # 不做举证封顶
        "flat_score": lambda *a, **kw: 1.0,                                # 换算恒为1.0
        "no_dim_cap": lambda *a, **kw: [],                                 # 不做体验封顶
        "no_evidence_gate": lambda *a, **kw: [],                           # 不做任何证据封顶
        "no_extract": lambda *a, **kw: [],                                  # 提取器失效（T6 实测又抓不到）
        "no_space_cap": lambda *a, **kw: ([], []),                          # 空间校准失效（MONA 9.3 又无人管）
        "no_sf_extract": lambda *a, **kw: [],                               # 空间要素提取失效（得房率/前备箱又没用）
        "no_audit_extract": lambda *a, **kw: {},                            # 核算字段提取失效（规则又没数据可算）
        "no_evidence_crash": lambda *a, **kw: None,                         # 严格证据解析失效（Model Y C-IASI 又白采）
        "no_authoritative_fill": lambda *a, **kw: [],                       # 权威库回流失效（蔚来ET5 C-IASI 又取不到）
        "no_multi_scores": lambda *a, **kw: [],                             # 多成绩收集失效（又变成只取第一个+次要成绩丢失）
        "no_fusion": lambda *a, **kw: [],                                    # 多测评融合失效（数字图标车又无成绩、木桶不生效）
        "always_missing": lambda *a, **kw: {"status": "missing", "source": "", "trims": [],
                                           "attempts": [], "evidence": {}, "sid": None,
                                           "ah_name": ""},                # 调度退化为直接失败
    }.get(mode)
    if wrong is None:
        return False, False
    setattr(mod, func_name, wrong)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            fn()
        return True, False
    except Exception:
        return True, True
    finally:
        setattr(mod, func_name, orig)


def _read_src_probe(name, fn, targets):
    """源码断言型注入：抹掉 _read 返回内容里的目标字符串。"""
    from modules.regress import bug_regression as BR
    orig_read = BR._read
    state = {"hit": False}

    def patched_read(rel):
        txt = orig_read(rel)
        for t in targets:
            if t in txt:
                state["hit"] = True
                txt = txt.replace(t, "___NEG_REMOVED___")
        return txt

    BR._read = patched_read
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            fn()
        return state["hit"], False   # 抹掉后仍PASS=断言无效
    except Exception:
        return state["hit"], True    # 抹掉后FAIL=断言有效
    finally:
        BR._read = orig_read


def _data_probe(name, fn, path, key, mode):
    """数据断言型注入：monkeypatch json.load，篡改目标文件对象。"""
    if "." in path and key is None:  # 函数级探针
        return _func_probe(name, fn, path, mode)
    from modules.regress import bug_regression as BR
    # 生成物（trims/master_pool…）已迁到 data/gen/，人工配置仍在 categories/。
    if "/" not in path:
        full = str(paths.cat(path))
    else:
        full = os.path.join(HERE, path)
    if not os.path.exists(full):
        return False, False
    orig_load = BR.json.load
    try:
        obj = orig_load(open(full, encoding="utf-8"))
    except Exception:
        return False, False
    import copy
    fake = copy.deepcopy(obj)

    def walk(o):
        if isinstance(o, dict):
            if o.get("model") == key and mode == "delete_scores":
                o.pop("scores", None)
            if o.get("model") == key and mode == "same_score":
                for r in o.get("items", []):
                    if isinstance(r, dict) and "score" in r:
                        r["score"] = 50.0
            for v in list(o.values()):
                walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    if mode == "delete_scores":
        walk(fake)
    elif mode == "same_score":
        walk(fake)
    elif mode == "rename_trim":
        for e in fake.get("entries", []):
            if e.get("model") == key:
                e["scored_trim"] = "不存在的配置XYZ"
    elif mode == "delete_cltc":
        for e in fake.get("entries", []):
            if e.get("model") == key:
                for t in e.get("trims", []):
                    t.pop("cltc_km", None)
    elif mode == "same_score_trims":
        # 让两个配置的所有差异消失（scores+价格+电池+续航），car_verdict 的 pros 才会相同
        meta = fake.get("meta", {})
        tb = meta.get("trim_board", {}).get(key, {})
        trims = tb.get("trims", [])
        if len(trims) >= 2:
            import copy as _c
            identical = _c.deepcopy(trims[0])
            for t in trims[1:]:
                t.clear()
                t.update(_c.deepcopy(identical))
    elif mode == "remove_lidar_name":
        # 去掉配置名里的"激光雷达"，测试找不到lidar配置→StopIteration→FAIL
        for e in fake.get("entries", []):
            if e.get("model") == key:
                for t in e.get("trims", []):
                    if "激光雷达" in str(t.get("name", "")):
                        t["name"] = t["name"].replace("激光雷达", "普通版")
    elif mode == "flatten_adas_trims":
        for e in fake.get("entries", []):
            if e.get("model") == key:
                for t in e.get("trims", []):
                    if isinstance(t.get("scores"), dict):
                        t["scores"]["adas"] = 5.0
    elif mode == "remove_charge":
        for e in fake.get("entries", []):
            if e.get("model") == key:
                for t in e.get("trims", []):
                    t.pop("charge", None)
    elif mode == "flatten_adas_all":
        for c in fake.get("candidates", []):
            sc = c.get("scores") or {}
            if "adas" in sc:
                sc["adas"] = 5.0
    elif mode == "evidence_to_B":
        for c in fake.get("candidates", []):
            if c.get("model") == key:
                c["evidence_status"] = "B"
    elif mode == "fake_single_month":
        for c in fake.get("candidates", []):
            if c.get("model") == key:
                c["trend_sales"] = 1010  # 改回异常单月→t050断言14372 FAIL
                c["trend_method"] = "单月零售"
    elif mode == "flatten_adas_cands":
        for c in fake.get("candidates", []):
            for t in c.get("trims", []):
                if isinstance(t.get("scores"), dict) and "adas" in t["scores"]:
                    t["scores"]["adas"] = 5.0
    elif mode == "tighten_band":
        for b in fake.get("boards", []):
            if "price_band" in b.get("gates", {}):
                b["gates"]["price_band"] = [5, 10]
    elif mode == "flatten_adas":
        def flat(o):
            if isinstance(o, dict):
                if "scores" in o and isinstance(o["scores"], dict):
                    o["scores"]["adas"] = 5.0
                for v in o.values():
                    flat(v)
            elif isinstance(o, list):
                for x in o:
                    flat(x)
        flat(fake)

    def patched_load(f, *a, **kw):
        try:
            fp = f.name if hasattr(f, "name") else str(f)
            if os.path.normpath(fp).endswith(os.path.normpath(path)):
                return fake
        except Exception:
            pass
        return orig_load(f, *a, **kw)

    BR.json.load = patched_load
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            fn()
        return True, False
    except Exception:
        return True, True
    finally:
        BR.json.load = orig_load


def probe_all():
    """每条探针在独立子进程跑，彻底隔离模块缓存/json patch 状态。
    单进程内 monkeypatch 会让前面测试的 import 缓存影响后面（t036 单跑检出、批量不检出），
    子进程隔离是唯一可靠做法。"""
    import subprocess, sys as _sys
    detected, broken, skipped = [], [], []
    all_names = list(NEG_TARGETS) + list(NEG_DATA)
    for name in all_names:
        code = (
            "from modules.regress import negative_regression as N, bug_regression as BR;"
            "import sys;"
            "fn=getattr(BR,sys.argv[1],None);"
            "applied,caught=N._probe_one_isolated(sys.argv[1],fn);"
            "sys.exit(2 if not applied else (0 if caught else 1))"
        )
        p = subprocess.run([_sys.executable, "-c", code, name],
                           cwd=HERE, capture_output=True, timeout=60)
        # 退出码：0=检出(测试FAIL了)，1=未检出，2=探针不适用
        if p.returncode == 0:
            detected.append(name)
        elif p.returncode == 2:
            skipped.append(name)
        else:
            broken.append(name)
    total_probed = len(detected) + len(broken)
    print(f"[失败回测] 可注入 {total_probed} 条 / 检出 {len(detected)} / 未检出 {len(broken)} / 跳过 {len(skipped)}")
    if broken:
        print("  ⛔ 注入错误后仍PASS（断言形同虚设）：" + "、".join(broken))
    if skipped:
        print("  ⚠ 跳过（探针不适用）：" + "、".join(skipped[:12]))
    return broken, skipped


def _probe_one_isolated(name, fn):
    """子进程内跑单条探针：返回 (applied, caught)。"""
    if name in NEG_TARGETS:
        return _read_src_probe(name, fn, NEG_TARGETS[name])
    elif name in NEG_DATA:
        path, key, mode = NEG_DATA[name]
        return _data_probe(name, fn, path, key, mode)
    return False, False


if __name__ == "__main__":
    b, s = probe_all()
    raise SystemExit(1 if b else 0)
