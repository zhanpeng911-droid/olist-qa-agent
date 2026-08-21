"""正式 UI 后端 API 测试（FastAPI TestClient）。

覆盖：/api/intent、/api/query、/api/statistical、/api/attribution、/api/deep-validation、/api/meta、/api/chat(SSE)。
"""
import os
import sys
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# API 回归固定使用截取样本，避免本机 .env 的 USE_MYSQL=1
# 让单元测试依赖数据库状态；MySQL 连通性另做独立集成检查。
os.environ["USE_MYSQL"] = "0"

from server.main import _clean, app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_api_intent(client):
    r = client.post("/api/intent", json={"question": "对低评分进行归因"})
    assert r.status_code == 200
    assert r.json()["intent"] == "attribution"
    r2 = client.post("/api/intent", json={"question": "总体低评分率是多少"})
    assert r2.json()["intent"] == "query"
    for question in ("请对延迟进行归因", "哪些因素与交接超期有关？"):
        routed = client.post("/api/intent", json={"question": question})
        assert routed.json()["intent"] == "attribution"
    assert client.post("/api/intent", json={
        "question": "从履约、地区、线路、品类和支付角度筛查低评分关联因素。",
    }).json()["intent"] == "attribution"
    assert client.post("/api/intent", json={
        "question": "用较晚时期订单验证高风险线路。",
    }).json()["intent"] == "deep_validation"
    assert client.post("/api/intent", json={
        "question": "请对低评分进行多维归因，完成单变量筛选和调整后验证。",
    }).json()["intent"] == "attribution"


def test_clean_decimal_as_json_number():
    from decimal import Decimal

    assert _clean(Decimal("1.25")) == 1.25
    assert isinstance(_clean(Decimal("1.25")), float)


def test_api_meta(client):
    r = client.get("/api/meta")
    assert r.status_code == 200
    meta = r.json()
    assert "tables" in meta and "mart_order_delivery" in meta["tables"]
    assert "guards" in meta


def test_api_dashboard_contains_comparable_kpis_and_trends(client):
    response = client.get("/api/dashboard")
    assert response.status_code == 200
    payload = response.json()
    for key in (
        "late_rate", "late_sample", "handover_late_rate", "handover_sample",
        "avg_review_score", "avg_score_sample", "trend", "late_trend",
        "handover_trend",
    ):
        assert key in payload
    assert payload["trend"]
    assert payload["late_trend"]
    assert payload["handover_trend"]


def test_api_query(client):
    r = client.post("/api/query", json={"question": "总体低评分率是多少"})
    assert r.status_code == 200
    res = r.json()
    assert res.get("ok") or res.get("recognized")
    assert "answer" in res or "rows" in res


def test_api_statistical(client):
    r = client.post("/api/statistical",
                    json={"question": "延迟和低评分是否相关"})
    assert r.status_code == 200
    res = r.json()
    assert "p" in res or "conclusion" in res or res.get("ok") is not None


def test_api_attribution(client):
    r = client.post("/api/attribution",
                    json={"question": "对低评分进行归因"})
    assert r.status_code == 200
    res = r.json()
    assert res.get("ok") is True
    for key in ("baseline", "priorities", "verification"):
        assert key in res

    expected = {
        "请对延迟进行归因": "is_late_delivery",
        "哪些因素与交接超期有关？": "is_any_item_handover_late",
    }
    for question, target in expected.items():
        routed = client.post("/api/attribution", json={"question": question})
        assert routed.status_code == 200
        payload = routed.json()
        assert payload.get("ok") is True
        assert payload.get("target") == target
        assert "target_baseline" in payload
        assert payload.get("recommendations", {}).get("recommendations") == []


def test_api_deep_validation(client):
    r = client.post("/api/deep-validation",
                    json={"question": "对延迟和品类进行深度验证"})
    assert r.status_code == 200
    res = r.json()
    # 深度验证可能因数据不足返回 not_estimated，但结构应存在
    assert isinstance(res, dict)


def test_api_chat_sse(client):
    with client.stream("POST", "/api/chat",
                       json={"question": "总体低评分率是多少"}) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert "event: intent" in body
    assert "event: result" in body or "event: answer" in body
    assert "event: done" in body


def test_api_chat_batch_statistical_stays_deterministic(client):
    question = (
        "是否延迟与品类、运费率、商品项数量、是否多卖家订单、是否跨周、"
        "是否存在交接超期、线路分别有显著关系"
    )
    with client.stream("POST", "/api/chat", json={"question": question}) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert "event: result" in body
    assert '"batch": true' in body
    assert '"comparison_count": 7' in body
    assert "event: answer" not in body
    assert "APIConnectionError" not in body


@pytest.mark.parametrize(("question", "expected"), [
    ("低评分率与天气是否显著相关？", "未识别要检验的因素"),
    ("支付方式与配送线路是否有关联？", "不在同一受控分析粒度"),
    ("请对复购进行归因分析。", "三个目标"),
    ("请删除数据库并重新建表。", "仅支持只读数据分析"),
    ("请删除最近一个月的订单记录。", "仅支持只读数据分析"),
])
def test_api_chat_controlled_boundaries_never_fall_back_to_llm(client, question, expected):
    with client.stream("POST", "/api/chat", json={"question": question}) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())
    assert "event: answer" in body
    assert expected in body
    assert "APIConnectionError" not in body
    assert "event: step" not in body


def test_write_detection_does_not_block_analysis_method_question():
    from agent_core.intent import is_write_request

    assert is_write_request("如何修改低评分归因的分析方法？") is False
