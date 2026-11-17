#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""口碑样本合并（koubei_merge.py）——把第二来源（懂车帝/vehicle-data-hub）的口碑样本数并入 ICE 池。

为什么：
    ice_scoring 在 koubei_users < MIN_USERS(30) 时，该维度不做结论 → 同榜中位插补并标 low，
    拉高插补率。汽车之家对很多热门车的口碑样本数偏低，但懂车帝同款车的口碑样本通常多得多。
    并入懂车帝样本数后，这些车越过 30 阈值，原本被插补的 汽车之家维度分即可采信，插补率下降。

数据来源（两种都支持）：
    1. 本项目种子 categories/koubei_seed.json
       —— 格式 {"entries":[{"model","users","source_type","note"}]}，users 为种子估计值。
    2. vehicle-data-hub 的 benchmark 输出
       —— 目录里 <dcd_series_id>.json（懂车帝口碑缓存），按 dcd_series_id 匹配池子。

纪律（重要）：
    - 仅并入「样本数」，维度分（0–5）仍用汽车之家 koubei_dims，不混源。
    - 懂车帝样本数 <= 汽车之家现有数时不覆盖（不降级）。
    - 种子估计值必须明确标注 source_type，真实全量请跑 vehicle-data-hub（见 report）。

用法：
    python3 koubei_merge.py --pool ice_pool_full.json \
        --seed categories/koubei_seed.json --out ice_pool_full.json --report
    # 或接 vehicle-data-hub 真实产出：
    python3 koubei_merge.py --pool ice_pool_full.json \
        --vdh-dir /path/to/vehicle-data-hub/downloads/benchmark --out ice_pool_full.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def load_seed(path: str) -> dict:
    d = json.load(open(path, encoding="utf-8"))
    return {e["model"]: e for e in d.get("entries", [])}


def load_vdh_benchmark(dir_: str) -> dict:
    """vehicle-data-hub 的 downloads/benchmark/<dcd_series_id>.json → {dcd_series_id: users}。"""
    out = {}
    if not os.path.isdir(dir_):
        return out
    for fn in os.listdir(dir_):
        if not fn.endswith(".json"):
            continue
        sid = fn[:-5]
        try:
            j = json.load(open(os.path.join(dir_, fn), encoding="utf-8"))
        except Exception:
            continue
        # vehicle-data-hub 口碑缓存结构不定，尽量兼容常见字段
        users = (j.get("review_count") or j.get("koubei_users")
                 or j.get("users") or (j.get("data") or {}).get("reviewCount"))
        if users:
            out[sid] = {"users": int(users), "source_type": "vehicle-data-hub benchmark"}
    return out


def merge(pool: list[dict], by_model: dict, by_dcd: dict | None = None,
          label: str = "懂车帝/vehicle-data-hub") -> dict:
    """就地并入口碑样本数。返回统计。"""
    by_dcd = by_dcd or {}
    lifted = 0
    seen_models = set()
    for c in pool:
        e = by_model.get(c.get("model"))
        su = e.get("users") if e else None
        src = e.get("source_type") if e else None
        if su is None and by_dcd:
            d = by_dcd.get(c.get("dcd_series_id"))
            if d:
                su = d["users"]; src = d.get("source_type")
        if su is None:
            continue
        cur = c.get("koubei_users") or 0
        if su <= cur:
            continue  # 不降级
        c["koubei_users"] = int(su)
        c["koubei_source"] = src or label
        c["_koubei_merged"] = True
        lifted += 1
        seen_models.add(c["model"])
    n = len(pool)
    after = sum(1 for c in pool if (c.get("koubei_users") or 0) >= 30)
    return {"pool": n, "lifted": lifted, "after_ge30": after,
            "lifted_models": sorted(seen_models)}


def coverage(pool: list[dict]) -> dict:
    n = len(pool)
    ge30 = sum(1 for c in pool if (c.get("koubei_users") or 0) >= 30)
    return {"n": n, "ge30": ge30, "pct": round(100.0 * ge30 / n, 1) if n else 0.0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True)
    ap.add_argument("--seed", help="categories/koubei_seed.json（种子估计）")
    ap.add_argument("--vdh-dir", help="vehicle-data-hub benchmark 目录（真实产出）")
    ap.add_argument("--out", required=True, help="写回的池文件（可用 --pool 同名覆盖）")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()

    pool = json.load(open(a.pool, encoding="utf-8")).get("pool") \
        if "pool" in open(a.pool, encoding="utf-8").read(200) else \
        json.load(open(a.pool, encoding="utf-8"))
    # 兼容两种顶层结构
    raw = json.load(open(a.pool, encoding="utf-8"))
    pool = raw["pool"] if "pool" in raw else raw

    before = coverage(pool)
    by_model, by_dcd = {}, None
    if a.seed:
        by_model = load_seed(a.seed)
        print(f"[seed] {len(by_model)} 条种子（{a.seed}）")
    if a.vdh_dir:
        by_dcd = load_vdh_benchmark(a.vdh_dir)
        print(f"[vdh] benchmark 目录命中 {len(by_dcd)} 个 dcd_series_id")

    stat = merge(pool, by_model, by_dcd)
    after = coverage(pool)

    # 写回
    raw["koubei_merge"] = {
        "seed": a.seed, "vdh_dir": a.vdh_dir,
        "lifted": stat["lifted"], "after_ge30": stat["after_ge30"],
    }
    json.dump(raw, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[ok] {a.out}")
    print(f"[覆盖] koubei_users>=30：{before['ge30']}/{before['n']} ({before['pct']}%) "
          f"→ {after['ge30']}/{after['n']} ({after['pct']}%)")
    print(f"[提升] 本轮并入 {stat['lifted']} 款（超过汽车之家现有样本数）")
    if a.report and stat["lifted_models"]:
        print("[明细] " + "、".join(stat["lifted_models"]))


if __name__ == "__main__":
    main()
