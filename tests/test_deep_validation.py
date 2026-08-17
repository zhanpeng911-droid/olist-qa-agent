"""深度验证：多变量调整、全部变量抽取和线路时间留出。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_core.data_provider import ProjectCsvProvider  # noqa: E402
from agent_core.deep_validation import (  # noqa: E402
    analyze_deep_validation,
    extract_deep_features,
    is_deep_validation_question,
)
from agent_core.intent import Intent  # noqa: E402
from agent_core.semantic import SemanticLayer  # noqa: E402


QUESTION = (
    "深度验证是否延迟、延迟程度、总履约时长、地区、跨州及高风险线路"
    "与低评分的相关性"
)


@pytest.fixture(scope="module")
def deep_result():
    provider = ProjectCsvProvider()
    result = analyze_deep_validation(provider, QUESTION)
    provider.close()
    return result


def test_deep_intent_precedes_single_statistical_route():
    assert is_deep_validation_question(QUESTION)
    assert Intent(SemanticLayer()).classify(QUESTION) == "deep_validation"
    assert Intent(SemanticLayer()).classify(
        "用留出数据验证高风险线路是否稳定"
    ) == "deep_validation"


def test_extracts_every_named_feature():
    assert extract_deep_features(QUESTION) == [
        "is_late_delivery", "late_days", "fulfillment_days",
        "customer_state", "seller_state", "cross_state", "route",
    ]


def test_deep_validation_uses_adjusted_models(deep_result):
    assert deep_result["ok"]
    assert deep_result["mode"] == "deep_validation"
    assert deep_result["successful_models"] >= 2
    labels = {row["label"] for row in deep_result["feature_results"]}
    assert {"是否延迟", "延迟程度（延迟天数）", "总履约时长",
            "客户地区", "卖家地区", "是否跨州"} <= labels
    assert all(
        "Pearson" not in row.get("method", "")
        for row in deep_result["feature_results"]
    )
    assert any(
        "Logistic" in row.get("method", "")
        for row in deep_result["feature_results"] if row.get("ok")
    )


def test_route_uses_time_holdout(deep_result):
    route = deep_result["route_validation"]
    assert route["ok"]
    assert route["train_n"] > route["holdout_n"] > 0
    assert route["cutoff"]
    assert route["routes"]
    assert all("stability" in row for row in route["routes"])


def test_deep_load_is_bounded_and_sequential(deep_result):
    assert len(deep_result["sqls"]) == 2
    assert all("limit" in sql.lower() for sql in deep_result["sqls"])
    extracts = deep_result["load_profile"]["extracts"]
    assert [row["table"] for row in extracts] == [
        "mart_order_delivery", "mart_order_seller_delivery"
    ]
    assert max(row["columns"] for row in extracts) <= 9
