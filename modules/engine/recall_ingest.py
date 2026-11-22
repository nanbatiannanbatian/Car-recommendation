#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""召回事件注入（recall_ingest.py）——把 categories/recall_events.json 按车型写入候选池 recall_events。

质量与品控维度 quality_score._recall_penalty 早已支持 recall_events 字段，
但候选池 20 款全缺此字段 → 召回因子形同虚设。本模块补数据链路（非补丁：
任何候选池重新加载都会走 apply 重新注入，不是改某一次产物快照）。

纪律（红线）：
  - 只写 c["recall_events"] 展示/审计字段；quality 分由 quality_score 统一算，不在此处打分
  - coverage_uncertain=true 的条目跳过（召回批次不覆盖在售款，如一代ES6 vs 换代ES6）
  - 严格车型匹配（canon 归一化），不模糊套用；同品牌不同车型不共享召回
  - 未查到召回的车型不写空列表冒充「已核验无召回」——留空由 quality_score 显示中性

用法：
  from modules.engine import recall_ingest as RI
  cands = RI.apply(cands)            # 原地注入 recall_events
  RI.coverage(cands)                 # 覆盖统计
"""
from __future__ import annotations

import json
import os

import paths

HERE = str(paths.ROOT)
LIB = str(paths.cat("recall_events.json"))


def load_library(path: str | None = None) -> dict:
    p = path or LIB
    if not os.path.exists(p):
        return {}
    return json.load(open(p, encoding="utf-8"))


def _key(model: str) -> str:
    from modules.contracts.model_names import canon
    return canon(model)


def events_for(model: str, lib: dict | None = None) -> list[dict]:
    """车型 → 已确认覆盖在售款的召回事件列表（跳过 coverage_uncertain）。"""
    lib = lib if lib is not None else load_library()
    table = lib.get("events") or {}
    rec = None
    for m, evs in table.items():
        if _key(m) == _key(model):
            rec = evs
            break
    if not rec:
        return []
    kept = []
    for e in rec:
        if e.get("coverage_uncertain"):
            continue
        kept.append({
            "id": e.get("id"), "date": e.get("date"), "count": e.get("count"),
            "reason": e.get("reason"), "severity": e.get("severity", "normal"),
            # v10.19.0：透传处置方式，供 quality_score.is_ota_recall 判定是否扣分
            "remedy": e.get("remedy"),
            "source_url": e.get("source_url"),
        })
    return kept


def apply(cands: list[dict], lib: dict | None = None) -> dict:
    """原地注入 recall_events；返回审计统计。同输入重复执行结果一致（幂等）。"""
    # v10.19.0：OTA 判定复用 quality_score 的单一真相源，避免两处判定漂移
    from modules.engine.quality_score import is_ota_recall as _is_ota
    lib = lib if lib is not None else load_library()
    injected = []
    skipped = []
    for c in cands:
        model = c.get("model", "")
        evs = events_for(model, lib)
        if evs:
            c["recall_events"] = evs
            c["recall_source"] = lib.get("source")
            injected.append({"model": model, "n": len(evs),
                             "ids": [e["id"] for e in evs],
                             "ota_skipped": sum(1 for e in evs if _is_ota(e))})
        # coverage_uncertain 的条目单独披露（有召回但批次不覆盖在售款）
        table = lib.get("events") or {}
        for m, all_evs in table.items():
            if _key(m) != _key(model):
                continue
            unc = [e for e in all_evs if e.get("coverage_uncertain")]
            if unc:
                skipped.append({"model": model, "n": len(unc),
                                "reason": unc[0].get("coverage_note")})
    return {"status": "applied", "total": len(cands), "injected": len(injected),
            "injected_detail": injected, "skipped_uncertain": skipped}


def coverage(cands: list[dict]) -> dict:
    have = [c.get("model") for c in cands if c.get("recall_events")]
    return {"total": len(cands), "with_recall": len(have), "models": have}


def selftest() -> int:
    from modules.engine.quality_score import is_ota_recall as _is_ota
    lib = load_library()
    # 1) 特斯拉Model Y：1 high + 3 normal，全部覆盖
    tesla = events_for("特斯拉 Model Y", lib)
    assert len(tesla) == 4, f"Model Y 应有4起召回，实得{len(tesla)}"
    assert sum(1 for e in tesla if e["severity"] == "high") == 1, "Model Y 应有1起high"
    # 2) 蔚来ES6：coverage_uncertain 跳过 → 0
    assert events_for("蔚来ES6", lib) == [], "ES6 换代车不套用一代平台召回"
    # 3) 小鹏G6 / 零跑C11：各1起normal
    assert len(events_for("小鹏G6", lib)) == 1
    assert len(events_for("零跑C11", lib)) == 1
    # 4) 未查到的车型留空
    assert events_for("丰田bZ5", lib) == []
    # 5) v10.19.0 remedy 透传 + OTA 判定：Model Y 4起中3起ota、1起mixed
    assert sum(1 for e in tesla if _is_ota(e)) == 3, "Model Y 应有3起纯OTA"
    g6 = events_for("小鹏G6", lib)[0]
    c11 = events_for("零跑C11", lib)[0]
    assert g6.get("remedy") == "physical" and not _is_ota(g6), "G6 物理标识应计入"
    assert c11.get("remedy") == "mixed" and not _is_ota(c11), "C11 物理+OTA混合应计入"
    # 6) 幂等 + 不直接打分
    cands = [{"model": "小鹏G6", "scores": {"quality": 8.0}},
             {"model": "特斯拉 Model Y", "scores": {"quality": 8.0}},
             {"model": "蔚来ES6", "scores": {"quality": 8.0}}]
    r1 = apply(cands, lib)
    q1 = [c["scores"]["quality"] for c in cands]
    apply(cands, lib)
    assert [c["scores"]["quality"] for c in cands] == q1, "注入不得改分数"
    assert r1["injected"] == 2 and len(r1["skipped_uncertain"]) == 1, "ES6应跳过并披露"
    my_audit = next(x for x in r1["injected_detail"] if x["model"] == "特斯拉 Model Y")
    assert my_audit["ota_skipped"] == 3, f"Model Y OTA统计错误: {my_audit}"
    return 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        rc = selftest()
        print("[recall_ingest selftest] OK" if rc == 0 else "FAIL")
        raise SystemExit(rc)
    lib = load_library()
    for m in ["特斯拉 Model Y", "小鹏G6", "零跑C11", "蔚来ES6", "丰田bZ5"]:
        print(f"{m:16s} -> {len(events_for(m, lib))} 起（覆盖在售款）")
