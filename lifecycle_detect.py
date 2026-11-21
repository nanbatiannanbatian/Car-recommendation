# -*- coding: utf-8 -*-
"""CLI 兼容入口（v10.0 模块化）：实现已迁到 modules/research/lifecycle_detect.py。

保留本文件是为了让 SKILL.md / README 里的 `python3 lifecycle_detect.py` 命令继续可用。
逻辑一律改 modules/research/lifecycle_detect.py，本文件不要写业务代码。
"""
import runpy

if __name__ == "__main__":
    runpy.run_module("modules.research.lifecycle_detect", run_name="__main__", alter_sys=True)
