# -*- coding: utf-8 -*-
"""bootstrap_data.py —— 生成物一键重建（新装包首跑用）

背景（2026-11-21 用户定的规则）：
  「只要后续可以自动生成就不用打包，这些数据属于额外部分，应该让用户随着代码逐渐生成，
    而且有些数据会随着版本号提高失效。」
  → 所以 data/gen/ 的 6 个生成物不进发布包，但**必须有一条命令能把它们建回来**，
    而且缺哪个、要不要联网、跑哪条命令都要能一眼看到，不能靠用户猜。

判据（写在 paths.py，单一事实源）：
  GENERATED_JSON = 能用代码重建的 → 不打包，本脚本负责建
  CURATED_JSON   = 人工采集核验的（气囊/物理参数/基线池）→ 必须随包，本脚本只校验在不在

用法：
    python3 bootstrap_data.py --check                 # 体检：缺什么、怎么建、是否联网、指纹是否过期
    python3 bootstrap_data.py --apply                 # 只建缺失的（全自动项）
    python3 bootstrap_data.py --apply --include-semi  # 连「需前置输入」的半自动项也试
    python3 bootstrap_data.py --apply --only trims.json --force
    python3 bootstrap_data.py --apply --all --force   # 全部重算（版本升级后常用）

退出码：0 = 数据齐备可用；1 = 有生成物缺失或重建失败（报告里写明哪一条）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

import paths

HERE = str(paths.ROOT)


def _fingerprint_of(path) -> str:
    """读取文件里记录的指纹（有则返回，没有返回空串）。"""
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:
        return ""
    meta = doc.get("_generated_by") if isinstance(doc, dict) else None
    return str((meta or {}).get("fingerprint") or "")


def _current_fingerprint() -> str:
    try:
        from modules.engine import score_fingerprint as SF
        return SF.compute_fingerprint()
    except Exception:
        return ""


def survey() -> list[dict]:
    rows = []
    for name in sorted(paths.GENERATED_JSON):
        p = paths.GEN_DIR / name
        spec = paths.spec(name)
        fp = _fingerprint_of(p) if p.exists() else ""
        rows.append({
            "name": name,
            "path": p,
            "exists": p.exists(),
            "size_kb": round(p.stat().st_size / 1024, 1) if p.exists() else 0.0,
            "spec": spec,
            "fingerprint": fp,
            "stale": bool(fp and _current_fingerprint() and fp != _current_fingerprint()),
        })
    return rows


def survey_stamped_pools() -> list[dict]:
    """随包的候选池快照带打分指纹；版本/口径一变就该重算，否则出榜会被指纹门禁拦下。

    用户 2026-11-21 的判断："有些数据会随着版本号提高失效的吧" —— 是的，
    指纹 = 打分代码哈希 + 配置哈希 + 版本号。这里把「已失效的快照」显式列出来，
    免得用户跑到 pipeline 才被拦、还看不懂为什么。
    """
    cur = _current_fingerprint()
    out = []
    if not cur:
        return out
    for d in (paths.CAT_DIR, paths.GEN_DIR):
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            fp = _fingerprint_of(f)
            if fp and fp != cur:
                out.append({"name": f.name, "path": f, "fp": fp})
    return out


def print_check(rows: list[dict]) -> int:
    missing = [r for r in rows if not r["exists"]]
    stale = [r for r in rows if r["exists"] and r["stale"]]
    print("=" * 78)
    print(f"生成物体检（data/gen/）  共 {len(rows)} 项：在 {len(rows) - len(missing)} / 缺 {len(missing)}")
    print("=" * 78)
    for r in rows:
        flag = "缺失" if not r["exists"] else ("过期" if r["stale"] else "OK  ")
        net = "联网" if r["spec"].get("needs_net") else "离线"
        semi = "·需前置" if r["spec"].get("semi") else ""
        size = f"{r['size_kb']}KB" if r["exists"] else "—"
        print(f"\n[{flag}] {r['name']}  ({size}, {net}{semi})")
        if r["spec"].get("cmd"):
            print(f"        重建：{r['spec']['cmd']}")
        if r["spec"].get("note"):
            print(f"        说明：{r['spec']['note']}")
    print()
    print("人工事实库（随包发布，不重建）：")
    for name in sorted(paths.CURATED_JSON):
        p = paths.CAT_DIR / name
        print(f"  {'OK  ' if p.exists() else '缺失'}  {name}"
              f"  ({round(p.stat().st_size/1024, 1)}KB)" if p.exists() else f"  缺失  {name}")
    pools = survey_stamped_pools()
    if pools:
        print(f"\n候选池快照指纹已过期（{len(pools)} 个）："
              + "、".join(p["name"] for p in pools))
        print("  含义：这些池子是按旧版打分代码/配置算的，直接出榜会被指纹门禁拦。")
        print("  处理：重跑池构建（推荐，数据本来就是随版本生成），或对确认口径未变的老池"
              "`python3 score_fingerprint.py --stamp <池.json>` 重新打标。")
    if missing:
        print(f"\n→ 缺失 {len(missing)} 项，跑 `python3 bootstrap_data.py --apply` 重建。")
    if stale:
        print("\n→ 生成物指纹过期：" + "、".join(r["name"] for r in stale))
    return 0 if not missing else 1


def run_one(row: dict) -> tuple[bool, str]:
    spec = row["spec"]
    cmd = spec.get("cmd", "")
    if not cmd:
        return False, "没有重建命令"
    print(f"\n[run] {row['name']}: {cmd}")
    t0 = time.time()
    p = subprocess.run(cmd, shell=True, cwd=HERE, capture_output=True, text=True)
    out = (p.stdout or "").strip().splitlines()
    err = (p.stderr or "").strip().splitlines()
    tail = " | ".join((out or err)[-3:])[:300]
    cost = time.time() - t0
    ok = p.returncode == 0 and row["path"].exists()
    print(f"      {'ok' if ok else 'FAIL'} ({cost:.1f}s) {tail}")
    return ok, tail


def main() -> int:
    ap = argparse.ArgumentParser(description="生成物一键重建")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--all", action="store_true", help="已存在的也重算")
    ap.add_argument("--force", action="store_true", help="覆盖已有（配 --only 用）")
    ap.add_argument("--only", action="append", default=None, help="只处理指定文件（可重复）")
    ap.add_argument("--include-semi", action="store_true", help="连需前置输入的半自动项也跑")
    a = ap.parse_args()

    rows = survey()
    if a.only:
        unknown = [n for n in a.only if n not in paths.GENERATED_JSON]
        if unknown:
            print(f"[ERROR] 不在生成物名单：{unknown}；可选：{sorted(paths.GENERATED_JSON)}")
            return 2
        rows = [r for r in rows if r["name"] in a.only]

    if not a.apply:
        return print_check(rows)

    todo = []
    for r in rows:
        if r["exists"] and not (a.all or a.force):
            continue
        if r["spec"].get("semi") and not a.include_semi:
            print(f"[skip] {r['name']}：需前置输入（{r['spec'].get('note', '')}）")
            continue
        todo.append(r)
    if not todo:
        print("没有需要重建的生成物（--all / --force 可强制重算）。")
        return 0

    print(f"待重建 {len(todo)} 项：" + "、".join(r["name"] for r in todo))
    results = [(r["name"], *run_one(r)) for r in todo]

    failed = [n for n, ok, _ in results if not ok]
    print("\n" + "=" * 78)
    print(f"完成：成功 {len(results) - len(failed)} / 失败 {len(failed)}")
    for n in failed:
        spec = paths.spec(n)
        print(f"  ✗ {n}  → 看上面的输出；命令：{spec.get('cmd', '')}")
        if spec.get("semi"):
            print(f"    这是半自动项：{spec.get('note', '')}")
    print("=" * 78)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
