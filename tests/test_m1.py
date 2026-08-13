"""M1 测试：工具层对账 + ReAct(Mock) 端到端 + 安全校验。

核心是对账：工具层 query_mart 生成并执行 SQL 得到的结果，
必须与手写 SQL 直接查询 SampleProvider 的期望值一致（保证"数字可对账"）。
"""
import sys
from pathlib import Path

import pytest

# 确保能从项目根导入 agent_core
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_core.data_provider import SampleProvider  # noqa: E402
from agent_core.llm import MockLLM  # noqa: E402
from agent_core.loop import ReActLoop  # noqa: E402
from agent_core.semantic import SemanticLayer  # noqa: E402
from agent_core.tools import Tools  # noqa: E402


@pytest.fixture(scope="module")
def env():
    semantic = SemanticLayer()
    provider = SampleProvider()
    tools = Tools(provider, semantic)
    yield semantic, provider, tools
    provider.close()


def q(provider, sql):
    rows = provider.execute(sql)
    return rows[0] if rows else {}


def approx_equal(a, b, rel=1e-6):
    if a is None or b is None:
        return a == b
    return abs(a - b) <= rel * max(1.0, abs(a), abs(b))


# ---- 1. 总体延迟率与低评分率 ----
def test_1_overall_rates(env):
    _, provider, tools = env
    res = tools.query_mart(
        "mart_order_delivery", metrics=["late_rate", "low_score_rate"]
    )
    assert res["ok"]
    exp = q(provider, "SELECT AVG(is_late_delivery) AS d, AVG(is_low_score) AS l "
                      "FROM mart_order_delivery WHERE is_delivery_analysis_eligible=1")
    assert approx_equal(res["rows"][0]["_m_late_rate"], exp["d"])
    assert approx_equal(res["rows"][0]["_m_low_score_rate"], exp["l"])


# ---- 2. 按时 vs 延迟评分对比 ----
def test_2_delayed_vs_ontime(env):
    _, provider, tools = env
    res = tools.query_mart(
        "mart_order_delivery", metrics=["avg_review_score"], dimensions=["is_late_delivery"]
    )
    assert res["ok"]
    exp = provider.execute(
        "SELECT is_late_delivery, AVG(review_score) AS a FROM mart_order_delivery "
        "WHERE is_delivery_analysis_eligible=1 GROUP BY is_late_delivery"
    )
    exp = {int(r["is_late_delivery"]): r["a"] for r in exp}
    by = {int(r["is_late_delivery"]): r["_m_avg_review_score"] for r in res["rows"]}
    assert set(by) == set(exp)
    for k in exp:
        assert approx_equal(by[k], exp[k])
    # 期望：延迟订单平均评分低于按时订单
    assert by[1] < by[0]


# ---- 3. 延迟分档低评分率 ----
def test_3_delay_bucket(env):
    _, provider, tools = env
    res = tools.query_mart(
        "mart_order_delivery", metrics=["low_score_rate"], dimensions=["delay_bucket"]
    )
    assert res["ok"]
    exp = provider.execute(
        "SELECT delay_bucket, AVG(is_low_score) AS l FROM mart_order_delivery "
        "WHERE is_delivery_analysis_eligible=1 GROUP BY delay_bucket"
    )
    exp = {r["delay_bucket"]: r["l"] for r in exp}
    by = {r["delay_bucket"]: r["_m_low_score_rate"] for r in res["rows"]}
    assert set(by) == set(exp)
    for k in exp:
        assert approx_equal(by[k], exp[k])
    # 期望低评分率随延迟档位单调上升
    order = ["按时", "1-3天", "4-7天", "8-14天", "15天+"]
    vals = [by[k] for k in order if k in by]
    assert vals == sorted(vals)


# ---- 4. Top-N 品类 / 州 ----
def test_4_top_n(env):
    _, provider, tools = env
    res = tools.top_n("mart_order_delivery", "low_score_rate", "primary_category_name", 5)
    assert res["ok"]
    assert res["row_count"] == 5
    exp = provider.execute(
        "SELECT primary_category_name, AVG(is_low_score) AS l FROM mart_order_delivery "
        "WHERE is_delivery_analysis_eligible=1 GROUP BY primary_category_name "
        "ORDER BY l DESC LIMIT 5"
    )
    exp = [r["primary_category_name"] for r in exp]
    got = [r["primary_category_name"] for r in res["rows"]]
    assert got == exp


# ---- 5. 时长拆解 ----
def test_5_time_breakdown(env):
    _, provider, tools = env
    res = tools.query_mart(
        "mart_order_delivery", metrics=["avg_approval_days", "avg_fulfillment_days"]
    )
    assert res["ok"]
    exp = q(provider, "SELECT AVG(approval_days) AS a, AVG(fulfillment_days) AS f "
                      "FROM mart_order_delivery WHERE is_delivery_analysis_eligible=1")
    assert approx_equal(res["rows"][0]["_m_avg_approval_days"], exp["a"])
    assert approx_equal(res["rows"][0]["_m_avg_fulfillment_days"], exp["f"])


# ---- 卖家表 ----
def test_seller_table(env):
    _, provider, tools = env
    res = tools.top_n("mart_order_seller_delivery", "low_score_rate", "seller_state", 5)
    assert res["ok"]
    exp = provider.execute(
        "SELECT seller_state, AVG(is_low_score) AS l FROM mart_order_seller_delivery "
        "GROUP BY seller_state ORDER BY l DESC LIMIT 5"
    )
    assert [r["seller_state"] for r in res["rows"]] == [r["seller_state"] for r in exp]


# ---- 安全/口径校验 ----
def test_reject_unknown_metric(env):
    _, _, tools = env
    res = tools.query_mart("mart_order_delivery", metrics=["not_a_metric"])
    assert not res["ok"]
    assert "指标不存在" in res["error"]


def test_reject_unknown_table(env):
    _, _, tools = env
    res = tools.query_mart("raw_orders", metrics=["late_rate"])
    assert not res["ok"]


def test_order_by_must_be_in_query(env):
    _, _, tools = env
    res = tools.query_mart(
        "mart_order_delivery", metrics=["late_rate"], order_by="low_score_rate"
    )
    assert not res["ok"]
    assert "order_by" in res["error"]


# ---- ReAct(Mock) 端到端 ----
def test_react_mock_end_to_end():
    semantic = SemanticLayer()
    provider = SampleProvider()
    llm = MockLLM(
        tool_call={
            "tool": "query_mart",
            "args": {"table": "mart_order_delivery",
                     "metrics": ["late_rate", "low_score_rate"]},
        },
        answer="总体延迟率 X%，低评分率 Y%（来源 SQL 见轨迹，可对账）。",
    )
    loop = ReActLoop(llm, provider, semantic)
    res = loop.run("总体延迟率和低评分率是多少？")
    provider.close()
    assert res["ok"]
    assert "延迟率" in res["answer"]
    # 轨迹中确实执行了一次工具调用
    assert any(t.get("event") == "tool" and t.get("tool") == "query_mart"
               for t in res["trace"])
