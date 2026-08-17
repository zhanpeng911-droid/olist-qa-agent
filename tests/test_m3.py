"""M3 测试：统计验证（单变量检验 / Logistic / 校正 / 证据分级 / 集成）。

用构造已知关系的数据验证统计方法正确性，再用项目截取 CSV 验证业务关联与归因集成。
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_core.data_provider import ProjectCsvProvider  # noqa: E402
from agent_core.semantic import SemanticLayer  # noqa: E402
from agent_core.statistics import (  # noqa: E402
    categorical_test, chi_square_rc, correlation_test, distribution_test,
    evidence_grade, logistic_model_formula, multiple_correction,
    trend_test, verify_factors,
)


@pytest.fixture(scope="module")
def env():
    semantic = SemanticLayer()
    provider = ProjectCsvProvider()
    yield semantic, provider
    provider.close()


def _known_df(n=400, seed=1):
    """构造 x→y 强关联 + 无关变量 w 的已知数据。"""
    rng = np.random.default_rng(seed)
    x = rng.integers(0, 2, n)
    p_y = np.where(x == 1, 0.7, 0.2)
    y = rng.binomial(1, p_y)
    w = rng.normal(size=n)
    return pd.DataFrame({"x": x, "y": y, "w": w})


# ---- 单变量方法正确性（构造数据） ----
def test_categorical_test_known():
    df = _known_df()
    res = categorical_test(df, "x", "y")
    assert res["p"] < 0.01
    assert res["or"] > 1
    assert res["or_ci"][0] > 1  # 95%CI 不含 1


def test_categorical_test_no_assoc():
    rng = np.random.default_rng(2)
    x = rng.integers(0, 2, 200)
    y = rng.integers(0, 2, 200)
    res = categorical_test(pd.DataFrame({"x": x, "y": y}), "x", "y")
    assert res["p"] > 0.05


def test_trend_test_known():
    rng = np.random.default_rng(3)
    # 有序分值 0-4，高分值 → 高事件率
    score = rng.integers(0, 5, 500)
    p = 0.1 + 0.15 * score
    y = rng.binomial(1, p)
    res = trend_test(pd.DataFrame({"s": score, "y": y}), "s", "y")
    assert res["p"] < 0.01
    assert res["n"] == 500


def test_distribution_test_known():
    rng = np.random.default_rng(4)
    g = rng.integers(0, 2, 300)
    v = np.where(g == 1, rng.normal(3, 1, 300), rng.normal(0, 1, 300))
    res = distribution_test(pd.DataFrame({"g": g, "v": v}), "v", "g")
    assert res["p"] < 0.01
    assert res["median_1"] > res["median_0"]


def test_correlation_test_known():
    rng = np.random.default_rng(5)
    a = rng.normal(size=300)
    b = -a + rng.normal(size=300) * 0.5
    res = correlation_test(pd.DataFrame({"a": a, "b": b}), "a", "b")
    assert res["rho"] < -0.5
    assert res["p"] < 0.01


def test_multiple_correction():
    res = multiple_correction([0.01, 0.03, 0.2, 0.5])
    assert res["method"] == "fdr_bh"
    assert len(res["p_adjusted"]) == 4
    assert len(res["significant"]) == 4
    assert res["p_adjusted"][0] <= res["p_adjusted"][1]


def test_evidence_grade():
    assert evidence_grade(1e-6, 0.001, 2.0, 500) == "强证据"
    assert evidence_grade(0.01, None, 8.0, 500) == "强证据"
    assert evidence_grade(0.01, None, 1.1, 500) == "中等证据"
    assert evidence_grade(0.5, None, 3.0, 500) == "待验证线索"
    assert evidence_grade(0.01, None, 3.0, 50) == "待验证线索"


def test_logistic_rank_deficient_design_does_not_crash():
    """重复解释变量造成秩不足时也应得到可审计结果，而非LinAlgError。"""
    df = _known_df(n=600, seed=17)
    df["x_duplicate"] = df["x"]
    result = logistic_model_formula(df, "y ~ x + x_duplicate + w")
    assert result["fit_method"] in {"Logit-Newton", "GLM-Binomial回退"}
    assert result["nobs"] == len(df)
    assert result["robust"] == "HC3"
    assert {row["term"] for row in result["terms"]} >= {"x", "x_duplicate", "w"}


# ---- 项目截取 CSV 业务关联 ----
def test_project_csv_late_association(env):
    semantic, provider = env
    df = provider.execute(
        "SELECT is_late_delivery, is_low_score, late_days, review_score, "
        "delay_bucket FROM mart_order_delivery "
        "WHERE is_delivery_analysis_eligible=1 LIMIT 100000")
    df = pd.DataFrame(df)
    res = categorical_test(df, "is_late_delivery", "is_low_score")
    assert res["p"] < 0.05
    assert res["or"] > 1
    corr = correlation_test(df, "late_days", "review_score")
    assert corr["rho"] < 0            # 延迟天数越多评分越低
    assert corr["p"] < 0.05


# ---- 集成：verify_factors / run_attribution ----
def test_verify_factors_integration(env):
    semantic, provider = env
    v = verify_factors(provider)
    assert v["single_tests"]
    assert "is_late_delivery" in [t["factor"] for t in v["single_tests"]]
    assert v["mode"] == "lightweight"
    assert v["load_profile"]["max_interactive_python_columns"] == 2
    assert not v["logistic"]["enabled"]
    for model in (v["logistic"]["order"], v["logistic"]["seller"]):
        assert model.get("skipped") is True
        assert "轻量交互归因" in model["error"]
    assert v["evidence"]["grade"] == "强证据"
    assert v["evidence"]["or"] > 1


def test_lightweight_verification_never_loads_wide_rows():
    class RecordingProvider:
        def __init__(self):
            self.inner = ProjectCsvProvider()
            self.sqls = []

        def execute(self, sql):
            self.sqls.append(sql)
            return self.inner.execute(sql)

        def close(self):
            self.inner.close()

    provider = RecordingProvider()
    try:
        result = verify_factors(provider)
    finally:
        provider.close()
    extracts = result["load_profile"]["row_level_extracts"]
    assert extracts
    assert all(len(item["columns"]) == 2 for item in extracts)
    assert not any("seller_price" in sql for sql in provider.sqls)
    raw_selects = [
        sql for sql in provider.sqls
        if "COUNT(*)" not in sql and "FROM mart_order_delivery" in sql
    ]
    assert raw_selects
    assert all(sql.split(" FROM ", 1)[0].count(",") == 1
               for sql in raw_selects)


def test_lightweight_aggregates_match_raw_reference(env):
    _, provider = env
    rows = provider.execute(
        "SELECT is_late_delivery, is_low_score, delay_bucket, customer_state, "
        "primary_category_name, primary_payment_type FROM mart_order_delivery "
        "WHERE is_delivery_analysis_eligible=1 AND has_review_record=1 LIMIT 100000"
    )
    raw = pd.DataFrame(rows)
    verification = verify_factors(provider)
    by_factor = {t["factor"]: t for t in verification["single_tests"]}

    binary = categorical_test(raw, "is_late_delivery", "is_low_score")
    assert by_factor["is_late_delivery"]["p"] == pytest.approx(binary["p"])
    assert by_factor["is_late_delivery"]["or"] == pytest.approx(binary["or"])

    raw["delay_rank"] = raw["delay_bucket"].map(
        {"按时": 0, "1-3天": 1, "4-7天": 2, "8-14天": 3, "15天+": 4}
    )
    trend = trend_test(raw, "delay_rank", "is_low_score")
    assert by_factor["delay_bucket(趋势)"]["p"] == pytest.approx(trend["p"])

    for factor in ("customer_state", "primary_category_name",
                   "primary_payment_type"):
        reference = chi_square_rc(raw, factor, "is_low_score")
        assert by_factor[factor]["p"] == pytest.approx(reference["p"])
        assert by_factor[factor]["cramers_v"] == reference["cramers_v"]


def test_run_attribution_has_verification(env):
    from agent_core.attribution import run_attribution
    semantic, provider = env
    res = run_attribution(provider, semantic)
    assert res["ok"]
    assert "verification" in res
    v = res["verification"]
    assert "single_tests" in v and "logistic" in v and "evidence" in v


def test_logistic_reports_result_or_explicit_degradation(env):
    semantic, provider = env
    v = verify_factors(provider, include_logistic=True)
    assert v["mode"] == "deep"
    assert v["logistic"]["enabled"]
    order = v["logistic"]["order"]
    if order.get("ok", True):
        late = next(t for t in order["terms"] if t["term"] == "is_late_delivery")
        assert late["or"] > 1
        assert late["p"] < 0.05
        assert late["ci95"][0] > 1
    else:
        assert order.get("note")
        assert "不报告调整 OR" in order["note"]
