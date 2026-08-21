"""复杂统计问句：方法选择、真实截取 CSV 执行和失败兜底。"""
from __future__ import annotations

import sys
import time
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_core.data_provider import ProjectCsvProvider  # noqa: E402
from agent_core.data_provider import MySQLProvider  # noqa: E402
from agent_core.intent import Intent  # noqa: E402
from agent_core.llm import LLMClient  # noqa: E402
from agent_core.loop import ReActLoop, parse_decision  # noqa: E402
from agent_core.semantic import SemanticLayer  # noqa: E402
from agent_core.statistical_analysis import (  # noqa: E402
    analyze_statistical_question,
    format_statistical_result,
    plan_statistical_question,
)


@pytest.fixture(scope="module")
def env():
    semantic = SemanticLayer()
    provider = ProjectCsvProvider()
    yield semantic, provider
    provider.close()


@pytest.mark.parametrize(("question", "factor", "method"), [
    ("分析低评分率与路线是否有显著相关", "route", "pearson_chi_square"),
    ("低评分与是否跨州存在关联吗？", "cross_state", "binary_association"),
    ("延迟程度越高，低评分率是否显著上升？", "delay_bucket", "cochran_armitage_trend"),
    ("延迟天数与评价分数是否相关？", "late_days", "spearman"),
    ("运费在低评分和非低评分订单间有显著差异吗？", "freight_total", "mann_whitney_u"),
    ("不同品类的评价分数分布是否不同？", "primary_category_name", "kruskal_wallis"),
])
def test_method_selection(question, factor, method):
    plan = plan_statistical_question(question)
    assert plan["ok"]
    assert plan["factor"] == factor
    assert plan["method"] == method


def test_route_significance_executes_on_project_csv(env):
    _, provider = env
    started = time.perf_counter()
    result = analyze_statistical_question(
        provider, "分析低评分率与路线是否有显著相关"
    )
    elapsed = time.perf_counter() - started
    assert result["ok"]
    assert result["method"] == "pearson_chi_square"
    assert result["sample"] > 0 and result["groups_tested"] >= 2
    assert 0 <= result["p"] <= 1
    assert 0 <= result["effect_size"] <= 1
    assert "GROUP BY route, is_low_score" in result["sql"]
    assert elapsed < 5, "CSV 已加载后，聚合检验不应长时间挂起"
    answer = format_statistical_result(result)
    assert "方法" in answer and "结论" in answer and "边界" in answer


def test_route_question_bypasses_generic_query_path(env):
    semantic, _ = env
    assert Intent(semantic).classify("分析低评分率与路线是否有显著相关") == "statistical"


def test_feature_method_and_p_value_query(env):
    semantic, provider = env
    question = "延迟在低评分归因中使用了什么检验方法以及p值？"
    assert Intent(semantic).classify(question) == "statistical"
    plan = plan_statistical_question(question)
    assert plan["factor"] == "is_late_delivery"
    assert plan["target"] == "is_low_score"
    result = analyze_statistical_question(provider, question)
    answer = format_statistical_result(result)
    assert result["ok"] and 0 <= result["p"] <= 1
    assert "方法：" in answer and "p值：" in answer and "效应量：" in answer


def test_unknown_factor_returns_actionable_error(env):
    _, provider = env
    result = analyze_statistical_question(provider, "低评分率和天气是否显著相关")
    assert not result["ok"]
    assert "未识别要检验的因素" in result["error"]


def test_mysql_decimal_amount_is_coerced_before_mann_whitney():
    class DecimalAmountProvider:
        def execute(self, sql: str) -> list[dict]:
            return [
                {"price_total": Decimal("10.50"), "is_low_score": 0},
                {"price_total": Decimal("20.00"), "is_low_score": 0},
                {"price_total": Decimal("30.75"), "is_low_score": 1},
                {"price_total": Decimal("40.25"), "is_low_score": 1},
            ]

    result = analyze_statistical_question(
        DecimalAmountProvider(), "商品金额与低评分是否有显著关系"
    )
    assert result["ok"]
    assert result["method"] == "mann_whitney_u"
    assert 0 <= result["p"] <= 1


@pytest.mark.parametrize(("question", "method", "table", "x", "y"), [
    ("配送时长是否与路线有显著相关", "kruskal_wallis",
     "mart_order_seller_delivery", "fulfillment_days", "route"),
    ("商品金额与运费是否相关", "spearman",
     "mart_order_delivery", "price_total", "freight_total"),
    ("是否跨州与配送时长有显著差异", "mann_whitney_u",
     "mart_order_seller_delivery", "cross_state", "fulfillment_days"),
    ("品类与支付方式是否有关联", "pearson_chi_square",
     "mart_order_delivery", "category", "payment_type"),
    ("商品项金额与商品重量是否相关", "spearman",
     "mart_order_item_analysis", "price_total", "weight_g"),
])
def test_general_pair_planning(question, method, table, x, y):
    plan = plan_statistical_question(question)
    assert plan["ok"]
    assert plan["method"] == method
    assert plan["table"] == table
    assert plan["variable_x"] == x
    assert plan["variable_y"] == y
    assert plan["grain"]


@pytest.mark.parametrize("question", [
    "配送时长是否与路线有显著相关",
    "商品金额与运费是否相关",
    "是否跨州与配送时长有显著差异",
    "品类与支付方式是否有关联",
    "商品项金额与商品重量是否相关",
])
def test_general_pair_executes_on_project_csv(env, question):
    _, provider = env
    result = analyze_statistical_question(provider, question)
    assert result["ok"], result.get("error")
    assert result["sample"] > 0
    assert 0 <= result["p"] <= 1
    assert result["variable_x_label"] in result["conclusion"]
    assert result["variable_y_label"] in result["conclusion"]
    assert result.get("sql") or result.get("sqls")


def test_cross_grain_pair_is_rejected():
    plan = plan_statistical_question("支付方式与配送线路是否有关联")
    assert not plan["ok"]
    assert "不在同一受控分析粒度" in plan["error"]


def test_more_than_two_variables_creates_batch_plan():
    plan = plan_statistical_question("商品金额与运费、配送时长分别是否相关")
    assert plan["ok"] and plan["batch"]
    assert plan["anchor_variable"] == "price_total"
    assert plan["comparison_count"] == 2
    assert [p["variable_y"] for p in plan["comparisons"]] == [
        "freight_total", "fulfillment_days",
    ]


def test_batch_statistical_question_executes_without_llm(env):
    _, provider = env
    question = (
        "是否延迟与品类、运费率、商品项数量、是否多卖家订单、是否跨州、"
        "是否存在交接超期、线路分别有显著关系"
    )
    result = analyze_statistical_question(provider, question)
    assert result["ok"] and result["batch"]
    assert result["anchor_variable"] == "is_late_delivery"
    assert result["comparison_count"] == 7
    assert result["successful_count"] == 7
    assert result["failed_count"] == 0
    assert all(row.get("p_adjusted") is not None for row in result["results"])
    assert all(row.get("p_correction") == "FDR-BH" for row in result["results"])
    assert {row["comparison_variable"] for row in result["results"]} == {
        "category", "freight_ratio", "item_count", "is_multi_seller_order",
        "cross_state", "is_any_item_handover_late", "route",
    }
    result_tables = {row["comparison_variable"]: row["table"] for row in result["results"]}
    assert result_tables["category"] == "mart_order_delivery"
    assert result_tables["freight_ratio"] == "mart_order_delivery"
    assert result_tables["item_count"] == "mart_order_delivery"
    assert result_tables["is_multi_seller_order"] == "mart_order_delivery"
    assert result_tables["cross_state"] == "mart_order_seller_delivery"
    assert result_tables["route"] == "mart_order_seller_delivery"
    answer = format_statistical_result(result)
    assert "FDR校正后显著" in answer and "原始p=" in answer


def test_batch_question_accepts_common_cross_state_typo():
    plan = plan_statistical_question(
        "是否延迟与品类、是否跨周、线路分别有显著关系"
    )
    assert plan["ok"] and plan["batch"]
    assert [p["variable_y"] for p in plan["comparisons"]] == [
        "category", "cross_state", "route",
    ]


def test_mysql_compatibility_views_expose_new_duration_fields():
    provider = object.__new__(MySQLProvider)
    provider._item_table = "mart_order_item_business"
    seller_sql = provider._compatibility_sql(
        "SELECT fulfillment_days, route FROM mart_order_seller_delivery LIMIT 10"
    )
    item_sql = provider._compatibility_sql(
        "SELECT fulfillment_days, approval_days FROM mart_order_item_analysis LIMIT 10"
    )
    assert "TIMESTAMPDIFF" in seller_sql
    assert "AS fulfillment_days" in seller_sql
    assert "total_fulfillment_hours / 24.0 AS fulfillment_days" in item_sql
    assert "payment_approval_hours / 24.0 AS approval_days" in item_sql


def test_parse_fenced_and_prefixed_json():
    assert parse_decision('```json\n{"action":"answer","content":"完成"}\n```')["content"] == "完成"
    assert parse_decision('结果如下：{"action":"answer","content":"完成"}。')["content"] == "完成"


class _SequenceLLM(LLMClient):
    def __init__(self, replies: list[str]):
        self.replies = replies
        self.i = 0

    def chat(self, messages: list[dict]) -> str:
        reply = self.replies[min(self.i, len(self.replies) - 1)]
        self.i += 1
        return reply


def test_react_never_returns_blank_answer(env):
    semantic, provider = env
    llm = _SequenceLLM(['{"action":"answer","content":""}'])
    result = ReActLoop(llm, provider, semantic, max_steps=2).run("任意问题")
    assert not result["ok"]
    assert result["answer"]
    assert result["answer"] == result["error"]


def test_react_api_failure_is_nonblank_and_does_not_crash(env):
    class FailingLLM(LLMClient):
        def chat(self, messages: list[dict]) -> str:
            raise TimeoutError("simulated timeout")

    semantic, provider = env
    result = ReActLoop(FailingLLM(), provider, semantic).run("总体延迟率是多少")
    assert not result["ok"]
    assert "模型调用失败" in result["answer"]
    assert result["trace"][0]["event"] == "llm_error"
