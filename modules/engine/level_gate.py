#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""level_gate.py —— 级别口径单一真相源（v10.69 根因修复）

用户（2026-11-24）："口径改为严格中大型SUV你不能只靠我选择，你要自己判断，
我说中大型就是中大型"。

含义：榜单叫"中大型SUV"，就严格按**官方二级分类 = 中大型SUV** 自动判定
入榜资格——不靠用户临时拍板、不靠我在 universe 清单里手工挑、不靠轴距推定。
级别来自 levels.json（auto_levels.py 联网核验的官方二级分类缓存，查不到不猜）。

设计：
  - eligible(cand, cfg)：候选是否符合本榜级别口径（cfg["gates"]["class"]["allow"]）。
    级别缺失 → 拒绝（不许默认在售默认符合）；级别冲突 → 拒绝并报出。
  - audit(pool, cfg)：全池逐车判定，返回 {eligible, rejected, unverified}。
  - pipeline 出榜硬拦：unverified（级别无官方来源）不得出榜。

与 universe 的关系：universe 是"同级在售车盘点清单"（发现漏收用），
level_gate 是"单车是否符合本榜口径"（准入判定用）——两者职责不同，不重复。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
import paths  # noqa: E402


def load_levels() -> dict:
    p = paths.ROOT / "levels.json"
    if not p.exists():
        return {}
    return (json.load(open(p, encoding="utf-8")).get("levels") or {})


def official_cls(model: str, levels: dict | None = None) -> tuple[str | None, str]:
    """返回 (官方二级分类, 来源说明)。查不到返回 (None, 原因)——绝不猜。"""
    levels = levels if levels is not None else load_levels()
    e = levels.get(model) or {}
    cls = e.get("cls")
    if not cls:
        return None, "levels.json 无官方级别缓存（须 auto_levels.py 联网核验，不许推定）"
    return cls, str(e.get("note") or "levels.json")


def eligible(cand: dict, allow: list[str], levels: dict | None = None) -> dict:
    """单车准入判定。allow = cfg.gates.class.allow（官方二级分类白名单）。"""
    model = cand.get("model") or ""
    cls, src = official_cls(model, levels)
    ok = cls in allow if cls else False
    return {"model": model, "ok": ok, "cls": cls, "source": src,
            "allow": list(allow),
            "reason": ("符合" if ok else
                       f"官方级别 {cls or '未取得'} 不在 {allow}（{src}）")}


def audit(cands: list[dict], allow: list[str]) -> dict:
    levels = load_levels()
    checks = [eligible(c, allow, levels) for c in cands]
    return {"checked": len(checks),
            "eligible": [c for c in checks if c["ok"]],
            "rejected": [c for c in checks if not c["ok"] and c["cls"]],
            "unverified": [c for c in checks if not c["cls"]]}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="级别口径单一真相源检查")
    ap.add_argument("--category", help="品类配置ID（如 car_largesuv_bev）")
    ap.add_argument("--candidates", help="候选池 json（给出则逐车审计）")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        lv = {"A": {"cls": "中大型SUV", "note": "官方"}, "B": {"cls": "大型SUV", "note": "官方"}}
        assert eligible({"model": "A"}, ["中大型SUV"], lv)["ok"], "符合级别被拒"
        assert not eligible({"model": "B"}, ["中大型SUV"], lv)["ok"], "不符合级别被放行"
        assert not eligible({"model": "C"}, ["中大型SUV"], lv)["ok"], "无级别被放行"
        print("[selftest OK] 符合/不符合/无级别三种判定正确")
        return 0

    if not a.category:
        ap.error("需要 --category（或 --selftest）")
    cfg = json.load(open(str(paths.cat(f"{a.category}.json")), encoding="utf-8"))
    allow = ((cfg.get("gates") or {}).get("class") or {}).get("allow") or []
    assert allow, f"{a.category} 未配置 gates.class.allow"
    print(f"官方级别白名单: {allow}")
    if a.candidates:
        d = json.load(open(a.candidates, encoding="utf-8"))
        r = audit(d.get("candidates", d), allow)
        print(f"审计 {r['checked']} 款: 符合 {len(r['eligible'])} / "
              f"级别不符 {len(r['rejected'])} / 无级别缓存 {len(r['unverified'])}")
        for c in r["rejected"]:
            print(f"  [不符] {c['model']}: {c['reason']}")
        for c in r["unverified"]:
            print(f"  [无级别] {c['model']}: {c['reason']}")
        return 1 if (r["rejected"] or r["unverified"]) else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
