#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""榜单全链路（pipeline.py）——一条命令出榜，不经过多模型辩论。

为什么把辩论从链路里去掉：
  实测同一个题目（12–18 万纯电中型车）跑了 5 轮双辩手 + 裁判，辩论产出的是
  「分歧清单」，而分歧清单的每一项都能由数据机械算出来，且辩论本身还引入了三类错误：
    · 越界——裁判把候选池外的车型排进榜（零跑C01），又因「口径存疑」剔除了本在池里的车；
    · 无法裁决——辩手没有检索权，两套销量口径互相矛盾时只能互不相让；
    · 不可复现——同一问题两次跑，名次会变，且无法定位是哪一轮引入的。
  现在这些工作分别交给：robust（方法间一致性）、weightaudit（权重来源）、
  review（确定性质证）。三者都是纯函数，同一输入必得同一输出。

链路：
  取数 → 闸门 → 打分 → 稳健性 → 三档 → 反方审查 → 合成报告

用法：
    python3 pipeline.py --category car_ev --candidates cands.json --out runs/x
    python3 pipeline.py --category car_ev --candidates cands.json --sliders "省心=90"
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from modules.engine import leaderboard as L
from modules.engine import ranking
from modules.engine import thirdparty
from modules.engine import airbags
from modules.engine import ground_clearance
from modules.engine import spec_facts
from modules.engine import trims
from modules.board import forecast as FC
from modules.engine import quality_score as QS
import paths


def parse_sliders(s):
    out = {}
    if not s:
        return None
    for part in s.replace("，", ",").split(","):
        if not part.strip():
            continue
        k, v = (part.split("=", 1) if "=" in part else part.split(":", 1))
        out[k.strip()] = float(v)
    return out


def gate_report(cands, cfg, board=None):
    """跑准入闸门，返回 (通过, 落榜, 报告)。

    gates 可以挂在 board 上（四个分榜的价格带上限不同：16 万 / 18 万），
    优先级 board.gates > cfg.ranking.gates > cfg.gates。
    """
    gates = ((board or {}).get("gates")
             or (cfg.get("ranking") or {}).get("gates")
             or cfg.get("gates") or {})
    if not gates:
        return cands, [], "> 未配置 gates，跳过准入检查。"
    ok, bad = [], []
    for c in cands:
        try:
            r = ranking.apply_gates(c, gates)
        except Exception as e:
            # v10.64 根因修复：旧版 except 里 ok.append(c) 放行——闸门代码报错
            # 反而让问题车过闸（fail-open），与"闸门失败不许上线"的纪律相反。
            # 改为 fail-close：按"闸门无法判定"落榜并写明原因，绝不静默放行。
            r = {"pass": False, "checks": [],
                 "why": f"闸门判定异常（fail-close，不静默放行）：{type(e).__name__}: {e}"}
        (ok if r["pass"] else bad).append({**c, "_gate": r})
    band = gates.get("price_band")
    head = f"## 准入闸门（{len(cands)} → {len(ok)} 款）"
    if band:
        head += f" · 价格带 ≤{band[1]:g} 万"
    lines = [head, ""]
    if bad:
        lines += ["| 落榜 | 未过项 | 说明 |", "|---|---|---|"]
        for c in bad:
            lines.append(f"| {c['model']} | {'、'.join(c['_gate']['failed'])} | "
                         f"{'；'.join(c['_gate']['notes']) or '—'} |")
    else:
        lines.append("全部通过。")
    return ok, bad, "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", required=True)
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--sliders", help='优先级滑杆，如 "省心=90,智驾=20"')
    ap.add_argument("--top-k", type=int, default=3)
    ap.add_argument("--forecast", help="未上市预测 JSON（filings+forecast 产物）"
                                       "；给出则在每榜后附「未上市预测区」")
    ap.add_argument("--complaints", help="车质网投诉聚合 JSON（fetch_complaints 产物）"
                                         "；给出则报告附「投诉反馈」参考区")
    ap.add_argument("--quality", action="store_true",
                    help="把质量与品控作为第 8 维进综合打分；可与 --complaints 联用")
    ap.add_argument("--score-audit", action="store_true",
                    help="附「人工分核算审计」：逐维度盘点算分链路 + 从 evidence 硬参数按规则算客观分与人工分对照"
                         "（只审计不改分，差异是待核实线索）")
    ap.add_argument("--no-thirdparty", action="store_true",
                    help="跳过第三方实测/拆车证据（默认启用，走 adjust 封顶通道）")
    ap.add_argument("--no-powertrain-efficiency", action="store_true",
                    help="跳过三电能耗效率修正（默认启用，全榜统一±0.4封顶；仅 A/B 对照用）")
    ap.add_argument("--no-range-achievement", action="store_true",
                    help="跳过续航达成率调整（默认启用，物理估算高速达成率榜内中位锚±1.0；仅 A/B 对照用）")
    ap.add_argument("--no-crash-verify", action="store_true",
                    help="跳过出榜前的逐车联网碰撞核验（默认启用；仅 A/B/断网对照用）")
    ap.add_argument("--no-crash-cache", action="store_true",
                    help="忽略当天核验缓存、强制全部重新联网抓取（默认当天命中复用，跨天自动失效）")
    ap.add_argument("--no-cell-verify", action="store_true",
                    help="跳过电池来源（电芯品牌）联网核验（默认对缺失车型联网查并标注原因）")
    ap.add_argument("--no-cell-cache", action="store_true",
                    help="忽略当天电芯核验缓存、强制全部重新联网（默认当天复用，跨天失效）")
    ap.add_argument("--allow-degraded-network", action="store_true",
                    help="仅当联网主通道失败时允许降级出榜（标记 degraded；"
                         "官网有本地无/数值冲突永远阻断，此开关不适用）")
    ap.add_argument("--no-config-anchor", action="store_true",
                    help="判档改用候选池车系基础分（回退到 v9.7.42 老口径，仅供 A/B 对照；默认用主榜同款最高配置分）")
    ap.add_argument("--airbag-gate", action="store_true",
                    help="把「气囊基线已核验（前排侧气囊+前后排头部气帘齐备）」"
                         "作为第一档硬门槛；未核验的车不降分、只不进第一档")
    ap.add_argument("--crash-gate", action="store_true",
                    help="把「有官方碰撞成绩（C-NCAP/C-IASI）」作为第一档硬门槛；"
                         "只读 safety_basis，不采信人工填写的 evidence_status")
    ap.add_argument("--no-airbags", action="store_true",
                    help="跳过被动安全气囊事实列与无成绩车受限修正（回测对照用）")
    ap.add_argument("--no-ground-clearance", action="store_true",
                    help="跳过最小离地间隙事实列与 safety 受限修正（回测对照用）")
    ap.add_argument("--no-spec-facts", action="store_true",
                    help="跳过官网配置事实节（该节本身不改分，仅作回测对照）")
    ap.add_argument("--no-trims", action="store_true",
                    help="跳过具体配置阶梯与带内配置推荐（默认启用，回测对照用）")
    ap.add_argument("--no-html", action="store_true",
                    help="不随报告生成 HTML 榜单页（默认生成，需 board_v2 链路）")
    ap.add_argument("--as-of", default=None, help="HTML 页面销量数据月标注(YYYYMM)，默认自动取上个月；出榜须让用户看到基于哪月销量")
    ap.add_argument("--universe", help="候选池完整性盘点的同级权威车型清单JSON(默认 categories/universe_<category>.json)")
    ap.add_argument("--skip-universe-gate", action="store_true", help="跳过完整性门禁(须在报告说明为何同级漏收车无需处理)")
    ap.add_argument("--allow-discontinued", action="store_true", help="允许已停产停售车参与(默认硬拦;须在报告说明原因)")
    ap.add_argument("--skip-discovery-gate", action="store_true", help="跳过候选发现门禁(须在报告说明为何池外高销量车无需判定)")
    ap.add_argument("--skip-lifecycle-gate", action="store_true", help="跳过生命周期症状门禁(须在报告说明为何销量骤降车无需核验)")
    ap.add_argument("--no-space-score", action="store_true", help="不按硬数据重算space分(默认按轴距+后备箱算)")
    ap.add_argument("--skip-consistency-gate", action="store_true", help="跳过条目内一致性门禁(须在报告说明为何note与结构字段矛盾不是错误)")
    ap.add_argument("--skip-sibling-gate", action="store_true", help="跳过同源车评分一致性门禁(须在报告说明为何无据差异合理)")
    ap.add_argument("--skip-safety-gate", action="store_true", help="跳过安全分严谨性门禁(须在报告说明为何成绩映射分可偏离)")
    ap.add_argument("--html-title", help="HTML 页面标题")
    ap.add_argument("--skip-experience-gap", action="store_true",
                    help="跳过体验证据缺口门禁(须在报告说明为何文本实测无需入库)")
    ap.add_argument("--no-evidence-cap", action="store_true",
                    help="不做安全分举证封顶（仅 A/B 对照，验证封顶确实改变分数）")
    ap.add_argument("--no-space-cap", action="store_true",
                    help="不做空间硬数据一致性封顶（仅 A/B 对照，验证 space 虚高确实被压）")
    ap.add_argument("--no-value-residual", action="store_true",
                    help="不做性价比价格残差法（仅 A/B 对照，回退人工 value 分）")
    ap.add_argument("--no-auto-safety", action="store_true",
                    help="不按官方成绩自动映射 safety 分（仅 A/B 对照；正常出榜不要关）")
    ap.add_argument("--no-backfill", action="store_true",
                    help="不回填 trims 官方指导价到候选池（仅 A/B 对照用；正常出榜不要关）")
    ap.add_argument("--no-sales-backfill", action="store_true",
                    help="不回填懂车帝权威逐月销量到候选池（仅 A/B 对照；正常出榜必须开——"
                         "候选池 trend_sales 是手填常量，曾在阿维塔07/Model Y 等差 8 倍）")
    ap.add_argument("--sales-snapshot", help="懂车帝销量离线快照 json（零网络，离线复现/回测用）")
    ap.add_argument("--sales-months", type=int, default=6, help="销量回填补捉的逐月数（默认6）")
    ap.add_argument("--no-ciasi-rescue", action="store_true",
                    help="不从 C-IASI 新闻稿联网救回数字图标车（仅 A/B 对照；正常出榜不要关）")
    ap.add_argument("--force-ciasi-rescue", action="store_true",
                    help="强制重扫已缓存车型的新闻稿（人工刷新时用）")
    ap.add_argument("--force-stale", action="store_true",
                    help="强制使用旧指纹候选池出榜（仅调试/回测用，报告会标注数据过期）")
    ap.add_argument("--no-anchor-pool", action="store_true",
                    help="不读全价位榜固定锚点快照，四个榜内模块改用本池现算（A/B 对照；"
                         "正常出子榜必须开锚点池，否则同车跨榜分数不一致）")
    a = ap.parse_args()

    cfg = L.load_category(a.category)
    errs = L.validate(cfg)
    if errs:
        sys.exit("配置未通过校验：" + "；".join(errs))
    os.makedirs(a.out, exist_ok=True)

    # v9.7.36：指纹门禁——skill/打分代码升级后，老版本生成的候选池分数不许混进新榜。
    # 事实数据不作废，只拒绝"用老分数出新榜"；重跑打分逻辑后才能出。
    from modules.engine import score_fingerprint as SF
    _here = str(paths.ROOT)
    cfg_path = str(paths.cat(f"{a.category}.json"))
    _fp = SF.check(a.candidates, cfg_path if os.path.exists(cfg_path) else None)
    if not _fp["ok"] and not a.force_stale:
        sys.exit("指纹门禁：" + _fp["reason"] +
                 "\n→ 请重算候选池分数后用 score_fingerprint.py --stamp 打标；"
                 "确需强制出榜加 --force-stale（报告会标注数据过期）。")

    data = json.load(open(a.candidates, encoding="utf-8"))
    cands = data.get("candidates", data)
    # 候选池形状归一化（price "24.98万" → 24.98；trim 字符串 → {"name":...}）。
    # 这是契约层的事，不归一化会在 trim_annotate / auto_trims 里 TypeError。
    from modules.contracts.candidate_schema import normalize_pool
    cands = normalize_pool(cands)

    # v10.17.0：性价比产品力基准快照——value 残差法的 pf 取候选池入池时的原始七维分，
    # 不用 pipeline 中途被规则增量/安全回流/证据封顶改动的分数。理由：那些中间步骤含
    # 联网核验（碰撞/销量）与榜内锚点，两榜执行时点不同会让 pf 漂移→档均漂移→同车
    # value 不一致（宋Ultra 曾 9.5 vs 8.6）。入池原始分是全榜/子榜共用的稳定基准。
    for _c in cands:
        _c["_value_pf_base"] = dict(_c.get("scores") or {})

    # 固定锚点快照（v10.16.0）：子榜用全价位榜算好的榜内锚点，避免同车跨榜分数漂移。
    # 全价位榜自身没配 anchor_pool（或 --no-anchor-pool）→ 四个模块仍用本池现算。
    from modules.engine import anchor_pool as AP
    _anchor = None if getattr(a, "no_anchor_pool", False) else AP.load(a.category)
    # 全价位榜自身（本池款数≥快照源池）不应用锚点——它本就是锚点来源，用快照会循环。
    _src_n = (_anchor or {}).get("source_n")
    if _anchor and isinstance(_src_n, int) and len(cands) >= _src_n:
        _anchor = None
    if _anchor:
        print(f"[固定锚点] 读全价位快照：证据封顶 {len(_anchor.get('evidence_cap') or {})} 维 / "
              f"空间封顶 {len(_anchor.get('space_caps') or {})} 款 / "
              f"达成率中位 {_anchor.get('range_median')} / "
              f"回归斜率 {(_anchor.get('efficiency') or {}).get('slope')}")

    # C-IASI 数字图标车联网救回（v10.5.0）：候选池里出现「数字图标/等级未文字化」且
    # 随包事实库还没有该车文字等级时，调用 research 唯一网络出口解析官网结果发布稿，
    # 合并回 categories/ciasi_decoded.json。已缓存车型不联网；失败只告警不阻断。
    _ciasi_rescue = {"status": "skipped", "targets": [], "matched": {}}
    if not getattr(a, "no_ciasi_rescue", False):
        from modules.research import ciasi_text_decode as CTD
        _rescue_targets = CTD.targets_from_candidates(cands)
        if _rescue_targets:
            _decoded_file = str(paths.cat("ciasi_decoded.json"))
            _start = 100
            if getattr(a, "force_ciasi_rescue", False):
                _start = 100
            elif os.path.exists(_decoded_file):
                try:
                    _cached = json.load(open(_decoded_file, encoding="utf-8"))
                    _start = int((_cached.get("scan_range") or [100, 100])[1]) + 1
                except Exception:
                    _start = 100
            try:
                _ciasi_rescue = CTD.rescue_targets(
                    _rescue_targets, out_path=_decoded_file, start=_start,
                    end=None, sleep=0.05, max_misses=12,
                    force=getattr(a, "force_ciasi_rescue", False))
                if _ciasi_rescue.get("matched"):
                    print(f"[新闻稿救回] {len(_ciasi_rescue['matched'])} 款数字图标车已从 C-IASI 官网"
                          "结果发布稿文字等级救回：" +
                          "、".join(_ciasi_rescue["matched"]))
            except Exception as _ex:                       # 网络抖动不阻断本地出榜
                _ciasi_rescue = {"status": "error", "targets": _rescue_targets,
                                 "matched": {}, "error": f"{type(_ex).__name__}: {_ex}"}
                print(f"[新闻稿救回] 联网失败，沿用已缓存文字等级：{_ciasi_rescue['error']}")

    # 销量权威回填（2026-11-22 阿维塔07 事故根因修复）：候选池 trend_sales 是**手填常量**，
    # 出榜链从不校验。实测阿维塔07 填 7000（懂车帝车系合计 356、纯电≤356）、
    # Model Y 填 48000（实际纯电榜中位 29086），差 8 倍，却一路过闸误导第一档
    # t1_min_sales=2000 门槛。现在出榜前用懂车帝逐月权威值强制覆盖：
    #   纯电榜命中 → 逐月中位（scope=pure_bev）；只有全部榜命中 → 车系合计上界（scope=series_total，
    #   下游页面显示「≤N」不参与分位）。手填值与权威值差≥2倍记 dispute 披露。
    _sales_report = {"status": "skipped"}
    if not getattr(a, "no_sales_backfill", False):
        from modules.board import sales_backfill as SB
        try:
            _snap = SB._load_snapshot(a.sales_snapshot) if a.sales_snapshot else None
            _sales_report = SB.backfill(
                cands, as_of=str(getattr(a, "as_of", None) or "202610"),
                snapshot=_snap, months=getattr(a, "sales_months", 6))
            _disp = [x for x in _sales_report["applied"] if x["dispute"]]
            print(f"[销量回填] 回填 {len(_sales_report['applied'])}/{_sales_report['n']} 款 / "
                  f"无数据 {len(_sales_report['no_data'])} / 手填分歧 {_sales_report['dispute_count']}")
            if _disp:
                print("  分歧（手填→权威，以权威为准）：" + "、".join(
                    f"{x['model']} {x['old']}→{x['new']}({x['scope']})" for x in _disp[:8]))
        except Exception as _ex:                          # 联网失败不阻断，但显式告警
            _sales_report = {"status": "error", "error": f"{type(_ex).__name__}: {_ex}"}
            print(f"[销量回填] 联网失败，沿用候选池手填值（可能过期）：{_sales_report['error']}")

    # 价格回流（2026-11-21 事故根因修复）：trims.json 里已核验的官方指导价必须回流到
    # 候选池 price_guide。闸门只认 price_guide，不回流就会把数据齐全的车判成
    # price_unknown 落榜（当时 11 款全中招，榜单只剩 13-16 万的车）。
    # 原来靠手动跑 --sync-prices，那是「规则存在人的记忆里」；现在出榜前自动执行。
    from modules.engine import facts_sync as FS
    from modules.contracts.model_names import canon as _canon
    if getattr(a, "no_backfill", False):
        _p_filled, _p_still = [], [c.get("model") for c in cands]
    else:
        _p_filled, _p_still = FS.backfill_price_guide_from_trims(cands)
    if _p_filled:
        print(f"[价格回流] trims 官方指导价 → 候选池 price_guide：{len(_p_filled)} 款（"
              + "、".join(m for m, _ in _p_filled[:8]) + ("…" if len(_p_filled) > 8 else "") + "）")
    # 碰撞成绩回流（2026-11-22 改为单一同步器）：
    #   原来这里是**三段**独立回流——文本抽取 / official_safety_scores /
    #   cncap_scores，各自一套裁决逻辑、互相覆盖，冲突只能靠「谁后跑谁赢」。
    #   现在统一走 categories/safety_scores.json（安全成绩域唯一事实源），
    #   裁决规则只有一个（tier 高者为主），不同体系不合并、不平均。
    #   note 里的自由文本成绩**降级为非权威描述**（用户 2026-11-22 拍板），
    #   不再单独回填 crash 列。
    from modules.engine import sync_safety_scores as SSS
    _s_filled, _s_unres = SSS.sync(cands)
    if _s_filled:
        print(f"[安全成绩回流] safety_scores SSOT → crash/safety_basis：{len(_s_filled)} 款（"
              + "、".join(f"{m}({d})" for m, d, _ in _s_filled[:6])
              + ("…" if len(_s_filled) > 6 else "") + "）")
    _c_filled, _c_unres = _s_filled, _s_unres

    # 逐车碰撞成绩联网核验 + 出榜门禁（v10.7.0）：对每一款入池车产出明确核验状态。
    # 必须在三条回流之后——ES6 这类成绩只在 evidence 文本里，回流前 crash 字段为空
    # 会被误标 no_score（实跑踩过）。research 唯一网络出口逐车判定：官网有本地无 /
    # 数值冲突 / 主通道失败一律硬阻断出榜；历史批次回访一手来源页确认后再采信。
    _crash_verify = {"status": "skipped"}
    if not getattr(a, "no_crash_verify", False):
        from modules.research import crash_auto_verify as CAV
        try:
            # board 层用 engine 解析器把「页面实际显示」的 crash 字段结构化后传给
            # research（research 不反向依赖 engine，包依赖只向下）。
            from modules.engine import crash_systems as CRS
            _pool_parsed: dict[str, dict] = {}
            for _c in cands:
                _p = (CRS.parse_score(_c.get("crash"))
                      or CRS.parse_score(_c.get("safety_basis")))
                if _p:
                    _pool_parsed[_c.get("model")] = _p
            _crash_verify = CAV.verify_all(
                cands, pool_parsed=_pool_parsed,
                ciasi_fresh=list((_ciasi_rescue.get("matched") or {}).keys()),
                # v10.8.0：当天核验状态带时间戳缓存复用；--no-crash-cache 强制重抓。
                # 缓存落 runs/（不进 GitHub/发布包），跨天或权威库变更自动失效。
                use_cache=not getattr(a, "no_crash_cache", False))
            _gate = CAV.coverage_gate(
                _crash_verify, allow_degraded=getattr(a, "allow_degraded_network", False))
            _crash_verify["gate"] = _gate
            _cnt: dict[str, int] = {}
            for _r in _crash_verify["per_car"].values():
                _cnt[_r["status"]] = _cnt.get(_r["status"], 0) + 1
            _cc = _crash_verify.get("cache") or {}
            print("[碰撞核验] 逐车状态：" + "、".join(
                f"{k} {v}" for k, v in sorted(_cnt.items())))
            print(f"[碰撞核验] 缓存：核验日 {_cc.get('date')} / "
                  f"当天复用 {_cc.get('hits', 0)} / 重新联网 {_cc.get('misses', 0)}"
                  + ("（--no-crash-cache 强制全量重抓）"
                     if getattr(a, "no_crash_cache", False) else ""))
            if not _gate["ok"]:
                sys.exit("碰撞联网核验门禁：" + _gate["reason"] +
                         "\n→ 数据问题先补权威库/人工核验后重跑；断网重跑或加"
                         " --allow-degraded-network 显式降级（报告会标注）。")
        except SystemExit:
            raise                                          # 门禁阻断不能被下面吞掉
        except Exception as _ex:                           # 代码异常也按联网失败处理
            _crash_verify = {"status": "error",
                             "error": f"{type(_ex).__name__}: {_ex}"}
            if not getattr(a, "allow_degraded_network", False):
                sys.exit("碰撞联网核验异常（无法确认是否漏报）：" + _crash_verify["error"] +
                         "\n→ 修代码/网络后重跑；确需降级加 --allow-degraded-network。")
            print(f"[碰撞核验] 异常已显式降级：{_crash_verify['error']}")

    # 车辆事实一致性门禁（v10.10.0）：同一事实在权威源与各派生副本的值必须一致。
    # 旧架构里电芯散落 5 处各自手写，改一处漏四处（极氪7X 补 3 处仍漏），
    # 现在唯一可写源在契约层，派生副本只被读取；漂移=有人绕开权威源手改了副本，
    # 出榜前显式告警并写审计，避免静默展示错误事实。不阻断（副本不影响页面取数）。
    try:
        from modules.research import vehicle_facts as VF
        _vf_consistency = VF.consistency_report()
        if not _vf_consistency["ok"]:
            print("[事实一致性] 警告：派生副本与权威源漂移（页面以权威源为准）：" +
                  "、".join(f"{x['copy']}/{x['model']}={x['copy_value']}"
                           f"(权威 {x['authoritative']})"
                           for x in _vf_consistency["drifts"][:8]))
    except Exception as _ex:                               # 自检失败不影响出榜
        _vf_consistency = {"ok": False, "error": f"{type(_ex).__name__}: {_ex}"}

    # 底盘事实覆盖率（v10.51.0）：操控维规则的结构化数据源（悬架形式/驱动形式）
    # 此前从未有任何结构化采集通道（汽车之家底盘组对所有能源类型整组为空、
    # 易车/懂车帝参数表反爬、官网 SPA 无 SSR），只靠人工取证文本正则提取。
    # 现在 chassis_fetch 多源链落地（文章正文提取带引文 + 人工核验），这里出榜前
    # 披露覆盖率——告警不阻断（同 v10.10.0 事实一致性门禁纪律：缺口车缺数据
    # ≠ 差，不插补不改分，但缺口必须显式可见，不许静默伪装成「数据源没有」）。
    try:
        from modules.research import chassis_fetch as CF
        _chassis_cov = CF.coverage([c.get("model") for c in cands])
        _chassis_sum = {"n": _chassis_cov["n"], "full": _chassis_cov["full"],
                        "partial": _chassis_cov["partial"], "none": _chassis_cov["none"],
                        "pct": _chassis_cov["pct"],
                        "missing": _chassis_cov.get("missing") or [],
                        "partial_models": _chassis_cov.get("partial_models") or []}
        if _chassis_cov["none"]:
            print(f"[底盘覆盖] ⚠ {_chassis_cov['none']}/{_chassis_cov['n']} 款底盘三字段"
                  f"（前悬/后悬/驱动）全缺：{'、'.join(_chassis_cov['missing'][:8])}"
                  + ("…" if len(_chassis_cov['missing']) > 8 else ""))
    except Exception as _ex:                               # 自检失败不影响出榜
        _chassis_sum = {"error": f"{type(_ex).__name__}: {_ex}"}

    # 多测评融合（v10.4.0，2026-11-21）：用户要求「有评测就要纳入，按业内原则重新设计」。
    # 同体系多维度按木桶取最低（Euro NCAP/C-NCAP 官方口径）；跨体系按严格度选主分、
    # 其余列示不叠加（各国 NCAP 无对应关系）。一手来源含 C-IASI 官网新闻稿纯文字等级
    # （research 模块 data/gen/ciasi_decoded.json），救回数字图标不可解码的车。
    # 必须在 crash 回流之后、apply_auto_scores 之前——融合改写 crash 字段后才走既有映射。
    from modules.engine import multi_evidence_score as MES
    if getattr(a, "no_fusion", False):
        _fusion = []
    else:
        _fusion = MES.fuse(cands)
    _fused_n = sum(1 for x in _fusion if x.get("primary"))
    if _fused_n:
        print(f"[多测评融合] {_fused_n} 款有一手评测纳入（同体系木桶取最低/跨体系取最严格主分）；"
              + "、".join(f"{x['model']}={x['primary']}" for x in _fusion if x.get("primary"))[:180])

    # 碰撞展示门禁（v10.5.0）：有权威成绩来源却仍显示「—/在测」= 回流链路失效
    # （车名归一化漂移/字段改名/新数据源没接），必须硬拦，不许只在报告里写「有成绩」。
    _display_leaks = FS.crash_display_leaks(cands)
    if _display_leaks:
        sys.exit("碰撞展示门禁：以下车型有可核验权威碰撞成绩，但页面仍无成绩显示：\n"
                 + "\n".join(f"  - {x['model']}：{x['sources']}" for x in _display_leaks)
                 + "\n→ 查 facts_sync 的成绩回流与 model_names.canon，不许改渲染层绕过。")
    print(f"[碰撞展示门禁] 全池 {len(cands)} 款，有成绩来源但未显示 0 款")

    # 成绩→分数自动映射（2026-11-21）：表外但有碰撞成绩的车，按 official_safety_scores.json
    # 的 map_spec 算出 safety 分。不映射等于成绩白采（safety 仍按无成绩车插补）。
    from modules.engine import safety_check as SFC
    if getattr(a, "no_auto_safety", False):
        _sa, _ss = [], []
    else:
        _sa, _ss = SFC.apply_auto_scores(cands)
    if _sa:
        print(f"[安全映射] 按官方映射规则给 {len(_sa)} 款算出 safety 分（"
              + "、".join(f"{x['model']} {x['score']:g}" for x in _sa[:6])
              + ("…" if len(_sa) > 6 else "") + "）")


    # 表分同步（2026-11-21）：映射表里的车，候选池必须对齐表分，否则「表里 8.7、榜上 8.9」两套数
    _st_sync = SFC.sync_table_scores_to_pool(cands)
    if _st_sync:
        print(f"[表分同步] {len(_st_sync)} 款 safety 对齐映射表（"
              + "、".join(f"{x['model']} {x['prev']}→{x['score']:g}" for x in _st_sync[:5])
              + ("…" if len(_st_sync) > 5 else "") + "）")

    # 维度证据门槛（2026-11-21 统一）：safety 要碰撞成绩、adas/cabin/control 要实测、
    # quality 要 PPH 样本；space/value/powertrain 纸面可判定，不封顶。
    # 必须放在 crash 回流与安全映射之后，否则有成绩的车会被误判成「无证据」。
    # 规则增量（v10.11.0）：把配置驱动规则接进人工基准分。
    # 顺序理由：先硬件规则（人工基准 + 客观配置差），再体验证据（实测折损），
    # 再电耗效率（能效），最后证据封顶（门槛）。
    # 必须排在 with_config_scores 之前——那个函数用 best_trim 的 scores 覆盖
    # c["scores"]，排在它后面会被整体盖掉。
    from modules.engine import rule_adjust as _RA
    _rule_rep = _RA.apply_rule_adjust(cands, fixed_medians=(_anchor or {}).get("rule_medians"))
    # 逐车规则增量明细（供页面三电子维度分组展示）
    _rule_detail = {c["model"]: c["rule_adjust"]
                     for c in cands if c.get("rule_adjust")}
    if _rule_rep:
        print("[规则增量] " + " / ".join(
            f"{d} {v.get('status')}({len(v.get('touched') or [])}款)"
            for d, v in _rule_rep.items()))

    # 体验证据调整（2026-11-21）：原来只有 board_v2（HTML 链路）会 apply_experience，
    # pipeline（report/summary 链路）不调 → 同一份数据，report 说极氪7X 第一、HTML 说 MONA 第一。
    # 现在统一在 pipeline 里调一次，并把 no_experience=True 传给 board_v2，避免重复累加。
    from modules.engine import experience_score as _ES
    if not getattr(a, "no_experience", False):
        _ES.apply_experience(cands, enabled=True)

    # 电耗效率修正（v10.6.0 引入，v10.15.3 改车重归一残差法）：
    # 三电维度此前只看车端硬件（容量/电压/充电/电芯），忽略系统能效。
    # v10.15.3：电耗~车重相关+0.839，绝对电耗中位法系统性冤枉重车
    # （蔚来16.1中15.3是车重必然、极氪7X真高效却被扣）→ 改为榜内对
    # (车重→电耗) 回归后按残差（实际−车重预测）双向调整，±0.4封顶，
    # 缺电耗或车重不插补。同口径见 modules/engine/powertrain_efficiency.py。
    # 必须放在 experience 之后、evidence_gate 之前：证据封顶读的是最终分。
    from modules.engine import powertrain_efficiency as _PE
    _eff_applied = 0
    if not getattr(a, "no_powertrain_efficiency", False):
        _PE.apply_efficiency(cands, fixed=(_anchor or {}).get("efficiency"))
        _eff_applied = sum(1 for c in cands
                           if (c.get("powertrain_efficiency") or {}).get("status") == "applied")
        print(f"[电耗效率修正] {_eff_applied} 款有电耗+车重一手数据"
              f"（车重归一残差法，±0.4封顶）")

    # 续航达成率调整（v10.15.2 · 审计 P0-1）：三电此前只看 CLTC 标称，达成率零散在体验库。
    # 现在用 physics.ev_range_model 从 kwh/电耗/车重/Cd 估算高速达成率（一手实测优先），
    # 榜内中位锚双向 ±1.0，无数据不插补。必须在 efficiency 之后、evidence_gate 之前。
    from modules.board import range_achievement as _RCH
    _ach_report = {"status": "skipped"}
    if not getattr(a, "no_range_achievement", False):
        _ach_report = _RCH.apply(cands, fixed_median=(_anchor or {}).get("range_median"))
        print(f"[续航达成率] 覆盖 {_ach_report['covered']}/{_ach_report['n'] if 'n' in _ach_report else len(cands)} 款 / "
              f"调整 {len(_ach_report['applied'])} 款 / {_ach_report['method']}")

    # 主榜三电软上限（v10.16.0）：效率+达成率叠加后由 min(10,..) 硬顶出的 10.0 满分
    # 与上轮「三电不可能满分」要求冲突。统一过 soft_ceiling（knee 9.3 → 渐近 9.5），
    # 单调保序、消除并列满分。审计字段留痕供报告披露。
    from modules.engine import spec_score as _SS
    _pt_capped = []
    for _c in cands:
        _s = (_c.get("scores") or {}).get("powertrain")
        if isinstance(_s, (int, float)) and _s > _SS.MAIN_PT_KNEE:
            # v10.73 实测闸门：只有独立实测达成率（kind=independent）才允许越过 9.3
            # 走到软上限 9.5；车主口碑/物理估算/无数据一律上界 9.0。
            _measured = ((_c.get("range_achievement") or {}).get("kind") == "independent")
            _new = _SS.main_board_powertrain_cap(_s, measured=_measured)
            if _new < _s:
                _c["scores"]["powertrain"] = _new
                _c.setdefault("score_cap_reasons", []).append(
                    (f"powertrain 主榜软上限 {_s:g}→{_new:g}（渐近 {_SS.MAIN_PT_CAP:g}，不硬顶满分）"
                     if _measured else
                     f"powertrain {_s:g}→{_new:g}（无一手实测达成率，上界 {_SS.MAIN_PT_UNVERIFIED_CAP:g}，"
                     f"不声称第一梯队）"))
                _pt_capped.append({"model": _c["model"], "prev": _s, "to": _new,
                                   "measured": _measured})
    if _pt_capped:
        print(f"[主榜三电软上限] {len(_pt_capped)} 款满分/近满分收敛：" +
              "、".join(f"{x['model']} {x['prev']:g}→{x['to']:g}" for x in _pt_capped[:5]))

    # v10.73 结构防线出榜：补能「单一账本」批量体检（800V 与快充不得双计）。
    # 逐配置跑 spec_score.powertrain_parts；若谁把平台分/快充分改成同时为正，
    # 这里直接抛 RuntimeError（宁可出榜失败，也不发布一个虚高的三电分）。
    # 同时披露两项计数：platform_muted=有快充→平台被置 0 的配置数；
    # platform_proxy=无快充数据→平台作补能代理计分的配置数。
    from modules.engine import trim_board as _TB
    from modules.data import trims_store as _TS
    _pt_db = _TS.load_db()
    _pt_specs = []
    for _e in (_pt_db.get("entries") or []):
        for _t in (_e.get("trims") or []):
            _sp = _TB.spec_from_trim(_t)
            if isinstance(_sp, dict) and _sp.get("charge_10_80_min") is not None:
                _pt_specs.append(_sp)
    _sc_aud = _SS.powertrain_single_credit_audit(_pt_specs)
    print(f"[补能单一账本] 体检 {_sc_aud['checked']} 条配置：平台并入补能置 0 "
          f"{_sc_aud['platform_muted']} 条 / 无快充作代理 {_sc_aud['platform_proxy']} 条 / "
          f"双计违规 {len(_sc_aud['violations'])} 条")


    # v10.72 根因修复：space 从"纯手填"改为"硬数据规则算"（用户"特斯拉空间为什么差"）。
    # 旧版 evidence_gate 把 space 标"纸面可判定、不封顶"，却没有任何算分规则——
    # 手填的 Model Y(轴距2890/尾箱822L) 8.0 低于 海狮06EV(2820/757L) 9.0，
    # 物理数据不支持。现在有轴距+后备箱的按公式覆盖，缺数据的标 unverified
    # 保留原值（不猜），并在报告披露。必须在 evidence_gate 之前（space 不封顶）。
    from modules.engine import space_score as _SSP
    if not getattr(a, "no_space_score", False):
        _space_rows = _SSP.apply(cands)
        if _space_rows:
            print(f"[空间硬数据] 按轴距+后备箱算 {len(_space_rows)} 款 space 分（"
                  + "、".join(f"{x['model']}→{x['score']:g}" for x in _space_rows[:4])
                  + "）")

    from modules.engine import evidence_gate as EG
    if getattr(a, "no_evidence_cap", False):
        _ev_adj = []
    else:
        # v10.72：quality 封顶必须在 QS.apply（算 quality_detail）之后——
        # 此前 EG 在 L524 先跑，读不到 quality_detail，所有车被当"无PPH证据"
        # force 到同一 cap（4.19），有负面数据的车也被误封顶。
        _ev_adj = EG.apply_all(cands, anchor=(_anchor or {}).get("evidence_cap"),
                               only=[d for d in EG.DIM_POLICY if d != "quality"])
    _ev_sum = EG.summarize(_ev_adj)
    _cap_adj = [x for x in _ev_adj if x["dim"] == "safety"]
    _dim_cap = [x for x in _ev_adj if x["dim"] != "safety"]
    if _ev_sum:
        print("[证据封顶] " + " / ".join(f"{d} {v['count']}款→{v['cap']:g}"
                                         for d, v in _ev_sum.items()))

    # space 硬数据一致性封顶（2026-11-21，用户第三次质疑 MONA 空间 9.3 触发）：
    # evidence_gate 把 space 判为"纸面可判定、不封顶"，但 vehicle_physics 全库
    # 轴距 0% 覆盖，space 其实是随包人工库拍的分、从没用硬数据校验过。
    # space_consistency 用"尺寸不小于本车的车的中位"设上限，只压虚高不倒扣。
    from modules.engine import space_consistency as SP
    if getattr(a, "no_space_cap", False):
        _space_adj, _space_uncal = [], []
    else:
        _space_adj, _space_uncal = SP.calibrate_space(
            cands, fixed_caps=(_anchor or {}).get("space_caps"))
    _space_sum = SP.summarize(_space_adj, _space_uncal)

    # 空间要素结构化（2026-11-21 用户问「空间要考虑前备箱/下沉后备箱/储物/真实表现吗」）：
    # 从 evidence.space_evidence 提取两类——real(得房率/膝部/腿部/间距，参与上面的校验)
    # 与 storage(前备箱/后备箱/下沉储物，仅披露不进分)。只提取不打分，不碰渲染层。
    from modules.engine import space_features as SPF
    _sf_per_car = {c["model"]: SPF.extract_space_features(c) for c in cands}
    _sf_per_car = {m: f for m, f in _sf_per_car.items() if f}
    _sf_sum = SPF.summarize(_sf_per_car)

    if _space_adj:
        print("[空间一致性封顶] " + " / ".join(
            f"{x['model']} {x['prev']:g}→{x['capped']:g}（{x['basis']}）" for x in _space_adj))

    # 证据缺口门禁（2026-11-21，用户追问「是否在代码层面确保不出错」后从 print 升级为硬拦）：
    # 文本/外部稿里有可核查实测（麋鹿 XX km/h / 城市NOA 实测）但体验证据库没记录 → 拦截出榜。
    # T6 的麋鹿 80.02km/h 就这样漏过一次，被判「零实测」封顶到 7.8，而实测 75.2 的车是 8.5。
    # 只拦不自动入库——麋鹿可能是宣传口径，须人工核实来源后补录（用 research 的
    # fill_experience.extract_driving_evidence 提取候选证据）。确认为无需入库时用
    # --skip-experience-gap 并在报告说明。
    from modules.engine import cross_verify as XV
    _exp_gaps = XV.check_experience_gaps(cands) if not getattr(a, "no_evidence_cap", False) else []
    if _exp_gaps:
        _gap_lines = [f"  - {g['model']}({g['dim']}/{g['kind']}) 来源:{g.get('source','候选池文本')}"
                      f" — {g['fix']}" for g in _exp_gaps]
        if not getattr(a, "skip_experience_gap", False):
            sys.exit("体验证据缺口门禁：以下车型有实测文本但证据库无条目（会被误判零实测封顶）：\n"
                     + "\n".join(_gap_lines[:8])
                     + "\n→ 用 research.fill_experience.extract_driving_evidence 提取候选证据，"
                       "人工核实来源后补录 experience_facts.json；确认为无需入库时用 "
                       "--skip-experience-gap 并在报告说明。")
        print(f"[证据缺口·已跳过] {len(_exp_gaps)} 条文本实测未入库（--skip-experience-gap）：" +
              "、".join(f"{g['model']}({g['dim']})" for g in _exp_gaps[:5]))

    # adjust 黑箱门禁：总分加减必须留依据（此前 11 款 adjust 无 reason，不可审计）
    _adj_bad = [c.get("model") for c in cands
                if (c.get("adjust") or 0) and not c.get("adjust_reason")]
    if _adj_bad:
        sys.exit("adjust 门禁：以下车型有总分加减但没写 adjust_reason（不可审计）："
                 + "、".join(_adj_bad[:8]) + "\n→ 要么补依据，要么置 0 让短板由维度分承担。")

    # 门禁：回填后仍「trims 有价而候选池无价」= 回流逻辑失效（canon 漂移/字段改名），必须硬拦。
    # 真缺口（trims 也没价）只披露不拦截，交给人补采集。
    if _p_still and not getattr(a, "no_backfill", False):
        _pm = FS.trims_price_map()
        _leak = [m for m in _p_still if _canon(m) in _pm]
        if _leak:
            sys.exit("价格回流门禁：以下车型 trims.json 有官方指导价，但没能回填到候选池"
                     "（多半是车名归一化/字段漂移）：" + "、".join(_leak) +
                     "\n→ 查 modules/contracts/model_names.py 的 canon 与 trims 条目名；"
                     "修好后重跑，不要用 --no-backfill 绕过。")

    # v9.7.43：候选池完整性门禁——出榜前必须联网盘点同级全量在售车型，与候选池比对。
    # 漏收的车必须逐个判定：能入池的补入候选池+trims.json，不能入池的写进 pool_excluded[] 并写明事实原因。
    # v10.69 根因修复：级别口径单一真相源——官方二级分类自动判定准入。
    # 用户："我说中大型就是中大型，你要自己判断"——级别按 levels.json 的
    # 官方二级分类自动判，不问用户、不靠 universe 手工挑、不按轴距推定。
    # 无官方级别缓存的车一律不得出榜（防"默认在售默认符合"的静默放行）。
    from modules.engine import level_gate as _LG
    _allow_cls = ((cfg.get("gates") or {}).get("class") or {}).get("allow") or []
    if _allow_cls:
        _lvl = _LG.audit(cands, _allow_cls)
        if _lvl["rejected"] or _lvl["unverified"]:
            bad = _lvl["rejected"] + _lvl["unverified"]
            sys.exit("级别口径门禁：以下候选不符合本榜官方级别口径：\n"
                     + "\n".join(f"  {x['model']}：{x['reason']}" for x in bad[:12])
                     + f"\n→ 本榜官方二级分类白名单={_allow_cls}；"
                     "不符的移出候选池，无级别缓存的先跑 auto_levels.py 联网核验写 levels.json。")
        print(f"[级别口径门禁] 官方二级分类白名单 {_allow_cls} / {len(cands)} 款全部符合")

    # v10.69 根因修复：生命周期症状门禁——销量骤降车必须逐车核验。
    # 2026-11-24 用户："更新 lifecycle_status.json 写成代码，单独更新这个
    # 以后其他车还是会有问题"。根因：手工写 JSON 是补丁；lifecycle_detect.py
    # 早就能从销量序列判 collapse?/cliff?/clearance?，但没接出榜链路。
    # 现在 lifecycle_reviewed 由 lifecycle_gate_build.py 从销量自动生成症状，
    # 出榜时检查"疑似但未核验"并硬拦——任何车退市都会自己报警，不靠人搜新闻。
    _lrev = data.get("lifecycle_reviewed") or {}
    if _lrev and not getattr(a, "skip_lifecycle_gate", False):
        _unverified = [r for r in (_lrev.get("reviewed") or []) if not r.get("status")]
        if _unverified:
            sys.exit("生命周期症状门禁：以下车销量骤降但未核验，不得出榜：\n"
                     + "\n".join(f"  {r.get('model')}（{r.get('detected')}）："
                                 f"{r.get('values')} {r.get('reasons', [''])[0]}"
                                 for r in _unverified[:12])
                     + "\n→ 跑 modules/research/lifecycle_gate_build.py --apply 重生成症状，"
                     "逐车核验写 status（normal / confirmed_discontinued / relaunched，带url+note）。"
                     "\n→ 确需带未核验车出榜用 --skip-lifecycle-gate（须在报告说明）。")
        print(f"[生命周期症状门禁] 销量异常 {len(_lrev.get('reviewed') or [])} 款已逐车核验，未核验 0")

    # v10.68 根因修复：候选发现门禁——池外高销量车必须逐车判定。
    # 2026-11-24 事故：极狐阿尔法T7（中大型纯电SUV、月销8131）漏收，
    # discover.py 早已抓到它（其文档开头就写着这个案例），但从未接进出榜链路。
    # 现在检查候选池 discovery_reviewed：每条池外高销量车必须有明确判定
    # （已入池 / 级别不符写明具体级别），出现"未判定/待核实"即硬拦。
    _drv = data.get("discovery_reviewed") or {}
    if _drv and not getattr(a, "skip_discovery_gate", False):
        _undecided = [r for r in (_drv.get("reviewed") or [])
                      if not r.get("verdict") or r["verdict"] in ("未判定", "待核实")
                      or str(r["verdict"]).startswith("待核实")]
        if _undecided:
            sys.exit("候选发现门禁：以下池外高销量车未判定级别，不得出榜：\n"
                     + "\n".join(f"  {r.get('name')}（月销{r.get('sales')}）" for r in _undecided)
                     + "\n→ 逐个查官方二级分类：符合本榜口径的补入候选池；不符合的写明具体级别。"
                     "\n→ 确需带未判定项出榜用 --skip-discovery-gate（须在报告说明）。")
        print(f"[候选发现门禁] 池外高销量 {len(_drv.get('reviewed') or [])} 款已逐车判定，未判定 0")

    # v10.68 根因修复：生命周期确证门禁——停产/停售车不得进主榜。
    # 2026-11-24 事故：唐L EV 2026-06 已停产，却仍排中大型榜第1。
    # lifecycle_confirm.py 模块与 lifecycle_status.json 权威库都在，但 pipeline
    # 从未调用它做硬拦（只有"状态值中文化"门禁，不判断停产）——工具存在没接线。
    # 现在出榜前逐候选查确证库，discontinued/stopped 的車一律硬拦。
    from modules.engine import lifecycle_confirm as _LC
    _lc_db = _LC.load_db()
    _dead = []
    for _c in cands:
        _st = _LC.resolve(_c, _lc_db)
        if str(_st.get("status") or "").lower() in ("discontinued", "stopped"):
            _dead.append((_c.get("model"), _st.get("date") or "?",
                          _st.get("successor") or "", str(_st.get("note") or "")[:60]))
    if _dead and not getattr(a, "allow_discontinued", False):
        sys.exit("生命周期门禁：以下候选已停产停售，不得进主榜（lifecycle_status.json 确证）：\n"
                 + "\n".join(f"  {m}（{d}停产；继任{s}）：{n}" for m, d, s, n in _dead)
                 + "\n→ 从候选池移出+写进 pool_excluded，或确认已复产并更新 lifecycle_status.json。"
                 "\n→ 确需带着停产车出榜用 --allow-discontinued（须在报告说明）。")
    if _dead:
        print(f"[生命周期门禁] ⚠ 允许 {len(_dead)} 款停产车参与（--allow-discontinued）")
    else:
        print(f"[生命周期门禁] {len(cands)} 款候选均非停产停售")

    # 这是对"Model 3 漏收"事故的根因修复（此前只靠人记得盘点，没接进出榜链路）。
    from modules.engine import trim_coverage as TC
    # v10.62 根因修复：优先用品类配置显式指定的唯一权威清单（cfg["universe"]），
    # 其次命令行 --universe，最后才回退命名约定。防止同品类多副本清单各自为政。
    _cfg_univ = (cfg.get("universe") or "").strip()
    if _cfg_univ:
        _cfg_univ = _cfg_univ if os.path.isabs(_cfg_univ) else str(paths.ROOT / _cfg_univ)
    _univ = _cfg_univ or a.universe or str(paths.cat(f"universe_{a.category}.json"))
    if os.path.exists(_univ) and not a.skip_universe_gate:
        _ur = TC.check_universe(a.candidates, _univ, data.get("pool_excluded", []))
        # v10.62 根因修复：清单格式读不到车名必须硬拦。
        # 旧版只查 not_covered，names 字段清单被读成 0 款→静默跳过→漏收腾势N8 没被拦。
        if _ur.get("error"):
            sys.exit("完整性门禁：权威清单读取失败（拒绝静默跳过）：" + _ur["error"] +
                     f"\n→ 清单路径 {_univ}；应含 names/models 车名数组。")
        if _ur.get("available") and _ur.get("not_covered"):
            sys.exit("完整性门禁：以下同级在售车漏收且未说明原因：" +
                     "、".join(_ur["not_covered"]) +
                     "\n→ 能入池的补入候选池+categories/trims.json；"
                     "不能入池的写进 pool_excluded[] 并写明事实原因。"
                     "\n→ 确认已处理仍要出榜用 --skip-universe-gate（须在报告说明理由）。")
        print(f"[完整性门禁] 同级权威 {_ur.get('universe', 0)} 款 / 入池 {_ur.get('in_pool', 0)} 款 / 漏收未说明 0")

    # v9.7.43：条目内一致性门禁——note 文案不得与 crash/safety_basis 等结构字段矛盾。
    # 深蓝L06 note 写"无碰撞成绩"却有 C-NCAP 92.3%/证据A，脏数据滞留数轮没人发现——
    # 因为此前只有"铁律"没有可执行校验。铁律=自然语言提示，会忘；门禁=代码，不会漏。
    from modules.engine import cross_verify as XV
    _ic = XV.check_internal_consistency(cands)
    if not _ic["passed"] and not a.skip_consistency_gate:
        lines = [f"  - {x['model']}：note「{x['note']}」 vs crash「{x['crash']}」" for x in _ic["conflicts"]]
        sys.exit("内部一致性门禁：note 文案与结构字段自相矛盾：\n" + "\n".join(lines) +
                 "\n→ 修数据（更新/删除冲突文案），不许只改展示绕过。"
                 "\n→ 确认为口径差非错误时用 --skip-consistency-gate 并在报告说明。")
    print(f"[内部一致性门禁] {_ic['checked']} 款候选，note/结构字段矛盾 0")

    # 质价比法（v10.73：同价位段残差）：
    # 旧 value 是人工裁决分（名实不符=电池密度、不可复算）；v10.17 改价格残差（分档），
    # v10.47 曾改「全池统一锚」，但实测把「便宜」本身变成加分项（本池最低价 G6 的
    # value 直接 9.0 排全榜第 2，而空间/品控/安全均中下游），用户判定不合理；
    # v10.73 改回同价位段残差：锚点=本档每万元产品力的中位（档内<3款回退全池中位），
    # 价低但产品力同样低的车不再占便宜，跨档不可比。保值率仍不进 value（v10.49）。
    # 必须在 sibling/安全门禁之前：新 value 要过同源车一致性校验；此时七维主体
    # （规则增量/体验/效率/达成率/软上限/证据封顶/空间校准）已定稿，仅差后续
    # airbags/ground_clearance 对 safety 的 ≤±0.3 微调（对 pf 影响<0.02）。
    # 子榜从全价位固定快照读档中位（value_bins），保证同车跨榜同分。
    from modules.engine import value_residual as _VR
    _wv = {x["key"]: x["weight"] for x in cfg.get("dimensions", [])}
    # v10.45 根因修复：ppw 分母必须用主榜评分配置价（与产品力同配置），
    # 不能再用 price_guide[0] 区间最低价（Model Y 26.35后驱价配28.85长续航产品力 →
    # value 虚高 9.54，而 note 写"性价比三车最低"）。评分配置价由 trim_annotate
    # 本地确定性算出（读 trims.json，无网络）；无配置数据的车回退 range_min。
    # 子榜从锚点快照读同一份 price_map，保证同车跨榜 value 一致。
    _price_map = dict((_anchor or {}).get("value_price_map") or {})
    if not _price_map:  # 全价位榜：本池算评分配置价
        try:
            from modules.engine import trim_annotate as _TA
            _td = _TA.annotate_all(cands, cfg)
            for _m, _tdm in _td.items():
                _bt = next((t for t in (_tdm.get("trims") or [])
                            if t.get("name") == _tdm.get("best_trim")), None)
                if _bt and isinstance(_bt.get("price"), (int, float)) and _bt["price"] > 0:
                    _price_map[_m] = float(_bt["price"])
        except Exception as _e:
            print(f"[性价比价格口径] 评分配置价解析失败({_e})，回退区间最低价")
    _vr_report = {"status": "skipped"}
    if not getattr(a, "no_value_residual", False):
        _vr_report = _VR.apply(cands, weights=_wv,
                               fixed_bins=(_anchor or {}).get("value_bins"),
                               price_map=_price_map)
        _n_trim = sum(1 for c in cands if c.get("model") in _price_map)
        print(f"[质价比·同价位段] 覆盖 {_vr_report['covered']}/{_vr_report['n']} 款（"
              f"每万元产品力对本档中位，档内<3款回退全池"
              + ("，读全榜锚点" if _anchor else "，本池拟合")
              + f"，评分配置价 {_n_trim}/{_vr_report['n']} 款）")

    # v9.7.43：同源车评分一致性门禁——同品牌/同价带/同词根的车，维度分差>1.5
    # 必须在 score_diff_reasons 登记事实理由，否则拦截。根治"证据不足被当成产品差"
    # （007GT 曾 value 6.0/cabin 8.4 无据压低）。
    from modules.engine import sibling_check as SC
    _sr = SC.check(cands, data.get("score_diff_reasons", {}))
    if not _sr["passed"] and not a.skip_sibling_gate:
        lines = [f"  - {x['pair']} {x['dim']} 差{x['diff']}（{x['a']} vs {x['b']}）" for x in _sr["issues"]]
        sys.exit("同源车评分一致性门禁：以下同源车维度分差>1.5且未登记理由：\n" + "\n".join(lines) +
                 "\n→ 对齐分数，或在候选池 score_diff_reasons[车A|车B][维度] 写明事实差异原因。"
                 "\n→ 确认差异合理用 --skip-sibling-gate（须在报告说明）。")
    print(f"[同源车一致性门禁] {_sr['pairs']} 对同源车 / 无据差异 {len(_sr['issues'])}")

    # v9.7.43：安全分严谨性门禁——有官方碰撞成绩的车必须按体系映射表给分，
    # 且不得低于无成绩车中位（防"有强证据反分更低"，007GT 曾 7.2 < 无成绩8.0）。
    # 各体系不可跨体系套同一得分率公式，映射表见 official_safety_scores.json。
    from modules.engine import safety_check as SFC
    _sc = SFC.check(cands)
    if not _sc["passed"] and not a.skip_safety_gate:
        lines = [f"  - [{x['type']}] {x['model']}: {x['actual']} → 应 {x['expected']}" for x in _sc["issues"]]
        sys.exit("安全分严谨性门禁：\n" + "\n".join(lines) +
                 "\n→ 按 official_safety_scores.json 映射表统一有成绩车的安全分与 basis。"
                 "\n→ 确认合理用 --skip-safety-gate（须在报告说明）。")
    _um = _sc.get("unmapped") or []
    _am = _sc.get("auto_mapped") or []
    print(f"[安全分门禁] 池内有碰撞成绩 {_sc.get('has_score', _sc['checked'])} 款 / "
          f"表内人工 {_sc.get('mapped', _sc['checked'])} 款 / 自动映射 {len(_am)} 款 / "
          f"无来源 {len(_um)} 款 / 问题 0"
          + (("（无来源：" + "、".join(x["model"] for x in _um[:5])
              + ("…" if len(_um) > 5 else "") + "，仍按无成绩插补）") if _um else ""))

    # v9.7.43：生命周期中文化门禁——所有状态值必须有中文映射，
    # 防止 on_sale 这类漏映射导致页面直出英文（特斯拉曾中招）。
    from modules.board import board_view as BView
    _lc = BView.check_lifecycle_coverage(cands)
    if not _lc["passed"]:
        lines = [f"  - {s}：{', '.join(ms)}" for s, ms in _lc["missing"].items()]
        sys.exit("生命周期中文化门禁：以下状态值无中文映射，页面会直出英文：\n" + "\n".join(lines) +
                 "\n→ 在 board_view.LIFECYCLE_STATUS_CN 补映射。")

    # v9.7.43：分数证据文本一致性门禁——score_evidence 文本不得与 safety_basis 矛盾
    # （007GT 曾分数改8.8/basis改euro_ncap，文本却还写"无官方碰撞成绩"）。
    from modules.engine import evidence_consistency as EC
    _ec = EC.check(cands)
    if not _ec["passed"]:
        lines = [f"  - [{x['rule']}] {x['model']}: {x['basis']} / {x['text']}" for x in _ec["issues"]]
        sys.exit("证据文本一致性门禁：以下车的证据文本与结构化字段矛盾：\n" + "\n".join(lines) +
                 "\n→ 按结构化字段(safety_basis/scores)修正 score_evidence 文本，不许只改展示绕过。")
    print(f"[证据文本一致性门禁] {_ec['checked']} 款有证据文本 / 矛盾 0")

    # 第三方实测/拆车证据 → adjust（带封顶）。放在闸门之前：adjust 不影响准入，
    # 只影响名次；审计行进报告，方便逐条质疑。
    tp_audit = []
    if not a.no_thirdparty:
        cands, tp_audit = thirdparty.apply(cands)
    # 被动安全气囊：事实列 + 仅无官方成绩车的受限 safety 修正。
    # 必须放在 thirdparty.apply 之后、闸门之前：它改的是 scores.safety，
    # 会影响后续所有打分 / 稳健性 / 三档，但不能影响准入。
    if not a.no_airbags:
        cands, _ = airbags.apply(cands)
    # 最小离地间隙：事实列 + 空载口径受限 safety 修正（±0.3，不重复计分）。
    # 与 airbags 同位置：改 scores.safety，必须在 safety_check 门禁之前。
    if not getattr(a, "no_ground_clearance", False):
        cands, _ = ground_clearance.apply(cands)
    # 官网配置事实库：只写 c['spec_facts']，SCORING_ENABLED=False 且有前后
    # scores 深比较断言。放在打分前是为了让报告能拿到全量候选，不改任何分数。
    if not a.no_spec_facts:
        cands, _ = spec_facts.apply(cands)
    # 具体配置阶梯：只写 c['trim_ladder']，SCORING_ENABLED=False 且对
    # scores/total/adjust/price/trim 做前后深比较断言。不改「评分配置」原值。
    if not a.no_trims:
        cands, _ = trims.apply(cands)
    custom = parse_sliders(a.sliders)
    wv = L.build_schemes(cfg, custom).get("用户自定义") if custom else None

    # 人工分核算审计（--score-audit）：此时分数已全部调整完，对照「出榜最终分 vs 客观规则分」。
    # 只审计不改分：链路盘点 + 从 evidence 硬参数按 spec_score 规则算客观分，差异是待核实线索。
    _sa_audit = None
    if getattr(a, "score_audit", False):
        from modules.engine import score_audit as SA
        _sa_audit = SA.diff_audit(cands, threshold=0.5)
        _sa_sum = SA.summarize(_sa_audit)
        print("[人工分核算] 链路缺口 " + " / ".join(
            f"{r['dim']}:{r['gap']}" for r in SA.link_audit() if r["gap"] != "WIRED")
            + f"；差异 {_sa_audit['diff_count']} 项（只审计不改分）")

    _pool_data = json.load(open(a.candidates, encoding="utf-8"))
    # v10.16.0：标题去硬编码。cfg.label 写死了「全价位 · 21款候选」，子榜复用同一
    # category 就会误标（12-18万 11 款也写"全价位·21款"）。改为按本池实际款数与
    # 锚点来源动态生成标题，事实与文案一致。
    _label_raw = str(cfg.get("label") or "榜单")
    import re as _re
    _label_base = _re.sub(r"（[^）]*(?:全价位|款候选)[^）]*）", "", _label_raw).strip()
    _pl = sorted(float(c["price"]) for c in cands
                 if isinstance((c.get("price")), (int, float)))
    _price_desc = (f"{_pl[0]:g}-{_pl[-1]:g}万" if len(_pl) >= 2 and _pl[0] != _pl[-1]
                   else (f"{_pl[0]:g}万" if _pl else "价位未标注"))
    _scope = "固定锚点子榜" if _anchor else "全价位主榜"
    _title = f"{_label_base}（{_price_desc} · {len(cands)}款候选 · {_scope}）"
    parts = [f"# {_title} · 出榜报告", ""]
    parts.append(f"> 来源：`{os.path.basename(a.candidates)}`；"
                 f"链路 取数→闸门→打分→稳健性→三档→审查。**全程无辩论。**")
    # v9.7.36：数据版本可追溯——读者知道分数是哪个 skill 版本/指纹生成的
    _gb = _pool_data.get("_generated_by") if isinstance(_pool_data, dict) else None
    if _ev_sum:
        _lines = "；".join(f"{d} {v['count']} 款 → {v['cap']:g}（锚点 {v['anchor']}）"
                          for d, v in _ev_sum.items())
        parts.append(f"> **维度证据封顶**：{_lines}。规则分两种：`safety` 用「有实锤车最低分」"
                     "（碰撞是门槛，没成绩不能和有成绩并列）；`adas/cabin/control/quality` 用"
                     "「有证据车中位」（举证责任只要求不超过典型水平，不是垫底）。"
                     f"有证据车不足 3 款的维度不封顶（避免拿 2 款车的噪声当全池天花板）。"
                     f"**{'、'.join(d for d in EG.uncapped_dims() if d != 'space')} 不封顶**——配置/三电参数是硬数据。")
    if _space_adj:
        _sp_lines = "；".join(f"{x['model']} {x['prev']:g}→{x['capped']:g}"
                            for x in _space_adj[:6])
        _sp_eff = [x for x in _space_adj if x.get("signal") == "efficiency"]
        _sp_size = [x for x in _space_adj if x.get("signal") != "efficiency"]
        parts.append(f"> **空间一致性封顶**：{_sp_lines}。`space` 不受实测证据门槛约束，"
                     f"但随包人工分从未用硬数据校验——本榜两级信号只压虚高不倒扣："
                     f"①尺寸（轴距>长×宽×高体积）：以「尺寸不小于本车的车」space 中位为上限，"
                     f"容忍带 {SP.TOL:g}（高得房率车放宽 {SP.HIGH_EFF_RELIEF:g}），"
                     f"可比车不足 {SP.MIN_PEERS} 款不封顶；"
                     f"②真实空间表现（得房率）：得房率明显低于同尺寸正常车却高分，"
                     f"压到正常车中位（容忍带 {SP.EFF_TOL:g}）。"
                     f"本榜信号①命中 {len(_sp_size)} 款、信号②命中 {len(_sp_eff)} 款。")
    if _space_uncal:
        parts.append(f"> **空间硬数据待补**：{'、'.join(_space_uncal[:8])}（共 {len(_space_uncal)} 款）"
                     "无轴距/长宽高，无法做一致性校验——用 "
                     "`research.fill_experience.extract_dimensions` 从参数页提取候选事实，"
                     "人工核实后补进 `categories/vehicle_physics.json`（补后自动优先用轴距）。")
    if _sf_per_car:
        _sf_real_lines, _sf_store_lines = [], []
        for _m, _feats in list(_sf_per_car.items())[:8]:
            _g = SPF.by_category(_feats)
            if _g[SPF.CAT_REAL]:
                _sf_real_lines.append(_m + "：" + "、".join(
                    f"{x['label']}{x['value']:g}{x['unit']}" for x in _g[SPF.CAT_REAL]))
            if _g[SPF.CAT_STORAGE]:
                _sf_store_lines.append(_m + "：" + "、".join(
                    f"{x['label']}{x['value']:g}{x['unit']}" for x in _g[SPF.CAT_STORAGE]))
        _parts_sf = [f"> **空间要素披露**（从 `evidence.space_evidence` 结构化，"
                     f"{_sf_sum['cars_with_features']} 款有数据；只披露/校验不直接加分）："]
        if _sf_real_lines:
            _parts_sf.append("**真实空间表现**（参与一致性校验）：" + "；".join(_sf_real_lines) + "。")
        if _sf_store_lines:
            _parts_sf.append("**储物便利**（不混进乘坐空间分，仅作选购参考）：" + "；".join(_sf_store_lines) + "。")
        if _sf_sum["low_confidence"]:
            _parts_sf.append("低置信（厂商标定/软文口径，待核实）：" + "、".join(_sf_sum["low_confidence"][:8]) + "。")
        parts.append("\n".join(_parts_sf))

    if _sa_audit is not None:
        from modules.engine import score_audit as SA
        _links = SA.link_audit()
        _gap_lines = "；".join(f"{r['dim']}={r['gap']}" for r in _links)
        _diff_lines = "；".join(
            f"{d['model']} {d['dim']} {d['human']:g}→客观{d['objective']:g}({d['diff']:+g})"
            for d in _sa_audit["diffs"][:12])
        parts.append("> **人工分核算审计**（只审计不改分）：链路状态 " + _gap_lines + "。"
                     f"跳过已闭环维度 {('、'.join(_sa_audit['skipped_wired']) or '无')}；"
                     f"有足够硬参数可核算的车 {_sa_audit['checked']} 款，覆盖 "
                     + "、".join(f"{k}:{v}" for k, v in _sa_audit["coverage"].items()) + "。"
                     f"差异超 {_sa_audit['threshold']:g} 共 {_sa_audit['diff_count']} 项"
                     + ("：" + _diff_lines if _diff_lines else "") + "。"
                     "**差异是待核实线索、不是改分指令**——硬件强≠体验好（零实测车已由证据门槛封顶），"
                     "须人工核实来源后再决定是否调分；无数据可算的维度（如 powertrain/value）"
                     "本身就是「规则有了但数据没进候选池」的证据。")

    if _exp_gaps:
        parts.append(f"> **证据缺口**：{'、'.join(g['model']+chr(40)+g['dim']+chr(41) for g in _exp_gaps[:6])}"
                     f"（共 {len(_exp_gaps)} 条）的文本里有实测数据但证据库无记录，"
                     "按「零实测」封顶——核实来源后补录 `categories/experience_facts.json`。")
    if _sa:
        _chg = [x for x in _sa if x["prev"] is not None and abs(float(x["prev"]) - x["score"]) > 0.05]
        # 文案按每车实际映射规则显示，不再写死 C-NCAP 得分率公式——
        # Model Y 走 C-IASI 等级映射、Model 3 走 Euro NCAP 星级，写死 C-NCAP 会误导。
        from modules.engine import crash_systems as _CSdoc
        _sys_counts = {}
        for x in _sa:
            _basis = _CSdoc.normalize((x.get("crash") or "").split()[0]) if x.get("crash") else None
            _sys_counts[_CSdoc.cn_name(_basis) if _basis else "官方碰撞成绩"] = \
                _sys_counts.get(_CSdoc.cn_name(_basis) if _basis else "官方碰撞成绩", 0) + 1
        _sys_text = "、".join(f"{k} {v}款" for k, v in sorted(_sys_counts.items(), key=lambda t: -t[1]))
        parts.append(f"> **安全分来源**：{len(_sa)} 款按官方碰撞成绩自动映射（{_sys_text}；"
                     f"各体系按 `official_safety_scores.json` 的映射规则换算，"
                     f"得分率/等级/星级各用对应口径，不跨体系套用）"
                     + (f"，其中 {len(_chg)} 款较原插补值有调整：" +
                        "、".join(f"{x['model']} {x['prev']}→{x['score']:g}" for x in _chg[:5])
                        if _chg else "，与原插补值一致") + "。")
        # 多测评融合口径披露（v10.4.0，2026-11-21 用户要求「有评测就要纳入」）：
        # 同体系多维度按木桶取最低（Euro NCAP/C-NCAP 官方口径），跨体系按严格度取主分、
        # 其余列示不叠加；C-IASI 数字图标车以官网新闻稿纯文字等级救回。
        if _fusion:
            _fl = []
            for x in _fusion[:8]:
                if not x.get("primary"):
                    continue
                _all = [s["display"] for s in x.get("all_scores", [])]
                _fl.append(f"{x['model']} 纳入 {' + '.join(_all)} → 主分 {x['primary']}")
            parts.append("> **多测评纳入**：同一体系内多维度按木桶取最低档（Euro NCAP/"
                         "C-NCAP 官方口径，总评级由最低分项决定），不同体系并存时全部列入、"
                         "取最严格体系作主分（C-IASI > Euro NCAP/C-NCAP），其余不叠加——"
                         "各国 NCAP 无对应关系，不套同一公式。C-IASI 数字图标无官方文字"
                         "对照不解码，改由官网结果发布稿的纯文字等级核验纳入。"
                         + ("；".join(_fl) + "。" if _fl else ""))
            _fused_docs = [x for x in _fusion if any(s.get("via") == "C-IASI新闻稿文字"
                                                      for s in x.get("all_scores", []))]
            if _fused_docs:
                parts.append("> **新闻稿核回**：" + "、".join(
                    f"{x['model']} 官方文字稿 {x['primary']}" for x in _fused_docs)
                    + "（稿件仅给等级分布、未点名车型的维度一律不猜、不计分）。")
    if _p_still:
        parts.append(f"> **价格缺口**：{'、'.join(_p_still[:6])}"
                     f"（共 {len(_p_still)} 款）候选池与 trims 均无官方指导价，"
                     "这类车按 `price_unknown` 落榜，需补采集后重出。")
    if _gb:
        parts.append(f"> **数据版本**：候选池生成于 skill v{_gb.get('skill_version')}"
                     f"（指纹 `{str(_gb.get('fingerprint'))[:8]}`），"
                     f"当前出榜 skill v{SF.skill_version()}；"
                     + ("版本一致。" if _fp["ok"] else "**数据已过期，本次为 --force-stale 强制出榜。**"))

    # v9.7.17：候选池与证据质量披露自动生成（不再手工写报告，重跑不丢）
    _all = cands
    _src_named = sum(1 for c in _all if c.get("source_ids"))
    # 用碰撞体系真相源判定「是否真有成绩」，不在这里另写一套关键词表
    # （曾各写一套：披露写 4/21、实际 3 款，因为「已抽车未测」被当成有成绩）
    from modules.engine import crash_systems as CRS
    # 只统计 crash 字段里能解析出成绩的车（不看 note：note 里可能有「无成绩+快充百分比」）
    _crash_named = sum(1 for c in _all if CRS.parse_score(c.get("crash")))
    _ev_cnt = {k: sum(1 for c in _all if c.get("evidence_status") == k) for k in ("A", "B", "C")}
    _ext_n = sum(1 for c in _all if c.get("extended") or c.get("score_evidence"))
    # v10.24.0：入池口径文案从品类 gates 动态生成（HTML/报告共用 board_view 唯一来源），
    # 不再写死「紧凑型车+纯电动月销榜前10」这类在轿车/SUV榜上错误的旧口径。
    from modules.board import board_view as _BV
    _pool_criteria = _BV.pool_criteria_text(cfg, label="", as_of=str(getattr(a, "as_of", "") or ""))
    parts.append(
        f"> **候选池与证据披露（自动生成）：**本池 {len(_all)} 款，"
        f"带核验来源 {_src_named}/{len(_all)}，有官方碰撞成绩 {_crash_named}/{len(_all)}，"
        f"证据等级 A/B/C = {_ev_cnt['A']}/{_ev_cnt['B']}/{_ev_cnt['C']}，"
        f"其中 {_ext_n} 款为网查补采（置信低）。" + _pool_criteria)
    _pmnotes = (_pool_data.get("pool_meta") or {}).get("notes") if isinstance(_pool_data, dict) else None
    if _pmnotes:
        parts.append("> **候选池说明：**" + "；".join(str(x) for x in _pmnotes) + "。")
    parts.append(
        "> **分数性质：**全部分数为「官方参数 + 客观差异规则推算」，**非实车实测**；"
        "冬测续航达成率、充电曲线、麋鹿成绩等第三方统一实测未纳入打分。"
        "档位与名次是两套逻辑，同档内逐名排序参考价值有限。")
    if custom:
        parts.append(f"> 用户优先级："
                     + " / ".join(f"{k} {int(v)}" for k, v in custom.items()))
    if tp_audit:
        parts.append(thirdparty.audit_report(tp_audit))
        parts.append("")
    fdata = None
    if a.forecast and os.path.exists(a.forecast):
        fdata = json.load(open(a.forecast, encoding="utf-8"))
        parts.append(f"> 未上市预测：第{fdata.get('batch')}批工信部备案"
                     f"（{fdata.get('date')}）+ 新闻口径，独立分区不计入正式名次。")
        parts.append("")
    cdata = None
    if a.complaints and os.path.exists(a.complaints):
        cdata = json.load(open(a.complaints, encoding="utf-8"))
        parts.append(f"> 投诉反馈：车质网缓存窗口 {cdata.get('window_days')} 天"
                     f"（覆盖 {', '.join(cdata.get('cache_cover') or [])}），"
                     "密度=投诉/万台，仅参考不改档。")
        parts.append("")

    # v10.51.0：底盘事实覆盖率披露（操控维规则的结构化数据源；告警不阻断）
    if isinstance(_chassis_sum, dict) and "n" in _chassis_sum:
        _miss = _chassis_sum.get("missing") or []
        _miss_txt = ""
        if _miss:
            _miss_txt = ("（全缺：" + "、".join(_miss[:10])
                         + ("…" if len(_miss) > 10 else "") + "）")
        parts.append(
            f"> **底盘事实覆盖（v10.51.0 起结构化采集）：**"
            f"{_chassis_sum['full']}/{_chassis_sum['n']} 款三字段（前悬/后悬/驱动）"
            f"齐全，{_chassis_sum['partial']} 款部分覆盖，"
            f"{_chassis_sum['none']} 款全缺{_miss_txt}。"
            "全缺车的操控维规则增量不参与（缺数据≠差，不插补）；"
            "事实来源与引文见 trims 条目 chassis_sources / 人工核验条目。")
        parts.append("")

    # 1 逐榜：分榜 → 闸门 → 三档 → 审查 → 稳健性 → 权重审计 → 三榜并列
    #    顺序是「先分榜再闸门」，因为四个榜的价格带上限不同（16 万 / 18 万），
    #    gates 必须按榜取，不能全局一刀切。
    # 调整预算审计（v10.15.2 · 审计 P1-5）：多模块叠加同维的总量暴露（只审计不截断）
    from modules.engine import adjust_budget as _AB
    _budget_report = _AB.audit(cands)
    if _budget_report["exceeded"]:
        print(f"[调整预算] ⚠ {len(_budget_report['exceeded'])} 车·维累计调整超名义预算："
              + "、".join(f"{x['model']}/{x['dim']} {x['total_abs']}>{x['nominal_budget']}"
                         for x in _budget_report["exceeded"][:5]))

    # v10.20.0：跨榜档位对照（只展示，不参与判档/打分）——子榜从锚点源全榜 summary
    # 读取全榜档位；当前榜判档与全榜不同时，由报告/页面标注「全榜第X档」。
    # 全榜自身 _anchor=None → 空表 → 不加任何标记。
    _cross_global: dict[str, str] = {}
    if _anchor:
        _rel = (cfg.get("anchor_pool") or {}).get("path")
        if _rel:
            _gp = _rel if os.path.isabs(_rel) else str(paths.ROOT / _rel)
            if os.path.exists(_gp):
                try:
                    _gs = json.load(open(_gp, encoding="utf-8"))
                    for _tmap in (_gs.get("tiers") or {}).values():
                        if not isinstance(_tmap, dict):
                            continue
                        for _tk, _cn in (("T1", "第一档"), ("T2", "第二档"), ("T3", "第三档")):
                            for _nm in (_tmap.get(_tk) or []):
                                _cross_global[str(_nm)] = _cn
                except (OSError, ValueError):
                    _cross_global = {}

    summary = {
        "category": a.category, "source": os.path.basename(a.candidates),
        "in": len(cands), "passed": 0, "dropped": 0, "sliders": custom,
        "boards": {}, "tiers": {}, "review": {},
        # v10.20.0：全榜档位对照（只展示，判档结果仍以本榜 tiers 为准）
        "tier_crossboard": _cross_global,
        # v10.16.0：榜内相对锚点来源（固定锚点=跨榜同分；无=本池现算）
        "anchor_pool": {"enabled": bool(_anchor),
                        "source": (cfg.get("anchor_pool") or {}).get("path") if _anchor else None,
                        "evidence_cap": (_anchor or {}).get("evidence_cap"),
                        "range_median": (_anchor or {}).get("range_median"),
                        "efficiency": (_anchor or {}).get("efficiency"),
                        "space_caps_n": len((_anchor or {}).get("space_caps") or {})},
        # v10.16.0：主榜三电软上限审计（消除效率+达成率叠加的满分）
        "main_powertrain_cap": _pt_capped,
        # v10.17.0：性价比价格残差法审计（供子榜锚点+报告披露）
        "value_residual": _VR.summarize(_vr_report)
            if isinstance(_vr_report, dict) and _vr_report.get("bins") is not None
            else {"status": "skipped"},
        # v9.7.23：未入池车型及原因（用户要求"能比就比，不能比说明原因"）
        "pool_excluded": (_pool_data.get("pool_excluded") if isinstance(_pool_data, dict) else []),
        # 2026-11-21：价格回流审计（回填了哪些、哪些 trims 也没价）
        "price_backfill": {"filled": [{"model": m, "price_guide": pg} for m, pg in _p_filled],
                           "still_missing": _p_still},
        # v10.15.2：销量权威回填审计（懂车帝逐月，纯电榜优先/全部榜标≤上界/手填分歧）
        "sales_backfill": _sales_report,
        # v10.14.0：三段回流合并为唯一同步器（sync_safety_scores），
        # 审计字段同步收敛为一份（source = safety_scores SSOT）。
        "crash_backfill": {"filled": [{"model": m, "crash": d, "basis": b} for m, d, b in _c_filled],
                           "unresolved": _c_unres,
                           "source": "categories/safety_scores.json（唯一事实源）"},
        # v10.5.0：C-IASI 数字图标车联网救回审计 + 展示覆盖门禁审计
        "ciasi_rescue": _ciasi_rescue,
        "crash_auto_verify": _crash_verify,
        # v10.10.0：车辆事实单一数据源审计（权威源 94 台 / 派生副本漂移）
        "vehicle_facts_consistency": _vf_consistency,
        # v10.51.0：底盘事实覆盖率（操控维结构化数据源；告警不阻断）
        "chassis_coverage": _chassis_sum,
        "crash_display_audit": {"leaks": _display_leaks},
        # 多成绩审计：全部成绩留痕 + 主成绩选择口径（取证据最强，不取最高/不叠加）
        "crash_multi_scores": [
            {"model": c["model"], "scores": c["crash_all_scores"]}
            for c in cands if c.get("crash_all_scores")
        ],
        # v10.4.0：多测评融合审计（同体系木桶取最低/跨体系取最严格主分/新闻稿一手等级）
        "crash_fusion": [
            {"model": x["model"], "primary": x.get("primary"), "rule": x.get("rule"),
             "all_scores": [{"display": s.get("display"), "score": s.get("score"),
                             "basis": s.get("basis"), "via": s.get("via")}
                            for s in x.get("all_scores", [])],
             "excluded": x.get("excluded", [])}
            for x in _fusion if x.get("primary")
        ],
        # v10.6.0：三电能耗效率修正审计（全榜统一、榜内中位基准、±0.4封顶）
        "powertrain_efficiency": {
            "enabled": not getattr(a, "no_powertrain_efficiency", False),
            "applied": _eff_applied,
            "detail": {c["model"]: c["powertrain_efficiency"]
                       for c in cands if c.get("powertrain_efficiency")},
        },
        # v10.15.2（审计 P1-5）：同维多模块叠加调整预算审计（只披露不截断）
        "adjust_budget": _budget_report,
        # v10.15.2（审计 P0-1）：续航达成率调整审计（物理估算/一手实测优先、榜内中位锚±1.0）
        "range_achievement": {
            "enabled": not getattr(a, "no_range_achievement", False),
            **{k: v for k, v in _ach_report.items() if k != "applied"},
            "detail": {c["model"]: c["range_achievement"]
                       for c in cands if c.get("range_achievement")},
        },
        # v10.11.0：配置驱动规则增量审计（逐项中位锚点、缺项不参与、双向封顶）
        "rule_adjust": {
            "enabled": True,
            "summary": _rule_rep,
            "detail": {c["model"]: c["rule_adjust"]
                       for c in cands if c.get("rule_adjust")},
        },
        "safety_auto": {"applied": _sa, "skipped": _ss},
        "safety_cap": _cap_adj,
        "dimension_cap": _dim_cap,
        "space_calibration": _space_sum,
        "space_features": _sf_sum,
        "space_features_detail": {m: feats for m, feats in _sf_per_car.items()},
        "score_audit": (_sa_sum if _sa_audit is not None else None),
        "experience_gaps": _exp_gaps,
        "safety_table_sync": _st_sync,
        # v9.7.20：候选池披露同步给页面（评审反复指出报告有、页面看不到）
        "pool_disclosure": {
            "total": len(_all), "sourced": _src_named, "crash": _crash_named,
            "ev_a": _ev_cnt["A"], "ev_b": _ev_cnt["B"], "ev_c": _ev_cnt["C"],
            "extended": _ext_n,
            # v10.24.0：入池口径文案（HTML 与报告共用唯一来源，禁止渲染层写死）
            "criteria": _pool_criteria,
        },
    }
    # 2026-11-22：官方召回事件注入（用户要求"召回入质量维度"）。
    # recall_events.json 有官方公告+批次，quality_score._recall_penalty 早已支持该字段，
    # 此前候选池全缺此字段导致召回因子形同虚设。必须在 QS.apply 之前注入，
    # 否则重算 quality 时读不到。coverage_uncertain 的条目（如一代ES6召回不覆盖在售换代车）
    # 由模块自动跳过，不误扣。只读数据源→注入候选，不改任何既有分数。
    from modules.engine import recall_ingest as _RI
    _recall_report = _RI.apply(cands)
    if _recall_report["injected"]:
        print(f"[召回事件] 注入 {_recall_report['injected']} 款官方召回"
              + (f"（跳过存疑 {len(_recall_report['skipped_uncertain'])} 款）"
                 if _recall_report["skipped_uncertain"] else ""))

    # v10.63 根因固化：质量维的投诉数据不再依赖人记得传 --complaints + 预先聚合。
    # 根因（2026-11-24）：流程写在 SKILL.md 里，执行时照样漏做 → 质量分全 8.0。
    # 文档纪律靠不住，必须代码强制：--quality 时若 cdata 为空，自动从投诉缓存
    # 按当前 cands（已回填销量）就地聚合，密度 per_10k 当场可算。
    # 用内存 cands 而非候选池文件：销量回填只在内存发生，写文件会引入副本漂移。
    _auto_cdata = None
    if a.quality and not cdata:
        try:
            from modules.research.fetch_complaints import aggregate as _agg, load_cache as _loadc
            _rows = _loadc()  # 默认 runs/_complaints/complaints.jsonl
            _auto_cdata = _agg(_rows, cands)  # 用刚回填过销量的内存 cands
            cdata = _auto_cdata
            parts.append(f"> 投诉反馈（自动聚合）：车质网缓存窗口 "
                         f"{cdata.get('window_days')} 天，命中 "
                         f"{len(cdata.get('models') or {})} 款，密度=投诉/万台，仅参考不改档。")
            parts.append("")
            print(f"[投诉自动聚合] 缓存 {len(_rows)} 条 → 命中 {len(cdata.get('models') or {})} 款"
                  "（无需 --complaints 参数）")
        except Exception as _ex:  # 聚合失败不阻断，显式告警
            print(f"[投诉自动聚合] 失败，质量维退化为纯网查PPH：{type(_ex).__name__}: {_ex}")

    if a.quality:
        cands, _ = QS.apply(cands, cdata)
        # v10.74：把旧「质量与品控」一格拆成两个独立维度（用户 2026-11-25 指示）
        #   reliability（可靠性）= 故障频度（投诉密度/PPH），召回不计（属处置不属频发）
        #   quality（质量与品控）= 故障严重度（安全类标签/物理召回处置）
        # 用同一次投诉聚合数据、不同分量，不重算不双计；quality_score.score_one 的
        # 合成分保持不变（历史回归不受影响），这里只把分量分别落到两个维。
        for _c in cands:
            _qd = _c.get("quality_detail") or {}
            _comp = _qd.get("components") or {}
            _sev = 8.0 - float(_comp.get("severe") or 0.0) + float(_comp.get("recall_adj") or 0.0)
            _c.setdefault("scores", {})["quality"] = round(max(3.0, min(9.5, _sev)), 2)
        from modules.engine import reliability_score as _RS
        cands, _r_audit = _RS.apply(cands, cdata)
        _r_cov = _RS.coverage(cands)
        print(f"[可靠性维] 覆盖 {_r_cov['covered']}/{_r_cov['total']} 款"
              f"（故障频度：投诉密度 + PPH；召回不计入）")
        summary["reliability"] = _r_cov
        # 持有成本（三年保值率）：单一真相源 residual_values.json，C 级只展示不计分
        from modules.engine import holding_cost as _HC
        cands, _h_audit = _HC.apply(cands)
        _h_cov = _HC.coverage(cands)
        print(f"[持有成本维] 三年保值率覆盖 {_h_cov['covered']}/{_h_cov['total']} 款"
              f"（C级/未覆盖交中位插补）")
        summary["holding_cost"] = _h_cov
        # v10.72：quality 证据封顶放在 QS.apply 之后（此时 quality_detail 已生成，
        # _has_pph 能识别"有投诉明细但PPH样本不足"的车，不再一律 force）。
        _q_adj = EG.apply_all(cands, only=["quality", "reliability", "holding_cost"])
        if _q_adj:
            # v10.74：三个维各自的封顶数分开打印（旧版只打总量，把可靠性/持有成本的
            # 插补数误算成「质量证据封顶」，口径披露失真）
            _by = EG.summarize(_q_adj)
            _msg = " / ".join(f"{_d} {_v['count']} 款→{_v['cap']:g}"
                              for _d, _v in _by.items())
            print(f"[证据封顶·拆维] {_msg}（无证据=中位插补，不判劣、不冒充更好）")
        parts += ["---", "", QS.report(cands), ""]
        summary["quality"] = QS.coverage(cands)
        summary["quality"]["complaints_auto"] = bool(_auto_cdata)
    summary["recall"] = {"status": _recall_report["status"],
                         "injected": _recall_report["injected"],
                         "detail": _recall_report["injected_detail"],
                         "skipped_uncertain": _recall_report["skipped_uncertain"]}
    for board in L.board_items(cfg):
        label = board.get("label") or "全部"
        sub = L.filter_board(cands, board)
        if not sub:
            continue
        passed, dropped, greport = gate_report(sub, cfg, board)
        parts += ["---", "", f"# {label}", "",
                  f"> 候选 {len(sub)} 款 → 过闸 {len(passed)} 款", "", greport, ""]
        summary["boards"][label] = {"in": len(sub), "passed": len(passed),
                                    "dropped": len(dropped)}
        summary["passed"] += len(passed)
        summary["dropped"] += len(dropped)
        if not passed:
            parts += ["> **本榜无车通过闸门**：该价格带内没有同时满足"
                      "「官方级别 + 纯电 + 指导价 ≤ 上限」的车型。", ""]
            summary["tiers"][label] = {"T1": [], "T2": [], "T3": []}
            summary["review"][label] = "无车过闸"
            continue
        _band = ((board.get("gates") or {}).get("price_band") or [None, None])[1]
        # v9.7.43：判档/主表/稳健性统一用「主榜同款最高配置分」，杜绝车系基础分与
        # 配置锚定分两套口径打架（MG07 主榜845版88.0、判档却用610版84.0→0/60）。
        passed = L.with_config_scores(passed, cfg) if not a.no_config_anchor else passed
        parts.append(L.top_table(passed, cfg, wv, 10, band=_band)["report"])
        parts.append("")
        _tier = L.tier_report(passed, cfg, wv, label, airbag_gate=a.airbag_gate,
                              crash_gate=a.crash_gate, crossboard=_cross_global)
        parts.append(_tier["report"])
        parts.append("")
        # v9.7.18：证据 C 软提示（只降第一档资格、不判劣）——写入 summary 供页面展示
        _soft = []
        for _tk in ("T1", "T2", "T3"):
            for _rec in (_tier.get("detail") or {}).get(_tk, []):
                if _rec.get("evidence_soft"):
                    _soft.append({"model": _rec.get("model"), "tier": _tk,
                                  "reason": "；".join(_rec["evidence_soft"])})
        summary.setdefault("evidence_soft", []).extend(_soft)
        parts.append(L.review_report(passed, cfg, wv)["report"])
        parts.append("")
        _rob = L.robust_report(passed, cfg, custom)
        parts.append(_rob["report"])
        # v9.7.16：把 τ 稳健性指标写进 summary，供页面顶部显示（只展示不改分）
        summary["stability"] = {
            "kendall_mean": round(float(_rob.get("kendall_mean") or 0), 3),
            "n_methods": _rob.get("n_methods"), "n_schemes": _rob.get("n_schemes"),
            "note": "10种MCDA×多套权重两两Kendall τ均值；<0.8逐名排序不可靠，≥0.9较稳定",
            # v9.7.48：连续名次指标（补二值命中截断盲区）+ 产品力≠市场热度口径
            "rank_mean_by_model": {
                nm: round(float(a["rank_mean"]), 1)
                for nm, a in (_rob.get("per_alt") or {}).items()
                if a.get("rank_mean") is not None},
            "scope_note": ("本榜8维衡量产品力而非市场热度：品牌力/超充网络/保值预期/售后覆盖"
                           "未单列计分。可能出现产品力排第5但全球销量第一（如Model3）——"
                           "不是算法错也不是市场错，是二者回答的问题不同。算法间高度相关"
                           "只说明共享偏见、不代表结论正确。"),
        }
        parts.append("")
        parts.append(L.weight_audit(passed, cfg, wv)["report"])
        parts.append("")
        parts.append(L.tri_report(L.score_rows(passed, cfg, wv), cfg, passed, 10, wv)["report"])
        parts.append("")
        if fdata:
            fitems = (fdata.get("boards") or {}).get(label) or []
            parts += FC.render_board(label, fitems, len(passed))
        _tr = L.tier_report(passed, cfg, wv, label, airbag_gate=a.airbag_gate,
                            crash_gate=a.crash_gate, crossboard=_cross_global)
        summary["tiers"][label] = _tr["tiers"]
        # v9.7.43：第二档"强推荐"名单（有碰撞实锤+高分+无硬风险，但换算法不够稳压）
        summary.setdefault("strong_picks", {})[label] = [
            x["model"] for x in (_tr.get("detail") or {}).get("T2", []) if x.get("strong_pick")]
        # v9.7.48：强推荐原因(family=家族命中不够 / steady=稳定但名次略后)，供页面分情况说明
        summary.setdefault("strong_pick_reasons", {})[label] = {
            x["model"]: x.get("strong_pick_reason") or ""
            for x in (_tr.get("detail") or {}).get("T2", []) if x.get("strong_pick")}
        # v9.7.43：高分低档车的T1卡点导出给页面（如实展示为何分高却没推荐）
        _blockers = {}
        for _tk in ("T1", "T2", "T3"):
            for _x in (_tr.get("detail") or {}).get(_tk, []):
                if _x.get("t1_blockers"):
                    _blockers[_x["model"]] = {"tier": _tk, "score": _x.get("score"),
                                              "blockers": _x["t1_blockers"],
                                              "sales": _x.get("sales")}
        summary.setdefault("t1_blockers", {})[label] = _blockers
        # v10.30.0：稳健通道入第一档的车（水桶无短板），供页面给徽标说明
        summary.setdefault("robust_t1", {})[label] = {
            x["model"]: x.get("robust_t1_note") or ""
            for _tk in ("T1",) for x in (_tr.get("detail") or {}).get(_tk, [])
            if x.get("robust_t1")}
        summary["review"][label] = L.review_report(passed, cfg, wv)["verdict"]

    # 1b 被动安全配置事实列（全局一节，逐条带来源；不计入加权）
    if not a.no_airbags:
        parts += ["---", "", airbags.report(cands), ""]
        cov = airbags.coverage(cands)
        summary["airbags"] = {
            "verified": cov["verified"], "total": cov["total"],
            "coverage_pct": cov["coverage_pct"],
            "adjusted": cov["adjusted"], "todo_n": len(cov["todo"]),
        }

    # 1b-2 最小离地间隙事实列（全局一节，来源+引述可核验；safety 已在打分前受限修正）
    if not getattr(a, "no_ground_clearance", False):
        parts += ["---", "", ground_clearance.report(cands), ""]
        gc_cov = ground_clearance.coverage(cands)
        summary["ground_clearance"] = {
            "verified": gc_cov["verified"], "total": gc_cov["total"],
            "adjusted": gc_cov["adjusted"], "conflicts": gc_cov["conflicts"],
            "todo": gc_cov["todo"],
        }

    # 1c 细分维度决策表 + 官网配置事实（全局一节，不计入加权）
    if not a.no_spec_facts:
        parts += ["---", "", spec_facts.summary_md(cands), ""]
        fcov = spec_facts.coverage(cands)
        gates = spec_facts.admission(cands)
        summary["spec_facts"] = {
            "scoring_enabled": spec_facts.SCORING_ENABLED,
            "verified": fcov["verified"], "total": fcov["total"],
            "coverage_pct": fcov["coverage_pct"], "todo_n": len(fcov["todo"]),
            "dimensions": len(gates),
            "admitted_new_dims": [r["key"] for r in gates
                                  if r["needs_gate"] and r["admitted"]],
            "blocked_dims": [r["key"] for r in gates
                             if r["needs_gate"] and not r["admitted"]],
        }

    # 1d 具体配置阶梯与带内配置推荐（全局一节；完整逐配置表另写 trims.md）
    if not a.no_trims:
        parts += ["---", "", trims.summary_md(cands), ""]
        tcov = trims.coverage(cands)
        summary["trims"] = {
            "scoring_enabled": trims.SCORING_ENABLED,
            "verified": tcov["verified"], "total": tcov["total"],
            "coverage_pct": tcov["coverage_pct"], "todo_n": len(tcov["todo"]),
            "jumps": tcov["jumps"],
            "actionable_models": tcov["actionable_models"],
            "upsell_models": tcov["upsell_models"],
            "price_mismatch_models": tcov["price_mismatch_models"],
        }
        open(os.path.join(a.out, "trims.md"), "w", encoding="utf-8").write(
            trims.report(cands) + "\n")

    # 预测摘要进 summary.json（机读，供月度对比与回测）
    if fdata:
        summary["forecast"] = {
            "batch": fdata.get("batch"), "date": fdata.get("date"),
            "boards": {k: [{"name": r["name"], "p_top10": r["p_top10"],
                            "p_t1": r["p_t1"], "rank": r["rank"]}
                           for r in v]
                       for k, v in (fdata.get("boards") or {}).items()},
        }

    # 投诉反馈区（L2 证据，不改档）：放报告尾部，逐榜列出
    if cdata:
        parts += ["---", "", "# 投诉反馈（车质网 · 参考证据）", "",
                  f"> 窗口 {cdata.get('window_days')} 天｜缓存覆盖 "
                  f"{', '.join(cdata.get('cache_cover') or [])}｜"
                  "密度=投诉/万台。**投诉量没有分母就没有意义**；样本 <5 的车型"
                  "不比较。此区不改三档，只提示「该看哪些车的哪些问题」。", ""]
        models = cdata.get("models") or {}
        rows = [(m, v) for m, v in models.items() if (v.get("count") or 0) >= 5]
        rows.sort(key=lambda kv: -(kv[1].get("per_10k") or 0))
        if rows:
            parts += ["| 车型 | 投诉 | 密度(/万台) | 高频问题 |", "|---|---|---|---|"]
            for m, v in rows:
                tags = "、".join(f"{t}×{n}" for t, n in v.get("top_tags") or []) or "—"
                pk = v.get("per_10k")
                parts.append(f"| {m} | {v['count']} | "
                             f"{pk if pk is not None else '销量缺'} | {tags} |")
            parts.append("")
        # summary 机读
        summary["complaints"] = {
            "window_days": cdata.get("window_days"),
            "cover": cdata.get("cache_cover"),
            "models": {m: {"count": v.get("count"), "per_10k": v.get("per_10k"),
                           "top_tags": v.get("top_tags")}
                       for m, v in models.items()},
        }

    # 召回/保值率等数据扩展在报告正文披露，与页面自洽
    parts += ["---", "", "# 数据扩展披露（2026-11-22）", ""]
    _rec_r = summary.get("recall") or {}
    _ota_n = sum(int(x.get("ota_skipped") or 0) for x in (_rec_r.get("detail") or []))
    parts.append("> **召回事件**：来源国家市场监管总局缺陷产品召回公告，"
                 + (f"注入 {_rec_r.get('injected')} 款（"
                    + "、".join(x["model"] for x in _rec_r.get("detail") or []) + "），"
                    if _rec_r.get("injected") else "本期无召回数据，")
                 + "线下物理/混合措施按严重度有限扣分（high -1.5 / normal -0.5 / 封顶 -3），"
                 + f"纯软件 OTA 修复的召回（本榜 {_ota_n} 起）按 v10.19.0 口径暂不纳入扣分——"
                   "软件缺陷经 OTA 无损修复不等同于硬件安全隐患，避免多起软件召回机械触顶。"
                 + (f"跳过 {len(_rec_r.get('skipped_uncertain') or [])} 款批次不覆盖在售款存疑项（如换代ES6不套用一代平台召回）。"
                    if _rec_r.get("skipped_uncertain") else "")
                 + "未查到召回公告≠无召回。")

    rp = os.path.join(a.out, "report.md")
    open(rp, "w", encoding="utf-8").write("\n".join(parts) + "\n")
    sp = os.path.join(a.out, "summary.json")
    json.dump(summary, open(sp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[ok] {rp}")
    print(f"[ok] {sp}")
    for k, v in summary["review"].items():
        print(f"  {k}: {v}")

    # v8.5: 自动出可视化 HTML。用同一份候选 + 同一配置重建 board_v2 数据，
    # 事实列不计分、分段/产品力复用主榜分数——与 board_v2 单独跑完全一致。
    if not a.no_html:
        try:
            from modules.board import board_v2 as BV
            from modules.board import render_board_html as RH
            from modules.board import board_view as BView
            _here = str(paths.ROOT)
            # v10.15.7 根因修复：页面维度权重必须与打分/报告同源（用户：智驾降权后页面要一致显示）。
            # 原先硬编码 car_ev_1218_quality.json（18%/13.5%），而打分用 --category 配置
            # （car_midsuv_bev.json 25%/8%），导致报告显示 8% 但页面表头仍是 13.5% 的漂移。
            # board_v2.build 只读 cfg["dimensions"]，改用当前 category 配置即与打分同源；
            # 若该配置缺失则回退旧通用配置（兼容老榜）。
            _bv_cfg_path = str(paths.cat(f"{a.category}.json"))
            bv_cfg_path = _bv_cfg_path if os.path.exists(_bv_cfg_path) else str(paths.cat("car_ev_1218_quality.json"))
            policy_path = str(paths.cat("rank_v2_policy.json"))
            if os.path.exists(bv_cfg_path) and os.path.exists(policy_path):
                cfg_v2 = json.load(open(bv_cfg_path, encoding="utf-8"))
                pol = json.load(open(policy_path, encoding="utf-8"))
                facts_v2 = BView.load_fact_library()   # v9.0.1 默认带事实库
                _tiers_v2 = {}
                for tmap in (summary.get("tiers") or {}).values():
                    for tk, label in (("T1", "第一档"), ("T2", "第二档"), ("T3", "第三档")):
                        for nm in (tmap.get(tk) or []):
                            _tiers_v2[nm] = label
                # v9.7.10：页面与报告一致——只把通过闸门的候选（有档位的）传给渲染，
                # 否则被生命周期闸门淘汰的停产车（如秦L EV）会出现在主榜却没档位。
                _passed = set(_tiers_v2.keys())
                # v9.7.10：只把通过闸门的候选传给渲染。
                # v10.30.0 注：页面维度分用原始候选（车系分），判档用配置锚定分——
                # 二者存在落差，但页面总分/名次经 board_v2 另一路径已与 report.md 对齐，
                # 强行改用 graded_by_model 会让 HTML 总分与 report 打架（实测 23/28 变动），
                # 故此处保持原取数，维度落差作为已知边界另案处理。
                cands_for_html = [c for c in cands if c.get("model") in _passed] if _passed else cands
                # v10.72：多体系碰撞成绩"全部展示"——把 fuse 的一手测评全部并进
                # crash 字段（事实层合并），任何渲染路径都能显示；评分仍取 fuse 的
                # 最严格主分，不重复计分。只改展示字符串，不动 safety 分。
                for _hc in cands_for_html:
                    _fus = _hc.get("crash_fusion") or {}
                    _all = [x.get("display") for x in (_fus.get("all_scores") or [])
                            if isinstance(x, dict) and x.get("display")]
                    _main = str(_hc.get("crash") or "")
                    _extras = [x for x in _all if x and x not in _main]
                    if _extras and _main:
                        _hc["crash"] = _main + " / " + " / ".join(_extras)
                # v10.9.0：电池来源联网核验（research 唯一出口）——事实库缺电芯的车
                # 逐台联网查线索；查不到/仅线索的写入候选 _cell_verify，页面电池来源栏
                # 显示「—（原因）」。绝不把搜索线索当事实回填（实跑会串配置）。
                _cell_verify = {"status": "skipped"}
                if not getattr(a, "no_cell_verify", False):
                    try:
                        from modules.research import cell_brand_verify as CBV
                        _cell_verify = CBV.verify_all(
                            cands_for_html, facts=facts_v2,
                            use_cache=not getattr(a, "no_cell_cache", False))
                        for _m, _r in _cell_verify["per_car"].items():
                            if _r["status"] != "ok":
                                for _c in cands_for_html:
                                    if _c.get("model") == _m:
                                        _c["_cell_verify"] = _r
                                        break
                        print("[电芯核验] " + "、".join(
                            f"{k} {v}" for k, v in _cell_verify["summary"].items() if v))
                    except Exception as _ex:                 # 联网失败不阻断出榜
                        _cell_verify = {"status": "error",
                                        "error": f"{type(_ex).__name__}: {_ex}"}
                        print(f"[电芯核验] 失败（缺失将显示原因）：{_cell_verify['error']}",
                              file=sys.stderr)
                # 审计落本地 runs/（零 JSON 规则保护，不进 GitHub/发布包）
                try:
                    json.dump(_cell_verify, open(os.path.join(a.out, "cell_verify.json"), "w",
                                                 encoding="utf-8"), ensure_ascii=False, indent=2)
                except Exception:                            # noqa: BLE001
                    pass
                data_v2 = BV.build(cands_for_html, cfg_v2, pol, facts_v2, tiers=_tiers_v2,
                                   rule_adjust_detail=_rule_detail,
                                   no_experience=True)  # 已在 pipeline 里调过，不重复累加
                # v10.74.4：名次带（插补不确定性 → P10–P90 名次区间）+ 未验证权重。
                # 只加区间/披露，不改任何分数与名次（页面排序仍以 total 为准）。
                try:
                    from modules.engine import rank_band as RB
                    rb = RB.apply_all(data_v2.get("main_board") or [], cands_for_html, cfg_v2)
                    summary["rank_band"] = rb
                    # 名次带要进 summary.json（机读对比/回归用）——summary 文件在 HTML 段之前
                    # 已写过一次，这里补写一次，避免"页面有带、JSON 没有"两套真相。
                    try:
                        json.dump(summary, open(sp, "w", encoding="utf-8"),
                                  ensure_ascii=False, indent=2)
                    except Exception:                  # noqa: BLE001
                        pass
                    _wide = [f"{m} {v['lo']}–{v['hi']}" for m, v in rb["models"].items()
                             if v["hi"] - v["lo"] >= 3]
                    print(f"[名次带] 覆盖 {len(rb['models'])} 款；带宽≥3 名 "
                          f"{len(_wide)} 款" + ("：" + "、".join(_wide[:6]) if _wide else ""))
                except Exception as _rbex:                 # 不阻断出榜
                    import traceback as _tb2
                    _tb2.print_exc(file=sys.stderr)
                    print(f"[warn] 名次带跳过：{type(_rbex).__name__}: {_rbex}", file=sys.stderr)
                _as_of = a.as_of
                if not _as_of:
                    try:
                        from modules.board import monthly as _MO
                        _as_of = _MO.default_sales_month()
                    except Exception:
                        _as_of = "—"
                doc = RH.render(data_v2, summary, a.html_title, _as_of)
                hp = os.path.join(a.out, "榜单.html")
                open(hp, "w", encoding="utf-8").write(doc)
                print(f"[ok] {hp}")
        except Exception as ex:
            # v10.72.1：渲染异常只打一行 type/msg 时排查全靠猜（多碰撞列断点就是这样漏掉的），
            # 这里补完整栈，出问题时一眼看到是哪一行。
            import traceback as _tb
            _tb.print_exc(file=sys.stderr)
            print(f"[warn] HTML 渲染跳过（不影响榜单）：{type(ex).__name__}: {ex}", file=sys.stderr)


if __name__ == "__main__":
    main()
