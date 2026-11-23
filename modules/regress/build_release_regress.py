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

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)
SRC = str(paths.src("build_release.py"))
def _zip_path() -> str:
    """从 SKILL.md 读版本拼产物名——别在这里硬编码版本号（每次升版都要改一次，必漏）。"""
    import re as _re
    txt = open(os.path.join(HERE, "SKILL.md"), encoding="utf-8").read(3000)
    m = _re.search(r"^version:\s*([0-9.]+)", txt, _re.M)
    return os.path.join(HERE, "dist", f"car-recommendation-v{m.group(1) if m else '0.0.0'}.zip")


ZIP = _zip_path()
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
              "*_regression.py", "build_midsuv_202610.py"):
        check(f"排除清单含:{f}", f'"{f}"' in src)
    # 判据只有「能不能重建」：可重建的生成物不打包，人工事实库必须打包。
    # 名单不再在本文件复制一份（复制必分叉），直接取 paths 的单一事实源。
    excl_block = src.split("EXCLUDE_GLOBS")[1].split("]")[0]
    for f in sorted(paths.GENERATED_JSON):
        check(f"可重建生成物不打包:{f}", f in paths.GENERATED_JSON and "GENERATED_JSON" in src)
    for f in sorted(paths.CURATED_JSON):
        check(f"人工事实库随包:{f}", f not in paths.GENERATED_JSON)
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
        check("产物无回归测试脚本", not any(n.endswith(("_regress.py", "_regression.py")) for n in names))
        check("产物含核心模块", "car-recommendation/build_midsuv_full.py" in names and "car-recommendation/SKILL.md" in names)
        # 可重建的生成物不进包；人工事实库（气囊/物理参数/基线池）必须在包里
        check("产物无生成物缓存", not any(any(f in n for f in paths.GENERATED_JSON) for n in names))
        # v10.5.1：回归专用样本不进发布包（源码树/仓库保留供测试读）
        check("产物无回归专用样本", not any(os.path.basename(n) in paths.REGRESSION_FIXTURES
                                        for n in names))
        check("回归样本仍在源码树", all((Path(HERE) / "categories" / f).exists()
                                   for f in paths.REGRESSION_FIXTURES))
        # v10.5.2：零 JSON 策略——发布包不含任何 .json，数据由 data_bootstrap 从仓库补回。
        check("产物零JSON(v10.5.2)", not any(n.endswith(".json") for n in names),
              f"仍有 {sum(n.endswith('.json') for n in names)} 个JSON")
        check("产物含数据补回模块", any(n.endswith("/data_bootstrap.py") for n in names))
        check("补回模块联网源是仓库", ("Car-recommendation" in
              open(str(Path(HERE) / "modules" / "research" / "data_bootstrap.py"),
                   encoding="utf-8").read()))
        gens = ("auto_trims.py", "build_ice_pool.py", "master_pool.py", "ice_crash.py",
                "auto_levels.py", "facts_sync.py", "universe_build.py", "airbags.py")
        check("产物含全部生成脚本", all(any(n.endswith("/" + g) for n in names) for g in gens))
        check("产物含模块树", any(n.startswith("car-recommendation/modules/") for n in names))
        # 只排除**根目录**的 data/（生成物缓存）；modules/data/ 是代码层，
        # 2026-11-22 新增（事实源读取层），必须随包发布。
        check("产物不含 data/ 生成物缓存",
              not any(n.startswith("car-recommendation/data/") for n in names))
        # v10.31.0：平台硬规则——zip 内路径最多 3 段（根/二级/文件）
        check("产物目录层级≤3(v10.31)", all(len(n.split("/")) <= 3 for n in names),
              f"超限 {sum(len(n.split('/'))>3 for n in names)} 条")
        check("产物modules已扁平(无子包)", not any(
            n.startswith("car-recommendation/modules/") and len(n.split("/")) > 3
            for n in names))
        check("产物含扁平核心模块", "car-recommendation/modules/leaderboard.py" in names
              and "car-recommendation/modules/pipeline.py" in names)
        check("产物无子包__init__", not any(
            n.startswith("car-recommendation/modules/") and n.endswith("/__init__.py")
            and len(n.split("/")) > 3 for n in names))
    else:
        check("实际产物存在", False, "请先运行 python build_release.py")

    print(f"\n结果: {len(P)}/{len(P)+len(F)} 通过")
    if F:
        print("失败:", F)
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(run())
