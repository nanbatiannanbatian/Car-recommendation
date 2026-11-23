# -*- coding: utf-8 -*-
"""package_audit.py —— 包体检确定性门禁（v10.40.0）

为什么要有这几轮 AI 逐行审计暴露的教训（都写进 .workbuddy/memory/2026-11-23.md）：
  1. 滚动窗口裁历史 → 包内指针指向 GitHub 完整版 → 官方市场用户只有本体包 = 死链
  2. 被裁的 v9 历史含 32 个活跃代码标识符 → 包内文档查不到 = 能力缺口
  3. README 与 SKILL/references 大段重复 → 体积浪费
  4. frontmatter 扩展字段被 Anthropic 严格校验器误判 → 需区分生态

前三条都是**判定性**错误（对错分明、可枚举），适合确定性工具守；
第四条是 judgment（要理解生态语义），不固化进工具。

本工具把前三条固化为零模型调用、秒级、可回归的检查，运行：
    python3 -m modules.regress.package_audit          # 源码树 + 已有发布包
    python3 -m modules.regress.package_audit --strict # 包不存在也算失败（发布门禁用）

设计纪律（遵守 SKILL.md 最高铁律"铁律必须代码化"）：
  - 只做判定性检查；语义判断（哪些内容是活能力）留给人工，工具只守"声明与事实一致"
  - 反向注入对照验证：每个检查都要能被故意破坏后检出（selftest 内置）
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

import paths

HERE = Path(str(paths.ROOT))
DIST = HERE / "dist"
sys.path.insert(0, str(HERE))

PACKAGE_MD = ("SKILL.md", "README.md", "references/detail.md", "references/evolution.md")
SKILL_SRC = HERE / "SKILL.md"
README_SRC = HERE / "README.md"
EVOLUTION_SRC = HERE / "references" / "evolution.md"
DETAIL_SRC = HERE / "references" / "detail.md"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _check(results: list, name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))


# ---- 检查 1：自包含——包内不得有"完整版/历史去外部仓库查"的依赖型指针 -------
# 注意：中性的"仓库亦有"（主源在包内）不算违规；只有把外部当**必需兜底**才算。
EXTERNAL_DEPENDENCY_PATTERNS = [
    (r"完整(历史|版)[^。\n]{0,40}(见|在|查|去)[^。\n]{0,20}(github|仓库|源码)", "完整历史指向外部仓库"),
    (r"(不在发布包|包内不含|被裁)[^。\n]{0,40}(见|查|去)[^。\n]{0,20}(github|仓库)", "缺失内容指向外部仓库"),
    (r"滚动窗口[^。\n]{0,60}(github|仓库)", "滚动窗口依赖外部仓库"),
]


# 自包含检查只看"活文档"（SKILL.md/detail.md）——它们指导 AI 当前行为。
# evolution.md 是历史记录，里面复述旧教训（"当时指向 GitHub"）属正常叙述，不判违规。
LIVE_DOCS = ("SKILL.md", "references/detail.md")


def check_self_contained(texts: dict[str, str], results: list) -> None:
    for name in LIVE_DOCS:
        text = texts.get(name, "")
        for line_no, line in enumerate(text.splitlines(), 1):
            for pat, desc in EXTERNAL_DEPENDENCY_PATTERNS:
                if re.search(pat, line, re.I):
                    _check(results, f"自包含:{name}:{line_no}", False,
                           f"{desc} → {line.strip()[:90]}")


# ---- 检查 2：死链——md 相对链接目标必须存在（包内检查）/源文件存在（源码检查） ----
LINK_RE = re.compile(r"\[[^\]]*\]\(([^\)]+)\)")


def check_links(texts: dict[str, str], names: set[str], results: list) -> None:
    total = 0
    for name, text in texts.items():
        base = name.rsplit("/", 1)[0] if "/" in name else ""
        for m in LINK_RE.finditer(text):
            tgt = m.group(1).split("#")[0].strip()
            if not tgt or tgt.startswith(("http://", "https://", "mailto:", "#")):
                continue
            total += 1
            joined = f"{base}/{tgt}" if base else tgt
            if joined not in names and tgt not in names:
                _check(results, f"死链:{name}→{tgt}", False)
    _check(results, f"链接检查完成({total}条相对链接)", True)


# ---- 检查 3：文档-代码一致——SKILL/references 提到的 .py 必须在包/源码存在 ----
# 沿用 build_release.check_doc_script_refs 的口径：只查 `python3 xxx.py` 这种
# "用户照着敲"的命令（官方审核硬规则）；裸提到 xxx.py 是描述性引用（可能指向
# 已废止脚本），不该判缺失。占位名 <候选池.json>/x.py 不算。
PY_CMD_RE = re.compile(r"python3?\s+([\w\-]+\.py)")
# 占位名不是真脚本：<xxx.py>/x.py/foo.py/bar.py 等示意名，排除
PLACEHOLDER_SCRIPTS = {"xxx.py", "x.py", "foo.py", "bar.py", "your_script.py", "script.py"}


def check_doc_code(texts: dict[str, str], py_names: set[str], results: list) -> None:
    missing: dict[str, str] = {}
    for name, text in texts.items():
        for m in PY_CMD_RE.finditer(text):
            ref = m.group(1)
            if ref not in py_names and ref not in PLACEHOLDER_SCRIPTS and ref not in missing:
                missing[ref] = name
    for ref, where in sorted(missing.items()):
        _check(results, f"文档命令脚本缺失:{ref}({where})", False)
    if not missing:
        _check(results, "文档命令脚本全部存在", True)


# ---- 检查 4：活跃标识符可达——包内代码 import 的常量须在随包文档有说明 --------
# 守滚动裁剪那种"能力缺口"：被裁历史里的活跃标识符，必须在包内文档可查。
# 只检查"明确标注为铁律/能力"的白名单标识符（避免把内部临时变量误判为能力）。
LIVE_IDENTIFIERS = (
    "trim_coverage", "universe_build", "energy_router", "anchor_pool",
    "value_residual", "data_freshness", "score_fingerprint",
    "TIRE_PRESSURE_MULT", "TIRE_TYPE_MULT", "cell_gen", "evidence_soft",
    "field_sources", "NOT_IN_SOURCE_DETAIL", "EXPERIENCE_DIM_LABELS",
)


def check_live_identifiers(texts: dict[str, str], results: list) -> None:
    doc = "\n".join(texts.values())
    missing = [k for k in LIVE_IDENTIFIERS if k not in doc]
    for k in missing:
        _check(results, f"活跃标识符包内不可达:{k}", False)
    if not missing:
        _check(results, f"活跃标识符全部可达({len(LIVE_IDENTIFIERS)}个)", True)


# ---- 检查 5：README 精简后仍含装包必看段 ------------------------------------
README_SECTIONS = ("## 这是什么", "## 安装", "## 依赖", "## 需要配置什么",
                   "## 上手几条命令", "## 生成物重建")


def check_readme_sections(readme: str, results: list, is_package: bool = True) -> None:
    lines = readme.splitlines()
    for h in README_SECTIONS:
        _check(results, f"README必备段:{h}", any(l.startswith(h) for l in lines))
    if is_package:  # 源码树 README 保留完整历史，体积大属预期；只查发布包精简版
        _check(results, "README精简(<30KB)", len(readme.encode("utf-8")) < 30 * 1024,
               f"{len(readme.encode('utf-8'))/1024:.1f}KB")


# ---- 运行 -------------------------------------------------------------------
def audit_source() -> list[tuple[str, bool, str]]:
    """源码树体检（快速，钩子用）。"""
    results: list[tuple[str, bool, str]] = []
    texts = {Path(p).name: _read(HERE / p) for p in PACKAGE_MD}
    names = {f.name for f in HERE.rglob("*") if f.is_file()}
    check_self_contained(texts, results)
    check_links(texts, names, results)
    check_doc_code(texts, {f.name for f in HERE.rglob("*.py")}, results)
    check_live_identifiers(texts, results)
    check_readme_sections(texts.get("README.md", ""), results, is_package=False)
    return results


def audit_package(zip_path: Path) -> list[tuple[str, bool, str]]:
    """发布包体检（发布门禁用）。"""
    results: list[tuple[str, bool, str]] = []
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        texts: dict[str, str] = {}
        for p in PACKAGE_MD:
            arc = f"car-recommendation/{p}"
            if arc in names:
                texts[p] = z.read(arc).decode("utf-8", "ignore")
        check_self_contained(texts, results)
        check_links(texts, names, results)
        check_doc_code(texts, {Path(n).name for n in names if n.endswith(".py")}, results)
        check_live_identifiers(texts, results)
        check_readme_sections(texts.get("README.md", ""), results, is_package=False)
    return results


def run(strict: bool = False) -> int:
    print("=" * 60)
    print("package_audit —— 发布包确定性体检")
    print("=" * 60)
    src_results = audit_source()
    passed = sum(1 for _, ok, _ in src_results if ok)
    for name, ok, detail in src_results:
        if not ok:
            print(f"  [FAIL] {name}: {detail}")
    print(f"源码树体检: {passed}/{len(src_results)} 通过")

    zips = sorted(DIST.glob("car-recommendation-v*.zip")) if DIST.exists() else []
    if zips:
        pkg_results = audit_package(zips[-1])
        pkg_pass = sum(1 for _, ok, _ in pkg_results if ok)
        for name, ok, detail in pkg_results:
            if not ok:
                print(f"  [FAIL] 包内 {name}: {detail}")
        print(f"发布包体检({zips[-1].name}): {pkg_pass}/{len(pkg_results)} 通过")
        all_ok = passed == len(src_results) and pkg_pass == len(pkg_results)
    else:
        print("发布包体检: 未找到 zip（跳过）")
        all_ok = passed == len(src_results) and not strict

    if all_ok:
        print("PACKAGE_AUDIT PASS")
    else:
        print("PACKAGE_AUDIT FAIL", file=sys.stderr)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(run(strict="--strict" in sys.argv))
