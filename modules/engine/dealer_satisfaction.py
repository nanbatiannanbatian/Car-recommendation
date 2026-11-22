#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""经销商/服务体验满意度展示查询（dealer_satisfaction.py）——纯展示，不进评分。

背景（2026-11-22 用户要求）：
  用户问「新能源车的经销商体验能不能获取到，能不能加入性价比维度」。
  调研结论：J.D. Power 2025 NEV-SSI/CSI 有权威品牌级满意度分，但只公开各分组
  前三名、品牌级非车型级；本榜 20 款仅 4 个品牌命中。若把它塞进评分，会造成
  「有数据的 4 款被区别对待、其余 16 款中性」的证据不对称（与乐道 L60 小样本被罚
  同一个坑）。用户拍板：不进主分，只做展示列并标注来源与覆盖。

纪律（红线）：
  - 本模块只产出展示字符串/审计字典，绝不写 scores / total / adjust
  - 品牌级分数不能代表具体门店/车型，无公开分不等于服务差
  - 乐道等子品牌未单独参评时不套用母品牌分数
  - 1000 分制原样展示，不折算到本榜 10 分制（避免制造可比性假象）

用法：
  from modules.engine import dealer_satisfaction as DS
  DS.display_for("小鹏G6")   -> "J.D. Power 销售800/售后787（品牌级）"
  DS.field_for("丰田bZ5")   -> "—（该品牌无公开分）"
"""
from __future__ import annotations

import json
import os

import paths

HERE = str(paths.ROOT)
LIB = str(paths.cat("dealer_satisfaction.json"))


def load_library(path: str | None = None) -> dict:
    p = path or LIB
    if not os.path.exists(p):
        return {}
    return json.load(open(p, encoding="utf-8"))


def _canon_model(model: str) -> str:
    from modules.contracts.model_names import canon
    return canon(model)


def resolve(model: str, lib: dict | None = None) -> dict | None:
    """车型 → 品牌满意度记录。返回 None 表示该车型无公开可核验数据。"""
    lib = lib if lib is not None else load_library()
    mb = lib.get("model_brand") or {}
    bs = lib.get("brand_scores") or {}
    # 先精确 canon 匹配，再按包含关系匹配（车型名里带品牌前缀）
    key = _canon_model(model)
    brand = None
    for m, b in mb.items():
        if _canon_model(m) == key:
            brand = b
            break
    if brand is None:
        for m, b in mb.items():
            if _canon_model(m) in key or key in _canon_model(m):
                brand = b
                break
    if brand is None:
        return None
    rec = bs.get(brand)
    if not rec:
        return None
    return {"brand": brand, **rec}


def field_for(model: str, lib: dict | None = None) -> str:
    """事实列展示值。无数据显示破折号+原因，不显示空、不显示 0。"""
    rec = resolve(model, lib)
    if not rec:
        lib = lib if lib is not None else load_library()
        if any(_canon_model(m) in _canon_model(model) for m in (lib.get("model_brand") or {})):
            return "—（子品牌未单列）"
        return "—（该品牌无公开分）"
    ssi = rec.get("NEV-SSI")
    csi = rec.get("NEV-CSI")
    parts = []
    if ssi is not None:
        parts.append(f"销售{ssi}")
    if csi is not None:
        parts.append(f"售后{csi}")
    return f"J.D. Power {'/'.join(parts)}（{rec['brand']}品牌级）"


def audit_for(model: str, lib: dict | None = None) -> dict:
    """审计字段：来源/期别/边界，供页面 hover 或报告引用。"""
    lib = lib if lib is not None else load_library()
    rec = resolve(model, lib)
    out = {"model": model, "display": field_for(model, lib),
           "source": lib.get("source"), "scale": lib.get("scale"),
           "scope_note": "品牌级、仅各分组前三名公开；不代表具体门店/车型体验",
           "in_score": False}
    if rec:
        out.update({"brand": rec["brand"], "NEV-SSI": rec.get("NEV-SSI"),
                    "NEV-CSI": rec.get("NEV-CSI"), "rank_note": rec.get("rank_note")})
    return out


def coverage(cands: list[dict], lib: dict | None = None) -> dict:
    """统计覆盖：几款有公开分、几款没有，供页面披露。"""
    lib = lib if lib is not None else load_library()
    have = [c.get("model") for c in cands if resolve(c.get("model", ""), lib)]
    lack = [c.get("model") for c in cands if not resolve(c.get("model", ""), lib)]
    return {"total": len(cands), "covered": len(have), "missing": len(lack),
            "have_models": have, "missing_models": lack}


def selftest() -> int:
    lib = load_library()
    assert "销售800/售后787" in field_for("小鹏G6", lib), "小鹏G6 应命中品牌级分"
    assert "销售806/售后801" in field_for("蔚来ES6", lib), "蔚来ES6 应命中"
    assert "800" in field_for("小鹏MONA L05", lib), "小鹏MONA L05 应命中"
    assert "无公开分" in field_for("丰田bZ5", lib), "丰田应标无公开分"
    assert "无公开分" in field_for("特斯拉 Model Y", lib), "特斯拉应标无公开分"
    a = audit_for("小鹏G6", lib)
    assert a["in_score"] is False and a["NEV-SSI"] == 800, "审计字段必须标明不进分"
    # 关键回归：本模块绝不写 scores
    c = {"model": "小鹏G6", "scores": {"value": 9.7}}
    before = dict(c["scores"])
    field_for(c["model"], lib)
    assert c["scores"] == before, "展示查询不得修改 scores"
    return 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        rc = selftest()
        print("[dealer_satisfaction selftest] OK" if rc == 0 else "FAIL")
        raise SystemExit(rc)
    for m in ["小鹏G6", "蔚来ES6", "极氪7X", "阿维塔07 纯电版", "丰田bZ5", "乐道L60"]:
        print(f"{m:16s} -> {field_for(m)}")
