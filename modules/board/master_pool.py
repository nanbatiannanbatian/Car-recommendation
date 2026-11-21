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
  1. 提问 → `--intake` 一条命令拉懂车帝四口径逐月榜 → 按现行「有效销量」口径
     （逐月中位数 + 新车爬坡取最新月，复用 fetch_dcd_sales._trend_pick）算有效销量
     → 达标的：池内命中直接刷新销量/标签，池外未命中建 `needs_data` 档案待采 8 维证据。
     ⚠ 这是按需触发的 skill 命令，不挂任何 automation/定时器——每次提问手动调用。
     `--sales-cache <快照.json>` 可离线复现；不给则实时联网拉榜。默认 dry-run，`--apply` 写回。
  2. `--discover-in discover.json`（旧入口，现由 --intake 取代）吸收已算好的清单
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
    python3 master_pool.py --intake [--sales-cache 快照.json] [--month 202610] [--months 6] \
                            [--min-sales 1000] [--power BEV] [--price-max 40] [--apply]
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
import statistics
import sys
from collections import Counter, defaultdict

import paths

HERE = str(paths.ROOT)
DEFAULT_POOL = str(paths.cat("master_pool.json"))
DEFAULT_CONFIG = str(paths.cat("car_ev_1218_quality.json"))

try:
    from modules.engine import leaderboard as LB
except ImportError:  # pragma: no cover
    LB = None


# ---------------------------------------------------------------- 工具
# 车名归一化单一事实源（v10：原先写在本文件，cross_verify 反向 import 造成依赖倒挂）
from modules.contracts.model_names import canon  # noqa: E402


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


# 价格/配置形状的读取与归一化统一走契约层（v10.1：原来这里自己解析，
# 与 trim_annotate / leaderboard 各写一套，遇到 price="24.98万" 或 trim 是字符串就崩）
from modules.contracts.candidate_schema import (  # noqa: E402
    num as _num, normalize_candidate, price_of, trim_of,
)


def backfill_price_guide(pool: dict, trims_path: str | None = None):
    """池级包装：实现与出榜链路共用同一份（engine/facts_sync），不各写一套。"""
    from modules.engine.facts_sync import backfill_price_guide_from_trims
    filled, _still = backfill_price_guide_from_trims(
        [car.get("candidate") or {} for car in pool.get("cars", [])], trims_path)
    return filled


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
    pool = json.load(open(path, encoding="utf-8"))
    # 读入即归一化：历史池子里 price 可能是 "24.98万"、trim 可能是纯字符串，
    # 不归一化会在下游 `t.get("price") - hint` 处崩（2026-11-21 实际踩到）。
    for car in pool.get("cars", []):
        normalize_candidate(car.get("candidate") or {})
    return pool


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
    # 门禁：写池前先归一化 + 体检，脏形状（price 字符串 / trim 字符串）不许入库。
    # 出过事：原始记录直接塞进 candidate，下游 `price - hint` TypeError 整个出榜崩掉。
    from modules.contracts.candidate_schema import check_candidate, normalize_candidate
    bad = []
    for car in pool.get("cars", []):
        cand = car.get("candidate") or {}
        normalize_candidate(cand)
        errs = check_candidate(cand)
        if errs:
            bad.append(f"{car.get('model')}: {'; '.join(errs)}")
    if bad:
        raise SystemExit("候选池形状门禁未通过（先修数据再写池）：\n  " + "\n  ".join(bad[:10]))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    json.dump(pool, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def save_pool_split(pool: dict, membership_path: str | None = None,
                    generated_path: str | None = None) -> dict:
    """**按来源分流写池**（2026-11-22 P3）：人工层写 membership，派生层写生成物。

    为什么分两处写（一个 owner + 一条写路径）：
      原来 master_pool.json 一个文件里混着人工判断（谁入池/别名/人工基准分/
      人工调整额）与派生事实（算出来的分/电芯/销量），两边都能写 →
      人工改一个字段，build 一跑就被覆盖；build 写回又把人工值冲掉。

    分流后：
      · categories/pool_membership.json  人工层（唯一可写，随包）
      · data/gen/master_pool.json        生成层（只读派生，可重建，不随包）
    """
    from modules.board import pool_split as PS
    mem_cars, gen_cars = [], []
    for c in pool.get("cars", []):
        m = {k: c.get(k) for k in PS.MANUAL_FIELDS if k in c}
        m["key"] = c.get("key")
        mem_cars.append(m)
        g = {k: c.get(k) for k in PS.GENERATED_FIELDS if k in c}
        g["model"] = c.get("model")
        g["key"] = c.get("key")
        gen_cars.append(g)
    mp = membership_path or PS.MEMBERSHIP
    gp = generated_path or PS.GENERATED
    meta = pool.get("meta") or {}
    PS._write(mp, {"note": "候选池人工层（唯一可写）：谁入池/别名/标签/人工基准分/"
                          "评分配置/生命周期/人工调整额。派生事实在 data/gen/master_pool.json。",
                   "version": "1.0.0", "updated": "2026-11-22",
                   "meta": meta, "cars": mem_cars})
    PS._write(gp, {"note": "候选池生成层（只读派生，可重建）：由 pool_membership + 事实库合成，"
                          "不要手改。",
                   "version": "1.0.0", "updated": "2026-11-22",
                   "meta": meta, "cars": gen_cars})
    return {"membership": mp, "generated": gp,
            "n_membership": len(mem_cars), "n_generated": len(gen_cars)}


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
    save_pool_split(pool)
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
    save_pool_split(pool)
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
    save_pool_split(pool)
    print(f"销量榜清单吸收：刷新 {refreshed}，新建待采集 {added}，总池 {len(pool['cars'])} 款")
    return 0


# ---------------------------------------------------------------- 按需候选摄入（intake）
# 用户要求的 skill 模式（2026-11-19，明确不挂 automation）：
#   提问 → 拉懂车帝等销量榜 → 按现行「有效销量」口径计算 → 达标的纳入总池
#   → 池里有的直接用（刷新销量/标签），没有的建 needs_data 档案（待采 8 维证据）
# 有效销量口径与 fetch_dcd_sales._trend_pick 完全一致（逐月中位数 + 新车爬坡取最新月），
# 直接 import 复用，不重造，避免两处口径漂移。

def effective_sales(series_by_month: dict, lifecycle: str = "") -> tuple[int, str]:
    """按现行口径算有效销量：逐月中位数；新车爬坡期取中位与最新月的较大值。

    直接复用 fetch_dcd_sales._trend_pick —— 这是 skill 里 trend_sales 的唯一口径来源。
    series_by_month: {"2026-05": 6723, ...}（值为正数）。
    返回 (有效销量, 口径说明)。
    """
    vals = {m: int(v) for m, v in (series_by_month or {}).items() if (v or 0) > 0}
    if not vals:
        return 0, "无销量"
    med0 = round(statistics.median(list(vals.values())))
    try:
        from modules.research import fetch_dcd_sales as FD
        eff, how = FD._trend_pick({"lifecycle": {"status": lifecycle}}, vals, med0, "有效销量")
        return int(eff), how
    except Exception:
        # 兜底：与 _trend_pick 同逻辑（fetch 模块不可用时）
        lc = str(lifecycle or "").lower()
        if lc == "new_launch" and len(vals) >= 2:
            latest_k = max(vals)
            if vals[latest_k] > med0:
                return vals[latest_k], f"有效销量新车爬坡，取最新月 {latest_k}（中位 {med0:,} 已低估）"
        return med0, f"有效销量{len(vals)}个月中位数"


def _load_sales_rows(args) -> list[dict]:
    """两种销量来源：① --sales-cache 读已落盘的懂车帝逐月榜快照（离线可复现）；
    ② 否则调用 fetch_dcd_sales 实时拉四口径逐月榜。返回统一行结构：
       {name, brand, series_id, months:{bev/phev/all -> {月:销量}}, min_price, max_price}"""
    if getattr(args, "sales_cache", None):
        d = json.load(open(args.sales_cache, encoding="utf-8"))
        rows = d.get("rows", d.get("data", d if isinstance(d, list) else []))
        if isinstance(rows, dict):
            rows = list(rows.values())
        return [r for r in rows if isinstance(r, dict) and (r.get("name") or r.get("series_name"))]

    # 实时拉（按需，仅在用户点名搜榜时执行；本命令本身不挂任何定时器）
    from modules.research import fetch_dcd_sales as FD
    months = [str(m) for m in FD.month_list(args.month, args.months)]
    series: dict[str, dict] = {}
    for m in months:
        for ne in ("1", "2", ""):
            rows, _ = FD.fetch_all(m, ne)
            bucket = {"1": "bev", "2": "phev", "": "all"}[ne]
            for r in rows:
                k = FD.norm(r["series_name"])
                rec = series.setdefault(k, {"name": r["series_name"], "brand": r.get("brand", ""),
                                            "series_id": r.get("series_id"),
                                            "months": {"bev": {}, "phev": {}, "all": {}},
                                            "min_price": r.get("min_price"), "max_price": r.get("max_price")})
                rec["months"][bucket][m] = r.get("sales") or 0
    return list(series.values())


def _row_to_sales(row: dict) -> dict | None:
    """把一行榜单算出有效销量 + 动力判定。纯电榜优先 → 插混 → 全部榜兜底(车系合计上界)。
    返回 {name, brand, power, eff, method, scope, min_price, max_price, series_id} 或 None。"""
    name = row.get("name") or row.get("series_name")
    if not name:
        return None
    months = row.get("months") or {}
    bev = {m: v for m, v in months.get("bev", {}).items() if (v or 0) > 0}
    phev = {m: v for m, v in months.get("phev", {}).items() if (v or 0) > 0}
    allv = {m: v for m, v in months.get("all", {}).items() if (v or 0) > 0}
    lc = str(row.get("lifecycle") or "")
    out = {"name": name, "brand": row.get("brand", ""), "series_id": row.get("series_id"),
           "min_price": row.get("min_price"), "max_price": row.get("max_price")}
    if bev:
        eff, how = effective_sales(bev, lc)
        out.update(power="BEV", eff=eff, method=how, scope="pure_bev")
    elif phev and not allv:
        out.update(power="PHEV", eff=0, method="仅插混榜命中", scope="phev")
    elif allv:
        eff, how = effective_sales(allv, lc)
        out.update(power="", eff=eff, method=how + "（非纯电）", scope="series_total")
    else:
        return None
    return out


def cmd_intake(args):
    """按需候选摄入：搜榜 → 有效销量 → 达标筛选 → 池内复用/池外建档。默认 dry-run。"""
    import json as _json
    # 销量月份时效（2026-11-20 用户强制）：auto = 探测"上个月，月初未出取上上个月"，不硬编码历史月。
    if str(args.month).lower() == "auto":
        if args.sales_cache:
            raise SystemExit("--month auto 与 --sales-cache 冲突：离线快照请显式指定 --month")
        from modules.board import monthly as _M
        # 只探测"上个月→上上个月"两个月：上上个月销量一定已发布，不需要再往前回退。
        ym, note = _M.probe_month(None, prefer=None, max_back=2)
        print(f"[intake] 销量口径月份自动探测 = {note}")
        args.month = str(ym)
        args._sales_month_note = note
    pool = load_pool(args.pool)
    cfg = load_config(args.config)
    idx = index_by_key(pool)

    rows = _load_sales_rows(args)
    sales = [s for s in (_row_to_sales(r) for r in rows) if s]
    # 只保留纯电有效供给：BEV 直接命中；series_total 保留但标注上界；PHEV-only 剔除
    sales = [s for s in sales if s["power"] != "PHEV"]
    for s in sales:
        if s["scope"] == "series_total":
            s["sales_note"] = "懂车帝三动力细分榜均未收录（一车多动力未拆名），有效销量为车系合计上限，纯电≤该值"

    # 达标筛选：有效销量门槛 + 价格带 + 动力（提问条件）
    qualified, rejected = [], Counter()
    for s in sales:
        if s["eff"] < args.min_sales:
            rejected["销量不足"] += 1
            continue
        mp = _num(s.get("min_price"))
        if args.price_min and (mp is None or mp < args.price_min):
            rejected["价格低于下界"] += 1
            continue
        if args.price_max and (mp is None or mp > args.price_max):
            rejected["价格高于上界"] += 1
            continue
        if args.power and s["power"] and s["power"].upper() not in [x.upper() for x in args.power.split(",")]:
            rejected["动力不符"] += 1
            continue
        qualified.append(s)

    # 池内命中：刷新销量/标签，直接用；池外：建 needs_data 档案待采
    reused, created = [], []
    src = os.path.basename(args.sales_cache) if getattr(args, "sales_cache", None) else \
          f"dcd_live:{args.month}x{args.months}"
    for s in qualified:
        k = canon(s["name"])
        facts = {"trend_sales": s["eff"], "trend_method": s["method"],
                 "sales_scope": s["scope"], "sales_source": src}
        if s.get("sales_note"):
            facts["sales_note"] = s["sales_note"]
        if s.get("min_price") is not None:
            facts["min_price"] = s["min_price"]
        if k in idx:
            car = idx[k]
            for fk, fv in facts.items():
                car["facts"][fk] = fv
            if not car["tags"].get("price_wan") and s.get("min_price") is not None:
                car["tags"]["price_wan"] = _num(s["min_price"])
            if s.get("brand") and not car["tags"].get("brand"):
                car["tags"]["brand"] = s["brand"]
            car["searchable"] = build_searchable(car["model"], car["tags"]["cls"],
                                                 car["tags"]["power"], car["tags"]["brand"],
                                                 car["tags"]["price_wan"])
            srcs = car["meta"]["source_history"] if "meta" in car else None
            reused.append(s["name"])
        else:
            rec = {"model": s["name"], "brand": s["brand"], "power": s["power"] or "BEV",
                   "price_guide": [_num(s["min_price"])] if s.get("min_price") is not None else None,
                   "trend_sales": s["eff"], "note": "销量榜发现入池，级别待确认、8维证据待采集"}
            car = make_car(rec, "intake:" + src, cfg)
            car["pool_status"] = "needs_data"
            for fk, fv in facts.items():
                car["facts"][fk] = fv
            pool["cars"].append(car)
            idx[k] = car
            created.append(s["name"])

    if args.apply:
        for car in pool["cars"]:
            car["candidate"]["scores"] = dict(car["scores"])
            car["candidate"]["adjust"] = car["adjust"]
        pool["meta"]["as_of"] = "2026-11-19"
        save_pool_split(pool)

    # 报告：待采集清单（池外新建）+ 可直接出榜子集（池内 ready）
    report = {
        "meta": {"source": src, "as_of": args.month, "months": args.months,
                 "min_sales": args.min_sales, "price": [args.price_min, args.price_max],
                 "power": args.power or "全部", "applied": bool(args.apply),
                 "effective_sales_method": "逐月中位数 + 新车爬坡取最新月（fetch_dcd_sales._trend_pick）"},
        "summary": {"榜单行": len(rows), "有有效销量": len(sales), "达标": len(qualified),
                    "池内复用": len(reused), "池外待采": len(created), "淘汰": dict(rejected)},
        "池内复用": reused, "池外待采": created,
        "待采字段": ["cls(官方二级分类)", "trim(评分配置+指导价)", "8维scores(三电/安全/智驾/空间/性价比/操控/车机/品控)",
                 "电芯/800V/kWh/电耗", "crash安全证据"],
    }
    rp = args.intake_report or os.path.join(os.path.dirname(os.path.abspath(args.pool)), "intake_report.json")
    _json.dump(report, open(rp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print(f"候选摄入（{'已写回' if args.apply else 'dry-run 未写回'}）")
    print(f"  销量来源 {src} | 榜单 {len(rows)} 行 → 有销量 {len(sales)} → 达标 {len(qualified)}")
    print(f"  池内复用 {len(reused)}：{reused[:8]}")
    print(f"  池外待采 {len(created)}：{created[:12]}")
    print(f"  淘汰：{dict(rejected)}")
    print(f"  报告 → {rp}")
    if not args.apply:
        print("  （加 --apply 才写回总池）")
    return 0


def cmd_verify(args):
    """多源交叉核验（v8.2）：所有已落盘事实源比对同一字段 → 一致/冲突/单源/缺失。
    --apply 时把核验摘要(verify_flags)写回总池每车，不改 scores/adjust。"""
    from modules.engine import cross_verify as CV
    models = [m.strip() for m in args.verify_models.split(",")] if getattr(args, "verify_models", None) else None
    result, S = CV.verify_all(root=HERE, models=models,
                              dcd_path=getattr(args, "sales_cache", None) or "",
                              pool_only=not getattr(args, "verify_all_sources", False))
    out_dir = getattr(args, "verify_out", None) or os.path.join(HERE, "runs", "cross_verify_latest")
    jp, mp, data = CV.write_report(result, out_dir)

    # 写回核验摘要到总池（只加 verify 字段，绝不动 scores/adjust）
    if args.apply:
        pool = load_pool(args.pool)
        idx = index_by_key(pool)
        written = 0
        for car, fields in result.items():
            if car in idx:
                ff = {f: a for f, a in fields.items() if isinstance(a, dict) and "status" in a}
                idx[car]["verify"] = {
                    "as_of": "2026-11-19",
                    "conflicts": [f for f, a in ff.items() if a["status"] == "CONFLICT"],
                    "single_source": [f for f, a in ff.items() if a["status"] == "SINGLE"],
                    "verified_fields": [f for f, a in ff.items() if a.get("verified")],
                }
                written += 1
        save_pool_split(pool)
        print(f"  核验摘要已写回 {written} 款（仅 verify 字段，scores/adjust 未动）")

    print(f"多源交叉核验（{'已写回' if args.apply else 'dry-run 未写回'}）")
    print(f"  车型 {data['summary']['车型']} | 冲突 {data['summary']['冲突项']} | 单源 {data['summary']['单源项']}")
    from collections import Counter
    print(f"  冲突字段分布：{dict(Counter(c['field'] for c in data['冲突清单']))}")
    print(f"  JSON → {jp}\n  MD   → {mp}")
    if not args.apply:
        print("  （加 --apply 才把核验摘要写回总池）")
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
        save_pool_split(pool)
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
    # 8 有效销量口径（与 fetch_dcd_sales 一致）
    med, _ = effective_sales({"2026-05": 100, "2026-06": 200, "2026-07": 300})
    assert med == 200, med
    ramp, _ = effective_sales({"2026-08": 6, "2026-09": 2332, "2026-10": 8131}, "new_launch")
    assert ramp == 8131, ramp  # 新车爬坡取最新月，不被中位低估
    print("  [8] 有效销量口径（中位/新车爬坡）✓")
    # 9 榜单行→销量判定
    bevrow = _row_to_sales({"name": "某纯电车", "months": {"bev": {"2026-09": 2000, "2026-10": 3000}}})
    assert bevrow["power"] == "BEV" and bevrow["eff"] == 2500 and bevrow["scope"] == "pure_bev", bevrow
    allrow = _row_to_sales({"name": "某多动力车", "months": {"all": {"2026-09": 500, "2026-10": 700}}})
    assert allrow["scope"] == "series_total" and "非纯电" in allrow["method"], allrow
    print("  [9] 榜单行销量判定（纯电/车系合计）✓")
    # 10 多源交叉核验模块可加载 + 裁决纪律
    try:
        from modules.engine import cross_verify as CV
        assert CV.adjudicate("x", [])["status"] == "MISSING"
        one = [{"value": 1, "display": 1, "source": "a", "rank": 50, "url": "", "date": "", "detail": ""}]
        assert CV.adjudicate("x", one)["status"] == "SINGLE"
        diff = one + [{"value": 2, "display": 2, "source": "b", "rank": 40, "url": "", "date": "", "detail": ""}]
        assert CV.adjudicate("x", diff)["status"] == "CONFLICT"  # 非网查分歧不静默选值
        print("  [10] 多源交叉核验裁决纪律 ✓")
    except ImportError:
        print("  [10] cross_verify 未安装，跳过")
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
    sub.add_argument("--intake", action="store_true", help="按需摄入：搜榜→有效销量→达标入池→复用/待采(默认dry-run)")
    sub.add_argument("--verify", action="store_true", help="多源交叉核验：所有已落盘事实源比对→冲突标旗→网查确认回填(调cross_verify.py)")
    sub.add_argument("--query", action="store_true")
    sub.add_argument("--audit", action="store_true")
    sub.add_argument("--stats", action="store_true")
    sub.add_argument("--rescore", action="store_true")
    sub.add_argument("--sync-prices", action="store_true",
                     help="用 trims.json 的官方指导价回填候选池缺失 price_guide（只填空值）")
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
    ap.add_argument("--apply", action="store_true", help="rescore/intake 写回（默认 dry-run）")
    # intake 专用
    ap.add_argument("--sales-cache", help="懂车帝逐月榜快照 json（离线可复现）；不给则实时拉")
    ap.add_argument("--month", default="auto", help="数据月(截止)：auto=探测上个月/月初回退(默认)；或显式 YYYYMM；--sales-cache 复现时须显式给")
    ap.add_argument("--months", type=int, default=6, help="逐月回看月数，默认 6")
    ap.add_argument("--min-sales", type=int, default=1000, help="有效销量门槛，默认 1000")
    ap.add_argument("--intake-report", help="intake 报告输出路径")
    ap.add_argument("--verify-out", help="verify 核验报告输出目录")
    ap.add_argument("--verify-models", help="verify 只核验指定车型（逗号分隔）")
    ap.add_argument("--verify-all-sources", action="store_true", help="verify 含池外源车（默认只核验总池内）")
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
    if a.intake:
        return cmd_intake(a)
    if a.verify:
        return cmd_verify(a)
    if a.query:
        a.out = a.out_subset
        return cmd_query(a)
    if a.audit:
        return cmd_audit(a)
    if a.stats:
        return cmd_stats(a)
    if a.sync_prices:
        pool = load_pool(a.pool)
        fixed = backfill_price_guide(pool)
        for m, pg in fixed:
            print(f"  + {m:<18} price_guide = {pg[0]:g}–{pg[1]:g} 万")
        if fixed and a.apply:
            save_pool_split(pool)
            print(f"已回填 {len(fixed)} 款 → {a.out}")
        elif fixed:
            print(f"dry-run：将回填 {len(fixed)} 款（加 --apply 写入）")
        else:
            print("没有需要回填的 price_guide")
        return 0
    if a.rescore:
        return cmd_rescore(a)
    return 1


if __name__ == "__main__":
    sys.exit(main())
