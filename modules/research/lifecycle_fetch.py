#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lifecycle_fetch.py — 生命周期确证层「自动拉取 / 一键重生成」。

为什么存在：
    上一步 (b) 把生命周期从症状式升级为确证式，确证源是手工维护的
    categories/lifecycle_status.json（15 条）。每往候选池加一台车，它就静默
    回退成「默认在售、无确证」，确认层价值被新车型稀释。本脚本把确证源
    升级为「分层自动合并」，让确认层随池扩展、可一键重生成。

分层优先级（高优先 wins，命中即停）：
    Layer 0  --manual     现有手工确证（带原因，永不覆盖；这是覆盖层）
    Layer 1  --gonggao    工信部公告提取 JSON，按车型名 join（监管金标准，优先于市场状态）
    Layer 2  --dcd-status 懂车帝车系在售/停售，按 dcd_series_id 主键 join（覆盖 99% 车型）
    Layer 3  (症状复核)   无硬源且销量骤降严重的车型 → 标 needs_review（不自动判，避免假阳性）
    Layer 4  default      其余 → 隐式在售（不写库，resolve 回退即可）

    注：工信部公告(gonggao) 优先于懂车帝市场状态(dcd)，因为当二者冲突时
        公告（监管停产认定）更权威；dcd 仍承担绝大多数车型的判定。

设计纪律：
    - 只把「非平凡判定」写回 entries：手工条目、dcd/gonggao 命中的、needs_review 的。
      纯默认在售不入库（避免 200+ 冗余 active 行）；resolve() 对不在库的车型本就回退 active。
    - confirmed=True 仅当来源是「硬事实」：手工 / dcd / gonggao。症状与默认一律 False。
    - 状态值必须落在 leaderboard.LC_LABEL 词表内。

用法：
    python lifecycle_fetch.py \
        --manual   categories/lifecycle_status.json \
        --pool    runs/car-ice-4boards/ice_pool_full.json \
        --pool    runs/car-ev-4boards/candidates_trend.json \
        --dcd-status  <vehicle-data-hub 产出的 dcd-status.json> \
        --gonggao     <gonggao 提取.json> \
        --out      categories/lifecycle_status.json \
        --report   runs/.../lifecycle_autogen.md

    python lifecycle_fetch.py --selftest     # 合成数据自检，不读写任何项目文件
"""
import argparse
import json
import os
import sys
from collections import Counter, OrderedDict

import paths

HERE = str(paths.ROOT)
# 状态词表（与 leaderboard.LC_LABEL 对齐）
STATUS_ENUM = ["active", "new_launch", "facelift_pending", "clearance", "discontinued"]
SOURCE_TYPE_ENUM = ["工信部公告", "官方停产通告", "厂商在售状态", "媒体确证",
                    "推断(无公告)", "懂车帝车系状态", "销量症状复核"]

# 懂车帝车系 sale_status → (生命周期 status, source_type)
DCD_MAP = {
    "onsale":  ("active", "懂车帝车系状态"),
    "在售":    ("active", "懂车帝车系状态"),
    "offsale": ("discontinued", "懂车帝车系状态"),
    "停售":    ("discontinued", "懂车帝车系状态"),
    "停产":    ("discontinued", "懂车帝车系状态"),
    "presale": ("new_launch", "懂车帝车系状态"),
    "预售":    ("new_launch", "懂车帝车系状态"),
    "future":  ("facelift_pending", "懂车帝车系状态"),
    "待售":    ("facelift_pending", "懂车帝车系状态"),
}


# --------------------------------------------------------------------------- #
# 输入加载
# --------------------------------------------------------------------------- #
def load_pool_models(pool_paths):
    """从 ICE 池 / EV 候选池提取 model -> {dcd_series_id, name}。

    兼容两种结构：
      - {"pool": [ {model, dcd_series_id, ...}, ... ]}
      - {"candidates": [ {model, dcd_series_id, ...}, ... ]}
      - 裸 list
    返回 OrderedDict(model -> info)，保持首次出现顺序。
    """
    models = OrderedDict()
    for path in pool_paths:
        raw = json.load(open(path, encoding="utf-8"))
        if isinstance(raw, dict):
            arr = raw.get("pool") or raw.get("candidates") or []
        else:
            arr = raw
        for c in arr:
            m = c.get("model")
            if not m:
                continue
            if m not in models:
                models[m] = {
                    "model": m,
                    "dcd_series_id": c.get("dcd_series_id"),
                    "sales": c.get("sales") or c.get("monthly_sales"),
                }
    return models


def load_manual(path):
    """加载现有 lifecycle_status.json 作为覆盖层（手工确证，永不覆盖）。"""
    if not path or not os.path.exists(path):
        return {}, {}
    db = json.load(open(path, encoding="utf-8"))
    entries = db.get("entries", [])
    by_model = OrderedDict()
    for e in entries:
        by_model[e["model"]] = e
    return by_model, db.get("_meta", {})


def _norm_dcd_status(s):
    if s is None:
        return None
    return str(s).strip().lower()


def load_dcd_status(path):
    """加载懂车帝车系状态。支持多种形态（见 runbook）：

      A) {"series": [ {"dcd_series_id": 546, "sale_status": "onsale", "url": "...", "name": "..."}, ... ]}
      B) {"546": "onsale", ...}                       （主键 = dcd_series_id，值=状态串）
      C) {"546": {"sale_status": "onsale", "url": "..."}, ...}
      D) {"RAV4荣放": "onsale", ...}                  （主键 = model 名，兜底）

    统一返回：dcd_id -> {"status": <映射后生命周期>, "source_type": "懂车帝车系状态",
                        "sale_status": <原始>, "url": <>, "note": <>}
    """
    raw = json.load(open(path, encoding="utf-8"))
    out = {}
    series = raw.get("series") if isinstance(raw, dict) else None
    src = series if series is not None else (raw if isinstance(raw, list) else [raw])
    if isinstance(raw, dict) and "series" not in raw:
        # 形态 B/C/D：dict
        for k, v in raw.items():
            sale = v if isinstance(v, str) else (v or {}).get("sale_status")
            url = v.get("url") if isinstance(v, dict) else ""
            note = v.get("note") if isinstance(v, dict) else ""
            mapped = DCD_MAP.get(_norm_dcd_status(sale))
            if not mapped:
                continue
            key = int(k) if str(k).isdigit() else k  # 数字键视为 dcd_series_id
            out[key] = {"status": mapped[0], "source_type": mapped[1],
                        "sale_status": sale, "url": url or "", "note": note or ""}
    else:
        # 形态 A / 裸 list
        for it in src:
            if not isinstance(it, dict):
                continue
            sid = it.get("dcd_series_id")
            sale = it.get("sale_status") or it.get("status")
            mapped = DCD_MAP.get(_norm_dcd_status(sale))
            if sid is None or not mapped:
                continue
            out[int(sid)] = {"status": mapped[0], "source_type": mapped[1],
                             "sale_status": sale, "url": it.get("url", "") or "",
                             "note": it.get("note", "") or ""}
    return out


def load_gonggao(path):
    """加载工信部公告提取 JSON（按车型名 join）。

    支持：
      A) {"entries": [ {"model": "...", "status": "discontinued", "note": "...", "date": "...", "url": "..."}, ... ]}
      B) {"车型名": {"status": ..., "note": ..., "date": ..., "url": ...}, ...}

    只接受状态词表内的值；其余跳过。
    """
    raw = json.load(open(path, encoding="utf-8"))
    out = OrderedDict()
    if isinstance(raw, dict):
        if "entries" in raw:
            items = raw["entries"]
        else:
            items = [{"model": k, **(v if isinstance(v, dict) else {"status": v})}
                     for k, v in raw.items()]
    else:
        items = raw
    for it in items:
        m = it.get("model")
        st = it.get("status")
        if not m or st not in STATUS_ENUM:
            continue
        out[m] = {
            "status": st,
            "source_type": it.get("source_type") or "工信部公告",
            "note": it.get("note", "") or "",
            "date": it.get("date", "") or "",
            "url": it.get("url", "") or "",
        }
    return out


# --------------------------------------------------------------------------- #
# 合并核心
# --------------------------------------------------------------------------- #
def resolve_all(models, manual, dcd, gonggao):
    """对每台车分层解析，返回 (entries, stats, needs_review)。

    entries: 仅非平凡判定（手工 + dcd + gonggao + needs_review）；按模型顺序。
    needs_review: 无硬源且销量骤降严重的车型列表。
    """
    entries = OrderedDict()
    stats = Counter()
    needs_review = []

    for m, info in models.items():
        # Layer 0：手工覆盖
        if m in manual:
            e = dict(manual[m])
            e["confirmed"] = True
            e["_layer"] = "manual"
            entries[m] = e
            stats["manual"] += 1
            continue

        # Layer 1：工信部公告（监管金标准，优先于市场状态）
        if m in gonggao:
            g = gonggao[m]
            e = {
                "model": m, "status": g["status"],
                "date": g.get("date", ""), "successor": "",
                "note": g.get("note", "") or "工信部公告提取",
                "source": g.get("source", "") or "工信部公告",
                "source_type": g["source_type"],
                "url": g.get("url", "") or "",
                "confirmed": True, "_layer": "gonggao",
            }
            entries[m] = e
            stats["gonggao"] += 1
            continue

        # Layer 2：懂车帝车系状态（主键 dcd_series_id）
        sid = info.get("dcd_series_id")
        dcd_hit = None
        if sid is not None and int(sid) in dcd:
            dcd_hit = dcd[int(sid)]
        elif m in dcd:  # 兜底：dcd 文件用 model 名做键
            dcd_hit = dcd[m]
        if dcd_hit:
            e = {
                "model": m, "status": dcd_hit["status"],
                "date": "", "successor": "",
                "note": dcd_hit.get("note") or f"懂车帝车系状态：{dcd_hit.get('sale_status','')}",
                "source": "懂车帝车系页",
                "source_type": dcd_hit["source_type"],
                "url": dcd_hit.get("url", "") or "",
                "confirmed": True, "_layer": "dcd",
            }
            entries[m] = e
            stats["dcd"] += 1
            continue

        # Layer 3：症状复核（仅标记，不自动判，避免假阳性）
        if _severe_drop(info):
            needs_review.append({
                "model": m, "dcd_series_id": sid,
                "reason": "无硬源且近月销量较峰值骤降 ≥50% 且绝对降幅 ≥800，建议人工核生命周期",
            })
            stats["needs_review"] += 1
            # 同时作为一条 needs_review 条目写库（status 暂置 active，confirmed=False，note 标注待核）
            e = {
                "model": m, "status": "active",
                "date": "", "successor": "",
                "note": "⚠待核：无硬源且销量骤降严重，疑似停产/清库，需人工确认。",
                "source": "销量症状复核（待人工确证）",
                "source_type": "销量症状复核",
                "url": "", "confirmed": False, "_layer": "needs_review",
            }
            entries[m] = e
            continue

        # Layer 4：隐式默认在售（不写库）
        stats["default"] += 1

    return entries, stats, needs_review


def _severe_drop(info):
    """轻量症状启发式：近月销量相对峰值骤降严重 → True。"""
    s = info.get("sales")
    if not isinstance(s, (list, tuple)) or len(s) < 3:
        return False
    try:
        vals = [float(x) for x in s if x is not None]
    except (TypeError, ValueError):
        return False
    if not vals:
        return False
    peak = max(vals)
    recent = vals[-1]
    if peak <= 0:
        return False
    drop_ratio = (peak - recent) / peak
    drop_abs = peak - recent
    return drop_ratio >= 0.5 and drop_abs >= 800


# --------------------------------------------------------------------------- #
# 写出
# --------------------------------------------------------------------------- #
def write_status(out_path, entries, meta_in, stats, needs_review):
    clean = [OrderedDict((k, v) for k, v in e.items() if not k.startswith("_"))
             for e in entries.values()]
    # 以原 _meta 为基底，保留人工撰写的 purpose/schema/note，叠加自动生成字段
    meta = OrderedDict()
    base = meta_in or {}
    meta["purpose"] = base.get("purpose") or "生命周期确证来源（分层自动合并：手工覆盖 + 懂车帝车系状态 + 工信部公告 + 症状复核）。"
    meta["how_to_use"] = base.get("how_to_use") or "lifecycle_confirm.resolve(c) 优先查本库；查不到回退症状式 detect_lifecycle / 默认在售。"
    meta["schema"] = base.get("schema") or "model -> {status, note, date, successor, source, source_type, url, confirmed}"
    meta["status_enum"] = base.get("status_enum") or STATUS_ENUM
    meta["source_type_enum"] = base.get("source_type_enum") or SOURCE_TYPE_ENUM
    old_note = base.get("note", "")
    meta["note"] = ((old_note + " " if old_note else "") +
                   "本文件由 lifecycle_fetch.py 分层自动合并生成；手工条目（source_type 非「懂车帝车系状态/"
                   "工信部公告/销量症状复核」）为人工覆盖，重建时保留、永不自动覆盖。")
    meta["generated"] = "auto-by-lifecycle_fetch"
    meta["layer_counts"] = {
        "manual": stats.get("manual", 0),
        "dcd": stats.get("dcd", 0),
        "gonggao": stats.get("gonggao", 0),
        "needs_review": stats.get("needs_review", 0),
        "default_implicit": stats.get("default", 0),
    }
    meta["needs_review"] = [r["model"] for r in needs_review]
    meta["coverage"] = (f"非平凡确证 {len(clean)} 款（手工 {stats.get('manual',0)} + "
                        f"dcd {stats.get('dcd',0)} + gonggao {stats.get('gonggao',0)} + "
                        f"待核 {stats.get('needs_review',0)}）；"
                        f"隐式默认在售 {stats.get('default',0)} 款不写库。")
    db = {"_meta": meta, "entries": clean}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    return db


def write_report(report_path, entries, stats, needs_review, pools):
    lines = []
    lines.append("# 生命周期确证层 · 自动合并审计（lifecycle_fetch.py）\n")
    lines.append(f"候选车型总数（合并池）：**{sum(stats.values())}**\n")
    lines.append("## 分层命中统计\n")
    lines.append("| 层 | 含义 | 命中数 | confirmed |")
    lines.append("|---|---|---|---|")
    lines.append(f"| manual | 手工覆盖（永不覆盖） | {stats.get('manual',0)} | ✅ |")
    lines.append(f"| dcd | 懂车帝车系状态（主键 join） | {stats.get('dcd',0)} | ✅ |")
    lines.append(f"| gonggao | 工信部公告提取 | {stats.get('gonggao',0)} | ✅ |")
    lines.append(f"| needs_review | 症状复核待人工确证 | {stats.get('needs_review',0)} | ❌ |")
    lines.append(f"| default | 隐式默认在售（不写库） | {stats.get('default',0)} | ❌ |")
    lines.append("\n## 逐车型解析（非平凡判定）\n")
    lines.append("| 车型 | 状态 | source_type | 层 | url | note |")
    lines.append("|---|---|---|---|---|---|")
    for e in entries.values():
        note = (e.get("note") or "").replace("|", "/").replace("\n", " ")
        url = e.get("url") or ""
        lines.append(f"| {e['model']} | {e['status']} | {e.get('source_type','')} | "
                     f"{e.get('_layer','')} | {url} | {note} |")
    if needs_review:
        lines.append("\n## ⚠ 待人工核（疑似停产/清库但无硬源）\n")
        for r in needs_review:
            lines.append(f"- **{r['model']}**（dcd_series_id={r['dcd_series_id']}）：{r['reason']}")
    lines.append("\n## 输入池\n")
    for p in pools:
        lines.append(f"- {p}")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# --------------------------------------------------------------------------- #
# 自检（合成数据，不碰项目文件）
# --------------------------------------------------------------------------- #
def selftest():
    models = OrderedDict([
        ("M1", {"model": "M1", "dcd_series_id": 101, "sales": None}),
        ("M2", {"model": "M2", "dcd_series_id": 102, "sales": None}),
        ("M3", {"model": "M3", "dcd_series_id": 103, "sales": None}),
        ("M4", {"model": "M4", "dcd_series_id": 104, "sales": None}),
    ])
    manual = OrderedDict([
        ("M1", {"model": "M1", "status": "discontinued", "date": "2026-07",
                "successor": "", "note": "手工确证停产", "source": "X",
                "source_type": "官方停产通告", "url": "http://x"}),
    ])
    dcd = {
        102: {"status": "discontinued", "source_type": "懂车帝车系状态",
              "sale_status": "offsale", "url": "http://dcd/102", "note": ""},
        103: {"status": "active", "source_type": "懂车帝车系状态",
              "sale_status": "onsale", "url": "http://dcd/103", "note": ""},
    }
    gonggao = {}
    entries, stats, nr = resolve_all(models, manual, dcd, gonggao)
    assert stats["manual"] == 1, stats
    assert stats["dcd"] == 2, stats
    assert stats["default"] == 1, stats
    assert "M1" in entries and entries["M1"]["status"] == "discontinued"
    assert entries["M1"]["confirmed"] is True
    assert "M2" in entries and entries["M2"]["status"] == "discontinued" and entries["M2"]["confirmed"] is True
    assert "M3" in entries and entries["M3"]["status"] == "active" and entries["M3"]["confirmed"] is True
    assert "M4" not in entries, "默认在售不应写库"
    # 验证 gonggao 层优先于 dcd（监管金标准）
    entries2, stats2, _ = resolve_all(
        models, manual,
        {102: {"status": "active", "source_type": "懂车帝车系状态", "sale_status": "onsale", "url": "", "note": ""}},
        {"M2": {"status": "discontinued", "source_type": "工信部公告", "note": "公告停产",
                "date": "2026-06", "url": "http://gg"}})
    assert entries2["M2"]["source_type"] == "工信部公告", "gonggao 应优先于 dcd"
    # 验证 dcd 在 gonggao 缺位时承担判定（M2 无 gonggao）
    entries3, stats3, _ = resolve_all(
        models, manual,
        {102: {"status": "discontinued", "source_type": "懂车帝车系状态", "sale_status": "offsale", "url": "http://dcd/102", "note": ""}},
        {})
    assert entries3["M2"]["source_type"] == "懂车帝车系状态" and entries3["M2"]["status"] == "discontinued"
    # 验证症状复核
    models_s = OrderedDict([("S1", {"model": "S1", "dcd_series_id": 200,
                                    "sales": [5000, 4800, 100, 50]})])
    e3, s3, nr3 = resolve_all(models_s, {}, {}, {})
    assert s3["needs_review"] == 1 and "S1" in e3, (s3, list(e3))
    print("[selftest] PASS — 分层合并 / 手工优先 / 主键join / 症状复核 全部正确")
    return True


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="生命周期确证层自动合并")
    ap.add_argument("--manual", default=str(paths.cat("lifecycle_status.json")),
                    help="现有手工确证文件（覆盖层，永不覆盖）")
    ap.add_argument("--pool", action="append", default=[],
                    help="候选池 JSON（可多次：ICE 池 + EV 候选），提供 model/dcd_series_id/sales")
    ap.add_argument("--dcd-status", default=None, help="懂车帝车系状态 JSON（按 dcd_series_id 主键 join）")
    ap.add_argument("--gonggao", default=None, help="工信部公告提取 JSON（按车型名 join）")
    ap.add_argument("--out", default=str(paths.cat("lifecycle_status.json")),
                    help="输出 lifecycle_status.json")
    ap.add_argument("--report", default=None, help="输出三态审计 md")
    ap.add_argument("--selftest", action="store_true", help="合成数据自检，不读写项目文件")
    args = ap.parse_args()

    if args.selftest:
        return 0 if selftest() else 1

    if not args.pool:
        print("[err] 至少传一个 --pool", file=sys.stderr)
        return 2

    models = load_pool_models(args.pool)
    manual, manual_meta = load_manual(args.manual)
    dcd = load_dcd_status(args.dcd_status) if args.dcd_status else {}
    gonggao = load_gonggao(args.gonggao) if args.gonggao else {}

    entries, stats, needs_review = resolve_all(models, manual, dcd, gonggao)
    db = write_status(args.out, entries, manual_meta, stats, needs_review)
    if args.report:
        write_report(args.report, entries, stats, needs_review, args.pool)

    print(f"[ok] 候选 {sum(stats.values())} 款 → 写库 {len(entries)} 条 "
          f"(手工 {stats.get('manual',0)} / dcd {stats.get('dcd',0)} / "
          f"gonggao {stats.get('gonggao',0)} / 待核 {stats.get('needs_review',0)}；"
          f"隐式默认 {stats.get('default',0)})")
    print(f"[ok] 已写出 {args.out}")
    if args.report:
        print(f"[ok] 审计已写出 {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
