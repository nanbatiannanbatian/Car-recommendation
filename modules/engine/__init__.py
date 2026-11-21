# -*- coding: utf-8 -*-
"""engine —— 打分口径 / 闸门 / 事实注入（纯计算，无 IO、无网络）

职责：拿到已经在内存里的候选池与事实库，算出可复现的分数与档位。
  leaderboard.score_rows(cands, cfg, weights)   8 维加权分 + 证据等级
  leaderboard.tier_report(...) / top_table(...) 三档分级、上表
  ranking.apply_gates(cands, cfg)              级别/动力/价格/证据硬拦
  energy_router.classify(query)                榜型自动判定（A 同能源 / B 跨能源）
  trim_board / trim_annotate                   逐配置评分与统一人工口径
  trim_coverage.check(cands, trims)            出榜前逐配置覆盖校验（铁律守门）
  robust / verify_mcda                         10 种 MCDA 交叉 + Kendall tau
  thirdparty / airbags / spec_facts / trims    证据与事实注入（SCORING_ENABLED=False，不改排名）

纪律（改代码前必读）：
  - 事实进分禁止：任何事实库模块都必须 SCORING_ENABLED=False，并有 apply 前后深比较断言。
  - 同一事实多字段必须有一致性门禁（cross_verify / evidence_consistency / sibling_check）。
  - 口径改动必须同时改回归，否则下次出榜必退化。

不职责：取数 -> research；编排/渲染 -> board；续航/环境分 -> physics/envscore。

可独立发布性：概念上可独立（购车评分口径库），但当前绑定本项目的 categories 配置结构。
"""
