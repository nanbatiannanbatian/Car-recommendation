# -*- coding: utf-8 -*-
"""把 skill 目录（源码树）镜像同步到 GitHub 发布仓库（Car-recommendation）。

背景：skill 运行目录 ~/.workbuddy/skills/omni-debate 与 GitHub 副本
（WorkBuddy/<session>/omni-debate-github，origin=Car-recommendation.git）长期双轨。
每次改完代码必须同步到仓库再提交，否则改动只活在本地、上不了 GitHub。

v10.0 起改成「镜像同步」而不是白名单：
  旧版靠 NEW_FILES 手工白名单 + 「仓库已有 *.py 才同步」。模块化之后文件全挪到
  modules/、还多了 paths.py，白名单必然漏（漏一个就上不了 GitHub，而且静默失败）。
  现在同步集 = 源码树本身（排除产物与本地文件），规则只有一条：
  仓库里应有什么 = 源码目录里除 runs/ data/ dist/ 缓存/ 备份/ key.txt 之外的一切。

用法：
    python sync_to_repo.py --check              # 只报告差异（默认）
    python sync_to_repo.py --apply              # 执行同步（复制新增/改动）
    python sync_to_repo.py --apply --prune      # 顺带删除仓库里源码已不存在的文件
    python sync_to_repo.py --apply --repo <路径>

边界：
  - 绝不复制 runs/（400MB+ 产物）、data/（生成物）、dist/（zip）、.workbuddy/、__pycache__
  - 绝不复制 key.txt、*.bak / *.bak-* / *.tmp / *.pyc / *.orig
  - v10.6.0 起绝不复制任何 .json（用户：GitHub 不传 JSON）。数据与代码分离：
    仓库只存代码/文档，JSON 事实库不入 git；新装包需联网重建或从数据分发渠道获取。
  - 默认不删仓库文件（--prune 才删），但会列出「源码已无、仓库还在」的候选
  - 本脚本不碰 git，同步后自行 git add/commit/push
"""
from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import sys

SKILL = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REPO = r"C:\Users\海南岛南霸天\WorkBuddy\2026-11-13-15-03-08\omni-debate-github"

SKIP_DIRS = {"runs", "data", "dist", ".git", "__pycache__", ".workbuddy", "cache",
             ".pytest_cache", ".mypy_cache", "node_modules"}
SKIP_FILES = {"key.txt", "config.json.bak-20261113", "iv_archive_tmp.html"}
SKIP_SUFFIX = (".pyc", ".pyo", ".tmp", ".orig", ".log", ".json")
SKIP_MARKERS = (".bak", ".bak-", "~$")


# 2026-11-22：顶层才整棵排除。原先按「任意层级目录名匹配」，于是新增的
# modules/data/（事实源读取层）被当成顶层 data/ 生成物目录整棵跳过——
# 同步**静默漏文件**，代码上不了 GitHub 而不报错。
# 顶层 data/ 是生成物（不传 JSON），但 modules/data/ 是**源码**，必须同步。
TOPLEVEL_ONLY = {"runs", "data", "dist", ".git", ".workbuddy", "cache",
                 ".pytest_cache", ".mypy_cache", "node_modules"}


def should_skip(rel: str) -> bool:
    parts = rel.split(os.sep)
    # 只有**顶层**目录名才整棵排除；嵌套层只排除缓存类
    if parts[0] in TOPLEVEL_ONLY:
        return True
    if any(p in (SKIP_DIRS - TOPLEVEL_ONLY) for p in parts[:-1]):
        return True
    name = parts[-1]
    if name in SKIP_FILES:
        return True
    if name.endswith(SKIP_SUFFIX):
        return True
    return any(m in name for m in SKIP_MARKERS)


def collect(root: str) -> dict[str, str]:
    """返回 {相对路径: 绝对路径}。"""
    out: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        # 顶层才按 SKIP_DIRS 剪枝；嵌套层只剪缓存类目录（modules/data/ 要留）
        if os.path.relpath(dirpath, root) == ".":
            dirnames[:] = [d for d in dirnames if d not in TOPLEVEL_ONLY]
        else:
            dirnames[:] = [d for d in dirnames
                           if d not in (SKIP_DIRS - TOPLEVEL_ONLY)]
        for fn in filenames:
            abs_p = os.path.join(dirpath, fn)
            rel = os.path.relpath(abs_p, root)
            if should_skip(rel):
                continue
            out[rel] = abs_p
    return out


def same(a: str, b: str) -> bool:
    try:
        return filecmp.cmp(a, b, shallow=False)
    except OSError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--prune", action="store_true", help="删除仓库里源码已不存在的文件")
    ap.add_argument("--repo", default=DEFAULT_REPO)
    a = ap.parse_args()
    if not os.path.isdir(a.repo):
        print(f"[ERROR] 仓库路径不存在：{a.repo}")
        return 2

    src = collect(SKILL)
    dst = collect(a.repo)

    new = sorted(set(src) - set(dst))
    common = sorted(set(src) & set(dst))
    changed = [r for r in common if not same(src[r], os.path.join(a.repo, r))]
    stale = sorted(set(dst) - set(src))

    print(f"源码 {len(src)} 个文件 / 仓库 {len(dst)} 个")
    print(f"新增 {len(new)} / 改动 {len(changed)} / 仓库多出 {len(stale)}")
    for r in new[:20]:
        print(f"  + {r}")
    if len(new) > 20:
        print(f"  ... 另有 {len(new) - 20} 个新增")
    for r in changed[:20]:
        print(f"  ~ {r}")
    if len(changed) > 20:
        print(f"  ... 另有 {len(changed) - 20} 个改动")
    for r in stale[:20]:
        print(f"  - {r}（源码已无）")
    if len(stale) > 20:
        print(f"  ... 另有 {len(stale) - 20} 个")

    if not a.apply:
        print("\n[check] 未写入。加 --apply 执行同步。")
        return 0

    for r in new + changed:
        target = os.path.join(a.repo, r)
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        shutil.copy2(src[r], target)
    print(f"[apply] 已复制 {len(new) + len(changed)} 个文件")

    if a.prune and stale:
        # 只删「确认已搬家/已废弃」的文件，绝不删仓库独有文档（ARCHITECTURE.md / LICENSE / .gitignore）
        from paths import GENERATED_JSON, MODULES, src as _src
        pruned, kept = [], []
        for r in stale:
            base = os.path.basename(r)
            stem, ext = os.path.splitext(base)
            # 根目录的 .py/.js 若在 modules/ 里能按同名找到，就是「搬家留下的旧副本」
            moved = (ext in (".py", ".js") and os.sep not in r
                     and (_src(base).exists()
                          or any(MODULES.glob(f"*/{base}"))))
            generated = r.replace(os.sep, "/").startswith("categories/") and base in GENERATED_JSON
            # modules/ 整棵树归源码所有：源码里没有就是删过/挪走了（如 model_names 迁到 contracts）
            in_modules = r.replace(os.sep, "/").startswith("modules/")
            if moved or generated or in_modules:
                os.remove(os.path.join(a.repo, r))
                pruned.append(r)
            else:
                kept.append(r)
        print(f"[prune] 已删除 {len(pruned)} 个源码已无的文件（搬家/生成物）")
        if kept:
            print("[prune] 保留仓库独有文件（未删除）：" + "、".join(kept[:10])
                  + ("…" if len(kept) > 10 else ""))

    # 清掉 prune 后留下的空目录
    for dirpath, dirnames, filenames in os.walk(a.repo, topdown=False):
        if dirpath == a.repo or ".git" in dirpath.split(os.sep):
            continue
        if not os.listdir(dirpath):
            os.rmdir(dirpath)

    print("\n下一步：进仓库 git add -A && git commit && git push")
    return 0


if __name__ == "__main__":
    sys.exit(main())
