#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""车系数据抓取（fetch_specs.py）——通过 OpenCLI 的 autohome 适配器取一手字段。

为什么用 autohome 而不是 dongchedi：
  实测 `opencli dongchedi search/specs` 返回「returned no __NEXT_DATA__」——
  被反爬挡住了，headless 取不到；而 `opencli autohome brand/score` 标称
  `Browser: no`，无登录、无浏览器扩展即可取数。所以这条链只用 autohome。

autohome score 能给的字段（正好补我们最缺的几层证据）：
  level           官方二级分类（中型车 / 中型SUV …）→ 直接可用于级别闸门
  guide_price     厂商指导价 → 直接可用于价格闸门
  overall + 8 维   空间/驾驶感受/续航/外观/内饰/性价比/智能化 → L4 口碑
  pph 每百车故障   故障率 → 目前车质网给不了的量化指标
  review_users    样本量 → **PPH 必须结合它看**
  competitors     同级车与分数 → 天然对照组

硬规则（写进代码，不靠人记）：
  PPH 只有在 review_users ≥ 30 时才可引用。样本 4 条的 PPH = 0 不是「零故障」，
  是「没抽到」。低于阈值一律标 sample_gap，不参与评分。

用法：
    python3 fetch_specs.py --brands 比亚迪 大众 零跑 --limit 40
    python3 fetch_specs.py --series 8511 8312 6139        # 直接给 series_id
    python3 fetch_specs.py --brands 小鹏 --filter "MONA|P7" --scores
"""

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys

PPH_MIN_SAMPLE = 30          # PPH 可引用的最小样本量
MID_CLASSES = ("中型车", "中型SUV")


def _resolve_exe():
    """定位 opencli 可执行文件。

    Windows 上 npm 全局安装生成的是 opencli.CMD（不是 .exe），
    直接 subprocess 调用会失败，需要经 COMSPEC 转一层。
    """
    for name in ("opencli", "opencli.cmd", "opencli.bat"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _run(args, timeout=120):
    """调用 opencli 并解析 JSON。失败返回 (None, 错误文本)。"""
    exe = _resolve_exe()
    if not exe:
        return None, "未找到 opencli（先执行 npm install -g @jackwener/opencli）"
    cmd = [exe] + list(args) + ["-f", "json"]
    if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
        cmd = [os.environ.get("COMSPEC", "cmd.exe"), "/c"] + cmd
    try:
        p = subprocess.run(cmd,
                           capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None, "opencli 无法执行（检查 PATH）"
    except subprocess.TimeoutExpired:
        return None, "超时"
    out = (p.stdout or "").strip()
    if not out:
        return None, (p.stderr or "空输出")[:160]
    try:
        d = json.loads(out)
    except json.JSONDecodeError:
        return None, "非 JSON 输出：" + out[:120]
    if not d.get("ok"):
        return None, (d.get("error") or {}).get("message", "未知错误")[:160]
    return d.get("data"), None


def list_brand(brand, limit=40):
    data, err = _run(["autohome", "brand", brand, "--limit", str(limit)])
    if err:
        return [], err
    return [{"series_id": str(x.get("series_id") or ""),
             "name": x.get("name") or "",
             "price_text": x.get("price") or "",
             "url": x.get("url") or "",
             "brand": brand} for x in (data or [])], None


def score_series(sid):
    data, err = _run(["autohome", "score", str(sid)])
    if err:
        return None, err
    d = {x.get("field"): x.get("value") for x in (data or [])}
    try:
        users = int(float(d.get("review_users") or 0))
    except (TypeError, ValueError):
        users = 0
    try:
        pph = float(d.get("pph_每百车故障") or 0)
    except (TypeError, ValueError):
        pph = None
    return {
        "series_id": str(d.get("series_id") or sid),
        "name": d.get("name") or "",
        "brand": d.get("brand") or "",
        "level": d.get("level") or "",
        "guide_price": d.get("guide_price") or "",
        "overall": float(d.get("overall") or 0) or None,
        "dims": {k: float(d.get(k) or 0) or None for k in
                 ("空间", "驾驶感受", "续航", "外观", "内饰", "性价比", "智能化")},
        "pph": pph,
        "review_users": users,
        # 样本不足的 PPH 一律作废，不当「零故障」
        "pph_usable": bool(pph is not None and users >= PPH_MIN_SAMPLE),
        "pph_note": ("" if users >= PPH_MIN_SAMPLE
                     else f"样本仅 {users} 条，PPH 不可引用"),
        "competitors": d.get("competitors") or "",
        "url": d.get("url") or "",
        # 闸门字段：直接来自官方二级分类
        "cls_gate": d.get("level") or "",
        "is_mid": (d.get("level") or "") in MID_CLASSES,
    }, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brands", nargs="+", help="品牌名（比亚迪 / 大众 / 小鹏 …）")
    ap.add_argument("--series", nargs="+", help="直接给 series_id")
    ap.add_argument("--limit", type=int, default=40, help="每个品牌最多取多少车系")
    ap.add_argument("--filter", help="车系名正则过滤，如 \"MONA|P7\"")
    ap.add_argument("--scores", action="store_true", help="对品牌列出的车系逐个取口碑分")
    ap.add_argument("--out-prefix", default="autohome", help="输出文件前缀")
    a = ap.parse_args()

    rows = []
    if a.brands:
        for b in a.brands:
            got, err = list_brand(b, a.limit)
            if err:
                print(f"  [skip] 品牌「{b}」：{err}", file=sys.stderr)
                continue
            if a.filter:
                rx = re.compile(a.filter)
                got = [x for x in got if rx.search(x["name"])]
            print(f"  [ok] {b}：{len(got)} 个车系", file=sys.stderr)
            if a.scores:
                # 取到分的替换原行，取不到分的保留（否则会在 CSV 里出现重复车系）
                enriched = []
                for x in got:
                    s, e = score_series(x["series_id"])
                    if e:
                        print(f"    [skip] {x['name']}：{e}", file=sys.stderr)
                        enriched.append(x)
                    else:
                        enriched.append({**x, "score": s})
                rows += enriched
            else:
                rows += got
    if a.series:
        for sid in a.series:
            s, e = score_series(sid)
            if e:
                print(f"  [skip] {sid}：{e}", file=sys.stderr)
                continue
            rows.append({"series_id": sid, "name": s["name"], "brand": s["brand"],
                         "price_text": s["guide_price"], "url": s["url"], "score": s})

    if not rows:
        sys.exit("没有取到任何数据。先用 --brands 验证品牌名，或直接给 --series。")

    # 1) 车系列表（对应 ingest 的 series.csv）
    list_path = f"{a.out_prefix}_series.csv"
    with open(list_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["series_id", "series_name", "brand", "cls", "url", "guide_price"])
        for r in rows:
            s = r.get("score") or {}
            w.writerow([r["series_id"], r["name"], r.get("brand", ""),
                        s.get("level", ""), r.get("url", ""),
                        s.get("guide_price") or r.get("price_text", "")])

    # 2) 口碑分（L4 证据）
    scored = [r for r in rows if r.get("score")]
    if scored:
        sc_path = f"{a.out_prefix}_scores.csv"
        with open(sc_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["series_id", "name", "level", "guide_price", "overall",
                        "空间", "驾驶感受", "续航", "外观", "内饰", "性价比", "智能化",
                        "pph", "review_users", "pph_usable", "pph_note"])
            for r in scored:
                s = r["score"]
                w.writerow([s["series_id"], s["name"], s["level"], s["guide_price"],
                            s["overall"]] + [s["dims"][k] for k in
                                             ("空间", "驾驶感受", "续航", "外观",
                                              "内饰", "性价比", "智能化")] +
                           [s["pph"], s["review_users"], s["pph_usable"], s["pph_note"]])

    # 3) 汇总
    mid = [r for r in rows if (r.get("score") or {}).get("is_mid")]
    print(f"\n## 抓取结果")
    print(f"- 车系 {len(rows)} 个；其中官方二级分类属中型：**{len(mid)}** 个")
    print(f"- 已取口碑分 {len(scored)} 个；PPH 可引用（样本≥{PPH_MIN_SAMPLE}）"
          f"**{sum(1 for r in scored if r['score']['pph_usable'])}** 个")
    print(f"- 输出：`{list_path}`" + (f"、`{a.out_prefix}_scores.csv`" if scored else ""))
    if scored:
        print("\n| 车系 | 官方级别 | 指导价 | 口碑总分 | 样本 | PPH | 可引用 |")
        print("|---|---|---|---|---|---|---|")
        for r in sorted(scored, key=lambda x: -(x["score"]["overall"] or 0)):
            s = r["score"]
            print(f"| {s['name']} | {s['level']} | {s['guide_price']} | "
                  f"{s['overall']} | {s['review_users']} | "
                  f"{'—' if s['pph'] is None else s['pph']} | "
                  f"{'✓' if s['pph_usable'] else '✗ ' + s['pph_note']} |")
    print("\n> 这些字段可以直接填进 `ingest.py` 的 series.csv（cls/guide_price 即两道闸门的依据）。")
    print("> 但 `autohome score` 不含碰撞成绩与智驾实测，L1 与 L3 仍需 C-IASI / 懂车帝实测榜。")


if __name__ == "__main__":
    main()
