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
        except Exception:
            ok.append(c)
            continue
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
    ap.add_argument("--no-thirdparty", action="store_true",
                    help="跳过第三方实测/拆车证据（默认启用，走 adjust 封顶通道）")
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
    ap.add_argument("--no-spec-facts", action="store_true",
                    help="跳过官网配置事实节（该节本身不改分，仅作回测对照）")
    ap.add_argument("--no-trims", action="store_true",
                    help="跳过具体配置阶梯与带内配置推荐（默认启用，回测对照用）")
    ap.add_argument("--no-html", action="store_true",
                    help="不随报告生成 HTML 榜单页（默认生成，需 board_v2 链路）")
    ap.add_argument("--as-of", default=None, help="HTML 页面销量数据月标注(YYYYMM)，默认自动取上个月；出榜须让用户看到基于哪月销量")
    ap.add_argument("--universe", help="候选池完整性盘点的同级权威车型清单JSON(默认 categories/universe_<category>.json)")
    ap.add_argument("--skip-universe-gate", action="store_true", help="跳过完整性门禁(须在报告说明为何同级漏收车无需处理)")
    ap.add_argument("--skip-consistency-gate", action="store_true", help="跳过条目内一致性门禁(须在报告说明为何note与结构字段矛盾不是错误)")
    ap.add_argument("--skip-sibling-gate", action="store_true", help="跳过同源车评分一致性门禁(须在报告说明为何无据差异合理)")
    ap.add_argument("--skip-safety-gate", action="store_true", help="跳过安全分严谨性门禁(须在报告说明为何成绩映射分可偏离)")
    ap.add_argument("--html-title", help="HTML 页面标题")
    ap.add_argument("--no-evidence-cap", action="store_true",
                    help="不做安全分举证封顶（仅 A/B 对照，验证封顶确实改变分数）")
    ap.add_argument("--no-auto-safety", action="store_true",
                    help="不按官方成绩自动映射 safety 分（仅 A/B 对照；正常出榜不要关）")
    ap.add_argument("--no-backfill", action="store_true",
                    help="不回填 trims 官方指导价到候选池（仅 A/B 对照用；正常出榜不要关）")
    ap.add_argument("--force-stale", action="store_true",
                    help="强制使用旧指纹候选池出榜（仅调试/回测用，报告会标注数据过期）")
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
    # 碰撞成绩回流（2026-11-21）：note 文本里写了 C-NCAP/C-IASI 成绩但 crash/safety_basis
    # 是空的 → 自动解析进字段。极氪7X 曾因这一条进不了一档（家族 6/6、全情景 98%）。
    _c_filled, _c_unres = FS.backfill_crash_from_text(cands)
    if _c_filled:
        print(f"[碰撞回流] 文本成绩 → crash/safety_basis：{len(_c_filled)} 款（"
              + "、".join(f"{m}({d})" for m, d, _ in _c_filled[:6])
              + ("…" if len(_c_filled) > 6 else "") + "）")

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

    # 举证责任封顶（2026-11-21）：无官方碰撞成绩的车 safety 不得超过有实锤车最低分。
    # 用户质疑 MONA L05「零碰撞成绩却 8.6、比 C-NCAP 86.7% 的 bZ5 还高」。
    if getattr(a, "no_evidence_cap", False):
        _cap_adj = []
    else:
        _cap_adj = SFC.evidence_cap(cands)
    if _cap_adj:
        print(f"[举证封顶] {len(_cap_adj)} 款无实锤车 safety 封顶至 "
              f"{_cap_adj[0]['capped']:g}（锚点 {_cap_adj[0]['anchor']}）")

    # 体验维度举证封顶（2026-11-21）：智驾/车机/操控这三类纸面看不出来，
    # 无实测记录的车不得声称高于有实测的车。空间不封顶（轴距/车长是硬数据）。
    from modules.engine import experience_score as ES
    _dim_cap = ES.cap_unproven(cands)
    if _dim_cap:
        _by_model = {}
        for x in _dim_cap:
            _by_model.setdefault(x["model"], []).append(x["dim"])
        print(f"[体验封顶] {len(_by_model)} 款无实测，{"、".join(f"{m}({'/'.join(d)})" for m, d in list(_by_model.items())[:5])}"
              f" 封顶至有实测车最低分")

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
    # 这是对"Model 3 漏收"事故的根因修复（此前只靠人记得盘点，没接进出榜链路）。
    from modules.engine import trim_coverage as TC
    _univ = a.universe or str(paths.cat(f"universe_{a.category}.json"))
    if os.path.exists(_univ) and not a.skip_universe_gate:
        _ur = TC.check_universe(a.candidates, _univ, data.get("pool_excluded", []))
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

    _pool_data = json.load(open(a.candidates, encoding="utf-8"))
    parts = [f"# {cfg.get('label')} · 出榜报告", ""]
    parts.append(f"> 来源：`{os.path.basename(a.candidates)}`；"
                 f"链路 取数→闸门→打分→稳健性→三档→审查。**全程无辩论。**")
    # v9.7.36：数据版本可追溯——读者知道分数是哪个 skill 版本/指纹生成的
    _gb = _pool_data.get("_generated_by") if isinstance(_pool_data, dict) else None
    if _dim_cap:
        _bm = {}
        for x in _dim_cap:
            _bm.setdefault(x["model"], set()).add(x["dim"])
        parts.append(f"> **体验维度举证封顶**：{len(_bm)} 款无独立实测/车主样本，"
                     f"智驾/车机/操控按「不超过有实测车最低分」封顶（空间不封顶，轴距车长为硬数据）。"
                     f"涉及：{'、'.join(list(_bm)[:6])}{'…' if len(_bm) > 6 else ''}。"
                     "纸面配置推断不能替代实车表现。")
    if _cap_adj:
        parts.append(f"> **安全分举证封顶**：{len(_cap_adj)} 款无官方碰撞成绩，safety 统一封顶至 "
                     f"{_cap_adj[0]['capped']:g}（不超过有实锤车最低分，锚点 {_cap_adj[0]['anchor']}）。"
                     "「没测过」不等于不安全，但不能被推断成「比测过的更安全」；"
                     "该维度不用于区分这些车，差异请看其他维度。")
    # 价格来源披露（2026-11-21）：回填了多少、哪些是真缺口，写在报告开头而不是藏在附录
    if _p_filled:
        parts.append(f"> **价格来源**：{len(_p_filled)} 款的指导价由 `trims.json` 官方配置页回填"
                     f"（候选池原本缺 `price_guide`）："
                     + "、".join(m for m, _ in _p_filled[:6])
                     + ("…" if len(_p_filled) > 6 else "") + "。")
    if _sa:
        _chg = [x for x in _sa if x["prev"] is not None and abs(float(x["prev"]) - x["score"]) > 0.05]
        parts.append(f"> **安全分来源**：{len(_sa)} 款按官方碰撞成绩自动映射"
                     f"（`C-NCAP pct → 7.0+(pct-80)×0.155` 封顶 9.3）"
                     + (f"，其中 {len(_chg)} 款较原插补值有调整：" +
                        "、".join(f"{x['model']} {x['prev']}→{x['score']:g}" for x in _chg[:5])
                        if _chg else "，与原插补值一致") + "。")
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
    parts.append(
        f"> **候选池与证据披露（自动生成）：**本池 {len(_all)} 款，"
        f"带核验来源 {_src_named}/{len(_all)}，有官方碰撞成绩 {_crash_named}/{len(_all)}，"
        f"证据等级 A/B/C = {_ev_cnt['A']}/{_ev_cnt['B']}/{_ev_cnt['C']}，"
        f"其中 {_ext_n} 款为网查补采（置信低）。"
        "入池口径：官方二级分类 + 纯电 + 指导价；未见来源/无碰撞成绩的车只作参数参考，"
        "不构成安全推荐。**没有官方碰撞成绩不记 0 分、只标缺失，降权≠安全。**")
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

    # 1 逐榜：分榜 → 闸门 → 三档 → 审查 → 稳健性 → 权重审计 → 三榜并列
    #    顺序是「先分榜再闸门」，因为四个榜的价格带上限不同（16 万 / 18 万），
    #    gates 必须按榜取，不能全局一刀切。
    summary = {
        "category": a.category, "source": os.path.basename(a.candidates),
        "in": len(cands), "passed": 0, "dropped": 0, "sliders": custom,
        "boards": {}, "tiers": {}, "review": {},
        # v9.7.23：未入池车型及原因（用户要求"能比就比，不能比说明原因"）
        "pool_excluded": (_pool_data.get("pool_excluded") if isinstance(_pool_data, dict) else []),
        # 2026-11-21：价格回流审计（回填了哪些、哪些 trims 也没价）
        "price_backfill": {"filled": [{"model": m, "price_guide": pg} for m, pg in _p_filled],
                           "still_missing": _p_still},
        "crash_backfill": {"filled": [{"model": m, "crash": d, "basis": b} for m, d, b in _c_filled],
                           "unresolved": _c_unres},
        "safety_auto": {"applied": _sa, "skipped": _ss},
        "safety_cap": _cap_adj,
        "dimension_cap": _dim_cap,
        "safety_table_sync": _st_sync,
        # v9.7.20：候选池披露同步给页面（评审反复指出报告有、页面看不到）
        "pool_disclosure": {
            "total": len(_all), "sourced": _src_named, "crash": _crash_named,
            "ev_a": _ev_cnt["A"], "ev_b": _ev_cnt["B"], "ev_c": _ev_cnt["C"],
            "extended": _ext_n,
        },
    }
    if a.quality:
        cands, _ = QS.apply(cands, cdata)
        parts += ["---", "", QS.report(cands), ""]
        summary["quality"] = QS.coverage(cands)
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
        _tier = L.tier_report(passed, cfg, wv, label, airbag_gate=a.airbag_gate, crash_gate=a.crash_gate)
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
        _tr = L.tier_report(passed, cfg, wv, label, airbag_gate=a.airbag_gate, crash_gate=a.crash_gate)
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
            bv_cfg_path = str(paths.cat("car_ev_1218_quality.json"))
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
                cands_for_html = [c for c in cands if c.get("model") in _passed] if _passed else cands
                data_v2 = BV.build(cands_for_html, cfg_v2, pol, facts_v2, tiers=_tiers_v2)
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
            print(f"[warn] HTML 渲染跳过（不影响榜单）：{type(ex).__name__}: {ex}", file=sys.stderr)


if __name__ == "__main__":
    main()
