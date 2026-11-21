# -*- coding: utf-8 -*-
"""CLI 兼容入口（v10.0 模块化）：实现已迁到 modules/physics/ev_range_model.py。

保留本文件是为了让 SKILL.md / README 里的 `python3 ev_range_model.py` 命令继续可用。
逻辑一律改 modules/physics/ev_range_model.py，本文件不要写业务代码。
"""
import runpy

if __name__ == "__main__":
    runpy.run_module("modules.physics.ev_range_model", run_name="__main__", alter_sys=True)
