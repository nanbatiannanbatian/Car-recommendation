# -*- coding: utf-8 -*-
"""CLI 入口：安全分映射表复算/校验（实现见 modules/engine/safety_check.py）。

    python3 safety_check.py --check-table        # 校验表内分数是否都等于公式复算值
    python3 safety_check.py --recompute --apply  # 按公式统一重算全表
"""
import runpy

if __name__ == "__main__":
    runpy.run_module("modules.engine.safety_check", run_name="__main__", alter_sys=True)
