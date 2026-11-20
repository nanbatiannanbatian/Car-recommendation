# -*- coding: utf-8 -*-
"""
build_release_regress.py — 打包发布回归（v9.7.53）
用户报"解压后超3MB被拒"，而旧打包脚本只查压缩后体积。固化：
  1) 解压后大小检查存在且口径正确（MAX_UNCOMPRESSED_BYTES）
  2) 排除清单含无引用的历史数据/一次性脚本/回归测试
  3) 无引用的大文件不会混进包
  4) 反向篡改：把上限改极小后打包必须 FAIL（证明检查真生效）
  5) 实际产物解压后 < 3MB（若已打包）
"""
import os, re, sys, tempfile, shutil, subprocess, zipfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SRC = os.path.join(HERE, "build_release.py")
ZIP = os.path.join(HERE, "dist", "car-recommendation-v9.7.53.zip")
MAX = 3 * 1024 * 1024

P, F = [], []
def check(n, c, m=""):
    (P if c else F).append(n)
    print(f"  {'PASS' if c else 'FAIL'}  {n}" + (f" :: {m}" if m else ""))

def run():
    print("[打包发布回归]")
    src = open(SRC, encoding="utf-8").read()
    # 1) 解压后检查存在
    check("有MAX_UNCOMPRESSED_BYTES常量", "MAX_UNCOMPRESSED_BYTES = 3 * 1024 * 1024" in src)
    check("build_zip累加解压字节", "uncompressed += p.stat().st_size" in src)
    check("build_zip校验解压上限", "uncompressed > MAX_UNCOMPRESSED_BYTES" in src)
    check("错误提示含清理指引", "请清理无引用的大文件" in src)
    # 2) 排除清单
    for f in ("trims_v61polluted.json", "trims_v60before.json", "*_regress.py",
              "build_midsuv_202610.py"):
        check(f"排除清单含:{f}", f'"{f}"' in src)
    # v9.7.54 用户决定：大 JSON 不随包发布，联网重建。断言它们确实被排除。
    big_json = ("trims.json", "master_pool.json", "ice_crash.json", "baseline_car_ev.json",
                "fact_library.json", "universe_car_midsedan_allprice.json", "airbags.json",
                "vehicle_physics.json")
    excl_block = src.split("EXCLUDE_GLOBS")[1].split("]")[0]
    for f in big_json:
        check(f"大JSON已排除:{f}", f'"{f}"' in excl_block)
    # 生成脚本必须在包内，否则数据删了无法重建（尤其 ice_crash.py 曾误排，已加回）
    check("ice_crash.py未被排除(生成脚本)", '"ice_crash.py"' not in excl_block)
    # 3) 被排除的历史数据确实无代码引用（防止误排）
    pycode = ""
    for fn in os.listdir(HERE):
        if fn.endswith(".py") and not fn.startswith("build_release"):
            try: pycode += open(os.path.join(HERE, fn), encoding="utf-8").read() + "\n"
            except Exception: pass
    for f in ("trims_v61polluted.json", "trims_v60before.json"):
        check(f"历史数据无代码引用:{f}", f not in pycode)
    # 4) 反向篡改（真跑）：把模块里的上限改成 1 字节，调用 build_zip 必须抛 ReleaseError
    import build_release as BR
    orig_max = BR.MAX_UNCOMPRESSED_BYTES
    try:
        BR.MAX_UNCOMPRESSED_BYTES = 1   # 注入错误：任何包都超大
        try:
            BR.build_zip([Path(SRC)])   # 任意文件都会超 1 字节
            raised = False
        except BR.ReleaseError:
            raised = True
        check("反向篡改(上限=1)打包被拦截", raised, "ReleaseError" if raised else "未拦截")
    finally:
        BR.MAX_UNCOMPRESSED_BYTES = orig_max
    # build_zip 会清空 DIST，重建真实产物供后续检查/交付
    rb = subprocess.run([sys.executable, "build_release.py"], cwd=HERE,
                        capture_output=True, text=True, timeout=180)
    check("篡改后重建产物成功", rb.returncode == 0 and "SUCCESS" in rb.stdout,
          rb.stdout[-200:] if rb.returncode else "")
    # 5) 实际产物
    if os.path.exists(ZIP):
        with zipfile.ZipFile(ZIP) as z:
            total = sum(i.file_size for i in z.infolist())
            names = set(z.namelist())
        check("实际产物解压后<3MB", total < MAX, f"{total/1024:.1f}KB")
        check("产物无历史污染数据", not any("v61polluted" in n or "v60before" in n for n in names))
        check("产物无回归测试脚本", not any(n.endswith("_regress.py") for n in names))
        check("产物含核心模块", "car-recommendation/build_midsuv_full.py" in names and "car-recommendation/SKILL.md" in names)
        # v9.7.54：大JSON已排除 + 生成脚本在包内
        bigs = ("trims.json", "master_pool.json", "ice_crash.json", "baseline_car_ev.json",
                "fact_library.json", "universe_car_midsedan_allprice.json", "airbags.json", "vehicle_physics.json")
        check("产物无大JSON", not any(any(f in n for f in bigs) for n in names))
        gens = ("auto_trims.py", "build_ice_pool.py", "master_pool.py", "ice_crash.py",
                "auto_levels.py", "facts_sync.py", "universe_build.py", "airbags.py")
        check("产物含全部生成脚本", all(f"car-recommendation/{g}" in names for g in gens))
    else:
        check("实际产物存在", False, "请先运行 python build_release.py")

    print(f"\n结果: {len(P)}/{len(P)+len(F)} 通过")
    if F:
        print("失败:", F)
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(run())
