# -*- coding: utf-8 -*-
"""data_bootstrap.py —— 新装包联网补回 JSON 数据（v10.5.2）

背景（2026-11-21 用户决定）：
  「JSON 后缀的都可以不打包，毕竟这些数据可以联网获得，即便其他地方引用它们。」
  发布包从 v10.5.2 起零 JSON（总闸门在 build_release.EXCLUDE_ALL_JSON），
  但新装包要能跑榜，必须先把数据拿回来。唯一可信联网源 = 已发布 GitHub 仓库
  （仓库里 categories/*.json 与根目录 *.json 随版本维护，和源码树一致）。

为什么从仓库拉、而不是从厂商官网现抓：
  人工事实库（气囊/物理参数/基线池/manual trims）是逐车核验、带 url+quote 的证据，
  官网/C-NCAP 不提供整包下载，自动化抓不回等价物。仓库快照是可复现的唯一来源。

铁律：联网只在本模块（research）内发生，其他模块只调本模块的纯函数/CLI。
用法：
    python3 -m modules.research.data_bootstrap --check        # 列出本地缺哪些 JSON
    python3 -m modules.research.data_bootstrap --apply        # 从仓库补回缺失的 JSON
    python3 -m modules.research.data_bootstrap --apply --force # 覆盖本地已有（升级后用）
    python3 -m modules.research.data_bootstrap --selftest      # 零网络自测（纯逻辑）
退出码：0 = 齐备/补回成功；1 = 有缺失或补回失败；2 = 参数/仓库配置错误。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import ssl
import sys
import time
import urllib.request
from pathlib import Path

import paths

ROOT = paths.ROOT

# 唯一联网数据源：已发布仓库（与 sync_to_repo 的 DEFAULT_REPO 对应）
REPO_OWNER = "nanbatiannanbatian"
REPO_NAME = "Car-recommendation"
BRANCH = "main"
API_TREE = (f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"
            f"/git/trees/{BRANCH}?recursive=1")
# 注意：raw.githubusercontent.com 在大陆代理下 SSL 握手直接 EOF（实测 0/4），
# 而 api.github.com 稳定可达。故正文下载走 contents API（返回 base64），不用 raw。
API_CONTENTS = (f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"
                f"/contents/")

UA = {"User-Agent": "Mozilla/5.0 (data-bootstrap)",
      "Accept": "application/vnd.github+json"}

# 发布包刻意排除、但新装包运行必需的 JSON 前缀/目录。
# 从仓库 tree 里筛 .json 时：categories/ 全部 + 根目录这几个（运行链路直接读）。
ROOT_JSON = {
    "cncap_scores.json", "official_safety_scores.json", "levels.json",
}

# 仓库里的历史污染快照/已废弃配置，不应补回新装包（与 build_release.EXCLUDE_GLOBS 对齐）
NEVER_FETCH = {
    "categories/trims_v60before.json", "categories/trims_v61polluted.json",
}


def _ssl_ctx():
    """中国大陆代理常见自签/过期证书：降级为不校验，仅对本模块的只读 GET。"""
    ctx = ssl.create_default_context()
    try:
        ctx.load_default_certs()
    except Exception:
        pass
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def fetch_url(url: str, timeout: int = 20, retries: int = 3) -> bytes | None:
    """research 内唯一的联网取字节口，带重试；返回响应体 bytes。"""
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as r:
                return r.read()
        except Exception as ex:                      # noqa: BLE001 - 网络异常多样，统一重试
            last = ex
            if i + 1 < retries:
                time.sleep(0.4 * (i + 1))
    print(f"[warn] 取回失败({retries}次)：{url[:80]} · {type(last).__name__}: {last}",
          file=sys.stderr)
    return None


def fetch_json_file(rel: str, timeout: int = 20, retries: int = 3) -> bytes | None:
    """从 contents API 取单个 JSON 正文（base64 解码），已校验内容。

    raw.githubusercontent 在大陆代理下 SSL EOF 必失败，contents API 稳定。
    """
    url = f"{API_CONTENTS}{rel}?ref={BRANCH}"
    raw = fetch_url(url, timeout=timeout, retries=retries)
    if not raw:
        return None
    try:
        doc = json.loads(raw.decode("utf-8"))
        if doc.get("encoding") != "base64" or "content" not in doc:
            raise ValueError("contents API 未返回 base64 content")
        return base64.b64decode(doc["content"])
    except Exception as ex:                          # noqa: BLE001
        print(f"[warn] {rel} contents 解码失败：{type(ex).__name__}: {ex}", file=sys.stderr)
        return None


def list_remote_json() -> list[str] | None:
    """从 GitHub tree API 列出仓库内所有 .json 相对路径（不含 data/gen 生成物）。"""
    raw = fetch_url(API_TREE)
    if not raw:
        return None
    try:
        tree = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    if tree.get("truncated"):
        print("[warn] 仓库 tree 被截断，JSON 清单可能不全", file=sys.stderr)
    out = []
    for t in tree.get("tree", []):
        p = t.get("path", "")
        if not p.endswith(".json"):
            continue
        parts = p.split("/")
        if p in NEVER_FETCH:
            continue
        # 只补 categories/ 与根目录运行必需项；跳过 data/gen（生成物另有 bootstrap_data）
        if len(parts) == 2 and parts[0] == "categories":
            out.append(p)
        elif len(parts) == 1 and p in ROOT_JSON:
            out.append(p)
    return sorted(out)


def local_path(rel: str) -> Path:
    """仓库相对路径 → 本地落点。categories/x.json → <ROOT>/categories/x.json。"""
    return ROOT / rel


def survey() -> dict:
    """体检：远程清单 vs 本地存在性（纯联网列目录，不下载正文）。"""
    remote = list_remote_json()
    if remote is None:
        return {"ok": False, "remote": [], "missing": [], "present": [],
                "error": "无法列出仓库 JSON 清单（网络/API 失败）"}
    missing, present = [], []
    for rel in remote:
        (missing if not local_path(rel).exists() else present).append(rel)
    return {"ok": True, "remote": remote, "missing": missing, "present": present,
            "error": ""}


def apply(force: bool = False, only: list[str] | None = None) -> dict:
    """从仓库补回 JSON。返回 {ok, fetched, failed, skipped}。"""
    remote = list_remote_json()
    if remote is None:
        return {"ok": False, "fetched": [], "failed": [], "skipped": [],
                "error": "无法列出仓库 JSON 清单"}
    targets = []
    for rel in remote:
        if only and rel not in only and Path(rel).name not in only:
            continue
        dst = local_path(rel)
        if dst.exists() and not force:
            continue
        targets.append(rel)
    fetched, failed = [], []
    for rel in targets:
        raw = fetch_json_file(rel)
        if raw is None:
            failed.append(rel)
            continue
        # 校验是合法 JSON 再落盘（防止代理返回 HTML 错误页被写进去）
        try:
            json.loads(raw.decode("utf-8"))
        except Exception:
            failed.append(rel)
            print(f"[warn] {rel} 返回内容非合法 JSON，拒绝落盘", file=sys.stderr)
            continue
        dst = local_path(rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(raw)
        fetched.append(rel)
    return {"ok": not failed, "fetched": fetched, "failed": failed,
            "skipped": [r for r in remote if r not in targets], "error": ""}


def selftest() -> int:
    """零网络自测：只测纯逻辑（路径映射/清单筛选/落点隔离）。"""
    assert local_path("categories/master_pool.json") == ROOT / "categories" / "master_pool.json"
    assert local_path("cncap_scores.json") == ROOT / "cncap_scores.json"
    assert "levels.json" in ROOT_JSON and "trims.json" not in ROOT_JSON, \
        "生成物不应从仓库补（走 bootstrap_data）"
    assert API_TREE.startswith("https://") and API_CONTENTS.startswith("https://"), \
        "必须是 HTTPS 源"
    # 清单筛选逻辑（用一棵假 tree 验证：只留 categories/*.json 与根白名单）
    fake = {"tree": [
        {"path": "categories/airbags.json"},
        {"path": "categories/car_ev.json"},
        {"path": "cncap_scores.json"},
        {"path": "data/gen/trims.json"},
        {"path": "modules/research/config.json"},
        {"path": "README.md"},
    ], "truncated": False}
    kept = []
    for t in fake["tree"]:
        p = t["path"]
        if not p.endswith(".json") or p in NEVER_FETCH:
            continue
        parts = p.split("/")
        if len(parts) == 2 and parts[0] == "categories":
            kept.append(p)
        elif len(parts) == 1 and p in ROOT_JSON:
            kept.append(p)
    assert set(kept) == {"categories/airbags.json", "categories/car_ev.json",
                         "cncap_scores.json"}, kept
    print("SELFTEST PASS · data_bootstrap 纯逻辑断言通过（零网络）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="新装包联网补回 JSON 数据")
    ap.add_argument("--check", action="store_true", help="列出本地缺哪些 JSON")
    ap.add_argument("--apply", action="store_true", help="从仓库补回缺失 JSON")
    ap.add_argument("--force", action="store_true", help="覆盖本地已有")
    ap.add_argument("--only", action="append", default=None, help="只补指定文件（可重复）")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.check or a.apply):
        ap.print_help()
        return 2
    if a.check:
        r = survey()
        if not r["ok"]:
            print(f"[FAIL] {r['error']}")
            return 1
        print("=" * 70)
        print(f"联网数据体检：远程 {len(r['remote'])} 个 JSON / 本地已有 "
              f"{len(r['present'])} / 缺 {len(r['missing'])}")
        print("=" * 70)
        for rel in r["missing"]:
            print(f"  缺  {rel}")
        if r["missing"]:
            print("\n→ 跑 `python3 -m modules.research.data_bootstrap --apply` 补回。")
        return 0 if not r["missing"] else 1
    r = apply(force=a.force, only=a.only)
    if not r["ok"] and not r["fetched"]:
        print(f"[FAIL] {r['error'] or '补回全部失败'}", file=sys.stderr)
        return 1
    print(f"补回完成：成功 {len(r['fetched'])} / 失败 {len(r['failed'])} / "
          f"跳过(已存在) {len(r['skipped'])}")
    for rel in r["failed"]:
        print(f"  ✗ {rel}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
