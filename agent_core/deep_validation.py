"""低评分补充验证：多变量调整 + 跨时间验证。

该模块与轻量单变量检验分开。它只在用户明确要求“深度验证/调整后验证”时运行，
以低评分（二分类）为统一结果变量，并避免把相关性检验重复包装成深度分析。
"""
from __future__ import annotations

import gc
import math
from typing import Iterable

import numpy as np
import pandas as pd
from scipy import stats

from .data_provider import DataProvider
from .statistics import (
    ORDER_WHERE,
    SELLER_WHERE,
    load_table,
    logistic_model_formula,
    multiple_correction,
)

DEEP_HINTS = (
    "深度验证", "深度检验", "多变量", "调整后", "控制混杂", "留出验证",
    "留出数据", "留出集", "稳定性验证",
)

FEATURE_LABELS = {
    "is_late_delivery": "是否延迟",
    "late_days": "延迟程度（延迟天数）",
    "fulfillment_days": "总履约时长",
    "customer_state": "客户地区",
    "seller_state": "卖家地区",
    "cross_state": "是否跨州",
    "route": "高风险线路",
    "primary_category_name": "主要品类",
    "primary_payment_type": "支付方式",
    "price_total": "商品金额",
}

DEFAULT_FEATURES = [
    "is_late_delivery", "late_days", "fulfillment_days", "customer_state",
    "seller_state", "cross_state", "route",
]


def is_deep_validation_question(question: str) -> bool:
    q = question.lower()
    return any(hint.lower() in q for hint in DEEP_HINTS)


def extract_deep_features(question: str) -> list[str]:
    """提取全部被点名的变量；不再只返回第一个关键词。"""
    q = question.lower()
    features: list[str] = []

    def add(feature: str) -> None:
        if feature not in features:
            features.append(feature)

    if any(word in q for word in ("是否延迟", "延迟订单", "延迟与")):
        add("is_late_delivery")
    if any(word in q for word in ("延迟程度", "延迟天数", "延迟分档", "延迟档位")):
        add("late_days")
    if any(word in q for word in ("总履约时长", "履约时长", "总时长")):
        add("fulfillment_days")
    if any(word in q for word in ("客户州", "客户地区", "收货州")):
        add("customer_state")
    if any(word in q for word in ("卖家州", "卖家地区")):
        add("seller_state")
    if "地区" in q and "客户地区" not in q and "卖家地区" not in q:
        add("customer_state")
        add("seller_state")
    if any(word in q for word in ("跨州", "cross_state")):
        add("cross_state")
    if any(word in q for word in ("高风险线路", "线路", "路线", "route")):
        add("route")
    if any(word in q for word in ("主要品类", "品类", "类别")):
        add("primary_category_name")
    if any(word in q for word in ("支付方式", "支付类型")):
        add("primary_payment_type")
    if any(word in q for word in ("商品金额", "订单金额", "价格")):
        add("price_total")
    if not features and is_deep_validation_question(q):
        return DEFAULT_FEATURES.copy()
    return features


def _collapse_rare(df: pd.DataFrame, columns: Iterable[str],
                   min_count: int) -> pd.DataFrame:
    """合并稀疏类别，降低完全分离和高维虚拟变量造成的不稳定。"""
    result = df.copy()
    for column in columns:
        values = result[column].astype("string").fillna("unknown")
        counts = values.value_counts(dropna=False)
        keep = set(counts[counts >= min_count].index)
        result[column] = values.where(values.isin(keep), "OTHER").astype(str)
    return result


def _fit_model(df: pd.DataFrame, formula: str, label: str) -> dict:
    required = [part.strip() for part in formula.split("~", 1)]
    if df.empty or df[required[0]].nunique(dropna=True) < 2:
        return {"ok": False, "label": label, "error": "结果变量无有效变异"}
    try:
        model = logistic_model_formula(df, formula)
        return {"ok": True, "label": label, "formula": formula, **model}
    except Exception as error:
        return {
            "ok": False,
            "label": label,
            "formula": formula,
            "error": f"{type(error).__name__}: 模型未能稳定估计",
        }


def _term(model: dict, name: str) -> dict | None:
    return next((row for row in model.get("terms", []) if row["term"] == name), None)


def _joint(model: dict, name: str) -> dict | None:
    return next(
        (row for row in model.get("joint_tests", []) if row["term"] == name),
        None,
    )


def _direct_result(feature: str, model: dict, term_name: str,
                   unit: str, model_label: str) -> dict:
    row = _term(model, term_name) if model.get("ok") else None
    if not row:
        return {
            "feature": feature, "label": FEATURE_LABELS[feature],
            "model": model_label, "ok": False,
            "error": model.get("error", "模型中没有可估计项"),
        }
    return {
        "feature": feature,
        "label": FEATURE_LABELS[feature],
        "model": model_label,
        "ok": True,
        "method": "多变量二项Logistic回归（HC3稳健标准误）",
        "effect": f"调整后OR（{unit}）",
        "adjusted_or": row["or"],
        "ci95": row["ci95"],
        "p": row["p"],
        "n": model.get("nobs"),
    }


def _joint_result(feature: str, model: dict, term_name: str,
                  model_label: str) -> dict:
    row = _joint(model, term_name) if model.get("ok") else None
    if not row:
        return {
            "feature": feature, "label": FEATURE_LABELS[feature],
            "model": model_label, "ok": False,
            "error": model.get("error", "联合Wald检验不可用"),
        }
    return {
        "feature": feature,
        "label": FEATURE_LABELS[feature],
        "model": model_label,
        "ok": True,
        "method": "多变量Logistic回归中的联合Wald检验（HC3）",
        "effect": "分类变量整体效应（无单一OR）",
        "adjusted_or": None,
        "ci95": None,
        "p": row["p"],
        "n": model.get("nobs"),
    }


def _odds_ratio(group_low: int, group_non_low: int,
                rest_low: int, rest_non_low: int) -> dict:
    cells = np.asarray(
        [group_low, group_non_low, rest_low, rest_non_low], dtype=float
    )
    corrected = cells + 0.5 if (cells == 0).any() else cells
    a, b, c, d = corrected
    odds_ratio = (a * d) / (b * c)
    se = math.sqrt(1 / a + 1 / b + 1 / c + 1 / d)
    log_or = math.log(odds_ratio)
    ci = [math.exp(log_or - 1.96 * se), math.exp(log_or + 1.96 * se)]
    _, p = stats.fisher_exact([[group_low, group_non_low], [rest_low, rest_non_low]])
    return {"or": float(odds_ratio), "ci95": [float(ci[0]), float(ci[1])],
            "p": float(p)}


def _route_validation(df: pd.DataFrame) -> dict:
    data = df.copy()
    data["order_purchase_timestamp"] = pd.to_datetime(
        data["order_purchase_timestamp"], errors="coerce"
    )
    data = data.dropna(subset=["order_purchase_timestamp", "route", "is_low_score"])
    if len(data) < 200:
        return {"ok": False, "error": "线路跨时间验证至少需要200条有效记录"}
    cutoff = data["order_purchase_timestamp"].quantile(0.8)
    train = data[data["order_purchase_timestamp"] <= cutoff].copy()
    holdout = data[data["order_purchase_timestamp"] > cutoff].copy()
    grouped = train.groupby("route", dropna=False)["is_low_score"].agg(["size", "sum"])
    grouped["rate"] = grouped["sum"] / grouped["size"]
    base_rate = float(train["is_low_score"].mean())
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
            "ok": False, "error": "较早时期数据中没有达到样本门槛的高风险线路",
            "cutoff": str(cutoff.date()), "min_route_sample": min_route,
        }

    for frame in (train, holdout):
        frame["route_group"] = frame["route"].where(
            frame["route"].isin(candidates), "OTHER"
        ).astype(str)
        frame["log_seller_price"] = np.log1p(
            pd.to_numeric(frame["seller_price"], errors="coerce").clip(lower=0)
        )
    formula = (
        "is_low_score ~ is_late_delivery + "
        "C(route_group, Treatment(reference='OTHER')) + log_seller_price"
    )
    model = _fit_model(train, formula, "高风险线路调整模型（较早时期数据）")
    route_rows = []
    for route in candidates:
        model_term = next(
            (row for row in model.get("terms", [])
             if row["term"].endswith(f"[T.{route}]")),
            None,
        )
        train_mask = train["route"] == route
        holdout_mask = holdout["route"] == route
        holdout_n = int(holdout_mask.sum())
        holdout_low = int(holdout.loc[holdout_mask, "is_low_score"].sum())
        rest_low = int(holdout.loc[~holdout_mask, "is_low_score"].sum())
        holdout_or = _odds_ratio(
            holdout_low, holdout_n - holdout_low,
            rest_low, int((~holdout_mask).sum()) - rest_low,
        ) if holdout_n else None
        route_rows.append({
            "route": route,
            "train_n": int(train_mask.sum()),
            "train_low_score_rate": float(train.loc[train_mask, "is_low_score"].mean()),
            "adjusted_or": model_term.get("or") if model_term else None,
            "adjusted_ci95": model_term.get("ci95") if model_term else None,
            "adjusted_p": model_term.get("p") if model_term else None,
            "holdout_n": holdout_n,
            "holdout_low_score_rate": (
                float(holdout_low / holdout_n) if holdout_n else None
            ),
            "holdout_or": holdout_or.get("or") if holdout_or else None,
            "holdout_ci95": holdout_or.get("ci95") if holdout_or else None,
            "holdout_p": holdout_or.get("p") if holdout_or else None,
        })
    valid = [row for row in route_rows if isinstance(row.get("adjusted_p"), float)]
    if valid:
        corrected = multiple_correction([row["adjusted_p"] for row in valid])
        for index, row in enumerate(valid):
            row["adjusted_p_fdr"] = corrected["p_adjusted"][index]
    for row in route_rows:
        enough = row["holdout_n"] >= 20
        adjusted_risk = (
            isinstance(row.get("adjusted_or"), (int, float))
            and row["adjusted_or"] > 1
            and row.get("adjusted_p_fdr", 1) < 0.05
        )
        same_direction = (
            isinstance(row.get("holdout_or"), (int, float))
            and row["holdout_or"] > 1
        )
        if adjusted_risk and enough and same_direction:
            row["stability"] = "稳定复现"
        elif adjusted_risk and same_direction:
            row["stability"] = "方向一致，但留出样本不足"
        else:
            row["stability"] = "未稳定复现"
    return {
        "ok": True,
        "method": "较早时期多变量Logistic调整＋较晚20%订单独立验证",
        "cutoff": str(cutoff.date()),
        "train_n": int(len(train)),
        "holdout_n": int(len(holdout)),
        "min_route_sample": min_route,
        "model": model,
        "routes": route_rows,
    }


def _apply_core_fdr(results: list[dict]) -> None:
    valid = [row for row in results if row.get("ok") and isinstance(row.get("p"), float)]
    if not valid:
        return
    corrected = multiple_correction([row["p"] for row in valid])
    for index, row in enumerate(valid):
        row["p_adjusted"] = corrected["p_adjusted"][index]
        row["significant"] = bool(row["p_adjusted"] < 0.05)
        row["conclusion"] = (
            "调整后仍存在显著关联" if row["significant"]
            else "调整后未发现稳定的独立关联"
        )


def analyze_deep_validation(provider: DataProvider, question: str) -> dict:
    """执行用户点名变量的深度验证；结果变量统一为是否低评分。"""
    requested = extract_deep_features(question)
    if not requested:
        return {"ok": False, "error": "未识别需要深度验证的变量"}
    sqls: list[str] = []
    results: list[dict] = []
    models: list[dict] = []
    load_profile: list[dict] = []

    order_needed = set(requested) & {
        "is_late_delivery", "late_days", "fulfillment_days", "customer_state",
        "primary_category_name", "primary_payment_type", "price_total",
    }
    if order_needed:
        order_columns = [
            "is_low_score", "is_late_delivery", "late_days", "fulfillment_days",
            "customer_state", "primary_category_name", "primary_payment_type",
            "price_total", "freight_total",
        ]
        order = load_table(
            provider, "mart_order_delivery", order_columns,
            where=ORDER_WHERE, sql_sink=sqls,
        )
        load_profile.append({"table": "mart_order_delivery", "rows": len(order),
                             "columns": len(order_columns)})
        numeric = [
            "is_low_score", "is_late_delivery", "late_days", "fulfillment_days",
            "price_total", "freight_total",
        ]
        for column in numeric:
            order[column] = pd.to_numeric(order[column], errors="coerce")
        order = order.dropna(subset=["is_low_score", "is_late_delivery"])
        min_category = max(20, math.ceil(len(order) * 0.002))
        order = _collapse_rare(
            order,
            ["customer_state", "primary_category_name", "primary_payment_type"],
            min_category,
        )
        order["log_price"] = np.log1p(order["price_total"].clip(lower=0))
        order["log_freight"] = np.log1p(order["freight_total"].clip(lower=0))
        order_formula = (
            "is_low_score ~ is_late_delivery + fulfillment_days + "
            "C(customer_state) + C(primary_category_name) + "
            "C(primary_payment_type) + log_price + log_freight"
        )
        order_model = _fit_model(order, order_formula, "订单级调整模型")
        models.append(order_model)
        if "is_late_delivery" in requested:
            results.append(_direct_result(
                "is_late_delivery", order_model, "is_late_delivery",
                "延迟相对非延迟", "订单级调整模型",
            ))
        if "fulfillment_days" in requested:
            results.append(_direct_result(
                "fulfillment_days", order_model, "fulfillment_days",
                "每增加1天", "订单级调整模型",
            ))
        if "customer_state" in requested:
            results.append(_joint_result(
                "customer_state", order_model, "C(customer_state)",
                "订单级调整模型",
            ))
        if "primary_category_name" in requested:
            results.append(_joint_result(
                "primary_category_name", order_model, "C(primary_category_name)",
                "订单级调整模型",
            ))
        if "primary_payment_type" in requested:
            results.append(_joint_result(
                "primary_payment_type", order_model, "C(primary_payment_type)",
                "订单级调整模型",
            ))
        if "price_total" in requested:
            results.append(_direct_result(
                "price_total", order_model, "log_price",
                "商品金额每增加约2.72倍", "订单级调整模型",
            ))

        if "late_days" in requested:
            late = order[order["is_late_delivery"] == 1].copy()
            late_min = max(10, math.ceil(len(late) * 0.01))
            late = _collapse_rare(late, ["customer_state"], late_min)
            late_formula = (
                "is_low_score ~ late_days + fulfillment_days + "
                "C(customer_state) + log_price + log_freight"
            )
            late_model = _fit_model(late, late_formula, "延迟订单程度模型")
            models.append(late_model)
            results.append(_direct_result(
                "late_days", late_model, "late_days", "每多延迟1天",
                "仅延迟订单的程度模型",
            ))
            del late
        del order
        gc.collect()

    seller_needed = set(requested) & {
        "seller_state", "customer_state", "cross_state", "route",
    }
    route_validation = None
    if seller_needed:
        seller_columns = [
            "order_purchase_timestamp", "is_low_score", "is_late_delivery",
            "cross_state", "seller_state", "customer_state", "route",
            "seller_price",
        ]
        seller = load_table(
            provider, "mart_order_seller_delivery", seller_columns,
            where=SELLER_WHERE, sql_sink=sqls,
        )
        load_profile.append({"table": "mart_order_seller_delivery", "rows": len(seller),
                             "columns": len(seller_columns)})
        for column in ["is_low_score", "is_late_delivery", "cross_state", "seller_price"]:
            seller[column] = pd.to_numeric(seller[column], errors="coerce")
        seller = seller.dropna(subset=["is_low_score", "is_late_delivery"])
        min_state = max(20, math.ceil(len(seller) * 0.002))
        seller = _collapse_rare(seller, ["seller_state", "customer_state"], min_state)
        seller["log_seller_price"] = np.log1p(seller["seller_price"].clip(lower=0))
        geo_formula = (
            "is_low_score ~ is_late_delivery + cross_state + "
            "C(seller_state) + C(customer_state) + log_seller_price"
        )
        geo_model = _fit_model(seller, geo_formula, "卖家地区与跨州调整模型")
        models.append(geo_model)
        if "cross_state" in requested:
            results.append(_direct_result(
                "cross_state", geo_model, "cross_state", "跨州相对同州",
                "卖家地区与跨州调整模型",
            ))
        if "seller_state" in requested:
            results.append(_joint_result(
                "seller_state", geo_model, "C(seller_state)",
                "卖家地区与跨州调整模型",
            ))
        if "route" in requested:
            route_validation = _route_validation(seller)
        del seller
        gc.collect()

    _apply_core_fdr(results)
    successful_models = sum(1 for model in models if model.get("ok"))
    significant = [row["label"] for row in results if row.get("significant")]
    not_significant = [
        row["label"] for row in results
        if row.get("ok") and row.get("significant") is False
    ]
    failed = [row["label"] for row in results if not row.get("ok")]
    stable_routes = (
        [row["route"] for row in route_validation.get("routes", [])
         if row.get("stability") == "稳定复现"]
        if route_validation and route_validation.get("ok") else []
    )
    return {
        "ok": True,
        "mode": "deep_validation",
        "target": "is_low_score",
        "target_label": "是否低评分（1-3分）",
        "requested_features": requested,
        "requested_labels": [FEATURE_LABELS[f] for f in requested],
        "feature_results": results,
        "models": models,
        "successful_models": successful_models,
        "route_validation": route_validation,
        "summary": {
            "adjusted_significant": significant,
            "adjusted_not_significant": not_significant,
            "not_estimated": failed,
            "stable_routes": stable_routes,
        },
        "sqls": sqls,
        "load_profile": {
            "strategy": "每张分析宽表仅提取建模所需字段；订单级模型完成并释放内存后，再读取订单-卖家级数据",
            "extracts": load_profile,
        },
        "caveats": [
            "调整后显著表示控制已纳入变量后仍有关联，不等于因果。",
            "候选线路仅在较早时期数据中识别，再用较晚20%的订单验证关联方向，避免在同一批数据上同时发现和验证。",
            "未纳入的文本投诉、承运商和天气等因素仍可能造成残余混杂。",
        ],
    }
