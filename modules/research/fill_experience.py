# -*- coding: utf-8 -*-
"""fill_experience.py —— 批量补「证据类字段」到候选池（可复用采集器）

为什么需要（2026-11-21 用户：「补数据吧，但是要做到 research 模块里面可以复用」）：
  维度封顶（evidence_gate）只解决「没证据不能声称更强」，但覆盖率低时会把大量车压到同一水位。
  真正的解法是**把证据补上来**：quality 维度要 PPH 样本、体验维度要独立实测/车主样本。
  这里先做最能自动化的一项——用汽车之家口碑的 PPH + 样本量回填 quality 证据
  （score_series 已封装 opencli autohome score，PPH_MIN_SAMPLE=30 才可引用）。

复用方式：
  · 作为库：`from modules.research.fill_experience import fill_pph` → fill_pph(pool, apply=True)
  · 作为 CLI：python3 fill_experience.py --check / --apply [--models 小鹏MONA L05,极氪7X]

纪律：
  · 只写「采集到的事实」（pph / review_users / pph_usable），不改任何 scores。
  · 样本 <30 一律标 pph_usable=False（样本 4 条的 PPH=0 不是「零故障」）。
  · 拿不到就是拿不到，不猜、不填默认值。

体验实测提取（2026-11-21，用户质疑「T6 麋鹿 80.02 为何没录入」根因修复）：
  fill_pph 只能补口碑 PPH，抓不到「麋鹿 XX km/h / 城市NOA 实车」这类写在试驾稿
  里的实测——T6 就是这样漏的：数据在外部媒体稿，候选池 note 没有、证据库也没有，
  检测器两头都扫不到。extract_driving_evidence() 把「从任意文本提取实测」做成
  research 层可复用能力：给它一段试驾稿（research 检索到的正文），它产出**候选
  证据条目**（dimension/kind/数值/原文/来源/url/待核实标记）。
  **只提取、不入库、不打分**——麋鹿数字可能是厂商宣传口径，必须由人核实来源后
  才能写进 experience_facts.json（user_review/independent_test 的判定是人工的）。
  这样「数据没录入」的根因（没有可复用的文本提取器）被代码消除：以后任何车入池，
  研究流程都能用同一个函数扫试驾稿，人工据此核实补录。
"""
from __future__ import annotations

import argparse
import json
import re
import sys

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)


# ---- 体验实测文本提取（可复用，纯函数，零网络零 IO）----
# 正则与 cross_verify.check_experience_gaps 共用同一套模式——那边检测缺口、
# 这边提取候选证据，必须同一口径，否则「检测到的」和「能提取的」对不上。
_MOOSE_RE = re.compile(r"麋鹿[^0-9]{0,8}(\d{2,3}(?:\.\d+)?)\s*(?:km\s*/?\s*h|公里)")
_NOA_RE = re.compile(r"(城市\s*NOA|城区\s*NOA)[^。；]{0,16}"
                     r"(实测|实车|试驾|路测|表现|体验)")


def extract_driving_evidence(text: str, model: str = "", url: str = "",
                             source: str = "research_text") -> list[dict]:
    """从一段试驾/实测稿正文提取候选体验证据条目（**不入库、不打分**）。

    Returns: [{model, dimension, kind, value, claim, source, source_type, url, verification}]
      - control/moose_test: 捕获麋鹿成绩 km/h，附上下文原句
      - adas/city_noa_review: 捕获城市NOA 实测描述
      - verification 恒为 "pending"：提醒调用方（人工/上游）核实来源与口径后才能
        写进 experience_facts.json。厂商宣称的麋鹿应标 up_review 且降权，
        第三方独立横评才标 independent_test——这个判断不由正则做。

    与 cross_verify 的缺口检测互为镜像：检测层说「这段文本有实测但库里没有」，
    本函数说「这段文本里实测是什么」，合起来构成「发现→提取→人工核实→入库」闭环。
    """
    text = str(text or "")
    out: list[dict] = []
    m = _MOOSE_RE.search(text)
    if m:
        s, e = max(0, m.start() - 12), min(len(text), m.end() + 40)
        out.append({
            "model": model, "dimension": "control", "kind": "moose_test",
            "value": float(m.group(1)), "unit": "km/h",
            "claim": text[s:e].strip(),
            "source": source, "source_type": "pending", "url": url,
            "verification": "pending",
            "note": "麋鹿成绩可能是厂商公布或第三方实测，需核实来源后定 "
                    "up_review(降权) / independent_test，再写入 experience_facts",
        })
    n = _NOA_RE.search(text)
    if n:
        s, e = max(0, n.start() - 8), min(len(text), n.end() + 60)
        out.append({
            "model": model, "dimension": "adas", "kind": "city_noa_review",
            "value": None, "claim": text[s:e].strip(),
            "source": source, "source_type": "pending", "url": url,
            "verification": "pending",
            "note": "城市NOA 实车描述，需核实是否有接管里程/通过率等量化数据，"
                    "据此定方向后再写入 experience_facts",
        })
    return out


def driving_evidence_selftest() -> int:
    """T6 场景回归：从试驾稿提取麋鹿+NOA，且不自动入库/不打分的契约。"""
    article = ("ID.AURA T6 麋鹿测试 80.02km/h 表现亮眼；"
               "城市NOA实车路测风格谨慎保守，路口黄灯多数刹停。")
    got = extract_driving_evidence(article, model="大众ID.AURA T6",
                                   url="https://example.com/t6")
    assert len(got) == 2, got
    moose = [x for x in got if x["kind"] == "moose_test"][0]
    assert moose["value"] == 80.02 and moose["dimension"] == "control", moose
    assert moose["verification"] == "pending", "提取结果必须标记待核实"
    assert all(x["source_type"] == "pending" for x in got), "不允许提取器判定来源类型"
    # 无实测的普通文本 → 空
    assert extract_driving_evidence("这款车空间很大，价格厚道。") == []
    # 麋鹿正则不误伤百分比/快充（与 crash_systems 同纪律）
    assert extract_driving_evidence("充电 10-80% 用时 22 分钟") == []
    return 0


_IDX = None


def _index():
    """懒加载汽车之家车系索引（与 auto_trims 同一份缓存）。"""
    global _IDX
    if _IDX is None:
        p = paths.ROOT / "runs" / "_ah_index" / "ah_grade_index.json"
        _IDX = json.load(open(p, encoding="utf-8")) if p.exists() else {}
    return _IDX


def resolve_sid(model: str) -> str:
    """缺 ah_series_id 时用「本地索引 → 站点检索」补，与 auto_trims 共用同一套寻址。"""
    from modules.research import auto_trims as AT
    idx = _index()
    if idx:
        sid, _n, _h = AT.find_sid_local(model, idx)
        if sid:
            return str(sid)
    sid, _n, _h = AT.find_sid_remote(model)
    return str(sid or "")


def _score_one(sid: str):
    from modules.research import fetch_specs as FS
    return FS.score_series(sid)


def fill_pph(pool: dict, apply: bool = False, models=None, limit=None) -> dict:
    """给候选车补 PPH 样本证据（原地改 candidate，apply=False 时只报告）。

    返回 {"filled": [...], "no_sid": [...], "failed": [...], "skipped": [...]}
    """
    out = {"filled": [], "no_sid": [], "failed": [], "skipped": []}
    n = 0
    for car in pool.get("cars", []):
        c = car.get("candidate") or {}
        model = car.get("model") or c.get("model")
        if models and model not in models:
            continue
        if limit and n >= limit:
            break
        if c.get("ah_pph_usable"):
            out["skipped"].append(model)
            continue
        sid = str(c.get("ah_series_id") or "").strip()
        if not sid:
            sid = resolve_sid(model)
            if sid and apply:
                c["ah_series_id"] = sid
                c["ah_series_id_source"] = "索引/检索补采（fill_experience）"
        if not sid:
            out["no_sid"].append(model)
            continue
        n += 1
        rec, err = _score_one(sid)
        if err or not rec:
            out["failed"].append({"model": model, "sid": sid, "err": err or "无返回"})
            continue
        users = int(rec.get("review_users") or 0)
        pph = rec.get("pph")
        usable = bool(rec.get("pph_usable"))
        if apply:
            c["ah_pph"] = pph
            c["ah_review_users"] = users
            c["ah_pph_usable"] = usable
            c["ah_pph_note"] = rec.get("pph_note") or ""
            c["ah_overall"] = rec.get("overall")
        out["filled"].append({"model": model, "pph": pph, "users": users, "usable": usable,
                              "note": rec.get("pph_note") or ""})
    return out


def report(r: dict) -> str:
    L = [f"PPH 补采：成功 {len(r['filled'])} / 无可引用（样本不足）{len(r['skipped'])}"
         f" / 缺 series_id {len(r['no_sid'])} / 失败 {len(r['failed'])}"]
    for x in r["filled"]:
        flag = "可用" if x["usable"] else "样本不足"
        L.append(f"  {x['model']:<18} PPH={x['pph']} 样本={x['users']} → {flag}")
    for x in r["failed"][:5]:
        L.append(f"  ✗ {x['model']} ({x['sid']}): {x['err']}")
    if r["no_sid"]:
        L.append("  缺 series_id（先用 auto_trims 的索引兜底补 ah_series_id）："
                 + "、".join(r["no_sid"][:8]))
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="补 PPH/口碑样本证据到候选池")
    ap.add_argument("--selftest", action="store_true", help="体验实测提取器自测")
    ap.add_argument("--pool", default=str(paths.cat("master_pool.json")))
    ap.add_argument("--models", help="只处理指定车型（逗号分隔）")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--apply", action="store_true", help="写回池（默认 dry-run）")
    a = ap.parse_args(argv)
    if a.selftest:
        driving_evidence_selftest()
        print("[fill_experience] 体验实测提取器自测通过")
        return 0

    pool = json.load(open(a.pool, encoding="utf-8"))
    models = [x.strip() for x in a.models.split(",")] if a.models else None
    r = fill_pph(pool, apply=a.apply, models=models, limit=a.limit)
    print(report(r))
    if a.apply and r["filled"]:
        # 不 import board（那是向上依赖，layout_regress 会拦）：
        # 本模块只改 candidate 的证据字段，不引入新形状，直接序列化即可。
        json.dump(pool, open(a.pool, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"已写回 {a.pool}")
    elif r["filled"]:
        print("dry-run；加 --apply 写回")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
