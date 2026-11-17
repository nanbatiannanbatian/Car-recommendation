#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把候选池的「级别 / 指导价」对齐到汽车之家官方字段（align_specs.py）。

背景：v4 的 cls 与 price 是人工搜索拼的，「官方二级分类」与「厂商指导价」两道闸门
必须来自一手源。汽车之家 `autohome score` 直接给 level 与 guide_price。

解析策略（实测：品牌名不可靠，深蓝/极狐/MG/银河/风云 作为品牌名都查不到）：
  1. 品牌目录  `autohome brand <品牌>`（比亚迪 / 大众 / 马自达 / 丰田 …）
  2. 字母目录  `autohome brand <A-Z>`（按车系拼音首字母；深蓝在 S、MG 在 M）
  3. 都没有 → unresolved，保留原值，**不猜**

匹配的四道闸（踩出来的）：
  · 归一化只去空格标点、去品牌前缀与尾部「纯电/EV」，**不剥「新能源」**
    —— 剥了会把「秦新能源」变成「秦」，进而错配到「秦MAX」。
  · 包含匹配要求长度比 ≥0.55 —— 否则「风云A9」会配到「风云」、「ID.AURA T6」配到「大众ID.」。
  · 排除「海外/进口」版 —— 否则「丰田bZ3」配到「丰田bZ3(海外)」。
  · 纯电口径校验：我方带 EV/纯电、车系不带 → 标记 powertrain_ambiguous，
    **级别照收，价格不覆盖**（如 海豹06EV → 车系「海豹06」的 9.68 万含插混，不是纯电价）。

用法：
    python3 align_specs.py                  # 对齐并写 v3/candidates_v5_aligned.json
    python3 align_specs.py --report-only    # 只出报告
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_specs import list_brand, score_series   # 复用取数实现

RUN = os.path.dirname(os.path.abspath(__file__))
# 三个路径都在 main() 里按 --run-dir / --src / --out 重新赋值。
# 保留模块级默认值只为兼容「直接 import 调用」的场景。
SRC = ""
OUT = ""
SPECS = ""
REPORT = ""

PLAN = {
    "深蓝L06": [("letter", "S")],
    "深蓝L07": [("letter", "S")],
    "深蓝S07": [("letter", "S")],
    "马自达EZ-6": [("brand", "马自达")],
    "马自达EZ-60": [("brand", "马自达")],
    "风云A9": [("brand", "奇瑞")],
    "秦MAX EV": [("brand", "比亚迪")],
    "秦L EV": [("brand", "比亚迪")],
    "海豹06EV": [("brand", "比亚迪")],
    "海狮06EV": [("brand", "比亚迪")],
    "宋Ultra EV": [("brand", "比亚迪")],
    "大众ID.ERA 5S": [("brand", "大众")],
    "大众ID.AURA T6": [("brand", "大众")],
    "大众与众07": [("brand", "大众")],
    "MG 07 纯电": [("letter", "M")],
    "银河A7 EV": [("brand", "吉利")],
    "极狐阿尔法S5": [("letter", "J"), ("brand", "北汽")],
    "极狐阿尔法T5": [("letter", "J"), ("brand", "北汽")],
    "极狐贝塔S3": [("letter", "J"), ("brand", "北汽")],
    "红旗天工05": [("brand", "红旗")],
    "丰田bZ3": [("brand", "丰田"), ("letter", "B")],
    "丰田bZ5": [("brand", "丰田"), ("letter", "B")],
    "小鹏MONA L05": [("brand", "小鹏")],
    "零跑C10": [("brand", "零跑")],
    "零跑C11": [("brand", "零跑")],
    "长安启源Q06": [("brand", "长安")],
}

BRAND_PREFIX = ("大众", "长安", "一汽", "上汽", "东风", "广汽", "北汽",
                "奇瑞", "吉利", "比亚迪", "广汽丰田", "一汽丰田")
EV_TOKENS = ("纯电", "EV", "ev")
MIN_RATIO = 0.55


def norm(name: str) -> str:
    s = str(name or "")
    for ch in (" ", "　", "·", "-", "_", "/", "（", "）", "(", ")", "\\"):
        s = s.replace(ch, "")
    for p in BRAND_PREFIX:
        if s.startswith(p) and len(s) - len(p) >= 3:
            s = s[len(p):]
            break
    for suf in ("纯电", "EV"):
        if s.endswith(suf) and len(s) - len(suf) >= 2:
            s = s[: -len(suf)]
            break
    return s.lower()


def has_ev(name: str) -> bool:
    return any(t in str(name or "") for t in EV_TOKENS)


def similarity(a: str, b: str) -> float:
    x, y = norm(a), norm(b)
    if not x or not y or len(x) < 2 or len(y) < 2:
        return 0
    if x == y:
        return 100
    if x in y or y in x:
        short, long_ = (x, y) if len(x) <= len(y) else (y, x)
        ratio = len(short) / len(long_)
        if ratio < MIN_RATIO:
            return 0                     # 长度差太多，视为不同车系
        return 70 + 30 * ratio
    short, long_ = (x, y) if len(x) <= len(y) else (y, x)
    hits = sum(1 for i in range(len(short) - 1) if short[i:i + 2] in long_)
    sc = 60 * hits / (len(short) - 1)
    return sc if sc >= 60 and len(short) / len(long_) >= MIN_RATIO else 0


_cache = {}


def catalog(kind, key):
    ck = (kind, key)
    if ck not in _cache:
        rows, err = list_brand(key, limit=120)
        if err:
            print(f"  [warn] {kind}:{key} → {err}", file=sys.stderr)
            rows = []
        _cache[ck] = rows
    return _cache[ck]


def resolve(model):
    best, best_sc, tried = None, 0, []
    for kind, key in PLAN.get(model, []):
        rows = catalog(kind, key)
        tried.append(f"{kind}:{key}({len(rows)})")
        for r in rows:
            nm = r["name"]
            if ("海外" in nm or "进口" in nm) and not ("海外" in model or "进口" in model):
                continue                 # 海外版不当国产车系
            s = similarity(model, nm)
            if s > best_sc:
                best, best_sc = r, s
        if best_sc >= 95:
            break
    return (best, best_sc, tried) if best_sc >= 70 else (None, best_sc, tried)


def _lo(p):
    m = re.findall(r"\d+\.?\d*", str(p or ""))
    return float(m[0]) if m else None


def _fmt_price(s):
    """把「11.79-16.49万」统一成报告里用的「11.79–16.49」（不含单位、用连接号）。"""
    t = str(s or "").strip()
    if not t:
        return None
    t = t.replace("万", "").replace(" ", "")
    t = re.sub(r"-|~|至", "–", t)
    return t or None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--map", help='手工映射 json：{"深蓝L06": "12345"}（覆盖目录接口不稳的车系）')
    ap.add_argument("--apply-price", action="store_true",
                    help="用官方车系价覆盖 price 主字段（仅在纯电口径一致时；默认不覆盖）")
    ap.add_argument("--run-dir", help="数据目录（默认取 --src 所在目录）")
    ap.add_argument("--src", help="候选池 json（默认 <run-dir>/v3/candidates_v4_scored.json）")
    ap.add_argument("--out", help="对齐后输出 json（默认 <src 目录>/candidates_aligned.json）")
    ap.add_argument("--report", help="报告 md 路径（默认 <src 目录>/alignment_report.md）")
    ap.add_argument("--only", help="只对齐这些车型（逗号分隔），其余原样透传")
    a = ap.parse_args()

    global SRC, OUT, SPECS, REPORT, RUN
    if a.run_dir:
        RUN = os.path.abspath(a.run_dir)
    if a.src:
        SRC = os.path.abspath(a.src)
    elif a.run_dir:
        SRC = os.path.join(RUN, "v3", "candidates_v4_scored.json")
    else:
        sys.exit("需要 --src 或 --run-dir")
    base = os.path.dirname(SRC)
    OUT = os.path.abspath(a.out) if a.out else os.path.join(base, "candidates_aligned.json")
    SPECS = os.path.join(base, "specs_aligned.json")
    REPORT = os.path.abspath(a.report) if a.report else os.path.join(base, "alignment_report.md")
    only = {x.strip() for x in (a.only or "").split(",") if x.strip()}

    manual = {}
    if a.map and os.path.exists(a.map):
        manual = json.load(open(a.map, encoding="utf-8"))
        print(f"手工映射 {len(manual)} 条", file=sys.stderr)

    data = json.load(open(SRC, encoding="utf-8"))
    cands = data["candidates"]
    print(f"候选 {len(cands)} 款，开始解析…", file=sys.stderr)

    specs, rows = {}, []
    for c in cands:
        model = c["model"]
        if only and model not in only:
            rows.append({"model": model, "status": "skipped", "tried": [],
                         "reason": "--only 未列出"})
            continue
        if model in manual:
            hit, sc, tried = ({"series_id": str(manual[model]), "name": "(手工指定)"},
                              100.0, ["manual"])
        else:
            hit, sc, tried = resolve(model)
        rec = {"model": model, "old_cls": c.get("cls"), "old_price": c.get("price"),
               "tried": tried, "match_score": round(sc, 1)}
        if not hit:
            rec.update({"status": "unresolved",
                        "note": f"目录内无匹配（最佳 {sc:.0f}），保留原值"})
            rows.append(rec)
            print(f"  ✗ {model:14s} 未解析（最佳 {sc:.0f}）", file=sys.stderr)
            continue
        s, err = score_series(hit["series_id"])
        if err:
            rec.update({"status": "score_failed", "note": err,
                        "series_id": hit["series_id"], "matched": hit["name"]})
            rows.append(rec)
            print(f"  ! {model:14s} 取分失败：{err}", file=sys.stderr)
            continue

        amb = has_ev(model) and not has_ev(s["name"])
        # 车系名能匹配，但 score 页没给 level / guide_price → 视为未对齐，不能假装成功
        if not (s.get("level") or s.get("guide_price")):
            rec.update({"status": "fields_missing", "series_id": s["series_id"],
                        "matched": s["name"], "level": s.get("level"),
                        "guide_price": s.get("guide_price"),
                        "note": "车系匹配成功但官方页未提供级别/指导价（多为新车未评级）"})
            rows.append(rec)
            print(f"  ? {model:14s} → {s['name']:12s} 字段缺失", file=sys.stderr)
            continue
        rec.update({"status": "ok", "series_id": s["series_id"], "matched": s["name"],
                    "level": s["level"], "guide_price": s["guide_price"],
                    "overall": s["overall"], "pph": s["pph"],
                    "review_users": s["review_users"], "pph_usable": s["pph_usable"],
                    "competitors": s["competitors"], "url": s["url"],
                    "powertrain_ambiguous": amb,
                    "note": ("车系含插混，指导价不可直接当纯电价" if amb else "")})
        specs[model] = s
        rows.append(rec)
        print(f"  {'△' if amb else '✓'} {model:14s} → {s['name']:12s} "
              f"{(s['level'] or '—'):8s} {s['guide_price'] or '—'}", file=sys.stderr)

    json.dump(specs, open(SPECS, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    ok = [r for r in rows if r["status"] == "ok"]
    bad = [r for r in rows if r["status"] != "ok"]
    for r in ok:
        r["level_conflict"] = (str(r["old_cls"]).split("（")[0].strip() != (r["level"] or ""))
        d = None if r["powertrain_ambiguous"] else (
            None if _lo(r["old_price"]) is None or _lo(r["guide_price"]) is None
            else round(_lo(r["guide_price"]) - _lo(r["old_price"]), 2))
        r["price_delta"] = d
    conflicts = [r for r in ok if r["level_conflict"] and r["level"]]
    pph_ok = [r for r in ok if r["pph_usable"]]

    L = ["# 级别与指导价对齐报告（汽车之家官方字段）", ""]
    L.append(f"- 候选 **{len(rows)}** 款：对齐 **{len(ok)}**（其中纯电口径存疑 "
             f"{sum(1 for r in ok if r['powertrain_ambiguous'])}）、未解析 **{len(bad)}**")
    L.append(f"- **级别冲突 {len(conflicts)} 处**；价格下限有变化 "
             f"**{sum(1 for r in ok if r['price_delta'] not in (None, 0))}** 款")
    L.append("")
    L.append("> 来源：`opencli autohome score <series_id>`，免登录。")
    L.append("> `level` 为汽车之家官方二级分类，可直接作为级别闸门；`guide_price` 为厂商指导价。")
    L.append("")

    if conflicts:
        L.append("## 一、级别冲突（会改变榜单结构）")
        L.append("")
        L.append("| 候选 | 我方 cls | 官方 level | 匹配车系 |")
        L.append("|---|---|---|---|")
        for r in conflicts:
            L.append(f"| **{r['model']}** | {r['old_cls']} | **{r['level']}** | {r['matched']} |")
        L.append("")

    L.append("## 二、价格对照（官方车系价 vs 我方纯电价）")
    L.append("")
    L.append("> **不自动替换的原因**：汽车之家 `guide_price` 是**车系级**（同一车系含插混/增程版本），"
             "直接换会把纯电起价换成更低的车系起价——海豹06EV 的 10.98 万会变成「海豹06」车系的 9.68 万。")
    L.append("> 所以主字段保留纯电口径，官方值并列供核对；差异大的需要用厂家配置表确认。")
    L.append("")
    diff = [r for r in ok if r["price_delta"] not in (None, 0)]
    if diff:
        L.append("| 候选 | 我方（纯电口径） | 官方（车系级） | 下限差 | 判断 |")
        L.append("|---|---|---|---|---|")
        for r in sorted(diff, key=lambda x: -abs(x["price_delta"])):
            d = r["price_delta"]
            if r["powertrain_ambiguous"]:
                judge = "车系含插混，官方价不可直接采信"
            elif abs(d) >= 1.0:
                judge = "**差异大，须用厂家配置表核**"
            else:
                judge = "差异小，可能是我方取价有误"
            L.append(f"| {r['model']} | {r['old_price']} | {r['guide_price']} | "
                     f"**{d:+.2f}** | {judge} |")
    else:
        L.append("> 无差异。")
    same = [r for r in ok if r["price_delta"] == 0]
    if same:
        L.append("")
        L.append(f"价格下限完全一致 **{len(same)}** 款：{'、'.join(r['model'] for r in same)}。")
    L.append("")

    L.append("## 三、逐款对齐")
    L.append("")
    L.append("| 候选 | 匹配车系 | 匹配度 | 官方级别 | 原价格 | 官方指导价 | 下限差 | 备注 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for r in sorted(ok, key=lambda x: (not x["level_conflict"], x["model"])):
        d = r["price_delta"]
        dtxt = "—" if d is None else (f"**{d:+.2f}**" if abs(d) >= 0.1 else f"{d:+.2f}")
        lv = f"**{r['level']}**" if r["level_conflict"] else (r["level"] or "—")
        L.append(f"| {r['model']} | {r['matched']} | {r['match_score']:.0f} | {lv} | "
                 f"{r['old_price']} | {r['guide_price'] or '—'} | {dtxt} | {r['note'] or '—'} |")

    if bad:
        L.append("")
        L.append("## 四、未能解析（保留原值，未猜）")
        L.append("")
        L.append("| 候选 | 状态 | 尝试过 | 说明 |")
        L.append("|---|---|---|---|")
        for r in bad:
            L.append(f"| {r['model']} | {r['status']} | {'、'.join(r['tried']) or '—'} | "
                     f"{r.get('note', '')} |")
        L.append("")
        L.append("> 这些车系在汽车之家目录里不存在同名条目（可能是车系改名、子品牌未收录、"
                 "或我方命名与官方不一致）。**宁可标 unresolved 也不硬配一个名字相近的别家车。**")

    L.append("")
    L.append("## 五、PPH（百车故障数）可引用情况")
    L.append("")
    if pph_ok:
        L.append("| 候选 | PPH | 样本量 |")
        L.append("|---|---|---|")
        for r in sorted(pph_ok, key=lambda x: -(x["pph"] or 0)):
            L.append(f"| {r['model']} | **{r['pph']:.0f}** | {r['review_users']} |")
        L.append("")
    L.append(f"- 仅 **{len(pph_ok)}/{len(ok)}** 款样本量 ≥30 可引用 PPH；"
             "其余 PPH=0 应读作「没抽到」而非「零故障」。")

    open(REPORT, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"\n[ok] {REPORT}", file=sys.stderr)

    if a.report_only:
        print("\n".join(L))
        return

    for c in cands:
        r = next((x for x in rows if x["model"] == c["model"]), None)
        c["cls_prev"] = c.get("cls")
        c["price_prev"] = c.get("price")
        if r and r["status"] == "ok":
            c["cls_official"] = r["level"] or None
            c["price_official"] = (None if r["powertrain_ambiguous"]
                                   else _fmt_price(r["guide_price"]))
            c["price_official_raw"] = r["guide_price"]
            c["price_powertrain_ambiguous"] = r["powertrain_ambiguous"]
            c["level_conflict"] = r["level_conflict"] if r["level"] else None
            # 级别有官方值就换（实测 0 冲突，等于交叉验证通过）
            if r["level"]:
                c["cls"] = r["level"]
                c["cls_source"] = "autohome"
            # 价格**不自动替换**：官方 guide_price 是车系级，含插混/增程版本，
            # 直接换会把纯电起价换成更低的车系起价（海豹06EV 10.98 → 海豹06 9.68）。
            # 保留我方纯电口径，另存官方值供人工核对。
            c["price_scope"] = "车系级（含可能存在的插混/增程版本）"
            c["price_source"] = "manual(纯电口径)"
            if a.apply_price and r["guide_price"] and not r["powertrain_ambiguous"]:
                c["price"] = _fmt_price(r["guide_price"])
                c["price_source"] = "autohome(车系级指导价)"
            # PPH 与口碑
            c["evidence_status_note"] = (
                f"汽车之家口碑 {r['overall']}（样本 {r['review_users']}）"
                + (f"，PPH {r['pph']:.0f}" if r["pph_usable"] else "，PPH 样本不足"))
            c.update({"ah_series_id": r["series_id"], "ah_matched": r["matched"],
                      "ah_match_score": r["match_score"], "ah_overall": r["overall"],
                      "ah_pph": r["pph"], "ah_pph_usable": r["pph_usable"],
                      "ah_review_users": r["review_users"]})
        else:
            c["cls_official"] = None
            c["price_official"] = None
            c["level_conflict"] = None
            c["ah_status"] = (r or {}).get("status", "unplanned")

    data["alignment"] = {
        "source": "汽车之家 autohome score via opencli（免登录）",
        "aligned": len(ok),
        "powertrain_ambiguous": [r["model"] for r in ok if r["powertrain_ambiguous"]],
        "unresolved": [r["model"] for r in bad],
        "level_conflicts": [{"model": r["model"], "old": r["old_cls"], "official": r["level"]}
                            for r in conflicts],
        "note": "cls_official/price_official 为一手字段；cls_prev/price_prev 保留原值便于回溯。"
                "price_powertrain_ambiguous=True 表示车系含插混，其指导价不可当纯电价。",
    }
    json.dump(data, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[ok] {OUT}", file=sys.stderr)
    print(f"对齐 {len(ok)}/{len(rows)}；级别冲突 {len(conflicts)}；未解析 {len(bad)}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
