"""常见自然语言取数的确定性规划与执行。

指标和维度仍由语义字典校验。可确定解析的问题不依赖大模型；解析不完整时返回
recognized=False，由页面决定是否回退到DeepSeek。
"""
from __future__ import annotations

import re

from .data_provider import DataProvider
from .semantic import SemanticLayer
from .tools import Tools

METRIC_ALIASES = {
    "order_count": ("总体订单量", "订单量", "订单数", "有效订单"),
    "reviewed_orders": ("有评价订单数", "评价订单数"),
    "low_score_rate": (
        "低评分率", "低分率", "低评分占比", "差评占比", "三星及以下",
        "容易给低分", "容易给三星",
    ),
    "strict_negative_rate": ("严格负面率", "一二星占比", "两星及以下"),
    "late_rate": ("延迟率", "延迟交付比例", "晚到比例", "容易晚到"),
    "on_time_rate": ("按时交付率", "准时率", "按期率"),
    "avg_late_days": ("平均延迟天数", "平均晚到天数"),
    "avg_review_score": ("平均评分", "平均评价得分", "平均评价分数"),
    "gmv": ("GMV", "成交商品金额", "商品成交额"),
    "freight_total": ("运费总额", "总运费", "运费一共"),
    "payment_total": ("支付总金额", "支付金额", "支付额"),
    "avg_approval_days": ("平均支付审批时长", "支付审批平均", "审批平均"),
    "avg_fulfillment_days": ("平均总履约时长", "平均履约时长", "履约平均"),
}

DIMENSION_ALIASES = {
    "order_month": ("按月份", "按月", "每月", "各月", "月份走势", "月度趋势", "月度", "每月趋势", "每个月"),
    "order_year": ("按年份", "按年", "各年", "每年", "年份", "年度", "各年份", "历年"),
    "primary_category_name": ("按品类", "各品类", "不同品类", "品类和", "商品品类", "品类"),
    "primary_payment_type": ("按支付方式", "各支付方式", "不同支付方式", "支付方式交叉"),
    "delay_bucket": ("延迟档位", "延迟分档", "各延迟档"),
    "is_late_delivery": (
        "延迟与非延迟", "延迟和非延迟", "未延迟订单", "按时和延迟",
    ),
    "route": ("按线路", "各线路", "哪些线路", "哪条线路", "条线路", "州际线路", "线路"),
    "seller_state": ("按卖家州", "各卖家州", "卖家地区"),
    "cross_state": ("跨州和同州", "跨州与同州", "跨省和省内", "是否跨州"),
    "customer_state": ("按客户州", "各客户州", "收货地区", "客户地区"),
}

# 出现这些词说明用户期望分组/对比/趋势，但未识别出维度时视为“解析不完整”
DIMENSION_HINTS = (
    "各", "对比", "按", "趋势", "分布", "不同", "分别", "分类", "哪些", "排行",
    "相比", "较", "同比", "环比", "变化", "去年", "今年", "上年", "近一年",
)

METRIC_LABELS = {
    "order_count": "订单量",
    "record_count": "订单-卖家记录数",
    "reviewed_orders": "有评价订单数",
    "low_score_rate": "低评分率",
    "strict_negative_rate": "严格负面率",
    "late_rate": "延迟率",
    "on_time_rate": "按时交付率",
    "avg_late_days": "平均延迟天数",
    "avg_review_score": "平均评分",
    "gmv": "成交商品金额",
    "freight_total": "运费总额",
    "payment_total": "支付总金额",
    "avg_approval_days": "平均支付审批时长（天）",
    "avg_fulfillment_days": "平均总履约时长（天）",
}

DIMENSION_LABELS = {
    "order_month": "月份",
    "order_year": "年份",
    "primary_category_name": "品类",
    "primary_payment_type": "支付方式",
    "delay_bucket": "延迟分档",
    "is_late_delivery": "是否延迟",
    "route": "线路",
    "seller_state": "卖家州",
    "cross_state": "是否跨州",
    "customer_state": "客户州",
}

RATE_METRICS = {
    "low_score_rate", "strict_negative_rate", "late_rate", "on_time_rate",
}
COUNT_METRICS = {"order_count", "record_count", "reviewed_orders"}
AMOUNT_METRICS = {"gmv", "freight_total", "payment_total"}
SELLER_DIMENSIONS = {"route", "seller_state", "cross_state"}


def _mentioned(question: str, aliases: tuple[str, ...]) -> bool:
    lower = question.lower()
    return any(alias.lower() in lower for alias in aliases)


def plan_query_question(question: str, semantic: SemanticLayer) -> dict:
    """提取全部明确出现的指标和维度。"""
    metrics = [
        metric for metric, aliases in METRIC_ALIASES.items()
        if _mentioned(question, aliases)
    ]
    dimensions = [
        dimension for dimension, aliases in DIMENSION_ALIASES.items()
        if _mentioned(question, aliases)
    ]
    # 常见口语补充。
    if not metrics and any(word in question for word in ("低评分风险", "低评分表现")):
        metrics.append("low_score_rate")
    if not metrics and "评分" in question and "平均" in question:
        metrics.append("avg_review_score")
    if not metrics:
        return {"ok": False, "recognized": False, "error": "未识别明确指标"}

    table = (
        "mart_order_seller_delivery"
        if SELLER_DIMENSIONS.intersection(dimensions)
        else "mart_order_delivery"
    )
    if table == "mart_order_seller_delivery" and "order_count" in metrics:
        metrics = ["record_count" if metric == "order_count" else metric for metric in metrics]

    available_metrics = semantic.get_metrics(table)
    if any(metric not in available_metrics for metric in metrics):
        return {
            "ok": False, "recognized": False,
            "error": f"{table}不支持本次指标组合",
        }
    if any(not semantic.check_dimension(table, dimension) for dimension in dimensions):
        return {"ok": False, "recognized": False, "error": "表与维度组合不受支持"}

    rank_match = re.search(
        r"(?:top\s*|前|最高的?|最多的?|风险最高的?)\s*(\d+)",
        question, flags=re.IGNORECASE,
    )
    ranking = bool(rank_match or any(word in question.lower() for word in ("top", "最高", "最多")))
    limit = int(rank_match.group(1)) if rank_match else (10 if ranking else 100)
    limit = min(max(limit, 1), 10000)

    # 条件筛选：识别“延迟 X 天以上/以内”“运费 X 块以上”等数值范围 → WHERE
    filters: dict = {}

    # 延迟天数
    delay_ge = re.search(
        r"延迟\s*(\d+)\s*天?\s*(?:以上|及以上|超过|大于|至少|超)|(?:>=|≥)\s*(\d+)\s*天",
        question)
    delay_le = re.search(
        r"延迟\s*(\d+)\s*天?\s*(?:以内|之内|不超过|小于|最多)|(?:<=|≤)\s*(\d+)\s*天",
        question)
    if delay_ge:
        filters["late_days"] = {"op": ">=", "value": int(delay_ge.group(1) or delay_ge.group(2))}
    elif delay_le:
        filters["late_days"] = {"op": "<=", "value": int(delay_le.group(1) or delay_le.group(2))}
    # 金额类（运费/金额/价格），支持“运费 X 块以上”和“运费超过 X 元”两种语序
    else:
        money_ge = re.search(
            r"(?:运费|金额|价格|支付额|货款)\s*(?:超过|大于|至少|以上|高于|不低于|>=|≥)\s*(\d+\.?\d*)\s*(?:块|元)?"
            r"|(?:运费|金额|价格|支付额|货款)\s*(\d+\.?\d*)\s*(?:块|元)?\s*(?:以上|及以上|超过|大于|至少|高于|不低于)",
            question)
        money_le = re.search(
            r"(?:运费|金额|价格|支付额|货款)\s*(?:不超过|小于|低于|以内|不大于|<=|≤)\s*(\d+\.?\d*)\s*(?:块|元)?"
            r"|(?:运费|金额|价格|支付额|货款)\s*(\d+\.?\d*)\s*(?:块|元)?\s*(?:以内|之内|不超过|小于|低于|最多)",
            question)
        if money_ge:
            v = money_ge.group(1) or money_ge.group(2)
            filters["freight_total"] = {"op": ">=", "value": float(v)}
        elif money_le:
            v = money_le.group(1) or money_le.group(2)
            filters["freight_total"] = {"op": "<=", "value": float(v)}

    # 期间对比：识别“2020 年相比 2019 年”这类具体年份对比 → 按年份分组并计算差值
    compare = None
    years_mentioned = re.findall(r"((?:19|20)\d{2})\s*年", question)
    if len(years_mentioned) >= 2 and any(
        w in question for w in ("相比", "对比", "较", "同比", "环比", "变化", "涨", "跌", "提高", "降低")
    ):
        years: list[int] = []
        for y in years_mentioned:
            if int(y) not in years:
                years.append(int(y))
        if len(years) >= 2:
            compare = {"years": sorted(years[:2])}
            dimensions = ["order_year"]

    order_by = None
    if ranking and dimensions:
        if "低评分" in question or "低分" in question or "风险" in question:
            order_by = "low_score_rate" if "low_score_rate" in metrics else metrics[0]
        elif "延迟" in question or "晚到" in question:
            order_by = "late_rate" if "late_rate" in metrics else metrics[0]
        else:
            order_by = metrics[0]
    # 完整性检测：用户明显要求分组/对比/趋势但未识别出维度 → 解析不完整（由上层回退 LLM）
    incomplete = bool(
        metrics and not dimensions
        and any(h in question for h in DIMENSION_HINTS)
    )
    return {
        "ok": True,
        "recognized": True,
        "incomplete": incomplete,
        "table": table,
        "metrics": list(dict.fromkeys(metrics)),
        "dimensions": list(dict.fromkeys(dimensions)),
        "filters": filters,
        "order_by": order_by,
        "limit": limit,
        "compare": compare,
    }


def _display_dimension(dimension: str, value) -> str:
    if dimension == "is_late_delivery":
        return "延迟" if int(value) == 1 else "未延迟"
    if dimension == "cross_state":
        return "跨州" if int(value) == 1 else "同州"
    return str(value)


def _display_metric(metric: str, value) -> str:
    if value is None:
        return "—"
    if metric in RATE_METRICS:
        return f"{float(value):.2%}"
    if metric in COUNT_METRICS:
        return f"{int(value):,}"
    if metric in AMOUNT_METRICS:
        return f"{float(value):,.2f}"
    return f"{float(value):.2f}"


def analyze_query_question(provider: DataProvider, semantic: SemanticLayer,
                           question: str) -> dict:
    plan = plan_query_question(question, semantic)
    if not plan.get("ok"):
        return plan
    result = Tools(provider, semantic).query_mart(
        table=plan["table"],
        metrics=plan["metrics"],
        dimensions=plan["dimensions"],
        filters=plan.get("filters"),
        order_by=plan["order_by"],
        limit=plan["limit"],
    )
    if not result.get("ok"):
        return {**plan, **result}
    display_rows = []
    for raw in result["rows"]:
        row = {
            DIMENSION_LABELS[dimension]: _display_dimension(dimension, raw[dimension])
            for dimension in plan["dimensions"]
        }
        row.update({
            METRIC_LABELS[metric]: _display_metric(metric, raw[f"_m_{metric}"])
            for metric in plan["metrics"]
        })
        display_rows.append(row)

    # 期间对比：筛出目标两年，计算差值并生成自然语言回答
    compare = plan.get("compare")
    if compare and compare.get("years") and plan["dimensions"] == ["order_year"]:
        target = {int(y) for y in compare["years"]}
        by_year = {
            int(raw.get("order_year")): raw for raw in result["rows"]
            if raw.get("order_year") is not None
        }
        present = sorted(target & by_year.keys())
        if len(present) == 2:
            metric = plan["metrics"][0]
            y_lo, y_hi = present
            v_lo = by_year[y_lo].get(f"_m_{metric}")
            v_hi = by_year[y_hi].get(f"_m_{metric}")
            if v_lo is not None and v_hi is not None:
                diff = v_hi - v_lo
                delta_label = METRIC_LABELS.get(metric, metric)
                compare_info = {
                    "metric": metric,
                    "metric_label": delta_label,
                    "years": present,
                    "values": {y_lo: v_lo, y_hi: v_hi},
                    "diff": diff,
                }
                if metric in RATE_METRICS:
                    diff_text = f"{diff * 100:+.2f} 个百分点"
                    v_text = lambda y: f"{by_year[y][f'_m_{metric}'] * 100:.2f}%"
                else:
                    diff_text = f"{diff:+,.2f}"
                    v_text = lambda y: f"{by_year[y][f'_m_{metric}']:,.2f}"
                answer = (
                    f"{y_lo} 年{delta_label} {v_text(y_lo)}，"
                    f"{y_hi} 年{delta_label} {v_text(y_hi)}，"
                    f"变化 {diff_text}。"
                )
                display_rows = [
                    {DIMENSION_LABELS["order_year"]: str(y),
                     delta_label: _display_metric(metric, by_year[y][f"_m_{metric}"])}
                    for y in present
                ]
                return {
                    "ok": True,
                    "recognized": True,
                    "incomplete": False,
                    **plan,
                    "rows": [by_year[y] for y in present],
                    "display_rows": display_rows,
                    "row_count": len(present),
                    "compare": compare_info,
                    "sql": result["sql"],
                    "answer": answer,
                    "execution_mode": "deterministic_period_compare",
                }
        # 目标年份在数据中缺失（如问 2020/2019，但数据只到 2018）→ 明确提示，不静默落回
        missing = sorted(target - by_year.keys())
        available = sorted(by_year.keys())
        if available:
            missing_text = "、".join(f"{y} 年" for y in missing)
            answer = (
                f"数据中缺少 {missing_text} 的数据，无法进行对比；"
                f"当前数据覆盖的年份：{'、'.join(f'{y} 年' for y in available)}。"
            )
        else:
            answer = f"数据中没有年份信息，无法进行期间对比。"
        return {
            "ok": True,
            "recognized": True,
            "incomplete": False,
            **plan,
            "rows": [],
            "display_rows": [],
            "row_count": 0,
            "sql": result["sql"],
            "answer": answer,
            "execution_mode": "deterministic_period_compare",
        }

    if not plan["dimensions"] and display_rows:
        answer = "；".join(f"{key}：{value}" for key, value in display_rows[0].items())
    else:
        answer = f"已返回{len(display_rows)}组结果，详见页面表格。"
    return {
        "ok": True,
        "recognized": True,
        **plan,
        "rows": result["rows"],
        "display_rows": display_rows,
        "row_count": result["row_count"],
        "sql": result["sql"],
        "answer": answer,
        "execution_mode": "deterministic_query",
    }
