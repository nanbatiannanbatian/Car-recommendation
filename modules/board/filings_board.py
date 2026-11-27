#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
filings_board.py —— 申报车型列表（待上市）参考评价，附带在榜单 HTML/MD 之后。

用途（2026-11-20 用户要求）：
  「家越07纯电快上市了，如果用户问中型纯电SUV，把这款车放入申报表，
    家越在上汽旗下，可以参考MG07的产品力给出评价，是否可以让用户等等。」

设计：
  - 只展示与本次榜单【同级别/同车身/同能源】的申报车（问中型纯电SUV才显示家越07纯电版）。
  - 参考评价：用 ref_car 的8维产品力分做基准，按已知差异调整，给预估分区间。
  - 是否值得等：对比主榜同级车的分数，三档结论 + 依据 + 不确定性声明。
  - 申报车不进主榜（无实际销量/官方价），明确标注"预估·非官方核验"。

数据源：categories/filings_notes.json（申报库，含 ref_car/ref_relation）+ master_pool.json（参考车分）
"""
import json, os, re, sys, datetime

import paths

HERE = str(paths.ROOT)
FILINGS = str(paths.cat("filings_notes.json"))
POOL = str(paths.cat("master_pool.json"))

# 数据时效门禁（2026-11-20 用户强制：先联网找全量，本地表只作缓存）
# 申报/待上市状态变化快，本地缓存超过该天数必须提示先联网刷新，不许静默当"截止今天"的事实。
STALE_DAYS = 3
_FRESHNESS = {"updated": None, "days": None, "stale": True, "label": "未检查"}


def freshness_state(filings_path=None, verified_as_of=None):
    """检查申报库新鲜度。verified_as_of=联网核实到当天(YYYY-MM-DD)时以该日期为准。
    返回 dict：{updated, days, stale, label}。"""
    p = filings_path or FILINGS
    try:
        doc = json.load(open(p, encoding="utf-8"))
        updated = doc.get("updated")
    except Exception:
        updated = None
    # 若本次已联网核实到某天，视同该天刷新（本地表即使旧也不告警）
    ref = verified_as_of or updated
    today = datetime.date.today()
    days = None
    if ref:
        try:
            days = (today - datetime.date.fromisoformat(str(ref)[:10])).days
        except Exception:
            days = None
    stale = (days is None) or (days > STALE_DAYS)
    label = ("联网核实至 " + str(verified_as_of)) if verified_as_of else (str(updated) or "未知")
    return {"updated": updated, "days": days, "stale": stale, "label": label}


def freshness_warning_md(st=None):
    st = st or _FRESHNESS
    if not st["stale"]:
        return ""
    return (f"\n> ⚠️ **数据时效告警**：申报库更新于 {st['label']}"
            f"（距今 {st['days'] if st['days'] is not None else '未知'} 天，阈值 {STALE_DAYS} 天）。"
            "本表为**本地缓存**，上市/预售/价格状态可能已过时——按出榜铁律应先联网找全量最新信息，"
            "再以本地表交叉核验。此处展示仅为缓存快照，**不可直接当作「截止今天」的事实**。\n")


def freshness_warning_html(st=None):
    st = st or _FRESHNESS
    if not st["stale"]:
        return ""
    return (f'<div class="note" style="border-left-color:#b42318;background:#fbe9e7;color:#8a1c13">'
            f'⚠️ 数据时效告警：申报库更新于 {st["label"]}'
            f'（距今 {st["days"] if st["days"] is not None else "未知"} 天，阈值 {STALE_DAYS} 天）。'
            f'本表为<b>本地缓存</b>，上市/预售/价格状态可能已过时——按出榜铁律应先联网找全量最新信息再交叉核验。'
            f'此处仅为缓存快照，<b>不可直接当作「截止今天」的事实</b>。</div>')

W = {"powertrain":0.18,"safety":0.162,"adas":0.135,"space":0.126,
     "value":0.117,"quality":0.10,"control":0.09,"cabin":0.09}
DIM_LABEL = {"powertrain":"动力总成","safety":"安全","adas":"智驾","space":"空间",
             "value":"性价比","quality":"质量品控","control":"操控","cabin":"车机座舱"}


def norm(s):
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


def infer_power(name, note):
    """从车名/tech 文本推断能源（申报库无 power 字段）。

    v10.75 修复：原版只认「纯电/BEV」字面词，若申报条目写「宁德时代电池+5C超充」
    而不写「纯电」会被推断为 None，筛选逻辑「推断不到时保留」会把纯电车混进
    纯燃油/油混榜的申报区（实例：αS5=极狐阿尔法S5 纯电轿车混入纯油榜）。
    补电池/补能/电机特征词识别；HEV/PHEV/EREV 关键词优先，避免「48V轻混+电池」
    被误判为纯电。
    """
    t = (name or "") + " " + (note or "")
    tu = t.upper()
    # 混动/增程优先（「48V轻混+电池」「插混+电池」不能被判成纯电）
    if "增程" in t or "EREV" in tu: return "EREV"
    if "插混" in t or "插电" in t or "PHEV" in tu: return "PHEV"
    if "油混" in t or "HEV" in tu or "双擎" in t or "轻混" in t: return "HEV"
    if "纯电" in t or "BEV" in tu: return "BEV"
    # 无「纯电」字面但含纯电强特征（电池供应商/超充/续航里程/驱动电机/kWh）
    bev_marks = ("超充", "快充", "宁德时代电池", "磷酸铁锂", "三元锂", "kwh",
                 "驱动电机", "单电机", "双电机", "电机总功率", "前备厢", "cltc")
    if any(m.lower() in t.lower() for m in bev_marks):
        if "续航" in t or "km" in t.lower() or "电池" in t or "电机" in t:
            return "BEV"
    return None


def load_filings(verified_as_of=None, strict_fresh=False):
    """加载申报库。

    verified_as_of: 若本次已联网核实到某天(YYYY-MM-DD)，传入后本地缓存旧状态不再告警。
    strict_fresh: 为 True 且缓存过期时打印告警到 stderr（出榜链路默认应打开）。
    注意：本函数只读本地缓存做交叉核验；按用户铁律，时效问题必须先联网找全量再调用本函数。
    """
    global _FRESHNESS
    _FRESHNESS = freshness_state(verified_as_of=verified_as_of)
    if _FRESHNESS["stale"]:
        msg = (f"[FILINGS-CACHE-STALE] 申报库缓存更新于 {_FRESHNESS['label']}，"
               f"距今 {_FRESHNESS['days']} 天>{STALE_DAYS} 天；请先联网找全量最新状态再用本地表核验。")
        if strict_fresh:
            print(msg, file=sys.stderr)
    d = json.load(open(FILINGS, encoding="utf-8"))["models"]
    out = []
    for name, v in d.items():
        pw = v.get("power") or infer_power(name, v.get("tech", "") + v.get("launch", ""))
        out.append({"model":name, "power":pw, "cls":v.get("cls"), "body":v.get("body"),
                    "price_expect":v.get("price_expect"), "launch":v.get("launch"),
                    "tech":v.get("tech"), "ref_car":v.get("ref_car"),
                    "ref_relation":v.get("ref_relation"), "src":v.get("src"),
                    "conf":v.get("conf"), "note":v.get("note")})
    return out


def ref_scores(ref_car):
    if not ref_car: return None
    pool = json.load(open(POOL, encoding="utf-8"))["cars"]
    c = next((x for x in pool if norm(x["model"]) == norm(ref_car)), None)
    return c["scores"] if c and c.get("scores") else None


def estimate_filing(f, ref_scores_dict):
    """用参考车8维分做基准，按已知差异调整，返回预估分与逐维说明。"""
    if not ref_scores_dict:
        return None
    est = dict(ref_scores_dict)
    reasons = {}
    # SUV 相对轿车空间+0.3；方盒子/大车按车身微调
    if f.get("body") == "SUV":
        est["space"] = round(min(10, est.get("space", 7) + 0.3), 1)
        reasons["space"] = "SUV 车身，空间较参考轿车 +0.3"
    # 新车上市初期品控未知，质量维向中性靠拢（不凭空给高分）
    q = est.get("quality", 7.0)
    est["quality"] = round((q + 7.5) / 2, 1)
    reasons["quality"] = "新车品控未经市场验证，向中性 7.5 收敛（不确定）"
    # 参考车性价比若因价格预期变化，按价格差微调（同价位不调）
    product = round(sum(est.get(k, 7) * W[k] for k in W) * 10, 1)
    return {"scores":est, "product":product, "range":[round(product-1.5,1), round(product+1.0,1)],
            "reasons":reasons, "confidence":"参考车推导·非官方核验"}


def verdict(est_range, main_scores):
    """三档结论：对比主榜同级车分数。"""
    if not est_range or not main_scores:
        return ("信息不足", "无参考车分或主榜同级车数据，不下结论")
    mid = sum(est_range) / 2
    top3 = sorted(main_scores, reverse=True)[:3]
    top3_min = min(top3) if top3 else max(main_scores)
    median = sorted(main_scores)[len(main_scores)//2]
    if mid >= top3_min - 0.5:
        return ("值得等", f"预估中值 {mid:.1f} 接近/超过主榜前三门槛({top3_min:.1f})；但新车未上市，交付与品控存疑")
    if mid >= median - 1.0:
        return ("可等可不急", f"预估中值 {mid:.1f} 与主榜中游({median:.1f})接近；若不急可等实测价与交付")
    return ("不建议等", f"预估中值 {mid:.1f} 低于主榜中游({median:.1f})；除非价格明显下探，否则现款更稳")


def related_filings(category_cls=None, category_body=None, category_power=None, all_filings=None):
    """过滤出与本次榜单同级别/同车身/同能源的申报车。"""
    fs = all_filings or load_filings()
    out = []
    for f in fs:
        # 级别：严格一致（中型轿车=中型车+轿车）
        if category_cls and f.get("cls") and f["cls"] != category_cls: continue
        if category_body and f.get("body") and f["body"] != category_body: continue
        # 能源：严格一致；申报车 power 推断不到时保留（可能是目标能源）
        if category_power and f.get("power") and f["power"] != category_power: continue
        out.append(f)
    return out


def build_section_md(rel_filings, main_scores):
    if not rel_filings:
        return ""
    L = ["\n## 🚧 待上市申报车型（同级别参考，不进主榜）\n",
         "> 以下车型与本次榜单同级别，**尚未上市/无官方价与销量**，不进主榜。",
         "评价用同集团/同级参考车的产品力分推导，**属预估、非官方核验**，仅供「要不要等」判断。\n"]
    warn = freshness_warning_md()
    if warn:
        L.append(warn)
    L.append("| 车型 | 状态 | 预计价(万) | 参考车 | 预估产品力 | 是否值得等 |")
    L.append("|---|---|---|---|---:|---|")
    details = []
    for f in rel_filings:
        rs = ref_scores(f.get("ref_car"))
        est = estimate_filing(f, rs)
        if est:
            rng = f"{est['range'][0]:.1f}–{est['range'][1]:.1f}"
            vd, why = verdict(est["range"], main_scores)
        else:
            rng, vd, why = "—", "信息不足", "无参考车产品力分"
        L.append(f"| **{f['model']}** | {f.get('launch','')[:22]} | "
                 f"{f['price_expect'][0]}–{f['price_expect'][1]}" if f.get('price_expect') else
                 f"| **{f['model']}** | {f.get('launch','')[:22]} | —")
        # 上面拼接可能因三元表达式分支列数不一致，重建整行
        L[-1] = f"| **{f['model']}** | {f.get('launch','')[:22]} | " + \
                (f"{f['price_expect'][0]}–{f['price_expect'][1]}" if f.get('price_expect') else "—") + \
                f" | {f.get('ref_car') or '—'} | {rng} | **{vd}** |"
        details.append(f"### {f['model']} — {vd}\n")
        details.append(f"- **状态**：{f.get('launch')}｜置信度 {f.get('conf','—')}")
        details.append(f"- **已知参数**：{f.get('tech')}")
        if f.get("ref_relation"): details.append(f"- **参考关系**：{f['ref_relation']}")
        if est:
            details.append(f"- **预估产品力**：{est['product']:.1f}（区间 {est['range'][0]:.1f}–{est['range'][1]:.1f}），基准参考车 {f.get('ref_car')}")
            for k, why2 in est["reasons"].items():
                details.append(f"  - {DIM_LABEL[k]}：{why2}")
            details.append(f"- **值得等判断**：{why}")
        else:
            details.append(f"- **值得等判断**：{why}")
        details.append(f"- **来源**：{f.get('src')}｜**说明**：{f.get('note')}\n")
    L.extend(details)
    return "\n".join(L)


def build_section_html(rel_filings, main_scores):
    if not rel_filings: return ""
    rows = []
    for f in rel_filings:
        rs = ref_scores(f.get("ref_car"))
        est = estimate_filing(f, rs)
        rng = f"{est['range'][0]:.1f}–{est['range'][1]:.1f}" if est else "—"
        vd, why = verdict(est["range"], main_scores) if est else ("信息不足", "无参考车分")
        price = f"{f['price_expect'][0]}–{f['price_expect'][1]}" if f.get('price_expect') else "—"
        vcls = {"值得等":"ve","可等可不急":"vm","不建议等":"vn","信息不足":"vu"}.get(vd,"vu")
        detail = f"{f.get('tech') or ''}"
        if f.get("ref_relation"): detail += f"<br>参考关系：{f['ref_relation']}"
        detail += f"<br>判断依据：{why}"
        rows.append(f"<tr><td><b>{f['model']}</b></td><td>{f.get('launch','')[:26]}</td>"
                    f"<td>{price}</td><td>{f.get('ref_car') or '—'}</td><td>{rng}</td>"
                    f"<td><span class='verdict {vcls}'>{vd}</span></td>"
                    f"<td class='fdetail'><details><summary>展开</summary>{detail}"
                    f"<br>来源：{f.get('src')}</details></td></tr>")
    return f"""
<h2>🚧 待上市申报车型（同级别参考，不进主榜）</h2>
{freshness_warning_html()}
<div class="note" style="border-left-color:#b07d10;background:#fbf3e6;color:#7a5710">
以下车型与本次榜单同级别但<b>尚未上市/无官方价与销量</b>，不进主榜。评价用同集团/同级参考车的产品力分推导，
<b>属预估、非官方核验</b>，仅供「要不要等」判断。
</div>
<table><thead><tr><th>车型</th><th>状态</th><th>预计价(万)</th><th>参考车</th><th>预估产品力</th><th>是否值得等</th><th>依据</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
"""

FILINGS_CSS = """
.verdict{padding:2px 8px;border-radius:5px;font-size:11px;font-weight:600}
.verdict.ve{background:#e7f4ea;color:#1a7f37}.verdict.vm{background:#fbf3e6;color:#9a6700}
.verdict.vn{background:#fbe9e7;color:#b42318}.verdict.vu{background:#eee;color:#666}
.fdetail{font-size:11px;color:#5b6470;max-width:340px}
"""


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="申报车型列表参考评价")
    ap.add_argument("--cls", help="本次榜单级别(如 中型SUV)")
    ap.add_argument("--body", help="本次榜单车身(轿车/SUV)")
    ap.add_argument("--power", help="本次榜单能源(BEV/PHEV...)")
    ap.add_argument("--main-scores", nargs="*", type=float, help="主榜同级车综合分(用于值得等判断)")
    ap.add_argument("--verified-as-of", help="本次已联网核实到的日期(YYYY-MM-DD)，传入则缓存旧状态不告警")
    ap.add_argument("--freshness", action="store_true", help="只看申报库新鲜度门禁状态")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.freshness:
        print(json.dumps(freshness_state(verified_as_of=a.verified_as_of), ensure_ascii=False, indent=1))
    elif a.selftest:
        # 新鲜度门禁本身要可测：旧缓存→stale；联网核实到今天→not stale
        st_old = freshness_state(filings_path=FILINGS, verified_as_of=None)
        st_live = freshness_state(filings_path=FILINGS, verified_as_of=datetime.date.today().isoformat())
        assert st_live["stale"] is False, "联网核实到当天应消除告警"
        assert st_live["days"] == 0, "联网核实当天 days 应为 0"
        print("FRESHNESS GATE OK: verified_as_of=今天 -> stale=False; 本地缓存 days=", st_old["days"])
        # 能源推断回归：无「纯电」字面但含电池/超充特征的车必须判 BEV，
        # 否则会混进纯燃油榜申报区（αS5=极狐阿尔法S5 真实踩坑）
        assert infer_power("αS5", "192线激光雷达+5C超充；宁德时代电池") == "BEV", \
            "αS5 应判 BEV（电池/超充特征），不能漏判混入纯油榜"
        assert infer_power("某车", "48V轻混+电池") == "HEV", "轻混不得误判纯电"
        assert infer_power("某车", "插混+电池+续航") == "PHEV", "插混不得误判纯电"
        assert infer_power("汽油中型车", "2.0T 涡轮增压") is None, "纯燃油不得误判纯电"
        fs = load_filings(verified_as_of=a.verified_as_of)
        jy = next((f for f in fs if "家越07纯电" in f["model"]), None)
        assert jy and jy["power"] == "BEV", "家越07纯电版申报数据/能源缺失"
        a5 = next((f for f in fs if f["model"] == "αS5"), None)
        assert a5 and a5["power"] == "BEV", "αS5 能源推断失败（纯电轿车混入纯油榜）"
        assert jy["ref_car"] == "MG 07 纯电", "参考车关系丢失"
        rs = ref_scores(jy["ref_car"])
        assert rs and rs["value"] >= 9.0, "MG07参考分异常"
        est = estimate_filing(jy, rs)
        assert est and 75 <= est["product"] <= 90, f"预估分异常: {est}"
        print("SELFTEST OK: 家越07纯电版 → 参考MG07 预估", est["product"], "区间", est["range"])
        print(verdict(est["range"], [88,85,84,82,80]))
    else:
        rel = related_filings(a.cls, a.body, a.power)
        print(build_section_md(rel, a.main_scores or []))
