#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新闻层沉淀工具（notes_tool.py）——把「检索→填字段→入库」变成固定流程。

为什么需要这个工具：
  备案表只给「存在」和续航/电量/整备，不给车长（轿车）、价格、上市窗口、MPV 判定——
  这四项决定能不能进价格带，只能靠新闻/官方口径补。补过的不重复补，
  沉淀到 categories/filings_notes.json 每月增量。此前这一步是"顺手写 JSON"，
  没有校验、没有覆盖率视图、没有字段规范——这就是修模型之后要补的能力。

用法（每月跑一次，配合 filings.py）：
    python3 notes_tool.py --todo                     # 列出未沉淀车型+要补的字段+检索词
    python3 notes_tool.py --add batch.json           # 校验并合并入库（增量，不覆盖已有字段）
    python3 notes_tool.py --show                     # 看覆盖率与 conf 分布
    python3 notes_tool.py --export-template t.json   # 导出待填模板（可直接编辑后 --add）

入库校验（不过就拒收，防止脏数据进预测）：
  · name 必填，与备案「通用名称」一致（key）
  · price_expect 必须是 [lo, hi] 且 0 < lo <= hi <= 120
  · len_m 在 2.5–6.5 之间；cls ∈ 官方二级分类词表
  · body ∈ {轿车, SUV, MPV}
  · conf ∈ {A,B,C}：A=官方口径（官网/上市公告/工信部）B=两家以上媒体一致 C=单一来源或推断
  · src 必填（可核验来源），A/B 级还必须给 url
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
NOTES_PATH = os.path.join(HERE, "categories", "filings_notes.json")

CLS_OK = {"微型车", "小型车", "紧凑型车", "中型车", "中大型车", "大型车",
          "小型SUV", "紧凑型SUV", "中型SUV", "中大型SUV", "大型SUV",
          "小型MPV", "紧凑型MPV", "中型MPV", "中大型MPV", "大型MPV"}
BODY_OK = {"轿车", "SUV", "MPV"}
CONF_OK = {"A", "B", "C"}


def load(p: str = NOTES_PATH) -> dict:
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    return {"note": "", "updated": "", "models": {}}


def save(db: dict, p: str = NOTES_PATH) -> None:
    db["updated"] = dt.datetime.now().isoformat(timespec="seconds")
    json.dump(db, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def need(item: dict, have: dict) -> list[str]:
    """这款车还缺哪些字段。"""
    miss = []
    if not have.get("len_m"):
        miss.append("len_m（车长，米；轿车只能靠新闻/实拍，SUV 也可用型号编码兜底）")
    if not have.get("price_expect"):
        miss.append("price_expect（预售价/指导价区间 [lo,hi]，决定能不能进价格带）")
    if not have.get("launch"):
        miss.append("launch（上市窗口或已上市日期）")
    have_body = have.get("body") or (item.get("cls_guess") or {}).get("body")
    if not have_body or have_body == "SUV/MPV":
        miss.append("body（SUV 还是 MPV——多用途乘用车两者都可能）")
    return miss


def search_hint(item: dict) -> str:
    """给一个可直接用的检索词。型号编码是最稳的锚。"""
    codes = ",".join(item.get("model_codes") or [])
    return (f"「{(item.get('names') or [item.get('name')])[0]} 上市 价格」"
            + (f"；查不到再搜「{codes} 工信部」" if codes else ""))


def validate_entry(name: str, e: dict) -> list[str]:
    errs = []
    if not name:
        errs.append("缺 name（用备案通用名称）")
    pe = e.get("price_expect")
    if pe is not None:
        if not (isinstance(pe, list) and len(pe) == 2
                and all(isinstance(x, (int, float)) for x in pe)):
            errs.append("price_expect 必须是 [lo, hi] 数字区间")
        elif not (0 < pe[0] <= pe[1] <= 120):
            errs.append(f"price_expect {pe} 不合理（0 < lo <= hi <= 120 万）")
    lm = e.get("len_m")
    if lm is not None and not (2.5 <= float(lm) <= 6.5):
        errs.append(f"len_m={lm} 超出 2.5–6.5 m")
    if e.get("cls") and e["cls"] not in CLS_OK:
        errs.append(f"cls={e['cls']} 不在官方二级分类词表")
    if e.get("body") and e["body"] not in BODY_OK:
        errs.append(f"body={e['body']} 必须是 {sorted(BODY_OK)}")
    if e.get("conf") and e["conf"] not in CONF_OK:
        errs.append(f"conf={e['conf']} 必须是 {sorted(CONF_OK)}")
    if not e.get("src"):
        errs.append("缺 src（来源描述，可核验）")
    if e.get("conf") in ("A", "B") and not e.get("url"):
        errs.append("conf=A/B 必须给 url（官方口径/双源一致才值 A/B）")
    return errs


def merge(db: dict, entries: dict) -> tuple[int, int, list[str]]:
    """合并入库：已有字段不覆盖（除非显式给 None 标记删除），返回 (新增, 更新, 拒收)。"""
    added = updated = 0
    rejected = []
    for name, e in entries.items():
        errs = validate_entry(name, e)
        if errs:
            rejected.append(f"{name}: " + "；".join(errs))
            continue
        cur = db["models"].get(name)
        if cur is None:
            db["models"][name] = e
            added += 1
        else:
            for k, v in e.items():
                if v is not None and not cur.get(k):
                    cur[k] = v
                elif v is not None and k in ("note", "tech", "src", "url"):
                    cur[k] = v          # 文本类字段允许更新
            updated += 1
    return added, updated, rejected


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--todo", action="store_true", help="列出未沉淀车型与要补的字段")
    ap.add_argument("--show", action="store_true", help="覆盖率与置信度分布")
    ap.add_argument("--add", help="待入库 JSON（{车名: {...}}），校验通过才合并")
    ap.add_argument("--filings", help="本期 filings json（--todo 数据源，缺省取最新）")
    ap.add_argument("--export-template", help="导出待填模板到该路径")
    ap.add_argument("--notes", default=NOTES_PATH)
    a = ap.parse_args()

    db = load(a.notes)
    models = db.get("models") or {}

    # 数据源：显式给 --filings，否则找 runs/_filings 里最新的 filings_*.json
    fpath = a.filings
    if not fpath:
        cand_dir = os.path.join(HERE, "runs", "_filings")
        fs = sorted((f for f in os.listdir(cand_dir)
                     if f.startswith("filings_") and f.endswith(".json")),
                    reverse=True) if os.path.isdir(cand_dir) else []
        if fs:
            fpath = os.path.join(cand_dir, fs[0])
    items = []
    if fpath and os.path.exists(fpath):
        fl = json.load(open(fpath, encoding="utf-8"))
        items = fl.get("items") or []
        print(f"[数据源] {os.path.basename(fpath)}（第{fl.get('batch')}批）")

    if a.todo:
        todo = []
        for it in items:
            name = it.get("name")
            have = models.get(name) or {}
            miss = need(it, have)
            if miss:
                todo.append((name, miss, search_hint(it), have.get("conf")))
        print(f"\n## 待沉淀 {len(todo)}/{len(items)} 款\n")
        print("| 车型 | 现有conf | 还缺 | 建议检索词 |")
        print("|---|---|---|---|")
        for name, miss, hint, cf in todo:
            print(f"| **{name}** | {cf or '—'} | {'；'.join(miss)} | {hint} |")
        if a.export_template:
            tpl = {name: {"len_m": None, "price_expect": None, "launch": None,
                          "body": None, "cls": None, "tech": None,
                          "src": None, "url": None, "conf": None}
                   for name, _, _, _ in todo}
            json.dump(tpl, open(a.export_template, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            print(f"\n[ok] 模板 → {a.export_template}（填完 --add 入库）")
        return

    if a.show:
        n = len(items) or None
        confs = {}
        for m, e in models.items():
            confs[e.get("conf") or "?"] = confs.get(e.get("conf") or "?", 0) + 1
        covered = sum(1 for it in items if it.get("name") in models)
        print(f"notes 覆盖：{covered}/{len(items)} 款（本期批次）｜"
              f"库内共 {len(models)} 款｜conf 分布 {confs}")
        return

    if a.add:
        entries = json.load(open(a.add, encoding="utf-8"))
        added, updated, rejected = merge(db, entries)
        save(db, a.notes)
        print(f"[ok] 入库 {a.notes}：新增 {added}、更新 {updated}、拒收 {len(rejected)}")
        for r in rejected:
            print(f"  ✗ {r}")
        if rejected:
            sys.exit(1)
        return

    ap.print_help()


if __name__ == "__main__":
    main()
