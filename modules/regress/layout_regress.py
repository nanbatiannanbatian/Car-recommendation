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

PKGS = ("contracts", "data", "research", "physics", "envscore", "engine", "board", "regress")
ROOT_INFRA = {"paths", "build_release", "sync_to_repo"}
THIN_MAX_LINES = 25

# 允许的包间依赖（只准向下）。未列出的边 = 违规。
ALLOWED = {
    "contracts": set(),                                  # 契约层：零依赖，谁都别依赖它之外的东西
    # data 层（2026-11-22 新增）：事实源读取（人工库+自动库合并视图）。
    # 位置卡在 contracts 之上、research/engine 之下——
    # research（写）与 engine（读）都要用同一个入口，放任一边都造成依赖倒挂。
    "data": {"contracts"},
    "research": {"contracts", "data"},
    "physics": {"contracts"},
    "envscore": {"physics", "contracts", "data"},
    "engine": {"contracts", "data"},
    "board": {"engine", "research", "envscore", "physics", "contracts", "data"},
    "regress": set(PKGS),
}

# 已知且已确认的例外：模块 -> (目标包, 原因)
# v10.1 起为空：唯一的例外 research/auto_trims -> engine.trims.validate 已通过
# 契约层（modules/contracts/trims_schema.py）消解，不再需要登记。
# 新增例外必须在此写原因，否则 layout_regress 会报依赖违规。
EXCEPTIONS: dict[tuple[str, str, str], str] = {}

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


def check_contracts_pure() -> None:
    """契约层必须纯净：零 IO、零联网、不 import 其他本项目包。"""
    bad = []
    for f in sorted((paths.MODULES / "contracts").glob("*.py")):
        if f.name == "__init__.py":
            continue
        text = f.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            if s.startswith("#"):
                continue
            if re.search(r"\b(open|requests\.|urlopen)\s*\(", s):
                bad.append(f"{f.name}:{i} 有 IO/网络调用")
            if re.search(r"^\s*(?:from|import)\s+modules\.", s):
                bad.append(f"{f.name}:{i} 依赖了其他项目包")
    check("契约层零 IO / 零依赖", not bad, "；".join(bad[:6]))


def check_rebuildable() -> None:
    """不打包的生成物必须都能重建：每个 GENERATED_JSON 都要有重建规格。"""
    missing = [n for n in paths.GENERATED_JSON if n not in paths.GENERATED_SPEC]
    check("每个生成物都有重建规格", not missing, "缺规格（会在新装包永久缺失）：" + "、".join(sorted(missing)))
    bad_cmd = []
    for name, spec in paths.GENERATED_SPEC.items():
        m = re.search(r"([\w/\-]+\.py)", spec.get("cmd", ""))
        script = m.group(1) if m else ""
        if not script or not (Path(HERE) / script).exists():
            bad_cmd.append(f"{name}->{script or '未写命令'}")
    check("重建命令指向真实入口", not bad_cmd, "；".join(bad_cmd))
    absent = [n for n in paths.CURATED_JSON if not (paths.CAT_DIR / n).exists()]
    check("人工事实库随包存在", not absent, "缺文件（应随包，不重建）：" + "、".join(sorted(absent)))


# 联网库：只允许出现在 modules/research/（唯一网络出口）
_NET_IMPORT_RE = re.compile(
    r"^\s*(?:import|from)\s+(requests|urllib|http\.client|httpx|aiohttp|websocket)\b")


def scan_network_egress() -> list[str]:
    """扫描除 research 外的所有项目源码，返回越界引用网络的「文件:行 内容」。"""
    bad: list[str] = []
    roots = [paths.MODULES] + [paths.ROOT]
    for base in roots:
        for f in sorted(base.glob("*.py")):
            rel = f.relative_to(paths.ROOT).as_posix()
            if rel.startswith("modules/research/"):
                continue
            if rel.startswith("tools/") or rel.startswith("runs/"):
                continue
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                s = line.strip()
                if s.startswith("#"):
                    continue
                if _NET_IMPORT_RE.match(line):
                    bad.append(f"{rel}:{i} {s[:60]}")
        for pkg in paths.MODULES.iterdir():
            if not pkg.is_dir() or pkg.name in ("research", "__pycache__"):
                continue
            for f in sorted(pkg.glob("*.py")):
                rel = f.relative_to(paths.ROOT).as_posix()
                for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                    s = line.strip()
                    if s.startswith("#"):
                        continue
                    if _NET_IMPORT_RE.match(line):
                        bad.append(f"{rel}:{i} {s[:60]}")
    return bad


def check_network_egress() -> None:
    """联网取数只走 research（唯一网络出口）。

    2026-11-21 用户要求把「查数据必须用 research」变成铁律并代码化。
    此前只是 MODULES.md 里一句声明——声明会忘，门禁不会：
    任何业务模块自己 import requests 抓页面，都会绕过超时/重试/留痕/防编造这一整套约束，
    也会让「数据从哪来」无法追溯。越界即 FAIL。

    **本条不接受任何例外**（2026-11-21 用户明确）：极轻量级的联网也要在 research 内实现、
    对外暴露一个函数供业务模块调用，而不是在业务模块里就地写两行 requests。
    所以这里不读 EXCEPTIONS——即使有人往例外表里登记，也照样拦。
    """
    bad = scan_network_egress()
    check("联网只走 research", not bad,
          "以下位置直接引用了网络库（应改为调用 modules/research/）：" + "；".join(bad[:6]))


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
    check_contracts_pure()
    check_rebuildable()
    check_network_egress()
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
