#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工信部备案抓取（filings.py）——把「还没上市但已经报备」的车捞出来。

为什么用购置税目录而不是产品公告正文：
  1. 《道路机动车辆生产企业及产品公告》附件只有企业/商标/型号前缀，没有车型名，
     没法对到「消费者认得的名字」；
  2. 《减免车辆购置税的新能源汽车车型目录》附件是纯电乘用车逐型号表，一行就带
     **通用名称 + 续驶里程 + 整备质量 + 电池总能量**——这四项刚好是预判产品力的
     最小充分集，且全部是官方一手（车企自己申报、工信部核过）。
  3. 参数详情页（queryCpParamPage）有滑块验证码，走不通；附件没有。

数据链路（全部免登录、无验证码）：
  公告发布栏目 → dataproxy.jsp 列表 → 找到最新「予以公告」批次文章
  → 下载附件 doc（OLE2）→ 纯 Python 解 Word 文本流 → 解析表格 → 结构化 JSON

用法：
    python3 filings.py                        # 抓最新已公告批次，列纯电乘用车
    python3 filings.py --batch 411            # 指定批次
    python3 filings.py --list                 # 列出近几批（含公示/公告状态）
    python3 filings.py --json out.json        # 存 JSON
    python3 filings.py --new-only             # 只留不在基准池里的（即待上市）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import struct
import sys
import time

import requests

try:
    import olefile
except ImportError:
    olefile = None

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_COL = "https://www.miit-eidc.org.cn"
COL_1691 = BASE_COL + "/col/col1691/index.html"
LIST_API = BASE_COL + "/module/web/jpage/dataproxy.jsp"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False          # 本机有代理，政务站走直连更稳
    s.headers.update({"User-Agent": UA, "Referer": COL_1691})
    return s


# ---------------------------------------------------------------- Word 97 文本
def _fib(ole):
    wd = ole.openstream("WordDocument").read()
    flags = struct.unpack_from("<H", wd, 0x0A)[0]
    table_name = "1Table" if (flags & 0x0200) else "0Table"
    tbl = ole.openstream(table_name).read()
    fc_clx, lcb_clx = struct.unpack_from("<II", wd, 0x01A2)
    return wd, tbl, fc_clx, lcb_clx


def doc_text(path: str) -> str:
    """解 Word 97 二进制：FIB → 片段表 Clx → 文本。单元格分隔符保留为 \\x07。"""
    if olefile is None:
        raise RuntimeError("缺少 olefile：python -m pip install olefile -i "
                           "https://mirrors.aliyun.com/pypi/simple/")
    ole = olefile.OleFileIO(path)
    try:
        wd, tbl, fc_clx, lcb_clx = _fib(ole)
        clx = tbl[fc_clx:fc_clx + lcb_clx]
        plc, i = None, 0
        while i < len(clx):
            if clx[i] == 0x01:
                i += 3 + struct.unpack_from("<H", clx, i + 1)[0]
            elif clx[i] == 0x02:
                lcb = struct.unpack_from("<I", clx, i + 1)[0]
                plc = clx[i + 5:i + 5 + lcb]
                break
            else:
                i += 1
        if plc is None:
            raise ValueError("找不到 Word 片段表")
        n = (len(plc) - 4) // 12
        cps = [struct.unpack_from("<I", plc, 4 * k)[0] for k in range(n + 1)]
        out = []
        for k in range(n):
            off = 4 * (n + 1) + 8 * k
            fc = struct.unpack_from("<I", plc, off + 2)[0]
            comp = bool(fc & 0x40000000)
            fc &= 0x3FFFFFFF
            ln = cps[k + 1] - cps[k]
            if comp:
                out.append(wd[fc // 2: fc // 2 + ln].decode("cp1252", "replace"))
            else:
                out.append(wd[fc: fc + ln * 2].decode("utf-16-le", "replace"))
        return "".join(out)
    finally:
        ole.close()


# ---------------------------------------------------------------- 列表 / 附件
BATCH_RE = re.compile(r"第\s*(\d+)\s*批")


def list_batches(sess, pages: int = 3) -> list[dict]:
    """抓公告发布栏目，返回 [{batch, url, date, title, published}]。"""
    out, seen = [], set()
    for p in range(1, pages + 1):
        url = (f"{LIST_API}?page={p}&webid=12&path={BASE_COL}/"
               f"&columnid=1691&unitid=4638&webname=x&permissiontype=0")
        try:
            r = sess.get(url, timeout=30)
        except Exception:
            break
        if r.status_code != 200:
            break
        for m in re.finditer(
                r"<a href='([^']+)' title='([^']*)'[^>]*>.*?</a>\s*"
                r"<span class=\"art_time\">\[([\d-]+)\]</span>", r.text, re.S):
            u, title, date = m.group(1), m.group(2), m.group(3)
            if u in seen or "道路机动车辆生产企业及产品" not in title:
                continue
            seen.add(u)
            bm = BATCH_RE.search(title)
            if not bm:
                continue
            out.append({
                "batch": int(bm.group(1)), "url": u, "date": date, "title": title,
                "published": ("公示" not in title) and ("拟发布" not in title),
            })
        if "totalpage" in r.text:
            tp = int(re.search(r"<totalpage>(\d+)</totalpage>", r.text).group(1))
            if p >= tp:
                break
        time.sleep(0.3)
    out.sort(key=lambda x: (-x["batch"], x["date"]))
    return out


def pick_batch(items: list[dict], want: int | None) -> dict:
    if want:
        hit = [x for x in items if x["batch"] == want and x["published"]]
        if not hit:
            hit = [x for x in items if x["batch"] == want]
        if not hit:
            raise SystemExit(f"栏目里没有第 {want} 批")
        return hit[0]
    pub = [x for x in items if x["published"]]
    if not pub:
        raise SystemExit("栏目里没有已公告批次（只有公示稿）")
    return pub[0]


def article_attachments(sess, url: str) -> list[dict]:
    r = sess.get(url, timeout=30)
    r.encoding = r.apparent_encoding or "utf-8"
    html = r.text
    out, seen = [], set()
    for m in re.finditer(r'href="(https?://[^"]+\.doc)"', html, re.I):
        u = m.group(1)
        if u in seen:
            continue
        seen.add(u)
        # 标题在链接前后 200 字符内
        pos = html.find(u)
        ctx = re.sub(r"<[^>]+>", " ", html[max(0, pos - 260):pos + 320])
        out.append({"url": u, "ctx": re.sub(r"\s+", " ", ctx)})
    return out


def download(sess, url: str, dest: str) -> bool:
    if os.path.exists(dest) and os.path.getsize(dest) > 10000:
        return True
    try:
        r = sess.get(url, timeout=90, headers={"Referer": BASE_COL + "/"})
    except Exception:
        return False
    if r.status_code != 200 or len(r.content) < 10000:
        return False
    with open(dest, "wb") as f:
        f.write(r.content)
    return True


# ---------------------------------------------------------------- 目录解析
SEC_POWER = re.compile(r"^[一二三四五六]、\s*(.+)$")
SEC_CLASS = re.compile(r"^（[一二三四五六]）\s*(.+)$")
POWER_MAP = [("纯电动", "BEV"), ("插电式混合动力", "PHEV"),
             ("插电式增程", "EREV"), ("燃料电池", "FCEV")]


def _power_of(s: str) -> str | None:
    for k, v in POWER_MAP:
        if k in s:
            return v
    return None


def _num(s: str) -> float | None:
    if not s:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)", s.replace(",", ""))
    return float(m.group(1)) if m else None


def _tokens(text: str) -> list[str]:
    """切成「单元格级」词元。

    难点：Word 表格前的标题段落和第一个单元格粘在一起（如
    `…（一）乘用车序号` 后面才是 \\x07），直接按 \\x07 切会漏掉章节标记与表头。
    这里在章节标记和「序号」前插分隔符，再统一切。
    """
    sep = "\x01"
    text = re.sub(r"([一二三四五六]、)", lambda m: sep + m.group(1), text)
    text = re.sub(r"(（[一二三四五六]）)", lambda m: sep + m.group(1), text)
    text = re.sub(r"(序号)", lambda m: sep + m.group(1) + sep, text)
    out = []
    for chunk in text.split("\x07"):
        for p in chunk.split("\x01"):
            p = p.replace("\r", "").replace("\n", "").strip()
            out.append(p)
    return out


MODEL_RE = re.compile(r"^[A-Za-z]{1,4}\d[0-9A-Za-z\-]{3,}$")
ENT_TAIL = ("公司", "集团", "厂", "中心", "研究院")


def _is_model(t: str) -> bool:
    return bool(MODEL_RE.match(t or ""))


def _is_ent(t: str) -> bool:
    return bool(t) and t.endswith(ENT_TAIL) and len(t) >= 4


def parse_catalog(text: str) -> list[dict]:
    """解析《减免车辆购置税的新能源汽车车型目录》表格。

    结构：一、纯电动汽车 →（一）乘用车 /（二）客车；再 二、插电式…
    坑：批次之间表格列不一致——有的有「序号」列（411 批），有的没有（410 批），
    数据行还可能省略企业名（同一家企业连续多行）。所以**不能按序号切行**，
    要按「车辆型号」这个唯一稳定字段定位行，再按表头顺序取其余字段。
    """
    text = text.replace("\x0b", "").replace("\x0c", "").replace("\x0e", "")
    for ch in ("\x13", "\x14", "\x15", "\x1e", "\x1f"):
        text = text.replace(ch, "")
    tok = _tokens(text)

    rows, power, klass, headers, ent = [], None, None, [], None
    i, n = 0, len(tok)
    while i < n:
        t = tok[i]
        if not t:
            i += 1
            continue
        m = SEC_POWER.match(t)
        if m:
            power, klass, headers = _power_of(m.group(1)), None, []
            i += 1
            continue
        m = SEC_CLASS.match(t)
        if m:
            klass, headers = m.group(1), []
            i += 1
            continue
        if t == "序号":
            j, hs = i + 1, []
            while j < n and len(hs) < 12:
                v = tok[j]
                if not v:
                    j += 1
                    continue
                if _is_model(v) or _is_ent(v):
                    break
                hs.append(v)
                j += 1
            headers, i = hs, j
            continue
        if headers and _is_model(t):
            mi = headers.index("车辆型号") if "车辆型号" in headers else None
            if mi is None:
                i += 1
                continue
            after = [x for x in tok[i + 1:i + 1 + (len(headers) - mi - 1)]]
            tail = headers[mi + 1:]

            def field(name_kw, arr=after, hd=tail):
                for k, v in zip(hd, arr):
                    if name_kw in k:
                        return v
                return ""

            pname = field("产品名称")
            if not any(k in pname for k in ("乘用车", "轿车", "客车", "货车",
                                            "专用", "挂车", "越野")):
                i += 1
                continue
            prev = tok[i - 1] if i else ""
            if _is_ent(prev):
                ent = prev
            common = field("通用名称")
            rng = field("续驶里程")
            rngs = re.findall(r"\d+(?:\.\d+)?", rng or "")
            rows.append({
                "batch": None, "power": power, "cls_cat": klass,
                "enterprise": ent, "model_code": t,
                "common_name": (common or "").strip(),
                "names": [x.strip() for x in re.split(r"[,，、/]", common or "")
                          if x.strip()],
                "product_name": pname,
                "range_km": max(float(x) for x in rngs) if rngs else None,
                "batt_kwh": _num(field("总能量")),
                "kerb_kg": _num(field("整备质量")),
                "batt_kg": _num(field("总质量")),
                "row_no": None,
            })
            i += 1
            continue
        i += 1
    return rows


def pick_catalog(sess, atts, tmpdir: str) -> tuple[str, str] | None:
    """下载附件，认出「减免车辆购置税的新能源汽车车型目录」那一份。"""
    os.makedirs(tmpdir, exist_ok=True)
    cands = sorted(atts, key=lambda a: 0 if "购置税" in a["ctx"] else 1)
    for a in cands:
        name = a["url"].rsplit("/", 1)[-1]
        dest = os.path.join(tmpdir, name)
        if not download(sess, a["url"], dest):
            continue
        try:
            head = doc_text(dest)[:400]
        except Exception:
            continue
        if "减免车辆购置税" in head and "车型目录" in head:
            return dest, a["url"]
    return None


# ---------------------------------------------------------------- 名称归一
def norm_name(s: str) -> str:
    s = (s or "").strip()
    s = s.replace("（", "(").replace("）", ")")
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"[\s\-·_]", "", s)
    s = s.replace("牌", "")
    return s.upper()


# 同一台车在三种材料里叫三个名字：备案目录用官方商标名（ΑS5、ID.UNYX 与众07、
# 比亚迪海豹06），榜单用消费者名（极狐阿尔法S5、大众与众07、海豹06EV）。
# 归一化到「一组候选键」，任一命中即算同车——只做规则，不做模糊猜测。
GREEK_ALIAS = [("阿尔法", "Α"), ("贝塔", "Β"), ("伽马", "Γ"),
               ("阿尔法", ""), ("贝塔", ""), ("伽马", "")]
BRAND_STRIP = ["比亚迪", "大众汽车", "上汽大众", "一汽大众", "大众", "ID.UNYX",
               "ID.", "丰田", "一汽丰田", "广汽丰田", "日产", "东风日产", "别克",
               "上汽通用", "长安启源", "长安", "深蓝", "极狐", "北汽", "奇瑞",
               "捷途", "星途", "吉利", "银河", "领克", "极氪", "五菱", "宝骏",
               "广汽埃安", "埃安", "AION", "东风风行", "东风", "奕派", "岚图",
               "小鹏", "MONA", "零跑", "理想", "蔚来", "乐道", "红旗", "荣威",
               "名爵", "MG", "哈弗", "魏牌", "欧拉", "ORA", "星海", "宝马", "BMW"]


def name_keys(s: str) -> set[str]:
    base = norm_name(s)
    out = {base}
    v = base
    for a, b in GREEK_ALIAS:
        v = v.replace(a, b)
    out.add(v)
    for suf in ("EV", "BEV", "纯电"):
        if v.endswith(suf) and len(v) > len(suf) + 1:
            out.add(v[:-len(suf)])
    for pre in BRAND_STRIP:
        if v.startswith(pre) and len(v) > len(pre) + 1:
            out.add(v[len(pre):])
    out = {x for x in out if len(x) >= 2}
    return out


def find_in(lib: dict, name: str) -> str | None:
    """在 lib 里找同名车，返回 lib 的 key。只接受：键完全相同 / 一方包含另一方
    且长度差 ≤2。包含匹配要求短键 ≥3 字符——否则「i6」会认亲「i60」（理想 vs 埃安）。"""
    ks = name_keys(name)
    for k in ks:
        if k in lib:
            return k
    libkeys = {}
    for lk in lib:
        for x in name_keys(lk):
            libkeys.setdefault(x, lk)
    for k in ks:
        if k in libkeys:
            return libkeys[k]
    for k in ks:
        if len(k) < 3:
            continue
        for lk, orig in libkeys.items():
            if (k in lk or lk in k) and abs(len(k) - len(lk)) <= 2 \
                    and min(len(k), len(lk)) >= 3:
                return orig
    return None


def guess_class(product_name: str, model_code: str, kerb: float | None,
                kwh: float | None = None) -> dict:
    """从产品名称 + 型号编码 + 整备质量推级别（近似官方二级分类）。

    型号编码（GB/T 9417）：企业代号在前（字母），之后第一位数字是车辆类别
    （7=轿车，6=客车/SUV/MPV），再两位是主要参数——6 类为车长（分米），
    7 类为排量（纯电记 00，用不上）。所以要先取**第一段数字**，不能看首位字符。

    轿车拿不到车长，只能用整备质量分段，误差大 → how 标 kerb_proxy，
    下游据此降置信度，并在有新闻尺寸时覆盖。
    """
    is_car = "轿车" in product_name
    m = re.search(r"\d{4,}", re.sub(r"[^0-9A-Za-z]", "", model_code or ""))
    d = m.group(0) if m else ""
    length = None
    if not is_car and len(d) >= 3 and d[0] == "6":
        try:
            length = int(d[1:3]) / 10.0     # 米
        except ValueError:
            length = None
    cls, how = None, "unknown"
    if is_car:
        if kerb:
            if kerb < 1300:
                cls = "小型车"
            elif kerb < 1520:
                cls = "紧凑型车"
            elif kerb < 1830:
                cls = "中型车"
            else:
                cls = "中大型车"
            how = "kerb_proxy"
        if length is None and len(d) >= 3 and d[0] == "7":
            # 7 类第二位起是排量，对纯电无意义；但部分厂把车长塞在这里，不用。
            pass
    else:
        if length is not None:
            if length >= 4.85:
                cls = "中大型SUV"
            elif length >= 4.55:
                cls = "中型SUV"
            elif length >= 4.3:
                cls = "紧凑型SUV"
            else:
                cls = "小型SUV"
            how = "model_code"
        elif kerb:
            cls = "中大型SUV" if kerb >= 2050 else "中型SUV"
            how = "kerb_proxy"
    # MPV 与 SUV 在产品名称里都叫「多用途乘用车」，靠车长 + 整备质量区分不可靠，
    # 只给提示：5.0 m 以上且整备 ≥ 2.4 t 的，多半是 MPV 或大六座。
    body = "轿车" if is_car else "SUV/MPV"
    mpv_risk = bool(not is_car and length and length >= 5.0 and (kerb or 0) >= 2300)
    return {"cls": cls, "len_m": length, "how": how, "body": body,
            "mpv_risk": mpv_risk}


def load_pool(path: str) -> list[dict]:
    d = json.load(open(path, encoding="utf-8"))
    return d.get("candidates", d)


def fetch_batch_rows(sess, pick: dict, cache: str) -> tuple[list[dict], str | None]:
    """取一批的乘用车行（含全部动力）。失败返回 ([], None)，由调用方决定跳过。"""
    atts = article_attachments(sess, pick["url"])
    got = pick_catalog(sess, atts, cache)
    if not got:
        return [], None
    doc_path, doc_url = got
    rows = parse_catalog(doc_text(doc_path))
    for r in rows:
        r["batch"] = pick["batch"]
        r["batch_date"] = pick["date"]
        r["source_url"] = pick["url"]
        r["doc_url"] = doc_url
    return rows, doc_url


def build_spec_lib(sess, items: list[dict], cache: str, n_batch: int,
                   out_path: str) -> dict:
    """累积近 N 批备案，建「车名 → 官方申报参数」库。

    单批只有本月新报的车型；要拿在售车当坐标系，就得把历史批次攒起来。
    每批大约 20-40 款乘用车，12 批足够覆盖当前在售主力。
    """
    pubs = [x for x in items if x["published"]][:n_batch]
    lib, seen = {}, 0
    for pick in pubs:
        rows, _ = fetch_batch_rows(sess, pick, cache)
        pax = [r for r in rows if r.get("cls_cat") == "乘用车"]
        seen += len(pax)
        print(f"  第{pick['batch']}批 · {pick['date']} · 乘用车 {len(pax)} 行")
        for r in pax:
            k = norm_name((r["names"] or [r["common_name"]])[0])
            if not k:
                continue
            g = lib.setdefault(k, {"name": (r["names"] or [r["common_name"]])[0],
                                   "enterprise": r["enterprise"],
                                   "power": r.get("power"),
                                   "product_name": r.get("product_name"),
                                   "batches": []})
            g["batches"].append(pick["batch"])
            for f in ("range_km", "batt_kwh", "kerb_kg"):
                v = r.get(f)
                if v is not None:
                    g[f] = max(g.get(f) or 0, v)
            gd = guess_class(r["product_name"], r["model_code"], r.get("kerb_kg"),
                             r.get("batt_kwh"))
            if gd.get("len_m") or not g.get("len_m"):
                g["len_m"] = gd.get("len_m") or g.get("len_m")
                g["cls"] = g.get("cls") or gd.get("cls")
                g["cls_how"] = gd.get("how")
        time.sleep(0.5)
    payload = {"generated": dt.datetime.now().isoformat(timespec="seconds"),
               "batches": [p["batch"] for p in pubs], "rows": seen,
               "models": lib}
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    json.dump(payload, open(out_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"[ok] {out_path}（{len(lib)} 款车，{len(pubs)} 批）")
    return payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, help="指定批次（默认最新已公告）")
    ap.add_argument("--list", action="store_true", help="只列近几批")
    ap.add_argument("--json", help="输出 JSON 路径")
    ap.add_argument("--md", help="输出 Markdown 路径")
    ap.add_argument("--cache", default=os.path.join(HERE, "runs", "_filings"),
                    help="附件缓存目录")
    ap.add_argument("--pool", default=os.path.join(HERE, "categories",
                                                   "baseline_car_ev.json"),
                    help="在售基准池（用于标「已上市/待上市」）")
    ap.add_argument("--new-only", action="store_true")
    ap.add_argument("--spec-lib", type=int, metavar="N",
                    help="累积近 N 批备案，建「车名→官方参数」库（在售车当坐标系用）")
    ap.add_argument("--spec-out", default=os.path.join(HERE, "runs", "_filings",
                                                       "specs.json"))
    ap.add_argument("--powers", default="BEV",
                    help="动力类型过滤，逗号分隔；BEV,PHEV,EREV,FCEV；all=全部")
    a = ap.parse_args()

    s = _session()
    items = list_batches(s)
    if a.list:
        print("| 批次 | 日期 | 状态 | 标题 |")
        print("|---|---|---|---|")
        for x in items[:10]:
            print(f"| {x['batch']} | {x['date']} | "
                  f"{'已公告' if x['published'] else '公示'} | {x['title'][:52]} |")
        return

    if a.spec_lib:
        build_spec_lib(s, items, a.cache, a.spec_lib, a.spec_out)
        return

    pick = pick_batch(items, a.batch)
    print(f"[批次] 第{pick['batch']}批 · {pick['date']} · "
          f"{'已公告' if pick['published'] else '公示稿'}")
    atts = article_attachments(s, pick["url"])
    print(f"[附件] {len(atts)} 个 doc")
    got = pick_catalog(s, atts, a.cache)
    if not got:
        raise SystemExit("没找到/没下到《减免车辆购置税的新能源汽车车型目录》附件")
    doc_path, doc_url = got
    print(f"[目录] {os.path.basename(doc_path)} ({os.path.getsize(doc_path)//1024} KB)")

    rows = parse_catalog(doc_text(doc_path))
    for r in rows:
        r["batch"] = pick["batch"]
        r["batch_date"] = pick["date"]
        r["source_url"] = pick["url"]
        r["doc_url"] = doc_url
    print(f"[解析] {len(rows)} 行（含客车/货车）")

    want = {p.strip().upper() for p in a.powers.split(",")}
    keep = [r for r in rows
            if (a.powers.lower() == "all" or (r.get("power") or "") in want)
            and r.get("cls_cat") == "乘用车"]
    for r in keep:
        r["guess"] = guess_class(r["product_name"], r["model_code"],
                                 r["kerb_kg"], r["batt_kwh"])
        r["name_key"] = norm_name((r["names"] or [r["common_name"]])[0])
    print(f"[过滤] 乘用车 {len(keep)} 行")

    # 与在售池对齐：名字归一后做包含匹配（备案名可能带版本后缀）
    pool_keys = {}
    for p in (a.pool, os.path.join(HERE, "levels.json")):
        if not os.path.exists(p):
            continue
        try:
            if p.endswith("levels.json"):
                for k in (json.load(open(p, encoding="utf-8")).get("levels") or {}):
                    pool_keys.setdefault(norm_name(k), k)
            else:
                for c in load_pool(p):
                    pool_keys[norm_name(c.get("model", ""))] = c.get("model")
        except Exception:
            pass
    for r in keep:
        hk = find_in(pool_keys, (r["names"] or [r["common_name"]])[0])
        hit = pool_keys.get(hk) if hk else None
        r["on_sale_as"] = hit
        r["status"] = "on_sale" if hit else "filed_new"

    new = [r for r in keep if r["status"] == "filed_new"]
    print(f"[对齐] 在售 {len(keep)-len(new)} 行 · 待上市/未收录 {len(new)} 行")
    outrows = new if a.new_only else keep

    # 同型号多配置合并（同一车通常报 2-3 个电量）
    grouped = {}
    for r in outrows:
        key = (r["name_key"], r["product_name"])
        g = grouped.setdefault(key, {
            "name": (r["names"] or [r["common_name"]])[0],
            "names": r["names"], "enterprise": r["enterprise"],
            "power": r["power"], "product_name": r["product_name"],
            "cls_guess": r["guess"], "model_codes": [], "variants": [],
            "status": r["status"], "on_sale_as": r["on_sale_as"], "batch": r["batch"],
        })
        g["model_codes"].append(r["model_code"])
        g["variants"].append({k: r.get(k) for k in
                              ("model_code", "range_km", "batt_kwh", "kerb_kg")})
        for f in ("range_km", "batt_kwh", "kerb_kg"):
            v = r.get(f)
            if v is not None:
                g[f] = max(g.get(f) or 0, v)
    merged = sorted(grouped.values(), key=lambda g: -(g.get("batt_kwh") or 0))

    # 叠加「新闻/主机厂透露」人工沉淀：车长、预售价、上市窗口、车身形式、技术点。
    # 备案表不给尺寸与价格，这两项恰恰决定能不能进价格带，只能靠检索补齐；
    # 补过的不再重复补，写回 notes 文件累积（每月增量）。
    notes_path = os.path.join(HERE, "categories", "filings_notes.json")
    notes = {}
    if os.path.exists(notes_path):
        try:
            notes = json.load(open(notes_path, encoding="utf-8")).get("models") or {}
        except Exception:
            notes = {}
    for g in merged:
        nt = notes.get(g["name"]) or notes.get(norm_name(g["name"])) or {}
        if nt:
            g["notes"] = nt
            for src, dst in (("len_m", "len_m"), ("price_expect", "price_expect"),
                             ("launch", "launch"), ("body", "body"),
                             ("tech", "tech"), ("range_official", "range_km")):
                if nt.get(src) is not None:
                    if dst in ("len_m", "range_km"):
                        g[dst] = nt[src]
                    else:
                        g[dst] = nt[src]
            if nt.get("len_m"):
                g["cls_guess"]["len_m"] = nt["len_m"]
                g["cls_guess"]["how"] = "news"
                if nt.get("cls"):
                    g["cls_guess"]["cls"] = nt["cls"]
            elif nt.get("cls"):
                g["cls_guess"]["cls"] = nt["cls"]
                g["cls_guess"]["how"] = "news"
            if nt.get("body"):
                g["cls_guess"]["body"] = nt["body"]
        g["notes_status"] = ("curated" if nt else "auto")
    have = sum(1 for g in merged if g["notes_status"] == "curated")
    print(f"[注释] 已沉淀 {have}/{len(merged)} 款（其余只有备案参数，置信度低）")

    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)) or ".", exist_ok=True)
        json.dump({"batch": pick["batch"], "date": pick["date"],
                   "source_url": pick["url"], "doc_url": doc_url,
                   "generated": dt.datetime.now().isoformat(timespec="seconds"),
                   "total_rows": len(rows), "pax_rows": len(keep),
                   "items": merged},
                  open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"[ok] {a.json}")

    L = [f"# 工信部备案 · 第{pick['batch']}批（{pick['date']}）", "",
         f"> 源：{pick['title']}", f"> 目录附件：{doc_url}", ""]
    if a.new_only:
        L.append(f"> 只列**未在基准池**的 {len(merged)} 款（即报备但尚未在榜的车型）。")
    L += ["", "| 车型 | 企业 | 产品名称 | 推定级别 | 续航(km) | 电池(kWh) | 整备(kg) | 型号 |",
          "|---|---|---|---|---|---|---|---|"]
    for g in merged:
        gd = g["cls_guess"]
        L.append(f"| **{g['name']}** | {g['enterprise']} | {g['product_name']} | "
                 f"{gd['cls'] or '?'}"
                 f"{'（车长≈%.1fm）' % gd['len_m'] if gd.get('len_m') else ''} | "
                 f"{g.get('range_km') or '—'} | {g.get('batt_kwh') or '—'} | "
                 f"{g.get('kerb_kg') or '—'} | {', '.join(g['model_codes'][:2])} |")
    cur = [g for g in merged if g.get("notes")]
    if cur:
        L += ["", "## 已补新闻/官方口径（价格、上市窗口、车身形式）", "",
              "| 车型 | 预期价格 | 上市窗口 | 车身 | 关键点 | 来源 |",
              "|---|---|---|---|---|---|"]
        for g in cur:
            n = g["notes"]
            pe = n.get("price_expect")
            L.append(f"| **{g['name']}** | "
                     f"{'%.1f–%.1f 万' % (pe[0], pe[1]) if isinstance(pe, list) and len(pe) == 2 else (pe or '—')} | "
                     f"{n.get('launch') or '—'} | {n.get('body') or '—'} | "
                     f"{n.get('tech') or '—'} | {n.get('src') or '—'} |")
    L += ["", "> 级别由「产品名称 + 型号编码车长 + 整备质量」推定，属**预先判断**，"
              "上市后以官方二级分类为准；价格未申报，需另行预估。", ""]
    if a.md:
        open(a.md, "w", encoding="utf-8").write("\n".join(L))
        print(f"[ok] {a.md}")
    else:
        print("\n".join(L))


if __name__ == "__main__":
    main()
