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

# ── 真实空间表现信号（2026-11-21 用户问「空间要考虑得房率/储物/真实表现吗」）──
# 体积只是上限的天花板，不反映「尺寸→体验」的转换率。得房率（套内/建筑面积比，
# 车里=乘员舱占车长的比例）是随包 evidence 里已有的硬信息，用它补两件事：
#   1) 得房率显著偏低却 space 高（腾势N7 得房率64%却8.5）→ 用「同尺寸且得房率正常
#      的车」的 space 中位再压一道（这类车真实乘坐空间小，纸面尺寸占优也不该高分）。
#   2) 得房率显著偏高（小鹏G6 85.6%）→ 体积封顶容忍带临时放宽，避免误伤高得房率车。
# 阈值由来：本榜得房率 3 款有效值 64/83.34/85.6，正常档 >=78，高得房率 >=85。
EFF_NORMAL_MIN = 78.0       # 视为「得房率正常」的下限（%）
EFF_HIGH_MIN = 85.0         # 视为「高得房率」的下限（%），触发体积封顶放宽
EFF_GAP = 10.0              # 得房率低于正常群体锚点这么多百分点才算「显著偏低」
EFF_TOL = 0.15              # 得房率信号的容忍带（比体积 tol 小：得房率已是强证据，不该再宽待）
MIN_EFF_PEERS = 2           # 得房率锚点最少可比车（实测仅 2 款正常 → 取 2，另加 gap 双条件防噪声）
HIGH_EFF_RELIEF = 0.3       # 高得房率车体积封顶容忍带的额外放宽（分）

# ── 储物容积一致性（2026-11-21，用户问「极氪7X 后备箱大，MONA 空间分凭什么更高」）──
# 之前的后备箱容积被 space_features 标成 CAT_STORAGE「仅披露不进分」，而页面连披露都没有，
# 读者只能凭感觉猜。这里补一道**只压虚高**的信号：
#   同尺寸车里后备箱容积明显偏小（< 同组中位 × 70%）却 space 虚高 → 压到同组 space 中位。
# 容积**大**不加分——后备箱大 ≠ 乘坐空间好，不能建立错误因果（容积装东西、空间坐人）。
STORAGE_RATIO = 0.7         # 本车容积 < 同组中位 × 此值 → 视为显著偏小
# 容忍带取 0.15（比体积的 0.4 严）：容积是直接可比的数字，不是间接代理；
# 若与体积同宽，信号 1 总会先触发，信号 3 永远轮不上（等于白写）。
STORAGE_TOL = 0.15
MIN_STORAGE_PEERS = 3       # 储物锚点最少可比车


def _trunk_of(c: dict) -> float | None:
    """后备箱容积（L）——从 evidence.space_evidence 提取（唯一出处 space_features）。"""
    from modules.engine.space_features import extract_space_features
    for f in extract_space_features(c):
        if f.get("key") == "trunk":
            try:
                return float(f.get("value"))
            except (TypeError, ValueError):
                return None
    return None


def _load_physics():
    """读随包物理参数库，key 用 canon 归一化（跨库写法不一，T6 踩过坑）。"""
    import paths
    from modules.contracts.model_names import canon
    d = json.load(open(paths.cat("vehicle_physics.json"), encoding="utf-8"))
    return {canon(x.get("model") or ""): x for x in d.get("cars", [])}


_TRIM_WB_CACHE: dict | None = None


def _load_trim_wheelbase() -> dict:
    """trims.json 里的**官方轴距**：{canon(车型): wheelbase_mm}。

    2026-11-22：汽车之家参数页「车身」组（id=1169）有官方轴距，实测覆盖 382/427 配置；
    而 `vehicle_physics.json` 的 `wheelbase_mm` 覆盖率**一直是 0%** —— 空间维封顶因此
    长期只能用车身三维体积做代理。官方轴距更准（它就是轴距，不是体积近似）。
    """
    global _TRIM_WB_CACHE
    if _TRIM_WB_CACHE is not None:
        return _TRIM_WB_CACHE
    out: dict = {}
    try:
        import os
        import paths
        from modules.contracts.model_names import canon
        # 2026-11-22：走统一入口（人工库 + 自动库合流）——人工车型的官方轴距
        # 已随迁移回填进人工库，只读自动库会漏掉它们。
        from modules.data.trims_store import load as _load
        for e in _load().values():
            m = e.get("model")
            if not m:
                continue
            vals = [t.get("wheelbase_mm") for t in (e.get("trims") or [])
                    if isinstance(t.get("wheelbase_mm"), (int, float)) and t.get("wheelbase_mm")]
            if vals:
                vals.sort()
                out[canon(m)] = float(vals[len(vals) // 2])
    except (ImportError, OSError, ValueError):
        out = {}
    _TRIM_WB_CACHE = out
    return out


def size_of(c, physics=None):
    """返回 (size, basis)：优先官方轴距(mm)，否则长×宽×高体积(m³)；无数据返回 (None, None)。

    数据源优先级：
      · **显式传入 physics** → 只用这份数据（测试隔离/调用方指定口径），
        它没有轴距才退官方轴距。传了 physics 却混进全局 trims 的真车轴距，
        会让 selftest 的构造样本被真实数据盖掉（t083/t084 曾因此失效）。
      · 默认 → 官方轴距（trims.json 汽车之家参数页「车身」组）→ 人工库轴距 → 人工库体积。
    """
    from modules.contracts.model_names import canon
    key = canon(c.get("model") or "")
    if physics is not None:
        rec = physics.get(key)
        wb = (rec or {}).get("wheelbase_mm")
        if isinstance(wb, (int, float)) and wb > 0:
            return float(wb), "wheelbase_mm"
        # 官方轴距比体积更准（它就是轴距，不是外廓近似）——放在体积之前。
        # 顺序反了的话，vehicle_physics 有长宽高就永远走体积，官方轴距白接。
        tb = _load_trim_wheelbase().get(key)
        if isinstance(tb, (int, float)) and tb > 0:
            return float(tb), "wheelbase_mm(官方)"
        if rec:
            L, W, H = rec.get("length_mm"), rec.get("width_mm"), rec.get("height_mm")
            if all(isinstance(x, (int, float)) and x > 0 for x in (L, W, H)):
                return round(L * W * H / 1e9, 3), "volume_m3"
        return None, None
    tb = _load_trim_wheelbase().get(key)
    if isinstance(tb, (int, float)) and tb > 0:
        return float(tb), "wheelbase_mm(官方)"
    phys = _load_physics()
    rec = phys.get(key)
    if not rec:
        return None, None
    wb = rec.get("wheelbase_mm")
    if isinstance(wb, (int, float)) and wb > 0:
        return float(wb), "wheelbase_mm"
    L, W, H = rec.get("length_mm"), rec.get("width_mm"), rec.get("height_mm")
    if all(isinstance(x, (int, float)) and x > 0 for x in (L, W, H)):
        return round(L * W * H / 1e9, 3), "volume_m3"
    return None, None


def _apply_cap(c, anchor, prev, reason_detail, out, basis, tol_eff, peers, signal):
    """统一封顶落点：只压虚高、不倒扣、留审计。"""
    c["scores"]["space"] = round(anchor, 2)
    reason = (f"space 一致性封顶（{signal}）：{reason_detail}，"
              f"本车原 {prev:g} 超出容忍带 {tol_eff:g}，封顶至 {anchor:g}（不倒扣、仅压虚高）")
    c.setdefault("score_cap_reasons", []).append(reason)
    out.append({"model": c.get("model"), "dim": "space", "prev": prev,
                "capped": round(anchor, 2), "anchor": round(anchor, 2),
                "peers": peers, "basis": basis, "tol": tol_eff, "signal": signal})


def calibrate_space(cands, physics=None, tol=TOL, min_peers=MIN_PEERS,
                    eff_gap=EFF_GAP, eff_tol=EFF_TOL, min_eff_peers=MIN_EFF_PEERS,
                    high_eff_relief=HIGH_EFF_RELIEF,
                    storage_ratio=STORAGE_RATIO, storage_tol=STORAGE_TOL,
                    min_storage_peers=MIN_STORAGE_PEERS):
    """空间分一致性封顶，原地修改 cands。

    两级信号：
      1) 尺寸硬数据（轴距>体积）：尺寸不小于本车的车 space 中位为上限（原逻辑）。
         高得房率车 tol 临时 +high_eff_relief，避免误伤得房率高的车。
      2) 真实空间表现（得房率）：得房率显著低于同尺寸正常车却 space 高 →
         用「同尺寸且得房率正常」车的 space 中位再压一道。

    Returns (adjustments, uncalibrated)：
      adjustments = [{model, dim, prev, capped, anchor, peers, size, basis, tol, signal}]
      uncalibrated = [model, ...]  # 缺尺寸数据，research 待补
    """
    from modules.engine.space_features import efficiency_of

    phys = physics if physics is not None else _load_physics()
    scored = []
    for c in cands:
        s = (c.get("scores") or {}).get("space")
        if not isinstance(s, (int, float)):
            continue
        size, basis = size_of(c, phys)
        eff = efficiency_of(c)
        scored.append((c, float(s), size, basis, eff))

    out: list[dict] = []
    uncal: list[str] = []
    capped_models: set[str] = set()
    trunks = {c.get("model"): _trunk_of(c) for c in cands}

    # ── 信号 1：尺寸硬数据封顶（高得房率车放宽容忍带）──
    for c, space, size, basis, eff in scored:
        if size is None:
            uncal.append(c.get("model"))
            continue
        peers = [v for _, v, s2, _, _ in scored if s2 is not None and s2 >= size - 1e-9]
        if len(peers) < min_peers:
            continue
        anchor = round(statistics.median(peers), 2)
        tol_eff = tol + (high_eff_relief if (eff is not None and eff >= EFF_HIGH_MIN) else 0.0)
        if space <= anchor + tol_eff + 1e-9:
            continue
        prev = space
        _apply_cap(c, anchor, prev,
                   f"尺寸不小于本车（{size:g}{basis}）的 {len(peers)} 款车 space 中位 {anchor:g}"
                   + (f"；本车得房率{eff:g}%属高档，容忍带放宽至{tol_eff:g}" if eff and eff >= EFF_HIGH_MIN else ""),
                   out, f"{basis}" + ("+high_eff" if eff and eff >= EFF_HIGH_MIN else ""),
                   tol_eff, len(peers), "size")
        capped_models.add(c.get("model"))

    # ── 信号 2：得房率真实性封顶（低得房率却高分）──
    # v10.12.1：锚点群体**不再限定同尺寸** —— 得房率是「套内/车长」的**比例**，
    # 与车长无关，跨尺寸可比；而轴距/体积是绝对尺寸，跨尺寸不可比。
    # 之前把锚点绑在「同尺寸」上，接了官方轴距后对照组骤减（腾势N7 的同轴距
    # 得房率正常车只剩 0 个）→ 样本不足 → 低得房率虚高反而抓不到了（机制被削弱）。
    # 双条件防噪声：本车得房率 < 锚点群体下沿 - eff_gap，且 space > 锚点中位 + tol。
    for c, space, size, basis, eff in scored:
        if c.get("model") in capped_models:
            continue                       # 已被信号1压过，不重复压
        if eff is None or eff >= EFF_NORMAL_MIN:
            continue
        if size is None:
            continue
        normal = [(c2.get("model"), sp2, eff2) for c2, sp2, sz2, _, eff2 in scored
                  if sp2 is not None and eff2 is not None
                  and eff2 >= EFF_NORMAL_MIN]
        if len(normal) < min_eff_peers:
            continue
        eff_values = [e for _, _, e in normal]
        # 「正常群体下沿」取最小得房率（保守：只要明显低于最差正常车才算异常）
        normal_floor = min(eff_values)
        if eff > normal_floor - eff_gap + 1e-9:
            continue                       # 差距不够，不算显著偏低
        anchor = round(statistics.median([sp for _, sp, _ in normal]), 2)
        if space <= anchor + eff_tol + 1e-9:
            continue
        prev = space
        _apply_cap(c, anchor, prev,
                   f"得房率仅 {eff:g}%，明显低于得房率正常车（{len(normal)}款，最低 {normal_floor:g}%）"
                   f"的 space 中位 {anchor:g}",
                   out, "efficiency", eff_tol, len(normal), "efficiency")
        capped_models.add(c.get("model"))

    # ── 信号 3：储物容积一致性（后备箱明显偏小却 space 虚高 → 压）──
    # 容积**大不加分**：后备箱大 ≠ 乘坐空间好（容积装东西、空间坐人），
    # 只能用来压「储物明显不足却声称空间大」的虚高，不制造错误因果。
    for c, space, size, basis, eff in scored:
        if c.get("model") in capped_models:
            continue                       # 已被信号1/2压过，不重复压
        if size is None:
            continue
        vol = trunks.get(c.get("model"))
        if vol is None:
            continue
        peers = [(c2.get("model"), sp2)
                 for c2, sp2, sz2, _b, _e in scored
                 if sz2 is not None and sz2 >= size - 1e-9
                 and trunks.get(c2.get("model")) is not None]
        if len(peers) < min_storage_peers:
            continue
        vol_med = statistics.median([trunks[m] for m, _s in peers])
        if vol_med <= 0 or vol >= vol_med * storage_ratio:
            continue                       # 容积不显著偏小
        anchor = round(statistics.median([sp for _m, sp in peers]), 2)
        if space <= anchor + storage_tol + 1e-9:
            continue
        prev = space
        _apply_cap(c, anchor, prev,
                   f"后备箱容积 {vol:g}L 明显小于同尺寸车（{len(peers)}款，中位 {vol_med:g}L）"
                   f"的 space 中位 {anchor:g}",
                   out, "storage", storage_tol, len(peers), "storage")
        capped_models.add(c.get("model"))
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
    # v10.12.1：车名用**假名**（真车名会被 trims.json 里的真实官方轴距盖掉，
    # 构造样本就失效了——2026-11-22 首次接官方轴距时踩过）。
    physics = {
        "尺寸车a": {"length_mm": 4870, "width_mm": 1930, "height_mm": 1636},  # 15.38
        "尺寸车b": {"length_mm": 4854, "width_mm": 1995, "height_mm": 1703},   # 16.49
        "尺寸车c": {"length_mm": 4837, "width_mm": 1970, "height_mm": 1670},   # 15.91
        "尺寸车d": {"length_mm": 4780, "width_mm": 1905, "height_mm": 1658},   # 15.10
        "尺寸车e": {"length_mm": 4758, "width_mm": 1920, "height_mm": 1650},    # 15.07
        "尺寸车f": {"length_mm": 4785, "width_mm": 1930, "height_mm": 1620},    # 14.96
    }
    def mk(m, sp):
        return {"model": m, "scores": {"space": sp}}
    cands = [mk("尺寸车A", 9.3), mk("尺寸车B", 8.3), mk("尺寸车C", 8.8),
             mk("尺寸车D", 9.2), mk("尺寸车E", 8.0), mk("尺寸车F", 7.6)]
    adj, uncal = calibrate_space(cands, physics=physics)
    got = {a["model"]: a["capped"] for a in adj}
    assert "尺寸车A" in got, f"虚高样本未被抓出: {got}"
    assert got["尺寸车A"] < 9.3, "虚高样本未被封顶"
    assert "尺寸车B" not in got, "体积最大且分最低的车不该被封顶"
    assert cands[1]["scores"]["space"] == 8.3, "不倒扣：原分保持"
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

    # ── 得房率真实表现信号（v10.2.9）──
    # 构造：4 款同尺寸，得房率 64/83/85/84，得分 8.5/8.3/8.0/8.0。
    # 低得房率车(64)给 8.5 > 正常车中位 8.15+EFF_TOL → 应封顶；高得房率车(85)不误伤。
    from modules.engine.space_features import efficiency_of as _eff
    phys6 = {f"得房车{i}": {"length_mm": 4860, "width_mm": 1935, "height_mm": 1602} for i in range(4)}
    effs = [64.0, 83.0, 85.0, 84.0]
    sps = [8.5, 8.3, 8.0, 8.0]
    def mke(i):
        c = mk(f"得房车{i}", sps[i])
        c["evidence"] = {"space_evidence": f"得房率{effs[i]}%"}
        return c
    c6 = [mke(i) for i in range(4)]
    assert all(_eff(c) is not None for c in c6), "得房率提取失败"
    a6, _ = calibrate_space(c6, physics=phys6)
    eff_hits = {a["model"]: a["capped"] for a in a6 if a.get("signal") == "efficiency"}
    # 低得房率车被压；其余正常/高得房率车不被压
    assert "得房车0" in eff_hits, f"低得房率虚高未被抓: {a6}"
    assert all(f"得房车{i}" not in {a["model"] for a in a6} for i in (1, 2, 3)), f"误伤正常得房率车: {a6}"
    assert all(c6[i]["scores"]["space"] == sps[i] for i in (1, 2, 3)), "正常车分数被改"

    # 高得房率放宽：体积封顶时，高得房率车 tol +relief，容忍带内不封顶
    # 3 款同尺寸，低分车给 8.8、中位 8.5，差 0.3 < 默认tol0.4 本就不封；
    # 改成中位 8.4 → 差 0.4 卡边界；高得房率车 tol=0.7 → 不封，普通车 tol=0.4 → 封。
    phys7 = {f"高得房{i}": {"length_mm": 4800, "width_mm": 1900, "height_mm": 1650} for i in range(4)}
    def mkh(i, sp, eff):
        c = mk(f"高得房{i}", sp)
        c["evidence"] = {"space_evidence": f"得房率{eff}%"}
        return c
    # 3 款 8.4 做锚点，1 款 8.9 高得房率(≥85) → 差 0.5：普通车会被封(>0.4)，高得房率不封(0.5<0.7)
    c7 = [mkh(0, 8.9, 86.0), mkh(1, 8.4, 82.0), mkh(2, 8.4, 82.0), mkh(3, 8.4, 82.0)]
    a7, _ = calibrate_space(c7, physics=phys7)
    assert "高得房0" not in {a["model"] for a in a7}, f"高得房率车被体积封顶误伤: {a7}"

    # 小样本保护：只有 1 款正常得房率车 → 不足以做锚点，不封顶
    c8 = [mke(0), {"model": "唯一正常", "scores": {"space": 8.0},
                   "evidence": {"space_evidence": "得房率83%"}}]
    a8, _ = calibrate_space(c8, physics={"得房车0": {"length_mm": 4860, "width_mm": 1935, "height_mm": 1602},
                                         "唯一正常": {"length_mm": 4800, "width_mm": 1900, "height_mm": 1650}})
    assert not any(a.get("signal") == "efficiency" for a in a8), f"小样本不该做效率封顶: {a8}"

    # ── 储物容积信号（v10.11.0）──
    # 构造：同尺寸 4 款，后备箱 905/822/763/420L，space 8.2/8.2/8.1/8.4。
    # 容积 420L 那款明显偏小（同组中位 792.5 × 0.7 = 554.75）却 space 最高 8.4 → 应被压。
    # 注意 space 刻意排布成「信号1（体积封顶）不触发」的样子，单独验证信号 3。
    phys9 = {f"储物车{i}": {"length_mm": 4800, "width_mm": 1930, "height_mm": 1650}
             for i in range(4)}
    def mks(i, sp, vol):
        c = mk(f"储物车{i}", sp)
        c["evidence"] = {"space_evidence": f"后备箱约{vol}L"}
        return c
    c9 = [mks(0, 8.2, 905), mks(1, 8.2, 822), mks(2, 8.1, 763), mks(3, 8.4, 420)]
    a9, _ = calibrate_space(c9, physics=phys9)
    st_hits = [a for a in a9 if a.get("signal") == "storage"]
    assert st_hits, f"储物容积偏小却虚高未被抓: {a9}"
    assert st_hits[0]["model"] == "储物车3", f"压错了车: {st_hits}"
    assert st_hits[0]["capped"] < 8.4, st_hits
    # 容积大的车不被压（后备箱大 ≠ 乘坐空间好，不得反向加分）
    assert "储物车0" not in {a["model"] for a in a9}, f"容积大不该被压: {a9}"
    # 无后备箱数据的车不进储物信号
    c10 = [mk(f"无容积车{i}", 8.2) for i in range(4)]
    a10, _ = calibrate_space(c10, physics=phys9)
    assert not any(a.get("signal") == "storage" for a in a10), a10
    return 0


if __name__ == "__main__":
    import sys
    import paths
    sys.path.insert(0, str(paths.ROOT))
    print("space_consistency selftest:", "PASS" if selftest() == 0 else "FAIL")
    sys.exit(0)
