"""Demo UI 测试：验证 ui/app.py 的纯展示函数（不启动 Streamlit）。

确保归因结果的展示数据准备正确，且模块可导入。
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent_core.attribution import run_attribution  # noqa: E402
from agent_core.data_provider import SampleProvider  # noqa: E402
from agent_core.semantic import SemanticLayer  # noqa: E402
from ui.app import (  # noqa: E402
    build_priority_df, build_recommendation_lines, build_route_summary,
    build_verification_summary, is_attribution_question,
)


@pytest.fixture(scope="module")
def attr():
    semantic = SemanticLayer()
    provider = SampleProvider()
    yield run_attribution(provider, semantic)
    provider.close()


def test_is_attribution_question():
    assert is_attribution_question("对低评分进行归因")
    assert is_attribution_question("为什么低评分高")
    assert not is_attribution_question("总体延迟率是多少")


def test_build_priority_df(attr):
    df = build_priority_df(attr)
    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    assert {"优先级", "维度", "对象", "样本量", "Lift"} <= set(df.columns)


def test_build_route_summary(attr):
    lines = build_route_summary(attr)
    assert lines, "应有 route 摘要"


def test_build_verification_summary(attr):
    lines = build_verification_summary(attr)
    assert any("强证据" in l for l in lines), "应有强证据摘要"


def test_build_recommendation_lines(attr):
    lines = build_recommendation_lines(attr)
    assert lines, "应有建议行"
    assert all("责任方" in l and "监控" in l and "验证" in l for l in lines)
