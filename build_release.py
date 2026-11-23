#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
car-recommendation skill —— 一键发布打包脚本

用法（在本 skill 根目录执行）：
    python build_release.py

输出：
    dist/car-recommendation-vX.Y.Z.zip   （可直接上传 WorkBuddy 开放平台）

本脚本把发布规范固化在代码里，每次打包自动做以下检查，任一失败即中止：
  1. SKILL.md 存在且含合规 YAML frontmatter（必填字段齐全）
  2. 白名单筛选：只收根目录 .py/.md/.json/requirements.txt 与 categories/*.json
  3. 剔除：runs/ cache/ __pycache__/ 备份文件 key.txt 等敏感/无关文件
  4. 敏感信息扫描：本机路径、疑似密钥、sk- 前缀 token
  5. Python 语法检查（compileall）
  6. 依赖清单检查（requirements.txt 必含核心依赖）
  7. 文档命令引用检查（md 里 python xxx.py 的脚本必须在包内——官方审核硬规则）
  8. 打包 zip（顶层目录名 = frontmatter 的 name）
  9. 体积检查（<= 3MB，官方限制）
  10. 解包复查（结构与敏感信息二次确认）

设计原则：源 skill 目录可以保留 runs/、备份、key.txt 等本地文件，
打包时自动排除，无需为了发布而手动清理工作目录。
"""

import ast
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIST = HERE / "dist"

sys.path.insert(0, str(HERE))
import paths  # noqa: E402  生成物名单单一事实源
GENERATED_JSON = paths.GENERATED_JSON
REGRESSION_FIXTURES = paths.REGRESSION_FIXTURES

# v10.5.2 用户决定（2026-11-21）：「JSON 后缀的都可以不打包，数据可联网获得」。
# 发布包不再携带任何 .json —— 包括人工事实库与品类配置。数据在新装包首跑时由
# modules/research/data_bootstrap.py 从已发布仓库（唯一可信联网源）补回，
# 生成物再由 bootstrap_data.py 重建。这是一条总闸门，覆盖 categories/*.json 与
# 根目录的 *.json（cncap_scores / official_safety_scores / levels 等），
# 不再靠逐个文件列名（列名必漏，新增 JSON 又会被打进包）。
EXCLUDE_ALL_JSON = True

# ---- 发布规范配置（改这里即可调整规则）--------------------------------
REQUIRED_FRONTMATTER = [
    "name", "display_name", "display_name_en", "description",
    "description_zh", "description_en", "version", "author",
]
SKILL_NAME = "car-recommendation"          # zip 内顶层目录名
CATEGORIES_DIR = "categories"
ROOT_INCLUDE_EXT = {".py", ".md", ".json", ".txt", ".js"}   # v10：.js 是 envscore 引擎真身，必须随包
EXCLUDE_FILES = {                                     # 文件名精确排除
    # 回归用 node 测试（开发者自用）；envscore/engine.js 是产品代码，不在此列
    "dynamic_blocks_test.js", "env_options_test.js",
    "ev_range_test.js", "filter_default_test.js",
    "key.txt",                # 疑似密钥
    "build_release.py",       # 打包脚本自身
    "sync_to_repo.py",        # 作者私用同步脚本，含本机仓库路径
}
EXCLUDE_GLOBS = ["*.bak", "*.bak-*", "*.tmp", "*.pyc", "*.orig",
                 # v6.0/v6.1 配置快照遗留，无任何代码/文档引用（已核实）；解压后共 223KB
                 "trims_v61polluted.json", "trims_v60before.json",
                 # 回归测试脚本：开发者用，不属于 skill 运行能力（SKILL.md 约定一次性/测试脚本不进发布包）
                 # v10.5.0：补上 *_regression.py——bug/negative 两份历史回归 154KB+ 一直混进发布包，
                 # 是 3MB 超限的真实根因（不是把新功能砍掉）。
                 "*_regress.py", "*_regression.py",
                 # 一次性榜单生成脚本：仅产出某月某价位的一次性 run 页面，非通用能力
                 "build_midsedan_202609.py", "build_midsuv_202610.py",
                 # v9.7.54：大 JSON 数据不随包发布，靠包内生成脚本联网重建（用户决定，已实测链路可通）。
                 # 故生成脚本必须在包内——ice_crash.py 从排除清单移除，其余生成脚本本就在包内。
                 # 采集/验证脚本（0 import，产出数据已不随包或为历史）：
                 "ice_funnel.py", "ice_sales.py", "ice_sensitivity.py", "verify_mcda.py",
                 # v10.1 判据改为「能不能重建」（名单在 paths.GENERATED_JSON，这里不再手抄）：
                 #   可重建 → is_excluded_name() 依 GENERATED_JSON 排除，用户用 bootstrap_data.py 生成
                 #   人工事实库（airbags / vehicle_physics / baseline_car_ev）→ 无法重建，必须随包
                 # 下面仅保留历史快照：无任何代码引用，纯留给作者回溯
                 # 一次性数据采集脚本：作者维护数据时用，仅文档提及、无代码调用
                 "fetch_bili.py", "fetch_cncap.py",
                 # 注：lifecycle_fetch/sync_cncap/auto_levels/auto_trims 虽无 import，
                 #     但被文档命令或其他模块以文件名引用，排除会触发文档引用检查失败/运行风险，保留。
                 ]
EXCLUDE_DIRS = {"runs", "cache", "__pycache__", "dist", ".git",
                # v10.0 模块化：data/ 是生成物缓存（可重建），tools/ 是一次性迁移脚本
                "data", "tools"}
MAX_ZIP_BYTES = 3 * 1024 * 1024                      # 官方压缩 zip 体积上限 3MB
# v10.38.0：演进史完整随包（撤销 v10.36 滚动窗口）。理由：官方市场安装的 skill
# 拿不到源码仓库，包内指针指向 GitHub = 死链；且被裁的 v9 演进史里有 32 个活跃
# 代码标识符（TIRE_PRESSURE_MULT/cell_gen/evidence_soft…）在包内文档查不到 = 能力缺口。
# 完整 history 153KB，总包 ~2360KB 远低于 3MB，自包含优先于省体积。
# 官方实际校验的是「解压后」总大小 <= 3MB；此前只查压缩后体积导致上传被拒。
# 该常量把口径代码化，打包时第二个检查会用解压后总字节数比较。
MAX_UNCOMPRESSED_BYTES = 3 * 1024 * 1024
# 预警线：离 3MB 只剩不到 200KB 时提醒。随包数据（master_pool/trims_manual/fact_library…）
# 只会越加越多，等真超了再回滚很麻烦（2026-11-21 已到 2799.9KB / 3072KB）。
WARN_UNCOMPRESSED_BYTES = int(MAX_UNCOMPRESSED_BYTES * 0.92)

# 敏感信息特征：本机绝对路径 / 用户名 / 常见密钥形态
SENSITIVE_PATTERNS = [
    (r"C:/Users/", "Windows 绝对路径 C:/Users/"),
    (r"C:\\Users\\", "Windows 绝对路径 C:\\Users\\"),
    (r"海南岛南霸天", "本机用户名"),
    (r"sk-[A-Za-z0-9_-]{16,}", "疑似 API token（sk- 前缀）"),
    (r"(?i)api[_-]?key\s*[:=]\s*['\"]?[A-Za-z0-9_-]{12,}", "疑似硬编码 API key"),
]

REQUIRED_DEPS = ["lxml", "requests", "numpy", "pymcdm"]


class ReleaseError(Exception):
    pass


def fail(msg):
    raise ReleaseError(msg)


def info(msg):
    print(f"[ok] {msg}")


def warn(msg):
    print(f"[warn] {msg}")


def is_excluded_name(name):
    if name in EXCLUDE_FILES:
        return True
    if name in GENERATED_JSON:      # 生成物（可联网重建）一律不进包，名单见 paths.py
        return True
    if name in REGRESSION_FIXTURES:  # 回归测试专用样本：源码树/仓库保留，发布包不打包
        return True
    if EXCLUDE_ALL_JSON and name.lower().endswith(".json"):  # v10.5.2：零 JSON 总闸门
        return True
    for g in EXCLUDE_GLOBS:
        if name.endswith(g.lstrip("*")):
            return True
    return False


# ---- 1. frontmatter 校验 -------------------------------------------------
def check_frontmatter(skill_md):
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        fail("SKILL.md 缺少 YAML frontmatter（必须以 --- 开头）")
    end = text.find("\n---\n", 4)
    if end < 0:
        fail("SKILL.md frontmatter 未正确闭合（缺少第二个 ---）")
    fm = {}
    for line in text[4:end].splitlines():
        m = re.match(r"^([a-zA-Z_]+):\s*(.*)$", line)
        if m:
            fm[m.group(1)] = m.group(2).strip().strip('"\'')
    missing = [k for k in REQUIRED_FRONTMATTER if not fm.get(k)]
    if missing:
        fail(f"SKILL.md frontmatter 缺少必填字段: {', '.join(missing)}")
    name = fm["name"]
    if name != SKILL_NAME:
        warn(f"frontmatter name={name!r} 与脚本常量 SKILL_NAME={SKILL_NAME!r} 不一致，"
             f"zip 顶层目录以脚本常量为准")
    info(f"frontmatter 校验通过（version={fm['version']}, name={name}）")
    return fm


# ---- 2. 收集打包文件（白名单）-------------------------------------------
def collect_files():
    files = []
    for p in sorted(HERE.iterdir()):
        if p.is_dir():
            if p.name in EXCLUDE_DIRS:
                continue
            if p.name == "modules":
                # v10.0：代码按职责分包在 modules/<pkg>/，整树放行（排除 __pycache__ 与 *regress*）
                for c in sorted(p.rglob("*")):
                    if not c.is_file() or c.suffix not in ROOT_INCLUDE_EXT:
                        continue
                    if "__pycache__" in c.parts or is_excluded_name(c.name):
                        continue
                    files.append(c)
                continue
            if p.name == CATEGORIES_DIR:
                for c in sorted(p.glob("*.json")):
                    if not is_excluded_name(c.name):
                        files.append(c)
            elif p.name == "research":
                # v9.7.37：内置证据采集增强（自 research-collector），随包发布无需另装 skill
                for c in sorted(p.glob("*")):
                    if c.is_file() and not is_excluded_name(c.name) and c.suffix in ROOT_INCLUDE_EXT:
                        files.append(c)
            elif p.name == "references":
                # v10.34.0：官方渐进式披露——详细能力/演进史放 references/*.md 随包
                # （两级目录：references/<file>.md，符合平台层级限制）
                for c in sorted(p.glob("*")):
                    if c.is_file() and not is_excluded_name(c.name) and c.suffix in ROOT_INCLUDE_EXT:
                        files.append(c)
            continue
        if not p.is_file():
            continue
        if p.suffix not in ROOT_INCLUDE_EXT:
            continue
        if is_excluded_name(p.name):
            continue
        files.append(p)
    if not any(f.name == "SKILL.md" for f in files):
        fail("白名单里没有 SKILL.md")
    if not any(f.name == "requirements.txt" for f in files):
        fail("白名单里没有 requirements.txt")
    info(f"白名单收集完成：{len(files)} 个文件")
    return files


# ---- 3. 敏感信息扫描 -----------------------------------------------------
def scan_sensitive(files):
    hits = []
    for p in files:
        if p.suffix not in {".py", ".md", ".json", ".txt"}:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for pat, desc in SENSITIVE_PATTERNS:
                if re.search(pat, line):
                    hits.append(f"  {p.name}:{i}  [{desc}]  {line.strip()[:100]}")
    if hits:
        fail("发现敏感信息，打包中止：\n" + "\n".join(hits[:20]))
    info("敏感信息扫描通过")


# ---- 4. 语法检查 ---------------------------------------------------------
def check_syntax(files):
    bad = []
    for p in files:
        if p.suffix != ".py":
            continue
        try:
            ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        except SyntaxError as e:
            bad.append(f"  {p.name}:{e.lineno} {e.msg}")
    if bad:
        fail("Python 语法错误：\n" + "\n".join(bad))
    info("Python 语法检查通过")


# ---- 5. 依赖清单检查 -----------------------------------------------------
def check_requirements():
    p = HERE / "requirements.txt"
    text = p.read_text(encoding="utf-8")
    active = [l for l in text.splitlines()
              if l.strip() and not l.lstrip().startswith("#")]
    body = "\n".join(active)
    missing = [d for d in REQUIRED_DEPS if d not in body]
    if missing:
        fail(f"requirements.txt 缺少核心依赖（未注释）: {', '.join(missing)}")
    info("依赖清单检查通过")


# ---- 6. 文档命令引用检查 -------------------------------------------------
def check_doc_script_refs(files):
    """官方审核硬规则：md 文档里 `python xxx.py` 引用的脚本必须在包内。

    审核曾因 VDH_LIFECYCLE_RUNBOOK.md 引用了包外的 build_html.py 而驳回
    （评级：内置文件缺失）。此处在打包前主动拦截，防止复发。
    """
    packed = {p.name for p in files}
    bad = []
    for p in files:
        if p.suffix != ".md":
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            for m in re.finditer(r"python3?\s+([\w\-]+\.py)", line):
                ref = m.group(1)
                if ref not in packed:
                    bad.append(f"  {p.name}:{i}  引用包外脚本 {ref}  | {line.strip()[:90]}")
    if bad:
        fail("文档引用了包内不存在的脚本（官方审核会判「内置文件缺失」）：\n"
             + "\n".join(bad))
    info("文档脚本引用检查通过（所有 python xxx.py 引用的脚本均在包内）")


# ---- 6b. zip 内扁平化（v10.31.0）-----------------------------------------
# 平台上传校验：zip 内最多两级目录（根/二级/文件，即路径最多 3 段）。
# 源码树保留 modules/<pkg>/ 分包（357 处 import、单一事实源、回归 paths.src 依赖），
# 打包时做「编译式」变换产出扁平包：modules/<pkg>/x.py -> modules/x.py，
# 并改写包内所有跨包 import / runpy / paths 动态加载，使扁平包自身可运行。
# 源码与 GitHub 仓库完全不动；改写只作用于 zip 内字节。
FLAT_PKGS = ("contracts", "research", "physics", "envscore", "engine", "board", "regress", "data")


def _flat_arcname(p: Path) -> str:
    """源码路径 -> zip 内路径（扁平化 modules/<pkg>/x.py -> modules/x.py）。"""
    rel = p.relative_to(HERE).as_posix()
    parts = rel.split("/")
    if len(parts) == 3 and parts[0] == "modules" and parts[1] in FLAT_PKGS:
        name = parts[2]
        # 各子包的 __init__.py 都是纯 docstring（无可执行代码），扁平后无意义且会重名 → 丢弃
        if name == "__init__.py":
            return ""
        return f"modules/{name}"
    return rel


def strip_changelog(text: str, arc: str) -> str:
    """v10.32.0→v10.34.0：打包时的文档处理（仅 zip 内字节）。

    v10.34.0 起 SKILL.md 已按官方渐进式披露重构：正文只留核心+指针，
    详细能力/演进史物理移到 references/detail.md 与 references/evolution.md 随包，
    不再需要剥离（SKILL.md 内已无 `## v9.0.0`）——直接原样返回。
    README.md 仍含逐版演进史（源码树保留审计），打包时剥离该段留指针。
    """
    if arc == "SKILL.md":
        return text
    marker = "\n## v9.0.0"
    idx = text.find(marker)
    if idx < 0:
        fail(f"{arc} 未找到演进史剥离点 '## v9.0.0'（文档结构升级后需同步此规则）")
    head = text[:idx].rstrip()
    repo = "https://github.com/nanbatiannanbatian/Car-recommendation"
    return head + (f"\n\n---\n\n> **版本演进史（v9.0.0 起）不在 README 内**："
                   f"完整逐版历史随包见 `references/evolution.md`，源码仓库 {repo} 亦有。\n")


def rewrite_for_flat(text: str, arc: str) -> str:
    """把源码里的分层 import 改写为扁平包可用的 import（仅作用于 zip 内字节）。"""
    # 1) runpy 薄壳与显式三级 import：modules.<pkg>.<mod> -> modules.<mod>
    for pkg in FLAT_PKGS:
        text = re.sub(rf"modules\.{pkg}\.([A-Za-z_][\w]*)", r"modules.\1", text)
    # 2) 二级包导入：from modules.<pkg> import X -> from modules import X
    for pkg in FLAT_PKGS:
        text = re.sub(rf"from modules\.{pkg} import", "from modules import", text)
        text = re.sub(rf"import modules\.{pkg}(?!\.)", "import modules", text)
    # 3) paths.py：src() 的 glob 不再有子包目录；module() 的 pkg 轮询改直查 modules
    if arc == "paths.py":
        text = text.replace('MODULES.glob(f"*/{p.stem}.py")', 'MODULES.glob(f"{p.stem}.py")')
        text = text.replace('return MODULES / "research" / "config.json"',
                            'return MODULES / "config.json"')
        old_loop = '''    for pkg in ("contracts", "research", "physics", "envscore", "engine", "board", "regress"):
        full = f"modules.{pkg}.{dotted}"
        try:
            return importlib.import_module(full)
        except ModuleNotFoundError as e:
            if getattr(e, "name", "") != full:
                raise  # 模块存在但内部依赖缺失，照实抛
    return importlib.import_module(dotted)'''
        new_loop = '''    # v10.31.0：zip 内已扁平为 modules/<name>.py，直接查 modules 包
    full = f"modules.{dotted}"
    try:
        return importlib.import_module(full)
    except ModuleNotFoundError as e:
        if getattr(e, "name", "") != full:
            raise  # 模块存在但内部依赖缺失，照实抛
    return importlib.import_module(dotted)'''
        if old_loop not in text:
            fail("paths.module() 轮询结构与预期不符（源码升级后需同步扁平改写规则）")
        text = text.replace(old_loop, new_loop)
    # 4) __file__ 三级反推（原 modules/engine/x.py -> 根；扁平后 modules/x.py 少一层）
    if "dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))" in text:
        text = text.replace("dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))",
                            "dirname(os.path.dirname(os.path.abspath(__file__)))")
    return text


def check_flat_no_collision(files):
    """扁平后 modules/<name>.py 不得重名（__init__.py 已丢弃）。"""
    seen: dict[str, str] = {}
    for p in files:
        arc = _flat_arcname(p)
        if not arc or not arc.startswith("modules/"):
            continue
        if arc in seen and seen[arc] != p.as_posix():
            fail(f"扁平化后模块重名: {arc} <- {seen[arc]} / {p}")
        seen[arc] = p.as_posix()
    info(f"扁平化名称检查通过（{len(seen)} 个模块无重名）")


# ---- 7. 打包 -------------------------------------------------------------
def build_zip(files):
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()
    version = "0.0.0"
    m = re.search(r"^version:\s*(.+)$",
                  (HERE / "SKILL.md").read_text(encoding="utf-8"), re.M)
    if m:
        version = m.group(1).strip().strip('"\'')
    out = DIST / f"{SKILL_NAME}-v{version}.zip"
    uncompressed = 0
    skipped_init = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in files:
            flat_rel = _flat_arcname(p)
            if not flat_rel:          # 丢弃各子包纯 docstring 的 __init__.py
                skipped_init += 1
                continue
            arc = f"{SKILL_NAME}/{flat_rel}"
            if p.suffix == ".py":
                text = rewrite_for_flat(p.read_text(encoding="utf-8"), flat_rel)
                z.writestr(arc, text)
                uncompressed += len(text.encode("utf-8"))
            elif flat_rel in ("SKILL.md", "README.md"):
                # v10.32.0：发布包剥离逐版演进史（源码树完整保留，仅 zip 内字节裁剪）
                text = strip_changelog(p.read_text(encoding="utf-8"), flat_rel)
                z.writestr(arc, text)
                uncompressed += len(text.encode("utf-8"))
            else:
                # v10.38.0：references/evolution.md 完整随包（不再滚动裁剪，自包含优先）
                z.write(p, arc)
                uncompressed += p.stat().st_size
    size = out.stat().st_size
    if size > MAX_ZIP_BYTES:
        out.unlink()
        fail(f"zip 体积 {size/1048576:.2f}MB 超过官方压缩上限 3MB")
    # 关键：官方校验「解压后」总大小。压缩后 <3MB 不代表解压后 <3MB（曾因此被拒）。
    if uncompressed > MAX_UNCOMPRESSED_BYTES:
        out.unlink()
        over = uncompressed - MAX_UNCOMPRESSED_BYTES
        fail(f"解压后总大小 {uncompressed/1024:.1f}KB 超过官方 3MB 限制（超出 {over/1024:.1f}KB）；"
             f"请清理无引用的大文件或从 EXCLUDE_GLOBS 排除")
    info(f"打包完成：{out.name}（压缩 {size/1048576:.2f}MB / 解压后 {uncompressed/1024:.1f}KB，{len(files)} 文件）")
    if uncompressed > WARN_UNCOMPRESSED_BYTES:
        left = (MAX_UNCOMPRESSED_BYTES - uncompressed) / 1024
        print(f"[warn] 解压后距 3MB 上限只剩 {left:.0f}KB —— 下次再加随包数据就会超；"
              f"先看 paths.CURATED_JSON 里哪些能转成可重建生成物", file=sys.stderr)
    return out


# ---- 7. 解包复查 ---------------------------------------------------------
def verify_zip(out):
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        tops = {n.split("/")[0] for n in names}
        if tops != {SKILL_NAME}:
            fail(f"zip 顶层目录异常：{tops}")
        if f"{SKILL_NAME}/SKILL.md" not in names:
            fail("zip 内缺少 SKILL.md")
        # v10.31.0：平台硬规则——zip 内路径最多 3 段（根/二级/文件）
        deep = [n for n in names if len(n.split("/")) > 3]
        if deep:
            fail(f"zip 内目录层级超限（>{SKILL_NAME}/<二级>/<文件>）：{len(deep)} 条，"
                 f"示例 {deep[:3]}")
        info("目录层级检查通过（最多 根/二级/文件）")
        bad = []
        for n in names:
            if not n.endswith((".py", ".md", ".json", ".txt")):
                continue
            t = z.read(n).decode("utf-8", "ignore")
            for pat, desc in SENSITIVE_PATTERNS:
                if re.search(pat, t):
                    bad.append(f"  {n} [{desc}]")
            if ".bak" in n or "__pycache__" in n or n.endswith("key.txt"):
                bad.append(f"  {n} [不应存在的文件]")
        if bad:
            out.unlink()
            fail("解包复查失败：\n" + "\n".join(bad))
    info("解包复查通过（结构、敏感信息、文件清单）")


def main():
    print("=" * 60)
    print("car-recommendation skill 发布打包")
    print("=" * 60)
    try:
        fm = check_frontmatter(HERE / "SKILL.md")
        files = collect_files()
        scan_sensitive(files)
        check_syntax(files)
        check_requirements()
        check_doc_script_refs(files)
        check_flat_no_collision(files)
        out = build_zip(files)
        verify_zip(out)
    except ReleaseError as e:
        print(f"\n[FAIL] {e}", file=sys.stderr)
        sys.exit(1)
    print("\n" + "=" * 60)
    print(f"SUCCESS  可直接上传：{out}")
    print("=" * 60)


if __name__ == "__main__":
    main()
