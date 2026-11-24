#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""power_split_regress.py — 款型按动力形式拆组的回归（v10.58.0）

守护一件事：油混/插混/增程/纯电的油耗与续航事实，必须取自**该动力形式自己的款型**，
不能整条车系共用一批款型（否则 TCO 里油混的低油耗被抹平、结论失真）。
规则唯一来源 = ah_series.spec_power（参数页能源类型优先，缺失才用款型名关键词）。

覆盖：
  A. 能源类型文本 → 动力形式（含 48V 轻混仍算燃油、e-POWER 归油混）
  B. 能源类型缺失 → 款型名关键词（双擎 / 超混电驱 / 插电混动 / 增程 / 纯电）
  C. 拿不到的款型不被硬塞进任何组（unknown 计数），不猜
  D. 某动力拆不出款型 → 退回全车系并标 series_all（披露，不伪造）
  E. 真实缓存（卡罗拉）：油混与燃油款型不是同一批、油混油耗显著更低
  F. build_ice_scores 产出的 car 带 trims_src，且油混/燃油的 trims 不是同一条
零网络：只用 runs/_ah_cache 里的缓存。
"""
import io
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                if os.path.basename(os.getcwd()) != "omni-debate" else ".")
from modules.research import ah_series as AH  # noqa: E402

FAIL = []


def ck(cond, name, detail=""):
    if cond:
        print("  PASS  %s" % name)
    else:
        print("  FAIL  %s :: %s" % (name, detail))
        FAIL.append(name)


def spec(name, energy=None, fuel=None, price=10.0):
    fields = {}
    if energy is not None:
        fields["1149"] = energy
    if fuel is not None:
        # fuel_wltc 的 id 从 AH.ID 取，避免硬编码
        fields[str(AH.ID["fuel_wltc"])] = str(fuel)
    return {"name": name, "price": price, "fields": fields}


def fuel_of(s):
    return AH._num(AH.field(s, AH.ID["fuel_wltc"]))


def main():
    # A. 能源类型文本
    cases = [
        ("汽油", "ICE"), ("汽油+48V轻混", "ICE"), ("柴油", "ICE"),
        ("油电混合", "HEV"), ("汽油电驱", "HEV"), ("超混电驱", "HEV"),
        ("插电式混合动力", "PHEV"), ("插电混动", "PHEV"),
        ("增程式", "EREV"), ("纯电动", "BEV"),
        ("混合动力", "HEV"), ("油电混动", "HEV"),
    ]
    a_ok = all(AH.map_energy(t) == p for t, p in cases)
    ck(a_ok, "A 能源类型文本映射", [t for t, p in cases if AH.map_energy(t) != p])

    # B. 能源类型缺失 → 款型名关键词
    b_cases = [
        ("2026款 双擎 1.8L 精英版", "HEV"),
        ("2026款 超混电驱 超智联Plus", "HEV"),
        ("2026款 1.5T DHT 混动 旗舰", "HEV"),
        ("2026款 1.5T DM-i 插电混动 冠军版", "PHEV"),
        ("2026款 增程 200km 长续航", "EREV"),
        ("2026款 纯电 605km 长续航", "BEV"),
    ]
    for nm, want in b_cases:
        got, src = AH.spec_power(spec(nm, energy=None))
        ck(got == want and src == "name_hint", "B 款型名兜底 %s → %s" % (nm, want), (got, src))
    # 能源类型存在时优先级更高，不被名字带偏
    got, src = AH.spec_power(spec("2026款 双擎 1.8L（汽油版备注）", energy="汽油"))
    ck(got == "ICE" and src == "energy_text", "B 能源类型优先于款型名", (got, src))

    # C. 拿不到 → unknown，不硬塞
    groups, stats = AH.split_specs_by_power([spec("2026款 1.5L 舒适"), spec("2026款 1.5L 豪华", energy="汽油")])
    ck(stats["unknown"] == 1 and len(groups.get("ICE") or []) == 1,
       "C 未知款型计入 unknown 不参与分组", stats)

    # D. 拆不出 → 退回全车系并标 series_all
    only_suv = [spec("2026款 2.0T 四驱", energy="汽油"), spec("2026款 2.0T 两驱", energy="汽油")]
    got_specs, src = AH.specs_for_power(only_suv, "HEV")
    ck(src == "series_all" and len(got_specs) == 2, "D 拆不出该动力时退回全量并披露", (src, len(got_specs)))
    got_specs, src = AH.specs_for_power([], "HEV")
    ck(src == "none" and got_specs == [], "D 空车系标 none", src)

    # D2. 混合车系拆得开：油混只拿油混款、燃油只拿燃油款
    mixed = [spec("2026款 1.5T 燃油 精英", energy="汽油", fuel=6.5),
             spec("2026款 1.5T 燃油 豪华", energy="汽油", fuel=6.3),
             spec("2026款 双擎 1.8L 精英", energy="油电混合", fuel=4.1),
             spec("2026款 双擎 1.8L 豪华", energy="油电混合", fuel=4.3)]
    hv, src_hv = AH.specs_for_power(mixed, "HEV")
    ic, src_ic = AH.specs_for_power(mixed, "ICE")
    ck(src_hv == "per_spec" and len(hv) == 2 and statistics.median([fuel_of(s) for s in hv]) < 4.5,
       "D2 油混只拿油混款（油耗 4.2）", [fuel_of(s) for s in hv])
    ck(src_ic == "per_spec" and len(ic) == 2 and statistics.median([fuel_of(s) for s in ic]) > 6.0,
       "D2 燃油只拿燃油款（油耗 6.4）", [fuel_of(s) for s in ic])
    ck(not (set(s["name"] for s in hv) & set(s["name"] for s in ic)), "D2 两组款型零交集")

    # E. 真实缓存：卡罗拉的油混与燃油不是同一批款型，油混油耗显著更低
    cache_dir = os.path.join(AH.CACHE_DIR)
    car_rec = None
    for f in sorted(os.listdir(cache_dir)):
        if not f.endswith(".json"):
            continue
        d = json.load(io.open(os.path.join(cache_dir, f), encoding="utf-8"))
        if d.get("name") == "卡罗拉":
            car_rec = d
            break
    if car_rec is None:
        ck(False, "E 缓存在位（卡罗拉）", "runs/_ah_cache 无卡罗拉缓存")
    else:
        hv_s, src_hv = AH.specs_for_power(car_rec["specs"], "HEV")
        ic_s, src_ic = AH.specs_for_power(car_rec["specs"], "ICE")
        ck(src_hv == src_ic == "per_spec", "E 卡罗拉两动力均按款型拆开", (src_hv, src_ic))
        ck(len(hv_s) < len(car_rec["specs"]) and len(ic_s) < len(car_rec["specs"]),
           "E 两款型集合都不是整条车系", (len(hv_s), len(ic_s), len(car_rec["specs"])))
        med_hv = statistics.median([v for v in [fuel_of(s) for s in hv_s] if v])
        med_ic = statistics.median([v for v in [fuel_of(s) for s in ic_s] if v])
        ck(med_hv < med_ic * 0.85, "E 油混油耗中位显著低于燃油（≥15%）",
           "HEV %.2f vs ICE %.2f" % (med_hv, med_ic))

    # F. 端到端：build_ice_scores 产出的 car 带 trims_src，且同车系两动力的油耗中位不同
    from modules.research import build_ice_scores as BI
    if car_rec is not None:
        groups_agg = (car_rec.get("agg") or {}).get("groups") or {}
        meds = {}
        for p in ("ICE", "HEV"):
            g = groups_agg.get(p)
            if not g:
                continue
            car, why = BI.car_from_series(car_rec, g, (6, 16), {})
            if not car:
                ck(False, "F %s 出车" % p, why)
                continue
            fl = [t["fuel_l100"] for t in car["trims"] if t.get("fuel_l100")]
            meds[p] = statistics.median(fl) if fl else None
            ck(car.get("trims_src") == "per_spec", "F %s 标记 per_spec" % p, car.get("trims_src"))
            ck(len(car["trims"]) == car.get("trims_n") or car.get("trims_n") <= 14,
               "F %s trims_n 与实际一致" % p, (car.get("trims_n"), len(car["trims"])))
        if meds.get("ICE") and meds.get("HEV"):
            ck(meds["HEV"] < meds["ICE"] * 0.85, "F 两动力油耗中位不同（HEV 更低）",
               "HEV %.2f vs ICE %.2f" % (meds["HEV"], meds["ICE"]))
            ck(meds["HEV"] != meds["ICE"], "F 不再是同一批款型（旧 bug 会完全相同）", meds)

    print("\n[power_split_regress] %s · %d 项失败" % ("FAIL" if FAIL else "PASS", len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
