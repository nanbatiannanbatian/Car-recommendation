# -*- coding: utf-8 -*-
"""CLI 兼容入口：实现在 modules/board/sales_backfill.py。"""
import runpy

if __name__ == "__main__":
    runpy.run_module("modules.board.sales_backfill", run_name="__main__", alter_sys=True)
