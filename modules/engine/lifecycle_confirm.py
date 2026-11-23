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
    from modules.engine.lifecycle_confirm import resolve
    r = resolve(c)          # -> {status, note, date, successor, source, source_type, url, confirmed, basis}
    # leaderboard.lifecycle_of / ice_scoring.detect_lifecycle 都改为调用它。

设计纪律：
    - 确证来源是「硬事实」，覆盖症状式结果（无论症状怎么说，只要库里有确证就信确证）。
    - 库里没有的车型，老老实实回退到症状式，不编造确证。
    - 库里标 active（含「推断(无公告)」）的，用来清掉被误报的 sales_drop 假阳性。
"""
import json
import os

import paths

HERE = str(paths.ROOT)
DB = str(paths.cat("lifecycle_status.json"))

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


# ---------------------------------------------------------------- 中文标签单一事实源
# v10.50.0：此前两套漂移的表——leaderboard.LC_LABEL（6 键，MD/判档/审查侧）与
# board_view.LIFECYCLE_STATUS_CN（15 键，HTML 侧）互不同步：同状态两种文案
# （新车爬坡期/新车爬坡）、MD 侧漏 on_sale/steady 等映射（英文直出漏洞，
# BUG-019/060 只修了 HTML 侧）、admit 闸门认 "stopped" 但两表都不认识。
# 统一到本模块作为唯一事实源，两处改为引用别名，新增状态只改这一处。
# 未映射的非空状态原样返回（门禁负责拦，不静默吞）；空值按系统约定
# 「无标注=默认在售」显示「在售（默认）」，杜绝裸「—」被读成保养周期。
LIFECYCLE_CN = {
    "active": "在售", "on_sale": "在售", "stable": "在售", "steady": "在售（稳定）",
    "new_launch": "新车爬坡期", "new": "新车上市", "ramp_up": "爬坡期",
    "facelift_pending": "改款在即（现款清库）", "clearance": "清库",
    "discontinued": "已停产", "stopped": "已停产",
    "sales_drop": "销量骤降·需查生命周期", "sales_collapse": "销量待确认",
    "mature": "成熟期", "declining": "销量回落", "pending": "待确认",
    "unknown": "状态未知",
}


def lifecycle_status_cn(status) -> str:
    """状态 → 中文（唯一入口）。空值=「在售（默认）」（resolve 第三回退的诚实显示）。"""
    s = str(status or "")
    if not s:
        return "在售（默认）"
    return LIFECYCLE_CN.get(s, s)


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
