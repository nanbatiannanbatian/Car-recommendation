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
    "trims.json",        # auto_trims.py（seed 随包人工条目 + 联网补录自动条目）
    "ice_crash.json",    # ice_crash.py --pool <池>（联网碰撞证据）
    "cell_facts.json",   # build_cell_facts.py（联网电芯事实）
    # 2026-11-21：ciasi_decoded.json 原先在 categories/ 与 data/gen/ 各存一份，
    # 内容完全相同 → 同一事实两个可写副本（正是用户要根治的那类病）。
    # 它由 ciasi_text_decode.py 从 C-IASI 官网发布稿解码生成，可重建 → 归生成物，
    # 唯一位置 data/gen/，categories/ 那份删除。
    "ciasi_decoded.json",
}

# 回归测试专用样本：只被 tests/regress 读、发布包运行链路不碰的一次性候选池快照。
# v10.5.1：发布包体积治理——这类文件随源码树/仓库保留供回归用，但不进发布 zip。
# 判据与 GENERATED_JSON 不同：不是「能重建」，而是「只有测试读、产品运行不需要」。
# 误把运行依赖放进来会让新装包功能残缺，故每新增一条都要过 t092 的「零运行引用」检查。
REGRESSION_FIXTURES = {
    "candidates_compact_suv_bev.json",       # 紧凑型纯电SUV候选池快照（指纹/占位的测试样本）
    "candidates_compact_bev_extended.json",  # 紧凑型纯电扩展榜快照
    "candidates_compact_bev_audit.json",     # 紧凑型纯电审计榜快照
    "universe_compact_bev_202610.json",      # 紧凑型纯电 universe 快照（非本品类运行门禁）
}

# 人工采集/核验的事实库：无法用代码重建，必须随包发布（合计约 143KB，在 3MB 限额内）
CURATED_JSON = {
    "baseline_car_ev.json",     # 纯电基线候选池（214 款，一次性数据集）
    "airbags.json",             # 被动安全气囊配置核验表（逐车带 url+quote）
    "vehicle_physics.json",     # 整备质量/风阻/电压/换电参数（汽车之家逐车采集）
    # 2026-11-21 分类修正：这两个原来当"可重建生成物"，实测重建不了——
    # fact_library 需要 car_all 原始库（不在项目内），universe 需要 discover 输出，
    # 且 trims 里的人工条目联网也抓不回来（车系库查无）。按"能不能重建"的判据，它们必须随包。
    "trims_manual.json",        # 人工核验的逐配置条目（auto_trims 生成时会合并）
    "fact_library.json",        # 真实电耗/电池事实库（从 car_all 提炼，源库不在项目内）
    "universe_car_midsedan_allprice.json",  # 中型轿车全价位权威清单（盘点产物，手工维护）
    # 2026-11-21 实测后追加：master_pool 用 --intake 能"跑通"，但产出 291 车 / 0 车有分数
    # （纯骨架）。它靠逐车采集+打分累积而来，不是一条命令能还原的 → 判据上是"数据仓库"而非"缓存"。
    "master_pool.json",         # 总候选池（累积型，随包；新增车型用 --intake 增量摄入）
}

# 每个不打包的生成物都必须有重建规格——否则新装包里它永远缺，功能静默降级。
# cmd: 一条可执行的命令（在项目根目录跑）；needs_net: 是否必须联网；
# semi: 需要前置输入（不能一条命令跑完），见 note。
GENERATED_SPEC: dict[str, dict] = {
    "trims.json": {
        "cmd": "python3 auto_trims.py --seed-only",
        "needs_net": False, "semi": False,
        "note": "先用随包的 categories/trims_manual.json 生成基础库（人工核验条目，开箱可用）；"
                "要补齐自动条目再跑 auto_trims.py --candidates <池> --index <索引> --out data/gen/trims.json",
    },
    "ice_crash.json": {
        "cmd": "python3 ice_crash.py --pool categories/master_pool.json",
        "needs_net": True, "semi": False,
        "note": "需车型集合（读随包 master_pool.json）；重抓 C-IASI 加 --fetch。"
                "原命令漏了 --pool，实测直接报错（2026-11-21 删缓存重建时暴露）",
    },
    "cell_facts.json": {
        "cmd": "python3 build_cell_facts.py",
        "needs_net": True, "semi": False,
        "note": "电芯品牌/容量核验事实表",
    },
    "ciasi_decoded.json": {
        "cmd": "python3 ciasi_text_decode.py --out data/gen/ciasi_decoded.json",
        "needs_net": True, "semi": False,
        "note": "C-IASI 官网结果发布稿的纯文字等级解码（可重建）。"
                "原默认写 categories/ciasi_decoded.json，与 data/gen/ 那份重复，"
                "已统一到 data/gen/ 单一副本。",
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
