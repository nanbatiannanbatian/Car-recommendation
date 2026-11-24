# -*- coding: utf-8 -*-
"""research —— 取数与证据采集（本项目唯一的对外网络出口）

职责：把外部世界的东西拿回来，落成可复现的 JSON/字典。
  - 车型/销量/备案/碰撞/投诉/口碑/视频证据的采集脚本
  - 检索与站点画像（researcher.py，自带 17 站点配置）
  - 逐配置阶梯导入（trim_ingest）、候选池构建（build_ice_pool/universe_build/discover）

不职责（别塞进来）：
  - 打分、口径闸门 -> modules.engine
  - 榜单编排/渲染 -> modules.board
  - 环境分/续航 -> modules.envscore / modules.physics

契约：本包函数只返回数据（dict/list），不打印排名、不写报告、不 import engine。

可独立发布性：YES 完全独立（无本项目其他包依赖），对外就是
「多平台取数 + 广告识别 + 博主可信度」的检索 skill。
入口见 researcher.py（python3 research/researcher.py --list-sites）。

常用入口：
    fetch_dcd_sales   逐月销量（懂车帝，纯电/插混/增程拆分）
    fetch_cncap       C-NCAP 官方成绩
    fetch_complaints  车质网投诉
    filings           工信部购置税目录（未上市备案）
    ice_crash / ice_sales   非纯电碰撞证据与销量
    build_ice_pool    汽车之家车系索引建池
    auto_trims        逐配置阶梯自动补录
    chassis_fetch     底盘事实采集（悬架形式/驱动形式，多源链+引文）
    discover / universe_build   漏车发现 / 权威清单盘点
"""
