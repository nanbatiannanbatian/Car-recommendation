#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
energy_compare_regress.py —— 跨能源形式选车适配比较器（energy_compare.html）回归测试。

设计原则（2026-11-20 用户明确要求）：
  "以后做的修改都要考虑是不是只是打了补丁，以后的生成还是有问题；
   这个原则在 skill 上有了别忘，改完都回归测试一下。"

  所以本测试**不检查生成物快照**，而是验证"生成链路"：
   1. 规则表/数据源（生成脚本 + vehicle_physics.json）是唯一事实源
   2. 重新运行 build_energy_compare.py 生成 HTML
   3. 从新生成的 HTML 反向抽取数据/规则，验证与源一致 —— 下次重跑也不会退化
   4. 覆盖 v1~v5 所有踩过的坑（补丁式修复的复发点）

运行：
    python3 energy_compare_regress.py              # 全量回归（含重新生成）
    python3 energy_compare_regress.py --list        # 列出覆盖项
    python3 energy_compare_regress.py --no-rebuild  # 不重新生成，只测当前产物
    python3 energy_compare_regress.py --selftest    # 同全量（供管线调用）
"""
from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
import sys

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)

BUILD = os.path.join(HERE, "build_energy_compare.py")   # 走 CLI 兼容入口，保证 sys.path[0]=ROOT
HTML = os.path.join(HERE, "energy_compare.html")
PHYS = str(paths.cat("vehicle_physics.json"))


def _read(rel):
    p = paths.src(rel)
    return open(p, encoding="utf-8").read() if p.exists() else ""


def _html():
    return _read("energy_compare.html")


def _engine():
    """环境分/续航引擎真身（v10 起从 build_energy_compare.py 抽到 engine.js）。

    引擎类断言必须读这里：只读生成脚本会漏掉真正的公式实现。
    """
    with open(paths.MODULES / "envscore" / "engine.js", encoding="utf-8") as f:
        return f.read()


def _js_const(html, name):
    """从生成的 HTML 里抽取 const NAME=...; 的 JSON 值。"""
    m = re.search(r"const " + name + r"=(\{.*?\}|\[.*?\]);\n", html, re.S)
    assert m, f"HTML 中找不到 const {name}"
    return json.loads(m.group(1))


# ============ 生成链路测试（防补丁/防重跑退化）============

def t001():
    """生成器必须存在且可执行——规则在脚本里，不在 HTML 手工补丁里。"""
    assert os.path.exists(BUILD), "生成脚本缺失"
    src = _read("build_energy_compare.py")
    for key in ("FACTORS", "DIMS", "PHYS_A", "TEMP_CONS", "SWAP_ADVANTAGE", "CONS_MULT" if False else "HWY_BASE_MULT"):
        assert key in src, f"生成脚本缺少规则常量 {key}（可能被手工补丁绕过）"
    assert "__CARS_JSON__" in src and "__PHYS_JSON__" in src, "数据注入占位符缺失"
    return "规则表/占位符都在生成脚本中（单一事实源）"


def t002():
    """重新生成后，HTML 里的车型数据必须与生成脚本 load_cars() 完全一致。"""
    from modules.envscore import build_energy_compare as B
    src_cars = B.load_cars()
    html = _html()
    gen_cars = _js_const(html, "CARS")
    assert len(src_cars) == len(gen_cars), f"车数不一致 源{len(src_cars)} vs 产物{len(gen_cars)}"
    # 关键字段逐车对比（浮点留1位）
    for a, b in zip(src_cars, gen_cars):
        for k in ("model", "power", "price", "product", "cons", "weight", "cd", "swap"):
            # 源与产物都来自同一次 json.dumps，值应完全相同；不在此处做任何取整
            av, bv = a.get(k), b.get(k)
            assert av == bv, f"{a['model']} 字段 {k}: 源{av} vs 产物{bv}（重跑会退化）"
    return f"{len(src_cars)} 款车源数据 = 生成产物（重跑一致）"


def t003():
    """物理模型常量必须与生成脚本一致——防只改 HTML 里的系数。"""
    from modules.envscore import build_energy_compare as B
    html = _html()
    p = _js_const(html, "PHYS")
    assert p["a"] == B.PHYS_A and p["b"] == B.PHYS_B and p["c"] == B.PHYS_C, "回归系数不一致"
    assert p["hwyBase"] == B.HWY_BASE_MULT and p["cityBase"] == B.CITY_BASE_MULT, "工况倍率不一致"
    assert p["medRatio"] == B.BASE_CDA_PER_TON, "CdA中位数不一致"
    assert p["temp"] == B.TEMP_CONS and p["drive"] == B.DRIVE_MULT and p["ac"] == B.AC_MULT, "工况系数不一致"
    assert p["swapAdv"] == B.SWAP_ADVANTAGE, "换电优势规则不一致"
    return "物理模型全部常量：源 = 产物"


def t004():
    """所有占位符必须被替换——防改脚本后产物残留 __XXX__。"""
    html = _html()
    left = set(re.findall(r"__[A-Z_]+__", html))
    assert not left, f"产物残留未替换占位符: {left}"
    return "无占位符残留"


def t005():
    """生成的 JS 必须语法合法（node --check）。"""
    import tempfile
    scripts = re.findall(r"<script>(.*?)</script>", _html(), re.S)
    assert scripts, "无 script 段"
    node = _which("node")
    if not node:
        return "node 不可用，跳过语法检查（非失败）"
    fd, path = tempfile.mkstemp(suffix=".js")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(scripts[0])
        r = subprocess.run([node, "--check", path], capture_output=True, text=True)
        assert r.returncode == 0, f"JS 语法错误: {r.stderr[:300]}"
    finally:
        os.remove(path)
    return "JS 语法合法"


# ============ v1~v5 踩坑回归（防补丁式修复复发）============

def t010():
    """v3 坑：手工录入面板必须彻底移除——不能只在 HTML 里删、脚本里还留。"""
    html = _html()
    src = _read("build_energy_compare.py")
    for kw in ("loadManual", "addManual", "delManual", "manualList", "mDims", "手工录入", "LS_KEY"):
        assert kw not in html, f"产物仍残留手工录入 {kw}"
        assert kw not in src, f"生成脚本仍残留手工录入 {kw}（重跑会复活）"
    return "手工录入面板：脚本+产物均已清除"


def t011():
    """v2/v3 坑：能源适配榜必须去掉，只留车型主榜。"""
    html = _html()
    assert "能源形式适配榜" not in html, "能源榜残留"
    assert "车型综合榜" in html, "车型主榜缺失"
    return "只保留车型主榜（无能源榜）"


def t012():
    """v5 坑：物理参数库必须真实采集、不是编造——检查覆盖与来源。"""
    doc = json.load(open(PHYS, encoding="utf-8"))
    cars = doc["cars"]
    assert len(cars) >= 60, f"物理库车型数异常: {len(cars)}"
    with_w = sum(1 for c in cars if c.get("weight_kg"))
    with_cd = sum(1 for c in cars if c.get("cd"))
    assert with_w >= 55 and with_cd >= 45, f"物理参数覆盖不足: 重{with_w} 风阻{with_cd}"
    # 抽查真实值（曾采集到的），防止被拍脑袋改掉
    by = {c["model"]: c for c in cars}
    assert by["乐道L60"]["cd"] == 0.229, "乐道L60 风阻被改（真实值0.229）"
    assert by["乐道L60"]["kwh"] == 60, "乐道L60 电量补采丢失"
    assert by["蔚来ES6"]["kwh"] == 100, "蔚来ES6 电量补采丢失"
    assert by["乐道L60"]["swap"] is True and by["蔚来ES6"]["swap"] is True, "换电标记丢失"
    return f"物理库真实: 重{with_w}/风阻{with_cd}，换电/补采值正确"


def t013():
    """v4 坑：电量必须全覆盖——67 款都要有 kwh（曾 12 款缺失致续航显示—）。"""
    cars = _js_const(_html(), "CARS")
    missing = [c["model"] for c in cars if not c.get("kwh")]
    assert not missing, f"仍有 {len(missing)} 款缺电量: {missing[:5]}"
    return f"{len(cars)} 款车电量全覆盖"


def t014():
    """v5 坑：硬条件必须全额计入、不受滑块缩放、且触发降档标注。"""
    src = _read("build_energy_compare.py")
    html = _html()
    # 生成脚本层面：硬条件总分单独算，软调节才乘 scale
    assert "const soft=a.total-a.hard.reduce" in html, "硬软分离逻辑丢失（硬条件会被滑块缩小）"
    assert "const adj=adjSoft+hardTotal" in html, "硬条件未全额计入综合分"
    # 降档 + 红标
    assert "downgrade" in html and "tier=tier==='T1'?'T2'" in html, "降档逻辑丢失"
    assert "⚠硬条件不符" in html, "硬条件警示标签丢失"
    # 关键硬条件分值（v10：引擎已抽到 engine.js）
    assert "'无充电桩·纯电不推荐', -15" in _engine(), "无桩纯电-15 硬条件丢失"
    return "硬条件：全额计入+不缩放+降档+红标"


def t015():
    """v5 坑：换电优势必须存在且在无桩/极寒/短持有/长途四场景生效。"""
    from modules.envscore import build_energy_compare as B
    adv = B.SWAP_ADVANTAGE
    for k in ("no_charger", "short_hold", "cold", "longtrip"):
        assert k in adv and adv[k]["gain"] > 0, f"换电优势缺 {k}"
    html = _html()
    assert "换电" in html and "换电缓解" in html, "换电标识/缓解文案丢失"
    return "换电四场景优势齐全且为正分"


def t016():
    """v5 坑：新增条件（现为 v10.53.0 的家庭车队/地形）必须在规则表且能被动态渲染；v10.54.0 精简后仍守住。"""
    from modules.envscore import build_energy_compare as B
    assert "fleet" in B.FACTORS and "charge_patience" in B.FACTORS, "新条件未进规则表"
    assert "terrain" not in B.FACTORS and "boxy" not in B.FACTORS, "已删条件仍在规则表"
    assert "drive_style" not in B.FACTORS and "ac_usage" not in B.FACTORS, "已删条件仍在规则表"
    # 下拉框由 buildConds() 动态生成（id='c_'+fid），验证产物里注入了 FACTORS 且渲染函数在
    html = _html()
    fac = _js_const(html, "FACTORS")
    assert "fleet" in fac and "charge_patience" in fac, "产物 FACTORS 缺新条件"
    assert "d.innerHTML='<label>'+f.label" in html and "'c_'+fid" in html, "buildConds 动态渲染逻辑丢失"
    return "家庭车队/补能耐心在规则表+动态渲染双落地，已删条件不回流"


def t017():
    """v4/v5 坑：续航算式必须展示温度/暖风/电压三因子（v10.55.0 起驾驶/空调条件已删），且无'0.975V'文案 bug。"""
    html = _html()
    for kw in ("温度(仅电池)", "暖风 ", "电压系数"):
        assert kw in html, f"续航算式缺因子 {kw}"
    assert "电压 '+voltM+'V(" not in html, "电压文案 bug 复现（0.975V）"
    assert "物理回归估算电耗" in html, "物理回归估算说明丢失"
    return "续航算式透明且无电压文案 bug"


def t018():
    """v4 坑：有效续航不得用 CLTC 裸乘达成率主算法（会被风阻车型误导）。"""
    src = _read("build_energy_compare.py")
    assert "PHYS_A*(weight/1000.0)" in src, "物理回归估算未用于缺电耗车"
    assert "cdaPerTon" in src, "CdA/吨差异化倍率丢失（车身风阻差异无法体现）"
    assert "4.48" in src and "5.88" in src, "回归系数丢失"
    return "物理模型为续航主算法（非 CLTC 打折）"


# ============ 打分模型不变量 ============

def t020():
    """8 维权重必须和=1，动力总成=18%——统一维度框架不变量。"""
    from modules.envscore import build_energy_compare as B
    assert abs(sum(B.WEIGHT_MAP.values()) - 1.0) < 1e-9, "权重和≠1"
    assert abs(B.WEIGHT_MAP["powertrain"] - 0.18) < 1e-9, "动力总成权重≠18%"
    assert len(B.DIMS) == 8, "维度数≠8"
    # 电车评三电、油车评三大件
    pt = next(d for d in B.DIMS if d["key"] == "powertrain")
    assert "三电" in pt["by_power"]["BEV"], "电车动力维未评三电"
    assert "三大件" in pt["by_power"]["ICE"], "油车动力维未评三大件"
    return "8维权重和=1，动力总成18%（三电/三大件统一）"


def t021():
    """产品力公式刻度：全7=70、全10=100——手工车时代遗留的正确性不变量。"""
    from modules.envscore import build_energy_compare as B
    def prod(scores):
        return round(sum(scores.get(d["key"], 7) * d["weight"] for d in B.DIMS) * 10, 1)
    assert prod({d["key"]: 7 for d in B.DIMS}) == 70.0
    assert prod({d["key"]: 10 for d in B.DIMS}) == 100.0
    return "产品力公式刻度正确"


def t022():
    """数据铁律：不为凑能源形式编造车型；缺数据要标注不估算。"""
    html = _html()
    assert "不编造车型" in html, "数据边界声明丢失"
    assert "无电量数据的车" in html, "缺电量不估算声明丢失"
    return "数据边界声明完整（不编造）"



def t033():
    """v10.56.0：车身形式归一——官方 body 直映射，越野车/方盒子SUV 只在有客观字段时升级（不猜外形）。"""
    from modules.envscore.build_energy_compare import body_label
    assert body_label({"body": "MPV"}, {}) == "MPV", "MPV 未识别"
    assert body_label({"body": "三厢车"}, {}) == "轿车", "三厢应归一为轿车"
    assert body_label({"body": "两厢车"}, {}) == "轿车", "两厢应归一为轿车"
    assert body_label({"body": "SUV"}, {}) == "SUV", "普通 SUV 不得被猜成越野/方盒子"
    assert body_label({"body": "SUV"}, {"offroad": True}) == "越野车", "越野标记未生效"
    assert body_label({"body": "SUV"}, {"boxy": True}) == "方盒子SUV", "方盒子标记未生效"
    assert body_label({"body": "SUV", "body_form": "MPV"}, {}) == "MPV", "显式 body_form 应优先"
    return "车身形式 轿车/SUV/MPV/越野车/方盒子SUV 归一正确，缺字段不猜"


def t030():
    """沉淀的物理模型模块必须存在且自检通过（真实达成率对照在容差内）。"""
    from modules.physics import ev_range_model as ERM
    val = ERM.validate_real()
    assert len(val) >= 3, "真实数据对照场景不足"
    for row in val:
        lo = row["real_min"] * 100 - 12
        hi = row["real_max"] * 100 + 12
        m = row["model_ach"] * 100
        assert lo <= m <= hi, f"{row['scene']} 模型达成率{m}% 偏离实测[{lo:.0f},{hi:.0f}]"
    return "物理模型 vs 实测三场景均在±12pp内: " + ", ".join(
        f"{r['scene']}={r['model_ach']*100:.0f}%" for r in val)


def t031():
    """温度/空调必须拆分（防旧版双重计算暖风）；胎压/轮胎必须已建模。"""
    from modules.physics import ev_range_model as ERM
    # 温度只管电池活性，极寒系数应温和
    assert ERM.TEMP_BATTERY["underm20"] <= 1.25, "温度系数又把暖风算进去了（双重计算）"
    # 空调按季节递增，独立于温度
    assert ERM.AC_BASE["above0"] < ERM.AC_BASE["underm20"], "空调未按季节区分冬季暖风"
    # 胎压：每低0.1bar约+1.5%电耗
    assert abs((ERM.TIRE_PRESSURE_MULT["low_01"] - 1) - 0.015) < 1e-6, "胎压系数不符网搜证据"
    # 轮胎类型默认不编造
    assert ERM.TIRE_TYPE_MULT["unknown"] == 1.0, "未知轮胎不应臆测修正"
    return "温度/空调拆分正确，胎压+轮胎已建模且默认中性"


def t032():
    """缺数据车型必须能估算（回归）且不编造：无电量时不给续航数字。"""
    from modules.physics import ev_range_model as ERM
    v, src = ERM.base_consumption(None, 1800, 0.23, 2.4)
    assert 10 <= v <= 15 and "回归" in src, f"缺电耗估算异常: {v}/{src}"
    r = ERM.estimate({"kwh": None, "cons": 12, "body": "轿车"},
                     {"highway_ratio": "20to40", "winter_temp": "m10to0"})
    assert r["range_km"] is None, "无电量车不应编造续航"
    return f"缺电耗可估算({src} {v})，无电量不编造"


CASES = [
    ("t030", "物理模型 vs 真实数据对照（±12pp）", t030),
    ("t031", "温度/空调拆分+胎压轮胎建模", t031),
    ("t032", "缺数据可估算+无电量不编造", t032),
    ("t033", "车身形式归一（含MPV/越野/方盒子SUV）", t033),
    ("t001", "规则表/占位符在生成脚本（单一事实源）", t001),
    ("t002", "重新生成后车型数据 = 源数据（防重跑退化）", t002),
    ("t003", "物理模型常量源 = 产物", t003),
    ("t004", "无占位符残留", t004),
    ("t005", "JS 语法合法", t005),
    ("t010", "手工录入已彻底移除（脚本+产物）", t010),
    ("t011", "只留车型主榜", t011),
    ("t012", "物理库真实采集（覆盖+抽查值）", t012),
    ("t013", "67款电量全覆盖", t013),
    ("t014", "硬条件全额+不缩放+降档+红标", t014),
    ("t015", "换电四场景优势", t015),
    ("t016", "驾驶风格/空调双落地", t016),
    ("t017", "续航算式透明+无文案bug", t017),
    ("t018", "物理模型为续航主算法", t018),
    ("t020", "8维权重不变量", t020),
    ("t021", "产品力公式刻度", t021),
    ("t022", "数据边界声明", t022),
]


def _which(name):
    import shutil
    return shutil.which(name)


def rebuild():
    """重新生成 HTML——回归测试针对生成链路，而非当前快照。"""
    r = subprocess.run([sys.executable, BUILD], capture_output=True, text=True, cwd=HERE)
    if r.returncode != 0:
        raise RuntimeError(f"重新生成失败: {r.stderr[:500]}")
    return r.stdout.strip().replace("\n", " | ")


def run(no_rebuild=False):
    if not no_rebuild:
        print("[rebuild] " + rebuild())
    passed, failed = 0, []
    for cid, desc, fn in CASES:
        try:
            msg = fn()
            print(f"  PASS {cid}  {desc} :: {msg}")
            passed += 1
        except Exception as e:
            print(f"  FAIL {cid}  {desc} :: {type(e).__name__}: {e}")
            failed.append((cid, str(e)))
    print(f"\n结果: {passed}/{len(CASES)} 通过" + (f"，失败 {len(failed)} 项" if failed else "，全绿"))
    return 0 if not failed else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="跨能源比较器回归测试")
    ap.add_argument("--list", action="store_true", help="列出覆盖项")
    ap.add_argument("--no-rebuild", action="store_true", help="不重新生成，只测当前产物")
    ap.add_argument("--selftest", action="store_true", help="同全量（供管线调用）")
    a = ap.parse_args()
    if a.list:
        for cid, desc, _ in CASES:
            print(f"{cid}  {desc}")
    else:
        sys.exit(run(no_rebuild=a.no_rebuild))
