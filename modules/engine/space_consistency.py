# -*- coding: utf-8 -*-
"""space_consistency.py —— 空间分「硬数据一致性」封顶（唯一入口）

问题（2026-11-21，用户第三次问同一类）：
  「小鹏MONA L05 空间分这么高合理吗？」——查实 space=9.3 全场第一，
  但它零交付零实测、车长 4870mm 只算中上、车高 1636mm 偏矮；
  体积最大的蔚来ES6(16.49m³) 反而只有 8.3。体积与 space 分几乎无相关性，
  说明 space 是随包人工库里拍出来的分，从未用硬数据校验过。

  更麻烦的是 evidence_gate.DIM_POLICY 把 space 列为"纸面可判定、不封顶"
  （上一轮修智驾/车机时我以"轴距是硬数据"为由放过了它），但
  vehicle_physics.json 全库 67 款车**轴距覆盖率 0%**——所谓硬数据支撑是空的。

  这和之前抓到的 safety/adas/quality 虚高是同构病：无依据却声称更强。
  区别只是 space 的"依据"是尺寸硬数据而非实测证据，所以不能用 evidence_gate
  的实测证据门槛（那会误伤所有车），要用**尺寸一致性**约束。

修法（本模块，纯函数、只封顶不倒扣、留审计）：
  · 尺寸代理 size：优先 wheelbase_mm（research 补录后自动生效），
    否则用长×宽×高体积(m³)。跨库车名一律走 canon。
  · 对每辆有 space 分且有尺寸的车 c：取"体积/轴距 >= c"的所有车 space 集合，
    若样本 >= MIN_PEERS，锚点 = 该集合中位；c 的分超过 锚点 + TOL 才封顶到锚点。
  · TOL=0.4 是标定的：0.3 会误伤宋Ultra/C11，0.5 会漏掉 MONA，
    0.4 在全池只命中 MONA 一款（9.3→8.8）。t083 锁定这个标定。
  · 只封顶不倒扣：分低的车（如 ES6 8.3）原样保留。
  · 缺尺寸数据的车进 uncalibrated，交给 research 的 fill_dimensions 补采。
  · 不改权重、不产生新分，与 evidence_gate 同一纪律。

为什么不是"按体积直接重算 space"：空间体验 ≠ 体积（得房率、轴距、座椅布局
都会影响，ES6 溜背就是反例）。硬数据的作用是设**上限**——尺寸不如人的车
不能声称空间分更高；但在尺寸允许的范围内，人工判断仍然有效。
"""
from __future__ import annotations

import json
import statistics

# 容忍带（分）：超过"尺寸不小于自己的车"中位 + 此值才封顶。
# 标定见模块 docstring，t083 锁定。
TOL = 0.4
# 可比样本下限：不足这么多款不做中位锚点（2 款的中位是噪声）。
MIN_PEERS = 3


def _load_physics():
    """读随包物理参数库，key 用 canon 归一化（跨库写法不一，T6 踩过坑）。"""
    import paths
    from modules.contracts.model_names import canon
    d = json.load(open(paths.cat("vehicle_physics.json"), encoding="utf-8"))
    return {canon(x.get("model") or ""): x for x in d.get("cars", [])}


def size_of(c, physics=None):
    """返回 (size, basis)：优先轴距(mm)，否则长×宽×高体积(m³)；无数据返回 (None, None)。"""
    from modules.contracts.model_names import canon
    phys = physics if physics is not None else _load_physics()
    rec = phys.get(canon(c.get("model") or ""))
    if not rec:
        return None, None
    wb = rec.get("wheelbase_mm")
    if isinstance(wb, (int, float)) and wb > 0:
        return float(wb), "wheelbase_mm"
    L, W, H = rec.get("length_mm"), rec.get("width_mm"), rec.get("height_mm")
    if all(isinstance(x, (int, float)) and x > 0 for x in (L, W, H)):
        return round(L * W * H / 1e9, 3), "volume_m3"
    return None, None


def calibrate_space(cands, physics=None, tol=TOL, min_peers=MIN_PEERS):
    """空间分硬数据一致性封顶，原地修改 cands。

    Returns (adjustments, uncalibrated)：
      adjustments = [{model, dim, prev, capped, anchor, peers, size, basis, tol}]
      uncalibrated = [model, ...]  # 缺尺寸数据，research 待补
    """
    phys = physics if physics is not None else _load_physics()
    scored = []
    for c in cands:
        s = (c.get("scores") or {}).get("space")
        if not isinstance(s, (int, float)):
            continue
        size, basis = size_of(c, phys)
        scored.append((c, float(s), size, basis))

    out: list[dict] = []
    uncal: list[str] = []
    for c, space, size, basis in scored:
        if size is None:
            uncal.append(c.get("model"))
            continue
        # 尺寸不小于本车的车（含自己）space 集合
        peers = [v for _, v, s2, _ in scored if s2 is not None and s2 >= size - 1e-9]
        if len(peers) < min_peers:
            continue
        anchor = round(statistics.median(peers), 2)
        if space <= anchor + tol + 1e-9:
            continue                      # 容忍带内，人工判断有效
        prev = space
        c["scores"]["space"] = anchor
        reason = (f"space 硬数据一致性封顶：尺寸不小于本车（{size:g}{basis}）的 "
                  f"{len(peers)} 款车 space 中位 {anchor:g}，本车原 {prev:g} "
                  f"超出容忍带 {tol:g}，封顶至 {anchor:g}（不倒扣、仅压虚高）")
        c.setdefault("score_cap_reasons", []).append(reason)
        out.append({"model": c.get("model"), "dim": "space", "prev": prev,
                    "capped": anchor, "anchor": anchor, "peers": len(peers),
                    "size": size, "basis": basis, "tol": tol})
    return out, uncal


def summarize(adjustments, uncalibrated=None):
    return {
        "count": len(adjustments),
        "capped": [{"model": a["model"], "prev": a["prev"], "to": a["capped"],
                    "anchor": a["anchor"], "basis": a["basis"]} for a in adjustments],
        "uncalibrated": list(uncalibrated or []),
        "tol": TOL,
        "min_peers": MIN_PEERS,
    }


def selftest() -> int:
    """空间一致性封顶的单元回归（MONA 场景 + 小样本 + 容忍带 + 不倒扣）。"""
    # 构造体积（m³）与 space，模拟真实分布：MONA 体积中上却给全场最高
    physics = {
        "小鹏monal05": {"length_mm": 4870, "width_mm": 1930, "height_mm": 1636},  # 15.38
        "蔚来es6": {"length_mm": 4854, "width_mm": 1995, "height_mm": 1703},       # 16.49
        "长安启源q06": {"length_mm": 4837, "width_mm": 1970, "height_mm": 1670},   # 15.91
        "零跑c11": {"length_mm": 4780, "width_mm": 1905, "height_mm": 1658},       # 15.10
        "小鹏g6": {"length_mm": 4758, "width_mm": 1920, "height_mm": 1650},        # 15.07
        "问界m5": {"length_mm": 4785, "width_mm": 1930, "height_mm": 1620},        # 14.96
    }
    def mk(m, sp):
        return {"model": m, "scores": {"space": sp}}
    cands = [mk("小鹏MONA L05", 9.3), mk("蔚来ES6", 8.3), mk("长安启源Q06", 8.8),
             mk("零跑C11", 9.2), mk("小鹏G6", 8.0), mk("问界M5纯电版", 7.6)]
    adj, uncal = calibrate_space(cands, physics=physics)
    got = {a["model"]: a["capped"] for a in adj}
    assert "小鹏MONA L05" in got, f"MONA 虚高未被抓出: {got}"
    assert got["小鹏MONA L05"] < 9.3, "MONA 未被封顶"
    assert "蔚来ES6" not in got, "体积最大且分最低的车不该被封顶"
    assert cands[1]["scores"]["space"] == 8.3, "不倒扣：ES6 原分保持"
    assert all(c.get("score_cap_reasons") for c in cands if c["model"] in got), "封顶未留审计"
    assert uncal == [], f"6 款都有尺寸，不该有 uncalibrated: {uncal}"

    # 小样本不封顶：可比车 < 3 款
    c2 = [mk("甲", 9.5), mk("乙", 7.0)]
    a2, _ = calibrate_space(c2, physics={"甲": {"length_mm": 4000, "width_mm": 1800, "height_mm": 1600},
                                         "乙": {"length_mm": 3900, "width_mm": 1800, "height_mm": 1600}})
    assert a2 == [], "可比车<3 不该封顶"

    # 无尺寸数据 → uncalibrated，不封顶（research 待补）
    c3 = [mk(f"车{i}", 9.0) for i in range(4)]
    a3, u3 = calibrate_space(c3, physics={})
    assert a3 == [] and len(u3) == 4, "无尺寸数据应进 uncalibrated 且不封顶"

    # 容忍带内（差 <= tol）不封顶
    p4 = {f"t{i}": {"length_mm": 4800 - i, "width_mm": 1900, "height_mm": 1650} for i in range(4)}
    c4 = [mk("T0", 8.8), mk("T1", 8.5), mk("T2", 8.5), mk("T3", 8.5)]  # 8.8-8.5=0.3<0.4
    a4, _ = calibrate_space(c4, physics=p4)
    assert a4 == [], f"容忍带内不该封顶: {a4}"

    # 轴距优先：补录 wheelbase_mm 后用它而非体积（W0 轴距最小却分最高 → 被抓）
    p5 = {"w0": {"wheelbase_mm": 2897, "length_mm": 4800, "width_mm": 1900, "height_mm": 1650},
          "w1": {"wheelbase_mm": 2898, "length_mm": 4800, "width_mm": 1900, "height_mm": 1650},
          "w2": {"wheelbase_mm": 2899, "length_mm": 4800, "width_mm": 1900, "height_mm": 1650},
          "w3": {"wheelbase_mm": 2900, "length_mm": 4800, "width_mm": 1900, "height_mm": 1650}}
    c5 = [mk("W0", 9.6), mk("W1", 8.0), mk("W2", 8.0), mk("W3", 8.0)]
    a5, _ = calibrate_space(c5, physics=p5)
    assert a5 and a5[0]["basis"] == "wheelbase_mm", f"应优先轴距: {a5}"
    return 0


if __name__ == "__main__":
    import sys
    import paths
    sys.path.insert(0, str(paths.ROOT))
    print("space_consistency selftest:", "PASS" if selftest() == 0 else "FAIL")
    sys.exit(0)
