"""Olist 业务数据分析助手的 Streamlit 界面。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_VERSION = "v1.1.0"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import altair as alt
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# 加载项目根 .env（DEEPSEEK_API_KEY / DB 等）
load_dotenv(ROOT / ".env")

from agent_core.attribution import ATTRIBUTION_SCHEMA_VERSION, run_attribution
from agent_core.data_provider import (
    DATABASE_SOURCE_LABEL, SAMPLE_SOURCE_LABEL, ProjectCsvProvider,
)
from agent_core.deep_validation import analyze_deep_validation
from agent_core.intent import Intent
from agent_core.query_analysis import analyze_query_question
from agent_core.semantic import SemanticLayer
from agent_core.statistical_analysis import (
    analyze_statistical_question,
    format_statistical_result,
    supported_variables,
)

FEATURE_SHORT_LABELS = {
    "is_late_delivery": "是否延迟",
    "delay_bucket": "延迟分档",
    "late_days": "延迟天数",
    "fulfillment_days": "总履约时长",
    "approval_days": "支付审批时长",
    "customer_state": "客户州",
    "seller_state": "卖家州",
    "route": "卖家州→客户州线路",
    "cross_state": "是否跨州",
    "primary_category_name": "主要品类",
    "primary_payment_type": "支付方式",
    "price_total": "商品金额",
    "order_month": "购买月份",
}

FIT_METHOD_LABELS = {
    "Logit-Newton": "标准二项逻辑回归",
    "GLM-Binomial回退": "二项广义线性模型（自动回退）",
    "线路专项模型＋按时间划分的独立验证集": "线路专项模型＋按时间划分的独立验证集",
}

TABLE_DISPLAY_NAMES = {
    "mart_order_delivery": "订单级分析宽表",
    "mart_order_seller_delivery": "订单-卖家级分析宽表",
    "mart_order_item_delivery": "商品项级分析宽表",
    "mart_order_item_analysis": "商品项级分析宽表",
}


def display_fit_method(value) -> str:
    return FIT_METHOD_LABELS.get(value, value or "—")

# =====================================================================
# 纯函数（供展示与测试复用，不依赖 streamlit）
# =====================================================================

def format_p_value(value) -> str:
    """避免极小p值因浮点下溢被展示成0。"""
    if not isinstance(value, (int, float)) or pd.isna(value):
        return "—"
    if value == 0:
        return "<1e-300"
    if value < 0.0001:
        return f"{value:.3e}"
    return f"{value:.4g}"

def build_priority_df(res: dict) -> pd.DataFrame:
    """归因优先级 → DataFrame（P0/P1/P2 表格）。"""
    rows = []
    for g in res.get("priorities", []):
        rows.append({
            "排查优先级（P0最高）": g.get("priority"),
            "维度": g.get("dimension"),
            "对象": str(g.get("value")),
            "样本量": g.get("sample"),
            "低评分率": f"{g.get('low_score_rate', 0):.1%}",
            "相对总体倍数（Lift）": round(g.get("lift") or 0, 2),
            "高于总体水平的预计低评分数": g.get("excess_low_score"),
        })
    return pd.DataFrame(rows)


def build_route_summary(res: dict) -> list[str]:
    """route 深挖摘要（文本行）。"""
    rt = res.get("routes", {})
    lines = []
    for g in rt.get("top_routes", [])[:5]:
        lines.append(f"{g.get('priority')} 线路 {g.get('value')}："
                     f"低评分率{g.get('low_score_rate', 0):.1%}，"
                     f"为总体的{g.get('lift') or 0:.2f}倍，"
                     f"预计多出{g.get('excess_low_score', 0):.0f}个低评分")
    conc = rt.get("concentration", {})
    if conc.get("top5_share") is not None:
        lines.append(f"Top5 线路集中度：{conc['top5_share']:.1%} "
                     f"({conc['top5_low_score_count']}/{conc['total_low_score_count']})")
    return lines


def build_verification_summary(res: dict) -> list[str]:
    """总结单变量筛选、共线性处理和多变量调整结论。"""
    lines = []
    first_layer = res.get("significant_features", [])
    selected = res.get("selected_features", [])
    adjusted = res.get("adjusted_features", [])
    first_labels = [row.get("label", row.get("feature")) for row in first_layer]
    selected_labels = [row.get("label", row.get("feature")) for row in selected]
    adjusted_labels = [row.get("label", row.get("feature")) for row in adjusted]
    lines.append(
        "单变量检验同时满足多重检验校正和95%置信区间标准："
        + ("、".join(dict.fromkeys(first_labels)) if first_labels else "无")
        + "。"
    )
    lines.append(
        "共线性处理后进入多变量模型："
        + ("、".join(dict.fromkeys(selected_labels)) if selected_labels else "无")
        + "。"
    )
    lines.append(
        "控制其他影响因素后仍存在显著关联："
        + ("、".join(dict.fromkeys(adjusted_labels)) if adjusted_labels else "无")
        + "。"
    )
    inconclusive = res.get("inconclusive_features", [])
    if inconclusive:
        lines.append(
            "暂不能判断（满足最小样本量的有效分组不足）："
            + "、".join(dict.fromkeys(row.get("label") for row in inconclusive))
        )
    plan_features = {
        row.get("feature") for row in res.get("deep_validation_plan", [])
    }
    no_priority = [
        row.get("label") for row in res.get("not_significant_features", [])
        if row.get("feature") not in plan_features
    ]
    if no_priority:
        lines.append(
            "当前未发现显著关联，也没有足够的描述性异常支持优先追加验证："
            + "、".join(dict.fromkeys(no_priority))
        )
    return lines


def build_feature_test_df(res: dict) -> pd.DataFrame:
    """每个特征采用的方法、p值与效应量。"""
    rows = []
    for test in res.get("feature_tests", []):
        p = test.get("p")
        p_adjusted = test.get("p_adjusted")
        effect = test.get("effect_value")
        rows.append({
            "特征": test.get("label"),
            "检验目标": test.get("target"),
            "检验方法": test.get("method"),
            "原始 p 值": format_p_value(p),
            "多重检验校正后 p 值（FDR）": format_p_value(p_adjusted),
            "是否显著": (
                "检验前提不足" if test.get("assumption_ok") is False
                else ("是" if test.get("significant") else "否")
            ),
            "效应量": (
                f"{test.get('effect_name')}={effect:.4g}"
                if test.get("effect_name") and isinstance(effect, (int, float))
                else "—"
            ),
            "效应量的95%置信区间": (
                f"[{test['ci95'][0]:.4g}, {test['ci95'][1]:.4g}]"
                if test.get("ci95") else "—"
            ),
            "置信区间是否排除无效值": "是" if test.get("ci_passed") else "否",
            "进入多变量模型": (
                "是" if test.get("selected_for_logistic") else "否"
            ),
            "选择说明": test.get("selection_reason") or "—",
            "样本量": test.get("sample"),
            "初步判断": test.get("lightweight_judgment"),
        })
    return pd.DataFrame(rows)


def build_adjusted_attribution_df(res: dict) -> pd.DataFrame:
    """自动Logistic后的变量级结论。"""
    rows = []
    for result in res.get("adjusted_validation", {}).get("results", []):
        if not result.get("ok"):
            rows.append({
                "变量": result.get("label"), "调整模型": result.get("model", "—"),
                "模型估计方式": display_fit_method(result.get("fit_method")),
                "控制其他因素后的效应": "—", "95%置信区间": "—",
                "多重检验校正后 p 值（FDR）": "—",
                "控制其他因素后仍显著": "否",
                "结论": "未能估计：" + result.get("error", "未知原因"),
            })
            continue
        ci = result.get("ci95")
        rows.append({
            "变量": result.get("label"), "调整模型": result.get("model"),
            "模型估计方式": display_fit_method(result.get("fit_method")),
            "控制其他因素后的效应": (
                f"优势比（OR）={result['adjusted_or']:.3f}"
                if isinstance(result.get("adjusted_or"), (int, float))
                else result.get("effect", "分类变量联合检验")
            ),
            "95%置信区间": f"[{ci[0]:.3f}, {ci[1]:.3f}]" if ci else "分类对象明细见下方",
            "多重检验校正后 p 值（FDR）": format_p_value(result.get("p_adjusted")),
            "控制其他因素后仍显著": "是" if result.get("stable") else "否",
            "结论": result.get("conclusion"),
        })
    return pd.DataFrame(rows)


def build_attribution_route_df(explanation: dict) -> pd.DataFrame:
    """仅展示同时通过调整模型和跨时间验证的线路。"""
    rows = []
    for route in explanation.get("route_validation") or []:
        adjusted_ci = route.get("adjusted_ci95")
        holdout_ci = route.get("holdout_ci95")
        rows.append({
            "线路": route.get("route"),
            "较早时期建模订单数": route.get("train_n"),
            "较早时期低评分率": (
                f"{route['train_low_score_rate']:.1%}"
                if isinstance(route.get("train_low_score_rate"), (int, float)) else "—"
            ),
            "控制其他因素后的优势比（OR）": (
                f"{route['adjusted_or']:.3f}"
                if isinstance(route.get("adjusted_or"), (int, float)) else "—"
            ),
            "优势比的95%置信区间": (
                f"[{adjusted_ci[0]:.3f}, {adjusted_ci[1]:.3f}]"
                if adjusted_ci else "—"
            ),
            "多重检验校正后 p 值（FDR）": format_p_value(route.get("adjusted_p_fdr")),
            "较晚时期验证订单数": route.get("holdout_n"),
            "较晚时期低评分率": (
                f"{route['holdout_low_score_rate']:.1%}"
                if isinstance(route.get("holdout_low_score_rate"), (int, float)) else "—"
            ),
            "较晚时期优势比（OR）": (
                f"{route['holdout_or']:.3f}"
                if isinstance(route.get("holdout_or"), (int, float)) else "—"
            ),
            "较晚时期95%置信区间": (
                f"[{holdout_ci[0]:.3f}, {holdout_ci[1]:.3f}]"
                if holdout_ci else "—"
            ),
            "稳定性": route.get("stability"),
        })
    return pd.DataFrame(rows)


def build_delay_distribution_df(explanation: dict) -> pd.DataFrame:
    """把后端延迟分层结果整理为可视化字段。"""
    visual = explanation.get("delay_visualization") or {}
    rows = []
    binary_labels = {
        "cross_state": {"0": "同州", "1": "跨州"},
        "is_multi_seller_order": {"0": "单卖家订单", "1": "多卖家订单"},
        "is_any_item_handover_late": {"0": "无交接超期", "1": "存在交接超期"},
    }
    value_labels = binary_labels.get(explanation.get("feature"), {})
    for row in visual.get("rows") or []:
        raw_group = str(row.get("group"))
        rows.append({
            "分组": value_labels.get(raw_group, raw_group),
            "是否延迟": row.get("delay_status"),
            "样本量": row.get("sample"),
            "组内占比": row.get("within_delay_share"),
            "低评分率": row.get("low_score_rate"),
        })
    return pd.DataFrame(rows)


def render_delay_distribution_chart(explanation: dict) -> None:
    """按是否延迟分层展示调整后仍显著变量的分布；是否延迟本身展示低评分率。"""
    visual = explanation.get("delay_visualization") or {}
    if not visual.get("ok"):
        if visual.get("error"):
            st.caption("延迟分层图未生成：" + visual["error"])
        return
    frame = build_delay_distribution_df(explanation)
    if frame.empty:
        return
    st.markdown("**按是否延迟分层的可视化**")
    chart_type = visual.get("chart_type")
    if chart_type == "delay_outcome":
        metric = "低评分率"
        title = "延迟与未延迟订单的低评分率"
        order = ["未延迟", "延迟"]
        chart = alt.Chart(frame).mark_bar(size=44).encode(
            x=alt.X("分组:N", sort=order, title=None),
            y=alt.Y(f"{metric}:Q", axis=alt.Axis(format=".0%"), title=metric),
            color=alt.Color(
                "是否延迟:N", sort=order,
                scale=alt.Scale(domain=order, range=["#4C78A8", "#E45756"]),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("分组:N"), alt.Tooltip("样本量:Q", format=","),
                alt.Tooltip("低评分率:Q", format=".1%"),
            ],
        ).properties(height=300, title=title)
    else:
        metric = "组内占比"
        raw_order = [str(value) for value in visual.get("group_order", [])]
        value_map = {
            str(row.get("group")): display
            for row, display in zip(visual.get("rows") or [], frame["分组"])
        }
        order = [value_map.get(value, value) for value in raw_order]
        chart = alt.Chart(frame).mark_bar().encode(
            x=alt.X("分组:N", sort=order or None, title=explanation.get("label")),
            xOffset=alt.XOffset("是否延迟:N", sort=["未延迟", "延迟"]),
            y=alt.Y(f"{metric}:Q", axis=alt.Axis(format=".0%"), title="各延迟组内部占比"),
            color=alt.Color(
                "是否延迟:N", sort=["未延迟", "延迟"],
                scale=alt.Scale(
                    domain=["未延迟", "延迟"], range=["#4C78A8", "#E45756"]
                ),
            ),
            tooltip=[
                alt.Tooltip("分组:N"), alt.Tooltip("是否延迟:N"),
                alt.Tooltip("样本量:Q", format=","),
                alt.Tooltip("组内占比:Q", format=".1%"),
            ],
        ).properties(height=320)
    st.altair_chart(chart, use_container_width=True)
    st.caption(visual.get("note", "柱高按延迟与未延迟组分别计算。"))


def build_explanation_group_df(explanation: dict) -> pd.DataFrame:
    """分类/二分类/有序变量的直观分组结果。"""
    details = explanation.get("details")
    if not isinstance(details, list):
        return pd.DataFrame()
    binary_labels = {
        "is_late_delivery": {0: "未延迟", 1: "延迟"},
        "cross_state": {0: "同州", 1: "跨州"},
        "is_multi_seller_order": {0: "单卖家订单", 1: "多卖家订单"},
        "is_any_item_handover_late": {0: "无交接超期", 1: "存在交接超期"},
    }
    value_labels = binary_labels.get(explanation.get("feature"), {})
    rows = []
    for row in details:
        ci = row.get("ci95")
        raw_value = row.get("value")
        try:
            normalized_value = int(raw_value)
        except (TypeError, ValueError):
            normalized_value = raw_value
        rows.append({
            "对象/分组": value_labels.get(normalized_value, raw_value),
            "样本量": row.get("sample"),
            "低评分数": row.get("low_score_count"),
            "低评分率": (
                f"{row['low_score_rate']:.1%}"
                if isinstance(row.get("low_score_rate"), (int, float)) else "—"
            ),
            "高于总体": (
                f"{row['rate_difference']:+.1%}"
                if isinstance(row.get("rate_difference"), (int, float)) else "—"
            ),
            "相对总体倍数（Lift）": round(row.get("lift"), 3) if isinstance(row.get("lift"), (int, float)) else "—",
            "高于总体水平的预计低评分数": round(row.get("excess_low_score"), 1)
            if isinstance(row.get("excess_low_score"), (int, float)) else "—",
            "对象优势比（OR）": round(row.get("or"), 3) if isinstance(row.get("or"), (int, float)) else "—",
            "对象OR的95%置信区间": f"[{ci[0]:.3f}, {ci[1]:.3f}]" if ci else "—",
            "对象校正后 p 值（FDR）": format_p_value(row.get("p_adjusted")),
            "是否为显著高风险对象": "是" if row.get("significant_risk") else "否",
        })
    return pd.DataFrame(rows)


def build_compact_group_df(explanation: dict, max_rows: int = 20) -> pd.DataFrame:
    """高基数对象只保留业务判断所需字段，并优先显示高风险/高超额组。"""
    frame = build_explanation_group_df(explanation)
    if frame.empty:
        return frame
    ranked = frame.assign(
        _risk=(frame["是否为显著高风险对象"] == "是").astype(int),
        _excess=pd.to_numeric(
            frame["高于总体水平的预计低评分数"], errors="coerce"
        ).fillna(0),
        _sample=pd.to_numeric(frame["样本量"], errors="coerce").fillna(0),
    ).sort_values(
        ["_risk", "_excess", "_sample"], ascending=[False, False, False]
    )
    columns = [
        "对象/分组", "样本量", "低评分率", "高于总体",
        "相对总体倍数（Lift）", "高于总体水平的预计低评分数",
        "对象优势比（OR）", "对象OR的95%置信区间", "对象校正后 p 值（FDR）",
    ]
    return ranked[columns].head(max_rows).reset_index(drop=True)


def build_compact_level_df(level_results: list[dict],
                           max_rows: int = 20) -> pd.DataFrame:
    """分类变量调整后显著水平的紧凑中文表。"""
    rows = []
    for row in level_results:
        if not row.get("stable_level"):
            continue
        ci = row.get("ci95")
        rows.append({
            "类别（相对参考组）": row.get("level"),
            "控制其他因素后的优势比（OR）": row.get("adjusted_or"),
            "95%置信区间": f"[{ci[0]:.3f}, {ci[1]:.3f}]" if ci else "—",
            "多重检验校正后 p 值（FDR）": format_p_value(row.get("p_adjusted")),
        })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        "控制其他因素后的优势比（OR）", ascending=False
    ).head(max_rows).reset_index(drop=True)


def render_static_table(frame: pd.DataFrame) -> None:
    """使用简单HTML表格，规避旧版Streamlit交互表的React #185。"""
    if frame.empty:
        return
    html = frame.to_html(index=False, escape=True, border=0)
    st.markdown(
        '<div style="max-height:520px;overflow:auto;border:1px solid #e6e6e6;'
        'border-radius:6px;padding:4px">' + html + "</div>",
        unsafe_allow_html=True,
    )


def build_numeric_explanation_dfs(explanation: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    details = explanation.get("details") or {}
    by_target = pd.DataFrame(details.get("by_target", []))
    bins = pd.DataFrame(details.get("quantile_bins", []))
    if not by_target.empty:
        by_target = by_target.rename(columns={
            "group": "是否低评分分组", "sample": "样本量", "p25": "P25",
            "median": "中位数", "p75": "P75", "mean": "均值",
        })
    if not bins.empty:
        bins = bins.rename(columns={
            "value_range": "变量分位区间", "sample": "样本量",
            "low_score_count": "低评分数", "low_score_rate": "低评分率",
        })
        bins["低评分率"] = bins["低评分率"].map(lambda value: f"{value:.1%}")
    return by_target, bins


def build_deep_validation_lines(res: dict) -> list[str]:
    """按业务主题合并补充验证任务，不重复单变量检验方法和p值。"""
    plan = {item.get("feature"): item for item in res.get("deep_validation_plan", [])}
    tasks = [
        (
            "履约因素",
            ["is_late_delivery", "delay_bucket", "late_days", "fulfillment_days",
             "approval_days"],
            "统一进入多变量模型，控制月份、地区、品类、金额和支付结构，"
            "区分是否延迟、延迟程度与总履约时长各自的独立贡献，并检查阈值效应。",
        ),
        (
            "地区与线路因素",
            ["route", "cross_state", "customer_state", "seller_state"],
            "合并稀疏线路并控制订单结构和履约表现，检验地区、跨州及高风险线路"
            "在多变量调整后是否仍显著，并用较晚时期订单验证线路方向。",
        ),
        (
            "订单结构因素",
            ["primary_category_name", "primary_payment_type", "price_total",
             "order_month"],
            "作为控制变量纳入模型；对检验前提不足的品类或月份先合并稀疏分组，"
            "再判断是否存在独立关联。",
        ),
    ]
    lines = []
    covered = set()
    for title, features, purpose in tasks:
        labels = []
        for feature in features:
            item = plan.get(feature)
            if item:
                labels.append(FEATURE_SHORT_LABELS.get(feature, item.get("label")))
                covered.add(feature)
        if labels:
            lines.append(f"{title}（{'、'.join(dict.fromkeys(labels))}）：{purpose}")
    for feature, item in plan.items():
        if feature not in covered:
            lines.append(f"{item.get('label')}：{item.get('purpose')}")
    return lines


def build_item_significance_df(res: dict, item_type: str,
                               result_type: str) -> pd.DataFrame:
    """商品项显著/待验证对象 → 可解释表格。"""
    detail = (
        res.get("item_drilldown", {}).get("significance", {})
        .get(item_type, {})
    )
    rows = []
    for row in detail.get(result_type, []):
        rows.append({
            "对象": row.get("value"),
            "订单数": row.get("sample"),
            "低评分率": f"{row.get('low_score_rate', 0):.1%}",
            "相对总体倍数（Lift）": round(row.get("lift") or 0, 2),
            "检验方法": row.get("method"),
            "原始 p 值": format_p_value(row.get("p")),
            "多重检验校正后 p 值（FDR）": format_p_value(row.get("p_adjusted")),
            "优势比（OR）": round(row.get("or") or 0, 3),
            "判断": (
                "与低评分显著正相关，可关注"
                if result_type == "significant_risk"
                else "描述性风险较高但未显著，需更多数据或深度验证"
            ),
        })
    return pd.DataFrame(rows)


def build_attribution_history_answer(res: dict) -> str:
    """对话历史中的自包含低评分关联因素分析摘要。"""
    if not res.get("ok"):
        return res.get("error", "低评分关联因素分析未完成。")
    summary = " ".join(build_verification_summary(res))
    return (
        f"低评分关联因素的两阶段分析已完成。{summary}"
        "检验方法、多重检验校正后p值、95%置信区间，以及控制其他因素后仍显著变量的分布见结果页面。"
        "本分析助手只提供统计证据与对象定位，不生成治理策略。"
    )


def build_recommendation_lines(res: dict) -> list[str]:
    """改善建议 → 文本行。"""
    recs = res.get("recommendations", {}).get("recommendations", [])
    return [f"[{r.get('priority')}] {r.get('factor')} → 责任方:{r.get('responsibility')} "
            f"| 动作:{'、'.join(r.get('actions', []))} "
            f"| 监控:{'、'.join(r.get('monitor_metrics', []))} "
            f"| 验证:{r.get('verify')}" for r in recs]


def build_deep_feature_df(res: dict) -> pd.DataFrame:
    """深度验证的调整后变量结果。"""
    rows = []
    for result in res.get("feature_results", []):
        if not result.get("ok"):
            rows.append({
                "变量": result.get("label"), "调整模型": result.get("model"),
                "控制其他因素后的优势比（OR）": "—",
                "95%置信区间": "—", "校正后 p 值": "—",
                "结论": "未能估计：" + result.get("error", "模型未稳定估计"),
            })
            continue
        ci = result.get("ci95")
        rows.append({
            "变量": result.get("label"),
            "调整模型": result.get("model"),
            "控制其他因素后的优势比（OR）": (
                f"{result['adjusted_or']:.3f}"
                if isinstance(result.get("adjusted_or"), (int, float)) else "整体联合检验"
            ),
            "95%置信区间": (
                f"[{ci[0]:.3f}, {ci[1]:.3f}]" if ci else "不适用"
            ),
            "校正后 p 值": format_p_value(result.get("p_adjusted")),
            "结论": result.get("conclusion"),
        })
    return pd.DataFrame(rows)


def build_route_validation_df(res: dict) -> pd.DataFrame:
    """高风险线路的多变量调整结果与跨时间验证结果。"""
    route_result = res.get("route_validation") or {}
    rows = []
    for route in route_result.get("routes", []):
        adjusted_ci = route.get("adjusted_ci95")
        holdout_ci = route.get("holdout_ci95")
        rows.append({
            "线路": route.get("route"),
            "较早时期建模订单数": route.get("train_n"),
            "较早时期低评分率": f"{route.get('train_low_score_rate', 0):.1%}",
            "控制其他因素后的优势比（OR）": (
                f"{route['adjusted_or']:.3f}"
                if isinstance(route.get("adjusted_or"), (int, float)) else "—"
            ),
            "优势比的95%置信区间": (
                f"[{adjusted_ci[0]:.3f}, {adjusted_ci[1]:.3f}]"
                if adjusted_ci else "—"
            ),
            "多重检验校正后 p 值（FDR）": format_p_value(route.get("adjusted_p_fdr")),
            "较晚时期验证订单数": route.get("holdout_n"),
            "较晚时期低评分率": (
                f"{route['holdout_low_score_rate']:.1%}"
                if isinstance(route.get("holdout_low_score_rate"), (int, float)) else "—"
            ),
            "较晚时期优势比（OR）": (
                f"{route['holdout_or']:.3f}"
                if isinstance(route.get("holdout_or"), (int, float)) else "—"
            ),
            "较晚时期95%置信区间": (
                f"[{holdout_ci[0]:.3f}, {holdout_ci[1]:.3f}]"
                if holdout_ci else "—"
            ),
            "稳定性": route.get("stability"),
        })
    return pd.DataFrame(rows)


def build_deep_history_answer(res: dict) -> str:
    summary = res.get("summary", {})
    significant = "、".join(summary.get("adjusted_significant", [])) or "暂无"
    stable_routes = "、".join(summary.get("stable_routes", [])) or "暂无"
    return (
        f"补充验证已完成。控制其他因素后仍显著：{significant}；"
        f"在较晚时期订单中保持同方向的高风险线路：{stable_routes}。"
        "详细优势比（OR）、置信区间和校正后p值见页面表格；结果仍为观察性关联。"
    )


# =====================================================================
# Streamlit 界面
# =====================================================================


def _provider(source: str = SAMPLE_SOURCE_LABEL,
              mysql_config: dict | None = None):
    """按界面选择返回演示样本或完整业务数据库。"""
    if source == DATABASE_SOURCE_LABEL:
        from agent_core.data_provider import MySQLProvider
        return MySQLProvider(
            **(mysql_config or {}),
            allow_tables=_semantic().allowed_tables(),
        )
    return ProjectCsvProvider()


def _semantic():
    return SemanticLayer()


def validate_attribution_result(res: dict) -> None:
    """拒绝渲染由旧核心模块生成的混合版本结果。"""
    required = {
        "feature_tests", "significant_features", "inconclusive_features",
        "not_significant_features", "deep_validation_plan", "item_drilldown",
        "selected_features", "adjusted_features", "adjusted_explanations",
        "adjusted_validation", "control_policy",
    }
    missing = required - set(res)
    if res.get("schema_version") != ATTRIBUTION_SCHEMA_VERSION or missing:
        raise RuntimeError(
            "分析模块与当前页面版本不一致。请关闭正在运行的服务，重新启动分析助手后刷新页面。"
            f" 当前结果版本={res.get('schema_version', '旧版')}，缺少={sorted(missing)}"
        )


def _attribution(source: str = SAMPLE_SOURCE_LABEL,
                 mysql_config: dict | None = None,
                 question: str | None = None):
    p = _provider(source, mysql_config)
    try:
        result = run_attribution(p, _semantic(), question=question)
        if result.get("ok"):
            validate_attribution_result(result)
        return result
    finally:
        p.close()


def render_attribution(q: str, res: dict) -> None:
    if not res.get("ok"):
        st.error(res.get("error", "低评分关联因素分析未完成"))
        return
    validate_attribution_result(res)
    st.markdown(f"**低评分关联因素分析**（问题：{q}）")
    st.info(
        "分析分为两个阶段：先逐一检验各变量与低评分的关联，仅保留FDR校正后p<0.05"
        "且95%置信区间排除无效值的变量；再处理共线性并进入多变量Logistic模型。"
        "结果用于识别调整后仍显著的关联因素，不作因果判断。"
    )
    base = res["baseline"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("订单级低评分率", f"{base['order']['low_score_rate']:.1%}",
              help=f"样本 {base['order']['sample']}")
    c2.metric("单变量筛选通过", len(res.get("significant_features", [])))
    c3.metric("进入多变量模型", len(res.get("selected_features", [])))
    c4.metric("调整后仍显著", len(res.get("adjusted_features", [])))

    test_df = build_feature_test_df(res)
    st.subheader("一、单变量关联筛选（FDR＋95%置信区间）")
    if test_df.empty:
        st.error("没有收到变量检验明细；结果不完整，已停止继续解释。")
    else:
        st.dataframe(test_df, use_container_width=True, hide_index=True)
        st.caption(
            "单变量显著仅表示样本内关联。大样本下较小效应也可能显著，"
            "业务重要性还需结合效应量、样本规模和多变量调整结果判断。"
        )

    selected = res.get("selected_features", [])
    st.subheader("二、共线性处理与控制变量")
    if selected:
        st.write("进入多变量Logistic模型的变量：" + "、".join(
            row.get("label", row.get("feature", "")) for row in selected
        ))
    else:
        st.warning("没有变量同时通过单变量显著性与置信区间标准，因此未形成候选解释变量。")
    policy = res.get("control_policy", {})
    c1, c2 = st.columns(2)
    c1.markdown("**订单级模型的固定控制变量**")
    c1.write("、".join(policy.get("order", [])) or "—")
    c2.markdown("**订单-卖家级模型的固定控制变量**")
    c2.write("、".join(policy.get("seller", [])) or "—")
    st.caption(policy.get("selection_rule", ""))

    st.subheader("三、多变量Logistic调整结果")
    adjusted_df = build_adjusted_attribution_df(res)
    if adjusted_df.empty:
        st.warning("没有可展示的调整后模型结果。")
    else:
        st.dataframe(adjusted_df, use_container_width=True, hide_index=True)
    for model in res.get("adjusted_validation", {}).get("models", []):
        if model.get("ok"):
            message = (
                f"{model.get('label')}估计方法："
                f"{display_fit_method(model.get('fit_method'))}"
            )
            if model.get("fallback_reason"):
                st.caption(message + "（默认估计方法未收敛，已自动改用二项GLM。）")
            else:
                st.caption(message)
        else:
            st.warning(f"{model.get('label')}未稳定估计：{model.get('error')}")

    st.subheader("四、调整后仍显著变量的分布")
    explanations = res.get("adjusted_explanations", [])
    if not explanations:
        st.info("当前没有变量在多变量调整后仍满足显著性与置信区间门槛。")
    for explanation in explanations:
        result = explanation.get("adjusted_result", {})
        with st.expander(f"{explanation.get('label')}：{result.get('conclusion')}", expanded=True):
            st.caption(explanation.get("interpretation"))
            render_delay_distribution_chart(explanation)
            if explanation.get("feature") == "route":
                route_df = build_attribution_route_df(explanation)
                if not route_df.empty:
                    st.caption("仅列出较早时期模型中显著，且在较晚20%订单中保持同方向的线路。")
                    st.dataframe(route_df, use_container_width=True, hide_index=True)
                else:
                    st.info("没有线路同时通过多变量调整和较晚时期订单验证。")
            elif explanation.get("kind") == "numeric":
                by_target, bins = build_numeric_explanation_dfs(explanation)
                if not by_target.empty:
                    st.markdown("**低评分与非低评分订单中的变量分布**")
                    st.dataframe(by_target, use_container_width=True, hide_index=True)
                if not bins.empty:
                    st.markdown("**变量分位区间对应的低评分率**")
                    st.dataframe(bins, use_container_width=True, hide_index=True)
            else:
                group_df = build_compact_group_df(explanation, max_rows=20)
                if not group_df.empty:
                    st.markdown("**重点对象明细（按显著高风险、高于总体水平的预计低评分数和样本量排序）**")
                    render_static_table(group_df)
                    total_groups = len(explanation.get("details") or [])
                    if total_groups > len(group_df):
                        st.caption(
                            f"为保证页面稳定，仅显示前{len(group_df)}组；"
                            f"完整结果共{total_groups}组。"
                        )
            level_results = result.get("level_results", [])
            stable_level_df = build_compact_level_df(level_results, max_rows=20)
            if not stable_level_df.empty:
                st.caption("以下类别相对模型参考组的调整后优势比（OR）仍显著：")
                render_static_table(stable_level_df)
                stable_count = sum(
                    1 for row in level_results if row.get("stable_level")
                )
                if stable_count > len(stable_level_df):
                    st.caption(
                        f"仅显示调整后优势比（OR）最高的前{len(stable_level_df)}项；"
                        f"共{stable_count}项。"
                    )

    st.warning(
        "分析边界：本结果不自动生成责任归属、治理动作、监控指标或A/B测试方案。"
    )

    item = res.get("item_drilldown", {})
    with st.expander("补充描述性结果：排查优先级、线路和商品项", expanded=False):
        st.caption("以下内容只用于定位问题对象，不用于判断统计显著性。")
        df = build_priority_df(res)
        if not df.empty:
            st.markdown("**问题排查优先级（P0最高）**")
            st.dataframe(df, use_container_width=True, hide_index=True)
        rt = build_route_summary(res)
        if rt:
            st.markdown("**线路描述性下钻**")
            st.write("\n".join(f"- {x}" for x in rt))
        if item.get("ok") and item.get("by_category"):
            st.markdown("**商品品类描述性排名**")
            st.dataframe(pd.DataFrame(item["by_category"]),
                         use_container_width=True, hide_index=True)

    st.caption("结果边界：" + "；".join(res.get("caveats", [])))


def render_answer(q: str, answer: str) -> None:
    st.markdown(f"**问**：{q}")
    st.write(answer if answer else "（未得到答案）")


def render_query_analysis(q: str, result: dict) -> None:
    """展示不依赖LLM的确定性取数结果。"""
    st.markdown(f"**取数结果**（问题：{q}）")
    st.caption("本次依据预设指标口径直接生成SQL，未调用大模型；结果来自当前所选数据源。")
    display_rows = result.get("display_rows", [])
    if not result.get("dimensions") and display_rows:
        items = list(display_rows[0].items())
        for start in range(0, len(items), 4):
            columns = st.columns(min(4, len(items) - start))
            for column, (label, value) in zip(columns, items[start:start + 4]):
                column.metric(label, value)
    elif display_rows:
        st.dataframe(pd.DataFrame(display_rows), use_container_width=True, hide_index=True)
    else:
        st.warning("查询成功，但没有符合条件的数据。")
    with st.expander("可对账 SQL"):
        st.code(result.get("sql", ""), language="sql")


def render_statistical_analysis(q: str, result: dict) -> None:
    """展示固定方法选择、显著性结果和业务边界。"""
    st.markdown(f"**统计分析**（问题：{q}）")
    if not result.get("ok"):
        st.error(format_statistical_result(result))
        return
    st.caption(
        f"变量组合：{result.get('variable_x_label', '—')} × "
        f"{result.get('variable_y_label', '—')}"
    )
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("检验方法", result.get("method_label", "—"))
    c2.metric("有效样本量", f"{result.get('sample', 0):,}")
    p = result.get("p")
    c3.metric("p 值", format_p_value(p))
    c4.metric("分析粒度", result.get("grain", "—").split("（")[0])
    st.write(format_statistical_result(result))
    if result.get("top_groups"):
        st.dataframe(pd.DataFrame(result["top_groups"]),
                     use_container_width=True, hide_index=True)
    if result.get("group_summaries"):
        st.caption(result.get("descriptive_label", "分组描述"))
        st.dataframe(pd.DataFrame(result["group_summaries"]),
                     use_container_width=True, hide_index=True)
    with st.expander("可对账 SQL"):
        sqls = result.get("sqls") or ([result["sql"]] if result.get("sql") else [])
        if sqls:
            for index, sql in enumerate(sqls, start=1):
                st.code(f"-- 查询 {index}\n{sql}", language="sql")
        else:
            st.write("该检验未生成行级查询。")


def render_deep_validation(q: str, result: dict) -> None:
    """展示指定变量的多变量调整与线路跨时间验证。"""
    st.markdown(f"**指定变量的补充验证**（问题：{q}）")
    if not result.get("ok"):
        st.error("补充验证未完成：" + result.get("error", "未知错误"))
        return
    st.info(
        "本次以是否低评分为结果变量，通过多变量Logistic控制已纳入的混杂因素；"
        "线路另外使用时间较晚的20%订单进行独立方向验证。"
    )
    summary = result.get("summary", {})
    route_result = result.get("route_validation") or {}
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("指定变量", len(result.get("requested_features", [])))
    c2.metric("成功估计模型", result.get("successful_models", 0))
    c3.metric("控制其他因素后仍显著", len(summary.get("adjusted_significant", [])))
    c4.metric("验证期保持同方向的线路", len(summary.get("stable_routes", [])))

    st.subheader("一、实际验证范围")
    st.write("、".join(result.get("requested_labels", [])))

    st.subheader("二、多变量调整结果")
    feature_df = build_deep_feature_df(result)
    if feature_df.empty:
        st.warning("没有生成可解释的调整后变量结果。")
    else:
        st.dataframe(feature_df, use_container_width=True, hide_index=True)
    failed_models = [
        model for model in result.get("models", []) if not model.get("ok")
    ]
    for model in failed_models:
        st.warning(f"{model.get('label')}未稳定估计：{model.get('error')}")

    st.subheader("三、高风险线路的跨时间验证")
    if route_result.get("ok"):
        st.caption(
            f"建模数据截止 {route_result.get('cutoff')}；较早时期样本 "
            f"{route_result.get('train_n', 0):,}，较晚时期验证样本 "
            f"{route_result.get('holdout_n', 0):,}。候选线路仅从较早时期数据识别。"
        )
        route_df = build_route_validation_df(result)
        st.dataframe(route_df, use_container_width=True, hide_index=True)
    elif "route" in result.get("requested_features", []):
        st.warning("线路跨时间验证未完成：" + route_result.get("error", "结果缺失"))
    else:
        st.write("本次问题没有要求验证线路。")

    st.subheader("四、综合判断")
    significant = summary.get("adjusted_significant", [])
    not_significant = summary.get("adjusted_not_significant", [])
    not_estimated = summary.get("not_estimated", [])
    stable_routes = summary.get("stable_routes", [])
    lines = [
        "控制已纳入因素后仍显著：" + ("、".join(significant) if significant else "暂无"),
        "控制已纳入因素后未达到显著性标准：" + (
            "、".join(not_significant) if not_significant else "暂无"
        ),
        "在较晚时期订单中保持同方向的高风险线路：" + (
            "、".join(stable_routes) if stable_routes else "暂无"
        ),
    ]
    if not_estimated:
        lines.append("因样本或模型稳定性暂不能判断：" + "、".join(not_estimated))
    st.write("\n".join(f"- {line}" for line in lines))
    st.warning("多变量调整仍只能说明统计关联，不代表因果；本页面不自动生成治理策略。")

    with st.expander("查询与复现信息"):
        profile = result.get("load_profile", {})
        st.write(profile.get("strategy"))
        if profile.get("extracts"):
            st.dataframe(pd.DataFrame(profile["extracts"]), hide_index=True,
                         use_container_width=True)
        for index, sql in enumerate(result.get("sqls", []), start=1):
            st.code(f"-- 查询 {index}\n{sql}", language="sql")
    st.caption("结果边界：" + "；".join(result.get("caveats", [])))


def main() -> None:
    """启动 Streamlit 页面；纯函数导入测试不会执行页面代码。"""
    st.set_page_config(page_title="Olist 业务数据分析助手", page_icon="📊", layout="wide")
    st.markdown("## 📊 Olist 业务数据分析助手")
    st.caption(f"版本：{APP_VERSION}")

    with st.sidebar:
        st.markdown("#### Olist 业务数据分析助手")
        st.caption(f"版本：{APP_VERSION}")
        with st.expander("📘 推荐分析流程与提问示例", expanded=True):
            st.markdown(
                """
**第1步：指标查询，确认现状**

- `总体低评分率、延迟率和订单量是多少？`
- `按月份查看订单量和低评分率。`
- `低评分率最高的10条线路是什么？`

建议明确写出“指标＋分组维度＋Top数量或时间范围”。指标口径明确时，
系统依据语义字典直接生成SQL；只有规则无法可靠解析时才调用DeepSeek。

**第2步：双变量统计检验**

- `配送时长与路线是否有显著关联？`
- `商品金额与运费是否相关？`
- `是否跨州与配送时长有显著差异？`
- `品类与支付方式是否有关联？`
- `商品项金额与商品重量是否相关？`

系统根据变量类型自动选择 Spearman、Mann–Whitney U、Kruskal–Wallis、
卡方/Fisher 或趋势检验，并展示分析粒度、p值和效应量。
只在相同数据粒度内比较，不跨分析宽表强行连接，避免一对多关系造成重复样本。

**第3步：低评分关联因素分析**

- `请对低评分进行归因分析。`
- `从履约、地区、线路、品类和支付角度筛查低评分关联因素。`

系统先检验各变量与“是否低评分（1-3分）”的关联，仅保留FDR校正后p<0.05
且95%置信区间排除无效值的变量；处理共线性后进入含固定控制变量的多变量
Logistic模型。最终展示调整后仍显著的变量及其分布，不输出因果结论或治理策略。
当前自动化关联因素分析仅支持低评分作为目标变量。

**第4步（可选）：指定变量做补充多变量验证**

- `深度验证是否延迟、延迟程度、总履约时长、地区、跨州及高风险线路与低评分的关联。`
- `深度验证延迟和跨州在控制地区、品类及金额后是否仍显著，并用较晚时期订单验证高风险线路。`

必须明确写出“深度验证”，并列出希望纳入的变量。该入口用于补充验证指定变量；
线路会额外使用较晚20%订单检查关联方向能否跨时间保持。

**推荐顺序：指标查询 → 双变量检验 → 低评分关联因素分析 → 必要时补充多变量验证。**
                """
            )
        with st.expander("统计术语与结果口径", expanded=False):
            st.markdown(
                """
- **分析宽表（Mart）**：按订单、订单-卖家或商品项等固定粒度整理的分析表。
- **FDR校正**：控制同时检验多个变量时的预期假发现比例。
- **优势比（OR）**：Logistic模型中的相对优势；OR>1表示事件优势上升，不等同风险比。
- **Lift**：某组低评分率相对总体低评分率的倍数。
- **HC3稳健标准误**：降低异方差对标准误和置信区间的影响。
- **跨时间验证**：在较早时期识别线路，再用较晚时期订单检查方向是否保持。
                """
            )
        with st.expander("当前支持的统计变量", expanded=False):
            rows = supported_variables()
            for kind, title in (
                ("numeric", "连续变量"),
                ("ordinal", "有序变量"),
                ("binary", "二分类变量"),
                ("categorical", "分类变量"),
            ):
                labels = list(dict.fromkeys(
                    row["label"] for row in rows if row["kind"] == kind
                ))
                st.markdown(f"**{title}：**" + "、".join(labels))
            st.caption("订单ID、客户ID等标识符不属于可解释统计变量；时间戳先转换为业务时长再检验。")
        if st.button("清空对话记录"):
            st.session_state["history"] = []
            st.rerun()
        data_source = st.radio(
            "分析数据源", [SAMPLE_SOURCE_LABEL, DATABASE_SOURCE_LABEL], index=0
        )
        if data_source == SAMPLE_SOURCE_LABEL:
            try:
                probe = ProjectCsvProvider()
                counts = {k: v for k, v in probe.row_counts.items()
                          if k != "mart_order_item_analysis"}
                probe.close()
                st.caption("数据行数：" + "；".join(
                    f"{TABLE_DISPLAY_NAMES.get(k, k)}={v}" for k, v in counts.items()))
                if counts and max(counts.values()) <= 1000:
                    st.warning(
                        "当前为约1,000行的演示样本，仅用于检查功能和分析流程；"
                        "业务结论请使用完整业务数据库。"
                    )
            except Exception as e:
                st.error(f"演示样本检查失败：{e}")
        mysql_config = None
        if data_source == DATABASE_SOURCE_LABEL:
            st.markdown("##### 完整业务数据库连接（MySQL）")
            with st.form("mysql_connection"):
                host = st.text_input("主机", value="127.0.0.1")
                port = st.number_input("端口", min_value=1, max_value=65535,
                                       value=3306, step=1)
                user = st.text_input("用户名", value="root")
                password = st.text_input("密码", type="password")
                database = st.text_input("数据库", value="olist_ecommerce")
                connect = st.form_submit_button("连接并检查数据库")
            if connect:
                candidate = {
                    "host": host, "port": int(port), "user": user,
                    "password": password, "database": database,
                }
                try:
                    from agent_core.data_provider import MySQLProvider
                    probe = MySQLProvider(
                        **candidate, allow_tables=_semantic().allowed_tables())
                    report = probe.inspect_marts()
                    probe.close()
                    st.session_state["mysql_config"] = candidate
                    st.session_state["mysql_report"] = report
                    st.success("数据库已连接，3张分析宽表的字段检查通过。")
                except Exception as e:
                    st.session_state.pop("mysql_config", None)
                    st.session_state.pop("mysql_report", None)
                    st.error(f"连接或字段检查失败：{e}")
            mysql_config = st.session_state.get("mysql_config")
            report = st.session_state.get("mysql_report")
            if mysql_config and report:
                st.success(f"当前数据源：完整业务数据库 `{report['database']}`")
                st.dataframe(pd.DataFrame([
                    {"分析宽表": TABLE_DISPLAY_NAMES.get(table, table),
                     "行数": info["row_count"],
                     "字段数": info["column_count"]}
                    for table, info in report["tables"].items()
                ]), hide_index=True, use_container_width=True)
                if st.button("断开数据库"):
                    st.session_state.pop("mysql_config", None)
                    st.session_state.pop("mysql_report", None)
                    st.rerun()
            else:
                st.info("请填写MySQL连接信息，并点击“连接并检查数据库”。")
        st.caption(
            "指标查询优先使用预设口径直接生成SQL；统计问题使用固定方法；"
            "规则无法可靠解析的开放式问题才调用DeepSeek。"
        )
        if st.button("运行核心评测"):
            import subprocess
            r = subprocess.run([sys.executable, str(ROOT / "tests" / "run_eval.py")],
                               capture_output=True, text=True)
            st.text(r.stdout[-500:] if r.stdout else r.stderr[-500:])

    st.subheader("对话")
    if "history" not in st.session_state:
        st.session_state["history"] = []

    for role, text in st.session_state["history"]:
        with st.chat_message(role):
            st.write(text)

    q = st.chat_input("例如：分析与低评分相关的因素 / 总体延迟率是多少？")
    if not q:
        return

    st.session_state["history"].append(("user", q))
    with st.chat_message("user"):
        st.write(q)
    with st.chat_message("assistant"):
        try:
            intent = Intent(_semantic()).classify(q)
            if intent == "deep_validation":
                if data_source == DATABASE_SOURCE_LABEL and not mysql_config:
                    raise RuntimeError("请先在左侧连接并检查完整业务数据库")
                p = _provider(data_source, mysql_config)
                try:
                    res = analyze_deep_validation(p, q)
                finally:
                    p.close()
                render_deep_validation(q, res)
                answer = build_deep_history_answer(res)
            elif intent == "statistical":
                if data_source == DATABASE_SOURCE_LABEL and not mysql_config:
                    raise RuntimeError("请先在左侧连接并检查完整业务数据库")
                p = _provider(data_source, mysql_config)
                try:
                    res = analyze_statistical_question(p, q)
                finally:
                    p.close()
                render_statistical_analysis(q, res)
                answer = format_statistical_result(res)
            elif intent == "query":
                if data_source == DATABASE_SOURCE_LABEL and not mysql_config:
                    raise RuntimeError("请先在左侧连接并检查完整业务数据库")
                p = _provider(data_source, mysql_config)
                try:
                    res = analyze_query_question(p, _semantic(), q)
                finally:
                    p.close()
                if res.get("ok"):
                    render_query_analysis(q, res)
                    answer = res.get("answer", "已完成取数。")
                else:
                    # 规则无法可靠解析时才回退大模型，不把猜测当成确定性SQL。
                    from agent_core.llm import DeepSeekLLM
                    from agent_core.loop import ReActLoop
                    llm = DeepSeekLLM()
                    p = _provider(data_source, mysql_config)
                    try:
                        fallback = ReActLoop(llm, p, _semantic()).run(q)
                    finally:
                        p.close()
                    answer = fallback.get("answer", "") or "（未得到答案）"
                    render_answer(q, answer)
            elif intent == "attribution":
                if data_source == DATABASE_SOURCE_LABEL and not mysql_config:
                    raise RuntimeError("请先在左侧连接并检查完整业务数据库")
                res = _attribution(data_source, mysql_config, q)
                render_attribution(q, res)
                answer = build_attribution_history_answer(res)
            else:
                from agent_core.llm import DeepSeekLLM, MockLLM
                from agent_core.loop import ReActLoop
                try:
                    llm = DeepSeekLLM()
                except (RuntimeError, ValueError) as e:
                    st.warning(
                        f"{e}；当前使用内置示例响应检查交互流程，"
                        "该响应不作为数据结论。"
                    )
                    llm = MockLLM(
                        tool_call={"tool": "query_mart",
                                   "args": {"table": "mart_order_delivery",
                                            "metrics": ["low_score_rate"]}},
                        answer="（内置示例响应）已按预设口径查询低评分率。")
                if data_source == DATABASE_SOURCE_LABEL and not mysql_config:
                    raise RuntimeError("请先在左侧连接并检查完整业务数据库")
                p = _provider(data_source, mysql_config)
                try:
                    res = ReActLoop(llm, p, _semantic()).run(q)
                finally:
                    p.close()
                answer = res.get("answer", "") or "（未得到答案）"
                render_answer(q, answer)
        except Exception as e:
            answer = f"执行失败：{e}"
            st.error(answer)
        st.session_state["history"].append(("assistant", answer))


if __name__ == "__main__":
    main()
