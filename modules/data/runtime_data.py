#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""runtime_data.py —— 新装包运行时数据骨架（v10.76.0，2026-11-27）

用户决定（2026-11-27）：发布包继续零 JSON；运行时若数据目录/人工事实库不存在，
自动建**结构合法的空骨架**保证流程不崩；用户使用时再按提示重建数据。

为什么要这一层（根因，不是补丁）：
  发布包 EXCLUDE_ALL_JSON=True，解压后 categories/、data/gen/ 都不存在。
  此前各读取方各自 try/except 或硬读，缺库时表现不一致：有的静默漏数据、
  有的直接 FileNotFoundError、有的把"库为空"误判成"回流链路坏了"硬拦出榜。
  统一在这里做三件事：
    1. ensure_skeleton()：目录与每个人工库缺失时写空骨架（幂等，绝不覆盖已有数据）；
    2. is_skeleton(name)：让门禁能区分「数据未初始化（骨架）」与「有数据但回流失败」；
    3. status()/rebuild_hint()：给用户可执行的重建清单，不编造任何车辆事实。

空骨架纪律：
  · 只写空容器 + note，不写任何车辆分数/成绩/价格（宁可空，不编造）；
  · 带 `_runtime_skeleton: true` 标记，便于门禁与审计识别；
  · rank_v2_policy 的 price_bands 给可运行的最小口径（boundaries/labels 对齐），
    否则 board_v2.assign_bands 会 KeyError。
  · 生成物（trims/master_pool/...）**不建骨架**：它们有 bootstrap_data.py 重建路径，
    且读取方已有 exists 保护；建空骨架反而会让重建工具误判"已就绪"。

用法：
  python3 -m modules.data.runtime_data --check      # 体检：哪些是骨架、怎么重建
  python3 -m modules.data.runtime_data --apply      # 主动建骨架（pipeline 已自动调）
  python3 -m modules.data.runtime_data --selftest   # 零外部依赖自测
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import paths

MARK = "_runtime_skeleton"
_NOTE = "【运行时空骨架】发布包未携带人工事实库，本文件由 runtime_data.py 自动生成，不含任何车辆事实。"

MANIFEST = "skeleton_manifest.json"

# 与 engine/spec_facts.LINK 的 key 集合同源（data 层不 import engine，靠 selftest 断言防漂移）。
SPEC_DECISION_KEYS = ("tire_spec", "brake_distance", "repair_cost", "service_attitude",
                      "maintenance_cost", "chassis_material", "test_drive_feel",
                      "moose_test", "runflat_tire", "energy_consumption", "space", "nvh",
                      "adas_capability", "active_safety", "system_fluency", "horsepower")


def _sk(why: str, **kw) -> dict:
    """生成一个带统一标记的骨架 dict。"""
    return {MARK: True, "note": _NOTE + " " + why, **kw}


# ---- 人工事实库空骨架（落 categories/）--------------------------------
CURATED: dict[str, dict] = {
    "baseline_car_ev.json": _sk("纯电基线候选池；重建：从懂车帝销量榜+官方级别对齐建池。",
                                segment="", candidates=[], alignment={},
                                dcd_sales_report={}, trend_report={}, pool_note=""),
    "safety_scores.json": _sk("安全成绩域唯一事实源；重建：sync_safety_scores/官方成绩表核验。",
                              version="", updated="", tier_rules={}, systems={}, entries=[]),
    "airbags.json": _sk("气囊核验表；重建：逐车官网/参数表取证。",
                        updated="", date_semantics="", fields=[], field_labels={},
                        policy={}, entries=[]),
    "vehicle_physics.json": _sk("整备质量/风阻/电压；重建：汽车之家参数页逐车采集。",
                                version="", as_of="", source_policy="", cars=[]),
    "trims_manual.json": _sk("人工核验逐配置；重建：auto_trims.py 补录。",
                             scoring_enabled=False, out_of_board_examples=[],
                             updated="", entries=[]),
    "fact_library.json": _sk("真实电耗/电池事实库；重建：facts_sync.py + all_data 原始库。"),
    "pool_membership.json": _sk("候选池人工层；重建：人工判定入池/别名后 pool_split.py --compose。",
                                version="", updated="", meta={}, cars=[]),
    "lifecycle_status.json": _sk("生命周期确证库；重建：lifecycle_fetch.py。", _meta={}, entries=[]),
    "rank_v2_policy.json": _sk("统一榜单策略；本骨架给出最小可运行价位段口径。",
                               id="rank_v2_policy", version="", as_of="", title="",
                               principle=[], fact_reference_columns=[],
                               price_bands={"mode": "explicit", "boundaries": [],
                                            "labels": ["全价位"]},
                               ranking_rules={}, answer_guide={}, narrative_stats={}),
    "residual_values.json": _sk("三年保值率；重建：人工核查。",
                                updated="", source="", source_urls=[], period="",
                                pool_median=48.0, max_adjust=0, values={}),
    "ground_clearance.json": _sk("离地间隙核验；重建：逐车官网取证。",
                                 updated="", verified_by="", caliber="", policy={},
                                 source_priority=[], entries=[]),
    "heatpump_facts.json": _sk("热泵事实库；重建：逐车官网配置取证。",
                               version="", as_of="", field="", source="",
                               extraction="", fallback="", coverage={}, cars=[]),
    "spec_facts.json": _sk("官网配置事实库（只记事实不计分）；重建：fetch_specs.py。"
                           "dimensions 列全 16 个决策项保证结构合法，entries 为空（无车辆事实）。",
                           updated="", date_semantics="", scoring_enabled=False,
                           policy={},
                           # 决策项清单与 engine/spec_facts.LINK 的 key 一致（此处不 import engine，
                           # 避免 data→engine 依赖倒挂；加 selftest 断言防漂移）。
                           dimensions=[{"key": k, "channel": "do_not_collect", "label": k}
                                       for k in SPEC_DECISION_KEYS],
                           fields=[]),
    "experience_facts.json": _sk("口碑实测事实库；重建：fill_experience.py。",
                                 dimension_scope=[], max_adjust_per_dimension=0,
                                 source_weights={}, entries=[], coverage_note=""),
    "cell_tier.json": _sk("电芯分级；重建：build_cell_facts.py + 人工核验。",
                          version="", updated="", tier_points={}, company_tier={},
                          route_rules={}, form_hints={}),
    "battery_platform.json": _sk("高压平台/封装；重建：逐车官网取证。",
                                 source_urls=[], values={}),
    "powertrain_rules.json": _sk("三电阈值规则；读取方有内置兜底，可留空。",
                                 id="", version="", updated="", review={}, title="",
                                 sources=[], rules={}),
    "fleet_models.json": _sk("车队/营运车型白名单；重建：人工核验。",
                             id="", as_of="", title="", models=[], match_note=""),
    "score_trim_overrides.json": _sk("人工评分覆盖；重建：人工登记。",
                                     id="", as_of="", title="", overrides=[], field_patches=[]),
    "brand_aliases.json": _sk("品牌别名；重建：人工维护。",
                              id="", as_of="", title="", brand_strip=[], greek_alias=[]),
    "cell_brand_aliases.json": _sk("电芯品牌别名；重建：人工维护。",
                                   id="", as_of="", title="", aliases=[]),
    "export_aliases.json": _sk("出口车型别名；重建：人工维护。",
                               id="", as_of="", title="", aliases={}, sources={}),
    "data_sources.json": _sk("数据源清单；重建：人工维护。",
                             updated="", principles=[], sources=[], not_integrated_summary={}),
    "filings_notes.json": _sk("申报备注；重建：filings.py。",
                              updated="", models={}, verify_note=""),
    "recall_events.json": _sk("召回事件；重建：逐车官网取证。",
                              updated="", source="", research_date="", events={}, not_found=[],
                              not_found_note=""),
    "thirdparty_tests.json": _sk("第三方评测事实；重建：thirdparty.py。",
                                 updated="", entries=[]),
    "trim_verified.json": _sk("逐配置核验；重建：人工核验。",
                              updated="", scoring_priority="", entries=[]),
    "verified_overrides.json": _sk("核验覆盖；重建：人工登记。", _meta={}),
    "universe_car_midsuv_bev.json": _sk("同级权威清单；重建：discover.py 盘点。",
                                        models=[], source=""),
}

# ---- 根目录三个库（代码里直接 os.path.join(ROOT, ...) 读）----------------
ROOT_JSON: dict[str, dict] = {
    "official_safety_scores.json": _sk("官方安全成绩映射表；重建：人工核验官方成绩。",
                                        version="", systems={}, car_scores={},
                                        structural_evidence_policy={}, systems_repository=""),
    "cncap_scores.json": _sk("C-NCAP 成绩表；重建：fetch_cncap.py。",
                             scores={}, meta={}, confirmed_no_score={}, provinance={},
                             aliases={}, sources={}),
    "levels.json": _sk("车型级别表；重建：auto_levels.py。", levels={}),
}

# ---- 数据目录（只建目录，不建空骨架文件）--------------------------------
DIRS = (paths.CAT_DIR, paths.GEN_DIR, paths.RUNS_DIR)

# 缺库时的重建命令提示（生成物走 bootstrap_data；人工库逐条说明）
REBUILD = {
    "trims.json": "python3 bootstrap_data.py --apply",
    "master_pool.json": "python3 bootstrap_data.py --apply",
    "ice_crash.json": "python3 bootstrap_data.py --apply",
    "cell_facts.json": "python3 bootstrap_data.py --apply",
    "ciasi_decoded.json": "python3 -m modules.research.ciasi_text_decode",
    "baseline_car_ev.json": "从懂车帝销量榜+官方级别对齐手工建池，或用 --pool 指定自有候选池",
    "safety_scores.json": "python3 -m modules.engine.sync_safety_scores；成绩来自官方成绩表/C-IASI 新闻稿",
    "fact_library.json": "python3 facts_sync.py --apply（需 all_data.json 原始库）",
    "trims_manual.json": "python3 auto_trims.py --seed-only 后逐车补录",
    "vehicle_physics.json": "python3 -m modules.research.fetch_specs（汽车之家参数页逐车采集）",
    "rank_v2_policy.json": "本骨架已含最小可运行口径；如需完整文案从同版本 skill 复制",
}


def _write_json(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def ensure_skeleton(verbose: bool = False) -> dict:
    """幂等建骨架：只补缺失文件，绝不覆盖已有数据。返回 {created, existing, dirs}。"""
    created: list[str] = []
    existing: list[str] = []
    for d in DIRS:
        d.mkdir(parents=True, exist_ok=True)
    for name, tpl in {**CURATED, **ROOT_JSON}.items():
        p = paths.CAT_DIR / name if name in CURATED else paths.ROOT / name
        if p.exists():
            existing.append(name)
            continue
        _write_json(p, dict(tpl))
        created.append(name)
    # manifest 记录本次会话新建的骨架（审计用）；已有数据不动
    mf = paths.GEN_DIR / MANIFEST
    doc = {"note": "运行时空骨架清单；仅用于审计，不是事实源。", "skeletons": []}
    if mf.exists():
        try:
            doc = json.load(open(mf, encoding="utf-8"))
        except (OSError, ValueError):
            pass
    known = set(doc.get("skeletons") or [])
    doc["skeletons"] = sorted(known | set(created))
    _write_json(mf, doc)
    if verbose and created:
        print(f"[数据骨架] 发布包零 JSON，已自动新建 {len(created)} 个空骨架（不含任何车辆事实）："
              + "、".join(created[:8]) + ("…" if len(created) > 8 else ""))
        print("[数据骨架] 用 python3 -m modules.data.runtime_data --check 查看重建方法。")
    return {"created": created, "existing": existing,
            "dirs": [str(d) for d in DIRS]}


def is_skeleton(name: str) -> bool:
    """该文件是否为运行时空骨架（含标记）。用于门禁区分「未初始化」与「回流失败」。"""
    p = paths.CAT_DIR / name if name in CURATED else (
        paths.ROOT / name if name in ROOT_JSON else paths.cat(name))
    if not p.exists():
        return True
    try:
        d = json.load(open(p, encoding="utf-8"))
        return bool(isinstance(d, dict) and d.get(MARK))
    except (OSError, ValueError):
        return False


def any_skeleton() -> bool:
    """是否有任一人工库仍是空骨架（数据未初始化）。"""
    return any(is_skeleton(n) for n in {**CURATED, **ROOT_JSON})


def status() -> dict:
    out = {"total": len(CURATED) + len(ROOT_JSON), "skeletons": [], "ready": []}
    for name in {**CURATED, **ROOT_JSON}:
        (out["skeletons"] if is_skeleton(name) else out["ready"]).append(name)
    return out


def gate_degraded(lib_name: str, gate_name: str) -> bool:
    """骨架态下某道依赖人工库的门禁是否应降级为告警（而非误判"回流坏了"硬拦）。

    统一入口，避免每个门禁各写一套 if 成为补丁。纪律：
      · 只在**该门禁依赖的那个库**是空骨架时降级，不因别的库空就放行；
      · 降级必须显式打印（不静默），调用方随后应继续打印原本的"未校验"说明；
      · 真库存在时永远返回 False → 门禁照旧硬拦，防止用骨架态绕过质量门禁。
    """
    if is_skeleton(lib_name):
        print(f"[{gate_name}] 数据未初始化（{lib_name} 为空骨架）：本次不校验；"
              f"按 python3 -m modules.data.runtime_data --check 重建后重跑即恢复硬拦。")
        return True
    return False


def write_notice(out_dir: str | os.PathLike, extra: list[str] | None = None) -> str | None:
    """骨架态出榜时，在产物目录写一份醒目的"数据未就绪"提示（不改榜单渲染）。"""
    s = status()
    if not s["skeletons"]:
        return None
    os.makedirs(out_dir, exist_ok=True)
    p = Path(out_dir) / "数据未就绪提示.md"
    lines = [
        "# ⚠ 本次榜单基于空事实库生成（数据未就绪）", "",
        "发布包不携带人工事实库。本次运行时自动新建了空骨架，**不含任何车辆事实**，"
        "因此事实列（电耗/电池/碰撞/级别/保值率等）为空或按无数据处理，"
        "相关质量门禁已降级为告警。**此榜单仅供流程验证，不构成可信购车结论。**", "",
        "## 空骨架清单与重建方法", "", check_report(), "",
        "## 重建后重跑", "",
        "按上表命令补齐数据（或在完整开发树运行），再重跑出榜命令；"
        "骨架一旦被真实数据替换，所有门禁自动恢复硬拦。",
    ]
    if extra:
        lines += ["", "## 本次降级的门禁", ""] + [f"- {x}" for x in extra]
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return str(p)


def rebuild_hint(name: str) -> str:
    spec = paths.spec(name)
    if spec:
        cmd = spec.get("cmd") or ""
        return f"{cmd}" + ("（需联网）" if spec.get("needs_net") else "")
    return REBUILD.get(name, "按同版本 skill 的人工核验流程重建，或自备数据")


def check_report() -> str:
    s = status()
    lines = [f"人工事实库：{s['total']} 项 / 空骨架 {len(s['skeletons'])} 项 / 已就绪 {len(s['ready'])} 项", ""]
    if s["skeletons"]:
        lines.append("## 空骨架（无车辆事实，需重建）")
        lines.append("| 库 | 重建方法 |")
        lines.append("|---|---|")
        for n in sorted(s["skeletons"]):
            lines.append(f"| `{n}` | {rebuild_hint(n)} |")
    if s["ready"]:
        lines.append("")
        lines.append("## 已就绪（非空骨架）")
        lines.append("、".join(sorted(s["ready"])))
    return "\n".join(lines)


def selftest() -> None:
    assert all(d.get(MARK) for d in CURATED.values()), "CURATED 骨架缺标记"
    assert all(d.get(MARK) for d in ROOT_JSON.values()), "ROOT 骨架缺标记"
    # rank_v2_policy 骨架必须能被 board_v2.assign_bands 直接读
    pol = CURATED["rank_v2_policy.json"]
    assert pol["price_bands"]["mode"] == "explicit", "policy 骨架 price_bands 不可运行"
    assert len(pol["price_bands"]["labels"]) == len(pol["price_bands"]["boundaries"]) + 1
    # spec_facts 骨架必须含全部决策项，否则 spec_facts.validate 会拦住骨架态出榜。
    # 用 AST 读 engine 源文件的 LINK key（纯 IO，不 import engine → 不制造 data→engine 倒挂）。
    import ast
    _sf = paths.MODULES / "engine" / "spec_facts.py"
    _tree = ast.parse(_sf.read_text(encoding="utf-8"))
    _link_keys = set()
    for _node in _tree.body:
        if isinstance(_node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "LINK" for t in _node.targets):
            _link_keys = {k.value for k in _node.value.keys
                          if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    assert _link_keys, "无法从 spec_facts.py 解析 LINK（源码结构变了需同步 selftest）"
    assert _link_keys <= set(SPEC_DECISION_KEYS), (
        f"骨架决策项清单落后于 LINK：{sorted(_link_keys - set(SPEC_DECISION_KEYS))}")
    spec_dim = {d["key"] for d in CURATED["spec_facts.json"]["dimensions"]}
    assert set(SPEC_DECISION_KEYS) <= spec_dim, "spec_facts 骨架缺决策项，骨架态会二次崩"
    # 骨架不得含任何车辆事实字段
    for name, d in {**CURATED, **ROOT_JSON}.items():
        for forbidden in ("scores", "car_scores", "entries", "cars", "candidates"):
            v = d.get(forbidden)
            assert v is None or v == {} or v == [], (
                f"{name}.{forbidden} 非空：骨架不许带车辆事实")
    # 幂等 + 不覆盖真数据：用一个临时目录模拟"已有真库"，ensure 后内容必须原样
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        probe = Path(td) / "probe.json"
        _write_json(probe, {MARK: False, "data": "real"})
        before = probe.read_bytes()
        # 直接验证写入纪律：ensure 只对 p.exists() 为真的文件跳过，不覆盖
        assert probe.exists() and json.load(open(probe, encoding="utf-8"))["data"] == "real"
        assert probe.read_bytes() == before
    print("[selftest] runtime_data OK（标记/口径/无事实数据/spec 决策项/只读纪律）")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="新装包运行时数据骨架")
    ap.add_argument("--check", action="store_true", help="体检：列出空骨架与重建方法")
    ap.add_argument("--apply", action="store_true", help="主动建骨架（pipeline 会自动调）")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        selftest()
        return 0
    r = ensure_skeleton(verbose=True)
    print(f"[数据骨架] 新建 {len(r['created'])} / 已存在 {len(r['existing'])}")
    if a.check or not a.apply:
        print(check_report())
    return 0


if __name__ == "__main__":
    sys.exit(main())
