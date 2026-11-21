"""多方法稳健性对比（robust.py）——纯标准库，无 numpy / pyDecision 依赖。

为什么不用 pyDecision：它把 OpenAI 与 Google GenAI SDK 列为强制依赖，即使只跑 AHP
也会被装上；而 40 余种方法里本流程只需要 9 种。自己实现的好处是公式可逐行审计，
代价是需要做自检（见 selftest）。

提供的 10 种聚合方法（均支持 benefit / cost 两类准则）：
  saw       加权和（线性极大归一）
  waspas    加权和 + 加权积（λ=0.5）
  topsis    距理想解/负理想解
  vikor     折衷解（v=0.5，最小遗憾）
  mabac     距边界近似域
  moora     比值系统（向量归一）
  edas      基于平均解
  promethee PROMETHEE II 净流（线性偏好函数）
  gra       灰色关联度
  spotis    稳定偏好序（Dezert 等，pyDecision 收录；专为抑制秩反转设计）

外加 AHP 权重生成（特征向量法 + 一致性比率 CR），用于检验「换一套权威权重，排序是否变化」。

核心用法：
    from modules.engine.robust import run_robust
    res = run_robust(matrix, weights, types, names)
    print(res["verdict"])        # 前 3 名跨 9 方法的稳定性判定
"""

import itertools
import json
import math

EPS = 1e-12

METHODS = ["saw", "waspas", "topsis", "vikor", "mabac", "moora", "edas",
           "promethee", "gra", "spotis"]

WEIGHT_SCHEMES = ["base", "ahp", "省心", "智驾", "空间", "预算"]

RI = {1: 0.0, 2: 0.0, 3: 0.58, 4: 0.90, 5: 1.12, 6: 1.24, 7: 1.32,
      8: 1.41, 9: 1.45, 10: 1.49}


# ---------------------------------------------------------------- 基础工具
def _col(m, j):
    return [row[j] for row in m]


def _best_worst(matrix, types):
    best, worst = [], []
    for j, t in enumerate(types):
        c = _col(matrix, j)
        if t == "cost":
            best.append(min(c)); worst.append(max(c))
        else:
            best.append(max(c)); worst.append(min(c))
    return best, worst


def _safe_div(a, b):
    return a / b if abs(b) > EPS else 0.0


def _ranks(scores):
    """分数越高名次越前；同分取平均名次（竞赛名次）。返回 1-based 名次数组。"""
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    ranks = [0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and abs(scores[order[j + 1]] - scores[order[i]]) < 1e-9:
            j += 1
        avg = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


# ---------------------------------------------------------------- 9 种方法
def _norm_linear(matrix, types):
    """线性极大归一：benefit x/max，cost min/x。"""
    best, _ = _best_worst(matrix, types)
    out = []
    for row in matrix:
        nr = []
        for j, t in enumerate(types):
            if t == "cost":
                nr.append(_safe_div(min(_col(matrix, j)), row[j]) or EPS)
            else:
                nr.append(_safe_div(row[j], best[j]))
        out.append(nr)
    return out


def _norm_vector(matrix, types):
    out = []
    denom = [math.sqrt(sum(v * v for v in _col(matrix, j))) or EPS
             for j in range(len(types))]
    for row in matrix:
        out.append([_safe_div(row[j], denom[j]) for j in range(len(types))])
    return out


def m_saw(matrix, w, types):
    n = _norm_linear(matrix, types)
    return [sum(w[j] * r[j] for j in range(len(w))) for r in n]


def m_waspas(matrix, w, types, lam=0.5):
    n = _norm_linear(matrix, types)
    out = []
    for r in n:
        s = sum(w[j] * r[j] for j in range(len(w)))
        p = 1.0
        for j in range(len(w)):
            p *= max(r[j], EPS) ** w[j]
        out.append(lam * s + (1 - lam) * p)
    return out


def m_topsis(matrix, w, types):
    n = _norm_vector(matrix, types)
    best, worst = _best_worst(n, types)
    out = []
    for r in n:
        dp = math.sqrt(sum(w[j] ** 2 * (r[j] - best[j]) ** 2 for j in range(len(w))))
        dm = math.sqrt(sum(w[j] ** 2 * (r[j] - worst[j]) ** 2 for j in range(len(w))))
        out.append(_safe_div(dm, dp + dm))
    return out


def m_vikor(matrix, w, types, v=0.5):
    best, worst = _best_worst(matrix, types)
    S, R = [], []
    for row in matrix:
        terms = []
        for j, t in enumerate(types):
            span = abs(best[j] - worst[j])
            d = abs(row[j] - best[j])
            terms.append(w[j] * (_safe_div(d, span)))
        S.append(sum(terms))
        R.append(max(terms) if terms else 0.0)
    s_star, s_minus = min(S), max(S)
    r_star, r_minus = min(R), max(R)
    Q = []
    for i in range(len(matrix)):
        a = _safe_div(S[i] - s_star, s_minus - s_star)
        b = _safe_div(R[i] - r_star, r_minus - r_star)
        Q.append(v * a + (1 - v) * b)
    # Q 越小越好 → 取负，统一为「越大越好」
    return [-q for q in Q]


def m_mabac(matrix, w, types):
    n = []
    best, worst = _best_worst(matrix, types)
    for row in matrix:
        nr = []
        for j in range(len(w)):
            span = abs(best[j] - worst[j]) or EPS
            nr.append((row[j] - worst[j]) / span if types[j] != "cost"
                      else (best[j] - row[j]) / span)
        n.append(nr)
    V = [[w[j] * (n[i][j] + 1) for j in range(len(w))] for i in range(len(n))]
    G = []
    for j in range(len(w)):
        prod = 1.0
        for i in range(len(V)):
            prod *= max(V[i][j], EPS)
        G.append(prod ** (1.0 / len(V)))
    return [sum(V[i][j] - G[j] for j in range(len(w))) for i in range(len(V))]


def m_moora(matrix, w, types):
    n = _norm_vector(matrix, types)
    out = []
    for r in n:
        v = 0.0
        for j, t in enumerate(types):
            v += (-w[j] * r[j]) if t == "cost" else (w[j] * r[j])
        out.append(v)
    return out


def m_edas(matrix, w, types):
    m, n_c = len(matrix), len(w)
    AV = [sum(_col(matrix, j)) / m for j in range(n_c)]
    SP = [0.0] * m
    SN = [0.0] * m
    for i in range(m):
        for j in range(n_c):
            d = matrix[i][j] - AV[j]
            if types[j] == "cost":
                d = -d
            pda = max(0.0, d) / (abs(AV[j]) or EPS)
            nda = max(0.0, -d) / (abs(AV[j]) or EPS)
            SP[i] += w[j] * pda
            SN[i] += w[j] * nda
    mx_sp, mx_sn = max(SP) or EPS, max(SN) or EPS
    nsp = [s / mx_sp for s in SP]
    nsn = [1 - s / mx_sn for s in SN]
    return [0.5 * (nsp[i] + nsn[i]) for i in range(m)]


def m_promethee(matrix, w, types, q_ratio=0.1, p_ratio=0.5):
    m, n_c = len(matrix), len(w)
    best, worst = _best_worst(matrix, types)
    q, p = [], []
    for j in range(n_c):
        span = abs(best[j] - worst[j]) or 1.0
        q.append(q_ratio * span)
        p.append(p_ratio * span)
    plus = [0.0] * m
    minus = [0.0] * m
    for i in range(m):
        for k in range(m):
            if i == k:
                continue
            pi = 0.0
            for j in range(n_c):
                d = matrix[i][j] - matrix[k][j]
                if types[j] == "cost":
                    d = -d
                if d <= q[j]:
                    pref = 0.0
                elif d >= p[j]:
                    pref = 1.0
                else:
                    pref = (d - q[j]) / (p[j] - q[j])
                pi += w[j] * pref
            plus[i] += pi
            minus[k] += pi
    return [plus[i] - minus[i] for i in range(m)]


def m_gra(matrix, w, types, zeta=0.5):
    best, worst = _best_worst(matrix, types)
    norm = []
    for row in matrix:
        nr = []
        for j in range(len(w)):
            span = abs(best[j] - worst[j]) or EPS
            nr.append((row[j] - worst[j]) / span if types[j] != "cost"
                      else (best[j] - row[j]) / span)
        norm.append(nr)
    delta = [[1.0 - norm[i][j] for j in range(len(w))] for i in range(len(matrix))]
    flat = [d for row in delta for d in row]
    dmin, dmax = min(flat), max(flat)
    out = []
    for i in range(len(matrix)):
        out.append(sum(w[j] * (dmin + zeta * dmax) / (delta[i][j] + zeta * dmax + EPS)
                       for j in range(len(w))))
    return out


def m_spotis(matrix, w, types):
    """SPOTIS：把每个值映射为「到理想点的归一化距离」，越小越好。

    与 TOPSIS 的关键差别：SPOTIS 用**固定的准则边界**（而非样本内的最优/最劣）算距离，
    所以增删候选不会改变已有候选的得分——这正是它抗秩反转的原因。
    边界取 0–10（本流程的评分域）；若数据越界则退回观测极值。
    """
    flat = [v for row in matrix for v in row]
    if flat and min(flat) >= 0 and max(flat) <= 10:
        lb, ub = 0.0, 10.0
    else:
        lb, ub = (min(flat), max(flat)) if flat else (0.0, 1.0)
    span = (ub - lb) or EPS
    ideal = []
    for j, t in enumerate(types):
        ideal.append(lb if t == "cost" else ub)
    out = []
    for row in matrix:
        d = 0.0
        for j in range(len(w)):
            d += w[j] * (abs(row[j] - ideal[j]) / span)
        out.append(-d)          # 距离越小越好 → 取负统一为越大越好
    return out


RUNNERS = {"spotis": m_spotis, "saw": m_saw, "waspas": m_waspas, "topsis": m_topsis, "vikor": m_vikor,
           "mabac": m_mabac, "moora": m_moora, "edas": m_edas,
           "promethee": m_promethee, "gra": m_gra}

# v9.7.43：算法家族（按实测 Kendall τ≥0.8 聚类，2026-11-20 在纯电中型车池验证）。
# 为什么不按10种算法直接计票：saw/moora/waspas/spotis/edas 之间 τ=0.94~1.00，
# 是同一类算法被数了5次；MG07 曾"6/10命中"但6票全来自抱团组，虚高。
# 家族去重后每家族算1票，命中率=进前top_k的家族数/总家族数，更真实反映独立支持。
# 分组依据：加权和/比值系(saw系)、向量距离(topsis)、折衷(vikor)、边界(mabac)、
#           偏好流(promethee)、灰色关联(gra)——覆盖不同数学原理。
METHOD_FAMILIES = {
    "weighted_sum": ["saw", "moora", "waspas", "spotis", "edas"],
    "distance": ["topsis"],
    "compromise": ["vikor"],
    "boundary": ["mabac"],
    "preference_flow": ["promethee"],
    "grey_relational": ["gra"],
}
FAMILY_OF = {m: f for f, ms in METHOD_FAMILIES.items() for m in ms}

METHOD_LABEL = {"saw": "SAW 加权和", "waspas": "WASPAS 和+积", "topsis": "TOPSIS 理想解",
                "vikor": "VIKOR 折衷", "mabac": "MABAC 边界距离",
                "moora": "MOORA 比值", "edas": "EDAS 平均解",
                "promethee": "PROMETHEE II 净流", "gra": "GRA 灰色关联",
                "spotis": "SPOTIS 稳定偏好序"}


# ---------------------------------------------------------------- AHP
def ahp_weights(pairwise):
    """判断矩阵 → 权重（特征向量法）+ 一致性比率 CR。"""
    n = len(pairwise)
    v = [1.0 / n] * n
    lam = n
    for _ in range(200):
        wv = [sum(pairwise[i][j] * v[j] for j in range(n)) for i in range(n)]
        s = sum(wv)
        if s <= EPS:
            break
        nv = [x / s for x in wv]
        lam = sum(wv) / max(sum(v), EPS)
        if max(abs(nv[i] - v[i]) for i in range(n)) < 1e-12:
            v = nv
            break
        v = nv
    ci = (lam - n) / (n - 1) if n > 1 else 0.0
    cr = ci / RI.get(n, 1.49) if n > 1 and RI.get(n, 1.49) > 0 else 0.0
    return {"weights": v, "lambda_max": lam, "ci": ci, "cr": cr}


def ratio_matrix(weights):
    """由权重直接构造完全一致的判断矩阵（CR=0），用于回推校验。"""
    return [[weights[i] / weights[j] for j in range(len(weights))]
            for i in range(len(weights))]


# ---------------------------------------------------------------- 权重情景
def tilt_weights(base_w, sliders, slider_specs, gain=1.0):
    """滑杆 0–100 映射到权重倾斜：50=不动，0=该组维度权重清零，100=翻倍。"""
    w = list(base_w)
    for name, val in (sliders or {}).items():
        spec = (slider_specs or {}).get(name)
        if not spec:
            continue
        k = (float(val) - 50.0) / 50.0
        for dk in spec.get("dims", []):
            for i, d in enumerate(spec.get("_order", [])):
                if d == dk:
                    w[i] = max(w[i] * (1 + gain * k), 0.0)
    s = sum(w)
    return [x / s for x in w] if s > 0 else base_w



# ---------------------------------------------------------------- 客观赋权
def _colmean(m, j):
    col = _col(m, j)
    return sum(col) / len(col)


def _std(xs):
    if len(xs) < 2:
        return 0.0
    mu = sum(xs) / len(xs)
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / (len(xs) - 1))


def _pearson(a, b):
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    num = sum((a[i] - ma) * (b[i] - mb) for i in range(len(a)))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((x - mb) ** 2 for x in b))
    return num / (da * db) if da > EPS and db > EPS else 0.0


def _minmax(matrix, types):
    """统一到正区间（≥0），供熵权/CRITIC/MEREC 使用。"""
    best, worst = _best_worst(matrix, types)
    out = []
    for row in matrix:
        nr = []
        for j in range(len(types)):
            span = abs(best[j] - worst[j]) or EPS
            nr.append((row[j] - worst[j]) / span if types[j] != "cost"
                      else (best[j] - row[j]) / span)
        out.append(nr)
    # 平移到严格正，避免 ln(0)
    mn = min(v for r in out for v in r)
    if mn <= 1e-6:
        out = [[v + 1e-6 for v in r] for r in out]
    return out


def obj_entropy(matrix, types):
    """熵权法：离散度越大的准则，信息量越大，权重越高。"""
    n = _minmax(matrix, types)
    m, nc = len(n), len(types)
    k = 1.0 / math.log(m) if m > 1 else 1.0
    w = []
    for j in range(nc):
        col = _col(n, j)
        s = sum(col)
        if s <= EPS:
            w.append(0.0)
            continue
        p = [c / s for c in col]
        e = -k * sum(pi * math.log(pi) for pi in p if pi > 0)
        w.append(max(0.0, 1.0 - e))
    tot = sum(w)
    return [x / tot for x in w] if tot > 0 else [1.0 / nc] * nc


def obj_critic(matrix, types):
    """CRITIC：对比强度（标准差）× 冲突性（与其它准则的相关性）。"""
    n = _minmax(matrix, types)
    nc = len(types)
    sig = [_std(_col(n, j)) for j in range(nc)]
    w = []
    for j in range(nc):
        conflict = 0.0
        for k in range(nc):
            if k != j:
                conflict += 1.0 - _pearson(_col(n, j), _col(n, k))
        w.append(sig[j] * conflict)
    tot = sum(w)
    return [x / tot for x in w] if tot > 0 else [1.0 / nc] * nc


def obj_merec(matrix, types):
    """MEREC：逐个移除某准则，看整体表现变化多大；变化越大越重要。"""
    n = _minmax(matrix, types)
    m, nc = len(n), len(types)
    eq = 1.0 / nc

    def perf(weights):
        out = []
        for row in n:
            out.append(math.log(1.0 + sum(weights[j] * abs(math.log(max(row[j], EPS)))
                                          for j in range(nc))))
        return out

    base = perf([eq] * nc)
    eff = []
    for j in range(nc):
        wj = [eq] * nc
        wj[j] = 0.0
        # 移除后其余准则按等权重新归一，保持与基准可比
        rest = sum(wj) or 1.0
        wj = [x / rest for x in wj]
        alt = perf(wj)
        eff.append(sum(abs(alt[i] - base[i]) for i in range(m)))
    tot = sum(eff)
    return [x / tot for x in eff] if tot > 0 else [eq] * nc


def obj_sd(matrix, types):
    """标准差法：只看离散度，不看相关性。"""
    n = _minmax(matrix, types)
    nc = len(types)
    w = [_std(_col(n, j)) for j in range(nc)]
    tot = sum(w)
    return [x / tot for x in w] if tot > 0 else [1.0 / nc] * nc


OBJECTIVE = {"熵权法": obj_entropy, "CRITIC": obj_critic, "MEREC": obj_merec,
             "标准差法": obj_sd}


def objective_weights(matrix, types):
    return {name: fn(matrix, types) for name, fn in OBJECTIVE.items()}


# ---------------------------------------------------------------- 比较工具
def weight_similarity(a, b):
    """两组权重的相似度：余弦 + 排序一致性 τ。"""
    na = math.sqrt(sum(x * x for x in a)) or EPS
    nb = math.sqrt(sum(x * x for x in b)) or EPS
    cos = sum(a[i] * b[i] for i in range(len(a))) / (na * nb)
    ta = _ranks(list(a))
    tb = _ranks(list(b))
    return {"cosine": cos, "tau": kendall_tau(ta, tb),
            "l1": sum(abs(a[i] - b[i]) for i in range(len(a)))}


def kendall_matrix(rankings: dict):
    """多份排序 → 两两 Kendall τ 矩阵。"""
    keys = list(rankings)
    mat = {}
    for a in keys:
        mat[a] = {}
        for b in keys:
            mat[a][b] = kendall_tau(rankings[a], rankings[b])
    vals = [mat[a][b] for i, a in enumerate(keys) for b in keys[i + 1:]]
    return {"matrix": mat, "keys": keys,
            "mean": sum(vals) / len(vals) if vals else 1.0,
            "min": min(vals) if vals else 1.0}


# ---------------------------------------------------------------- 归一化敏感性审计（v9.7.39 借用 scikit-criteria）
# 借鉴 scikit-criteria 的「方法学空间穷举」思想（arXiv:2509.24996）：归一化方式的选择
# 会影响 20%–40% 的最终名次。原 robust.py 只换聚合/权重，归一化写死在各方法内部，
# 看不到「同一份数据换归一化方式会不会换位」。这里把归一化作为独立变量做敏感性审计。
# 纯标准库实现，不引入 numpy/scikit-criteria 依赖（与主链路零依赖原则一致）。
NORMALIZATIONS = ["linear", "minmax", "vector", "zscore", "sum"]


def _norm_minmax(matrix, types):
    """极值归一到 [0,1]：benefit (x-min)/(max-min)，cost 取反。"""
    best, worst = _best_worst(matrix, types)
    out = []
    for row in matrix:
        nr = []
        for j, t in enumerate(types):
            span = abs(best[j] - worst[j]) or EPS
            v = (row[j] - worst[j]) / span if t != "cost" else (best[j] - row[j]) / span
            nr.append(v)
        out.append(nr)
    return out


def _norm_zscore(matrix, types):
    """z-score 归一（(x-μ)/σ，cost 取反），平移到正区间便于加权和。"""
    n = len(matrix)
    if n == 0:
        return []
    nc = len(types)
    cols = [_col(matrix, j) for j in range(nc)]
    mu = [sum(c) / len(c) for c in cols]
    sd = [_std(c) or EPS for c in cols]
    raw = []
    for row in matrix:
        z = [(row[j] - mu[j]) / sd[j] * (1 if types[j] != "cost" else -1)
             for j in range(nc)]
        raw.append(z)
    mn = min(v for r in raw for v in r)
    shift = (1e-6 - mn) if mn < 0 else 0.0
    return [[v + shift for v in r] for r in raw]


def _norm_sum(matrix, types):
    """占比归一：benefit x/Σx，cost (1/x)/Σ(1/x)。"""
    nc = len(types)
    cols = [_col(matrix, j) for j in range(nc)]
    out = [[0.0] * nc for _ in matrix]
    for j, t in enumerate(types):
        if t == "cost":
            inv = [1.0 / max(abs(v), EPS) for v in cols[j]]
            s = sum(inv) or EPS
            vals = [v / s for v in inv]
        else:
            pos = [max(v, 0.0) for v in cols[j]]
            s = sum(pos) or EPS
            vals = [v / s for v in pos]
        for i, v in enumerate(vals):
            out[i][j] = v
    return out


def normalize_matrix(matrix, types, method="linear"):
    """按指定方式归一到「越大越好」的可比区间。linear/vector 复用既有实现。"""
    if method == "linear":
        return _norm_linear(matrix, types)
    if method == "vector":
        return _norm_vector(matrix, types)
    if method == "minmax":
        return _norm_minmax(matrix, types)
    if method == "zscore":
        return _norm_zscore(matrix, types)
    if method == "sum":
        return _norm_sum(matrix, types)
    raise ValueError("未知归一化方式: %s" % method)


def normalization_audit(matrix, weights, types, names,
                        normalizations=None, top_k=3, scheme="saw"):
    """归一化敏感性审计：固定权重与聚合，只换归一化，看名次是否翻转。

    固定聚合（默认 SAW=加权和）是为了把「归一化」隔离成唯一变量：
    这样看到的名次变化只能来自归一化方式，而不是聚合方法差异。
    scheme 可选 saw / topsis：topsis 内部自带 vector 归一，故只对 saw 严格隔离。

    返回每个候选跨归一化的名次范围、两两 τ、以及人类可读报告。
    """
    norms = normalizations or NORMALIZATIONS
    rankings, scores_by_norm = {}, {}
    for nm in norms:
        nmat = normalize_matrix(matrix, types, nm)
        if scheme == "topsis":
            sc = m_topsis(nmat, weights, types)
        else:
            sc = [sum(weights[j] * r[j] for j in range(len(weights))) for r in nmat]
        scores_by_norm[nm] = sc
        rankings[nm] = _ranks(sc)
    km = kendall_matrix(rankings)
    per_alt = {}
    for i, name in enumerate(names):
        rs = [rankings[nm][i] for nm in norms]
        per_alt[name] = {"ranks": {nm: rankings[nm][i] for nm in norms},
                         "min": min(rs), "max": max(rs), "range": max(rs) - min(rs)}
    unstable = sorted(names, key=lambda x: (-per_alt[x]["range"], x))
    if km["mean"] >= 0.95:
        verdict = "归一化方式对名次影响很小（τ≥0.95），排名在此维度稳健。"
    elif km["mean"] >= 0.85:
        verdict = "归一化方式对名次有中等影响（τ 0.85–0.95），建议核对前三名。"
    else:
        verdict = "名次对归一化方式敏感（τ<0.85），**不能把名次当确定结论**，须结合三档分级使用。"
    lines = [
        "### 归一化敏感性审计（%d 种归一化 × %s，纯标准库）" % (len(norms), scheme),
        "",
        "- 两两 Kendall τ 均值 **%.3f**、最低 **%.3f**" % (km["mean"], km["min"]),
        "- %s" % verdict,
        "",
        "| 归一化 | " + " | ".join(names) + " |",
        "|---|" + "---|" * len(names),
    ]
    for nm in norms:
        lines.append("| %s | " % nm + " | ".join(str(int(rankings[nm][i])) for i in range(len(names))) + " |")
    lines += ["", "名次波动最大的候选："]
    for name in unstable[:5]:
        p = per_alt[name]
        lines.append("- %s：%d–%d 名（跨 %d 位）" % (name, p["min"], p["max"], p["range"]))
    return {"normalizations": norms, "scheme": scheme, "rankings": rankings,
            "scores": scores_by_norm, "tau_matrix": km["matrix"],
            "tau_mean": km["mean"], "tau_min": km["min"], "per_alt": per_alt,
            "unstable_top": unstable[:5], "verdict": verdict,
            "report": "\n".join(lines)}


# ---------------------------------------------------------------- 名次置信区间
def bootstrap_rank_ci(matrix, w, types, names, n_boot: int = 1000,
                      noise: float = 0.5, seed: int = 7, method: str = "saw"):
    """对评分矩阵加扰动重采样，估计每个候选名次的 95% 区间。

    为什么要它：原来用「相邻分差 <1 分」判同档，这个 1 分是拍的。
    真实的问题是「这个名次有多不确定」，而评分本身的误差（人工打分、配置换算）
    完全可以用 bootstrap 表达出来：给每格加 ±noise 的均匀扰动，重排 1000 次，
    看某个候选的名次分布有多宽。区间重叠 = 名次不可辨。

    noise 取 0.5 分是保守设定（0–10 量表上的 5%），代表「同一台车，换个人打分
    可能差半档」。它不是统计意义上的采样误差，而是评分不确定性的代理。
    """
    import random
    rnd = random.Random(seed)
    fn = RUNNERS.get(method, m_saw)
    n, d = len(names), len(w)
    hist = [[] for _ in range(n)]
    for _ in range(n_boot):
        sm = [[min(10.0, max(0.0, v + rnd.uniform(-noise, noise))) for v in row]
              for row in matrix]
        rk = _ranks(fn(sm, w, types))
        for i in range(n):
            hist[i].append(rk[i])
    out = {}
    for i, nm in enumerate(names):
        rs = sorted(hist[i])
        out[nm] = {"median": rs[len(rs) // 2],
                   "p05": rs[max(0, int(round(len(rs) * 0.025)) - 1)],
                   "p95": rs[min(len(rs) - 1, int(round(len(rs) * 0.975)))],
                   "mean": sum(rs) / len(rs),
                   "width": rs[min(len(rs) - 1, int(round(len(rs) * 0.975)))]
                            - rs[max(0, int(round(len(rs) * 0.025)) - 1)]}
    return out


def overlap(a, b) -> bool:
    """两个名次区间是否重叠（重叠即不可分优劣）。"""
    return a["p05"] <= b["p95"] and b["p05"] <= a["p95"]


# ---------------------------------------------------------------- 稳定性度量
def kendall_tau(a, b):
    """名次数组间的 Kendall τ（同序=1，反序=-1）。"""
    n = len(a)
    if n < 2:
        return 1.0
    conc = disc = 0
    for i, j in itertools.combinations(range(n), 2):
        s = (a[i] - a[j]) * (b[i] - b[j])
        if s > 0:
            conc += 1
        elif s < 0:
            disc += 1
    tot = conc + disc
    return (conc - disc) / tot if tot else 0.0


def run_robust(matrix, weights, types, names, top_k=3,
               scheme_weights=None, stable_frac=0.85):
    """跑 9 种方法 × 若干权重情景，分三层报告稳定性。

    把「算法差异」和「权重差异」分开，是因为它们回答的是不同问题：
      方法稳定性 = 同一套权重下换算法，名次变不变（考验矩阵分辨力）
      权重稳定性 = 同一套算法下换权重，名次变不变（考验结论对价值判断的敏感度）
    混在一起算总频率，会把两个原因叠成一个数字，看不出该改哪里。
    """
    schemes = scheme_weights or {"base": list(weights)}
    first_scheme = list(schemes)[0]
    runs = []
    for sname, w in schemes.items():
        for mkey in METHODS:
            try:
                sc = RUNNERS[mkey](matrix, w, types)
            except Exception as e:
                runs.append({"scheme": sname, "method": mkey, "error": str(e)})
                continue
            runs.append({"scheme": sname, "method": mkey, "scores": sc,
                         "ranks": _ranks(sc)})

    ok = [r for r in runs if "ranks" in r]
    n_alt = len(names)
    total = len(ok) or 1

    def _freq(rs):
        f = {nm: 0 for nm in names}
        hist = {nm: [] for nm in names}
        for r in rs:
            for i, nm in enumerate(names):
                hist[nm].append(r["ranks"][i])
                if r["ranks"][i] <= top_k:
                    f[nm] += 1
        return f, hist

    base_runs = [r for r in ok if r["scheme"] == first_scheme]
    meth_freq, meth_hist = _freq(base_runs)
    n_meth = len(base_runs) or 1

    joint_freq, joint_hist = _freq(ok)

    # v9.7.43：家族去重命中率——同家族算法只算1票（防相关算法重复计数虚高）。
    _fam_hits = {nm: set() for nm in names}
    for r in base_runs:
        if "ranks" not in r:
            continue
        fam = FAMILY_OF.get(r.get("method"))
        if not fam:
            continue
        for i, nm in enumerate(names):
            if r["ranks"][i] <= top_k:
                _fam_hits[nm].add(fam)
    n_fam = len(METHOD_FAMILIES)

    # v9.7.48：连续名次指标（补二值"进前top_k"截断的信息损失）。
    # Model3 曾"家族命中0%"但其实是稳定第5、距第3名仅0.009分——
    # 硬截断把它和"排第20"混为一谈。平均名次+区间能区分"稳但靠后"与"不稳"。
    _base_pos = {nm: [] for nm in names}
    for r in base_runs:
        if "ranks" not in r:
            continue
        for i, nm in enumerate(names):
            _base_pos[nm].append(r["ranks"][i])

    def _verdict(freq, n, strict=1.0, good=0.85, mid=0.5):
        r = freq / n if n else 0
        if r >= strict:
            return "稳定"
        if r >= good:
            return "较稳"
        if r >= mid:
            return "脆弱"
        return "不进前%d" % top_k

    per_alt = {}
    for nm in names:
        mh = sorted(meth_hist[nm]); jh = sorted(joint_hist[nm])
        per_alt[nm] = {
            "method_freq": meth_freq[nm] / n_meth,
            "method_hits": meth_freq[nm],
            "method_n": n_meth,
            "method_verdict": _verdict(meth_freq[nm], n_meth),
            # v9.7.43：家族去重命中（每家族1票，防抱团算法虚高）
            "family_freq": len(_fam_hits[nm]) / n_fam if n_fam else 0,
            "family_hits": len(_fam_hits[nm]),
            "family_n": n_fam,
            "family_names": sorted(_fam_hits[nm]),
            # v9.7.48：连续名次（基准权重跨算法），补二值命中截断的盲区
            "rank_mean": (sum(_base_pos[nm]) / len(_base_pos[nm])) if _base_pos[nm] else None,
            "rank_median": (sorted(_base_pos[nm])[len(_base_pos[nm]) // 2]) if _base_pos[nm] else None,
            "rank_span": (min(_base_pos[nm]), max(_base_pos[nm])) if _base_pos[nm] else None,
            "method_range": (mh[0], mh[-1]) if mh else (0, 0),
            "method_median": mh[len(mh) // 2] if mh else 0,
            "topk_freq": joint_freq[nm] / total,
            "topk_count": joint_freq[nm],
            "rank_min": jh[0] if jh else 0,
            "rank_max": jh[-1] if jh else 0,
            "rank_med": jh[len(jh) // 2] if jh else 0,
            "verdict": _verdict(joint_freq[nm], total),
        }

    # 各权重情景的前 top_k（9 方法平均名次）
    scheme_top = {}
    for sname in schemes:
        rs = [r for r in ok if r["scheme"] == sname]
        if not rs:
            continue
        avg = [sum(r["ranks"][i] for r in rs) / len(rs) for i in range(n_alt)]
        idx = sorted(range(n_alt), key=lambda i: avg[i])[:top_k]
        scheme_top[sname] = [names[i] for i in idx]

    base_top = set(scheme_top.get(first_scheme, []))

    per_method = {}
    for mkey in METHODS:
        rs = [r for r in ok if r["method"] == mkey]
        if not rs:
            continue
        avg = [sum(r["ranks"][i] for r in rs) / len(rs) for i in range(n_alt)]
        per_method[mkey] = {"avg_rank": avg, "n_schemes": len(rs)}

    base_by_method = {r["method"]: r["ranks"] for r in base_runs}
    keys = [m for m in METHODS if m in base_by_method]
    taus = [kendall_tau(base_by_method[a], base_by_method[b])
            for a, b in itertools.combinations(keys, 2)]
    mean_tau = sum(taus) / len(taus) if taus else 1.0
    tau_mat = {a: {b: kendall_tau(base_by_method[a], base_by_method[b]) for b in keys}
               for a in keys}

    order_m = sorted(names, key=lambda n: (-per_alt[n]["method_hits"],
                                          per_alt[n]["method_median"]))
    report = _build_report(names, order_m, per_alt, per_method, scheme_top,
                          base_top, mean_tau, top_k, len(schemes), total, n_meth,
                          tau_mat)
    return {"runs": runs, "per_method": per_method, "per_alt": per_alt,
            "scheme_top": scheme_top, "kendall_mean": mean_tau,
            "n_runs": total, "n_schemes": len(schemes), "n_methods": n_meth,
            "tau_matrix": tau_mat, "top_k": top_k, "report": report}


def _build_report(names, order, per_alt, per_method, scheme_top, base_top,
                  mean_tau, top_k, n_schemes, n_runs, n_meth, tau_mat=None):
    L = [f"# 多方法稳健性对比（{len(METHODS)} 种聚合 × {n_schemes} 套权重 = {n_runs} 次排序）", ""]
    L.append("> 目的不是再给一个名次，而是回答「这个名次换一种算法会不会变」。")
    L.append("> 顺序变了，说明原结论是算法的偏好；顺序不变，才算产品本身的差异。")
    L.append("")
    # v9.7.48：口径边界——排名相关性≠正确性，产品力榜≠市场热度榜（业内共识）
    L.append("> **口径边界（务必先读）**：本节只检验「同一份评分矩阵下换算法名次变不变」，"
             "**不检验排名是否符合真实市场**。业内共识（DecisionMind/Sałabun）："
             "算法间高度相关只说明它们共享同一套偏见，不代表结论正确——"
             "「哪个对」必须由数据与方法本身判断。本榜 8 维衡量的是**产品力**，"
             "**不是市场热度**：品牌力、超充网络、保值预期、售后覆盖等真实购车因子"
             "未单列计分。因此可能出现「产品力排第5、但全球销量第一」（如 Model 3）——"
             "这不是算法错，也不是市场错，是二者回答的问题不同。"
             "买车时请把本榜当作「产品力筛选」，再叠加你个人的品牌/充电/保值偏好。")
    L.append("")

    L.append(f"## 一、方法稳定性（同一套基准权重，{len(METHODS)} 种聚合算法）")
    L.append("")
    # v9.7.48：加"平均名次"连续指标——二值"进前top_k"会埋没"稳稳第5"的车
    # （Model3 10算法全是第5、距第3名仅0.009分，命中0%但并非不稳）。
    L.append(f"| 候选 | 进前{top_k} | 平均名次 | 名次区间 | 中位 | 判定 |")
    L.append("|---|---|---|---|---|---|")
    for nm in order:
        a = per_alt[nm]
        # 不再剔除零命中车：平均名次能区分"稳但靠后"与"真不稳"，全展示更透明
        _rm = a.get("rank_mean")
        _mean_txt = f"{_rm:.1f}" if _rm is not None else "—"
        L.append(f"| {nm} | {a['method_hits']}/{n_meth} | {_mean_txt} | "
                 f"{a['method_range'][0]:g}–{a['method_range'][1]:g} | "
                 f"{a['method_median']:g} | {a['method_verdict']} |")
    stable = [nm for nm in order if per_alt[nm]["method_verdict"] == "稳定"]
    fragile = [nm for nm in order if per_alt[nm]["method_verdict"] == "脆弱"
               and per_alt[nm]["method_hits"] > 0]
    L.append("")
    L.append(f"- **算法无关（{n_meth} 种方法全进前{top_k}）**：{'、'.join(stable) or '无'}")
    if fragile:
        L.append(f"- **脆弱（仅 4–6 种方法进前{top_k}，换算法就掉）**：{'、'.join(fragile)}")
    L.append(f"- {n_meth} 种方法两两 Kendall τ 均值 **{mean_tau:.3f}**")
    if mean_tau >= 0.9:
        L.append("  → τ≥0.9：各方法排序高度一致，**名次是产品差异，不是算法偏好**。")
    elif mean_tau >= 0.8:
        L.append("  → 0.8≤τ<0.9：大体一致，但前几名存在方法敏感性，2 分以内的名次差不要当结论。")
    else:
        L.append("  → τ<0.8：**矩阵分辨力不足**——候选之间分差太小，不同算法会给出不同前几名。"
                 "此时「第 1 名是谁」不可作为结论，只能说「这几款在同一档」。")
    L.append("")

    L.append("## 二、权重稳定性（换一套权重，前 %d 是否变化）" % top_k)
    L.append("")
    L.append(f"| 权重情景 | 前{top_k}（9 方法平均） | 与基准交集 |")
    L.append("|---|---|---|")
    for sname, tops in scheme_top.items():
        inter = len(set(tops) & base_top) if sname != list(scheme_top)[0] else top_k
        mark = "—" if sname == list(scheme_top)[0] else f"**{inter}/{top_k}**"
        L.append(f"| {sname} | {' / '.join(tops)} | {mark} |")
    L.append("")
    L.append("> 交集 3/3 表示该权重下前几名不变；出现 1/3 说明结论对「你在意什么」高度敏感——"
             "这时不该给单一榜单，该给滑杆。")
    L.append("")

    L.append(f"## 三、联合稳定性（{n_runs} 次全部排序）")
    L.append("")
    L.append(f"| 候选 | 命中 | 频率 | 名次区间 | 判定 |")
    L.append("|---|---|---|---|---|")
    for nm in order[:12]:
        a = per_alt[nm]
        L.append(f"| {nm} | {a['topk_count']}/{n_runs} | {a['topk_freq']:.0%} | "
                 f"{a['rank_min']:g}–{a['rank_max']:g} | {a['verdict']} |")
    L.append("")

    L.append("## 四、各方法的基准排序（第 1 / 2 / 3 名）")
    L.append("")
    L.append("| 方法 | 1 | 2 | 3 |")
    L.append("|---|---|---|---|")
    for mkey in METHODS:
        if mkey not in per_method:
            continue
        avg = per_method[mkey]["avg_rank"]
        idx = sorted(range(len(avg)), key=lambda i: avg[i])[:3]
        L.append(f"| {METHOD_LABEL[mkey]} | " + " | ".join(names[i] for i in idx) + " |")
    L.append("")
    L.append("> COPRAS 在「全为效益型准则」时会退化为加权和，故未纳入；保留的方法在纯效益矩阵下"
             "仍各有独立机制（距离理想解、最小遗憾、边界距离、净流、灰色关联、固定边界距离等）。")
    if tau_mat:
        keys = list(tau_mat)
        short = {"saw": "SAW", "waspas": "WASP", "topsis": "TOPS", "vikor": "VIKO",
                 "mabac": "MABA", "moora": "MOOR", "edas": "EDAS",
                 "promethee": "PROM", "gra": "GRA", "spotis": "SPOT"}
        L += ["", "## 五、方法间 τ 矩阵（谁和谁不一致）", ""]
        L.append("| | " + " | ".join(short.get(k, k) for k in keys) + " |")
        L.append("|" + "---|" * (len(keys) + 1))
        for a in keys:
            row = [f"**{tau_mat[a][b]:.2f}**" if a != b and tau_mat[a][b] < 0.8
                   else (f"{tau_mat[a][b]:.2f}" if a != b else "—") for b in keys]
            L.append(f"| {short.get(a, a)} | " + " | ".join(row) + " |")
        dups = [(tau_mat[a][b], a, b) for i, a in enumerate(keys)
                for b in keys[i + 1:] if tau_mat[a][b] >= 0.98]
        # 并查集：把 τ≥0.98 的方法并成一个等价类，数真正独立的意见有几票
        parent = {k: k for k in keys}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for tv, a, b in dups:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
        clusters = {}
        for k in keys:
            clusters.setdefault(find(k), []).append(k)
        if dups:
            L += ["", "### 方法独立性检查（τ≥0.98 合并为同一等价类）", ""]
            for root, members in clusters.items():
                if len(members) > 1:
                    L.append("- **" + " ≡ ".join(short.get(m, m) for m in members)
                             + "**：在本矩阵上给出几乎相同的排序，"
                               "**只算一票**，不是多次独立验证。")
                else:
                    L.append(f"- {short.get(members[0], members[0])}：独立")
            n_ind = len(clusters)
            L += ["", f"> **有效独立方法数 ≈ {n_ind}**（名义 {len(keys)} 种）。"
                       "「进前N 比例」的分母应按独立票数理解。", ""]
            if n_ind >= len(keys) * 0.6:
                L.append(f"> ⚠ **独立方法数多不等于结论更稳，恰恰相反**："
                         f"{n_ind} 个等价类即 {n_ind} 种互不相同的排序意见，"
                         "说明各算法在这份矩阵上分歧明显、结论依赖算法选择。"
                         "分档清楚的榜（如分差 3 分以上）会让大部分方法收敛到同一个等价类，"
                         "独立方法数反而少——那种「少」才是稳。")
                L.append("")
            else:
                L.append(f"> 独立方法数 {n_ind} 偏低，说明多数算法在这份矩阵上收敛到同一排序，"
                         "名次主要由产品差异驱动、而非算法偏好。")
                L.append("")
        worst = min(((tau_mat[a][b], a, b) for i, a in enumerate(keys) for b in keys[i + 1:]),
                    default=None)
        if worst and worst[0] < 0.8:
            L.append(f"\n- 分歧最大的一对：**{short.get(worst[1])} vs {short.get(worst[2])}**"
                     f"（τ={worst[0]:.2f}）。加粗单元格即 τ<0.8，说明这两个算法的名次不可互换解读。")
    return "\n".join(L)


def selftest():
    ok = True
    # 1) 完全分离的矩阵：所有方法都应给出同一序
    m = [[9, 8, 7], [5, 5, 5], [1, 2, 3]]
    w = [1 / 3] * 3
    t = ["benefit"] * 3
    names = ["A", "B", "C"]
    r = run_robust(m, w, t, names)
    orders = {tuple(sorted(range(3), key=lambda i: r["per_method"][k]["avg_rank"][i]))
              for k in r["per_method"]}
    print("  [1] 分离矩阵各方法序一致:", orders == {(0, 1, 2)})
    ok &= orders == {(0, 1, 2)}

    # 2) SAW 手算校验
    saw = m_saw([[10.0, 5.0], [5.0, 10.0]], [0.7, 0.3], ["benefit", "benefit"])
    expect = [0.7 * 1 + 0.3 * 0.5, 0.7 * 0.5 + 0.3 * 1]
    print("  [2] SAW 手算一致:", all(abs(saw[i] - expect[i]) < 1e-9 for i in range(2)))
    ok &= all(abs(saw[i] - expect[i]) < 1e-9 for i in range(2))

    # 3) TOPSIS 在「全优/全劣」时趋近 1/0
    tt = m_topsis([[10.0, 10.0], [0.0, 0.0], [5.0, 5.0]], [0.5, 0.5],
                  ["benefit", "benefit"])
    print(f"  [3] TOPSIS 边界: max={max(tt):.3f} min={min(tt):.3f}",
          abs(max(tt) - 1) < 1e-9 and abs(min(tt)) < 1e-9)
    ok &= abs(max(tt) - 1) < 1e-9 and abs(min(tt)) < 1e-9

    # 4) AHP 回推：完全一致矩阵应还原原权重，CR≈0
    base = [0.20, 0.18, 0.15, 0.14, 0.13, 0.10, 0.10]
    ahp = ahp_weights(ratio_matrix(base))
    dev = max(abs(ahp["weights"][i] - base[i]) for i in range(len(base)))
    print(f"  [4] AHP 还原权重 最大偏差={dev:.2e} CR={ahp['cr']:.1e}", dev < 1e-6)
    ok &= dev < 1e-6

    # 5) 权重倾斜：滑杆 50 时不动，100 时该组翻倍
    spec = {"智驾": {"dims": ["adas"], "_order": ["a", "adas", "b"]}}
    w50 = tilt_weights([0.5, 0.25, 0.25], {"智驾": 50}, spec)
    w100 = tilt_weights([0.5, 0.25, 0.25], {"智驾": 100}, spec)
    print(f"  [5] 滑杆 50={[round(x,3) for x in w50]} 100={[round(x,3) for x in w100]}",
          abs(w50[1] - 0.25) < 1e-9 and w100[1] > w50[1])
    ok &= abs(w50[1] - 0.25) < 1e-9 and w100[1] > w50[1]

    # 6) Kendall：同序 1、反序 -1
    t1 = kendall_tau([1, 2, 3], [1, 2, 3])
    t2 = kendall_tau([1, 2, 3], [3, 2, 1])
    print(f"  [6] Kendall 同序={t1:.2f} 反序={t2:.2f}", t1 == 1 and t2 == -1)
    ok &= t1 == 1 and t2 == -1

    # 7) 客观权重：四种方法都应归一
    m7 = [[9.0, 5.0, 6.0], [5.0, 5.0, 7.0], [3.0, 5.0, 8.0], [7.0, 5.0, 6.5]]
    ow = objective_weights(m7, ["benefit"] * 3)
    sums_ok = all(abs(sum(v) - 1.0) < 1e-9 for v in ow.values())
    print("  [7] 客观权重归一:", {k: round(sum(v), 6) for k, v in ow.items()}, sums_ok)
    ok &= sums_ok

    # 8) 熵权：常量列（无信息）权重应≈0，且低于离散列
    ew = ow["熵权法"]
    print(f"  [8] 熵权={dict(zip(['c1','c2(常量)','c3'], [round(x,3) for x in ew]))}",
          ew[1] < 1e-6 and ew[1] < min(ew[0], ew[2]))
    ok &= ew[1] < 1e-6 and ew[1] < min(ew[0], ew[2])

    # 9) SPOTIS 抗秩反转：删掉一个候选后 TOPSIS 换位、SPOTIS 不换位
    #    这组数据是搜出来的真实反例（TOPSIS 在删除 D 后 A/B 互换）
    pts = [[8.75, 3.14, 6.95], [5.94, 5.80, 4.56],
           [8.40, 9.45, 4.74], [6.64, 0.61, 7.01]]
    w9 = [0.4, 0.35, 0.25]
    t9 = ["benefit"] * 3
    sub9 = pts[:3]
    tf = _ranks(m_topsis(pts, w9, t9))[:3]
    ts = _ranks(m_topsis(sub9, w9, t9))
    sf = _ranks(m_spotis(pts, w9, t9))[:3]
    ss = _ranks(m_spotis(sub9, w9, t9))
    reversal = list(tf) != list(ts)
    spotis_stable = list(sf) == list(ss)
    print(f"  [9] TOPSIS 出现秩反转={reversal} ｜ SPOTIS 保持稳定={spotis_stable}")
    ok &= reversal and spotis_stable

    # 10) 权重相似度：同一组权重 τ=1、余弦=1
    ws = weight_similarity([0.3, 0.3, 0.4], [0.3, 0.3, 0.4])
    print(f"  [10] 权重自比较 cos={ws['cosine']:.3f} tau={ws['tau']:.2f}",
          abs(ws["cosine"] - 1) < 1e-9 and ws["tau"] == 1.0)
    ok &= abs(ws["cosine"] - 1) < 1e-9 and ws["tau"] == 1.0

    # 11) bootstrap CI：分差悬殊的两组必须不重叠，接近的必须重叠
    m11 = [[9.2, 8.8], [8.6, 8.4], [5.0, 5.2]]
    ci = bootstrap_rank_ci(m11, [0.5, 0.5], ["benefit"] * 2,
                           ["好A", "好B", "差C"], n_boot=300, noise=0.5)
    sep = not overlap(ci["好A"], ci["差C"])
    near = overlap(ci["好A"], ci["好B"])
    print(f"  [11] CI 好A={ci['好A']['p05']}-{ci['好A']['p95']} "
          f"好B={ci['好B']['p05']}-{ci['好B']['p95']} "
          f"差C={ci['差C']['p05']}-{ci['差C']['p95']} ｜ 强弱可辨={sep} 相近重叠={near}")
    ok &= sep

    # 12) 归一化审计：完全分离的矩阵，任何归一化方式都不应换位
    m12 = [[9, 2, 1], [6, 5, 4], [2, 8, 9]]
    na = normalization_audit(m12, [1 / 3] * 3, ["benefit"] * 3,
                             ["A", "B", "C"])
    base_order = tuple(na["rankings"][na["normalizations"][0]])
    all_same = all(tuple(na["rankings"][nm]) == base_order for nm in na["normalizations"])
    print(f"  [12] 归一化审计 分离矩阵 τ均值={na['tau_mean']:.3f} 全序一致={all_same}")
    ok &= all_same and na["tau_mean"] > 0.99
    # 12b) 归一化不变量：linear/minmax 对整体正比例缩放不改变名次
    scaled = [[v * 3 for v in row] for row in m12]
    na2 = normalization_audit(scaled, [1 / 3] * 3, ["benefit"] * 3,
                              ["A", "B", "C"], normalizations=["linear", "minmax"])
    scale_stable = na2["rankings"]["linear"] == na["rankings"]["linear"]
    print(f"  [12b] 归一化整体缩放不变: {scale_stable}")
    ok &= scale_stable

    print("SELFTEST", "PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(0 if selftest() else 1)
    print(__doc__)
