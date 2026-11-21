#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""param_field_audit.py —— 参数页字段覆盖率审计（缺口的「哨兵」）

这个模块回答用户 2026-11-22 的那个问题，并且**防止它再发生**：
  「高压平台(V)、快充时间+区间、百公里耗电、电芯品牌 全都躺在参数页里没被提取，
    为什么没有被提取？以后还会不会出现这样的问题？」

机制：
  ① **台账**（`param_fields.KNOWN_FIELDS`）：参数页每个字段的 id 与名称，
     来自实测盘点。台账里每个 id 都必须**要么被提取、要么在 SKIPPED 里写明理由**。
  ② **离线自检**（`unassigned_fields`）：不联网，检查台账里有没有「既没提取、
     也没解释」的字段——有就是漏洞，回归直接报错。**这是「以后不会再漏」的保证**。
  ③ **联网审计**（`audit_series`）：真抓一次参数页，比对台账——
     页面新增字段（台账里没有）也要报出来，提示「参数页改版，需复核台账」。

用法：
    python3 -m modules.research.param_field_audit --offline
    python3 -m modules.research.param_field_audit --sid 7777
"""
from __future__ import annotations

import argparse
import json
import re
import sys

import paths

# 按**字段名**定位（非按 id）的字段：这些 id 在台账里，但取值走名字匹配
USED_BY_NAME = {
    1149: "能源类型（名字匹配）",
    1282: "电池类型（名字匹配）",
    8428: "CLTC 续航（名字匹配）",
    8448: "电动机总功率（名字匹配）",
    1233: "电池能量(kWh)（名字匹配）",
}


def _extracted_ids() -> set[int]:
    from modules.research import auto_trims as AT
    return (set(AT.AH_BASE_IDS.values()) | set(AT.AH_BODY_IDS.values())
            | set(AT.AH_BAT_IDS.values()) | set(USED_BY_NAME))


def unassigned_fields() -> list[tuple[int, tuple[str, str]]]:
    """台账里既没被提取、也没被解释的字段（离线）。有就是漏洞。"""
    from modules.research import param_fields as PF
    got = _extracted_ids()
    out = []
    for fid, meta in PF.KNOWN_FIELDS.items():
        if fid in got or fid in PF.SKIPPED:
            continue
        out.append((fid, meta))
    return out


def orphan_skips() -> list[tuple[int, str]]:
    """SKIPPED 里写了理由、但台账里根本没有该 id（台账与理由表漂移）。"""
    from modules.research import param_fields as PF
    return [(fid, why) for fid, why in PF.SKIPPED.items() if fid not in PF.KNOWN_FIELDS]


def audit_series(sid: str, fetcher=None) -> dict:
    """联网审计一个车系：页面上的字段 vs 台账 vs 提取清单。

    返回 {sid, page_fields, unassigned, not_in_ledger, skipped_present}
    """
    from modules.research import auto_trims as AT
    from modules.research import param_fields as PF
    cfg, err = AT._fetch_config_json(sid)
    if cfg is None:
        return {"sid": sid, "error": err}
    pt = (cfg.get("result") or {}).get("paramtypeitems") or []
    page: dict[int, tuple[str, str]] = {}
    for grp in pt:
        gname = str(grp.get("groupname") or grp.get("name") or "")
        for it in (grp.get("paramitems") or []):
            fid = it.get("id")
            vals = [AT.clean(v.get("value")) for v in (it.get("valueitems") or [])]
            if not any(v and v not in ("-", "—") for v in vals):
                continue
            if fid is not None and fid >= 0:
                page.setdefault(fid, (gname, AT.clean(it.get("name"))))
    got = _extracted_ids()
    unassigned = [(f, m) for f, m in page.items()
                  if f not in got and f not in PF.SKIPPED]
    not_in_ledger = [(f, m) for f, m in page.items() if f not in PF.KNOWN_FIELDS]
    return {
        "sid": sid,
        "page_fields": len(page),
        "extracted": len([f for f in page if f in got]),
        "skipped": len([f for f in page if f in PF.SKIPPED]),
        "unassigned": [{"id": f, "group": m[0], "name": m[1]} for f, m in unassigned],
        "not_in_ledger": [{"id": f, "group": m[0], "name": m[1]} for f, m in not_in_ledger],
    }


def selftest() -> int:
    """台账自洽性：每个已知字段都有归属；跳过理由不悬空。"""
    bad = unassigned_fields()
    assert not bad, f"参数页台账里有字段既没提取也没解释: {bad}"
    orphan = orphan_skips()
    assert not orphan, f"SKIPPED 里写了台账中不存在的 id: {orphan}"
    from modules.research import param_fields as PF
    assert len(PF.KNOWN_FIELDS) >= 50, "台账条目过少，盘点可能不完整"
    # 已提取的关键字段必须在台账里（防台账与提取清单漂移）
    got = _extracted_ids()
    for fid in (9027, 1290, 9015, 1169, 1175, 1250):
        assert fid in PF.KNOWN_FIELDS, f"关键字段 {fid} 不在台账里"
        assert fid in got, f"关键字段 {fid} 未被提取"
    print(f"SELFTEST PASS · 参数页字段台账自洽（台账 {len(PF.KNOWN_FIELDS)} 项 / "
          f"已提取 {len([f for f in PF.KNOWN_FIELDS if f in got])} / "
          f"已解释跳过 {len(PF.SKIPPED)} / 无归属 0）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true", help="只做台账自洽性检查（不联网）")
    ap.add_argument("--selftest", action="store_true", help="同 --offline（与其它模块一致的入口名）")
    ap.add_argument("--sid", help="审计某个车系的参数页（需联网）")
    a = ap.parse_args()
    if a.offline or a.selftest or not a.sid:
        return selftest()
    rep = audit_series(a.sid)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    if rep.get("unassigned"):
        print(f"\n[缺口] {len(rep['unassigned'])} 个字段有值但既没提取也没解释：")
        for x in rep["unassigned"]:
            print(f"  id={x['id']} [{x['group']}] {x['name']}")
        return 1
    if rep.get("not_in_ledger"):
        print(f"\n[台账过期] {len(rep['not_in_ledger'])} 个页面字段不在台账里（参数页可能改版）：")
        for x in rep["not_in_ledger"]:
            print(f"  id={x['id']} [{x['group']}] {x['name']}")
        return 1
    print("\n[OK] 页面字段全部有归属（已提取或有跳过理由）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
