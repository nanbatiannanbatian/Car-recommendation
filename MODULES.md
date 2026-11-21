# 模块结构与契约（v10.0）

> 这份文档回答三个问题：**代码在哪、边界在哪、能不能单独拿走用**。
> 改代码前先读这里 + 目标包的 `__init__.py`，不要凭记忆找文件。

## 1. 为什么重构

重构前：81 个 `.py` 全平铺在根目录、2.9 万行，`leaderboard.py`（1948 行）被 11 个模块反向依赖，
`os.path.join(HERE, "categories", ...)` 散落 46 处。表现是：

- 想单独发布「续航估算」或「取数检索」——拿不走，一动就带走半个项目；
- 换目录/打包就集体指错路径；
- 改打分公式和改页面渲染在同一个文件里，review 分不清影响面。

重构后：按「能否独立发布」切六个包，依赖单向，路径收口到一个 `paths.py`。

## 2. 目录与职责

```
paths.py                     路径唯一事实源：ROOT / cat() / gen() / src() / module()
modules/
  contracts/                 数据契约层（零依赖零 IO）
    trims_schema.py          逐配置阶梯 trims.json 的字段/枚举/校验
    candidate_schema.py      候选池单条形状：price 文本/trim 字符串归一化 + 形状体检
    model_names.py           车名归一化 canon
  research/                  取数 · 检索 · 证据采集（唯一网络出口）
    researcher.py            站点画像检索（17 站点）+ 广告识别 + 博主可信度
    fetch_dcd_sales.py       懂车帝逐月销量（纯电/插混/增程拆分）
    fetch_cncap.py           C-NCAP 官方成绩
    fetch_complaints.py      车质网投诉
    fetch_bili.py            B 站视频证据初筛
    fetch_sales.py           乘联会大盘快照
    fetch_specs.py           汽车之家车系字段（走 OpenCLI）
    filings.py               工信部备案（未上市车型）
    ice_crash.py / ice_sales.py   非纯电碰撞证据 / 车型销量
    lifecycle_fetch.py       在售状态拉取（懂车帝 / VDH）
    lifecycle_detect.py      销量变点检测（停产/清库/爬坡）
    build_ice_pool.py        汽车之家车系索引建池
    auto_trims.py            逐配置阶梯自动补录（含索引 miss 的站点检索兜底）
    trim_ingest.py           外部配置表规范化导入
    discover.py              漏车发现
    universe_build.py        权威清单盘点
    build_cell_facts.py      电芯事实表
    sync_cncap.py / sync_cell_brand.py   成绩/电芯同步进池
    align_specs.py / auto_levels.py      级别与指导价对齐
    ingest.py / koubei_merge.py / notes_tool.py / swoiow_probe.py / ciasi_text_decode.py
  physics/
    ev_range_model.py        续航物理模型（常量单一事实源，零依赖）
  envscore/
    engine.js                环境分/续航引擎唯一事实源（hwyMult/rangeCalc/adjust/compositeTier）
    build_energy_compare.py  页面生成器（把 engine.js 内联成自包含 HTML）
  engine/
    leaderboard.py           8 维加权分 + 三档 + 上表
    ranking.py               口径协议与硬闸门
    energy_router.py         A/B 榜型自动判定
    trim_board.py / trim_annotate.py / trim_coverage.py   逐配置评分/人工口径/覆盖门禁
    robust.py / verify_mcda.py                            MCDA 交叉验证
    thirdparty.py / airbags.py / spec_facts.py / trims.py 事实注入（不进分）
    cross_verify.py / evidence_consistency.py / sibling_check.py / safety_check.py
                            一致性门禁
    score_fingerprint.py     打分逻辑指纹
    model_names.py           车名归一化（修 engine→board 依赖倒挂）
    ice_scoring.py / quality_score.py / experience_score.py / crash_systems.py
    admit.py / facts_sync.py / schema_audit.py / lifecycle_confirm.py
  board/
    pipeline.py              单次出榜全链路
    monthly.py               月度跑批
    board_v2.py              统一榜单引擎 v2
    board_view.py            页面区块注册表（展示层唯一事实源）
    render_board_html.py     页面渲染（trim_selector 统一处理配置下拉/待补标注）
    render_board_html.py     HTML 渲染
    master_pool.py           总候选池（单一事实源）
    bootstrap.py             生成物一键重建（新装包首跑）
    warehouse.py / forecast.py / backfill.py   仓库/预测/回填
    ice_board.py / ice_funnel.py / ice_sensitivity.py
    ev_battery_board.py / ev_cell_board.py
    build_midsuv_full.py / build_midsuv_202610.py / build_midsedan_202609.py
    filings_board.py / cross_energy_sections.py
  regress/
    bug_regression.py        67 项历史 bug 断言 + 63 条反向注入
    negative_regression.py   失败回测框架
    layout_regress.py        结构回归（本文件所述的边界）
    energy_compare_regress.py / energy_router_regress.py / midsuv_full_regress.py
    build_release_regress.py 打包回归
    *.js                     node 跑生成页面的 DOM 实跑
data/gen/                    生成物（可重建，打包排除）
categories/                  人工配置与事实库（随包）
runs/ dist/ tools/           产物 / 发行 zip / 一次性迁移脚本（均不打包）
```

## 3. 依赖方向（硬约束）

```
contracts（零依赖、零 IO）
   ↑
research ─┬─ physics
          └─ envscore ─→ engine ─→ board
regress ────────────────────────→ 任意（测试可读全部）
```

- `contracts` 在最底层：不放业务逻辑，只放「谁写谁读都要认同一套」的规则，且不得 import 任何本项目包、不得 IO。
- `research` / `physics` 只允许依赖 `contracts`。
- `engine` 不得 import `board`；`research` 不得 import `engine`。
- 两处历史倒挂已消解：
  - `engine/cross_verify → board/master_pool.canon`：下沉到 `contracts/model_names.py`；
  - `research/auto_trims → engine/trims.validate`：下沉到 `contracts/trims_schema.py`（采集方不该依赖打分引擎）。
- `layout_regress.EXCEPTIONS` 现为空。新增例外必须登记并写原因，否则结构回归报违规。

## 4. 数据落盘

**判据只有一条：能不能用代码重新生成。不是体积。**

| 类别 | 位置 | 打包 | 举例 |
|---|---|---|---|
| 可重建的生成物 | `data/gen/` | 排除 | `trims.json`、`ice_crash.json`、`cell_facts.json` |
| 人工事实库（不可重建） | `categories/` | **随包** | `master_pool.json`、`trims_manual.json`、`fact_library.json`、`airbags.json`、`vehicle_physics.json`、`baseline_car_ev.json` |
| 人工口径配置 | `categories/` | 随包 | `rank_v2_policy.json`、`experience_facts.json` |
| 跑批产物 | `runs/` | 排除 | 榜单 HTML、summary.json |
| 发行 zip | `dist/` | 排除 | `car-recommendation-vX.Y.Z.zip` |

> 2026-11-21 用「删光 data/gen 再重建」实测校准过这条线：`master_pool` 虽然能跑 `--intake`，
> 但产出 291 车 / 0 车有分数（空骨架）——它靠逐车采集+打分累积，不是缓存，故改判随包。
> `ice_crash` 的重建命令原来漏了 `--pool`（实测直接报错），已修正。

为什么这么切：生成物带口径与版本指纹，版本一升、口径一变本来就该重算；
把它们打进包，等于把**过期数据**发给用户，还会让新装包看上去"有数据"却算不对。
反过来，气囊/物理参数/基线池是逐车核验出来的事实库，没有代码能重造，不随包就是功能残缺。

名单单一事实源：`paths.GENERATED_JSON` / `paths.CURATED_JSON`，
重建规格 `paths.GENERATED_SPEC`（`build_release` 与 `layout_regress` 都直接复用，不各自维护）。

读写：`paths.cat("trims.json")` 读（生成物自动指向 `data/gen/`，缺文件回退 `categories/`）；
`paths.gen("trims.json")` 写。**禁止手拼 `categories` 路径**（`layout_regress` 会拦）。

## 5. 生成物重建（新装包首跑）

```bash
python3 bootstrap_data.py --check                  # 体检：缺什么 / 怎么建 / 是否联网 / 指纹是否过期
python3 bootstrap_data.py --apply                  # 只建缺失的全自动项
python3 bootstrap_data.py --apply --include-semi    # 连需前置输入的也试
python3 bootstrap_data.py --apply --all --force     # 版本升级后全部重算
python3 bootstrap_data.py --apply --only trims.json --force
```

| 生成物 | 重建命令 | 联网 | 前置 |
|---|---|---|---|
| `cell_facts.json` | `build_cell_facts.py` | 是 | — |
| `ice_crash.json` | `ice_crash.py` | 是 | — |
| `trims.json` | `auto_trims.py --apply` | 是 | — |
| `master_pool.json` | `master_pool.py --intake --month <YYYYMM> --apply` | 是 | — |
| `universe_car_midsedan_allprice.json` | `universe_build.py --discover <json> --out <json>` | 是 | 先跑 `discover.py` |
| `fact_library.json` | `facts_sync.py --apply` | 否 | 需 car_all 原始库 |

**数据回流**：`facts_sync.backfill_price_guide_from_trims()`（价格）与
`facts_sync.backfill_crash_from_text()`（碰撞成绩）是唯一实现，pipeline/board_v2 出榜前自动调用；
成绩→分数由 `safety_check.apply_auto_scores()` 按 `official_safety_scores.json` 的 `map_spec` 复算，
表内条目由 `recompute_table()` 统一复算、`sync_table_scores_to_pool()` 对齐候选池，
`master_pool --sync-prices` 只是手动入口。新增「某事实库已有、候选池缺」的字段时，照这个模式做：
自动回流 + 独立复核门禁 + 回归（t069 是样板），不要只加一个手动命令。

**约束**：新增任何不打包的生成物，必须同时在 `paths.GENERATED_SPEC` 写重建命令，
否则 `layout_regress` 报「每个生成物都有重建规格」失败——防止产出「永远缺、也没人知道怎么建」的文件。

## 6. 根目录薄壳

根目录保留的 `.py` 只有两类：基础设施（`paths.py` / `build_release.py` / `sync_to_repo.py`）
与**薄壳**（≤25 行，`runpy.run_module` 转发）。薄壳存在只为两件事：

1. 文档里的 `python3 monthly.py` 这类命令不用改；
2. 外部脚本/回归按文件名调用时不断。

薄壳里**不许写业务逻辑**——逻辑在 `modules/`，否则又变成两处真相。

## 7. 回归怎么跑

回归脚本是**开发者自用、不随包发布**（`build_release.py` 的 `EXCLUDE_GLOBS` 排除 `*_regress.py`）。
在源码目录（含 `modules/` 全树）执行：

| 脚本 | 守什么 | 项数 |
|---|---|---|
| `layout_regress.py` | 结构：薄壳 / 依赖方向 / 契约层纯净 / 生成物可重建 / 路径硬编码 / 文档 CLI | 15 |
| `bug_regression.py` | 口径：历史 bug 断言 + 反向注入回测 | 67（含 63 条注入） |
| `energy_router_regress.py` | A/B 榜型自动判定 | 32 |
| `energy_compare_regress.py` | 比较器与环境分引擎 | 20 |
| `midsuv_full_regress.py` | 跨能源正式榜（含 node DOM 实跑） | 46 |
| `build_release_regress.py` | 打包：体积 / 排除清单 / 模块树 | 29 |

改完代码**必须全跑**。断言读 `paths.src("xxx.py")` 的真实现，不读根目录薄壳——
否则断言跑在 10 行转发壳上，形同虚设（迁移时踩过）。
