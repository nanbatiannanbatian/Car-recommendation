#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""install_hooks.py —— 把确定性提交钩子装进 GitHub 仓库（v10.40.0）

钩子文件在 .git/hooks/ 下，不受 git 跟踪——换机器/重新 clone 后会丢。
本脚本是钩子的**唯一可写源**，重装时跑一次即可：
    python install_hooks.py            # 装到默认仓库路径
    python install_hooks.py <repo路径> # 指定仓库

设计：钩子只跑 <1s 的 package_audit 源码检查（自包含/死链/文档命令/活跃标识符），
不跑 41s 的 bug_regression（显式命令才跑），带 SKIP_AUDIT=1 逃生阀。
详见钩子内容与设计理由。
"""
import os
import sys
from pathlib import Path

HOOK_NAME = "pre-commit"
DEFAULT_REPO = Path("C:/Users/海南岛南霸天/WorkBuddy/2026-11-13-15-03-08/omni-debate-github")

HOOK_BODY = r'''#!/bin/sh
# pre-commit 快速门禁（v10.40.0）——确定性工具取代人工逐行检查
# 每次提交跑 <1s 的判定性检查；41s 的全量 bug_regression 不在此（显式命令跑）
# 逃生阀：SKIP_AUDIT=1 git commit ...（紧急情况用，事后补跑）

if [ "${SKIP_AUDIT:-0}" = "1" ]; then
  echo "[pre-commit] SKIP_AUDIT=1，跳过体检（记得事后补跑）"
  exit 0
fi

cd "$(git rev-parse --show-toplevel)" || exit 1
PY=python
command -v python3 >/dev/null 2>&1 && PY=python3

# 只对涉及代码/核心文档的提交跑门禁；纯 runs/ 产物提交跳过
STAGED=$(git diff --cached --name-only)
if ! echo "$STAGED" | grep -qE '\.(py|md|json)$|(^|/)SKILL\.md$|(^|/)README\.md$'; then
  echo "[pre-commit] 无代码/文档变更，跳过体检"
  exit 0
fi

echo "[pre-commit] 快速体检（包自包含/死链/文档命令/活跃标识符，~0.4s）..."
FAILED=0

# 包体检源码树部分——在 skill 目录与 GitHub 仓库都能过（自包含，不依赖随包 JSON）。
# 不跑发布包 zip 部分（钩子时 zip 可能过期）；不跑 layout_regress（它查人工事实库存在，
# 而仓库按零JSON策略不带这些文件，只在 skill 目录 PASS——钩子在仓库跑会误拦）。
if ! PYTHONPATH=. "$PY" -m modules.regress.package_audit > /tmp/pkg_audit_hook.log 2>&1; then
  echo "[pre-commit][FAIL] 包体检未通过，详情: /tmp/pkg_audit_hook.log"
  tail -8 /tmp/pkg_audit_hook.log
  FAILED=1
fi

if [ "$FAILED" = "1" ]; then
  echo ""
  echo "提交被拦。处理："
  echo "  - 修复后重新 git add && git commit"
  echo "  - 全量回归（含41s bug_regression）: PYTHONPATH=. python -m modules.regress.bug_regression"
  echo "  - 紧急绕过: SKIP_AUDIT=1 git commit ... （事后必须补跑）"
  exit 1
fi

echo "[pre-commit] 快速体检通过（全量回归请在出榜/发布前显式运行）"
exit 0
'''


def main() -> int:
    repo = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_REPO
    hooks = repo / ".git" / "hooks"
    if not hooks.is_dir():
        print(f"错误：不是 git 仓库或 hooks 目录不存在: {hooks}", file=sys.stderr)
        return 1
    target = hooks / HOOK_NAME
    target.write_text(HOOK_BODY, encoding="utf-8", newline="\n")
    # POSIX 执行位（Windows 上 os.chmod 对 .git bash 钩子同样有效）
    os.chmod(target, 0o755)
    print(f"已安装 {HOOK_NAME} 钩子 → {target}")
    print("验证：git commit 时应显示 '[pre-commit] 快速体检...'")
    print("绕过：SKIP_AUDIT=1 git commit ...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
