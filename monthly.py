#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""月度出榜（monthly.py）——每月一条命令，把整条链路跑完并留档。

链路（全程无模型调用，同一输入必得同一输出）：
  1 探测最新可用月份（懂车帝销量榜有没有该月数据）
  2 建月度目录（历史永不覆盖）
  3 基准候选池 → 当月副本
  4 AKShare 乘联会大盘（背景与校验）
  5 懂车帝逐月销量（近 6 个月中位数 → trend_sales）
  6 汽车之家官方级别 / 指导价对齐
  6a C-NCAP 官方成绩对齐（无成绩车 blend 插补，标 low）
  7 pipeline：闸门 → 打分 → 稳健性 → 三档 → 审查 → 报告
  8 与上月对比，写 INDEX.md

为什么月份要探测：
  乘联会与懂车帝的月度数据有滞后，15 号跑「上月」通常可用，但遇到长假会缺。
  探测失败就往前退一个月，而不是拿空数据算出一份空榜。

为什么候选池是「复制」而不是「发现」：
  自动发现新上市车型需要判断官方二级分类与主力动力类型，这一步目前没有可信的自动源。
  所以候选池是人工维护的基准文件，月度任务只刷新它的销量、级别与价格。
  新增车型必须人工加入 baseline，报告里会明确写这一点，不假装是自动的。

用法：
    python3 monthly.py                       # 自动探测月份，输出到默认根目录
    python3 monthly.py --month 202611        # 指定月份
    python3 monthly.py --force               # 同月重跑（覆盖该月目录）
    python3 monthly.py --skip-align          # 跳过汽车之家对齐（最慢的一步）
    python3 monthly.py --dry-run             # 只探测月份与路径，不执行
"""

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# 默认输出根目录：环境变量 > 本机历史归档（存在才沿用，保证老归档连续性）
# > skill 内 runs 目录。开源环境下前两者都不命中，自动落到第三档。
_LEGACY_ROOT = r"C:/Users/海南岛南霸天/WorkBuddy/2026-11-13-15-03-08/runs/car-ev-monthly"
ROOT_DEFAULT = (os.environ.get("CAR_EV_MONTHLY_ROOT")
                or (_LEGACY_ROOT if os.path.isdir(_LEGACY_ROOT)
                    else os.path.join(HERE, "runs", "car-ev-monthly")))
BASELINE_DEFAULT = os.path.join(HERE, "categories", "baseline_car_ev.json")
PY = sys.executable


def log(msg, fh=None):
    line = f"[{dt.datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    if fh:
        fh.write(line + "\n")
        fh.flush()


def run(cmd, fh, timeout=1800, allow_fail=False):
    """跑一条子命令，输出同时写进 run.log。返回 (ok, stdout)。"""
    log("$ " + " ".join(os.path.basename(c) if i == 1 else str(c)
                        for i, c in enumerate(cmd)), fh)
    t0 = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        log(f"  ✗ 超时（>{timeout}s）", fh)
        if allow_fail:
            return False, ""
        raise SystemExit(f"步骤超时：{cmd[1]}")
    cost = time.time() - t0
    if p.returncode != 0:
        log(f"  ✗ 退出码 {p.returncode}，耗时 {cost:.0f}s", fh)
        if p.stderr:
            fh.write("  --- stderr ---\n" + p.stderr[-3000:] + "\n")
        if not allow_fail:
            raise SystemExit(f"步骤失败：{cmd[1]}")
        return False, p.stdout
    log(f"  ✓ 耗时 {cost:.0f}s", fh)
    return True, p.stdout


def prev_month(y, m, back=1):
    for _ in range(back):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return y, m


def probe_month(fh, prefer=None, max_back=4):
    """从 prefer（默认上个月）往前找第一个有数据的月份。返回 (yyyymm, 说明)。"""
    import fetch_dcd_sales as D
    today = dt.date.today()
    y, m = prev_month(today.year, today.month)
    if prefer:
        y, m = int(str(prefer)[:4]), int(str(prefer)[4:])
        max_back = 1      # 指定了月份就只验它自己
    tried = []
    for i in range(max_back):
        ym = y * 100 + m
        try:
            rows, _ = D.fetch_all(str(ym), "1")
        except Exception as e:
            rows = []
            tried.append(f"{ym}: 接口异常 {type(e).__name__}")
        if rows:
            note = f"{ym}" + (f"（原定 {prefer} 不可用，已回退 {i} 个月）" if i else "")
            log(f"  目标月份 = {note}，纯电榜 {len(rows)} 款", fh)
            return ym, note
        tried.append(f"{ym}: 无数据")
        log(f"  {ym} 无数据，回退", fh)
        y, m = prev_month(y, m)
    raise SystemExit("连续 %d 个月都取不到销量数据：%s" % (max_back, "；".join(tried)))


def load_summary(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None


def tier_diff(prev, cur):
    """对比两个 summary 的三档成员，输出新增/掉档。"""
    out = []
    if not prev or not cur:
        return out
    for board, tiers in (cur.get("tiers") or {}).items():
        p = (prev.get("tiers") or {}).get(board) or {}
        for t in ("T1", "T2", "T3"):
            a, b = set(p.get(t) or []), set(tiers.get(t) or [])
            if not a and not b:
                continue
            up, down = b - a, a - b
            if up:
                out.append(f"| {board} · {t} | 新进 **{'、'.join(sorted(up))}** |")
            if down:
                out.append(f"| {board} · {t} | 移出 {'、'.join(sorted(down))} |")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", help="目标月份 YYYYMM（默认自动探测上个月）")
    ap.add_argument("--root", default=ROOT_DEFAULT, help="月度输出根目录")
    ap.add_argument("--baseline", default=BASELINE_DEFAULT, help="基准候选池 json")
    ap.add_argument("--category", default="car_ev")
    ap.add_argument("--trend", type=int, default=6, help="趋势销量取最近 N 个月中位数")
    ap.add_argument("--sliders", help='优先级滑杆，如 "省心=90,智驾=20"')
    ap.add_argument("--skip-align", action="store_true", help="跳过汽车之家级别/价格对齐")
    ap.add_argument("--force", action="store_true", help="该月目录已存在时覆盖")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    os.makedirs(a.root, exist_ok=True)
    probe_log = open(os.path.join(a.root, "_probe.log"), "a", encoding="utf-8")
    ym, note = probe_month(probe_log, a.month)
    mdir = os.path.join(a.root, str(ym))
    probe_log.close()

    if a.dry_run:
        print(f"目标月份 {ym}（{note}）\n输出目录 {mdir}\n基准池 {a.baseline}")
        return

    if os.path.exists(os.path.join(mdir, "INDEX.md")) and not a.force:
        print(f"{mdir} 已存在当月结果；如需重跑加 --force")
        return
    os.makedirs(mdir, exist_ok=True)

    logf = open(os.path.join(mdir, "run.log"), "w", encoding="utf-8")
    t_all = time.time()
    log(f"=== 月度出榜 {ym}（{note}）===", logf)

    if not os.path.exists(a.baseline):
        raise SystemExit(f"基准候选池不存在：{a.baseline}")

    # 1 基准池 → 当月副本
    base = os.path.join(mdir, "candidates_base.json")
    shutil.copy(a.baseline, base)
    data = json.load(open(base, encoding="utf-8"))
    log(f"基准候选池 {len(data['candidates'])} 款 → {os.path.basename(base)}", logf)

    # 2 乘联会大盘（失败不阻断：它只做背景校验）
    market = os.path.join(mdir, "market.json")
    run([PY, os.path.join(HERE, "fetch_sales.py"),
         "--digest", "--month", str(int(str(ym)[4:])), "--out", market],
        logf, timeout=300, allow_fail=True)

    # 3 逐月销量
    trend_out = os.path.join(mdir, "candidates_trend.json")
    ok, _ = run([PY, os.path.join(HERE, "fetch_dcd_sales.py"),
                 "--month", str(ym), "--trend", str(a.trend), "--candidates", base],
                logf, timeout=1800)
    if not ok or not os.path.exists(trend_out):
        raise SystemExit("趋势销量生成失败，终止（不拿旧数据冒充新月度）")

    # 4 官方级别 / 指导价对齐
    aligned = trend_out
    if not a.skip_align:
        al_out = os.path.join(mdir, "candidates_aligned.json")
        ok, _ = run([PY, os.path.join(HERE, "align_specs.py"),
                     "--src", trend_out, "--out", al_out,
                     "--report", os.path.join(mdir, "alignment_report.md")],
                    logf, timeout=1800, allow_fail=True)
        if ok and os.path.exists(al_out):
            aligned = al_out
        else:
            log("  ! 对齐失败，改用未对齐数据继续（级别/价格保留上月值）", logf)

    # 4a C-NCAP 官方成绩对齐。此前月度链路漏了这一步：安全分一直是人工值、
    #    crash 文本滞后，有成绩的 8 款车没被官方得分率覆盖。必须放在 pipeline
    #    之前、warehouse --build 之前，让报告与数据库用同一份安全分。
    #    默认 --fill-mode blend：纯中位插补会让 31 款无成绩车全部落到同一个数、
    #    safety 维 sd 归零、18% 权重失效，blend 保留相对序但仍受中位约束。
    cncap_ok, _ = run([PY, os.path.join(HERE, "sync_cncap.py"),
                       "--candidates", aligned, "--fill-mode", "blend"],
                      logf, timeout=300, allow_fail=True)
    if cncap_ok:
        log("  ✓ C-NCAP 官方成绩已对齐（无成绩车 blend 插补，safety_confidence=low）", logf)
    else:
        log("  ! C-NCAP 对齐失败，本月安全分沿用人工值（低置信，INDEX 会披露）", logf)

    # 4b 工信部备案 → 未上市预测（失败不阻断：预测是附加分区，不是正式榜）
    filings_out = os.path.join(mdir, "filings.json")
    forecast_out = os.path.join(mdir, "forecast.json")
    have_forecast = False
    ok, _ = run([PY, os.path.join(HERE, "filings.py"), "--spec-lib", "14"],
                logf, timeout=1800, allow_fail=True)
    if ok:
        ok, _ = run([PY, os.path.join(HERE, "filings.py"),
                     "--json", filings_out, "--md", os.path.join(mdir, "filings.md")],
                    logf, timeout=900, allow_fail=True)
    if ok and os.path.exists(filings_out):
        ok, _ = run([PY, os.path.join(HERE, "forecast.py"),
                     "--filings", filings_out, "--candidates", aligned,
                     "--out", forecast_out,
                     "--md", os.path.join(mdir, "forecast.md")],
                    logf, timeout=600, allow_fail=True)
        have_forecast = ok and os.path.exists(forecast_out)
        if have_forecast:
            log("  ✓ 未上市预测已生成", logf)
        else:
            log("  ! 预测生成失败，本月榜不带预测区", logf)

    # 4c 车质网投诉（car_crawler 改造）：增量抓取 + 聚合（失败不阻断）
    complaints_json = os.path.join(mdir, "complaints.json")
    have_complaints = False
    ok, _ = run([PY, os.path.join(HERE, "fetch_complaints.py"),
                 "--pages", "80", "--json", complaints_json,
                 "--md", os.path.join(mdir, "complaints.md")],
                logf, timeout=1200, allow_fail=True)
    have_complaints = ok and os.path.exists(complaints_json)
    if have_complaints:
        log("  ✓ 投诉聚合已生成", logf)
    else:
        log("  ! 投诉抓取失败，本月报告不带投诉区", logf)

    # 5 出榜
    outdir = os.path.join(mdir, "out")
    cmd = [PY, os.path.join(HERE, "pipeline.py"),
           "--category", a.category, "--candidates", aligned, "--out", outdir]
    if a.sliders:
        cmd += ["--sliders", a.sliders]
    if have_forecast:
        cmd += ["--forecast", forecast_out]
    if have_complaints:
        cmd += ["--complaints", complaints_json]
    run(cmd, logf, timeout=900)

    # 5b 官网配置事实独立报告（决策表 + 逐条依据；不改分，失败不阻断）
    run([PY, os.path.join(HERE, "spec_facts.py"), "--report",
         "--candidates", aligned,
         "--out", os.path.join(mdir, "spec_facts.md")],
        logf, timeout=300, allow_fail=True)

    # 6b 建仓：把当月数据写进 sqlite，历史上累积后可回测
    dbp = os.path.join(a.root, "car_ev.db")
    run([PY, os.path.join(HERE, "warehouse.py"), "--build",
         "--candidates", aligned, "--db", dbp], logf, timeout=300, allow_fail=True)

    # 6c 能力沉淀入库：备案/预测/第三方证据/当期真实名次/投诉。
    #    必须放在 --build 之后：build 会重建正式六表，放前面会被清掉。
    #    能力表（dim_filing 等）是 IF NOT EXISTS，build 会先搬走再放回，不清历史。
    if os.path.exists(filings_out):
        run([PY, os.path.join(HERE, "warehouse.py"), "--db", dbp,
             "--ingest-filings", filings_out], logf, timeout=300, allow_fail=True)
    if have_forecast:
        run([PY, os.path.join(HERE, "warehouse.py"), "--db", dbp,
             "--ingest-forecast", forecast_out], logf, timeout=300, allow_fail=True)
    run([PY, os.path.join(HERE, "warehouse.py"), "--db", dbp,
         "--ingest-thirdparty", "--candidates", aligned],
        logf, timeout=300, allow_fail=True)
    run([PY, os.path.join(HERE, "warehouse.py"), "--db", dbp,
         "--ingest-board", str(ym), "--candidates", aligned],
        logf, timeout=300, allow_fail=True)
    if have_complaints:
        run([PY, os.path.join(HERE, "warehouse.py"), "--db", dbp,
             "--ingest-complaints", complaints_json, "--month", str(ym)],
            logf, timeout=300, allow_fail=True)

    # 6e 历史序列增量：当月逐车型纯电销量 → fact_sales_history（累积品牌/车型史）
    run([PY, os.path.join(HERE, "backfill.py"), "--db", dbp,
         "--sales", "--month", str(ym)], logf, timeout=600, allow_fail=True)
    run([PY, os.path.join(HERE, "backfill.py"), "--db", dbp,
         "--brand-stats", "--asof", str(ym)], logf, timeout=300, allow_fail=True)

    cur = load_summary(os.path.join(outdir, "summary.json"))
    py_, pm = prev_month(int(str(ym)[:4]), int(str(ym)[4:]))
    prev = load_summary(os.path.join(a.root, str(py_ * 100 + pm), "out", "summary.json"))

    # 6 INDEX
    trend = load_summary(trend_out) or {}
    tcands = trend.get("candidates") or []
    n_pure = sum(1 for c in tcands if c.get("sales_scope") == "pure_bev")
    n_total = sum(1 for c in tcands if c.get("sales_scope") == "series_total")
    n_phev = sum(1 for c in tcands if c.get("power") == "PHEV")
    rows = tier_diff(prev, cur)
    ab = (cur or {}).get("airbags") or {}
    sf = (cur or {}).get("spec_facts") or {}
    tr = (cur or {}).get("trims") or {}
    L = [f"# {ym} 月度榜单", ""]
    L.append(f"> 数据月：**{ym}**（{note}）｜趋势销量：近 {a.trend} 个月逐月中位数")
    L.append(f"> 生成时间：{dt.datetime.now():%Y-%m-%d %H:%M}｜"
             f"耗时 {(time.time() - t_all) / 60:.1f} 分钟｜全程无模型调用")
    L.append("")
    if cur:
        L.append(f"- 候选 **{cur.get('in')}** 款 → 过闸 **{cur.get('passed')}** 款"
                 f"（落榜 {cur.get('dropped')}）")
        for board, tiers in (cur.get("tiers") or {}).items():
            cnt = {t: len(v) for t, v in tiers.items()}
            L.append(f"- **{board}**：第一档 {cnt.get('T1', 0)} ｜ 第二档 {cnt.get('T2', 0)}"
                     f" ｜ 第三档 {cnt.get('T3', 0)}")
        L.append("")
    if tcands:
        L.append(f"- 销量口径：**{n_pure}** 款纯电单独可采；**{n_total}** 款只有车系合计"
                 f"（纯电 ≤ 合计，榜上标 ≤）；**{n_phev}** 款判定为插混已移出")
        L.append("")
    L.append("## 审查结论")
    L.append("")
    for k, v in ((cur or {}).get("review") or {}).items():
        L.append(f"- **{k}**：{v}")
    L.append("")
    if rows:
        L += ["## 与上月对比", "", "| 榜单 · 档 | 变化 |", "|---|---|"] + rows + [""]
    elif prev:
        L += ["## 与上月对比", "", "三档成员无变化。", ""]
    else:
        L += ["## 与上月对比", "", "无上月归档，本次为首期。", ""]
    L += ["## 本期未自动完成的部分", "",
          "- **候选池主体仍需人工维护**：新增上市车型需人工加入 "
          f"`{os.path.basename(a.baseline)}` 后重跑；但「已报备未上市」的车型"
          "已由工信部备案自动覆盖（见预测区），备案→上市约 4-10 周。",
          "- **C-NCAP 成绩表本身仍需人工累积**：官网无公开接口，`cncap_scores.json`"
          " 靠人工核验增量维护；但**成绩表 → 候选池的对齐已自动完成**（`sync_cncap.py`，"
          "命中按官方得分率覆盖，无成绩车按 blend 插补并标 `safety_confidence=low`）。"
          " 懂车帝实测榜的加速/制动/麋鹿/充电曲线仍靠人工记录数值（有反爬）。",
          "- **安全分口径**：本期 fill-mode=blend（α=0.5，人工分与中位各半）。"
          "纯中位插补会让无成绩车全部同分、safety 权重失效；`--fill-mode median/manual`"
          " 可回退对照。",
          "- **第三方证据靠人工入库**：对撞/拆车/刚度视频由人工核验后写入"
          " `categories/thirdparty_tests.json`，自动抓视频不可靠。",
          f"- **被动安全气囊按 trim 人工核验**：`categories/airbags.json` 本期覆盖 "
          f"**{ab.get('verified', '—')}/{ab.get('total', '—')}** 款"
          f"（{ab.get('coverage_pct', '—')}%）。未核验的写 null，不判劣、不参与"
          "加减分；`airbags.py --todo` 列待补清单。有官方碰撞成绩的车不叠加配置分。",
          f"- **官网配置事实库只记事实、不改分**：`spec_facts.py` 的 "
          "`SCORING_ENABLED=False` 有前后 scores 深比较断言；本期采集 "
          f"**{sf.get('verified', '—')}/{sf.get('total', '—')}** 款，"
          f"16 项细分维度分层处置，**{len(sf.get('blocked_dims') or [])} 项**待准入维度"
          "因覆盖率 <70% 整条禁用。逐条依据见 `spec_facts.md`。",
          f"- **具体配置阶梯只列事实、给场景分支，不加分**：`trims.py` 的 "
          "`SCORING_ENABLED=False` 对 `scores/price/trim` 做前后深比较断言；本期逐配置核验 "
          f"**{tr.get('verified', '—')}/{tr.get('total', '—')}** 款，识别 "
          f"**{tr.get('jumps', '—')}** 条跃迁、带内加价建议 "
          f"**{tr.get('actionable_models', '—')}** 款。"
          "电芯供应商只分类证据形状、不排等级；评分配置价格与逐配置表冲突的只披露不擅改"
          "（会动闸门）。逐配置表见 `out/trims.md`。",
          "- **非阻断步骤失败会缺分区**：乘联会大盘、工信部备案导出、车质网投诉抓取"
          "任何一步失败都不终止链路，但对应分区会缺席或沿用同月归档；"
          "以 `run.log` 的 ✗ 记录为准，不要把缺分区误读成「本月无数据」。",
          "- **PPH 对新车失效**：样本 <30 条的百车故障数不作结论。", ""]
    if not cncap_ok:
        L.append("- **⚠ 本期 C-NCAP 对齐失败**：安全分沿用人工值，未覆盖官方得分率，"
                 "跨月对比时口径不一致。")
    L += ["## 产物", "",
          "| 文件 | 内容 |", "|---|---|",
          f"| `out/report.md` | 完整报告（闸门/三档/审查/稳健性/权重审计/三榜并列/预测区） |",
          f"| `out/summary.json` | 机读摘要（含 forecast / airbags / spec_facts / trims） |",
          f"| `out/trims.md` | 具体配置阶梯、加价-增配跃迁、带内建议与带外示例 |",
          f"| `spec_facts.md` | 细分维度决策表 + 官网配置事实逐条依据 |",
          f"| `candidates_trend.json` | 已回填趋势销量的候选池 |",
          f"| `alignment_report.md` | 汽车之家级别/价格对齐明细 |",
          f"| `candidates_aligned.json` | 已回填销量/级别/指导价/C-NCAP 安全分的候选池 |",
          f"| `filings.json` / `filings.md` | 工信部备案解析（本期新报车型） |",
          f"| `forecast.json` / `forecast.md` | 未上市预测（概率+名次区间） |",
          f"| `market.json` | 乘联会大盘快照 |",
          f"| `run.log` | 逐步骤耗时与错误（含 sync_cncap 命中/插补数） |", ""]
    open(os.path.join(mdir, "INDEX.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")

    log(f"=== 完成，总耗时 {(time.time() - t_all) / 60:.1f} 分钟 ===", logf)
    logf.close()

    # 根目录总索引
    idx = os.path.join(a.root, "README.md")
    months = sorted(d for d in os.listdir(a.root)
                    if d.isdigit() and os.path.isdir(os.path.join(a.root, d)))
    lines = ["# 月度榜单归档", "", "| 月份 | 状态 | 入口 |", "|---|---|---|"]
    for m in reversed(months):
        has = os.path.exists(os.path.join(a.root, m, "INDEX.md"))
        lines.append(f"| {m} | {'已完成' if has else '进行中/失败'} | `{m}/INDEX.md` |")
    lines += ["", "每月 15 日由定时任务生成；同月重跑用 `--force`。", ""]
    open(idx, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print(f"\n[ok] {os.path.join(mdir, 'INDEX.md')}")
    print(f"[ok] {idx}")


if __name__ == "__main__":
    main()
