"""M4 测试：建议生成 + 安全 + 边界（确定性评测的核心断言）。

建议生成（recommend_actions / run_attribution.recommendations）：
- 结构完整、可执行、对应已验证证据、不凭空给未验证建议
安全：禁 DML / 无 JOIN / 未知指标与表拒绝 / 行数封顶
边界：无因果措辞 / 提示无评价正文 / 小样本过滤
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_core.attribution import run_attribution  # noqa: E402
from agent_core.data_provider import SampleProvider  # noqa: E402
from agent_core.recommendation import recommend_actions  # noqa: E402
from agent_core.semantic import SemanticLayer  # noqa: E402
from agent_core.tools import Tools  # noqa: E402

CAUSAL_WORDS = ["导致", "造成", "引起了"]
DML_WORDS = ["insert", "update", "delete", "drop", "truncate", "alter"]


@pytest.fixture(scope="module")
def env():
    semantic = SemanticLayer()
    provider = SampleProvider()
    tools = Tools(provider, semantic)
    attr = run_attribution(provider, semantic)
    yield semantic, provider, tools, attr
    provider.close()


# ---- 建议生成 ----
def test_recommendations_structure(env):
    _, _, _, attr = env
    recs = attr["recommendations"]["recommendations"]
    assert recs, "应有建议"
    for r in recs:
        assert {"responsibility", "actions", "monitor_metrics", "verify",
                "evidence_grade"} <= set(r)
        assert r["actions"] and r["monitor_metrics"] and r["verify"]


def test_recommendations_delay(env):
    _, _, _, attr = env
    recs = attr["recommendations"]["recommendations"]
    assert any("delay" in r["factor"] for r in recs), "应有延迟建议"


def test_recommendations_no_unverified(env):
    _, _, _, attr = env
    recs = attr["recommendations"]["recommendations"]
    # 样例中品类统计不显著，不应有品类建议
    assert not any("category" in r["factor"] for r in recs)


def test_recommend_actions_direct(env):
    semantic, provider, _, _ = env
    res = recommend_actions(provider, semantic)
    assert res["ok"]
    assert res["recommendations"]


def test_recommendations_no_causal_words(env):
    _, _, _, attr = env
    text = (attr.get("note", "") + " " + " ".join(attr.get("caveats", []))).lower()
    assert not any(w in text for w in CAUSAL_WORDS)


def test_recommendations_text_caveat(env):
    _, _, _, attr = env
    assert any("评价正文" in c for c in attr["caveats"])


# ---- 安全 ----
def test_no_dml_in_sql(env):
    _, _, tools, _ = env
    sql = tools.query_mart("mart_order_delivery",
                           metrics=["low_score_rate"])["sql"].lower()
    assert not any(w in sql for w in DML_WORDS)


def test_no_join_in_sql(env):
    _, _, tools, _ = env
    sql = tools.query_mart("mart_order_delivery",
                           metrics=["low_score_rate"])["sql"].lower()
    assert "join" not in sql


def test_reject_unknown_metric(env):
    _, _, tools, _ = env
    assert not tools.query_mart("mart_order_delivery",
                                metrics=["nope"])["ok"]


def test_reject_unknown_table(env):
    _, _, tools, _ = env
    assert not tools.query_mart("raw_orders", metrics=["low_score_rate"])["ok"]


def test_limit_capped(env):
    _, _, tools, _ = env
    r = tools.query_mart("mart_order_delivery", metrics=["low_score_rate"],
                         limit=99999999)
    assert r["sql"].lower().split("limit")[-1].strip() == "10000"


# ---- 小样本过滤 ----
def test_min_sample_filter(env):
    semantic, _, _, attr = env
    mn = semantic.guards["min_group_sample"]
    assert all(g["sample"] >= mn for g in attr["priorities"])
