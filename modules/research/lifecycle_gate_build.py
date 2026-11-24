#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lifecycle_gate_build.py —— 生命周期门禁数据自动生成器（v10.69 根因修复）

为什么有这个（2026-11-24 用户："更新 lifecycle_status.json 写成代码，
单独更新这个以后其他车还是会有问题"）：
  此前 lifecycle_status.json 靠手工往里加车（唐L停产、唐EV上市都是我手写的），
  下次别的车退市照样没人写、照样漏。而销量变点检测 lifecycle_detect.py
  早就存在（唐L 销量 87→5→1→3→3 会判 collapse?），lifecycle_fetch.py 也能
  分层合并——但两者都没接进出榜链路。

本脚本把"销量症状检测"变成门禁数据：从候选池的逐月销量自动算每款车的
生命周期症状（collapse?/cliff?/clearance?/ramp_up/stable），写入候选池
`lifecycle_reviewed` 字段；出榜 pipeline 检查"疑似异常但未核验"并硬拦。

设计纪律：
  - 只写"症状+需核验"，不自动判停产（把清库当停产会误杀好车——detect 模块原话）；
  - 人工核验结论写进 status 字段（confirmed_discontinued / normal / relaunched，带 url+note）；
  - 纯 stable/ramp_up 不写库（避免冗余）；
  - 确定性：同一销量序列同一输出，零网络。

用法：
  python3 modules/research/lifecycle_gate_build.py --candidates runs/x/pool.json [--apply]
  python3 modules/research/lifecycle_gate_build.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
import paths  # noqa: E402
sys.path.insert(0, str(paths.MODULES / "research"))
import lifecycle_detect as LD  # noqa: E402

# 需要人工核验的症状（不会自动判死，只要求给个说法）
SUSPECT = ("collapse?", "cliff?", "clearance?")


def build_reviewed(cands: list[dict], prev: dict | None = None) -> dict:
    """从候选池逐月销量生成 lifecycle_reviewed 门禁数据。

    prev：已有的 lifecycle_reviewed（保留人工核验结论，不覆盖）。
    """
    prev = prev or {}
    old = {r.get("model"): r for r in (prev.get("reviewed") or [])}
    reviewed = []
    for c in cands:
        model = c.get("model")
        keys, vals = LD.read_series(c)
        if not vals:
            continue
        st, reasons, conf = LD.classify(vals)
        if st not in SUSPECT and model not in old:
            continue  # 正常车不写库
        prior = old.get(model) or {}
        reviewed.append({
            "model": model,
            "detected": st,
            "confidence": conf,
            "values": vals,
            "reasons": reasons,
            # 人工核验结论：空=未核验（出榜硬拦）；已核验的保留
            "status": prior.get("status") or "",
            "note": prior.get("note") or "",
            "url": prior.get("url") or "",
        })
    return {
        "generated_by": "lifecycle_gate_build.py（销量变点自动检测，非手工维护）",
        "as_of": prev.get("as_of") or "auto",
        "suspect_states": list(SUSPECT),
        "reviewed": reviewed,
        "note": "疑似停产/清库车必须逐车核验并写 status（confirmed_discontinued/normal/relaunched，带url+note），否则出榜门禁硬拦。",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="生命周期门禁数据自动生成器")
    ap.add_argument("--candidates", required=False, help="候选池json(selftest时不需要)")
    ap.add_argument("--apply", action="store_true", help="写回候选池")
    ap.add_argument("--out", help="另存 JSON（不写候选池）")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        cands = [
            {"model": "停产症状", "trend_months": {"202606": 90, "202607": 5,
                                                         "202608": 1, "202609": 2}},
            {"model": "正常", "trend_months": {"202606": 100, "202607": 105,
                                                    "202608": 98, "202609": 102}},
        ]
        r = build_reviewed(cands)
        assert any(x["model"] == "停产症状" for x in r["reviewed"]), "未检出停产症状"
        assert not any(x["model"] == "正常" for x in r["reviewed"]), "正常车误报"
        print("[selftest OK] 停产症状检出 / 正常车不报 / 确定性")
        return 0

    if not a.candidates:
        ap.error("需要 --candidates（或 --selftest）")
    d = json.load(open(a.candidates, encoding="utf-8"))
    cands = d.get("candidates", d)
    r = build_reviewed(cands, d.get("lifecycle_reviewed"))
    pending = [x for x in r["reviewed"] if not x["status"]]
    print(f"[生命周期门禁] 检测 {len(r['reviewed'])} 款疑似异常，未核验 {len(pending)} 款")
    for x in pending[:12]:
        print(f"  {x['model']}: {x['detected']} {x['values']} — {x['reasons'][0] if x['reasons'] else ''}")
    if a.out:
        json.dump(r, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"[ok] {a.out}")
    if a.apply:
        d["lifecycle_reviewed"] = r
        json.dump(d, open(a.candidates, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"[ok] 写回 {a.candidates}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
