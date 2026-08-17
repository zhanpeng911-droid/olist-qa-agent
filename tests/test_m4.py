"""证据边界 + 安全测试（确定性评测的核心断言）。

归因Agent只输出统计证据和对象定位，任何模型结果均不得生成策略。
安全：禁 DML / 无 JOIN / 未知指标与表拒绝 / 行数封顶
边界：无因果措辞 / 提示无评价正文 / 小样本过滤
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_core.attribution import run_attribution  # noqa: E402
from agent_core.data_provider import ProjectCsvProvider  # noqa: E402
from agent_core.semantic import SemanticLayer  # noqa: E402
from agent_core.tools import Tools  # noqa: E402

CAUSAL_WORDS = ["导致", "造成", "引起了"]
DML_WORDS = ["insert", "update", "delete", "drop", "truncate", "alter"]


@pytest.fixture(scope="module")
def env():
    semantic = SemanticLayer()
    provider = ProjectCsvProvider()
    tools = Tools(provider, semantic)
    attr = run_attribution(provider, semantic)
    yield semantic, provider, tools, attr
    provider.close()


# ---- 建议生成门槛 ----
def test_attribution_disables_strategy(env):
    _, _, _, attr = env
    rec = attr["recommendations"]
    assert rec["status"] == "disabled_evidence_only"
    assert rec["recommendations"] == []
    assert attr["deep_validation_plan"]


def test_lightweight_returns_validation_candidates_not_actions(env):
    _, _, _, attr = env
    candidates = attr["deep_validation_plan"]
    assert any(r["feature"] in {"is_late_delivery", "delay_bucket"}
               for r in candidates)
    assert all("actions" not in r and "responsibility" not in r
               for r in candidates)


def test_automatic_logistic_still_generates_no_strategy(env):
    semantic, provider, _, _ = env
    res = run_attribution(provider, semantic, include_logistic=True)
    assert res["verification"]["logistic"]["enabled"] is True
    assert res["recommendations"]["status"] == "disabled_evidence_only"
    assert res["recommendations"]["recommendations"] == []


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
