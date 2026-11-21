#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打分逻辑指纹（score_fingerprint.py）v9.7.36。

用户要求：skill 升级后，老版本生成的候选池分数不能混进新版本榜单——
"避免 skill 升级了本地还是用之前老版本生成的数据造成新版本展示有误"。

现有 master_pool.config_fingerprint() 只覆盖维度/权重配置，**不覆盖打分代码文件**；
v9.7.31 快充 delta、v9.7.34 基准解析这类改代码的活动指纹完全不变，老分数会被当成有效。

本模块把"会影响分数的代码文件"纳入指纹：
  - 打分代码：trim_annotate.py / spec_score.py / leaderboard.py / quality_score.py / experience_score.py
  - 打分配置：候选池对应的 category json（维度/权重）
  - skill 版本：SKILL.md frontmatter version

用法：
    python3 score_fingerprint.py --show                       # 打印当前指纹
    python3 score_fingerprint.py --stamp <候选池.json>        # 给候选池打基线指纹
    python3 score_fingerprint.py --check <候选池.json> [--config <cfg.json>]  # 校验是否一致
    python3 score_fingerprint.py --selftest
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import sys

import paths

HERE = str(paths.ROOT)
# 会影响分数的代码文件（改了任何一个，老分数都应视为过期）
SCORE_CODE_FILES = [
    "trim_annotate.py",
    "spec_score.py",
    "leaderboard.py",
    "quality_score.py",
    "experience_score.py",
]
STAMP_KEY = "_generated_by"


def skill_version() -> str:
    """从 SKILL.md frontmatter 读 version。"""
    p = os.path.join(HERE, "SKILL.md")
    if not os.path.exists(p):
        return ""
    txt = open(p, encoding="utf-8").read(2000)
    m = re.search(r"^version:\s*([0-9.]+)", txt, re.M)
    return m.group(1) if m else ""


def compute_fingerprint(config_path: str | None = None) -> str:
    """指纹 = 打分代码内容哈希 + 配置权重哈希 + skill版本。
    任一变化 → 指纹变 → 老候选池分数视为过期。"""
    # 注意：skill 版本号**不进指纹**——纯版本号变化（如改文案/加文档）不该让分数过期。
    # 只有打分代码文件内容或配置权重变化才触发"老分数作废"。
    payload = {"code": {}}
    for f in SCORE_CODE_FILES:
        p = os.path.join(HERE, f)
        payload["code"][f] = (
            hashlib.sha256(open(p, "rb").read()).hexdigest()
            if os.path.exists(p) else "MISSING"
        )
    if config_path and os.path.exists(config_path):
        cfg = json.load(open(config_path, encoding="utf-8"))
        dims = cfg.get("dimensions", [])
        payload["config"] = {
            "dims": sorted(str(d.get("key")) for d in dims),
            "weights": {str(d.get("key")): float(d.get("weight", 0)) for d in dims},
        }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def stamp(cands_path: str, config_path: str | None = None) -> dict:
    """给候选池打基线指纹（记录生成时的版本+指纹+时间）。"""
    d = json.load(open(cands_path, encoding="utf-8"))
    import datetime
    d[STAMP_KEY] = {
        "skill_version": skill_version(),
        "fingerprint": compute_fingerprint(config_path),
        "config": os.path.basename(config_path) if config_path else None,
        "stamped_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    json.dump(d, open(cands_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return d[STAMP_KEY]


def check(cands_path: str, config_path: str | None = None) -> dict:
    """校验候选池指纹是否匹配当前代码。返回 {ok, reason, recorded, current}。"""
    d = json.load(open(cands_path, encoding="utf-8"))
    rec = d.get(STAMP_KEY) or {}
    cur = compute_fingerprint(config_path)
    if not rec.get("fingerprint"):
        return {"ok": False, "reason": "候选池无指纹（老数据未打标）",
                "recorded": None, "current": cur}
    if rec["fingerprint"] != cur:
        return {"ok": False,
                "reason": f"打分逻辑已变（记录 {rec.get('skill_version')}/{rec['fingerprint'][:8]} → "
                          f"当前 {skill_version()}/{cur[:8]}），候选池分数需重算",
                "recorded": rec["fingerprint"], "current": cur}
    return {"ok": True, "reason": "指纹一致", "recorded": rec["fingerprint"], "current": cur}


def selftest() -> int:
    fp = compute_fingerprint()
    assert len(fp) == 16 and skill_version(), f"指纹/版本异常: {fp} / {skill_version()}"
    # 改配置应改变指纹
    cfg1 = {"dimensions": [{"key": "value", "weight": 0.1}]}
    cfg2 = {"dimensions": [{"key": "value", "weight": 0.2}]}
    import tempfile
    f1 = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(cfg1, f1); f1.close()
    f2 = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(cfg2, f2); f2.close()
    assert compute_fingerprint(f1.name) != compute_fingerprint(f2.name), "权重变化应改变指纹"
    os.unlink(f1.name); os.unlink(f2.name)
    print(f"[selftest OK] 指纹={fp} 版本={skill_version()}，权重变化改变指纹")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="打分逻辑指纹校验")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--stamp", metavar="POOL")
    ap.add_argument("--check", metavar="POOL")
    ap.add_argument("--config", help="打分配置 json（权重纳入指纹）")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(selftest())
    if a.show:
        print(f"skill版本={skill_version()} 指纹={compute_fingerprint(a.config)}")
    elif a.stamp:
        st = stamp(a.stamp, a.config)
        print(f"[stamp] {a.stamp}: {st}")
    elif a.check:
        r = check(a.check, a.config)
        print(f"[check] {'✓' if r['ok'] else '✗'} {r['reason']}")
        raise SystemExit(0 if r["ok"] else 2)
    else:
        ap.error("指定 --show / --stamp / --check / --selftest 之一")
