#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""级别自动拉取（auto_levels.py）——把 levels.json 的「人工攒」变「自动补」。

原理：OpenCLI 的 autohome score 接口直接返回官方二级分类（level 字段，
fetch_specs.py 早已在用）。本模块把它接到 levels.json：
  1. 对候选池里 levels.json 还没有的车，先用 autohome brand 列表换出
     汽车之家 series_id（与懂车帝 ID 不同体系，必须换）；
  2. score_series(aid) 取 level；
  3. level 在官方词表内 → 写入 levels.json，标 source/日期；
  4. 与已有值冲突 → 只报告不覆盖（老值是人工确认过的）。

注意：这不是"拿第三方猜测替代官方口径"——汽车之家展示的级别本身就是
厂商申报的官方二级分类，与 align_specs 的口径一致；自动写入后仍走
admit.py 的同一道闸门。

用法：
    python3 auto_levels.py --pool categories/baseline_car_ev.json
    python3 auto_levels.py --models "奕派EP7,7X"      # 只查指定车型
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)
from modules.research import fetch_specs as FS
from modules.research import filings as F

LEVELS_PATH = os.path.join(HERE, "levels.json")
CLASSES_OK = {"微型车", "小型车", "紧凑型车", "中型车", "中大型车", "大型车",
              "小型SUV", "紧凑型SUV", "中型SUV", "中大型SUV", "大型SUV",
              "小型MPV", "紧凑型MPV", "中型MPV", "中大型MPV", "大型MPV",
              "微面", "皮卡"}
BRAND_OF_MODEL = {   # 车名 → autohome brand 检索词（宽松前缀，够用即可）
    "比亚迪": ["海豹", "海狮", "汉", "秦", "宋", "元", "海豚", "海鸥"],
    "吉利": ["银河", "星愿"],
    "奇瑞": ["风云", "iCAR"],
    "上汽": ["MG", "荣威", "智己", "大众", "ID."],
    "广汽": ["AION", "埃安"],
    "东风": ["东风", "奕派", "岚图", "风神", "纳米"],
    "北汽": ["极狐"],
    "长城": ["欧拉", "ORA"],
    "长安": ["深蓝", "启源"],
    "蔚来": ["乐道", "萤火虫"],
    "零跑": ["零跑"],
    "深蓝": ["深蓝汽车", "深蓝"],
    "奕派": ["eπ", "奕派科技", "奕派"],
    "小米": ["小米"],
    "一汽": ["奔腾"],
    "上汽通用五菱": ["五菱"],
    "极氪": ["极氪"],
    "领克": ["领克"],
}


def guess_brands(model: str) -> list[str]:
    out = []
    for b, keys in BRAND_OF_MODEL.items():
        if any(k.upper() in model.upper() for k in keys):
            out.append(b)
    if not out:                       # 没命中就整名当检索词
        out = [re.sub(r"[0-9EV]+$", "", model)[:4] or model]
    return out


import re                            # noqa: E402


def ah_lookup(model: str) -> dict | None:
    """通过 autohome brand 列表找到该车 → 换 aid → score_series 取 level。"""
    for brand in guess_brands(model):
        rows, err = FS.list_brand(brand, limit=100)
        if err or not rows:
            continue
        hit = F.find_in({F.norm_name(r["name"]): r for r in rows}, model)
        if not hit:
            continue
        row = {F.norm_name(r["name"]): r for r in rows}[hit]
        aid = row.get("series_id")
        if not aid:
            continue
        d, err2 = FS.score_series(aid)
        if err2 or not d:
            continue
        if F.norm_name(d.get("name") or "") and \
                abs(len(F.norm_name(d.get("name") or "")) - len(F.norm_name(model))) > 4 \
                and F.norm_name(model) not in F.norm_name(d.get("name") or ""):
            continue                  # 名称差太远，宁可不写
        return d
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default=str(paths.cat("baseline_car_ev.json")))
    ap.add_argument("--models", help="逗号分隔，只查指定车型")
    ap.add_argument("--levels", default=LEVELS_PATH)
    a = ap.parse_args()

    store = json.load(open(a.levels, encoding="utf-8"))
    levels = store.setdefault("levels", {})
    pool = json.load(open(a.pool, encoding="utf-8"))["candidates"]
    todo = ([m.strip() for m in a.models.split(",")] if a.models
            else [c["model"] for c in pool
                  if c.get("model") not in levels])

    print(f"[待查] {len(todo)} 款")
    ok = conflict = miss = 0
    for model in todo:
        d = ah_lookup(model)
        if not d or not d.get("level"):
            print(f"  ✗ {model}: 查不到（保持拒绝入池规则）")
            miss += 1
            continue
        level = d["level"]
        if level not in CLASSES_OK:
            print(f"  ✗ {model}: level={level} 不在词表")
            miss += 1
            continue
        old = levels.get(model)
        entry = {"cls": level, "power": "BEV",
                 "note": f"autohome via opencli {dt.date.today().isoformat()}"}
        if old and old.get("cls") and old["cls"] != level:
            conflict += 1
            print(f"  ⚠️ {model}: 已有 {old['cls']}（人工）vs autohome {level}，不覆盖")
            continue
        levels[model] = entry
        ok += 1
        print(f"  ✓ {model}: {level}")
    json.dump(store, open(a.levels, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"[ok] levels.json 新增 {ok} · 冲突 {conflict} · 查不到 {miss}，"
          f"现覆盖 {len(levels)} 款")


if __name__ == "__main__":
    main()
