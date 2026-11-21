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
"""
from __future__ import annotations

import argparse
import json
import sys

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)


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
    ap.add_argument("--pool", default=str(paths.cat("master_pool.json")))
    ap.add_argument("--models", help="只处理指定车型（逗号分隔）")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--apply", action="store_true", help="写回池（默认 dry-run）")
    a = ap.parse_args(argv)

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
