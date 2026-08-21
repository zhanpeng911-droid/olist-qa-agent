"""受控的多目标二分类关联因素分析。

当前只支持两个非评价目标：是否最终延迟、是否存在交接超期。低评分继续使用
``low_score_attribution`` 中已经稳定运行的同构流程。每个目标拥有预先锁定的
候选变量、样本口径和固定控制变量，后续结果变量不会反向进入较早目标的模型。
"""
from __future__ import annotations

import gc
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

from .binning import numeric_rate_bins
from .data_provider import DataProvider, SAMPLE_SOURCE_LABEL
from .low_score_attribution import (
    ALPHA,
    _adjusted_categorical,
    _adjusted_direct,
    _collapse_rare,
    _cramers_v,
    _cramers_v_ci,
    _effect_ci_passed,
    _fisher_rho_ci,
    _fit,
    _rank_biserial_ci,
    _zscore,
)
from .model_cache import cached_frame
from .statistics import (
    SINGLE_SELLER_SQL,
    VALID_SAMPLE_SQL,
    categorical_test_counts,
    chi_square_rc_counts,
    distribution_test,
    load_group_counts,
    load_table,
    multiple_correction,
)

ORDER_TABLE = "mart_order_delivery"
SELLER_TABLE = "mart_order_seller_delivery"


@dataclass(frozen=True)
class TargetFeatureSpec:
    name: str
    label: str
    table: str
    field: str
    kind: str
    collinear_group: str
    model_term: str
    unit: str = ""
    representative_rank: int = 0


@dataclass(frozen=True)
class TargetSpec:
    name: str
    label: str
    short_label: str
    positive_label: str
    negative_label: str
    primary_table: str
    candidates: tuple[TargetFeatureSpec, ...]
    where_by_table: dict[str, str]
    fixed_columns: dict[str, tuple[str, ...]]
    fixed_terms: dict[str, tuple[str, ...]]
    control_policy: dict[str, list[str] | str]


def _f(name: str, label: str, table: str, field: str, kind: str,
       group: str, term: str, unit: str = "", rank: int = 0) -> TargetFeatureSpec:
    return TargetFeatureSpec(
        name, label, table, field, kind, group, term, unit, rank
    )


# 延迟目标：订单级使用订单基础属性；订单-卖家级使用地理/线路和交接结果。
DELAY_FEATURES = (
    _f("approval_days", "支付审批时长", ORDER_TABLE, "approval_days", "numeric",
       "approval_time", "z_approval", "增加1个标准差"),
    _f("customer_state", "客户州", ORDER_TABLE, "customer_state", "categorical",
       "customer_region", "C(customer_state)"),
    _f("primary_category_name", "主要品类", ORDER_TABLE, "primary_category_name",
       "categorical", "category", "C(primary_category_name)"),
    _f("primary_payment_type", "支付方式", ORDER_TABLE, "primary_payment_type",
       "categorical", "payment_channel", "C(primary_payment_type)"),
    _f("order_month", "购买月份", ORDER_TABLE, "order_month", "categorical",
       "purchase_time", "C(order_month)"),
    _f("price_total", "商品金额", ORDER_TABLE, "price_total", "numeric",
       "order_value", "z_price", "增加1个标准差"),
    _f("freight_ratio", "运费率", ORDER_TABLE, "freight_ratio", "numeric",
       "freight_burden", "z_freight_ratio", "增加1个标准差"),
    _f("item_count", "商品项数量", ORDER_TABLE, "item_count", "numeric",
       "order_complexity", "z_item_count", "增加1个标准差"),
    _f("is_multi_seller_order", "是否多卖家订单", ORDER_TABLE,
       "is_multi_seller_order", "binary", "seller_complexity",
       "is_multi_seller_order", "多卖家相对单卖家"),
    _f("promised_delivery_days", "承诺交付天数", ORDER_TABLE,
       "promised_delivery_days", "numeric", "sla_window",
       "z_promised_days", "增加1个标准差"),
    _f("cross_state", "是否跨州", SELLER_TABLE, "cross_state", "binary",
       "shipping_geography", "cross_state", "跨州相对同州", 0),
    _f("distance_km", "近似配送距离", SELLER_TABLE,
       "approximate_distance_km", "numeric", "shipping_geography",
       "z_distance", "增加1个标准差", 1),
    _f("seller_state", "卖家州", SELLER_TABLE, "seller_state", "categorical",
       "seller_region", "C(seller_state)"),
    _f("route", "卖家州→客户州线路", SELLER_TABLE, "route", "categorical",
       "route", "C(route)"),
    _f("is_any_item_handover_late", "是否存在交接超期", SELLER_TABLE,
       "is_any_item_handover_late", "binary", "handover_result",
       "is_any_item_handover_late", "存在交接超期相对不存在"),
)


# 交接目标：只使用目标发生前已经确定的订单、商品、SLA和地理属性。
HANDOVER_FEATURES = (
    _f("order_month", "购买月份", SELLER_TABLE, "order_month", "categorical",
       "purchase_time", "C(order_month)"),
    _f("customer_state", "客户州", SELLER_TABLE, "customer_state", "categorical",
       "customer_region", "C(customer_state)"),
    _f("primary_category_name", "主要品类", SELLER_TABLE,
       "primary_category_name", "categorical", "category",
       "C(primary_category_name)"),
    _f("seller_price", "卖家商品金额", SELLER_TABLE, "seller_price", "numeric",
       "order_value", "z_seller_price", "增加1个标准差"),
    _f("seller_freight_ratio", "卖家运费率", SELLER_TABLE,
       "seller_freight_ratio", "numeric", "freight_burden",
       "z_seller_freight_ratio", "增加1个标准差"),
    _f("seller_items", "卖家商品项数量", SELLER_TABLE, "seller_items", "numeric",
       "order_complexity", "z_seller_items", "增加1个标准差"),
    _f("is_multi_seller_order", "是否多卖家订单", SELLER_TABLE,
       "is_multi_seller_order", "binary", "seller_complexity",
       "is_multi_seller_order", "多卖家相对单卖家"),
    _f("promised_delivery_days", "承诺交付天数", SELLER_TABLE,
       "promised_delivery_days", "numeric", "sla_window",
       "z_promised_days", "增加1个标准差"),
    _f("cross_state", "是否跨州", SELLER_TABLE, "cross_state", "binary",
       "shipping_geography", "cross_state", "跨州相对同州", 0),
    _f("distance_km", "近似配送距离", SELLER_TABLE,
       "approximate_distance_km", "numeric", "shipping_geography",
       "z_distance", "增加1个标准差", 1),
    _f("seller_state", "卖家州", SELLER_TABLE, "seller_state", "categorical",
       "seller_region", "C(seller_state)"),
    _f("route", "卖家州→客户州线路", SELLER_TABLE, "route", "categorical",
       "route", "C(route)"),
)


ORDER_CONTROLS = [
    "购买月份（order_month）", "客户州（customer_state）",
    "主要品类（primary_category_name）", "主要支付方式（primary_payment_type）",
    "对数商品金额（price_total）", "运费率（freight_ratio）",
    "商品项数量（item_count）", "是否多卖家订单（is_multi_seller_order）",
    "承诺交付天数（promised_delivery_days）",
]
SELLER_CONTROLS = [
    "购买月份（order_month）", "客户州（customer_state）",
    "主要品类（primary_category_name）", "对数卖家商品金额（seller_price）",
    "卖家运费率（seller_freight_ratio）", "卖家商品项数量（seller_items）",
]
SELECTION_RULE = (
    "如果多个变量表达的信息高度重复（共线性），只保留业务含义最直观的一个，"
    "不根据本次p值或OR临时选择。"
)


TARGET_SPECS = {
    "is_late_delivery": TargetSpec(
        name="is_late_delivery", label="是否最终延迟",
        short_label="延迟", positive_label="延迟", negative_label="未延迟",
        primary_table=ORDER_TABLE, candidates=DELAY_FEATURES,
        where_by_table={
            ORDER_TABLE: VALID_SAMPLE_SQL,
            SELLER_TABLE: f"{VALID_SAMPLE_SQL} AND {SINGLE_SELLER_SQL}",
        },
        fixed_columns={
            ORDER_TABLE: (
                "order_month", "customer_state", "primary_category_name",
                "primary_payment_type", "price_total", "freight_ratio",
                "item_count", "is_multi_seller_order", "promised_delivery_days",
            ),
            SELLER_TABLE: (
                "order_month", "customer_state", "primary_category_name",
                "seller_price", "seller_freight_ratio", "seller_items",
            ),
        },
        fixed_terms={
            ORDER_TABLE: (
                "C(order_month)", "C(customer_state)",
                "C(primary_category_name)", "C(primary_payment_type)",
                "z_price", "z_freight_ratio", "z_item_count",
                "is_multi_seller_order", "z_promised_days",
            ),
            SELLER_TABLE: (
                "C(order_month)", "C(customer_state)",
                "C(primary_category_name)", "z_seller_price",
                "z_seller_freight_ratio", "z_seller_items",
            ),
        },
        control_policy={
            "order": ORDER_CONTROLS,
            "seller": SELLER_CONTROLS,
            "selection_rule": SELECTION_RULE,
        },
    ),
    "is_any_item_handover_late": TargetSpec(
        name="is_any_item_handover_late", label="是否存在交接超期",
        short_label="交接超期", positive_label="存在交接超期",
        negative_label="无交接超期", primary_table=SELLER_TABLE,
        candidates=HANDOVER_FEATURES,
        where_by_table={SELLER_TABLE: VALID_SAMPLE_SQL},
        fixed_columns={
            SELLER_TABLE: (
                "order_month", "customer_state", "primary_category_name",
                "seller_price", "seller_freight_ratio", "seller_items",
                "is_multi_seller_order", "promised_delivery_days",
            ),
        },
        fixed_terms={
            SELLER_TABLE: (
                "C(order_month)", "C(customer_state)",
                "C(primary_category_name)", "z_seller_price",
                "z_seller_freight_ratio", "z_seller_items",
                "is_multi_seller_order", "z_promised_days",
            ),
        },
        control_policy={
            "order": [],
            "seller": SELLER_CONTROLS + [
                "是否多卖家订单（is_multi_seller_order）",
                "承诺交付天数（promised_delivery_days）",
            ],
            "selection_rule": SELECTION_RULE,
        },
    ),
}


def _float(value, default=None):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _two_by_two(group_event: int, group_non_event: int,
                rest_event: int, rest_non_event: int, target: str) -> dict:
    counts = pd.DataFrame([
        {"group": 1, target: 1, "n": group_event},
        {"group": 1, target: 0, "n": group_non_event},
        {"group": 0, target: 1, "n": rest_event},
        {"group": 0, target: 0, "n": rest_non_event},
    ])
    return categorical_test_counts(counts, "group", target)


def _group_details(counts: pd.DataFrame, field: str, target: TargetSpec,
                   min_group_sample: int) -> tuple[list[dict], pd.DataFrame]:
    data = counts.dropna(subset=[field, target.name, "n"]).copy()
    data[target.name] = pd.to_numeric(data[target.name], errors="coerce")
    data["n"] = pd.to_numeric(data["n"], errors="coerce")
    totals = data.groupby(field)["n"].sum()
    keep = set(totals[totals >= min_group_sample].index)
    data = data[data[field].isin(keep)]
    pivot = data.pivot_table(
        index=field, columns=target.name, values="n",
        aggfunc="sum", fill_value=0,
    ).reindex(columns=[0, 1], fill_value=0)
    total_n = int(pivot.to_numpy().sum())
    total_event = int(pivot[1].sum())
    base_rate = total_event / total_n if total_n else 0.0
    details: list[dict] = []
    raw_p: list[float] = []
    for value, row in pivot.iterrows():
        sample = int(row[0] + row[1])
        event = int(row[1])
        rest_event = total_event - event
        rest_non_event = total_n - total_event - int(row[0])
        test = _two_by_two(event, int(row[0]), rest_event, rest_non_event, target.name)
        rate = event / sample if sample else 0.0
        details.append({
            "value": value, "sample": sample, "target_count": event,
            "target_rate": rate, "base_rate": base_rate,
            "rate_difference": rate - base_rate,
            "lift": rate / base_rate if base_rate else None,
            "excess_target": sample * max(rate - base_rate, 0),
            "or": test.get("or"), "ci95": test.get("or_ci"),
            "p": test.get("p"),
        })
        raw_p.append(float(test.get("p", 1.0)))
    if details:
        correction = multiple_correction(raw_p)
        for index, detail in enumerate(details):
            detail["p_adjusted"] = correction["p_adjusted"][index]
            detail["significant_risk"] = bool(
                detail["p_adjusted"] < ALPHA
                and detail["target_rate"] > base_rate
                and _effect_ci_passed(detail.get("ci95"), 1.0)
            )
    details.sort(
        key=lambda row: (
            row.get("significant_risk", False), row["excess_target"],
            row["rate_difference"],
        ), reverse=True,
    )
    return details, pivot


def _numeric_details(df: pd.DataFrame, spec: TargetFeatureSpec,
                     target: TargetSpec) -> dict:
    by_target = []
    for value, label in ((0, target.negative_label), (1, target.positive_label)):
        values = df.loc[df[target.name] == value, spec.field].dropna()
        if values.empty:
            continue
        by_target.append({
            "group": label, "sample": int(len(values)),
            "p25": float(values.quantile(0.25)),
            "median": float(values.median()),
            "p75": float(values.quantile(0.75)), "mean": float(values.mean()),
        })
    binned = numeric_rate_bins(df, spec.field, target.name)
    return {
        "by_target": by_target,
        "quantile_bins": binned["rows"],
        "binning_method": binned["method"],
        "binning_note": binned["note"],
    }


def _screen_feature(provider: DataProvider, spec: TargetFeatureSpec,
                    target: TargetSpec, min_group_sample: int,
                    sqls: list[str]) -> dict:
    where = target.where_by_table[spec.table]
    if spec.kind in {"binary", "categorical"}:
        counts = load_group_counts(
            provider, spec.table, spec.field, target=target.name,
            where=where, sql_sink=sqls,
        )
        if spec.kind == "binary":
            test = categorical_test_counts(counts, spec.field, target.name)
            if "error" in test:
                return {"ok": False, "error": test["error"]}
            details, _ = _group_details(counts, spec.field, target, 1)
            return {
                "ok": True,
                "method": (
                    "两组比例比较：Fisher精确检验"
                    if test["method"] == "fisher" else
                    "两组比例比较：Pearson卡方检验（Yates校正）"
                ),
                "p": float(test["p"]), "effect_name": "OR",
                "effect_value": float(test["or"]), "ci95": test.get("or_ci"),
                "null_value": 1.0, "sample": int(test["n"]),
                "details": details,
            }
        total = int(counts["n"].sum()) if not counts.empty else 0
        dynamic_min = max(
            min_group_sample,
            math.ceil(total * (0.001 if spec.name == "route" else 0.0005)),
        )
        details, pivot = _group_details(counts, spec.field, target, dynamic_min)
        if min(pivot.shape, default=0) < 2:
            return {"ok": False, "error": f"达到样本门槛{dynamic_min}的分组不足"}
        prepared = counts[counts[spec.field].isin(pivot.index)]
        test = chi_square_rc_counts(prepared, spec.field, target.name)
        if "error" in test:
            return {"ok": False, "error": test["error"]}
        table = pivot.to_numpy(dtype=float)
        return {
            "ok": True, "method": "多组比例比较：Pearson卡方独立性检验",
            "p": float(test["p"]), "effect_name": "Cramér's V",
            "effect_value": _cramers_v(table),
            "ci95": _cramers_v_ci(table, seed=sum(map(ord, target.name + spec.name))),
            "null_value": 0.0, "sample": int(table.sum()),
            "groups_tested": int(pivot.shape[0]),
            "min_group_sample": dynamic_min,
            "assumption_ok": test.get("assumption_ok", True),
            "low_expected_share": test.get("low_expected_share"),
            "details": details,
        }

    frame = load_table(
        provider, spec.table, [spec.field, target.name],
        where=where, sql_sink=sqls,
    )
    frame[spec.field] = pd.to_numeric(frame[spec.field], errors="coerce")
    frame[target.name] = pd.to_numeric(frame[target.name], errors="coerce")
    frame = frame.dropna(subset=[spec.field, target.name])
    test = distribution_test(frame, spec.field, target.name)
    if "error" in test:
        return {"ok": False, "error": test["error"]}
    n0, n1 = int(test["n0"]), int(test["n1"])
    return {
        "ok": True, "method": "两组分布比较：Mann–Whitney U检验",
        "p": float(test["p"]), "effect_name": "秩二列相关",
        "effect_value": float(test["effect_size"]),
        "ci95": _rank_biserial_ci(float(test["u"]), n0, n1),
        "null_value": 0.0, "sample": n0 + n1,
        "details": _numeric_details(frame, spec, target),
    }


def screen_target_features(provider: DataProvider, target: TargetSpec,
                           min_group_sample: int = 100) -> dict:
    sqls: list[str] = []
    rows: list[dict] = []
    for spec in target.candidates:
        try:
            result = _screen_feature(
                provider, spec, target, min_group_sample, sqls
            )
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
                row["p_adjusted"] < ALPHA and row["ci_passed"]
                and row.get("assumption_ok") is not False
            )
    retained = [row for row in rows if row.get("retained")]
    by_group: dict[str, list[dict]] = {}
    for row in retained:
        # 不同分析粒度的同名共线性组分别选择代表，不能跨表互相剔除。
        by_group.setdefault(f"{row['table']}::{row['collinear_group']}", []).append(row)
    selected: list[dict] = []
    for members in by_group.values():
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
                    f"与{representative['label']}信息高度重复，为避免重复计算而不同时入模"
                )
    return {
        "ok": True, "target": target.name, "target_label": target.label,
        "alpha": ALPHA, "multiple_correction": "FDR-BH",
        "tests": rows, "retained": retained, "selected": selected,
        "sqls": sqls,
    }


def _spec_map(target: TargetSpec) -> dict[tuple[str, str], TargetFeatureSpec]:
    return {(spec.table, spec.name): spec for spec in target.candidates}


NUMERIC_TERMS = {
    "approval_days": "z_approval", "price_total": "z_price",
    "freight_ratio": "z_freight_ratio", "item_count": "z_item_count",
    "promised_delivery_days": "z_promised_days",
    "seller_price": "z_seller_price",
    "seller_freight_ratio": "z_seller_freight_ratio",
    "seller_items": "z_seller_items",
    "approximate_distance_km": "z_distance",
}


def _engineer(frame: pd.DataFrame, target: TargetSpec,
              specs: list[TargetFeatureSpec]) -> pd.DataFrame:
    frame[target.name] = pd.to_numeric(frame[target.name], errors="coerce")
    frame = frame.dropna(subset=[target.name])
    categorical = {
        column for column in (
            "order_month", "customer_state", "primary_category_name",
            "primary_payment_type", "seller_state",
        ) if column in frame
    }
    categorical.update(spec.field for spec in specs if spec.kind == "categorical"
                       and spec.name != "route")
    base_min = max(100, math.ceil(len(frame) * 0.002))
    ordinary = sorted(column for column in categorical
                      if column != "primary_category_name")
    if ordinary:
        _collapse_rare(frame, ordinary, base_min)
    if "primary_category_name" in categorical:
        _collapse_rare(
            frame, ["primary_category_name"],
            max(300, math.ceil(len(frame) * 0.005)),
        )
    for source, engineered in NUMERIC_TERMS.items():
        if source in frame:
            _zscore(frame, source, engineered)
    binary = {
        "is_multi_seller_order", "cross_state", "is_any_item_handover_late"
    }
    for column in binary:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def _selected_specs(screening: dict, target: TargetSpec,
                    table: str) -> list[TargetFeatureSpec]:
    specs = _spec_map(target)
    return [
        specs[(row["table"], row["feature"])]
        for row in screening.get("selected", []) if row["table"] == table
    ]


def _run_model(provider: DataProvider, target: TargetSpec,
               table: str, specs: list[TargetFeatureSpec],
               sqls: list[str]) -> tuple[dict | None, list[dict], dict]:
    modeled = [spec for spec in specs if spec.name != "route"]
    if not modeled:
        return None, [], {"table": table, "rows": 0, "columns": 0}
    columns = sorted(
        {target.name, *target.fixed_columns.get(table, ())}
        | {spec.field for spec in modeled}
    )

    def engineer(frame: pd.DataFrame) -> pd.DataFrame:
        return _engineer(frame, target, modeled)

    frame = cached_frame(
        provider, table, columns, target.where_by_table[table], engineer,
        sql_sink=sqls,
    )
    terms = set(target.fixed_terms.get(table, ()))
    terms.update(spec.model_term for spec in modeled)
    formula = f"{target.name} ~ " + " + ".join(sorted(terms))
    label = "订单级自动调整模型" if table == ORDER_TABLE else "订单-卖家级自动调整模型"
    model = _fit(frame, formula, label)
    results = []
    for spec in modeled:
        if spec.kind == "categorical":
            results.append(_adjusted_categorical(spec, model, spec.model_term))
        else:
            results.append(_adjusted_direct(spec, model, spec.model_term, spec.unit))
    profile = {"table": table, "rows": int(len(frame)), "columns": len(columns)}
    del frame
    gc.collect()
    return model, results, profile


def _odds_ratio(group_event: int, group_non_event: int,
                rest_event: int, rest_non_event: int) -> dict:
    cells = np.asarray(
        [group_event, group_non_event, rest_event, rest_non_event], dtype=float
    )
    corrected = cells + 0.5 if (cells == 0).any() else cells
    a, b, c, d = corrected
    odds = (a * d) / (b * c)
    se = math.sqrt(1 / a + 1 / b + 1 / c + 1 / d)
    log_or = math.log(odds)
    _, p = stats.fisher_exact(
        [[group_event, group_non_event], [rest_event, rest_non_event]]
    )
    return {
        "or": float(odds),
        "ci95": [math.exp(log_or - 1.96 * se), math.exp(log_or + 1.96 * se)],
        "p": float(p),
    }


def _route_validation(provider: DataProvider, target: TargetSpec,
                      sqls: list[str]) -> dict:
    columns = [
        "order_purchase_timestamp", target.name, "route", "seller_price",
        "seller_items",
    ]
    if target.name == "is_late_delivery":
        columns.append("is_any_item_handover_late")
    frame = load_table(
        provider, SELLER_TABLE, columns,
        where=target.where_by_table[SELLER_TABLE], sql_sink=sqls,
    )
    frame["order_purchase_timestamp"] = pd.to_datetime(
        frame["order_purchase_timestamp"], errors="coerce"
    )
    frame[target.name] = pd.to_numeric(frame[target.name], errors="coerce")
    frame = frame.dropna(subset=["order_purchase_timestamp", "route", target.name])
    if len(frame) < 200:
        return {"ok": False, "error": "线路跨时间验证至少需要200条有效记录"}
    cutoff = frame["order_purchase_timestamp"].quantile(0.8)
    train = frame[frame["order_purchase_timestamp"] <= cutoff].copy()
    holdout = frame[frame["order_purchase_timestamp"] > cutoff].copy()
    holdout_size = int(len(holdout))
    grouped = train.groupby("route")[target.name].agg(["size", "sum"])
    grouped["rate"] = grouped["sum"] / grouped["size"]
    base_rate = float(train[target.name].mean())
    min_route = max(20, math.ceil(len(train) * 0.001))
    eligible = grouped[
        (grouped["size"] >= min_route) & (grouped["rate"] > base_rate)
    ].copy()
    eligible["excess"] = (eligible["rate"] - base_rate) * eligible["size"]
    candidates = list(
        eligible.sort_values(["excess", "rate"], ascending=False).head(8).index
    )
    if not candidates:
        return {
            "ok": False, "error": "较早时期没有达到样本门槛的高风险线路",
            "cutoff": str(cutoff.date()), "min_route_sample": min_route,
        }
    for data in (train, holdout):
        data["route_group"] = data["route"].where(
            data["route"].isin(candidates), "OTHER"
        ).astype(str)
        _zscore(data, "seller_price", "z_seller_price")
        _zscore(data, "seller_items", "z_seller_items")
        if "is_any_item_handover_late" in data:
            data["is_any_item_handover_late"] = pd.to_numeric(
                data["is_any_item_handover_late"], errors="coerce"
            ).fillna(0)
    controls = "z_seller_price + z_seller_items"
    if target.name == "is_late_delivery":
        controls += " + is_any_item_handover_late"
    route_term = "C(route_group, Treatment(reference='OTHER'))"
    model = _fit(
        train, f"{target.name} ~ {controls} + {route_term}",
        "高风险线路调整模型（较早时期数据）",
    )
    rows = []
    for route in candidates:
        term = next(
            (row for row in model.get("terms", [])
             if row["term"].endswith(f"[T.{route}]")), None,
        )
        train_mask = train["route"] == route
        holdout_mask = holdout["route"] == route
        holdout_n = int(holdout_mask.sum())
        holdout_event = int(holdout.loc[holdout_mask, target.name].sum())
        rest_event = int(holdout.loc[~holdout_mask, target.name].sum())
        holdout_or = _odds_ratio(
            holdout_event, holdout_n - holdout_event,
            rest_event, int((~holdout_mask).sum()) - rest_event,
        ) if holdout_n else None
        rows.append({
            "route": route, "train_n": int(train_mask.sum()),
            "train_target_rate": float(train.loc[train_mask, target.name].mean()),
            "adjusted_or": term.get("or") if term else None,
            "adjusted_ci95": term.get("ci95") if term else None,
            "adjusted_p": term.get("p") if term else None,
            "holdout_n": holdout_n,
            "holdout_target_rate": holdout_event / holdout_n if holdout_n else None,
            "holdout_or": holdout_or.get("or") if holdout_or else None,
            "holdout_ci95": holdout_or.get("ci95") if holdout_or else None,
        })
    valid = [row for row in rows if isinstance(row.get("adjusted_p"), float)]
    if valid:
        correction = multiple_correction([row["adjusted_p"] for row in valid])
        for index, row in enumerate(valid):
            row["adjusted_p_fdr"] = correction["p_adjusted"][index]
    for row in rows:
        adjusted_risk = (
            isinstance(row.get("adjusted_or"), (int, float))
            and row["adjusted_or"] > 1 and row.get("adjusted_p_fdr", 1) < ALPHA
        )
        same_direction = (
            isinstance(row.get("holdout_or"), (int, float))
            and row["holdout_or"] > 1
        )
        if adjusted_risk and row["holdout_n"] >= 20 and same_direction:
            row["stability"] = "稳定复现"
        elif adjusted_risk and same_direction:
            row["stability"] = "方向一致，但留出样本不足"
        else:
            row["stability"] = "未稳定复现"
    del frame, train, holdout
    gc.collect()
    return {
        "ok": True, "method": "较早时期多变量Logistic调整＋较晚20%记录验证",
        "cutoff": str(cutoff.date()), "train_n": int(grouped["size"].sum()),
        "holdout_n": holdout_size,
        "min_route_sample": min_route, "model": model, "routes": rows,
    }


def run_adjusted_validation(provider: DataProvider, target: TargetSpec,
                            screening: dict) -> dict:
    sqls: list[str] = []
    results: list[dict] = []
    models: list[dict] = []
    profiles: list[dict] = []
    for table in (ORDER_TABLE, SELLER_TABLE):
        if table not in target.where_by_table:
            continue
        model, rows, profile = _run_model(
            provider, target, table, _selected_specs(screening, target, table), sqls
        )
        if model:
            models.append(model)
        results.extend(rows)
        profiles.append(profile)
    route_validation = None
    if any(row.get("feature") == "route" for row in screening.get("selected", [])):
        route_validation = _route_validation(provider, target, sqls)
        stable_routes = [
            row for row in route_validation.get("routes", [])
            if row.get("stability") == "稳定复现"
        ] if route_validation.get("ok") else []
        route_p = min(
            (row.get("adjusted_p_fdr", 1.0)
             for row in route_validation.get("routes", [])), default=1.0,
        ) if route_validation.get("ok") else 1.0
        results.append({
            "feature": "route", "label": "卖家州→客户州线路",
            "ok": bool(route_validation.get("ok")),
            "model": "线路：较早时期建模＋较晚时期独立验证",
            "method": "候选线路多变量模型＋较晚20%记录验证",
            "fit_method": "线路专项模型＋时间留出验证",
            "effect": "控制其他因素后的线路OR", "adjusted_or": None,
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
            "控制预设变量后仍显著且置信区间有效" if row["stable"]
            else "调整后未同时满足显著性与置信区间门槛"
        )
    return {
        "ok": True, "target": target.name, "target_label": target.label,
        "results": results, "stable": [row for row in results if row.get("stable")],
        "models": models, "route_validation": route_validation,
        "control_policy": target.control_policy, "sqls": sqls,
        "load_profile": profiles,
    }


def _target_visualization(details, kind: str, target: TargetSpec) -> dict:
    if kind == "numeric":
        rows = [
            {
                "group": row["value_range"], "sample": row["sample"],
                "target_count": row["target_count"],
                "target_rate": row["target_rate"],
            }
            for row in (details or {}).get("quantile_bins", [])
        ]
        return {
            "ok": bool(rows), "chart_type": "binned_rate", "rows": rows,
            "note": (details or {}).get("binning_note")
            or f"连续变量按五分位分箱，柱高表示各区间的{target.short_label}发生率。",
            "binning_method": (details or {}).get("binning_method"),
        }
    rows = [
        {
            "group": row.get("value"), "sample": row.get("sample"),
            "target_count": row.get("target_count"),
            "target_rate": row.get("target_rate"),
        }
        for row in (details or [])
    ]
    return {
        "ok": bool(rows), "chart_type": "group_rate", "rows": rows,
        "note": f"柱高表示各分组的{target.short_label}发生率。",
    }


def _baseline(provider: DataProvider, target: TargetSpec, sqls: list[str]) -> dict:
    where = target.where_by_table[target.primary_table]
    sql = (
        f"SELECT COUNT(*) AS sample, SUM({target.name}) AS target_count, "
        f"AVG({target.name}) AS target_rate FROM {target.primary_table} "
        f"WHERE {where} LIMIT 1"
    )
    sqls.append(sql)
    row = provider.execute(sql)[0]
    return {
        "table": target.primary_table, "sample": int(row["sample"]),
        "target_count": int(row["target_count"] or 0),
        "target_rate": float(row["target_rate"] or 0), "sql": sql,
    }


def run_target_attribution(provider: DataProvider, target_name: str,
                           min_group_sample: int = 100) -> dict:
    target = TARGET_SPECS[target_name]
    screening = screen_target_features(provider, target, min_group_sample)
    adjusted = run_adjusted_validation(provider, target, screening)
    screen_by_feature = {
        (row["table"], row["feature"]): row for row in screening.get("tests", [])
    }
    explanations = []
    for result in adjusted.get("stable", []):
        matches = [row for (_, feature), row in screen_by_feature.items()
                   if feature == result["feature"]]
        screen = matches[0] if matches else {}
        details = screen.get("details")
        route_rows = None
        if result["feature"] == "route":
            stable_routes = set(result.get("stable_routes", []))
            details = [
                row for row in (details or []) if row.get("value") in stable_routes
            ]
            route_rows = [
                row for row in (adjusted.get("route_validation") or {}).get("routes", [])
                if row.get("route") in stable_routes
            ]
        explanations.append({
            "feature": result["feature"], "label": result["label"],
            "kind": screen.get("kind"), "adjusted_result": result,
            "details": details, "route_validation": route_rows,
            "target_visualization": _target_visualization(
                details, screen.get("kind"), target
            ),
            "interpretation": (
                f"以下分布说明该变量与{target.short_label}的关联表现；"
                "不能据此认定因果，也不会自动生成治理策略。"
            ),
        })
    baseline_sqls: list[str] = []
    baseline = _baseline(provider, target, baseline_sqls)
    feature_tests = []
    for row in screening.get("tests", []):
        p_adjusted = row.get("p_adjusted")
        feature_tests.append({
            **row, "target": target.label,
            "p_used": p_adjusted if p_adjusted is not None else row.get("p"),
            "p_basis": "FDR-BH校正后 p 值",
            "significant": bool(row.get("retained")),
            "lightweight_judgment": (
                "通过单变量筛选，进入共线性处理" if row.get("retained")
                else ((f"未执行：{row.get('error')}" if row.get("error")
                       else "因样本或检验前提不足未执行") if not row.get("ok")
                      else "未同时满足FDR显著性与95%置信区间标准")
            ),
        })
    significant = [row for row in feature_tests if row["significant"]]
    inconclusive = [
        row for row in feature_tests
        if row.get("assumption_ok") is False or row.get("p") is None
    ]
    not_significant = [
        row for row in feature_tests
        if not row["significant"] and row.get("assumption_ok") is not False
    ]
    caveats = [
        "候选变量严格按业务时间顺序限定，目标发生后的变量不会进入筛选或模型。",
        "第一层只保留FDR校正后p<0.05且95%置信区间不含无效值的变量。",
        "共线性组只保留一个预设业务代表变量，不根据本次结果临时选优。",
        "调整后仍显著表示关联更稳定，仍不能证明因果。",
        "未记录的承运商、仓库负载、天气与促销等因素仍可能造成残余混杂。",
    ]
    row_counts = getattr(provider, "row_counts", {})
    if getattr(provider, "source_name", "") == SAMPLE_SOURCE_LABEL and row_counts:
        physical = {key: value for key, value in row_counts.items()
                    if key != "mart_order_item_analysis"}
        if physical and max(physical.values()) <= 1000:
            caveats.append("当前为截取样本，只适合检查流程；业务结论应使用完整数据库。")
    adjusted_results = adjusted.get("results", [])
    deep_plan = [{
        "feature": row.get("feature"), "label": row.get("label"),
        "screening_method": next(
            (test.get("method") for test in feature_tests
             if test.get("feature") == row.get("feature")), "单变量检验"
        ),
        "screening_p": row.get("p_adjusted"),
        "screening_p_basis": "FDR-BH校正后 p 值",
        "recommended_method": row.get("method"),
        "purpose": f"确认控制预设变量后是否仍与{target.short_label}显著相关。",
        "reason": row.get("conclusion"), "status": "已自动完成调整后验证",
    } for row in adjusted_results]
    return {
        "ok": True, "schema_version": "2026-08-21.2",
        "target": target.name, "target_label": target.label,
        "target_short_label": target.short_label,
        "target_positive_label": target.positive_label,
        "target_negative_label": target.negative_label,
        "target_baseline": baseline, "target_rate": baseline["target_rate"],
        "feature_tests": feature_tests,
        "significant_features": significant,
        "inconclusive_features": inconclusive,
        "not_significant_features": not_significant,
        "selected_features": screening.get("selected", []),
        "adjusted_features": adjusted.get("stable", []),
        "adjusted_explanations": explanations,
        "adjusted_validation": adjusted,
        "control_policy": target.control_policy,
        "deep_validation_plan": deep_plan,
        "baseline": {"target": baseline},
        "factors": {}, "priorities": [], "routes": {}, "item_drilldown": {},
        "verification": {
            "ok": True, "mode": "automatic_adjusted",
            "single_tests": screening.get("tests", []),
            "logistic": {"enabled": True, "models": adjusted.get("models", [])},
            "load_profile": {
                "strategy": "每张分析宽表只读取当前目标建模所需字段",
                "extracts": adjusted.get("load_profile", []),
                "logistic_enabled": True,
            },
        },
        "recommendations": {
            "ok": True, "status": "disabled_evidence_only",
            "recommendations": [], "note": "仅输出统计证据，不自动生成治理策略。",
        },
        "caveats": caveats,
        "sqls": baseline_sqls + screening.get("sqls", []) + adjusted.get("sqls", []),
        "analysis_mode": "automatic_adjusted_attribution",
        "note": (
            f"以{target.short_label}为目标，按时间顺序限定候选变量，依次执行"
            "单变量筛选、共线性代表选择、多变量Logistic调整和稳定变量分布展示。"
        ),
    }
