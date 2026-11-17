# Car Recommendation · 可信购车榜单引擎

> 纯函数、无 LLM、可复现的汽车榜单引擎。把「取数 → 口径闸门 → 打分 → 10 种 MCDA 交叉 → 三档 → 出榜」做成一条**可审计**的流水线；品类差异全部放进 `categories/*.json`，换品类只换配置、不改代码。

## 这是什么

- 数据源：懂车帝（逐月销量 / 车系在售状态）、汽车之家（官方级别 / 厂商指导价 / 口碑 / 参数页）、C-NCAP、C-IASI、工信部购置税目录、乘联会、车质网、B站（视频证据初筛）。
- 榜单：纯电四榜、燃油混动四榜、电池优先对照榜、月度出榜、未上市预测。
- 无 LLM、无辩论：同一输入必得同一输出，名次可复现、可定位。

## 快速开始

```bash
pip install -r requirements.txt

python3 monthly.py --dry-run                        # 月度全链路（探测月份→建池→取数→出榜→留档）
python3 pipeline.py --category car_ev --candidates cands.json --out runs/x
python3 ice_board.py --pool ice_pool_full.json --category car_ice_phev --out runs/ice
python3 ev_battery_board.py --candidates candidates.json --out runs/ev
```

## 目录

| 文件 / 目录 | 作用 |
|---|---|
| `SKILL.md` | 口径协议、模块说明、版本记录 |
| `ARCHITECTURE.md` | 架构与运维手册（每个 py 的作用、流程、问题清单、可借鉴开源） |
| `categories/` | 品类配置与事实库（维度权重、闸门、气囊/碰撞/配置/生命周期/口碑/电池偏好档） |
| `ranking.py` / `leaderboard.py` | 口径闸门 + 品类无关榜单引擎 |
| `robust.py` / `verify_mcda.py` | 10 种 MCDA 稳健性 + 交叉验证 |
| `fetch_*.py` / `ice_*.py` | 取数层 与 燃油混动专用链 |
| `ev_battery_board.py` | 电池优先对照榜（用户偏好参考档，不改默认分） |

## 红线与边界

- `SCORING_ENABLED=False`：事实库（气囊 / 配置 / 第三方）只写事实、不改分，apply 前后对 scores 做深比较断言。
- 价格只认**官方指导价**；级别只认**官方二级分类**；促销价 / 经销商价不入闸门。
- 第三方证据封顶：厂商口径合计 ±0.6、单车合计 ±1.5。
- 不硬凑十款；证据三态（有 / 确认无 / 未核验）；数据缺口如实披露。

## 免责声明

仅供个人研究与导购参考，**不构成购买建议**。数据版权归各来源站点所有；本仓库**不包含**抓取的原始数据本体、登录态或任何密钥。

## License

MIT，见 [LICENSE](./LICENSE)。
