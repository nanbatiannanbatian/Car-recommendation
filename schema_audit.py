#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐配置字段规范对照（schema_audit.py）——借鉴 open-ev-data 的分层 schema。

背景（v9.7.39）
---------------
调研发现 open-ev-data/open-ev-data-dataset（CDLA-Permissive）用「分层规范数据集
（LCD）+ 严格 JSON Schema + Base→Year→Trim 继承」管 EV 配置，字段覆盖到电池化学、
电压、GB/T 充电、CLTC、V2X。它的价值不在数据本身（中国车型覆盖待核），而在
**字段规范**：让我们能回答「trims.json 到底缺哪些展示/核验字段，缺多少」。

本脚本做三件事（纯标准库，不改数据、不进分）：
  1. 定义逐配置字段规范 SCHEMA：core（出榜必填）/ recommended（规范推荐）/ provenance（证据溯源）
  2. 对照 categories/trims.json 统计每个字段的覆盖率（按配置数、按车型数两种口径）
  3. 输出人类可读报告 + JSON；--check 可对低于阈值的字段发非零退出码（接出榜门禁）

它和 trim_coverage.py 的分工：
  - trim_coverage.py：校验「候选池每款车有没有配置、核心字段有没有值」（出榜硬门禁）
  - schema_audit.py：审计「配置库整体字段规范覆盖度」（治理/补采优先级，不硬拦出榜）

用法
----
  python3 schema_audit.py --report
  python3 schema_audit.py --report --out runs/schema_audit.md
  python3 schema_audit.py --check --min-coverage 0.90     # core 字段低于90%则 exit 1
  python3 schema_audit.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
TRIMS = os.path.join(HERE, "categories", "trims.json")
FACTS = os.path.join(HERE, "categories", "fact_library.json")

# 字段规范：level 决定强制程度；source 标注借鉴自哪里
# core        = 出榜展示/铁律必填（与 trim_coverage.REQUIRED_FIELDS 对齐，另加电芯）
# recommended = open-ev-data 规范里有、本项目展示需要但当前非必填的字段
# provenance  = 可核验证据链（R1：platform+url+date+claim）
SCHEMA = {
    "name":          {"level": "core",        "source": "本项目 trims.json"},
    "price":         {"level": "core",        "source": "本项目 trims.json"},
    "battery_kwh":   {"level": "core",        "source": "open-ev-data: capacity(gross/net)"},
    "cltc_km":       {"level": "core",        "source": "open-ev-data: range(CLTC)"},
    "cell_supplier": {"level": "core",        "source": "本项目铁律 v9.7.33 电芯必填"},
    "battery_type":  {"level": "recommended", "source": "open-ev-data: chemistry"},
    "charge":        {"level": "recommended", "source": "open-ev-data: DC fast charging/power"},
    "voltage_platform": {"level": "recommended", "source": "open-ev-data: voltage class(400V/800V)"},
    "motor_kw":      {"level": "recommended", "source": "open-ev-data: powertrain power"},
    "key_configs":   {"level": "recommended", "source": "本项目 trims.json"},
    "energy_type":   {"level": "recommended", "source": "open-ev-data: powertrain type"},
    "cell_source":   {"level": "provenance",  "source": "本项目 R1 证据链(配置级电芯来源)"},
    "charge_source": {"level": "provenance",  "source": "本项目 R1 证据链(配置级快充来源)"},
    "quote":         {"level": "provenance",  "source": "本项目 R1 引文"},
}


def load_trims(path: str | None = None) -> list[dict]:
    p = path or TRIMS
    if not os.path.exists(p):
        raise SystemExit("找不到 trims.json：%s" % p)
    d = json.load(open(p, encoding="utf-8"))
    return d.get("entries", [])


def _present(v) -> bool:
    """字段是否算「有值」：非空字符串/list/dict/数字（0 也算缺失语义上给 False）。"""
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip() not in ("", "-", "—", "未披露", "待核", "N/A")
    if isinstance(v, (list, dict)):
        return len(v) > 0
    return bool(v)


def audit(entries: list[dict], facts: dict | None = None) -> dict:
    """统计每个规范字段在配置级、车型级的覆盖率。

    facts 用于电芯回退：与 trim_coverage.py / main_cell 展示链一致——
    配置级 cell_supplier 缺失时，允许车系级 fact_library[m].cell_brand 兜底。
    """
    facts = facts or {}
    n_trim = sum(len(e.get("trims", [])) for e in entries)
    n_model = len(entries)
    trim_hit = Counter()
    model_hit = Counter()
    fallback_hit = Counter()
    for e in entries:
        trims = e.get("trims", [])
        model_name = e.get("model")
        for fld in SCHEMA:
            # 车型级：任一配置有该字段，或车系级字段有值
            model_val = _present(e.get(fld)) or any(
                _present(t.get(fld)) for t in trims)
            # 电芯专属回退：事实库车系级 cell_brand
            if fld == "cell_supplier" and not model_val:
                fv = (facts.get(model_name) or {}).get("cell_brand")
                if _present(fv):
                    model_val = True
                    fallback_hit[fld] += 1
            if model_val:
                model_hit[fld] += 1
            for t in trims:
                if _present(t.get(fld)):
                    trim_hit[fld] += 1
    fields = {}
    for fld, meta in SCHEMA.items():
        fields[fld] = {
            "level": meta["level"],
            "source": meta["source"],
            "trim_coverage": (trim_hit[fld] / n_trim) if n_trim else 0.0,
            "model_coverage": (model_hit[fld] / n_model) if n_model else 0.0,
            "trim_count": trim_hit[fld],
            "model_count": model_hit[fld],
            "model_fallback_count": fallback_hit[fld],
        }
    core_mean = (sum(f["model_coverage"] for f in fields.values()
                     if f["level"] == "core") /
                 max(1, sum(1 for f in fields.values() if f["level"] == "core")))
    return {"n_models": n_model, "n_trims": n_trim, "fields": fields,
            "core_model_coverage_mean": core_mean}


def build_report(result: dict, min_coverage: float) -> str:
    lines = [
        "# 逐配置字段规范对照报告（schema_audit）",
        "",
        "借鉴 open-ev-data 分层字段规范（CDLA-Permissive）。只读审计，不写回、不进分。",
        "",
        f"- 车型数：**{result['n_models']}** ｜ 配置数：**{result['n_trims']}**",
        f"- core 字段车型级平均覆盖：**{result['core_model_coverage_mean']*100:.1f}%**",
        "",
        "| 字段 | 级别 | 配置覆盖 | 车型覆盖 | 规范来源 |",
        "|---|---|---|---|---|",
    ]
    order = {"core": 0, "recommended": 1, "provenance": 2}
    for fld, f in sorted(result["fields"].items(),
                         key=lambda kv: (order[kv[1]["level"]], -kv[1]["model_coverage"])):
        flag = " ⚠" if f["level"] == "core" and f["model_coverage"] < min_coverage else ""
        fb = f" [含车系级回退{f['model_fallback_count']}]" if f.get("model_fallback_count") else ""
        lines.append(
            f"| {fld}{flag} | {f['level']} | {f['trim_coverage']*100:.1f}% "
            f"({f['trim_count']}) | {f['model_coverage']*100:.1f}% "
            f"({f['model_count']}){fb} | {f['source']} |"
        )
    lines += ["", "## 补采优先级", ""]
    gaps = [(fld, f) for fld, f in result["fields"].items()
            if f["model_coverage"] < min_coverage]
    gaps.sort(key=lambda kv: (order[kv[1]["level"]], kv[1]["model_coverage"]))
    if not gaps:
        lines.append(f"所有字段车型级覆盖均 ≥ {min_coverage*100:.0f}%。")
    else:
        for fld, f in gaps:
            lines.append(
                f"- [{f['level']}] `{fld}`：车型覆盖 {f['model_coverage']*100:.1f}%，"
                f"配置覆盖 {f['trim_coverage']*100:.1f}% → 建议优先补采")
    return "\n".join(lines)


def selftest() -> int:
    ok = True
    entries = [
        {"model": "A", "cell_supplier": "宁德时代", "trims": [
            {"name": "低配", "price": 10, "battery_kwh": 50, "cltc_km": 400,
             "charge": "400V"},
            {"name": "高配", "price": 12, "battery_kwh": 60, "cltc_km": 500, "charge": ""},
        ]},
        {"model": "B", "trims": [
            {"name": "标准", "price": 9, "battery_kwh": 45, "cltc_km": 350, "charge": "800V"},
        ]},
    ]
    r = audit(entries)
    c1 = r["n_models"] == 2 and r["n_trims"] == 3
    print(f"  [1] 计数 车型={r['n_models']} 配置={r['n_trims']}", c1)
    ok &= c1
    f = r["fields"]
    c2 = f["price"]["trim_coverage"] == 1.0
    print(f"  [2] price 配置覆盖={f['price']['trim_coverage']:.2f}", c2)
    ok &= c2
    # charge 字段：A 两配置一个空，B 有值 → 2/3；空串不算有值
    c3 = abs(f["charge"]["trim_coverage"] - 2 / 3) < 1e-9
    print(f"  [3] charge 配置覆盖={f['charge']['trim_coverage']:.2f}（空串不计）", c3)
    ok &= c3
    # voltage_platform 未出现，应为 0
    c4 = f["voltage_platform"]["trim_coverage"] == 0.0
    print(f"  [4] voltage_platform 覆盖={f['voltage_platform']['trim_coverage']:.2f}", c4)
    ok &= c4
    # cell_supplier：A 车系级有值 → A 命中；B 无 → 车型覆盖 1/2
    c5 = abs(f["cell_supplier"]["model_coverage"] - 0.5) < 1e-9
    print(f"  [5] cell_supplier 车型覆盖={f['cell_supplier']['model_coverage']:.2f}（车系级回退）", c5)
    ok &= c5
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trims", default=TRIMS, help="trims.json 路径")
    ap.add_argument("--report", action="store_true", help="输出人类可读报告（默认行为）")
    ap.add_argument("--out", default="", help="报告写文件（Markdown）")
    ap.add_argument("--json-out", default="", help="结构化结果写 JSON")
    ap.add_argument("--check", action="store_true", help="core 字段车型覆盖低于阈值则 exit 1")
    ap.add_argument("--min-coverage", type=float, default=0.90)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    entries = load_trims(a.trims)
    facts = {}
    facts_path = os.path.join(os.path.dirname(a.trims or TRIMS), "fact_library.json")
    if os.path.exists(facts_path):
        facts = json.load(open(facts_path, encoding="utf-8"))
    r = audit(entries, facts)
    report = build_report(r, a.min_coverage)
    if a.out:
        with open(os.path.abspath(a.out), "w", encoding="utf-8") as f:
            f.write(report + "\n")
    if a.json_out:
        with open(os.path.abspath(a.json_out), "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=2)
    if a.out or a.json_out:
        print("报告已写入输出文件")
    else:
        print(report)
    if a.check:
        core = {fld: f for fld, f in r["fields"].items() if f["level"] == "core"}
        below = {fld: f["model_coverage"] for fld, f in core.items()
                 if f["model_coverage"] < a.min_coverage}
        if below:
            print("\n[CHECK FAIL] core 字段车型覆盖低于 %.0f%%：" % (a.min_coverage*100),
                  file=sys.stderr)
            for fld, cov in below.items():
                print(f"  {fld}: {cov*100:.1f}%", file=sys.stderr)
            return 1
        print("\n[CHECK PASS] 所有 core 字段车型覆盖 ≥ %.0f%%" % (a.min_coverage*100))
    return 0


if __name__ == "__main__":
    sys.exit(main())
