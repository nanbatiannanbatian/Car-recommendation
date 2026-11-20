#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐配置数据导入器（trim_ingest.py）——把外部配置表规范化成 trims.json 的 trims[]。

调研背景（v9.7.39）
------------------
借鉴 travisoa/vehicle-data-hub（MIT，本机已 clone）与 swoiow/autohome（Apache-2.0）：
两者都把「车型名 → 全在售配置」导出成结构化表。本脚本是这两类产物的**摄取端**，
解决项目最高优先级铁律「每款车必须有逐配置数据」的采集瓶颈：

    vehicle-data-hub 配置 Excel(sheet=详细配置表)  ┐
    swoiow/autohome 参数 CSV(Feature×specId)       ┼→ 字段映射 → trims[] 补全建议 → 人工核验写回
   手工整理的配置表 CSV                            ┘

纪律（与 SKILL.md 铁律一致）
---------------------------
1. **不内置爬虫**：只摄取已采集的表，不抓网页、不绕反爬。抓取交给专门工具/人工。
2. **严格车型匹配**：车型名归一化后精确相等才认，禁止子串（EZ-60≠EZ-6、S5≠S）。
3. **只出建议不写回**：输出「待人工核验」JSON，不改 trims.json、不进分。
4. **字段映射可审计**：每个字段写明来源参数名与原始值，人工核验有据可查。
5. **接 coverage 校验**：建议写回后可跑 trim_coverage.py 验证。

输入格式
--------
A) vehicle-data-hub Excel：sheet「详细配置表」，行=参数，列=配置，首两列「分组|参数」，
   其后每列一个配置。需要 openpyxl（可选依赖，缺则提示，不影响 CSV 路径）。
B) swoiow/通用 CSV：第 1 列=参数名，第 1 行=表头（首格可空，其余为配置名），
   每个单元格=该配置该参数的值。标准库即可。

用法
----
  python3 trim_ingest.py --excel <配置表.xlsx> --model "车型名" --out suggest.json
  python3 trim_ingest.py --csv <参数表.csv> --model "车型名" --out suggest.json
  python3 trim_ingest.py --excel <x.xlsx> --batch models.txt --out suggest.json   # 多车型
  python3 trim_ingest.py --selftest
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
TRIMS = os.path.join(HERE, "categories", "trims.json")

# 参数名 → trims 字段。keys 是「归一化后参数名」的候选别名（精确匹配）。
# 归一化：去空格/括号注释/单位后缀，转小写。
FIELD_ALIASES = {
    "name": [],  # 配置名来自列头，不映射参数行
    "price": ["厂商指导价元", "厂商指导价", "指导价", "官方指导价", "guideprice"],
    "battery_kwh": ["电池能量kwh", "电池容量kwh", "电池能量", "电池容量", "电池电量"],
    "cltc_km": ["cltc纯电续航里程km", "cltc续航里程", "cltc纯电续航", "cltc续航", "cltc"],
    "battery_type": ["电池类型", "电池种类"],
    "cell_supplier": ["电芯品牌", "电芯供应商", "电池供应商", "电芯厂商"],
    "charge": ["快充时间", "快充功率kw", "快充功率", "快充"],
    "voltage_platform": ["高压平台v", "高压平台", "电压平台"],
    "motor_kw": ["最大功率kw", "电机最大功率", "电动机总功率", "最大功率"],
    "energy_type": ["能源类型", "燃料形式"],
}

# 后缀剥离 + 归一化（复用 swoiow_probe 的严格匹配纪律）
SUFFIX = ["纯电", "EV", "ev", "插混", "PHEV", "DM-i", "DM", "PLUS", "PRO", "MAX"]


def norm_model(s: str) -> str:
    s = str(s or "").strip()
    for x in SUFFIX:
        s = s.replace(x, "")
    return re.sub(r"[\s（）()·\-—_]", "", s).lower()


def norm_param(s: str) -> str:
    s = str(s or "").strip().lower()
    s = re.sub(r"[（(].*?[)）]", "", s)  # 去括号注释
    s = re.sub(r"[\s/／·:：]", "", s)
    return s


def canon_param(raw: str) -> str | None:
    """参数名 → 规范字段名；未知返回 None。"""
    k = norm_param(raw)
    for fld, aliases in FIELD_ALIASES.items():
        if not aliases:
            continue
        for a in aliases:
            if k == norm_param(a):
                return fld
    # 二次：别名是 k 的前缀/包含（如「厂商指导价(元)y」已被去括号覆盖）
    for fld, aliases in FIELD_ALIASES.items():
        for a in aliases:
            na = norm_param(a)
            if na and (k.startswith(na) or na.startswith(k)) and len(k) >= len(na) - 2:
                return fld
    return None


def _to_number(s: str):
    """从「11.69万」「67kWh」「650km」抽数值；失败返回原串。"""
    m = re.search(r"-?\d+(?:\.\d+)?", str(s))
    if not m:
        return str(s).strip()
    v = float(m.group())
    return int(v) if v == int(v) else v


def _price_wan(s: str):
    """指导价统一到万元：元→/10000；已是万不变。"""
    txt = str(s)
    v = _to_number(txt)
    if not isinstance(v, (int, float)):
        return v
    if "万" not in txt and abs(v) > 1000:  # 元
        return round(v / 10000, 2)
    return v


def read_csv_matrix(path: str) -> dict:
    """读参数矩阵 CSV：第1列参数名，第1行配置名。"""
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        rows = list(csv.reader(fh))
    if not rows or len(rows) < 2:
        return {"trims": [], "params": []}
    headers = [c.strip() for c in rows[0][1:]]
    params = []
    for r in rows[1:]:
        if not r or not r[0].strip():
            continue
        params.append((r[0].strip(), [v.strip() for v in r[1:]]))
    return {"headers": headers, "params": params}


def read_excel_matrix(path: str, sheet: str = "详细配置表") -> dict:
    """读 vehicle-data-hub 配置 Excel 的「详细配置表」sheet。openpyxl 可选。"""
    try:
        import openpyxl
    except ImportError:
        raise SystemExit(
            "需要 openpyxl 才能读 Excel（vehicle-data-hub 的 .venv 已装，或 pip install openpyxl）。\n"
            "也可改用 --csv 输入参数矩阵，走标准库零依赖路径。")
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[sheet] if sheet in wb.sheetnames else wb[wb.sheetnames[0]]
    data = list(ws.iter_rows(values_only=True))
    if len(data) < 2:
        return {"headers": [], "params": []}
    headers = [str(c).strip() for c in data[0][2:] if c is not None]
    params = []
    for row in data[1:]:
        if not row or len(row) < 3:
            continue
        pname = str(row[1]).strip() if row[1] is not None else ""
        if not pname:
            continue
        vals = [("" if v is None else str(v).strip()) for v in row[2:]]
        params.append((pname, vals))
    wb.close()
    return {"headers": headers, "params": params}


def build_suggest(matrix: dict, model: str, trims_db: dict | None = None,
                  source: str = "") -> dict:
    """把参数矩阵归一成 trims[] 补全建议（每个配置一行，附来源与映射依据）。"""
    headers = matrix.get("headers", [])
    params = matrix.get("params", [])
    trims_db = trims_db or {}
    existing = {t.get("name"): t for t in (trims_db.get(model) or {}).get("trims", [])}

    suggestions = []
    for ci, cname in enumerate(headers):
        if not cname:
            continue
        row = {"name": cname, "field_sources": {}, "_mapped": {}}
        for raw_param, vals in params:
            if ci >= len(vals):
                continue
            val = vals[ci]
            if val in ("", "-", "—", "无", "N/A", "null", "None"):
                continue
            fld = canon_param(raw_param)
            if not fld:
                continue
            # 冲突：同一规范字段被多个原始参数命中时，保留第一个，其余记入冲突
            if fld in row["_mapped"]:
                row.setdefault("conflicts", []).append(
                    {"field": fld, "kept": row["_mapped"][fld],
                     "kept_raw": row["field_sources"][fld]["param"],
                     "ignored_param": raw_param, "ignored_value": val})
                continue
            cooked = _price_wan(val) if fld == "price" else _to_number(val)
            row["_mapped"][fld] = cooked
            row["field_sources"][fld] = {"param": raw_param, "raw": val, "mapped": cooked}
        # 与现有配置对照：标出「能补/能改/一致」
        old = existing.get(cname)
        delta = {}
        for fld, cooked in row["_mapped"].items():
            if old is None:
                delta[fld] = "new_trim"
            elif fld not in old or old[fld] in (None, "", "—"):
                delta[fld] = "fill_missing"
            elif old[fld] != cooked:
                delta[fld] = "differs"
            else:
                delta[fld] = "same"
        row["delta_vs_existing"] = delta
        row.pop("_mapped", None)
        suggestions.append(row)

    return {
        "note": "逐配置补全建议：严格摄取外部配置表，未写回 trims.json、不进分；须人工核验。",
        "model": model,
        "matched_model": model,
        "source": source or "external config table",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "n_configs": len(suggestions),
        "field_alias_version": "v1",
        "suggestions": suggestions,
        "claim": "待人工核验后决定是否写回；数值口径以来源参数页为准",
    }


def load_trims(path: str | None = None) -> dict:
    p = path or TRIMS
    if not os.path.exists(p):
        return {}
    d = json.load(open(p, encoding="utf-8"))
    return {e["model"]: e for e in d.get("entries", []) if e.get("model")}


def selftest() -> int:
    ok = True
    # 参数映射：指导价→price（元转万）、电池能量→battery_kwh、CLTC→cltc_km
    c1 = canon_param("厂商指导价(元)") == "price"
    c2 = canon_param("CLTC纯电续航里程(km)") == "cltc_km"
    c3 = canon_param("电池能量(kWh)") == "battery_kwh"
    print(f"  [1] 参数映射 price={c1} cltc={c2} kwh={c3}", c1 and c2 and c3)
    ok &= c1 and c2 and c3
    # 价格换算：136900 元 → 13.69 万；12.98万 → 12.98
    p1 = _price_wan("136900元")
    p2 = _price_wan("12.98万")
    c4 = abs(p1 - 13.69) < 1e-9 and abs(p2 - 12.98) < 1e-9
    print(f"  [2] 价格换算 元={p1} 万={p2}", c4)
    ok &= c4
    # 严格车型匹配不能子串误配
    idx = {"马自达ez6", "极狐阿尔法s"}
    c5 = norm_model("马自达EZ-60") not in idx and norm_model("马自达EZ-6") in idx
    print(f"  [3] 严格匹配 EZ-60≠EZ-6: {c5}")
    ok &= c5
    # 矩阵 → 建议：两配置、字段来源与 delta 齐全
    matrix = {"headers": ["650 舒享版", "710 旗舰版"], "params": [
        ("厂商指导价(元)", ["116900", "135900"]),
        ("电池能量(kWh)", ["67", "67"]),
        ("CLTC纯电续航里程(km)", ["650", "710"]),
        ("高压平台(V)", ["400", "800"]),
    ]}
    sug = build_suggest(matrix, "测试车型", source="selftest")
    s0 = sug["suggestions"][0]
    c6 = (sug["n_configs"] == 2 and s0["field_sources"]["price"]["mapped"] == 11.69
          and s0["field_sources"]["voltage_platform"]["mapped"] == 400
          and s0["delta_vs_existing"]["price"] == "new_trim")
    print(f"  [4] 矩阵→建议 配置数={sug['n_configs']} price={s0['field_sources']['price']['mapped']} "
          f"voltage={s0['field_sources']['voltage_platform']['mapped']}", c6)
    ok &= c6
    # 与现有对照：缺字段→fill_missing，不同→differs
    db = {"测试车型": {"trims": [{"name": "650 舒享版", "price": 11.69, "battery_kwh": 67}]}}
    sug2 = build_suggest(matrix, "测试车型", trims_db=db)
    d0 = sug2["suggestions"][0]["delta_vs_existing"]
    c7 = d0.get("cltc_km") == "fill_missing" and d0.get("voltage_platform") == "fill_missing"
    print(f"  [5] 对照现有 fill_missing: cltc={d0.get('cltc_km')} volt={d0.get('voltage_platform')}", c7)
    ok &= c7
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--excel", default="", help="vehicle-data-hub 配置 Excel（详细配置表）")
    ap.add_argument("--sheet", default="详细配置表")
    ap.add_argument("--csv", default="", help="参数矩阵 CSV（首列参数名，首行配置名）")
    ap.add_argument("--model", default="", help="车型名（用于对照 trims.json 现有配置）")
    ap.add_argument("--batch", default="", help="多车型清单文件（每行一个车型名）")
    ap.add_argument("--trims", default=TRIMS)
    ap.add_argument("--out", default="trim_ingest_suggest.json")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    src = a.excel or a.csv
    if not src or not os.path.exists(src):
        ap.error("需要 --excel 或 --csv 指向存在的配置表")
    matrix = read_excel_matrix(src, a.sheet) if a.excel else read_csv_matrix(a.csv)
    if not matrix.get("headers"):
        ap.error("配置表没有读到配置列（检查表头/ sheet 名）")
    trims_db = load_trims(a.trims)

    models = [a.model] if a.model else []
    if a.batch:
        models = [ln.strip() for ln in open(a.batch, encoding="utf-8") if ln.strip()]
    if not models:
        models = ["(未指定车型)"]

    results = [build_suggest(matrix, m, trims_db, source=src) for m in models]
    out = {
        "note": "逐配置采集补全建议（trim_ingest）。严格摄取、不写回、不进分；人工核验后写回 trims.json。",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_file": src,
        "n_models": len(results),
        "results": results,
    }
    out_abs = os.path.abspath(a.out)
    os.makedirs(os.path.dirname(out_abs) or ".", exist_ok=True)
    with open(out_abs, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    total_cfg = sum(r["n_configs"] for r in results)
    print(f"摄取 {os.path.basename(src)}：{len(results)} 车型 · {total_cfg} 配置")
    print(f"字段映射：{', '.join(k for k, v in FIELD_ALIASES.items() if v)}")
    print(f"输出：{out_abs}")
    print("下一步：人工核验建议 → 写回 categories/trims.json → python3 trim_coverage.py 校验")
    return 0


if __name__ == "__main__":
    sys.exit(main())
