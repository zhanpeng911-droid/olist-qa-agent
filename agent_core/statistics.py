"""M3 统计验证：单变量检验 + Logistic 回归 + 多重校正 + 证据分级。

观察性数据，只谈关联、禁止因果措辞。所有检验基于从 provider 拉取的原始行数据，
在样例/真库上均可运行（真库由使用者自测验证）。
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.stats.multitest import multipletests

from .data_provider import DataProvider

_LOAD_LIMIT = 100000


def load_table(provider: DataProvider, table: str, columns: list[str],
               where: str | None = None, limit: int = _LOAD_LIMIT) -> pd.DataFrame:
    """从 provider 拉取原始行数据转 DataFrame。"""
    cols = ", ".join(columns) if columns else "*"
    sql = f"SELECT {cols} FROM {table}"
    if where:
        sql += f" WHERE {where}"
    sql += f" LIMIT {limit}"
    rows = provider.execute(sql)
    return pd.DataFrame(rows)


# ---------- 单变量检验 ----------

def categorical_test(df: pd.DataFrame, col_x: str, col_y: str) -> dict:
    """2x2 二分类检验：卡方（期望<5 用 Fisher）+ OR/RR + 95%CI。

    col_x 为风险因素(0/1)，col_y 为结果(0/1)。OR 表示 x=1 相对 x=0 的结果 odds 比。
    """
    tab = pd.crosstab(df[col_x], df[col_y]).reindex(
        index=[0, 1], columns=[0, 1], fill_value=0)
    a = tab.loc[0, 0]; b = tab.loc[0, 1]; c = tab.loc[1, 0]; d = tab.loc[1, 1]
    n = int(a + b + c + d)
    exp = np.array([[(a + b) * (a + c) / n, (a + b) * (b + d) / n],
                    [(c + d) * (a + c) / n, (c + d) * (b + d) / n]])
    if (exp < 5).any() or (tab.values < 5).any():
        odds, p = stats.fisher_exact(tab.values)
        method = "fisher"
    else:
        chi2, p, _, _ = stats.chi2_contingency(tab.values, correction=True)
        odds = (a * d) / (b * c) if b * c > 0 else float("inf")
        method = "chi2"
    p1 = d / (c + d) if (c + d) > 0 else 0.0
    p0 = b / (a + b) if (a + b) > 0 else 0.0
    rr = p1 / p0 if p0 > 0 else None
    or_ci = None
    if a > 0 and b > 0 and c > 0 and d > 0 and math.isfinite(odds) and odds > 0:
        se = math.sqrt(1 / a + 1 / b + 1 / c + 1 / d)
        or_ci = [round(math.exp(math.log(odds) - 1.96 * se), 3),
                 round(math.exp(math.log(odds) + 1.96 * se), 3)]
    return {"method": method, "p": float(p), "or": float(odds),
            "or_ci": or_ci, "rr": rr, "n": n,
            "rate_x1": p1, "rate_x0": p0}


def chi_square_rc(df: pd.DataFrame, col: str, target: str) -> dict:
    """R×C 卡方：多类别 × 二分类，报告 p 与 Cramér's V，及各类别率。"""
    tab = pd.crosstab(df[col], df[target])
    chi2, p, dof, _ = stats.chi2_contingency(tab)
    n = tab.values.sum()
    v = math.sqrt(chi2 / (n * (min(tab.shape) - 1))) if n > 0 and min(tab.shape) > 1 else 0.0
    groups = []
    for cat in tab.index:
        r = tab.loc[cat]
        tot = int(r.sum())
        groups.append({"value": cat, "n": tot,
                       "rate": float(r[1] / tot) if tot > 0 else 0.0})
    return {"p": float(p), "cramers_v": round(float(v), 4),
            "groups": groups, "n": int(n)}


def distribution_test(df: pd.DataFrame, numeric: str, group: str) -> dict:
    """连续偏态 × 二组：Mann-Whitney U + rank-biserial 效应量。"""
    g0 = df.loc[df[group] == 0, numeric].dropna()
    g1 = df.loc[df[group] == 1, numeric].dropna()
    if len(g0) == 0 or len(g1) == 0:
        return {"error": "某组为空"}
    res = stats.mannwhitneyu(g0, g1, alternative="two-sided")
    u = float(res.statistic)
    rb = 1 - 2 * u / (len(g0) * len(g1))
    return {"p": float(res.pvalue), "u": u, "effect_size": round(float(rb), 4),
            "median_0": float(g0.median()), "median_1": float(g1.median()),
            "n0": int(len(g0)), "n1": int(len(g1))}


def correlation_test(df: pd.DataFrame, col_a: str, col_b: str) -> dict:
    """连续/有序相关：Spearman ρ + p。"""
    d = df[[col_a, col_b]].dropna()
    res = stats.spearmanr(d[col_a], d[col_b])
    return {"rho": float(res.statistic), "p": float(res.pvalue), "n": int(len(d))}


def trend_test(df: pd.DataFrame, score_col: str, target: str) -> dict:
    """有序 × 二分类：Cochran-Armitage 趋势检验。

    score_col 为有序分值（如延迟等级 0/1/2/3/4），target 为二分类结果。
    """
    g = df.groupby(score_col)[target]
    sizes = g.size().to_numpy(dtype=float)
    sums = g.sum().to_numpy(dtype=float)
    scores = np.array(sorted(g.size().index), dtype=float)
    n = sizes.sum(); p = sums.sum() / n if n > 0 else 0.0
    if p in (0.0, 1.0):
        return {"error": "结果变量无变异"}
    tm = (sizes * scores).sum() / n
    var = p * (1 - p) * ((sizes * scores ** 2).sum() - n * tm ** 2)
    z = ((scores * sums).sum() - n * p * tm) / math.sqrt(var) if var > 0 else 0.0
    p_val = 2 * stats.norm.sf(abs(z))
    levels = []
    for i, s in enumerate(sorted(g.size().index)):
        levels.append({"score": int(s), "n": int(sizes[i]), "rate": float(sums[i] / sizes[i])})
    return {"z": round(float(z), 4), "p": float(p_val), "n": int(n), "levels": levels}


# ---------- 多变量 ----------

def logistic_model_formula(df: pd.DataFrame, formula: str,
                           robust: str = "HC3", maxiter: int = 500) -> dict:
    """Logistic 回归（statsmodels formula，分类变量自动哑编码）+ 稳健 SE。

    返回各解释变量的调整 OR / 95%CI / p，以及伪 R²、样本量。
    """
    from statsmodels.formula.api import logit

    # cov_type 直接传给 fit：results 即携带稳健标准误（HC3）
    model = logit(formula, df).fit(disp=0, maxiter=maxiter, cov_type=robust)
    ci = model.conf_int()
    terms = []
    for term in model.params.index:
        if term == "Intercept":
            continue
        lo, hi = ci.loc[term]
        terms.append({
            "term": term,
            "or": round(float(np.exp(model.params[term])), 3),
            "ci95": [round(float(np.exp(lo)), 3), round(float(np.exp(hi)), 3)],
            "p": float(model.pvalues[term]),
        })
    return {"terms": terms, "pseudo_r2": float(model.prsquared),
            "nobs": int(model.nobs), "robust": robust}


# ---------- 多重校正 / 证据分级 ----------

def multiple_correction(pvalues: list[float], method: str = "fdr_bh") -> dict:
    """对一组 p 值做多重检验校正，返回校正 p 与是否通过。"""
    pv = np.asarray([float(v) for v in pvalues], dtype=float)
    reject, pcorr, _, _ = multipletests(pv, method=method)
    return {"method": method, "p_adjusted": [round(float(x), 4) for x in pcorr],
            "significant": [bool(r) for r in reject]}


def evidence_grade(p_raw: float, p_adjusted: float | None,
                   effect: float | None, sample: int, min_sample: int = 100) -> str:
    """证据分级：强 / 中等 / 待验证线索。

    强证据：样本充分、校正后显著、效应有业务意义（|effect|≥1.3 或效应量显著）。
    中等证据：单变量显著但校正后/样本/效应不满足强条件。
    待验证线索：样本不足或单变量也不显著。
    """
    if sample < min_sample:
        return "待验证线索"
    # 无多重校正时（单次检验）用原始 p，否则用校正后 p
    p_used = p_adjusted if p_adjusted is not None else p_raw
    if p_used < 0.05 and effect is not None and abs(effect) >= 1.3:
        return "强证据"
    if p_used < 0.05:
        return "中等证据"
    return "待验证线索"


# ---------- 归因验证集成 ----------

DELAY_RANK = {"按时": 0, "1-3天": 1, "4-7天": 2, "8-14天": 3, "15天+": 4}

ORDER_ANALYSIS_COLS = [
    "is_low_score", "is_late_delivery", "late_days", "review_score",
    "delay_bucket", "customer_state", "primary_category_name",
    "primary_payment_type", "order_month", "approval_days",
    "fulfillment_days", "price_total", "freight_total",
    "is_delivery_analysis_eligible",
]
SELLER_ANALYSIS_COLS = [
    "is_low_score", "is_late_delivery", "cross_state", "seller_state",
    "customer_state", "seller_price", "is_multi_seller_order",
]


def verify_factors(provider: DataProvider, min_group_sample: int = 100) -> dict:
    """对归因候选因素做批量单变量检验 + 双 Logistic + 证据分级（自动并入归因）。

    返回 verification 块：
    - single_tests: 卡方/趋势/多类别卡方(含校正)/Spearman/MWU
    - logistic: 订单级 + 单卖家级 两个模型（HC3）
    - evidence: 关键因素 is_late_delivery 的证据分级
    """
    df = load_table(provider, "mart_order_delivery", ORDER_ANALYSIS_COLS)
    df = df[df["is_delivery_analysis_eligible"] == 1].copy()
    df["delay_rank"] = df["delay_bucket"].map(DELAY_RANK)

    tests: list[dict] = []

    # 1. 二分类卡方：is_late_delivery × is_low_score
    ct = categorical_test(df, "is_late_delivery", "is_low_score")
    tests.append({"factor": "is_late_delivery", "type": "categorical",
                  "method": ct["method"], "p": ct["p"], "or": ct["or"],
                  "or_ci": ct["or_ci"], "rr": ct["rr"], "n": ct["n"]})

    # 2. 趋势检验：delay_bucket(有序) × low_score
    tt = trend_test(df, "delay_rank", "is_low_score")
    if "error" not in tt:
        tests.append({"factor": "delay_bucket(趋势)", "type": "trend",
                      "z": tt["z"], "p": tt["p"], "n": tt["n"]})

    # 3. 多类别卡方 + 多重校正
    rc_factors = ["customer_state", "primary_category_name", "primary_payment_type"]
    rc_pvals: list[float] = []
    rc_tests: list[dict] = []
    for f in rc_factors:
        rc = chi_square_rc(df, f, "is_low_score")
        rc_pvals.append(rc["p"])
        t = {"factor": f, "type": "rc", "method": "chi2",
             "p": rc["p"], "cramers_v": rc["cramers_v"], "n": rc["n"]}
        rc_tests.append(t)
        tests.append(t)
    corr = multiple_correction(rc_pvals)
    for i, t in enumerate(rc_tests):
        t["p_adjusted"] = corr["p_adjusted"][i]

    # 4. Spearman 相关
    for col in ["late_days", "approval_days", "fulfillment_days", "price_total"]:
        sp = correlation_test(df, col, "review_score")
        tests.append({"factor": f"{col}×review_score", "type": "spearman",
                      "rho": sp["rho"], "p": sp["p"], "n": sp["n"]})

    # 5. Mann-Whitney U：延迟组 vs 非延迟组 评分
    mw = distribution_test(df, "review_score", "is_late_delivery")
    if "error" not in mw:
        tests.append({"factor": "review_score by is_late_delivery", "type": "mwu",
                      "p": mw["p"], "effect_size": mw["effect_size"],
                      "median_0": mw["median_0"], "median_1": mw["median_1"],
                      "n": mw["n0"] + mw["n1"]})

    # 6. 双 Logistic（HC3 稳健 SE）
    order_formula = (
        "is_low_score ~ is_late_delivery + C(customer_state) + "
        "C(primary_category_name) + C(primary_payment_type) + "
        "approval_days + fulfillment_days + price_total + freight_total"
    )
    order_logit = logistic_model_formula(df, order_formula)

    sdf = load_table(provider, "mart_order_seller_delivery", SELLER_ANALYSIS_COLS)
    sdf = sdf[sdf["is_multi_seller_order"] == 0].copy()
    seller_formula = ("is_low_score ~ is_late_delivery + C(cross_state) + "
                      "C(seller_state) + seller_price")
    seller_logit = logistic_model_formula(sdf, seller_formula)

    # 7. 关键因素证据分级（is_late_delivery：单次检验无需多重校正）
    p_raw = ct["p"]
    grade = evidence_grade(p_raw, None, ct["or"], ct["n"], min_group_sample)

    return {
        "single_tests": tests,
        "logistic": {"order": order_logit, "seller": seller_logit},
        "evidence": {
            "factor": "is_late_delivery", "grade": grade,
            "p": p_raw, "or": ct["or"], "or_ci": ct["or_ci"], "n": ct["n"],
        },
        "note": "观察性数据，只谈关联、禁因果；真库由使用者自测验证。",
    }
