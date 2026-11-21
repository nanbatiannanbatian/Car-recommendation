---
name: car-recommendation
display_name: 可信购车榜单引擎
display_name_en: Trustworthy Car Recommendation Engine
description: 购车推荐、车型横评、汽车排行榜、纯电/燃油混动对比、碰撞安全与电池电芯核验场景使用。用可执行口径闸门约束级别/动力/价格/证据，产出可复现、可审计的榜单与预测，不依赖 LLM 主观排名。
description_zh: 用可执行口径与证据分层，生成可复现、可审计的购车榜单与车型横评，覆盖纯电/燃油混动、碰撞安全与电池电芯核验。
description_en: Generate reproducible, auditable car rankings and comparisons with executable ranking gates and evidence layering, covering EV/ICE/PHEV, crash safety and battery cell facts.
examples_zh:
  - "帮我出一份 15-20 万纯电轿车的可信榜单"
  - "对比这两款车的碰撞安全成绩和电池参数"
  - "核验某车型是否属于中型车、指导价多少"
examples_en:
  - "Build a trustworthy ranking of EV sedans priced 15-20万"
  - "Compare crash test scores and battery specs of these two cars"
  - "Verify a model's official class and guide price"
version: 10.2.2
author: "nanbatiannanbatian"
---

# Car recommendation —— 可信购车榜单引擎（v10.2.2）

> 前身 omni-debate（多模型辩论 skill）；2026-11 辩论引擎整体移除，本 skill 只保留
> **可复现的榜单与预测链路**。为什么不用 LLM 直接排名，见《为什么出榜流程不含 LLM/辩论》。
>
> 从 WorkBuddy 技能市场安装后即可使用，**无需配置任何 API key 或账号**
> （榜单链路不调用模型）。直接用自然语言提问即可，例如「帮我出一份 15-20 万纯电轿车榜单」。
>
> 进阶用户也可以在命令行直接调用底层脚本（安装 Python 3.10+ 后执行
> `pip install -r requirements.txt`）：
>
>     python3 monthly.py --dry-run
>     python3 filings.py --list
>     python3 pipeline.py --category car_ev --candidates 候选池.json --out runs/x

## 代码结构（v10.1 模块化，改代码前先看这里）

根目录 81 个平铺脚本已按职责收进 `modules/`，**根目录只剩薄壳入口**（`python3 monthly.py` 这类命令照旧可用，
薄壳只做 `runpy` 转发，不含业务逻辑）。

```
modules/
  contracts/   数据契约层（零依赖零 IO）：trims 字段/枚举/校验、车名归一化
  research/    取数与证据采集：所有对外网络调用的唯一出口（25 个采集器 + researcher 检索）
  physics/     电动车实际续航物理模型（零依赖；物理常量单一事实源）
  envscore/    跨能源环境分引擎：engine.js（唯一事实源）+ 页面生成器
  engine/      打分口径 / 闸门 / 事实注入（纯计算，无 IO）
  board/       出榜编排与渲染（pipeline / monthly / board_v2 / master_pool / bootstrap）
  regress/     回归测试：结构 15 项 + bug 67 项 + 失败回测 63 条 + 专项回归 + node 测试
data/gen/      可重建的生成物（trims/master_pool/ice_crash…），**不打包**，用 bootstrap_data.py 生成
categories/    人工维护的口径配置 + 不可重建的事实库（气囊/物理参数/基线池），随包发布
runs/ dist/    跑批产物与发行 zip，**不打包**
paths.py       全项目路径唯一事实源（ROOT/cat()/gen()）+ 生成物名单与重建规格
```

**依赖只能向下**：`contracts`（最底层，零依赖）← `research`/`physics` ← `envscore` ← `engine` ← `board`。
两处倒挂已消解：车名归一化 `canon` 下沉到 `contracts/model_names.py`（原 `engine→board`）；
逐配置校验 `validate` 下沉到 `contracts/trims_schema.py`（原 `research→engine`，采集方不该依赖打分引擎）。
违规由 `layout_regress.py` 拦截（开发者回归，不随包发布）：薄壳、依赖方向、契约层纯净度、
生成物位置与**可重建性**、路径硬编码、文档 CLI 共六类。

**数据落盘规则**：读数据一律 `paths.cat("xxx.json")`——生成物自动指向 `data/gen/`，人工配置指向 `categories/`；
写生成物用 `paths.gen("xxx.json")`。**禁止再写 `os.path.join(HERE, "categories", ...)`**（重构前散落 46 处，
文件一进 `modules/` 全指错目录）。文档里出现的 `categories/trims.json` 是逻辑数据名，实际位置以此规则解析。

**打包判据只有一条：能不能用代码重建**（不是体积大小）。
- 可重建 → 不打包（`paths.GENERATED_JSON`），用户随代码逐步生成：版本一升、口径一变，本来就该重算，
  打包反而把过期数据发出去。新装包首跑执行 `python3 bootstrap_data.py --check` 看缺什么，
  `--apply` 自动重建（每项都有命令、联网标记与前置说明）。
- 不可重建（人工采集核验的气囊/物理参数/基线池，`paths.CURATED_JSON`）→ **必须随包**，否则新装包功能残缺。

**为什么这么分**（可独立发布性）：`research` 与 `physics` 是真正的独立 skill（取数检索 / 续航估算），
`envscore` 依赖仅 physics、也能独立发布为「用车条件适配计算器」；`engine`+`board` 是本项目主流程，`regress` 项目专属。
模块契约写在各自 `__init__.py` 顶部，新增文件前先读对应包的契约。

## ⛔⛔ 铁律必须代码化：写进 SKILL.md = 会忘，接进出榜链路 = 不会漏（2026-11-20 用户追问"已有铁律为何还出错"的总则）

用户 2026-11-20 一整天连续抓出多个同类问题：候选池漏收 Model 3/极氪007/比亚迪e7、
判档分与主榜分两套口径、深蓝L06 note 写"无碰撞成绩"但 crash 有 C-NCAP 92.3%。
**这些此前都有铁律明文写着，照样出错。** 根因不是"没写规则"，是**规则只存在自然语言里、靠执行者记得**——
人（和模型）一定会忘、会看漏、会抄错层。

**元原则（适用于每一条铁律）**：
1. **每条防止错误复发的规则，必须落成可执行校验 + 接入出榜链路（sys.exit 拦截），
   并配一条回归测试（含反向篡改验证：注入错误必须被检出）。** 只写 SKILL.md 文字 = 不算完成。
2. **"同一事实多字段"必须有一致性门禁**：note 自由文本 vs crash/safety_basis/evidence_status、
   trim 标签 vs score_basis、判档分 vs 主榜 total——凡是同一件事写了两遍的地方，
   都要有代码比对，不能靠人同步。现成工具：`cross_verify.check_internal_consistency()`（条目内）、
   `trim_coverage.check_universe()`（候选池全集）、`with_config_scores()`（判档分同源）。
3. **数据出错先修根因+加门禁，再修本次产物**；只改产物不改门禁 = 下次必复发（用户"不许打补丁"铁律）。

当前已代码化的门禁清单（出榜前 pipeline 自动跑）：完整性门禁 / 内部一致性门禁 / 判档分同源 / 指纹门禁 /
逐配置覆盖 / 48+ 条 bug 回归。新增任何铁律时，对照这套模式补门禁+回归。

## ⛔ 联网取数只走 research（唯一网络出口，代码强制）

来源：2026-11-21 用户「查数据是否要用 research 模块，是否要加入铁律让你严格遵守，
你搭的可插拔也要纳入其中」。此前只有 MODULES.md 里一句声明——**声明会忘，门禁不会**。

1. **任何模块要联网，只能调用 `modules/research/` 下的函数**；不得自己 `import requests / urllib /
   httpx / aiohttp / http.client`。由 `layout_regress.check_network_egress()` 强制，越界即 FAIL。
   **本条不接受任何例外**：极轻量级的联网（哪怕只是 GET 一个 JSON）也要在 `research/` 内实现一个
   函数、由业务模块调用。「就两行，就地写方便」正是把超时/重试/留痕/可追溯一起丢掉的开始。
   为什么：业务模块直接抓页面会绕过超时/重试/留痕/防编造整套约束，且「这条数据从哪来」无法追溯。
2. **新增数据源必须注册进 `modules/research/trim_sources.py` 的 `SOURCES` 源链**，实现
   `_try_xxx(model)` 后登记；不许在业务模块里临时抓一个页面就写库。源链顺序固定：
   本地索引 → 站点检索 → **存在性核对（最后，只出证据）**。
3. **存在性核对只产出证据**（车在售、指导价区间），**不得产出要入库的结构化字段**。
   宁可标「配置待补」，不许编造配置——这是「事实不进分」的同一条底线。
4. 联网函数必须：带超时、失败返回 None/空而**不抛未捕获异常**、每次尝试写进 `attempts` 留痕
   （待补清单要能看出「试过哪些源、各因何失败」，而不是一句「未匹配」）。
5. 跨站现状（2026-11-21 实测）：易车 API 返回反爬 HTML、懂车帝 config 页 0 字节、
   有驾内嵌数据只有 1 条主推款 → 靠 requests 拿不到齐全的跨站配置表；要做得引入浏览器自动化，
   届时仍必须遵守本条：实现放在 `modules/research/` 内并注册源链。
6. `layout_regress.EXCEPTIONS` 只用于**包间依赖方向**的个别登记，**对网络出口不适用**：
   往例外表里塞网络豁免也不会生效（`scan_network_egress` 不读它），这一点由回归 t077 验证。

## ⛔ 第零步：接到排行指令，先判榜型再出榜（2026-11-20 用户明确要求，强制）

用户原话：「现在主要有两种榜，一种相同能源类型的打分榜，另一种不同能源类型的打分榜加环境分。
接到排行指令你先判断是哪种榜然后再出榜。」

**任何"出榜/排行榜/前十/推荐"指令，动手前必须先判定属于哪一类，走错流程=整榜作废。**

| | A 类：同能源打分榜 | B 类：跨能源打分榜 + 环境分 |
|---|---|---|
| 触发条件 | 指令限定**单一能源**：纯电/油车/油混/插混/增程其一；或车型池本身只有一种能源 | 指令要**多种能源混排**：同时出现燃油/油混/插混/增程/纯电中≥2 种；或用户只说"轿车/SUV/某价位"没限能源、天然混着多种能源 |
| 典型问法 | "15-20万纯电轿车榜""燃油中型车前十""插混SUV排行" | "12-14万轿车mix了油车电车的比较""不同能源放一起打分""我想买轿车（没说能源）" |
| 统一维度 | 各能源用各自配置：纯电8维(三电/安全/智驾/空间/性价比/质量/操控/车机)；油混榜6维(市场/性价比/空间/驾驶/座舱/可靠性) | 跨能源统一8维：第1维"动力总成"电车=三电、油车=三大件、混动=两者，共用18%权重；其余7维共用 |
| 打分公式 | 现有主榜：8维(或6维)加权×10 + adjust，`board_v2.main_board()` | 综合分 = 产品力(统一8维) + 场景调节(封顶±10) + 硬条件惩罚(全额·降档·红标) |
| 环境/场景分 | **不加**，纯产品力排名（同能源内无需环境修正） | **必须加**：10项用车条件下拉(营运/气温/里程/长途/充电/限牌/持有/高速/方盒子/驾驶/空调)+硬闸门；续航用 `ev_range_model.py` 物理模型算 |
| 走哪个流程 | `pipeline.py --category <car_ev*/car_ice_phev>` → `board_v2.py` → `render_board_html.py`，配 `categories/car_*.json` | `build_energy_compare.py`（读 `master_pool.json`+`vehicle_physics.json`，import `ev_range_model.py`）→ `energy_compare.html` |
| 销量角色 | 事实列/候选发现器，不计分 | 同左，不计分 |
| 铁律 | 两类都遵守「每车必须有逐配置数据」「候选池收全」「不许打补丁+改完回归」 | 同左 |

**⛔ 判定规则已代码化：`energy_router.py::classify(query)` —— 自动判定，禁止问用户（2026-11-20 用户强制）**

用户原话：「用户提问自动准确选择什么能源形式能不能固定下来，不让用户选择。」
以前这条规则只写在文档里靠人记、每次都用 AskUserQuestion 问用户选能源，现改为可执行判定：
```
python3 energy_router.py "<用户原始提问>"    # 返回 board_type/powers/needs_user_choice/reason
```
判定逻辑（确定性，全部代码在 energy_router.py，不要在本文件另写一套）：
1. 显式单一能源词（纯电/BEV、燃油/油车/ICE、油混/HEV/双擎、插混/PHEV、增程/EREV） → A 类单能源。
2. 显式跨能源词（跨能源/不同能源/混排/全能源/油电一起…）或 >=2 种能源词 → B 类，按枚举能源排。
3. "新能源" → B 类 [PHEV/EREV/BEV]（国家新能源口径，不含纯燃油/油混）。
4. **只说车型级别/价位、完全没提能源**（如"12-18万中型SUV前十"）→ **默认 B 类跨能源全 5 种（ICE/HEV/PHEV/EREV/BEV，含油混）**。
5. 任何情况 `needs_user_choice` 恒为 False —— **禁止再用 AskUserQuestion 问用户选哪种能源口径**。

接入方式：出榜脚本调用 `ER.classify(USER_QUERY)`，用返回的 `powers` 做候选池能源闸门，把
`board_type`/`reason` 写进榜单页面（参考 build_midsuv_202610.py）。回归：`energy_router_regress.py`（含反向篡改）。

**⛔ B 类正式榜必须有「真环境分 + 全套区块导航」（2026-11-20 用户强制，禁止简陋版）**
用户质疑跨能源前十榜"没有环境打分、没有参考可信榜本榜导航多项，严重不足"。根因：早期只用 3 项固定加分当环境分、
页面只有 5-6 个简陋区块。现固化为（代码化，见 `build_midsuv_full.py` + `cross_energy_sections.py`）：
- **环境分引擎单一事实源 = `build_energy_compare.py`**：12 项用车条件下拉（营运/气温/里程/长途/充电/限牌/持有/高速/方盒子/驾驶/空调）
  + `ev_range_model.py` 物理续航 + 6 条硬条件惩罚（无桩纯电−15/极寒低价−12/短持有−8/长途续航不足−10/限牌油车−8/营运油车−10，
  全额计入+降档+红标）。**禁止另写简化调节逻辑**；通过 `EC.build(cars_override=…, extra_head=…, extra_tail=…)` 注入榜单数据。
- **全套区块 = `cross_energy_sections.py::BLOCKS`（对齐 board_view 18 项契约）+ 导航卡**。必要区块 `REQUIRED` 自检齐全。
- **定稿榜固定 + 场景推演器分离（v9.7.49 用户追问"让区块随环境分变是否好方法"后重构，取代旧的4区块动态方案）**：**榜单是可复现结论，推演是个人探索，同页混时态会破坏可复现性**。故：①`cross_energy_sections.py` 的 14 区块**全部是默认画像下的静态定稿快照**（`DYNAMIC_BLOCKS=∅`），可引用、可截图、不随下拉变化；②稳健性 = 跨 6 个代表性场景（默认/无家充/北方极寒/长途高频/营运/限牌）的**名次跨度**，首次加载用引擎算一次后固定——这才配叫"跨场景稳健性"（旧做法"当前场景下换力度"是概念错误）；③页面中部比较器主区的 12 项下拉 + 实时表 = **场景推演器**，`window.onBoardRerender(rows,cond,scale)` 钩子**只更新推演态标识 `sim-state`**（默认画像/自定义N项），绝不改写定稿区块。回归 `dynamic_blocks_test.js` 用 DOM stub 实跑验证「改条件后定稿区不变、只有标识变」。
- **纯电续航数据边界（v9.7.52，禁 null km）**：电量 kwh 必须优先取结构化 `battery_kwh`，缺失时用 `build_midsuv_full.extract_kwh()` 从 `key_configs/name` 文本正则提取（如「59.9kWh电池」「77.94kWh」）——补采常把电量写文本、结构化字段空，曾致 ID.AURA T6/马自达EZ-60 续航显示 null km。渲染层防御：有效续航列用 `r.eff!=null` 判断，纯电无电量显示「数据待核」、非纯电显示「—」，禁止把 null/undefined 直接显示。回归 `ev_range_test.js`（9项：所有纯电 kwh>0 且续航非null、ID.AURA T6 专项）+ extract_kwh 单测。
- **用车条件分组与默认过滤（v9.7.50，v9.7.51 聚焦化）**：12 项条件按业内调研权重分三组——核心能源决策8项（充电条件/冬季气温/年里程/单次里程/长途频率/限牌/持有年限/高速占比）**默认平铺可见**；能耗微调3项（方盒子造型/驾驶风格/空调）+特殊场景1项（营运）**收进「高级选项」折叠区、默认收起**（v9.7.51：聚焦直接影响油电选择的8项，但保留物理续航模型输入与小众场景，不删除——方盒子/驾驶风格/空调是 rangeCalc 输入，营运触发油车−10硬条件）。`box` 文案统一为「方盒子造型」。**默认过滤必须随榜口径注入**：`build_energy_compare.build(def_low,def_high,def_body)` 传该榜级别/价位（中型SUV榜=12-18万/SUV）——历史上因沿用示例默认「12-14万轿车」导致 SUV 榜默认空表，回归 `filter_default_test.js` 守此（15项：8项平铺/4项折叠默认收起/12项无遗漏/默认非空表/有匹配车/场景调节生效）。
- **全环境项回归（`env_options_test.js`，77项）**：见上一条 test(v9.7.47)——遍历 12 项×每选项断言生效/无NaN/硬条件阈值/极端组合合法，中性选项白名单需 hint 有据。此测试守引擎，与本页展示层分离。
- **全环境项回归（`env_options_test.js`，77项）**：遍历 12 项×每个选项，断言每项非中性选项至少对某车生效（软分/硬条件/续航之一变化）、无 NaN/越界、关键硬条件阈值正确（无桩纯电−15/极寒低价−12/限牌油车−8/营运油车−10/over8纯电+1.0）、极端组合结果合法。设计为中性的选项（hint 含"不调节/默认/平衡点"）登记在 `NEUTRAL_OPTIONS` 并要求其 hint 有据，禁止用白名单掩盖漏实现。**已抓出并修复**：`hold_years=over8` 选项 hint 承诺"纯电加分"但引擎漏实现（完全无效），已补软调节（纯电+1.0/插混增程+0.3/油车油混−0.3）。
- 出 B 类固定榜命令：`python3 build_midsuv_full.py`（读候选池+补采分→构造 cars→注入区块→交互 HTML）。
- 回归：`midsuv_full_regress.py`（30 项，含 node 实跑环境分 + 反向篡改）；`energy_compare_regress.py` 20 项守引擎。
- 交付 B 类榜默认给**完整交互榜**（HTML），静态前十表可作为结论速览一并给。

**出榜前自检**：判定结果要在回复开头讲明（"这是 A 类同能源榜，走XX流程" 或 "这是 B 类跨能源榜，加环境分"），
并注明"由 energy_router 自动判定、未询问用户"；再按对应流程执行；两类都先过「每车逐配置数据」铁律与「候选池收全」铁律。
**⛔ 出榜回复必须主动告知销量口径（2026-11-20 用户强制）**：每次出榜，在回复显著位置明确写出两件事：
① **趋势销量口径**：判档/打分用的是「最近 N 个月（默认6，或3）逐月中位数」，**不是单月**——
   稳态车取中位（抗清库月/换代月异常），新车爬坡取「中位 vs 最新月」较大值，结果存 `trend_sales`。
② **数据月**：页面「销量」事实列显示的是单月（YYYY年M月，上个月；月初则上上个月）。
**两者是不同口径，必须分开讲清楚**，不能只说"销量是X月"让人误以为判档也看单月。
HTML/报告用 `--as-of <YYYYMM>` 标注事实列数据月，禁止停在"—"。

> 补录车销量口径已代码化：`bug_regression.py::t050`（单月冒充/假方法名会被回归拦截），此处不再展开。
**判完型后不要再问用户"要不要联网/补采"**——数据获取一律走下方「出榜数据闭环」自动完成（**联网找全量→本地表仅作缓存核验→缺口补采→出榜**）。

**⛔ 榜单实时性铁律（2026-11-20 用户强制）**：排行榜是**当前状态**的排名，不是历史快照回放。
用户若在请求里带一个"历史月份"（如现在 2026-11 问「2026年3月纯电中型轿车排行榜」），
必须识别出该榜单要求"实时"、不能往前推很久——往回 3 个月以上的历史榜没有意义（车型在售状态、价格、产品力、销量口径全变了），
**要明确告知用户：「排行榜按当前状态排名，×月是已过去的历史月份，我没有按那个时点出榜；当前榜用的是最近已发布月份（上个月/月初则上上个月）的销量」**，
然后按当前状态出榜。例外：用户明确要"历史归档/某月复盘/与上月档位对比"且数据确实落盘（如 `runs/<YYYYMM>/`），
才走历史目录回溯并标注"历史快照"。销量口径月份 ≠ 榜单状态月份——销量取最近已发布月，榜单车型/价格/产品力一律当前。

**⛔ 销量异常必须联网查因并标注（2026-11-20 用户强制）**：某车销量骤降/骤升或明显异常时，
**必须联网查明原因**（季度初出口集中/促销透支/换代改款清库/停产/营运车转B端等），
并像"换代改款、停产"一样在页面显著标注，**不许把口径性低值当成"车不行"**。
落地三处：① `categories/lifecycle_status.json` 加条目（status 用 active 等，不改档位；note 写异常原因+数据+来源）；
② 候选池 `note` 字段写含关键词的文案（主链路只读 `note`，**`trend_note` 是死字段，写了不会显示**）；
③ `render_board_html.py::risk_flags()` 与 `board_view.py::car_verdict()` 识别关键词
（"销量口径异常/出口集中/季度初出口/促销透支"）→ 页面出"⚠销量口径异常"徽标 + 评价列说明。
实证：Model 3 的 2026-10 零售 1010 辆（9月14478）经联网核实为季度初出口(59012辆占批发87%)+9月促销透支+大盘-27%
多重因素，非产品力问题，已按上述三处标注。

**出榜必附「待上市申报车型列表」**（2026-11-20 用户要求）：每次出榜，在主榜之后自动附一个申报表，
只展示与本次榜单**同级别/同车身/同能源**的待上市车（问中型纯电SUV才显示家越07纯电版）。
工具 `filings_board.py`：读 `categories/filings_notes.json`（申报库，含 `ref_car` 参考车关系），
用参考车 8 维产品力分 + 已知差异（车身/新车品控向中性收敛）推导**预估分区间**，
对比主榜同级车给三档结论（值得等/可等可不急/不建议等）+ 依据。
**申报车不进主榜**（无实际销量/官方价），标"预估·非官方核验"，帮用户回答"要不要等"。
新申报车入库时按事实记录品牌归属（如荣威家越07属上汽，与MG同集团）、参考车关系与来源。
**用户问"截止今天有哪些申报车未上市"这类时效问题时，必须先联网找全量最新状态，本地申报库只作缓存/交叉核验——不许先读本地表再补联网**（那样一直是旧数据）。

### ⛔⛔ 正式排行榜唯一标准：候选池 + 流程 + HTML 排版全部固定，禁止简化（2026-11-20 用户强制）

用户："把之前正式榜的候选池和流程以及HTML排版固定下来，以后正式排行榜都用一套，不要简化。写入skill。"

**凡是"正式排行榜"（A/B 两类都算），一律走下面这一套，不许另起炉灶、不许写简版脚本、不许自拼榜单表格。** 前车之鉴：2026-11-20 图快写了 `build_bev_midsedan_202610.py` 简版，只从销量榜 Top 挑 9 款、丢 15 款车型、丢全部区块、还用 Agent 临时补采分顶替已核验正式分 → 用户质疑"少了很多项、排名完全不一样、只有9款"。**这类简版今后一律禁止。**

**① 候选池（唯一来源，不许临时拼）**
- 权威母池 = `categories/baseline_car_ev.json`（人工维护基准源）；出榜候选优先用 `categories/` 下**已打指纹**的池（`score_fingerprint.py --stamp`，带 `_generated_by`）。
- 已核验的领域快照（如 `runs/midsedan_bev_1218_202610/candidates_allprice.json` 25款）可作候选，**分数一律复用其中已核验的 `scores`**，不许用 Agent 补采分顶替。
- "销量榜 Top" ≠ 候选全集：只能做候选发现器，绝不直接当入池名单（会系统性漏车）。
- 用 `runs/` 下无指纹快照必须 `--force-stale`，并在报告显著标注"过期/强制出榜"。

**② 出榜流程（唯一命令）**
```bash
python3 pipeline.py --category <categories/下的配置ID> \
    --candidates <候选池.json> --out <输出目录> \
    --as-of <YYYYMM销量月> [--quality --complaints] [--force-stale 仅在用无指纹快照时]
```
链路固定：`leaderboard` 配置校验 → `score_fingerprint` 指纹门禁 → thirdparty/airbags/spec_facts/trims 改分 → 逐榜**先分榜再闸门** → `L.top_table/tier_report/review/robust/weight_audit/tri` → 报告 `report.md`+`summary.json`+`trims.md` → **HTML 自动链路** `board_view.load_fact_library → board_v2.build → render_board_html.render`。
不许绕开 pipeline 自己写"出榜脚本"；不许跳过闸门/稳健性/三档/审查任一环。

**③ HTML 排版（唯一事实源 = `board_view.py`）**
- **区块清单/顺序/显示条件的唯一来源 = `board_view.py::BLOCKS`（18 项）**，渲染器 `render_board_html.py::render()` 严格按该顺序输出；导航卡也由 BLOCKS 生成。`MAIN_BOARD_COLUMNS` 定义主榜列。任何人不得在渲染器里另造区块、改顺序、自拼页面。
- **11 项必要区块 `REQUIRED_BLOCK_IDS`（硬契约，一个都不能少）**：`main_board / score_breakdown / tiers / product_power / evidence_notes / lifecycle / method_boundary / reproduce / three_board / robustness / audit_boundary`。`board_view.selftest()` + `render_board_html --selftest` 断言其完整——删必要区块会让自检 FAIL。
- 出榜后必须跑 `python3 render_board_html.py --selftest`（11 项区块完整）与 `python3 board_view.py --selftest`（契约一致），FAIL 不许交付。

**④ 唯一允许的定制**：在 pipeline 生成的 HTML 顶部加一块"实时性/销量口径"告知横幅（本次产物级，不碰渲染代码）；规则变动按正常流程改 `board_view.py`/`render_board_html.py` 并回归，绝不另写并行排版。

---

## 逐配置数据（已代码化）

> 用户铁律「每款车必须有逐配置数据」已代码化：`trim_coverage.check()` 出榜门禁 +
> `bug_regression.py::t009/t010` 回归（删配置即 FAIL）。入池三要素 = 8维分 + 逐配置 + 证据源，
> 由门禁保证。内置 `research/` 采集工具随包发布（自包含，pipeline 不依赖它）。

### 候选池收全（已代码化）

> 用户铁律「候选池尽量收全、能比就比」已代码化：`discover.py` 联网发现 → `universe_build.py`
> 独立生成权威清单 → `trim_coverage.check_universe` 比对 → `pipeline.py` 完整性门禁 + `bug_regression.py::t048`
> （删车能检出）。先联网盘点 universe、禁从候选池生成 universe 再自查（循环论证）均已断言，此处不再展开。

## 榜单口径协议 ranking.py（v4.1）+ 榜序复核 pairwise.py（v4.2）

排行榜 / 横评 / 购车类问题的失败，很少是「模型不会写」，而是**口径没被约束**。`ranking.py` 把口径做成可执行代码，而不是只写在提示词里。

### 它拦的六类真实翻车

| 翻车 | 表现 | 拦截点 |
|---|---|---|
| 自定义级别 | 用「轴距 ≥ 2800」把官方紧凑型车放进中型榜 | `gate_class`（只认官方二级分类） |
| 车系当 SKU | 拿车系合并销量当纯电销量、拿入门版定义整车 | `gate_power` + `require_trim` |
| 促销价入榜 | 限时价 / 补贴价 / 置换价 / 租电车身价当成入榜价 | `gate_price`（只认官方指导价） |
| 单月定性 | 新车首月、改款清库月被当成「卖不动」 | `trend_sales()` |
| 无证据给高分 | 没有 C-NCAP/C-IASI 成绩却给 8.x 安全分 | `evidence_grade` + 三态 |
| 越界与漏配置 | 模型把池外车型排进榜、整表不写评分配置 | `audit()` |

### 开启方式

榜单 / 横评类请求会自动走 `ranking.py` 的闸门与复核，无需单独启动脚本。
也可把规则写进 `config.json`（默认 `enabled:false`，不影响其他场景）：

```json
"ranking": {
  "enabled": true,
  "gates": {"classes": ["中型车","中型SUV"], "power_main": ["BEV"],
            "price_band": [12.0, 18.0], "require_guide_price": true},
  "trend_sales": {"window": 6, "method": "trimmed_mean",
                  "exclude_flags": ["clearance","launch_first"]},
  "require_trim": true,
  "require_evidence_grade": true
}
```

开启后：① `build_candidate_block()` 会把闸门规则（A–F 六条）拼进候选池提示词，优先级高于模型直觉；② 跑完自动产出 `ranking_audit.md/.json`，并追加进 `final_answer.md`。

### candidates.json 扩展字段（向后兼容，缺字段则跳过闸门检查）

```json
{
  "model": "深蓝L06",
  "aliases": ["深蓝 L06"],
  "cls": "中型车",                                  // 官方二级分类，必填才过级别闸门
  "power": "BEV",
  "price_guide": [11.79, 16.49],                    // 官方指导价区间（万元）
  "price_promo": "补贴价11.39（不计入）",             // 促销口径，只展示
  "trim": {"name": "560 Ultra 激光版", "price": 12.99},  // 评分对象＝带内推荐配置
  "trend_sales": 6500,                               // 趋势月销，由 sales_history 折算
  "sales_history": [
    {"month": "2026-06", "units": 7977},
    {"month": "2026-08", "units": 1867, "flag": "clearance"}
  ],
  "cell_brand": "宁德时代", "cell_multi_source": false,
  "bms_owner": "self", "pack_tech": "CTV",
  "battery_warranty": {"soh_threshold": 70, "lifetime": true, "first_owner_only": true},
  "crash": {"cncap": 92.7, "protocol": "2024", "year": 2025},
  "evidence_status": "A"
}
```

### 电芯 / 电池怎么判（`battery_score()`）

判的不是「品牌排行榜」，而是**体系确定性**四项：

1. **供应商是否确定**：同款多供应商混装要扣 1.3 分——用户无法选批次，一致性不可预期（例：零跑 C10 明确写「国轩高科/亿纬动力/中创新航」）。
2. **BMS 是否自研**：+0.7。热管理与均衡策略的主动权在这里。
3. **Pack 是否一体化**：CTB/CTC/刀片 +0.4。
4. **质保条款**：写了衰减阈值 +0.6；只换不修 +0.3；终身 +0.4；**仅限首任 −0.4**。

基线分：宁德时代 8.6 / 弗迪 8.6 / 中创新航 7.2 / 国轩 7.0 / 亿纬 6.8 / 未披露 6.0。
含义：宁德与弗迪是「体系级」供应商（弗迪＝比亚迪垂直整合，风险与整车绑定），但**品牌不等于单体性能**；同一品牌不同批次的差异，可能大于品牌之间的差异。所以品牌只作基线，混装与质保条款才是可判别的变量。


### 成对比较聚合（`pairwise_rank` / `consensus_from_rankings`）

多份榜单「平均名次」会被极端名次带偏。改成两两胜负再跑 Bradley-Terry（LMArena 同款思路）：

```python
import ranking
r = ranking.consensus_from_rankings([["A","B","C"], ["B","A","C"]])
# r["rank"] -> [("A", 1311.5, 26.5, 1.5), ...]   分数 / 胜 / 负
```

- 不需要先验权重，规避「权重是我拍的」这一质疑；
- 内置拉普拉斯先验（`prior=1.0`），避免全胜/全负时 MLE 发散；
- 平局按各 0.5 胜；输出 Elo 刻度（1000 基准，400/十倍胜率）。
- CLI：`python3 ranking.py --pairwise battles.json`（支持 `[{a,b,winner}]` 或 `[[名次...],...]`）。

**用途**：让 2–3 个辩手各自给一份排序，再用它合成共识 —— 比让裁判「直接拍一个榜」稳，也比加权求和容易解释。

### audit 的边界

只做机械结构检查：池外车型、未写评分配置、促销价入榜、单月销量定性、候选本身不过闸门。**不判断产品好坏、不核对数字真假**——数字真伪交给 grounding（precision）与人工抽查，候选覆盖交给 coverage（recall）。三者都过才算可交付。

单独用：

```bash
python3 ranking.py --selftest
python3 ranking.py --gate  runs/x/candidates.json
python3 ranking.py --audit runs/x/final_answer.md --candidates runs/x/candidates.json
python3 ranking.py --protocol
```

### 量化实测证据（v4.2，leaderboard.py）：证据层级升权

盖得/Consumer Reports 的护城河是**数字化测量**（残粉 0.2g、62dB、粒径分布），不是定性「好用」。`leaderboard.py` 证据层把来源分三级：**量化实测 > 一手 > 二手**——独立来源 + 正文有「数字+单位+测量语境」才算量化实测；有量化实测的维度 A 档门槛降一格（4→3 维），并联动 `evidence_gate`（第一权重维度须有一手及以上证据才进主榜）。`excluded_dims`（一手覆盖太薄的维度）不参与打分，剩余权重重归一化。**无量化实测支撑的维度不得写精确数值对比**（如「残粉少 0.3g」），只能做方向性判断。research.json 契约配套字段：`quant_measured: true|false`、`quant_metrics: {"残粉":"0.2g","噪音":"62dB"}`（见 research-collector/SKILL.md）。

```bash
python3 leaderboard.py --coverage coffee_grinder_strict --candidates cands.json --research research.json
```

## 品类无关排行榜引擎 leaderboard.py（v4.2）

排行榜的通用骨架是固定的：**发现 → 闸门 → 市场验证 → 横评打分 → 审计**。品类差异全部收进 `categories/<id>.json`，换品类只换配置。

```bash
python3 leaderboard.py --list
python3 leaderboard.py --validate coffee_grinder
python3 leaderboard.py --slots car_ev            # 每个维度的一手证据源与缺口
python3 leaderboard.py --render car_ev --candidates cands.json --top 10
```

配置里必须写清的六件事：

| 字段 | 作用 | 换品类时是否必须重写 |
|---|---|---|
| `discovery.sources` | 候选从哪来（分级销量榜/类目榜/年度榜单） | **是** |
| `gates` | 准入：级别/类型、动力、价格口径与带 | **是**（字段与 basis 全不同） |
| `sales` | 销量口径与趋势化规则 | **是**（汽车=乘联会零售，咖啡机=平台评价数代理） |
| `dimensions` | 维度、权重、每维的 `evidence` | 权重可借，evidence 必须重写 |
| `boards` | 是否分榜（汽车分轿车/SUV，咖啡机单榜） | **是** |
| `audit` | 越界/配置/促销价/单月定性检查 | 否（通用） |

`--slots` 是这套引擎最有用的一个命令：它把每个维度的「一手证据源 / 厂商与二手口径 / 缺口」摊开，逼你承认哪些维度其实没证据。实测汽车 7 维里有 3 维（智驾、空间、操控）完全没有一手源，咖啡机的研磨质量有一手拆机实测但样本只有单台单次——这类缺口必须在榜上标出来，不能藏在评分里。

**注意**：销量榜是「候选发现器 + 市场验证」，不是「产品评价器」。拿分级销量榜当候选来源是对的，拿它的名次当最终排名就错位了。

## 稳健性与用户优先级（robust.py，v4.3）

排行榜最容易挨的一句质疑是「**你的权重是拍的**」。再加辩手、再换模型都消不掉它，
能消掉它的只有两件事：换算法排序不变、换权重排序不变。

### 10 种聚合 × N 套权重

```bash
python3 leaderboard.py --robust car_ev --candidates cands.json --out robust.md
```

纯标准库实现（不用 numpy / pyDecision —— 后者把 OpenAI 与 Google GenAI SDK 列为强制依赖，
即使只跑 AHP 也会装上；本流程只需要 9 个方法，实现成本远低于依赖成本）。

| 方法 | 机制 | 在纯效益矩阵下是否独立 |
|---|---|---|
| SAW | 加权和（线性极大归一） | 基准 |
| WASPAS | 加权和 + 加权积（λ=0.5） | 独立（乘性惩罚低分项） |
| TOPSIS | 距理想解/负理想解 | 独立 |
| VIKOR | 折衷解，最小遗憾（v=0.5） | 独立（关注最差维度） |
| MABAC | 距边界近似域 | 独立 |
| MOORA | 比值系统（向量归一） | 独立 |
| EDAS | 基于平均解的正/负距 | 独立 |
| PROMETHEE II | 成对偏好净流 | 独立 |
| GRA | 灰色关联度 | 独立 |
| SPOTIS | 到固定边界的归一化距离（pyDecision 收录） | 独立，且**抗秩反转**：增删候选不改变已有候选得分 |

COPRAS 未被纳入：它在「全为效益型准则」时退化为加权和，不构成独立检验。

### 稳定性分三层，不能混算

| 层 | 固定什么、变什么 | 回答的问题 |
|---|---|---|
| 方法稳定性 | 固定权重，换 10 种算法 | 矩阵有分辨力吗？还是分差太小导致算法随便排？ |
| 权重稳定性 | 固定算法，换基准/AHP/各滑杆情景 | 结论对「你在意什么」有多敏感？ |
| 联合稳定性 | 两者都变 | 这个位置到底有多硬 |

**Kendall τ 是关键读数**：τ≥0.9 说明名次是产品差异；τ<0.8 说明矩阵分辨力不足，
此时「第 1 名是谁」不能作结论，只能说「这几款同档」。实测同一个引擎下：
汽车轿车榜 τ=0.754（84.8 与 84.5 只差 0.3 分，算法一换就换位），
磨豆机榜 τ=0.963（81.5 / 78.5 / 76.5 分档清楚，结论硬）。

### AHP 只作权重来源，不作聚合方法

AHP 是「判断矩阵 → 权重」的方法，与聚合算法正交，所以它进的是权重情景而不是方法列表。
`categories/*.json` 的 `ahp_matrix` 存 1–9 标度判断矩阵，代码用特征向量法求权重，
**并输出一致性比率 CR**：CR≥0.1 直接判定该情景不可采信（判断自相矛盾）。
car_ev 用「家庭安全优先」视角构造（安全 36% vs 基准 18%），CR=0.009。

### 稳健 ≠ 可信（这一条最容易被忽略）

算法在 10 种方法下都稳，只说明「这份评分矩阵算得稳」，**不检验矩阵输入是否可信**。
零交付、无实测的车完全可以 9/9 稳定第一——那只能说明评分是照着参数表打的。
所以 `--robust` 会自动交叉证据等级，输出「稳健但证据薄」清单：
证据等级非 A 的车即使排序稳定，也明确标注「名次不构成推荐」。

### 用户优先级滑杆

```bash
python3 leaderboard.py --render car_ev --candidates cands.json \
  --sliders "省心=90,智驾=20,空间=50,预算=50"
```

滑杆 0–100：50 不动，0 把该组维度权重压到 0，100 翻倍，再整体归一化。
汽车四个滑杆：省心（安全/三电/车机）、智驾（adas）、空间、预算（性价比）。
操控没有对应滑杆，保持基准权重——这是刻意的，因为「操控好」不是所有人都要的。

输出的权重对照表会打印基准 vs 本次，**打分实际使用用户权重**（不是只改显示）。
实测：省心拉到 90，马自达 EZ-6 从第 3 升到第 1（安全 8.9 全带最高），深蓝 L06 退到第 2。
这正是「指导买车」该有的样子：给的是按你偏好的排序，不是客观真理。

### 三榜并列

```bash
python3 leaderboard.py --render car_ev --candidates cands.json --tri
```

| 榜 | 回答什么 | 来源 |
|---|---|---|
| 销量序 | 市场认不认 | 趋势月销（新车看上市以来、清库月剔除） |
| 加权序 | 产品维度综合分 | 配置权重 + 各维评分 |
| BT 共识序 | 10 种算法投票后的共识 | 两两胜负 → Bradley-Terry |

BT 共识序**不用平均名次**（会被极端名次带偏），而是把 10 种方法的排序各自转成两两胜负再聚合。
`销量↔加权` 为负＝卖得好但产品分不出彩；为正＝被销量低估；差值 ≥3 加粗。

### 五层证据栈（`--slots`）

```bash
python3 leaderboard.py --slots car_ev --research research.json
```

配置里的 `evidence_stack` 分五层，每层写明**能支撑什么 / 不能支撑什么 / 独立性 / 来源与位置**；
带 `--research` 时会统计本次来源落在各层的条数，**零条直接标「⚠ 缺层」**。
这样「缺哪一层」在出榜时自动暴露，不靠人记得去查——实测它准确抓出了汽车榜 L1（官方碰撞）为 0 条。

汽车五层：L1 官方碰撞（C-IASI/C-NCAP）→ L2 投诉原始记录（车质网编号）→
L3 平台标准化实测（懂车帝，只看传感器读数不看排名）→ L4 大样本车主口碑（懂车分/车友圈）→
L5 销量与保值（乘联会/保值率）。**缺层意味着对应结论无一手支撑，必须标 evidence_gap，不得用软文补。**


## 四个外部项目的借鉴（v4.4）

不是照搬，是各取一条被验证过的方法。

### 1. pyDecision → 客观赋权（`--weightaudit`）

pyDecision 最有价值的不是它的 70 多种方法，而是它把「比较排序」与「比较权重」当成两个独立功能。
我们只借后者最深的一层：**把权重交给数据自己推一遍**。

```bash
python3 leaderboard.py --weightaudit car_ev --candidates cands.json
```

四种客观赋权（都在 `robust.py` 里，纯标准库）：

| 方法 | 逻辑 | 什么时候用 |
|---|---|---|
| 熵权法 | 离散度越大信息量越大 | 检查「权重是否与数据的区分力匹配」 |
| CRITIC | 标准差 × 与其他准则的冲突性 | 惩罚高度相关的维度（三电和安全常常同向） |
| MEREC | 逐个移除准则看整体变化 | 判断「少了这一维，结论会不会变」 |
| 标准差法 | 只看离散度 | 最朴素的对照 |

输出三张表：逐维权重对照、两组权重的余弦/τ、两套权重算出的排序 τ。
**判读要点**：余弦高但权重排序 τ 低，说明各维权重挤在窄带里，「哪维最重要」的顺序是噪声，
不该把权重表读成优先级排序。这正是本流程 car_ev 的实测结果（余弦 0.978 / 权重 τ 0.45）。

### 2. pyDecision → SPOTIS（第 10 种方法）

SPOTIS 用**固定准则边界**（0–10）而非样本内最优/最劣算距离，所以增删候选不改变已有候选的得分。
自检里放了真实反例：删掉一个候选后 TOPSIS 会出现 A/B 换位，SPOTIS 不变（`selftest` 第 9 项）。
把它加进方法集，等于给「名次是不是被候选池构成带偏的」加了一道检测。

### 3. voltpilot → 单点推荐 + 赞助位分离

```bash
python3 leaderboard.py --recommend car_ev --candidates cands.json --sliders "省心=90,智驾=20"
```

推荐理由不是写出来的，是算出来的：`w × (score − 全带均值)` 即该维对总分的贡献。
Top2 正贡献＝强项，Top2 负贡献＝代价，与亚军的差值分解＝「为什么是它」。
另外两道保险：

- **噪声检查**：首选与亚军差 <1.0 分时，自动标注「落在算法噪声内，实质并列」，
  并打印当期方法间 τ。**不允许用 0.2 分的差当作选型结论。**
- **赞助位分离**（借 voltpilot 的 `ad_sponsors` 做法）：`sponsor_slots` 里的车型若进入前 10，
  必须单独披露并声明不得影响名次；赞助只出现在披露区，不参与排序。

### 4. AutoInfoScraper / spider_dongchedi → 摄取契约（`ingest.py`）

两个爬虫项目的方法论是一致的：**先发现候选、再取详情、中间落表**。
AutoInfoScraper 是 Selenium 出 `car_list.csv` → requests 出 `models_info.csv` 两段式；
spider_dongchedi 是先建表再灌数据，产出评论/口碑。我们借的是这套**表结构契约**，不是爬虫本身：

| 表 | 层 | 作用 | 对应证据层 |
|---|---|---|---|
| `series.csv` | 系列 | 候选发现（series_id / name / cls） | 候选池 |
| `trims.csv` | 配置 | 评分对象（trim_id / 指导价 / 电芯 / 智驾硬件 / 悬架） | 评分输入 |
| `reviews.csv` | 口碑投诉 | 可核投诉编号 + 红旗词命中 | L4 + L2 |

```bash
python3 ingest.py --check series.csv trims.csv reviews.csv
python3 ingest.py --build series.csv trims.csv reviews.csv --band 12,18 --out candidates.json
```

`--check` 拦六类脏数据：缺必填列、主键为空/重复、价格无法解析、配置挂在不存在系列上、
口碑挂在不存在配置上。`--build` 转成引擎的 candidates.json，并按三条通道给证据等级：
可核投诉 ≥2 条 / 投诉 ≥1 且命中结构性红旗 / 口碑 ≥30 条 → B，否则 C。
**注意：爬虫产物最高只能到 B——A 级必须有官方碰撞成绩，这层爬虫拿不到，必须另行补。**

**不做的事**：不内置爬虫。反爬绕过与站点合规不该由排行榜引擎承担；
契约与校验交给引擎，抓取交给专门脚本，责任边界清楚。


## 三档分级（`--tier`）——当分差小于算法噪声时，不要给名次

```bash
python3 leaderboard.py --tier car_ev --candidates cands.json --out tier.md
```

判档不看分数高低，看三件互相独立的事：**算法稳不稳、证据够不够、市场认不认**。

> 判档分与主榜同源已代码化：`leaderboard.with_config_scores()` + `bug_regression.py::t051`（篡改注入即 FAIL），此处不再展开。

| 档 | 判据 | 回答的问题 |
|---|---|---|
| 第一档 | **独立算法家族命中 ≥5/6票** + 全情景命中 ≥60% + 证据 A + 趋势月销 ≥2000 + **跨归一化名次波动 ≤2 位**（v9.7.43） | 能不能直接买（不用取舍） |

**算法家族去重（v9.7.43）**：`robust.METHODS` 10 种 MCDA 算法按实测 Kendall τ≥0.8 聚类成 **6 个独立家族**（加权和系 saw/moora/waspas/spotis/edas、距离 topsis、折衷 vikor、边界 mabac、偏好流 promethee、灰色关联 gra）。第一档命中率按家族计票（每家族1票），不再按10种算法逐票——此前 saw~moora τ=1.0、saw系5种高度相关，MG07 曾"6/10命中"但6票全是同一家族虚高。家族去重后 MG07=33%、007GT=83%、007=100%，更真实反映独立算法支持。分组见 `robust.METHOD_FAMILIES`，回归 `t061` 锁死。

**阈值用票数不用比例（v9.7.43）**：家族数=6 无法整除 80%（实际变成83.3%），故第一档阈值改为**明确票数** `tiering.t1_min_family_hits=5`（6家族至少5票认可），报告文案同步显示"≥5/6票"，消除"名义80%、实际83%"的语义漂移。配置写 `"ratio"` 可回退按比例（旧榜兼容）。

**第二档强推荐 + 主榜金标（v9.7.50/52/53）**：第一档是"稳压群雄、不用取舍"的严选，不放宽（曾有人质疑 Model 3 稳稳第5为何不进一档——稳定第5≠稳压第1）。真正问题是别让用户误以为"只有T1那几款能买"：第二档加**强推荐**标记，条件=有权威碰撞实锤+总分≥80+无硬风险+(家族命中≥0.5 **或** 连续名次稳定：平均名次≤6且区间跨度≤5)，让"稳稳第5"的 Model 3 也被标出；`strong_pick_reasons` 导出 family/steady 供分情况说明。展示：三档卡片区绿标「强推荐」+ **打分主榜档位列金标「强推」**（`.strong-main`；v9.7.53 从车名列移到档位列，hover 讲清"为何强推+为何未进一档"，强推车不再叠加"为何未进一档"标记避免语义打架），两处共同构成"可优先看清单"。回归 `t066`（判定+叙事）、`t067`（金标在档位列、车名不重复、hover双重理由、非强推不误标）锁死。

**证据文本一致性门禁（v9.7.43）**：`evidence_consistency.py` 检查 `score_evidence.safety` 文本不得与 `safety_basis`/`scores` 矛盾——防"分数改了证据说明没同步"（007GT 曾分数8.8/basis=euro_ncap，文本却还写"无官方碰撞成绩"）。三条规则：文本说无成绩但有权威basis / 文本说有成绩但basis空 / 文本主体系与basis不符（佐证体系不算）。出榜拦截，回归 `t063` 锁死。

**碰撞体系单一真相源（v9.7.44）**：`crash_systems.py` 定义全球 **12 个权威碰撞体系**（C-NCAP/C-IASI/Euro NCAP/ANCAP/IIHS/NHTSA-NCAP/JNCAP/KNCAP/ASEAN NCAP/Latin NCAP/Bharat NCAP/Global NCAP）的 code/中文名/别名/家族/权威性。此前这些定义散落5处（映射表/徽标/证据门禁/安全门禁/leaderboard），每加一个体系改5处→漏掉 Model3 的 Euro NCAP。现全链路 import 它，加新体系只改这一个文件。`normalize()` 做别名归一（E-NCAP/Euro NCAP/euro_ncap→同一 code），防不同写法漏判。`official_safety_scores.json` 只管"各体系如何映射到0-10分"（NCAP家族星级共用一套、C-IASI等级制/IIHS奖项制各自映射）。回归 `t064` 锁死12体系+别名+全链路引用。

**连续名次补二值命中盲区 + 产品力≠市场热度口径（v9.7.48）**：用户质疑「Model3 全球热销但家族命中0%，是不是算法不准/太学术」。实测发现 Model3 总分87.2全场第4、8维全优，10算法稳定排第5（SAW 0.943 vs 第4名深蓝L06 0.952，仅差0.009）——**不是不稳，是"稳稳第5"被"进前3"二值截断埋没**（稳健性表此前还直接剔除零命中车）。业内（DecisionMind/Sałabun/NCBI rank aggregation）：①排名相关性≠正确性，算法高度相关可能只共享偏见；②top-k二值截断会因cutoff误导，差0.009和差9分算成一样。修复：`robust.per_alt` 加 `rank_mean`/`rank_span`（连续名次），稳健性表加"平均名次"列且不再剔除零命中车；pipeline→summary→页面展示平均名次TOP5 + 口径边界框。报告写明：本榜8维衡量**产品力非市场热度**，品牌/超充网络/保值/售后未单列计分，所以「产品力第5但销量第一」不是算法错也不是市场错，是二者回答的问题不同。**不改T1门槛、不加市场维度**（用户选：透明披露比硬塞维度诚实）。回归 `t065` 锁死。
| 第二档 | 无第三档风险，但未达第一档 | 要不要按偏好取舍 |
| 第三档 | 证据 C / 无交付 / 月销 <500 / 人工标注硬伤 | 现在该不该看它 |

**证据 A 的认定（v9.7.43 修正）**：以**是否有权威碰撞成绩**（`safety_basis` ∈ C-NCAP/C-IASI/Euro NCAP/ANCAP/IIHS）为准，**不看人工散落的 `evidence_status`**。此前 007GT 有 Euro NCAP 五星（碰撞保护93%同期第一）、命中率全场最高，却因人工标 B 被挡在 T1，导致 T1 全空、失去推荐意义。修后有碰撞实锤+高命中的车可进 T1；非国内体系（Euro NCAP/IIHS）只提示含金量、**不阻断**（符合"提示级不判劣"纪律）。其他 T1 门槛（命中率/月销/波动）不放水。`bug_regression.py::t056` 锁死，删碰撞实锤即出局。

阈值全部写在 `categories/<id>.json → tiering`，改口径不必改代码。

**归一化敏感性判据（v9.7.41，借 scikit-criteria 方法学空间思想）**：第一档要求「换算法、换权重都靠前」，
而归一化方式也是「算法选择」的一种（`robust.normalization_audit()` 的 5 种方式 linear/minmax/vector/zscore/sum）。
若某车换个等价归一化名次就跳 >2 位（`tiering.t1_max_norm_swing` 可配），说明它进前三的成绩落在算法精度噪声里，
不满足"稳"，**不进第一档**（降为第二档并披露"跨 5 种归一化名次 X–Y"）。
关键纪律：名次不稳是**算法精度问题，不是产品硬伤**——绝不因此进第三档（与「证据 C 不判劣」同理）。
实测 16 款扩展榜：τ 均值 0.920，小鹏MONA M03 / 奔驰CLA新能源 波动 3 位（2–5 名）被拦在 T2，档位本身未误伤。
可用 `tiering.norm_audit_enabled=false` 关闭（老榜样本过少时）。

**第二档必须再拆成两半**，否则会变成垃圾桶：
- 有偏好触发点——某套权重下能进前三，把优先级输入滑杆它就会上浮
- 无偏好触发点——任何单一优先级都推不进前三；没有硬伤，但也没有一项强到能当理由
（后者买它通常是因为品牌、渠道、保值、售后，不是产品力胜出。这一点必须写明，否则读者会以为它被否定。）

**第一档为空是合法结果**，但不能只写「无」。要输出「最接近的几款 + 卡在哪一条」：
实测 SUV 段第一档为空——命中 97% 的 T6 卡在证据 B，命中 98% 的 MONA L05 卡在证据 C 与零交付。
结论是「这个细分里算法稳、证据足、卖得动还没落在同一台车上」，比「无」有信息量得多。

每款给出「相对强项」：`w × (score − 池内均值)` 最大的那一维。
负值一律显示「无相对强项」，不要挑个最不差的当强项。


## 取数链：AKShare + OpenCLI（v4.6）

证据栈靠人工搜是走不远的。两条已实测可行的自动化取数链，分别补「大盘」和「车系」两层。

### 1. AKShare → 乘联会大盘（`fetch_sales.py`）

```bash
python -m pip install akshare -i https://pypi.org/simple   # 清华源可能取不到，用官方源
python3 fetch_sales.py --digest --month 10
python3 fetch_sales.py --out market.json
```

`akshare` 有 6 个 `car_*` 接口，**全部是厂商/大类/级别聚合，没有任何车型级销量接口**：

| 接口 | 给什么 |
|---|---|
| `car_market_total_cpca` | 狭义/广义乘用车 产量·批发·零售·出口（月度，含去年同期） |
| `car_market_cate_cpca` | 轿车 / SUV / MPV 分大类零售·批发 |
| `car_market_segment_cpca` | A00/A0/A/B/C 级别结构占比（**B 级占比 = 中型车细分市场的分量**） |
| `car_market_fuel_cpca` | 新能源细分市场（注意：`整体市场` 返回的是**新能源**，不是燃油） |
| `car_market_man_rank_cpca` | 厂商排名（单月/累计，前 10） |
| `car_market_country_cpca` | 分国别份额 |

实测能复现报告口径：2026-10 狭义乘用车零售 164.8 万辆（同比 −26.8%）、轿车 −38.2%、燃油（推算）−44.9%、新能源渗透率 67.6%。
**这三项与我此前人工核过的数字一致（差 ≤0.1pt）**，说明「10 月是价格战月」不是拍脑袋。

用途定位：L5 只做**背景与校验**，不做候选评分。要某款车卖了多少台，AKShare 给不了，得用乘联会明细表或上险量。

### 2. OpenCLI → 汽车之家车系字段（`fetch_specs.py`）

```bash
npm install -g @jackwener/opencli
python3 fetch_specs.py --brands 零跑 --limit 6 --scores
python3 fetch_specs.py --series 8511 8312 6139
```

`opencli autohome brand/score` 标称 `Browser: no`——**无登录、无浏览器扩展即可取数**，实测通过。能拿到的字段正好补最缺的几层：

| 字段 | 补哪一层 | 备注 |
|---|---|---|
| `level` | 级别闸门 | 官方二级分类（中型车/中型SUV），不用再猜轴距 |
| `guide_price` | 价格闸门 | 厂商指导价，天然排除限时价/租电价 |
| `overall` + 8 维 | L4 大样本口碑 | 空间/驾驶/续航/外观/内饰/性价比/智能化 |
| `pph 每百车故障` | 质量量化 | 车质网给不了的指标 |
| `review_users` | 样本量 | **PPH 必须结合它看** |
| `competitors` | 对照组 | 同级车与分数，天然竞品基准 |

**硬规则（写进代码）**：`PPH_MIN_SAMPLE = 30`。样本 4 条的 PPH = 0 不是「零故障」是「没抽到」。
实测零跑 C10 样本 120 条、PPH **170**，可引用；零跑 A10 样本 24 条 PPH 0，判为不可引用。
这批 2026 新车里大多数样本量只有 0–25 条，所以 **PPH 目前对新车基本失效**——这一点必须写在榜上。

### OpenCLI 的能力边界（实测）

| 目标 | 结果 |
|---|---|
| `autohome brand / score` | ✓ 免登录、免扩展，字段可用 |
| `dongchedi search / specs / series / score / koubei` | ✗ 返回 `returned no __NEXT_DATA__`（反爬或页面改版），headless 取不到 |
| `hackernews` 等公开 API | ✓ 免扩展 |

懂车帝要拿到数据必须挂浏览器扩展（复用登录态）。**本流程不代做这件事**：
该工具的设计目标包含复用登录态与反检测，用于批量抓取商业站点违反其条款、有封 IP/封号风险；
而且它给不了我们真正缺的 L1（碰撞成绩在 C-IASI 官网，不是登录后可见的数据）。
要补懂车帝的实测数据，用人工看榜 + 记录具体数值，比自动化更稳且合规。

### 已知数据陷阱

- **同一站点内部价格不一致**：零跑 C10 在 `autohome brand` 显示 12.28–14.28 万，
  在 `autohome score` 显示 12.58–14.28 万。抓下来必须标「两个口径」，不能挑一个当准。
- **`autohome brand` 不含级别**，级别只能从 `score` 取；只跑 brand 会让 `cls` 全空，闸门失效。
- **品牌名解析不全**：深蓝、极狐、吉利银河作为品牌名查不到（子品牌），要改用 series_id 或父品牌。
- **Windows 上 npm 全局装的是 `opencli.CMD`**，`subprocess` 需经 `COMSPEC` 转一层才能调用。


### 3. 级别与指导价对齐（`align_specs.py`）

候选池的 `cls`/`price` 若来自人工搜索，必须先与官方字段对齐再出榜。

```bash
python3 align_specs.py                      # 输出 alignment_report.md + candidates_v5_aligned.json
python3 align_specs.py --map manual.json    # 目录解析不到时手工给 series_id
python3 align_specs.py --apply-price        # 用官方车系价覆盖 price（默认不覆盖）
```

**级别可以直接换，价格不能直接换。** 原因：`autohome score` 的 `guide_price` 是**车系级**，
同一车系往往同时含插混/增程版本——海豹06EV 的纯电起价 10.98 万，车系「海豹06」起价是 9.68 万。
直接覆盖会把纯电价换成含插混价。故默认并列保存 `price_official`，主字段保留纯电口径，
差异在报告里逐条列出，由人用厂家配置表裁决。

**实测（car_ev 26 款）**：级别对齐 11 款、**冲突 0 处**（等于交叉验证原判定全对）；
价格下限一致 3 款、有差异 3 款（最大 −2.60 万）、口径存疑 5 款；PPH 可引用 6 款。
三档分级结果与对齐前**完全一致**——说明原 cls 没错，错的是价格精度。

**⚠ 目录接口不稳定（踩了两次）**：`autohome brand <品牌|A-Z>` 每次返回的 120 条窗口会变，
同一字母十分钟内两次调用可能给出不同集合，深蓝、银河这类会时有时无。因此：
- 品牌名精确命中（比亚迪/大众/马自达/零跑/小鹏/长安）可复现，优先走这条路；
- 字母目录只当兜底，**结果不可复现**，不能作为唯一依据；
- 解析不到的一律 `unresolved` 且保留原值，用 `--map` 手工给 `series_id` 兜底。

**匹配四道闸（都是踩出来的）**：
① 归一化不剥「新能源」（剥了「秦新能源」会变「秦」，错配到「秦MAX」）；
② 包含匹配要求长度比 ≥0.55（否则「风云A9」配到「风云」、「ID.AURA T6」配到「大众ID.」）；
③ 排除「海外/进口」版（否则「丰田bZ3」配到「丰田bZ3(海外)」）；
④ 我方带 EV/纯电、车系不带 → 标 `powertrain_ambiguous`，级别照收、价格不覆盖。


## 为什么出榜流程不含 LLM/辩论（v4.9）

同一个题目（12–18 万纯电中型车）实跑 5 轮双辩手 + 裁判，结论是：**辩论对出榜没有正贡献，还有三处硬伤。**

| 硬伤 | 具体表现 |
|---|---|
| 越界 | 裁判把候选池外的「零跑C01」排进前十；又以「口径存疑」剔除了本在池内的海豹06EV |
| 无法裁决 | 辩手没有检索权。两套销量口径矛盾时，只能互不相让，永远裁决不了 |
| 不可复现 | 同一问题重跑，名次会变，且无法定位是哪一轮引入的 |

辩论真正提供的东西只有一样：**分歧清单**。而分歧清单的每一项都能由数据机械算出来。

### 替代方案：三个纯函数

| 原来由辩论做 | 现在由谁做 | 输出 |
|---|---|---|
| 挑反例、指出排序不稳 | `robust.run_robust()` | 10 种算法 × N 套权重的命中率、τ 矩阵、有效独立方法数 |
| 质疑「权重是拍的」 | `weight_audit()` | 主观 vs 熵权/CRITIC/MEREC，两套排序的 τ |
| 找口径冲突、证据不足 | `review_report()` | 六类反对意见，分阻断/边界/提示三级 |

`--review` 是新增的确定性质证入口，替代裁判：

```bash
python3 leaderboard.py --review car_ev --candidates cands.json
```

它输出四段，**每条都带具体数字，不写形容词**：

- 🔴 **阻断级**：硬约束被违反（PHEV 混入、级别冲突）——必须修
- 🟠 **结论边界**：分差 <1 分、τ<0.8、权重情景改变前三——修不了，但必须写进结论正文
- 🟡 **提示级**：证据 C、PPH 样本不足、销量未拆分
- ⚪ **说明级**：促销价口径

**关键区分**：「分差小于噪声」不是缺陷，是结论的边界。它不该被归为「待修问题」，
而应直接改写结论——从「第 1 名是 X」改成「X 与 Y 同档」。

### 一条命令跑完整链（`pipeline.py`）

```bash
python3 pipeline.py --category car_ev --candidates cands.json --out runs/x
python3 pipeline.py --category car_ev --candidates cands.json --sliders "省心=90"
```

链路：**取数 → 闸门 → 打分 → 稳健性 → 三档 → 审查 → 合成报告**，产出 `report.md` + `summary.json` + **`榜单.html`**（可视化页，默认自动生成，`--no-html` 可关；`--as-of 202610` 标注数据月）。
全程无模型调用，同一输入必得同一输出。

### 榜单 HTML 可视化（v8.3.5 起内置）
此前只有 `ice_board.py` / `ev_battery_board.py` 自带 HTML，主榜链路只出 Markdown；
那些漂亮的分段榜页面是各 run 目录的一次性 `build_*.py` 产物、没进发布包。
v8.3.5 起 `render_board_html.py` 把渲染能力固化进包，`pipeline.py` 与 `board_v2.py` 出榜时自动生成 `榜单.html`：
主榜（事实参考列不计分）+ 三档分级 + 价位段子榜 + 分段汇总总榜 + 纯产品力辅助榜（`rank_delta`），一张页看全。

```bash
# 出榜后单独重渲染（换个标题/补上数据月）
python3 render_board_html.py --data runs/x/board_v2_data.json --summary runs/x/summary.json \
    --out runs/x/榜单.html --title "中型纯电轿车 12-18万" --as-of 202610
python3 render_board_html.py --selftest   # 结构自检
```

渲染纪律与榜单一致：事实列只展示不排序、分段/产品力榜复用主榜分数、无模型调用。
`board_v2.py --no-html` / `pipeline.py --no-html` 可关闭自动生成。

辩论引擎已于 2026-11 整体移除（orchestrator/pairwise/baseline/mcp_server/run.* 均删除）。非数据型争议（如维度取舍）改用 notes_tool 的检索沉淀 + 人工决策。

## 月度自动化（monthly.py）

```bash
python3 monthly.py                    # 探测月份 → 全链路 → 归档
python3 monthly.py --dry-run          # 只看会跑哪个月、写到哪
python3 monthly.py --force            # 同月重跑（覆盖该月目录）
python3 monthly.py --skip-align       # 跳过汽车之家对齐（最慢、且覆盖有限）
```

链路：探测月份 → 乘联会大盘 → 懂车帝逐月销量（6 个月中位数）→ 汽车之家级别/价格对齐 → pipeline → INDEX + 与上月对比。
实测端到端 **约 1 分钟**，全程无模型调用。

### 三条设计决定

| 决定 | 原因 |
|---|---|
| **月份要探测，不能硬编码** | 乘联会与懂车帝有滞后，遇长假会缺月。探测失败往前回退，绝不拿空数据算出一份空榜 |
| **销量取"上个月"，月初未出取"上上个月"** | 排榜销量默认口径 = 上一个自然月；若当前是月初、上月销量尚未发布，则取上上个月（**上上个月的数据一定已发布**，故最多探测这 2 个月即可，见 `monthly.probe_month()`）。**不拿当月未完整数据出榜**，也不硬编码某个历史月份 |

**⛔ 销量月份时效铁律（2026-11-20 用户强制）**：
- 提到"销量"一律是**最近一个已发布完整月份**：默认上个月；月初（上月数据未出）取上上个月；
  探测回退由 `monthly.py::probe_month()` 完成（**只探测上个月、上上个月两个月**——上上个月数据一定已发布，不需要再往前），出榜报告必须写明**销量口径月份**。
- `--intake --month` 不写死历史值：`master_pool.py` 支持 `--month auto`（走探测），提问出榜默认用它；
  旧硬编码 `202610` 仅作离线复现示例，不代表"当前数据月"。
| **候选池是复制，不是发现** | 自动发现新车需要判断官方二级分类与主力动力类型，目前没有可信自动源。候选池是人工维护的基准文件，月度只刷新销量/级别/价格。新增车型必须人工加进 `categories/baseline_car_ev.json` |
| **历史目录永不覆盖** | 每月一个 `<YYYYMM>/` 子目录，INDEX.md 存在即跳过（除非 `--force`）。归档可回溯，能算出档位变化 |

### 一个必须知道的坑：懂车帝动力细分榜漏车系

`new_energy_type` 的纯电/插混/增程三个榜**只收「独立动力车系」**——即厂家把动力写进车系名的
（`MG 07 EV` / `MG 07 PHEV`、`海豹06EV` / `海豹06DM`）。

**一车多动力的车系（深蓝L06、零跑C10/C11、EZ-6/EZ-60、启源Q06、小鹏MONA L03/L05、
极狐阿尔法T5/T7）在三个细分榜里一个都不出现，只在「全部」榜里存在。**

第一次实现只抓 `ne=1/2`，结果 13 款静默匹配不到、`trend_sales` 停在旧值——**表面上跑成功，
实际拿旧数据冒充新月度**。现在第三个桶 `ne=""` 兜底，并把这类标记为
`sales_scope = series_total`：**车系合计是纯电销量的上界（含同车系插混/增程），
榜上一律写 `≤X（车系合计）`，不能读成纯电值**。

实测 2026-10 的构成：13 款纯电单独可采、11 款只有车系合计、1 款（ID.ERA 5S）判定插混移出、
1 款（小鹏MONA L05）未交付无数据。

### 定时任务

每月 15 日 09:00 自动执行（automation id `940cbefe`）。汇报要求写在任务 prompt 里：
数据月与回退、两榜档位分布、审查结论、落榜车型与原因、销量口径统计、与上月变化、
本期未自动完成的部分。失败时读 `run.log` 如实报告，**禁止用旧数据拼榜单**。


## 多榜口径与生命周期（v5.1）

### 一份候选池，四张榜

同一批候选按「官方二级分类 + 主销动力」分榜，**每个榜有自己的 gates**（价格带上限不同）：

```json
"boards": [
  {"label":"中型纯电轿车（≤16万）","match":{"cls":"中型车","power":"BEV"},
   "gates":{"classes":["中型车"],"power_main":["BEV"],"price_band":[0,16],"price_basis":"trim"}},
  ...
]
```

`pipeline.py` 的顺序是**先分榜再闸门**，不是先闸门再分榜——否则价格带只能全局一刀切，
16 万和 18 万两个口径无法共存。

**级别匹配用子串，必须写完整级别**：`"中型车" in "中大型车"` 为 False，所以两榜不会互相污染；
但若写成 `"车"` 这种过短关键词就会全部命中。

### `price_basis: "trim"`：预算内买得到才算入榜

默认的价格判定是「车系任一价格落在带内」，这会让「推荐配置 16.09 万、车系 14.59 万起」的车
混进 16 万榜——读者按推荐配置买不到。`price_basis="trim"` 改为以**带内推荐配置的指导价**为准：

- trim 价 ≤ 上限 → 通过
- trim 价超上限但车系起价在带内 → 通过，但标注「需选低配」，top_table 里显示 `⚠超16万，需选低配`
- 全系都超 → `price_out`

### 生命周期：把「销量下滑」拆成三种原因

| 状态 | 含义 | 对销量数据的处理 |
|---|---|---|
| `discontinued` | 已停产 | **闸门直接落榜**（`lifecycle_out`）。不是排名靠后，是已经不能买 |
| `facelift_pending` / `clearance` | 改款在即、清库 | 低销量**不作为**竞争力下降证据；不阻断第一档，但必须写明原因 |
| `new_launch` | 新车爬坡 | 同上，但建议等 3–6 个月看稳态 |
| `active` | 在售 | 正常判定 |

停产车必须走闸门而不是留在池里——留在池里会让 `review_report` 把整榜判成「不可交付」，
正确做法是落榜并写明停产日期与替代车型。

实测这一条改变了结构：**秦L EV 2026-07 已停产**（被秦 MAX 取代，销量半年从 3,834 跌到 552），
若不判生命周期，它会被当成「卖不动」而扣分；实际上它是**退出过程**。
**极狐阿尔法S5** 2027 款 11-16 上市（预售 12.18–14.48 万，增 192 线激光雷达 + 5C 超充），
现款销量下滑是**清库**，等新款更划算。

### `top_table()`：名次与同档组并存

用户要「前十」，而 τ<0.9 时逐个名次是假精度。解法是两个视图并列：

- **加权排名**：给出名次，但相邻分差 <1 分合并为同一「同档组」
- **三档分级**：能不能买

两者必须显式区分开，否则读者会把「同档组第 1 组」误当成「第一档可推荐」——
而 C 级证据的车完全可能排在加权第 1（参数表好看、但没有碰撞成绩也没有实测）。


## 防错四件套（v5.2）：漏车、误判周期、拍分、拍阈值

这四个脚本解决的都是「不报错但会错」的问题——它们不会让程序崩，只会让结论悄悄偏掉。

### 1 `discover.py` — 候选发现（防漏车）

```bash
python3 discover.py --candidates runs/x/candidates_trend.json --out discover.md
```

拉懂车帝四口径逐月全量 → 按「价格 9–18 万 + 有纯电供给 + 近 3 月中位销量 ≥1000」筛 →
与现有池做差集 → 输出两张表：**确认纯电的池外车系** 与 **动力待确认的池外车系**。

为什么必须分开两张表：「只在全部榜出现」这一层混着大量燃油车（朗逸、凯美瑞、CR-V），
门槛不单独抬高，清单会被它们淹掉（实测 159 条 → 分表后 25 + 32 条）。

**级别列刻意留空**：官方二级分类没有可批量调用的接口，自动猜会重演「用轴距自定义」的错误。
它只负责找出来，判定仍由人做。

实例：这个脚本上线第一次就抓出 **汉EV（15.78 万起，1,616 台）与红旗 E-QM5（12.18 万起，1,273 台）
都是中大型车却没在池里**——和极狐 T7 同一类漏车。

### 2 `lifecycle_detect.py` — 变点检测（防误判周期）

```bash
python3 lifecycle_detect.py --candidates runs/x/candidates_trend.json --out detect.md
```

二分段最优分割（O(n²)，n≤12）+ 形状判据 → `collapse?` / `cliff?` / `clearance?` /
`rebound` / `ramp_up` / `stable`，再与人工标注对照，冲突处提示核查。

**核心边界：`collapse?` 只表示「销量崩塌」，不表示「停产」。**
实测同样是跌到峰值 12%–14%，秦L 是停产，极狐S5 是清库——区别不在数据里，在新闻里。
所以它是「提醒你去查」，不是「替你下结论」；命名用 collapse 而不用 discontinued 就是为了不越界。

### 3 `spec_score.py` — 配置驱动打分（防拍分）

```bash
python3 spec_score.py --specs specs.json --candidates cands.json --out spec_compare.md
```

每维 = 基线 + 若干加减项（激光雷达 +1.6、800V +1.0、10–80% ≤12min +1.4、轴距 ≥3000 +1.6…），
逐条可复算。输出「配置分 vs 人工分」并列，差 ≥0.5 的逐条给出依据。

**它最重要的产出不是分数，是「维度有效性检查」**：某一维若在本榜是常数，
它的权重实际失效。实测中大型车 safety 全部落在 6.3（标准差 0.00），
因为**全榜没有一台有官方碰撞成绩**——18% 的权重被平均分给所有人，等于没有权重。

反过来更值得警惕：人工分在这一维给出了 7.2–7.8 的差异（标准差 0.36–0.44），
看起来有区分度，实际是**没有依据的差异化**——没人有成绩，凭什么差 0.6 分。
配置分诚实地全给基线，这里人工分是错的。

### 4 `warehouse.py` — 数据仓库（防「只能出一期榜」）

```bash
python3 warehouse.py --build --candidates cands.json --db car_ev.db
python3 warehouse.py --rank --db car_ev.db --cls 中大型SUV --band 18
python3 warehouse.py --reconcile --db car_ev.db --candidates cands.json
python3 warehouse.py --trend --db car_ev.db
```

六张表：`dim_series` / `dim_weight` / `fact_sales` / `fact_price` / `fact_score` / `lifecycle_event`。
出榜变成一条 SQL（级别 + 动力 + 价格 + 停产四闸门都在 WHERE 里），回测变成 JOIN。

**存储引擎用 sqlite3 而不是 DuckDB**：`pip install duckdb` 要下 15.4MB，实测两轮安装
（各 5–9 分钟）都没完成；而数据量是 39 款 × 6 月 ≈ 1,500 行，sqlite 完全够用。
真要接全国上险量再换引擎——SQL 已经写好，换引擎只改连接串。

`--reconcile` 是这套仓的保险丝：SQL 与 Python 两条路径算同一个加权分，
差 >0.05 就是有一边错了。它当场抓出 SQL 漏算 `adjust`（人工加减分）的 bug。

### 5 `bootstrap_rank_ci()` — 名次置信区间（防拍阈值）

原来用「相邻分差 <1 分」判同档，这个 1 分是拍的。现在给评分矩阵加 ±0.5 分扰动、
重排 600 次，算每个候选的名次 95% 区间；区间有公共交集才算同档。

**必须用「组内公共交集」而不是「相邻两两重叠」**：两两重叠有传递性陷阱，
A 挨着 B、B 挨着 C，会把 10 款串成一组（实测会全并成 1 组）。要求整组共享非空交集，
等价于「存在一个名次，组内每台车都可能占它」。


## 入池规则与安全数据源（v5.4）

### `admit.py` — 入池是一条规则，不是一次判断

```bash
python3 discover.py --candidates runs/x/candidates.json --json discover.json
python3 admit.py --discover discover.json          # 自动判定入池 / 拒绝
python3 admit.py --candidates runs/x/candidates.json --check   # 复核现有池
```

五条规则，任一不过即拒，理由码可回溯：

| 规则 | 判定 | 拒绝码 |
|---|---|---|
| R1 级别 | 官方二级分类 ∈ 四类之一，来源 `levels.json` 缓存 | `level_unknown` / `class_out` |
| R2 动力 | 必须有纯电供给 | `power_out` |
| R3 价格 | 带内最低配 ≤ 16 万（中型）/ 18 万（中大型） | `price_unknown` / `price_out` |
| R4 销量 | 近 3 月中位 ≥ 300 | `sales_low` |
| R5 周期 | 非 discontinued | `lifecycle_out` |

**级别查不到就拒绝，不猜。** 这是硬约束——按轴距推定级别正是上一版把深蓝 S05
放进中型榜的原因。`levels.json` 是半自动累积的缓存，查一款补一款，不阻塞流程。

规则外两类标注（不参与判定，跟着车走）：`fleet_risk`（可能大量流向网约车，如红旗 E-QM5、
比亚迪 e7）、`price_promo`（只有促销价）。

实测：discover 出 25 条 → **入池 5 款、拒绝 20 款**，拒绝的全是 `class_out`
（海豚-小型车、铂智3X/零跑B10/海狮05EV-紧凑型SUV）。全程无人工。

### `sync_cncap.py` + `cncap_scores.json` — 安全维的数据源到底能到哪

**调研结论（花了四轮探测）：**

| 数据源 | 能拿到什么 | 障碍 |
|---|---|---|
| C-NCAP 官网 `www.c-ncap.org.cn` | 综合得分率 + 三个分项 | v5.9 起首页 swiper **服务端渲染**可自动解析最新批次（`fetch_cncap.py`）；历史批次的分页查询走前端，未强行逆向 |
| C-IASI 官网 `www.ciasi.org.cn` | 五个分指数评级 | **v6.3 更新**：列表页表单公开调用 `GET /get_products_list.html`，返回 2018 年至今 **189 条全量评级表**；详情页给年份/规程/具体配置。但等级用数字图标、官网图例用 G/A/M/P 字母图、无文字对照，故不解码 |
| 公众号《C-NCAP测评结果数据》 | 42 款完整表（2024 至今） | 需要人工汇总，不是接口 |
| 媒体报道 | 单款成绩（含分项） | 只能逐个搜 |

最终累积 `cncap_scores.json` **57 款**，池内命中 **9 款**。
上面「无公开接口 / 只能半年人工汇总」是 v5.x 的旧结论，**v6.3 已被 C-IASI 的公开
全量成绩表推翻**（见 `ice_crash.py`）；余下的硬边界是 C-NCAP 历史批次的分页查询
与 C-IASI 等级图标无官方文字对照，这两件事仍不靠猜来补。

**评分原则修正（重要）**：没成绩 ≠ 不合格。有成绩的按得分率映射
（80%→7.0、90%→8.55、95%→9.33，斜率按「五星 83% / 五星+ 92%」这组公开门槛标定）；
**没成绩的不惩罚**，取同榜有成绩车型的中位数插补，只把 `safety_confidence` 标 `low`。
否则会变成「谁没被测谁吃亏」，而没被测往往是上市晚、销量小。

同榜有成绩车型 <3 时回退全池中位——否则「榜里只有一款有成绩」会让其余车全部被插补成那款的分
（中大型 SUV 榜只有 NX8 一款有成绩，踩过这个坑）。

**实测修正了 6 处人工分**，其中偏差最大的是：丰田 bZ5 人工 9.0 → 实测 86.7% 对应 8.0；
日产 NX8 人工 7.4 → 实测 92.2% 对应 8.9。人工看参数猜安全，偏差可以到 1.5 分。

### `sync_cell_brand.py` — 电芯核验表回填候选池（v6.x，2026-11-18）

**踩过的坑**：`cell_facts.json`（2026-11-16 核验表，37 款）note 明写「本表只补事实，
不改候选池快照」，结果 baseline/v3/v4 候选池的 `cell_brand` 长期停在 null。
39 款候选里有 **16 款**池内为空、事实库却有明确电芯（含丰田 bZ5 的弗迪刀片、
大众ID.AURA T6 的宁德时代、秦MAX EV 的弗迪二代刀片）。懂车帝/汽车之家/易车
三源都写弗迪，事实库 11-16 就核验入库，但快照一直没同步——用户当场指出才发现。

**为什么没被发现**：主链路 `score_rows()` 读预填 `scores`，不读 `cell_brand`，
所以打分结果没错（这些车的人工三电分本就按一线电芯给了，均分 8.2–8.7）；
但配置级比较榜 `spec_score.score_powertrain()` **读** `cell_brand`，会因此漏掉
一线电芯 +0.7。元数据不一致在打分榜里潜伏，到比较榜才暴露。

```bash
python3 sync_cell_brand.py --check cands.json                 # 只体检，报 MISSING/CONFLICT
python3 sync_cell_brand.py --apply cands.json                 # 回填空值+写来源，自动 .bak-cell
python3 sync_cell_brand.py --check cands.json --json audit.json
```

**规则**：单一事实源 = `cell_facts.json`。池空+事实有值=MISSING→回填并写
`cell_brand_src`（file/verified/source_url/tier）；两边都有值但不同=CONFLICT→
**只报告不自动覆盖**（如「上汽时代」vs「上汽时代(宁德合资)」，需人工判）。
**绝不改 scores/adjust**——回填后重跑 pipeline 报告逐字一致（39 款分数零改动）。
生成月度候选池后应先跑 `--check`，把 MISSING 清零再出榜。

**结果**：中型 SUV 榜 safety 恢复区分度（sd 0.26，8.0–9.0）；
其余三个榜仍落在 8.7（sd 0.00–0.07）——**这几个榜的 safety 权重实际失效，报告里会自动标出来。**
这不是打分规则的问题，是这些车确实没有第三方验证。

### `sync_to_repo.py` — skill 代码同步到 GitHub 仓库（强制流程，2026-11-19）

**双轨风险**：skill 实际运行目录 `~/.workbuddy/skills/omni-debate` 与 GitHub 发布副本
（`WorkBuddy/<session>/omni-debate-github`，origin=`Car-recommendation.git`）是两套目录。
改了 skill 代码却没同步，改动只活在 skill 本地，永远上不了 GitHub。
**用户明确要求：每次修改 skill 代码后，必须同步到 omni-debate 仓库并提交。**

作者本地维护用（**该脚本不随 skill 发布**，含本机仓库路径，故不在包内）：
`sync_to_repo.py --check` 先体检列出将同步哪些文件（只读），
`sync_to_repo.py --apply` 执行同步（白名单复制 + baseline 仓库端回填），
然后进仓库 `git add -A && git commit && git push`。
安装本 skill 的用户无需关心这一步——发布包里已经同步好了。

**同步规则**：白名单 = 仓库已有 `*.py`/`categories/*.json`/根 `*.md`（以 skill 为准）
+ 新增代码（quality_score/sync_cell_brand/sync_to_repo/car_ev_1218_quality.json）。
内容相同（仅行尾符差异）跳过；**仓库有、skill 无的文件不动**（防丢仓库独有内容，如 README）。
`baseline_car_ev.json` 不整文件覆盖，调 sync_cell_brand 在仓库端回填（保持 indent=2，避免 2400 行重排）。
`runs/` 和 `cache/` 由 .gitignore 排除，绝不入库。

**踩过的坑**：GitHub 推送常遇间歇性断连（Connection reset / 21s 超时）；环境变量里有
`HTTPS_PROXY=127.0.0.1:60770`，时通时断。推送失败时重试即可，不是代码问题。

**新增文件必须先加白名单（2026-11-20 踩坑）**：同步集只认"仓库已有文件 + NEW_FILES 白名单"。
新建的 `.py`/`.json` 若不在白名单，`--check` 会显示"内容相同、跳过"而**静默不同步**——
曾导致 crash_systems.py/evidence_consistency.py/safety_check.py/official_safety_scores.json
4 个新文件改了却上不了 GitHub。**每次新建代码文件，立即加进 sync_to_repo.py 的 NEW_FILES**，
加完再 `--check` 确认出现在"将复制"清单里。

**多会话并行改代码的纪律（2026-11-20，两个会话同时改本 skill）**：不强制单会话独占，
但必须遵守——① **提交前先 `git log --oneline -5` + `git status`**，确认没有别的会话已提交
（曾出现另一会话的 test commit 和版本号撞号）；② **版本号撞号要记录不抢号**，若发现
version 已被并行推进，以远端为准、在 commit message 写明"与另一会话撞号、内容无冲突"；
③ **共用定义放唯一真相源**（如 crash_systems.py），减少多处改动重叠；改完跑全套回归
确认双方改动兼容（曾验证双会话改动并存时 65/65 仍全绿）。

## 工信部备案预测与第三方视频证据（v5.5）

### `filings.py` — 工信部备案（每月自动，提前 4-10 周看到要上市的车）

```bash
python3 filings.py --list                       # 列近几批（公示/公告状态）
python3 filings.py                              # 最新已公告批次的纯电乘用车
python3 filings.py --spec-lib 14                # 累积 14 批 → runs/_filings/specs.json（车名→官方参数库）
python3 filings.py --json f.json --md f.md      # 结构化输出
```

链路：公告发布栏目（`miit-eidc.org.cn`，dataproxy.jsp 列表接口）→「予以公告」批次文章
→ 下载附件 .doc（OLE2）→ 纯 Python 解 Word 文本流（FIB→片段表，无需 antiword/LibreOffice）
→ 按「车辆型号」定位行（**不能按序号切**——批次间列不一致，410 批就没有序号列）
→ 《减免车辆购置税目录》给出 **通用名称 + 续航 + 电池 kWh + 整备质量**，全部官方一手。

| 数据 | 来源 | 状态 |
|---|---|---|
| 续航/电量/整备/通用名称 | 免购置税目录附件 | ✅ 自动，官方一手 |
| 车长（SUV/MPV） | 型号编码第 2-3 位（GB/T 9417，6 类=车长分米） | ✅ 自动，±0.05m |
| 车长（轿车）/价格/上市窗口/MPV 判定 | 新闻+官方口径 → `categories/filings_notes.json` | ⚠️ 人工+检索沉淀，每月增量 |
| 参数详情页 | `queryCpParamPage` | ❌ 滑块验证码，走不通 |

**实测第 411 批（2026-10-16）**：解析 92 行 → 乘用车 21 行 → 15 款待上市。
ID.ERA 5X 备案 10-16 → 上市 11-12（27 天），验证「备案→上市」间隔。

### `forecast.py` — 上榜预测（概率 + 名次区间，不进正式榜）

```bash
python3 forecast.py --filings filings.json --candidates cands_trend.json \
    --out forecast.json --md forecast.md
```

方法（全部可复现，固定随机种子）：

1. **坐标系**：把在售榜当参照系——品牌锚点 = 同品牌在榜车实际得分的销量加权均值；
2. **代理分** = 品牌锚点 + 续航/电量/尺寸/性价比的**榜内分位偏移** + 技术点加分（800V/激光雷达）；
3. **价格是闸门不是分项**：备案不报价格——有新闻预售价用之；否则电池容量回归；回归外推超出
   该带产品力上限（电量 >1.15×在售最大值）判「大概率超带」，砍 88% 概率且**不排名次**；
4. **概率**：logistic((代理分 − 榜内第10名分)/3.2) × 价格状态系数；T1 概率上限锁 0.35
   （无官方碰撞成绩的证据 C 硬约束）；
5. **名次区间**：±σ 蒙特卡洛重排（有新闻口径 σ=2.2，仅备案 σ=3.6），报 10–90% 区间。

**第 411 批实测结论**：真正能进 ≤18 万四榜的只有奕派 EP7（中大型轿车，名次 1（1–2），
进 Top10 概率 68%，但官方若 18.99 万起则超带）；极氪 7X/理想 i6/V8X/BMW 新 i3 全部价格带外；
ID.ERA 5X/ORA 5/菱智系列级别不符或 MPV。「备案 ≠ 入榜」，大部分备案车本来就与四榜口径无关。

### `thirdparty.py` — 第三方实测/拆车证据（懂车帝对撞、UP 主拆车、扭转刚度）

```bash
python3 thirdparty.py                            # 校验+列出证据库
# 证据库：categories/thirdparty_tests.json（人工核验后入库，不自动抓视频）
```

能进，但**必须过四条硬规则**（config `thirdparty_policy` 同步维护）：

| 规则 | 内容 |
|---|---|
| R1 可核验 | platform + url + date + claim 必填，链接必须能点开看到结论；缺一整条作废 |
| R2 分口径 | independent_test（平台自购/双车对撞，±1.0）> up_review（UP 主拆车，±0.5）> vendor_claim（厂商口径，±0.3） |
| R3 封顶 | 单车合计 ±1.5 分（10 分制），其中厂商口径合计 ±0.6——一个爆款视频改不动大局 |
| R4 相对性 | 双车对撞只证明「同条件下两车相对」，对方不在候选池时**只入库不评分** |

打分只走 `adjust` 通道（封顶后写进候选记录，报告出「第三方证据明细」表，逐条带链接，
被质疑可撤）。为什么不用直接改 scores：adjust 是已有机制，warehouse/报告/回测全链路行为一致。

**扭转刚度的定位**：与被动安全正相关（乘员舱完整性、碰撞能量管理路径），但**不是碰撞分数**——
C-IASI/C-NCAP 的结论只有官方规程能给出；且主流刚度数据是**厂商申报口径**
（极狐阿尔法 S5 51897、小米 SU7 51000 N·m/deg 均为厂方宣传），第三方实测稀缺。
所以刚度进 `vendor_claim` 口径，safety 上限 ±0.3，只作辅助佐证。
注意：第三方内容还会被记错——「Model Y 撞极狐S」实际是两次不同测试
（2021-07 懂车帝 汉EV vs 阿尔法S、2022-03 Model Y vs 阿尔法T），这正是 R1 要求链接的原因。

pipeline 集成：`pipeline.py --forecast forecast.json`（每榜尾部附「未上市预测区」，
summary.json 增 `forecast` 字段供月度对比与回测）；monthly.py 自动跑
备案→预测→出榜（失败不阻断正式榜）。

### `notes_tool.py` — 新闻层沉淀（把「检索→填字段→入库」变成流程）

```bash
python3 notes_tool.py --todo                      # 未沉淀车型+缺哪些字段+建议检索词
python3 notes_tool.py --export-template t.json    # 导出待填模板
python3 notes_tool.py --add t.json                # 校验通过才合并入库
python3 notes_tool.py --show                      # 覆盖率与 conf 分布
```

数据落在 `categories/filings_notes.json`（key=备案通用名称）。校验规则写死在工具里：
price_expect 必须是 [lo,hi] 数字区间、len_m ∈ 2.5–6.5、cls 必须是官方二级分类词表、
**conf=A/B 必须给 url**（A=官方口径，B=双源一致，C=单一来源或推断）——
没有来源的数据收不进去，这是防脏数据的第一道闸。

第 411 批实测：`--todo` 报 7 款待沉淀 → 检索补齐（理想i6 24.98–26.98 万官方价、
理想MEGA 50.98 万 Home 版、极氪009光辉 78.9 万 MPV、魏牌V8X 官方中大型SUV/插混 23.98 起、
捷途环游者 紧凑型SUV 10–14 万、αS5 2027 款 11-16 上市）→ `--add` 全过，覆盖 15/15。

### warehouse.py 扩展 — 备案/预测/证据入库存档，回测变成一条命令

```bash
python3 warehouse.py --ingest-filings filings.json      # 备案 → dim_filing
python3 warehouse.py --ingest-forecast forecast.json    # 预测 → fact_forecast
python3 warehouse.py --ingest-thirdparty --candidates cands.json   # 证据 → fact_thirdparty
python3 warehouse.py --ingest-board 202611 --candidates cands.json # 当期真实名次 → fact_board
python3 warehouse.py --backtest                          # 预测 vs 真实名次
```

四张「能力沉淀」表（与正式六表分开，`--build` 会先搬走再放回，不会清历史）：

| 表 | 内容 | 回答什么问题 |
|---|---|---|
| `dim_filing` | 每批备案车型（参数+级别推定+新闻口径） | 这台车什么时候报备的、报备时什么参数 |
| `fact_forecast` | 每批预测（概率/名次区间/价格状态） | 当时预测它第几 |
| `fact_board` | 每期真实名次快照 | 实际第几 |
| `fact_thirdparty` | 第三方证据（口径/delta/applied） | 哪些加分来自视频证据 |

`--backtest` 的命中定义写死在代码里（防回测时临时改口径）：Top10 命中=真实 ≤10 且
p_top10≥0.5 判「进」；名次命中=真实落 [p10,p90]；带外判定=预测带外且真实未入池。
月度任务的 ingest 必须放在 `--build` **之后**（build 重建正式表，放前面会被清掉）。

## 边界与合规

抓取频率克制（月度级、0.4s/页）、不绕任何验证，仅供个人研究与导购参考；
各站点条款归原方所有。候选池主体为人工维护，见 README 已知边界。

### `fetch_complaints.py` — 车质网投诉（L2 层自动化，borrowed from car_crawler）

```bash
python3 fetch_complaints.py --pages 80        # 增量抓最新页，按投诉编号去重累积
python3 fetch_complaints.py --skip-fetch      # 只用缓存聚合（离线）
```

来源：开源项目 car_crawler（MIT）的车质网爬虫逻辑，按本项目架构改造——
**feapder+MySQL+Redis+Chrome → requests+lxml+JSONL**，无重依赖。
原仓库 README 宣称的投诉/评级爬虫实际已被剥离（items 还在、spiders 被删），
其 aqsiqauto.com 数据源实测已下线，只有 12365auto.com 这条活的。

关键设计：
- **按投诉编号去重追加** JSONL 缓存——月度任务每次抓最新 80 页（≈2400 条），
  缓存随时间累积出投诉趋势，报告里写明缓存实际覆盖区间，不谎称"近90天"；
- **匹配候选池用车名归一**（filings.find_in），覆盖率如实报告（首跑 28/39）；
- **密度=投诉/万台**（除以 trend_sales）——投诉量没有分母就没有意义；
- 样本 <5 的车型不比较；**不改三档判定**，只作为 review 层参考证据。

集成：pipeline `--complaints complaints.json`（报告尾部"投诉反馈"参考区 +
summary.complaints 机读）；monthly 步骤 4c 抓取 → 5 出榜 → 6c 入库
（fact_complaints 月度快照，已在 build 保留清单 CAP_TABLES 里）。

### `backfill.py` — 历史回填与品牌画像（车型/主机厂长期权重）

```bash
python3 backfill.py --sales --from 202310        # 逐月纯电销量回填（37个月实测全成）
python3 backfill.py --sales --month 202610       # 月度任务增量补当月
python3 backfill.py --filings --since 2022-01    # 备案史回填
python3 backfill.py --brand-stats --asof 202610  # 品牌历史画像 → fact_brand_stats
```

**数据边界（实测，不伪造）**：
- 懂车帝逐月纯电销量：**2023-10 起真实可用**；2023-09 以前 API 静默回退到最新月
  （数据看起来完整其实是同一个月复制 N 遍）——用最新月签名比对检测并跳过。
  2022 年无免费逐车型源，宁缺毋滥。
- 工信部备案：**2023-12（第378批）起**可解析（更早批次附件里没有免税目录）。
  第378批是大目录批（844 行），首见日期近似为 2023-12。

**品牌历史画像**（fact_brand_stats，随月度任务刷新）：
- 字段：近6月纯电销量/份额、备案款数、**命中率=备案后6个月内月销≥300 的比例**；
- 品牌口径统一用懂车帝侧（备案名先匹配车系再取品牌——归类名 join 会静默断掉）；
- forecast.py 的品牌锚点升级为两级：在榜得分（65%）+ 历史画像（35%，仅历史样本
  ≥2 款才参与）——「当下产品力」与「这家厂过去报备的车有多少卖起来了」分开度量，
  混合方式写进每款车的依据里。实测：奕派EP7 品牌锚 78.9→81.3（东风奕派 5款
  100% 命中），进 Top10 概率 69%→72%。

### fetch_cncap.py / auto_levels.py / fetch_bili.py — 三条自动化通道（v5.7）

| 模块 | 干什么 | 状态 |
|---|---|---|
| `fetch_cncap.py` | C-NCAP 官网首页 swiper **服务端渲染可直接解析**（推翻旧结论），最新批次 17 款自动入库，与人工汇总表交叉验证 17/17 数值一致 | ✅ 自动 |
| `auto_levels.py` | OpenCLI autohome 的 level 字段（官方二级分类）自动写 levels.json；品牌词表随用随补，冲突不覆盖 | ✅ 自动（品牌映射待补全） |
| `fetch_bili.py` | B站搜索（免登录）→ 人工确认队列（标题/UP主/播放量排序）；CC 字幕有则抓；**AI 字幕需登录态，不做** | ✅ 机器初筛 + 人工确认 |

数据边界实测：C-IASI 结果列表为 JS 异步加载（需一层 XHR 逆向，暂缓）；
B站弹幕不取（情绪不是事实，本项目不碰弹幕/评论接口）；抖音 robots 全禁 + 需签名逆向，不做。
法律边界见 README 合规声明：不破解签名、不模拟登录态、不绕验证码、限速、
开源仓库不含抓取的数据本体。

## 被动安全气囊：事实列与受限修正（v5.8 · 方案 A）

### 结论先行：重要，但不进 safety 主分

侧气囊 / 头部气帘 / 前排中央（远端）气囊确实是重要的被动安全配置：

- NHTSA 对 73,228 例致命事故的回归：侧气帘＋躯干气囊让近侧碰撞死亡率下降
  **30.0%**（轿车，95% CI 22.8–36.6%），驾驶员单独算 32.8%；IIHS 口径为
  头部保护侧气囊降驾驶侧死亡率 37%（SUV 52%）、胸腹保护 26%。
- 侧碰约占事故 1/3，却占致命/重伤的 30–40%。
- Euro NCAP 2020 起把 far-side 纳入成人乘员保护，2023 版协议规定「中央气囊展开
  不正确」会取消 far-side 加分；C-NCAP 2024 版乘员保护里「侧面远端乘员保护」8 分、
  「保压气帘性能要求」3 分，所有车型都做侧碰＋柱碰。

**但气囊是拿分手段，得分率是结果。** 有官方成绩的车再按配置加分，等于同一份保护
效果算两遍；用 L3 配置表替代 L1 实测，也和 `thirdparty.py` 的 R5「厂商申报参数
只作辅助佐证」冲突。所以方案 A 是：**事实列 + 仅无成绩车的受限修正**。

### 实现

| 文件 | 作用 |
|---|---|
| `categories/airbags.json` | 按 trim 核验的三态配置库（true / false / **null=未核验**），每条带 source + url + date + quote；`policy` 写明 A1–A7 规则 |
| `airbags.py` | `validate / apply / coverage / report / --todo`；判「有无成绩」以 `cncap_scores.json` 为准 |
| `pipeline.py` | `airbags.apply()` 放在 `thirdparty.apply()` 之后、闸门之前；报告尾部出事实表；`--no-airbags` 供回测对照 |
| `leaderboard.py` | `top_table` 加「气囊 侧/帘/中」事实列；`review_report` 加提示级硬伤与未核验披露 |
| `build_html.py`（四榜产物脚本） | HTML 同步加列与口径说明 |

**关键技术修正**：初版方案写的是走 `thirdparty.adjust`，但 `adjust` 只平移加权总分，
MCDA 矩阵里 `scores.safety` 仍是常数，权重依旧失效。落地时改为直接改
`scores.safety`（仅无成绩车、|Δ| ≤ 0.3、写 `safety_note` + `safety_confidence=low`），
`apply()` 幂等，重复调用不叠加。

规则：A1 有官方成绩 → Δ=0（只展示）；A2 任一项 null → Δ=0 且不触发硬伤；
A3 侧气囊/气帘是基本盘，明确缺失才 −0.3；A4 前排中央（远端）气囊且无成绩 → +0.2；
A5 单车封顶 ±0.3；A6 **「N 气囊」不映射到布尔字段**（MG 07「全系 6 气囊」、
海豹06EV「7 vs 11 两源冲突」是库里刻意保留的两个反例）；A7 不改闸门与三档。

### 10 月链路实测验收（202610，39 款）

| 指标 | 结果 |
|---|---|
| 配置核验覆盖 | **10/39（25.6%）**，29 款列入待补清单 |
| 触发受限修正 | 小鹏MONA L05、马自达EZ-60（各 +0.2，均为无官方成绩且有前排中央气囊） |
| safety 维 sd | 0.348 / 0.379 / 0.368 / 0.442 → 0.348 / **0.365** / 0.368 / 0.442（几乎不变） |
| 加权名次 | 仅中型 SUV 榜 #7/#8 互换（深蓝S07 ↔ 马自达EZ-60），其余三榜不变 |
| 三档名单 | **0 变化**；Kendall τ 中型 SUV 0.809 → 0.816，其余不变 |
| 报告 | +7,038 字，含事实列、逐条依据（可点开）、待补清单、口径冲突披露 |

**诚实结论**：本期气囊字段的价值是**事实透明与可审计**，不是改变排名。
月度 202610 的 safety 维本来就有区分度（人工分 sd 0.35–0.44），
SKILL.md 里「四榜 safety 落在 8.7、sd 0.00」指的是跑过 `sync_cncap` 的
四榜独立产物，两者不是同一条链路——**不能拿气囊去补一个本链路并不存在的常数问题**。
若覆盖率上去后 sd 仍不改善，就说明这个价位段气囊确实无区分度，应只保留事实列、不给权重。

### 顺带查出的链路问题（v5.8 记录，v5.9 已修）

月度 `monthly.py` **曾没有调用 `sync_cncap.py`**：202610 的 39 款车 `safety_basis`
全为 `None`，`crash` 文本滞后（深蓝L06、零跑C11、长安启源A07、日产NX8 在
`cncap_scores.json` 里已有成绩，文本仍写「无官方成绩」），人工 safety 分与官方
得分率未对齐（如极狐阿尔法T5 92.7% 对应 9.0，候选池写 9.5）。
`airbags.py` 因此以成绩表判「有无成绩」，并把冲突车型在事实表里显式披露。
**v5.9 已把该步骤接入月度链路并全量重跑**，见下节。

---

## v5.9 · 安全分对齐、细分维度决策表与官网配置事实库

### 1 `sync_cncap` 接进月度链路

插入点在官方级别/价格对齐之后、`pipeline.py` 与 `warehouse.py --build` 之前，
让报告、候选池与数据库用同一份安全分。失败不阻断但会在 INDEX 披露。

**接入时发现的坑比漏接本身更严重**：`sync_cncap.py` 原本对无成绩车做纯中位插补，
39 款里只有 8 款有成绩，同榜有成绩车型 <3 时回退全池中位 → 31 款全部落到 8.7 附近，
safety 维 sd 从 0.35–0.44 掉到 0.07–0.26，**18% 权重实际失效**（SKILL.md:742 记的
「四榜 safety 落 8.7、sd 0.00」正是这个坑）。所以新增 `--fill-mode` 三档：

| 模式 | 中型车 | 中型SUV | 中大型车 | 中大型SUV | 评价 |
|---|---|---|---|---|---|
| 原人工分 | 0.348 | 0.379 | 0.368 | 0.442 | 有区分度但无证据支撑 |
| `median` | 0.077 | 0.260 | 0.070 | 0.112 | 旧行为，权重失效，仅作对照 |
| **`blend`（默认）** | **0.208** | **0.276** | **0.210** | **0.414** | 官方成绩覆盖 + 保留相对序 |
| `manual` | 0.359 | 0.305 | 0.462 | 0.736 | 只覆盖有成绩车，其余保留人工分 |

`blend` = α·原人工分 + (1−α)·中位插补，默认 α=0.5，可用 `--blend-alpha` 调；
无成绩车一律 `safety_basis=imputed_blend` / `manual_unverified` 且 `safety_confidence=low`，
`safety_note` 写清口径来源。三种模式都可回退对照。

### 2 202610 全量重跑验收

`monthly.py --force --month 202610` 端到端跑通（2.8 分钟），`sync_cncap` 步骤 ✓。
三个非阻断步骤 ✗（乘联会大盘、备案 JSON 导出、投诉抓取），出榜后复用同月归档的
`forecast.json` / `complaints.json` 补齐分区，`run.log` 有完整记录。

| 项目 | 结果 |
|---|---|
| C-NCAP 命中 | 8/39；31 款 blend 插补；1 款 `confirmed_none` |
| 官方成绩显著修正 | 7 款（|Δ|≥0.3）：深蓝L06 7.6→8.9、长安启源A07 7.2→8.4、日产NX8 7.4→8.9、极狐阿尔法T5 9.5→9.0、丰田bZ5 9.0→8.0、零跑C10 8.9→8.6、零跑C11 8.3→8.0 |
| 三档名单 | **0 变化** |
| 加权名次 | 同档组内互换：中型轿车 #3/#4/#6、中型SUV #4–#8、中大型轿车 #5–#7；中大型SUV 无变化 |
| Kendall τ | 0.789→0.744、0.816→0.799、0.924→0.814，0.926 不变（区分度压缩的代价） |
| 报告 | 58,979 字（v5.8 50,206），新增细分维度决策表、事实库节、15 条气囊逐条依据 |

**结论**：官方成绩改变了逐名排序，但没有改变「可直接买 / 按偏好取舍 / 证据不足」的
推荐结论——这正是三档 + 同档组设计的作用。τ 小幅下降属于把无证据人工分换成有证据
官方分的正常代价，比继续挂着失效权重可信。

### 3 气囊库扩容

`categories/airbags.json` 从 10 → **25 条（25/39，64.1%）**，新增 15 款按 trim 核验，
触发受限修正从 2 → 3 款（新增红旗天工05，+0.2）。补录纪律：

- 只采信**逐项参数表**或官网「全系标配」声明；`source/url/date/quote/source_type` 必填。
- 两源冲突或单源未标 trim → 记 `null` + `conflict`，**不推断、不加分**。实例：宋Ultra EV
  （智车派标「前排中间气囊 标配」但 trim 不明）、日产NX8（网上车市标无 vs 软文称标配）。
- 「N 气囊」营销总数一律不映射（别克E5「6 气囊」、零跑C16「11 气囊」）。
- 跨动力借用（A07/NX8/S07 用增程版配置）标 `series_partial` 并写明。
- 14 款仍待补，多为 2026-11 新上市或预测车型，官网逐配置页尚未放出；`airbags.py --todo` 有清单。

### 4 细分维度：为什么不做「有数据就加一点分」

用户提议把轮胎品牌、制动距离、维修经济性、售后态度、保养经济性、底盘用料、试驾感受、
麋鹿成绩、防爆胎、电耗、空间、NVH、智驾、主动安全、流畅度、马力做成加分项。
**不做**，五个失效模式：① 缺数据非随机（MNAR），实际在给营销曝光度打分；
② 厂商选择性披露，只加不减会奖励会宣传的品牌；③ 大半已被现有 7 维吸收，双计；
④ 覆盖率 10–30% 的维度方差由「有没有数据」决定，会劫持 MCDA 权重；
⑤ 制动/麋鹿跨媒体条件不统一，主观项无标准化口径。

替代方案落成 `spec_facts.py` + `categories/spec_facts.json`：

| 处置 | 项数 | 维度 |
|---|---|---|
| 已吸收·不加分 | 6 | 电耗、空间、智驾能力、主动安全、系统流畅度、马力 |
| 仅事实列 | 4 | 轮胎品牌型号、保养经济性、底盘用料、高速防爆胎 |
| 封顶通道·双向 | 2 | 刹车制动距离、麋鹿测试成绩 |
| 有官方口径·待准入 | 1 | 维修经济性（中保研 C-IASI 维修经济性指数） |
| 用户偏好滑杆 | 1 | NVH |
| 不采集 | 2 | 试驾感受、售后服务态度 |

**准入门槛 G1–G4**：官方标准化口径（G1）+ 覆盖率 ≥70%（G2）+ 按 trim 可核验来源（G3）
+ 加减双向对称（G4）。不达标**整条维度禁用**，而不是把缺失记 0 分。本期 3 个待准入
维度覆盖率均 0%，全部禁用（`blocked_dims` 写进 `summary.json`）。

**红线**：`SCORING_ENABLED=False`，`apply()` 只写 `c['spec_facts']`，并在返回前对
`scores/total/adjust/airbag_delta` 做前后深比较断言，改动评分字段直接抛 `AssertionError`。
事实库的公信力来自「它不可能改排名」。报告主链出精简决策表，逐条依据走
`spec_facts.py --report --out spec_facts.md`。

### 5 遗留问题（需单独授权再动）

**四榜独立 HTML 仍用旧纯中位插补**：`runs/car-ev-4boards/candidates_trend.json` 的
safety sd 为 0.000 / 0.050 / 0.075 / 0.260，18% 权重失效；而月度已改为 blend。
两者口径不同，**跨产物比较安全分无效**，HTML 里已明确披露。统一需从
`_v58before/candidates_aligned.json` 回填原始人工分再跑 blend，会改动四榜名次，
影响面比本次大，列为后续待办。HTML 第②条「中大型车全部没有官方碰撞成绩」也已
订正为「中大型两榜第一档全空」并说明日产NX8 有 92.2%。

---

## 6 具体配置推荐与配置阶梯（v6.0）

### 问题与结论

候选池只按一个 `trim`（带内推荐配置）打分，`cell_brand` 却是**车系级字符串**
（MG 07 写「清陶（半固态）/宁德（800V版）」）。这掩盖了买车时真正要做的选择：
同一车系不同配置的电池容量、电芯供应商、电压平台可能完全不同。
用户提出的两条线索都已查证：

| 车型 | 核实结果 | 出处 |
|---|---|---|
| MG 07 纯电 | 610/650 版 = 苏州清陶 67kWh 半固态 + 400V/3C；845 版 = 宁德时代 91kWh 三元锂 + 800V/5C。从评分配置 12.69 万到 845 版需 **+2.30 万** | 易车逐车款参数配置表 `car.m.yiche.com/mg07/peizhi/` |
| 腾势 N8 | 26.99→27.99 万 = **+1.00 万 / +24.36kWh / +203km CLTC**（20 英寸轮毂无其他选装；21 英寸为 970km），电芯均为弗迪 | 易车逐车款参数表 + 汽车之家车家号 26876988 |

**该列，但不能加分**。四个具体失效模式：层级错位（买哪款车 vs 买哪个版本）；
配置性价比是场景函数（同一笔 +1 万，媒体结论相反）；MNAR（有逐配置表的都是高曝光车型）；
「低配二线、高配宁德」概括不成立（零跑C10 是同配置多供应商混装，MG07 是技术路线分层）。
电池包由整车厂集成，公开数据不足以按电芯品牌拆分长期可靠性，
所以**不给供应商排一线/二线/三线，也不映射分数**，只记「是谁、什么路线、上市多久」。

### 实现

`categories/trims.json` + `trims.py`（架构复用 `spec_facts.py`）：

- 按 trim 记录 `price / battery_kwh / battery_type / cell_supplier / cltc_km / charge /
  motor_kw / key_configs / group / note`；每条 entry 带 url + quote + date + launch_date。
- `supplier_class` 只分类**证据形状**：`single_named` / `multi_batch` / `trim_split` /
  `new_tech_route` / `conflict` / `undisclosed`，不是供应商质量等级。
- 跃迁规则 J1–J4：同年款 `group` 内比较（J2b，避免跨年款换代误判）、
  `0 < Δprice ≤ 1.5 万`、`ΔkWh/万元 ≥10` 或 `ΔCLTCkm/万元 ≥100`、目标须在价格带内。
  超过 1.5 万但 ≤3.0 万判 `platform_step`，**只陈述事实与场景分支，不给单一建议配置**。
- 建议只从「评分配置」出发（R2b），建议配置必须比评分配置更贵；
  其它支线的加价差异只进事实表、不冒充建议；带外车型绝不参与名次。
- **红线** `SCORING_ENABLED=False`：apply() 只写 `c['trim_ladder']`，返回前对
  `scores/total/adjust/airbag_delta/price/price_guide/trim/cell_brand` 做深比较断言。
  「评分配置」保持原值，不替换、不改 `price_basis`、不动闸门。
- CLI：`--validate / --todo / --report --out`；主链 `--no-trims` 回测对照。
- pipeline 出主报告精简节 + `out/trims.md` 完整逐配置表；
  `leaderboard.top_table` 给有阶梯的车打 `<sup>配</sup>` 角标并加脚注；
  四榜 HTML 增专章（MG07 逐配置表、腾势N8 带外示例、供应商不排等级说明）。

### 本期验收（202610，只重跑出榜段）

- 在榜 4 款（MG 07 纯电 / 深蓝L06 / 零跑C10 / 日产N7）+ 带外示例 1 款（腾势N8），
  逐配置核验 **4/39（10.3%）**，识别 **7** 条跃迁，带内加价建议 **1** 款（深蓝L06：
  12.99→13.99 万 / +12.7kWh / +110km）。
- **三档名单 0 变化**；过闸 36 / 落榜 2 一致；四榜前 10 名次**完全一致**；
  Kendall τ 四榜全部不变（0.744 / 0.799 / 0.814 / 0.926）；报告 58,979 → 61,652 字。
- 顺带查出**日产 N7 评分配置口径冲突**：候选池 `trim.name`=625 Pro、`trim.price`=14.99 万，
  逐配置表中 625 Pro 为 13.99 万、14.99 万对应 625 Max 激光雷达版。
  价格会进闸门，故只披露不擅改，列下期人工核对。
- MG 07 的「91 度是同级别最大电池」只在**本库已核验的 4 款**中成立，
  未穷举全级别，故不宣称「市面最大」；清陶半固态上市约 3 个月只写「待市场验证」，
  不判劣、不扣分。
- 腾势 N8 同一笔 +1 万被车家号 26876988 称「六款中单位成本最划算」、
  车家号 26881166 写「只为续航加 1 万，我觉得没必要」。
  **媒体结论相反，正是配置性价比不能进默认权重的实证**，已写进 HTML 与报告。

### 待办（v6.1 已结清，详见下节）

- ~~补 35 款逐配置表~~ → v6.1 补到 **35/39（89.7%）**，剩 4 款在汽车之家车系库中查无此车系，明确留待补。
- ~~核对并修正日产 N7 的 `trim.name` / `trim.price` 错配~~ → v6.1 已在月度 202610 与四榜两份候选池同步修正，均留 `trim_fix` 记录。
- ~~四榜独立 HTML 安全分口径统一~~ → v6.1 已从 `candidates_4b.json` 原始人工分重建 `blend`，与月度口径一致。

## v6.1 · 逐配置自动补录、安全口径统一与燃油/混动四榜（2026-11-14）

### 1 逐配置表自动补录（`auto_trims.py`）

数据源为汽车之家车系参数页的 SSR 结构化矩阵 `var config = {...}`，按 `specid` 对齐
款型名 / 官方指导价 / 电池能量(kWh) / CLTC / 电池类型 / 电动机功率。新增 31 款自动条目，
加上 4 款人工核验条目，覆盖率 **4/39 → 35/39（89.7%）**，跃迁 7 → **36** 条，
带内加价建议 1 → **5** 款。

四条硬纪律（都是这次踩坑后补的）：

1. **按「能源类型」列只保留纯电动款型**。汽车之家把海豹06 / 秦L / 零跑C11 这类
   一车多动力车系的 DM-i、增程、纯电款型放在同一矩阵里；不过滤会把 9.68 万的 DM-i
   当成纯电入门配置，把跨动力差价算成「加价跃迁」（未过滤时假跃迁达 100 条）。
   现有 31 款自动条目共 **190 个款型，能源类型全部为「纯电动」，零残留**。
2. **名称只用精确匹配 + 6 条显式别名白名单**（`大众与众07 → 与众07` 等），
   不做模糊相似度匹配；别名/变体命中还须过「参数页起售价 vs 候选池指导价下界」一致性。
   6 款别名车型的价格区间与候选池逐项完全一致（如 Ray7 12.38–15.38、星海V6 8.99–12.49）。
   `ID.AURA ≠ ID.AURA T6`，同名前缀指向另一款车，宁可留待补也不硬配。
3. **自动条目证据密度显式降级**：`source_type=official_config_page_auto`、
   `auto_parsed=True`、`partial_fields` 列出电芯供应商 / 补能 / 上市时长 / 配置亮点等缺项，
   一律 `null`，不推断、不写叙述。`scored_trim_ref_basis` 记录锚定方式
   （`exact_price` / `latest_group_price_nearest` / `inband_lowest`）。
4. **评分配置先全表按价格精确匹配，再退到最新年款**。只在最新年款里找会把
   秦L EV 的 12.98 万错锚到 15.58 万新款，凭空造出 2 个假口径冲突。

`SCORING_ENABLED=False` 不变，`trims.apply()` 仍对 `scores/total/adjust/price/trim`
做前后深比较断言。**剩余 4 款待补**：秦MAX EV、大众ID.ERA 5S、大众ID.AURA T6、
小鹏MONA L05（汽车之家车系库查无对应车系，不猜）。
**剩余 4 款真实价格冲突**：极狐阿尔法T5、零跑C11、日产NX8、极狐阿尔法T7
（候选池 `trim.price` 与官方指导价差 0.08–0.5 万），按纪律只披露、不擅改闸门价格。

### 2 两件遗留事项结清

- **日产 N7**：`625 Pro` 原记 14.99 万，实际 13.99 万；14.99 万是「625 Max 激光雷达版」。
  月度 `candidates_aligned.json` 已在上一轮修正，本轮同步到四榜 `candidates_trend.json`：
  `trim.price=13.99`、`price_guide=[11.99,15.69]`、`price="11.99–15.69"`，两份都写
  `trim_fix`（before/after/依据/影响）。修复后冲突清单不再含 N7。
- **四榜安全分**：从 `candidates_4b.json` 保留的原始人工分重建
  `sync_cncap.py --fill-mode blend`（α=0.5），与月度 202610 一致；
  `safety_basis` = `cncap 8 / imputed_blend 30 / confirmed_none 1`。
  HTML 中「纯中位插补 sd 0.00–0.26、跨产物比较无效」的过时结论已全部改为现算：
  四榜 safety 标准差 **0.235 / 0.289 / 0.210 / 0.414**。

### 3 验收：排名零变化

- 月度与四榜：`in 39 / passed 36 / dropped 2` 不变，四榜前 10 **完全一致**，
  三档、审查结论全部一致；预测区与投诉区补回 `--forecast` / `--complaints` 后完整。
- 严格单变量对照：同一候选池跑 `--no-trims` 到独立目录，前十、三档、过闸/落榜
  **逐项相同**，证明 31 款新增事实条目没有进入任何分数。
- `out/trims.md` 由 22,167 → **122,013 字节**（35 款逐配置表 + 带外示例 + 待补清单）。

### 4 新增：202610 燃油/混动四榜（紧凑型/中型 × 轿车/SUV）

四个新模块：`ice_sales.py`（销量）、`build_ice_pool.py`（候选池）、
`ice_scoring.py`（代理分）、`ice_board.py`（报告），配置 `categories/car_ice_phev.json`。

- **动力分类互斥完备**：懂车帝 `new_energy_type` `0=燃油/HEV`、`1=纯电`、`2=插混`、
  `3=增程`、`4=一车多动力未拆车系名`。入池 `0+2+3`（388 款），排除 `1+4`（278 款）；
  `ne=4` 销量含纯电，硬算进来就是「拿车系合计当单一动力销量」。实测五类两两无交集、
  合计 666 款且销量合计等于「全部」榜。
- **候选池 122 款**：汽车之家 `grade/carhtml/A-Z.html` 5372 车系索引 + 车系页
  `levelName/energytype` + 口碑 7 维/PPH + 6 个月销量趋势。名称未匹配 30 款、
  级别拒绝 68 款，一律不猜。
- **6 维代理分**：`market .24 / value .18 / space .16 / control .16 / cabin .14 / quality .12`，
  **不设 safety 维**（无官方碰撞成绩）；`adas` 覆盖率仅 8.2% 被整条禁用而非记 0。
  口碑样本 <30 的维度按同榜中位插补并标 `low`；销量/价格用全池分位秩而非 log min-max。
  加权分**不可与纯电四榜直接比较**。
- **四榜第一档全部为空是设计**：无官方碰撞证据，按判档纪律不给「可直接推荐」。
- **5 款因未取得可核验官方指导价落榜**：宋Pro DM、银河M7、星瑞L PLUS、大众CC、
  沃尔沃XC70。懂车帝榜单价格与经销商价同值，不替代指导价；落榜 ≠ 车不好。

前十见 `runs/car-ice-4boards/INDEX.md`；逐车逐维公式见 `scoring_audit.md`。

## v6.2 · 按外部评审做的披露强化（2026-11-14，基准名次零变化）

用户拿一份外部模型对燃油/混动四榜的评审来问「怎么看」。逐条对着磁盘产物核过后：
**8 条批评里 6 条成立、2 条读错**，还顺带查出我们自己的一个文案 bug。

**成立**：评分公式与权重没进正文（只在 `scoring_audit.md`）；插补比例高
（space/control/cabin/quality 真实覆盖 47/122 = 38.5%，61.5% 靠中位插补）；
销量在「入池取前 220」和「market 维」双重出现；指导价不是成交价；
口碑是 L4 自选择样本；分动力榜不足 10 款却写「前十」；0.1 分过度精确；无安全维。

**读错的两条**：
1. 「总榜第 9、混动分榜第 4 说明口径不同」——分动力视图**用的是同一套加权分**，
   只是参照系从全榜换成同动力子集，代码里就是 `rows` 的子集切片，没有第二套权重。
2. 「同档组 bootstrap」被当成已给名次区间——此前只给**组号**，没给逐车区间数字；
   现在补上了。

**顺带查出的真 bug**：配置 `dimensions[market].gap` 写「权重上限 22%」，
实配权重却是 **0.24**，旧版报告照抄 22%，与代码不符。v6.2 统一按 24% 披露，
并把配置文案改为自洽说明；**是否下调到 22% 会改动名次，列为待决策项，本期未动**。

**落地（全部是披露层，不改任何分数与名次）**：

- `report.md` 新增「复现用：权重、公式与归一化」节：6 维权重表（含真实覆盖/插补数）、
  `Σ(维分×权重)×10` 归一化说明、9 条逐维公式与判定规则全部进正文。
- 表格新增 **bootstrap 名次区间(95%)** 列（`leaderboard.top_table` 现在返回 `ci`），
  并注明「0.1 分只是机器排序需要，同档组内不可辨」。
- 分动力视图标题改为 **「分列（共 N 款）」**，N<10 时明说尾部无统计意义。
  实测：紧凑轿混动 2 款、紧凑SUV 混动 8 款、中型轿混动 9 款、中型SUV 混动 7 款。
- 口径节明确定性：**市场热度 / 指导价 / L4 口碑综合参考**，不是产品力榜、安全榜、
  成交价建议；边界节补「指导价 ≠ 成交价，终端优惠大的车价值维会失真」。
- 新增 `ice_sensitivity.py` → `sensitivity.md` / `.json`：三情景压力测试
  （A 基准 / B 去掉 market 维 / C 禁用插补维只用真实数据维），
  输出全榜 Kendall τ、前十交集、基准前十逐车位移、薄证据车清单。

**量化结论（比口头承认硬）**：

| 榜单 | τ(A,B) 去销量 | 前十交集 A∩B | τ(A,C) 禁插补 | 前十交集 A∩C | 前十薄证据车 |
|---|---|---|---|---|---|
| 紧凑型轿车 | 0.238 | 4/10 | 0.771 | 9/10 | 2 |
| 紧凑型SUV | 0.446 | 4/10 | 0.710 | 9/10 | 3 |
| 中型轿车 | 0.306 | 4/10 | 0.779 | 8/10 | 1 |
| 中型SUV | 0.313 | 6/10 | 0.667 | 8/10 | 4 |

→ **销量才是主要驱动**：拿掉 market 维后名次几乎重排（τ 0.24–0.45、前十只剩 4–6 款），
而且这还只量化了评分那一处，入池漏斗的影响没算进去。
→ **插补对前十影响有限、对全榜排序影响显著**：禁插补后前十仍留 8–9 款，
但 τ 只有 0.67–0.78；薄证据进前十的车（卡罗拉、BJ30、途岳、锋兰达、银河星耀7、
捷途X70、宋Ultra DM、别克至境E7、奔驰GLC）名次最不能当真。

**v6.2 遗留待决策项 → v6.3 已逐条处理**（详见下节）。

## v6.3 · L1 碰撞证据层与入池漏斗解耦（2026-11-14）

按「把该做的都做了」的授权，把上一轮列的四件事逐条落地；其中两件**会改名次**，
均已量化披露，并归档 v6.2 产物（`*_v62before.*`）供对照。

### 1 新建 `ice_crash.py`：L1 官方碰撞证据层

推翻旧结论「C-NCAP / C-IASI 无公开接口」：C-IASI 列表页
`/products/list-492.html` 的表单公开调用 `GET /get_products_list.html`，
返回 **2018 年至今 189 条全量评级表**（免登录、无签名）；详情页
`/products/show-<id>.html` 给测评年份、规程版本与具体测试配置。
加上已核验的 `cncap_scores.json`（57 款），做受控匹配：

- 匹配只允许三种：精确归一化、`品牌+车型` 拼接、显式白名单别名；
  **不做子串模糊匹配**，`途观L Pro ≠ 途观L`、`海狮07 ≠ 海狮06DM`。
- 命中 **50/187**，其中 C-IASI 48 / C-NCAP 5 / 双源 3。
- 年款新鲜度：详情页明确「20XX款」优先（high），否则用列表页发布年份兜底
  （medium）。≥2023 或 C-NCAP 2024 至今才算**近期 L1 证据**：**16 款**，
  另 34 款只有历史年款，只进事实列、不外推到现款。
- **C-IASI 等级图标未解码**：列表页用 `level1/1..4.png`，详情页图例用
  `G+/G/A/M/P.png`，官网无文字对照；只把「存在官方测评」当事实，不证明好坏。

**为什么不做第 7 个评分维**：近期证据覆盖率 16/187 = **8.6%**，远低于 G2(≥70%)；
把「有没有被抽去测过」写进加权分会奖励选择性披露，且 L1 与 L4 不同质。
故 `SCORING_ENABLED=False`，只作事实列与判档证据，`scores/total/adjust` 不变。

### 2 更正一个实质性错误解释

v6.1/v6.2 报告写「第一档全空是因为没有官方碰撞证据」——**这是错的**。
实际 `tiering.t1_evidence=["A"]`，而燃油/混动榜的 A 沿用纯电榜定义
「口碑样本 ≥100」，池里几乎没有车达标，所以第一档必然全空。
按证据栈纪律补上 L1 路径后：`ice_scoring._evidence()` 允许
「L4 样本 ≥100」或「L1 近期官方测评」两条路达到 A。
结果中型轿车出现 **1 款第一档（迈腾）**，其余 L1 车仍被
「算法命中 ≥80% / 全情景命中 ≥60%」的稳定性门槛留在第二档——**不放水填第一档**。

### 3 新建 `ice_funnel.py`：解耦入池销量漏斗

`build_ice_pool.py --top 0` 全量核验 388 款 → **187 款**正式池（原 122 款）。
四情景 A（旧 220 截断）/ D（全量）/ E（全量去 market）/ F（全量 market 22%）：

| 榜单 | 过闸 A→D | τ(A,D) | 前十交集 | 新进前十 | D∩F(22%) |
|---|---|---|---|---|---|
| 紧凑型轿车 | 21→34 | 0.829 | 8/10 | 宝来、捷达VA3 | 10/10 |
| 紧凑型SUV | 41→61 | 0.868 | 8/10 | 哈弗H6、捷达VS5 | 9/10 |
| 中型轿车 | 30→36 | 0.867 | 9/10 | 天籁 | 10/10 |
| 中型SUV | 25→47 | 0.867 | 8/10 | 宋L DM、豹5 | 10/10 |

→ 旧漏斗**确实漏掉了能进前十的车系**，故正式榜切到 `ice_pool_full.json`。
→ market 22% vs 24% 前十交集 9–10/10，注记争议不改变结论，**默认权重不动**。

### 4 必须同时披露的代价

全量池解耦了漏斗，但把低销量车纳入后，space/control/cabin/quality
真实覆盖从 47/122（38.5%）降到 **50/187（26.7%）**，插补比例升到 **73.3%**；
`adas` 覆盖率 11/187 = 5.9%（原 8.2%）。三情景敏感性基于新池重算：
去 market 维前十只剩 **3–6/10**，禁插补维 τ 0.655–0.768。
**这是「更全的候选池」与「更薄的证据密度」之间的取舍**，不是净改进；报告已写明。

### 5 验收

- 四榜前十与漏斗情景 D **逐项一致**；三档仅因 L1 证据路径变化（预期内）。
- 报告/HTML 表格新增「官方碰撞」列（L1 / 史 / —），附 `crash_evidence.md`。
- 过时文案清零：成绩未入库、8.2%、第一档全空的错误解释、v6.1 版本号等均为 0 处。
- 新产物：`funnel_sensitivity.md/.json`、`crash_evidence.md`、
  `categories/ice_crash.json`、`ice_pool_full.json`。
- 纯电四榜与月度 202610 链路未动，未回归。

### 6 仍未做（有明确理由）

- **不引入终端成交价**：口径不可核、随时变，违反「促销价不进闸门」纪律。
- **不把 C-IASI 等级换算成分数**：图标映射无官方文字对照，解码即猜测。
- **仍非全市场穷举**：数据源是销量榜，汽车之家 5372 车系中无销量记录的车不在池内。
- 名称未匹配 55 款、级别拒绝 146 款（全量池）均逐条披露、不猜。

---

## 用户偏好档：电池优先（参考，不改默认分）

默认榜用 7 维 MCDA；但有些用户的核心诉求是「电池品牌 > 同价位度数 > 销量」。本 skill 提供两个对照版，**都不改默认榜**。

### 首选：`ev_cell_board.py`（严格版，2026-11-16 新增，结论以它为准）

```bash
python3 build_cell_facts.py                      # 生成 categories/cell_facts.json（37 款逐车核验）
python3 ev_cell_board.py --candidates <池.json> --out <目录>
# → cell_board.json（四榜 + 逐车档位/桶内位次/销量判定）
# → sensitivity_selfmade.json（自研并入一线的名次对照）
```

**三条规则 → 代码**：
1. **电芯**：分层不打分。1=宁德系（**含时代一汽/时代长安/上汽时代等合资**）/弗迪；2=二线（国轩·中创新航·亿纬·欣旺达·蜂巢·因湃·正力）或宁德混装、厂商自研；3=新路线（半固态）。tier 是第一顺位。
2. **同价位度数**：不跨价位比绝对 kWh，用 **kWh/万元** 作二级键。⚠️ 别用「2 万分桶内的位次分位」当排序键——独占一桶的车会拿到 1/1 满分，首版就因此把 54kWh/10.53 万的贝塔 S3 顶到中型轿车第 1。
3. **销量**：**硬门槛**，月销 <1000 移出推荐区单列「销量不足区」，1000–1500 标偏低。不用扣分——扣分只是降几名，低销量车仍在推荐位。

**为什么另起脚本**：`ev_battery_board.py` 把 `cell_brand` 未披露的车一律压第 3 档 → 中型轿车 13 款里 11 款被压，**等于拿取证盲区罚车**（与气囊闸门同一个错误）。严格版先补事实：`categories/cell_facts.json` 37 条，每条带 url+quote+核验日期，记的是**评分配置对应的那一款电池**（同车系换配置可能换电芯）。

**实测（2026-10 数据月）**：四榜第 1 = 中型轿车 极狐贝塔S3（宁德 54kWh/10.53万，但绝对度数最低）、中大型轿车 长安启源A07（宁德 77.94）、中型SUV 大众ID.AURA T6（宁德·时代一汽 77.5/14.99，月销12,032）、中大型SUV 极狐阿尔法T7（宁德 84.8）。关键发现：① **MG 07 评分配置 610 版是上汽清陶半固态 67kWh**，只有 845 版才是宁德 91kWh——按「宁德/弗迪为佳」它落第 3 档；② 度数冠军大众与众09（91.96kWh 宁德）月销 69 台被门槛拦下；③ **自研是否算一线只影响中型轿车**：若算，风云A9 从第 8 到第 1（70.11kWh、5.40/万、月销5,859），其余三榜几乎不动。

### 旧版：`ev_battery_board.py`（保留，仅供追溯）

```bash
python3 ev_battery_board.py --candidates <候选池.json> --out <目录>
# → 电池优先榜.html + battery_rank.json（逐车与现榜名次对照 + Kendall τ）
```

**规则（字典序）**：电池档 1（弗迪 / 宁德单源）> 2（二线 / 宁德混装 / **厂商自研**：吉利神盾·金砖、广汽弹匣；2026 LFP 6C/12C 已追平）> 3（未披露 / 新路线）；档内按「度数分位×0.7 + 销量分位×0.3 − 低销量扣分」；月销 &lt;800 扣 2.5、&lt;1500 扣 1。若你把自研视作一线，把脚本 `tier_of` 的自研分支改成 `return 1, ...` 即可。

**数据边界（必读）**：① 只有电池品牌，**分不出宁德整包 / 宁德电芯**，统一「宁德系」、单源第 1 档、混装第 2 档；② `cell_brand` 未披露车在旧版被压第 3 档（**不代表电池差，只代表没核到**，严格版已补齐 37 款）；③ trims 少量无度数 → 标「缺」。配置见 `categories/ev_battery_profile.json`。

实测（2026-11 EV 池）：与现榜 Kendall τ = 中型轿车 0.41 / 中型SUV 0.38 / 中大型轿车 **-0.33** / 中大型SUV 0.47——中大型轿车分歧最大（电池逻辑更看重宁德/大电池；吉利神盾自研在第 2 档、低销量车被下调）。

**2026 认知更新**：LFP 电芯的 6C/12C 已在弗迪 / 宁德 / 国轩 / 吉利等多家量产，**纸面差距缩小**；真正差距在整包能量密度、CTB/CTP、BMS、热管理、低温、循环寿命、量产一致性与售后/超充生态。**质保是风险缓释、不是质量保证**（终身质保多限首任车主 / 全程 4S / 年里程 &lt;3 万 / 过户失效；正常老化不在保；8 年健康度均值 &gt;80% 但出保车实测 65–75%）——定车前核对：年限 / 能否过户 / 衰减阈值 / 是否保正常衰减。**可维修性**：集成度越高越难修（传统三级 &gt; CTP &gt; CTB &gt; CTC）；二代刀片实测需磨/切/锤 + 整包冷冻约 40 小时、环氧灌封，电芯级现场几乎不可修；2026-04「能修不换」让约 75% 局部故障可模组级修（LFP 限价 ~150 元/kWh），但一体化粘接的 CTB/CTC 仍难。选购核对：集成方式 + 长质保 + 车损险。





## 数据源扩展（2026-11-17 核实落地，不进分）

### C-IASI 官方文字等级解码 `ciasi_text_decode.py`

手册旧标「等级图标未解码」是误解：C-IASI 结果发布稿（`ciasi.org.cn/news/show-N.html`）是**纯文字** G/G+/A/M/P。零依赖解码 5 分指数；「耐撞性与维修经济性」=官方可维修性事实列。
```bash
python3 ciasi_text_decode.py --start 100 --end 130 --out <目录>/ciasi_decoded.json
python3 ciasi_text_decode.py --selftest
```
实测 8 篇/30 款；早期稿只给分布不给逐车对应时标 `aggregate_only`（不猜）。只作事实列，**不进加权分**。

### swoiow/autohome 参数补全探针 `swoiow_probe.py`

GitHub `swoiow/autohome`（Apache-2.0，月度，1666 款 CSV）含指导价/kWh/CLTC/风阻/轴距/质保政策。
```bash
git clone --depth 1 --filter=blob:none --sparse https://github.com/swoiow/autohome.git
cd autohome && git sparse-checkout set data && cd ..
python3 swoiow_probe.py --data-dir autohome/data --candidates <候选池.json> --out <目录>/swoiow_suggest.json
python3 swoiow_probe.py --selftest
```
实测严格命中 16/39。**只出补全建议、不写回候选池、不进分**；严格精确匹配（子串会把 EZ-60 误配 EZ-6、S5 误配 S，自检已覆盖）。

### 暂不自动化（已核实可用，留接口）

- **中汽流通协会×精真估保值率月报**：EV 3 年保值率 45.1%（2026-10），网页/PDF 无车型级 API，人工摘关注车型即可。
- **samrdprc.org.cn 召回公告**：静态 HTML，召回编号/车型/数量/缺陷结构化；召回≠质量差，先定「只披露不评分」呈现规则再接。

完整方案见 `车辆榜单引擎_架构与运维手册.md` §3.2–3.3。

---

## v7.0 · 统一榜单：打分榜唯一主榜，事实榜降级参考列，价位段子榜（2026-11-19）

### 为什么要改
旧链路并行产出「事实榜」（只按 kWh/万元、电耗排序）和「打分榜」，用户混淆「哪个是排行」。
用户明确决定：**以后所有排行、比较都以打分榜为准**。事实指标不再单独成榜。

### 三条硬规则（已固化进 `board_v2.py` + `categories/rank_v2_policy.json`）
1. **打分榜是唯一权威名次来源**。主榜 = 8 维加权（含品控 10%），直接复用 `leaderboard.score_rows`，
   不重写打分逻辑。所有「排行 / 前十 / 同级对比 / 比较榜」都从这里取名次。
2. **事实指标降级为「参考·不计分」列**。kWh、kWh/万元、电耗、CLTC 续航、电芯、800V、月销
   贴在主榜每行，只帮助理解，**绝不参与加权**。自检已验证：改某车的 kWh 不改变任何名次。
3. **价位差异不设硬排除**。同级别（官方二级分类）已控制级别混淆变量；价格对竞争力的影响
   由「性价比」维度（11.7%）吸收——贵车性价比低但产品力维度占优，便宜车反之，混排本身
   就是有意义的综合比较。「价格相差太多就不排名」会丢掉「多花钱值不值」的信息，且价位
   边界会造成临界车跳榜的假精确，故不采用。

### 价位段子榜：回答「我这个预算买哪款最好」
`board_v2.py` 把**同一份主榜分数**按预算分组（默认 18/28 万切三段，或用价格分位自动切），
生成价位段子榜。关键纪律：**子榜复用主榜分数，不重新打分、不改权重**——它只是展示层的分组视图。
- 「综合谁最强 / 给前十」→ 看全价位主榜
- 「我这个预算买哪个」→ 看价位段子榜
- 「某车电池/电耗到底怎样」→ 看主榜的参考列（不计分）

### 用法
```bash
python3 board_v2.py --selftest
python3 board_v2.py --candidates <带scores候选池.json> --facts <事实参数.json> \
    --out <目录>
# → board_v2_data.json（主榜+子榜+参考列） + board_v2.md
```
策略与字段映射在 `categories/rank_v2_policy.json`（价位段边界、哪些是参考列、三榜问答指南），
改口径改配置即可，不动代码。`--facts` 可选：不传则用候选池自带的事实字段。

### 验收（2026-11-19，中型纯电 SUV 全价位 12 款）
主榜：极氪7X 86.4 / 阿维塔07 83.3 / 腾势N7 80.3 … Model Y 76.8（第8）。
Kendall τ=0.913（10 算法×6 权重情景）。事实榜的零跑C11 kWh/万元第1、Model Y 电耗第1，
在 v2 里都只是参考列——综合名次分别是第9、第8，印证「参考不计分」。

---

## v8.0 · 总候选池：单一事实源，根治「临时拼池漏车」（2026-11-19）

### 为什么要改（T6 遗漏的直接教训）
出「中型纯电 SUV 全价位打分榜」时，候选池沿用了上次「Model Y 同级事实榜」的 12 款
（偏 15–34 万），T6(14.99万)、极狐T5、零跑C10、深蓝S07、bZ5 等整批 12–18 万车
**从未进池**——不是算错分，是根本没被纳入。根因：没有总池，每次出榜临时从某个 run
目录捡一份候选池，覆盖全靠人记得。v8.0 把散落各 run 的候选池收敛成一份持久化总池。

### 核心：一份总池 `categories/master_pool.json`
所有搜索过/采集过/发现过的车都进池（含被排除的车，excluded 也留档），每款车带：
- **可检索标签**：级别(cls)/动力(power)/车身(body)/品牌/价格(price_wan)/价位段/证据等级/生命周期；
- **完整数据**：8 维 scores + adjust + 事实(kWh/电耗/续航/电芯) + 原始 candidate 记录（可直接喂 board_v2）；
- **来源溯源**：provenance 数组记录该车从哪些池吸收而来；
- **状态**：ready（数据齐）/ needs_rescore（缺维待重采集）/ needs_data（销量榜发现、无任何打分）。

### ⛔ 出榜数据闭环：联网优先 → 本地缓存核验 → 缺口补采，**全程不问用户**（2026-11-20 用户强制）

用户原话（两条，按顺序）：
- 「排榜的时候不需要问用户是不是要联网，你先用查出来一共哪些需要车型配置，然后到总池，没有再搜集车型，按照这一套逻辑就可以不需要问用户了，要减少用户操作。」
- 「逻辑都要直接改，第一步是先找所有信息，不要直接查本地表，这样一直会是旧数据。」（2026-11-20 盘点申报车后纠正）

**⛔ 顺序铁律（不许颠倒）**：先联网找全量最新信息 → 本地表只作为缓存/历史沉淀做交叉核验，
**绝不先读本地表当事实源**（先读本地表会一直停在旧数据，用户明确禁止）。本地表的作用只有三个：
① 复用已核验的稳定字段（8维分/参考车关系）；② 交叉校验联网结果；③ 联网失败时的兜底并显著标注"缓存·待核"。

**接到排行/盘点指令后，一律按这套逻辑自动走完，不许用 AskUserQuestion 问"要不要联网/要不要补采/怎么处理缺数据/选哪种能源"。**
（榜型与能源形式由 `energy_router.py::classify(query)` 自动判定，未提能源默认 B 类全 5 种含油混，禁止问用户——见第零步。仅"级别和价位完全没给、无法定位是什么榜"时才需用户补充基本信息，但那也不涉及能源选择。）

闭环五步：
1. **联网找全量最新信息（第一步，强制）**：按提问的级别/价位/能源/时效口径，用 WebSearch/WebFetch 搜当期
   销量榜、权威在售/待上市清单、价格、配置、上市状态，**先拿到一份完整的最新事实清单**。
   工具：`discover.py`（懂车帝四口径销量榜）或网搜采集；跨能源榜还要按官方二级分类过滤（级别闸门）。
   "截止今天有哪些X"这类问题尤其必须先联网，本地表此刻只当背景。
2. **本地表交叉核验（第二步，缓存定位）**：把第1步的联网清单与 `master_pool.py --query` / `filings_notes.json`
   / `categories/*.json` 比对：
   - 联网+池内都有 → 复用池内已核验的**稳定字段**（8维分/参考车关系/逐配置），联网结果更新时效字段（销量/价格/上市状态）；
   - 联网有、池内缺 → 进入第3步补采建档案；
   - 池内有、联网查不到 → 以联网为准，池内该条标"缓存·联网未证实"，**不静默当最新事实用**。
3. **缺口自动补采（不问用户）**：对第2步的缺口，用 WebSearch/WebFetch（汽车之家/易车/懂车帝/C-NCAP）
   自动采集：官方级别、指导价、逐配置（配置名/价/电量/电芯/电压/快充/续航/关键配置）、8维分（每维要有证据，
   查不到给中性 7.0 并标注，不编造）、销量。**这一步是系统默认行为，不需要请示。**
4. **回池/落地**：`master_pool.py --ingest <补采池.json>` 幂等吸收（补空缺、记来源、不覆盖）；
   或把补采结果落到本次出榜的候选池。跨能源榜写入 `runs/` 采集文件供 `build_energy_compare.py` 消费。
5. **出榜**：A 类走 `pipeline.py`/`board_v2.py`；B 类走 `build_energy_compare.py`（+`ev_range_model.py` 环境分）。
   出榜前仍过「每车逐配置数据」铁律与「候选池收全」铁律；缺数据的车标注原因移到未入榜名单，不编造。

**技术门禁**：`filings_board.py` 的 `load_filings(strict_fresh=True)` 与榜单生成器会在申报库 `updated` 距今
超过阈值时打印"缓存过期·须先联网"告警，并在页面显著标注，防止旧状态被静默当成"截止今天"的事实。

**减少用户操作的具体表现**：
- 不问"要不要联网补采" → 默认补，且网搜是内置能力（WebSearch/WebFetch 或内建 `research/researcher.py`）。
- 不问"缺数据怎么办" → 默认按铁律：补采或标注待核移出，不塞假数据。
- 不问"交付 Markdown 还是 HTML" → 默认两者都出。
- 不问"排序依据" → 由第零步榜型决定（A 产品力；B 产品力+环境分）。
- 榜型/能源形式一律由 energy_router 自动判定，不问用户（A/B 类、能源选择都不问）；仅"级别+价位完全没给"无法定位榜单时才问基本信息。

### 逻辑变更全池重算（用户要求的「新加逻辑要把旧池重新更新一遍」）
打分配置（维度键/权重/排除维）算 SHA256 指纹存进池 meta。配置一变指纹就变，
`--rescore --config <新配置> [--apply]` 对全池：
- 8 维齐全的车 → 用新权重机械重算总分，打新指纹；
- 缺维的车（如新增品控维后旧池 17 款缺 quality）→ 标 needs_rescore，**不填假分**，列出缺哪些维；
- `--audit` 随时体检：指纹是否一致、缺级别/价格、待采集清单、价位段覆盖（防「全价位榜却没低价车」复发）。

### 关键纪律（继承既有红线）
- **官方安全分权威覆盖**：safety 冲突按证据分级裁决——`safety_basis=cncap/ciasi` 的对齐分(rank3)
  覆盖仅 crash 文本声称有成绩的旧人工分(rank2)，后者覆盖无依据分(rank0/1)。
  实测修正深蓝L06 7.6→8.9、极狐T5 9.5→9.0、bZ5 9.0→8.0、NX8 7.4→8.9（与 v5.9 一致）。
- **不猜标签/分数**：级别来自官方字段，缺失留空并在 audit 报出；重算不填假分。
- **幂等**：多次 ingest 只补空缺，冲突逐条报出供人工裁决；顶层 scores/adjust 与 candidate 同步。

### 用法
```bash
python3 master_pool.py --selftest
python3 master_pool.py --init
python3 master_pool.py --ingest 池1.json 池2.json ...
python3 master_pool.py --query --cls 中型SUV --power BEV --out subset.json
python3 master_pool.py --audit
python3 master_pool.py --stats --by cls
python3 master_pool.py --rescore --config <新配置> --apply
python3 board_v2.py --candidates subset.json --out <出榜目录>   # 接回 v7 出榜
```

### 验收（2026-11-19，吸收 6 个来源池）
67 款入池：中型SUV 22 / 中型车 15 / 紧凑SUV 9 / 紧凑车 8 / 中大型车 7 / 中大型SUV 6。
检索「中型SUV+纯电」命中 22 款（旧临时池仅 12 款），T6 在列——遗漏根治。
品控权重 10%→15% 指纹 cdd863c069c5→9d0f36579a16，50 款可重算、17 款缺 quality 被标记。
验证报告：`runs/car_midsuv_ev_202610/总候选池验证报告.md`。

---

## 统一证据采集（内建，原 research-collector 能力已整合 v9.7.38）

本引擎**自带**证据采集方法论与脚本（`research/`），不再依赖独立 skill。采集分三层，产出 `research.json`（证据）与候选池（车型）两份**职责分离**的产物。

### 第 0 层：资料源规划（先规划再检索）
不上去就照固定平台抓。先产出 source_plan，过目后再检索：①静态垂类画像（汽车画像必查 **C-NCAP / 中保研碰撞（注明年款+规程版本）、车质网投诉排行与典型故障、缺陷产品召回**）；②动态平台发现（陌生领域先搜权威评测/监管渠道，硬编码列表会过期）；③判断门（确认覆盖**正负双轨**、站点不重复、广告风险已标注）。
**正负双轨取证**：正面证据（评测/参数/榜单）易被商单污染；负面证据（投诉/召回/碰撞/监管/差评）抗污染、是硬证据。缺一轨要显式报 `evidence_balance=missing_negative/positive`，不许静默跳过。

### 第 0.5 层：候选池全量发现（与"候选池尽量收全"铁律合并）
**先问"市面上有哪些在售车型"，再问"每款车有什么证据"。** 严禁从检索到的来源反推候选池（那会系统性漏掉新品和"越新越没差评"的车型）。多路合并：销量榜+级别筛选+官网/易车/汽车之家配置表+历史榜单；每款带 source_id，缺失标 unknown；零证据新品标 `new_product_zero_evidence` 而不是排除。**排序只能从候选池取，不能从来源反推。**（本条即最高优先级铁律"候选池尽量收全"的执行细则）

### 证据优先级与五步协议
优先级：**监管/安全/召回公示 ≥ 官方参数页 ≥ 第三方量化实测（带数字+单位+测量语境）≥ 第三方定性实测 ≥ 多博主横评 ≥ 社区口碑 ≥ 二手转述**。
协议：规划+建池 → `domain_search` 起手2–4轮不同措辞、WebFetch抓关键页 → 筛选可追溯URL → 边搜边写来源对象并**跑广告识别** → 落盘 `research.json`（排行榜类同时落候选池）→ 说明边界（credibility_pre/ad_score 是启发式初标）。

### 站点解析 / 广告降权 / 博主评分（脚本 `research/researcher.py`）
17 站点解析器：`--list-sites` 查看；`--sites dongchedi,12365auto,cncap,ciasi` 定向采集；`--detect-ads <research.json> --inplace` 广告识别（查小黄车/报备/送测/合作，高_广告风险 −25 / 中 −10）；`--score-authors --inplace` 博主可信度（粉丝量级+认证+平台微调）。
**边界**：脚本是"站点画像+检索词生成+广告降权"工具，**不是懂车帝/抖音私有API爬虫**（site_query 只生成 `site:` 检索词）；真正抓网页靠内置 domain_search/WebFetch，反爬/登录墙平台走检索摘要更稳。

### 与出榜链路的接口（已消除旧 orchestrator 引用）
`leaderboard.py --research research.json` 把证据覆盖层接进打分；`pipeline.py --candidates <候选池.json>` 消费候选池。**本引擎不走辩论/orchestrator，全程无 LLM 排序**——采集到的视频/评论是主观舆情，只作参考列与证据，**不进 8 维打分**（延续"事实列不计分"纪律）。

## v8.1 · 按需候选摄入 `--intake`：搜榜→有效销量→入池→复用/待采（2026-11-19）

### 为什么要改（用户明确否决 automation）
上一轮提议把 `--ingest` 接进月度自动化，用户否决：**自动化不符合 skill 的模式**。
改为 skill 内**按需触发**的一条命令——每次提问手动调用，不挂任何定时器/automation。
同时解决三个入口口径割裂的问题：`discover.py` 用「近3月中位」、`fetch_dcd_sales.py` 用
「逐月中位数+新车爬坡」、总池 `--discover-in` 被动接收清单——v8.1 统一成一条链。

### 一条命令完成用户要求的闭环
```bash
# 实时拉懂车帝四口径逐月榜（提问时手动触发，不自动）
python3 master_pool.py --intake --month 202610 --months 6 \
    --min-sales 1000 --power BEV --price-max 40 --apply
# 离线复现（用已落盘的逐月榜快照，结果可复现、不联网）
python3 master_pool.py --intake --sales-cache dcd_snapshot.json --month 202610 \
    --min-sales 1000 --apply
```
链路：① 拉懂车帝纯电/插混/增程/全部四口径逐月榜 → ② 每车按**现行有效销量**口径算
（逐月中位数；新车爬坡期取中位与最新月较大值）——直接 import `fetch_dcd_sales._trend_pick`，
**不重造口径** → ③ 达标筛选（有效销量门槛 + 价格带 + 动力，均为提问条件）→
④ 池内命中**直接刷新销量/标签复用**，池外未命中**建 needs_data 档案待采 8 维证据** →
⑤ 出 `intake_report.json`（复用/待采/淘汰清单 + 待采字段）。默认 **dry-run**，`--apply` 才写回。

### 关键纪律
- **有效销量单一事实源**：复用 `fetch_dcd_sales._trend_pick`，两处不会口径漂移。
  一车多动力（只在「全部」榜出现）标 `sales_scope=series_total`——销量是车系合计**上限**，
  纯电≤该值，只作发现/门槛，不当纯电值用。
- **不挂 automation**：这是按需命令，每次提问由人触发；SKILL 不注册任何定时任务。
- **池内复用不覆盖打分**：刷新的是销量事实与检索标签（trend_sales/price_wan/brand），
  scores/adjust 一律不动——与 `merge_car` 幂等纪律一致。
- **池外不猜标签/分数**：新车只带名字/品牌/价格/销量，`cls` 留空、scores 为空，标 needs_data，
  待人工确认官方二级分类并采集 8 维证据后才 ready。
- **可离线复现**：`--sales-cache` 读逐月榜快照（结构 `{rows:[{name,brand,months:{bev/phev/all:{月:销量}}}]}`），
  同快照同参数必得同结果，便于审计「为什么这款入池/落榜」。

### 验收（2026-11-19）
`python3 master_pool.py --selftest` 9 项全过（新增有效销量中位/新车爬坡、榜单行纯电/车系合计判定）。
用 baseline 缓存的 36 款逐月销量造快照跑 intake：达标 19 款全池内复用；注入 1 款池外新车
（800/1500/2600）→ 有效销量 1500、`needs_data` 建档、总池 67→68；dry-run 不写回、`--apply` 才写。
验证产物：`runs/car_midsuv_ev_202610/dcd_snapshot_202610.json` + `intake_report_dryrun.json`。

---

## v8.2 · 多源交叉核验 cross_verify：所有数据源都查、比对、冲突网查确认（2026-11-19）

### 为什么要改（用户明确要求）
用户指出：候选摄入只查懂车帝一个源不够——**「别只查一个，要所有数据源都查，
最后比对，对不上的网上找资料确认」**。此前 `--intake` 只用懂车帝销量榜，
而 skill 里其实早已落盘 12 个车型级事实源，却从未交叉比对。

### 12 个已落盘事实源（覆盖字段）
`C-NCAP成绩(57款)` / `C-IASI评级(189条)` / `电芯核验表cell_facts(37款)` /
`官方级别缓存levels(29款)` / `逐配置表trims(35款)` / `气囊库airbags(38款)` /
`生命周期lifecycle(15款)` / `口碑koubei(50款)` / `第三方实测thirdparty(7条)` /
`懂车帝销量快照` / `基准池baseline(39款)` / `总池master_pool(67款)`。
每个源能给同一款车的不同字段提供**带来源/URL/日期**的值。

### 四态裁决（字段级，继承「不猜」红线）
对每车 × 每字段汇总所有源的值 → 按证据等级裁决：
- **AGREE** 多源一致；或网查确认(verified_web)覆盖。
- **CONFLICT** 多源分歧——**除网查确认外，任何分歧一律列出，绝不静默挑一个当事实**
  （曾踩坑：等级覆盖把车系合计/纯电、配置价/车系价的分歧悄悄抹平）。
- **SINGLE** 只有单源（证据单薄，标注来源）。
- **MISSING** 所有源都没有。
证据等级：网查确认(100) > C-NCAP/C-IASI(90) > 官方配置表(85) > 电芯核验表(80)
> 懂车帝(60) > 基准池(55) > 总池(50) > 缓存(40-45)。

### 网查确认闭环（用户要求的「对不上的网上找资料确认」）
```
多源比对 → CONFLICT清单 → agent/人工联网核实 → 写 categories/verified_overrides.json
        → 重跑 cross_verify（verified_web 作为 rank100 覆盖）→ 冲突清零
```
`verified_overrides.json` 每条必带 url/date/note，只写 ≥2 个独立权威源一致的值。
实测：星海V6 电芯「总池未披露 vs 核验表欣旺达」→ 网查 5 源（搜狐/易车/头条/太平洋/零部件明细）
一致确认欣旺达 → 写确认表 → 重跑该字段从 CONFLICT 变 AGREE，冲突 20→19。
**销量/价格口径差（车系合计vs纯电、中位vs当期、配置价vs车系价）属「已解释的口径差」，
保留 CONFLICT 并在 cause 字段写明，不强行裁决**——这是诚实，不是缺陷。

### 关键纪律
- **只产出核验表，不进打分链**：`scores/adjust/total` 零改动（selftest + 写回实测验证）。
- **量纲隔离**：C-NCAP 得分率(%) 与 safety 分(10分制) 不混为同一字段（曾混淆，已修）。
- **表述差异归一**：电芯括号注解（「上汽时代(宁德合资)」→「上汽时代」）比对时剥离、展示保留，
  避免把同一答案的不同写法误报冲突（修复后电芯冲突 4→1，剩下的 1 个是真分歧）。
- **跨库车名对齐**：内联 filings 的 name_keys 规则（品牌剥离/希腊字母别名/纯电后缀），
  零依赖（不 import 需要 requests 的 filings 模块）。
- **默认只核验总池 67 款**（`--all-sources` 才含池外源车），报告才有「我的候选池」意义。
- 按需手动触发，不挂 automation。

### 用法
```bash
python3 cross_verify.py --selftest
python3 master_pool.py --verify --dcd-cache 快照.json --verify-out 报告目录           # 全池核验，dry-run
python3 master_pool.py --verify --verify-models 丰田bZ5 --apply                        # 单车核验+写回摘要
python3 cross_verify.py --conflicts-only                                              # 只看冲突清单
# 冲突 → 网查 → 写 categories/verified_overrides.json → 重跑冲突清零
```
`--apply` 时把核验摘要（conflicts/single_source/verified_fields）写回总池每车的 `verify` 字段，
只加核验元数据、不动打分。

### 验收（2026-11-19）
cross_verify selftest 6 项 + master_pool selftest 10 项全过。
总池 67 款核验：19 个冲突全部为已解释口径差（16 price + 3 trend_sales），
电芯真冲突经网查确认清零；185 个单源项（多为 C-NCAP 官方成绩与逐配置 kWh，标注来源）。
写回实测 scores 改动 0 款。报告：`runs/car_midsuv_ev_202610/cross_verify/cross_verify.md/.json`。

---

## v8.3 · 分段汇总总榜：无价位段的排行榜默认分段→各段取前几→汇总成总榜（2026-11-19）

### 为什么要改（用户明确要求）
用户规则：**「以后排行榜如果没有价位段，就分几个价位段，每个价位段按前几排名，
最后把这几个榜单汇总成总榜」**。此前 v7 的统一榜虽有价位段子榜，但那是「全价位混排主榜 +
各段全量分组视图」，缺少「各段各取前几 → 拼成一张带分段配额的总榜」这个默认展示模板。

### 事实榜融合状态核查（用户要求检查）
- 现行口径：**没有独立事实榜**。kWh/万元、电耗等已作为「参考·不计分」列并入打分主榜。
- 最新两份榜单（`全价位合并榜_中型纯电SUV_21款_202610.html`、`统一榜单v7_中型纯电SUV_202610.html`）
  经核查均无独立事实榜章节，只有「全价位打分主榜 + 事实参考列 + 价位段子榜」。
- 目录里 `中型纯电SUV全价位事实榜_202610.html`（11:48）是融合前的历史中间产物，非现行口径。

### 三段规则（已固化进 `board_v2.py` + `categories/rank_v2_policy.json`）
1. **先分段**：按 `price_bands`（默认 18/28 万切入门/主流/高端三段，或价格分位自动切）。
2. **各段列前 10**：每段按主榜分数取前 `per_band` 名（**默认 10**，CLI `--per-band` 可调）。
   段内不足 10 款的按实际款数（如高端段 >28 万中型纯电常只有 1–2 款），如实标注、不硬凑。
3. **汇总成总榜**：把各段前 10 汇总后按主榜分数降序排成一张总榜，每行标注来自哪一段、段内第几。
- **纪律**：汇总榜分数全部来自主榜 `score_rows`，**不重新打分、不改权重**。
- **价值**：回答「全价位前 10」时不全池混排（会被高端车垄断），而是入门/主流/高端各列前 10
  再汇总——每个价位段的前 10 都看得到，段内选车逻辑各自成立。
- **全价位总榜不可剥离任何维度**（用户铁律）：总榜与各段榜单都是 **8 维全方面对比**，
  性价比（11.7%）实实在在计入——价格竞争力是产品力的一部分。综合分 = `score_rows` 原样输出。
- **必须配分析**：分段榜要同时给「选车逻辑 / 对比逻辑 / 低价位段能否超越高价位段」三段评价。
  低价位能否超越的实证口径 = **剥离性价比维度后的 7 维归一化产品力分**（避免「便宜=性价比高」混淆产品力）：
  2026-10 中型纯电 SUV 实测入门段均值 80.1 > 主流段 79.4 > 高端段 79.0，
  全池产品力第 1 是 15.98 万 MONA L05（86.8）——但高端段仅 1 款，统计意义弱，须如实披露。
  **产品力分绝不进主榜/各段榜单、不改主榜名次**——主榜表格只出 8 维综合分。
  若用户明确要「单独对比产品力」，见 v8.3.3：出一个独立命名的「纯产品力总榜」辅助榜，与主榜并列展示。

### 与既有视图的区别
| 视图 | 口径 | 回答什么 |
|---|---|---|
| 全价位打分主榜 | 全部车按综合分混排 | 综合产品力谁最强（唯一权威） |
| 价位段子榜 | 各段全量分组 | 我这个预算买哪款最好 |
| **分段汇总总榜(v8.3)** | 各段取前 N → 汇总 | 排行榜没分段时，各价位代表凑成总榜 |
| **纯产品力总榜(v8.3.3，辅助榜)** | 剔除性价比/7维归一化/全池混排 | 不看价格谁的产品力更强（不改主榜名次） |

### 用法
```bash
python3 board_v2.py --selftest
python3 board_v2.py --candidates <带scores候选池.json> --facts <事实参数.json> \
    --per-band 10 --out <目录>
# → board_v2_data.json 增加 segmented_summary 字段（per_band/bands/items）
# → board_v2.md 增加「分段汇总总榜」章节
```

### 验收（2026-11-19）
selftest 6 项全过（新增：各段不超过 per_band、汇总按分数降序、分数来自主榜、各段都有代表）。
21 款中型纯电 SUV 样例：入门段取 10 / 主流段取 9（全段）/ 高端段取 1（该段仅 1 款），汇总成总榜；
配套分析见 `runs/car_midsuv_ev_202610/分段价位榜_中型纯电SUV_202610.html`。

---

## v8.3.3 · 纯产品力总榜：双榜并存，剔除性价比单看产品力（2026-11-19）

### 用户要求
「是否再增加一个剔除性价比的总榜，这样剔除价位的影响可以单纯对比产品力」。
这不是推翻 v8.3.2 的铁律——铁律针对**主榜**（8 维全方面、含性价比）不可被剥离；
用户要的是**额外并列一张**明确命名的辅助榜，专门回答「不看价格谁的产品力更强」。

### 口径（已固化进 `board_v2.py: product_power_board()` + `rank_v2_policy.json: ranking_rules.product_board`）
- **剔除性价比维度 value（11.7%）**，其余 7 维按原权重归一化 ×10。
- **全池混排、不分价位段**——目的正是跨价位直接比产品力（分段就失去意义）。
- `rank_delta = 主榜名次 − 产品力名次`：正数=剔除性价比后名次上升（性价比拖累了它），
  负数=靠性价比抬升了主榜名次。
- **纪律**：这是并列【辅助榜】，`build()` 输出 `product_power_board` 字段；
  主榜（8维含性价比）仍是唯一权威排序，本榜不改主榜任何名次/分数/权重；两榜分开标注、严禁混淆。
- 维度不全（缺任一 7 维分数）的车不进产品力榜；价格缺失的车仍进全池榜但不归入价位段。

### 用法
```bash
python3 board_v2.py --selftest   # 7项，含"只改value不改产品力分"断言
# build() 返回的 data["product_power_board"] = {excluded_dimension, note, n, items}
# 每项：model/power_score/main_score/main_rank/price/band/power_rank/rank_delta
```

### 验收（2026-11-19）
selftest 7 项全过（新增：产品力榜降序、覆盖全池、携带主榜分一致、改 value 不改产品力分）。
21 款实测：MONA L05 主榜#4→产品力#1（↑3）、小鹏G6 主榜#11→#17（↓6，最靠性价比抬升）、
宋Ultra EV #14→#8（↑6，价格缺失未归类但仍在全池榜）。
报告 `runs/car_midsuv_ev_202610/分段价位榜_中型纯电SUV_202610.html` 已改为双榜并存展示，
「低价位能否超越」一节沿用同源口径，措辞不再称"唯一剥离口径"。

---

## v8.3.4 · 发布前 A/B 回归修复与全能力覆盖测试（2026-11-19）

### 为什么要改
发布候选（v8.3.3）与已发布版（v5.6.1）做同输入 A/B 回归时，发现 2 处**渲染层退化**
（不改分、不改名次，但会输出误导性结论），且原测试只覆盖 4 个问题、未覆盖 skill 其余能力。

### 修复（均在 `ice_board.py`）
1. **三档分级全空**：`render_board_md()` 入参 `tiers` 是调用处解包后的内层 dict
  （`{"T1":[…],"T2":[…],"T3":[…]}`），新版只读 `tiers.get("tiers")` 导致四个分榜三档全 0 款。
   恢复兼容回退 `tiers.get("tiers") or (tiers if isinstance(tiers, dict) else {}) or {}`。
   验证：四榜三档数与 v5.6.1 完全一致（0/13/21、2/26/33、3/17/15、1/18/28）。
2. **market 权重披露写反**：文案写死 24%，但配置实配 22%。改为从 `cfg["dimensions"]`
   动态取 `market` 权重（`_market_w`），正文与「全量核验」段共用同一值，杜绝再次漂移。

### 全能力覆盖测试（离线可复现）
`run_ab.py` 从 `test-v5.6.1`（基线）与 `test-v8.3.1`（候选）各导出隔离代码，清缓存后喂
完全相同输入，跑 4 个问题 + **24 项能力矩阵**：8 项 selftest、5 项数据事实库校验、
3 项总池专有（新版）、4 项稳健性解释（三榜并列/客观赋权/反方审查/滑杆）、2 项专项榜、
生命周期变点、质量第 8 维 + 气囊/碰撞闸门。
结果：新版 **24/24 全过**，老版 17/24（7 项为新版专有，按设计跳过）；
4 问主榜名次与老版一致，Q1/Q2/Q4 报告逐字节相同。
另修两处测试脚手架问题：历史池 `trim` 字符串需规范化为 dict；
`board_v2 --config` 维度数须匹配候选（7 维 Q2 用 `car_compact_1218`），否则产品力榜空榜。

### 发布
两处退化修复 + 全能力回归通过后，同步到 GitHub `test-v8.3.1` 分支提交
（`a9369ba`）；以该分支作为发布候选，版本号 v8.3.4。

---

## v8.3.5 · 榜单 HTML 可视化内置（2026-11-19）

### 为什么要改
主榜链路（`pipeline.py` / `board_v2.py`）此前只出 Markdown；燃油榜和电池榜虽自带 HTML，
但用户要看可视化页面时，只能靠各 run 目录里写死车型/文案的一次性 `build_*.py`——
这些脚本没进发布包，安装用户拿不到 HTML，是真实能力缺口。

### 实现
- 新增 `render_board_html.py`：消费 `board_v2_data.json`（+ 可选 pipeline `summary.json`），
  产出单文件、自包含样式的 `榜单.html`；五块内容一页看全：打分主榜（事实参考列不计分）、
  三档分级、价位段子榜、分段汇总总榜、纯产品力辅助榜（`rank_delta` 标出性价比拖累/抬升）。
  三档优先取 summary；没有时从主榜每行的 `tier` 字段聚合。
- `board_v2.py` / `pipeline.py` 出榜后**默认自动生成 `榜单.html`**，`--no-html` 可关；
  `pipeline.py` 复用同一份候选 + 同一配置重建 board_v2 数据，与单独跑 `board_v2.py` 完全一致。
- 渲染器自带 `--selftest`（结构断言），不依赖真实数据、无第三方包、无模型调用、同输入同输出。

### 验收
`render_board_html.py --selftest` + `board_v2/master_pool/cross_verify/ranking/robust --selftest` 全过；
用 2026-10 纯电中型轿车 12–18万榜（14 款）验证：主榜/三档/分段/产品力/参考列全部正确呈现，
`--no-html` 时只出 Markdown。`build_release.py` 打包 10 项检查通过（文档引用的脚本均在包内）。

---

## v8.3.6 · 单价位段跳过分段汇总，保留纯产品力榜（2026-11-19）

### 为什么要改
v8.3 的「分段汇总总榜」是为**没给价位段**的提问设计的：先分段、各段取前几、再汇总，
避免全价位混排被高端车垄断。但用户给出价位段（如「12–18万」）后，所有候选只落入一个段，
分段汇总就退化成把主榜原样再列一遍——没有信息增量，还多占一屏。
另一方面，**纯产品力榜**（剔除性价比、跨价位直接比产品力）在同一价位段内仍有意义：
12–18万榜实测红旗天工05 主榜#8→产品力#4（↑4）、风云A9 #5→#10（↓5），
揭示的是「靠价格抬升还是靠产品力」，与是否已给价位段无关。

### 规则（已固化进 `board_v2.py:segmented_summary()`）
- 统计非空价位段数：`≤1` → 跳过分段汇总（`segmented_summary.skipped=true` + reason），
  Markdown/HTML 给一句说明而不是空表；`≥2` → 维持 v8.3 原规则不变（各段前 N → 汇总）。
- 纯产品力榜**不受影响**，任何场景都保留。
- 自检：`board_v2.py --selftest` 新增单段断言（跳过 + 产品力榜仍在 + md 有说明）。

### 验收
- 12–18万中型纯电轿车榜（14 款，单段）：分段汇总 skipped，产品力榜 n=14，主榜/三档不变。
- 全价位中型纯电 SUV 榜（3 段）：分段汇总照常 20 条（入门10/主流9/高端1）。
- 全部 selftest 与 A/B 24 项能力矩阵回归通过。

---

## v8.3.7 · 给价位段时价位段子榜也跳过，Markdown 补产品力榜（2026-11-19）

### 为什么要改
v8.3.6 已让「分段汇总总榜」在单价位段时跳过，但**价位段子榜**仍在显示：
给定 12–18万 后它输出 3 个段（2 个空段 + 1 个把主榜原样重列的段），
且价格缺失的 3 款车在子榜里被丢掉——零信息增量还制造误导。
用户给价位段时，该价位段本身就是`主榜`，子榜与汇总榜都不必再展示。

### 规则
- `band_subboards()` 在非空价位段 `≤1` 时返回空列表；`meta.band_subboards_skipped=true` + reason，
  Markdown/HTML 给一句说明。`≥2` 段的全价位榜不受影响。
- 与 v8.3.6 的分段汇总跳过共用同一判据（非空段数），两处行为一致。
- **纯产品力榜任何场景保留**；v8.3.7 起 Markdown 也渲染它（此前只有 HTML 有），两种格式一致。
- selftest 加双断言：多段保留子榜、单段跳过子榜且产品力榜仍在。

### 验收
12–18万榜：子榜 0、分段汇总 skipped、产品力榜 14 款，md/html 都有跳过说明与产品力榜；
全价位 SUV 榜：3 段子榜与分段汇总照常；全部 selftest + A/B 24 项能力矩阵通过。

---

## v8.3.8 · 单段无数据时两块榜直接不渲染（2026-11-19）

### 为什么要改
v8.3.7 让价位段子榜和分段汇总在单价位段时「跳过」，但仍在页面留了两段「已跳过」说明——
用户指出：没有数据就直接不显示，说明文字本身也是噪音。

### 规则
- 单段（非空价位段 ≤1）时：Markdown / HTML **整块不渲染**这两节，连「已跳过」字样都不出现。
- 机读字段保留（`segmented_summary.skipped`、`meta.band_subboards_skipped` + reason），供审计用。
- 页面顶部导语与规则说明同步清理，不再指向不存在的章节。
- 纯产品力榜任何场景保留；多段全价位榜两节照常显示。
- selftest 改为断言 `## 价位段子榜` / `## 分段汇总总榜` 章节不存在、`已跳过` 不出现、产品力榜仍在。

### 验收
12–18万榜页面只剩：打分主榜 → 三档分级 → 纯产品力总榜；全部 selftest + A/B 24 项矩阵通过。

---

## v8.3.9 · 总分维度明细 + 事实列覆盖披露（2026-11-19）

### 为什么要改
用户问「为什么没有事实榜数据」和「能不能把总分每一项列出来」。
查证：v7.0 起事实榜按用户决定降级为参考列（不再单独成榜），本榜候选只有指导价/电芯/销量
三个字段有数据，kWh/电耗/CLTC/800V 无数据源——显示「—」是对的（不猜不造），
但页面没说明，容易被误读成「数据丢了」。同时每条只有加权总分，看不到 8 维怎么构成。

### 实现
- `board_v2.build()` 的 `meta` 随数据带出 `dimensions`（标签+权重）与 `fact_reference_columns`。
- 主榜后新增**总分构成表**：每车 8 维原始分（表头带权重）+ 加权分，md 和 html 都有；
  html 维分 ≥9 标红、<8 标绿（中国习惯），不改变任何排序。
- 主榜前新增**事实列覆盖披露**：列出本榜有数据的字段与「—」字段，明确说明不以估计值填充。

### 验收
12–18万榜：维度明细 8 列全出（三电18%/安全16.2%/智驾13.5%/空间12.6%/性价比11.7%/操控9%/车机9%/品控10%），
覆盖披露写明「有数据=指导价/电芯/趋势月销；缺=电池/kWh·万元/电耗/CLTC/800V」。
selftest 加断言（meta 8 维、md 含维度表与权重、覆盖披露）。全部 selftest + A/B 24 项通过。

---

## v9.0.0 · 页面展示逻辑单点收敛 + 历史必要区块补回（2026-11-19）

### 为什么要改（架构问题）
历史漂亮页面（各 run 目录的 `build_*.py`）里有丰富区块：8维拆解、逐车取证依据、事实参考区、
生命周期、落榜名单、方法与边界、复现说明。v8.3.5 把 HTML 固化进发布包后，连续几轮「改一处漏一处」：
事实列空了、HTML 有产品力榜 md 没有、跳过时连说明占版面、三档/生命周期在 md 里消失……
根因是展示逻辑散在两处（board_v2.render_md 与 render_board_html.render），各改各的。

### 方案（已实现）
- 新增 **`board_view.py`** —— 页面展示的唯一事实源：
  - `BLOCKS` 注册表列出全部 13 个区块（id / 标题 / 适用条件 / 数据来源）
  - `REQUIRED_BLOCK_IDS` 是契约：8 个历史必要区块任何版本不得少（**注：v9.6.0 起已扩为 11 项，当前契约以 `board_view.py` 代码和本文开头的正式榜唯一标准铁律为准**）
  - `active_blocks(data, summary)` 判定当前数据该渲染哪些块
  - 公共辅助：`fact_coverage()` / `dimension_lines()` / `lifecycle_line()` /
    `method_boundary_text()` / `reproduce_text()` —— 文案与格式只写一份
  - `board_view.py --selftest` 守契约（A/B 矩阵已加为第 25 项）
- `board_v2.py.render_md()` 与 `render_board_html.py.render()` 改为**遍历同一注册表**，
  每块只负责自己的标记语言；新增区块改注册表 + 两处各一个分支，不再到处找漏。
- 数据侧：board_v2 的 `meta` 随数据带出 `dimensions` / `fact_reference_columns` / `lifecycle`，
  渲染器只读，不再依赖外部 summary 才能出三档/生命周期。

### 补回的历史必要区块（此前 v8.x 缺失）
逐车取证依据（note/证据等级/销量口径）、生命周期提示（换代/清库/爬坡）、
落榜与待核名单、方法与边界（公式/稳健性读法/闸门/排除理由）、复现说明。
非必要且按 v7 决定已废除的：独立事实榜（已降级为参考列）、
选车对比逻辑（只对多段全价位榜有意义，单段不适用）。

### 验收
- 12–18万榜 HTML/MD 区块集合**完全一致**：打分主榜 / 总分构成 / 三档 / 产品力榜 /
  逐车取证 / 生命周期 / 落榜 / 方法与边界 / 复现（9 块；两段榜再加子榜与分段汇总 = 11 块）。
- 三个 selftest 全绿（注册表契约 13 项、必要 8 项、HTML 11 块）；
  A/B 矩阵加入 board_view 契约测试（提交后重导出即可跑通）。

---

## v9.0.1 · 修复「漏喂事实库」bug（2026-11-19）

### 问题
用户指出榜单页写「电池/kWh/万元/电耗/CLTC 未采集」，但懂车帝等平台明明有这些数据。
排查：数据确实存在——`car_all_202610/all_data.json`（73 款，带易车来源 URL 与原文引文）
里有 kWh/consumption，CLTC 续航藏在引文里。根因是出榜只从 master_pool 导候选，
候选里只有 cell_brand/trend_sales，**没把事实库作为 `--facts` 喂给 board_v2**，
页面那句「未采集」掩盖的是输入遗漏。

### 修复（数据链路，非渲染）
- 事实库提炼为 `categories/fact_library.json`（73 款：72 有 kWh、71 有电耗、39 有 CLTC），随包发布。
- `board_view.load_fact_library()` 统一加载：显式 `--facts` 优先，否则自动用随包库。
- `board_v2.py` CLI 与 `pipeline.py` 自动 HTML 链路都改为默认加载，不再漏喂。
- selftest 加断言：事实库必须加载到 ≥50 款且有 kWh。

### 效果
同为 12–18万中型纯电轿车榜，事实列覆盖从 0/14 提升到：
电池 13/14、kWh/万元 10/14、电耗 12/14、CLTC 8/14、电芯 14/14。
kWh/万元 作为既有参考项正常显示（如 MG 07 5.28、风云A9 5.40，越高越"量大价优"），
是否按优劣着色属渲染改动，需用户单独确认后才做。

---

## v9.1.0 · 事实列三分法着色（2026-11-19，用户确认后实施）

### 方案（用户确认的三分法）
- **第一类 · 按本池分位着色**：`电耗`（越低越好）、`kWh/万元`（越高越好）。
  前 25% 标「优（绿 ▲）」、后 25% 标「弱（红 ▼）」、中间灰；样本 <4 不分档避免假精确。
  仅本池可比、非跨榜可比；HTML 带图例，Markdown 用 ▲/▼ 文字标记。
- **第二类 · 事实标签不着色**：`电芯`/`CLTC`/`电池kWh`/`800V`/`指导价` 只展示真实值，
  缺失标「未采集」浅灰——电池大不等于好、电芯品牌无优劣序。
- **第三类 · 月销上界**：`sales_scope=series_total` 的销量加 `≤` 前缀并标「上界」灰色斜体，
  表示车系合计是纯电销量上界，不和纯电单独值同色比较。

### 实现
- `board_view.fact_grades()` 统一判定（纯数据，不碰分数）；`grade_color()` 统一色值。
- `fact_columns()` 把销量口径随参考值带出（`≤` 前缀），`fact_coverage()` 沿用。
- 两版渲染器只在主榜参考列套标记/颜色；着色不参与打分、不改名次。
- selftest 断言：电耗低=优、kWh/万元高=优、上界识别、着色前后 total 不变。

### 验收
12–18万榜实测：MG 07 kWh/万元 5.28 ▲、电耗 12.3 ▼；深蓝L06 电耗 11.5 ▲、
月销 `≤5758（上界）`。三版 selftest 与 A/B 25 项矩阵全过。

---

## v9.2.0 · 折中布局：主榜参考列精简 + 取证表补全（2026-11-19，用户确认后实施）

### 为什么要改
主榜参考列塞了 8 项事实（每行 80+ 字符），跟 11 列的总分构成表放一起太挤；
用户确认「折中方案」——不合表，但让两表视觉成组、参考列瘦身。

### 实现
- `board_view.MAIN_FACT_FIELDS = [kWh/万元, 电耗, 电芯]`：主榜只留这 3 项（带色标），
  一行从 80+ 字符降到 ~35 字符；`DETAIL_FACT_FIELDS = [指导价, 电池, CLTC, 趋势月销, 800V]`
  移到「逐车取证依据」表完整呈现（月销上界仍标灰斜体）。
- 两版渲染器只按这两个常量取字段；主榜下方加一句引导「完整事实见逐车取证依据表」。
- selftest 断言：8 项事实被 3+5 拆分且**不重不漏**、着色不改分。

### 验收
主榜参考列实测 31–41 字符（例：kWh/万元 4.32 · 电耗 11.5 · 电芯 宁德时代），
取证表补全指导价/电量/CLTC/月销；三版 selftest 与 A/B 25 项矩阵全过。

---

## v9.3.0 · 智驾/操控从"纯硬件"改为"硬件+体验证据"（2026-11-19，用户授权网查后实施）

### 为什么要改
用户质疑：极氪7X 抖音博主说"轻量化差、电耗差、智驾差不推荐"，而我给智驾 9.0、榜首。
查证：原方法论智驾公式只有硬件触发项（激光雷达/算力/城区NOA），操控只有悬架/驱动/零百——
**看配置单就能算出来**，用户指出这不合理的批评成立。
但官方 IVISTA/C-ICAP 对当前 21 款中型纯电 SUV **零覆盖**（只测新旗舰），
所以经用户授权网查，建立体验证据库。

### 数据来源（全部公开可核验，R1-R4 继承 thirdparty_tests.json）
- 懂车帝/P3中国/汽车之家 36 车辅助驾驶实测（城市NOA通过率：华为98.2%/小鹏95.6%/理想93.1%/特斯拉84.7%）
- 懂车帝五款25万级纯电SUV麋鹿对比（Model Y 80.9 / 极氪7X 75.2 km/h 等）
- 多源车主口碑（极氪7X 城区保守、蔚来ES6 城区保守但平顺、零跑C11 急刹率5%等）
- 已有候选池证据字段（宋L EV 悬挂偏硬、海狮06 暗光激活率低）

### 实现
- 新增 `categories/experience_facts.json`：12 条证据 / 10 款车（adas 9、control 3），
  每条含 platform/url/date/claim/direction/weight/source_type，来源分级独立实测>UP主>车主口碑>厂商。
- 新增 `experience_score.py`：按来源权重加总、每维封顶 ±1.5，只调 adas/control，
  写入 `candidate.experience_adjust` 审计字段（前后分+逐条依据），无证据不插补。
- `board_v2.build()` 默认应用（深拷贝，不改总池），`--no-experience` 可关（A/B 对照用）。
- `meta.experience_adjust` 带出审计，页面展示待用户确认（本次未改渲染区块）。
- selftest 断言：只改两维、无证据不动分、极氪7X 智驾下修操控上修。

### 效果（21 款中型纯电 SUV 榜）
极氪7X 智驾 9.0→8.5（口碑下修）、操控 8.5→9.0（麋鹿75.2上修），总分 86.4→86.1，仍第 1；
小鹏G6 智驾 7.6→8.5（XNGP 95.6%实测），#11→#8（↑3）；阿维塔07 #3→#2；
特斯拉Model Y 智驾 7.0→6.0（84.7%垫底）但操控 8.2→9.2（麋鹿80.9第一），净名次不变。
共 9 款车被调，其余 12 款标"仅硬件口径"不插补。
A/B 矩阵加第 26 项体验自检。

---

## v9.3.1 · 页面展示体验证据修正（2026-11-19，用户确认后实施）

用户确认"增加页面渲染"：board_view 注册表加 `experience_adjust` 区块（14项），
有审计数据时在 HTML/Markdown 展示"车型/维度/前→后/调整量/依据来源（可点开的URL）"，
无证据的车不出现该区块。selftest 加区块激活断言；Q1 榜实测 9 款车的调整全部带来源链接呈现。

---

## v9.4.0 · 车机/能耗体验分 + 拆车置信因子 + 多平台数据接入（2026-11-19，用户确认全做）

### 为什么要改
用户问"除智驾操控还有哪些维度该加体验""拆车数据有没有价值""易车/汽车之家数据能否接"。
调研结论：车机与三电能耗最该加（现有只看芯片型号/容量电压，同样是看配置单）；
拆车有价值但不能打分（单样本不可推广整批次，skill 红线已不认单条拆车）。

### 实现
- 体验证据库扩到四维（adas/control/cabin/powertrain）：24 条 / 10 款。
  新增车机口碑（极氪7X 三级菜单深+误唤醒、鸿蒙座舱流畅、蔚来多屏领先）、
  能耗实测（懂车帝五车高速续航达成率：Model Y 61.7% 但样本 62.5kWh vs 官方 78.4kWh 存疑已降权；
  极氪7X 55.32%、小鹏G6 达成率约85%/70%）。
- 小数方向（0.3）= 温和调整，整数 ±1 = 明确证据；跨口径不混算。
- 拆车证据作**置信度因子不打分**：`teardown_confidence()` 从 thirdparty_tests.json 读
  teardown/stiffness 证据写进审计字段，遵循"单条拆车不推广整批次"红线。
- `EXPERIENCE_DIM_LABELS` 统一维度中文标签（board_view 单点定义），页面四维都显示。

### 多平台数据接入判断（已写入证据分级）
懂车帝（自购统一标准+36车NOA，最可审计，已接）> 汽车之家（AH-100 标准公开 ISO3888-2/FIA车手，
单车型为主）> 易车（AutoLab 自建基地，栏目制覆盖有限）> 太平洋（转述二手，不接）。
已接来源含懂车帝/新浪/汽车之家口碑/网易/什么值得买；AH-100 量化结果待逐车采集。

### 效果
21 款中型纯电 SUV 榜：极氪7X 四维 智驾8.5/操控9.0/车机8.3/三电8.3，总分 86.4→84.6 仍 #1；
小鹏G6 #11→#8、问界M5 #13→#10（各↑3）；腾势N7/深蓝S07/乐道L60 因车机或能耗证据下修。
A/B 矩阵 26 项、全部 selftest 通过。

---

## v9.4.1 · 数据源注册表（2026-11-19）

### 为什么要改
用户问 B站/知乎/抖音/小红书 能不能接、还有哪些数据源、已接入渠道有没有遗漏。
调研结论：官方 API 四家 UGC 都拿不到第三方车型评测（B站无全站搜索、抖音能力改造中且禁品类词、
小红书只有 basic_info 且有两起爬虫判例赔110万/490万）；**只有知乎数据开放平台可行**
（实名后每日约5000次免费搜索，但只给摘要、单次≤10条，只能做声量层）。
已接入渠道的未用数据：易车开放平台有实测API+中保研/中汽研安全榜（需企业商务）、
懂车帝开放平台有懂车分+全网舆情（需商务）、汽车之家无官方API但有第三方结构化口碑接口（付费）。

### 实现（用户指示"耗时就先做标识"）
- 新增 `categories/data_sources.json`：13 个渠道逐条登记 status（integrated/partial/
  available_needs_auth/ugc_needs_credentials/third_party_paid/legal_risk）、数据类型、接入条件、优先级。
- 明确写出 `not_integrated_summary`：需商务/凭据的4项、合规风险不建议采集的3项、当前零覆盖的IVISTA。
- selftest 加注册表完整性断言；不改分数、不改页面渲染。

### 未接事项（需用户决策，不擅自做）
知乎需用户提供 Access Secret；易车/懂车帝/汽车之家 API 需企业资质或付费；UGC 平台不采集。

---

## v9.5.0 · 保值率进 value 维（2026-11-19，用户确认"做1"）

### 为什么要改
用户指出特斯拉排名第17但销量/保值率/品牌/供应商强，问是否该影响打分。
逐条判断：品牌（结果非产品属性，不加）、销量（方法论已定只作门槛不排序）、
售后（数据覆盖差、地域差异大，只披露不加）、供应商/集成化（已在三电确定性/安全中间接体现）、
**保值率（真实的持有成本，确实漏了）** —— 用户确认只做保值率这一条。

### 实现
- 新增 `categories/residual_values.json`：中国汽车流通协会×精真估《2026年6月报告》三年保值率，
  带来源URL。覆盖3款（Model Y 54.2% / 蔚来ES6 47.2% / 零跑C11 47%），其余无数据不插补。
- 口径：delta = clamp((保值率 - 池内中位48%) / 8, ±1.0)，只调 value 维，
  不改其它7维、不改总分公式、不改产品力排序逻辑（保值率是市场结果不是产品力）。
- `experience_score.residual_adjust()` 复用现有 experience_adjust 审计字段，
  页面"体验证据修正"区块自动显示（性价比(保值率) 6.5→7.3 +0.78），**未改任何渲染代码**。
- selftest 断言：有数据上调、无数据不动、只作用 value、审计 basis 正确。

### 效果
Model Y value 6.5→7.28、总分 76.8→77.7、#17→#15；蔚来ES6 -0.10、零跑C11 -0.13（低保值率轻扣）。
核心短板（智驾6.0、容量最小非800V）未变，保值率补不回大差距。A/B 26项、全部 selftest 通过。

---

## v9.5.1 · 插混按场景选车指南（2026-11-19）

用户转述抖音博主观点「插混只推荐比亚迪——最早/单档最简/销量高维保便/自产电池质保好」，要求写入报告一节。
新增 `PHEV_SCENE_GUIDE.md`（方法论附注，不进评分）：逐条核实四条说法，
结论「他把风险最低的选择包装成唯一正确的选择」；给出按场景分支的选型表
（市区通勤→DM-i、北方均衡→吉利雷神、高速四驱→长城Hi4、满载山区→奇瑞鲲鹏、无桩→油混）。
明确为什么不写进打分：market维已计销量、quality已间接体现故障率、场景相关不能做普适加分、
无跨品牌同口径故障率数据、插混榜本就不设safety维（证据覆盖仅8.6%）。
来源：易车/有驾/头条多源横评交叉，无单一厂商软文。

---

## v9.6.0 · 主榜单表多列 + 三个分析区块入页面（2026-11-19，用户确认后实施）

用户给了参考榜单链接，肯定其 UI：加权前十有评分配置、指导价/月销/证据/周期列、kWh万元/电池来源，
以及生命周期提示、三榜并列、稳健性、审查边界。用户确认：单表多列、不要气囊列、三个分析区块全做。

### 实现
- 主榜从 6 列改 11 列单表：#/车型/评分配置/指导价/趋势月销/加权分/档位/证据/周期/kWh万元/电池来源。
  `board_view.MAIN_BOARD_COLUMNS` + `MAIN_COL_LABELS` + `main_cell()` 单点定义，两版渲染器共用；
  评分配置/证据/周期随 main_board 行带出（构建时加，不改分）。周期显示中文（新车爬坡/改款清库等）。
  气囊列按用户要求不做（数据在 airbags.json 另存、覆盖不全）。
- 三个分析区块复用 pipeline 同款 gate 逻辑（`analysis_blocks()` 调 leaderboard 的
  tri_report/robust_report/review_report），结构化写进 meta：
  - three_board：销量序/加权序/BT共识序前十 + 差值≥3加粗 + 三方交集
  - robustness：Kendall τ（当前 0.850）/60 次排序/逐车算法命中
  - audit_boundary：可交付但只能说到同档 + 结论边界/提示级/说明级
  全部注册进 BLOCKS 契约（17 项区块、11 项必要）。
- 数据来自我们自己的 report（τ=0.850、交集7/10），**不抄参考榜单的数字**（那份 τ=0.802 是不同配置跑的）。
- selftest 加断言：列清单完整、三区块激活。

### 效果
12–18万榜页面 12 个 h2（原 9 + 三榜并列/稳健性/审查边界），主榜 11 列实测正确。
全 selftest + A/B 25 项通过。

---

## v9.6.1 · 档位补齐 + 电压并入电池来源 + 总分构成加列（2026-11-19，用户确认后实施）

用户指出三处：档位列空、「未采集（800V）」不对（车辆信息里有电压）、电池来源要区分宁德电芯/整包。
用户确认：电压并入「电池来源」列、总分构成加「评分配置」+「指导价」列。

### 实现
- **档位**：build 新增 `tiers` 参数，从 `summary.tiers` 反查每车 T1/T2/T3 → 中文「第一/二/三档」写进
  main_board 行。CLI 与 pipeline 都传。落榜车（秦L EV）无档位正确。
- **电压**：新建 `categories/battery_platform.json`（14 款，按**评分配置**判定电压——关键：
  MG 07 评分配置是 610 激光雷达版=400V 而非 845 版 800V；极狐S5 带内最低配 400V；
  红旗天工05 550青春版 400V 而非 850版 900V）。电压并入「电池来源」列显示「宁德时代 · 400V」。
  查不到的（极狐贝塔S3/大众与众07）标「未披露」不猜。
- **封装形式**：同库记录 cell/pack。公开数据普遍只给电芯品牌、不披露整包集成商，
  如实标「宁德时代电芯·未披露整包集成商」；比亚迪/CTB 明确自产整包的才标。
- **800V 披露**：从 `rank_v2_policy.fact_reference_columns` 移除 is_800v（电压改由电池来源列展示），
  页面不再出现误导性的「未采集（800V）」。
- **总分构成**：HTML/MD 都加「评分配置」「指导价(万)」两列，逐车明细更可读。

### 验收
12–18万榜主榜首行：深蓝L06 / 第一档 / 宁德时代·400V；总分构成 13 列（含评分配置+指导价）。
selftest 加电压并入与档位中文断言；全 selftest + A/B 通过。

---

## v9.6.2 · 缺失理由 + 评价列 + report质量分修复（2026-11-19，用户确认后实施）

用户确认三件事：kWh/万元缺失写明理由、主榜加评价列（机械生成）、修report质量分脱节。
另提出质疑「投诉数是否没考虑销量」——核查后确认代码已做销量归一化，但指出两个真问题并一并披露。

### 实现
- **kWh/万元缺失理由**：`fact_columns` 写 `kWh/万元_缺失原因`（缺电量/缺配置价/数据缺失），
  `main_cell` 显示「—（缺配置价）」。查明13/14名为空非"前10才算"，是数据缺口：
  秦L EV 事实库无 kwh；大众与众07 有 kwh 但评分配置无具体指导价（只有区间）。
- **评价列**：`board_view.car_verdict()` 纯规则机械生成（无模型调用、可复现）：
  优点（各维≥8.5打标签）/短板（<7.0、月销上界、CLTC缺、改款在即）/适合人群（最高维映射）/
  建议（三档+证据C+落榜）。主榜第12列，HTML三行彩色、Markdown单行。selftest断言完整性。
- **report质量分脱节修复**：report.md 是投诉数据补齐前的旧快照（写8.18/8.0），
  候选池已是5.73。带 `--complaints` 重跑 pipeline 后一致（海豹06EV 5.73/high 21条46每万台；
  风云A9 5.73/medium 12条20.5每万台）。
- **质量分口径披露**：用户质疑「卖得多投诉多」——核查确认已按销量归一化（投诉/万台+对数压缩），
  但补充披露4点：分母单月销量vs90天投诉口径不对齐（保守高估）、投诉自选择偏差、
  「无投诉」=低置信非质量好、PPH正向对冲+密度封顶。写进 quality_score.report。

### 用户质疑的结论
"没投诉不一定好、有投诉不一定差" —— 后半句代码已处理（归一化+PPH+封顶），
前半句体现在低置信标注，但之前页面只显示分数不显示置信，report已明确列出置信列与披露语。

---

## v9.7.0 · 逐配置评分 + 配置下拉对比 + 质量分温和平衡（2026-11-19，用户确认方案B）

用户指出「总榜本质是配置排行不是车型排行」，要求方案B（spec_score 逐配置实算分），
打分主榜的配置下拉可展开看各配置对比，总分构成也可下拉；质量分温和平衡。

### 实现
- 新增 `trim_board.py`：把 trims.json 的每个配置映射成 spec_score 入参，逐配置算 8 维分+加权总分，
  标 n_imputed（每配置约 6 维插补：配置表缺悬架/TOPS/轴距）。selftest 断言 MG07 845版三电>610版。
- `board_v2.build()` 调 build_trim_board 写进 `meta.trim_board`（13 款覆盖），**不改主榜名次**。
- 页面新增「逐配置对比」区块（BLOCKS 第 18 项）：每车 details 展开，列全部配置的价格/电量/电芯/
  电压/各维分，★标最高分配置，标注「有插补、只作同车相对对比」。
- 总分构成表车型单元格加 `<select>` 下拉（13 个），JS 切换该行 8 维分/总分/配置名为所选配置，
  选「车系评分」恢复。只改显示不动排序。
- **质量分温和平衡**：投诉密度封顶 2.6→2.2、系数 2.2→2.0；问题性质上限 1.2→0.8（每高危0.4→0.3、
  中危0.15→0.1）；PPH 正向空间 +0.6→+1.0。海豹06EV 5.73→6.02。带 --complaints 重跑 pipeline 生效。

### 关于"主榜只排最高分配置"——诚实披露未采纳
实测 13 款里 **11 款的"最高分配置"就是最低价配置**：逐配置打分里 value 随价格单调降、
其它维（三电/智驾）在配置间几乎不变（插补基线），导致最高分≈最便宜。
若按此重排主榜＝"最低配霸榜"，三电/智驾用最低配插补基线、价格虚低，榜单严重失真。
故主榜保持车系级分（唯一权威名次），逐配置分只用于下拉对比。这是对用户的明确技术反馈。

---

## v9.7.1 · 主榜按最高配置分排名 + 修配置字段映射（2026-11-19，用户坚持要求）

用户指出「845版比610版分高却不用它排名没有意义；买车买的是具体配置；主榜按车型最高配置得分评价」。
上一轮 v9.7.0 我未采纳（因 11/13 款最高分=最低配），用户再次明确要求——复查后发现**根因是我的字段映射错误**。

### 根因修复（关键）
`spec_from_trim()` v9.7.0 只映射 kwh/电压/激光雷达，漏了 `cltc_km→range_km`、
`cell_supplier→cell_brand`、`charge里的分钟→charge_10_80_min`，导致所有配置三电都是基线 6.0、
只剩 value 随价格降 → "最高分=最低配"是**映射缺失造成的假象**，不是打分逻辑问题。
修复后"最高分=最低配"从 11/13 降到 6/13（剩余的是配置间三电/智驾确实相同、低价版本该胜出的车）。

### 主榜改造
- `build()` 在排序前算 trim_board，取每车 `best_total`，用**标准差锚定**到车系分量纲
  （`锚定分 = 车系均值 + (配置分-配置均值)/配置sd × 车系sd`），重排 rows 与 rank。
- 主榜分显示锚定配置分，标「（配置）」；无配置数据的车沿用原车系分标「（车系）」；
  `series_total` 保留车系分对照。`meta.total_basis=config_anchored`。
- 示例：MG07 845版(800V/宁德/91kWh) 三电 9.9、锚定 88.5 → **#1**（原 #2）；
  深蓝L06 84.9 #2；海豹06EV 最高配置变 630KM 智享型。符合"按最高配置评价"。

### 验收
selftest 加配置/车系口径标记断言；全 selftest + A/B 26 项通过。

---

## v9.7.2 · 主榜配置/价格/电池来源跟随最高配置 + 逐配置对比可读化（2026-11-19）

用户指出两点：①主榜排名用了845版、但"评分配置/指导价"列仍显示610版（12.69万），分数与配置对不上；
②逐配置对比看不出意义。

### 修复
- `build()` 在锚定时把最高配置完整详情写进 `row.best_trim_detail`。
- `main_cell()` 的 trim/price/cell_brand 优先读 best_trim_detail：
  MG07 主榜现显示 **845 激光雷达版 / 14.99万 / 宁德时代·800V / 88.5（配置）**，与排名同源。
  `fact_columns` 的 kWh/万元、电池、电芯也按最高配置重算（91kWh÷14.99=6.07）。
- 逐配置对比表新增「三电得分理由 / 关键差异」列，显示 spec_score 的加分项
  （800V+1.0、12min快充+1.4、一线电芯+0.7、845km+0.8）与配置库 note
  （"换整条三电路线：+24kWh/+235km/快充20→12min/电机176→235kW"），并加阅读指引。
- selftest 断言配置/价格/电芯电压都跟随最高配置。

### 验收
全 selftest + A/B 26 项通过。MG07 #1 的配置、价格、电池、分数完全同源。

---

## v9.7.3 · 统一人工口径逐配置排名（修复两套分打架，2026-11-19）

用户指出「845版加权分算出来是8.4」——追查发现 8.4 是六配置榜 MG07 845版的 base 加权分（十分制），
而 v9.7.2 主榜显示 88.5（spec_score 规则分锚定）。暴露出**两套逐配置打分互相矛盾**：
- 六配置榜：人工标注 base_scores + trim_deltas（845版三电9.2 high置信、总分86.0）
- trim_board：spec_score 规则从零算（845版三电9.9、6维插补、总分71.5→锚定88.5）

用户选择"全部车都做人工标注"，统一量纲。

### 实现
- 新增 `trim_annotate.py`：13 款统一人工口径——`base_scores` 取候选池人工车系 8 维分，
  `trim_deltas` 按配置客观差异（800V/续航/电量/激光雷达/kWh万元）加减，delta 带 high/medium 置信。
  全部百分制同量纲，**直接排名不再锚定**。
- 弃用 spec_score 从零算分作排名依据（6维插补、系统性偏低、与人工分不同量纲）。
  `trim_verified.json`（六配置榜人工标注）保留作参考，但只有 1 款覆盖，不作主排名源。
- 效果：MG07 845版 **87.4**（800V +0.8/845km +0.6/电量+4.8/激光雷达 → 三电9.86）#2；
  深蓝L06 88.5 #1；红旗天工05 因顶配900V/98kWh 升 #3（评分配置原本是550青春版400V）。
- 分数标签改中文「（配置）」；`meta.total_basis=annotated_trim`。
- selftest 断言 845版人工口径分 > 610版；全 selftest + A/B 26 项通过。

---

## v9.7.4 · 修配置匹配 bug：35款里34款delta基准错误（2026-11-19）

用户问「深蓝L06 670Ultra激光版加权分怎么算的」，列公式时发现：
`scored_trim="560 Ultra 激光版"` 与配置表实际 `"纯电版 560Ultra激光版"` **精确匹配失败**，
fallback 到第一个配置 560Max 作 delta 基准 → 误加「新增激光雷达 +0.8」（实际两个配置都有激光雷达）。

### 影响范围与修复
- 抽查 35 款：**34 款精确匹配失败**，几乎所有 delta 都算在错误基准上。
- `find_ref()` 模糊匹配（归一去年份/前缀/空格 + 数字串与关键配置词打分）：成功率 1/35 → 12/35。
- 仍匹配失败的 23 款（配置名过模糊如"带内主力配置"）：**不下任何 delta**（宁可不加分，
  不在错基准上加分），其逐配置分 = 车系基础分。
- 深蓝L06 修正：670Ultra 88.5 → **87.8**（去掉误加的智驾+0.8，多了合理的性价比delta
  kWh/万元4.32→4.92）。

### 修正后深蓝L06 670Ultra 计算（用户要的公式）
base 车系8维 + delta(三电续航+0.33/电量+0.25、性价比+0.28)，权重×10：
三电8.88×0.18 + 安全8.90×0.162 + 智驾8.70×0.135 + 空间8.30×0.126 +
性价比9.28×0.117 + 操控9.30×0.09 + 车机8.80×0.09 + 品控8.00×0.10 = 8.775 → **87.8**

### 效果
MG07 845版 87.9 #1、深蓝L06 87.8 #2（分差0.1，在算法噪声内）。
全 selftest + A/B 26 项通过。

---

## v9.7.5 · 配置下拉双表联动主榜排名（2026-11-19，用户确认后实施）

用户确认：下拉框两个表都放（B），档位固定不动、三榜/稳健性/审查边界不联动。

### 实现
- 主榜表加 `id=mainBoard`、行 `data-model`，车型单元格放配置下拉；总分构成表加 `id=scoreBoard`、
  下拉统一 `class=trimsel`。两表下拉共用 JS 处理函数。
- JS `applyTrim()`：切换配置后同步更新主榜+总分构成两表的总分/配置名/指导价/电池来源/kWh万元/维度分；
  `sortMain()` 按新总分重排主榜行并重编名次 badge；档位列不参与联动。
  两表下拉选中状态互相同步。页面标注"交互预览，不改权威名次与三档"。
- **踩坑**：Python 普通字符串里 `\"` 输出裸 `"`，导致 JS 选择器 `data-model=""+model+""` 语法错误
  （node --check 报 missing ) after argument list），整段脚本不执行。改用 raw 三引号字符串 + JS 内部单引号修复。
- 浏览器实测：MG07 切 650舒享版 → 分 87.9→84.2、名次 #1→#2、配置/价格/电池/kWh 全联动，档位保持第一档。

### 验收
全 selftest + A/B 26 项通过；node --check JS 语法通过。

---

## v9.7.6 · 纯产品力总榜用最高配置 + 加下拉框三表联动（2026-11-19）

用户要求「纯产品力总榜也用车型，也加下拉框」。

### 实现
- `product_power_board()` 改用最高配置的 scores 算产品力分（原来是车系基础分），price 也跟随最高配置，
  与主榜同口径：MG07 产品力 86.75 / 价 14.99（845版），不再是 82.71 车系分。
- `trim_annotate` 给每个配置预计算 `power_score`（剔除 value 的 7 维归一化×10，与产品力榜同公式）。
- 产品力榜 HTML 加下拉框（class=trimsel，与主榜/总分构成共 39 个），单元格加 class。
- JS 事件委托（改 document.addEventListener，解决脚本在产品力榜之前渲染、绑定不到的问题）：
  `applyTrim` 同步更新三表的分/配置/价格等；新增 `sortPower()` 按新产品力分重排产品力榜、
  重编名次与 rank_delta（主榜名次从当前 DOM 实时读取）。
- 验收：39 下拉框、node --check JS 通过、全 selftest + A/B 26 项通过。

---

## v9.7.7 · 主榜加权分去掉「（配置）」标签（2026-11-20）

用户指出加权分单元格里的「（配置）」多余（主榜标题已写明按最高配置排序）。
- `main_cell('total')` 改为纯数字；页面说明文字与 JS 联动（applyTrim）同步去掉标签。
- selftest 改为断言加权分是纯数字。全 selftest + node --check 通过。

---

## v9.7.8 · 评价列按配置生成并联动（2026-11-20）

用户指出 MG07 845版与深蓝L06 670Ultra版评价一样——根因是 `car_verdict()` 用的是车系基础分，
没用最高配置分，也没体现配置差异。

- `car_verdict()` 改用 `best_trim_detail.scores`（当前配置分）+ 配置级事实覆盖（kWh/万元、CLTC）；
  新增配置差异标签（800V/900V→高压快充、5C/4C→超充、激光雷达、长续航、大电池），
  配置标签优先展示，让不同配置评价一眼可辨。
- `build()` 在 tier/fact_ref 注入后，给 trim_board 每个配置预生成 verdict 文本（供 JS 联动）。
  踩坑：预生成最初放在 tier 赋值之前，导致评价显示"落榜/待核"，移到之后修复。
- JS `trimCellVals` 返回 verdict，`applyTrim` 切换配置时同步更新评价列（带优/短/适合彩色标记）。
- 实测：MG07 845版「高压快充、超充、三电强、智驾好/适合看重续航快充」vs 650版「智驾好、性价比高、
  操控好、车机流畅/适合预算敏感」；深蓝L06「三电强、安全高、智驾好、性价比高」——三者不再撞车。
- 全 selftest + A/B 26 项 + node --check 通过。

---

## v9.7.9 · 逐配置覆盖铁律可执行化（2026-11-20，用户强制要求）

用户原话：「我们是按车型配置来的，缺少车型配置信息这个榜单参考意义大打折扣，
宁愿多花时间搜集也不少搜集，确保以后每次的榜单车型配置都加上。」

- SKILL.md 顶部新增「⛔ 最高优先级铁律」章节（本文件最上方），写明每款车必须有 trims[] 逐配置数据。
- 新增 `trim_coverage.py`：出榜前强制校验每款车都有逐配置数据，
  `python3 trim_coverage.py --candidates <候选池> --strict` 缺配置则 exit 1。
- `categories/trims.json` 补齐 12 款车配置（8 款 auto_trims.py 自动解析 + 4 款网查手工补录：
  秦MAX EV/阿维塔06T/奥迪E5 Sportback/宝马i3），共 47 款在榜。
- 2026-10 纯电中型轿车全价位榜重出：25 款全部有配置，75 个下拉框（25×3），25 个 details。

---

## v9.7.10 · 页面主榜继承闸门淘汰结果（2026-11-20）

用户指出主榜有车没档位。根因两层：① 全价位配置的 `boards[].gates.price_band` 还是旧的 [12,18]，
带外车（>18万）没进档位判定（已修为全价位）；② pipeline 把全部原始候选传给 BV.build()，
导致被生命周期闸门淘汰的停产车（秦L EV，2026-07 停产）仍出现在页面主榜却没档位，
与报告 dropped=1 不一致。

- 修复 `pipeline.py`：HTML 渲染只传 tiers 覆盖的通过车型给 `BV.build()`，页面与报告一致。
- 2026-10 全价位榜重出并逐行复核：24 款全部有档位、无异常；72 个下拉框（24×3）、24 个 details。
- 全 selftest + A/B 26 项通过。

---

## v9.7.11 · 历史bug回归测试 + 紧凑纯电榜自检修复（2026-11-20）

- 新增 `bug_regression.py`：把 17 个历史 bug 变成可执行断言（配置映射/双套分/配置匹配/JS转义/
  联动/评价/覆盖/档位/闸门等），接入 A/B 测试管线 COVERAGE（新版专有）。
- 出 2026-10 紧凑型纯电轿车前十榜：10 款候选（原有 8 + 网查补 2），按铁律先补齐 10 款逐配置
  （9 款 auto_trims 自动解析 + 小鹏MONA M03 手工补录），逐配置覆盖校验通过。
- 自检发现并修复 BUG-017：auto_trims 补录车的 scored_trim 与配置表对不上 → find_ref 失败 →
  所有配置 delta=[] → 配置分全相同、下拉无意义。修 `trim_annotate` 加 fallback
  （按车系评分配置价选最接近配置作基准，降置信），配置分恢复差异。
- 榜单逐行自检：10 款全有档位、30 下拉框、10 details、10/10 配置分有差异、报告页面一致。
- 报告披露：MG4 销量冠军但产品力 #7（不是销量榜）；领汇e5/AION S 为营运车，低置信不代表私人推荐。
- 全 selftest + A/B 27/27 + bug回归 17/17 通过。

---

## v9.7.12 · 营运/网约车在主榜评价显著标识（2026-11-20，用户确认后实施）

用户：营运网约车是不是要在主榜评价体现，让人一目了然。确认后实施。

- `car_verdict()` 只读 `note` 明确关键词（营运/网约/B端/出租/对公）识别营运车，不猜；
  返回 `fleet` 标志，把「营运/网约车为主」写入短板、适合人群改为「营运/网约采购（非私人代步首选）」。
- `build()` 把候选 note 注入 row，让评价能读到证据。
- 主榜渲染与 JS 下拉联动都前置红色「⚠ 营运车为主」标识，切配置不消失；
  `main_cell('verdict')` 同步加标识。不改分数/排名/档位。
- 效果：紧凑榜领汇e5/AION S、中型榜红旗E-QM5 有标识，其余车无；两榜 JS 语法通过。
- 新增 BUG-018 进 bug_regression（18/18）；A/B 27/27 通过。

---

## v9.7.13 · 生命周期状态全中文（2026-11-20，用户确认后实施）

用户：生命周期显示的英文变成中文。确认后实施。
- `LIFECYCLE_STATUS_CN` 补 `steady`→在售（稳定）、`active`→在售，及 ramp_up/mature/declining/new/pending/unknown。
- `lifecycle_line()` 字符串分支（如 info="steady"）也过 `lifecycle_status_cn()`，不再原文直出。
- 两榜重出后页面无英文状态残留；BUG-019 进回归（19/19），A/B 27/27 通过。

---

## v9.7.14 · 顶部导航目录 + 区块跳转锚点（2026-11-20，用户确认后实施）

用户：最上面把榜单有几个部分、每部分功能显示出来，加跳转一键到具体位置。确认后实施。

- `render_board_html.py` 在 h1/meta 后加「本榜导航」卡片：从 `BV.BLOCKS` + `active_blocks()` 单一来源生成，
  只列本次实际渲染的区块，每项显示标题 + 一句话作用（desc 轻量精简条件说明），点击锚点直达。
- 页面末尾后处理给每个 h2 加 `id="sec-<blockid>"`（按标题文字映射 NAV_BY_TITLE，不依赖渲染顺序），
  目录项 `<a href="#sec-...">`；另加「↑ 回到顶部」。CSS 加 navbox/navgrid 样式 + h2 scroll-margin。
- 不改分数/排名/档位/区块内容，只加导航层。
- 验证：紧凑榜 9 项、中型榜 15 项，目录与 h2 锚点一一对应；BUG-020 进回归（20/20），A/B 27/27。

---

## v9.7.15 · 智驾分倒挂校正（DeepSeek 评审后核实，2026-11-20）

外部评审指出零跑B01 adas=9.7 却让小鹏MONA M03 Max（750TOPS+全国无图城区NGP）只有 6.6 且标"智驾弱"，
倒挂反直觉。逐条核实确认两个真 bug：

- BUG-021：`deltas_for()` 的激光雷达判断只看 `key_configs`，auto_trims 补录车该字段常为空；
  改为同时识别配置名里的「激光雷达」。修复后零跑激光版 adas 高于普通版。
- BUG-022：车系基准 adas 无证据来源（source_ids=None）。网查核实后重定：
  零跑B01 9.7→7.8（200TOPS，城区NOA仅5城白名单、无图未OTA、算法爬坡），
  小鹏MONA M03 6.6→8.8（750TOPS图灵+全国无图即开即用+第二代VLA，纯视觉短板折扣）。
- 排名随之修正：零跑B01 81.3→78.5（仍#1），小鹏MONA M03 73.9→76.8（#4→#2）。
- 报告新增「外部评审回应与已知局限」6 条：入池理由、质量维零区分度、安全维弱区分、
  智驾校正、纯产品力榜边界、AION UT 空间折扣。
- BUG-021/022 进回归（22/22），A/B 27/27 通过。

---

## v9.7.16 · 豆包评审清单落地（风险显性化，2026-11-20）

用户："要做的你都做了吧"。按上轮取舍全部落地（P0 全做 + P1 选做 + P2 保留）：

- **P0-1** 零跑B01 证据 B→C（媒体五星通稿≠官方碰撞报告，中汽测评官网无记录）。
- **P0-2** 风险标记主表化：`risk_flags()` 生成估算待核/营运为主/缺官方碰撞三类标签，车型单元格显示。
- **P0-3** 估算/插补数据（AION UT 电池）主表⚠标记，不只藏文末。
- **P0-4** 主榜顶部固定稳健性提示 + 同档底色分组（T1/T2/T3）+ τ=0.888 展示；
  τ 由 `pipeline.py` 写入 `summary.stability`（来自 robust_report 的 kendall_mean，只展示不改分）。
- **P1-4** rank_delta 中文标签「被性价比拖累/靠性价比抬升」，悬停看原数字，JS 联动同步。
- **P1-5** 证据类型说明：全部维度为「官方参数+规则推算」非实车实测，低置信单独标记（不逐维拆子项避免新黑盒）。
- **P1-6** 车型池分类说明进方法区（官方二级分类不按轴距重分，AION UT 案例）。
- **P2** TCO 列未做（保值率库仅3款与本榜零交集，无数据不编造）；τ 展示已做。
- **明确不做**：交互式权重模板切换（破坏可复现性）、剔除营运后的私人销量估算（无真实数据源）。
- BUG-023/024 进回归（24/24），A/B 27/27 通过。

---

## v9.7.17 · 全池智驾口径统一 + 报告披露自动化（2026-11-20）

DeepSeek 二轮评审指出「智驾校正只改了B01和小鹏，其他车是否一致」——核实属实：其余8款仍是历史人工分。
按「可用智驾」统一口径重定全池（网查依据）：
Lafa5 5.0→8.0（200TOPS+激光雷达+全国NAC即开通）、AION RT 5.4→7.2（高配Orin-X 254TOPS/仅选装打折）、
秦PLUS 5.0→6.8（天神之眼B选装254TOPS，C版仅高速混合）、MG4 5.0→5.8（80TOPS高速NOA）、
AION S 6.5→4.8（全系无NOA旧分虚高）、AION UT 5.0→4.5（中低配无L2）、荣威D6 5.0维持、领汇e5 6.5→5.0。
每款 note 留校正理由。**BUG-025**。

报告披露自动化：pipeline.py 生成报告时从候选池实时算「候选数/带来源/有碰撞/证据等级/网查补采数」
写进 `候选池与证据披露` 段，不再手工写报告（此前每次重跑都被覆盖）。**BUG-026**。

发现待确认问题：零跑B01 因证据 B→C 被三档规则打入第三档（暂缓），但它是销量第二、产品力第一，
仅缺 C-NCAP 成绩——「无证据=暂缓」是否过严需用户确认后再改核心分档逻辑（本版未动）。
BUG-025/026 进回归（26/26），A/B 27/27 通过。

---

## v9.7.18 · 证据 C 不再一律判第三档（2026-11-20，用户确认方案1）

用户确认：改规则——只有硬风险（营运/停产/无交付）才第三档；仅缺碰撞成绩的证据 C 降到第二档+警示。
此前零跑B01（销量第二/产品力第一/主榜第一）仅因无 C-NCAP 成绩被打成"第三档暂缓"，
与「降权≠判劣」纪律矛盾。

- `leaderboard.tier_report()`：证据 C 从 `risks`（硬风险→T3）改为 `evidence_soft` 软提示；
  T1 门槛仍要求 A（证据C进不了第一档），但不判劣、不进 T3。
- 营运车（领汇e5/AION S/红旗E-QM5）加 `tier_risk` 硬标记，保留第三档。
- `pipeline.py` 把证据C软提示写进 `summary.evidence_soft`，页面三档区块展示"不因此判劣"提示。
- 修正踩坑：detail/tiers 结构不同（tiers=车型名列表，detail=rec 列表），用 detail 取 evidence_soft。
- 结果：零跑B01 回 T2，领汇e5/AION S 仍 T3。BUG-027 进回归（27/27），A/B 27/27 通过。

---

## v9.7.19 · 证据风险分层可视化 + 子项依据（2026-11-20，豆包评审P0/P1）

豆包指出：证据C风险类型内部有区分但主表只显示一个C，用户分不清"缺碰撞证据"与"营运车"；
tooltip含糊；τ只写数字无解释；8维仍是黑盒。核实后落地：

- **证据列分层**：C 分两类显示——`C·证据不足`（参数完备但缺官方碰撞/口碑实锤，留T2，勿对标A类安全置信度）
  与 `C·营运风险`（营运/交付硬风险，T3暂缓）。按 `tier_risk` 字段判定，不猜。
- **tooltip 标准化**：缺官方碰撞/低置信/估算待核/营运为主各自的 title 写清含义与适用边界
  （"低置信分数仅同车配置对比，不适合跨车型横向对比"）。
- **τ 通俗解释**：τ≥0.9「高（排序稳定）」/ ≥0.8「中等（组内名次波动较大）」/ 否则「较低」，
  并写明"越接近1越一致，τ<0.9 勿严格按数字先后选车"。
- **维度子项依据**：总分构成表后加折叠面板（默认收起），列出每维分数来源
  （配置级 deltas 逐条理由 + 车系基准 + 取证 note），不编造、不重复主榜内容。
- 生命周期已在主表"周期"列（豆包该条为旧版误判），未动。
- 未做：私人/营运销量拆分（无真实营运占比数据源，标估算也是编）、权重模板切换（破坏可复现）、TCO（保值率库零交集）。
- BUG-028 进回归（28/28），A/B 27/27 通过。

---

## v9.7.20 · 候选池披露进页面 + 估算电池降精度（2026-11-20）

DeepSeek 六轮评审第1条反复说"候选池规则看不到"——核实发现披露**只在 report.md，HTML 页面没有**，
评审看的是页面。这是多轮误解的根因：写在报告≠用户看得到。

- `pipeline.py`：把候选池统计（总数/带来源/有碰撞/证据A/B/C/网查补采）写进 `summary.pool_disclosure`。
- `render_board_html.py`：导航区下方显示候选池披露段，页面读者一眼可见入池口径与"降权≠安全"。
- 估算电池值降精度：`_kwh_cell()` 对候选 note 含估算/待核的值显示 `≈整数+估标记`，
  其它浮点也最多1位小数；底层 TRIMDATA 保真，只降展示精度（AION UT 34.868→≈35）。
- 踩坑：`summary` 在报告拼装时（158行）尚未初始化（197行才建），pool_disclosure 写入要放初始化块里。
- 不改分、不改排名/档位。BUG-029 进回归（29/29），A/B 27/27 通过。

---

## v9.7.21 · 候选池披露空指向修正 + 定稿（2026-11-20）

DeepSeek 给 9/10 并认可定稿。三条小意见中：
- 安全维度动态更新：未来数据工程，非当前可做，不改。
- 「同级未入池车型见 report.md」是**空指向**（report 里根本没有未入池清单）——改为写实：
  "汽车之家紧凑型车+纯电动 2026-10 车系月销榜前10，来源多为网查多源交叉，不入池的车只作参数参考"。BUG-030。
- Lafa5 产品力>主榜加评价：**未采纳**——核实当前数据 rank_delta=0（产品力#6=主榜#6），
  DeepSeek 引用的是旧数据；无事实依据加这句话等于编，拒绝。
- 回归 30/30、A/B 27/27 通过。不升版本功能、只修展示层文案。

---

## v9.7.22 · 榜单定位正名（2026-11-20）

DeepSeek 定性："这是10款候选紧凑型纯电轿车的审计榜，不是全市场全价位覆盖榜"。核实属实：
配置闸门 ≤100万但候选只有6.88–17.98万、"全价位"标签名不副实。

- 配置 label 改「紧凑型纯电轿车前十榜（2026-10 · 10款候选审计榜）」。
- 页面顶部加定位说明：可审计横向对比榜，不是全市场覆盖榜；未入池≠更差；
  名次排序/同档组可信度/三档能不能买三者区分。
- 阈值（第一档命中≥80%/全情景≥60%/月销≥3000）其实 report 已有，DeepSeek 看页面没看到——
  载体差问题，本版不重复搬（报告是完整口径来源）。
- BUG-031 进回归（31/31），A/B 27/27 通过。定稿。

---

## v9.7.23 · 入池扩展：能比就比，不能比说明原因（2026-11-20，用户要求）

用户："未入池不等于更差，不能把能参与比较的都入池吗？榜单要更严谨，尽量对比更多车型"。

- 原 10 款榜限于销量前10，是人为框子。扩展候选池到 18 款（销量≥100 的紧凑型纯电）：
  新增奔驰CLA(152)/羿驰05(130)/曹操60(126)/秦新能源(213)/e爱丽舍(400)/比亚迪e3(164)/北汽EU(680)/欧尚520(468)。
- 按铁律先补逐配置：auto_trims 自动补6款 + 网查补 e3/曹操60 参数（domain_search），
  北汽EU/欧尚520 参数页无有效逐配置→标注"配置待核"不入主榜，**原因写进页面**。
- 最终 16 款入榜，16/16 配置齐全；前4名与原榜一致（头部稳定），新增长尾自然垫底。
- `pipeline.py` 把候选池顶层 `pool_excluded` 写进 summary，页面候选池披露后显示"未入池车型及原因"。
- 新增车 8 维分按网查客观配置规则推算、置信低，note 留依据；曹操60 标营运硬风险。
- BUG-032 进回归（32/32），A/B 27/27 通过。新产物 `runs/compact_bev_extended_202610`。
- **原则固化**：入池以"能取得逐配置数据"为标准，不以销量名次划界；销量极低(<500)只影响档位、不影响入池。

---

## v9.7.24 · 入池铁律固化 + 完整性盘点工具（2026-11-20，用户要求写进 skill）

用户："车型配置尽量入池加入 skill，这样榜单更加准确可靠，避免遗漏好的车型以及好的配置。"
（配套前一轮"未入池不等于更差"）

- SKILL 最高优先级铁律新增配套条「候选池尽量收全，能比就比」：
  入池标准=能否取得逐配置数据（非销量/价位），出榜前盘点同级全部在售车型，
  无法入池的必须显式列原因（pool_excluded→页面「未入池车型及原因」），销量低只影响档位不影响入池。
- `trim_coverage.py` 加 `--universe <权威清单.json>`：对照车型清单报出"漏收且未说明原因"的车型，
  与既有逐配置覆盖校验合为出榜前双门禁。实测 18 款权威清单 vs 16 入池 + 2 有原因 = ✅全覆盖。
- 用户级 MEMORY.md 同步写入（跨会话强制）。
- BUG-033 进回归（33/33），A/B 27/27 通过。

---

## v9.7.25 · 电池来源列回退修复（2026-11-20，用户指出）

用户："电池来源信息没有，不合理，电芯信息都有的"。核实属实：
`main_cell('cell_brand')` 的 `best_trim_detail` 分支在 cell_supplier=null（auto_trims 记 null）时
直接返回"—"，永远走不到事实库 `fact_ref['电芯']` 回退——明明 fact_library 有值却显示未采集。

- 修复回退链：best_trim.cell_supplier → fact_ref['电芯'] → battery_detail.cell → "—"。
- fact_library 补 4 款长尾车电芯（网查 2026-11-20）：奔驰CLA=宁德时代、羿驰05=孚能/宁德、
  秦新能源=弗迪、e爱丽舍=江苏正力。
- 结果：两张榜电池来源从 3/16 → 16/16、14/16 → 16/16 全覆盖。
- 报告披露：该值为车系级电芯品牌，配置差异（如 MG4 宁德/瑞浦/清陶）以逐配置表为准。
- BUG-034 进回归（34/34），A/B 27/27 通过。
- 教训：任何"字段回退链"都要测 null 分支能否走到下一级——本 bug 藏了多个版本。

---

## v9.7.26 · 全字段显示自检（2026-11-20，用户要求检查哪些值没显示）

用户："再检查一下榜单里面哪些值没有显示，确认是否有问题"。逐列扫描主榜 12 列 + 逐配置 11 列，结果：

- **修复 3 处真问题**：① 6 款长尾车 lifecycle 漏填→补录；② 逐配置表电芯列 99 处空→继承车系事实库值
  并标「车系」（`cell_inherited`，表示未逐配置核验）；③ 得分理由 1 处「—」→「基准配置（无配置级差异）」。
- **真实数据缺口（不编造）**：99/110 配置电压/快充未采集，页面明确显示「未披露」，
  不伪装成有值也不为空；有官方数据再补。报告写明这是采集缺口非渲染遗漏。
- 最终：主榜 12 列零缺失；逐配置 11 列零异常空白（电芯全有、快充缺口明确标注）。
- BUG-035 进回归（35/35），A/B 27/27 通过。
- 教训（重复踩坑）：在 bug_regression 里写含引号的正则必须用 raw 字符串，否则 Python 语法错；
  更稳的做法是简化断言、不硬写复杂正则。

---

## v9.7.27 · 电压/快充数据补采（2026-11-20，用户指出数据本应存在）

用户："电压数据没有不应该，懂车帝里就有，重新排查"。核实：汽车之家/易车参数页确有「高压平台(V)」
「快充时间(min)」字段，但 trims 自动解析漏采（记 null），上一版我误判为"真数据缺口"标「未披露」——
实际是**没去采**，这是我的疏漏。

- 网查补齐 110/110 配置的电压+快充，按配置级记录（零跑B01 590版400V/670版800V·3C 16min 等）。
- 逐配置对比「未披露」从 99 → 0；主榜电池来源列带上电压（B01 800V、AION RT 400V）。
- SKILL 铁律新增：电压平台/快充为必采字段，不可只记车系级、不可留空标未披露了事。
- BUG-036 进回归（36/36），A/B 27/27 通过。
- 教训：判断"数据真的没有"之前，先查权威参数源到底有没有这个字段——别把"没去采"当"采不到"。

---

## v9.7.28 · 原始库→事实库全字段映射（2026-11-20，用户要求）

用户："增加原始数据库到实时库的事实映射，不要原始库有数据事实库里面没有；
原始库没有的数据要再查找明确是不是真的没有还是 bug"。

- 核实原始库 `all_data.json` 有 19 字段/73款，事实库 `fact_library.json` 只映射了 5 个——
  sales/life/life_note/trim/cons_quote/cons_src 等 773 个字段值丢失。
- 新增 `facts_sync.py`：11 字段映射表 + 6 来源字段收进 `_source`，`--check/--apply/--report`。
  手工核验值优先不被覆盖；已同步 773 字段，`--check` 全绿。
- 原始库没有的字段逐一核实并写进 `NOT_IN_SOURCE_DETAIL`：
  **charge/voltage**=有但漏采（已 v9.7.27 修，110/110配置）；
  **crash**=这批紧凑纯电真没有（ice_crash.json 187款0命中，MG4仅Euro NCAP不在库）；
  **airbags**=另有专门库 airbags.json；**adas_hw**=另有配置表/thirdparty库。
- 接入 A/B COVERAGE 与 bug 回归：**37/37**、A/B **28/28** 通过。
- 教训：别假设"原始库没有"=数据源没有——先查权威参数源与专门库，区分漏采/真无/另有库。

---

## v9.7.29 · 弗迪电芯代际标注（2026-11-20，用户要求）

用户："弗迪电池是一代还是二代的标注一下，二代都是闪充电池"。

- 事实库 `fact_library.json` 给 16 款弗迪车标 `cell_gen`/`cell_flash`/`cell_gen_basis`
  （网查比亚迪官方/工信部/易车 2026-11-20）：二代（秦MAX/海狮06EV/秦PLUS EV/元PLUS三代等）、
  一代（秦新能源/比亚迪e3/领汇e5/丰田bZ3等）、待核（MONA M03/铂智7）。
- board_v2 把代际带进 fact_ref，main_cell 在电池来源列显示「弗迪[二代/非闪充]」等。
- **重要事实更正**：用户"二代都是闪充"不完全成立——2026款秦PLUS EV 全系二代刀片（CTB）
  但快充仅60-70kW（非闪充），闪充只有630km特定版本/部分车型。按事实标注不盲从。
- 新建事实库条目：领汇e5、比亚迪e3（原来只在候选池，事实库无）。
- 两榜披露+BUG-038 进回归（38/38），A/B 28/28 通过。

---

## v9.7.30 · 候选池固化防丢失（2026-11-20，用户追责"下次运行会不会又错"）

用户担心："是不是写入代码里了，不要下次运行榜单照样出错"。核查发现**真风险**：
两份候选池只活在 `runs/`（跑批产物、sync_to_repo 明确不同步、换机器就丢），
其中 6 款长尾车、手工分数（零跑B01 adas 7.8）、证据等级、生命周期、代际标注全靠这几个一次性文件。

- 固化到 `categories/` 随包发布：candidates_compact_bev_extended.json（16款）、
  candidates_compact_bev_audit.json（10款）、universe_compact_bev_202610.json（权威清单），
  并加入 sync_to_repo.py 白名单。
- **冷启动验证**：从随包候选池重跑两张榜，13/13 历史修复点全部自动呈现
  （入池/未入池原因/候选池披露/电池来源/弗迪代际/电压/导航/证据分层/τ/定位…）。
- BUG-039 进回归断言（持仓固化+白名单+数据完整性），**39/39**、A/B **28/28** 通过。
- 铁律追加：候选池必须在 categories/，修复写回随包文件并登记白名单，出榜不引用 runs 一次性文件。

---

## v9.7.31 · 快充能力配置级 delta（2026-11-20，用户坚持不能因当前榜无闪充车就不改）

用户纠正："不能因为榜单没有闪充车型就不改，纯电紧凑SUV就有这些车型，海鸥都有闪充了"。对——规则是通用的。

- 走过弯路：先在 `spec_score.py` 加 ≤8min 档，实测分差对，但查调用链发现**主流出榜不走 spec_score**
  （配置分来自 trim_annotate + 候选池人工基准分），故**回滚**该无效改动，不提交假象。
- 真实改动在 `trim_annotate.py`：
  - `charge_minutes()` 把 10-80% / 30-80% / 10-70% 三种口径折算成**等效10-80%分钟数**
    （30-80%20min→等效28min，不能当20min用；兆瓦闪充10-70%5min→等效5.8min，不虚高）。
  - `deltas_for()` 对同车系内快充配置 vs 慢充配置给三电 delta：快≥5min时 +0.15/+0.3/+0.5 分档，
    **按分钟数、不按品牌/电池代际**。全系皆闪充的车优势在人工基准分体现，不重复加。
- 实测 MG07 845版（等效16.8min）vs 650版（28min）得 +0.3 快充delta；海鸥/元PLUS三代等入榜即自动生效。
- 现有两紧凑榜无配置间快充差异（0配置触发），排名不变；BUG-040 进回归（40/40），A/B 28/28。
- 教训：改规则前先确认真实调用链；改在没人读的模块=白改。

---

## v9.7.32 · 紧凑型纯电SUV前十榜（8-16万，2026-10）

新榜单：20款候选全部逐配置齐全，10款取前十。要点：
- 入池按"能取得逐配置数据"：20/24入池；未入池2款（MONA L03 14885辆、iCAR V23 2585辆）
  因配置表索引无车系，标注"配置待核"并在页面显示原因。
- 元UP/零跑A10属小型SUV不入紧凑池；宋Pro DM-i等插混按纯电口径剔除。
- 零跑B10/银河E5零售批发差异大，取零售。
- 总池9款老adas(多为5.0)按"可用智驾"统一校正（B10 5.0→7.2激光城区NOA等）。
- 候选池+配置固化在 categories/（candidates_compact_suv_bev.json / car_compact_suv_bev.json），
  pool_meta.notes 由 pipeline 自动披露，重跑不丢。
- 前十：AION N60/i60/领克20/MG4X/元PLUS/B10/欧拉5/宝骏云海/海狮05EV/深蓝S05；τ=0.872。
- BUG-041 进回归（41/41），A/B 28/28 通过。

---

## v9.7.33 · 电芯校验补漏（2026-11-20，用户追责"校验为何没生效"）

用户发现紧凑SUV榜主榜1/3/4等 11/20 款电芯为空，质疑之前加的校验为何没生效。

- **根因**：`trim_coverage.REQUIRED_FIELDS` 只有 name/price/battery_kwh/cltc，
  代码注释明写"电芯/快充缺只记 warning"，而 `report()` 根本不输出 warning——
  所以校验显示"✅全部齐全"但主榜电芯列 11 处空白。这是我之前定的规则漏洞。
- 修复1（数据）：网查补 12 款车电芯到 trims.json 配置级（63配置）+ 事实库车系级；
  顺带发现并补上 AION S（轿车扩展榜也缺）。
- 修复2（校验）：`trim_coverage.check()` 新增 `no_cell` 检测——配置级或事实库车系级有值即可
  （与 main_cell 回退链一致），报告明确输出"✗ 无电芯来源 N 款"并阻止出榜。
- 修复3（回归）：BUG-042 断言三榜候选池电芯零缺失 + 页面主榜电芯列零空白。
- 教训：**校验必须覆盖所有展示字段**——"校验通过但展示空白"等于没校验。
- 42/42 回归、A/B 28/28 通过。两榜重出，电芯列 20/20、16/16 全覆盖。

---

## v9.7.34 · 占位基准配置回填 + 低置信刷屏修复（2026-11-20，用户提问）

用户："评分配置上的低置信是什么意思，所有都有是不是没有展示的必要了"。

- 核实：SUV榜 20/20 全标低置信。根因是这批 auto_trims 新入池车的 `scored_trim` 全是占位值
  "待配置核验"，`find_ref` 失败→ref_fallback→全车 low。低置信真实含义是"基准配置名对不上、用近似配置做基准"，
  不是"分数证据不足"，全中就失去区分度。
- 修复1（数据）：15 款 scored_trim 精确回填为配置表里的真实配置名（find_ref 第1步命中）。
  两榜置信变为全 high，逐车低置信标记自然消失。
- 修复2（渲染）：低置信只在"有混合"（部分low+部分high/medium）时逐车标；全high不标；
  全low则在顶部统一提示"系统性基准问题"，不逐车刷。
- 副作用（诚实披露）：基准修正后 delta 基于正确参照点重算，SUV榜分数/名次微调——
  领克20升至第1、AION i60 从第2降至第5；已写进候选池 pool_meta.notes 自动披露。
- BUG-043 进回归（43/43），A/B 28/28 通过。
- 教训：标记类字段要有区分度才有意义；全中的标记要么修根因、要么改展示规则。

---

## v9.7.35 · bug 修复代码化（2026-11-20，用户明确要求）

用户："这份问题不要只改榜单，要把修改放到代码里面，把这次的bug记录下来，确保以后不出一样的问题"。

- 核查：v9.7.34 主要改的是**数据**（回填15款 scored_trim）+ 显示规则，
  `annotate_all()` 仍会在占位/匹配失败时静默 fallback 给全车 low——下次复发。
- **代码级根治**（trim_annotate.py）：
  - `is_placeholder_ref()` 识别"待配置核验/待核/待定/—"等占位值；
  - `resolve_ref()` 统一解析基准：真实名先精确/模糊匹配，占位则按指导价中位就近选代表配置，
    返回 (ref, is_fallback, basis) 写明依据；
  - 置信只在实际 ref=None 时降 low，占位自动解析不再全车 low；
  - out 增加 `ref_basis` / `ref_fallback` 字段供审计。
- **BUG-043 回归升级**：故意把 AION N60 scored_trim 注入为占位，验证自动解析出真实配置、
  conf≠low、配置分有差异——测试的是"下次同类输入是否安全"而非"当前数据是否干净"。
- 三榜重出：低置信标=0、电芯空=0，无回归。43/43、A/B 28/28 通过。
- 原则固化：**每次修 bug 都要问——这是只改了本次数据，还是下次同类输入也安全？**

---

## v9.7.36 · 打分逻辑指纹门禁（2026-11-20，用户要求防老数据混入）

用户："是否需要在车型池加skill版本号，skill升级后缓存全部作废，避免老版本数据造成新版本展示有误"。

- 核查：SKILL.md 有 version，但 pipeline/board_v2/render 无任何版本校验；
  候选池无生成版本字段；master_pool.config_fingerprint() 只覆盖权重、**不覆盖打分代码**——
  v9.7.31 快充delta、v9.7.34 基准解析这些改代码活动指纹不变，老分数会被当有效。风险真实存在。
- 新增 `score_fingerprint.py`：指纹 = 5个打分代码文件内容哈希 + 配置权重 + skill版本（16位）。
  `--stamp` 给候选池打基线、`--check` 校验、`--show` 查看。
- `pipeline.py` 加门禁：出榜前 `SF.check()`，不一致则 sys.exit(1) 拒绝，
  `--force-stale` 可强制（报告标注过期）。报告头部自动显示数据版本 vs 当前版本。
- 三候选池已打当前基线指纹（9.7.35/11375c19，其分数刚重算过）。
- 验证：临时改 trim_annotate.py 模拟升级→出榜被拒（退出码1），恢复后全绿。
- **权衡**：事实数据不作废（不随代码变、重采浪费），只拒绝老分数混新版本；
  改打分代码后需重算分数并重新 stamp（一次性成本）。
- BUG-044 进回归（44/44），A/B 28/28 通过。

---

## v9.7.37 · 内置证据采集工具，发布包自包含（2026-11-20，用户问"只装榜单引擎能用research-collector吗"）

用户问：research-collector 是我们自己的 skill，别人只装"可信购车榜单引擎"能否用？不能的话是否整合。

- 核查：发布包 102 文件零 research 字样；榜单引擎 pipeline 不依赖它（只 leaderboard.py --research 可选输入）；
  结论：**不整合则别人出榜能跑但证据质量下降**（质量全8.0/安全缺碰撞的短板补不上）。
- 整合（方案A自包含）：`research/` 目录随包发布 researcher.py + config.json + README.md
  （源自 research-collector，176KB，无硬编码路径、无重依赖）。build_release 放行该目录。
- **重要边界写进 README**：它是"站点画像+检索词生成+广告降权+博主评分"工具，
  不是懂车帝/抖音私有API爬虫；真正抓网页靠内置 domain_search/WebFetch。
  视频/评论是主观舆情，只作参考不进 8 维分（延续事实列不计分纪律）。
- 验证：解包后 `python3 research/researcher.py --list-sites` 独立运行成功，17 站点解析器齐全。
- BUG-045 进回归（45/45），包 0.74MB/105 文件。A/B 不受影响。

---

## v9.7.38 · research-collector 完全整合（2026-11-20，用户要求"完全整合+解决冲突+优化"）

用户："是否可以把 research-collector 完全整合到我们的skill里面，如果有冲突是不是可以解决一下，优化一下"。

- **冲突排查与解决**：
  1) 身份冲突：采集器 SKILL.md 自称独立"研究素材收集器"、写"交给 omni-debate 消费方"→
     方法论并入本 SKILL「统一证据采集」章节，去掉独立身份/对外契约表述。
  2) 过时依赖：researcher.py 有 5 处 orchestrator 引用、`--extract` 会 `import orchestrator`——
     而辩论引擎早已删除（SKILL.md:635 明确），跑 `--extract` 必 ImportError。
     → 删除 extract_sources 死函数(37行)、清 5 处引用、config 删 api_key_env；
     `--extract` 保留参数但优雅降级（提示走主路径，不再崩溃）。
  3) 重复：采集器"第0.5步候选池全量发现"与本引擎"候选池尽量收全"铁律重叠→合并为同一执行细则。
- 整合后 research/ 定位为"本引擎内建证据采集模块"（README 已改写），不再称外部 skill 来源。
- 保留 research-collector 原 skill 不动（本机其它场景仍可用），但本引擎**不再依赖**它。
- 完整性验证：SKILL有统一章节、脚本无 orchestrator/extract_sources、config无密钥、--list-sites/--list-topics可跑。
- BUG-046 进回归（46/46），A/B 28/28 通过。researcher.py 不参与打分，指纹门禁不受影响。

---

## v9.7.39 · 开源生态借鉴：逐配置采集自动化 + 归一化审计 + 字段规范（2026-11-20，新分支试验）

背景：调研市面上可借鉴的开源项目/数据源（报告见根目录 `开源项目与数据源调研_20261120.md`）。
按「借方法不照搬、不内置爬虫、不破坏主链路零依赖」原则，在本分支落地三项**增量工具**：

### 1 `trim_ingest.py` — 逐配置采集导入器（借 travisoa/vehicle-data-hub + swoiow/autohome）
- 解决最高优先级铁律「每款车必须有逐配置数据」的**采集瓶颈**：把外部配置表规范化成 `trims.json` 的 `trims[]`。
- 输入两种：① vehicle-data-hub 配置 Excel（sheet「详细配置表」，行=参数列=配置，openpyxl 可选）；
  ② swoiow/通用参数矩阵 CSV（首列参数名、首行配置名，标准库零依赖）。
- 字段映射可审计：每个字段记 `field_sources`（原始参数名 + 原值 + 换算值），价格元→万自动换算，
  高压平台/电芯/快充/CLTC/电池能量等 9 类参数对齐本项目字段。
- 严格车型匹配（EZ-60≠EZ-6、S5≠S）、与现有配置对照标 `new_trim/fill_missing/differs/same`。
- **只出补全建议，不写回 trims.json、不进分**；人工核验写回后跑 `trim_coverage.py` 校验。
- 纪律：不抓网页、不绕反爬，抓取交给专门工具/人工（沿用 SKILL「不内置爬虫」原则）。

### 2 `robust.normalization_audit()` — 归一化敏感性审计（借 scikit-criteria）
- 借鉴 scikit-criteria 的方法学空间穷举（arXiv:2509.24996：归一化方式可影响 20%–40% 名次）。
- 原 robust.py 只换聚合/权重，归一化写死在各方法内；本函数把归一化作为独立变量，
  5 种方式（linear/minmax/vector/zscore/sum）× 加权和，输出两两 Kendall τ + 跨归一化名次范围。
- 纯标准库，无 numpy 依赖。实测 16 款扩展榜：τ均值 0.920、最低 0.867，
  奔驰CLA新能源/小鹏MONA M03 跨归一化波动 3 位（2–5 名）——现有稳健性分析看不到这一层。
- 定位为**诊断工具**（不进主链路、不改三档判据），回答「名次是不是被归一化方式带偏」。

### 3 `schema_audit.py` — 逐配置字段规范对照（借 open-ev-data schema）
- 借鉴 open-ev-data（CDLA-Permissive）分层字段规范，定义 core/recommended/provenance 三级字段。
- 对照 `trims.json` 统计字段覆盖（配置级 + 车型级），电芯车型级对齐 trim_coverage 的车系级回退链。
- 实测 83 车型/519 配置：core 车型级平均 97.6%，但电芯**配置级仅 21.6%**（靠车系级 50 款兜底），
  voltage_platform 0%、battery_type 10.8% — 给出补采优先级。
- 只读审计、不写回、不进分；`--check` 可做门禁（core 车型覆盖低于阈值 exit 1）。

说明：三项均为独立工具/函数，**不修改榜单渲染、不改打分逻辑、不动指纹覆盖的打分代码**，
故不触发指纹门禁、不影响 A/B 与回归。本分支跑通后由用户决定是否合并主分支。

---

## v9.7.40 · 实际续航物理估算能力（2026-11-20，用户要求沉淀）

用户："把这套物理模型技能在 skill 上沉淀下来，以后别人问车辆的实际续航可以结合计算以及网上信息综合给出一个合理续航，缺少实际续航的车型也可以估算……物理模型是否要考虑轮胎以及厂家默认胎压的影响。"

### 能力：回答"某车实际能跑多少公里"
用 `ev_range_model.py`（**物理模型单一事实源**），结合物理计算 + 真实数据给出合理续航，
不再只把 CLTC 打个折。调用方式：
```
python3 ev_range_model.py --selftest          # 自检 + 真实达成率对照
python3 ev_range_model.py --demo              # 演示几款车的三场景续航
# 代码里：from ev_range_model import estimate; estimate(vehicle_dict, scenario_dict)
```

### 模型骨架（透明、每系数有网搜证据）
```
基准电耗 = 事实库真实电耗；缺失时物理回归 4.48×车重(t)+5.88×Cd×迎风面积+0.61（39款拟合 MAE 0.69）
场景电耗 = 基准 × 工况倍率 × 温度(仅电池活性) × 空调 × 驾驶 × 胎压 × 轮胎 × 载重 × 热管理 × 电压
有效续航 = 电量 ÷ 场景电耗 × 100
```
- **迎风面积** ≈ 宽×高×0.84；**高速倍率**按每车 CdA/吨 偏离中位数差异化（风阻大的车高速更费，
  解释方盒子效应），基准1.65 由懂车帝21车高速达成率均值~60%反推。
- **重要修正**：温度系数**只管电池活性**（极寒1.20，无空调约-10%~-20%），
  暖风**单独**由空调档位负责（按季节基线×用户档位）。旧版把暖风塞进温度又叠加空调=双重计算、
  冬季高估衰减。依据：中国汽车报实测"-20℃无空调仅减10%，空调+高速才是主因"。
- 电压平台/SiC 影响小（0.97~1.0，主要影响充电速度），诚实标注。

### 轮胎 / 胎压要不要建模？（用户问，结论：胎压要、轮胎类型谨慎）
网搜多源一致的量化证据：
- **胎压**：每低 0.1bar 滚阻+1.5%、续航损失 1-2%；电车原厂推荐冷胎普遍 2.6-2.9bar(轿车) /
  2.8-3.1bar(SUV)，高于油车 2.5bar（电池重）。冬季每降10℃胎压掉0.1bar，易被忽视。
  → **已建模** `TIRE_PRESSURE_MULT`：推荐=1.0(中性默认)/低0.1=1.015/低0.3=1.045/低0.5=1.075。
  默认按"原厂推荐"（不臆测用户胎压），已知偏低时用户选档扣分。
- **轮胎类型**：低滚阻胎可提升续航 5-8%，但逐车数据缺失（仅9款有文本描述），
  → **谨慎建模** `TIRE_TYPE_MULT`：unknown=1.0(默认不编造)/standard=1.0/low_rolling=0.95(保守取+5%)。
- 载重、热泵+电池预热也已建模（见模块常量表）。

### 与真实数据校准（内置 REAL_WORLD 表，防拍系数）
模型自检会对三场景算达成率并与实测对照，偏差需 ≤±12 个百分点：
| 场景 | 模型达成率 | 实测均值(懂车帝/汽车之家/易车) | 偏差 |
|---|---|---|---|
| 市区温和 | 92.9% | 90.3% | +2.6pp |
| 高速120 | 55.7% | 60.4% | -4.6pp |
| 冬季 | 52.0% | 55.9% | -3.9pp |

### 数据铁律延续
- 缺电耗/缺电量的车：能算就算（回归估算电量÷电耗），**算不出就标注"无数据不估算"，不编造续航**。
- 页面生成器 `build_energy_compare.py` 的物理常量从本模块 import（单一事实源），
  不在两处各写一套系数（防止漂移 = 不打补丁原则）。
- 本能力不改主榜打分；只用于"实际续航问答"和选车比较器的续航列。

---

## v9.7.41 · 归一化敏感性进三档判据（2026-11-20，用户要求「3位波动反馈进判据+全池重算」）

用户看到 v9.7.39 归一化审计发现「奔驰CLA新能源 / 小鹏MONA M03 跨归一化 2–5 名波动 3 位」后要求反馈进判据。

- **判据**：`leaderboard.tier_rules()` 新增 `t1_max_norm_swing`（默认 2 位）与 `norm_audit_enabled`（默认开）。
  `tier_report()` 调用 `robust.normalization_audit()` 取每车跨 5 种归一化的名次跨度；
  `swing > 阈值` 的车加进 `t1_blockers` → 不进第一档、降第二档，并在报告披露"跨 5 种归一化名次 X–Y"。
- **纪律**：只拦第一档，**绝不进第三档**——名次不稳是算法精度问题，不是产品硬伤，与「证据 C 不判劣」同理。
  `norm_audit=None` 听配置开关，`norm_audit=False` 可显式关闭；审计异常时优雅降级不阻断出榜。
- **不误伤验证**：改动前后对 16 款扩展榜逐车对比，档位完全一致（T1空/T2九款/T3七款、名单相同）——
  归一化判据是"补充拦截理由 + 披露"，没有把任何车从第一档错误降级。它拦的两款本就因证据/算法命中未达 T1。
- **全池重算**：改了 `leaderboard.py`（指纹覆盖文件），三个固化候选池已重新 `--stamp`
  （数据本体 candidates 哈希不变，只更新 `_generated_by`），三池指纹 check 全部通过。
- BUG-047 进回归（47/47）。HTML 榜单页只显示档位结果，判据细则在 report.md（渲染层披露未改，守页面确认铁律）。

---

## v9.7.42 · 联网优先铁律 + 申报库新鲜度门禁（2026-11-20，用户强制改逻辑）

用户原话：「逻辑都要直接改，第一步是先找所有信息，不要直接查本地表，这样一直会是旧数据。」
（针对盘点申报车时"先读本地旧表→发现过期→再联网"的顺序错误。）

### 改了什么（根因，不是补丁）
1. **「出榜数据闭环」五步顺序重写**：第一步=联网找全量最新信息（强制），第二步=本地表交叉核验（缓存定位）。
   加"顺序铁律（不许颠倒）"：本地表只作①复用稳定字段②交叉校验③联网失败兜底并标注"缓存·待核"，
   联网与本地冲突以联网为准，池内联网查不到的标"缓存·联网未证实"。
2. **申报表规则**："截止今天有哪些X"时效问题必须先联网，不许先读本地表当事实源。
3. **`filings_board.py` 加技术门禁**（防复发）：`STALE_DAYS=3`；`freshness_state(verified_as_of)` 检查
   `updated` 距今；超阈值在 stderr + 页面注入醒目"缓存快照·不可当作截止今天事实"告警；
   `load_filings(verified_as_of, strict_fresh)` 支持联网核实日期消除告警；CLI 加 `--freshness` / `--verified-as-of`；
   selftest 纳入门禁自测。原则：状态判断只能靠联网事实，不能靠本地文本关键词（实测 αS5 已上市被粗筛误列）。
4. **联网结果回写缓存**：核实到捷途环游者定档11-24、V8X HEV"近期上市"、家越07纯电11-27(800V/80.6kWh/CLTC651km)、
   极氪7X 2027款11-27；发现本地库漏收方程豹钛9/享界V8/iCAR V25/第三代唐/宝马iX3/ID.AURA L5，记入 verify_note 待补录。

### 回归
filings selftest OK（门禁+预估）、比较器 20/20、物理模型 selftest OK、主链 bug 47/47、SKILL 规则 6 项 PASS。
模拟本地表停 11-14 → stale=True 告警生效；联网核实到当天 → 告警消除。
