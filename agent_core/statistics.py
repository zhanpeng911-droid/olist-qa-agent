"""M3 统计验证：单变量检验 + Logistic 回归 + 多重校正 + 证据分级。

观察性数据，只谈关联、禁止因果措辞。所有检验基于从 provider 拉取的原始行数据，
在项目截取 CSV/真库上均可运行。
"""
from __future__ import annotations

import math
import gc
import warnings

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.stats.multitest import multipletests

from .data_provider import DataProvider

_LOAD_LIMIT = 250000


def load_table(provider: DataProvider, table: str, columns: list[str],
               where: str | None = None, limit: int = _LOAD_LIMIT,
               sql_sink: list[str] | None = None) -> pd.DataFrame:
    """从 provider 拉取原始行数据转 DataFrame。"""
    cols = ", ".join(columns) if columns else "*"
    sql = f"SELECT {cols} FROM {table}"
    if where:
        sql += f" WHERE {where}"
    sql += f" LIMIT {limit}"
    if sql_sink is not None:
        sql_sink.append(sql)
    rows = provider.execute(sql)
    return pd.DataFrame(rows)


ORDER_WHERE = "is_delivery_analysis_eligible = 1 AND has_review_record = 1"
SELLER_WHERE = (
    "is_delivery_analysis_eligible = 1 AND has_review_record = 1 "
    "AND is_multi_seller_order = 0"
)


def load_group_counts(provider: DataProvider, table: str, factor: str,
                      target: str = "is_low_score", where: str | None = None,
                      sql_sink: list[str] | None = None) -> pd.DataFrame:
    """在数据库端聚合列联计数，Python不接收明细行。"""
    sql = f"SELECT {factor}, {target}, COUNT(*) AS n FROM {table}"
    if where:
        sql += f" WHERE {where}"
    sql += f" GROUP BY {factor}, {target} LIMIT 10000"
    if sql_sink is not None:
        sql_sink.append(sql)
    return pd.DataFrame(provider.execute(sql))


def _pivot_counts(counts: pd.DataFrame, factor: str,
                  target: str = "is_low_score") -> pd.DataFrame:
    """把数据库聚合结果转为分组×二分类列联表。"""
    if counts.empty:
        return pd.DataFrame(columns=[0, 1])
    data = counts.dropna(subset=[factor, target]).copy()
    data[target] = data[target].astype(int)
    return data.pivot_table(
        index=factor, columns=target, values="n", aggfunc="sum", fill_value=0
    ).reindex(columns=[0, 1], fill_value=0)


def filter_group_counts(counts: pd.DataFrame, factor: str,
                        min_group_sample: int) -> tuple[pd.DataFrame, int]:
    """过滤高基数因素的小组，返回保留计数与排除组数。"""
    if counts.empty:
        return counts, 0
    totals = counts.groupby(factor, dropna=False)["n"].sum()
    keep = set(totals[totals >= min_group_sample].index)
    filtered = counts[counts[factor].isin(keep)].copy()
    return filtered, int(len(totals) - len(keep))


# ---------- 单变量检验 ----------

def categorical_test(df: pd.DataFrame, col_x: str, col_y: str) -> dict:
    """2x2 二分类检验：卡方（期望<5 用 Fisher）+ OR/RR + 95%CI。

    col_x 为风险因素(0/1)，col_y 为结果(0/1)。OR 表示 x=1 相对 x=0 的结果 odds 比。
    """
    tab = pd.crosstab(df[col_x], df[col_y]).reindex(
        index=[0, 1], columns=[0, 1], fill_value=0)
    return _categorical_from_tab(tab)


def categorical_test_counts(counts: pd.DataFrame, col_x: str,
                            col_y: str = "is_low_score") -> dict:
    """从数据库聚合计数完成2×2检验，避免加载明细。"""
    tab = _pivot_counts(counts, col_x, col_y).reindex(
        index=[0, 1], columns=[0, 1], fill_value=0
    )
    return _categorical_from_tab(tab)


def _categorical_from_tab(tab: pd.DataFrame) -> dict:
    """2×2列联表的卡方/Fisher、OR、RR与区间。"""
    a = tab.loc[0, 0]; b = tab.loc[0, 1]; c = tab.loc[1, 0]; d = tab.loc[1, 1]
    n = int(a + b + c + d)
    if n == 0:
        return {"error": "有效样本为空"}
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


def chi_square_rc_counts(counts: pd.DataFrame, col: str,
                         target: str = "is_low_score") -> dict:
    """从数据库聚合计数完成R×C卡方与Cramér's V。"""
    tab = _pivot_counts(counts, col, target)
    if tab.empty or min(tab.shape) < 2:
        return {"error": "有效分组不足"}
    chi2, p, _, expected = stats.chi2_contingency(tab.to_numpy(dtype=float))
    n = int(tab.to_numpy().sum())
    v = math.sqrt(chi2 / (n * (min(tab.shape) - 1))) if n else 0.0
    low_expected_share = float((expected < 5).sum() / expected.size)
    assumption_ok = low_expected_share <= 0.2 and bool((expected >= 1).all())
    groups = []
    for cat, row in tab.iterrows():
        total = int(row.sum())
        groups.append({"value": cat, "n": total,
                       "rate": float(row[1] / total) if total else 0.0})
    return {"p": float(p), "cramers_v": round(float(v), 4),
            "groups": groups, "n": n, "assumption_ok": assumption_ok,
            "low_expected_share": low_expected_share}


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


def trend_test_counts(counts: pd.DataFrame, level_col: str,
                      score_map: dict, target: str = "is_low_score") -> dict:
    """从延迟档位×结果的聚合计数完成趋势检验。"""
    data = counts.copy()
    data["_score"] = data[level_col].map(score_map)
    data = data.dropna(subset=["_score", target])
    if data.empty:
        return {"error": "有效样本为空"}
    data[target] = data[target].astype(int)
    tab = data.pivot_table(
        index="_score", columns=target, values="n", aggfunc="sum", fill_value=0
    ).reindex(columns=[0, 1], fill_value=0).sort_index()
    sizes = tab.sum(axis=1).to_numpy(dtype=float)
    sums = tab[1].to_numpy(dtype=float)
    scores = tab.index.to_numpy(dtype=float)
    n = sizes.sum()
    p = sums.sum() / n if n else 0.0
    if p in (0.0, 1.0):
        return {"error": "结果变量无变异"}
    mean_score = (sizes * scores).sum() / n
    variance = p * (1 - p) * (
        (sizes * scores ** 2).sum() - n * mean_score ** 2
    )
    if variance <= 0:
        return {"error": "趋势分数无有效变异"}
    z = ((scores * sums).sum() - n * p * mean_score) / math.sqrt(variance)
    p_value = 2 * stats.norm.sf(abs(z))
    levels = [
        {"score": int(score), "n": int(sizes[i]),
         "rate": float(sums[i] / sizes[i])}
        for i, score in enumerate(scores)
    ]
    return {"z": round(float(z), 4), "p": float(p_value),
            "n": int(n), "levels": levels}


# ---------- 多变量 ----------

def logistic_model_formula(df: pd.DataFrame, formula: str,
                           robust: str = "HC3", maxiter: int = 500) -> dict:
    """Logistic 回归（statsmodels formula，分类变量自动哑编码）+ 稳健 SE。

    返回各解释变量的调整 OR / 95%CI / p，以及伪 R²、样本量。
    """
    import statsmodels.api as sm
    from statsmodels.formula.api import glm, logit

    # cov_type 直接传给 fit：results 即携带稳健标准误（HC3）
    # 完全分离/稀疏类别可能先产生数值 RuntimeWarning，随后抛出异常；
    # 上层会明确降级。这里避免这些预期警告污染页面和评测输出。
    fit_method = "Logit-Newton"
    fallback_reason = None
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", module=r"statsmodels\..*")
        try:
            # 先使用标准MLE。稳健协方差在下方用广义逆自行计算，避免
            # statsmodels在HC3阶段对近奇异Hessian直接求逆而抛LinAlgError。
            model = logit(formula, df).fit(
                disp=0, maxiter=maxiter, method="newton"
            )
            if not model.mle_retvals.get("converged", False):
                raise RuntimeError("Newton未收敛")
        except Exception as error:
            # GLM Binomial的IRLS使用广义逆处理秩不足设计矩阵；
            # HC3仍由下方统一计算，不依赖statsmodels内部的普通矩阵逆。
            # 这是统计等价的二项Logistic降级，不是改用另一种目标或口径。
            fallback_reason = f"{type(error).__name__}: {error}"
            fit_method = "GLM-Binomial回退"
            model = glm(
                formula, df, family=sm.families.Binomial()
            ).fit(maxiter=maxiter)
            if not getattr(model, "converged", True):
                raise RuntimeError("GLM-Binomial未收敛")

    # 二项Logistic的HC3 sandwich covariance。bread使用pinv，因此即使设计
    # 矩阵接近奇异也不会让整个模型崩溃；这正是全量分类变量模型所需的保护。
    design = np.asarray(model.model.exog, dtype=float)
    outcome = np.asarray(model.model.endog, dtype=float)
    fitted = np.asarray(model.predict(), dtype=float)
    weights = np.clip(fitted * (1.0 - fitted), 1e-12, None)
    bread = np.linalg.pinv(design.T @ (weights[:, None] * design))
    leverage = weights * np.einsum("ij,jk,ik->i", design, bread, design)
    adjusted_residual = (outcome - fitted) / np.clip(1.0 - leverage, 1e-6, None)
    meat = design.T @ ((adjusted_residual ** 2)[:, None] * design)
    robust_cov = bread @ meat @ bread
    robust_cov = (robust_cov + robust_cov.T) / 2.0
    robust_se = np.sqrt(np.clip(np.diag(robust_cov), 0.0, None))

    params = np.asarray(model.params, dtype=float)
    names = list(model.params.index)
    terms = []
    for index, term in enumerate(names):
        if term == "Intercept":
            continue
        estimate = params[index]
        se = robust_se[index]
        lo, hi = estimate - 1.96 * se, estimate + 1.96 * se
        p_value = 2.0 * stats.norm.sf(abs(estimate / se)) if se > 0 else 1.0
        terms.append({
            "term": term,
            "or": round(float(np.exp(estimate)), 3),
            "ci95": [round(float(np.exp(lo)), 3), round(float(np.exp(hi)), 3)],
            "p": float(p_value),
        })
    joint_tests = []
    try:
        slices = model.model.data.design_info.term_name_slices
        for term, column_slice in slices.items():
            if str(term) == "Intercept":
                continue
            indices = np.arange(len(names))[column_slice]
            beta = params[indices]
            covariance = robust_cov[np.ix_(indices, indices)]
            degrees = int(np.linalg.matrix_rank(covariance))
            if degrees <= 0:
                statistic, p_value = 0.0, 1.0
            else:
                statistic = float(beta.T @ np.linalg.pinv(covariance) @ beta)
                p_value = float(stats.chi2.sf(statistic, degrees))
            joint_tests.append({
                "term": str(term),
                "statistic": statistic,
                "p": p_value,
                "df": degrees,
            })
    except Exception:
        # 个别 statsmodels 版本或退化模型不支持联合Wald表；
        # 单项调整OR仍可正常返回，上层会明确标记联合检验不可用。
        joint_tests = []
    pseudo_r2 = getattr(model, "prsquared", None)
    if pseudo_r2 is None:
        null_deviance = float(getattr(model, "null_deviance", 0) or 0)
        deviance = float(getattr(model, "deviance", 0) or 0)
        pseudo_r2 = (1 - deviance / null_deviance) if null_deviance else 0.0
    return {"terms": terms, "joint_tests": joint_tests,
            "pseudo_r2": float(pseudo_r2),
            "nobs": int(model.nobs), "robust": robust,
            "fit_method": fit_method, "fallback_reason": fallback_reason}


# ---------- 多重校正 / 证据分级 ----------

def multiple_correction(pvalues: list[float], method: str = "fdr_bh") -> dict:
    """对一组 p 值做多重检验校正，返回校正 p 与是否通过。"""
    pv = np.asarray([float(v) for v in pvalues], dtype=float)
    reject, pcorr, _, _ = multipletests(pv, method=method)
    return {"method": method, "p_adjusted": [float(x) for x in pcorr],
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

ORDER_MODEL_COLS = [
    "is_low_score", "is_late_delivery", "customer_state",
    "primary_category_name", "primary_payment_type", "approval_days",
    "fulfillment_days", "price_total", "freight_total",
]
SELLER_MODEL_COLS = [
    "is_low_score", "is_late_delivery", "cross_state", "seller_state",
    "seller_price",
]
SPEARMAN_FACTORS = [
    "late_days", "approval_days", "fulfillment_days", "price_total",
]


def _model_failure(label: str, error: Exception) -> dict:
    return {
        "ok": False,
        "error": f"{label}模型未能稳定估计: {type(error).__name__}",
        "note": "类别稀疏、完全分离或数值不稳定时不报告调整 OR。",
    }


def _skipped_model(label: str) -> dict:
    return {
        "ok": False,
        "skipped": True,
        "error": f"{label}Logistic未在轻量交互归因中运行",
        "note": "描述性归因和单变量检验已完成；深度回归应作为单独任务运行。",
    }


def _run_logistic_models(provider: DataProvider,
                         sqls: list[str]) -> dict:
    """显式深度模式：两套模型严格串行，前一数据集释放后再读取下一张表。"""
    order_formula = (
        "is_low_score ~ is_late_delivery + C(customer_state) + "
        "C(primary_category_name) + C(primary_payment_type) + "
        "approval_days + fulfillment_days + price_total + freight_total"
    )
    order_df = load_table(
        provider, "mart_order_delivery", ORDER_MODEL_COLS,
        where=ORDER_WHERE, sql_sink=sqls,
    )
    try:
        order_logit = logistic_model_formula(order_df, order_formula)
        order_logit["ok"] = True
    except Exception as error:
        order_logit = _model_failure("订单", error)
    finally:
        del order_df
        gc.collect()

    seller_formula = (
        "is_low_score ~ is_late_delivery + C(cross_state) + "
        "C(seller_state) + seller_price"
    )
    seller_df = load_table(
        provider, "mart_order_seller_delivery", SELLER_MODEL_COLS,
        where=SELLER_WHERE, sql_sink=sqls,
    )
    try:
        seller_logit = logistic_model_formula(seller_df, seller_formula)
        seller_logit["ok"] = True
    except Exception as error:
        seller_logit = _model_failure("卖家", error)
    finally:
        del seller_df
        gc.collect()
    return {"enabled": True, "order": order_logit, "seller": seller_logit}


def verify_factors(provider: DataProvider, min_group_sample: int = 100,
                   include_logistic: bool = False) -> dict:
    """低负载统计验证。

    分类检验只接收数据库聚合计数；相关性/分布检验每次只读取两列。
    Logistic默认跳过，显式深度模式下也按订单→释放→卖家的顺序串行执行。
    """
    tests: list[dict] = []
    sqls: list[str] = []
    row_extracts: list[dict] = []

    # 1. 二分类卡方：数据库只返回4格计数。
    binary_counts = load_group_counts(
        provider, "mart_order_delivery", "is_late_delivery",
        where=ORDER_WHERE, sql_sink=sqls,
    )
    ct = categorical_test_counts(
        binary_counts, "is_late_delivery", "is_low_score"
    )
    if "error" in ct:
        return {"ok": False, "error": ct["error"], "sqls": sqls}
    tests.append({"factor": "is_late_delivery", "type": "categorical",
                  "method": ct["method"], "p": ct["p"], "or": ct["or"],
                  "or_ci": ct["or_ci"], "rr": ct["rr"], "n": ct["n"]})

    # 2. 有序趋势：数据库只返回延迟档位×结果计数。
    delay_counts = load_group_counts(
        provider, "mart_order_delivery", "delay_bucket",
        where=ORDER_WHERE, sql_sink=sqls,
    )
    trend = trend_test_counts(
        delay_counts, "delay_bucket", DELAY_RANK, "is_low_score"
    )
    if "error" not in trend:
        tests.append({"factor": "delay_bucket(趋势)", "type": "trend",
                      "z": trend["z"], "p": trend["p"], "n": trend["n"]})

    # 3. 多类别卡方：订单与卖家维度均在数据库端形成列联计数。
    rc_tests: list[dict] = []
    rc_specs = [
        ("mart_order_delivery", ORDER_WHERE, "customer_state", None),
        ("mart_order_delivery", ORDER_WHERE, "primary_category_name", None),
        ("mart_order_delivery", ORDER_WHERE, "primary_payment_type", None),
        ("mart_order_delivery", ORDER_WHERE, "order_month", None),
        ("mart_order_seller_delivery", SELLER_WHERE, "seller_state", "normal"),
        ("mart_order_seller_delivery", SELLER_WHERE, "route", "route"),
    ]
    for table, where, factor, filter_kind in rc_specs:
        counts = load_group_counts(
            provider, table, factor, where=where, sql_sink=sqls,
        )
        excluded_groups = 0
        applied_min_sample = None
        if filter_kind:
            total = int(counts["n"].sum()) if not counts.empty else 0
            fraction = 0.001 if filter_kind == "route" else 0.0005
            applied_min_sample = max(20, math.ceil(total * fraction))
            counts, excluded_groups = filter_group_counts(
                counts, factor, applied_min_sample
            )
        rc = chi_square_rc_counts(counts, factor, "is_low_score")
        if "error" in rc:
            continue
        test = {"factor": factor, "type": "rc", "method": "chi2",
                "p": rc["p"], "cramers_v": rc["cramers_v"], "n": rc["n"],
                "assumption_ok": rc["assumption_ok"],
                "low_expected_share": rc["low_expected_share"],
                "source_table": table, "excluded_groups": excluded_groups,
                "min_group_sample": applied_min_sample}
        rc_tests.append(test)
        tests.append(test)

    # 3b. 跨州是二分类因素，只返回2×2四格计数。
    cross_counts = load_group_counts(
        provider, "mart_order_seller_delivery", "cross_state",
        where=SELLER_WHERE, sql_sink=sqls,
    )
    cross = categorical_test_counts(cross_counts, "cross_state", "is_low_score")
    if "error" not in cross:
        tests.append({
            "factor": "cross_state", "type": "categorical",
            "method": cross["method"], "p": cross["p"], "or": cross["or"],
            "or_ci": cross["or_ci"], "rr": cross["rr"], "n": cross["n"],
            "source_table": "mart_order_seller_delivery",
        })
    if rc_tests:
        correction = multiple_correction([t["p"] for t in rc_tests])
        for index, test in enumerate(rc_tests):
            test["p_adjusted"] = correction["p_adjusted"][index]

    # 4. Spearman：逐项只读取“因素+评分”两列，计算后立即释放。
    for factor in SPEARMAN_FACTORS:
        pair = load_table(
            provider, "mart_order_delivery", [factor, "review_score"],
            where=ORDER_WHERE, sql_sink=sqls,
        )
        row_extracts.append({"columns": [factor, "review_score"],
                             "rows": int(len(pair))})
        result = correlation_test(pair, factor, "review_score")
        tests.append({"factor": f"{factor}×review_score", "type": "spearman",
                      "rho": result["rho"], "p": result["p"], "n": result["n"]})
        del pair
        gc.collect()

    # 5. Mann–Whitney U：仅读取评分和延迟标记两列。
    score_groups = load_table(
        provider, "mart_order_delivery",
        ["review_score", "is_late_delivery"],
        where=ORDER_WHERE, sql_sink=sqls,
    )
    row_extracts.append({"columns": ["review_score", "is_late_delivery"],
                         "rows": int(len(score_groups))})
    distribution = distribution_test(
        score_groups, "review_score", "is_late_delivery"
    )
    if "error" not in distribution:
        tests.append({
            "factor": "review_score by is_late_delivery", "type": "mwu",
            "p": distribution["p"],
            "effect_size": distribution["effect_size"],
            "median_0": distribution["median_0"],
            "median_1": distribution["median_1"],
            "n": distribution["n0"] + distribution["n1"],
        })
    del score_groups
    gc.collect()

    logistic = (
        _run_logistic_models(provider, sqls)
        if include_logistic
        else {"enabled": False, "order": _skipped_model("订单"),
              "seller": _skipped_model("卖家")}
    )

    p_raw = ct["p"]
    grade = evidence_grade(p_raw, None, ct["or"], ct["n"], min_group_sample)
    return {
        "ok": True,
        "mode": "deep" if include_logistic else "lightweight",
        "single_tests": tests,
        "logistic": logistic,
        "evidence": {
            "factor": "is_late_delivery", "grade": grade,
            "p": p_raw, "or": ct["or"], "or_ci": ct["or_ci"], "n": ct["n"],
        },
        "load_profile": {
            "strategy": "SQL聚合优先；行级检验逐次只读取两列",
            "max_interactive_python_columns": 2,
            "row_level_extracts": row_extracts,
            "logistic_enabled": include_logistic,
        },
        "sqls": sqls,
        "note": (
            "观察性数据，只谈关联、禁因果；轻量模式不在交互请求中运行Logistic。"
            if not include_logistic else
            "观察性数据，只谈关联、禁因果；深度Logistic已按模型串行运行。"
        ),
    }
