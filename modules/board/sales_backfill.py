#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""销量权威回填（sales_backfill.py）——出榜前用懂车帝逐月权威销量覆盖候选池手填值。

为什么要它（2026-11-22 阿维塔07 事故根因）：
  出榜链路 pipeline.py 从来不校验候选池的 trend_sales，那个字段是**手填常量**：
    阿维塔07 = 7000（实际懂车帝车系合计 356，且纯电 ≤356）
    特斯拉Model Y = 48000（实际纯电榜 7807）
    蔚来ES6 = 4000（实际 656）、小鹏G6 = 5000（实际 398）
  cross_verify.py 默认又不加载懂车帝快照（CROSS_VERIFY_DCD 未设），
  于是错值一路过闸、误导第一档 t1_min_sales=2000 门槛和页面展示。
  这是**链路缺失**（权威源没接进出榜），不是单个数字填错——补单个数字是打补丁。

口径（用户 2026-11-22 拍板）：
  - 纯电榜命中 → 逐月中位数（新车爬坡取中位与最新月较大），scope=pure_bev，直接采信。
  - 只有「全部榜」命中（一车多动力未拆车系名）→ 车系合计是纯电**上界**，
    scope=series_total，下游渲染显示「≤N」、标灰不参与销量分位（board_v2/board_view 已有此逻辑）。
  - 三榜皆无 → 保留原值并写 sales_backfill.status=no_data + 原因，不臆造。
  - 手填值与权威值差异 ≥2 倍或 ≥3000 → 记 dispute，默认以权威源为准回填（可 --no-sales-backfill 关）。

数据来源与去重：
  懂车帝公开接口 rank_data（modules/research/fetch_dcd_sales.fetch_all），逐月抓
  new_energy_type=1/2/空 三口径。抓一次落当天缓存 runs/dcd_sales_snapshot_<asof>.json，
  同日复用，避免每次出榜打 18 次接口。

纪律：
  - 只写 trend_sales / sales_scope / sales_note / trend_note / trend_method / trend_months /
    sales_source / sales_backfill 这些**销量事实字段**，绝不碰 scores/adjust/total。
  - 车名走 F.norm + canon 双归一（剥离动力后缀、品牌前缀），匹配不到就明确报 no_data，不猜。
  - 离线可复现：有 --snapshot 时零网络；同输入必得同输出。

用法：
  from modules.board.sales_backfill import backfill
  report = backfill(cands, as_of="202610", snapshot=None)   # 原地改 cands 的销量字段
  python3 sales_backfill.py --selftest
  python3 sales_backfill.py --candidates <pool> --as-of 202610 --apply
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import datetime

import paths

HERE = str(paths.ROOT)


def _load_snapshot(path: str) -> dict | None:
    if not path or not os.path.exists(path):
        return None
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return None


def snapshot_path(as_of: str) -> str:
    return os.path.join(str(paths.RUNS_DIR), f"dcd_sales_snapshot_{as_of}.json")


def fetch_snapshot(as_of: str, months: int = 6, force: bool = False) -> dict:
    """抓懂车帝逐月三口径销量，落当天缓存。返回 {as_of, months, rows:[{name,months:{bev,phev,all}}]}。"""
    from modules.research import fetch_dcd_sales as F

    p = snapshot_path(as_of)
    if not force:
        cached = _load_snapshot(p)
        if cached and cached.get("as_of") == str(as_of):
            return cached

    mlist = [str(m) for m in F.month_list(str(as_of), months)]
    series: dict[str, dict] = {}
    for mth in mlist:
        for ne in ("1", "2", ""):
            rows, _ = F.fetch_all(mth, ne)
            bucket = {"1": "bev", "2": "phev", "": "all"}[ne]
            for r in rows:
                k = F.norm(r["series_name"])
                rec = series.setdefault(k, {"name": r["series_name"], "months": {"bev": {}, "phev": {}, "all": {}}})
                rec["months"][bucket][mth] = r.get("sales") or 0

    snap = {"as_of": str(as_of), "months": mlist,
            "source": "懂车帝销量榜 rank_data（new_energy_type=1/2/空 逐月三口径）",
            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "rows": [{"key": k, **v} for k, v in series.items()]}
    os.makedirs(str(paths.RUNS_DIR), exist_ok=True)
    json.dump(snap, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return snap


def _effective(months_map: dict, lifecycle: str) -> tuple[int, str]:
    """逐月中位数；新车爬坡取中位与最新月较大。复用 fetch_dcd_sales._trend_pick 唯一口径。"""
    vals = {m: int(v) for m, v in (months_map or {}).items() if (v or 0) > 0}
    if not vals:
        return 0, "无销量"
    med = round(statistics.median(list(vals.values())))
    try:
        from modules.research import fetch_dcd_sales as F
        eff, how = F._trend_pick({"lifecycle": {"status": lifecycle}}, vals, med, "")
        return int(eff), how
    except Exception:
        lc = str(lifecycle or "").lower()
        if lc == "new_launch" and len(vals) >= 2:
            latest = max(vals)
            if vals[latest] > med:
                return vals[latest], f"新车爬坡取最新月 {latest}"
        return med, f"{len(vals)}个月中位数"


def _match_key(model: str, snap: dict) -> tuple[str, dict] | None:
    """车名匹配：先 F.norm 全等，再剥离候选池动力后缀，最后包含（长度差≤3）。

    候选池车名常带「纯电版/EV/BEV」后缀（如「阿维塔07 纯电版」），而懂车帝榜
    只有车系名「阿维塔 07」。F.norm 的 POWER_TOKENS 不剥「纯电版」（整词），
    所以这里额外生成一层候选 key，否则 2026-11-22 阿维塔07 会匹配失败。
    """
    from modules.research import fetch_dcd_sales as F
    # 品牌别名：候选池用中文名、懂车帝用英文名（ZEEKR 7X = 极氪7X）。
    # 别名必须先替换再做 F.norm，否则「极氪7X」永远匹配不到「ZEEKR 7X」。
    ALIAS = {"极氪": "ZEEKR", "zeekr": "ZEEKR"}
    raw = str(model or "")
    aliases = {raw}
    for zh, en in ALIAS.items():
        if zh in raw:
            aliases.add(raw.replace(zh, en))
    candidates: set[str] = set()
    for a in aliases:
        base = F.norm(a)
        candidates.add(base)
        # 剥离候选池侧的动力后缀（F.norm 只剥整词 token，不剥「纯电版」这类带「版」后缀）
        v = base
        for suf in ("纯电版", "纯电", "bev", "ev", "electric"):
            if v.endswith(suf) and len(v) > len(suf) + 1:
                candidates.add(v[: -len(suf)])
    candidates |= {F.norm(x) for x in list(candidates)}

    rows = snap.get("rows", [])
    by = {r["key"]: r for r in rows}
    for ck in candidates:
        if ck in by:
            return ck, by[ck]
    for ck in candidates:
        if len(ck) < 3:
            continue
        for rk, r in by.items():
            if (ck in rk or rk in ck) and abs(len(ck) - len(rk)) <= 3:
                return rk, r
    return None


def backfill(cands: list[dict], as_of: str = "202610", snapshot: dict | None = None,
             months: int = 6, force_fetch: bool = False) -> dict:
    """用权威销量回填候选池（原地改销量字段）。返回审计 report。"""
    snap = snapshot or fetch_snapshot(as_of, months=months, force=force_fetch)
    applied, no_data, disputes = [], [], []
    for c in cands:
        model = c.get("model", "")
        lc = (c.get("lifecycle") or {}).get("status", "") if isinstance(c.get("lifecycle"), dict) else ""
        old = c.get("trend_sales")
        hit = _match_key(model, snap)
        if not hit:
            c["sales_backfill"] = {"status": "no_data", "reason": "懂车帝三口径榜均未匹配到该车系", "old": old}
            no_data.append(model)
            continue
        _key, row = hit
        mm = row.get("months") or {}
        bev = {m: v for m, v in mm.get("bev", {}).items() if (v or 0) > 0}
        allv = {m: v for m, v in mm.get("all", {}).items() if (v or 0) > 0}
        if bev:
            eff, how = _effective(bev, lc)
            scope = "pure_bev"
            note = "懂车帝纯电榜逐月：" + "/".join(f"{m[-2:]}月{v}" for m, v in sorted(bev.items()))
            c["sales_scope"] = "pure_bev"
        elif allv:
            eff, how = _effective(allv, lc)
            scope = "series_total"
            note = ("懂车帝三动力细分榜均未收录（一车多动力未拆车系名），"
                    "车系合计为纯电上界，页面标 ≤，不参与销量分位；逐月：" +
                    "/".join(f"{m[-2:]}月{v}" for m, v in sorted(allv.items())))
            c["sales_scope"] = "series_total"
            c["sales_upper_bound"] = eff
        else:
            c["sales_backfill"] = {"status": "no_data", "reason": "匹配到车系但三口径销量均为空", "old": old}
            no_data.append(model)
            continue

        # 分歧判定：手填值与权威值差 ≥2 倍或 ≥3000，记 dispute（默认以权威为准）
        try:
            ov = float(old) if old is not None else None
        except (TypeError, ValueError):
            ov = None
        dispute = None
        if ov and eff and (max(ov, eff) / max(1, min(ov, eff)) >= 2 or abs(ov - eff) >= 3000):
            dispute = {"old": ov, "new": eff, "ratio": round(max(ov, eff) / max(1, min(ov, eff)), 1)}

        c["trend_sales"] = eff
        c["trend_method"] = how
        c["trend_note"] = note
        c["trend_months"] = bev if bev else allv
        c["sales_source"] = "dcd_rank_data"
        c["sales_backfill"] = {"status": "applied", "scope": scope, "old": old,
                               "new": eff, "dispute": dispute}
        applied.append({"model": model, "old": old, "new": eff, "scope": scope,
                        "dispute": bool(dispute)})

    return {"as_of": as_of, "source": snap.get("source"), "applied": applied,
            "no_data": no_data, "dispute_count": sum(1 for x in applied if x["dispute"]),
            "n": len(cands)}


def selftest() -> int:
    """锁死三条口径：纯电榜优先、全部榜标上界、分歧检测。"""
    # ① 纯电榜命中直接采信（key 用真实 F.norm 值，避免测试与实现口径分叉）
    from modules.research import fetch_dcd_sales as F
    snap = {"rows": [{"key": F.norm("极氪7X"), "name": "极氪7X", "months": {
        "bev": {"202608": 8000, "202609": 8500, "202610": 9000}, "all": {}, "phev": {}}}]}
    c = [{"model": "极氪7X", "trend_sales": 99999}]
    r = backfill(c, "202610", snapshot=snap)
    assert c[0]["trend_sales"] == 8500, f"纯电中位应 8500，得 {c[0]['trend_sales']}"
    assert c[0]["sales_scope"] == "pure_bev", c[0]["sales_scope"]
    assert r["dispute_count"] == 1, f"99999 vs 8500 应判 dispute，得 {r}"
    # ② 全部榜兜底标上界（候选池带「纯电版」后缀，必须能匹配到车系名）
    snap2 = {"rows": [{"key": F.norm("阿维塔 07"), "name": "阿维塔 07", "months": {
        "bev": {}, "all": {"202608": 716, "202609": 461, "202610": 356}, "phev": {}}}]}
    c2 = [{"model": "阿维塔07 纯电版", "trend_sales": 7000}]
    r2 = backfill(c2, "202610", snapshot=snap2)
    assert c2[0]["trend_sales"] == 461, f"全部榜中位应 461，得 {c2[0]['trend_sales']}"
    assert c2[0]["sales_scope"] == "series_total", c2[0]["sales_scope"]
    assert c2[0]["sales_upper_bound"] == 461
    assert r2["dispute_count"] == 1, "7000 vs 461 应判 dispute"
    # ③ 无数据不臆造
    c3 = [{"model": "某不存在车", "trend_sales": 1234}]
    r3 = backfill(c3, "202610", snapshot=snap2)
    assert c3[0]["trend_sales"] == 1234, "无数据应保留原值"
    assert c3[0]["sales_backfill"]["status"] == "no_data"
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--candidates")
    ap.add_argument("--as-of", default="202610")
    ap.add_argument("--snapshot", help="离线快照 json（零网络）")
    ap.add_argument("--months", type=int, default=6)
    ap.add_argument("--force-fetch", action="store_true", help="忽略当天缓存重抓")
    ap.add_argument("--apply", action="store_true", help="写回候选池（默认只打印报告）")
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.selftest:
        try:
            selftest()
            print("sales_backfill selftest OK")
            return 0
        except AssertionError as e:
            print(f"[FAIL] {e}", file=sys.stderr)
            return 1
    if not a.candidates:
        ap.error("需要 --candidates 或 --selftest")
    d = json.load(open(a.candidates, encoding="utf-8"))
    cands = d.get("candidates", d)
    snap = _load_snapshot(a.snapshot) if a.snapshot else None
    r = backfill(cands, a.as_of, snapshot=snap, months=a.months, force_fetch=a.force_fetch)
    L = [f"# 销量权威回填（{r['as_of']}）", "",
         f"- 候选 {r['n']} 款 / 回填 {len(r['applied'])} / 无数据 {len(r['no_data'])} / 分歧 {r['dispute_count']}",
         f"- 来源：{r['source']}", "",
         "| 车型 | 原值 | 权威值 | 口径 | 分歧 |", "|---|---:|---:|---|---|"]
    for x in r["applied"]:
        L.append(f"| {x['model']} | {x['old']} | {x['new']} | {x['scope']} | {'⚠' if x['dispute'] else ''} |")
    if r["no_data"]:
        L += ["", "## 无权威数据（保留原值）", ""] + [f"- {m}" for m in r["no_data"]]
    txt = "\n".join(L)
    print(txt)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(txt + "\n")
    if a.apply:
        json.dump(d, open(a.candidates, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"[ok] 已写回 {a.candidates}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
