# -*- coding: utf-8 -*-
"""模块结构回归（layout_regress.py）—— 守住 v10 的模块边界，防止再退回大平铺

为什么要有：2026-11-21 重构前 81 个 .py 全平铺在根目录、互相 import 成一团，
`leaderboard.py` 被 11 个模块反向依赖，路径基准散落 46 处。结构一旦没人守，
下次加个脚本又会顺手扔回根目录、又反向 import 上层，「模块化」两版就名存实亡。

守七条：
  1. 根目录只允许「薄壳」（<=25 行）或明确的基础设施脚本（paths / build_release / sync_to_repo）
  2. 每个薄壳的转发目标必须真实存在（否则 `python3 x.py` 静默炸）
  3. 包依赖只能向下：research/physics <- envscore <- engine <- board，regress 可依赖任意
     （例外必须写进 EXCEPTIONS 并写明原因，不许悄悄新增）
  4. 生成物只准落在 data/gen/：categories/ 里不得残留 GENERATED_JSON 名单里的文件
  5. 路径单一事实源：modules/ 里不得再出现 `"categories"` 字面量拼接
  6. 文档里的 `python3 xxx.py` 必须在根目录有入口（用户照文档敲不能 404）
  7. data/ 与 tools/ 不进发布包（详细在 build_release_regress，这里只查常量一致）

运行：python3 layout_regress.py
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)

PKGS = ("research", "physics", "envscore", "engine", "board", "regress")
ROOT_INFRA = {"paths", "build_release", "sync_to_repo"}
THIN_MAX_LINES = 25

# 允许的包间依赖（只准向下）。未列出的边 = 违规。
ALLOWED = {
    "research": set(),
    "physics": set(),
    "envscore": {"physics"},
    "engine": set(),
    "board": {"engine", "research", "envscore", "physics"},
    "regress": set(PKGS),
}

# 已知且已确认的例外：模块 -> (目标包, 原因)
EXCEPTIONS = {
    ("research", "auto_trims", "engine"):
        "auto_trims 采集完配置阶梯要调 engine.trims.validate 做入库前 schema 校验；"
        "校验规则属数据契约，暂时无法下沉。新增此类依赖必须在此登记。",
}

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name if ok else (name, detail))
    if ok:
        print("  PASS  " + name)
    else:
        print(f"  FAIL  {name} :: {detail}")


def check_root_thin() -> None:
    big, broken = [], []
    for p in sorted(Path(HERE).glob("*.py")):
        if p.stem in ROOT_INFRA:
            continue
        n = len(p.read_text(encoding="utf-8").splitlines())
        if n > THIN_MAX_LINES:
            big.append(f"{p.name}({n}行)")
        else:
            m = re.search(r'run_module\("modules\.(\w+)\.(\w+)"', p.read_text(encoding="utf-8"))
            if not m:
                broken.append(f"{p.name}(无转发目标)")
            else:
                if not (paths.MODULES / m.group(1) / f"{m.group(2)}.py").exists():
                    broken.append(f"{p.name} -> modules/{m.group(1)}/{m.group(2)}.py 不存在")
    check("根目录只剩薄壳", not big, "根目录出现实现文件：" + "、".join(big))
    check("薄壳转发目标均存在", not broken, "；".join(broken))


def check_direction() -> None:
    bad, exc_used = [], []
    for pkg in PKGS:
        for f in sorted((paths.MODULES / pkg).glob("*.py")):
            text = f.read_text(encoding="utf-8")
            for m in re.finditer(r"^\s*(?:from|import)\s+modules\.(\w+)", text, re.M):
                dep = m.group(1)
                if dep == pkg:
                    continue
                key = (pkg, f.stem, dep)
                if key in EXCEPTIONS:
                    exc_used.append(f"{pkg}/{f.stem}->{dep}")
                    continue
                if dep not in ALLOWED.get(pkg, set()):
                    bad.append(f"{pkg}/{f.stem} -> {dep}")
    check("包依赖只向下", not bad, "违规：" + "、".join(sorted(set(bad))))
    unknown = [k for k in EXCEPTIONS if f"{k[0]}/{k[1]}->{k[2]}" not in exc_used]
    check("例外表无僵尸条目", not unknown,
          "已不再需要的例外应删除：" + "、".join(f"{a}/{b}->{c}" for a, b, c in unknown))


def check_generated_placement() -> None:
    leaked = [n for n in paths.GENERATED_JSON if (paths.CAT_DIR / n).exists()]
    check("生成物只在 data/gen/", not leaked,
          "categories/ 残留生成物（会被打包、且与 data/gen 两份真相）：" + "、".join(sorted(leaked)))
    # 不要求 data/gen 一定存在：新装包（zip 不含 data/）本来就还没有生成物，
    # 目录由 paths.gen() 首次写入时创建。这里只校验「写入路径确实收口到 GEN_DIR」。
    gen_target = paths.gen  # noqa: F841  存在性检查
    ok = paths.GEN_DIR == paths.DATA_DIR / "gen" and paths.GEN_DIR.parent == paths.DATA_DIR
    check("生成物写入路径收口到 data/gen", ok, f"GEN_DIR={paths.GEN_DIR} 不在 data/ 下")


def check_no_hardcoded_paths() -> None:
    bad = []
    pat = re.compile(r'join\([^)]{0,120}?"categories"[^)]{0,60}?\)')
    for pkg in PKGS:
        for f in sorted((paths.MODULES / pkg).glob("*.py")):
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                s = line.strip()
                if s.startswith("#") or '"""' in s or "'''" in s:
                    continue
                if pat.search(s):
                    bad.append(f"{pkg}/{f.name}:{i}")
    check("路径走 paths.cat（无硬编码 categories 拼接）", not bad, "、".join(bad[:8]))


def check_doc_cli() -> None:
    missing: list[str] = []
    for doc in ("SKILL.md", "README.md"):
        p = paths.ROOT / doc
        if not p.exists():
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for m in re.finditer(r"python3?\s+([\w\-/]+\.py)", line):
                ref = m.group(1)
                if not (Path(HERE) / ref).exists():
                    missing.append(f"{doc}:{i} -> {ref}")
    check("文档 CLI 入口都存在", not missing, "；".join(missing[:8]))


def check_pack_exclusions() -> None:
    text = (Path(HERE) / "build_release.py").read_text(encoding="utf-8")
    check("打包排除 data/ 与 tools/", '"data"' in text and '"tools"' in text,
          "build_release.EXCLUDE_DIRS 未排除 data/tools")
    check("打包用 paths.GENERATED_JSON 判大JSON", "GENERATED_JSON" in text,
          "build_release 未复用 paths.GENERATED_JSON（名单会分叉）")
    check("打包放行 modules/ 整树", '"modules"' in text, "build_release 未收集 modules/")


def main() -> int:
    print("=" * 60)
    print("模块结构回归（layout_regress v10.0）")
    print("=" * 60)
    check_root_thin()
    check_direction()
    check_generated_placement()
    check_no_hardcoded_paths()
    check_doc_cli()
    check_pack_exclusions()
    print(f"\n结果: {len(PASS)}/{len(PASS) + len(FAIL)} 通过")
    if FAIL:
        print("失败: " + "、".join(f[0] for f in FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
