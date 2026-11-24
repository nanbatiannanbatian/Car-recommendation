# -*- coding: utf-8 -*-
"""env_equivalence_regress.py —— envscore 双实现等价回归（v10.52.0）

背景：环境分规则原只活在 engine.js（浏览器侧），定稿榜靠"页面首次加载 JS 算一次"，
定稿结论不可服务端复现、不可 Python 审计。v10.52.0 起规则单一事实源 =
modules/envscore/engine.py，engine.js 是浏览器交互镜像。本回归用 node 实跑 engine.js
与 Python engine 对同一批 fixtures × 画像，断言：
  adjust 软+硬分项/合计、rangeCalc 续航、tco 8年合计、verdicts 级别 全部一致——
防两套口径漂移（项目"禁止两处系数"纪律的环境分版）。

运行：python env_equivalence_regress.py（内部调 node，缺 node 时跳过并提示）
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import paths

HERE = str(paths.ROOT)
sys.path.insert(0, HERE)

from modules.envscore import engine as ENG  # noqa: E402
from modules.physics import ev_range_model as ERM  # noqa: E402

ENGINE_JS = str(paths.MODULES / "envscore" / "engine.js")

# ===== fixtures（比较器格式 camelCase）=====
FIXTURES = [
    {"model": "测试纯电", "power": "BEV", "price": 14.0, "kwh": 60.0, "cons": 14.0,
     "consSrc": "事实库", "weight": 1800, "cd": 0.24, "area": 2.4, "voltage": 800,
     "swap": False, "cdaPerTon": 0.32, "body": "轿车", "product": 82.0},
    {"model": "测试纯电换电", "power": "BEV", "price": 16.0, "kwh": 100.0, "cons": 15.3,
     "consSrc": "事实库", "weight": 2100, "cd": 0.26, "area": 2.6, "voltage": None,
     "swap": True, "cdaPerTon": 0.30, "body": "SUV", "product": 85.0},
    {"model": "测试方盒子纯电", "power": "BEV", "price": 13.0, "kwh": 80.0, "cons": 18.0,
     "consSrc": "物理估算", "weight": 2300, "cd": 0.34, "area": 2.9, "voltage": 400,
     "swap": False, "cdaPerTon": 0.42, "body": "SUV", "product": 80.0,
     "fuelL100": None, "fuelDeprivedL100": None},
    {"model": "测试插混", "power": "PHEV", "price": 11.0, "product": 84.0,
     "fuelL100": 1.11, "fuelDeprivedL100": 2.79, "cons": 16.0, "kwh": 25.28},
    {"model": "测试增程", "power": "EREV", "price": 13.0, "product": 82.0,
     "fuelL100": 1.11, "fuelDeprivedL100": 4.0, "cons": 17.0, "kwh": 28.39},
    {"model": "测试油混", "power": "HEV", "price": 13.0, "product": 78.0, "fuelL100": 4.13},
    {"model": "测试燃油", "power": "ICE", "price": 12.5, "product": 76.0, "fuelL100": 5.57},
]

# ===== 画像：默认 + 8 稳健场景 + 新因素专项 =====
PROFILES = [
    ("默认", {}),
    ("无家充", {"home_charger": "none"}),
    ("北方极寒", {"winter_temp": "underm20"}),
    ("华北寒", {"winter_temp": "m20tom10"}),
    ("长途高频", {"long_trip_freq": "weekly", "single_trip": "over500"}),
    ("营运", {"operation": "yes"}),
    ("限牌", {"plate_limit": "yes"}),
    ("京沪限牌", {"plate_limit": "beijing"}),
    ("上海限牌", {"plate_limit": "shanghai"}),
    ("补能不耐烦", {"charge_patience": "reluctant"}),
    ("低里程无桩", {"annual_km": "under10", "home_charger": "none"}),
    ("长持有", {"hold_years": "over8"}),
    ("短持有", {"hold_years": "under3"}),
    ("常年高速", {"highway_ratio": "over60"}),
    # v10.53.0 新增因素专项（v10.55.0 已删条件不再保留画像）
    ("增购第二辆", {"fleet": "second"}),
    ("单位充电", {"home_charger": "work"}),
    ("增量+长途", {"fleet": "second", "long_trip_freq": "weekly", "single_trip": "over500"}),
    ("全硬条件组合", {"home_charger": "none", "winter_temp": "underm20",
                    "hold_years": "under3", "long_trip_freq": "weekly",
                    "single_trip": "over500", "plate_limit": "beijing",
                    "operation": "yes", "charge_patience": "reluctant",
                    "hold_years": "under3"}),
]


def phys_json():
    """与 build_energy_compare.build() 的 __PHYS_JSON__ 注入同构（单一事实源）。"""
    return {
        "a": ERM.REG_A, "b": ERM.REG_B, "c": ERM.REG_C, "areaFactor": ERM.AREA_FACTOR,
        "medRatio": ERM.MED_CDA_PER_TON,
        "phys": {"crr": ERM.CRR, "eta": ERM.ETA_DRIVE, "aux": ERM.AUX_12V_KW,
                 "calG": ERM.CAL_G, "usable": ERM.USABLE_SOC, "medRatio": ERM.MED_CDA_PER_TON,
                 "speed": ERM.SPEED_BY_BUCKET, "stopgo": ERM.STOPGO_BY_BUCKET,
                 "battery": ERM.BATTERY_TEMP, "hvacKw": ERM.HVAC_KW, "hpRatio": ERM.HVAC_HP_RATIO,
                 "hvacMode": ERM.HVAC_MODE, "acLevel": ERM.AC_LEVEL, "drive": ERM.DRIVE_MULT,
                 "volt": ERM.VOLTAGE_MULT, "road": ERM.ROAD_CRR_MULT,
                 "tire": ERM.TIRE_PRESSURE_MULT, "tireType": ERM.TIRE_TYPE_MULT, "load": ERM.LOAD_MULT},
        "drive": ERM.DRIVE_MULT, "volt": ERM.VOLTAGE_MULT,
        "swapAdv": {"no_charger": {"gain": 2.5, "reason": ""}, "short_hold": {"gain": 1.5, "reason": ""},
                    "cold": {"gain": 1.5, "reason": ""}, "longtrip": {"gain": 1.0, "reason": ""}},
        "tco": ENG.tco_js(), "annualKmMid": ENG.ANNUAL_KM_MID,
        "tcoBasis": "8年持有",
    }


NODE_HARNESS = r"""
const fs=require('fs');
const PHYS=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
const src=fs.readFileSync(process.argv[2],'utf8');
const factory=new Function('PHYS', src + '\n;return {adjust:adjust,rangeCalc:rangeCalc,tco:tco,verdicts:verdicts,effRange:effRange,recommendForm:recommendForm,checkProfile:checkProfile,formNarrative:formNarrative};');
const E=factory(PHYS);
const fixtures=JSON.parse(fs.readFileSync(process.argv[4],'utf8'));
const profiles=JSON.parse(fs.readFileSync(process.argv[5],'utf8'));
const out=[];
for(const [pname,cond] of profiles){
  for(const car of fixtures){
    const a=E.adjust(car,cond);
    const rc=E.rangeCalc(car,cond);
    const tc=E.tco(car,cond);
    out.push({p:pname,m:car.model,
      total:+a.total.toFixed(2),
      items:a.items.map(i=>[i.label,+(i.pt||0).toFixed(2),!!i.na]),
      hard:a.hard.map(h=>[h.label,h.pt,!!h.downgrade]),
      range:rc?rc.range:null,
      tco8y:tc.tco8y, unit:tc.unitEnergy,
      verdicts:Object.entries(a.verdicts||{}).map(([k,v])=>[k,v[0]])
    });
  }
  const rows=fixtures.map(c=>{const a=E.adjust(c,cond);return {model:c.model,power:c.power,
    composite:(c.product||0)+a.total,adj:a.total,hard:a.hard,verdicts:a.verdicts};});
  out.push({p:pname,m:'__FORM__',forms:E.recommendForm(rows,cond),
            diagnostics:E.checkProfile(cond),narrative:E.formNarrative(E.recommendForm(rows,cond),cond)});
}
console.log(JSON.stringify(out));
"""


def run_node():
    """node 实跑 engine.js，返回结果列表；缺 node 返回 None。
    画像传完整 cond（默认值已合并）——与页面 readCond() 行为一致，防 undefined 污染。"""
    if not shutil.which("node"):
        return None
    full_profiles = [[n, {**ENG.DEFAULT_COND, **p}] for n, p in PROFILES]
    with tempfile.TemporaryDirectory() as td:
        ph = os.path.join(td, "phys.json")
        fx = os.path.join(td, "fixtures.json")
        pf = os.path.join(td, "profiles.json")
        ha = os.path.join(td, "harness.js")
        open(ph, "w", encoding="utf-8").write(json.dumps(phys_json(), ensure_ascii=False))
        open(fx, "w", encoding="utf-8").write(json.dumps(FIXTURES, ensure_ascii=False))
        open(pf, "w", encoding="utf-8").write(json.dumps(full_profiles, ensure_ascii=False))
        open(ha, "w", encoding="utf-8").write(NODE_HARNESS)
        p = subprocess.run([shutil.which("node"), ha, ENGINE_JS, ph, fx, pf],
                           capture_output=True, timeout=120)
        if p.returncode != 0:
            raise AssertionError("node 执行失败: " + p.stderr.decode("utf-8", "replace")[:500])
        return json.loads(p.stdout.decode("utf-8"))


def py_side():
    out = []
    for pname, patch in PROFILES:
        cond = {**ENG.DEFAULT_COND, **patch}
        for car in FIXTURES:
            a = ENG.adjust(car, cond)
            rc = ENG.range_calc(car, cond)
            tc = ENG.tco(car, cond)
            out.append({
                "p": pname, "m": car["model"],
                "total": round(a["total"], 2),
                "items": [[it["label"], round(it["pt"], 2), bool(it.get("na"))] for it in a["items"]],
                "hard": [[h["label"], h["pt"], bool(h["downgrade"])] for h in a["hard"]],
                "range": (rc or {}).get("range"),
                "tco8y": tc["tco_8y"], "unit": tc["unit_energy"],
                "verdicts": [[k, v[0]] for k, v in a["verdicts"].items()],
            })
        rows = []
        for car in FIXTURES:
            a = ENG.adjust(car, cond)
            rows.append({"model": car["model"], "power": car["power"],
                         "composite": (car.get("product") or 0) + a["total"],
                         "adj": a["total"], "hard": a["hard"], "verdicts": a["verdicts"]})
        forms = ENG.recommend_form(rows, cond)
        out.append({"p": pname, "m": "__FORM__", "forms": forms,
                    "diagnostics": ENG.check_profile(cond),
                    "narrative": ENG.form_narrative(forms, cond)})
    return out


def main():
    js = run_node()
    if js is None:
        print("[env_equivalence] node 不可用，跳过 JS 侧（Python selftest 仍跑）")
        ENG.selftest()
        return 0
    py = py_side()
    assert len(js) == len(py), f"结果数不等: node={len(js)} py={len(py)}"
    diffs = []
    for j, q in zip(js, py):
        tag = f"[{j['p']}] {j['m']}"
        if j["m"] == "__FORM__":
            # 形式聚合结论：逐形式比 均分/车数/硬条件/最强车/标签，且名次顺序一致（页面直接按它排序展示）
            jf = {f["power"]: f for f in j["forms"]}
            qf = {f["power"]: f for f in q["forms"]}
            if set(jf) != set(qf):
                diffs.append(f"{tag} 形式集合 js={list(jf)} py={list(qf)}")
            for p in set(jf) & set(qf):
                a, b = jf[p], qf[p]
                if abs(a["avg"] - b["avg"]) > 0.06:
                    diffs.append(f"{tag} {p} avg js={a['avg']} py={b['avg']}")
                for k in ("n", "hard", "best", "label", "rank"):
                    if a.get(k) != b.get(k):
                        diffs.append(f"{tag} {p} {k} js={a.get(k)} py={b.get(k)}")
            jorder = [f["power"] for f in sorted([x for x in j["forms"] if x.get("rank")], key=lambda x: x["rank"])]
            qorder = [f["power"] for f in sorted([x for x in q["forms"] if x.get("rank")], key=lambda x: x["rank"])]
            if jorder != qorder:
                diffs.append(f"{tag} 形式排序 js={jorder} py={qorder}")
            jd = [w["text"] for w in j["diagnostics"]]
            qd = [w["text"] for w in q["diagnostics"]]
            if jd != qd:
                diffs.append(f"{tag} 画像自检 js={jd} py={qd}")
            if j["narrative"] != q["narrative"]:
                diffs.append(f"{tag} 形式结论文案 js={j['narrative']} py={q['narrative']}")
            continue
        if abs(j["total"] - q["total"]) > 0.011:
            diffs.append(f"{tag} adjust.total js={j['total']} py={q['total']}")
        if (j["range"] is None) != (q["range"] is None):
            diffs.append(f"{tag} range js={j['range']} py={q['range']}（一侧无电量数据）")
        elif j["range"] is not None and abs(j["range"] - q["range"]) > 1:
            diffs.append(f"{tag} range js={j['range']} py={q['range']}")
        if abs(j["tco8y"] - q["tco8y"]) > 2:
            diffs.append(f"{tag} tco8y js={j['tco8y']} py={q['tco8y']}")
        if abs(j["unit"] - q["unit"]) > 0.0011:
            diffs.append(f"{tag} unit js={j['unit']} py={q['unit']}")
        jh = sorted((h[0], h[1]) for h in j["hard"])
        qh = sorted((h[0], float(h[1])) for h in q["hard"])
        if jh != qh:
            diffs.append(f"{tag} hard js={jh} py={qh}")
        ji = sorted((i[0], i[1]) for i in j["items"])
        qi = sorted((i[0], float(i[1])) for i in q["items"])
        if ji != qi:
            diffs.append(f"{tag} items js={ji} py={qi}")
        jv = sorted((v[0], v[1]) for v in j["verdicts"])
        qv = sorted((v[0], v[1]) for v in q["verdicts"])
        if jv != qv:
            diffs.append(f"{tag} verdicts js={jv} py={qv}")
    if diffs:
        raise AssertionError("JS/Python 引擎不等价（漂移！）：\n" + "\n".join(diffs[:20]))
    n_cases = sum(1 for j in js if j["m"] != "__FORM__")
    print(f"[env_equivalence] PASS · {n_cases} 组（{len(FIXTURES)}车×{len(PROFILES)}画像）"
          f" adjust/续航/TCO/判定 + 形式结论/画像自检 全一致 · 硬条件H1-H7/纯电优先/11因素")
    return 0


if __name__ == "__main__":
    sys.exit(main())

