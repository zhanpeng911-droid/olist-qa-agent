"""自然语言统计问题的确定性规划与执行。

大模型不负责选择统计方法。这里根据变量类型固定选择检验，并尽量在数据库端
先聚合，避免把全量明细传回应用或模型。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

from .data_provider import DataProvider
from .statistics import correlation_test, distribution_test, load_table, trend_test


@dataclass(frozen=True)
class FactorSpec:
    name: str
    label: str
    table: str
    kind: str
    keywords: tuple[str, ...]


FACTOR_SPECS = (
    FactorSpec("route", "线路", "mart_order_seller_delivery", "categorical",
               ("线路", "路线", "route")),
    FactorSpec("cross_state", "是否跨州", "mart_order_seller_delivery", "binary",
               ("跨州", "cross_state")),
    FactorSpec("seller_state", "卖家州", "mart_order_seller_delivery", "categorical",
               ("卖家州", "卖家地区")),
    FactorSpec("customer_state", "客户州", "mart_order_delivery", "categorical",
               ("客户州", "客户地区", "收货州", "地区")),
    FactorSpec("primary_category_name", "主要品类", "mart_order_delivery", "categorical",
               ("品类", "类别", "category")),
    FactorSpec("primary_payment_type", "支付方式", "mart_order_delivery", "categorical",
               ("支付方式", "支付类型", "payment")),
    FactorSpec("delay_bucket", "延迟分档", "mart_order_delivery", "ordinal",
               ("延迟分档", "延迟档位", "延迟程度", "延迟等级")),
    FactorSpec("is_late_delivery", "是否延迟", "mart_order_delivery", "binary",
               ("是否延迟", "延迟订单", "延迟与", "延迟是否")),
    FactorSpec("late_days", "延迟天数", "mart_order_delivery", "numeric",
               ("延迟天数", "晚到天数")),
    FactorSpec("fulfillment_days", "总履约时长", "mart_order_delivery", "numeric",
               ("履约时长", "总时长")),
    FactorSpec("approval_days", "支付审批时长", "mart_order_delivery", "numeric",
               ("审批时长", "支付审批")),
    FactorSpec("freight_total", "运费", "mart_order_delivery", "numeric",
               ("运费", "freight")),
    FactorSpec("price_total", "商品金额", "mart_order_delivery", "numeric",
               ("商品金额", "订单金额", "价格", "price")),
)

STATISTICAL_HINTS = (
    "显著", "相关性", "相关", "关联", "检验", "p值", "p 值", "置信区间",
    "统计上", "是否有关", "有关系吗", "有没有关系", "影响是否", "差异",
)

DELAY_RANK = {"按时": 0, "1-3天": 1, "4-7天": 2, "8-14天": 3, "15天+": 4}


def is_statistical_question(question: str) -> bool:
    q = question.lower()
    return any(h.lower() in q for h in STATISTICAL_HINTS)


def plan_statistical_question(question: str) -> dict:
    """根据关键词和变量类型选择方法；不执行查询。"""
    q = question.lower()
    factor = next(
        (spec for spec in FACTOR_SPECS
         if any(keyword.lower() in q for keyword in spec.keywords)),
        None,
    )
    # “延迟用了什么检验”没有指明天数/档位时，默认解释为二分类的是否延迟。
    if factor is None and "延迟" in q:
        factor = next(spec for spec in FACTOR_SPECS
                      if spec.name == "is_late_delivery")
    target = "is_low_score" if "低评分" in q or "差评" in q else "review_score"
    target_label = "低评分" if target == "is_low_score" else "评价分数"
    if factor is None:
        return {
            "ok": False,
            "error": "识别到统计问题，但未识别要检验的因素。请明确写出线路、延迟、地区、品类、支付方式、金额或时长。",
        }

    if target == "is_low_score":
        method = {
            "categorical": "pearson_chi_square",
            "binary": "binary_association",
            "ordinal": "cochran_armitage_trend",
            "numeric": "mann_whitney_u",
        }[factor.kind]
    else:
        method = {
            "categorical": "kruskal_wallis",
            "binary": "mann_whitney_u",
            "ordinal": "spearman",
            "numeric": "spearman",
        }[factor.kind]
    return {
        "ok": True,
        "factor": factor.name,
        "factor_label": factor.label,
        "factor_kind": factor.kind,
        "target": target,
        "target_label": target_label,
        "table": factor.table,
        "method": method,
    }


def _where(table: str) -> str:
    clauses = ["is_delivery_analysis_eligible = 1", "has_review_record = 1"]
    if table == "mart_order_seller_delivery":
        clauses.append("is_multi_seller_order = 0")
    return " AND ".join(clauses)


def _cramers_v(chi2: float, n: int, shape: tuple[int, int]) -> float:
    denom = n * max(1, min(shape) - 1)
    return math.sqrt(chi2 / denom) if denom else 0.0


def _categorical_binary(provider: DataProvider, plan: dict) -> dict:
    """数据库端聚合分类因素×低评分，应用端只处理列联表。"""
    factor = plan["factor"]
    table = plan["table"]
    sql = (
        f"SELECT {factor}, is_low_score, COUNT(*) AS n FROM {table} "
        f"WHERE {_where(table)} GROUP BY {factor}, is_low_score LIMIT 10000"
    )
    rows = provider.execute(sql)
    raw = pd.DataFrame(rows)
    if raw.empty:
        return {"ok": False, "error": "有效样本为空", "sql": sql, **plan}
    pivot = raw.pivot_table(index=factor, columns="is_low_score", values="n",
                            aggfunc="sum", fill_value=0).reindex(columns=[0, 1], fill_value=0)
    pivot.columns = [0, 1]
    pivot["sample"] = pivot[0] + pivot[1]
    total_n = int(pivot["sample"].sum())
    # 高基数因素必须先过滤小组；阈值随全量样本增长。
    min_group = max(20, math.ceil(total_n * 0.001)) if factor == "route" else max(20, math.ceil(total_n * 0.0005))
    kept = pivot[pivot["sample"] >= min_group].copy()
    excluded_groups = int(len(pivot) - len(kept))
    excluded_n = int(pivot.loc[pivot["sample"] < min_group, "sample"].sum())
    if len(kept) < 2:
        return {
            "ok": False, "error": f"达到最小样本量 {min_group} 的分组不足两个，无法检验",
            "sql": sql, "sample": total_n, **plan,
        }
    table_values = kept[[0, 1]].to_numpy(dtype=float)
    chi2, p, dof, expected = stats.chi2_contingency(table_values)
    n = int(table_values.sum())
    low_expected_share = float((expected < 5).sum() / expected.size)
    assumption_ok = low_expected_share <= 0.2 and bool((expected >= 1).all())
    rates = []
    for value, row in kept.iterrows():
        rates.append({
            "value": value,
            "sample": int(row["sample"]),
            "low_score_count": int(row[1]),
            "low_score_rate": float(row[1] / row["sample"]),
        })
    rates.sort(key=lambda x: (x["low_score_rate"], x["sample"]), reverse=True)
    v = _cramers_v(float(chi2), n, table_values.shape)
    significant = bool(p < 0.05 and assumption_ok)
    return {
        "ok": True, **plan,
        "method_label": "Pearson 卡方独立性检验 + Cramér's V",
        "method_reason": f"{plan['factor_label']}是分类变量，低评分是二分类变量，不能使用 Pearson/Spearman 相关系数。",
        "sample": n,
        "original_sample": total_n,
        "groups_tested": int(len(kept)),
        "excluded_groups": excluded_groups,
        "excluded_sample": excluded_n,
        "min_group_sample": min_group,
        "statistic": float(chi2),
        "dof": int(dof),
        "p": float(p),
        "effect_size": float(v),
        "effect_name": "Cramér's V",
        "assumption_ok": assumption_ok,
        "low_expected_share": low_expected_share,
        "significant": significant,
        "top_groups": rates[:10],
        "sql": sql,
    }


def _binary_binary(provider: DataProvider, plan: dict) -> dict:
    factor = plan["factor"]
    table = plan["table"]
    sql = (
        f"SELECT {factor}, is_low_score, COUNT(*) AS n FROM {table} "
        f"WHERE {_where(table)} GROUP BY {factor}, is_low_score LIMIT 10"
    )
    rows = provider.execute(sql)
    counts = {(int(r[factor]), int(r["is_low_score"])): int(r["n"]) for r in rows}
    a, b = counts.get((0, 0), 0), counts.get((0, 1), 0)
    c, d = counts.get((1, 0), 0), counts.get((1, 1), 0)
    matrix = np.array([[a, b], [c, d]], dtype=float)
    n = int(matrix.sum())
    if not n or (matrix.sum(axis=0) == 0).any() or (matrix.sum(axis=1) == 0).any():
        return {"ok": False, "error": "二分类列联表存在空组", "sql": sql, **plan}
    _, _, _, expected = stats.chi2_contingency(matrix)
    if (expected < 5).any() or (matrix < 5).any():
        odds, p = stats.fisher_exact(matrix)
        method = "Fisher 精确检验"
    else:
        chi2, p, _, _ = stats.chi2_contingency(matrix, correction=True)
        odds = (a * d) / (b * c) if b * c else float("inf")
        method = "Pearson 卡方检验（Yates校正）"
    p0 = b / (a + b) if a + b else 0.0
    p1 = d / (c + d) if c + d else 0.0
    rr = p1 / p0 if p0 else None
    return {
        "ok": True, **plan, "method_label": method,
        "method_reason": f"{plan['factor_label']}和低评分均为二分类变量。",
        "sample": n, "p": float(p), "or": float(odds), "rr": rr,
        "rate_factor_0": p0, "rate_factor_1": p1,
        "significant": bool(p < 0.05), "sql": sql,
    }


def _raw_test(provider: DataProvider, plan: dict) -> dict:
    factor, target, table = plan["factor"], plan["target"], plan["table"]
    cols = [factor, target]
    df = load_table(provider, table, cols, where=_where(table))
    if df.empty:
        return {"ok": False, "error": "有效样本为空", **plan}
    # PyMySQL 会把 DECIMAL 返回为 decimal.Decimal；SciPy 的 isnan 不接受
    # object/Decimal 数组。进入统计检验前统一转换连续结果与数值/二分类因素。
    numeric_columns = {target}
    if plan["factor_kind"] in {"numeric", "binary"}:
        numeric_columns.add(factor)
    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    if plan["method"] == "mann_whitney_u":
        numeric, group = (factor, target) if target == "is_low_score" else (target, factor)
        test = distribution_test(df, numeric, group)
        return {"ok": "error" not in test, **plan, **test,
                "method_label": "Mann–Whitney U 检验",
                "method_reason": "比较二组的偏态连续/有序分布，不假设正态。",
                "sample": int(test.get("n0", 0) + test.get("n1", 0)),
                "significant": bool(test.get("p", 1) < 0.05)}
    if plan["method"] == "spearman":
        if factor == "delay_bucket":
            df["delay_rank"] = df[factor].map(DELAY_RANK)
            factor = "delay_rank"
        test = correlation_test(df, factor, target)
        return {"ok": True, **plan, **test,
                "method_label": "Spearman 秩相关",
                "method_reason": "变量为连续/有序数据，采用对异常值和非正态更稳健的秩相关。",
                "sample": int(test.get("n", 0)),
                "effect_size": test["rho"], "effect_name": "Spearman ρ",
                "significant": bool(test["p"] < 0.05)}
    if plan["method"] == "cochran_armitage_trend":
        df["delay_rank"] = df[factor].map(DELAY_RANK)
        test = trend_test(df, "delay_rank", target)
        return {"ok": "error" not in test, **plan, **test,
                "method_label": "Cochran–Armitage 趋势检验",
                "method_reason": "延迟分档具有明确顺序，检验低评分率是否随等级单调变化。",
                "sample": int(test.get("n", 0)),
                "significant": bool(test.get("p", 1) < 0.05)}
    # 分类因素 × 评分：Kruskal-Wallis
    groups = [g[target].dropna().to_numpy() for _, g in df.groupby(factor)]
    groups = [g for g in groups if len(g) >= 10]
    if len(groups) < 2:
        return {"ok": False, "error": "有效分组不足两个", **plan}
    h, p = stats.kruskal(*groups)
    return {"ok": True, **plan, "method_label": "Kruskal–Wallis 检验",
            "method_reason": "比较多个分类组的有序评分分布，不假设正态。",
            "sample": int(sum(map(len, groups))), "groups_tested": len(groups),
            "statistic": float(h), "p": float(p), "significant": bool(p < 0.05)}


def analyze_statistical_question(provider: DataProvider, question: str) -> dict:
    plan = plan_statistical_question(question)
    if not plan.get("ok"):
        return plan
    if plan["target"] == "is_low_score" and plan["factor_kind"] == "categorical":
        result = _categorical_binary(provider, plan)
    elif plan["target"] == "is_low_score" and plan["factor_kind"] == "binary":
        result = _binary_binary(provider, plan)
    else:
        result = _raw_test(provider, plan)
    if not result.get("ok"):
        return result
    p = result.get("p")
    if result.get("assumption_ok") is False:
        result["conclusion"] = (
            f"列联表稀疏，{result['method_label']}的近似条件未满足，当前不能判断"
            f"{result['factor_label']}与{result['target_label']}是否存在稳定关联。"
        )
    elif result.get("significant"):
        effect = result.get("effect_size")
        effect_text = f"，{result.get('effect_name')}={effect:.3f}" if effect is not None else ""
        result["conclusion"] = (
            f"在当前观察样本中，{result['factor_label']}与{result['target_label']}存在统计关联"
            f"（p={p:.4g}{effect_text}）；这是相关关系，不代表因果。"
        )
    else:
        result["conclusion"] = (
            f"当前数据未发现{result['factor_label']}与{result['target_label']}存在统计显著关联"
            f"（p={p:.4g}）。不显著不等于证明二者完全无关。"
        )
    return result


def format_statistical_result(result: dict) -> str:
    if not result.get("ok"):
        return "统计分析未完成：" + result.get("error", "未知错误")
    lines = [
        f"方法：{result['method_label']}",
        f"选择理由：{result['method_reason']}",
    ]
    p = result.get("p")
    if isinstance(p, (int, float)):
        lines.append(f"p值：{p:.6g}")
    if result.get("effect_name") and isinstance(result.get("effect_size"), (int, float)):
        lines.append(
            f"效应量：{result['effect_name']}={result['effect_size']:.4g}"
        )
    elif isinstance(result.get("or"), (int, float)):
        lines.append(f"效应量：优势比（OR）={result['or']:.4g}")
    lines.append(f"结论：{result['conclusion']}")
    if result.get("sample") is not None:
        lines.append(f"有效样本量：{result['sample']}")
    if result.get("groups_tested") is not None:
        lines.append(
            f"纳入分组：{result['groups_tested']}；最小组样本阈值："
            f"{result.get('min_group_sample', '—')}；排除小组：{result.get('excluded_groups', 0)}"
        )
    if result.get("top_groups"):
        lines.append("高低评分率分组（仅作描述性定位）：")
        for group in result["top_groups"][:5]:
            lines.append(
                f"- {group['value']}：{group['low_score_rate']:.2%} "
                f"(n={group['sample']})"
            )
    lines.append("边界：显著性用于判断统计关联；业务治理仍需结合效应量、问题规模和试点验证。")
    return "\n".join(lines)


# 通用双变量实现覆盖上面的第一版“评价结果固定为目标变量”逻辑。
# 保留旧实现源码便于对照历史版本，同时让现有导入路径无缝升级。
from .bivariate_analysis import (  # noqa: E402,F401
    VARIABLE_SPECS,
    analyze_statistical_question,
    format_statistical_result,
    is_statistical_question,
    plan_statistical_question,
    supported_variables,
)
