"""L2 描述性归因（M2）：固定顺序确定性流程。

覆盖订单级 + 卖家/线路级，输出带 P0/P1/P2 的描述性问题对象清单
（样本量 / 低评分率 / 基准率 / 率差 / Lift / 超额低评分 / 规模×风险排序）。

边界：只做描述性归因，不下因果结论，不生成改善建议（建议 = M4）。
所有分组基于语义字典口径与 min_group_sample 过滤，结果附 SQL 可对账。
"""
from __future__ import annotations

import math
import re

import pandas as pd

from .data_provider import DataProvider, SAMPLE_SOURCE_LABEL
from .low_score_attribution import run_low_score_attribution
from .semantic import SemanticLayer
from .target_attribution import TARGET_SPECS, run_target_attribution
from .statistics import (
    categorical_test_counts, multiple_correction,
)
from .tools import Tools

ORDER_TABLE = "mart_order_delivery"
SELLER_TABLE = "mart_order_seller_delivery"
ORDER_COUNT_METRIC = "order_count"
SELLER_COUNT_METRIC = "record_count"
ATTRIBUTION_SCHEMA_VERSION = "2026-08-21.2"

# 候选因素维度（订单级 / 卖家级）
ORDER_DIMENSIONS = [
    "is_late_delivery", "delay_bucket", "customer_state",
    "primary_category_name", "primary_payment_type", "order_month",
]
SELLER_DIMENSIONS = ["seller_state", "route", "cross_state"]

# 卖家表默认过滤多卖家订单，避免重复计算评价
SELLER_FILTER = {"is_multi_seller_order": 0}

# route 是高基数维度（卖家州×客户州组合），全局 min_group_sample 会全过滤，
# 因此线路深挖使用独立的较小基础阈值 + 按样本量动态放大。
ROUTE_MIN_SAMPLE = 15
ROUTE_SAMPLE_FRACTION = 0.02

CAVEATS = [
    "本结果来自观察性数据，只能说明统计关联，不能据此判断因果关系。",
    "样本量未达到最低要求的分组已排除；判断业务重要性时需同时考虑关联强度和涉及订单量。",
    "当前分析数据表不含评价正文，无法验证商品破损、错发或客服体验等文字反馈中的原因。",
    "商品项表只用于受控下钻；低评分率按去重 order_id 计算，不能把商品项行当成独立评价。",
    "商品和品类检验按“订单是否包含该对象”统计，并使用FDR校正控制多次检验增加的误报风险；显著仍只表示关联。",
    "问题排查级别P0/P1/P2按涉及规模和相对风险排序（P0最高），仅用于安排核查顺序，不是治理策略。",
]

FEATURE_LABELS = {
    "is_late_delivery": "是否延迟",
    "delay_bucket": "延迟分档",
    "customer_state": "客户州",
    "primary_category_name": "主要品类",
    "primary_payment_type": "支付方式",
    "order_month": "购买月份",
    "seller_state": "卖家州",
    "route": "卖家州→客户州线路",
    "cross_state": "是否跨州",
    "late_days": "延迟天数",
    "approval_days": "支付审批时长",
    "fulfillment_days": "总履约时长",
    "price_total": "商品金额",
}

LOW_SCORE_TARGET_HINTS = (
    "低评分", "差评", "低分", "三星及以下", "1-3分", "评价不好",
)
HANDOVER_TARGET_HINTS = (
    "交接超期", "晚交接", "交接延误", "发货超期", "揽收超期",
)
DELIVERY_TARGET_HINTS = (
    "延迟", "配送延误", "送达延误", "晚到", "未按时送达", "逾期送达",
)
UNSUPPORTED_ATTRIBUTION_TARGET_HINTS = (
    "复购", "销售额", "成交额", "订单量", "客单价", "运费", "金额", "退款",
)


def resolve_attribution_target(question: str | None) -> str | None:
    """识别三个受控目标；未点名目标的“归因分析”兼容为低评分归因。"""
    if not question:
        return "is_low_score"
    if any(hint in question for hint in LOW_SCORE_TARGET_HINTS):
        return "is_low_score"
    if any(hint in question for hint in HANDOVER_TARGET_HINTS):
        return "is_any_item_handover_late"
    if any(hint in question for hint in DELIVERY_TARGET_HINTS):
        return "is_late_delivery"
    # 没有明确目标时仍按既有行为处理为低评分；明确点名其他目标则拒绝，
    # 防止把复购、退款等问题静默替换成低评分模型。
    explicit_target = re.search(r"对(.{1,20}?)(?:进行|做)?(?:原因)?(?:归因|原因分析)", question)
    if explicit_target:
        return None
    if any(hint in question for hint in UNSUPPORTED_ATTRIBUTION_TARGET_HINTS):
        return None
    return "is_low_score"


def supports_attribution_target(question: str | None) -> bool:
    return resolve_attribution_target(question) is not None


def build_feature_test_catalog(verification: dict,
                               alpha: float = 0.05) -> list[dict]:
    """把底层检验结果整理成可直接向用户解释的特征清单。"""
    catalog: list[dict] = []
    for test in verification.get("single_tests", []):
        raw_factor = str(test.get("factor", ""))
        if raw_factor == "review_score by is_late_delivery":
            feature = "is_late_delivery"
            label = "是否延迟（评价分数分布）"
        elif raw_factor.endswith("×review_score"):
            feature = raw_factor.removesuffix("×review_score")
            label = f"{FEATURE_LABELS.get(feature, feature)}与评价分数"
        elif raw_factor.endswith("(趋势)"):
            feature = raw_factor.removesuffix("(趋势)")
            label = FEATURE_LABELS.get(feature, feature)
        else:
            feature = raw_factor
            label = FEATURE_LABELS.get(feature, feature)

        test_type = test.get("type")
        if test_type == "categorical":
            method = ("Fisher精确检验" if test.get("method") == "fisher"
                      else "Pearson卡方检验（Yates校正）")
            effect_name, effect_value = "OR", test.get("or")
        elif test_type == "rc":
            method = "Pearson卡方独立性检验"
            effect_name, effect_value = "Cramér's V", test.get("cramers_v")
        elif test_type == "trend":
            method = "Cochran–Armitage趋势检验"
            effect_name, effect_value = "Z", test.get("z")
        elif test_type == "spearman":
            method = "Spearman秩相关检验"
            effect_name, effect_value = "ρ", test.get("rho")
        elif test_type == "mwu":
            method = "Mann–Whitney U检验"
            effect_name, effect_value = "秩二列相关", test.get("effect_size")
        else:
            method = str(test.get("method") or test_type or "未知")
            effect_name, effect_value = None, None

        p_raw = test.get("p")
        p_adjusted = test.get("p_adjusted")
        p_used = p_adjusted if p_adjusted is not None else p_raw
        significant = (
            isinstance(p_used, (int, float))
            and math.isfinite(float(p_used))
            and float(p_used) < alpha
            and test.get("assumption_ok") is not False
        )
        catalog.append({
            "feature": feature,
            "label": label,
            "target": ("评价分数" if test_type in {"spearman", "mwu"}
                       else "是否低评分"),
            "method": method,
            "p": p_raw,
            "p_adjusted": p_adjusted,
            "p_used": p_used,
            "p_basis": "FDR-BH校正后p值" if p_adjusted is not None else "原始p值",
            "significant": significant,
            "effect_name": effect_name,
            "effect_value": effect_value,
            "sample": test.get("n"),
            "assumption_ok": test.get("assumption_ok"),
            "lightweight_judgment": (
                "检验前提不足，不能判断" if test.get("assumption_ok") is False
                else ("存在显著关联，可优先关注" if significant
                      else "当前未发现显著关联")
            ),
        })
    return catalog


def build_deep_validation_plan(
    test_catalog: list[dict],
    descriptive_priority_features: set[str] | None = None,
    max_features: int = 8,
) -> list[dict]:
    """把显著、前提不足或描述性异常的线索转成深度验证任务。"""
    descriptive_priority_features = descriptive_priority_features or set()
    method_by_feature = {
        "is_late_delivery": (
            "多变量二项Logistic回归（HC3稳健标准误）",
            "控制月份、地区、品类、金额、运费、支付及履约结构后，检查调整后OR和95%CI。",
        ),
        "delay_bucket": (
            "分档总体卡方＋有序Logistic趋势模型",
            "分别验证全部订单和延迟订单内部趋势，并报告每升高一档的OR。",
        ),
        "customer_state": (
            "多变量Logistic＋州别联合显著性检验",
            "控制订单结构和履约因素，避免地区订单构成差异被误判为地区效应。",
        ),
        "primary_category_name": (
            "多变量Logistic＋品类联合显著性检验",
            "控制金额、运费和履约因素，并对主要高风险品类做敏感性分析。",
        ),
        "primary_payment_type": (
            "多变量Logistic＋支付方式联合显著性检验",
            "控制订单金额、分期及地区结构，确认支付方式是否仍有独立关联。",
        ),
        "order_month": (
            "多变量Logistic＋月份联合显著性检验",
            "控制地区、品类和履约结构，区分时间趋势与订单构成变化。",
        ),
        "seller_state": (
            "卖家层多变量Logistic＋州别联合显著性检验",
            "控制跨州、订单金额和履约表现，确认卖家地区关联是否稳定。",
        ),
        "cross_state": (
            "卖家层多变量Logistic（HC3稳健标准误）",
            "控制卖家州、订单金额和延迟后，检查跨州变量的调整后OR。",
        ),
        "route": (
            "高基数线路分层模型或正则化Logistic＋留出验证",
            "控制线路小样本和多重比较，并验证高风险线路在留出数据上是否稳定。",
        ),
        "late_days": (
            "多变量Logistic（连续项/限制性样条）",
            "检查延迟天数与低评分风险是否存在非线性或阈值效应。",
        ),
        "approval_days": (
            "多变量Logistic（连续项）",
            "控制订单结构和后续履约时长，确认审批时长是否具有独立解释力。",
        ),
        "fulfillment_days": (
            "多变量Logistic（连续项/分段项）",
            "与是否延迟分开建模，检查总履约时长是否提供额外解释信息。",
        ),
        "price_total": (
            "多变量Logistic（对数金额或分位数组）",
            "控制品类、运费和支付方式，确认金额关联是否由订单构成造成。",
        ),
    }
    feature_order = {
        "is_late_delivery": 0, "delay_bucket": 1, "late_days": 2,
        "fulfillment_days": 3, "route": 4, "cross_state": 5,
        "primary_category_name": 6, "customer_state": 7,
        "seller_state": 8, "primary_payment_type": 9,
        "approval_days": 10, "price_total": 11,
        "order_month": 12,
    }
    candidates = sorted(
        (
            row for row in test_catalog
            if (row.get("significant")
                or row.get("assumption_ok") is False
                or row.get("feature") in descriptive_priority_features)
        ),
        key=lambda row: (
            0 if row.get("significant") else (
                1 if row.get("assumption_ok") is False else 2
            ),
            feature_order.get(row.get("feature"), 99),
            float(row.get("p_used") or 1),
        ),
    )
    plan: list[dict] = []
    seen: set[str] = set()
    for row in candidates:
        feature = row["feature"]
        if feature in seen:
            continue
        seen.add(feature)
        method, purpose = method_by_feature.get(
            feature,
            ("多变量模型或分层敏感性分析", "控制主要混杂因素后重新验证关联稳定性。"),
        )
        if row.get("significant"):
            reason = (
                f"单变量检验达到筛选标准（{row['method']}，"
                f"{row['p_basis']}={row['p_used']:.4g}）；"
                "仍需通过多变量模型评估调整后的关联。"
            )
            status = "通过单变量筛选，待多变量调整"
        elif row.get("assumption_ok") is False:
            reason = (
                f"{row['method']}的列联表前提不足；当前p值不能作为稳定结论。"
            )
            status = "检验前提不足，需更稳健的方法或合并稀疏组"
        else:
            reason = (
                "存在高于基准的描述性问题对象，但变量整体检验未显著；"
                "需要确认局部异常能否在更大样本或调整模型中复现。"
            )
            status = "描述性异常但未显著，按业务重要性决定是否深度验证"
        plan.append({
            "feature": feature,
            "label": FEATURE_LABELS.get(feature, row["label"]),
            "screening_method": row["method"],
            "screening_p": row["p_used"],
            "screening_p_basis": row["p_basis"],
            "recommended_method": method,
            "purpose": purpose,
            "reason": reason,
            "status": status,
        })
        if len(plan) >= max_features:
            break
    return plan


def build_baseline(tools: Tools, table: str, count_metric: str,
                   filters: dict | None = None) -> dict:
    """总体低评分基准：样本量、低评分数、低评分率。"""
    res = tools.query_mart(
        table, metrics=["low_score_count", "low_score_rate", count_metric],
        filters=filters, limit=1,
    )
    if not res["ok"]:
        raise RuntimeError(f"build_baseline({table}) 失败: {res['error']}")
    row = res["rows"][0]
    return {
        "table": table,
        "sample": row[f"_m_{count_metric}"],
        "low_score_count": row["_m_low_score_count"],
        "low_score_rate": row["_m_low_score_rate"],
        "sql": res["sql"],
    }


def analyze_item_presence_significance(
    provider: DataProvider,
    dimension: str,
    min_group_sample: int,
    top_k: int = 10,
) -> dict:
    """订单是否包含某品类/商品 × 低评分；按订单去重并校正多重检验。"""
    if dimension not in {"category_name", "product_id"}:
        raise ValueError(f"不支持的商品项检验维度: {dimension}")
    where = "is_delivery_analysis_eligible = 1 AND has_review_record = 1"
    baseline_sql = (
        "SELECT COUNT(DISTINCT order_id) AS total_orders, "
        "COUNT(DISTINCT CASE WHEN is_low_score = 1 THEN order_id END) "
        "AS low_score_orders FROM mart_order_item_analysis "
        f"WHERE {where} LIMIT 1"
    )
    baseline_rows = provider.execute(baseline_sql)
    if not baseline_rows:
        return {"ok": False, "error": "商品项有效订单为空", "sqls": [baseline_sql]}
    total_orders = int(baseline_rows[0]["total_orders"])
    total_low = int(baseline_rows[0]["low_score_orders"])
    total_non_low = total_orders - total_low
    base_rate = total_low / total_orders if total_orders else 0.0

    group_sql = (
        f"SELECT {dimension} AS factor_value, "
        "COUNT(DISTINCT order_id) AS sample, "
        "COUNT(DISTINCT CASE WHEN is_low_score = 1 THEN order_id END) "
        "AS low_score_count FROM mart_order_item_analysis "
        f"WHERE {where} AND {dimension} IS NOT NULL "
        f"GROUP BY {dimension} "
        f"HAVING COUNT(DISTINCT order_id) >= {int(min_group_sample)} "
        "ORDER BY sample DESC LIMIT 10000"
    )
    rows = provider.execute(group_sql)
    tests: list[dict] = []
    for row in rows:
        sample = int(row["sample"])
        group_low = int(row["low_score_count"])
        group_non_low = sample - group_low
        rest_low = total_low - group_low
        rest_non_low = total_non_low - group_non_low
        if min(group_low, group_non_low, rest_low, rest_non_low) < 0:
            continue
        counts = pd.DataFrame([
            {"has_factor": 0, "is_low_score": 0, "n": rest_non_low},
            {"has_factor": 0, "is_low_score": 1, "n": rest_low},
            {"has_factor": 1, "is_low_score": 0, "n": group_non_low},
            {"has_factor": 1, "is_low_score": 1, "n": group_low},
        ])
        result = categorical_test_counts(counts, "has_factor", "is_low_score")
        if "error" in result:
            continue
        rate = group_low / sample if sample else 0.0
        tests.append({
            "dimension": dimension,
            "value": row["factor_value"],
            "sample": sample,
            "low_score_count": group_low,
            "low_score_rate": rate,
            "base_rate": base_rate,
            "lift": rate / base_rate if base_rate else None,
            "excess_low_score": round(sample * max(rate - base_rate, 0), 1),
            "method": ("Fisher精确检验" if result["method"] == "fisher"
                       else "Pearson卡方检验（Yates校正）"),
            "p": result["p"],
            "or": result["or"],
            "or_ci": result["or_ci"],
            "rr": result["rr"],
        })

    if tests:
        correction = multiple_correction([row["p"] for row in tests])
        for index, row in enumerate(tests):
            row["p_adjusted"] = correction["p_adjusted"][index]
            row["significant"] = bool(
                correction["significant"][index]
                and row["p_adjusted"] < 0.05
            )
            row["direction"] = (
                "风险高于其余订单" if row["low_score_rate"] > base_rate
                else "风险低于其余订单"
            )

    significant_risk = sorted(
        (row for row in tests
         if row.get("significant") and row["low_score_rate"] > base_rate),
        key=lambda row: (row["excess_low_score"], row["lift"] or 0),
        reverse=True,
    )[:top_k]
    significant_protective = sorted(
        (row for row in tests
         if row.get("significant") and row["low_score_rate"] <= base_rate),
        key=lambda row: row["p_adjusted"],
    )[:top_k]
    pending = sorted(
        (row for row in tests
         if not row.get("significant") and row["low_score_rate"] > base_rate),
        key=lambda row: (row["excess_low_score"], row["lift"] or 0),
        reverse=True,
    )[:top_k]
    return {
        "ok": True,
        "dimension": dimension,
        "method": (
            "逐个对象进行订单级2×2卡方/Fisher检验，"
            "并使用FDR-BH校正多重比较"
        ),
        "baseline": {"orders": total_orders, "low_score_rate": base_rate},
        "min_group_sample": min_group_sample,
        "tested_count": len(tests),
        "significant_risk": significant_risk,
        "significant_protective": significant_protective,
        "pending_validation": pending,
        "all_tests": sorted(tests, key=lambda row: row.get("p_adjusted", 1)),
        "sqls": [baseline_sql, group_sql],
        "grain_note": "每项检验先按 order_id 去重；同一订单是否包含该对象作为二分类因素。",
    }


def analyze_item_drilldown(provider: DataProvider, semantic: SemanticLayer,
                           top_k: int = 10) -> dict:
    """商品项表受控下钻；描述性结果和显著性检验均按订单去重。"""
    tools = Tools(provider, semantic)
    sqls: list[str] = []
    outputs: dict[str, list[dict]] = {}
    for dim in ("category_name", "product_id", "seller_id"):
        res = tools.query_mart(
            "mart_order_item_analysis",
            metrics=["distinct_orders", "low_score_orders", "low_score_rate",
                     "item_product_value", "item_freight_value"],
            dimensions=[dim], order_by="low_score_orders", limit=10000,
        )
        if not res["ok"]:
            outputs[dim] = []
            continue
        sqls.append(res["sql"])
        rows = [r for r in res["rows"] if r["_m_distinct_orders"] >= 10]
        outputs[dim] = sorted(
            rows,
            key=lambda r: (r["_m_low_score_orders"], r["_m_low_score_rate"]),
            reverse=True,
        )[:top_k]

    base_res = tools.query_mart(
        "mart_order_item_analysis",
        metrics=["distinct_orders", "low_score_rate"], limit=1,
    )
    item_order_count = int(base_res["rows"][0]["_m_distinct_orders"])
    sqls.append(base_res["sql"])
    category_min = max(20, math.ceil(item_order_count * 0.0005))
    product_min = max(10, math.ceil(item_order_count * 0.0002))
    category_significance = analyze_item_presence_significance(
        provider, "category_name", category_min, top_k=top_k,
    )
    product_significance = analyze_item_presence_significance(
        provider, "product_id", product_min, top_k=top_k,
    )
    sqls.extend(category_significance.get("sqls", []))
    sqls.extend(product_significance.get("sqls", []))
    return {
        "ok": True,
        "by_category": outputs["category_name"],
        "by_product": outputs["product_id"],
        "by_seller": outputs["seller_id"],
        "significance": {
            "category": category_significance,
            "product": product_significance,
        },
        "sqls": sqls,
        "grain_note": "评价指标和显著性检验均按 COUNT(DISTINCT order_id) 去重。",
    }


def screen_factors(tools: Tools, table: str, dimensions: list[str],
                   base_rate: float, count_metric: str,
                   min_sample: int, filters: dict | None = None,
                   sql_sink: list[str] | None = None) -> list[dict]:
    """对候选维度逐一扫描，返回过滤 min_sample 后的分组证据。"""
    groups: list[dict] = []
    for dim in dimensions:
        res = tools.query_mart(
            table, metrics=["low_score_count", "low_score_rate", count_metric],
            dimensions=[dim], filters=filters, limit=10000,
        )
        if not res["ok"]:
            continue
        if sql_sink is not None:
            sql_sink.append(res["sql"])
        for row in res["rows"]:
            n = row[f"_m_{count_metric}"]
            if n < min_sample:
                continue
            rate = row["_m_low_score_rate"]
            lift = rate / base_rate if base_rate else None
            excess = n * max(rate - base_rate, 0.0)
            groups.append({
                "dimension": dim,
                "value": row[dim],
                "sample": n,
                "low_score_count": row["_m_low_score_count"],
                "low_score_rate": rate,
                "base_rate": base_rate,
                "rate_diff": rate - base_rate,
                "lift": lift,
                "excess_low_score": round(excess, 1),
            })
    return groups


def rank_priorities(groups: list[dict], top_k: int = 15) -> list[dict]:
    """按综合分(规模×风险增幅)生成描述性排查顺序，非治理策略。"""
    for g in groups:
        g["priority_score"] = g["excess_low_score"] * max((g["lift"] or 0) - 1, 0)
    # 只排列高于基准且确有超额低评分的对象；保护性因素不进入问题清单。
    risk_groups = [g for g in groups if g["priority_score"] > 0]
    ranked = sorted(risk_groups, key=lambda g: g["priority_score"], reverse=True)[:top_k]
    n = len(ranked)
    for i, g in enumerate(ranked):
        if i < max(1, n // 5):          # top 20%
            g["priority"] = "P0"
        elif i < max(2, n // 2):        # 前 50%
            g["priority"] = "P1"
        else:
            g["priority"] = "P2"
    return ranked


def analyze_routes(provider: DataProvider, semantic: SemanticLayer,
                   top_k: int = 10) -> dict:
    """route 线路深挖（M2 边角）：Top 线路 / 集中度 / 线路×延迟交叉。

    基于卖家级表（is_multi_seller_order=0），复用 Tools 生成安全 SQL，结果可对账。
    """
    tools = Tools(provider, semantic)
    sqls: list[str] = []

    base = build_baseline(tools, SELLER_TABLE, SELLER_COUNT_METRIC,
                          filters=SELLER_FILTER)
    sqls.append(base["sql"])
    # route 高基数：动态相对阈值（随卖家样本量放大，样例/真库通用）
    min_sample = max(ROUTE_MIN_SAMPLE, int(base["sample"] * ROUTE_SAMPLE_FRACTION))

    # 1. Top 线路（带 P0/P1/P2 优先级）
    route_groups = screen_factors(
        tools, SELLER_TABLE, ["route"], base["low_score_rate"],
        SELLER_COUNT_METRIC, min_sample, filters=SELLER_FILTER, sql_sink=sqls)
    top_routes = rank_priorities(route_groups, top_k=top_k)

    # 2. 线路集中度：Top-N 线路低评分订单数占总低评分的比例
    total_low = base["low_score_count"]
    by_count = sorted(route_groups, key=lambda g: g["low_score_count"], reverse=True)[:5]
    top5_low = sum(g["low_score_count"] for g in by_count)
    concentration = {
        "top_routes_by_count": [
            {"route": g["value"], "sample": g["sample"],
             "low_score_count": g["low_score_count"]} for g in by_count
        ],
        "top5_low_score_count": top5_low,
        "total_low_score_count": total_low,
        "top5_share": round(top5_low / total_low, 4) if total_low else None,
    }

    # 3. 线路×延迟 交叉低评分率（识别"延迟又低评分"的线路）
    cross = tools.query_mart(
        SELLER_TABLE,
        metrics=["low_score_count", "low_score_rate", SELLER_COUNT_METRIC],
        dimensions=["route", "is_late_delivery"], filters=SELLER_FILTER, limit=10000)
    route_cross_delay = []
    if cross["ok"]:
        sqls.append(cross["sql"])
        cross_map: dict = {}
        for row in cross["rows"]:
            cross_map.setdefault(row["route"], {})[row["is_late_delivery"]] = {
                "sample": row[f"_m_{SELLER_COUNT_METRIC}"],
                "low_score_rate": row["_m_low_score_rate"],
            }
        for g in top_routes[:5]:
            by_delay = cross_map.get(g["value"], {})
            route_cross_delay.append({
                "route": g["value"],
                "late": by_delay.get(1),
                "not_late": by_delay.get(0),
            })

    return {
        "ok": True,
        "base": base,
        "top_routes": top_routes,
        "concentration": concentration,
        "route_cross_delay": route_cross_delay,
        "sqls": sqls,
    }


def run_attribution(provider: DataProvider, semantic: SemanticLayer,
                    include_logistic: bool = False,
                    question: str | None = None) -> dict:
    """三个有时间顺序的二分类目标：第一层筛查后自动进入调整后Logistic。

    include_logistic 参数仅为兼容旧调用；新流程始终运行受控的自动调整模型，
    且永不生成治理策略。
    """
    target_name = resolve_attribution_target(question)
    if target_name is None:
        return {
            "ok": False, "unsupported_target": True,
            "error": (
                "当前自动化关联因素分析支持“是否交接超期、是否最终延迟、"
                "是否低评分（review_score≤3）”三个目标；"
                "若需分析其他目标，可先进行双变量统计检验或指标查询。"
            ),
        }
    if target_name in TARGET_SPECS:
        return run_target_attribution(
            provider, target_name, min_group_sample=semantic.guards.get(
                "min_group_sample", 100
            )
        )
    tools = Tools(provider, semantic)
    min_sample = semantic.guards.get("min_group_sample", 100)
    sqls: list[str] = []

    # 1. 基准
    order_base = build_baseline(tools, ORDER_TABLE, ORDER_COUNT_METRIC)
    seller_base = build_baseline(
        tools, SELLER_TABLE, SELLER_COUNT_METRIC, filters=SELLER_FILTER)
    sqls.extend([order_base["sql"], seller_base["sql"]])

    # 2. 扫描
    order_groups = screen_factors(
        tools, ORDER_TABLE, ORDER_DIMENSIONS,
        order_base["low_score_rate"], ORDER_COUNT_METRIC, min_sample,
        sql_sink=sqls)
    seller_groups = screen_factors(
        tools, SELLER_TABLE, SELLER_DIMENSIONS,
        seller_base["low_score_rate"], SELLER_COUNT_METRIC, min_sample,
        filters=SELLER_FILTER, sql_sink=sqls)

    # 3. 排序
    priorities = rank_priorities(order_groups + seller_groups)

    # 4. route 线路深挖
    routes = analyze_routes(provider, semantic)
    sqls.extend(routes["sqls"])

    try:
        item_drilldown = analyze_item_drilldown(provider, semantic)
        sqls.extend(item_drilldown.get("sqls", []))
    except Exception as e:
        item_drilldown = {"ok": False, "error": f"商品项明细分析失败：{e}"}

    # 5. 低评分专用两层推断：显著性+CI筛查 → 共线性代表 → Logistic。
    try:
        inference = run_low_score_attribution(provider, min_group_sample=min_sample)
        sqls.extend(inference.get("sqls", []))
    except Exception as e:
        inference = {"ok": False, "error": f"低评分关联因素分析失败：{e}"}

    if not inference.get("ok"):
        # 推断（单变量筛选 + Logistic 调整）失败时，仍返回已算好的描述性归因结果，
        # 避免把 baseline / factors / priorities 一起丢弃导致前端整块归零。
        return {
            "ok": False,
            "schema_version": ATTRIBUTION_SCHEMA_VERSION,
            "target": "is_low_score",
            "target_label": "是否低评分",
            "target_short_label": "低评分",
            "target_positive_label": "低评分",
            "target_negative_label": "非低评分",
            "target_rate": order_base["low_score_rate"],
            "error": inference.get("error", "低评分关联因素分析未完成"),
            "baseline": {"order": order_base, "seller": seller_base},
            "factors": {"order": order_groups, "seller": seller_groups},
            "priorities": priorities,
            "routes": routes,
            "item_drilldown": item_drilldown,
            "feature_tests": [],
            "significant_features": [],
            "inconclusive_features": [],
            "not_significant_features": [],
            "deep_validation_plan": [],
            "selected_features": [],
            "adjusted_features": [],
            "adjusted_explanations": [],
            "adjusted_validation": {},
            "control_policy": {},
            "recommendations": {
                "ok": True, "status": "disabled_evidence_only",
                "recommendations": [],
                "note": "推断未完成，仅提供描述性归因结果，不生成治理策略。",
            },
            "caveats": list(CAVEATS),
            "sqls": sqls,
            "analysis_mode": "descriptive_fallback",
            "note": (
                "低评分推断（单变量筛选 + 多变量 Logistic）未完成；"
                "以下为描述性归因结果，仅表示统计关联，不作因果判断。"
            ),
        }

    screening = inference.get("screening", {})
    adjusted = inference.get("adjusted_validation", {})
    test_catalog = []
    for row in screening.get("tests", []):
        p_adjusted = row.get("p_adjusted")
        test_catalog.append({
            "ok": bool(row.get("ok")),
            "feature": row.get("feature"), "label": row.get("label"),
            "target": "是否低评分", "method": row.get("method", "未执行"),
            "p": row.get("p"), "p_adjusted": p_adjusted,
            "p_used": p_adjusted if p_adjusted is not None else row.get("p"),
            "p_basis": "FDR-BH校正后 p 值",
            "significant": bool(row.get("retained")),
            "effect_name": row.get("effect_name"),
            "effect_value": row.get("effect_value"),
            "ci95": row.get("ci95"), "ci_passed": row.get("ci_passed", False),
            "sample": row.get("sample"),
            "assumption_ok": row.get("assumption_ok"),
            "selected_for_logistic": row.get("selected_for_logistic", False),
            "selection_reason": row.get("selection_reason"),
            "error": row.get("error"),
            "lightweight_judgment": (
                "通过单变量筛选，进入共线性处理" if row.get("retained")
                else ((f"未执行：{row.get('error')}" if row.get("error")
                       else "因样本或检验前提不足未执行") if not row.get("ok")
                      else "未同时满足FDR显著性与95%置信区间标准")
            ),
        })
    significant_features = [row for row in test_catalog if row["significant"]]
    inconclusive_features = [
        row for row in test_catalog
        if row.get("assumption_ok") is False or row.get("p") is None
    ]
    not_significant_features = [
        row for row in test_catalog
        if not row["significant"] and row.get("assumption_ok") is not False
    ]
    selected_features = screening.get("selected", [])
    adjusted_features = adjusted.get("stable", [])
    deep_validation_plan = [{
        "feature": row.get("feature"), "label": row.get("label"),
        "screening_method": next(
            (test.get("method") for test in test_catalog
             if test.get("feature") == row.get("feature")), "单变量检验"
        ),
        "screening_p": row.get("p_adjusted"),
        "screening_p_basis": "FDR-BH校正后 p 值",
        "recommended_method": row.get("method"),
        "purpose": "确认控制预设变量后是否仍与低评分显著相关。",
        "reason": row.get("conclusion"),
        "status": "已自动完成调整后验证",
    } for row in adjusted.get("results", [])]

    order_model = next(
        (model for model in adjusted.get("models", [])
         if model.get("label", "").startswith("订单级")),
        {"ok": False, "error": "没有运行订单级模型"},
    )
    seller_model = next(
        (model for model in adjusted.get("models", [])
         if model.get("label", "").startswith("订单-卖家级")),
        {"ok": False, "error": "没有运行订单-卖家级模型"},
    )
    late_screen = next(
        (row for row in screening.get("tests", [])
         if row.get("feature") == "is_late_delivery"), {}
    )
    verification = {
        "ok": inference.get("ok", False), "mode": "automatic_adjusted",
        "single_tests": screening.get("tests", []),
        "logistic": {"enabled": True, "order": order_model, "seller": seller_model},
        "evidence": {
            "factor": "is_late_delivery", "grade": (
                "通过第一层" if late_screen.get("retained") else "未通过第一层"
            ),
            "p": late_screen.get("p_adjusted"),
            "or": late_screen.get("effect_value"),
            "or_ci": late_screen.get("ci95"),
        },
        "load_profile": {
            "strategy": "分类变量先汇总计数；多变量模型每次只读取当前分析数据表的必要字段",
            "extracts": adjusted.get("load_profile", []),
            "logistic_enabled": True,
        },
        "sqls": inference.get("sqls", []),
    }

    # 自动化分析只提供证据，不论模型结果如何均不生成治理策略。
    recommendations = {
        "ok": True, "status": "disabled_evidence_only",
        "recommendations": [],
        "note": (
            "本分析仅输出单变量筛选、多变量调整和分布明细，"
            "不自动生成治理策略。"
        ),
    }

    caveats = list(CAVEATS)
    caveats.extend(inference.get("caveats", []))
    row_counts = getattr(provider, "row_counts", {})
    if getattr(provider, "source_name", "") == SAMPLE_SOURCE_LABEL and row_counts:
        physical = {k: v for k, v in row_counts.items()
                    if k != "mart_order_item_analysis"}
        if physical and max(physical.values()) <= 1000:
            caveats.append(
                "当前使用的演示样本每张表最多约1,000行，只适合查看功能和分析流程；"
                "正式业务判断必须切换到完整业务数据库（MySQL）。"
            )

    return {
        "ok": True,
        "schema_version": ATTRIBUTION_SCHEMA_VERSION,
        "target": "is_low_score",
        "target_label": "是否低评分",
        "target_short_label": "低评分",
        "target_positive_label": "低评分",
        "target_negative_label": "非低评分",
        "target_rate": order_base["low_score_rate"],
        "target_baseline": {
            "table": ORDER_TABLE,
            "sample": order_base.get("sample"),
            "target_count": order_base.get("low_score_count"),
            "target_rate": order_base["low_score_rate"],
            "sql": order_base.get("sql"),
        },
        "baseline": {"order": order_base, "seller": seller_base},
        "factors": {"order": order_groups, "seller": seller_groups},
        "priorities": priorities,
        "routes": routes,
        "item_drilldown": item_drilldown,
        "verification": verification,
        "feature_tests": test_catalog,
        "significant_features": significant_features,
        "inconclusive_features": inconclusive_features,
        "not_significant_features": not_significant_features,
        "deep_validation_plan": deep_validation_plan,
        "selected_features": selected_features,
        "adjusted_features": adjusted_features,
        "adjusted_explanations": inference.get("explanations", []),
        "adjusted_validation": adjusted,
        "control_policy": adjusted.get("control_policy", {}),
        "recommendations": recommendations,
        "caveats": caveats,
        "sqls": sqls,
        "analysis_mode": "automatic_adjusted_attribution",
        "note": (
            "本次以是否低评分为目标：先按FDR与95%置信区间完成"
            "单变量筛选，再运行预设的多变量Logistic模型；仅对控制其他因素后仍显著"
            "的变量展示分布与对象明细，不作因果判断或治理建议。"
        ),
    }
