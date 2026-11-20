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

# ---- 发布规范配置（改这里即可调整规则）--------------------------------
REQUIRED_FRONTMATTER = [
    "name", "display_name", "display_name_en", "description",
    "description_zh", "description_en", "version", "author",
]
SKILL_NAME = "car-recommendation"          # zip 内顶层目录名
CATEGORIES_DIR = "categories"
ROOT_INCLUDE_EXT = {".py", ".md", ".json", ".txt"}   # 根目录放行扩展名
EXCLUDE_FILES = {                                     # 文件名精确排除
    "key.txt",                # 疑似密钥
    "build_release.py",       # 打包脚本自身
    "sync_to_repo.py",        # 作者私用同步脚本，含本机仓库路径
}
EXCLUDE_GLOBS = ["*.bak", "*.bak-*", "*.tmp", "*.pyc", "*.orig",
                 # v6.0/v6.1 配置快照遗留，无任何代码/文档引用（已核实）；解压后共 223KB
                 "trims_v61polluted.json", "trims_v60before.json",
                 # 回归测试脚本：开发者用，不属于 skill 运行能力（SKILL.md 约定一次性/测试脚本不进发布包）
                 "*_regress.py",
                 # 一次性榜单生成脚本：仅产出某月某价位的一次性 run 页面，非通用能力
                 "build_midsedan_202609.py", "build_midsuv_202610.py",
                 # v9.7.54：大 JSON 数据不随包发布，靠包内生成脚本联网重建（用户决定，已实测链路可通）。
                 # 故生成脚本必须在包内——ice_crash.py 从排除清单移除，其余生成脚本本就在包内。
                 # 采集/验证脚本（0 import，产出数据已不随包或为历史）：
                 "ice_funnel.py", "ice_sales.py", "ice_sensitivity.py", "verify_mcda.py",
                 # v9.7.54 用户决定：大 JSON（>=30KB，约1.1MB）不随包发布，联网重建。
                 # 生成脚本均在包内：auto_trims/build_ice_pool/master_pool/auto_levels/facts_sync/
                 # universe_build/airbags/ice_crash；实测 build_index 可无 key 联网生成 5375 条映射。
                 "trims.json", "master_pool.json", "ice_crash.json", "baseline_car_ev.json",
                 "fact_library.json", "universe_car_midsedan_allprice.json", "airbags.json",
                 "vehicle_physics.json",
                 # 一次性数据采集脚本：作者维护数据时用，仅文档提及、无代码调用
                 "fetch_bili.py", "fetch_cncap.py",
                 # 注：lifecycle_fetch/sync_cncap/auto_levels/auto_trims 虽无 import，
                 #     但被文档命令或其他模块以文件名引用，排除会触发文档引用检查失败/运行风险，保留。
                 ]
EXCLUDE_DIRS = {"runs", "cache", "__pycache__", "dist", ".git"}
MAX_ZIP_BYTES = 3 * 1024 * 1024                      # 官方压缩 zip 体积上限 3MB
# 官方实际校验的是「解压后」总大小 <= 3MB；此前只查压缩后体积导致上传被拒。
# 该常量把口径代码化，打包时第二个检查会用解压后总字节数比较。
MAX_UNCOMPRESSED_BYTES = 3 * 1024 * 1024

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
            if p.name == CATEGORIES_DIR:
                for c in sorted(p.glob("*.json")):
                    if not is_excluded_name(c.name):
                        files.append(c)
            elif p.name == "research":
                # v9.7.37：内置证据采集增强（自 research-collector），随包发布无需另装 skill
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
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in files:
            arc = f"{SKILL_NAME}/{p.relative_to(HERE).as_posix()}"
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
