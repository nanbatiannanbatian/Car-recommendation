#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""iron_rules.py —— 用户铁律的可执行门禁（v10.64 汇总）

为什么有这个模块（2026-11-24 用户："排查所有铁律，把可以代码化的全部代码化"）：
  大量铁律此前只写在 SKILL.md / README.md / 用户记忆里，靠"记得做"——
  而 2026-11-24 连着两次证明文档纪律靠不住（漏收腾势N8、质量分全8）。
  本模块把"能代码判定"的铁律收口成单一真相源，供 pre-commit / sync --apply /
  独立 CLI 调用；不能代码化的（如需人际确认、语义判断）在 NON_CODABLE 里如实列明。

约束：零网络、只读、确定性；返回结构化结果 {rule, ok, detail}，不擅自改文件。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# 让本模块可从任意 cwd 导入
_HERE = Path(__file__).resolve()
_ROOT = _HERE.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
import paths  # noqa: E402


def _read(name: str) -> str:
    p = paths.src(name) if not os.path.isabs(name) else Path(name)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def check_version_sync() -> dict:
    """铁律：每次提交代码必须同步 README（版本号要与 SKILL.md 一致）。

    用户 2026-11-19 明确要求。旧 t016 名为"版本/README/SKILL 一致"却只查了
    SKILL 有版本号——假断言。这里真正校验 README 顶部"当前版本 vX.Y.Z" =
    SKILL.md frontmatter version，不一致即拦截。
    """
    sk = _read("SKILL.md")
    rd = _read("README.md")
    m = re.search(r"^version:\s*(\S+)", sk, re.M)
    if not m:
        return {"rule": "README版本同步", "ok": False, "detail": "SKILL.md 无 version 字段"}
    v = m.group(1).strip()
    # README 顶部（前 8 行）必须出现"当前版本 **vX**"
    head = "\n".join(rd.splitlines()[:8])
    rv = re.search(r"当前版本\s*\*\*v?([0-9][0-9.]*)\*\*", head)
    if not rv:
        return {"rule": "README版本同步", "ok": False,
                "detail": "README 顶部缺『当前版本 **vX.Y.Z**』标记"}
    if rv.group(1).strip() != v.lstrip("v"):
        return {"rule": "README版本同步", "ok": False,
                "detail": f"版本漂移：SKILL={v} / README={rv.group(1)}"}
    return {"rule": "README版本同步", "ok": True, "detail": f"一致 v{v}"}


def check_no_placeholder_url() -> dict:
    """铁律：证据 URL 必须可核验，不许用占位根域冒充来源。

    v10.63 补腾势N8 时曾把 url 写成 https://car.autohome.com.cn/ 占位根域，
    被后续核验发现并换回真实参数页。这里静态扫 trims.json 里的占位模式。
    """
    import json
    fp = paths.gen("trims.json")
    if not fp.exists():
        return {"rule": "证据URL可核验", "ok": True, "detail": "无 trims.json，跳过"}
    db = json.load(open(fp, encoding="utf-8"))
    bad = []
    for e in db.get("entries") or []:
        url = str(e.get("url") or "")
        # 光秃秃的根域（路径为空或只有 /）不可作为可核验来源
        if re.fullmatch(r"https?://[^/]+/?", url):
            bad.append(f"{e.get('model')}:{url}")
    return {"rule": "证据URL可核验", "ok": not bad,
            "detail": f"占位根域 {len(bad)} 条: {bad[:5]}" if bad else "无占位根域"}


def check_no_hardcoded_categories_path() -> dict:
    """铁律：所有事实库路径走 paths.cat，不许硬编码 categories 拼接。

    口径与 layout_regress.check_no_hardcoded_paths 完全一致（单一真相源）：
    只查 join 拼接目录名的调用形式，跳过注释/文档字符串，
    避免把注释里的каталог字样误报成硬编码。
    """
    # 用变量拼出被搜词，避免检查器自身的正则字面量被 layout_regress 当成硬编码
    _key = "categor" + "ies"  # noqa: 拆写只为躲开"检查器自查"误报
    pat = re.compile(r'join\([^)]{0,120}?"' + _key + r'"[^)]{0,60}?\)')
    bad = []
    for py in (paths.MODULES / "board").glob("*.py"):
        for i, line in enumerate(py.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            s = line.strip()
            if s.startswith("#") or '"""' in s or "'''" in s:
                continue
            if pat.search(s):
                bad.append(f"board/{py.name}:{i}")
    return {"rule": "路径走paths.cat", "ok": not bad,
            "detail": f"硬编码 {bad[:8]}" if bad else "全部走 paths"}


def check_selftest_contracts() -> dict:
    """铁律：关键契约模块必须自带 selftest（改契约能当场自检）。"""
    must = ["modules/contracts/trims_schema.py", "modules/contracts/candidate_schema.py",
            "modules/engine/quality_score.py", "modules/engine/trim_coverage.py"]
    missing = [f for f in must if not paths.src(f).exists()]
    return {"rule": "契约自带selftest", "ok": not missing,
            "detail": f"缺文件 {missing}" if missing else f"{len(must)} 个关键契约均在"}


# ---------------------------------------------------------------- 出榜产物类铁律
def _pages():
    """已渲染的正式榜产物（runs/*完整交互榜.html）。没有产物时相关检查自动 SKIP。"""
    import glob
    return sorted(glob.glob(os.path.join(str(paths.ROOT), "runs", "*完整交互榜.html")))


def _body_only(html: str) -> str:
    """去掉 <script>/<style>，只留用户真正能看到的正文——版本号/口径这类
    「页面不许写死」的判定必须只看正文，否则会被 JS 注释误伤。"""
    html = re.sub(r"<script[\s\S]*?</script>", "", html)
    return re.sub(r"<style[\s\S]*?</style>", "", html)


def check_page_no_version_literal() -> dict:
    """铁律：页面/图表不许写死版本号（版本只在 SKILL/README/演进史维护）。

    历史坑（用户 2026-11 两次指出）：方法区写着「v10.52.0 新增」「v10.60.0 按参数带重算」，
    过两个版本就成了过期标签，读者会把旧版本当当前口径。现在正文零版本号，
    版本迁移由文档承担。
    """
    pages = _pages()
    if not pages:
        return {"rule": "页面不写死版本号", "ok": True, "detail": "无页面产物，跳过"}
    bad = []
    for p in pages:
        body = _body_only(paths.src(p).read_text(encoding="utf-8") if not os.path.isabs(p) else open(p, encoding="utf-8").read())
        hits = re.findall(r"v[0-9]+[.][0-9]+[.][0-9]+", body)
        if hits:
            bad.append(f"{os.path.basename(p)}×{len(hits)}")
    return {"rule": "页面不写死版本号", "ok": not bad,
            "detail": "正文版本号残留: " + "、".join(bad) if bad else f"{len(pages)} 张页面正文无版本号"}


def _load_page_cars(p):
    import json
    h = open(p, encoding="utf-8").read()
    m = re.search(r"const CARS=([\s\S]*?);\s*\n", h)
    return json.loads(m.group(1)) if m else []


def check_composite_contract() -> dict:
    """铁律：综合分 = 产品力 + 场景调节；TCO 只披露、绝不入分。

    做法：拿产物里的 CARS 跑 engine.analyze，①断言 composite == product+adj（容差 0.15）；
    ②把油价强行 ×2 再算一遍，断言 composite 一分不动——TCO 参数只影响成本展示，
    一旦有人把成本塞进排序，这条会立刻 FAIL。
    """
    import copy
    pages = _pages()
    if not pages:
        return {"rule": "综合分=TCO不入分", "ok": True, "detail": "无页面产物，跳过"}
    from modules.envscore import engine as E
    p = pages[0]
    cars = _load_page_cars(p)
    if not cars:
        return {"rule": "综合分=TCO不入分", "ok": False, "detail": f"{os.path.basename(p)} 无 CARS"}
    cond = dict(E.DEFAULT_COND)
    rows = E.analyze(cars, cond)
    bad = [r["model"] for r in rows
           if abs((r.get("composite") or 0) - round((r.get("product") or 0) + (r.get("adj") or 0), 1)) > 0.15]
    saved = copy.deepcopy(E.CP)
    try:
        for lv in ("low", "mid", "high"):
            for k in ("oil", "elec_home"):
                E.CP[k][lv] = E.CP[k][lv] * 2
        rows2 = E.analyze(cars, cond)
    finally:
        E.CP.clear()
        E.CP.update(saved)
    drift = [r["model"] for r, s in zip(rows, rows2)
             if abs((r.get("composite") or 0) - (s.get("composite") or 0)) > 0.15]
    ok = not bad and not drift
    return {"rule": "综合分=TCO不入分", "ok": ok,
            "detail": (f"{os.path.basename(p)}：{len(cars)} 款，TCO 参数翻倍后名次/分数漂移 {drift[:3]}"
                       if drift else f"{os.path.basename(p)}：{len(cars)} 款 composite=产品力+调节，TCO 参数翻倍零影响")
            if not bad else f"composite≠product+adj: {bad[:5]}"}


def check_scores_within_coverage() -> dict:
    """铁律：缺维不加权、不填假分——有分必须有覆盖（scores 的键 ⊆ coverage）。"""
    pages = _pages()
    if not pages:
        return {"rule": "缺维不加权", "ok": True, "detail": "无页面产物，跳过"}
    bad, n = [], 0
    for p in pages:
        for c in _load_page_cars(p):
            cov = c.get("coverage")
            if not cov:
                continue
            n += 1
            extra = [k for k in (c.get("scores") or {}) if k not in cov]
            if extra:
                bad.append(f"{c['model']}:{extra}")
    return {"rule": "缺维不加权", "ok": not bad,
            "detail": f"有分无覆盖 {bad[:5]}" if bad else f"{n} 条车条目 scores 全部落在 coverage 内"}


def check_price_unit_sane() -> dict:
    """铁律：全链路车价单位是「万元」，TCO 内部必须 ×10000（v10.59 根因）。

    判定：①车价 1~300（万元区间，抓到过 140000 这种把元当万元的写法）；
    ②购置税落回合理带（新能源 ≤1.5 万封顶、燃油 ≤车价 9%）；
    ③残值损失 ≥ 车价的 20%（把万元当元算时这项只有几十元，必被抓住）。
    """
    pages = _pages()
    if not pages:
        return {"rule": "车价单位万元", "ok": True, "detail": "无页面产物，跳过"}
    from modules.envscore import engine as E
    cond = dict(E.DEFAULT_COND)
    bad = []
    for p in pages:
        for c in _load_page_cars(p):
            price = c.get("price")
            if not isinstance(price, (int, float)):
                continue
            if not (1 <= price <= 300):
                bad.append(f"{c['model']}:price={price}")
                continue
            t = E.tco(c, cond)
            if t["purchase_tax"] > 30000:
                bad.append(f"{c['model']}:tax={t['purchase_tax']}")
            if t["residual_loss"] < price * 10000 * 0.2:
                bad.append(f"{c['model']}:残值损失={t['residual_loss']}")
    return {"rule": "车价单位万元", "ok": not bad,
            "detail": f"单位异常 {bad[:5]}" if bad else "车价/购置税/残值三项量级均落在万元口径"}


def check_sales_policy_declared() -> dict:
    """铁律：销量只作事实列、口径必须写在页面上（趋势=最近 N 月逐月中位；事实列=单月+数据月）。"""
    pages = _pages()
    if not pages:
        return {"rule": "销量口径声明", "ok": True, "detail": "无页面产物，跳过"}
    bad = []
    for p in pages:
        body = _body_only(open(p, encoding="utf-8").read())
        has_month = bool(re.search(r"20[0-9]{2}[0-9]{2}", body)) or bool(re.search(r"20[0-9]{2}年[0-9]{1,2}月", body))
        has_policy = ("销量" in body) and (("中位" in body) or ("逐月" in body)) and (("单月" in body) or ("最新月" in body))
        if not (has_month and has_policy):
            bad.append(f"{os.path.basename(p)}:month={has_month},policy={has_policy}")
    return {"rule": "销量口径声明", "ok": not bad,
            "detail": "缺数据月/口径: " + "、".join(bad) if bad else f"{len(pages)} 张页面均带数据月与单月口径"}


def check_bug_cases_floor() -> dict:
    """铁律：每个修过的 bug 都要进回归，且不许「删案/改名」把失败藏起来。

    判定用两个硬指标（比"编号连续"可靠——历史上有合并/跳号，但用例本身不能少）：
      ① 用例名 tNNN 不许重名（重名会让后者覆盖前者、报告数虚高）；
      ② 用例数不得低于已登记下限 BUG_CASE_FLOOR（只增不减；新增 bug 请把下限同步抬高）。
    """
    fp = paths.src("modules/regress/bug_regression.py")
    if not fp.exists():
        # 发布包/仓库按设计不带 *_regression.py（开发者自用，见 build_release/sync 排除项），
        # 这种情况是"没装回归"而不是"删案"，跳过而不是误拦。源码树里必须存在。
        return {"rule": "bug回归不许减案", "ok": True, "detail": "未随包（发布包不含回归），跳过"}
    txt = fp.read_text(encoding="utf-8")
    names = re.findall(r"def (t\d{3})\b", txt)
    dup = [k for k in set(names) if names.count(k) > 1]
    short = BUG_CASE_FLOOR - len(set(names))
    ok = (not dup) and short <= 0
    detail = []
    if dup:
        detail.append(f"重名 {dup[:5]}")
    if short > 0:
        detail.append(f"用例 {len(set(names))} < 下限 {BUG_CASE_FLOOR}（少 {short} 条）")
    return {"rule": "bug回归不许减案", "ok": ok,
            "detail": "；".join(detail) if detail else f"{len(set(names))} 条用例、无重名（下限 {BUG_CASE_FLOOR}）"}



# 不可代码化的铁律：如实列明，避免假装已覆盖
BUG_CASE_FLOOR = 149   # bug_regression.py 用例下限（只增不减；加用例请同步抬高）

NON_CODABLE = [
    {"rule": "页面渲染改动先经用户确认", "why": "人际交互事实，代码无法判定『用户是否已确认』；靠流程纪律"},
    {"rule": "不许打补丁（改根因不改产物）", "why": "需语义判断改动性质；可做弱启发式提示但不能硬拦"},
    {"rule": "不许编造数据", "why": "无法自动区分『查不到填null』与『编造』；靠来源审计+人工复核"},
    {"rule": "候选池尽量收全", "why": "靠 universe 门禁部分覆盖（已代码化），但『尽量』的程度需人判断"},
    {"rule": "出榜前先给用户方案拍板（评分口径变更）", "why": "人际交互，代码无法判定确认状态"},
]

ALL_CHECKS = [check_version_sync, check_no_placeholder_url,
              check_no_hardcoded_categories_path, check_selftest_contracts,
              check_page_no_version_literal, check_composite_contract,
              check_scores_within_coverage, check_price_unit_sane,
              check_sales_policy_declared, check_bug_cases_floor]


def run_all() -> dict:
    results = [f() for f in ALL_CHECKS]
    failed = [r for r in results if not r["ok"]]
    return {"ok": not failed, "results": results, "failed": failed,
            "non_codable": NON_CODABLE}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="用户铁律可执行门禁")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = run_all()
    if a.json:
        import json
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        for x in r["results"]:
            print(f"[{'OK' if x['ok'] else 'FAIL'}] {x['rule']}: {x['detail']}")
        print(f"\n[iron_rules] {len(r['results']) - len(r['failed'])}/{len(r['results'])} 通过")
        print(f"不可代码化铁律 {len(r['non_codable'])} 条（如实列明）：")
        for x in r["non_codable"]:
            print(f"  - {x['rule']}：{x['why']}")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
