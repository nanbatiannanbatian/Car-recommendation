# -*- coding: utf-8 -*-
"""paths.py —— 全项目路径唯一事实源（v10.0 模块化）

为什么要有这个文件（2026-11-21 重构根因）：
  重构前 46 个文件各自 `HERE = dirname(abspath(__file__))` 再拼 "categories"，
  文件一进 modules/<pkg>/ 就全部指向错误目录（modules/engine/categories）。
  路径散落 = 每次挪文件都要全库审计，所以收口到这一个模块。

目录契约（改任何路径前先读这里）：
  ROOT        项目根，SKILL.md 所在；也是 sys.path 里唯一的项目入口
  modules/    代码，按职责分包：research / physics / envscore / engine / board / regress
  categories/ 人工维护的输入（口径配置、经验库、保值率…），随包发布
  data/gen/   机器生成的大 JSON（trims/master_pool/ice_crash…），可重建，打包排除
  runs/       跑批产物，打包排除
  dist/       发行 zip，打包排除

生成物读写规则：
  - 判据只有「能不能重建」：GENERATED_JSON 可重建 → 不打包；CURATED_JSON 人工核验 → 必须随包。
  - 生成物一律落在 data/gen/（gen()），用 bootstrap_data.py 重建（规格见 GENERATED_SPEC）。
  - 读生成物用 cat()：生成物名自动指向 data/gen/，人工配置指向 categories/。
    data/gen/ 里缺文件时（新装包）回退 categories/，再缺就由调用方报错或重建。
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent
MODULES = ROOT / "modules"
CAT_DIR = ROOT / "categories"
DATA_DIR = ROOT / "data"
GEN_DIR = DATA_DIR / "gen"
RUNS_DIR = ROOT / "runs"
DIST_DIR = ROOT / "dist"

# 判据只有一条：**能不能用代码重新生成**。不是体积。
#   能重建 → 不打包，让用户随代码逐步生成（版本一变本来就该重算）；
#   不能重建（人工采集/核验的事实库）→ 必须随包，否则新装包功能残缺。
GENERATED_JSON = {
    "trims.json",                            # auto_trims.py（联网采集逐配置阶梯）
    "master_pool.json",                      # master_pool.py --intake（联网销量建池）
    "ice_crash.json",                        # ice_crash.py（联网碰撞证据）
    "cell_facts.json",                       # build_cell_facts.py（联网电芯事实）
    "universe_car_midsedan_allprice.json",   # universe_build.py（半自动：需先 discover）
    "fact_library.json",                     # facts_sync.py --apply（半自动：需原始库）
}

# 人工采集/核验的事实库：无法用代码重建，必须随包发布（合计约 143KB，在 3MB 限额内）
CURATED_JSON = {
    "baseline_car_ev.json",     # 纯电基线候选池（214 款，一次性数据集）
    "airbags.json",             # 被动安全气囊配置核验表（逐车带 url+quote）
    "vehicle_physics.json",     # 整备质量/风阻/电压/换电参数（汽车之家逐车采集）
}

# 每个不打包的生成物都必须有重建规格——否则新装包里它永远缺，功能静默降级。
# cmd: 一条可执行的命令（在项目根目录跑）；needs_net: 是否必须联网；
# semi: 需要前置输入（不能一条命令跑完），见 note。
GENERATED_SPEC: dict[str, dict] = {
    "trims.json": {
        "cmd": "python3 auto_trims.py --apply",
        "needs_net": True, "semi": False,
        "note": "从汽车之家逐配置页补录阶梯；耗时较长，可只补指定车型",
    },
    "master_pool.json": {
        "cmd": "python3 master_pool.py --intake --month <YYYYMM> --apply",
        "needs_net": True, "semi": False,
        "note": "按销量榜建总池；月份换成当期，数据随版本重算",
    },
    "ice_crash.json": {
        "cmd": "python3 ice_crash.py",
        "needs_net": True, "semi": False,
        "note": "C-IASI/C-NCAP 官方碰撞记录",
    },
    "cell_facts.json": {
        "cmd": "python3 build_cell_facts.py",
        "needs_net": True, "semi": False,
        "note": "电芯品牌/容量核验事实表",
    },
    "universe_car_midsedan_allprice.json": {
        "cmd": "python3 universe_build.py --discover <discover输出.json> --out <目标.json>",
        "needs_net": True, "semi": True,
        "note": "前置：先跑 discover.py 拿联网销量榜 JSON",
    },
    "fact_library.json": {
        "cmd": "python3 facts_sync.py --apply",
        "needs_net": False, "semi": True,
        "note": "前置：需 car_all 原始库（环境变量 CAR_ALL_DATA 指向）",
    },
}


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def cat(name: str) -> Path:
    """数据文件路径。生成物 → data/gen/，人工配置 → categories/。

    生成物在 data/gen/ 缺失但 categories/ 还留着旧副本时回退旧副本
    （只在迁移期发生；打包后两者都不在，由调用方决定重建或报错）。
    """
    if name in GENERATED_JSON:
        p = GEN_DIR / name
        if not p.exists() and (CAT_DIR / name).exists():
            return CAT_DIR / name
        return p
    return CAT_DIR / name


def spec(name: str) -> dict:
    """生成物的重建规格；不在名单里返回空 dict（人工事实库无需重建）。"""
    return GENERATED_SPEC.get(name, {})


def gen(name: str) -> Path:
    """生成物写入路径（自动建目录）。"""
    return ensure_dir(GEN_DIR) / name


def runs(*parts: str) -> Path:
    return RUNS_DIR.joinpath(*parts)


def root(*parts: str) -> Path:
    return ROOT.joinpath(*parts)


def research_config() -> Path:
    """取数模块的站点画像配置（随 research 包发布）。"""
    return MODULES / "research" / "config.json"


def module(dotted: str):
    """按名字导入模块：优先 modules.<pkg>.<name>，找不到再按原样导入。

    回归/工具里要 patch 某个实现模块时用这个，不要 __import__("board_view")——
    搬迁后裸名在根目录只剩薄壳。
    """
    import importlib

    if "." in dotted:
        return importlib.import_module(dotted)
    for pkg in ("contracts", "research", "physics", "envscore", "engine", "board", "regress"):
        full = f"modules.{pkg}.{dotted}"
        try:
            return importlib.import_module(full)
        except ModuleNotFoundError as e:
            if getattr(e, "name", "") != full:
                raise  # 模块存在但内部依赖缺失，照实抛
    return importlib.import_module(dotted)


def src(name: str) -> Path:
    """实现源码路径解析（回归测试专用）。

    回归断言必须读「真实现」而不是根目录兼容薄壳，否则断言形同虚设。
      src("leaderboard.py")        -> modules/engine/leaderboard.py
      src("research/researcher.py")-> modules/research/researcher.py
      src("runs/x/榜单.html")      -> <ROOT>/runs/x/榜单.html
    """
    p = Path(name)
    if p.suffix == ".py":
        for f in MODULES.glob(f"*/{p.stem}.py"):
            return f
    return ROOT / name


def iter_categories_json() -> Iterable[str]:
    """遍历所有品类配置 id（categories/ ∪ data/gen/ 的 json 文件名去后缀）。"""
    seen: list[str] = []
    for d in (CAT_DIR, GEN_DIR):
        if not d.is_dir():
            continue
        for f in sorted(os.listdir(d)):
            if f.endswith(".json") and f[:-5] not in seen:
                seen.append(f[:-5])
    return seen


def migrate_generated(move: bool = True) -> list[tuple[str, str]]:
    """把 categories/ 里的生成物搬到 data/gen/。返回 [(源, 目标)]。

    幂等：目标已存在则跳过。build_release / 测试可调用。
    """
    ensure_dir(GEN_DIR)
    done: list[tuple[str, str]] = []
    for name in sorted(GENERATED_JSON):
        src, dst = CAT_DIR / name, GEN_DIR / name
        if not src.exists() or dst.exists():
            continue
        if move:
            shutil.move(str(src), str(dst))
        done.append((str(src), str(dst)))
    return done


def load_json(path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    print(f"ROOT      {ROOT}")
    print(f"MODULES   {MODULES}")
    print(f"CAT_DIR   {CAT_DIR}")
    print(f"GEN_DIR   {GEN_DIR}")
    print(f"RUNS_DIR  {RUNS_DIR}")
    print(f"生成物 {len(GENERATED_JSON)} 个：{', '.join(sorted(GENERATED_JSON))}")
