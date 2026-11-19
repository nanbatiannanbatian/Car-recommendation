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
version: 8.3.8
author: "nanbatiannanbatian"
---

# Car recommendation —— 可信购车榜单引擎（v8.3.8）

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

| 档 | 判据 | 回答的问题 |
|---|---|---|
| 第一档 | 基准算法命中 ≥80% + 全情景命中 ≥60% + 证据 A + 趋势月销 ≥2000 | 能不能直接买（不用取舍） |
| 第二档 | 无第三档风险，但未达第一档 | 要不要按偏好取舍 |
| 第三档 | 证据 C / 无交付 / 月销 <500 / 人工标注硬伤 | 现在该不该看它 |

阈值全部写在 `categories/<id>.json → tiering`，改口径不必改代码。

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

### 提问闭环（用户要求的「先按提问去懂车帝销量榜取候选，池里有的直接用，没有的重新采集」）
1. 提问 → `discover.py` 拉懂车帝四口径销量榜 → 当期在售车型清单
2. `master_pool.py --discover-in discover.json` → 池内车刷新销量，池外车建 needs_data 待采集档案
3. `master_pool.py --ingest <任意候选池.json>...` → 把已采集 scores 的池幂等吸收（补空缺、记来源、不覆盖）
4. `master_pool.py --query --cls 中型SUV --power BEV --body SUV --price-max 40 [--ready-only]` → 标签检索
5. `board_v2.py` 用检索结果出榜——候选来源从此唯一，不再临时拼池

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
