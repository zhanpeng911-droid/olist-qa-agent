"""网页界面测试：验证 ui/app.py 的纯展示函数（不启动 Streamlit）。

确保归因结果的展示数据准备正确，且模块可导入。
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_core.attribution import run_attribution  # noqa: E402
from agent_core.data_provider import (  # noqa: E402
    DATABASE_SOURCE_LABEL, SAMPLE_SOURCE_LABEL, ProjectCsvProvider,
)
from agent_core.intent import Intent  # noqa: E402
from agent_core.semantic import SemanticLayer  # noqa: E402
from ui.app import (  # noqa: E402
    build_adjusted_attribution_df, build_attribution_history_answer,
    build_attribution_route_df,
    build_compact_group_df, build_compact_level_df,
    build_deep_feature_df,
    build_deep_history_answer, build_deep_validation_lines,
    build_delay_distribution_df,
    build_explanation_group_df, build_feature_test_df,
    build_item_significance_df, build_numeric_explanation_dfs, build_priority_df,
    build_recommendation_lines, build_route_summary, build_verification_summary,
    build_route_validation_df, format_p_value, validate_attribution_result,
)


@pytest.fixture(scope="module")
def attr():
    semantic = SemanticLayer()
    provider = ProjectCsvProvider()
    yield run_attribution(provider, semantic)
    provider.close()


def test_is_attribution_question():
    intent = Intent(SemanticLayer())
    assert intent.classify("对低评分进行归因") == "attribution"
    assert intent.classify("为什么低评分高") == "attribution"
    assert intent.classify("总体延迟率是多少") == "query"
    assert intent.classify("低评分率与路线是否显著相关") == "statistical"
    assert intent.classify(
        "深度验证延迟、地区和线路与低评分的相关性"
    ) == "deep_validation"
    assert intent.classify(
        "请对低评分进行多维归因，完成单变量筛选和调整后验证。"
    ) == "attribution"
    assert intent.classify(
        "请对低评分进行归因，只列出调整后证据和分布。"
    ) == "attribution"
    assert intent.classify(
        "延迟在低评分归因中使用什么检验方法以及p值？"
    ) == "statistical"


@pytest.mark.parametrize("question", [
    "支付审批和整个履约平均各用了几天？",
    "晚到的单子是不是普遍打分更差？先把两组数据列出来。",
    "哪些地方的客人更容易给三星及以下？列前五。",
    "哪些收货地区又容易晚到又容易给低分？按客户州列出来。",
])
def test_query_paraphrases_are_not_routed_to_other(question):
    assert Intent(SemanticLayer()).classify(question) == "query"


def test_build_priority_df(attr):
    df = build_priority_df(attr)
    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    assert {"排查优先级（P0最高）", "维度", "对象", "样本量",
            "相对总体倍数（Lift）"} <= set(df.columns)


def test_external_copy_uses_consistent_professional_terms(attr):
    assert SAMPLE_SOURCE_LABEL == "演示样本（截取数据）"
    assert DATABASE_SOURCE_LABEL == "完整业务数据库（MySQL）"
    text = " ".join([
        build_attribution_history_answer(attr),
        attr.get("note", ""),
        *attr.get("caveats", []),
        *build_feature_test_df(attr).columns,
        *build_adjusted_attribution_df(attr).columns,
    ])
    forbidden = (
        "项目 CSV", "项目CSV", "真实 MySQL", "真实MySQL",
        "轻量回答", "调整后稳定变量", "本Agent",
    )
    assert not any(term in text for term in forbidden)
    assert "多变量" in text
    assert "置信区间" in text


def test_all_physical_tables_have_business_display_names():
    from ui.app import TABLE_DISPLAY_NAMES

    assert TABLE_DISPLAY_NAMES["mart_order_delivery"] == "订单级分析宽表"
    assert TABLE_DISPLAY_NAMES["mart_order_item_delivery"] == "商品项级分析宽表"
    assert TABLE_DISPLAY_NAMES["mart_order_seller_delivery"] == "订单-卖家级分析宽表"


def test_build_route_summary(attr):
    lines = build_route_summary(attr)
    assert lines, "应有 route 摘要"


def test_build_verification_summary(attr):
    lines = build_verification_summary(attr)
    assert any("单变量检验" in line for line in lines)
    assert any("共线性处理后进入多变量模型" in line for line in lines)
    assert any("控制其他影响因素后仍存在显著关联" in line for line in lines)


def test_p_value_zero_is_not_shown_as_zero():
    assert format_p_value(0.0) == "<1e-300"
    assert format_p_value(float("nan")) == "—"


def test_current_attribution_schema_is_accepted(attr):
    validate_attribution_result(attr)


def test_old_attribution_schema_is_rejected(attr):
    old_result = dict(attr)
    old_result.pop("schema_version")
    with pytest.raises(RuntimeError, match="版本不一致"):
        validate_attribution_result(old_result)


def test_incomplete_attribution_result_is_rejected(attr):
    incomplete = dict(attr)
    incomplete.pop("feature_tests")
    with pytest.raises(RuntimeError, match="版本不一致"):
        validate_attribution_result(incomplete)


def test_build_feature_test_df(attr):
    df = build_feature_test_df(attr)
    assert not df.empty
    assert {"特征", "检验方法", "原始 p 值", "多重检验校正后 p 值（FDR）",
            "是否显著", "效应量", "效应量的95%置信区间",
            "置信区间是否排除无效值", "进入多变量模型"} <= set(df.columns)
    assert "是否延迟" in set(df["特征"])


def test_build_deep_validation_lines(attr):
    lines = build_deep_validation_lines(attr)
    assert lines
    assert any("履约因素" in line and "多变量模型" in line for line in lines)
    assert all("p值" not in line and "轻量检验" not in line for line in lines)


def test_adjusted_result_and_explanation_tables(attr):
    adjusted = build_adjusted_attribution_df(attr)
    assert not adjusted.empty
    assert {"变量", "调整模型", "模型估计方式", "控制其他因素后的效应",
            "95%置信区间", "多重检验校正后 p 值（FDR）",
            "控制其他因素后仍显著"} <= set(adjusted.columns)
    explanations = attr["adjusted_explanations"]
    assert explanations
    for explanation in explanations:
        if explanation["kind"] == "numeric":
            by_target, bins = build_numeric_explanation_dfs(explanation)
            assert not by_target.empty
            assert not bins.empty
        else:
            assert not build_explanation_group_df(explanation).empty


def test_attribution_route_table_only_uses_stable_validation_rows():
    explanation = {
        "route_validation": [{
            "route": "SP→RJ", "train_n": 1000,
            "train_low_score_rate": 0.28, "adjusted_or": 1.4,
            "adjusted_ci95": [1.2, 1.6], "adjusted_p_fdr": 0.001,
            "holdout_n": 250, "holdout_low_score_rate": 0.27,
            "holdout_or": 1.3, "holdout_ci95": [1.05, 1.7],
            "stability": "稳定复现",
        }]
    }
    table = build_attribution_route_df(explanation)
    assert len(table) == 1
    assert table.iloc[0]["线路"] == "SP→RJ"
    assert table.iloc[0]["稳定性"] == "稳定复现"


def test_binary_explanation_uses_business_labels_instead_of_zero_one():
    explanation = {
        "feature": "cross_state",
        "details": [
            {"value": 0, "sample": 20, "low_score_count": 2},
            {"value": 1, "sample": 30, "low_score_count": 5},
        ],
    }
    table = build_explanation_group_df(explanation)
    assert list(table["对象/分组"]) == ["同州", "跨州"]


def test_delay_distribution_chart_data_uses_business_labels():
    explanation = {
        "feature": "is_any_item_handover_late",
        "delay_visualization": {
            "ok": True,
            "rows": [
                {"group": "0", "delay_status": "未延迟", "sample": 90,
                 "within_delay_share": 0.9},
                {"group": "1", "delay_status": "延迟", "sample": 10,
                 "within_delay_share": 0.1},
            ],
        },
    }
    table = build_delay_distribution_df(explanation)
    assert list(table["分组"]) == ["无交接超期", "存在交接超期"]
    assert list(table["是否延迟"]) == ["未延迟", "延迟"]


def test_high_cardinality_details_are_compact_and_risk_first():
    details = []
    for index in range(52):
        details.append({
            "value": f"category_{index}", "sample": 100 + index,
            "low_score_count": 10, "low_score_rate": 0.1,
            "rate_difference": 0.01, "lift": 1.1,
            "excess_low_score": float(index), "or": 1.1,
            "ci95": [1.01, 1.2], "p_adjusted": 0.01,
            "significant_risk": index == 0,
        })
    table = build_compact_group_df({"details": details}, max_rows=20)
    assert len(table) == 20
    assert table.iloc[0]["对象/分组"] == "category_0"
    assert "对象显著高风险" not in table.columns


def test_stable_level_table_is_chinese_and_capped():
    levels = [
        {"level": f"L{index}", "adjusted_or": 1 + index / 10,
         "ci95": [1.01, 2.0], "p_adjusted": 0.001,
         "stable_level": True}
        for index in range(25)
    ]
    table = build_compact_level_df(levels, max_rows=20)
    assert len(table) == 20
    assert set(table.columns) == {
        "类别（相对参考组）", "控制其他因素后的优势比（OR）",
        "95%置信区间", "多重检验校正后 p 值（FDR）"
    }
    assert table.iloc[0]["类别（相对参考组）"] == "L24"


def test_lightweight_has_no_recommendation_lines(attr):
    lines = build_recommendation_lines(attr)
    assert lines == []


def test_item_significance_table_is_explanatory(attr):
    df = build_item_significance_df(attr, "category", "significant_risk")
    assert not df.empty
    assert {"对象", "检验方法", "原始 p 值", "多重检验校正后 p 值（FDR）",
            "优势比（OR）", "判断"} <= set(df.columns)
    assert all("显著正相关" in value for value in df["判断"])


def test_attribution_history_answer_is_clear(attr):
    answer = build_attribution_history_answer(attr)
    assert "低评分关联因素的两阶段分析已完成" in answer
    assert "控制其他影响因素后仍存在显著关联" in answer
    assert "多重检验校正后p值、95%置信区间" in answer
    assert "不生成治理策略" in answer


def test_deep_result_tables_and_history():
    from agent_core.deep_validation import analyze_deep_validation

    provider = ProjectCsvProvider()
    result = analyze_deep_validation(
        provider,
        "深度验证是否延迟、延迟程度、总履约时长、地区、跨州和高风险线路",
    )
    provider.close()
    assert not build_deep_feature_df(result).empty
    assert not build_route_validation_df(result).empty
    answer = build_deep_history_answer(result)
    assert "补充验证已完成" in answer
    assert "控制其他因素后仍显著" in answer
