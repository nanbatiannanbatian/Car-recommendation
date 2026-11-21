#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sibling_check.py —— 同源车评分一致性门禁（2026-11-20 用户强制）

背景：极氪007三厢与007GT平台/三电/座舱/价格全同，却出现
value 8.8 vs 6.0、cabin 9.0 vs 8.4 的无据差异——因为GT是"自动补录缺配置"的条目，
"证据不足"被当成了"产品更差"，违反"降权≠判劣"纪律。

规则：
1. 识别同源车对：同品牌 + 指导价带重叠 + 车名有共同词根（一方是另一方前缀，如 极氪007 / 极氪007GT）。
2. 逐维比对：差异 > DIFF_THRESHOLD(1.5) 的维度，必须在候选池顶层
   score_diff_reasons[车A|车B][维度] 登记事实理由（如"safety：A有C-IASI G+，B只有Euro NCAP按规则降权"）。
3. 未登记理由的大差异 → 出榜拦截（sys.exit），报告列出待解释维度。

这是"差异可以有，但必须有据"的代码化——不再靠人记得。
"""
import json, os, re

import paths

HERE = str(paths.ROOT)
DIFF_THRESHOLD = 1.5
DIMS = ("powertrain", "safety", "adas", "space", "value", "quality", "control", "cabin")


def _brand(name):
    """取品牌：常见中文/英文品牌前缀。"""
    for b in ("极氪", "特斯拉", "比亚迪", "小米", "蔚来", "小鹏", "理想", "深蓝", "吉利", "大众", "丰田", "马自达", "红旗", "荣威", "MG", "阿维塔", "极狐", "银河", "海豹", "秦", "风云", "悦意", "奥迪", "宝马", "奔驰"):
        if name.startswith(b):
            return b
    return name[:2]


def _stem(name):
    """车名词根：去品牌、去动力/车身后缀，用于判同源。"""
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", name.lower())
    for suf in ("gt", "ev", "phev", "max", "ultra", "plus", "pro", "旅行版", "猎装版", "三厢版"):
        s = s.replace(suf, "")
    return s


def find_siblings(cands):
    """返回同源车对列表 [(a, b)]。"""
    pairs = []
    for i, a in enumerate(cands):
        for b in cands[i + 1:]:
            na, nb = a.get("model", ""), b.get("model", "")
            if _brand(na) != _brand(nb):
                continue
            # 名称包含关系（一方词根是另一方前缀）或词干完全相同
            sa, sb = _stem(na), _stem(nb)
            if len(sa) < 2 or len(sb) < 2:
                continue
            if not (sa.startswith(sb) or sb.startswith(sa) or sa == sb):
                continue
            # 价格带重叠
            pa, pb = a.get("price_guide") or [], b.get("price_guide") or []
            if pa and pb and not (max(pa[0], pb[0]) <= min(pa[-1], pb[-1])):
                continue
            pairs.append((a, b))
    return pairs


def check(cands, reasons=None):
    """检查同源车评分差异。返回 {"pairs": n, "issues": [...], "passed": bool}。"""
    reasons = reasons or {}
    issues = []
    for a, b in find_siblings(cands):
        na, nb = a["model"], b["model"]
        key = f"{na}|{nb}"
        r = reasons.get(key) or reasons.get(f"{nb}|{na}") or {}
        sa, sb = a.get("scores") or {}, b.get("scores") or {}
        for dim in DIMS:
            va, vb = sa.get(dim), sb.get(dim)
            if va is None or vb is None:
                continue
            diff = abs(float(va) - float(vb))
            if diff > DIFF_THRESHOLD and not r.get(dim):
                issues.append({"pair": key, "dim": dim, "a": va, "b": vb,
                               "diff": round(diff, 1),
                               "fix": f"在候选池 score_diff_reasons['{key}']['{dim}'] 写明事实差异原因，或对齐分数"})
    return {"pairs": len(find_siblings(cands)), "issues": issues, "passed": not issues}


def selftest():
    cands = [
        {"model": "极氪007", "price_guide": [20.39, 24.39],
         "scores": {"value": 8.8, "safety": 9.0, "cabin": 9.0}},
        {"model": "极氪007GT", "price_guide": [20.39, 24.39],
         "scores": {"value": 6.0, "safety": 7.2, "cabin": 7.0}},
    ]
    r = check(cands)
    assert not r["passed"] and r["pairs"] == 1, f"同源车无据差异未被检出: {r}"
    dims = {x["dim"] for x in r["issues"]}
    assert "value" in dims and "cabin" in dims and "safety" in dims, f"差异维度不全: {dims}"
    # 登记理由后：safety 有据、value/cabin 无据 → 仍拦截
    r2 = check(cands, {"极氪007|极氪007GT": {"safety": "A有C-IASI G+，B只有Euro NCAP"}})
    dims2 = {x["dim"] for x in r2["issues"]}
    assert "safety" not in dims2 and "value" in dims2, f"理由机制失效: {dims2}"
    # 全部登记 → 通过
    r3 = check(cands, {"极氪007|极氪007GT": {
        "safety": "认证差异", "value": "配置差异", "cabin": "座舱差异"}})
    assert r3["passed"], "理由齐全仍拦截"
    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="同源车评分一致性门禁")
    ap.add_argument("--candidates", help="候选池json")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        raise SystemExit(selftest())
    if a.candidates:
        d = json.load(open(a.candidates, encoding="utf-8"))
        cands = d.get("candidates", d)
        r = check(cands, d.get("score_diff_reasons", {}))
        print(f"[同源车检查] {r['pairs']} 对 / 无据差异 {len(r['issues'])}")
        for x in r["issues"]:
            print(f"  - {x['pair']} {x['dim']} 差{x['diff']}")
        raise SystemExit(0 if r["passed"] else 1)
