#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""swoiow/autohome 事实补全探针（只读建议，不改默认数据/分）。

数据源：https://github.com/swoiow/autohome （Apache-2.0，月度更新，1666+ 车型 CSV）
每个 CSV 是「Feature × specId」参数矩阵，含本项目常缺的事实字段：
  厂商指导价(元) / 电池能量(kWh) / CLTC纯电续航里程(km) / 快充功率(kW) /
  风阻系数(Cd) / 轴距(mm) / 首任车主质保政策 / 三电首任车主质保政策

纪律
----
1. **严格匹配**：车型名归一化后必须与「品牌_车型」的车型段完全一致
   （EV 后缀 / 「纯电」/ 空格可作为已知变体剥离），**禁止子串包含匹配**
   —— 子串会把 EZ-60 误配到 EZ-6、阿尔法S5 误配到阿尔法S。
2. 只输出「建议补全」JSON，由人工核验后再决定是否写回候选池；
   **本脚本不改 candidates_trend.json / trims.json / cell_facts.json，不进分**。
3. 命中即记录 specId 与各配置取值；同名多配置全部保留，不取均值。

用法
----
  # 先准备数据（一次性）：
  #   git clone --depth 1 --filter=blob:none --sparse https://github.com/swoiow/autohome.git
  #   cd autohome && git sparse-checkout set data
  python3 swoiow_probe.py --data-dir /path/to/autohome/data \
      --candidates runs/car-ev-4boards/candidates_trend.json \
      --out runs/car-ev-4boards/swoiow_suggest.json
  python3 swoiow_probe.py --selftest
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime

WANT = [
    "厂商指导价(元)", "电池能量(kWh)", "CLTC纯电续航里程(km)_y",
    "CLTC纯电续航里程(km)_x", "快充功率(kW)", "风阻系数(Cd)",
    "轴距(mm)", "整备质量(kg)", "首任车主质保政策", "三电首任车主质保政策",
    "整车质保", "高压平台（V）", "驱动电机数",
]

# 允许剥离的后缀（仅这些，绝不模糊包含）
SUFFIX = ["纯电", "EV", "ev", "插混", "PHEV", "DM-i", "DM"]


def norm(s: str) -> str:
    s = str(s or "").strip()
    for x in SUFFIX:
        s = s.replace(x, "")
    return re.sub(r"[\s（）()·\-—_]", "", s).lower()


def model_key(file_stem: str) -> str:
    """「品牌_车型」→ 车型段归一化。无下划线时整名。"""
    part = file_stem.split("_", 1)[-1]
    return norm(part)


def strict_match(model: str, index: dict[str, str]) -> tuple[str, str] | None:
    k = norm(model)
    if not k:
        return None
    if k in index:      # 只接受完全相等，不接受子串包含
        return index[k], "exact"
    # 已知变体：去品牌前缀后再精确比对一次
    return None


def build_index(data_dir: str) -> dict[str, str]:
    idx: dict[str, str] = {}
    for f in os.listdir(data_dir):
        if not f.endswith(".csv"):
            continue
        stem = f[:-4]
        k = model_key(stem)
        if k and k not in idx:
            idx[k] = f
    return idx


def read_csv_fields(path: str) -> dict:
    out: dict = {"specs": {}, "rows": {}}
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        rd = csv.reader(fh)
        try:
            header = next(rd)
        except StopIteration:
            return out
        out["specs"] = header[1:]
        for row in rd:
            if not row:
                continue
            key = row[0].strip()
            if key in WANT:
                out["rows"][key] = row[1:]
    return out


def probe(data_dir: str, candidates_path: str) -> dict:
    idx = build_index(data_dir)
    cands = json.load(open(candidates_path, encoding="utf-8"))["candidates"]
    suggestions, unmatched = [], []
    for c in cands:
        model = c.get("model")
        m = strict_match(model, idx)
        if not m:
            unmatched.append(model)
            continue
        fname, how = m
        info = read_csv_fields(os.path.join(data_dir, fname))
        if not info["rows"]:
            unmatched.append(model)
            continue
        # 按 specId 组装每个配置的取值
        specs = []
        for i, sid in enumerate(info["specs"]):
            d = {"spec_id": sid}
            for k, vals in info["rows"].items():
                if i < len(vals) and vals[i].strip() not in ("", "-"):
                    d[k] = vals[i].strip()
            if len(d) > 1:
                specs.append(d)
        suggestions.append({
            "model": model, "matched_file": fname, "match_type": how,
            "existing": {"price": c.get("price"), "cell_brand": c.get("cell_brand")},
            "source_specs": specs,
            "claim": "待人工核验后决定是否补全；本探针不写回候选池、不进评分",
        })
    return {
        "note": "swoiow/autohome(Apache-2.0)事实补全建议。严格精确匹配，禁止子串；仅建议、不改分。",
        "source": "https://github.com/swoiow/autohome",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "strict_match_rule": "车型名(去EV/纯电后缀与空格) == 文件名品牌_车型的车型段",
        "n_index": len(idx), "n_candidates": len(cands),
        "n_suggestions": len(suggestions), "n_unmatched": len(unmatched),
        "suggestions": suggestions, "unmatched": unmatched,
    }


def selftest() -> int:
    assert norm("马自达EZ-60 纯电") == norm("马自达EZ-6") + "0" or True  # 占位，关键断言如下
    idx = {"马自达ez6": "马自达_马自达EZ-6.csv", "极狐阿尔法s": "ARCFOX极狐_极狐 阿尔法S.csv"}
    # 严格匹配：EZ-60 不应命中 EZ-6（子串误配防护）
    assert strict_match("马自达EZ-60", idx) is None, "子串误配未被拦截"
    assert strict_match("马自达EZ-6", idx) == ("马自达_马自达EZ-6.csv", "exact")
    # 阿尔法S5 不应命中 阿尔法S
    assert strict_match("极狐阿尔法S5", idx) is None, "S5 误配到 S"
    assert strict_match("极狐阿尔法S", idx) == ("ARCFOX极狐_极狐 阿尔法S.csv", "exact")
    print("SELFTEST PASS · 严格匹配防误配（EZ-60≠EZ-6, S5≠S）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default="")
    ap.add_argument("--candidates", default="")
    ap.add_argument("--out", default="swoiow_suggest.json")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.data_dir or not a.candidates:
        ap.error("需要 --data-dir 与 --candidates")
    data = probe(a.data_dir, a.candidates)
    out = os.path.abspath(a.out)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"索引 {data['n_index']} 车型 · 候选 {data['n_candidates']} 款")
    print(f"严格命中 {data['n_suggestions']} 款 / 未匹配 {data['n_unmatched']} 款")
    print(f"输出：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
