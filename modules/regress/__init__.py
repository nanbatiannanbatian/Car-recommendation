# -*- coding: utf-8 -*-
"""regress —— 回归测试与失败回测

职责：把踩过的坑变成可执行断言，并证明断言不是摆设。
  bug_regression        67 项历史 bug 断言（含 63 条反向注入回测）
  negative_regression   失败回测框架：注入错误后必须 FAIL，否则断言无效
  energy_compare_regress / energy_router_regress / midsuv_full_regress  专项回归
  build_release_regress 打包回归（体积、排除清单、文档引用）
  *.js                  node 跑生成页面的 DOM 实跑（物理/环境项/定稿-推演分离）

约定：断言一律读 paths.src("xxx.py") 的真实现，不读根目录兼容薄壳——
      读薄壳会让断言跑在 10 行转发壳上，形同虚设（v10 迁移时踩过）。

可独立发布性：不可独立。断言绑定本项目口径与产物。
"""
