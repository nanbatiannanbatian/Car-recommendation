#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第三方实测/拆车证据（thirdparty.py）——把「能点开核验的视频证据」接进打分。

为什么单独做一个模块而不直接改分数：
  懂车帝对撞、UP 主拆车、B 站实测这类内容**有效但危险**：
    · 有效——双车对撞是「同条件、同批次的相对比较」，比厂商参数可信；
      拆车能看防撞梁/悬架用料，官方配置表看不到。
    · 危险——一个爆款视频就能把排名改了；UP 主口径与厂商口径混在一起；
      结论会被观众记错（例如「Model Y 撞极狐S」，实际是阿尔法 T（2022-03）和
      阿尔法 S（2021-07 懂车帝对撞汉 EV、另有《极限车试》对撞 Model Y）两次不同的测试）。

  所以这里只做三件事，全部可核验、可回滚：
    1. 证据先入库（带平台、链接、日期、口径类型），没有链接的条目直接拒绝；
    2. 打分只通过 `adjust` 加减分，且有**硬上限**——单一来源改不动大局；
    3. 每一笔加减分写进候选记录，review_report 里能看到来源，被质疑可以逐条撤。

四条硬规则（config.thirdparty_policy 同步维护）：
  R1 可核验：platform + url + date 缺一不可；url 打不开或与结论无关，整条作废。
  R2 分口径：independent_test（平台自购/双车对撞）> up_review（UP 主拆车）
      > vendor_claim（厂商宣传口径）。厂商口径权重减半且不参与「独立证据」统计。
  R3 封顶：单车全部第三方证据合计 ≤ ±1.5 分（10 分制）；厂商口径合计 ≤ ±0.6。
  R4 相对性：双车对撞只证明「同条件下 A 比 B 强」，对方不在候选池时只记不评。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DEFAULT_DB = os.path.join(HERE, "categories", "thirdparty_tests.json")

# 口径 → 上限（10 分制）。厂商口径最弱。
CAPS = {
    "independent_test": 1.0,   # 平台自购对撞/标准化实测
    "up_review": 0.5,          # UP 主拆车/解析（有实拍但无统一规程）
    "vendor_claim": 0.3,       # 厂商申报口径（扭转刚度、用料宣传）
}
VENDOR_TOTAL_CAP = 0.6
TOTAL_CAP = 1.5

KIND_DIM = {   # 证据类型 → 主要影响的维度（用于 review 展示）
    "crash_head2head": "safety",
    "crash_single": "safety",
    "stiffness": "safety",
    "teardown_material": "powertrain",
    "teardown_chassis": "control",
    "moose_test": "control",
    "brake_test": "control",
    "range_test": "powertrain",
    "charge_test": "powertrain",
}


def load_db(path: str = DEFAULT_DB) -> dict:
    if not os.path.exists(path):
        return {"entries": []}
    return json.load(open(path, encoding="utf-8"))


def validate(db: dict) -> list[str]:
    errs = []
    for i, e in enumerate(db.get("entries", [])):
        where = f"entries[{i}]"
        for k in ("model", "kind", "platform", "url", "date", "claim",
                  "source_type", "direction"):
            if not e.get(k):
                errs.append(f"{where}: 缺 {k}（R1 可核验要求必填）")
        if e.get("source_type") not in CAPS:
            errs.append(f"{where}: source_type 必须是 {list(CAPS)}")
        if e.get("direction") not in ("+", "-", "0"):
            errs.append(f"{where}: direction 必须是 + / - / 0")
        if e.get("kind") not in KIND_DIM:
            errs.append(f"{where}: kind 必须是 {sorted(KIND_DIM)}")
        d = str(e.get("date") or "")
        try:
            dt.date.fromisoformat(d[:10])
        except Exception:
            errs.append(f"{where}: date 不是 ISO 日期")
    return errs


def apply(cands: list[dict], db: dict | None = None,
          cfg: dict | None = None) -> tuple[list[dict], list[str]]:
    """把第三方证据折算成 adjust 加减分（带封顶），返回 (候选, 审计行)。

    adjust 是已有机制（人工微调入口），这里复用它而不是开新通道，
    保证 score_rows / warehouse / 报告 全链路行为一致。
    """
    if db is None:
        db = load_db()
    errs = validate(db)
    if errs:
        raise SystemExit("第三方证据库未通过校验：\n  " + "\n  ".join(errs))

    by_name = {}
    for c in cands:
        by_name[c.get("model")] = c

    audit = []
    for e in db.get("entries", []):
        c = by_name.get(e["model"])
        if not c:
            continue                      # R4：车不在池内，只入库不评分
        w = CAPS[e["source_type"]]
        delta = w * (1 if e["direction"] == "+" else
                     -1 if e["direction"] == "-" else 0)
        if delta == 0:
            continue
        rec = {"platform": e["platform"], "url": e["url"], "date": e["date"],
               "kind": e["kind"], "dim": KIND_DIM[e["kind"]],
               "claim": e["claim"][:80], "delta": round(delta, 2),
               "source_type": e["source_type"]}
        c.setdefault("_tp", []).append(rec)
        audit.append({**rec, "model": e["model"]})

    # 封顶：单车合计 ±1.5；其中厂商口径合计 ±0.6
    for c in cands:
        recs = c.get("_tp") or []
        if not recs:
            continue
        tot = sum(r["delta"] for r in recs)
        vend = sum(r["delta"] for r in recs if r["source_type"] == "vendor_claim")
        capped = tot
        vend_cap = VENDOR_TOTAL_CAP if vend > 0 else -VENDOR_TOTAL_CAP
        if abs(vend) > VENDOR_TOTAL_CAP:
            capped -= vend - vend_cap
        if abs(capped) > TOTAL_CAP:
            capped = TOTAL_CAP if capped > 0 else -TOTAL_CAP
        applied = round(capped, 2)
        c["adjust"] = round(float(c.get("adjust") or 0) + applied, 2)
        c["_tp_applied"] = applied
        c.setdefault("note", "")
        tags = "；".join(f"{r['platform']}·{r['dim']} {r['delta']:+.2f}"
                         for r in recs)
        c["note"] = (c["note"] + f"｜第三方证据：{tags}（合计 {applied:+.2f}）").strip("｜")
        audit.append({"model": c.get("model"), "kind": "APPLY",
                      "delta": applied, "claim": f"共 {len(recs)} 条",
                      "source_type": "cap"})
    return cands, audit


def audit_report(audit: list[dict]) -> str:
    if not audit:
        return "第三方证据：无在池条目。"
    L = ["### 第三方证据明细（thirdparty.py）", "",
         "| 车型 | 口径 | 类型 | 维度 | 加减分 | 依据 | 来源 |",
         "|---|---|---|---|---|---|---|"]
    for a in audit:
        if a.get("kind") == "APPLY":
            L.append(f"| **{a['model']}** | 封顶后 | — | — | **{a['delta']:+.2f}** | "
                     f"{a['claim']} | — |")
        else:
            L.append(f"| {a['model']} | {a['source_type']} | {a['kind']} | "
                     f"{a['dim']} | {a['delta']:+.2f} | {a['claim']} | "
                     f"[{a['platform']} {a['date']}]({a['url']}) |")
    L.append("")
    L.append(f"> 口径权重：独立实测 ±{CAPS['independent_test']}、UP 主拆车 "
             f"±{CAPS['up_review']}、厂商口径 ±{CAPS['vendor_claim']}；"
             f"单车合计封顶 ±{TOTAL_CAP}，其中厂商口径封顶 ±{VENDOR_TOTAL_CAP}。"
             "对方不在候选池的对撞只入库不评分。")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()
    db = load_db(a.db)
    errs = validate(db)
    for e in db.get("entries", []):
        print(f"{e.get('date')} | {e.get('model')} | {e.get('kind')} | "
              f"{e.get('source_type')} | {e.get('direction')} | "
              f"{e.get('platform')} | {str(e.get('claim'))[:50]}")
    print(f"\n{len(db.get('entries', []))} 条；"
          + ("校验通过" if not errs else "校验失败：\n  " + "\n  ".join(errs)))
    if errs:
        sys.exit(1)


if __name__ == "__main__":
    main()
