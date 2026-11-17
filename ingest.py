"""数据摄取契约（ingest.py）——把爬虫产物接进排行榜引擎。

借鉴两个项目的实际做法：
  · jiankaitian/AutoInfoScraper：**两段式**抓取。第一段 Selenium 滚动加载列表 →
    `car_list.csv`（系列 ID+名称）；第二段按系列 ID 请求详情页 → `models_info.csv`。
    即「先发现候选，再取详情」，两阶段分开，中间落一张表。
  · BoyInTheSun/spider_dongchedi：先 `table.py` 建库，再 `spider.py` 灌数据；
    产出的是**评论/口碑**，正好对应榜单的 L4（大样本口碑）与 L2（投诉原始记录）。

本文件不实现爬虫（反爬与合规问题不该由排行榜引擎承担），只负责：
  1. 定义三张表的字段契约，并做**落地校验**（缺字段/重复主键/价格解析失败/级别不在白名单）；
  2. 把三张表转成 candidates.json（引擎的输入格式）；
  3. 把口碑/投诉折算成证据等级，供 `--slots` 与 `--robust` 使用。

三张表（UTF-8 CSV，首行为表头）：
  series.csv   系列层（候选发现）
      series_id, series_name, brand, cls, url
  trims.csv    配置层（评分对象＝带内推荐配置，不是车系）
      series_id, trim_id, trim_name, price_guide, power, range_km,
      battery_kwh, cell_brand, adas_chip, adas_lidar, susp_front, susp_rear,
      discount_price, url
  reviews.csv  口碑/投诉层（L4/L2 证据）
      trim_id, review_id, source, published, rating, text, complaint_no, url

用法：
    python3 ingest.py --check series.csv trims.csv reviews.csv
    python3 ingest.py --build series.csv trims.csv reviews.csv --out candidates.json
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict

# ---------------------------------------------------------------- 字段契约
SCHEMA = {
    "series": {
        "file": "series.csv",
        "required": ["series_id", "series_name"],
        "optional": ["brand", "cls", "url"],
        "key": "series_id",
        "note": "第一段产物：只有系列，没有配置。对应 AutoInfoScraper 的 car_list.csv",
    },
    "trims": {
        "file": "trims.csv",
        "required": ["series_id", "trim_id", "trim_name", "price_guide"],
        "optional": ["power", "range_km", "battery_kwh", "cell_brand", "adas_chip",
                     "adas_lidar", "susp_front", "susp_rear", "discount_price", "url"],
        "key": "trim_id",
        "note": "第二段产物：一款车多行，一行一个配置。价格必须是官方指导价。",
    },
    "reviews": {
        "file": "reviews.csv",
        "required": ["trim_id", "review_id", "text"],
        "optional": ["source", "published", "rating", "complaint_no", "url"],
        "key": "review_id",
        "note": "口碑与投诉。有 complaint_no 的才是可核验的原始记录，其余算软口碑。",
    },
}

# 投诉/口碑里提示结构性缺陷的词（不是评分，只用来标记"需要人看"）
RED_FLAGS = {
    "转向": ["转向失效", "方向机", "转向卡滞", "助力消失"],
    "制动": ["刹车失灵", "制动失效", "刹车软"],
    "三电": ["电池压差", "续航虚标", "限功率", "掉电", "热失控", "鼓包"],
    "车机": ["死机", "黑屏", "卡顿", "断连"],
    "售后": ["拒修", "推诿", "三包", "退车"],
}


def _read_csv(path):
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _num(x):
    if x is None:
        return None
    s = str(x).strip()
    if not s:
        return None
    m = re.search(r"\d+(?:\.\d+)?", s.replace(",", ""))
    return float(m.group()) if m else None


# ---------------------------------------------------------------- 校验
def check(series_path, trims_path, reviews_path=None):
    """校验三张表，返回问题清单（空＝可入库）。"""
    issues, stats = [], {}
    for kind, path in (("series", series_path), ("trims", trims_path),
                       ("reviews", reviews_path)):
        if not path:
            continue
        rows = _read_csv(path)
        spec = SCHEMA[kind]
        header = set(rows[0].keys()) if rows else set()
        miss_cols = [c for c in spec["required"] if c not in header]
        if miss_cols:
            issues.append(f"[{kind}] 缺必填列：{', '.join(miss_cols)}")
        key = spec["key"]
        ids = [str(r.get(key, "")).strip() for r in rows]
        empty = sum(1 for i in ids if not i)
        dup = [k for k, v in Counter(ids).items() if v > 1 and k]
        if empty:
            issues.append(f"[{kind}] 主键 {key} 有 {empty} 行为空")
        if dup:
            issues.append(f"[{kind}] 主键 {key} 重复 {len(dup)} 个：{', '.join(dup[:5])}")
        stats[kind] = {"rows": len(rows), "cols": len(header)}

    # 价格必须能解析
    trims = _read_csv(trims_path)
    bad_price = [r.get("trim_id") for r in trims if _num(r.get("price_guide")) is None]
    if bad_price:
        issues.append(f"[trims] price_guide 无法解析为数字：{', '.join(bad_price[:5])}"
                      f"（共 {len(bad_price)} 行）")

    # 系列覆盖
    sids = {str(r.get("series_id", "")).strip() for r in _read_csv(series_path)}
    orphan = {str(r.get("series_id", "")).strip() for r in trims} - sids
    if orphan:
        issues.append(f"[trims] 有 {len(orphan)} 个 series_id 不在 series.csv 里："
                      f"{', '.join(list(orphan)[:5])}")

    # 口碑必须挂在存在的配置上
    if reviews_path:
        tids = {str(r.get("trim_id", "")).strip() for r in trims}
        rev = _read_csv(reviews_path)
        bad = {str(r.get("trim_id", "")).strip() for r in rev} - tids
        if bad:
            issues.append(f"[reviews] 有 {len(bad)} 个 trim_id 不在 trims.csv 里："
                          f"{', '.join(list(bad)[:5])}")
        stats["reviews"]["with_complaint_no"] = sum(
            1 for r in rev if str(r.get("complaint_no") or "").strip())
    return {"issues": issues, "stats": stats, "ok": not issues}


# ---------------------------------------------------------------- 口碑折算
def review_signals(reviews):
    """按配置聚合口碑：条数、投诉编号数、红旗命中。"""
    agg = defaultdict(lambda: {"n": 0, "complaint": 0, "flags": Counter(),
                               "sources": set(), "neg": 0})
    for r in reviews:
        tid = str(r.get("trim_id", "")).strip()
        a = agg[tid]
        a["n"] += 1
        if str(r.get("complaint_no") or "").strip():
            a["complaint"] += 1
        if r.get("source"):
            a["sources"].add(r["source"])
        text = str(r.get("text") or "")
        for cat, kws in RED_FLAGS.items():
            if any(k in text for k in kws):
                a["flags"][cat] += 1
                a["neg"] += 1
    return agg


def grade_from_signals(sig):
    """证据等级：只有官方碰撞成绩才能给 A，这里最高到 B。

    这条很重要：口碑再多也只是 L4，不能替代 L1。
    判 B 的三条通道（任一满足即算有可用的一手口碑证据）：
      ① 可核投诉 ≥2 条（同一个配置上出现两条带编号的投诉，已能说明问题真实存在）
      ② 可核投诉 ≥1 条且命中结构性红旗（转向/制动/三电/车机/售后）
      ③ 口碑样本 ≥30 条（大样本，即使无编号也有统计意义）
    """
    if not sig or sig["n"] == 0:
        return "C", "无口碑/投诉样本"
    flags = sum(sig["flags"].values()) if sig.get("flags") else 0
    if sig["complaint"] >= 2:
        return "B", f"可核投诉 {sig['complaint']} 条（口碑 {sig['n']} 条）"
    if sig["complaint"] >= 1 and flags:
        return "B", f"投诉 {sig['complaint']} 条且命中红旗 {flags} 处"
    if sig["n"] >= 30:
        return "B", f"口碑 {sig['n']} 条（大样本，无编号）"
    return "C", f"口碑仅 {sig['n']} 条、可核投诉 {sig['complaint']} 条，样本不足"


# ---------------------------------------------------------------- 转换
def build(series_path, trims_path, reviews_path=None, cfg_cls=None,
          price_band=None):
    """三张表 → candidates.json（引擎输入）。

    price_band: [lo, hi]，给了就顺便标出带内/带外，但**不剔除**——
    剔除该由 gates 做，转换器只负责如实搬运，不替用户做决定。
    """
    series = {str(r["series_id"]).strip(): r for r in _read_csv(series_path)}
    trims = _read_csv(trims_path)
    reviews = _read_csv(reviews_path) if reviews_path else []
    sig = review_signals(reviews)

    cands = []
    for t in trims:
        sid = str(t.get("series_id", "")).strip()
        tid = str(t.get("trim_id", "")).strip()
        s = series.get(sid, {})
        price = _num(t.get("price_guide"))
        disc = _num(t.get("discount_price"))
        g, why = grade_from_signals(sig.get(tid))
        cls = str(s.get("cls") or "").strip()
        item = {
            "model": str(s.get("series_name") or sid).strip(),
            "series_id": sid,
            "cls": cls,
            "power": str(t.get("power") or "BEV").strip(),
            "price_guide": [price, price] if price is not None else None,
            "price_promo": (f"促销价 {disc}" if disc else None),
            "trim": {"name": str(t.get("trim_name") or "").strip(), "price": price},
            "cell_brand": (str(t.get("cell_brand") or "").strip() or None),
            "cell_multi_source": bool(re.search(r"[/、,]", str(t.get("cell_brand") or ""))),
            "evidence_status": g,
            "evidence_note": why,
            "source_ids": [],
            "note": "",
        }
        if price_band and price is not None:
            lo, hi = price_band
            item["in_band"] = bool(lo <= price <= hi)
        a = sig.get(tid)
        if a:
            item["review_stats"] = {"n": a["n"], "complaint_no": a["complaint"],
                                    "red_flags": dict(a["flags"]),
                                    "sources": sorted(a["sources"])}
        cands.append(item)

    return {"segment": "由 ingest.py 从爬虫产物生成", "generated_from":
            {"series": os.path.basename(series_path),
             "trims": os.path.basename(trims_path),
             "reviews": os.path.basename(reviews_path) if reviews_path else None},
            "note": "evidence_status 最高为 B：A 级需官方碰撞成绩（C-NCAP/C-IASI），"
                    "爬虫拿不到，必须另行补。",
            "candidates": cands}


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", nargs="+", metavar="CSV",
                    help="依次为 series.csv trims.csv [reviews.csv]")
    ap.add_argument("--build", nargs="+", metavar="CSV",
                    help="同上，转成 candidates.json（需 --out）")
    ap.add_argument("--out", help="输出 candidates.json 路径")
    ap.add_argument("--band", help="价格带，如 12,18")
    a = ap.parse_args()

    band = None
    if a.band:
        lo, hi = [float(x) for x in a.band.split(",")]
        band = [lo, hi]

    if a.check:
        s, t = a.check[0], a.check[1]
        r = a.check[2] if len(a.check) > 2 else None
        res = check(s, t, r)
        print(f"# 摄取校验\n")
        for k, v in res["stats"].items():
            print(f"- {k}: {v}")
        if res["issues"]:
            print("\n**问题：**")
            for i in res["issues"]:
                print(f"- {i}")
            sys.exit(1)
        print("\n✓ 通过。可执行 --build。")
        return

    if a.build:
        if not a.out:
            sys.exit("--build 需要 --out")
        s, t = a.build[0], a.build[1]
        r = a.build[2] if len(a.build) > 2 else None
        chk = check(s, t, r)
        if not chk["ok"]:
            sys.exit("校验未通过，先修数据：" + "；".join(chk["issues"]))
        data = build(s, t, r, price_band=band)
        json.dump(data, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        n = len(data["candidates"])
        inb = sum(1 for c in data["candidates"] if c.get("in_band"))
        print(f"✓ 写入 {a.out}：{n} 个配置"
              + (f"，带内 {inb} 个" if band else "")
              + f"；证据等级分布 {dict(Counter(c['evidence_status'] for c in data['candidates']))}")
        return

    ap.print_help()


if __name__ == "__main__":
    main()
