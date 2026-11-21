# -*- coding: utf-8 -*-
"""CLI 入口：补 PPH/口碑样本证据（实现见 modules/research/fill_experience.py）。

    python3 fill_experience.py --check                 # 看哪些车缺可用样本
    python3 fill_experience.py --apply                 # 补齐并写回总池
    python3 fill_experience.py --models 小鹏MONA L05 --apply
"""
import runpy

if __name__ == "__main__":
    runpy.run_module("modules.research.fill_experience", run_name="__main__", alter_sys=True)
