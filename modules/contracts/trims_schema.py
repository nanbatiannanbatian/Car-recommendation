# -*- coding: utf-8 -*-
"""trims_schema.py —— 逐配置阶梯（trims.json）的数据契约

为什么单独成模块：
  `validate()` 原来长在 engine/trims.py 里，而唯一的另一个调用方是
  research/auto_trims.py（采集完写库前自检）——于是出现「research -> engine」
  这条向上的依赖。依赖方向一倒，research 就没法单独发布（会把整个打分引擎拖走）。

  校验规则本质是**数据契约**，不属于打分：谁写这个文件、谁读这个文件，都要认同一套
  字段与枚举。所以下沉到 contracts（比 research 还低的层），两边都向下依赖。

约束：本模块零依赖、零 IO、不 import 任何本项目其他包。
改字段就改这里，engine（读）和 research（写）自动同步，不会两套口径。
"""
from __future__ import annotations

SUPPLIER_CLASS_LABELS = {
    "single_named": "单一供应商（逐配置列明）",
    "multi_batch": "同配置多供应商混装（选配置无法规避）",
    "trim_split": "按配置固定分层（选配置可选择）",
    "new_tech_route": "新技术路线（另记上市时长）",
    "conflict": "两源冲突 / 已记 null",
    "undisclosed": "未披露",
}

TRIM_FIELDS = ("name", "price", "battery_kwh", "battery_type", "cell_supplier",
               "supplier_class", "cltc_km", "charge", "motor_kw", "key_configs",
               "group", "note", "consumption", "charge_power_kw", "swap", "voltage_v",
               # v10.12.1：第二批——「车身」「基本参数」组补提取。
               # 起因：盘点发现汽车之家参数页的可用字段**远多于**原先提取的 6 项，
               # 轴距/后备厢/零百/风阻/整备质量/上市时间全部躺在内存里被丢弃，
               # 而空间维（轴距封顶）、储物披露、操控规则、生命周期**已经在用这些概念**。
               # 字段名常被 CSS 混淆成「(mm)」「备厢(L)」之类，只能靠 id 定位。
               "wheelbase_mm", "length_mm", "width_mm", "height_mm",
               "trunk_l", "frunk_l", "cd", "body_structure",
               "zero_to_100_s", "max_power_kw", "max_torque_nm",
               "curb_weight_kg", "launch_month", "max_loaded_kg", "battery_cooling",
               # v10.51.0：底盘三字段（操控维规则的结构化事实源）。
               # 根因（2026-11-23 用户追问「底盘数据为什么缺」）：汽车之家 config JSON
               # 的「底盘转向」「车轮制动」两组对**所有能源类型**整组为空（实测燃油
               # 凯美瑞/迈腾同样 0 值，旧边界文档「燃油车有值」已失实）；易车参数页
               # WAF 验证码挡脚本、懂车帝反爬、官网 SPA 无 SSR 参数表——结构化参数表
               # 通道当前只有汽车之家一家且底盘组为空。底盘事实的合规通道是
               # 文章正文提取（chassis_fetch.py，带 url+quote 引文）与人工核验。
               # 粒度：悬架形式车系级（逐配置同名），驱动形式逐配置（后驱/四驱/空悬
               # 按配置不同）。无证据一律显式 null，不推断、不插补。
               "susp_front", "susp_rear", "drive_form")
# consumption = 官方电耗（kWh/100km），来自汽车之家参数页「百公里耗(kWh/100km)」。
# 2026-11-21 新增：此前电耗只有人工采集的 vehicle_physics.json（51/67 覆盖），
# 自动采集这一路一直漏采，导致能效强/弱的车在三电维上被系统性低估或高估。
# charge_power_kw = 最大充电功率（kW），「电池/充电」组的「功率(kW)」。
#   有一批车（特斯拉/小鹏MONA 等）参数页不给快充时间也不给电压，只有这个字段。
# swap = 是否支持换电（蔚来/乐道等），补能方式的一种。
# voltage_v = 高压平台电压（V），来自「高压平台(V)」。
#   为什么独立成字段而不塞进 charge 文本：charge 是人工核验过的表述，
#   自动采集不能改它；而「缺电压」不等于「400V」（不少车官方根本不标），
#   独立字段才分得清「官方没标」与「标了是 400V」。

ENTRY_FIELDS = ("model", "source", "url", "date", "source_type", "quote",
                "note", "trim_class")


def validate(db: dict) -> list[str]:
    """校验 trims.json 结构。返回错误列表（空 = 通过）。

    纪律：scoring_enabled 必须为 false —— 配置阶梯是事实层，永不产生分数。
    """
    errs: list[str] = []
    if db.get("scoring_enabled") is not False:
        errs.append("scoring_enabled 必须为 false（配置阶梯不得产生分数）")
    seen: set[str] = set()

    def check_entry(e: dict, w: str, need_in_board: bool) -> None:
        for k in ENTRY_FIELDS:
            if not e.get(k):
                errs.append(f"{w}: 缺 {k}（可核验要求必填）")
        if not str(e.get("url") or "").startswith(("http://", "https://")):
            errs.append(f"{w}: url 不是 http(s) 链接")
        if e.get("model") in seen:
            errs.append(f"{w}: 车型重复 {e.get('model')}")
        seen.add(e.get("model"))
        if e.get("trim_class") not in SUPPLIER_CLASS_LABELS:
            errs.append(f"{w}: trim_class 非法 {e.get('trim_class')}")
        trims = e.get("trims") or []
        if len(trims) < 2:
            errs.append(f"{w}: 阶梯至少 2 个配置")
        names = [t.get("name") for t in trims]
        if len(set(names)) != len(names):
            errs.append(f"{w}: 配置名重复")
        for i, t in enumerate(trims):
            tw = f"{w}.trims[{i}]"
            for f in TRIM_FIELDS:
                if f not in t:
                    errs.append(f"{tw}: 缺字段 {f}（无证据必须显式写 null）")
            if not isinstance(t.get("price"), (int, float)):
                errs.append(f"{tw}: price 必须为数值（指导价，禁用促销价）")
            if t.get("battery_kwh") is not None and not isinstance(
                    t.get("battery_kwh"), (int, float)):
                errs.append(f"{tw}: battery_kwh 必须为数值或 null")
            if not t.get("group"):
                errs.append(f"{tw}: 缺 group（跃迁只在同年款内比较，必须显式标年款）")
            if t.get("supplier_class") not in SUPPLIER_CLASS_LABELS:
                errs.append(f"{tw}: supplier_class 非法 {t.get('supplier_class')}")
            if t.get("supplier_class") == "conflict" and t.get("cell_supplier"):
                errs.append(f"{tw}: 标 conflict 时 cell_supplier 必须记 null")
        if need_in_board:
            if e.get("in_board") is not True:
                errs.append(f"{w}: 在榜条目 in_board 必须为 true")
            if not isinstance(e.get("price_band_max"), (int, float)):
                errs.append(f"{w}: 缺 price_band_max（J3 判带内需要）")
            ref = e.get("scored_trim_ref")
            if not ref or ref not in names:
                errs.append(f"{w}: scored_trim_ref 必须指向 trims 里存在的配置名")
        else:
            if e.get("in_board") is not False:
                errs.append(f"{w}: 带外示例 in_board 必须为 false")
            if e.get("scored_trim_ref") is not None:
                errs.append(f"{w}: 带外示例不应有 scored_trim_ref")

    for i, e in enumerate(db.get("entries", [])):
        check_entry(e, f"entries[{i}]", True)
    for i, e in enumerate(db.get("out_of_board_examples", [])):
        check_entry(e, f"out_of_board_examples[{i}]", False)
    return errs


def selftest() -> None:
    """反向篡改自检：合法样本必须过，动了任一条必须被拒。"""
    ok = {
        "scoring_enabled": False,
        "entries": [{
            "model": "测试车", "source": "官网", "url": "https://example.com",
            "date": "2026-11-01", "source_type": "official", "quote": "配置表",
            "note": "—", "trim_class": "single_named", "in_board": True,
            "price_band_max": 20, "scored_trim_ref": "标准版",
            "trims": [
                {"name": "标准版", "price": 15.0, "battery_kwh": 60, "battery_type": "磷酸铁锂",
                 "cell_supplier": "宁德时代", "supplier_class": "single_named", "cltc_km": 500,
                 "charge": [30, 80], "motor_kw": 150, "key_configs": [], "group": "2026款",
                 "consumption": 13.5, "charge_power_kw": 80.0, "swap": None, "note": "—",
                 "voltage_v": 800, "wheelbase_mm": 2900, "length_mm": 4700, "width_mm": 1900,
                 "height_mm": 1650, "trunk_l": 500, "frunk_l": None, "cd": 0.25,
                 "body_structure": "5门5座SUV", "zero_to_100_s": 6.5, "max_power_kw": 150,
                 "max_torque_nm": 300, "curb_weight_kg": 1900, "launch_month": "2026.01",
                 "max_loaded_kg": None, "battery_cooling": "液冷",
                 "susp_front": "双叉臂", "susp_rear": "多连杆", "drive_form": "后置后驱"},
                {"name": "长续航", "price": 17.0, "battery_kwh": 80, "battery_type": "磷酸铁锂",
                 "cell_supplier": "宁德时代", "supplier_class": "single_named", "cltc_km": 650,
                 "charge": [30, 80], "motor_kw": 170, "key_configs": [], "group": "2026款",
                 "consumption": 14.2, "charge_power_kw": 80.0, "swap": None, "note": "—",
                 "voltage_v": 800, "wheelbase_mm": 2900, "length_mm": 4700, "width_mm": 1900,
                 "height_mm": 1650, "trunk_l": 500, "frunk_l": None, "cd": 0.25,
                 "body_structure": "5门5座SUV", "zero_to_100_s": 6.8, "max_power_kw": 170,
                 "max_torque_nm": 320, "curb_weight_kg": 1950, "launch_month": "2026.01",
                 "max_loaded_kg": None, "battery_cooling": "液冷",
                 "susp_front": "双叉臂", "susp_rear": "多连杆", "drive_form": "后置后驱"},
            ],
        }],
        "out_of_board_examples": [],
    }
    assert validate(ok) == [], validate(ok)
    bad = {**ok, "scoring_enabled": True}
    assert validate(bad), "scoring_enabled=True 必须被拒"
    import copy
    bad2 = copy.deepcopy(ok)
    del bad2["entries"][0]["trims"][0]["cltc_km"]
    assert any("cltc_km" in e for e in validate(bad2)), "缺字段必须被拒"
    print("trims_schema selftest OK")


if __name__ == "__main__":
    selftest()
