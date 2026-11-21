# -*- coding: utf-8 -*-
"""physics —— 电动车实际续航物理模型

职责：只做纯物理。输入一台车（整备质量/风阻/电量/电耗）和一套工况，输出真实续航与分解步骤。
  ev_range_model.estimate(vehicle, scenario) -> dict
  ev_range_model.highway_mult() / base_consumption() / validate_real()

单一事实源约定：
  - 所有物理常量（CdA/吨中位数、市/高速倍率、温度/空调/电压/胎压修正）只在本模块定义。
  - 浏览器端不重写公式：engine.js 通过 PHYS 注入本模块常量，公式语义以本模块为准。
  - 改常量必须跑 ev_range_test.js（9 项）与 energy_compare_regress（20 项）。

不职责：用车偏好/政策加减分（限牌、营运、充电桩）-> modules.envscore。

可独立发布性：YES 完全独立（零第三方依赖，只用标准库），对外就是
「CLTC 标称 -> 现实续航」估算 skill。
"""
