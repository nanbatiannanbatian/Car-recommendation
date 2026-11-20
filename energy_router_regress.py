# -*- coding: utf-8 -*-
"""
energy_router_regress.py — 「能源形式自动判定」链路回归
按项目元原则：测生成链路而非产物快照，含反向篡改验证。
覆盖用户本次要求「自动准确选择能源形式、不让用户选择」的全部判定分支 + 两个真实踩坑：
  - 电车/电动车识别（首版漏「电车」→「油车和电车一起比」被误判 A 类单燃油）
  - powers 集合无序（首版「新能源」输出 PHEV/EREV/BEV 顺序漂移）
  - 接入出榜链路：build_midsuv 用判定 powers 做能源闸门、页面写判定依据
  - 反向篡改：把默认 B 类改成 A 类后，回归必须 FAIL（证明门禁真生效）
"""
import os, sys, json, tempfile, shutil, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import energy_router as ER

PASS, FAIL = [], []
def check(name, cond, msg=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" :: {msg}" if msg else ""))


def run():
    print("[能源自动判定回归]")
    # 1. 单一能源 → A 类（含别名）
    singles = [
        ("15-20万纯电轿车榜","BEV"), ("纯电车推荐","BEV"), ("电动车前十","BEV"),
        ("燃油中型车前十","ICE"), ("油车SUV排行","ICE"), ("汽油车推荐","ICE"),
        ("插混SUV排行","PHEV"), ("插电混动中型车","PHEV"),
        ("增程SUV推荐","EREV"), ("增程式中型车","EREV"),
        ("油混中型SUV前十","HEV"), ("双擎SUV哪个好","HEV"), ("混动轿车推荐","HEV"),
    ]
    for q, p in singles:
        r = ER.classify(q)
        check(f"A类:{q}", r["board_type"]=="A" and r["powers"]==[p] and not r["needs_user_choice"],
              f"{r['board_type']}/{r['powers']}")

    # 2. 未提能源 → B 类全 5 种含油混（本次核心规则）
    for q in ["中型SUV 12万到18万的前十榜单", "2026年9月中型轿车前十", "12-18万中型SUV排行榜"]:
        r = ER.classify(q)
        check(f"B类默认全类型:{q}",
              r["board_type"]=="B" and r["powers"]==ER.ALL_POWERS and "HEV" in r["powers"]
              and not r["needs_user_choice"], f"{r['board_type']}/{r['powers']}")

    # 3. 显式跨能源 / 多能源 → B
    for q, exp in [("跨能源中型SUV打分", ER.ALL_POWERS),
                   ("不同能源中型车比较", ER.ALL_POWERS),
                   ("油车和电车放在一起比", ["ICE","BEV"]),
                   ("纯电和插混一起比", ["PHEV","BEV"]),
                   ("新能源中型SUV推荐", ["PHEV","EREV","BEV"])]:
        r = ER.classify(q)
        check(f"B类显式:{q}", r["board_type"]=="B" and r["powers"]==exp, f"{r['board_type']}/{r['powers']}")

    # 4. needs_user_choice 恒 False（禁止问用户）
    for q in singles[:3] + ["中型SUV前十", "", "什么SUV都行随便推荐"]:
        check(f"不问用户:{q!r}", ER.classify(q)["needs_user_choice"] is False)

    # 5. powers 顺序稳定（确定性，重复调用一致）
    r1 = ER.classify("新能源SUV")["powers"]
    r2 = ER.classify("新能源SUV")["powers"]
    check("powers顺序确定(新能源)", r1==r2==["PHEV","EREV","BEV"], str(r1))

    # 6. 接入出榜链路：build_midsuv 用判定驱动
    import build_midsuv_202610 as B
    check("出榜链路已接入energy_router", B.ROUTE["board_type"]=="B" and "HEV" in B.ALLOWED_POWERS,
          f"{B.ROUTE['board_type']}/{sorted(B.ALLOWED_POWERS)}")
    check("页面写判定依据(auto)", "自动判定" in B.build_md(*B.load_all()) and "未询问用户" in B.build_md(*B.load_all()))

    # 7. 反向篡改：把默认分支 B 改成 A，再用子进程跑 selftest 必须 FAIL
    tmp = tempfile.mkdtemp()
    try:
        src = os.path.join(HERE, "energy_router.py")
        bad = open(src, encoding="utf-8").read()
        # 篡改"未指定能源→B类"分支的第一处赋值为 A
        tampered = bad.replace('    else:\n        board_type = "B"\n        powers = ALL_POWERS[:]\n        reason = "未指定能源',
                               '    else:\n        board_type = "A"\n        powers = ALL_POWERS[:]\n        reason = "未指定能源', 1)
        check("反向篡改注入成功(源码被改)", tampered != bad)
        badfile = os.path.join(tmp, "energy_router.py")
        open(badfile, "w", encoding="utf-8").write(tampered)
        # 用被篡改模块跑 selftest，期望非零退出（检出）
        p = subprocess.run([sys.executable, badfile, "--selftest"],
                           capture_output=True, text=True, timeout=30)
        check("反向篡改被回归检出(默认B改A→FAIL)", p.returncode != 0,
              f"returncode={p.returncode}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n结果: {len(PASS)}/{len(PASS)+len(FAIL)} 通过")
    if FAIL:
        print("失败:", FAIL)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
