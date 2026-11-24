# -*- coding: utf-8 -*-
"""param_score_regress.py — 燃油/油混/插混 8 维打分回归（离线，零网络）

覆盖：
  1) 权重与跨能源页 EC.DIMS 完全一致（两套口径不许漂移）
  2) 缺维不填假分：无碰撞判档时 safety 必须缺，不能填
  3) PPH 单调：可靠性 PPH 越高 → quality 越低
  4) 能源类型变体映射：汽油+48V轻混/汽油+CNG → ICE；汽油电驱 → HEV
  5) 主力款配对：功率/车重/价格取同一台（中位价款），不许「顶配功率 ÷ 丐版价格」
  6) 缓存 int/str 键兼容：field() 两种键都取得到
  7) 真实产物自检：data/gen/ice_scores_compact.json 价格带/维度/分值区间合法
"""
import json, os, sys

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, HERE)

from modules.engine import param_score as PS
from modules.research import ah_series as AH
from modules.envscore import build_energy_compare as EC


def t_weights():
    for k, w in PS.WEIGHTS.items():
        assert abs(float(EC.WEIGHT_MAP[k]) - w) < 1e-9, "维度 %s 权重不一致：%s vs %s" % (k, w, EC.WEIGHT_MAP[k])
    assert set(PS.WEIGHTS) == set(EC.WEIGHT_MAP), "维度集合不一致"


def t_no_fake_safety():
    sc = PS.score_facts({"power": "ICE", "price_min": 10.0, "price_ref": 10.0, "curb_kg": 1300,
                         "system_kw": 110, "fuel_wltc": 6.0, "wheelbase": 2700, "zero100": 9.0,
                         "koubei": {"dims": {"配置": 4.2, "空间": 4.4, "性价比": 4.3, "驾驶感受": 4.1}},
                         "pph": {"reliability": 150}})
    assert sc and "safety" not in sc["scores"], "无碰撞判档却给出了安全分"


def t_pph_monotonic():
    base = {"power": "ICE", "price_ref": 10.0, "curb_kg": 1300, "system_kw": 110,
            "fuel_wltc": 6.0, "wheelbase": 2700, "zero100": 9.0,
            "koubei": {"dims": {"配置": 4.2, "空间": 4.4, "性价比": 4.3, "驾驶感受": 4.1}}}
    good = PS.score_facts({**base, "pph": {"reliability": 80, "new_car": 60}})["scores"]["quality"]
    bad = PS.score_facts({**base, "pph": {"reliability": 300, "new_car": 220}})["scores"]["quality"]
    assert good > bad, "PPH 越高分数应越低：%s vs %s" % (good, bad)


def t_energy_map():
    assert AH.map_energy("汽油") == "ICE"
    assert AH.map_energy("汽油+48V轻混系统") == "ICE", "48V 轻混应算 ICE（弱混），不能当油混"
    assert AH.map_energy("汽油+CNG") == "ICE"
    assert AH.map_energy("汽油电驱") == "HEV", "e-POWER 类汽油电驱应归 HEV"
    assert AH.map_energy("插电式混合动力") == "PHEV"
    assert AH.map_energy("增程式") == "EREV"
    assert AH.map_energy("纯电动") == "BEV"
    assert AH.map_energy("") is None


def t_main_trim_pairing():
    """同一车系：用主力款功率与主力款价格配对；HEV 系统功率按规则估算。"""
    cache = os.path.join(AH.CACHE_DIR, "448.json")
    if not os.path.exists(cache):
        print("  (skip 主力款配对：缺 448 缓存)")
        return
    rec = AH._coerce(json.load(open(cache, encoding="utf-8")))
    g = (rec.get("agg") or {}).get("groups") or {}
    assert "HEV" in g, "轩逸 e-POWER 未被识别为 HEV"
    hev = g["HEV"]
    m = hev["main"]
    assert m["price"] and m["system_kw"], "主力款缺价格/系统功率"
    assert hev["engine_kw"] < hev["price_max"] * 100, "功率字段疑似串位"
    # e-POWER 串联：发动机只发电，系统功率应≈电机功率，而不是发动机+电机相加
    assert abs(m["system_kw"] - (m["elec_kw"] or 0)) < 15, \
        "串联混动系统功率估算偏差过大：%s vs 电机 %s" % (m["system_kw"], m["elec_kw"])


def t_cache_key_types():
    f = {"1149": "汽油"}
    assert AH.field({"fields": f}, 1149) == "汽油", "str 键缓存取不到"
    assert AH.field({"fields": {1149: "汽油"}}, 1149) == "汽油", "int 键取不到"


def t_real_doc():
    doc = os.path.join(HERE, "data", "gen", "ice_scores_compact.json")
    if not os.path.exists(doc):
        print("  (skip 真实产物：尚未生成)")
        return
    d = json.load(open(doc, encoding="utf-8"))
    for c in d["cars"]:
        assert c["power"] in ("ICE", "HEV", "PHEV", "EREV"), "纯电不应混进本管线：%s" % c["power"]
        assert 6.0 <= c["price_low"] <= 16.0, "%s 价格 %.2f 在带外" % (c["model"], c["price_low"])
        assert 30 <= c["product"] <= 95, "%s 综合分 %.1f 异常" % (c["model"], c["product"])
        assert len(c["coverage"]) >= PS.MIN_DIMS, "%s 维数不足却入榜" % c["model"]
        for k, v in c["scores"].items():
            assert k in PS.WEIGHTS and 0 <= v <= 10, "%s 维度 %s=%.2f 非法" % (c["model"], k, v)


def main():
    tests = [t_weights, t_no_fake_safety, t_pph_monotonic, t_energy_map,
             t_main_trim_pairing, t_cache_key_types, t_real_doc]
    for t in tests:
        t()
    print("[param_score_regress] PASS · %d 项（权重一致/缺维不填/PPH单调/能源映射/主力款配对/键兼容/产物合法）"
          % len(tests))


if __name__ == "__main__":
    main()
