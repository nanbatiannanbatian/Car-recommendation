#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""space_score.py —— 空间维度硬数据算分（v10.72 根因修复）

用户（2026-11-25）："特斯拉的空间为什么这么差，什么原因，有问题根因改"。
查证：space 维度在 evidence_gate.DIM_POLICY 里标为"纸面可判定、不封顶"，
但**根本没有从物理参数算分的规则，全是手工填分**——实测：
  · 特斯拉 Model Y 轴距2890mm / 后备箱822L → 手填 8.0（全池最低）
  · 海狮06EV 轴距2820mm / 后备箱757L → 手填 9.0（比 Model Y 高1分）
轴距更短、后备箱更小的车反而高1分，物理数据不支持。
  · 部分车物理参数全 null 却拿 9.3（最高）——正是"没数据比有数据高"。

本模块从 trims（自动库 + 人工库）读逐配置物理参数，按统一规则算 space：
  BASE 8.5 @ 轴距中位2915mm / 后备箱中位576L
  轴距每 +10mm → +0.1；后备箱每 +50L → +0.1
  clamp [8.0, 9.3]（沿用现榜分布范围，不整体平移）
缺任一参数 → 返回 None（不猜、不填中性），由调用方决定处理。

用法：
  python3 modules/engine/space_score.py --selftest
  python3 modules/engine/space_score.py --pool runs/x/pool.json --apply
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
import paths  # noqa: E402

BASE = 8.5
WB_MEDIAN = 2915.0       # 中型SUV池轴距中位（19款实测）
TRUNK_MEDIAN = 576.0     # 后备箱中位
WB_STEP = 10.0           # 每10mm
TRUNK_STEP = 50.0        # 每50L
STEP_SCORE = 0.1
LOW, HIGH = 8.0, 9.3


def load_phys() -> dict:
    """{model: {wheelbase_mm, trunk_l, length_mm}} 自动库+人工库合流。"""
    out = {}
    for p in (paths.cat("trims.json"), paths.cat("trims_manual.json")):
        if not os.path.exists(p):
            continue
        d = json.load(open(p, encoding="utf-8"))
        for e in d.get("entries", []):
            tr = (e.get("trims") or [{}])[0]
            wb = tr.get("wheelbase_mm")
            tk = tr.get("trunk_l")
            if wb is None and tk is None:
                continue
            out.setdefault(e["model"], {"wheelbase_mm": wb, "trunk_l": tk,
                                        "length_mm": tr.get("length_mm")})
    return out


def _num(v):
    """把参数页原样文本转成数值：支持「678-1758」「678~1758L」「822/1758」这类区间。

    v10.72.1：参数页后备箱常写成区间（放倒座椅后），旧版直接 float() 会 ValueError
    把整条出榜链路打断（space_score 是 pipeline 的必经步骤）。区间取中位，
    既不是最大也不是最小，避免把「放倒」值当成常规容积。
    """
    if isinstance(v, (int, float)):
        return float(v)
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    nums = re.findall(r"[0-9]+(?:\.[0-9]+)?", s)
    if not nums:
        return None
    vals = [float(x) for x in nums]
    if len(vals) >= 2 and re.search(r"[-~～/至到]", s):
        return round((min(vals) + max(vals)) / 2, 1)
    return vals[0]


def score(model: str, phys: dict | None = None) -> tuple[float | None, str]:
    phys = phys if phys is not None else load_phys()
    p = phys.get(model) or {}
    wb, tk = _num(p.get("wheelbase_mm")), _num(p.get("trunk_l"))
    if wb is None or tk is None:
        return None, f"{model} 轴距/后备箱缺一（不猜，返回空）"
    wb, tk = float(wb), float(tk)
    tk_raw = p.get("trunk_l")
    dw = (wb - WB_MEDIAN) / WB_STEP
    dt = (tk - TRUNK_MEDIAN) / TRUNK_STEP
    raw = BASE + (dw + dt) * STEP_SCORE
    val = round(max(LOW, min(HIGH, raw)), 1)
    tk_note = f"{tk:g}" if isinstance(tk_raw, (int, float)) else f"{tk:g}（{tk_raw} 取中位）"
    return val, (f"轴距{wb:g}mm({dw:+.1f}档) + 后备箱{tk_note}L({dt:+.1f}档) "
                 f"→ {raw:.2f} clamp[{LOW},{HIGH}] = {val}")


def apply(pool: list[dict], phys: dict | None = None) -> list[dict]:
    phys = phys if phys is not None else load_phys()
    out = []
    for c in pool:
        val, why = score(c.get("model") or "", phys)
        if val is None:
            c.setdefault("space_unverified", []).append(why)
            continue
        c.setdefault("scores", {})["space"] = val
        c["space_source"] = "space_score.py 硬数据规则（轴距+后备箱）"
        c["space_note"] = why
        out.append({"model": c.get("model"), "score": val, "why": why})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="空间维度硬数据算分")
    ap.add_argument("--pool", help="候选池 json（给出则写回 space 分）")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        fake = {"Model Y": {"wheelbase_mm": 2890, "trunk_l": 822},
                "短轴距大尾箱": {"wheelbase_mm": 2820, "trunk_l": 757},
                "缺数据": {"wheelbase_mm": None, "trunk_l": None}}
        v1, _ = score("Model Y", fake)
        v2, _ = score("短轴距大尾箱", fake)
        v3, _ = score("缺数据", fake)
        assert v1 is not None and v2 is not None and v3 is None, "三态判定失败"
        assert v1 > v2, f"Model Y 轴距/后备箱都更大，分应更高：{v1} vs {v2}"
        print(f"[selftest OK] Model Y={v1} > 短轴距大尾箱={v2}；缺数据不猜")
        return 0
    if not a.pool:
        ap.error("需要 --pool（或 --selftest）")
    d = json.load(open(a.pool, encoding="utf-8"))
    rows = apply(d.get("candidates", d))
    print(f"[space_score] 算分 {len(rows)} 款")
    for r in rows[:5]:
        print(f"  {r['model']}: {r['score']} — {r['why']}")
    if a.apply:
        json.dump(d, open(a.pool, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"[ok] 写回 {a.pool}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
