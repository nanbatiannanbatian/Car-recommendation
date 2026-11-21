# -*- coding: utf-8 -*-
"""CLI 兼容入口（v10.0 模块化）：实现已迁到 modules/research/auto_trims.py。

保留本文件是为了让文档/重建命令里的 `python3 auto_trims.py` 继续可用。
逻辑一律改 modules/research/auto_trims.py，本文件不要写业务代码。
"""
import runpy

if __name__ == "__main__":
    runpy.run_module("modules.research.auto_trims", run_name="__main__", alter_sys=True)
