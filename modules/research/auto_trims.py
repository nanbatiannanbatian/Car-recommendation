#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐配置阶梯自动补录（auto_trims.py）——把 35 款待补车型的配置阶梯建起来。

数据源：汽车之家车系参数页 https://car.autohome.com.cn/config/series/<sid>.html
页面 SSR 里的 `var config = {...}` 是结构化配置矩阵，实测可稳定解析：
  - 款型名（如「N7 2027款 510 Air」）
  - 官方指导价（N7 解析出 11.99/12.99/13.99/14.99/15.69，与人工核验逐项一致）
  - 电池能量(kWh)、CLTC 纯电续航里程(km)、电池类型、电动机总功率(kW)
按 specid 对齐各列，不靠猜；解析不到的字段一律记 null，不推断、不编造。

与人工核验条目的区别（必须显式可查）：
  source_type = "official_config_page_auto"、auto_parsed = true、partial_fields 列明缺项。
  电芯供应商 / 补能曲线 / 上市时长 / 加价跃迁的背景说明不属于参数页字段，
  一律留 null 或空，不能像人工条目那样给「三腔远端气囊」这类叙述。

纪律：SCORING_ENABLED 仍为 False，本脚本只写 categories/trims.json 的事实条目，
不碰候选池、不碰分数、不碰闸门。名称匹配只用精确/受控变体，匹配不到就跳过。

用法：
    python3 auto_trims.py --candidates <候选池> --index <ah_grade_index.json> \
        --db categories/trims.json --out categories/trims.json
    python3 auto_trims.py --dry-run ...   # 只打印将补哪些，不写文件
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time

import requests

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)
# 只依赖数据契约（不 import engine）：写库前自检字段是否合规
from modules.contracts.trims_schema import validate as validate_trims

HDR = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
       "Referer": "https://car.autohome.com.cn/"}
CONFIG_URL = "https://car.autohome.com.cn/config/series/{}.html"
# 移动版参数页字段名**不混淆**：桌面版把「快充/慢充/高压平台」等关键词用
# <span class='hs_kwNN_configFG'></span> 挖空，清洗后退化成同名的「电池(小时)」，
# 快充与慢充无法区分（实测极氪7X/启源Q06 两版对比）。两版字段 id 一致，
# 所以：优先移动版取干净字段名，桌面版作兜底（靠 id 定位）。
CONFIG_URL_MOBILE = "https://car.autohome.com.cn/config/series/{}.html?mobile=1"
SLEEP = 0.12
MAX_TRIMS = 14
TODAY = dt.date.today().isoformat()

# 汽车之家参数页「电池/充电」组的关键字段 id（2026-11-21 实测极氪7X/启源Q06 确认，
# 跨车系稳定）。用 id 定位而不是字段名——桌面版字段名被 CSS 挖空后重名，
# 靠名字匹配必然张冠李戴。
AH_BAT_IDS = {
    "cell_supplier": 9015,   # 电芯品牌（很多车留空，不推断）
    "battery_tech": 9020,    # 电池特有技术（麒麟/刀片/金钟罩…）
    "consumption": 1290,     # 百公里耗电(kWh/100km)——官方电耗，一手数据
    "voltage": 9027,         # 高压平台（V）——「电池来源」列的 V 电压数据来源
    "fast_charge_h": 9028,   # 电池快充时间(小时)
    "charge_range": 9030,    # 电池快充区间(%)，如 10-80
    "charge_power_kw": 8459,  # 对外充电功率(kW)——超充能力；无快充时间的车靠它
    "swap": 8456,             # 换电——补能方式（蔚来/乐道等）
    "cooling": 8458,          # 电池冷却方式（液冷/直冷）——影响快充与热管理
}

# ── v10.12.1：第二批提取（车身组 + 基本参数组）────────────────────────────
# 起因（2026-11-22 用户追问「为什么没被提取」时的全字段盘点）：
#   parse_config 原来只认「电池/充电」组（in_bat_group 判断），
#   其余 5 个组的字段**解析进了内存却被整体丢弃**。实测极氪7X/启源Q06 两页：
#   「车身」组有轴距 2925mm、后备厢 905–1978L、前备箱 38L、风阻 0.247、车身结构；
#   「基本参数」组有官方零百 5.4s、整备质量 2280kg、上市 2025.10、最大功率 370kW。
#   这些正是空间维封顶、储物披露、操控规则、生命周期**已经在用**的概念——
#   规则接不上不是规则缺失，是数据没接进来。
# 字段名普遍被 CSS 混淆（「(mm)」「备厢(L)」「阻系数(Cd)」这类还算好认，
# 「(kg)」「(mm)」这类则完全认不出），所以一律**按 id + 组名定位**，不靠名字。
AH_BODY_IDS = {          # 「车身」组
    "wheelbase_mm": 1169,     # 轴距 —— 空间维封顶的首选代理（原覆盖率 0%）
    "length_mm": 5886,
    "width_mm": 5887,
    "height_mm": 5888,
    "trunk_l": 1175,          # 后备厢容积(L)，值形如 "905-1978"（常规-放倒）
    "frunk_l": 9009,          # 前备箱容积(L)
    "cd": 1370,               # 风阻系数
    "body_structure": 1147,   # 车身结构（5门5座SUV / SUV）
}
AH_BASE_IDS = {          # 「基本参数」组
    "zero_to_100_s": 1250,    # 官方 0-100km/h 加速(s) —— 操控规则用
    "max_power_kw": 1185,
    "max_torque_nm": 9167,
    "curb_weight_kg": 1171,   # 整备质量
    "max_loaded_kg": 8445,
    "launch_month": 8453,     # 上市时间（生命周期缺的那一项）
    "dims_mm": 1148,          # 长*宽*高(mm) 合并字段（车身组分列缺失时的兜底）
}

# 「底盘转向」「车轮制动」两组对**纯电车整组为空**（实测极氪7X/启源Q06：
# 有值数均为 0）——汽车之家给燃油车填「前/后悬架类型、驱动方式」，
# 纯电车不填。所以操控规则里的悬架/驱动**拿不到官方值**，
# 只能靠各车取证文本提取（score_audit._EXTRACT）。这条写进已知边界，不假装能采到。

# 官方产品名 → 电芯厂商。这是厂商自己的产品命名（刀片=弗迪、麒麟=宁德），
# 属可核验的官方字段映射，不是推断；映射结果在 note 里标明来源字段。
TECH_TO_SUPPLIER = {
    "麒麟": "宁德时代", "神行": "宁德时代", "短刀": "宁德时代", "卧龙": "宁德时代",
    "刀片": "弗迪", "金砖": "弗迪", "神盾": "弗迪",
    "金钟罩": "中创新航", "晓光": "中创新航",
    "蜂巢": "蜂巢能源", "无风": "蜂巢能源",
    "因湃": "弗迪", "海豚": "弗迪",
    "国轩": "国轩高科", "欣旺达": "欣旺达", "亿纬": "亿纬锂能",
    "太行": "宁德时代", "驿": "宁德时代",
}


def supplier_from_tech(tech: str) -> str:
    """电池特有技术 → 电芯厂商（官方产品名映射，命中多个取最长关键词）。"""
    t = str(tech or "")
    if not t:
        return ""
    hits = [(len(k), v) for k, v in TECH_TO_SUPPLIER.items() if k in t]
    if not hits:
        return ""
    return max(hits)[1]


def norm(s):
    return re.sub(r"[\s\-_·（）()＋+／/]", "", str(s or "")).lower()


def clean(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", str(s or ""))).strip()


# 显式别名表：候选池车名带品牌前缀，汽车之家车系索引不带。
# 只列一对一可核验的 6 条，绝不做模糊相似度匹配。
# 未列入的（秦MAX EV / 大众ID.ERA 5S / 大众ID.AURA T6 / 小鹏MONA L05）
# 在汽车之家车系库中查无此车系，或同名前缀指向另一款车（ID.AURA ≠ ID.AURA T6），
# 一律留待补，不硬配、不推断。
ALIASES = {
    "大众与众07": "与众07",
    "大众与众09": "与众09",
    "丰田铂智7": "铂智7",
    "埃安Ray7": "Ray7",
    "东风奕派M8": "奕派M8",
    "东风风行星海V6": "星海V6",
    # 2026-11-21 出纯电中型SUV榜时补：候选池带品牌前缀、汽车之家索引不带。
    # 每条都在 ah_grade_index.json 里核实过车系名存在（不是模糊猜的）。
    "比亚迪宋L EV": "宋L EV",
    "比亚迪海狮07EV": "海狮07 EV",
    "比亚迪海狮06 EV": "海狮06",
    "比亚迪海狮06EV": "海狮06",
    "特斯拉 Model Y": "Model Y",
    "阿维塔07 纯电版": "阿维塔07",
    "问界M5纯电版": "问界M5",
}


def load_db_merged(path):
    """读取 trims 库，并把随包的人工核验条目合并进来（manual 优先）。

    为什么：trims.json 是「可重建生成物」不打包，但里面混着人工核验条目
    （逐配置表来自媒体/官网交叉核对，汽车之家车系库查无，联网也抓不回来）。
    不合并的话，新装包重建 = 人工条目全丢（2026-11-21 用户指出的隐患）。
    现在 categories/trims_manual.json 随包，生成时作为底座叠加自动补录。
    """
    db = None
    if path and os.path.exists(path):
        db = json.load(open(path, encoding="utf-8"))
    if not db:
        db = {"scoring_enabled": False, "entries": [], "out_of_board_examples": []}
    mp = paths.CAT_DIR / "trims_manual.json"
    if mp.exists():
        m = json.load(open(mp, encoding="utf-8"))
        have = {e.get("model") for e in db.get("entries", [])}
        db["entries"] = [e for e in m.get("entries", []) if e.get("model") not in have] + db.get("entries", [])
        if not db.get("out_of_board_examples"):
            db["out_of_board_examples"] = m.get("out_of_board_examples", [])
    return db


def seed_from_manual(out_path):
    """只把人工条目落成基础 trims 库（不联网、不需候选池）。新装包 bootstrap 用。"""
    db = load_db_merged(None)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    json.dump(db, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return len(db.get("entries") or [])


def find_sid_local(model, index):
    nidx = {norm(k): (k, v) for k, v in index.items()}
    # 别名优先：候选池带品牌前缀，索引不带；走显式白名单，不做模糊匹配
    alias = {norm(k): v for k, v in ALIASES.items()}.get(norm(model))
    if alias and norm(alias) in nidx:
        hit = nidx[norm(alias)]
        return hit[1]["sid"], hit[0], f"alias:{alias}"
    hit = nidx.get(norm(model))
    if hit:
        return hit[1]["sid"], hit[0], "exact"
    # 动力后缀变体（与 build_ice_pool 同一套规则）
    base = model
    for suf in ("纯电", "EV", "ev", "PHEV", "DM", "增程"):
        if base.endswith(suf) and len(base) - len(suf) >= 2:
            base = base[: -len(suf)].strip()
            break
    for cand in (base, base + "EV", base + "新能源", base + "纯电"):
        hit = nidx.get(norm(cand))
        if hit:
            return hit[1]["sid"], hit[0], f"variant:{cand}"
    return None, None, None


def find_sid(model, index):
    """本地索引 → 远程检索 的两级寻址（多源调度的兼容入口）。"""
    sid, name, how = find_sid_local(model, index)
    if sid:
        return sid, name, how
    # 索引兜底（2026-11-21）：A-Z 在售索引会漏新车系，但车系页已存在。
    # 只在本地索引全部 miss 时才走远程检索，失败返回 None，不猜。
    return find_sid_remote(model, index)


def find_sid_remote(model, index=None, timeout=25):
    """汽车之家站点搜索兜底：索引（A-Z 在售列表）常漏新车系，但车系页/配置页已存在。

    2026-11-21 根因：大众 ID.AURA T6 上市一个多月，A-Z 索引里没有它（grade 页只列
    "在售"车系且更新滞后），find_sid 直接返回 None → skipped，于是"没有下拉框"。
    实际上汽车之家车系页 (autohome.com.cn/8584) 和 config 页都在，parse_config 能正常
    解析出 4 款配置。也就是说：**不是数据源没有，是我们找车系的方式太单一**。

    策略：站点检索 → 从 URL 提取 series id（autohome.com.cn/<数字>）→ 交给 parse_config。
    只在索引 miss 时调用，不影响正常路径性能；失败一律返回 None，不猜。
    """
    import re as _re
    try:
        from modules.research import researcher as R
    except Exception:
        return None, None, None
    queries = [f"{model} 参数 配置", f"{model} 报价", model]
    seen = set()
    for q in queries:
        try:
            rs = R.search(q, backend="bing", n=8)
        except Exception:
            rs = []
        for r in rs or []:
            url = str(r.get("url") or "")
            m = _re.search(r"autohome\.com\.cn/(\d{3,5})(?:/|$|\?)", url)
            if not m:
                continue
            sid = m.group(1)
            if sid in seen:
                continue
            seen.add(sid)
            title = str(r.get("title") or "").strip()
            return sid, title or model, f"remote_search:{sid}"
    return None, None, None


def _fetch_config_json(sid) -> tuple[dict | None, str]:
    """抓参数页的 config JSON。优先移动版（字段名不混淆），桌面版兜底。

    为什么要移动版优先（2026-11-21 实测）：
      桌面版把「快充/慢充/高压平台」等关键词用 <span class='hs_kwNN_configFG'>
      挖空，clean() 之后「电池快充时间(小时)」和「电池慢充时间(小时)」都退化成
      「电池(小时)」——同名字段互相覆盖，快充永远取不到。移动版字段名完整，
      两版的字段 id 一致，所以移动版取干净名字、桌面版按 id 兜底。
    """
    last = ""
    for url in (CONFIG_URL_MOBILE, CONFIG_URL):
        try:
            r = requests.get(url.format(sid), headers=HDR, timeout=25)
            r.encoding = r.apparent_encoding
        except Exception as ex:                        # noqa: BLE001
            last = f"请求失败 {type(ex).__name__}"
            continue
        m = re.search(r"var config\s*=\s*(\{.*?\});\s*(?:var|\n)", r.text, re.S)
        if not m:
            last = "no config JSON"
            continue
        try:
            return json.loads(m.group(1)), ""
        except Exception as ex:                        # noqa: BLE001
            last = f"config JSON 解析失败 {type(ex).__name__}"
    return None, last or "参数页不可达"


def parse_config(sid, pure_ev_only=True):
    """解析参数页。pure_ev_only=True 时按「能源类型」列只保留纯电动款型。

    汽车之家把一车多动力车系（海豹06 / 秦L / 零跑C11 等）的 DM-i、增程、
    纯电款型放在同一个 config 矩阵里。纯电四榜的逐配置表若不过滤，会把
    9.68 万的 DM-i 当成纯电入门配置，直接违反「销量/价格必须纯电拆分」纪律。
    """
    cfg, err = _fetch_config_json(sid)
    if cfg is None:
        return None, err
    pt = (cfg.get("result") or {}).get("paramtypeitems") or []
    specs = {}
    bat: dict[str, dict] = {}          # specid → 关键字段（按 id 定位）
    price_cols = []
    # v10.12.1：不再只认「电池/充电」组——原来 in_bat_group 的判断让其余 5 个组
    # 的字段解析进内存后被整体丢弃。组优先级解决少数 id 跨组冲突：
    #   id=1147 车身结构：基本参数组「5门5座SUV」比车身组「SUV」信息更全 → 取前者
    #   id=1185 最大功率：基本参数组是电机总功率，发动机组是同车系燃油款 → 取前者
    _GROUP_PRIORITY = ("基本参数", "电池", "车身")
    def _prio(g):
        gn = str(g.get("groupname") or g.get("name") or "")
        for i, p in enumerate(_GROUP_PRIORITY):
            if p in gn:
                return i
        return len(_GROUP_PRIORITY)
    # id → 字段名 的反向表（合并三张 id 表；同 id 冲突时先到先得：
    # 基本参数 优先于 电池 优先于 车身，与 _GROUP_PRIORITY 一致）
    _ALL_IDS: dict[int, str] = {}
    for _d in (AH_BASE_IDS, AH_BODY_IDS, AH_BAT_IDS):
        for _k, _v in _d.items():
            _ALL_IDS.setdefault(_v, _k)
    pt = sorted(pt, key=_prio)
    for gi, grp in enumerate(pt):
        gname = str(grp.get("groupname") or grp.get("name") or "")
        for ii, it in enumerate(grp.get("paramitems") or []):
            nm = clean(it.get("name"))
            fid = it.get("id")
            vals = [clean(v.get("value")) for v in (it.get("valueitems") or [])]
            ids = [v.get("specid") for v in (it.get("valueitems") or [])]
            for sid2, val in zip(ids, vals):
                if sid2 is None:
                    continue
                specs.setdefault(sid2, {})[nm] = val
                slot = bat.setdefault(sid2, {})
                key = _ALL_IDS.get(fid)
                # id 不可靠的少数字段（「电池(分钟)」id=-1）按「电池组 + 骨架名」定位
                if key is None and "电池" in gname and nm == "电池(分钟)":
                    key = "fast_charge_min"
                if key and slot.get(key) in (None, "", "-", "—"):
                    slot[key] = val
                    slot.setdefault("_names", {})[key] = val
            # 按 specid 记录能源类型（同一列在基本参数/底盘两组里会重复出现）
            if nm == "能源类型":
                for sid2, val in zip(ids, vals):
                    if sid2 is not None:
                        specs.setdefault(sid2, {})["_energy"] = val
            # 指导价列键名被 CSS 混淆（实测只剩「厂()」），用「全是可解析数字」识别。
            # 页面里数字列很多（车长/功率/续航），先全收集，再取最靠前的一组里
            # 最靠前的一列——基本参数组第 2 项就是官方指导价。
            # 价格列有两道判据（2026-11-21 修）：
            #   a) 列名可信：汽车之家把"厂商指导价"混淆成「厂()」，含「厂」/「指导价」优先
            #   b) 值域兜底：纯数字列不止价格一个（CLTC 续航 620 也是纯数字，会抢到第一位，
            #      导致所有款型 price=620 → 被 3~120 万过滤掉 → 整款车解析出 0 个配置）
            named = nm != "车型" and ("厂" in nm or "指导价" in nm)
            numeric = sum(bool(re.fullmatch(r"\d{1,3}(?:\.\d{1,2})?", v)) for v in vals)
            in_range = sum(1 for v in vals
                           if re.fullmatch(r"\d{1,3}(?:\.\d{1,2})?", v) and 3 <= float(v) <= 120)
            if vals and named:
                price_cols.append((0, gi, ii, nm, list(zip(ids, vals))))
            elif (vals and nm != "车型" and numeric >= max(2, len(vals) * 0.7)
                  and in_range >= max(2, numeric * 0.9)):
                price_cols.append((1, gi, ii, nm, list(zip(ids, vals))))
    if price_cols:
        price_cols.sort(key=lambda x: (x[0], x[1], x[2]))
        for sid2, val in price_cols[0][4]:
            if sid2 is not None:
                specs[sid2]["_price"] = val
    if not specs:
        return None, "空配置矩阵"
    out = []
    for sid2, fields in specs.items():
        name = fields.get("车型") or ""
        p = fields.get("_price")
        if not name or not p or not re.fullmatch(r"\d{1,3}(?:\.\d{1,2})?", str(p)):
            continue
        energy = (fields.get("_energy") or "").strip()
        if energy in ("-", "—"):
            energy = ""
        if pure_ev_only:
            if energy:
                # 官方字段优先：插电混动 / 增程式 / 汽油 一律不进纯电逐配置表
                if "纯电动" not in energy and "纯电" not in energy:
                    continue
            else:
                # 极少数老页面无能源类型列，退到款型名，且要求明确出现 EV/纯电
                up = name.upper()
                if not (("EV" in up or "纯电" in name)
                        and not any(k in name for k in ("DM-i", "DM", "增程", "PHEV"))):
                    continue
                energy = "未标注（按款型名判定）"
        price = float(p)
        if not (3 <= price <= 120):
            continue
        def num(*keys):
            for k, v in fields.items():
                if k.startswith("_") or not any(x in k for x in keys):
                    continue
                mm = re.search(r"-?\d+(?:\.\d+)?", str(v))
                if mm:
                    return float(mm.group(0))
            return None
        kwh = num("电池能量(kWh)", "电池能量")
        cltc = num("CLTC纯电续航", "CLTC")
        motor = num("电动机总功率", "电机总功率", "最大功率")
        btype = next((v for k, v in fields.items()
                      if "电池类型" in k and v and v not in ("-", "—")), None)

        # ---- 电池组关键字段（2026-11-21 新增）----
        # 这些字段汽车之家参数页一直有，但此前解析时直接写死 None，等于
        # 「采到了却没提取」：电池来源列因此永远没有 V 电压，三电分也拿不到
        # 800V/快充加分（极氪7X 被压到低于 15 万的 T6 的根因之一）。
        b = bat.get(sid2, {})
        bn = b.get("_names", {})

        def _bv(key):
            """字段取值：按 id（或电池组骨架名）定位后的值；无值 → None。"""
            v = b.get(key)
            return None if v in (None, "", "-", "—") else v

        volt_n = None
        v_raw = _bv("voltage")
        if v_raw:
            mv = re.search(r"(\d{3,4})", str(v_raw))
            if mv:
                volt_n = int(mv.group(1))
        rng_pair = None
        r_raw = _bv("charge_range")
        if r_raw:
            mr = re.search(r"(\d+)\s*[-–]\s*(\d+)", str(r_raw))
            if mr:
                rng_pair = (int(mr.group(1)), int(mr.group(2)))
        fast_min = None
        h_raw = _bv("fast_charge_h")
        if h_raw:
            mf = re.search(r"(\d+(?:\.\d+)?)", str(h_raw))
            if mf:
                fast_min = round(float(mf.group(1)) * 60)
        if fast_min is None:
            m_raw = _bv("fast_charge_min")
            if m_raw:
                mf = re.search(r"(\d+(?:\.\d+)?)", str(m_raw))
                if mf:
                    fast_min = round(float(mf.group(1)))
        # charge 文本口径必须与 trim_board.spec_from_trim 的解析器对齐：
        #   "800V · 10–80% 约 10min" → platform_800v=True、charge_10_80_min=10
        cparts = []
        if volt_n:
            cparts.append(f"{volt_n}V")
        if fast_min:
            if rng_pair:
                cparts.append(f"{rng_pair[0]}–{rng_pair[1]}% 约 {fast_min}min")
            else:
                cparts.append(f"约 {fast_min}min")
        charge = " · ".join(cparts) if cparts else None

        tech = _bv("battery_tech")
        cell_raw = _bv("cell_supplier")
        cell_supplier, cell_src = None, ""
        if cell_raw:
            cell_supplier, cell_src = str(cell_raw), "电芯品牌"
        elif tech:
            mapped = supplier_from_tech(tech)
            if mapped:
                cell_supplier, cell_src = mapped, f"电池特有技术（{tech}）"

        cons_n = None
        c_raw = _bv("consumption")
        if c_raw:
            mc = re.search(r"(\d+(?:\.\d+)?)", str(c_raw))
            if mc:
                cons_n = float(mc.group(1))

        # 高压平台电压（V）独立成字段：charge 是人工核验过的表述，自动采集不改它；
        # 而「官方没标电压」与「标了是 400V」必须分得清，否则规则打分会把
        # 没标的车当成弱平台（缺数据≠差）。
        voltage_v = volt_n

        # 对外充电功率(kW)：「电池/充电」组的「功率(kW)」= 最大充电功率。
        # 有一批车（特斯拉/小鹏MONA 等）参数页**不给**快充时间与电压，
        # 只有这个功率字段——它是快充能力的唯一一手依据。
        power_kw = None
        p_raw = _bv("charge_power_kw")
        if p_raw:
            mp = re.search(r"(\d+(?:\.\d+)?)", str(p_raw))
            if mp:
                power_kw = float(mp.group(1))

        cooling = _bv("cooling")   # 液冷 / 直冷：影响快充与热管理

        swap = None
        s_raw = _bv("swap")
        if s_raw and str(s_raw).strip() not in ("-", "—"):
            swap = str(s_raw).strip()

        # ── 第二批：车身组 / 基本参数组（v10.12.1）──
        def _num_of(key):
            v = _bv(key)
            if v is None:
                return None
            m = re.search(r"-?\d+(?:\.\d+)?", str(v))
            return float(m.group(0)) if m else None

        wheelbase_mm = _num_of("wheelbase_mm")
        length_mm = _num_of("length_mm")
        width_mm = _num_of("width_mm")
        height_mm = _num_of("height_mm")
        if None in (length_mm, width_mm, height_mm):
            dims = _bv("dims_mm")          # "4825*1930*1666" 兜底
            if dims:
                mm = re.findall(r"\d+(?:\.\d+)?", str(dims))
                if len(mm) >= 3:
                    length_mm = length_mm or float(mm[0])
                    width_mm = width_mm or float(mm[1])
                    height_mm = height_mm or float(mm[2])
        trunk_l = _num_of("trunk_l")       # "905-1978" → 常规容积 905
        frunk_l = _num_of("frunk_l")
        cd = _num_of("cd")
        body_structure = _bv("body_structure")
        zero_to_100_s = _num_of("zero_to_100_s")
        max_power_kw = _num_of("max_power_kw")
        max_torque_nm = _num_of("max_torque_nm")
        curb_weight_kg = _num_of("curb_weight_kg")
        max_loaded_kg = _num_of("max_loaded_kg")
        launch_month = _bv("launch_month")

        yr = re.search(r"(20\d{2})款", name)
        bits = ["自动解析自汽车之家参数页"]
        if cell_src:
            bits.append(f"电芯取自「{cell_src}」")
        if charge:
            bits.append("电压/快充取自「高压平台(V)」与「电池快充时间/区间」")
        if power_kw is not None:
            bits.append("最大充电功率取自「电池/充电」组的「功率(kW)」")
        if cons_n is not None:
            bits.append("官方电耗取自「百公里耗(kWh/100km)」")
        if swap:
            bits.append(f"补能方式取自「换电」（{swap}）")
        bits.append("质保/上市时长/媒体配置亮点未取得，记 null")
        out.append({
            "name": name, "price": round(price, 2),
            "energy_type": energy or None,
            "battery_kwh": kwh, "battery_type": btype,
            "cell_supplier": cell_supplier,
            "supplier_class": ("single_named" if cell_supplier else "undisclosed"),
            "cltc_km": cltc, "charge": charge, "motor_kw": motor,
            "consumption": cons_n, "charge_power_kw": power_kw, "swap": swap,
            "voltage_v": voltage_v, "battery_cooling": cooling,
            # v10.12.1：车身 / 基本参数组字段（官方一手值）
            "wheelbase_mm": wheelbase_mm, "length_mm": length_mm,
            "width_mm": width_mm, "height_mm": height_mm,
            "trunk_l": trunk_l, "frunk_l": frunk_l, "cd": cd,
            "body_structure": body_structure,
            "zero_to_100_s": zero_to_100_s, "max_power_kw": max_power_kw,
            "max_torque_nm": max_torque_nm, "curb_weight_kg": curb_weight_kg,
            "max_loaded_kg": max_loaded_kg, "launch_month": launch_month,
            "key_configs": [], "group": (yr.group(1) + "款") if yr else "在售",
            "note": "；".join(bits),
        })
    # 去重 + 按价格升序
    seen, uniq = set(), []
    for t in sorted(out, key=lambda x: x["price"]):
        if t["name"] in seen:
            continue
        seen.add(t["name"])
        uniq.append(t)
    return uniq[:MAX_TRIMS], None


def band_of(c):
    cls = str(c.get("cls") or "")
    return 18.0 if "中大型" in cls else 16.0


def board_of(c):
    cls = str(c.get("cls") or "")
    if "SUV" in cls:
        return "中大型纯电SUV（≤18万）" if "中大型" in cls else "中型纯电SUV（≤16万）"
    return "中大型纯电轿车（≤18万）" if "中大型" in cls else "中型纯电轿车（≤16万）"


def _quote_of(trims_list: list[dict]) -> str:
    """配置表的取证引文（补录与刷新共用，保证两处口径一致）。"""
    prices = [t["price"] for t in trims_list if t.get("price") is not None]
    kwhs = [t["battery_kwh"] for t in trims_list if t.get("battery_kwh")]
    cltcs = [t["cltc_km"] for t in trims_list if t.get("cltc_km")]
    volts = sorted({int(re.match(r"(\d+)V", str(t.get("charge") or "")).group(1))
                    for t in trims_list
                    if t.get("charge") and re.match(r"(\d+)V", str(t["charge"]))})
    cells = sorted({str(t["cell_supplier"]) for t in trims_list if t.get("cell_supplier")})
    if not prices:
        return "汽车之家参数页"
    return (f"汽车之家参数页：{len(trims_list)} 个在售款型，指导价 {min(prices):g}–{max(prices):g} 万"
            + (f"；电池 {min(kwhs):g}–{max(kwhs):g}kWh" if kwhs else "")
            + (f"；CLTC {min(cltcs):.0f}–{max(cltcs):.0f}km" if cltcs else "")
            + (f"；高压平台 {'/'.join(str(v) for v in volts)}V" if volts else "")
            + (f"；电芯 {'/'.join(cells)}" if cells else ""))


def _missing_fields(trims_list: list[dict]) -> list[str]:
    """该车系配置表还缺哪些字段（partial_fields 契约）。"""
    missing = []
    if not any(t.get("cell_supplier") for t in trims_list):
        missing.append("cell_supplier")
    if not any(t.get("charge") for t in trims_list):
        missing.append("charge")
    missing += ["launch_date", "key_configs", "supplier_verification"]
    return missing


def make_entry(c, sid, ah_name, how, trims_list, pure_ev_only=True):
    from modules.contracts.candidate_schema import trim_of
    tr = trim_of(c) or {}
    target = tr.get("price")
    ref_basis = "latest_group_price_nearest"
    # 参数页常同时列多个年款；先在「全表」按候选池评分配置价格做精确匹配，
    # 因为候选池的评分配置可能属于旧年款。若只在最新年款里找，会把 12.98 万
    # 的评分配置错锚到 15.58 万的新款，凭空造出一个口径冲突。
    groups = sorted({t["group"] for t in trims_list})
    latest = groups[-1] if groups else "在售"
    pool = [t for t in trims_list if t["group"] == latest] or trims_list
    if isinstance(target, (int, float)):
        exact = [t for t in trims_list if abs(t["price"] - float(target)) <= 0.011]
        if exact:
            key = re.sub(r"\s+", "", str(tr.get("name") or ""))
            same = [t for t in exact
                    if key and key in re.sub(r"\s+", "", t["name"])]
            ref = (same or exact)[0]["name"]
            ref_basis = "exact_price" + ("_name_hint" if same else "")
        else:
            ref = min(pool, key=lambda t: abs(t["price"] - float(target)))["name"]
    else:
        band = band_of(c)
        inband = [t for t in pool if t["price"] <= band] or pool
        ref = min(inband, key=lambda t: t["price"])["name"]
        ref_basis = "inband_lowest"
    prices = [t["price"] for t in trims_list]
    kwhs = [t["battery_kwh"] for t in trims_list if t["battery_kwh"]]
    cltcs = [t["cltc_km"] for t in trims_list if t["cltc_km"]]
    quote = _quote_of(trims_list)
    missing = _missing_fields(trims_list)
    return {
        "model": c["model"], "in_board": True, "board": board_of(c),
        "scored_trim": (tr.get("name") or "—"), "launch_date": None,
        "source": ("汽车之家车系参数页（自动解析：款型名 / 官方指导价 / 电池能量 / CLTC / "
                   "电芯品牌 / 高压平台V / 快充时间与区间 / 官方电耗）"),
        "url": CONFIG_URL_MOBILE.format(sid), "date": TODAY,
        "source_type": "official_config_page_auto",
        "quote": quote,
        "note": (f"自动补录，目标车系「{ah_name}」匹配方式 {how}。"
                 + ("已按参数页「能源类型」列只保留纯电动款型，"
                    "排除同车系 DM-i / 增程 / 燃油款型；" if pure_ev_only else "")
                 + "只采信参数页里的结构化字段；电芯逐配置取自「电芯品牌」，"
                   "该字段为空时按「电池特有技术」的官方产品名映射厂商（如刀片→弗迪、"
                   "麒麟→宁德、金钟罩→中创新航），来源写在各配置 note 里。"
                   "电压取自「高压平台(V)」，快充取自「电池快充时间/区间」，"
                   "官方电耗取自「百公里耗(kWh/100km)」。"
                   "上市时长与媒体口径的配置亮点未取得，显式记 null / 空数组，不推断、不加分。"
                   "这是部分事实条目，证据密度低于人工核验条目（source_type 可区分）。"),
        "trim_class": "undisclosed",
        "pure_ev_filtered": bool(pure_ev_only),
        "validation_note": None, "user_claim_check": None,
        "auto_parsed": True,
        "partial_fields": missing,
        "trims": trims_list,
        "price_band_max": band_of(c),
        "scored_trim_ref": ref,
        "scored_trim_ref_basis": ref_basis,
    }


def _fill_missing_trim_fields(entry: dict) -> int:
    """给条目里每个配置补齐契约要求的字段（缺证据写 null）。返回补齐的字段数。"""
    from modules.contracts.trims_schema import TRIM_FIELDS
    n = 0
    for t in entry.get("trims") or []:
        for k in TRIM_FIELDS:
            if k not in t:
                t[k] = None
                n += 1
    return n


def refresh_entries(db: dict, index: dict, cands: list[dict],
                    out_path: str, dry_run: bool = False) -> int:
    """刷新已在库里的**自动补录**条目（重抓参数页补齐电压/快充/电芯/官方电耗）。

    为什么必须有这个入口（2026-11-21）：
      auto_trims 原来只补新车型（`todo = 不在库里的`）。解析器修好之后，
      库里已有的 71 款永远拿不到新字段——**改了生成器不重跑，等于没改**。
      这正是「不许打补丁」原则要治的病：修根因必须能作用到存量产物。

    纪律：
      · 只更新**同名配置**的字段，不新增/删除配置——避免动 scored_trim_ref 引用。
      · 人工核验条目（trims_manual）的事实一个字不改，只补齐契约新增字段的 null。
      · 空值不覆盖已有值（宁可不填，不抹掉人工核验结果）。
    """
    from modules.research import trim_sources as TS
    by_model = {e["model"]: e for e in db["entries"]}
    updated: list[str] = []
    skipped: list[tuple[str, str]] = []
    for c in cands:
        m = c.get("model")
        e = by_model.get(m)
        if not e:
            continue
        _fill_missing_trim_fields(e)
        is_auto = bool(e.get("auto_parsed"))
        # ① 优先复用条目自带 url 里的 sid 直接重抓。车系匹配会漂移：
        #    MONA L05 的 sid 8662 是远程检索来的，本地索引里没有，重新匹配必失败
        #    ——改了生成器却作用不到存量条目，等于没改。
        tl, how = None, "url_reuse"
        ms = re.search(r"/series/(\d+)\.html", str(e.get("url") or ""))
        if ms:
            tl, _err = parse_config(ms.group(1))
        if not tl:
            res = TS.collect_trims(m, index, parse_config,
                                   find_sid_local=find_sid_local,
                                   find_sid_remote=find_sid_remote)
            if res.get("status") != "ok":
                skipped.append((m, TS.summarize_attempts(res.get("attempts") or [])))
                continue
            tl, how = res.get("trims"), res.get("source")
        old = {t.get("name"): t for t in (e.get("trims") or [])}
        hits = 0
        for t in tl or []:
            o = old.get(t.get("name"))
            if not o:
                # 人工条目的款型名与汽车之家差异大（「660 Max 激光雷达版」vs
                # 「T6 2026款 660 Max 激光雷达版」）→ 按指导价后备匹配。
                tp = t.get("price")
                if isinstance(tp, (int, float)):
                    cands_same = [x for x in old.values()
                                  if isinstance(x.get("price"), (int, float))
                                  and abs(x["price"] - tp) <= 0.011]
                    o = cands_same[0] if len(cands_same) == 1 else None
            if not o:
                continue
            for k in ("cell_supplier", "supplier_class", "charge",
                      "consumption", "charge_power_kw", "swap", "voltage_v",
                      "battery_type", "battery_cooling",
                      # v10.12.1：车身 / 基本参数组
                      "wheelbase_mm", "length_mm", "width_mm", "height_mm",
                      "trunk_l", "frunk_l", "cd", "body_structure",
                      "zero_to_100_s", "max_power_kw", "max_torque_nm",
                      "curb_weight_kg", "max_loaded_kg", "launch_month"):
                nv = t.get(k)
                if nv in (None, "", "—"):
                    continue
                if o.get(k) == nv:
                    continue
                # 自动条目可覆盖；人工条目**只填空**，绝不覆盖已核验的值。
                if is_auto or o.get(k) in (None, "", "—"):
                    o[k] = nv
                    hits += 1
        e["date"] = TODAY
        e["quote"] = _quote_of(e.get("trims") or [])
        e["partial_fields"] = _missing_fields(e.get("trims") or [])
        e["source"] = ("汽车之家车系参数页（自动解析：款型名 / 官方指导价 / 电池能量 / CLTC / "
                       "电芯品牌 / 高压平台V / 快充时间与区间 / 官方电耗 / 换电）")
        e["note"] = (f"自动补录，目标车系「{m}」匹配方式 {how}。"
                     + "已按参数页「能源类型」列只保留纯电动款型，"
                       "排除同车系 DM-i / 增程 / 燃油款型；"
                     + "只采信参数页里的结构化字段；电芯逐配置取自「电芯品牌」，"
                       "该字段为空时按「电池特有技术」的官方产品名映射厂商（如刀片→弗迪、"
                       "麒麟→宁德、金钟罩→中创新航），来源写在各配置 note 里。"
                       "电压取自「高压平台(V)」，快充取自「电池快充时间/区间」，"
                       "官方电耗取自「百公里耗(kWh/100km)」。"
                       "上市时长与媒体口径的配置亮点未取得，显式记 null / 空数组，不推断、不加分。"
                       "这是部分事实条目，证据密度低于人工核验条目（source_type 可区分）。")
        if hits:
            updated.append(f"{m}({hits}字段)")
        time.sleep(SLEEP)
    # 契约新增字段在所有条目（含带外示例）补齐
    for e in db.get("entries", []) + db.get("out_of_board_examples", []):
        _fill_missing_trim_fields(e)
    if not dry_run:
        json.dump(db, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return len(updated)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates")
    ap.add_argument("--index", help="build_ice_pool 缓存的 ah_grade_index.json")
    ap.add_argument("--db", default=str(paths.cat("trims.json")))
    ap.add_argument("--out")
    ap.add_argument("--seed-only", action="store_true",
                    help="只从 categories/trims_manual.json 生成基础库（不联网、不需候选池）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--refresh", action="store_true",
                    help="刷新已在库里的自动补录条目（重抓参数页补齐电压/快充/电芯/官方电耗）；"
                         "人工核验条目不动，只补齐契约新增字段的 null")
    ap.add_argument("--no-pure-ev-filter", action="store_true",
                    help="不按能源类型过滤款型（仅用于调试，纯电榜禁止使用）")
    a = ap.parse_args()
    if not getattr(a, "seed_only", False) and not a.candidates:
        ap.error("--candidates 必填（或用 --seed-only）")
    if not getattr(a, "seed_only", False) and not a.index:
        ap.error("--index 必填（或用 --seed-only）")
    if getattr(a, "seed_only", False):
        n = seed_from_manual(a.out or a.db)
        print(f"[seed] 从 trims_manual.json 生成基础库 {n} 条 → {a.out or a.db}")
        return 0
    db = load_db_merged(a.db)
    index = json.load(open(a.index, encoding="utf-8"))
    cands = json.load(open(a.candidates, encoding="utf-8"))["candidates"]
    have = {e["model"] for e in db["entries"]}
    todo = [c for c in cands if c["model"] not in have]
    print(f"待补 {len(todo)} 款；已有 {len(have)} 款", file=sys.stderr)

    if getattr(a, "refresh", False):
        out = a.out or a.db
        n = refresh_entries(db, index, cands, out, dry_run=a.dry_run)
        if not a.dry_run:
            errs = validate_trims(db)
            if errs:
                print("[校验失败]", file=sys.stderr)
                for e in errs[:20]:
                    print("  ", e, file=sys.stderr)
                sys.exit(1)
            print(f"\n[refresh] 已刷新 {n} 款自动条目的字段，契约校验通过 → {out}")
        else:
            print(f"\n[dry-run] 将刷新 {n} 款")
        return 0

    from modules.research import trim_sources as TS
    added, skipped = [], []
    for i, c in enumerate(todo):
        # 多源调度：本地索引 → 站点检索 → 存在性核对；每步留痕写进待补清单
        res = TS.collect_trims(c["model"], index, parse_config,
                               find_sid_local=find_sid_local,
                               find_sid_remote=find_sid_remote)
        if res["status"] != "ok":
            why = TS.summarize_attempts(res["attempts"])
            ev = res.get("evidence") or {}
            if ev.get("exists"):
                why += f"；多站内容页确认该车在售，指导价区间 {ev.get('price_range')}"
            skipped.append((c["model"], why))
            time.sleep(SLEEP)
            continue
        sid, ah_name, tl = res["sid"], res["ah_name"], res["trims"]
        how = res["source"]
        if a.no_pure_ev_filter:
            tl2, err = parse_config(sid, pure_ev_only=False)
            if tl2:
                tl = tl2
        # 别名/变体匹配额外过一道价格一致性，防止把同名前缀的另一款车系配进来。
        # 候选池 price_guide 是官方指导价上下界；参数页常只列最新年款，故只比起售价。
        if how != "exact":
            lo = min(t["price"] for t in tl)
            pg = c.get("price_guide")
            if (isinstance(pg, list) and len(pg) == 2 and pg[0] is not None
                    and abs(lo - float(pg[0])) > 3.0):
                skipped.append((c["model"],
                                f"价格一致性不通过（参数页起售 {lo:g} 万 vs 候选池 {pg[0]} 万，"
                                f"匹配方式 {how}）"))
                time.sleep(SLEEP)
                continue
        added.append(make_entry(c, sid, ah_name, how, tl,
                               pure_ev_only=not a.no_pure_ev_filter))
        print(f"  ✓ {c['model']:16s} → {ah_name:14s} {len(tl)} 款 "
              f"{tl[0]['price']:g}–{tl[-1]['price']:g}万", file=sys.stderr)
        time.sleep(SLEEP)

    if not a.dry_run:
        db["entries"] += added
        db["updated"] = TODAY
        db["note"] = (db.get("note", "") +
                      f" {TODAY} 自动补录 {len(added)} 款：来自汽车之家车系参数页的结构化字段"
                      "（source_type=official_config_page_auto，partial_fields 列明缺项，"
                      "证据密度低于人工核验条目，不产生任何分数）。")
        out = a.out or a.db
        json.dump(db, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        errs = validate_trims(db)
        if errs:
            print("[校验失败]", file=sys.stderr)
            for e in errs[:20]:
                print("  ", e, file=sys.stderr)
            sys.exit(1)
        print(f"\n[ok] 新增 {len(added)} 款，跳过 {len(skipped)} 款；"
              f"trims.json 现共 {len(db['entries'])} 款，校验通过 → {out}")
    else:
        print(f"\n[dry-run] 将新增 {len(added)} 款")
    if skipped:
        print("\n跳过清单：", file=sys.stderr)
        for m, why in skipped:
            print(f"  - {m}: {why}", file=sys.stderr)


if __name__ == "__main__":
    main()
