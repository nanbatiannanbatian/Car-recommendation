# -*- coding: utf-8 -*-
"""data 层 —— 事实源读取（2026-11-22 新增）。

定位：位于 contracts 之上、research/engine 之下，专管「读事实库并合流」。

为什么单独一层：逐配置事实源拆成「人工库 categories/trims_manual.json +
自动库 data/gen/trims.json」后，research（采集侧要写）和 engine（计算侧要读）
都需要同一个合并读取入口。放 engine 会让 research→engine 依赖倒挂
（layout_regress 判「包依赖只向下」违规）；放 contracts 又违反「契约层零 IO」。
data 层正好卡在中间：谁都能往下用，不制造倒挂。
"""
