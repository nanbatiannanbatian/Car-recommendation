# 证据采集工具（内置自 research-collector）

本目录是榜单引擎的**可选证据采集增强**，随包发布、无需另装 skill。
脚本 `researcher.py` + `config.json` 来自 research-collector（176KB，无第三方重依赖，无硬编码路径）。

## 它是什么 / 不是什么

- **是**：站点画像 + 检索词生成 + 广告软广识别 + 博主可信度评分 + 结构化输出（research.json）
- **不是**：懂车帝/抖音的私有 API 直连爬虫。脚本里的 `site_query` 只是生成 `site:域名` 检索词，
  真正的网页抓取由 WorkBuddy 内置 `domain_search` / `WebFetch` 完成（agent 主路径）。
  有反爬/登录墙的平台（抖音/小红书/淘宝）纯脚本抓取常失败，走检索摘要更稳。

## 对榜单的价值（补两个证据空白）

| 榜单短板 | 本工具能补什么 | 站点 |
|---|---|---|
| "分数非实测：冬测续航/充电曲线/麋鹿未纳入" | 懂车帝**实测数据库**（加速/刹车/油耗/续航） | `dongchedi` |
| 质量维度全8.0、全low置信（投诉证据空白） | 车质网投诉量/典型故障 + 黑猫投诉 | `12365auto`,`heimao` |
| 安全多数缺官方碰撞成绩 | C-NCAP / 中保研 C-IASI 碰撞成绩（注意年款与规程版本） | `cncap`,`ciasi` |
| 视频/测评软文污染 | 广告识别（查小黄车/报备/送测/合作）+ 博主可信度降权 | 全平台 |

## 用法（在 skill 根目录执行）

```bash
python3 research/researcher.py --list-sites                    # 17 个已注册站点
python3 research/researcher.py --list-topics                   # 内置垂类画像
python3 research/researcher.py --query "元PLUS 实测 续航 投诉 碰撞" \
    --sites dongchedi,12365auto,cncap,ciasi --out research.json
python3 research/researcher.py --detect-ads research.json --inplace      # 广告降权
python3 research/researcher.py --score-authors research.json --inplace   # 博主评分
```

产出 `research.json` 可作为 `leaderboard.py --research research.json` 的可选输入（证据覆盖层）。
**注意**：`--detect-ads` / `--score-authors` 需要 research.json 里有 sources 字段，没有则跳过。

## 榜单使用纪律（别让舆情污染事实分）

1. **实测数据 > 一手 > 二手**；有数字+单位+测量语境才算量化实测。
2. 视频/评论是**主观舆情**，只作参考列/证据，**不进 8 维打分**（延续"事实列不计分"纪律）。
3. 懂车帝/抖音测评恰饭/商单密集，必须先跑广告识别再采信。
4. 老款碰撞成绩不能套新款；规程改版后星级不可直接横比。
5. 投诉要结合销量看密度，优先看"同年款同故障集中"。

## 没有本工具时的降级行为

榜单引擎的 `pipeline.py` 不依赖本目录——没有它照样出榜，只是证据采集退化到裸 `domain_search`，
缺站点画像/广告降权/博主评分，质量与安全维度更容易证据不足（表现为全8.0/缺碰撞）。
