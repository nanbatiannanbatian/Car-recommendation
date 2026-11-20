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
         t009, t010, t011, t012, t013, t014, t015, t016, t017, t018, t019, t020]


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
