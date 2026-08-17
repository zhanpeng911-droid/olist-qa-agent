"""确定性自然语言取数：常见问题不依赖API。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_core.data_provider import ProjectCsvProvider  # noqa: E402
from agent_core.query_analysis import (  # noqa: E402
    analyze_query_question,
    plan_query_question,
)
from agent_core.semantic import SemanticLayer  # noqa: E402


@pytest.fixture(scope="module")
def env():
    semantic = SemanticLayer()
    provider = ProjectCsvProvider()
    yield semantic, provider
    provider.close()


def test_composite_overall_query_without_api(env):
    semantic, provider = env
    question = "总体订单量、低评分率、延迟率和平均评分是多少？"
    result = analyze_query_question(provider, semantic, question)
    assert result["ok"]
    assert result["execution_mode"] == "deterministic_query"
    assert result["metrics"] == [
        "order_count", "low_score_rate", "late_rate", "avg_review_score"
    ]
    assert result["dimensions"] == []
    assert len(result["display_rows"][0]) == 4
    assert "SELECT" in result["sql"]


def test_grouped_multi_metric_query(env):
    semantic, provider = env
    result = analyze_query_question(
        provider, semantic, "按月查看订单量、低评分率和延迟率。"
    )
    assert result["ok"]
    assert result["dimensions"] == ["order_month"]
    assert set(result["metrics"]) == {"order_count", "low_score_rate", "late_rate"}
    assert len(result["display_rows"]) > 1


def test_route_top_n_and_order_count_mapping(env):
    semantic, provider = env
    result = analyze_query_question(
        provider, semantic, "低评分率最高的10条线路是什么？同时给出订单量。"
    )
    assert result["ok"]
    assert result["table"] == "mart_order_seller_delivery"
    assert result["dimensions"] == ["route"]
    assert result["metrics"] == ["record_count", "low_score_rate"]
    assert result["order_by"] == "low_score_rate"
    assert result["row_count"] <= 10


def test_crossed_dimensions_are_both_kept(env):
    semantic, provider = env
    result = analyze_query_question(
        provider, semantic, "按品类和支付方式交叉查看低评分率。"
    )
    assert result["ok"]
    assert result["dimensions"] == [
        "primary_category_name", "primary_payment_type"
    ]


def test_unknown_metric_does_not_guess(env):
    semantic, _ = env
    plan = plan_query_question("查询天气对销量的影响", semantic)
    assert not plan["ok"]
    assert plan["recognized"] is False
