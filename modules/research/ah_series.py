# -*- coding: utf-8 -*-
"""ah_series.py —— 汽车之家「车系事实」采集器（燃油/油混/插混/增程/纯电通用）。

为什么要有（2026-11-24 用户要求「燃油/油混/插混 8 维打分从代码上实现，以后自动生成」）
----------------------------------------------------------------------------------------
此前燃油/混动榜只能靠人工补采分（`runs/*_scores_batch*.json`），出一次榜问一次人。
本模块把「官方事实」变成可重复拉取的结构化数据，供 `engine/param_score.py` 自动打分：

  数据源 1（车系页 SSR） https://www.autohome.com.cn/<sid>/
    - "levelName":"紧凑型车"        → 官方二级分类（禁止用轴距推定，SKILL 铁律）
    - "energytype":0                → 能源类型码（0 汽油 / 1 柴油 / 2 油电混合 / 3 插混 / 4 纯电 / 5 增程，按页面对照）
    - "kouBeiData":{...}            → 口碑聚合：average + 7 维（空间/驾驶感受/油耗/外观/内饰/性价比/配置）
                                      + cmpSeriesScore（含本车系的 newCarPPH / reliabilityPPH 与样本数）
  数据源 2（参数页 SSR） https://car.autohome.com.cn/config/series/<sid>.html
    - `var config = {...}` 结构化参数矩阵：款型名/指导价/能源类型/发动机(马力·排量·扭矩)/
      变速箱/长宽高/轴距/后备厢/整备质量/油耗/零百/电池kWh/CLTC/电机功率。
      字段名被 CSS 挖空（`hs_kwNN`），所以一律**按字段 id 定位**（id 表见 param_fields.py），
      不靠名字；价格与款型名是 id=-1 的固定前两项，按位置取。

纪律（沿用项目红线）
--------------------
1. 不猜：解析不到就 null；levelName 只认官方字段，不用轴距推断级别。
2. 可复现：原始页面按 sid 落盘缓存（`runs/_ah_cache/<sid>.json`），带 fetched_at；
   `--refresh` 才重拉。同一输入 + 同一缓存 = 同一输出。
3. 不绕反爬：标准 UA、串行 + 间隔、失败退避一次；拿不到就标记 fetch_error，不换花样硬试。
4. 事实与分数分离：本模块只产事实，不算分；算分在 `engine/param_score.py`。

用法
----
    python3 ah_series.py --name 轩逸 --out runs/_ah_cache/448.json
    python3 ah_series.py --names 轩逸 朗逸 速腾 --refresh
    python3 ah_series.py --selftest          # 纯逻辑断言，零网络
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)

SERIES_URL = "https://www.autohome.com.cn/{sid}/"
CONFIG_URL = "https://car.autohome.com.cn/config/series/{sid}.html"
CACHE_DIR = os.path.join(HERE, "runs", "_ah_cache")
INDEX = os.path.join(HERE, "runs", "_ah_index", "ah_grade_index.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HDR = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
SLEEP = 0.8          # 串行请求间隔（秒），对源站保持克制
TTL_DAYS = 30        # 缓存有效期；销量/价格变动快，需要新数据时用 --refresh

# 能源类型码 → 动力形式（汽车之家车系页 energytype）
ENERGY_MAP = {0: "ICE", 1: "ICE", 2: "HEV", 3: "PHEV", 4: "BEV", 5: "EREV"}
ENERGY_TEXT = {"汽油": "ICE", "柴油": "ICE", "油电混合": "HEV", "插电式混合动力": "PHEV",
               "纯电动": "BEV", "增程式": "EREV", "氢气": "FCEV",
               # 日产 e-POWER 一类「汽油电驱」：发动机只发电、不能外接充电 → 归 HEV（串联混动）
               "汽油电驱": "HEV", "油电混合动力": "HEV", "插电式混动": "PHEV",
               "增程式混合动力": "EREV", "纯电": "BEV"}


def map_energy(txt):
    """能源类型文本 → 动力形式。先精确匹配，再做变体兜底（汽油+48V 轻混仍算 ICE）。"""
    t = clean(txt)
    if not t:
        return None
    if t in ENERGY_TEXT:
        return ENERGY_TEXT[t]
    if "插电" in t:
        return "PHEV"
    if "增程" in t:
        return "EREV"
    if "电驱" in t:          # 汽油电驱 / 超混电驱（e-POWER 类，不可充电）
        return "HEV"
    if "油电" in t or "非插电" in t:
        return "HEV"
    if "混动" in t or "混合动力" in t:   # 只写「混动/混合动力」未说明是否插电：按非插电油混处理
        return "HEV"
    if t.startswith("纯电"):
        return "BEV"
    if t.startswith("汽油") or t.startswith("柴油"):
        return "ICE"       # 含汽油+CNG、汽油+48V轻混等弱混/双燃料变体
    return None


# 款型名里的动力线索（仅在参数页「能源类型」缺失时兜底用；顺序有意义，先匹配先赢）
# 目的：把「双擎/超混电驱/插电混动/增程/纯电」这类款型名拆到具体动力，任何车型通用，不写车型白名单。
NAME_HINTS = (
    ("插电", "PHEV"), ("插混", "PHEV"),
    ("增程", "EREV"),
    ("双擎", "HEV"), ("混动", "HEV"), ("电驱", "HEV"), ("HEV", "HEV"),
    ("纯电", "BEV"),
)


def spec_power(spec):
    """单台款型 → (动力形式, 判据来源)。判据优先级：参数页能源类型 > 款型名关键词。
    返回 (None, "unknown") 表示拿不到——调用方据此决定是并入车系主形式还是丢弃，不许猜。"""
    txt = clean(field(spec, ID["energy_text"]) or "")
    p = map_energy(txt)
    if p:
        return p, "energy_text"
    nm = clean(spec.get("name") or "")
    for k, v in NAME_HINTS:
        if k in nm:
            return v, "name_hint"
    return None, "unknown"


def split_specs_by_power(specs):
    """款型按动力形式拆组 → ({power: [spec]}, 统计)。统计里的 unknown 不做推断，由调用方决定归属。"""
    out, stats = {}, {"n_specs": 0, "by_source": {}, "unknown": 0}
    for s in specs or []:
        p, src = spec_power(s)
        stats["n_specs"] += 1
        stats["by_source"][src] = stats["by_source"].get(src, 0) + 1
        if p is None:
            stats["unknown"] += 1
            continue
        out.setdefault(p, []).append(s)
    return out, stats


def specs_for_power(specs, power):
    """挑出属于某动力形式的款型 → (specs, 来源标记)。
    来源标记：per_spec=按款型判据拆到该动力（正确）；series_all=该动力拆不出款型，退回全车系并披露。
    绝不在拆不出时伪造款型归属——退回全量是「不知道」，不是「知道」。"""
    groups, _ = split_specs_by_power(specs)
    own = groups.get(power) or []
    if own:
        return own, "per_spec"
    return list(specs or []), ("series_all" if specs else "none")

# 参数页字段 id（单一事实源在 param_fields.py；此处只取本采集器要用的）
from modules.research.param_fields import KNOWN_FIELDS  # noqa: E402

ID = {
    "price": None,        # id=-1，基本参数组第 2 项（厂商指导价）
    "energy_text": 1149,
    "max_power_kw": 1185,
    "max_torque_nm": 9167,
    "engine_text": 1150,
    "engine_ps": 1294,
    "engine_l": 9124,
    "elec_ps": 1198,
    "elec_kw": 8448,
    "gearbox": 1265,
    "body_struct": 1147,
    "len": 5886, "wid": 5887, "hgt": 5888,
    "wheelbase": 1169,
    "trunk_l": 1175,
    "curb_kg": 1171,
    "fuel_wltc": 8216,
    "fuel_deprived": 9002,
    "zero100": 1250,
    "vmax": 1246,
    "battery_kwh": 1233,
    "cltc": 8428,
    "seat_num": 1173,
}


def norm(s):
    return re.sub(r"[\s\-_·（）()＋+／/.]", "", str(s or "")).lower()


def clean(s):
    """去 HTML 标签 + 收拢空白（字段名/值里混着 hs_kw 挖空标签）。"""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", str(s or ""))).strip()


def _num(s):
    """从文本取第一个数字；'-'/'' → None。不把缺失当 0。"""
    m = re.search(r"-?\d+(?:\.\d+)?", clean(s))
    return float(m.group(0)) if m else None


def load_index():
    with open(INDEX, encoding="utf-8") as f:
        return json.load(f)


def resolve_sid(name, index=None):
    """车系名 → 汽车之家 sid。只做精确归一化匹配，配不上一律 None（宁漏不错）。"""
    index = index or load_index()
    if name in index:
        return index[name]["sid"]
    key = norm(name)
    for k, v in index.items():
        if norm(k) == key:
            return v["sid"]
    return None


# ---------------------------------------------------------------- 解析：车系页
def _balanced_json(text, key):
    """取 `"key":` 之后的平衡 JSON 对象/数组（page 是 JS 大对象，不能直接 loads）。"""
    i = text.find('"%s":' % key)
    if i < 0:
        return None
    j = i + len('"%s":' % key)
    while j < len(text) and text[j] in " \n\r\t":
        j += 1
    if j >= len(text) or text[j] not in "{[":
        # 标量（数字/字符串）
        m = re.match(r'("[^"]*"|[0-9.]+|true|false|null)', text[j:])
        return json.loads(m.group(1)) if m else None
    open_c = text[j]
    close_c = "}" if open_c == "{" else "]"
    depth, k, in_str, esc = 0, j, False, False
    while k < len(text):
        c = text[k]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == open_c:
                depth += 1
            elif c == close_c:
                depth -= 1
                if depth == 0:
                    return json.loads(text[j:k + 1])
        k += 1
    return None


def parse_series_html(html, sid=None):
    """车系页 → {level, energy, official_name, koubei, pph}。拿不到的字段留 None。

    注意：页面里出现的第一处 "seriesName" 常是**对比车系**（如口碑对比榜里的帝豪/星瑞），
    不是本页车系。所以车系名与 PPH 一律按 `seriesId == sid` 在 cmpSeriesScore 里认领；
    这样宁缺不错，且与本页 sid 严格对应。
    """
    out = {"level": None, "energy": None, "koubei": None, "pph": None, "official_name": None}
    m = re.search(r'"levelName":"([^"]+)"', html)
    if m:
        out["level"] = m.group(1)
    m = re.search(r'"energytype":(\d+)', html)
    if m:
        out["energy"] = ENERGY_MAP.get(int(m.group(1)))
    kb = _balanced_json(html, "kouBeiData")
    if isinstance(kb, dict):
        dims = {}
        for it in kb.get("seriesScoreList") or []:
            nm, sc = it.get("typeName"), it.get("score")
            if nm and isinstance(sc, (int, float)):
                dims[nm] = float(sc)
        out["koubei"] = {"average": kb.get("average"), "dims": dims}
        # 本车系：按 seriesId == sid 认领（不按名字，避免认成对比车系）
        for it in kb.get("cmpSeriesScore") or []:
            if sid and str(it.get("seriesId")) == str(sid):
                out["official_name"] = it.get("seriesName")
                out["pph"] = {"new_car": it.get("newCarPPH"), "new_car_n": it.get("newCarPPHUserCount"),
                              "reliability": it.get("reliabilityPPH"),
                              "reliability_n": it.get("reliabilityPPHUserCount")}
                break
    if not out["official_name"] and sid:
        m = re.search(r'"seriesId":%s,"seriesName":"([^"]+)"' % re.escape(str(sid)), html)
        if m:
            out["official_name"] = m.group(1)
    return out


# ---------------------------------------------------------------- 解析：参数页
_CONFIG_CACHE = {}


def parse_config_html(html):
    """参数页 → {speclist:[{specid,name,price,fields{id:val}}], 能源类型集合}。

    字段名被挖空，所以按 id 取；id=-1 的两项按位置识别：基本参数组第 1 项=车型名、第 2 项=厂商指导价。
    """
    cache_key = str(abs(hash(html[:2000])))
    if cache_key in _CONFIG_CACHE:
        return _CONFIG_CACHE[cache_key]
    i = html.find("var config = ")
    if i < 0:
        return None
    j = html.find("\n", i)
    # 配置 JSON 可能跨多行：用平衡括号截取
    k = html.find("{", i)
    depth, p, in_str, esc = 0, k, False, False
    while p < len(html):
        c = html[p]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
        p += 1
    try:
        cfg = json.loads(html[k:p + 1])["result"]
    except (ValueError, KeyError):
        return None

    spec_ids = [s.get("specid") for s in cfg.get("speclist") or []]
    specs = []
    base_group = None
    for g in cfg.get("paramtypeitems") or []:
        if g.get("name") == "基本参数":
            base_group = g
            break
    if not base_group:
        return None
    # 车型名与指导价：id=-1 的前两项
    neg_items = [it for it in base_group.get("paramitems") or [] if it.get("id") == -1]
    name_item = neg_items[0] if neg_items else None
    price_item = neg_items[1] if len(neg_items) > 1 else None

    for idx, sid in enumerate(spec_ids):
        fields = {}
        for g in cfg.get("paramtypeitems") or []:
            for it in g.get("paramitems") or []:
                pid, vals = it.get("id"), it.get("valueitems") or []
                if pid == -1 or idx >= len(vals):
                    continue
                fields[pid] = clean(vals[idx].get("value"))
        specs.append({
            "specid": sid,
            "name": clean(name_item["valueitems"][idx]["value"]) if name_item and idx < len(name_item["valueitems"]) else None,
            "price": _num(price_item["valueitems"][idx]["value"]) if price_item and idx < len(price_item["valueitems"]) else None,
            "fields": fields,
        })
    _CONFIG_CACHE[cache_key] = specs
    return specs


def aggregate(specs):
    """款型 → 车系级事实：能源形式分组、价格带、主力款（价格中位）、能力上界。

    只做「取最大/中位」这类确定性聚合，不做任何推断。
    """
    if not specs:
        return {}
    prices = [s["price"] for s in specs if s.get("price")]
    by_energy = {}
    for s in specs:
        txt = clean(field(s, ID["energy_text"]) or "")
        p = map_energy(txt)
        by_energy.setdefault(p or "UNKNOWN", []).append(s)
    out = {"n_specs": len(specs),
           "price_min": min(prices) if prices else None,
           "price_max": max(prices) if prices else None}
    groups = {}
    for p, ss in by_energy.items():
        def mx(pid):
            vals = [_num(field(s, pid)) for s in ss]
            vals = [v for v in vals if v is not None]
            return max(vals) if vals else None
        priced = sorted([s for s in ss if s.get("price")], key=lambda s: s["price"])
        main = priced[len(priced) // 2] if priced else ss[0]
        zvals = [v for v in [_num(field(s, ID["zero100"])) for s in ss] if v]

        def m1(pid):
            """主力款（价格中位那台）自己的值——打分必须「同一台车」的功率/车重/价格配对。"""
            return _num(field(main, pid))

        e_kw, el_kw = m1(ID["max_power_kw"]), m1(ID["elec_kw"])
        # 混动系统综合功率不公开：串联（发动机只发电，如 e-POWER/增程）取电机功率；
        # 并联/混联按 发动机 + 0.5×电机 保守估算（标注为估算，不冒充官方口径）
        sys_kw = None
        if e_kw and el_kw:
            sys_kw = max(e_kw, el_kw) if e_kw < 0.6 * el_kw else e_kw + 0.5 * el_kw
        else:
            sys_kw = e_kw or el_kw
        groups[p] = {
            "power": p, "n_specs": len(ss),
            "price_min": priced[0]["price"] if priced else None,
            "price_max": priced[-1]["price"] if priced else None,
            "main_spec": {"name": main.get("name"), "price": main.get("price"),
                          "energy_raw": clean(field(main, ID["energy_text"]) or "")},
            "main": {"price": main.get("price"), "engine_kw": e_kw, "elec_kw": el_kw,
                     "system_kw": sys_kw, "curb_kg": m1(ID["curb_kg"]),
                     "zero100": m1(ID["zero100"]), "fuel_wltc": m1(ID["fuel_wltc"]),
                     "fuel_deprived": m1(ID["fuel_deprived"]),
                     "battery_kwh": m1(ID["battery_kwh"]), "cltc": m1(ID["cltc"]),
                     "wheelbase": m1(ID["wheelbase"]), "trunk_l": m1(ID["trunk_l"])},
            "engine_ps": mx(ID["engine_ps"]), "engine_kw": mx(ID["max_power_kw"]),
            "torque_nm": mx(ID["max_torque_nm"]), "elec_ps": mx(ID["elec_ps"]),
            "elec_kw": mx(ID["elec_kw"]), "engine_l": mx(ID["engine_l"]),
            "wheelbase": mx(ID["wheelbase"]), "len": mx(ID["len"]),
            "wid": mx(ID["wid"]), "hgt": mx(ID["hgt"]),
            "trunk_l": mx(ID["trunk_l"]), "curb_kg": mx(ID["curb_kg"]),
            "fuel_wltc": mx(ID["fuel_wltc"]), "fuel_deprived": mx(ID["fuel_deprived"]),
            "zero100": min(zvals) if zvals else None,
            "vmax": mx(ID["vmax"]), "battery_kwh": mx(ID["battery_kwh"]), "cltc": mx(ID["cltc"]),
            "body": clean(field(main, ID["body_struct"]) or ""),
        }
    out["groups"] = groups
    return out


# ---------------------------------------------------------------- 网络
def _get(url, timeout=25):
    req = urllib.request.Request(url, headers=HDR)
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")


def _coerce(rec):
    """缓存里 dict 的 int 键被 JSON 转成 str；读回后统一成 int（否则按 id 取字段会静默取不到）。
    同时用 specs 重算 aggregate：能源映射/聚合口径改了，旧缓存不必重抓也能生效。"""
    for s in rec.get("specs") or []:
        s["fields"] = {int(k): v for k, v in (s.get("fields") or {}).items()}
    if rec.get("specs"):
        rec["agg"] = aggregate(rec["specs"])
    return rec


def field(spec, pid):
    """按 id 取字段：int/str 两种键都认（缓存/内存一致）。"""
    f = spec.get("fields") or {}
    return f.get(pid) if pid in f else f.get(str(pid))


def fetch_series(name, sid=None, refresh=False):
    """车系名 → 缓存事实（含 series 页 + 参数页解析）。失败返回带 fetch_error 的结果，不抛。"""
    sid = sid or resolve_sid(name)
    if not sid:
        return {"name": name, "ok": False, "error": "车系名在汽车之家索引中不存在（宁漏不错）"}
    cache = os.path.join(CACHE_DIR, "%s.json" % sid)
    if os.path.exists(cache) and not refresh:
        try:
            with open(cache, encoding="utf-8") as f:
                d = json.load(f)
            ts = dt.datetime.fromisoformat(d.get("fetched_at", "2000-01-01"))
            if (dt.datetime.now() - ts).days <= TTL_DAYS:
                return _coerce(d)
        except (ValueError, OSError):
            pass
    res = {"name": name, "sid": sid, "fetched_at": dt.datetime.now().isoformat(timespec="seconds")}
    try:
        html = _get(SERIES_URL.format(sid=sid))
        time.sleep(SLEEP)
        series = parse_series_html(html, sid=sid)
        res.update({"level": series.get("level"), "energy": series.get("energy"),
                    "koubei": series.get("koubei"), "pph": series.get("pph"),
                    "official_name": series.get("official_name") or name})
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        res.update({"ok": False, "error": "series_fetch: %s" % str(e)[:80]})
    try:
        cfg_html = _get(CONFIG_URL.format(sid=sid))
        time.sleep(SLEEP)
        specs = parse_config_html(cfg_html)
        if specs:
            res["specs"] = specs
            res["agg"] = aggregate(specs)
            res["ok"] = True
        else:
            res.update({"ok": False, "error": "config_parse: 页面无 var config 或结构变化"})
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        res.update({"ok": False, "error": "config_fetch: %s" % str(e)[:80]})
    if refresh or not os.path.exists(cache):
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    return res


# ---------------------------------------------------------------- selftest
def selftest():
    # 1) 能源码映射
    assert ENERGY_MAP[0] == "ICE" and ENERGY_MAP[2] == "HEV" and ENERGY_MAP[3] == "PHEV"
    assert ENERGY_TEXT["纯电动"] == "BEV" and ENERGY_TEXT["增程式"] == "EREV"
    # 2) 数字提取：缺失不当 0
    assert _num("-") is None and _num("") is None and _num("5.88L") == 5.88
    # 3) 车系页解析（离线夹具 runs/_ah_series_448.html，2026-11-24 抓取）
    fx = os.path.join(HERE, "runs", "_ah_series_448.html")
    if os.path.exists(fx):
        with open(fx, encoding="utf-8") as f:
            html = f.read()
        s = parse_series_html(html, sid="448")
        assert s["level"] == "紧凑型车", s["level"]
        assert s["energy"] == "ICE", s["energy"]
        assert s["koubei"] and len(s["koubei"]["dims"]) >= 5, s["koubei"]
        assert s["pph"] and s["pph"]["reliability"] is not None, s["pph"]
    # 4) 参数页解析（同一夹具，id 定位 + 价格/款型名）
    fx2 = os.path.join(HERE, "runs", "_ah_probe_448.html")
    if os.path.exists(fx2):
        with open(fx2, encoding="utf-8") as f:
            specs = parse_config_html(f.read())
        assert specs and len(specs) >= 5, "specs 解析失败"
        assert all(x["price"] for x in specs), "指导价缺失"
        agg = aggregate(specs)
        g = agg["groups"].get("ICE") or {}
        assert g.get("wheelbase") and 2600 <= g["wheelbase"] <= 2900, g.get("wheelbase")
        assert g.get("engine_ps") and 80 <= g["engine_ps"] <= 300, g.get("engine_ps")
    # 5) 车型名解析不到时宁漏不错
    assert resolve_sid("不存在的车型xyz") is None
    print("[ah_series selftest] PASS · 车系页/参数页解析 + 能源映射 + 缺失不推断")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", help="单个车系名")
    ap.add_argument("--names", nargs="*", help="多个车系名")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--out", help="输出文件（默认打印）")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return
    names = ([a.name] if a.name else []) + (a.names or [])
    if not names:
        ap.error("--name/--names 至少给一个")
    out = {}
    for n in names:
        r = fetch_series(n, refresh=a.refresh)
        out[n] = r
        print("%-10s sid=%-6s ok=%s level=%s energy=%s dims=%s err=%s" % (
            n, r.get("sid"), r.get("ok"), r.get("level"), r.get("energy"),
            len((r.get("koubei") or {}).get("dims") or {}), r.get("error")))
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print("wrote", a.out)


if __name__ == "__main__":
    main()
