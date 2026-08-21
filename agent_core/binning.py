"""Stable numeric bucketing used by attribution detail charts."""
from __future__ import annotations

import numpy as np
import pandas as pd


# These fields are integer order-complexity measures. Quantile bucketing is a
# poor fit because most Olist orders contain exactly one item, which can make
# all quantile boundaries identical and collapse the chart to one bar.
COUNT_FIELDS = frozenset({"item_count", "seller_items"})


def _rate_rows(frame: pd.DataFrame, target_field: str) -> list[dict]:
    grouped = frame.groupby("_bucket", observed=True, sort=False)[target_field].agg(
        ["size", "sum", "mean"]
    )
    return [
        {
            "value_range": str(value),
            "sample": int(row["size"]),
            "target_count": int(row["sum"]),
            "target_rate": float(row["mean"]),
        }
        for value, row in grouped.iterrows()
    ]


def numeric_rate_bins(
    df: pd.DataFrame,
    value_field: str,
    target_field: str,
) -> dict:
    """Return non-collapsing buckets and event rates for a numeric feature.

    Count-like features use interpretable business buckets. Other numeric
    features use quintiles, with exact-value/equal-width fallbacks when tied
    quantiles leave fewer than two observed groups.
    """
    frame = df[[value_field, target_field]].copy()
    frame[value_field] = pd.to_numeric(frame[value_field], errors="coerce")
    frame[target_field] = pd.to_numeric(frame[target_field], errors="coerce")
    frame = frame.dropna(subset=[value_field, target_field])
    if frame.empty or frame[value_field].nunique() < 2:
        return {
            "rows": [],
            "method": "insufficient_variation",
            "note": "有效样本中的取值不足两个，无法形成可比较分组。",
        }

    values = frame[value_field]
    if value_field in COUNT_FIELDS:
        frame["_bucket"] = pd.cut(
            values,
            bins=[-np.inf, 1.5, 2.5, 3.5, np.inf],
            labels=["1", "2", "3", "4及以上"],
            include_lowest=True,
            ordered=True,
        )
        return {
            "rows": _rate_rows(frame, target_field),
            "method": "business_count_bins",
            "note": "数量型变量按1、2、3、4及以上分组，柱高表示各组目标事件发生率。",
        }

    try:
        frame["_bucket"] = pd.qcut(values, q=5, duplicates="drop")
    except (TypeError, ValueError):
        frame["_bucket"] = np.nan
    if frame["_bucket"].nunique(dropna=True) >= 2:
        return {
            "rows": _rate_rows(frame, target_field),
            "method": "quintile",
            "note": "连续变量按全体有效样本五分位分箱，柱高表示各区间目标事件发生率。",
        }

    unique_values = sorted(values.unique())
    if len(unique_values) <= 20:
        labels = {
            value: (str(int(value)) if float(value).is_integer() else f"{value:.4g}")
            for value in unique_values
        }
        frame["_bucket"] = pd.Categorical(
            values.map(labels),
            categories=[labels[value] for value in unique_values],
            ordered=True,
        )
        method = "exact_values"
        note = "分位点重复，已改按实际取值分组；柱高表示各组目标事件发生率。"
    else:
        try:
            frame["_bucket"] = pd.cut(
                values, bins=5, include_lowest=True, duplicates="drop"
            )
        except (TypeError, ValueError):
            frame["_bucket"] = np.nan
        method = "equal_width_fallback"
        note = "分位点重复，已改用等宽分箱；柱高表示各区间目标事件发生率。"

    rows = _rate_rows(frame, target_field)
    if len(rows) < 2:
        return {
            "rows": [],
            "method": "insufficient_variation",
            "note": "有效样本的取值过于集中，无法形成至少两个可比较分组。",
        }
    return {"rows": rows, "method": method, "note": note}
