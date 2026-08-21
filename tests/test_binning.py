import pandas as pd

from agent_core.binning import numeric_rate_bins


def test_skewed_item_count_uses_business_buckets_instead_of_one_quantile():
    frame = pd.DataFrame({
        "seller_items": [1] * 90 + [2] * 5 + [3] * 3 + [4, 8],
        "is_handover_late": [0] * 80 + [1] * 20,
    })

    result = numeric_rate_bins(frame, "seller_items", "is_handover_late")

    assert result["method"] == "business_count_bins"
    assert [row["value_range"] for row in result["rows"]] == [
        "1", "2", "3", "4及以上"
    ]
    assert sum(row["sample"] for row in result["rows"]) == len(frame)


def test_tied_numeric_quantiles_fall_back_to_multiple_groups():
    frame = pd.DataFrame({
        "metric": [0] * 95 + [1, 2, 3, 4, 5],
        "target": [0] * 90 + [1] * 10,
    })

    result = numeric_rate_bins(frame, "metric", "target")

    assert len(result["rows"]) >= 2
    assert sum(row["sample"] for row in result["rows"]) == len(frame)
