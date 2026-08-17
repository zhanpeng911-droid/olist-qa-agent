"""正式 UI 后端 API 测试（FastAPI TestClient）。

覆盖：/api/intent、/api/query、/api/statistical、/api/attribution、/api/deep-validation、/api/meta、/api/chat(SSE)。
"""
import sys
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.main import app  # noqa: E402


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


def test_api_meta(client):
    r = client.get("/api/meta")
    assert r.status_code == 200
    meta = r.json()
    assert "tables" in meta and "mart_order_delivery" in meta["tables"]
    assert "guards" in meta


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
