"""低评分专用的两层归因流水线。

第一层只保留 FDR 校正后显著且效应量 95%CI 不含无效值的变量；第二层按
预设共线性代表规则进入多变量二项 Logistic。最终只输出调整后关联与分布下钻，
不生成治理策略，也不支持把其他字段临时改成归因目标。
"""
from __future__ import annotations

import gc
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

from .binning import numeric_rate_bins
from .data_provider import DataProvider
from .deep_validation import _route_validation
from .model_cache import cached_frame
from .statistics import (
    ORDER_WHERE,
    SELLER_WHERE,
    categorical_test_counts,
    chi_square_rc_counts,
    distribution_test,
    load_group_counts,
    load_table,
    logistic_model_formula,
    multiple_correction,
    trend_test,
)

ORDER_TABLE = "mart_order_delivery"
SELLER_TABLE = "mart_order_seller_delivery"
ALPHA = 0.05
DELAY_RANK = {"按时": 0, "1-3天": 1, "4-7天": 2, "8-14天": 3, "15天+": 4}


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    label: str
    table: str
    field: str
    kind: str
    collinear_group: str
    representative_rank: int = 0


FEATURE_SPECS = (
    FeatureSpec("is_late_delivery", "是否延迟", ORDER_TABLE,
                "is_late_delivery", "binary", "delivery_result", 0),
    FeatureSpec("delay_bucket", "延迟分档", ORDER_TABLE,
                "delay_bucket", "ordinal", "delivery_result", 1),
    FeatureSpec("late_days", "延迟天数", ORDER_TABLE,
                "late_days", "numeric", "delivery_result", 2),
    FeatureSpec("fulfillment_days", "总履约时长", ORDER_TABLE,
                "fulfillment_days", "numeric", "delivery_result", 3),
    FeatureSpec("approval_days", "支付审批时长", ORDER_TABLE,
                "approval_days", "numeric", "approval_time"),
    FeatureSpec("customer_state", "客户州", ORDER_TABLE,
                "customer_state", "categorical", "customer_region"),
    FeatureSpec("primary_category_name", "主要品类", ORDER_TABLE,
                "primary_category_name", "categorical", "category"),
    FeatureSpec("primary_payment_type", "支付方式", ORDER_TABLE,
                "primary_payment_type", "categorical", "payment_channel"),
    FeatureSpec("order_month", "购买月份", ORDER_TABLE,
                "order_month", "categorical", "purchase_time"),
    FeatureSpec("price_total", "商品金额", ORDER_TABLE,
                "price_total", "numeric", "order_value"),
    FeatureSpec("freight_ratio", "运费率", ORDER_TABLE,
                "freight_ratio", "numeric", "freight_burden"),
    FeatureSpec("item_count", "商品项数量", ORDER_TABLE,
                "item_count", "numeric", "order_complexity"),
    FeatureSpec("is_multi_seller_order", "是否多卖家订单", ORDER_TABLE,
                "is_multi_seller_order", "binary", "seller_complexity"),
    FeatureSpec("cross_state", "是否跨州", SELLER_TABLE,
                "cross_state", "binary", "shipping_geography", 0),
    FeatureSpec("distance_km", "近似配送距离", SELLER_TABLE,
                "approximate_distance_km", "numeric", "shipping_geography", 1),
    FeatureSpec("seller_state", "卖家州", SELLER_TABLE,
                "seller_state", "categorical", "seller_region"),
    FeatureSpec("route", "卖家州→客户州线路", SELLER_TABLE,
                "route", "categorical", "route"),
    FeatureSpec("is_any_item_handover_late", "是否存在交接超期", SELLER_TABLE,
                "is_any_item_handover_late", "binary", "handover_result"),
)
SPEC_BY_NAME = {spec.name: spec for spec in FEATURE_SPECS}


CONTROL_POLICY = {
    "order": [
        "购买月份（order_month）",
        "客户州（customer_state）",
        "主要品类（primary_category_name）",
        "对数商品金额（price_total）",
        "运费率（freight_ratio）",
        "商品项数量（item_count）",
        "是否多卖家订单（is_multi_seller_order）",
        "主要支付方式（primary_payment_type）",
        "承诺交付天数（promised_delivery_days）",
    ],
    "seller": [
        "购买月份（order_month）",
        "客户州（customer_state）",
        "主要品类（primary_category_name）",
        "是否最终延迟（is_late_delivery）",
        "对数卖家商品金额（seller_price）",
        "卖家运费率（seller_freight_ratio）",
        "卖家商品项数量（seller_items）",
    ],
    "selection_rule": (
        "如果多个变量表达的信息高度重复（共线性），只保留业务含义最直观的一个，"
        "避免同一信息被模型重复计算；不会临时按最小p值或最大OR挑选。"
    ),
}


def _where(spec: FeatureSpec) -> str:
    return ORDER_WHERE if spec.table == ORDER_TABLE else SELLER_WHERE


def _float(value, default=None):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _effect_ci_passed(ci: list[float] | None, null_value: float) -> bool:
    if not ci or len(ci) != 2:
        return False
    lo, hi = _float(ci[0]), _float(ci[1])
    return bool(lo is not None and hi is not None and (hi < null_value or lo > null_value))


def _rank_biserial_ci(u: float, n0: int, n1: int) -> list[float]:
    """用 AUC 的大样本方差近似秩二列相关的95%CI。"""
    if n0 <= 1 or n1 <= 1:
        return [-1.0, 1.0]
    auc = min(1.0, max(0.0, u / (n0 * n1)))
    q1 = auc / (2 - auc) if auc < 2 else 0.0
    q2 = 2 * auc * auc / (1 + auc) if auc > -1 else 0.0
    variance = (
        auc * (1 - auc)
        + (n0 - 1) * (q1 - auc * auc)
        + (n1 - 1) * (q2 - auc * auc)
    ) / (n0 * n1)
    se = 2 * math.sqrt(max(0.0, variance))
    effect = 1 - 2 * auc
    return [max(-1.0, effect - 1.96 * se), min(1.0, effect + 1.96 * se)]


def _fisher_rho_ci(rho: float, n: int) -> list[float]:
    if n <= 3 or not -1 < rho < 1:
        return [rho, rho]
    z = math.atanh(rho)
    delta = 1.96 / math.sqrt(n - 3)
    return [math.tanh(z - delta), math.tanh(z + delta)]


def _cramers_v(table: np.ndarray) -> float:
    if table.size == 0 or min(table.shape) < 2 or table.sum() <= 0:
        return 0.0
    try:
        chi2 = stats.chi2_contingency(table, correction=False)[0]
    except ValueError:
        return 0.0
    denom = table.sum() * max(1, min(table.shape) - 1)
    return math.sqrt(float(chi2) / denom) if denom else 0.0


def _cramers_v_ci(table: np.ndarray, seed: int, draws: int = 300) -> list[float]:
    """对聚合列联表做确定性多项分布bootstrap，不拉取明细行。"""
    n = int(table.sum())
    if n <= 1:
        return [0.0, 1.0]
    probabilities = table.reshape(-1).astype(float) / n
    rng = np.random.default_rng(seed)
    values = []
    for draw in rng.multinomial(n, probabilities, size=draws):
        value = _cramers_v(draw.reshape(table.shape))
        if math.isfinite(value):
            values.append(value)
    if not values:
        return [0.0, 1.0]
    return [float(np.quantile(values, 0.025)), float(np.quantile(values, 0.975))]


def _two_by_two(group_low: int, group_non_low: int,
                rest_low: int, rest_non_low: int) -> dict:
    counts = pd.DataFrame([
        {"group": 1, "is_low_score": 1, "n": group_low},
        {"group": 1, "is_low_score": 0, "n": group_non_low},
        {"group": 0, "is_low_score": 1, "n": rest_low},
        {"group": 0, "is_low_score": 0, "n": rest_non_low},
    ])
    return categorical_test_counts(counts, "group", "is_low_score")


def _group_details(counts: pd.DataFrame, field: str,
                   min_group_sample: int) -> tuple[list[dict], pd.DataFrame]:
    data = counts.dropna(subset=[field, "is_low_score", "n"]).copy()
    data["is_low_score"] = pd.to_numeric(data["is_low_score"], errors="coerce")
    data["n"] = pd.to_numeric(data["n"], errors="coerce")
    totals = data.groupby(field)["n"].sum()
    keep = set(totals[totals >= min_group_sample].index)
    data = data[data[field].isin(keep)]
    pivot = data.pivot_table(
        index=field, columns="is_low_score", values="n",
        aggfunc="sum", fill_value=0,
    ).reindex(columns=[0, 1], fill_value=0)
    total_n = int(pivot.to_numpy().sum())
    total_low = int(pivot[1].sum())
    base_rate = total_low / total_n if total_n else 0.0
    details = []
    raw_tests = []
    for value, row in pivot.iterrows():
        sample = int(row[0] + row[1])
        low = int(row[1])
        rest_low = total_low - low
        rest_non_low = total_n - total_low - int(row[0])
        test = _two_by_two(low, int(row[0]), rest_low, rest_non_low)
        rate = low / sample if sample else 0.0
        details.append({
            "value": value, "sample": sample, "low_score_count": low,
            "low_score_rate": rate, "base_rate": base_rate,
            "rate_difference": rate - base_rate,
            "lift": rate / base_rate if base_rate else None,
            "excess_low_score": sample * max(rate - base_rate, 0),
            "or": test.get("or"), "ci95": test.get("or_ci"),
            "p": test.get("p"),
        })
        raw_tests.append(test.get("p", 1.0))
    if details:
        correction = multiple_correction(raw_tests)
        for index, detail in enumerate(details):
            detail["p_adjusted"] = correction["p_adjusted"][index]
            detail["significant_risk"] = bool(
                detail["p_adjusted"] < ALPHA
                and detail["low_score_rate"] > base_rate
                and _effect_ci_passed(detail.get("ci95"), 1.0)
            )
    details.sort(
        key=lambda row: (row["significant_risk"], row["excess_low_score"],
                         row["rate_difference"]),
        reverse=True,
    )
    return details, pivot


def _screen_binary(provider: DataProvider, spec: FeatureSpec,
                   sqls: list[str]) -> dict:
    counts = load_group_counts(
        provider, spec.table, spec.field, where=_where(spec), sql_sink=sqls,
    )
    test = categorical_test_counts(counts, spec.field, "is_low_score")
    if "error" in test:
        return {"ok": False, "error": test["error"]}
    details, _ = _group_details(counts, spec.field, 1)
    return {
        "ok": True, "method": (
            "两组比例比较：Fisher精确检验" if test["method"] == "fisher"
            else "两组比例比较：Pearson卡方检验（Yates校正）"
        ),
        "p": float(test["p"]), "effect_name": "OR",
        "effect_value": float(test["or"]), "ci95": test.get("or_ci"),
        "null_value": 1.0, "sample": int(test["n"]),
        "details": details,
    }


def _screen_categorical(provider: DataProvider, spec: FeatureSpec,
                        min_group_sample: int, sqls: list[str]) -> dict:
    counts = load_group_counts(
        provider, spec.table, spec.field, where=_where(spec), sql_sink=sqls,
    )
    total = int(counts["n"].sum()) if not counts.empty else 0
    dynamic_min = max(
        min_group_sample,
        math.ceil(total * (0.001 if spec.name == "route" else 0.0005)),
    )
    details, pivot = _group_details(counts, spec.field, dynamic_min)
    if min(pivot.shape, default=0) < 2:
        return {"ok": False, "error": f"达到样本门槛{dynamic_min}的分组不足"}
    prepared = counts[counts[spec.field].isin(pivot.index)]
    test = chi_square_rc_counts(prepared, spec.field, "is_low_score")
    if "error" in test:
        return {"ok": False, "error": test["error"]}
    table = pivot.to_numpy(dtype=float)
    effect = _cramers_v(table)
    ci = _cramers_v_ci(table, seed=sum(map(ord, spec.name)))
    return {
        "ok": True, "method": "多组比例比较：Pearson卡方独立性检验",
        "p": float(test["p"]), "effect_name": "Cramér's V",
        "effect_value": effect, "ci95": ci, "null_value": 0.0,
        "sample": int(table.sum()), "groups_tested": int(pivot.shape[0]),
        "min_group_sample": dynamic_min,
        "assumption_ok": test.get("assumption_ok", True),
        "low_expected_share": test.get("low_expected_share"),
        "details": details,
    }


def _numeric_details(df: pd.DataFrame, field: str) -> dict:
    summary = []
    for target, label in ((0, "非低评分"), (1, "低评分")):
        values = df.loc[df["is_low_score"] == target, field].dropna()
        if values.empty:
            continue
        summary.append({
            "group": label, "sample": int(len(values)),
            "p25": float(values.quantile(0.25)),
            "median": float(values.median()),
            "p75": float(values.quantile(0.75)),
            "mean": float(values.mean()),
        })
    binned = numeric_rate_bins(df, field, "is_low_score")
    bins = [
        {
            "value_range": row["value_range"],
            "sample": row["sample"],
            "low_score_count": row["target_count"],
            "low_score_rate": row["target_rate"],
        }
        for row in binned["rows"]
    ]
    return {
        "by_target": summary,
        "quantile_bins": bins,
        "binning_method": binned["method"],
        "binning_note": binned["note"],
    }


def _screen_numeric(provider: DataProvider, spec: FeatureSpec,
                    sqls: list[str]) -> dict:
    df = load_table(
        provider, spec.table, [spec.field, "is_low_score"],
        where=_where(spec), sql_sink=sqls,
    )
    df[spec.field] = pd.to_numeric(df[spec.field], errors="coerce")
    df["is_low_score"] = pd.to_numeric(df["is_low_score"], errors="coerce")
    df = df.dropna(subset=[spec.field, "is_low_score"])
    test = distribution_test(df, spec.field, "is_low_score")
    if "error" in test:
        return {"ok": False, "error": test["error"]}
    n0, n1 = int(test["n0"]), int(test["n1"])
    ci = _rank_biserial_ci(float(test["u"]), n0, n1)
    return {
        "ok": True, "method": "两组分布比较：Mann–Whitney U检验",
        "p": float(test["p"]), "effect_name": "秩二列相关",
        "effect_value": float(test["effect_size"]), "ci95": ci,
        "null_value": 0.0, "sample": n0 + n1,
        "details": _numeric_details(df, spec.field),
    }


def _screen_ordinal(provider: DataProvider, spec: FeatureSpec,
                    sqls: list[str]) -> dict:
    df = load_table(
        provider, spec.table, [spec.field, "is_low_score"],
        where=_where(spec), sql_sink=sqls,
    )
    df["rank"] = df[spec.field].map(DELAY_RANK)
    df["is_low_score"] = pd.to_numeric(df["is_low_score"], errors="coerce")
    df = df.dropna(subset=["rank", "is_low_score"])
    test = trend_test(df, "rank", "is_low_score")
    if "error" in test:
        return {"ok": False, "error": test["error"]}
    rho_result = stats.spearmanr(df["rank"], df["is_low_score"])
    rho = float(rho_result.statistic)
    levels = []
    for value, group in df.groupby(spec.field, dropna=False):
        levels.append({
            "value": value, "sample": int(len(group)),
            "low_score_count": int(group["is_low_score"].sum()),
            "low_score_rate": float(group["is_low_score"].mean()),
            "rank": int(group["rank"].iloc[0]),
        })
    levels.sort(key=lambda row: row["rank"])
    return {
        "ok": True, "method": "有序趋势检验：Cochran–Armitage检验",
        "p": float(test["p"]), "effect_name": "Spearman ρ",
        "effect_value": rho, "ci95": _fisher_rho_ci(rho, len(df)),
        "null_value": 0.0, "sample": int(len(df)), "details": levels,
    }


def screen_low_score_features(provider: DataProvider,
                              min_group_sample: int = 100) -> dict:
    """第一层：统一以是否低评分为目标，执行FDR+95%CI双门槛。"""
    sqls: list[str] = []
    rows: list[dict] = []
    handlers = {
        "binary": lambda spec: _screen_binary(provider, spec, sqls),
        "categorical": lambda spec: _screen_categorical(
            provider, spec, min_group_sample, sqls
        ),
        "numeric": lambda spec: _screen_numeric(provider, spec, sqls),
        "ordinal": lambda spec: _screen_ordinal(provider, spec, sqls),
    }
    for spec in FEATURE_SPECS:
        try:
            result = handlers[spec.kind](spec)
        except Exception as error:
            result = {"ok": False, "error": f"{type(error).__name__}: {error}"}
        rows.append({
            "feature": spec.name, "label": spec.label, "table": spec.table,
            "field": spec.field, "kind": spec.kind,
            "collinear_group": spec.collinear_group,
            "representative_rank": spec.representative_rank,
            **result,
        })

    valid = [row for row in rows if row.get("ok") and isinstance(row.get("p"), float)]
    if valid:
        correction = multiple_correction([row["p"] for row in valid])
        for index, row in enumerate(valid):
            row["p_adjusted"] = correction["p_adjusted"][index]
            row["ci_passed"] = _effect_ci_passed(
                row.get("ci95"), float(row.get("null_value", 0.0))
            )
            row["retained"] = bool(
                row["p_adjusted"] < ALPHA
                and row["ci_passed"]
                and row.get("assumption_ok") is not False
            )

    retained = [row for row in rows if row.get("retained")]
    by_group: dict[str, list[dict]] = {}
    for row in retained:
        by_group.setdefault(row["collinear_group"], []).append(row)
    selected = []
    for group, members in by_group.items():
        representative = sorted(
            members, key=lambda row: (row["representative_rank"], row["feature"])
        )[0]
        representative["selected_for_logistic"] = True
        representative["selection_reason"] = (
            "同组变量信息重复，选择业务含义最直观的代表变量"
            if len(members) > 1 else
            "没有表达相同信息的变量，直接进入多变量模型"
        )
        selected.append(representative)
        for row in members:
            if row is not representative:
                row["selected_for_logistic"] = False
                row["selection_reason"] = (
                    f"与{representative['label']}表达的信息高度重复，为避免重复计算而不同时纳入模型"
                )
    return {
        "ok": True, "target": "is_low_score", "target_label": "是否低评分（1-3分）",
        "alpha": ALPHA, "multiple_correction": "FDR-BH",
        "tests": rows, "retained": retained, "selected": selected,
        "sqls": sqls,
    }


def _collapse_rare(df: pd.DataFrame, columns: list[str], min_count: int) -> None:
    for column in columns:
        values = df[column].astype("string").fillna("unknown")
        counts = values.value_counts(dropna=False)
        keep = set(counts[counts >= min_count].index)
        df[column] = values.where(values.isin(keep), "OTHER").astype(str)


def _zscore(df: pd.DataFrame, source: str, target: str) -> None:
    values = pd.to_numeric(df[source], errors="coerce")
    median = values.median()
    values = values.fillna(median)
    std = values.std(ddof=0)
    df[target] = (values - values.mean()) / std if std and math.isfinite(std) else 0.0


def _fit(df: pd.DataFrame, formula: str, label: str) -> dict:
    try:
        result = logistic_model_formula(df, formula)
        return {"ok": True, "label": label, "formula": formula, **result}
    except Exception as error:
        return {
            "ok": False, "label": label, "formula": formula,
            "error": f"{type(error).__name__}: 模型未能稳定估计",
        }


def _term(model: dict, term: str) -> dict | None:
    return next((row for row in model.get("terms", []) if row["term"] == term), None)


def _joint(model: dict, term: str) -> dict | None:
    return next((row for row in model.get("joint_tests", []) if row["term"] == term), None)


def _adjusted_direct(spec: FeatureSpec, model: dict, term: str,
                     unit: str) -> dict:
    row = _term(model, term) if model.get("ok") else None
    if not row:
        return {"feature": spec.name, "label": spec.label, "ok": False,
                "error": model.get("error", "模型中没有可估计项")}
    ci = [float(row["ci95"][0]), float(row["ci95"][1])]
    return {
        "feature": spec.name, "label": spec.label, "ok": True,
        "model": model["label"],
        "method": "多变量二项逻辑回归（HC3稳健标准误）",
        "fit_method": model.get("fit_method"),
        "effect": f"控制其他因素后的优势比OR（{unit}）",
        "adjusted_or": float(row["or"]),
        "ci95": ci, "p": float(row["p"]),
        "ci_passed": _effect_ci_passed(ci, 1.0), "sample": model.get("nobs"),
    }


def _adjusted_categorical(spec: FeatureSpec, model: dict, term: str) -> dict:
    joint = _joint(model, term) if model.get("ok") else None
    if not joint:
        return {"feature": spec.name, "label": spec.label, "ok": False,
                "error": model.get("error", "分类变量联合Wald检验不可用")}
    prefix = term + "[T."
    levels = []
    for row in model.get("terms", []):
        if not row["term"].startswith(prefix):
            continue
        levels.append({
            "level": row["term"].removeprefix(prefix).removesuffix("]"),
            "adjusted_or": float(row["or"]),
            "ci95": [float(row["ci95"][0]), float(row["ci95"][1])],
            "p": float(row["p"]),
        })
    if levels:
        correction = multiple_correction([row["p"] for row in levels])
        for index, row in enumerate(levels):
            row["p_adjusted"] = correction["p_adjusted"][index]
            row["stable_level"] = bool(
                row["p_adjusted"] < ALPHA
                and _effect_ci_passed(row["ci95"], 1.0)
            )
    return {
        "feature": spec.name, "label": spec.label, "ok": True,
        "model": model["label"],
        "method": "多变量逻辑回归中的分类变量整体检验（联合Wald检验，HC3稳健标准误）",
        "fit_method": model.get("fit_method"),
        "effect": "分类变量整体调整后关联", "adjusted_or": None,
        "ci95": None, "p": float(joint["p"]),
        "ci_passed": any(row.get("stable_level") for row in levels),
        "sample": model.get("nobs"), "level_results": levels,
    }


def _selected(screening: dict, table: str) -> list[FeatureSpec]:
    return [
        SPEC_BY_NAME[row["feature"]] for row in screening.get("selected", [])
        if row["table"] == table
    ]


_ORDER_ZSCORE = {
    "price_total": "z_price", "freight_ratio": "z_freight_ratio",
    "item_count": "z_item_count", "promised_delivery_days": "z_promised_days",
    "fulfillment_days": "z_fulfillment", "approval_days": "z_approval",
    "late_days": "z_late_days",
}
_SELLER_ZSCORE = (
    ("seller_price", "z_seller_price"),
    ("seller_freight_ratio", "z_seller_freight_ratio"),
    ("seller_items", "z_seller_items"),
    ("approximate_distance_km", "z_distance"),
)


def _engineer_order(df: pd.DataFrame) -> pd.DataFrame:
    """订单级特征工程：数值化、去缺、稀疏类别合并、z 标准化、延迟分档秩。"""
    for column in ("is_low_score", "is_multi_seller_order"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=["is_low_score"])
    base_category_min = max(100, math.ceil(len(df) * 0.002))
    _collapse_rare(
        df, ["order_month", "customer_state", "primary_payment_type"],
        base_category_min,
    )
    # 品类基数最高且容易出现准完全分离，采用更高但仍保守的0.5%门槛。
    _collapse_rare(
        df, ["primary_category_name"],
        max(300, math.ceil(len(df) * 0.005)),
    )
    for source, target in _ORDER_ZSCORE.items():
        if source in df:
            _zscore(df, source, target)
    if "delay_bucket" in df:
        df["delay_rank"] = df["delay_bucket"].map(DELAY_RANK)
    return df


def _engineer_seller(df: pd.DataFrame) -> pd.DataFrame:
    """卖家级特征工程：数值化、去缺、稀疏类别合并、z 标准化。"""
    for column in ("is_low_score", "is_late_delivery"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=["is_low_score", "is_late_delivery"])
    base_category_min = max(100, math.ceil(len(df) * 0.002))
    _collapse_rare(df, ["order_month", "customer_state"], base_category_min)
    _collapse_rare(
        df, ["primary_category_name"],
        max(300, math.ceil(len(df) * 0.005)),
    )
    for source, target in _SELLER_ZSCORE:
        if source in df:
            _zscore(df, source, target)
    if "seller_state" in df.columns:
        _collapse_rare(df, ["seller_state"], base_category_min)
    for column in ("cross_state", "is_any_item_handover_late"):
        if column in df:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    return df


def _run_order_model(provider: DataProvider, specs: list[FeatureSpec],
                     sqls: list[str]) -> tuple[dict | None, list[dict], dict]:
    if not specs:
        return None, [], {"table": ORDER_TABLE, "rows": 0, "columns": 0}
    base_columns = {
        "is_low_score", "order_month", "customer_state", "primary_category_name",
        "primary_payment_type", "price_total", "freight_ratio", "item_count",
        "is_multi_seller_order", "promised_delivery_days",
    }
    columns = sorted(base_columns | {spec.field for spec in specs})
    df = cached_frame(
        provider, ORDER_TABLE, columns, ORDER_WHERE, _engineer_order, sql_sink=sqls,
    )

    terms = {
        "C(order_month)", "C(customer_state)", "C(primary_category_name)",
        "C(primary_payment_type)", "z_price", "z_freight_ratio",
        "z_item_count", "is_multi_seller_order", "z_promised_days",
    }
    direct_map = {
        "is_late_delivery": ("is_late_delivery", "延迟相对非延迟"),
        "delay_bucket": ("delay_rank", "延迟分档每升高1级"),
        "fulfillment_days": ("z_fulfillment", "增加1个标准差"),
        "late_days": ("z_late_days", "增加1个标准差"),
        "approval_days": ("z_approval", "增加1个标准差"),
        "price_total": ("z_price", "增加1个标准差"),
        "freight_ratio": ("z_freight_ratio", "增加1个标准差"),
        "item_count": ("z_item_count", "增加1个标准差"),
        "is_multi_seller_order": ("is_multi_seller_order", "多卖家相对单卖家"),
    }
    categorical_map = {
        "customer_state": "C(customer_state)",
        "primary_category_name": "C(primary_category_name)",
        "primary_payment_type": "C(primary_payment_type)",
        "order_month": "C(order_month)",
    }
    for spec in specs:
        if spec.name in direct_map:
            terms.add(direct_map[spec.name][0])
        elif spec.name in categorical_map:
            terms.add(categorical_map[spec.name])
    formula = "is_low_score ~ " + " + ".join(sorted(terms))
    model = _fit(df, formula, "订单级自动调整模型")
    results = []
    for spec in specs:
        if spec.name in direct_map:
            term, unit = direct_map[spec.name]
            results.append(_adjusted_direct(spec, model, term, unit))
        elif spec.name in categorical_map:
            results.append(_adjusted_categorical(spec, model, categorical_map[spec.name]))
    profile = {"table": ORDER_TABLE, "rows": int(len(df)), "columns": len(columns)}
    del df
    gc.collect()
    return model, results, profile


def _run_seller_model(provider: DataProvider, specs: list[FeatureSpec],
                      sqls: list[str]) -> tuple[dict | None, list[dict], dict]:
    modeled = [spec for spec in specs if spec.name != "route"]
    if not modeled:
        return None, [], {"table": SELLER_TABLE, "rows": 0, "columns": 0}
    base_columns = {
        "is_low_score", "order_month", "customer_state", "primary_category_name",
        "is_late_delivery", "seller_price", "seller_freight_ratio", "seller_items",
    }
    columns = sorted(base_columns | {spec.field for spec in modeled})
    df = cached_frame(
        provider, SELLER_TABLE, columns, SELLER_WHERE, _engineer_seller, sql_sink=sqls,
    )
    terms = {
        "C(order_month)", "C(customer_state)", "C(primary_category_name)",
        "is_late_delivery", "z_seller_price", "z_seller_freight_ratio",
        "z_seller_items",
    }
    direct_map = {
        "cross_state": ("cross_state", "跨州相对同州"),
        "distance_km": ("z_distance", "增加1个标准差"),
        "is_any_item_handover_late": (
            "is_any_item_handover_late", "存在交接超期相对不存在"
        ),
    }
    categorical_map = {"seller_state": "C(seller_state)"}
    for spec in modeled:
        if spec.name in direct_map:
            terms.add(direct_map[spec.name][0])
        elif spec.name in categorical_map:
            terms.add(categorical_map[spec.name])
    formula = "is_low_score ~ " + " + ".join(sorted(terms))
    model = _fit(df, formula, "订单-卖家级自动调整模型")
    results = []
    for spec in modeled:
        if spec.name in direct_map:
            term, unit = direct_map[spec.name]
            results.append(_adjusted_direct(spec, model, term, unit))
        elif spec.name in categorical_map:
            results.append(_adjusted_categorical(spec, model, categorical_map[spec.name]))
    profile = {"table": SELLER_TABLE, "rows": int(len(df)), "columns": len(columns)}
    del df
    gc.collect()
    return model, results, profile


def run_adjusted_validation(provider: DataProvider, screening: dict) -> dict:
    """第二层：只解释第一层保留后选出的共线性代表变量。"""
    sqls: list[str] = []
    models = []
    results: list[dict] = []
    profiles = []
    order_model, order_results, order_profile = _run_order_model(
        provider, _selected(screening, ORDER_TABLE), sqls
    )
    if order_model:
        models.append(order_model)
    results.extend(order_results)
    profiles.append(order_profile)

    seller_specs = _selected(screening, SELLER_TABLE)
    seller_model, seller_results, seller_profile = _run_seller_model(
        provider, seller_specs, sqls
    )
    if seller_model:
        models.append(seller_model)
    results.extend(seller_results)
    profiles.append(seller_profile)

    route_validation = None
    if any(spec.name == "route" for spec in seller_specs):
        route_columns = [
            "order_purchase_timestamp", "is_low_score", "is_late_delivery",
            "route", "seller_price",
        ]
        route_df = load_table(
            provider, SELLER_TABLE, route_columns, where=SELLER_WHERE, sql_sink=sqls,
        )
        route_validation = _route_validation(route_df)
        del route_df
        gc.collect()
        stable_routes = [
            row for row in route_validation.get("routes", [])
            if row.get("stability") == "稳定复现"
        ] if route_validation.get("ok") else []
        route_p = min(
            (row.get("adjusted_p_fdr", 1.0)
             for row in route_validation.get("routes", [])),
            default=1.0,
        ) if route_validation.get("ok") else 1.0
        results.append({
            "feature": "route", "label": SPEC_BY_NAME["route"].label,
            "ok": bool(route_validation.get("ok")),
            "model": "线路：较早时期建模＋较晚时期独立验证",
            "method": "候选线路多变量模型＋较晚20%订单独立验证",
            "fit_method": "线路专项模型＋按时间划分的独立验证集",
            "effect": "控制其他因素后的线路优势比OR", "adjusted_or": None,
            "ci95": None, "p": float(route_p),
            "ci_passed": bool(stable_routes),
            "stable_routes": [row["route"] for row in stable_routes],
            "sample": route_validation.get("train_n"),
            "error": route_validation.get("error"),
        })

    valid = [row for row in results if row.get("ok") and isinstance(row.get("p"), float)]
    non_route = [row for row in valid if row["feature"] != "route"]
    if non_route:
        correction = multiple_correction([row["p"] for row in non_route])
        for index, row in enumerate(non_route):
            row["p_adjusted"] = correction["p_adjusted"][index]
    for row in valid:
        if row["feature"] == "route":
            row["p_adjusted"] = row["p"]
        row["stable"] = bool(row["p_adjusted"] < ALPHA and row.get("ci_passed"))
        row["conclusion"] = (
            "控制预设变量后仍显著且置信区间有效"
            if row["stable"] else
            "调整后未同时满足显著性与置信区间门槛"
        )
    return {
        "ok": True, "target": "is_low_score", "results": results,
        "stable": [row for row in results if row.get("stable")],
        "models": models, "route_validation": route_validation,
        "control_policy": CONTROL_POLICY, "sqls": sqls,
        "load_profile": profiles,
    }


def build_adjusted_explanations(screening: dict, adjusted: dict) -> list[dict]:
    """只为控制其他因素后仍显著的变量保留分布和对象明细。"""
    screen_by_feature = {row["feature"]: row for row in screening.get("tests", [])}
    explanations = []
    for result in adjusted.get("stable", []):
        screen = screen_by_feature.get(result["feature"], {})
        details = screen.get("details")
        route_validation_rows = None
        if result["feature"] == "route":
            stable_routes = set(result.get("stable_routes", []))
            details = [
                row for row in (details or []) if row.get("value") in stable_routes
            ]
            route_validation_rows = [
                row for row in (
                    (adjusted.get("route_validation") or {}).get("routes", [])
                )
                if row.get("route") in stable_routes
            ]
        explanations.append({
            "feature": result["feature"], "label": result["label"],
            "kind": screen.get("kind"), "adjusted_result": result,
            "details": details, "route_validation": route_validation_rows,
            "delay_visualization": (
                adjusted.get("delay_visualizations", {}).get(result["feature"])
            ),
            "interpretation": (
                "以下分布用于说明统计关联主要出现在哪些对象或区间；不能据此认定因果，也不会自动生成治理策略。"
            ),
        })
    return explanations


def _delay_status(value) -> str | None:
    try:
        return {0: "未延迟", 1: "延迟"}.get(int(value))
    except (TypeError, ValueError):
        return None


def _delay_distribution_rows(grouped: pd.DataFrame, group_column: str,
                             denominators: dict | None = None) -> list[dict]:
    """把分组×是否延迟计数转换为两组内部占比，避免总量差异误导。"""
    data = grouped.copy()
    data["is_late_delivery"] = pd.to_numeric(
        data["is_late_delivery"], errors="coerce"
    )
    data["n"] = pd.to_numeric(data["n"], errors="coerce").fillna(0)
    data = data[data["is_late_delivery"].isin([0, 1])]
    if denominators is None:
        denominators = data.groupby("is_late_delivery")["n"].sum().to_dict()
    rows = []
    for _, row in data.iterrows():
        late_value = int(row["is_late_delivery"])
        sample = int(row["n"])
        denominator = float(denominators.get(late_value, 0) or 0)
        rows.append({
            "group": str(row[group_column]),
            "delay_status": _delay_status(late_value),
            "sample": sample,
            "within_delay_share": sample / denominator if denominator else None,
        })
    return rows


def build_delay_visualizations(provider: DataProvider, screening: dict,
                               adjusted: dict) -> tuple[dict, list[str]]:
    """仅为控制其他因素后仍显著的变量生成按延迟/未延迟分层的描述性分布。"""
    visuals: dict[str, dict] = {}
    sqls: list[str] = []
    screen_by_feature = {row["feature"]: row for row in screening.get("tests", [])}
    for result in adjusted.get("stable", []):
        spec = SPEC_BY_NAME[result["feature"]]
        if spec.name == "is_late_delivery":
            details = screen_by_feature.get(spec.name, {}).get("details") or []
            total = sum(int(row.get("sample") or 0) for row in details)
            rows = []
            for row in details:
                status = _delay_status(row.get("value"))
                if status is None:
                    continue
                sample = int(row.get("sample") or 0)
                rows.append({
                    "group": status, "delay_status": status,
                    "sample": sample,
                    "within_delay_share": sample / total if total else None,
                    "low_score_rate": _float(row.get("low_score_rate")),
                })
            visuals[spec.name] = {
                "ok": bool(rows), "chart_type": "delay_outcome",
                "rows": rows,
                "note": "是否延迟本身无法再按是否延迟拆分，因此展示两组低评分率。",
            }
            continue

        if spec.kind == "numeric":
            frame = load_table(
                provider, spec.table, [spec.field, "is_late_delivery"],
                where=_where(spec), sql_sink=sqls,
            )
            frame[spec.field] = pd.to_numeric(frame[spec.field], errors="coerce")
            frame["is_late_delivery"] = pd.to_numeric(
                frame["is_late_delivery"], errors="coerce"
            )
            frame = frame.dropna(subset=[spec.field, "is_late_delivery"])
            frame = frame[frame["is_late_delivery"].isin([0, 1])]
            try:
                frame["_bin"] = pd.qcut(frame[spec.field], q=5, duplicates="drop")
                categories = list(frame["_bin"].cat.categories)
            except (TypeError, ValueError):
                categories = []
            if len(categories) < 2:
                unique_values = sorted(frame[spec.field].dropna().unique())
                if 2 <= len(unique_values) <= 20:
                    def discrete_label(value) -> str:
                        return (
                            str(int(value)) if float(value).is_integer()
                            else f"{float(value):.2f}"
                        )
                    labels = {value: discrete_label(value) for value in unique_values}
                    frame["_bin_label"] = frame[spec.field].map(labels)
                    grouped = frame.groupby(
                        ["_bin_label", "is_late_delivery"], observed=True,
                        as_index=False,
                    ).size().rename(columns={"size": "n"})
                    rows = _delay_distribution_rows(grouped, "_bin_label")
                    visuals[spec.name] = {
                        "ok": True, "chart_type": "binned_bar", "rows": rows,
                        "group_order": [labels[value] for value in unique_values],
                        "note": "该数值变量取值较少，按实际取值展示；柱高为延迟组或未延迟组内部占比。",
                    }
                else:
                    visuals[spec.name] = {
                        "ok": False, "chart_type": "binned_bar", "rows": [],
                        "error": "数值变异不足，无法形成至少两个分箱",
                    }
            else:
                labels = {
                    interval: f"{interval.left:.2f}–{interval.right:.2f}"
                    for interval in categories
                }
                frame["_bin_label"] = frame["_bin"].map(labels).astype(str)
                grouped = frame.groupby(
                    ["_bin_label", "is_late_delivery"], observed=True,
                    as_index=False,
                ).size().rename(columns={"size": "n"})
                rows = _delay_distribution_rows(grouped, "_bin_label")
                order = [labels[interval] for interval in categories]
                visuals[spec.name] = {
                    "ok": True, "chart_type": "binned_bar", "rows": rows,
                    "group_order": order,
                    "note": "连续变量按全体有效样本五分位分箱；柱高为延迟组或未延迟组内部占比。",
                }
            del frame
            gc.collect()
            continue

        counts = load_group_counts(
            provider, spec.table, spec.field, target="is_late_delivery",
            where=_where(spec), sql_sink=sqls,
        )
        if counts.empty:
            visuals[spec.name] = {
                "ok": False, "chart_type": "grouped_bar", "rows": [],
                "error": "没有可用的延迟分层计数",
            }
            continue
        counts["n"] = pd.to_numeric(counts["n"], errors="coerce").fillna(0)
        counts["is_late_delivery"] = pd.to_numeric(
            counts["is_late_delivery"], errors="coerce"
        )
        counts = counts.dropna(subset=[spec.field, "is_late_delivery"])
        denominators = counts.groupby("is_late_delivery")["n"].sum().to_dict()
        totals = counts.groupby(spec.field, dropna=False)["n"].sum().sort_values(
            ascending=False
        )
        if spec.name == "route":
            allowed = set(result.get("stable_routes", []))
            order = [value for value in totals.index if value in allowed][:12]
            counts = counts[counts[spec.field].isin(order)]
        else:
            order = list(totals.index[:12])
            counts["_display_group"] = counts[spec.field].where(
                counts[spec.field].isin(order), "其他"
            )
            if len(totals) > len(order):
                order.append("其他")
            counts = counts.groupby(
                ["_display_group", "is_late_delivery"], observed=True,
                as_index=False,
            )["n"].sum()
            counts = counts.rename(columns={"_display_group": spec.field})
        rows = _delay_distribution_rows(
            counts, spec.field, denominators=denominators
        )
        visuals[spec.name] = {
            "ok": bool(rows), "chart_type": "grouped_bar", "rows": rows,
            "group_order": [str(value) for value in order],
            "note": (
                "线路仅展示在较早时期建模后、又在较晚时期订单中保持同方向的对象；柱高仍以全部合格线路订单为分母。"
                if spec.name == "route" else
                "展示样本量最高的12组，其余合并为“其他”；柱高为延迟组或未延迟组内部占比。"
            ),
        }
    return visuals, sqls


def run_low_score_attribution(provider: DataProvider,
                              min_group_sample: int = 100) -> dict:
    screening = screen_low_score_features(provider, min_group_sample)
    adjusted = run_adjusted_validation(provider, screening)
    delay_visualizations, visual_sqls = build_delay_visualizations(
        provider, screening, adjusted
    )
    adjusted["delay_visualizations"] = delay_visualizations
    explanations = build_adjusted_explanations(screening, adjusted)
    return {
        "ok": True, "target": "is_low_score",
        "target_label": "是否低评分（review_score <= 3）",
        "screening": screening, "adjusted_validation": adjusted,
        "explanations": explanations,
        "sqls": (
            screening.get("sqls", []) + adjusted.get("sqls", []) + visual_sqls
        ),
        "caveats": [
            "第一层只保留多重检验校正后p<0.05，且95%置信区间未跨过无效值的变量。",
            "信息高度重复的变量组只保留一个预设业务代表，避免同一信息被重复计算。",
            "控制其他已纳入因素后仍显著，只表示关联更稳定，仍不能证明因果。",
            "未记录的商品质量、包装、客服与评价正文原因意味着仍存在残余混杂。",
        ],
    }
