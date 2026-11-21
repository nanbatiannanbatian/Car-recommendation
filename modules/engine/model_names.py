# -*- coding: utf-8 -*-
"""model_names.py —— 车名归一化（全项目单一事实源）

为什么独立成模块：
  之前 `canon()` 只写在 board/master_pool.py 里，engine/cross_verify.py 为了跨库对齐车名
  反向 import 了 board（engine -> board，依赖倒挂）；异常兜底还换成了一个不等价的
  `lambda s: s.strip().lower()`——一旦 import 失败，两边归一化规则静默分叉，
  跨库核验会「对齐不上」却看不出原因。

  归一化规则属于数据契约，归 engine 最底层工具，谁都能往下用，不许反过来。

规则：去空格/全角空格/中点/连字符/斜杠/括号/加号；去尾部「纯电版/纯电动/纯电/EV/BEV」；
      保留品牌前缀（避免误并不同车），最后转小写。
"""
from __future__ import annotations


def canon(model: str) -> str:
    """去重键：去空格/标点/纯电后缀，保留品牌（不剥品牌前缀，避免误并不同车）。"""
    t = str(model or "").strip()
    for ch in (" ", "　", "·", "-", "_", "/", "（", "）", "(", ")", "＋", "+"):
        t = t.replace(ch, "")
    for suf in ("纯电版", "纯电动", "纯电", "EV", "ev", "BEV", "bev"):
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            t = t[: -len(suf)]
            break
    return t.lower()


def selftest() -> None:
    assert canon("大众 ID.AURA T6") == canon("大众ID.AURA T6"), "canon 空格"
    assert canon("零跑C11纯电版") == canon("零跑C11"), "canon 纯电后缀"
    assert canon("MG 07 EV") == canon("MG07"), "canon EV后缀"
    # 保留品牌：不同品牌同后缀不得并车
    assert canon("比亚迪e7") != canon("吉利e7"), "canon 误并不同品牌"
    print("model_names selftest OK")


if __name__ == "__main__":
    selftest()
