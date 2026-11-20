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

HERE = os.path.dirname(os.path.abspath(__file__))

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
    "t039": ["candidates_compact_bev_extended.json"],
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
}

# 数据断言型：测试名 -> (文件路径, 车型/键, 篡改模式)
NEG_DATA = {
    "t001": ("categories/trims.json", "MG 07 纯电", "delete_cltc"),      # 删trims的cltc_km→续航映射断言FAIL
    "t003": ("categories/trims.json", "深蓝L06", "rename_trim"),
    "t007": ("runs/midsedan_bev_1218_202610/board_v2_data.json", "MG 07 纯电", "same_score_trims"),
    "t011": ("categories/car_midsedan_allprice.json", "price_band", "tighten_band"),
    "t021": ("categories/trims.json", "零跑B01", "remove_lidar_name"),
    "t036": ("categories/trims.json", "零跑B01", "remove_charge"),       # 删charge→电压断言FAIL
    "t005": ("board_view.main_cell", None, "wrong_trim"),                # 函数patch
    "t013": ("board_view.main_cell", None, "wrong_total"),
    "t019": ("board_view.lifecycle_status_cn", None, "return_english"),
    "t040": ("trim_annotate.charge_minutes", None, "wrong_charge"),
    "t034": ("board_view.main_cell", None, "hide_cell"),
    "t022": ("runs/compact_bev_202610_candidates.json", "小鹏MONA M03", "flatten_adas_all"),
    "t023": ("runs/compact_bev_202610_candidates.json", "零跑B01", "evidence_to_B"),
    "t025": ("runs/compact_bev_202610_candidates.json", "零跑Lafa5", "flatten_adas_all"),
    "t050": ("runs/midsedan_bev_1218_202610/candidates_allprice.json", "特斯拉Model 3", "fake_single_month"),
}


def _func_probe(name, fn, dotted, mode):
    """函数级探针：patch 被测模块的函数返回错误值，验证断言FAIL。"""
    import bug_regression as BR
    mod_name, func_name = dotted.split(".")
    mod = __import__(mod_name)
    orig = getattr(mod, func_name)
    wrong = {
        "wrong_trim": lambda *a, **kw: "610版",
        "wrong_total": lambda *a, **kw: "85.0（配置）",
        "return_english": lambda st: st,
        "wrong_charge": lambda *a, **kw: (20, "wrong"),
        "hide_cell": lambda *a, **kw: "未披露电芯",
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
    import bug_regression as BR
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
    import bug_regression as BR
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
            "import negative_regression as N, bug_regression as BR, sys;"
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
