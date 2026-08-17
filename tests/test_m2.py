"""M2 测试：L2 描述性归因（对账 / 小样本过滤 / Lift 与超额 / 优先级 / 端到端）。

核心是对账：归因扫描结果必须与手写 SQL 直接计算一致。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_core.attribution import (  # noqa: E402
    ORDER_DIMENSIONS, SELLER_DIMENSIONS, SELLER_FILTER,
    analyze_item_presence_significance, analyze_routes, build_baseline,
    rank_priorities, run_attribution, screen_factors,
)
from agent_core.data_provider import ProjectCsvProvider  # noqa: E402
from agent_core.semantic import SemanticLayer  # noqa: E402
from agent_core.tools import Tools  # noqa: E402

ORDER_TABLE = "mart_order_delivery"
SELLER_TABLE = "mart_order_seller_delivery"


@pytest.fixture(scope="module")
def env():
    semantic = SemanticLayer()
    provider = ProjectCsvProvider()
    tools = Tools(provider, semantic)
    yield semantic, provider, tools
    provider.close()


def approx_equal(a, b, rel=1e-6):
    if a is None or b is None:
        return a == b
    return abs(a - b) <= rel * max(1.0, abs(a), abs(b))


# ---- baseline 对账 ----
def test_baseline_order(env):
    semantic, provider, tools = env
    base = build_baseline(tools, ORDER_TABLE, "order_count")
    exp = provider.execute(
        "SELECT COUNT(*) AS n, SUM(is_low_score) AS lc, AVG(is_low_score) AS r "
        "FROM mart_order_delivery WHERE is_delivery_analysis_eligible=1 "
        "AND has_review_record=1")[0]
    assert base["sample"] == exp["n"]
    assert base["low_score_count"] == exp["lc"]
    assert approx_equal(base["low_score_rate"], exp["r"])


def test_baseline_seller(env):
    semantic, provider, tools = env
    base = build_baseline(tools, SELLER_TABLE, "record_count", filters=SELLER_FILTER)
    exp = provider.execute(
        "SELECT COUNT(*) AS n, SUM(is_low_score) AS lc, AVG(is_low_score) AS r "
        "FROM mart_order_seller_delivery WHERE is_multi_seller_order=0 "
        "AND is_delivery_analysis_eligible=1 AND has_review_record=1")[0]
    assert base["sample"] == exp["n"]
    assert base["low_score_count"] == exp["lc"]
    assert approx_equal(base["low_score_rate"], exp["r"])


# ---- screen_factors 对账 + 小样本过滤 ----
def test_screen_factors_reconcile(env):
    _, _, tools = env
    base = build_baseline(tools, ORDER_TABLE, "order_count")
    groups = screen_factors(
        tools, ORDER_TABLE, ["delay_bucket"], base["low_score_rate"],
        "order_count", min_sample=1)
    exp_rows = tools.query_mart(
        ORDER_TABLE, metrics=["low_score_count", "low_score_rate", "order_count"],
        dimensions=["delay_bucket"], limit=10000)
    assert exp_rows["ok"]
    exp = {r["delay_bucket"]: r for r in exp_rows["rows"]}
    for g in groups:
        e = exp[g["value"]]
        assert g["sample"] == e["_m_order_count"]
        assert g["low_score_count"] == e["_m_low_score_count"]
        assert approx_equal(g["low_score_rate"], e["_m_low_score_rate"])


def test_min_sample_filter(env):
    _, _, tools = env
    base = build_baseline(tools, ORDER_TABLE, "order_count")
    all_groups = screen_factors(
        tools, ORDER_TABLE, ["delay_bucket"], base["low_score_rate"],
        "order_count", min_sample=1)
    filtered = screen_factors(
        tools, ORDER_TABLE, ["delay_bucket"], base["low_score_rate"],
        "order_count", min_sample=100)
    assert len(filtered) <= len(all_groups)
    assert all(g["sample"] >= 100 for g in filtered)


# ---- Lift 与超额公式 ----
def test_lift_and_excess(env):
    _, _, tools = env
    base = build_baseline(tools, ORDER_TABLE, "order_count")
    groups = screen_factors(
        tools, ORDER_TABLE, ["delay_bucket"], base["low_score_rate"],
        "order_count", min_sample=1)
    for g in groups:
        assert approx_equal(g["lift"], g["low_score_rate"] / base["low_score_rate"])
        expected_excess = g["sample"] * max(
            g["low_score_rate"] - base["low_score_rate"], 0)
        # 实现按 1 位小数舍入展示，故断言舍入后一致
        assert g["excess_low_score"] == round(expected_excess, 1)


# ---- 优先级 ----
def test_rank_priorities(env):
    _, _, tools = env
    base = build_baseline(tools, ORDER_TABLE, "order_count")
    groups = screen_factors(
        tools, ORDER_TABLE, ORDER_DIMENSIONS, base["low_score_rate"],
        "order_count", min_sample=100)
    ranked = rank_priorities(groups, top_k=15)
    assert ranked, "不应为空"
    # 综合分降序
    scores = [g["priority_score"] for g in ranked]
    assert scores == sorted(scores, reverse=True)
    # P0 存在且为最高综合分组
    assert any(g["priority"] == "P0" for g in ranked)
    p0_score = max(g["priority_score"] for g in ranked if g["priority"] == "P0")
    assert p0_score == scores[0]


# ---- 归因流程端到端 ----
def test_run_attribution_end_to_end(env):
    semantic, provider, _ = env
    res = run_attribution(provider, semantic)
    assert res["ok"]
    assert set(["order", "seller"]) <= set(res["baseline"])
    assert "order" in res["factors"] and "seller" in res["factors"]
    assert res["priorities"], "优先级列表不应为空"
    assert res["caveats"]
    assert res["sqls"], "应收集可对账 SQL"
    assert res["feature_tests"], "应给出每项检验的方法和p值"
    assert res["significant_features"], "应明确总结显著特征"
    assert res["selected_features"], "第一层后应选出共线性代表变量"
    assert res["adjusted_validation"]["ok"]
    assert res["recommendations"]["recommendations"] == []
    assert res["recommendations"]["status"] == "disabled_evidence_only"
    # 所有优先级对象都来自过滤后的组（样本量足够）
    assert all(g["sample"] >= semantic.guards["min_group_sample"] for g in res["priorities"])


def test_feature_test_catalog_is_explicit(env):
    semantic, provider, _ = env
    res = run_attribution(provider, semantic)
    for row in res["feature_tests"]:
        assert row["label"] and row["method"]
        if row["ok"]:
            assert 0 <= row["p"] <= 1
            assert 0 <= row["p_used"] <= 1
            assert row["ci95"] and len(row["ci95"]) == 2
        else:
            assert row["p"] is None
            assert row["error"]
        assert row["p_basis"] == "FDR-BH校正后 p 值"
    labels = {row["label"] for row in res["significant_features"]}
    assert "是否延迟" in labels


def test_automatic_adjustment_plan_contains_no_strategy_actions(env):
    semantic, provider, _ = env
    res = run_attribution(provider, semantic)
    assert res["deep_validation_plan"]
    for item in res["deep_validation_plan"]:
        assert {"feature", "recommended_method", "purpose", "reason", "status"} <= set(item)
        assert "actions" not in item and "responsibility" not in item
        assert item["status"] == "已自动完成调整后验证"


def test_item_category_significance_is_order_level_and_corrected(env):
    _, provider, _ = env
    result = analyze_item_presence_significance(
        provider, "category_name", min_group_sample=20
    )
    assert result["ok"] and result["tested_count"] > 0
    assert "FDR-BH" in result["method"]
    assert "order_id" in result["grain_note"]
    for row in result["all_tests"]:
        assert 0 <= row["p"] <= 1
        assert 0 <= row["p_adjusted"] <= 1
        assert row["sample"] <= result["baseline"]["orders"]
    for row in result["significant_risk"]:
        assert row["significant"]
        assert row["p_adjusted"] < 0.05
        assert row["low_score_rate"] > result["baseline"]["low_score_rate"]


def test_item_product_reports_insufficient_sample_explicitly(env):
    _, provider, _ = env
    result = analyze_item_presence_significance(
        provider, "product_id", min_group_sample=10
    )
    assert result["ok"]
    assert result["tested_count"] == len(result["all_tests"])
    assert result["min_group_sample"] == 10


def test_item_significance_sql_obeys_mysql_limit_guard(env):
    _, provider, _ = env
    result = analyze_item_presence_significance(
        provider, "category_name", min_group_sample=20
    )
    assert result["ok"]
    assert result["sqls"]
    assert all("limit" in sql.lower() for sql in result["sqls"])


# ---- route 线路深挖（M2 边角）----
def test_analyze_routes_structure(env):
    semantic, provider, _ = env
    res = analyze_routes(provider, semantic)
    assert res["ok"]
    assert res["top_routes"]
    assert res["concentration"]["total_low_score_count"] > 0
    assert res["concentration"]["top5_share"] is not None
    assert res["route_cross_delay"]
    assert res["sqls"]


def test_analyze_routes_reconcile(env):
    semantic, provider, _ = env
    res = analyze_routes(provider, semantic)
    exp = provider.execute(
        "SELECT route, COUNT(*) AS n, SUM(is_low_score) AS lc, AVG(is_low_score) AS r "
        "FROM mart_order_seller_delivery WHERE is_multi_seller_order=0 "
        "AND is_delivery_analysis_eligible=1 AND has_review_record=1 "
        "GROUP BY route")[0:99999]
    exp = {r["route"]: r for r in exp}
    for g in res["top_routes"]:
        e = exp[g["value"]]
        assert g["sample"] == e["n"]
        assert g["low_score_count"] == e["lc"]
        assert approx_equal(g["low_score_rate"], e["r"])


def test_route_concentration(env):
    semantic, provider, _ = env
    res = analyze_routes(provider, semantic)
    conc = res["concentration"]
    # Top5 线路低评分订单数占比 = 手算值（实现按 4 位小数舍入展示）
    top5_count = sum(g["low_score_count"] for g in conc["top_routes_by_count"])
    assert top5_count == conc["top5_low_score_count"]
    assert conc["top5_share"] == round(
        conc["top5_low_score_count"] / conc["total_low_score_count"], 4)


def test_route_cross_delay(env):
    semantic, provider, _ = env
    res = analyze_routes(provider, semantic)
    # 交叉结果里每条 Top 线路应含延迟/非延迟两组（若样本存在）
    for c in res["route_cross_delay"]:
        assert set(c) >= {"route", "late", "not_late"}
        for grp in (c["late"], c["not_late"]):
            if grp:
                assert "sample" in grp and "low_score_rate" in grp
