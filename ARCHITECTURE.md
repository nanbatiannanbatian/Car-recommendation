# 车辆榜单引擎 · 架构与运维手册

> 前身 **omni-debate**（多模型辩论 skill）。2026-11 辩论引擎整体移除，只保留**可复现的榜单与预测链路**。
>
> 本文面向两种读者：
> - **人**：想快速知道这套东西在干什么、数据从哪来、怎么改、哪里有坑。
> - **Agent / 后来的维护者**：想在不读完全部源码的前提下定位「改哪个文件、改哪一步、会不会破坏口径」。
>
> 版本锚点：`~/.workbuddy/skills/omni-debate`（**v10.0.0，规范副本**）。v10.0 完成模块化重构（81 个平铺脚本 → `modules/` 六包），口径与出榜结果不变；结构契约见 `MODULES.md`。

---

## 0. 三十秒速览

一句话：把「取公开数据 → 过口径闸门 → 按固定公式打分 → 10 种算法交叉 + 三档分级 → 出 HTML/Markdown 榜」做成**纯函数、无 LLM、可复现**的购车榜单引擎；品类差异全部塞进 `categories/*.json`，换品类只换配置、不改代码。

三条入口命令：

```bash
python3 monthly.py --dry-run                                  # 月度全链路（探测月份→建池→取数→出榜→留档）
python3 pipeline.py --category car_ev --candidates cands.json --out runs/x   # 单次出榜（纯电）
python3 ice_board.py --pool ice_pool_full.json --category car_ice_phev --out runs/car-ice-4boards   # 燃油混动四榜
```

---

## 1. 核心原则（改代码前必须遵守的六条红线）

1. **无 LLM / 无辩论**：同一输入必得同一输出，名次可复现、可定位。历史上用双辩手+裁判出榜，会出现越界、无法裁决、不可复现三类错误，已废弃。
2. **口径即代码**：级别、动力、价格、证据等级都用 `ranking.apply_gates()` 拦，不写在提示词里。
3. **证据三态**：有 / 确认无 / **未核验（❓ ≠ ❌）**。不把「没查」显示成「没有」。
4. **事实不进分**：气囊、配置阶梯、官网事实库、第三方证据 → `SCORING_ENABLED=False` + apply 前后对 `scores/total/adjust` 做深比较断言。事实库在**代码层面不可能改排名**。
5. **不硬凑十款**：过闸不足就少列，并注明「尾部名次无统计意义」。
6. **披露优先**：权重争议、插补率、覆盖缺口、价格口径、L1≠安全，全部写进报告，不做静默处理。

---

## 2. 目录结构（v10.0 模块化，⚠️ 先看这里）

> 结构契约与改代码规则以 **`MODULES.md`** 为准；本节只列「东西在哪」。

### 2.1 源码树

```
paths.py                  路径唯一事实源（ROOT / cat() / gen() / src()）
modules/
  research/               取数与证据采集（唯一网络出口，25 个采集器 + researcher 检索）
  physics/                续航物理模型（零依赖，物理常量单一事实源）
  envscore/               环境分引擎：engine.js（唯一事实源）+ 页面生成器
  engine/                 打分口径 / 闸门 / 事实注入（纯计算）
  board/                  出榜编排与渲染（pipeline / monthly / board_v2 / master_pool）
  regress/                回归测试（结构 / 口径 / 专项 / 打包 / node DOM）
categories/               人工口径配置与事实库（随包发布）
data/gen/                 机器生成的大 JSON（可重建，打包排除）
runs/ dist/ tools/        产物 / 发行 zip / 一次性迁移脚本（均不打包与不入库）
```

根目录只留薄壳（`python3 monthly.py` 照旧可用，内部 `runpy` 转发到 `modules/`）。

依赖方向单向：`research`、`physics` → `envscore` → `engine` → `board`；`regress` 可读全部。
`python3 layout_regress.py` 拦截结构违规。

（历史的 `runs/omni-debate-skill/omni-debate/` 开发副本与 `build_html.py` 均已在 v7–v9 期间废弃，本表不再维护。）

### 2.2 产物位置

| 目录 | 内容 |
|---|---|
| `runs/car-ev-monthly/<YYYYMM>/` | 月度出榜产物（board HTML、report.md、summary.json、trims.md） |
| `runs/midsuv_*_完整交互榜.html` | 跨能源正式榜（14 区块 + 内嵌推演器） |
| `runs/v10-smoke/` | 重构后冒烟出榜产物（可删） |
| `dist/` | `car-recommendation-vX.Y.Z.zip`（官方 3MB 上限） |

---

## 3. 全链路总览

### 3.1 文字流程

```
公开数据源
  ├─ 懂车帝：逐月销量（车系级，纯电/插混/增程拆分）、车系 sale_status
  ├─ 汽车之家：车系库索引、官方二级分类(levelName)、厂商指导价、口碑评价数、参数页 var config
  ├─ C-NCAP 官网：测评综合得分率
  ├─ C-IASI：官方碰撞测评记录（等级图标未解码）
  ├─ 工信部：购置税减免目录（未上市备案）、产品公告（生命周期次信号）
  ├─ 乘联会 CPCA：月度大盘（聚合，只做校验）
  ├─ 车质网 12365auto：投诉（L2 证据）
  └─ B站：视频证据初筛（不取弹幕）
        │
        ▼
[建池 build_*_pool / discover] → 候选池 JSON（含 cls/power/price/koubei/trend/lifecycle）
        │
        ▼
[闸门 ranking.apply_gates / admit] → 级别·动力·价格·证据 硬拦
        │
        ▼
[事实注入] thirdparty → airbags → spec_facts → trims（全部 SCORING_ENABLED=False，不改分）
        │
        ▼
[打分 leaderboard.score_rows / ice_scoring.score_pool] → 加权分 + 证据等级
        │
        ▼
[稳健 robust：10 种 MCDA × N 套权重 → Kendall τ；bootstrap 名次 CI]
        │
        ▼
[三档 tier_report] → T1 可推荐 / T2 取舍 / T3 证据不足（T2 再拆 2A/2B/2C 仅展示层）
        │
        ▼
[渲染] top_table 档组字母 + 决策卡 + 趋势箭头 + 三态列 → HTML / Markdown / summary.json
```

### 3.2 数据源与网站一览

| 网站 / 接口 | 用在哪 | 取什么 | 谁抓 | 边界 / 坑 |
|---|---|---|---|---|
| 懂车帝 逐月销量 API | 销量（车系级） | 纯电/插混/增程拆分月销 | `fetch_dcd_sales.py`、`ice_sales.py` | **2023-10 起**真实可用；2023-09 前静默回退最新月（数据看着完整其实是复制） |
| 懂车帝 车系页 `sale_status` | 生命周期 | 在售/停售/预售 | `lifecycle_fetch.py` dcd 层 / VDH | **有 dcd_series_id 主键，最干净**，首选用它 |
| 汽车之家 `grade/carhtml` | 车系索引 | 车系名→series_id + 指导价区间 | `build_ice_pool.py` | 按品牌字母静态页 |
| 汽车之家 `levelName` | 官方级别 | 官方二级分类 | `align_specs/auto_levels/fetch_specs` | 只认官方，不推轴距；取不到一律不入池 |
| 汽车之家 口碑 `rowcount` | 口碑样本量 | 评价条数 | `vdh_koubei_pull.py`（urllib） | 评价数 ≠ 评分，只作样本量 |
| 汽车之家 参数页 `var config` | 配置阶梯 | 逐配置价格/电池/CLTC | `auto_trims.py` | SSR 里的 JSON |
| C-NCAP 官网 | 安全成绩 | 综合得分率 | `fetch_cncap.py` | 首页 swiper 是**服务端渲染**，可直解 |
| C-IASI 官网测评稿 | 权威碰撞 + **可维修性事实** | G/G+/A/M/P 文字等级（5 分指数） | `ciasi_text_decode.py`（新增） | **等级图标没文字对照 ≠ 没文字稿**：官网 `/news/show-N.html` 结果发布稿是纯文字，2026-11 已解码 8 篇/30 款；早期稿只给分布不给逐车对应时标 `aggregate_only`，不猜 |
| 工信部 购置税目录 | 备案预测 | 未上市纯电车型 | `filings.py` | 逐型可解析；命名与市场名不一致 |
| 工信部 产品公告 | 生命周期次信号 | 在产/停产 | VDH `miit_gonggao/` | **无车型名主键**，只能半自动；`change_notice` 依赖 bs4/lxml |
| 乘联会 CPCA | 大盘校验 | 厂商/级别聚合 | `fetch_sales.py` | 无车型级接口 |
| 车质网 12365auto | 投诉（L2） | 投诉条目 | `fetch_complaints.py` | 按投诉编号去重累加；密度=投诉/万台 |
| B站 | 视频证据初筛 | 搜索/CC 字幕 | `fetch_bili.py` | 不取弹幕/评论；AI 字幕需登录态，不做 |
| **swoiow/autohome**（GitHub, Apache-2.0） | 参数/价格**交叉核验补全** | 指导价/kWh/CLTC/风阻/轴距/质保政策 | `swoiow_probe.py`（新增） | 月度更新、1666 款 CSV；**只出补全建议不写回、不进分**；车型名必须精确匹配（子串会把 EZ-60 误配成 EZ-6） |
| 中汽流通协会 × 精真估 保值率月报 | 保值率事实（第三方） | 3 年保值率（品牌/级别/车型） | 人工摘取 / 待自动化 | 有公开网页报道（cada.cn / 中国汽车报），**无车型级 API**；EV 3 年保值率 44.8–45.7%，仅作事实列不进分 |
| 市场监管总局 召回中心 samrdprc.org.cn | 质量/安全事实（L1 官方） | 召回编号/数量/缺陷描述/车型 VIN 范围 | 待接入（已验证可抓） | 公告为静态 HTML、`召回编号+车型+数量`结构化；**召回≠质量差**（主动召回常态化），只作事实/风险提示 |

### 3.3 数据源扩展方案（2026-11-17 核实并落地）

> 原则：**不为了"多"而加源**。每个新源必须通过三关：①能直接抓（已实测）②补的是真缺口（非重复）③不破坏打分纪律（事实列不进分）。

**已核实可用（本次落地 2 个）**

| 源 | 补什么缺口 | 落地状态 | 证据 / 边界 |
|---|---|---|---|
| C-IASI 官网文字稿 | 手册标注「等级图标未解码」+ 可维修性无官方口径 | ✅ **已落地** `ciasi_text_decode.py` | 实测 8 篇结果稿 / 30 款唯一车型；含逐车 `特斯拉Model Y 辅助=G` vs `享界S9T 辅助=G+`；「耐撞性与维修经济性」G/A/M/P 正是官方可维修性等级 |
| swoiow/autohome | 价格未知落榜、3 款缺 kWh、风阻/CLTC 缺 | ✅ **已落地** `swoiow_probe.py` | 实测 1632 车型 CSV、EV 候选严格命中 16/39；字段含 `厂商指导价(元)`/`电池能量(kWh)`/`CLTC`/`风阻系数(Cd)`；只出建议不写回 |

**已核实可用，但暂不自动化（留接口）**

| 源 | 价值 | 为何暂缓 |
|---|---|---|
| 中汽流通协会保值率月报 | 直接回应"销量太低影响保值"——EV 3 年 44.8% 已公开 | 只有网页/PDF 报道、无稳定车型级 API；每月人工摘 3–5 个关注车型即可 |
| samrdprc.org.cn 召回公告 | L1 官方质量事实（比车质网 L2 投诉更硬） | 召回是"负责任的主动行为"，不能简单当扣分；需先定"只披露不评分"的呈现规则 |
| dongchedi-cli / OpenCLI（已部分在用） | 销量/口碑/参数交叉验证 | 已足够，重复接入无新信息 |

**明确不接（避免踩坑）**

| 源 | 原因 |
|---|---|
| Apify `sinodata/china-car-sales` | 付费 + 需 Apify token，与现有懂车帝直抓重复 |
| 各类 CSDN "完整工程包" | 一次性 xlsx、无更新机制、字段不可溯源 |
| 百度百科 / 自媒体转述 | 二手来源，项目红线要求一手源 |

**落地命令（新源）**

```bash
# 1) C-IASI 文字解码（零依赖，直接跑）
python3 ciasi_text_decode.py --start 100 --end 130 --out runs/car-ev-4boards/ciasi_decoded.json
python3 ciasi_text_decode.py --selftest

# 2) swoiow 参数补全（需先准备本地数据，一次性）
git clone --depth 1 --filter=blob:none --sparse https://github.com/swoiow/autohome.git
cd autohome && git sparse-checkout set data && cd ..
python3 swoiow_probe.py --data-dir autohome/data \
    --candidates runs/car-ev-4boards/candidates_trend.json \
    --out runs/car-ev-4boards/swoiow_suggest.json
python3 swoiow_probe.py --selftest
```

**两个解码器的共同纪律**：只作事实列/交叉核验，**不改 `SCORING_ENABLED=False` 红线、不进加权分**；匹配不确定时标 `aggregate_only` / 未匹配，**不猜、不填充**。

---

## 4. 每个 Python 文件的作用

> 分组只是便于定位；实际调用关系见 §5。所有脚本均为**纯标准库优先**，无 numpy/pyDecision 依赖（`robust.py` 手写 10 种 MCDA）。

### A. 取数 / 抓取层

| 文件 | 作用 | 关键点 |
|---|---|---|
| `fetch_dcd_sales.py` | 懂车帝逐月销量，按纯电/插混/增程拆分 | 纯电榜专用；`norm()` 会合并 DM/EV key |
| `ice_sales.py` | 非纯电（燃油/HEV/PHEV/EREV）车型级销量 | **不剥动力后缀**，避免「宋Pro DM/EV」混 key（与纯电榜相反） |
| `fetch_sales.py` | 乘联会 CPCA 月度大盘快照 | 只有聚合，无车型级；用于校验宏观同比 |
| `fetch_specs.py` | 通过 OpenCLI autohome 适配器取车系一手字段 | dongchedi 无 `__NEXT_DATA__`，改用 autohome |
| `align_specs.py` | 把候选 cls/price 对齐到汽车之家官方字段 | 官方二级分类 + 厂商指导价两道闸门的取数 |
| `auto_levels.py` | 自动补齐 `levels.json`（autohome `level`） | 品牌词表随用随补，冲突不覆盖 |
| `auto_trims.py` | 自动补录逐配置阶梯（参数页 `var config`） | 只保留纯电款型，排除同车系 DM/增程/燃油 |
| `fetch_complaints.py` | 车质网投诉（L2 证据） | 按投诉编号去重追加 JSONL；密度需分母 |
| `fetch_cncap.py` | C-NCAP 官网最新测评 | 推翻「无法批量抓」的旧结论；17/17 与人工表一致 |
| `fetch_bili.py` | B站视频证据初筛 | 机器初筛 + 人工确认队列；不碰弹幕 |
| `filings.py` | 工信部购置税减免目录备案 | 提前 4–10 周看到要上市的车 |
| `build_ice_pool.py` | 构建燃油/混动候选池 | 汽车之家车系库 → 车系页级别/指导价/口碑 → 销量 |
| `discover.py` | 候选发现（防漏车） | 新上市/被漏车系自动捞出（如极狐阿尔法T7） |

### B. 口径闸门层

| 文件 | 作用 | 关键点 |
|---|---|---|
| `ranking.py` | **榜单口径协议 v4.1**：`apply_gates`（级别/动力/价格）、`trend_sales` 趋势、`evidence_grade` 三态、`audit` | **最核心红线**；拦六类真实翻车 |
| `admit.py` | 入池判定：一台车该不该进榜 | 规则化 + 理由码，任一不过即拒 |

### C. 打分 / 事实层

| 文件 | 作用 | 关键点 |
|---|---|---|
| `leaderboard.py` | **品类无关排行榜引擎**：`score_rows/top_table/tier_report/review_report/robust_report/weight_audit/render` | 类别配置驱动；`top_table` 给名次+同档组；`tier_report` 判三档 |
| `spec_score.py` | 配置驱动打分（拍分→按规则算分） | 把每维拆成可核对加减项 |
| `ice_scoring.py` | **燃油混动四榜代理评分**：公开数据映射 6 维 | 每分带 `basis`+`confidence`；`lifecycle` 在这里**硬编码 active**（见 §7 问题） |
| `sync_cncap.py` | C-NCAP 成绩映射成分数写回候选（safety 维） | `--fill-mode blend`（α=0.5） |
| `thirdparty.py` | 第三方实测/拆车证据 → adjust | 封顶：厂商口径合计 ±0.6，单车合计 ±1.5；`SCORING_ENABLED=False` |
| `airbags.py` | 气囊三态事实列 | 不进 safety 主分；仅无官方成绩车受限修正 |
| `spec_facts.py` | 官网配置事实库（16 项细分维度） | `SCORING_ENABLED=False`，覆盖率不足 G2(70%) 整条禁用 |
| `trims.py` | 具体配置阶梯 + 带内推荐 | 列事实、给分支建议；`SCORING_ENABLED=False` |

### D. 稳健 / 三档 / 渲染层

| 文件 | 作用 | 关键点 |
|---|---|---|
| `robust.py` | 10 种 MCDA（SAW/WASPAS/TOPSIS/VIKOR/MABAC/MOORA/EDAS/PROMETHEE…）+ bootstrap 名次 CI | 纯标准库；不装 pyDecision（会强拉 OpenAI/GenAI SDK） |
| `verify_mcda.py` | 用 `pymcdm` 交叉验证手写 10 算法 | 防「公式错一个下标」 |
| `ice_board.py` | **燃油混动四榜出榜** | 输出 report.md + 四榜报告.html + scoring_audit.md + summary.json + INDEX.md |
| `forecast.py` | 未上市车型上榜预测 | 概率 + 名次区间，不进正式榜 |
| `notes_tool.py` | 新闻层沉淀（检索→填字段→入库） | 补备案表缺的价/上市窗口/MPV 判定 |

### E. 生命周期 / 口碑（仅规范副本有）

| 文件 | 作用 | 关键点 |
|---|---|---|
| `lifecycle_detect.py` | 销量变点检测（停产/清库/爬坡） | **症状式**，只看销量曲线 |
| `lifecycle_confirm.py` | 生命周期确证层 | 区分停产/改款/正常波动，带溯源；`leaderboard.lifecycle_of` 优先查它 |
| `lifecycle_fetch.py` | 生命周期自动拉取 / 一键重生成 | 分层合并：**手工 > 工信部公告 > 懂车帝状态 > 症状复核 > 默认在售**；`--selftest` 自检 |
| `koubei_merge.py` | 口碑样本合并 | 把 VDH 真实评价数并入 ICE 池，插补率 36.9%→4.9% |

### F. 燃油 / 混动（ICE）专用层

| 文件 | 作用 | 关键点 |
|---|---|---|
| `ice_sales.py` | ICE 车型级销量数据层 | 见 A |
| `ice_scoring.py` | ICE 6 维代理评分 | 见 C |
| `ice_board.py` | ICE 四榜渲染 | 见 D |
| `ice_crash.py` | **L1 官方碰撞证据层**（v6.3） | C-IASI+C-NCAP 受控匹配；只作事实列/判档证据，不进分；等级未解码 |
| `ice_funnel.py` | 入池销量漏斗敏感性（A/D/E/F） | 量化「销量在入池与评分两处出现」 |
| `ice_sensitivity.py` | 三情景敏感性（去销量维/禁插补维/名次 CI） | Kendall τ、前十交集、逐车位移 |

### G. 仓库 / 编排 / 工具层

| 文件 | 作用 | 关键点 |
|---|---|---|
| `warehouse.py` | 本地 sqlite 数据仓库 | 「出一期榜」→「查一次库」，支持回测；不用 DuckDB（wheel 太大） |
| `backfill.py` | 历史回填 + 品牌画像 | 销量 2023-10 起；备案 2023-12 起；品牌命中率锚点 |
| `ingest.py` | 数据摄取契约（爬虫产物→引擎） | 两段式抓取产物接入 |
| `monthly.py` | 月度出榜编排 | 一条命令跑全链 + 留档，历史永不覆盖 |
| `pipeline.py` | 单次出榜全链路 | 取数→闸门→打分→稳健→三档→反方审查→合成报告 |

### H. vehicle-data-hub（外部数据源，独立仓库）

| 文件/目录 | 作用 |
|---|---|
| `main.py` | 统一入口：抓汽车之家配置 Excel / 工信部公告参数 PDF（`fetch` / `gonggao query`） |
| `autohome_cc/` | 汽车之家数据采集适配（需 Chrome + 登录态 / cookies） |
| `miit_gonggao/` | 工信部产品公告解析（`change_notice` 等，依赖 bs4/lxml） |
| `benchmark/` | 口碑/基准评测采集（`collector/analyzer/report`） |
| `vdh_koubei_pull.py` | 用 **urllib 标准库**复刻 VDH 口碑抓取（沙箱无 pip 时的替代） |
| `probe_koubei.py` | 口碑接口探测（`search_series`/`fetch_koubei`） |

---

## 5. 主流程与执行顺序

### 5.1 纯电四榜（EV）——`runs/car-ev-4boards/build_html.py`

```
main():
  load_category("car_ev")
  读 candidates_trend.json → 只留 power==BEV（非纯电排除，不再算进候选数）
  thirdparty.apply   (adjust, 封顶)
  airbags.apply      (气囊三态)
  spec_facts.apply   (SCORING_ENABLED=False)
  trims.apply        (配置阶梯, SCORING_ENABLED=False)
  for board in 4 榜:
      filter_board → apply_gates → score_rows
      top_table(…, band)        # 档组字母
      tier_report(…, airbag_gate=True)   # T1 需证据A + 气囊基线
      → 决策卡 + 9 列表格（档组/车型/指导价/趋势月销/加权分/证据/气囊三态/生命周期/三档）
  → 四榜报告.html
```

运行：`cd runs/car-ev-4boards && python3 build_html.py`（导入 `~/.workbuddy/skills/omni-debate` 的 leaderboard.py）

### 5.2 燃油混动四榜（ICE）——`ice_board.py`

```
main():
  load pool(ice_pool_full.json) → IS.score_pool(pool, cfg)  # 6 维代理分 + crash + 症状式生命周期
  for board in 4 榜:
      filter_board → apply_gates → score_rows
      top_table → tier_report(无 airbag_gate)
      → report.md 段落 + HTML（旧渲染：14 列、同档组数字）
  → report.md / 四榜报告.html / scoring_audit.md / summary.json / INDEX.md
```

运行（在 `runs/omni-debate-skill/omni-debate` 下执行，pool 须与 `ice_sales.json` 同目录）：
```bash
python3 ice_board.py --pool ../../car-ice-4boards/ice_pool_full.json \
  --category car_ice_phev --out ../../car-ice-4boards --top 10
```

### 5.3 月度全链路——`monthly.py`

顺序（`log()` 可查）：① 探测最新可用月份 → ② 建月度目录（不覆盖）→ ③ 基准池当月副本 → ④ AKShare 乘联会大盘 → ⑤ 懂车帝逐月销量 → ⑥ `sync_cncap` 对齐安全分 → ⑦ `forecast` 未上市预测 → ⑧ `fetch_complaints` 投诉 → ⑨ 出榜 → ⑩ 入库（warehouse）。

### 5.4 单次出榜——`pipeline.py`

`取数 → 闸门 → 打分 → robust 稳健性 → tier 三档 → review 反方审查 → weight_audit → 合成报告`（全部纯函数）。

---

## 6. 关键判档逻辑（最容易出错的地方）

| 榜单 | T1（可直接推荐）门槛 | T2 细分（仅展示层） |
|---|---|---|
| 纯电 EV | 证据 ∈ **A**（官方碰撞成绩）**且** 气囊基线已核验（侧气囊+头部气帘齐备；中央气囊为加分，❓不剥夺资格） | 2A 证据较全可优先试驾 / 2B 有取舍点（价格暂定/需选低配/等新款/清库/爬坡）/ 2C 证据偏薄仅备选 |
| 燃油混动 ICE | 证据 A 两条并行路径：L4 口碑样本 ≥100 **或** L1 近期官方碰撞测评；且过「算法命中 ≥80% / 全情景 ≥60%」稳定性门槛 | 同上（ICE 版无气囊，2B 主要来自价格暂定） |

**销量不进加权**（EV）：近 6 月中位数只作 T1/T3 门槛。ICE 的 market 维已从漂移的 **24% 回调至设计上限 22%**（v6.6；实测四榜 top10 交集仍 10/10，唯一档位变化是中型轿车「迈腾」掉出 T1）。

---

## 7. 检查发现的问题（按严重度）

> **2026-11-15 更新：下述 🔴 两副本分叉、🟠 EV/ICE 渲染不统一、🟠 ICE 生命周期假在售 均已修复**——
> ① runs 副本已同步为 v6.5（leaderboard / lifecycle_confirm / ice_scoring / ice_board / levels / categories 全套）；
> ② `ice_board.py` 已移植 EV 可读性（决策卡 / 档组字母 / 2A·2B·2C / 趋势箭头 / 整数分 / 证据徽章 / 官方碰撞三态 / 生命周期列）；
> ③ ICE 生命周期改为 v6.5 的 `detect_lifecycle`（确证优先），实测已出现「已停产1 / 清库1 / 骤降3」真实状态；
> ④ 顺手修复 `report.md` 三档长期显示「0 款」的层级 bug（`tiers.get("tiers")` 双取）。以下保留原始记录备查。
>
> **同轮（DeepSeek 评审落地，v6.6）：** ① market 权重 24%→22%（其余五维等比放大、合计 1.0；四榜 top10 交集 10/10，仅中型轿车「迈腾」掉出 T1）；② 证据等级 A/B/C 改为描述性「充分/中等/薄弱」并与三档解耦（顺带澄清：ICE 的 T1 本就要求证据 A，「第1档却是薄弱证据」不成立）；③ 细分样本<5 取消排名改无序清单；④ 决策区新增「近期事件（换代/停产/清库）」，如「秦L DM：已停产（接替 秦MAX）→ 建议关注新车数据」。**插补折扣未采纳为默认分**，但已做成**敏感性系数扫描（0.03/0.05/0.08）**：实测四榜 **top10 全部不变**、仅组内位移（τ 0.83–1.00），说明该建议不改头条结论，故不进默认。value 分级别价格分位、P1（补价/EWMA/碰撞等级解码）仍留待本机数据。
>
> **纯电四榜（同理念落地，v6.6）：** 新增**漏斗表**（39→38→14+11+7+6=38→36→32+4→2，每个数字可加总）；**价格暂定 4 款单列**、不给名次、不进决策摘要；**档组改 G1/G2、证据改 E-A/E-B/E-C、购买档改词**（三套编号解耦）；**τ<0.8 的三榜改「强弱条 + 组内按名排」**、不显示伪精确加权分（中大型SUV τ=0.93 保留整数）；第一档空时给**行动清单**（可试驾 2A + 缺的硬证据 + 等新款 + 跨榜）；生命周期快照日之后的标**「（计划）」**（如极狐S5 2026-11-16）。

> 以下是本次体检的真实结论，附修复建议。**改之前先看这里。**

### 🔴 高：两处副本已分叉，ICE 跑在旧引擎上

- `~/.workbuddy/skills/omni-debate/leaderboard.py` 是 **v6.5**：`import lifecycle_confirm`、`tier_report(..., airbag_gate)`、`LC_LABEL` 含 `sales_drop`。
- `runs/omni-debate-skill/omni-debate/leaderboard.py` 是 **v6.3**：无上述三项（`diff` 共 50 行差异）。
- **后果**：ICE 用的 `ice_board.py` 只 `import leaderboard`（同目录），拿到的是旧版，生命周期不走确证层、`sales_drop` 文案缺失。两份引擎行为不一致。
- **建议**：以 SKILL v6.5 为准，覆盖 runs 副本（或把 runs 副本软链/同步到 SKILL）；或把 `ice_board.py` 的 `sys.path.insert(0, HERE)` 改为优先 SKILL 路径。

### 🟠 中：EV 与 ICE 渲染口径不统一

- EV 有：决策摘要卡、2A/2B/2C、档组字母、气囊三态、生命周期列、趋势箭头、价格暂定标签、停产车标注、第三方敏感性说明。
- ICE 还是旧 14 列表格（`# / 车型 / 动力 / 官方级别 / 指导价 / 6月趋势 / 10月销量 / 加权分 / 名次区间 / 口碑样本 / 证据 / 官方碰撞 / 同档组 / 插补维`），无决策卡、无 2A/2B/2C、用数字「同档组」。
- **建议**：把 EV 的可读性渲染抽成公共模块（如 `render_common.py`），两边共用；ICE 若补生命周期/气囊，需先有数据（见下条）。

### 🟠 中：ICE 生命周期是「假在售」

- `ice_scoring.py` 把每个候选 `lifecycle` 硬编码为 `{"status":"active"}`；`candidates` 无 `lifecycle` 字段时 `lifecycle_of` 也返回 active。
- `categories/lifecycle_status.json`（手工 15 条）只在 SKILL 副本，且只覆盖部分车型。
- **后果**：ICE 若加「生命周期」列会全是「在售」，等于误导。
- **建议**：跑 `lifecycle_fetch.py`（dcd + 症状层不依赖 bs4）把 ICE 池富化，或如实标「未独立核验」，**不要编造**。

### 🟠 中：工信部公告层依赖 bs4，公告无车型名主键

- `miit_gonggao/change_notice.py` 依赖 bs4/lxml；沙箱 PyPI 不可达装不上。
- 公告库主键是「商标+型号+批次+产品ID」，**没有市场名**，`join` 会断。
- **建议**：本机装 `pip install -r requirements.txt`（lxml, bs4）后跑；生命周期主信号**优先用懂车帝 dcd 状态**（有主键），公告只作冲突复核。

### 🟡 低：证据 B 太宽

- B 同时覆盖「上市半年、口碑样本够」和「仅配置表推得」，两者可信度差很多。
- 已在展示层用 2A/2B/2C 缓解，但底层 `evidence` 仍是宽口径。
- **建议**：给 B 加子标签（B-有长测 / B-仅配置表），需补长测/实测数据源。

### 🟡 低：数据源先天偏向

- 数据源本身是销量榜 → 市场热度偏向无法完全消除；ICE market 22%（v6.6 由 24% 回调）。
- 无终端成交价，指导价 ≠ 成交价（轩逸/朗逸/速腾/帕萨特等优惠大的车价值维会失真）。
- 非全市场穷举：只覆盖懂车帝销量榜在售车系，汽车之家全库 5372 但无销量者不入池。
- **建议**：报告已披露；若要消除需另建终端价证据层与全库销量源。

### 🟠 中：密钥明文（本次已确认）

- `key.txt` 为 **35 字节、`sk-` 开头的真实 API 密钥**，明文放在 skill 目录。
- **已核实：全部 `.py` 均未引用 `key.txt`** → 属遗留文件，链路不需要它。
- **风险**：一旦把 skill 目录推到公开仓库（或打包分发），密钥直接泄露。
- **建议**：立即把 `key.txt` 移出仓库（或删除），并在 `.gitignore` 加 `key.txt`；如曾提交过历史，需 `git filter-repo` 清理并**轮换密钥**。

### 🟡 低：工程卫生

- skill 目录混入 `__pycache__/`、3 个 `.bak-*` 备份、`config.json.bak`、`leaderboard.py.bak`。
- **建议**：`.gitignore` 排除 `__pycache__/ *.bak* key.txt *.db`；备份移到 `_attic/` 或删除。

### ✅ 已验证正常

- `robust.py` 手写 10 算法 → `verify_mcda.py` 用 pymcdm 交叉验证，防公式错。
- `spec_facts/trims/thirdparty` 三处 `SCORING_ENABLED=False` + 深比较断言，事实库不改分。
- EV 四榜 `build_html.py` 在当前 leaderboard 下可正常跑（已实测 48791 字符）。

---

## 8. 可借鉴的开源项目（优化方向）

> 本次联网检索（2026-11-15）结果，按「可直接借 / 需改造 / 只作参考」分级。
> 选型原则：**优先纯标准库或轻依赖**，不破坏「无 LLM、可复现、易部署」；重依赖只作 method 参照。

### 8.1 可直接借鉴（轻依赖，建议纳入或有条件纳入）

| 项目 | 许可 | 能借什么 | 用在哪 | 注意 |
|---|---|---|---|---|
| **pymcdm** | MIT | 20+ MCDA 方法、Kendall/Spearman 相关系数、多种归一化 | 已在 `verify_mcda.py` 交叉验证；可扩到 `ice_sensitivity` | 仅 numpy 依赖，最轻 |
| **pyrepo-mcda** | 开源 | 敏感性分析 + 20 种距离度量 + 妥协排序 | 补 `ice_sensitivity.py` / `sensitivity` 的方法学 | 活跃维护（2026-10） |
| **evaluma** | MIT | IQM ranking + **bootstrap CI**、**Kendall τ-b with bootstrap CI**、Friedman+Nemenyi | 名次稳定性 / 置信区间的现成实现，可替代自写 τ | 偏 ML benchmark，需改列名 |
| **rapidfuzz** | MIT | 快速模糊匹配（Levenshtein / token_set / WRatio） | 车系名跨源匹配（`align_specs`/`build_ice_pool` 的 `norm`+`similarity`） | 需 VC++2019；**仅 dev/本机可选**，不进主链 |

### 8.2 需改造后借鉴（重依赖或架构不同）

| 项目 | 许可 | 能借什么 | 改造点 / 为什么不能直接用 |
|---|---|---|---|
| **scikit-criteria** | BSD-3 | MCDA 全套 + 科学栈交叉验证 | 依赖 numpy/scipy/pandas，与本项目「纯标准库」冲突 → 只作 verification 旁路 |
| **pyDecision** | 论文引用 | 40+ MCDA 方法（AHP/ELECTRE/PROMETHEE…） | 强依赖 OpenAI/Google GenAI SDK（只跑 AHP 也会装），正是本项目手写 `robust.py` 的原因 → 只作方法参照 |
| **Arena-Rank** | Apache-2.0 | Bradley-Terry + **闭式置信区间** | JAX 后端太重；只借「闭式 CI 替代 bootstrap」的思路 |
| **pandera** | MIT | DataFrame schema 校验（类型/范围/枚举/唯一） | 可自写轻量校验（price>0、power∈枚举、koubei≥0、cls∈枚举），不必引入依赖 |
| **pySpider（sL-BrokeMachine）** | 参考 | 懂车帝新能源销量榜 `requests`+线程池+`pyquery` 抓取分页 | 只借请求/分页模式；本项目已用 urllib/requests + 限速 |

### 8.3 只作参考（重框架/毕设，不建议引入）

- CSDN 上的 Django+Vue+Scrapy「懂车帝汽车数据分析可视化系统」系列：技术栈重（MySQL/Redis/Django/Vue/ECharts），是可视化大屏毕设，与「纯函数榜单」目标不符。
- **Great Expectations**：企业级数据契约，重；本项目用 §7 断言 + 轻量校验即可。

### 8.4 本项目已借鉴过（勿重复造轮子）

`car_crawler`（车质网投诉，MIT）、`AutoInfoScraper` / `spider_dongchedi`（摄入契约）、`voltpilot`（单点推荐+赞助位分离）、`pyDecision`（权重审计/SPOTIS）、`MCDM-Workbench`（交叉验证思路）。

### 8.5 落地优先级建议

1. **高**：`evaluma` 的 Kendall τ-b + bootstrap CI → 替换 `ice_sensitivity` 里自写的 τ（口径更硬）。
2. **中**：自写轻量 `validate_pool()`（仿 pandera 思路，零依赖）→ 在 `build_*_pool` 出口校验价格/枚举/口碑范围。
3. **低**：`rapidfuzz` 作为本机 dev 工具，离线跑一次生成「名称匹配映射表」，主链仍走纯标准库。

---

## 9. 安全修改指南

| 想改什么 | 改哪里 | 注意 |
|---|---|---|
| 加/减维度、改权重 | `categories/<id>.json` 的 `dimensions` | 权重和需归一；改权重会动名次，要重跑 sensitivity |
| 改闸门（级别/动力/价格） | `categories/<id>.json` 的 `boards[].gates` | 只认官方二级分类与厂商指导价 |
| 改判档门槛 | `leaderboard.py` `tier_rules()` + `tier_report` | T1 门槛不能放水；改后跑 `--tier` 复核 |
| 改渲染 | EV: `build_html.py`；ICE: `ice_board.py render_html` | 建议先抽公共渲染模块再改 |
| 加数据源 | 新建 `fetch_*.py` → 接入 `build_*_pool.py` | 遵守限速、不绕验证、不模拟登录 |
| 生命周期 | `lifecycle_fetch.py` 分层合并 | 手工层优先；dcd 有主键优先；公告只做冲突复核 |
| 跑月度 | `monthly.py` | 历史目录不覆盖；失败会降级继续 |

---

## 10. 产物清单

| 文件 | 内容 |
|---|---|
| `report.md` | 四榜前十 + 分动力视图 + 纯销量对照 + 三档 + 待补清单 |
| `四榜报告.html` | 可点开的可视化版本 |
| `scoring_audit.md` | 每车每维分数、公式、置信度、插补标记 |
| `summary.json` | 机读摘要（前十、三档、落榜、覆盖率） |
| `sensitivity.md/.json` | 三情景敏感性（Kendall τ、前十交集、逐车位移） |
| `funnel_sensitivity.md/.json` | 入池销量漏斗四情景对照 |
| `crash_evidence.md` | L1 官方碰撞证据清单与匹配纪律 |
| `INDEX.md` | 归档索引与版本记录 |

---

## 11. 术语表

| 术语 | 含义 |
|---|---|
| 档组 | bootstrap 名次区间重叠的一组车，同字母即名次不可辨（≈ 标并列） |
| 三档 | T1 可推荐 / T2 取舍 / T3 证据不足（与名次是两套逻辑） |
| 2A/2B/2C | T2 内的展示细分，不改档位判定 |
| 证据 A/B/C | A=官方碰撞；B=上市半年+口碑足够（含仅配置表）；C=新上市/样本不足 |
| L1/L4 | L1=官方碰撞测评记录；L4=口碑自选择样本 |
| 插补维 | 用同榜中位补的维数，越多证据越薄 |
| SCORING_ENABLED=False | 事实库代码红线：只写事实、绝不改分 |

---

_本手册由本次体检生成，配套代码见 `~/.workbuddy/skills/omni-debate` 与 `runs/omni-debate-skill/omni-debate`。_
