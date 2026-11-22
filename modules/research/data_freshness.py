#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""data_freshness.py —— 打分数据与阈值的「新鲜度体检」（纯读，不改分、不联网）

为什么有这个模块（2026-11-22 用户诉求）：
  「27 年以后厂家的三电全部升级了，三电评分是不是就要修改了？
    是不是在 research 增加模块查询数据填入打分，然后这些数据每隔一个月过期需要重新填入」

  拆成两个真问题：
  1) 阈值会通胀——2026 定的 800V/快充分档，2027 全面升级后就失真。
     powertrain_rules.json 已配置化（改档不改代码），但**缺「何时该改」的到期信号**。
  2) 车型参数会过时——trims 带 date 字段、research 已有整套联网采集脚本，
     但**缺一个统一体检**：谁过期了、该重采哪些车哪些字段。

为什么不是「research 实时查数直接填分 / 每月自动重填」：
  本榜铁律是「无模型调用、同一输入必得同一输出、出榜只读快照」。若出榜时联网查数直接进分，
  同一天两次出榜会不同、跨榜同分会碎、回归无法稳定。正确闭环是：
      体检发现过期 → 定向重采(research 脚本) → 人工核验落库 → 重打指纹 → 重出榜
  本模块只做第一步（体检 + 出刷新计划），绝不联网、绝不写任何数据文件、绝不改分。

体检四类对象：
  · thresholds  三电阈值复核到期（powertrain_rules.json 的 review 块）
  · specs       车型三电参数采集日期（data/gen/trims.json 各车 date）
  · pools       候选池打分逻辑指纹是否过期（_generated_by.fingerprint）
  · filings     申报库缓存时效（categories/filings_notes.json 的 updated）

契约（遵守 research 包纪律）：只返回 dict，不 import engine/board，不打印排名。
用法：
  python -m modules.research.data_freshness              # 人读摘要
  python -m modules.research.data_freshness --json       # 结构化 JSON（供回归/CI）
  python -m modules.research.data_freshness --strict     # 有 overdue 退出码 2
  python -m modules.research.data_freshness --as-of 2027-06-01  # 模拟未来某天体检
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

import paths

# 车型三电参数体检默认阈值（天）。参数页不像申报状态那样天天变，一个季度体检一次足够；
# 高频的销量/价格/优惠另有月度机制，不在此处。
DEFAULT_SPEC_STALE_DAYS = 90
# 候选池指纹过期天数（逻辑变更会重 stamp；这里只提示「多久没重 stamp」，
# 真正是否过期由 score_fingerprint --check 判定，本模块不 import engine 计算指纹）。
POOL_STALE_DAYS = 30

THRESHOLD_CFG = "powertrain_rules.json"
TRIMS_GEN = "data/gen/trims.json"
FILINGS = "filings_notes.json"
# 三榜主候选池（体检覆盖当前在维护的池；其他池不阻断，可 --pools 覆盖）
DEFAULT_POOLS = [
    "runs/midsedan_bev_pool_202611.json",
    "runs/midsuv_bev_pool_202611_final.json",
    "runs/midsuv_bev_u18_pool_202611.json",
]
# 三电关键字段：这些字段的采集日期才是「三电参数新鲜度」——缺这些才需要重采。
PT_FIELDS = ("battery_kwh", "cltc_km", "charge", "voltage_v", "motor_kw", "cell_supplier")


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _days(as_of, date_str):
    """返回 as_of - date_str 天数；无法解析返回 None。"""
    if not date_str:
        return None
    try:
        d = dt.date.fromisoformat(str(date_str)[:10])
        return (as_of - d).days
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 四类体检
# ---------------------------------------------------------------------------
def check_thresholds(as_of, cfg=None):
    cfg = cfg if cfg is not None else _load(paths.cat(THRESHOLD_CFG))
    if not cfg:
        return {"state": "missing", "reason": "阈值配置缺失"}
    rv = cfg.get("review") or {}
    reviewed = rv.get("reviewed_at")
    interval = rv.get("review_interval_days")
    days = _days(as_of, reviewed)
    if not reviewed or not isinstance(interval, int) or days is None:
        return {"state": "unknown", "reviewed_at": reviewed, "interval_days": interval,
                "reason": "review 块缺 reviewed_at/review_interval_days"}
    state = "overdue" if days > interval else ("due" if days > interval * 0.8 else "fresh")
    return {
        "state": state, "reviewed_at": reviewed, "interval_days": interval,
        "days": days, "next_review": rv.get("next_review"),
        "trigger": rv.get("trigger"),
        "action": ("必须重新联网核验 sources 并重定三电档位，改 powertrain_rules.json 后重打指纹重出榜"
                   if state in ("overdue", "due") else "无需动作"),
    }


def check_specs(as_of, spec_days=None, trims=None):
    """按车型 date 体检三电参数新鲜度。返回过期车型清单与字段。"""
    limit = spec_days if isinstance(spec_days, int) and spec_days > 0 else DEFAULT_SPEC_STALE_DAYS
    trims = trims if trims is not None else (_load(paths.ROOT / TRIMS_GEN) or {})
    entries = trims.get("entries") if isinstance(trims, dict) else None
    if entries is None:
        entries = list(trims.values()) if isinstance(trims, dict) else []
    stale, dates, no_date = [], [], []
    for e in entries:
        if not isinstance(e, dict):
            continue
        model = e.get("model")
        if not model:
            continue
        d = _days(as_of, e.get("date"))
        if d is None:
            no_date.append(model)
            continue
        dates.append(d)
        if d > limit:
            trows = e.get("trims") or []
            present = sorted({f for t in trows if isinstance(t, dict)
                              for f in PT_FIELDS if t.get(f) not in (None, "")})
            stale.append({"model": model, "days": d, "date": e.get("date"),
                          "url": e.get("url"), "pt_fields_present": present,
                          "refresh": "python -m modules.research.auto_trims --candidates <候选池> --index <车型索引> --db data/gen/trims.json --out data/gen/trims.json"})
    stale.sort(key=lambda x: -x["days"])
    return {
        "threshold_days": limit, "checked": len(dates) + len(no_date),
        "stale_count": len(stale), "no_date_count": len(no_date),
        "oldest_days": max(dates) if dates else None,
        "stale": stale, "no_date": no_date[:20],
    }


def check_pools(as_of, pools=None):
    """只读候选池的指纹 stamp 日期；不计算新指纹（那是 engine 的职责）。"""
    paths_in = pools if pools is not None else DEFAULT_POOLS
    out = []
    for p in paths_in:
        full = p if os.path.isabs(p) else str(paths.ROOT / p)
        d = _load(full)
        gb = (d or {}).get("_generated_by") or {}
        fp = gb.get("fingerprint") or (d or {}).get("score_fingerprint")
        stamped = gb.get("stamped_at") or gb.get("updated") or gb.get("date")
        days = _days(as_of, stamped)
        out.append({"path": p, "fingerprint": (str(fp)[:12] if fp else None),
                    "stamped": stamped, "days": days,
                    "state": ("no_stamp" if not fp else
                              ("old" if days is not None and days > POOL_STALE_DAYS else "ok")),
                    "verify": f"python -m modules.engine.score_fingerprint --check {p}"})
    return out


def check_filings(as_of, doc=None):
    doc = doc if doc is not None else _load(paths.cat(FILINGS))
    if not doc:
        return {"state": "missing"}
    days = _days(as_of, doc.get("updated"))
    stale = days is None or days > 3  # 与 filings_board.STALE_DAYS=3 一致
    return {"state": "stale" if stale else "fresh", "updated": doc.get("updated"),
            "days": days, "threshold_days": 3,
            "action": "先联网核实申报/上市/价格全量，再以本地表交叉核验" if stale else "无需动作"}


def audit(as_of=None, cfg=None, trims=None, spec_days=None, filings=None, pools=None):
    """纯读体检，返回结构化 dict。所有入参可注入（回归反注入用），不改任何文件。"""
    today = dt.date.fromisoformat(str(as_of)[:10]) if as_of else dt.date.today()
    th = check_thresholds(today, cfg=cfg)
    sp = check_specs(today, spec_days=spec_days, trims=trims)
    fl = check_filings(today, doc=filings)
    pl = check_pools(today, pools=pools)
    plan = []
    if th.get("state") in ("overdue", "due"):
        plan.append({"kind": "threshold_review", "target": THRESHOLD_CFG,
                     "why": f"阈值复核 {th.get('state')}（{th.get('days')} 天 / 周期 {th.get('interval_days')} 天）",
                     "how": "联网核验 powertrain_rules.json.sources → 重定档 → 重打指纹 → 重出三榜"})
    if sp["stale_count"]:
        plan.append({"kind": "spec_refresh", "target": [x["model"] for x in sp["stale"]],
                     "why": f"{sp['stale_count']} 款车三电参数采集日期超过 {sp['threshold_days']} 天",
                     "how": "auto_trims 定向重采 → 人工核验证据 → 重打指纹 → 重出三榜"})
    if fl.get("state") == "stale":
        plan.append({"kind": "filings_refresh", "target": FILINGS,
                     "why": f"申报库缓存 {fl.get('days')} 天（阈值 {fl.get('threshold_days')} 天）",
                     "how": fl.get("action")})
    old_pools = [x for x in pl if x["state"] in ("old", "no_stamp")]
    if old_pools:
        plan.append({"kind": "pool_restamp", "target": [x["path"] for x in old_pools],
                     "why": "候选池距上次 stamp 超 30 天或无指纹",
                     "how": "score_fingerprint --check 确认；逻辑已变须 --stamp 后重出榜"})
    overall = "overdue" if any([th.get("state") == "overdue", fl.get("state") == "stale"]) else (
        "due" if any([th.get("state") == "due", sp["stale_count"] > 0, old_pools]) else "fresh")
    return {
        "as_of": today.isoformat(), "overall": overall,
        "thresholds": th, "specs": sp, "pools": pl, "filings": fl,
        "refresh_plan": plan,
        "note": "本模块只体检、不联网、不改数据。出榜只读快照；过期须走「定向重采→人工核验→重打指纹→重出榜」闭环。",
    }


def render_md(r):
    L = [f"# 数据新鲜度体检（截至 {r['as_of']}）", "",
         f"总体：**{r['overall']}**", ""]
    th = r["thresholds"]
    L.append(f"- 三电阈值复核：{th.get('state')} — 复核于 {th.get('reviewed_at')}，"
             f"距今 {th.get('days')} 天 / 周期 {th.get('interval_days')} 天，下次 {th.get('next_review')}")
    sp = r["specs"]
    L.append(f"- 车型三电参数：体检 {sp['checked']} 款，过期 {sp['stale_count']} 款，"
             f"最旧 {sp['oldest_days']} 天 / 阈值 {sp['threshold_days']} 天")
    for x in sp["stale"][:10]:
        L.append(f"    - {x['model']}：{x['days']} 天（{x['date']}，有字段 {','.join(x['pt_fields_present']) or '无'}）")
    fl = r["filings"]
    L.append(f"- 申报库缓存：{fl.get('state')} — 更新于 {fl.get('updated')}，距今 {fl.get('days')} 天")
    L.append("- 候选池指纹：" + ", ".join(
        f"{p['path'].split('/')[-1]}={p['state']}" for p in r["pools"]))
    if r["refresh_plan"]:
        L.extend(["", "## 刷新计划", ""])
        for i, x in enumerate(r["refresh_plan"], 1):
            t = x["target"]
            L.append(f"{i}. **{x['kind']}**：{x['why']}")
            L.append(f"   - 对象：{', '.join(t) if isinstance(t, list) else t}")
            L.append(f"   - 做法：{x['how']}")
    L += ["", "> 本体检纯读本地、不联网、不改分。出榜只读快照；数据过期须走"
          "「定向重采 → 人工核验 → 重打指纹 → 重出榜」闭环，不能让 research 实时改分。"]
    return "\n".join(L)


def selftest():
    import datetime as d0
    base = d0.date(2026, 11, 22)
    cfg = {"review": {"reviewed_at": "2026-11-22", "review_interval_days": 180,
                      "next_review": "2027-05-21"}}
    assert check_thresholds(base, cfg=cfg)["state"] == "fresh"
    assert check_thresholds(d0.date(2027, 6, 1), cfg=cfg)["state"] == "overdue"
    trims = {"entries": [{"model": "早数据车", "date": "2026-06-01",
                          "trims": [{"battery_kwh": 70, "cltc_km": 600}]},
                         {"model": "新数据车", "date": "2026-11-20", "trims": []}]}
    sp = check_specs(base, spec_days=90, trims=trims)
    assert sp["stale_count"] == 1 and sp["stale"][0]["model"] == "早数据车"
    r = audit(base, cfg=cfg, trims=trims, spec_days=90,
              filings={"updated": "2026-11-20"}, pools=[])
    # 注入了一款过期车 → overall 应为 due，且刷新计划含 spec_refresh
    assert r["overall"] == "due", f"过期车应触发 due: {r['overall']}"
    assert any(x["kind"] == "spec_refresh" for x in r["refresh_plan"])
    assert render_md(r)
    # 全新鲜场景 → fresh
    fresh_trims = {"entries": [{"model": "新数据车", "date": "2026-11-20", "trims": []}]}
    r2 = audit(base, cfg=cfg, trims=fresh_trims, spec_days=90,
               filings={"updated": "2026-11-20"}, pools=[])
    assert r2["overall"] == "fresh" and not r2["refresh_plan"]
    # 阈值过期 + 申报库过期 → overdue
    r3 = audit(d0.date(2027, 6, 1), cfg=cfg, trims=fresh_trims, spec_days=90,
               filings={"updated": "2026-06-01"}, pools=[])
    assert r3["overall"] == "overdue" and len(r3["refresh_plan"]) >= 2
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="数据与阈值新鲜度体检（纯读，不改分、不联网）")
    ap.add_argument("--json", action="store_true", help="输出结构化 JSON")
    ap.add_argument("--strict", action="store_true", help="overdue/due 时退出码 2")
    ap.add_argument("--as-of", help="模拟体检日期 YYYY-MM-DD（默认今天）")
    ap.add_argument("--spec-days", type=int, default=None, help="车型参数过期阈值天数")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    r = audit(as_of=a.as_of, spec_days=a.spec_days)
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(render_md(r))
    if a.strict and r["overall"] in ("overdue", "due"):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
