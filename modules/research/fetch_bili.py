#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B站证据发现器（fetch_bili.py）——第三方视频证据的「机器初筛」层。

定位（先想清楚再写）：
  这不是"弹幕打分"——弹幕/评论是情绪，不是事实，本项目不取弹幕数据。
  它做两件初筛的事：
  1. 按车型+关键词搜视频（标题/UP主/播放量），产出**人工确认队列**；
  2. 视频有 CC 字幕（UP主上传）就抓全文，供人工快速浏览结论；
     AI 字幕需要登录态（SESSDATA）——不做：登录态爬取违反 B 站 ToS 且触发风控。

实测边界（2026-11-14）：
  · 搜索接口免登录可用（buvid3 随机 cookie 即可），每页 20 条；
  · 限速 0.5s/请求；
  · 评测类视频大多没有 CC 字幕 → 大部分入队条目只有标题+UP主，
    结论确认仍需人工点开；ASR 转写只对人工点名的单条视频做（fetch 深读层）。

用法：
    python3 fetch_bili.py --models "奕派EP7,零跑C16" --keywords "拆车,对撞,碰撞测试"
    python3 fetch_bili.py --queue-file <model> <bvid>   # 人工确认后写入 thirdparty_tests
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import time
import uuid

import requests

import paths

HERE = str(paths.ROOT)
QUEUE_PATH = os.path.join(HERE, "runs", "_bili", "queue.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131 Safari/537.36")


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False
    s.headers.update({"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
    s.cookies.set("buvid3", str(uuid.uuid4()))
    return s


def search(model: str, keyword: str, page: int = 1) -> list[dict]:
    s = _session()
    r = s.get("https://api.bilibili.com/x/web-interface/search/type",
              params={"search_type": "video",
                      "keyword": f"{model} {keyword}", "page": page},
              timeout=20)
    j = r.json()
    out = []
    for v in (j.get("data") or {}).get("result") or []:
        out.append({
            "bvid": v.get("bvid"), "title": re.sub("<[^>]+>", "", v.get("title") or ""),
            "author": v.get("author"), "mid": v.get("mid"),
            "play": v.get("play"),
            "duration": v.get("duration"), "pubdate": v.get("pubdate"),
            "model": model, "keyword": keyword,
        })
    return out


def cc_subtitle(bvid: str) -> dict | None:
    """CC 字幕（UP主上传）；AI 字幕需登录态，不取。"""
    s = _session()
    d = (s.get("https://api.bilibili.com/x/web-interface/view",
               params={"bvid": bvid}, timeout=20).json().get("data") or {})
    cid = d.get("cid")
    if not cid:
        return None
    j = s.get("https://api.bilibili.com/x/player/wbi/v2",
              params={"bvid": bvid, "cid": cid}, timeout=20).json()
    subs = ((j.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
    if not subs:
        return None
    url = subs[0].get("subtitle_url") or ""
    if url.startswith("//"):
        url = "https:" + url
    body = s.get(url, timeout=20).json() if url else {}
    lines = [x.get("content") for x in (body.get("body") or []) if x.get("content")]
    return {"lan": subs[0].get("lan_doc"), "text": "\n".join(lines)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True, help="逗号分隔车型")
    ap.add_argument("--keywords", default="拆车,对撞,碰撞测试,底盘",
                    help="逗号分隔检索词")
    ap.add_argument("--per", type=int, default=5, help="每组关键词取前 N 条")
    ap.add_argument("--queue", default=QUEUE_PATH)
    ap.add_argument("--subs", action="store_true",
                    help="对入队视频尝试抓 CC 字幕（多数没有）")
    a = ap.parse_args()

    models = [m.strip() for m in a.models.split(",") if m.strip()]
    keywords = [k.strip() for k in a.keywords.split(",") if k.strip()]
    queue = []
    if os.path.exists(a.queue):
        queue = json.load(open(a.queue, encoding="utf-8"))
    seen = {x.get("bvid") for x in queue}

    for model in models:
        for kw in keywords:
            try:
                vids = search(model, kw)
            except Exception as e:
                print(f"  ✗ {model}×{kw}: {type(e).__name__}")
                time.sleep(1)
                continue
            for v in vids[:a.per]:
                if v["bvid"] in seen:
                    continue
                seen.add(v["bvid"])
                v["queued_at"] = dt.datetime.now().isoformat(timespec="seconds")
                queue.append(v)
            print(f"  {model}×{kw}: +{min(len(vids), a.per)}")
            time.sleep(0.6)

    # 热度排序，人工从上往下看
    queue.sort(key=lambda x: -(x.get("play") or 0))
    os.makedirs(os.path.dirname(a.queue), exist_ok=True)
    json.dump(queue, open(a.queue, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"[ok] 队列 {len(queue)} 条 → {a.queue}")

    print("\n## 人工确认队列（按播放量排序，取前 12）\n")
    print("| 车型 | 标题 | UP主 | 播放 | 关键词 |")
    print("|---|---|---|---|---|")
    for v in queue[:12]:
        print(f"| {v['model']} | {v['title'][:36]} | {v['author']} | "
              f"{v.get('play') or 0:,} | {v['keyword']} |")
    print("\n> 确认后流程：人工点开视频 → 结论+链接 写入 "
          "categories/thirdparty_tests.json（thirdparty.py 会校验）——"
          "机器只做发现，不做裁决。")
    if a.subs:
        n = 0
        for v in queue[:5]:
            cc = cc_subtitle(v["bvid"])
            if cc:
                v["cc_text"] = cc["text"][:2000]
                v["cc_lan"] = cc["lan"]
                n += 1
            time.sleep(0.5)
        json.dump(queue, open(a.queue, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"\n[subs] 前 5 条中 {n} 条有 CC 字幕")


if __name__ == "__main__":
    main()
