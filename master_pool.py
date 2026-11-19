#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""总候选池 master_pool.py —— 单一事实源，根治「每次临时拼池导致漏车」。

为什么需要（2026-11-19 T6 遗漏的直接教训）
------------------------------------------------
出「中型纯电 SUV 全价位榜」时，候选池沿用了上次「Model Y 同级事实榜」的 12 款
（偏 15–34 万），T6(14.99 万) 及整批 12–18 万车从未进池——不是算错分，是根本没被纳入。
根因是**没有总池**：每次出榜临时从某个 run 目录捡一份候选池，覆盖全靠人记得。

本模块把散落在各 run 的候选池收敛成一份持久化总池 `categories/master_pool.json`：
  - 所有搜索过 / 采集过 / 发现过的车都进池，含被排除的车（excluded 也留档，不再丢失）；
  - 每款车带**可检索标签**（级别/动力/车身/品牌/价格/证据等级）+ 完整 8 维打分与事实；
  - 提问时先按标签检索总池，命中的直接复用，缺数据的标记待补采；
  - 打分逻辑（权重/维度）变更时，用配置指纹检测并对全池重算/标记，保证口径一致。

闭环（回答用户「每次先根据提问去懂车帝销量榜获取候选，池里有的直接用，没有的重新采集」）
------------------------------------------------------------------------------------------
  1. 提问 → `discover.py` 拉懂车帝销量榜 → 得到当期在售车型清单（pool 外候选）
  2. `--discover-in discover.json` 把清单吸收进总池：已命中的刷新销量，未命中的建
     `needs_data` 档案（有名字/价格/销量，缺 scores，需重新采集 8 维证据）
  3. `--ingest <各run候选池.json>` 把任何已采集过 scores 的池吸收进总池（幂等、补空缺、记来源）
  4. `--query --cls 中型SUV --power BEV ...` 按标签检索 → 输出可直接喂 board_v2 的候选子集
  5. `--rescore --config ...` 打分逻辑变更后全池重算总分 / 标记缺维车待重采集
  6. `board_v2.py` / pipeline 用检索结果出榜——候选来源从此唯一，不再临时拼池

关键纪律（继承 skill 既有红线）
--------------------------------
  - 不猜标签：cls/动力来自候选池原始字段（官方二级分类），查不到留空并在 audit 报出；
  - 不猜分数：重算只在新逻辑权重下重算已有 scores；维度缺失标记 needs_rescore，不填假分；
  - 幂等合并：同车多次 ingest 只补空缺、不覆盖已有非空值，冲突逐条报出供人工裁决；
  - 可复现：同一总池 + 同一查询 = 同一输出；指纹变化必然触发全池体检。

CLI
---
    python3 master_pool.py --init [--out categories/master_pool.json]
    python3 master_pool.py --ingest a.json b.json ... [--out ...]
    python3 master_pool.py --discover-in discover.json [--out ...]
    python3 master_pool.py --query --cls 中型SUV --power BEV --body SUV \
                            --price-max 40 --ready-only [--out subset.json]
    python3 master_pool.py --audit
    python3 master_pool.py --stats --by cls
    python3 master_pool.py --rescore --config categories/car_ev_1218_quality.json [--apply]
    python3 master_pool.py --selftest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_POOL = os.path.join(HERE, "categories", "master_pool.json")
DEFAULT_CONFIG = os.path.join(HERE, "categories", "car_ev_1218_quality.json")

try:
    import leaderboard as LB
except ImportError:  # pragma: no cover
    LB = None


# ---------------------------------------------------------------- 工具
def canon(model: str) -> str:
    """去重键：去空格/标点/纯电后缀，保留品牌（不剥品牌前缀，避免误并不同车）。"""
    t = str(model or "").strip()
    for ch in (" ", "　", "·", "-", "_", "/", "（", "）", "(", ")", "＋", "+"):
        t = t.replace(ch, "")
    for suf in ("纯电版", "纯电动", "纯电", "EV", "ev", "BEV", "bev"):
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            t = t[: -len(suf)]
            break
    return t.lower()


def body_from_cls(cls: str) -> str:
    """从官方二级分类派生车身形态（可检索标签）。"""
    c = str(cls or "")
    if "SUV" in c or "suv" in c:
        return "SUV"
    if "MPV" in c or "mpv" in c:
        return "MPV"
    if c.endswith("车") or "轿" in c:
        return "轿车"
    return ""


def price_of(c: dict) -> float | None:
    """取评分配置价（万元）：trim.price 优先，其次 price_guide 下限，最后解析 price 文本。"""
    t = c.get("trim")
    if isinstance(t, dict):
        for k in ("price", "guide_price"):
            v = _num(t.get(k))
            if v:
                return v
    g = c.get("price_guide")
    if isinstance(g, (list, tuple)) and g:
        v = _num(g[0])
        if v:
            return v
    v = _num(c.get("price_wan"))
    if v:
        return v
    return _num_first(str(c.get("price") or ""))


def _num_first(s: str) -> float | None:
    m = re.search(r"\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def _num(v):
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return None


def config_fingerprint(cfg: dict) -> str:
    """打分逻辑指纹：维度键集合 + 权重 + 排除维。任一变化都会触发全池体检/重算。"""
    dims = cfg.get("dimensions", [])
    payload = {
        "dims": sorted(d.get("key") for d in dims),
        "weights": {d.get("key"): round(float(d.get("weight", 0)), 6) for d in dims},
        "excluded": sorted(cfg.get("excluded_dims") or []),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def load_pool(path=DEFAULT_POOL) -> dict:
    if not os.path.exists(path):
        return new_pool()
    return json.load(open(path, encoding="utf-8"))


def new_pool() -> dict:
    return {
        "meta": {
            "version": "1.0",
            "as_of": "2026-11-19",
            "description": "总候选池：所有搜索/采集/发现过的车，带可检索标签与完整打分；单一事实源。",
            "scoring": {"config": "categories/car_ev_1218_quality.json",
                        "fingerprint": "", "dim_keys": [], "weight_map": {}},
            "sources": [],
        },
        "cars": [],
    }


def save_pool(pool: dict, path=DEFAULT_POOL):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    json.dump(pool, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def index_by_key(pool: dict) -> dict[str, dict]:
    return {canon(c.get("model")): c for c in pool["cars"]}


def safety_evidence_rank(c: dict) -> int:
    """安全维证据强度（冲突时高者覆盖低者）：
      3 = 带 safety_basis=cncap/ciasi 的官方成绩对齐分（v5.9 sync_cncap 产物，权威）
      2 = crash 文本声称有官方成绩但无 basis（baseline 里的旧人工分，可能未按得分率换算）
      1 = 其他人工估分（有 confidence）
      0 = 无依据
    T5/bZ5 就栽在 rank2：crash 写了「C-NCAP 92.7%」但分数是没换算的旧人工分 9.5，
    必须让 rank3 的 v4 对齐分 9.0 覆盖它。"""
    basis = str(c.get("safety_basis") or "").lower()
    crash = str(c.get("crash") or "")
    if "cncap" in basis or "ciasi" in basis:
        return 3
    if ("C-NCAP" in crash and "无" not in crash) or ("C-IASI" in crash and "无" not in crash):
        return 2
    if c.get("safety_confidence") in ("high", "medium") or c.get("safety_basis"):
        return 1
    return 0


def make_car(c: dict, source: str, config: dict | None = None) -> dict:
    """把任意候选池的一条记录，转成总池标准条目。"""
    cls = c.get("cls") or c.get("cls_official") or ""
    power = c.get("power") or ""
    price = price_of(c)
    scores = c.get("scores") or {}
    facts = {}
    for src, dst in (("kwh", "kwh"), ("consumption", "consumption"),
                     ("cltc", "cltc"), ("cell_brand", "cell_brand"),
                     ("is_800v", "is_800v")):
        if c.get(src) is not None:
            facts[dst] = c[src]
    brand = ""
    model = str(c.get("model") or "")
    # 品牌：从候选里取，或从车名前 2-3 字推断（仅作检索标签，不参与判定）
    brand = c.get("brand") or ""
    if not brand:
        for b in ("特斯拉", "大众", "极氪", "乐道", "小鹏", "阿维塔", "蔚来", "腾势", "问界",
                  "比亚迪", "零跑", "长安启源", "深蓝", "马自达", "丰田", "极狐", "小米",
                  "别克", "日产", "红旗", "荣威", "吉利银河", "东风奕派", "广汽"):
            if model.startswith(b):
                brand = b
                break
    dim_keys = sorted((config or {}).get("dimensions", []) and
                      [d.get("key") for d in config["dimensions"]] or [])
    have = [k for k in dim_keys if k in scores]
    complete = len(dim_keys) > 0 and len(have) == len(dim_keys)
    status = "ready" if complete else ("needs_rescore" if scores else "needs_data")
    return {
        "key": canon(model),
        "model": model,
        "aliases": [a for a in (c.get("aliases") or []) if a],
        "tags": {
            "brand": brand, "cls": cls, "power": power,
            "body": body_from_cls(cls), "price_wan": price,
            "price_band": ("≤18万" if price and price <= 18 else
                           "18-28万" if price and price <= 28 else
                           ">28万" if price else ""),
            "lifecycle": str(c.get("lifecycle") or c.get("lifecycle_status") or ""),
            "evidence": c.get("evidence_status") or c.get("grade") or "",
        },
        "searchable": build_searchable(model, cls, power, brand, price),
        "scores": dict(scores),
        "adjust": float(c.get("adjust") or 0),
        "facts": facts,
        "candidate": c,  # 原始完整记录，query 输出时直接复用（喂 board_v2/score_rows）
        "provenance": [{"source": source, "ingested": "2026-11-19"}],
        "completeness": {"has_scores": bool(scores), "score_dims_present": have,
                         "has_facts": bool(facts), "has_crash": bool(c.get("crash")),
                         "safety_rank": safety_evidence_rank(c)},
        "pool_status": status,
        "score_fingerprint": config_fingerprint(config) if config else "",
        "excluded": bool(c.get("excluded")),
    }


def build_searchable(model, cls, power, brand, price) -> list[str]:
    out = [x for x in (model, cls, power, brand, body_from_cls(cls)) if x]
    if cls:
        out.append(cls.replace("SUV", "").replace("车", "").strip())  # "中型" 等
    if power == "BEV":
        out.append("纯电")
    if price:
        out.append(f"{price:g}万")
    return sorted(set(out))


def merge_car(old: dict, new: dict) -> list[str]:
    """幂等合并：补空缺、不覆盖已有非空值。返回冲突说明列表。"""
    conflicts = []
    # scores 逐维补空缺；冲突时按证据强度裁决（安全维用官方碰撞成绩覆盖旧人工分）
    for k, v in new["scores"].items():
        if k not in old["scores"]:
            old["scores"][k] = v
        elif _num(old["scores"][k]) is not None and _num(v) is not None \
                and abs(float(old["scores"][k]) - float(v)) >= 0.5:
            if k == "safety":
                o_rank = old["completeness"].get("safety_rank", 0)
                n_rank = new["completeness"].get("safety_rank", 0)
                if n_rank > o_rank:
                    old["scores"][k] = v
                    old["completeness"]["safety_rank"] = n_rank
                    old["completeness"]["has_crash"] = new["completeness"].get("has_crash", False)
                    old["candidate"]["scores"][k] = v
                    # 同步证据字段，避免「分数换了但 basis/crash 还是旧的」
                    for ek in ("crash", "safety_basis", "safety_confidence", "safety_note"):
                        if new["candidate"].get(ek) is not None:
                            old["candidate"][ek] = new["candidate"][ek]
                    conflicts.append(f"{old['model']} 维safety: ← {v}"
                                     f"（官方成绩覆盖 rank {o_rank}→{n_rank}）")
                    continue
            conflicts.append(f"{old['model']} 维{k}: {old['scores'][k]} vs {v}（保留原值）")
    # facts 补空缺
    for k, v in new["facts"].items():
        if k not in old["facts"] or old["facts"][k] in (None, ""):
            old["facts"][k] = v
    # tags 补空缺
    for k, v in new["tags"].items():
        if not old["tags"].get(k) and v:
            old["tags"][k] = v
    # 来源累加去重
    seen = {(p["source"]) for p in old["provenance"]}
    for p in new["provenance"]:
        if p["source"] not in seen:
            old["provenance"].append(p)
            seen.add(p["source"])
    # 状态升级：ready 优先
    rank = {"needs_data": 0, "needs_rescore": 1, "ready": 2}
    if rank.get(new["pool_status"], 0) > rank.get(old["pool_status"], 0):
        old["pool_status"] = new["pool_status"]
    old["searchable"] = build_searchable(old["model"], old["tags"]["cls"],
                                         old["tags"]["power"], old["tags"]["brand"],
                                         old["tags"]["price_wan"])
    old["completeness"]["score_dims_present"] = sorted(old["scores"].keys())
    old["completeness"]["has_scores"] = bool(old["scores"])
    old["completeness"]["has_facts"] = bool(old["facts"])
    return conflicts


# ---------------------------------------------------------------- 命令
def cmd_init(args):
    pool = load_pool(args.out)
    cfg = load_config(args.config)
    pool["meta"]["scoring"] = {
        "config": os.path.relpath(args.config, HERE).replace("\\", "/"),
        "fingerprint": config_fingerprint(cfg),
        "dim_keys": sorted(d.get("key") for d in cfg["dimensions"]),
        "weight_map": {d["key"]: d["weight"] for d in cfg["dimensions"]},
    }
    save_pool(pool, args.out)
    print(f"总池已就绪：{args.out}（{len(pool['cars'])} 款，指纹 {config_fingerprint(cfg)}）")
    return 0


def load_config(path=DEFAULT_CONFIG) -> dict:
    return json.load(open(path, encoding="utf-8"))


def cmd_ingest(args):
    pool = load_pool(args.out)
    cfg = load_config(args.config)
    idx = index_by_key(pool)
    srcs = pool["meta"].setdefault("sources", [])
    added, updated, all_conflicts = 0, 0, []
    for f in args.files:
        data = json.load(open(f, encoding="utf-8"))
        rows = data.get("candidates", data.get("cars", data)) if isinstance(data, dict) else data
        if isinstance(rows, dict):
            rows = list(rows.values())
        src = os.path.basename(f)
        if src not in srcs:
            srcs.append(src)
        for c in rows:
            if not isinstance(c, dict) or not c.get("model"):
                continue
            # discover 清单：只有名字/价格/销量，没有 scores → needs_data
            new = make_car(c, src, cfg)
            if "trend_sales" in c:
                new["facts"]["trend_sales"] = c["trend_sales"]
            k = new["key"]
            if k in idx:
                all_conflicts += merge_car(idx[k], new)
                updated += 1
            else:
                pool["cars"].append(new)
                idx[k] = new
                added += 1
    # 顶层 scores/adjust 是权威，回写进 candidate 供 score_rows/board_v2 使用
    for car in pool["cars"]:
        car["candidate"]["scores"] = dict(car["scores"])
        car["candidate"]["adjust"] = car["adjust"]
    pool["meta"]["as_of"] = "2026-11-19"
    save_pool(pool, args.out)
    print(f"吸收完成：新增 {added}，更新 {updated}，总池 {len(pool['cars'])} 款")
    if all_conflicts:
        print(f"冲突 {len(all_conflicts)} 条（均保留原值，需人工裁决）：")
        for x in all_conflicts[:20]:
            print("  -", x)
    return 0


def cmd_discover_in(args):
    """把 discover.py 的输出吸收进总池：命中刷新销量，未命中建 needs_data 档案。"""
    pool = load_pool(args.out)
    cfg = load_config(args.config)
    idx = index_by_key(pool)
    d = json.load(open(args.discover, encoding="utf-8"))
    rows = d.get("rows") or d.get("candidates") or (d if isinstance(d, list) else [])
    if isinstance(rows, dict):
        rows = list(rows.values())
    added = refreshed = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        model = r.get("name") or r.get("model") or r.get("series_name")
        if not model:
            continue
        k = canon(model)
        sales = _num(r.get("median_sales") or r.get("trend_sales") or r.get("sales"))
        if k in idx:
            idx[k]["facts"]["trend_sales"] = sales
            refreshed += 1
        else:
            rec = {"model": model, "cls": r.get("cls"), "power": r.get("power", "BEV"),
                   "price_guide": [r["min_price"]] if r.get("min_price") else None,
                   "trend_sales": sales, "note": "销量榜发现，待人工确认级别并采集8维证据"}
            car = make_car(rec, "discover:" + os.path.basename(args.discover), cfg)
            car["pool_status"] = "needs_data"
            pool["cars"].append(car)
            idx[k] = car
            added += 1
    save_pool(pool, args.out)
    print(f"销量榜清单吸收：刷新 {refreshed}，新建待采集 {added}，总池 {len(pool['cars'])} 款")
    return 0


def car_matches(c: dict, args) -> bool:
    t = c["tags"]
    if args.cls and not any(x in str(t.get("cls", "")) for x in args.cls.split(",")):
        return False
    if args.power and t.get("power", "").upper() not in [x.upper() for x in args.power.split(",")]:
        return False
    if args.body and t.get("body", "") not in args.body.split(","):
        return False
    if args.brand and not any(x in str(t.get("brand", "")) for x in args.brand.split(",")):
        return False
    if args.evidence and t.get("evidence", "") not in args.evidence.split(","):
        return False
    p = t.get("price_wan")
    if args.price_min and (p is None or p < args.price_min):
        return False
    if args.price_max and (p is None or p > args.price_max):
        return False
    if args.ready_only and c["pool_status"] != "ready":
        return False
    if args.keyword:
        kw = args.keyword.lower()
        hay = " ".join(map(str, [c["model"]] + c["aliases"] + c["searchable"])).lower()
        if kw not in hay:
            return False
    return True


def cmd_query(args):
    pool = load_pool(args.pool if hasattr(args, "pool") else DEFAULT_POOL)
    hits = [c for c in pool["cars"] if car_matches(c, args)]
    # 按 config 权重算总分排序（不重写打分：直接复用 score_rows）
    cfg = load_config(args.config)
    cands = [c["candidate"] for c in hits]
    if LB and cands:
        try:
            rows = LB.score_rows(cands, cfg)
            tot = {r["model"]: r["total"] for r in rows}
            hits.sort(key=lambda c: -tot.get(c["model"], -1))
        except Exception:
            hits.sort(key=lambda c: c["tags"]["price_wan"] or 0)
    if args.out:
        out = [c["candidate"] for c in hits]
        json.dump({"candidates": out}, open(args.out, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print(f"检索命中 {len(hits)} 款 → {args.out}（可直接喂 board_v2.py / pipeline.py）")
    else:
        for c in hits:
            t = c["tags"]
            print(f"  {c['model']:<18} {t.get('cls',''):<8} {t.get('power',''):<5} "
                  f"{str(t.get('price_wan') or '-'):>7}万  {c['pool_status']:<14} "
                  f"{','.join(t.get('brand','').split()[:1])}")
        print(f"命中 {len(hits)} 款")
    return 0


def cmd_audit(args):
    pool = load_pool(args.pool if hasattr(args, "pool") else DEFAULT_POOL)
    cfg = load_config(args.config)
    fp = config_fingerprint(cfg)
    cur = pool["meta"]["scoring"].get("fingerprint", "")
    print(f"总池：{len(pool['cars'])} 款 | 配置指纹 当前={fp} 池记录={cur} "
          f"{'✓一致' if fp == cur else '⚠不一致，需 --rescore'}")
    st = Counter(c["pool_status"] for c in pool["cars"])
    print("状态分布：", dict(st))
    miss_cls = [c["model"] for c in pool["cars"] if not c["tags"].get("cls")]
    miss_price = [c["model"] for c in pool["cars"] if not c["tags"].get("price_wan")]
    stale = [c["model"] for c in pool["cars"]
             if c["score_fingerprint"] and c["score_fingerprint"] != fp]
    needs = [c["model"] for c in pool["cars"] if c["pool_status"] == "needs_data"]
    print(f"缺级别 {len(miss_cls)}：{miss_cls[:8]}")
    print(f"缺价格 {len(miss_price)}：{miss_price[:8]}")
    print(f"指纹过期 {len(stale)}：{stale[:8]}")
    print(f"待采集 {len(needs)}：{needs[:12]}")
    # 价位段覆盖（防「全价位榜却只有高价车」这类遗漏复发）
    band = Counter(c["tags"]["price_band"] for c in pool["cars"] if c["tags"].get("price_band"))
    print("价位段覆盖：", dict(band))
    return 0


def cmd_stats(args):
    pool = load_pool(args.pool if hasattr(args, "pool") else DEFAULT_POOL)
    key = args.by
    cnt = Counter((c["tags"].get(key) or "(空)") for c in pool["cars"])
    print(f"按 {key} 分布（共 {len(pool['cars'])} 款）：")
    for k, n in cnt.most_common():
        print(f"  {str(k):<16} {n}")
    return 0


def cmd_rescore(args):
    """打分逻辑变更后全池重算：权重变化重算总分；维度缺失标记 needs_rescore 不猜分。"""
    pool = load_pool(args.pool if hasattr(args, "pool") else DEFAULT_POOL)
    cfg = load_config(args.config)
    fp = config_fingerprint(cfg)
    dim_keys = [d["key"] for d in cfg["dimensions"] if d["key"] not in (cfg.get("excluded_dims") or [])]
    cands, regraded, flagged = [], 0, []
    for c in pool["cars"]:
        if not c["scores"]:
            continue
        missing = [k for k in dim_keys if k not in c["scores"]]
        if missing:
            flagged.append((c["model"], missing))
            if args.apply:
                c["pool_status"] = "needs_rescore"
            continue
        cands.append(c["candidate"])
        regraded += 1
    totals = {}
    if LB and cands:
        rows = LB.score_rows(cands, cfg)
        totals = {r["model"]: r["total"] for r in rows}
    print(f"新逻辑重算（指纹 {fp}）：可重算 {regraded} 款，缺维待重采集 {len(flagged)} 款")
    for m, miss in flagged[:15]:
        print(f"  ⚠ {m} 缺维 {miss}")
    if args.apply:
        for c in pool["cars"]:
            if c["model"] in totals:
                c["candidate"]["adjust"] = c["adjust"]  # 保证 adjust 与池一致
                c["score_fingerprint"] = fp
                if c["pool_status"] == "needs_rescore":
                    c["pool_status"] = "ready"
        pool["meta"]["scoring"] = {
            "config": os.path.relpath(args.config, HERE).replace("\\", "/"),
            "fingerprint": fp, "dim_keys": sorted(dim_keys),
            "weight_map": {d["key"]: d["weight"] for d in cfg["dimensions"]},
        }
        pool["meta"]["as_of"] = "2026-11-19"
        save_pool(pool, args.pool if hasattr(args, "pool") else DEFAULT_POOL)
        print(f"已写回：{regraded} 款标记新指纹，{len(flagged)} 款待重采集")
    else:
        print("（dry-run，未写回；加 --apply 生效）")
    return 0


def selftest():
    print("=== master_pool selftest ===")
    p = new_pool()
    cfg = load_config(DEFAULT_CONFIG)
    dims = [d["key"] for d in cfg["dimensions"]]
    # 1 去重键
    assert canon("大众 ID.AURA T6") == canon("大众ID.AURA T6"), "canon 空格"
    assert canon("零跑C11纯电版") == canon("零跑C11"), "canon 纯电后缀"
    assert canon("MG 07 EV") == canon("MG07"), "canon EV后缀"
    print("  [1] 去重键 ✓")
    # 2 车身派生
    assert body_from_cls("中型SUV") == "SUV"
    assert body_from_cls("中型车") == "轿车"
    assert body_from_cls("MPV") == "MPV"
    print("  [2] 车身派生 ✓")
    # 3 价格提取
    assert price_of({"price": "26.35万"}) == 26.35
    assert price_of({"price_guide": [11.79, 16.49]}) == 11.79
    assert price_of({"trim": {"price": 12.99}}) == 12.99
    print("  [3] 价格提取 ✓")
    # 4 指纹随权重变化
    cfg2 = json.loads(json.dumps(cfg))
    cfg2["dimensions"][0]["weight"] *= 1.5
    assert config_fingerprint(cfg) != config_fingerprint(cfg2), "指纹应随权重变化"
    print("  [4] 配置指纹 ✓")
    # 5 make_car 完整性判定
    full = {k: 8.0 for k in dims}
    a = make_car({"model": "测试A", "cls": "中型SUV", "power": "BEV",
                  "scores": full, "trim": {"price": 20}}, "t.json", cfg)
    b = make_car({"model": "测试B", "cls": "中型车", "power": "BEV",
                  "scores": {"powertrain": 8}, "trim": {"price": 15}}, "t.json", cfg)
    assert a["pool_status"] == "ready", a["pool_status"]
    assert b["pool_status"] == "needs_rescore", b["pool_status"]
    assert a["tags"]["body"] == "SUV"
    print("  [5] 完整性/状态判定 ✓")
    # 6 幂等合并
    before = dict(a["scores"])
    conflicts = merge_car(a, make_car({"model": "测试A", "scores": full,
                                       "cell_brand": "宁德时代"}, "b.json", cfg))
    assert a["scores"] == before, "合并不应覆盖已有分"
    assert a["facts"].get("cell_brand") == "宁德时代", "合并应补空缺"
    assert len(a["provenance"]) == 2
    print("  [6] 幂等合并 ✓")
    # 7 检索
    p["cars"] = [a, b]
    ns = argparse.Namespace(cls="中型SUV", power="BEV", body="SUV", brand="", evidence="",
                            price_min=None, price_max=None, ready_only=True, keyword="")
    hits = [c for c in p["cars"] if car_matches(c, ns)]
    assert len(hits) == 1 and hits[0]["model"] == "测试A"
    print("  [7] 标签检索 ✓")
    print("全部通过 ✓")


def main():
    ap = argparse.ArgumentParser(description="总候选池：单一事实源 + 标签检索 + 逻辑变更全池重算")
    ap.add_argument("--out", default=DEFAULT_POOL, help="总池文件路径")
    ap.add_argument("--pool", default=DEFAULT_POOL, help="query/audit/stats/rescore 读取的总池")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="打分配置")
    sub = ap.add_mutually_exclusive_group(required=True)
    sub.add_argument("--init", action="store_true")
    sub.add_argument("--ingest", nargs="+", metavar="FILE")
    sub.add_argument("--discover-in", metavar="FILE")
    sub.add_argument("--query", action="store_true")
    sub.add_argument("--audit", action="store_true")
    sub.add_argument("--stats", action="store_true")
    sub.add_argument("--rescore", action="store_true")
    sub.add_argument("--selftest", action="store_true")
    # query 过滤器
    ap.add_argument("--cls", help="级别关键词，逗号分隔（中型SUV,中型车）")
    ap.add_argument("--power", help="动力（BEV,PHEV,EREV）")
    ap.add_argument("--body", help="车身（SUV,轿车,MPV）")
    ap.add_argument("--brand", help="品牌关键词")
    ap.add_argument("--evidence", help="证据等级（A,B,C）")
    ap.add_argument("--price-min", type=float)
    ap.add_argument("--price-max", type=float)
    ap.add_argument("--keyword", help="全文关键词（车名/别名/标签）")
    ap.add_argument("--ready-only", action="store_true", help="只要数据齐全可直接出榜的车")
    ap.add_argument("--out-subset", help="query 结果输出到文件（候选池格式）")
    ap.add_argument("--by", default="cls", help="stats 分组字段")
    ap.add_argument("--apply", action="store_true", help="rescore 写回（默认 dry-run）")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.init:
        return cmd_init(a)
    if a.ingest:
        a.files = a.ingest
        return cmd_ingest(a)
    if a.discover_in:
        a.discover = a.discover_in
        return cmd_discover_in(a)
    if a.query:
        a.out = a.out_subset
        return cmd_query(a)
    if a.audit:
        return cmd_audit(a)
    if a.stats:
        return cmd_stats(a)
    if a.rescore:
        return cmd_rescore(a)
    return 1


if __name__ == "__main__":
    sys.exit(main())
