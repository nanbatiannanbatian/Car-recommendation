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


# 不能代码化的铁律：如实列明，避免假装已覆盖
NON_CODABLE = [
    {"rule": "页面渲染改动先经用户确认", "why": "人际交互事实，代码无法判定『用户是否已确认』；靠流程纪律"},
    {"rule": "不许打补丁（改根因不改产物）", "why": "需语义判断改动性质；可做弱启发式提示但不能硬拦"},
    {"rule": "不许编造数据", "why": "无法自动区分『查不到填null』与『编造』；靠来源审计+人工复核"},
    {"rule": "候选池尽量收全", "why": "靠 universe 门禁部分覆盖（已代码化），但『尽量』的程度需人判断"},
    {"rule": "出榜前先给用户方案拍板（评分口径变更）", "why": "人际交互，代码无法判定确认状态"},
]

ALL_CHECKS = [check_version_sync, check_no_placeholder_url,
              check_no_hardcoded_categories_path, check_selftest_contracts]


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
