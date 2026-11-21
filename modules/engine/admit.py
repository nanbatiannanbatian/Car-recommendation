#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""入池判定（admit.py）——把「这台车该不该进榜」变成一条可复算的规则。

为什么要有它：
  之前的流程是 discover 找出池外车系，然后我把清单发给人类看。这不成立——
  每次都要人判断，就等于没有流程。入池是一个可以用规则表达的决定，不该靠记性。

判定顺序（任一不过即拒，理由码可直接回溯）：
  R1 级别：官方二级分类 ∈ {中型车, 中型SUV, 中大型车, 中大型SUV}
        级别来源 = levels.json 缓存；缓存没有则拒绝（reason=level_unknown）
        **绝不按轴距或车长猜级别**——这正是上一版把深蓝S05 放进中型榜的原因
  R2 动力：必须有纯电供给（纯电榜出现，或车型名/参数确认纯电版）
  R3 价格：带内最低配的官方指导价 ≤ 上限（中型 16 万 / 中大型 18 万）
  R4 销量：近 3 个月销量中位 ≥ min_sales（默认 300，太低的车没有讨论价值）
  R5 生命周期：不得为 discontinued

规则外的两类标注（不参与判定，但要跟着车走）：
  · fleet_risk：可能大量流向网约车/租赁（如红旗E-QM5、比亚迪e7），销量不代表家用量
  · price_promo：只有促销价、没有官方指导价

用法：
    python3 admit.py --discover runs/x/discover.json --min-sales 300
    python3 admit.py --candidates runs/x/candidates.json --check     # 复核已有池
"""
import argparse
import json
import os
import re
import sys

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)

TARGET_CLASSES = ("中型车", "中型SUV", "中大型车", "中大型SUV")
FLEET_HINT = ("E-QM5", "比亚迪e7", "e7", "江豚", "星海V6")


def band_for(cls):
    """按级别取价格带上限。中型 16 万，中大型 18 万。"""
    return 18.0 if cls.startswith("中大型") else 16.0


def judge(name, cls=None, power="BEV", price=None, sales=None,
          lifecycle="active", levels=None, min_sales=300):
    """返回 {admit, code, reason, notes}。纯函数，便于单测与复核。"""
    notes = []
    levels = levels or {}

    # R1 级别
    if not cls:
        lv = levels.get(name)
        cls = (lv or {}).get("cls")
    if not cls:
        return {"admit": False, "code": "level_unknown",
                "reason": "无官方二级分类记录，不能按轴距/车长推定",
                "notes": notes}
    if not any(t in str(cls) for t in TARGET_CLASSES):
        return {"admit": False, "code": "class_out",
                "reason": f"官方定级「{cls}」不在四个目标级别内", "notes": notes}
    # 「中型车」不能误吃「中大型车」：上面用包含匹配，"中大型车" 不含 "中型车"，安全。
    band = band_for(str(cls))

    # R2 动力
    p = str(power or "").upper()
    if "BEV" not in p and "纯电" not in p:
        return {"admit": False, "code": "power_out",
                "reason": f"主销动力 {power}，非纯电", "notes": notes}
    if "PHEV" in p or "EREV" in p or "增程" in p:
        notes.append("车系含插混/增程：销量须用纯电拆分口径，否则会高估")

    # R3 价格
    if price is None:
        return {"admit": False, "code": "price_unknown",
                "reason": "未取得官方指导价，促销价/限时价不能替代", "notes": notes}
    if float(price) > band:
        return {"admit": False, "code": "price_out",
                "reason": f"带内最低配 {float(price):g} 万 > {band:g} 万上限",
                "notes": notes}

    # R4 销量
    if sales is None or float(sales) < min_sales:
        return {"admit": False, "code": "sales_low",
                "reason": f"近 3 月中位销量 {sales} < {min_sales}", "notes": notes}

    # R5 生命周期
    if str(lifecycle).lower() in ("discontinued", "stopped"):
        return {"admit": False, "code": "lifecycle_out",
                "reason": "已停产，不参与在售榜", "notes": notes}

    if any(k in name for k in FLEET_HINT):
        notes.append("fleet_risk：该车系可能大量流向网约车/租赁，销量不代表家庭需求")
    return {"admit": True, "code": "ok",
            "reason": f"{cls}、纯电、带内最低配 {float(price):g} 万、月销 {int(sales):,}",
            "notes": notes, "cls": cls, "band": band}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", help="discover 输出的 json（含池外车系列表）")
    ap.add_argument("--candidates", help="现有候选池，用于 --check 复核")
    ap.add_argument("--levels", default=os.path.join(HERE, "levels.json"))
    ap.add_argument("--min-sales", type=int, default=300)
    ap.add_argument("--check", action="store_true", help="复核已有池是否都该在池里")
    ap.add_argument("--out")
    a = ap.parse_args()

    levels = json.load(open(a.levels, encoding="utf-8"))["levels"] if os.path.exists(a.levels) else {}

    if a.check:
        cands = json.load(open(a.candidates, encoding="utf-8"))["candidates"]
        rows = []
        for c in cands:
            trim = (c.get("trim") or {}).get("price")
            price = float(trim) if trim else None
            if price is None:
                m = re.findall(r"\d+\.?\d*", str(c.get("price") or ""))
                price = float(m[0]) if m else None
            r = judge(c["model"], cls=c.get("cls"), power=c.get("power") or "BEV",
                      price=price, sales=c.get("trend_sales"),
                      lifecycle=(c.get("lifecycle") or {}).get("status") or "active",
                      levels=levels, min_sales=a.min_sales)
            rows.append((c["model"], r))
        bad = [(m, r) for m, r in rows if not r["admit"]]
        print(f"## 复核现有池（{len(rows)} 款）\n")
        print(f"- 规则判为应入池：**{len(rows) - len(bad)}** 款")
        print(f"- 规则判为不该在池：**{len(bad)}** 款\n")
        if bad:
            print("| 车型 | 理由码 | 说明 |")
            print("|---|---|---|")
            for m, r in bad:
                print(f"| {m} | {r['code']} | {r['reason']} |")
        return

    if not a.discover:
        ap.print_help()
        return

    data = json.load(open(a.discover, encoding="utf-8"))
    items = data.get("outside") or data.get("candidates") or []
    ok, rej = [], []
    for it in items:
        name = it.get("name") or it.get("model")
        price = it.get("price_min")
        if price is None and isinstance(it.get("price"), (list, tuple)):
            price = it["price"][0]
        if price is None:
            price = it.get("price")
        r = judge(name, cls=it.get("cls"), power=_power_flag(it), price=price,
                  sales=it.get("sales"), lifecycle=it.get("lifecycle") or "active",
                  levels=levels, min_sales=a.min_sales)
        (ok if r["admit"] else rej).append((name, it, r))

    print(f"## 入池判定\n\n候选 {len(items)} 条 → **入池 {len(ok)}** ／ 拒绝 {len(rej)}\n")
    if ok:
        print("### 自动入池（下一步：补评分配置与七维分数）\n")
        print("| 车系 | 级别 | 带内最低配 | 月销 | 备注 |")
        print("|---|---|---|---|---|")
        for n, it, r in ok:
            note = "；".join(r["notes"]) or "—"
            print(f"| {n} | {r['cls']} | {r.get('band') and float(it.get('price_min') or 0):g} "
                  f"| {int(it.get('sales') or 0):,} | {note} |")
    if rej:
        print("\n### 拒绝（理由码可直接回溯规则）\n")
        print("| 车系 | 理由码 | 说明 |")
        print("|---|---|---|")
        for n, it, r in rej:
            print(f"| {n} | {r['code']} | {r['reason']} |")

    if a.out:
        json.dump({"admitted": [{"name": n, **r} for n, it, r in ok],
                   "rejected": [{"name": n, **r} for n, it, r in rej]},
                  open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\n[ok] {a.out}", file=sys.stderr)


def _power_flag(it):
    pk = str(it.get("power") or "")
    if "纯电" in pk:
        return "BEV"
    if "纯电+" in pk:
        return "BEV+PHEV"
    if "无纯电" in pk:
        return "PHEV"
    return "BEV" if not pk else pk


if __name__ == "__main__":
    main()
