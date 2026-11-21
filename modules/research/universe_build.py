#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
universe_build.py —— 从联网销量榜独立生成"同级候选权威清单(universe)"。

⛔ 核心原则（2026-11-20 用户强制）：universe 必须来自联网数据源，绝不从候选池生成！
   错误做法（已废弃）：universe = 候选池车型 → 再用它检查候选池 → 循环论证，门禁形同虚设。
   正确做法：discover.py 联网拉榜 → 级别闸门(官方二级分类) → universe → 与候选池比对。

输入：discover.py --json 输出的联网榜（含 outside[] 池外车 + 全部在售车）
输出：categories/universe_<category>.json（联网权威清单，独立于候选池）

级别判定（只认官方二级分类，禁止轴距/尺寸推定）：
  1. LEVELS_OVERRIDE：已联网核验过的级别(含别名)，用于 auto_levels 查不到的车
  2. levels.json：auto_levels.py 联网查的官方分类缓存
  3. 两者都没有 → 标"级别待核"，不进 universe（但会单独列出提醒联网核验）
"""
import json, os, sys, argparse, datetime

import paths

HERE = str(paths.ROOT)
# 已联网核验的级别覆盖（auto_levels 用英文名查不到的车；来源见 note）
# 只记"官方二级分类=中型车 + 纯电主销"的确证，用于本榜(universe_car_midsedan_allprice)
LEVELS_OVERRIDE = {
    # 名 → (官方级别, 来源)
    "Model 3": ("中型车", "特斯拉官网+易车参数页 2026-11-20"),
    "model3": ("中型车", "特斯拉官网+易车参数页 2026-11-20"),
    "特斯拉Model 3": ("中型车", "特斯拉官网+易车参数页 2026-11-20"),
    "ZEEKR 007": ("中型车", "易车参数页 zeekrcs1e + 汽车之家 2026-11-20"),
    "极氪007": ("中型车", "易车参数页 zeekrcs1e + 汽车之家 2026-11-20"),
    "极氪007GT": ("中型车", "易车参数页 zeekrcs1e 2026-11-20"),
    "ZEEKR 007GT": ("中型车", "易车参数页 zeekrcs1e 2026-11-20"),
    "小米SU7": ("中大型车", "auto_levels.py via opencli 2026-11-20"),
    "星愿": ("小型车", "auto_levels.py via opencli 2026-11-20"),
    # 候选池已核验级别车的联网榜别名（销量榜用 EV/空格，候选池用 纯电/无空格）
    "MG 07 EV": ("中型车", "候选池已核验 MG 07 纯电=中型车 + 联网榜别名"),
    "MG07EV": ("中型车", "候选池已核验 MG 07 纯电=中型车 + 联网榜别名"),
    "极氪007 GT": ("中型车", "易车参数页 007GT=中型车"),
    "阿维塔 06": ("中型车", "候选池已核验 阿维塔06=中型车 + 联网榜空格别名"),
    "阿维塔 06T": ("中型车", "候选池已核验 阿维塔06T=中型车 + 联网榜空格别名"),
    "银河A7": ("中型车", "候选池已核验 银河A7 EV=中型车 + 联网榜别名"),
    "比亚迪e7": ("中型车", "车主之家10月榜+级别核验 2026-11-20"),
}


def norm(s):
    """名称归一化：去空格、小写、去动力后缀(EV/纯电/PHEV/DM-i等)，让销量榜别名能匹配级别库。
    注意：只用于级别匹配，不用于最终清单显示（显示用原名）。"""
    n = "".join(c for c in str(s).lower() if c.isalnum())
    for suf in ("ev", "pureev", "bev", "phev", "dmi", "evphev", "chun_dian"):
        if n.endswith(suf) and len(n) > len(suf) + 2:
            n = n[:-len(suf)]
            break
    # 纯电 → 空（中文后缀）；保留主体
    for zh in ("纯电",):
        if n.endswith(zh) and len(n) > len(zh) + 2:
            n = n[:-len(zh)]
            break
    # 品牌前缀归一化（与 trim_coverage._uname 保持一致）
    for brand in ("特斯拉", "tesla", "zeekr", "极氪", "byd", "比亚迪", "上汽mg", "mg", "蔚来", "nio"):
        if n.startswith(brand) and len(n) > len(brand) + 2:
            n = n[len(brand):]
            break
    return n


def load_levels():
    p = os.path.join(HERE, "levels.json")
    if not os.path.exists(p):
        return {}
    return json.load(open(p, encoding="utf-8")).get("levels", {})


def load_level_hints(hint_path):
    """从候选池读取"已联网核验的级别字段"作为级别判定兜底（仅属性，不提供成员）。
    ⚠ 注意：这里只读 cls 属性；universe 的成员(车型名)来自联网榜，绝不从候选池取成员——
    否则就是循环论证。已核验的 cls 是之前联网核实的沉淀，复用它是合理的(稳定字段)。"""
    if not hint_path or not os.path.exists(hint_path):
        return {}
    d = json.load(open(hint_path, encoding="utf-8"))
    cands = d.get("candidates", d) if isinstance(d, dict) else d
    return {norm(c["model"]): c.get("cls") for c in cands if c.get("model") and c.get("cls")}


def resolve_level(name, levels, hints=None):
    """返回 (cls, source) 或 (None, None)。优先 OVERRIDE(含归一化)，再 levels.json，最后候选池已核验cls(兜底)。"""
    for k, (cls, src) in LEVELS_OVERRIDE.items():
        if norm(k) == norm(name):
            return cls, src
    for k, v in levels.items():
        if norm(k) == norm(name) and v.get("cls"):
            return v["cls"], v.get("note", "levels.json")
    if hints and norm(name) in hints:
        return hints[norm(name)], "候选池已核验cls（仅属性兜底，成员来自联网榜）"
    return None, None


def build(discover_json, target_cls, power_filter, out_path, level_hint_path=None):
    """从联网榜独立生成 universe。
    discover_json: discover.py --json 的输出（含 outside[] 与可选 all[]）
    target_cls: 目标官方级别，如"中型车"
    power_filter: 动力过滤（"纯电"/None）
    """
    d = json.load(open(discover_json, encoding="utf-8"))
    # 联网榜车型：outside[] 是 discover 判定池外的；若有 all[]/rows[] 则合并（全量）
    rows = []
    for key in ("all", "rows", "outside", "ambiguous"):
        rows += d.get(key, []) or []
    # 去重（按归一化名）
    seen = {}
    for r in rows:
        n = norm(r.get("name"))
        if n and n not in seen:
            seen[n] = r

    levels = load_levels()
    hints = load_level_hints(level_hint_path)
    included, excluded, unknown = {}, [], []
    for n, r in seen.items():
        name = r["name"]
        cls, src = resolve_level(name, levels, hints)
        if not cls:
            unknown.append({"name": name, "sales": r.get("sales"), "reason": "官方级别待联网核验"})
            continue
        power = r.get("power", "")
        rec = {"name": name, "cls": cls, "power": power,
               "sales": r.get("sales"), "level_source": src}
        if cls == target_cls and (not power_filter or power_filter in power or "纯电" in power):
            included[name] = rec
        else:
            excluded.append(rec)

    univ = {
        "_meta": {
            "as_of": datetime.date.today().isoformat(),
            "generated_by": "universe_build.py（联网榜独立生成，不读候选池）",
            "source": f"discover.py --json 联网榜 {d.get('month','?')} + 官方二级分类(levels.json/LEVELS_OVERRIDE)",
            "scope": f"官方级别={target_cls} 且 动力含 {power_filter or '全部'} 的在售车型（联网权威清单）",
            "target_cls": target_cls,
            "rule": "三条路：①入候选池 ②写进pool_excluded附事实原因 ③两者皆无=漏收→门禁拦截",
            "note": "本清单由联网数据源独立生成，禁止从候选池复制生成（那会造成循环论证=门禁失效）。",
        },
        "models": sorted(included.keys()),
        "details": [included[k] for k in sorted(included)],
        "excluded_by_gate": excluded,
        "level_unknown": unknown,
    }
    json.dump(univ, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[universe] 联网榜共 {len(seen)} 款 → 目标级别 {target_cls} {len(included)} 款 / "
          f"级别不符排除 {len(excluded)} 款 / 级别待核 {len(unknown)} 款")
    print(f"[universe] 写入 {out_path}")
    if unknown:
        print(f"[universe] ⚠ 级别待核 {len(unknown)} 款（未纳入，需联网核验）："
              + "、".join(x["name"] for x in unknown[:15]))
    return univ


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="从联网榜独立生成候选池完整性权威清单")
    ap.add_argument("--discover", required=True, help="discover.py --json 输出的联网榜JSON")
    ap.add_argument("--cls", default="中型车", help="目标官方二级分类")
    ap.add_argument("--power", default="纯电", help="动力过滤（纯电/None）")
    ap.add_argument("--out", required=True, help="输出 universe JSON")
    ap.add_argument("--level-hint", help="候选池路径：仅取已核验cls作级别属性兜底（成员仍来自联网榜）")
    a = ap.parse_args()
    build(a.discover, a.cls, a.power, a.out, a.level_hint)
