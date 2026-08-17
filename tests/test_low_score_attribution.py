"""低评分专用归因：两层门槛、共线性规则、自动调整与输出边界。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_core.attribution import run_attribution  # noqa: E402
from agent_core.data_provider import ProjectCsvProvider  # noqa: E402
from agent_core.low_score_attribution import (  # noqa: E402
    ALPHA, screen_low_score_features,
)
from agent_core.semantic import SemanticLayer  # noqa: E402


@pytest.fixture(scope="module")
def result():
    provider = ProjectCsvProvider()
    try:
        yield run_attribution(
            provider, SemanticLayer(), question="请对低评分进行归因分析"
        )
    finally:
        provider.close()


def test_only_low_score_target_is_supported():
    provider = ProjectCsvProvider()
    try:
        for question in ("请对延迟进行归因", "请对复购进行归因", "对销售额进行原因分析"):
            unsupported = run_attribution(
                provider, SemanticLayer(), question=question
            )
            assert unsupported["ok"] is False
            assert unsupported["unsupported_target"] is True
            assert "只支持" in unsupported["error"]
    finally:
        provider.close()


def test_first_layer_uses_fdr_and_confidence_interval(result):
    assert result["ok"]
    rows = result["feature_tests"]
    assert rows
    for row in rows:
        if not row["ok"]:
            continue
        expected = (
            row["p_adjusted"] < ALPHA
            and row["ci_passed"]
            and row.get("assumption_ok") is not False
        )
        assert row["significant"] is expected
        assert row["target"] == "是否低评分"
        assert row["ci95"] and len(row["ci95"]) == 2


def test_collinear_group_keeps_one_preconfigured_representative(result):
    selected = result["selected_features"]
    groups = [row["collinear_group"] for row in selected]
    assert len(groups) == len(set(groups))
    delivery = [row for row in selected if row["collinear_group"] == "delivery_result"]
    if delivery:
        assert delivery[0]["feature"] == "is_late_delivery"
    geography = [row for row in selected if row["collinear_group"] == "shipping_geography"]
    if geography:
        assert geography[0]["feature"] == "cross_state"


def test_selection_reason_distinguishes_group_representative_from_singleton():
    provider = ProjectCsvProvider()
    try:
        screening = screen_low_score_features(provider)
    finally:
        provider.close()
    retained_by_group = {}
    for row in screening["retained"]:
        retained_by_group.setdefault(row["collinear_group"], []).append(row)
    for row in screening["selected"]:
        group_size = len(retained_by_group[row["collinear_group"]])
        expected = (
            "同组变量信息重复，选择业务含义最直观的代表变量" if group_size > 1
            else "没有表达相同信息的变量，直接进入多变量模型"
        )
        assert row["selection_reason"] == expected


def test_logistic_only_validates_selected_representatives(result):
    selected = {row["feature"] for row in result["selected_features"]}
    modeled = {
        row["feature"] for row in result["adjusted_validation"]["results"]
    }
    assert modeled == selected
    assert result["adjusted_validation"]["models"]
    assert all(
        model.get("robust") == "HC3"
        for model in result["adjusted_validation"]["models"]
        if model.get("ok")
    )


def test_adjusted_stability_also_requires_fdr_and_ci(result):
    for row in result["adjusted_validation"]["results"]:
        if not row.get("ok"):
            continue
        assert row["stable"] is (
            row["p_adjusted"] < ALPHA and bool(row["ci_passed"])
        )


def test_only_stable_variables_receive_explanations(result):
    stable = {row["feature"] for row in result["adjusted_features"]}
    explained = {row["feature"] for row in result["adjusted_explanations"]}
    assert explained == stable
    assert all(row.get("details") is not None
               for row in result["adjusted_explanations"])


def test_stable_variables_receive_delay_stratified_visual_data(result):
    explanations = result["adjusted_explanations"]
    assert explanations
    for explanation in explanations:
        visual = explanation.get("delay_visualization")
        assert visual is not None
        if not visual.get("ok"):
            assert visual.get("error")
            continue
        assert visual.get("rows")
        for row in visual["rows"]:
            assert row["delay_status"] in {"延迟", "未延迟"}
            share = row.get("within_delay_share")
            assert share is None or 0 <= share <= 1
    late = next(row for row in explanations if row["feature"] == "is_late_delivery")
    assert all(
        isinstance(row.get("low_score_rate"), float)
        for row in late["delay_visualization"]["rows"]
    )


def test_route_explanation_contains_only_holdout_stable_routes(result):
    route_explanation = next(
        (row for row in result["adjusted_explanations"]
         if row["feature"] == "route"),
        None,
    )
    if route_explanation is None:
        pytest.skip("当前截取样本没有通过两层门槛的线路")
    expected = set(route_explanation["adjusted_result"].get("stable_routes", []))
    assert {row["value"] for row in route_explanation["details"]} == expected
    assert {
        row["route"] for row in route_explanation.get("route_validation") or []
    } == expected
    assert all(
        row["stability"] == "稳定复现"
        for row in route_explanation.get("route_validation") or []
    )


def test_attribution_never_generates_strategy(result):
    assert result["recommendations"]["status"] == "disabled_evidence_only"
    assert result["recommendations"]["recommendations"] == []
    forbidden = {"actions", "responsibility", "monitor_metrics", "verify"}
    assert all(not (forbidden & set(row))
               for row in result["adjusted_features"])


def test_models_do_not_repeat_collinear_delivery_terms(result):
    order_models = [
        model for model in result["adjusted_validation"]["models"]
        if model.get("label", "").startswith("订单级")
    ]
    if not order_models:
        pytest.skip("样本第一层没有订单级候选变量")
    formula = order_models[0]["formula"]
    delivery_terms = {
        "is_late_delivery": "is_late_delivery" in formula,
        "delay_bucket": "delay_rank" in formula,
        "late_days": "z_late_days" in formula,
        "fulfillment_days": "z_fulfillment" in formula,
    }
    assert sum(delivery_terms.values()) <= 1
