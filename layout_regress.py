# -*- coding: utf-8 -*-
"""CLI 兼容入口（v10.0 模块化）：实现已迁到 modules/regress/layout_regress.py。

保留本文件是为了让文档/回归里的 `python3 layout_regress.py` 继续可用。
逻辑一律改 modules/regress/layout_regress.py，本文件不要写业务代码。
"""
import runpy

if __name__ == "__main__":
    runpy.run_module("modules.regress.layout_regress", run_name="__main__", alter_sys=True)
