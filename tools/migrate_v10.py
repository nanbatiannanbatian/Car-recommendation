# -*- coding: utf-8 -*-
"""v10.0 模块化迁移（一次性 bootstrap，不进发布包）

做四件事：
  1. 按 MAPPING 把根目录 .py 搬进 modules/<pkg>/
  2. 改写包内绝对导入：import leaderboard -> from modules.engine import leaderboard
  3. 路径收口：HERE/BASE 从「脚本目录」改为「项目根」（paths.ROOT），
     categories 生成物走 paths.cat()
  4. 生成根目录 CLI 兼容入口（薄壳），保证文档里的 python3 xxx.py 照旧可用

用法：
    python tools/migrate_v10.py --dry-run
    python tools/migrate_v10.py --apply
"""
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MAPPING: dict[str, list[str]] = {
    "research": [
        # 所有对外取数 / 检索 / 采集的唯一出口
        "align_specs", "auto_levels", "auto_trims", "build_cell_facts", "build_ice_pool",
        "ciasi_text_decode", "discover", "fetch_bili", "fetch_cncap", "fetch_complaints",
        "fetch_dcd_sales", "fetch_sales", "fetch_specs", "filings", "ice_crash", "ice_sales",
        "ingest", "koubei_merge", "lifecycle_detect", "lifecycle_fetch", "notes_tool",
        "swoiow_probe", "sync_cncap", "sync_cell_brand", "trim_ingest", "universe_build",
    ],
    "physics": ["ev_range_model"],
    "envscore": ["build_energy_compare"],
    "engine": [
        "admit", "airbags", "crash_systems", "cross_verify", "energy_router",
        "evidence_consistency", "experience_score", "facts_sync", "ice_scoring", "leaderboard",
        "lifecycle_confirm", "quality_score", "ranking", "robust", "safety_check",
        "schema_audit", "score_fingerprint", "sibling_check", "spec_facts", "spec_score",
        "thirdparty", "trim_annotate", "trim_board", "trim_coverage", "trims", "verify_mcda",
    ],
    "board": [
        "backfill", "board_v2", "board_view", "build_midsedan_202609", "build_midsuv_202610",
        "build_midsuv_full", "cross_energy_sections", "ev_battery_board", "ev_cell_board",
        "filings_board", "forecast", "ice_board", "ice_funnel", "ice_sensitivity",
        "master_pool", "monthly", "pipeline", "render_board_html", "warehouse",
    ],
    "regress": [
        "bug_regression", "negative_regression", "energy_compare_regress",
        "energy_router_regress", "midsuv_full_regress", "build_release_regress",
    ],
}

JS_TESTS = ["dynamic_blocks_test.js", "env_options_test.js", "ev_range_test.js",
            "filter_default_test.js"]

# 留在根目录的基础设施脚本
STAY_ROOT = {"build_release", "sync_to_repo", "paths"}

MOD2PKG = {m: pkg for pkg, mods in MAPPING.items() for m in mods}

HERE_PATTERNS = [
    (re.compile(r"^(\s*)HERE = os\.path\.dirname\(os\.path\.abspath\(__file__\)\)\s*$", re.M),
     r"\1HERE = str(paths.ROOT)"),
    (re.compile(r"^(\s*)BASE = os\.path\.dirname\(os\.path\.abspath\(__file__\)\)\s*$", re.M),
     r"\1BASE = str(paths.ROOT)"),
    (re.compile(r"^(\s*)HERE = Path\(__file__\)\.resolve\(\)\.parent\s*$", re.M),
     r"\1HERE = paths.ROOT"),
    (re.compile(r"^(\s*)BASE_DIR = os\.path\.dirname\(os\.path\.abspath\(__file__\)\)\s*$", re.M),
     r"\1BASE_DIR = str(paths.ROOT)"),
]

# os.path.join(HERE, "categories", x) -> paths.cat(x)；join(BASE,"categories") -> str(paths.CAT_DIR)
CAT1 = re.compile(r'os\.path\.join\(\s*(?:HERE|BASE)\s*,\s*"categories"\s*,\s*([^()]*?)\s*\)')
CAT0 = re.compile(r'os\.path\.join\(\s*(?:HERE|BASE)\s*,\s*"categories"\s*\)')
CAT_CATDIR = re.compile(r'os\.path\.join\(\s*CAT_DIR\s*,\s*([^()]*?)\s*\)')
RESEARCH_CFG = re.compile(r'os\.path\.join\(\s*HERE\s*,\s*"research"\s*,\s*"config\.json"\s*\)')


def rewrite_import(line: str) -> str:
    """把一行本地导入改写成包内绝对导入；非本地导入原样返回。"""
    m = re.match(r"^(\s*)import\s+([A-Za-z_]\w*)(\s+as\s+(\w+))?\s*(#.*)?$", line)
    if m:
        indent, mod, _, alias, cmt = m.groups()
        if mod in MOD2PKG:
            tail = f" as {alias}" if alias else ""
            return f"{indent}from modules.{MOD2PKG[mod]} import {mod}{tail}"
        return line
    m = re.match(r"^(\s*)from\s+([A-Za-z_]\w*)\s+import\s+(.+?)\s*(#.*)?$", line)
    if m:
        indent, mod, names, cmt = m.groups()
        if mod in MOD2PKG:
            return f"{indent}from modules.{MOD2PKG[mod]}.{mod} import {names}"
        return line
    return line


def migrate_file(path: Path, pkg: str) -> tuple[str, list[str]]:
    src = path.read_text(encoding="utf-8", errors="surrogateescape")
    notes: list[str] = []
    out_lines = []
    for line in src.splitlines():
        stripped = line.strip()
        # 自引用不改（模块内部 `from trim_board import ...` 的极端写法）
        new = rewrite_import(line) if not stripped.startswith("#") else line
        if new != line:
            notes.append(f"import: {stripped[:60]} -> {new.strip()[:70]}")
        out_lines.append(new)
    text = "\n".join(out_lines) + ("\n" if src.endswith("\n") else "")

    # 路径收口
    for pat, rep in HERE_PATTERNS:
        text2, n = pat.subn(rep, text)
        if n:
            notes.append(f"HERE->paths.ROOT x{n}")
            text = text2

    text, n = RESEARCH_CFG.subn("str(paths.research_config())", text)
    if n:
        notes.append(f"research config x{n}")
    text, n = CAT1.subn(r"str(paths.cat(\1))", text)
    if n:
        notes.append(f"categories join -> paths.cat x{n}")
    text, n = CAT0.subn("str(paths.CAT_DIR)", text)
    if n:
        notes.append(f"categories dir x{n}")
    text, n = CAT_CATDIR.subn(r"str(paths.cat(\1))", text)
    if n:
        notes.append(f"CAT_DIR join -> paths.cat x{n}")

    # 需要 paths 的补 import（放在 HERE/BASE 赋值前）
    if re.search(r"\bpaths\.", text) and not re.search(r"^import paths\b", text, re.M):
        m = re.search(r"^(\s*)(HERE|BASE|BASE_DIR) = ", text, re.M)
        if m:
            text = text[: m.start()] + f"{m.group(1)}import paths\n" + text[m.start():]
        else:
            m = re.search(r"^(from __future__ import annotations\s*)$", text, re.M)
            ins = m.end() if m else 0
            text = text[:ins] + "\nimport paths\n" + text[ins:]
        notes.append("+ import paths")

    # 模块顶部留一句来源标注（可追溯迁移）
    return text, notes


FACADE = '''# -*- coding: utf-8 -*-
"""CLI 兼容入口（v10.0 模块化）：实现已迁到 modules/{pkg}/{mod}.py。

保留本文件是为了让 SKILL.md / README 里的 `python3 {mod}.py` 命令继续可用。
逻辑一律改 modules/{pkg}/{mod}.py，本文件不要写业务代码。
"""
import runpy

if __name__ == "__main__":
    runpy.run_module("modules.{pkg}.{mod}", run_name="__main__", alter_sys=True)
'''


def doc_referenced() -> set[str]:
    names: set[str] = set()
    for doc in ("SKILL.md", "README.md", "ARCHITECTURE.md"):
        p = ROOT / doc
        if not p.exists():
            continue
        for m in re.finditer(r"python3?\s+([A-Za-z0-9_/]+\.py)", p.read_text(encoding="utf-8")):
            names.add(m.group(1))
    return names


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    apply = a.apply

    root_py = {p.stem: p for p in ROOT.glob("*.py")}
    assigned = {m for m in MOD2PKG}
    missing = sorted(set(root_py) - assigned - STAY_ROOT)
    if missing:
        print(f"[ERROR] 以下根目录脚本未分配模块：{missing}")
        return 2
    stray = sorted(assigned - set(root_py))
    if stray and not any((ROOT / "modules" / pkg / f"{m}.py").exists()
                         for m in stray for pkg in MAPPING):
        print(f"[ERROR] 映射表里有不存在的文件：{stray}")
        return 2

    moved = 0
    for pkg, mods in MAPPING.items():
        dst_dir = ROOT / "modules" / pkg
        dst_dir.mkdir(parents=True, exist_ok=True)
        for mod in mods:
            src = ROOT / f"{mod}.py"
            if not src.exists():
                continue
            dst = dst_dir / f"{mod}.py"
            text, notes = migrate_file(src, pkg)
            if apply:
                dst.write_text(text, encoding="utf-8")
                src.unlink()
            moved += 1
            print(f"  [{pkg:9s}] {mod}.py" + (f"  ({'; '.join(notes)})" if notes and not apply else ""))

    for js in JS_TESTS:
        src = ROOT / js
        if src.exists():
            dst = ROOT / "modules" / "regress" / js
            if apply:
                shutil.move(str(src), str(dst))
            print(f"  [regress  ] {js}")

    # research 目录：脚本进包，根目录留兼容入口
    rsrc = ROOT / "research" / "researcher.py"
    if rsrc.exists():
        dst = ROOT / "modules" / "research" / "researcher.py"
        text, notes = migrate_file(rsrc, "research")
        # researcher.py 的 HERE 必须留在模块目录（config.json 在旁边）
        text = text.replace("HERE = str(paths.ROOT)", "HERE = os.path.dirname(os.path.abspath(__file__))")
        if apply:
            dst.write_text(text, encoding="utf-8")
            rsrc.unlink()
            shutil.copy2(ROOT / "research" / "config.json", ROOT / "modules" / "research" / "config.json")
        print(f"  [research ] researcher.py (+config.json)")

    # 根目录兼容入口：文档引用 + 全部回归 + 打包脚本
    facades: set[str] = set()
    for ref in doc_referenced():
        ref = ref.replace("research/", "")
        if ref.endswith(".py") and ref[:-3] in MOD2PKG:
            facades.add(ref[:-3])
    facades |= {m for m in MOD2PKG if m.endswith("_regress") or m in
                ("bug_regression", "negative_regression", "build_release")}
    fpath = ROOT / "research" / "researcher.py"
    if apply:
        fpath.parent.mkdir(exist_ok=True)
        fpath.write_text(FACADE.format(pkg="research", mod="researcher"), encoding="utf-8")
    print(f"  [facade   ] research/researcher.py")
    for mod in sorted(facades):
        p = ROOT / f"{mod}.py"
        if apply:
            p.write_text(FACADE.format(pkg=MOD2PKG[mod], mod=mod), encoding="utf-8")
        print(f"  [facade   ] {mod}.py -> modules/{MOD2PKG[mod]}/")

    print(f"\n{'APPLIED' if apply else 'DRY-RUN'}: {moved} 个实现文件, {len(facades) + 1} 个兼容入口")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
