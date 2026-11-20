# -*- coding: utf-8 -*-
"""
midsuv_full_regress.py — 跨能源【完整正式榜】改造回归
测生成链路而非快照：每次重新运行 build_midsuv_full.py，再从新产物反查。
覆盖本次用户诉求「要有环境打分 + 18区块导航」，并含反向篡改：
  - 环境分引擎来自比较器单一事实源（不重写）
  - 12项用车条件 + 6条硬条件 + 物理续航 都在产出页面里
  - 18区块导航齐全（对齐 board_view BLOCKS 契约）
  - cars 数据含补采分（不是母池空分）
  - 页面能真实触发环境分（node 跑 adjust：无家充→纯电-15、限牌→燃油-8）
  - 反向篡改：删区块/删占位符注入后回归必须 FAIL
"""
import os, sys, json, re, subprocess, tempfile, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS = os.path.join(HERE, "runs")
OUT = os.path.join(RUNS, "midsuv_202610_12-18万_完整交互榜.html")
sys.path.insert(0, HERE)

PASS, FAIL = [], []
def check(name, cond, msg=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" :: {msg}" if msg else ""))


def run():
    print("[跨能源完整榜回归]")
    # 1. 重新生成（测链路）
    import build_midsuv_full as B
    r = subprocess.run([sys.executable, "build_midsuv_full.py"], cwd=HERE,
                       capture_output=True, text=True, timeout=120)
    check("生成脚本成功退出", r.returncode == 0, r.stderr[:200])

    html = open(OUT, encoding="utf-8").read()
    # 2. 环境分引擎三要素
    check("交互引擎adjust()内嵌", "function adjust" in html)
    check("12项用车条件FACTORS内嵌", "winter_temp" in html and "home_charger" in html and "drive_style" in html)
    check("6条硬条件逻辑内嵌", "无充电桩" in html and "限牌城市" in html and "营运" in html)
    check("物理续航引擎内嵌", "rangeCalc" in html and "PHYS" in html)
    check("场景力度滑块", "wEnergy" in html and "场景调节力度" in html)

    # 3. 18区块导航（对齐契约）
    import cross_energy_sections as CES
    nav_links = len(re.findall(r'href="#sec-', html))
    sec_blocks = html.count('<section class="xsec"')
    check("导航链接数=区块数", nav_links == len(CES.BLOCKS) == sec_blocks,
          f"nav={nav_links} sec={sec_blocks} expect={len(CES.BLOCKS)}")
    for bid,_ in CES.BLOCKS:
        check(f"区块存在:{bid}", f'id="sec-{bid}"' in html)
    check("必要区块契约满足", CES.REQUIRED.issubset({b[0] for b in CES.BLOCKS}))

    # 4. cars 数据含补采分（不是空分）
    m = re.search(r'const CARS=(\[.*?\]);\s*const FACTORS', html, re.S)
    check("CARS数据注入", bool(m))
    if m:
        cars = json.loads(m.group(1))
        check("车数=22", len(cars) == 22, str(len(cars)))
        check("产品力分非空(补采分生效)", all(c.get("product",0) > 60 for c in cars),
              f"min={min(c['product'] for c in cars)}")
        powers = set(c["power"] for c in cars)
        check("覆盖多能源", len(powers) >= 3, str(powers))

    # 4b. 定稿榜固定/推演器分离（v9.7.49）：定稿区静态、推演器只改标识
    check("比较器有重排钩子", "window.onBoardRerender" in html and "onBoardRerender(rows, cond, scale)" in html)
    check("定稿榜导航标注", "定稿榜导航" in html and "不随场景变化" in html)
    check("推演器标识存在", 'id="sim-state"' in html and "推演态" in html)
    for cid in ("fixed-three", "fixed-robust"):
        check(f"定稿容器存在:{cid}", f'id="{cid}"' in html)
    check("6场景稳健性内嵌", all(s in html for s in ("无家充","北方极寒","长途高频","营运","限牌城市")))
    check("旧动态容器已移除", 'id="dyn-' not in html)
    check("定稿区JS首次算一次", "finalizeFixed" in html and "ROBUST_SCENARIOS" in html)
    # node + DOM stub 实跑：改条件定稿区不变、推演态标识变
    nt = subprocess.run(["node", "dynamic_blocks_test.js", OUT], cwd=HERE,
                        capture_output=True, text=True, timeout=60)
    check("定稿固定/推演分离(node)全过", nt.returncode == 0 and "10/10 通过" in nt.stdout,
          (nt.stdout or nt.stderr)[-300:])
    # 4c. 全环境项测试：12项×每个选项都生效、结果无异常（用户要求"每个环境修改是否起效果"）
    et = subprocess.run(["node", "env_options_test.js", OUT], cwd=HERE,
                        capture_output=True, text=True, timeout=90)
    check("全环境项测试(node)全过", et.returncode == 0 and "77/77 通过" in et.stdout,
          (et.stdout or et.stderr)[-400:])

    # 5. 占位符已替换
    check("无占位符残留", "__EXTRA_" not in html and "__CARS_JSON__" not in html)
    check("HTML闭合", html.strip().endswith("</html>"))

    # 6. 比较器默认行为未破坏（向后兼容：原页面仍整池67款）
    d = subprocess.run([sys.executable, "build_energy_compare.py"], cwd=HERE,
                       capture_output=True, text=True, timeout=120)
    check("比较器默认仍可生成", d.returncode == 0 and "67 cars" in d.stdout, d.stdout[:100])

    # 7. 反向篡改：抹掉 extra_tail 注入后，区块必须消失（证明注入真生效）
    import build_energy_compare as EC
    tmp = tempfile.mkdtemp()
    try:
        bad_out = os.path.join(tmp, "bad.html")
        EC.build(cars_override=[{"model":"测试","cls":"中型SUV","body":"SUV","power":"BEV",
                                 "price":15,"priceLow":15,"priceHigh":15,"product":80,
                                 "cltc":500,"kwh":60,"cons":13,"consSrc":"事实库",
                                 "weight":1800,"cd":0.25,"area":2.5,"voltage":None,"swap":False,"cdaPerTon":0.35}],
                 out=bad_out, extra_head="", extra_tail="")
        bad_html = open(bad_out, encoding="utf-8").read()
        check("反向篡改:不注入则无导航区块", "boardNav" not in bad_html and "id=\"sec-" not in bad_html)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n结果: {len(PASS)}/{len(PASS)+len(FAIL)} 通过")
    if FAIL:
        print("失败:", FAIL)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
