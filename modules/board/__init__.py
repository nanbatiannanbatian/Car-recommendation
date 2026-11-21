# -*- coding: utf-8 -*-
"""board —— 出榜编排与展示

职责：把 research 取来的数据、engine 算出的分数、physics/envscore 的续航与环境分，
编排成一次可复现的出榜，并渲染成 HTML / Markdown / summary.json。
  pipeline    单次出榜全链路（闸门->打分->三档->门禁->报告）
  monthly     月度跑批（探测月份->建池->取数->出榜->留档）
  board_v2    统一榜单引擎 v2（打分主榜 + 事实参考列 + 价位段子榜）
  board_view  页面区块注册表（展示层唯一事实源）
  render_board_html  HTML 渲染器
  master_pool 总候选池（单一事实源，治「每次临时拼池漏车」）
  warehouse / forecast / backfill  本地数据仓库、未上市预测、历史回填
  cross_energy_sections  跨能源正式榜的 14 区块（默认画像静态定稿）

不职责：本包不含打分公式（-> engine）、不含取数（-> research）。

可独立发布性：不可独立。依赖 engine/research/physics/envscore，属于本项目主流程。
"""
