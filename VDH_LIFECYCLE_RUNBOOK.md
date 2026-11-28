# 本机实跑 vehicle-data-hub · 生命周期自动化 + 口碑合并 标准流程

> 适用：把《阳神》车辆榜的「生命周期确证层」和「口碑样本」从手工/种子升级为
> **本机实跑 vehicle-data-hub 后的真实全量数据**。两项都靠 vehicle-data-hub 的输出，
> 所以合并成一套流水线。
>
> ⚠️ 本文件在 WorkBuddy 沙箱内撰写，**沙箱无 Chrome/LibreOffice/登录态 + 国内网络，
> 下列 `main.py` 抓取命令均需在用户本机执行**。沙箱内已完成的是：脚本逻辑（含 `--selftest`
> 合成自检通过）与手工层重生成。

---

## 0. 前置：克隆并装好 vehicle-data-hub

```bash
git clone https://github.com/travisoa/vehicle-data-hub.git
cd vehicle-data-hub
python -m venv .venv && .venv/bin/activate
pip install -r requirements.txt        # 见仓库 README
# 浏览器抓取需要 Chrome + 登录态（懂车帝/汽车之家会风控），按 README 配置
#   --browser-login / --cookies / --cookie-file
```

> 仓库 README 已确认：公告库 `data/announcement_site.sqlite` **不以车型名/ VIN / dcd_series_id
> 为主键**，而是 `商标 + 车辆型号 + 批次 + 产品ID`；市场名与公告名经常不一致。因此——
> **生命周期主信号不要依赖公告库做自动 join，改用懂车帝车系状态（有 dcd_series_id 主键）**。

---

## 1. 生命周期自动化（主交付）

### 1.1 产出「懂车帝车系状态」JSON（主键 join，最干净）

我们池子里 **187/187 车都有 `dcd_series_id`**（ICE 池已验证），懂车帝车系页直接暴露
在售/停售状态，且 vehicle-data-hub 能抓 dcd 数据 → 这是有主键的干净信号。

两种取法（选你能跑通的）：

**A. 用 vehicle-data-hub 的 dcd 抓取**（推荐，若其 dcd 爬虫可用）
该步骤在 **vehicle-data-hub 仓库**内完成（属外部依赖工具，不在本 skill 包内）：把本榜所有
`dcd_series_id` 导成一行一个，用其 dcd 抓取入口按车型取 `sale_status`，再整理成下面 1.3 的
`dcd-status.json` 形态。具体命令以 vehicle-data-hub 仓库的 README 为准。

**B. 直接打懂车帝车系 API / 页面**（若 dcd 爬虫不便）
懂车帝车系页 `/series/<dcd_series_id>` 的接口返回 `sale_status` 类字段
（`在售 / 停售 / 预售 / 待售`）。批量请求这些 id，落盘即可。

无论哪种，最终整理成 **`dcd-status.json`**（脚本两种形态都认，见下）。

### 1.2 产出「工信部公告」提取 JSON（次信号，需名称对照）

公告库无车型名主键，所以这一步是**半自动**：在 **vehicle-data-hub 仓库**（外部依赖工具，
不在本 skill 包内）用其 `gonggao query --vehicle-name <市场名>` 入口逐车查，人工/脚本把结果
落成 `status`。判断口径：看输出是否仍在公告内 / 已无延续批次 → `discontinued` 或 `active`；
具体命令以 vehicle-data-hub 仓库的 README 为准。

落成 **`gonggao.json`**（见 1.3）。**只有当公告结论与 dcd 冲突时它才生效**（优先级：
手工 > 公告 > dcd > 症状复核 > 默认），日常绝大多数车由 dcd 覆盖即可。

### 1.3 两份输入的标准形态（脚本已容错解析）

`dcd-status.json`（主键 = dcd_series_id，最推荐）：
```json
{
  "series": [
    {"dcd_series_id": 546, "name": "RAV4荣放", "sale_status": "onsale",
     "url": "https://www.dongchedi.com/series/546", "note": ""},
    {"dcd_series_id": 4857, "name": "星越L", "sale_status": "offsale", "url": "", "note": ""}
  ]
}
```
也接受简写 `{ "546": "onsale" }` 或 `{ "546": {"sale_status":"onsale","url":"..."} }`。
`sale_status` 取值：`onsale/在售`、`offsale/停售/停产`、`presale/预售`、`future/待售`。

`gonggao.json`（主键 = 车型市场名）：
```json
{
  "entries": [
    {"model": "秦L DM", "status": "discontinued", "date": "2026-07",
     "note": "公告无延续批次", "url": "https://...", "source": "工信部公告"}
  ]
}
```
`status` 必须落在词表：`active / new_launch / facelift_pending / clearance / discontinued`。

### 1.4 一键合并（重生成 lifecycle_status.json）

```bash
cd ~/.workbuddy/skills/omni-debate
python lifecycle_fetch.py \
  --manual   <运行时 categories 子目录>/lifecycle_status.json \
  --pool     /path/to/runs/car-ice-4boards/ice_pool_full.json \
  --pool     /path/to/runs/car-ev-4boards/candidates_trend.json \
  --dcd-status /path/to/vehicle-data-hub/dcd-status.json \
  --gonggao    /path/to/vehicle-data-hub/gonggao.json \
  --out      <运行时 categories 子目录>/lifecycle_status.json \
  --report   /path/to/runs/car-ice-4boards/lifecycle_autogen.md
```

分层优先级（高优先 wins，命中即停）：
1. **manual** — 现有 15 条手工确证，永不自动覆盖（带原因保留）
2. **gonggao** — 工信部公告（监管金标准，仅在冲突时压过 dcd）
3. **dcd-status** — 懂车帝车系状态（按 dcd_series_id 主键，覆盖 99% 车型）
4. **症状复核** — 无硬源且销量骤降严重 → 标 `needs_review`（不自动判，避免假阳性）
5. **default** — 隐式在售（不写库，`lifecycle_confirm.resolve` 回退即可）

输出：
- `lifecycle_status.json`：只写**非平凡判定**（手工 + dcd + gonggao + 待核），纯默认在售不入库
- `lifecycle_autogen.md`：分层命中统计 + 逐车型表 + 待核清单
- 每次重跑都**保留手工条目**（Layer 0），幂等可重复

> 验证脚本逻辑（无需本机抓取）：`python lifecycle_fetch.py --selftest`

---

## 2. 口碑样本合并（选项 A，顺带交付）

`koubei_merge.py` 把真实口碑样本数并入 ICE 池，抬过 `MIN_USERS=30` 阈值，降低插补率。

```bash
cd ~/.workbuddy/skills/omni-debate
python koubei_merge.py \
  --pool   /path/to/runs/car-ice-4boards/ice_pool_full.json \
  --vdh-dir /path/to/vehicle-data-hub/downloads/benchmark \
  --out    /path/to/runs/car-ice-4boards/ice_pool_full.json \
  --report /path/to/runs/car-ice-4boards/koubei_merge.md
```

- `--vdh-dir` 指向 vehicle-data-hub 的 `downloads/benchmark/`（缓存 `<车型>.json`，含懂车帝口碑/销量）。
  脚本容错读 `review_count / koubei_users / users / data.reviewCount` 等可能字段名。
- 取多源**最大值不降级**（种子与真实不会互相压低）。
- 若还没跑 vehicle-data-hub，可先用 `koubei_seed.json`（种子估计值）占位：
  `--seed <运行时 categories 子目录>/koubei_seed.json`（当前 v6.5 就是这层）。

跑通后 ICE 池 `koubei_users≥30` 覆盖率会从 45.5%（种子）进一步抬升，评分期插补率
（现为 36.9%）继续下降。

---

## 3. 重跑双榜

生命周期/口碑数据更新后，重跑两榜让改动生效：

```bash
# 纯电四榜（airbag_gate 第1档气囊闸已在 pipeline.py 接通）
python pipeline.py --category car_ev --candidates <候选池.json> --out <输出目录>

# 燃油混动四榜（用本 skill 内置的 ice_board.py）
python ice_board.py --pool ice_pool_full.json \
   --category car_ice_phev --out 四榜报告.html --top 10
```

---

## 4. 诚实边界（务必知悉）

1. **沙箱未实跑 `main.py` 抓取**——本文件与脚本逻辑在沙箱完成，真实 dcd/gonggao 数据需你本机执行 1.1–1.2。
2. **gonggao 名称对照脆弱**：公告库无车型名主键，1.2 是半自动；日常靠 dcd（主键）兜底，gonggao 只在冲突时起作用，影响面小。
3. **dcd 车系状态 ≠ 监管停产认定**：dcd 停售可能是渠道/区域行为，但作为「清库/退市」信号足够；要 100% 严谨仍以公告为准（gonggao 层覆盖）。
4. **症状复核只标不判**：无硬源且骤降严重的车进 `needs_review`，需人工确认，绝不会自动打成 discontinued（延续 (b) 防假阳性的纪律）。
5. **口碑字段名未公开**：vehicle-data-hub 的 benchmark JSON 内部字段 README 未列全，`koubei_merge.py` 已做容错，若实跑发现新字段名，加到解析列表即可。
