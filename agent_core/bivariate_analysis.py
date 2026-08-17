"""受控的通用双变量统计分析。

自然语言只负责选取语义注册表中的两个变量；表、字段、分析粒度和检验方法均由
确定性规则决定。禁止跨粒度自由 JOIN，也不把标识符当作统计变量。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

from .data_provider import DataProvider
from .statistics import correlation_test, distribution_test, load_table, trend_test


ORDER_TABLE = "mart_order_delivery"
SELLER_TABLE = "mart_order_seller_delivery"
ITEM_TABLE = "mart_order_item_analysis"
TABLE_GRAINS = {
    ORDER_TABLE: "订单级（一单一行）",
    SELLER_TABLE: "订单-卖家级（默认仅单卖家订单）",
    ITEM_TABLE: "商品项级（一商品项一行）",
}
RATING_VARIABLES = {"review_score", "is_low_score", "is_strict_negative_score"}
DELAY_RANK = {"按时": 0, "1-3天": 1, "4-7天": 2, "8-14天": 3, "15天+": 4}


@dataclass(frozen=True)
class VariableSpec:
    name: str
    label: str
    kind: str  # numeric / ordinal / binary / categorical
    keywords: tuple[str, ...]
    fields: dict[str, str]
    order_map: dict | None = None


VARIABLE_SPECS = (
    # 评价结果
    VariableSpec("is_strict_negative_score", "是否一至二星", "binary",
                 ("是否一至二星", "一二星", "两星及以下", "严格负面"),
                 {ORDER_TABLE: "is_strict_negative_score", SELLER_TABLE: "is_strict_negative_score",
                  ITEM_TABLE: "is_strict_negative_score"}),
    VariableSpec("is_low_score", "是否低评分", "binary",
                 ("是否低评分", "低评分率", "低评分", "差评"),
                 {ORDER_TABLE: "is_low_score", SELLER_TABLE: "is_low_score",
                  ITEM_TABLE: "is_low_score"}),
    VariableSpec("review_score", "评价分数", "ordinal",
                 ("评价分数", "评价得分", "评分", "星级"),
                 {ORDER_TABLE: "review_score", SELLER_TABLE: "review_score",
                  ITEM_TABLE: "review_score"}),

    # 履约
    VariableSpec("fulfillment_days", "配送/总履约时长", "numeric",
                 ("总履约时长", "履约时长", "配送时长", "交付时长", "配送天数"),
                 {ORDER_TABLE: "fulfillment_days", SELLER_TABLE: "fulfillment_days",
                  ITEM_TABLE: "fulfillment_days"}),
    VariableSpec("delivery_variance_days", "相对预计交付偏差天数", "numeric",
                 ("交付偏差天数", "配送偏差", "预计交付偏差", "到货偏差"),
                 {ORDER_TABLE: "delivery_variance_days", SELLER_TABLE: "delivery_variance_days",
                  ITEM_TABLE: "delivery_variance_days"}),
    VariableSpec("late_days", "延迟天数", "numeric",
                 ("延迟天数", "晚到天数", "延误天数"),
                 {ORDER_TABLE: "late_days", ITEM_TABLE: "late_days"}),
    VariableSpec("delay_bucket", "延迟分档", "ordinal",
                 ("延迟分档", "延迟档位", "延迟程度", "延迟等级"),
                 {ORDER_TABLE: "delay_bucket"}, DELAY_RANK),
    VariableSpec("is_late_delivery", "是否延迟", "binary",
                 ("是否延迟", "延迟订单", "是否晚到", "晚到与否"),
                 {ORDER_TABLE: "is_late_delivery", SELLER_TABLE: "is_late_delivery",
                  ITEM_TABLE: "is_late_delivery"}),
    VariableSpec("approval_days", "支付审批时长", "numeric",
                 ("支付审批时长", "审批时长", "审批天数"),
                 {ORDER_TABLE: "approval_days", ITEM_TABLE: "approval_days"}),
    VariableSpec("handover_hours", "商家交接时长", "numeric",
                 ("商家交接时长", "交接时长", "出库时长"),
                 {ORDER_TABLE: "handover_hours"}),
    VariableSpec("shipping_hours", "承运配送时长", "numeric",
                 ("承运配送时长", "运输时长", "承运时长"),
                 {ORDER_TABLE: "shipping_hours"}),
    VariableSpec("is_any_item_handover_late", "是否存在交接超期", "binary",
                 ("是否交接超期", "交接超期", "发货超期"),
                 {SELLER_TABLE: "is_any_item_handover_late"}),

    # 金额、规模和商品属性：同一业务概念按当前分析粒度映射字段
    VariableSpec("price_total", "商品金额", "numeric",
                 ("商品项金额", "商品成交金额", "商品金额", "订单金额", "商品价格", "item价格"),
                 {ORDER_TABLE: "price_total", SELLER_TABLE: "seller_price",
                  ITEM_TABLE: "item_price"}),
    VariableSpec("freight_total", "运费金额", "numeric",
                 ("运费金额", "运费总额", "运费"),
                 {ORDER_TABLE: "freight_total", SELLER_TABLE: "seller_freight",
                  ITEM_TABLE: "item_freight_value"}),
    VariableSpec("freight_ratio", "运费率", "numeric",
                 ("运费占比", "运费率"),
                 {ORDER_TABLE: "freight_ratio", SELLER_TABLE: "seller_freight_ratio",
                  ITEM_TABLE: "item_freight_ratio"}),
    VariableSpec("payment_value", "支付金额", "numeric",
                 ("支付总金额", "支付金额", "实付金额"),
                 {ORDER_TABLE: "payment_value"}),
    VariableSpec("item_count", "商品项数量", "numeric",
                 ("商品项数量", "商品数量", "件数"),
                 {ORDER_TABLE: "item_count", SELLER_TABLE: "seller_items"}),
    VariableSpec("weight_g", "商品重量", "numeric",
                 ("商品总重量", "商品重量", "重量"),
                 {ORDER_TABLE: "total_weight_g", SELLER_TABLE: "seller_total_weight_g",
                  ITEM_TABLE: "product_weight_g"}),
    VariableSpec("volume_cm3", "商品体积", "numeric",
                 ("商品总体积", "商品体积", "体积"),
                 {ORDER_TABLE: "total_volume_cm3", SELLER_TABLE: "seller_total_volume_cm3",
                  ITEM_TABLE: "product_volume_cm3"}),
    VariableSpec("distance_km", "近似配送距离", "numeric",
                 ("近似配送距离", "配送距离", "运输距离", "距离"),
                 {SELLER_TABLE: "approximate_distance_km"}),
    VariableSpec("review_response_hours", "评价回复时长", "numeric",
                 ("评价回复时长", "回复评价时长", "评价响应时长"),
                 {ORDER_TABLE: "review_response_hours"}),

    # 分类/二分类维度
    VariableSpec("route", "线路", "categorical", ("州际线路", "配送线路", "线路", "路线"),
                 {SELLER_TABLE: "route"}),
    VariableSpec("cross_state", "是否跨州", "binary", ("是否跨州", "跨州与否", "跨州", "跨省"),
                 {SELLER_TABLE: "cross_state", ITEM_TABLE: "is_cross_state"}),
    VariableSpec("seller_state", "卖家州", "categorical", ("卖家州", "卖家地区"),
                 {SELLER_TABLE: "seller_state", ITEM_TABLE: "seller_state"}),
    VariableSpec("customer_state", "客户州", "categorical",
                 ("客户州", "客户地区", "收货州", "收货地区"),
                 {ORDER_TABLE: "customer_state", SELLER_TABLE: "customer_state",
                  ITEM_TABLE: "customer_state"}),
    VariableSpec("category", "商品品类", "categorical", ("主要品类", "商品品类", "品类", "类别"),
                 {ORDER_TABLE: "primary_category_name", SELLER_TABLE: "primary_category_name",
                  ITEM_TABLE: "category_name"}),
    VariableSpec("payment_type", "支付方式", "categorical", ("主要支付方式", "支付方式", "支付类型"),
                 {ORDER_TABLE: "primary_payment_type"}),
    VariableSpec("is_multi_seller_order", "是否多卖家订单", "binary",
                 ("是否多卖家", "多卖家订单", "多商家订单"),
                 {SELLER_TABLE: "is_multi_seller_order"}),
    VariableSpec("is_multi_payment_method", "是否多支付方式", "binary",
                 ("是否多支付方式", "多支付方式"),
                 {ORDER_TABLE: "is_multi_payment_method"}),
    VariableSpec("has_review_text", "是否有评价文本", "binary",
                 ("是否有评价文本", "有无评价文本", "评价文本"),
                 {ORDER_TABLE: "has_review_text"}),
    VariableSpec("product_id", "具体商品", "categorical", ("具体商品", "商品ID", "product_id", "SKU"),
                 {ITEM_TABLE: "product_id"}),
    VariableSpec("seller_id", "具体卖家", "categorical", ("具体卖家", "卖家ID", "seller_id"),
                 {ITEM_TABLE: "seller_id"}),
)

SPEC_BY_NAME = {spec.name: spec for spec in VARIABLE_SPECS}
STATISTICAL_HINTS = (
    "显著", "相关性", "相关", "关联", "检验", "p值", "p 值", "置信区间",
    "统计上", "是否有关", "有关系吗", "有没有关系", "影响是否", "差异",
)


def supported_variables() -> list[dict]:
    return [{
        "name": spec.name, "label": spec.label, "kind": spec.kind,
        "tables": list(spec.fields),
    } for spec in VARIABLE_SPECS]


def is_statistical_question(question: str) -> bool:
    q = question.lower()
    return any(h.lower() in q for h in STATISTICAL_HINTS)


def _extract_variables(question: str) -> list[VariableSpec]:
    """按文本位置提取非重叠变量；重叠时优先更长、更具体的别名。"""
    q = question.lower()
    candidates = []
    for spec in VARIABLE_SPECS:
        for keyword in spec.keywords:
            key = keyword.lower()
            start = q.find(key)
            while start >= 0:
                candidates.append((start, start + len(key), -len(key), spec))
                start = q.find(key, start + 1)
    candidates.sort(key=lambda item: (item[0], item[2]))
    chosen: list[tuple[int, int, VariableSpec]] = []
    for start, end, _, spec in candidates:
        if any(not (end <= old_start or start >= old_end)
               for old_start, old_end, _ in chosen):
            continue
        chosen.append((start, end, spec))
    chosen.sort(key=lambda item: item[0])
    unique: list[VariableSpec] = []
    seen: set[str] = set()
    for _, _, spec in chosen:
        if spec.name not in seen:
            unique.append(spec)
            seen.add(spec.name)
    return unique


def _choose_table(x: VariableSpec, y: VariableSpec, question: str) -> str | None:
    common = set(x.fields) & set(y.fields)
    if not common:
        return None
    if any(word.lower() in question.lower() for word in ("商品项", "sku", "具体商品")) \
            and ITEM_TABLE in common:
        return ITEM_TABLE
    seller_names = {"route", "distance_km", "seller_state", "cross_state",
                    "is_multi_seller_order", "is_any_item_handover_late"}
    if {x.name, y.name} & seller_names and SELLER_TABLE in common:
        return SELLER_TABLE
    for table in (ORDER_TABLE, SELLER_TABLE, ITEM_TABLE):
        if table in common:
            return table
    return None


def _method_for(x: VariableSpec, y: VariableSpec) -> str:
    kinds = {x.kind, y.kind}
    if x.kind == y.kind == "binary":
        return "binary_association"
    if "binary" in kinds and "ordinal" in kinds:
        return "cochran_armitage_trend"
    if x.kind in {"numeric", "ordinal"} and y.kind in {"numeric", "ordinal"}:
        return "spearman"
    if "binary" in kinds and "numeric" in kinds:
        return "mann_whitney_u"
    if "categorical" in kinds and ("numeric" in kinds or "ordinal" in kinds):
        return "kruskal_wallis"
    return "pearson_chi_square"


def plan_statistical_question(question: str) -> dict:
    variables = _extract_variables(question)
    # 兼容“延迟在低评分归因中用了什么检验”这一类省略“是否”的旧问法。
    # 只有已明确识别评价变量、且没有识别其他延迟变量时，才补为是否延迟。
    if len(variables) == 1 and variables[0].name in RATING_VARIABLES \
            and "延迟" in question:
        late_spec = SPEC_BY_NAME["is_late_delivery"]
        rating_spec = variables[0]
        variables = (
            [late_spec, rating_spec]
            if question.find("延迟") < min(
                (question.find(key) for key in rating_spec.keywords
                 if question.find(key) >= 0),
                default=len(question),
            )
            else [rating_spec, late_spec]
        )
    if len(variables) < 2:
        found = "、".join(spec.label for spec in variables) or "无"
        return {
            "ok": False,
            "error": f"未识别要检验的因素或两个变量（当前识别：{found}）。请明确写成“变量A与变量B是否显著相关”。",
        }
    if len(variables) > 2:
        return {
            "ok": False,
            "error": "一次双变量检验只能指定两个变量；多个变量请拆成两两问题，或明确使用“深度验证”。",
        }
    x, y = variables
    table = _choose_table(x, y, question)
    if table is None:
        return {
            "ok": False,
            "error": (
                f"{x.label}与{y.label}不在同一受控分析粒度中，当前禁止跨表自由连接。"
                "请改用同一订单、订单-卖家或商品项粒度的变量。"
            ),
        }
    method = _method_for(x, y)
    # 兼容旧版 factor/target 字段：评价变量仍作为 target；一般问题按文本顺序。
    if x.name in RATING_VARIABLES and y.name not in RATING_VARIABLES:
        factor_spec, target_spec = y, x
    else:
        factor_spec, target_spec = x, y
    return {
        "ok": True,
        "variable_x": x.name, "variable_x_label": x.label, "x_kind": x.kind,
        "variable_y": y.name, "variable_y_label": y.label, "y_kind": y.kind,
        "x_field": x.fields[table], "y_field": y.fields[table],
        "table": table, "grain": TABLE_GRAINS[table], "method": method,
        "factor": factor_spec.fields[table], "factor_label": factor_spec.label,
        "factor_kind": factor_spec.kind,
        "target": target_spec.fields[table], "target_label": target_spec.label,
    }


def _where(plan: dict) -> str:
    clauses = ["is_delivery_analysis_eligible = 1"]
    if {plan["variable_x"], plan["variable_y"]} & RATING_VARIABLES:
        clauses.append("has_review_record = 1")
    if plan["table"] == SELLER_TABLE and \
            "is_multi_seller_order" not in {plan["variable_x"], plan["variable_y"]}:
        clauses.append("is_multi_seller_order = 0")
    return " AND ".join(clauses)


def _coerce(series: pd.Series, spec: VariableSpec) -> pd.Series:
    if spec.order_map:
        return series.map(spec.order_map)
    if spec.kind in {"numeric", "ordinal", "binary"}:
        return pd.to_numeric(series, errors="coerce")
    return series


def _load_pair(provider: DataProvider, plan: dict) -> tuple[pd.DataFrame, list[str]]:
    sqls: list[str] = []
    df = load_table(
        provider, plan["table"], [plan["x_field"], plan["y_field"]],
        where=_where(plan), sql_sink=sqls,
    )
    if plan["x_field"] != "x_value":
        df = df.rename(columns={plan["x_field"]: "x_value", plan["y_field"]: "y_value"})
    df["x_value"] = _coerce(df["x_value"], SPEC_BY_NAME[plan["variable_x"]])
    df["y_value"] = _coerce(df["y_value"], SPEC_BY_NAME[plan["variable_y"]])
    return df.dropna(subset=["x_value", "y_value"]), sqls


def _spearman(provider: DataProvider, plan: dict) -> dict:
    df, sqls = _load_pair(provider, plan)
    if len(df) < 3 or df["x_value"].nunique() < 2 or df["y_value"].nunique() < 2:
        return {"ok": False, "error": "有效样本不足或变量没有变异", **plan, "sqls": sqls}
    test = correlation_test(df, "x_value", "y_value")
    return {
        "ok": True, **plan, **test, "sample": test["n"],
        "method_label": "Spearman 秩相关检验",
        "method_reason": "两个变量均为连续/有序变量，使用对非正态和异常值更稳健的秩相关。",
        "effect_size": test["rho"], "effect_name": "Spearman ρ",
        "significant": bool(test["p"] < 0.05), "sqls": sqls,
    }


def _mann_whitney(provider: DataProvider, plan: dict) -> dict:
    df, sqls = _load_pair(provider, plan)
    if plan["x_kind"] == "binary":
        group, numeric = "x_value", "y_value"
        group_label, numeric_label = plan["variable_x_label"], plan["variable_y_label"]
    else:
        group, numeric = "y_value", "x_value"
        group_label, numeric_label = plan["variable_y_label"], plan["variable_x_label"]
    test = distribution_test(df, numeric, group)
    if "error" in test:
        return {"ok": False, **plan, **test, "sqls": sqls}
    return {
        "ok": True, **plan, **test,
        "method_label": "Mann–Whitney U 检验",
        "method_reason": f"{group_label}为二分类变量，比较两组{numeric_label}的分布，不假设正态。",
        "sample": int(test["n0"] + test["n1"]),
        "effect_name": "秩二列相关", "significant": bool(test["p"] < 0.05),
        "sqls": sqls,
    }


def _trend(provider: DataProvider, plan: dict) -> dict:
    df, sqls = _load_pair(provider, plan)
    if plan["x_kind"] == "ordinal":
        score, target = "x_value", "y_value"
        score_label, target_label = plan["variable_x_label"], plan["variable_y_label"]
    else:
        score, target = "y_value", "x_value"
        score_label, target_label = plan["variable_y_label"], plan["variable_x_label"]
    test = trend_test(df, score, target)
    if "error" in test:
        return {"ok": False, **plan, **test, "sqls": sqls}
    return {
        "ok": True, **plan, **test,
        "method_label": "Cochran–Armitage 趋势检验",
        "method_reason": f"{score_label}有明确顺序，检验{target_label}比例是否随等级单调变化。",
        "sample": test["n"], "significant": bool(test["p"] < 0.05), "sqls": sqls,
    }


def _binary_association(provider: DataProvider, plan: dict) -> dict:
    # 使用 factor→target 顺序，让 OR 的方向和旧版输出保持一致。
    x, y, table = plan["factor"], plan["target"], plan["table"]
    sql = (
        f"SELECT {x}, {y}, COUNT(*) AS n FROM {table} WHERE {_where(plan)} "
        f"GROUP BY {x}, {y} LIMIT 10"
    )
    rows = provider.execute(sql)
    counts = {(int(r[x]), int(r[y])): int(r["n"]) for r in rows
              if r[x] is not None and r[y] is not None}
    matrix = np.array([
        [counts.get((0, 0), 0), counts.get((0, 1), 0)],
        [counts.get((1, 0), 0), counts.get((1, 1), 0)],
    ], dtype=float)
    if not matrix.sum() or (matrix.sum(axis=0) == 0).any() or (matrix.sum(axis=1) == 0).any():
        return {"ok": False, "error": "二分类列联表存在空组", **plan, "sql": sql}
    _, _, _, expected = stats.chi2_contingency(matrix)
    if (expected < 5).any() or (matrix < 5).any():
        odds, p = stats.fisher_exact(matrix)
        method_label = "Fisher 精确检验"
    else:
        _, p, _, _ = stats.chi2_contingency(matrix, correction=True)
        odds = (matrix[0, 0] * matrix[1, 1]) / (matrix[0, 1] * matrix[1, 0]) \
            if matrix[0, 1] * matrix[1, 0] else float("inf")
        method_label = "Pearson 卡方检验（Yates校正）"
    return {
        "ok": True, **plan, "p": float(p), "or": float(odds),
        "method_label": method_label,
        "method_reason": "两个变量均为二分类变量，使用2×2列联表检验并报告OR。",
        "sample": int(matrix.sum()), "significant": bool(p < 0.05), "sql": sql,
    }


def _categorical_association(provider: DataProvider, plan: dict) -> dict:
    x, y, table = plan["factor"], plan["target"], plan["table"]
    sql = (
        f"SELECT {x}, {y}, COUNT(*) AS n FROM {table} WHERE {_where(plan)} "
        f"GROUP BY {x}, {y} LIMIT 10001"
    )
    rows = provider.execute(sql)
    if len(rows) >= 10001:
        return {"ok": False, "error": "分类组合超过10000个，无法在当前安全上限内可靠检验", **plan, "sql": sql}
    raw = pd.DataFrame(rows).dropna(subset=[x, y])
    if raw.empty:
        return {"ok": False, "error": "有效样本为空", **plan, "sql": sql}
    total = int(raw["n"].sum())
    min_group = max(20, math.ceil(total * 0.0005))
    x_totals = raw.groupby(x)["n"].sum()
    y_totals = raw.groupby(y)["n"].sum()
    keep_x = set(x_totals[x_totals >= min_group].index)
    keep_y = set(y_totals[y_totals >= min_group].index)
    kept = raw[raw[x].isin(keep_x) & raw[y].isin(keep_y)]
    pivot = kept.pivot_table(index=x, columns=y, values="n", aggfunc="sum", fill_value=0)
    pivot = pivot.loc[pivot.sum(axis=1) > 0, pivot.sum(axis=0) > 0]
    if min(pivot.shape, default=0) < 2:
        return {"ok": False, "error": f"达到最小样本量{min_group}的有效分组不足", **plan, "sql": sql}
    chi2, p, dof, expected = stats.chi2_contingency(pivot.to_numpy(dtype=float))
    n = int(pivot.to_numpy().sum())
    denom = n * max(1, min(pivot.shape) - 1)
    v = math.sqrt(float(chi2) / denom) if denom else 0.0
    low_expected_share = float((expected < 5).sum() / expected.size)
    assumption_ok = low_expected_share <= 0.2 and bool((expected >= 1).all())
    return {
        "ok": True, **plan, "method_label": "Pearson 卡方独立性检验 + Cramér's V",
        "method_reason": "两个变量均按分类水平构造列联表，并用Cramér's V衡量关联强度。",
        "sample": n, "groups_tested": int(pivot.shape[0] + pivot.shape[1]),
        "group_shape": list(pivot.shape), "min_group_sample": min_group,
        "excluded_groups": int((len(x_totals) - len(keep_x)) + (len(y_totals) - len(keep_y))),
        "statistic": float(chi2), "dof": int(dof), "p": float(p),
        "effect_size": float(v), "effect_name": "Cramér's V",
        "assumption_ok": assumption_ok, "low_expected_share": low_expected_share,
        "significant": bool(p < 0.05 and assumption_ok), "sql": sql,
    }


def _kruskal(provider: DataProvider, plan: dict) -> dict:
    df, sqls = _load_pair(provider, plan)
    if plan["x_kind"] == "categorical":
        group, value = "x_value", "y_value"
        group_label, value_label = plan["variable_x_label"], plan["variable_y_label"]
    else:
        group, value = "y_value", "x_value"
        group_label, value_label = plan["variable_y_label"], plan["variable_x_label"]
    counts = df.groupby(group)[value].count().sort_values(ascending=False)
    min_group = max(20, math.ceil(len(df) * 0.0005))
    keep = list(counts[counts >= min_group].index[:200])
    groups = [df.loc[df[group] == key, value].dropna().to_numpy(dtype=float) for key in keep]
    if len(groups) < 2:
        return {"ok": False, "error": f"达到最小样本量{min_group}的分组不足两个", **plan, "sqls": sqls}
    h, p = stats.kruskal(*groups)
    n, k = sum(map(len, groups)), len(groups)
    epsilon = max(0.0, min(1.0, (float(h) - k + 1) / (n - k))) if n > k else 0.0
    summaries = [{
        "value": key,
        "sample": int(len(group_values)),
        "median": float(np.median(group_values)),
    } for key, group_values in zip(keep, groups)]
    summaries.sort(key=lambda row: (row["median"], row["sample"]), reverse=True)
    return {
        "ok": True, **plan, "method_label": "Kruskal–Wallis 检验",
        "method_reason": f"{group_label}为多分类变量，比较各组{value_label}分布且不假设正态。",
        "sample": int(n), "groups_tested": k, "min_group_sample": min_group,
        "excluded_groups": int(len(counts) - len(keep)), "statistic": float(h),
        "p": float(p), "effect_size": epsilon, "effect_name": "ε²",
        "significant": bool(p < 0.05), "group_summaries": summaries[:10],
        "descriptive_label": f"{value_label}中位数较高的{group_label}", "sqls": sqls,
    }


def analyze_statistical_question(provider: DataProvider, question: str) -> dict:
    plan = plan_statistical_question(question)
    if not plan.get("ok"):
        return plan
    handlers = {
        "spearman": _spearman,
        "mann_whitney_u": _mann_whitney,
        "cochran_armitage_trend": _trend,
        "binary_association": _binary_association,
        "pearson_chi_square": _categorical_association,
        "kruskal_wallis": _kruskal,
    }
    try:
        result = handlers[plan["method"]](provider, plan)
    except (TypeError, ValueError, KeyError) as exc:
        return {"ok": False, **plan, "error": f"统计计算失败：{exc}"}
    if not result.get("ok"):
        return result
    p = result.get("p")
    if result.get("assumption_ok") is False:
        result["conclusion"] = (
            f"列联表稀疏，{result['method_label']}的近似条件未满足，当前不能可靠判断"
            f"{result['variable_x_label']}与{result['variable_y_label']}的关联。"
        )
    elif result.get("significant"):
        effect = result.get("effect_size")
        effect_text = f"，{result.get('effect_name')}={effect:.3f}" if effect is not None else ""
        result["conclusion"] = (
            f"在当前观察样本中，{result['variable_x_label']}与{result['variable_y_label']}存在统计关联"
            f"（p={p:.4g}{effect_text}）；这是双变量关联，不代表因果。"
        )
    else:
        result["conclusion"] = (
            f"当前数据未发现{result['variable_x_label']}与{result['variable_y_label']}存在统计显著关联"
            f"（p={p:.4g}）。不显著不等于证明二者完全无关。"
        )
    return result


def format_statistical_result(result: dict) -> str:
    if not result.get("ok"):
        return "统计分析未完成：" + result.get("error", "未知错误")
    lines = [
        f"变量：{result['variable_x_label']} × {result['variable_y_label']}",
        f"分析粒度：{result['grain']}",
        f"方法：{result['method_label']}",
        f"选择理由：{result['method_reason']}",
    ]
    p = result.get("p")
    if isinstance(p, (int, float)):
        lines.append(f"p值：{p:.6g}")
    if result.get("effect_name") and isinstance(result.get("effect_size"), (int, float)):
        lines.append(f"效应量：{result['effect_name']}={result['effect_size']:.4g}")
    elif isinstance(result.get("or"), (int, float)):
        lines.append(f"效应量：优势比（OR）={result['or']:.4g}")
    lines.append(f"结论：{result['conclusion']}")
    lines.append(f"有效样本量：{result.get('sample', 0)}")
    if result.get("groups_tested") is not None:
        lines.append(
            f"纳入分组：{result['groups_tested']}；最小组样本阈值："
            f"{result.get('min_group_sample', '—')}；排除小组：{result.get('excluded_groups', 0)}"
        )
    if result.get("group_summaries"):
        lines.append(result.get("descriptive_label", "分组描述") + "：")
        for row in result["group_summaries"][:5]:
            lines.append(f"- {row['value']}：中位数={row['median']:.3g} (n={row['sample']})")
    lines.append(
        "边界：双变量显著性没有控制其他混杂因素；业务治理仍需结合效应量、问题规模和深度验证。"
    )
    return "\n".join(lines)
