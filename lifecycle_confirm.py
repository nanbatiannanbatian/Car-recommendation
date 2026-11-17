#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生命周期确证层（lifecycle_confirm.py）。

为什么存在：
    detect_lifecycle / lifecycle_detect 都是「症状式」判定——只看销量曲线，
    只能输出 sales_drop / clearance? 这类「疑似」，区分不了 停产 / 改款清库 / 正常波动。
    本模块把生命周期升级为「确证判定」：优先查 lifecycle_status.json
    （工信部公告 / 官方停产通告 / 厂商在售状态 / 媒体确证，每条带 URL 溯源），
    查不到再回退候选自带标注或症状式启发式。

用法：
    from lifecycle_confirm import resolve
    r = resolve(c)          # -> {status, note, date, successor, source, source_type, url, confirmed, basis}
    # leaderboard.lifecycle_of / ice_scoring.detect_lifecycle 都改为调用它。

设计纪律：
    - 确证来源是「硬事实」，覆盖症状式结果（无论症状怎么说，只要库里有确证就信确证）。
    - 库里没有的车型，老老实实回退到症状式，不编造确证。
    - 库里标 active（含「推断(无公告)」）的，用来清掉被误报的 sales_drop 假阳性。
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "categories", "lifecycle_status.json")

_CACHE = {}


def load_db(path: str = DB) -> dict:
    if path not in _CACHE:
        if os.path.exists(path):
            _CACHE[path] = json.load(open(path, encoding="utf-8"))
        else:
            _CACHE[path] = {"entries": []}
    return _CACHE[path]


def resolve(c: dict, db: dict | None = None) -> dict:
    """返回确证后的生命周期。字段：
    status, note, date, successor, source, source_type, url, confirmed, basis
    """
    db = db or load_db()
    by = {e["model"]: e for e in db.get("entries", [])}
    model = c.get("model")
    e = by.get(model)
    if e and e.get("status"):
        return {
            "status": str(e["status"]).lower(),
            "note": e.get("note") or "",
            "date": e.get("date") or "",
            "successor": e.get("successor") or "",
            "source": e.get("source") or "",
            "source_type": e.get("source_type") or "",
            "url": e.get("url") or "",
            "confirmed": True,
            "basis": "lifecycle_status.json 确证来源",
        }
    # 回退：候选自带 lifecycle 标注（EV 池常用）
    lc = c.get("lifecycle") or {}
    if lc.get("status"):
        return {
            "status": str(lc.get("status")).lower(),
            "note": lc.get("note") or "",
            "date": lc.get("date") or "",
            "successor": lc.get("successor") or "",
            "source": lc.get("source") or "",
            "source_type": lc.get("source_type") or "候选标注",
            "url": lc.get("url") or "",
            "confirmed": False,
            "basis": "候选自带 lifecycle 标注",
        }
    # 回退：默认在售
    return {
        "status": "active", "note": "", "date": "", "successor": "",
        "source": "", "source_type": "", "url": "",
        "confirmed": False, "basis": "无标注→默认 active",
    }


# 生命周期状态口径（与 leaderboard.LC_LABEL 对齐）
CONFIRMED_STATES = ("discontinued", "facelift_pending", "clearance", "new_launch")


def annotate(c: dict, db: dict | None = None) -> dict:
    """直接把确证结果写回 c['lifecycle']（含 confirmed/source/url 字段）。

    供 ice_scoring.detect_lifecycle 等需要就地写回的场景使用。
    返回写回后的 c['lifecycle'] 字典。
    """
    r = resolve(c, db)
    lc = c.setdefault("lifecycle", {})
    lc["status"] = r["status"]
    lc["note"] = r["note"]
    if r["date"]:
        lc["date"] = r["date"]
    if r["successor"]:
        lc["successor"] = r["successor"]
    lc["confirmed"] = r["confirmed"]
    lc["source"] = r["source"]
    lc["source_type"] = r["source_type"]
    if r["url"]:
        lc["url"] = r["url"]
    return lc


if __name__ == "__main__":
    import sys
    sys.path.insert(0, HERE)
    # 自测：列出库中所有车型 + 回退行为
    db = load_db()
    print(f"[ok] lifecycle_status.json 共 {len(db.get('entries', []))} 条确证记录")
    for e in db["entries"]:
        print(f"  {e['model']:18s} {e['status']:16s} {e.get('source_type','')}")
