"""L2 描述性归因（M2）：固定顺序确定性流程。

覆盖订单级 + 卖家/线路级，输出带 P0/P1/P2 的问题对象清单
（样本量 / 低评分率 / 基准率 / 率差 / Lift / 超额低评分 / 规模×风险排序）。

边界：只做描述性归因，不下因果结论，不生成改善建议（建议 = M4）。
所有分组基于语义字典口径与 min_group_sample 过滤，结果附 SQL 可对账。
"""
from __future__ import annotations

from .data_provider import DataProvider
from .recommendation import recommend_actions
from .semantic import SemanticLayer
from .statistics import verify_factors
from .tools import Tools

ORDER_TABLE = "mart_order_delivery"
SELLER_TABLE = "mart_order_seller_delivery"
ORDER_COUNT_METRIC = "order_count"
SELLER_COUNT_METRIC = "record_count"

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
    "本结果为观察性数据的描述性归因，可能受混杂因素影响，不代表因果关系。",
    "高 Lift 但样本不足的组已被 min_group_sample 过滤；需结合问题规模(超额低评分)判断。",
    "当前 mart 表不含评价正文，未覆盖商品破损、错发、客服等文本原因。",
    "P0/P1/P2 为按规模×风险的描述性优先级，正式统计验证与建议生成在后续阶段。",
]


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
    """按综合分(规模×风险增幅)排序，分位数划分 P0/P1/P2，取 Top-K。"""
    for g in groups:
        g["priority_score"] = g["excess_low_score"] * max((g["lift"] or 0) - 1, 0)
    ranked = sorted(groups, key=lambda g: g["priority_score"], reverse=True)[:top_k]
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


def run_attribution(provider: DataProvider, semantic: SemanticLayer) -> dict:
    """固定顺序归因流程：baseline → 扫描(订单级/卖家级) → 排序 → 输出。"""
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

    # 5. 统计验证（M3：批量单变量 + 双 Logistic + 证据分级，自动并入）
    try:
        verification = verify_factors(provider, min_group_sample=min_sample)
    except Exception as e:  # 统计验证失败不应阻断描述性归因
        verification = {"ok": False, "error": f"统计验证失败: {e}"}

    # 6. 建议生成（M4：基于已验证证据匹配规则库）
    try:
        recommendations = recommend_actions(provider, semantic, attribution_res={
            "priorities": priorities, "routes": routes,
            "verification": verification,
        })
    except Exception as e:  # 建议生成失败不应阻断归因
        recommendations = {"ok": False, "error": f"建议生成失败: {e}",
                           "recommendations": []}

    return {
        "ok": True,
        "baseline": {"order": order_base, "seller": seller_base},
        "factors": {"order": order_groups, "seller": seller_groups},
        "priorities": priorities,
        "routes": routes,
        "verification": verification,
        "recommendations": recommendations,
        "caveats": CAVEATS,
        "sqls": sqls,
        "note": "描述性归因 + 统计验证 + 建议：观察数据、禁因果。",
    }
