# -*- coding: utf-8 -*-
"""把 skill 目录的代码改动同步到 GitHub 仓库（Car-recommendation）。

背景：skill 实际运行目录 ~/.workbuddy/skills/omni-debate 与 GitHub 发布副本
（WorkBuddy/<session>/omni-debate-github，origin=Car-recommendation.git）长期双轨。
每次改完 skill 代码必须同步到仓库再提交，否则改动只活在 skill 本地、上不了 GitHub。
本脚本只同步「代码 + 品类配置 + 文档」，绝不碰 runs/ 跑批产物和 cache/。

用法：
    python sync_to_repo.py --check              # 只报告将同步哪些文件（默认）
    python sync_to_repo.py --apply              # 执行同步（复制 + 回填 baseline）
    python sync_to_repo.py --apply --repo <路径> # 指定仓库路径

规则：
  - 同步集 = 仓库已有的 *.py / categories/*.json / 根目录 *.md（以 skill 为准）
           + 白名单新增代码（quality_score.py / sync_cell_brand.py / car_ev_1218_quality.json）
  - 内容语义相同（仅行尾符差异）的文件跳过，不制造无谓 diff
  - baseline_car_ev.json 不整文件覆盖：调 sync_cell_brand.py --apply 在仓库端回填，
    保持仓库 indent=2 格式，避免 2400 行重排
  - 绝不覆盖仓库有、skill 没有的文件（防止丢仓库独有内容）
  - 同步后需在仓库里 git add/commit/push（本脚本不碰 git）
"""
import argparse
import difflib
import json
import os
import shutil
import sys

SKILL = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REPO = r"C:\Users\海南岛南霸天\WorkBuddy\2026-11-13-15-03-08\omni-debate-github"

# skill 端有、但仓库尚未收录的新增代码文件（本次会话起）
NEW_FILES = [
    "quality_score.py",
    "sync_cell_brand.py",
    "sync_to_repo.py",  # 本脚本自身也入库
    "board_v2.py",                              # v7.0 统一榜单引擎
    "categories/car_ev_1218_quality.json",
    "categories/rank_v2_policy.json",           # v7.0 榜单策略配置
    "master_pool.py",                           # v8.0 总候选池（单一事实源）
    "categories/master_pool.json",              # v8.0 总池数据（67款，随代码同步）
    "cross_verify.py",                          # v8.2 多源交叉核验
    "categories/verified_overrides.json",       # v8.2 网查确认回填表
    "render_board_html.py",                     # v8.3.5 榜单 HTML 渲染器
    "board_view.py",                            # v9.0.0 展示区块注册表（唯一事实源）
    "categories/fact_library.json",             # v9.0.1 事实参考库（KWh/电耗/CLTC，随包）
    "experience_score.py",                      # v9.3.0 体验证据评分修正（adas/control）
    "categories/experience_facts.json",         # v9.3.0 体验证据库（网查可核验来源）
    "categories/residual_values.json",          # v9.5.0 保值率库（中汽流通协会×精真估）
    "PHEV_SCENE_GUIDE.md",                      # v9.5.1 插混按场景选车指南（方法论附注，不进评分）
    "categories/battery_platform.json",         # v9.6.1 按评分配置的电压/电池封装事实库
    "trim_board.py",                            # v9.7.0 逐配置评分（下拉对比用）
    "trim_annotate.py",                         # v9.7.3 统一人工口径逐配置排名
    "trim_coverage.py",                         # v9.7.9 出榜前逐配置覆盖校验（铁律守门）
    "facts_sync.py",                            # v9.7.28 原始库→事实库全字段映射
    "bug_regression.py",                        # v9.7.11 历史bug回归断言（17项）
    "categories/car_compact_bev_allprice.json",  # 2026-10紧凑型纯电全价位榜配置
    "categories/car_compact_bev_extended.json",  # 2026-10紧凑型纯电16款扩展榜配置
    "categories/candidates_compact_bev_extended.json",  # v9.7.30 16款扩展候选池（固化，不依赖runs）
    "categories/candidates_compact_bev_audit.json",     # v9.7.30 10款审计榜候选池（固化，不依赖runs）
    "categories/universe_compact_bev_202610.json",      # v9.7.30 紧凑纯电权威车型清单（完整性盘点）
    "categories/trim_verified.json",            # v9.7.3 六配置榜人工标注（参考）
    "categories/car_midsedan_allprice.json",    # 2026-10纯电中型轿车全价位榜配置
]


def content_lines(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return [l.rstrip("\r\n") for l in f]


def same_content(a, b):
    try:
        return content_lines(a) == content_lines(b)
    except OSError:
        return False


def collect_sync_set(repo):
    """返回 [(relpath, kind)]，kind in {copy, baseline}。"""
    items = {}
    # 1) 仓库已存在的同步候选：根目录 py/json/md + categories/*.json
    for name in os.listdir(repo):
        p = os.path.join(repo, name)
        if os.path.isfile(p) and name.endswith((".py", ".json", ".md")):
            if name in ("quality_score.py", "sync_cell_brand.py", "sync_to_repo.py"):
                continue  # 新增文件单独处理
            items[name] = "copy"
    cdir = os.path.join(repo, "categories")
    if os.path.isdir(cdir):
        for name in os.listdir(cdir):
            if name.endswith(".json"):
                rel = "categories/" + name
                if name == "baseline_car_ev.json":
                    items[rel] = "baseline"
                elif rel not in [x for x in NEW_FILES]:
                    items[rel] = "copy"
    # 2) 新增文件
    for rel in NEW_FILES:
        if os.path.isfile(os.path.join(SKILL, rel)):
            items[rel] = "baseline" if rel.endswith("baseline_car_ev.json") else "copy"
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", nargs="?", default=DEFAULT_REPO)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    repo = a.repo
    do_apply = bool(a.apply)

    if not os.path.isdir(repo):
        print("仓库不存在:", repo)
        sys.exit(2)

    sync_set = collect_sync_set(repo)
    to_copy, to_skip, to_baseline = [], [], []

    for rel, kind in sorted(sync_set.items()):
        s = os.path.join(SKILL, rel)
        r = os.path.join(repo, rel)
        if not os.path.isfile(s):
            # skill 没有该文件：不动仓库（防止丢仓库独有内容）
            to_skip.append((rel, "skill 无此文件，保留仓库版本"))
            continue
        if kind == "baseline":
            to_baseline.append(rel)
            continue
        if os.path.isfile(r) and same_content(s, r):
            to_skip.append((rel, "内容相同"))
            continue
        to_copy.append((rel, "新增" if not os.path.isfile(r) else "内容更新"))

    print(f"skill: {SKILL}")
    print(f"repo : {repo}\n")
    print(f"=== 将复制 {len(to_copy)} ===")
    for rel, why in to_copy:
        print(f"  + {rel:<46} {why}")
    print(f"\n=== baseline 走仓库端回填（保持 indent=2）{len(to_baseline)} ===")
    for rel in to_baseline:
        print(f"  ~ {rel}")
    print(f"\n=== 跳过 {len(to_skip)}（内容相同或 skill 无此文件）===")
    for rel, why in to_skip[:12]:
        print(f"  = {rel:<46} {why}")
    if len(to_skip) > 12:
        print(f"  ... 另有 {len(to_skip)-12} 个")

    if not do_apply:
        print("\n[CHECK 模式] 未做任何改动。加 --apply 执行同步。")
        return

    # --- 执行复制 ---
    for rel, _ in to_copy:
        s, r = os.path.join(SKILL, rel), os.path.join(repo, rel)
        os.makedirs(os.path.dirname(r), exist_ok=True)
        shutil.copy2(s, r)
        print(f"已复制 {rel}")

    # --- baseline 用 sync_cell_brand 在仓库端回填 ---
    sync_tool = os.path.join(SKILL, "sync_cell_brand.py")
    for rel in to_baseline:
        target = os.path.join(repo, rel)
        if not os.path.isfile(target):
            shutil.copy2(os.path.join(SKILL, rel), target)
            print(f"baseline 仓库无此文件，直接复制 {rel}")
            continue
        # 先只复制核验表（保证事实源一致），再在仓库端回填
        facts_s = os.path.join(SKILL, "categories", "cell_facts.json")
        facts_r = os.path.join(repo, "categories", "cell_facts.json")
        if os.path.isfile(facts_s) and (not os.path.isfile(facts_r) or
                                        not same_content(facts_s, facts_r)):
            shutil.copy2(facts_s, facts_r)
            print("已同步 cell_facts.json（仓库端回填的事实源）")
        import subprocess
        cp = subprocess.run([sys.executable, sync_tool, "--apply", target],
                            capture_output=True, text=True)
        print(f"baseline 回填: {cp.stdout.strip()[-300:]}")
        if cp.returncode != 0:
            print("回填失败:", cp.stderr.strip()[-500:])

    print("\n同步完成。下一步：进入仓库 git add -A && git commit && git push。")


if __name__ == "__main__":
    main()
