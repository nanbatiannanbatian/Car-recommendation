# -*- coding: utf-8 -*-
"""CLI 兼容入口（v10.0 模块化）：实现已迁到 modules/engine/facts_sync.py。

保留本文件是为了让文档/重建命令里的 `python3 facts_sync.py` 继续可用。
逻辑一律改 modules/engine/facts_sync.py，本文件不要写业务代码。
"""
import runpy

if __name__ == "__main__":
    runpy.run_module("modules.engine.facts_sync", run_name="__main__", alter_sys=True)
