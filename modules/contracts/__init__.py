# -*- coding: utf-8 -*-
"""contracts —— 数据契约层（全项目最底层，零依赖、零 IO）

放什么：**谁写、谁读都要认同一套的规则**。不是业务逻辑，也不是工具函数。
  trims_schema.py   逐配置阶梯 trims.json 的字段/枚举/校验（采集方 research 写、engine 读）
  model_names.py    车名归一化 canon（跨库对齐，board 与 engine 都用）

为什么单独成层：
  v10.0 曾把 trims.validate 放在 engine，导致 research/auto_trims.py（写库方）
  需要 import engine —— 「采集层依赖打分引擎」，方向倒挂，research 就无法单独发布。
  契约下沉后双方都向下依赖，谁也不用认识对方。

约束（layout_regress 会拦）：
  - 本包不得 import 任何本项目其他包；
  - 不得读写文件、不得联网；
  - 新增契约文件前先想清楚：这是「两方都要守的规则」还是「某一方的实现细节」。
"""
