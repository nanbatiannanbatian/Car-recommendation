# -*- coding: utf-8 -*-
"""CLI 入口（v10.1）：生成物一键重建，实现见 modules/board/bootstrap.py。

新装包首跑先跑 `python3 bootstrap_data.py --check` 看缺什么、怎么建。
"""
import runpy

if __name__ == "__main__":
    runpy.run_module("modules.board.bootstrap", run_name="__main__", alter_sys=True)
