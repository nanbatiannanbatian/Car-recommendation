#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tco_cost_regress.py — 持有成本的单位与新增项回归（v10.59.0）

守三件容易悄悄坏掉的事：
  A. 车价单位：链路是「万元」，TCO 内部必须换算成元。曾经直接用万元当元算，
     购置税只有 1 元、残值损失只有 5 元 → 两项最大的固定成本等于没算，盈亏平衡点偏小一个量级。
  B. 新增非能源成本：轮胎按里程折损（电车寿命更短、胎更贵）、非事故易损/电子件年预算；
     且不得与保养（maint）或事故（保费）重复计。
  C. 盈亏平衡：可变成本含「能源+轮胎」，固定项为其余；区间 low ≤ mid ≤ high，缩放不改变可变部分。
"""
import os
import sys

sys.path.insert(0, ".")
from modules.envscore import engine as E  # noqa: E402

FAIL = []


def ck(cond, name, detail=""):
    print(("  PASS  %s" % name) if cond else ("  FAIL  %s :: %s" % (name, detail)))
    if not cond:
        FAIL.append(name)


def car(power, price_wan, cons=None, fuel=None, kwh=None):
    return {"model": "测试%s" % power, "power": power, "price": price_wan,
            "cons": cons, "kwh": kwh, "fuelL100": fuel, "product": 80.0}


COND = {**E.DEFAULT_COND, "annual_km": "15to25", "home_charger": "home"}
KM = E.ANNUAL_KM_MID["15to25"]
YEARS = E.TCO["years"]


def main():
    # A. 车价万元 → 元
    ice = E.tco(car("ICE", 10.0, fuel=6.5), COND)
    ck(8000 <= ice["purchase_tax"] <= 9500, "A 10万燃油购置税约 8850 元（不是 1 元）", ice["purchase_tax"])
    ck(50000 <= ice["residual_loss"] <= 56000, "A 10万燃油残值损失约 5.4 万", ice["residual_loss"])
    exp_tax = 100000 / 1.13 * E.TCO["tax_ice_rate"]
    ck(abs(ice["purchase_tax"] - round(exp_tax)) <= 1, "A 购置税 = 车价/1.13×10%", exp_tax)
    exp_res = 100000 * (1 - E.TCO["residual3"]["ICE"])
    ck(abs(ice["residual_loss"] - round(exp_res)) <= 1, "A 残值损失 = 车价×(1-3年保值率)", exp_res)

    # A2. 新能源购置税减半且有 1.5 万封顶
    bev40 = E.tco(car("BEV", 40.0, cons=16.0, kwh=80), COND)   # 40/1.13×5% ≈ 1.77 万 > 封顶
    ck(bev40["purchase_tax"] == E.TCO["tax_nev_cap"], "A2 40万纯电购置税封顶 1.5 万", bev40["purchase_tax"])
    bev12 = E.tco(car("BEV", 12.0, cons=14.0), COND)
    ck(abs(bev12["purchase_tax"] - round(120000 / 1.13 * 0.05)) <= 1, "A2 12万纯电未触顶（约 5310）",
       bev12["purchase_tax"])

    # B. 轮胎与易损
    bev = E.tco(car("BEV", 12.0, cons=14.0), COND)
    ev_tire = E.TCO["tire"]["price4"]["BEV"] / E.TCO["tire"]["life_km"]["BEV"] * KM * YEARS
    ck(abs(bev["tire_8y"] - round(ev_tire)) <= 1, "B 纯电轮胎 = 3200/4万km × 2万km × 8", bev["tire_8y"])
    ck(bev["tire_8y"] > ice["tire_8y"], "B 纯电轮胎成本高于燃油（寿命短+胎贵）", (bev["tire_8y"], ice["tire_8y"]))
    ck(bev["wear_8y"] == E.TCO["wear"]["BEV"] * YEARS, "B 纯电易损预算 1200×8", bev["wear_8y"])
    ck(bev["maint_8y"] == E.TCO["maint"]["BEV"] * YEARS, "B 保养仍单列（易损不吞保养）", bev["maint_8y"])

    # B2. 分项加总 == 合计（防止加总漂移，也确认没有重复计项）
    parts = (bev["energy_8y"] + bev["ins_8y"] + bev["maint_8y"] + bev["purchase_tax"]
             + bev["residual_loss"] + bev["battery_risk_8y"] + bev["idle_8y"]
             + bev["tire_8y"] + bev["wear_8y"])
    ck(abs(parts - bev["tco_8y"]) <= 4, "B2 九项加总等于 8 年合计", (parts, bev["tco_8y"]))
    ck(ice["idle_8y"] == 0 and bev["idle_8y"] == 0, "B2 驻车项恒 0（条件已删）", (ice["idle_8y"], bev["idle_8y"]))

    # C. 可变/固定拆分与盈亏平衡区间
    ck(abs(bev["variable_per_km"] - (bev["unit_energy"] + bev["unit_tire"])) <= 0.002,
       "C 可变单价 = 能源 + 轮胎", (bev["variable_per_km"], bev["unit_energy"], bev["unit_tire"]))
    fixed = bev["tco_8y"] - bev["energy_8y"] - bev["tire_8y"]
    ck(abs(bev["fixed_8y"] - fixed) <= 3, "C 固定项 = 合计 - 能源 - 轮胎", (bev["fixed_8y"], fixed))

    pool = [car("BEV", 12.0, cons=14.0), car("BEV", 13.0, cons=15.0), car("HEV", 11.0, fuel=4.3),
            car("ICE", 9.0, fuel=6.8)]
    be = E.breakeven_km(pool, COND)
    rng = E.breakeven_km_range(pool, COND)
    ck(be is not None and 5000 <= be <= 60000, "C 平衡点落在合理区间（km/年）", be)
    ck(rng and rng["low"] <= rng["km"] <= rng["high"], "C 区间 low ≤ 中性 ≤ high", rng)
    ck(abs(E.breakeven_km(pool, COND, non_energy_scale=1.0) - be) <= 0.1, "C 缩放 1.0 等于中性口径", be)

    # C2. 非能源成本越高，平衡点越高（方向正确）
    ck(E.breakeven_km(pool, COND, non_energy_scale=1.3) > be > E.breakeven_km(pool, COND, non_energy_scale=0.7),
       "C 固定项变大 → 平衡点上移（方向不反）", be)

    print("\n[tco_cost_regress] %s · %d 项失败" % ("FAIL" if FAIL else "PASS", len(FAIL)))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
