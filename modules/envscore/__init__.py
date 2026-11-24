# -*- coding: utf-8 -*-
"""envscore —— 跨能源「环境分 / 用车条件适配」引擎

职责：给一台车 + 一套用车条件（11 项：营运/冬季气候带/年里程/单次里程/长途/充电/限牌/持有/高速/补能耐心/家庭车队），
算出该加/减多少分、哪些条件下直接出局（hard 硬闸门），以及综合档位 T1/T2/T3。

结构（v10 起拆开，之前引擎和页面混在一个 937 行文件里）：
  engine.js               唯一事实源：hwyMult / rangeCalc / effRange / adjust / compositeTier
                          （纯函数，无 DOM；node 回归直接跑它）
  build_energy_compare.py 页面生成器：数据加载 + HTML/CSS 骨架 + 把 engine.js 内联成自包含页面
  FACTORS/DIMS/WEIGHT_MAP 条件表与权重（Python 侧注入页面）

依赖方向：envscore -> physics（PHYS 常量），单向。envscore 不认识 engine/board。

可独立发布性：YES 依赖仅 physics，可对外发布「我的用车条件下这台车值多少分」适配计算器。
产出：energy_compare.html（自包含，可离线打开）。

纪律：环境分只在 B 类跨能源榜生效；A 类同能源榜不加环境分（见 SKILL.md 第零步）。
"""




