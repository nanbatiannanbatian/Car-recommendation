# -*- coding: utf-8 -*-
"""candidate_schema.py —— 候选池单条记录（candidate）的数据契约

为什么要有（2026-11-21 出「纯电中型SUV」榜时踩到的真 bug）：
  同一个字段在候选池里有两种形状：
      price: 24.98（float）  vs  "24.98万"（str）
      trim:  {"name": "激光雷达版", "price": 14.99}  vs  "103kWh 后驱MAX"（str）
  原因是 master_pool 的 `"candidate": c  # 原始完整记录` 直接存来源原文，没归一化；
  各下游自己防御——board_v2 有 isinstance 保护，trim_annotate / auto_trims / leaderboard
  没有，于是 `t.get("price") - price_hint` 直接 TypeError，整个出榜崩掉。

  这不是某个函数写错，是**同一字段多种形状**没人收口，正是「契约缺失」。
  所以把「怎么读这些字段」收到契约层：写入方归一化一次，读取方统一调用，
  谁也不用再猜对方给的是 dict 还是 str。

约束：零依赖、零 IO（只 import re），不 import 任何本项目其他包。
"""
from __future__ import annotations

import re

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def num(v) -> float | None:
    """宽容转数值：24.98 / "24.98" / "24.98万" 都能取到 24.98；其余返回 None。"""
    if v is None or v is True or v is False or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = _NUM_RE.search(str(v))
    return float(m.group()) if m else None


def num_first(s) -> float | None:
    """从文本取第一个数字（"24.98万" → 24.98）。保留旧名，等价于 num。"""
    return num(s)


def trim_of(c: dict) -> dict | None:
    """统一读「评分配置」。

    允许的形状：
      {"name": "...", "price": 14.99}  → 原样（price 归一成 float 或 None）
      "激光雷达版"                      → {"name": "激光雷达版", "price": None}
      None / ""                        → None
    """
    t = (c or {}).get("trim")
    if isinstance(t, dict):
        out = dict(t)
        if "price" in out:
            out["price"] = num(out.get("price"))
        return out
    if isinstance(t, str) and t.strip():
        return {"name": t.strip(), "price": None}
    return None


def trim_name(c: dict) -> str | None:
    t = trim_of(c)
    return (t or {}).get("name")


def price_of(c: dict) -> float | None:
    """评分配置价（万元）。优先级：trim.price → price_guide 下限 → price_wan → price 文本。

    这个顺序就是原 master_pool.price_of 的顺序，保留不动——价位段子榜按它划带，
    改顺序会直接改名次归属。
    """
    if not c:
        return None
    t = trim_of(c)
    if t and t.get("price"):
        return float(t["price"])
    g = c.get("price_guide")
    if isinstance(g, (list, tuple)) and g:
        v = num(g[0])
        if v:
            return v
    v = num(c.get("price_wan"))
    if v:
        return v
    return num(c.get("price"))


def normalize_candidate(c: dict) -> dict:
    """把一条候选记录归一化成统一形状（幂等，可就地返回同一对象）。

    - price：能解析就转 float，原始文本留 price_raw 备查（不丢证据）
    - trim：str → {"name": ..., "price": None}，原始值留 trim_raw
    - price_wan / price_guide：顺带转数值，避免下游再解析
    """
    if not isinstance(c, dict):
        return c
    raw_price = c.get("price")
    p = price_of(c)
    if p is not None and not isinstance(raw_price, (int, float)):
        c["price_raw"] = raw_price
        c["price"] = p
    if "price_wan" in c:
        c["price_wan"] = num(c.get("price_wan"))
    g = c.get("price_guide")
    if isinstance(g, (list, tuple)) and g:
        c["price_guide"] = [num(g[0]), num(g[1]) if len(g) > 1 else None]
    if isinstance(c.get("trim"), str):
        c["trim_raw"] = c["trim"]
        c["trim"] = trim_of(c)
    elif isinstance(c.get("trim"), dict):
        c["trim"] = trim_of(c)
    return c


def normalize_pool(cands: list[dict]) -> list[dict]:
    """批量归一化（出榜入口统一调用）。"""
    return [normalize_candidate(c) for c in (cands or [])]


def check_candidate(c: dict) -> list[str]:
    """形状体检：返回问题列表（空 = 合格）。用于回归与 ingest 门禁。"""
    errs: list[str] = []
    if not isinstance(c, dict):
        return [f"candidate 不是 dict：{type(c).__name__}"]
    if c.get("price") is not None and not isinstance(c.get("price"), (int, float)):
        errs.append(f"price 未归一化为数值：{c.get('price')!r}")
    t = c.get("trim")
    if t is not None and not isinstance(t, dict):
        errs.append(f"trim 未归一化为 dict：{t!r}")
    if isinstance(t, dict) and t.get("price") is not None and not isinstance(
            t.get("price"), (int, float)):
        errs.append(f"trim.price 未归一化为数值：{t.get('price')!r}")
    return errs


def selftest() -> None:
    a = normalize_candidate({"model": "极氪7X", "price": "24.98万",
                             "trim": "103kWh 后驱MAX"})
    assert a["price"] == 24.98 and a["price_raw"] == "24.98万", a
    assert a["trim"] == {"name": "103kWh 后驱MAX", "price": None}, a
    assert check_candidate(a) == [], check_candidate(a)
    b = normalize_candidate({"model": "X", "trim": {"name": "激光雷达版", "price": "14.99"}})
    assert b["trim"]["price"] == 14.99, b
    assert price_of(b) == 14.99, price_of(b)
    c = normalize_candidate({"model": "Y", "price_guide": ["12.99", "16.69"]})
    assert price_of(c) == 12.99, price_of(c)
    # 幂等：再归一化一次结果不变
    d = normalize_candidate(dict(a))
    assert d == a, (d, a)
    # 反向：不归一化时 check 必须报错（证明门禁不是摆设）
    assert check_candidate({"price": "24.98万", "trim": "X"}), "未归一化必须被检出"
    print("candidate_schema selftest OK")


if __name__ == "__main__":
    selftest()
