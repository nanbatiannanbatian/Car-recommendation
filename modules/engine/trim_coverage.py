#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""出榜前逐配置覆盖校验（trim_coverage.py）——v9.7.9 铁律的可执行守门。

用户 2026-11-20 明确要求：按车型配置出榜，缺配置数据榜单参考意义大打折扣，
宁愿多花时间搜集也不少搜集。本工具在出榜前强制校验每款车都有 trims[] 逐配置数据。

用法：
    python3 trim_coverage.py --candidates runs/x/candidates.json
    python3 trim_coverage.py --candidates runs/x/candidates.json --strict   # 缺配置则 exit 1
    python3 trim_coverage.py --selftest
"""
from __future__ import annotations
import argparse
import json
import os
import sys

import paths

HERE = str(paths.ROOT)
TRIMS = str(paths.cat("trims.json"))
# v9.7.33：电芯提升为必需字段——此前"电芯缺只记warning"导致主榜11/20显示"—"却校验全绿。
# 铁律：能在主榜展示的字段必须先校验有值，不许"校验通过但展示空白"。
REQUIRED_FIELDS = ["name", "price", "battery_kwh", "cltc_km"]
# v9.7.33：电芯单独校验（配置级 或 事实库车系级 有值即可，与展示回退链一致）
CELL_FIELD = "cell_supplier"


def load_trims(path: str | None = None) -> dict[str, dict]:
    """读逐配置事实。**统一走 trims_store**（人工库 + 自动库合并读视图）。

    2026-11-22：原来这里直接 json.load(trims.json)，而人工条目在自动库里
    另有一份副本 → 读到哪份取决于文件状态，同一事实两个读法。
    现在人工与自动在存储层互斥，合流只在 trims_store 一处发生。
    path 参数保留：非空时读指定文件（测试/临时排查用）。
    """
    if path:
        if not os.path.exists(path):
            return {}
        d = json.load(open(path, encoding="utf-8"))
        return {e["model"]: e for e in d.get("entries", []) if e.get("model")}
    from modules.data.trims_store import load as _load
    return _load()


def check(cands_path: str, trims_path: str | None = None) -> dict:
    pool = json.load(open(cands_path, encoding="utf-8"))
    cands = pool.get("candidates", pool) if isinstance(pool, dict) else pool
    tdb = load_trims(trims_path)
    # v10.15.1 根因修复：原来 os.path.join(os.path.dirname(TRIMS), "fact_library.json")
    # 把人工事实库拼到 data/gen/（生成物目录），实际文件在 categories/ →
    # 7 款配置级无电芯但车系级有值的车被误报"无电芯来源"。
    # 数据落盘规则（SKILL.md）：读数据一律 paths.cat()，禁止拼路径。
    facts_path = paths.cat("fact_library.json")
    facts = json.load(open(facts_path, encoding="utf-8")) if os.path.exists(facts_path) else {}
    missing, incomplete, ok, no_cell = [], [], [], []
    for c in cands:
        m = c.get("model")
        e = tdb.get(m)
        if not e or not e.get("trims"):
            missing.append(m)
            continue
        # 检查配置字段完整度（至少一半配置有核心字段）
        good = sum(1 for t in e["trims"]
                   if all(t.get(f) not in (None, "", "—") for f in REQUIRED_FIELDS))
        if good < max(1, len(e["trims"]) // 2):
            incomplete.append((m, good, len(e["trims"])))
            continue
        # v9.7.33：电芯必须"配置级 或 车系事实库"至少一处有值（与 main_cell 回退链一致）
        cell_ok = any(t.get(CELL_FIELD) not in (None, "", "—") for t in e["trims"]) or \
                  str((facts.get(m) or {}).get("cell_brand") or "") not in ("", "—")
        if not cell_ok:
            no_cell.append(m)
        else:
            ok.append((m, len(e["trims"])))
    return {"total": len(cands), "ok": ok, "missing": missing,
            "incomplete": incomplete, "no_cell": no_cell}


def report(r: dict) -> str:
    L = [f"逐配置覆盖校验：{r['total']} 款候选",
         f"  ✓ 完整：{len(r['ok'])} 款",
         f"  ✗ 无配置数据：{len(r['missing'])} 款 —— {', '.join(r['missing']) or '无'}",
         f"  ⚠ 字段不全：{len(r['incomplete'])} 款 —— " +
         (', '.join(f"{m}({g}/{n})" for m, g, n in r['incomplete']) or '无')]
    L.append(f"  ✗ 无电芯来源：{len(r.get('no_cell', []))} 款 —— " +
             (', '.join(r.get('no_cell', [])) or '无'))
    if r["missing"] or r["incomplete"] or r.get("no_cell"):
        L.append("  ⛔ 出榜前必须补齐上述车型配置数据（trims.json / fact_library.json），或标注配置待核移出主榜。")
    else:
        L.append("  ✅ 全部车型配置齐全，可出榜。")
    return "\n".join(L)


def _uname(s):
    """车型名归一化：去空格、小写、去品牌前缀+动力后缀，用于 universe 比对。
    销量榜用「MG 07 EV/特斯拉Model 3/ZEEKR 007」，候选池用「MG 07 纯电/特斯拉Model 3/极氪007」——
    精确字符串会误报漏收，必须归一化。"""
    n = "".join(c for c in str(s).lower() if c.isalnum())
    # v10.62：补"纯电版/ev版/插混版/增程版"等后缀——
    # 旧版只处理"纯电"，"问界M7 纯电版"归一化后仍是"问界m7纯电版"，
    # 与权威清单"问界M7"匹配不上→误报漏收。
    for suf in ("纯电版", "ev版", "插混版", "增程版", "pureev", "evphev",
                "纯电", "bev", "phev", "dmi", "ev"):
        if n.endswith(suf) and len(n) > len(suf) + 2:
            n = n[:-len(suf)]
            break
    # 品牌前缀归一化：中英文品牌统一去掉（只留车型主体）
    for brand in ("特斯拉", "tesla", "zeekr", "极氪", "byd", "比亚迪", "上汽mg", "mg", "蔚来", "nio"):
        if n.startswith(brand) and len(n) > len(brand) + 2:
            n = n[len(brand):]
            break
    return n


def _extract_universe_names(u) -> tuple[list, str | None]:
    """从 universe JSON 提取车名列表——兼容多种历史格式（v10.62 根因修复）。

    背景（2026-11-24 漏收腾势N8 根因之一）：项目里两份 universe 用了不同字段名：
      · universe_car_midsuv_bev.json      → {"models": [...]}
      · universe_large_suv_bev.json       → {"names": [...]}
    旧版只读 models，读到 names 清单时 universe=0 → 门禁静默跳过、形同虚设。
    这里统一收口所有已知格式；读不到任何车名时返回 error，由调用方硬拦，绝不静默。
    """
    if isinstance(u, list):
        return u, None
    if not isinstance(u, dict):
        return [], f"universe 顶层类型非法：{type(u).__name__}"
    # 车名字段：names 优先（现成权威清单），其次 models / candidates / universe
    for key in ("names", "models", "candidates", "universe"):
        v = u.get(key)
        if isinstance(v, list) and v:
            # 元素可以是字符串，也可以是 {"model"/"name": ...} 对象
            out = []
            for x in v:
                if isinstance(x, str):
                    out.append(x)
                elif isinstance(x, dict):
                    nm = x.get("model") or x.get("name")
                    if nm:
                        out.append(nm)
            if out:
                return out, None
    return [], ("universe JSON 无任何车名（已试 names/models/candidates/universe 字段）——"
                "门禁拒绝静默跳过，请修清单格式")


def check_universe(cands_path: str, universe_path: str | None, excluded: list | None = None) -> dict:
    """候选池完整性盘点（v9.7.24）：对照权威车型清单，报出漏收的车型。
    universe json 格式：{"names":[...]} / {"models":[...]} / 纯 list，v10.62 全部兼容。
    v9.7.43：用归一化名称比对（去空格/EV后缀），避免别名误报漏收。
    v10.62：读不到车名时返回 error（不再静默返回空清单放过）。"""
    if not universe_path or not os.path.exists(universe_path):
        return {"available": False}
    u = json.load(open(universe_path, encoding="utf-8"))
    names, err = _extract_universe_names(u)
    if err:
        return {"available": True, "error": err, "universe": 0, "not_covered": []}
    universe = set(names)
    pool = json.load(open(cands_path, encoding="utf-8"))
    cands = pool.get("candidates", []) if isinstance(pool, dict) else pool
    inpool = {c.get("model") for c in cands}
    excluded = {x.get("model") for x in (excluded or [])}
    # 归一化集合
    uni_n = {_uname(x): x for x in universe}
    in_n = {_uname(x) for x in inpool}
    exc_n = {_uname(x) for x in excluded}
    missing_n = sorted(set(uni_n) - in_n - exc_n)
    missing = [uni_n[n] for n in missing_n]  # 用 universe 原名报出
    return {"available": True, "universe": len(universe), "in_pool": len(inpool),
            "excluded_with_reason": len(in_n & exc_n) + len(exc_n & set(uni_n)),
            "not_covered": missing}


def report_universe(r: dict) -> str:
    if not r.get("available"):
        return "完整性盘点：未提供 --universe 清单，跳过"
    L = [f"候选池完整性盘点：权威清单 {r['universe']} 款，入池 {r['in_pool']} 款，"
         f"已列明原因未入池 {r['excluded_with_reason']} 款"]
    if r["not_covered"]:
        L.append(f"  ⛔ 未覆盖且未说明原因 {len(r['not_covered'])} 款：{', '.join(r['not_covered'])}")
        L.append("  → 按铁律：能取配置的入池，不能取的写明原因进 pool_excluded[]，不许漏掉。")
    else:
        L.append("  ✅ 权威清单全部覆盖或已说明未入池原因")
    return "\n".join(L)


def selftest() -> int:
    r = check(os.path.join(HERE, "runs", "midsedan_bev_1218_202610", "candidates.json"))
    if not r["missing"] and not r["incomplete"]:
        print("[selftest OK] 覆盖校验可运行")
    else:
        print("[selftest FAIL]", report(r))
        return 1

    # v10.15.1 回归：电芯事实库路径必须走 paths.cat，防止硬拼 data/gen/ 复发。
    # 正断言：配置级无电芯、车系事实库有电芯的车不该被误报 no_cell；
    # 反向篡改：把事实库路径换回错误目录，同一辆车必须被检出 no_cell。
    facts_path = paths.cat("fact_library.json")
    wrong_path = os.path.join(os.path.dirname(TRIMS), "fact_library.json")
    if not os.path.exists(facts_path):
        print(f"[selftest FAIL] 事实库不存在: {facts_path}")
        return 1
    if os.path.normcase(os.path.abspath(facts_path)) == os.path.normcase(os.path.abspath(wrong_path)):
        print("[selftest FAIL] 事实库与配置库同目录，路径硬拼 bug 复发")
        return 1
    facts = json.load(open(facts_path, encoding="utf-8"))
    tdb = load_trims()
    probe = None
    for m, e in tdb.items():
        trims = e.get("trims") or []
        if not trims:
            continue
        trim_cell = any(t.get(CELL_FIELD) not in (None, "", "—") for t in trims)
        fact_cell = str((facts.get(m) or {}).get("cell_brand") or "") not in ("", "—")
        if not trim_cell and fact_cell:
            probe = m
            break
    if probe is None:
        print("[selftest FAIL] 找不到「配置无电芯/事实库有电芯」样本，回归无法验证")
        return 1
    tmp = os.path.join(HERE, "runs", "_ts_trimcoverage_probe.json")
    json.dump({"candidates": [{"model": probe}]}, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    try:
        good = check(tmp)
        if probe in good["no_cell"]:
            print(f"[selftest FAIL] 事实库电芯未生效: {probe}")
            return 1
        orig_cat = paths.cat
        def _bad_cat(name, *a, **kw):
            return wrong_path if name == "fact_library.json" else orig_cat(name, *a, **kw)
        paths.cat = _bad_cat
        try:
            bad = check(tmp)
            if probe not in bad["no_cell"]:
                print("[selftest FAIL] 反向篡改未检出：路径回归失效")
                return 1
        finally:
            paths.cat = orig_cat
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    print(f"[selftest OK] 事实库路径走 paths.cat + 反向篡改检出（样本 {probe}）")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="出榜前逐配置覆盖校验")
    ap.add_argument("--candidates", help="候选池 json")
    ap.add_argument("--trims", help="配置库 json（默认 categories/trims.json）")
    ap.add_argument("--strict", action="store_true", help="有缺失则 exit 1")
    ap.add_argument("--universe", help="权威车型清单 json（完整性盘点，对照是否漏收）")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(selftest())
    if not a.candidates:
        ap.error("需要 --candidates")
    r = check(a.candidates, a.trims)
    print(report(r))
    if a.universe:
        pool = json.load(open(a.candidates, encoding="utf-8"))
        ex = pool.get("pool_excluded", []) if isinstance(pool, dict) else []
        ur = check_universe(a.candidates, a.universe, ex)
        print(report_universe(ur))
    if a.strict and (r["missing"] or r["incomplete"]):
        raise SystemExit(1)
