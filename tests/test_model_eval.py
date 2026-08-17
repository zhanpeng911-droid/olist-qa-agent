"""模型评测器本身的回归测试，避免把正确 top_n 误判为失败。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from run_model_eval import _signature, _tool_match  # noqa: E402


def test_top_n_matches_expected_metric_and_dimension():
    case = {
        "table": "mart_order_delivery",
        "metrics": ["low_score_rate"],
        "dimensions": ["primary_category_name"],
    }
    trace = [{
        "event": "tool", "tool": "top_n", "ok": True,
        "args": {
            "table": "mart_order_delivery", "metric": "low_score_rate",
            "dimension": "primary_category_name", "n": 5,
        },
    }]
    assert _tool_match(case, trace)
    assert _signature(trace) == ((
        "top_n", "mart_order_delivery",
        ("low_score_rate",), ("primary_category_name",),
    ),)


def test_top_n_wrong_table_is_not_accepted():
    case = {
        "table": "mart_order_delivery",
        "metrics": ["low_score_rate"],
        "dimensions": ["primary_category_name"],
    }
    trace = [{
        "event": "tool", "tool": "top_n", "ok": True,
        "args": {
            "table": "mart_order_item_analysis", "metric": "low_score_rate",
            "dimension": "category_name", "n": 5,
        },
    }]
    assert not _tool_match(case, trace)


def test_route_accepts_equivalent_state_pair_on_seller_table():
    case = {
        "table": "mart_order_seller_delivery",
        "metrics": ["low_score_rate"], "dimensions": ["route"],
    }
    trace = [{
        "event": "tool", "tool": "query_mart", "ok": True,
        "args": {
            "table": "mart_order_seller_delivery",
            "metrics": ["record_count", "low_score_rate"],
            "dimensions": ["seller_state", "customer_state"],
        },
    }]
    assert _tool_match(case, trace)
