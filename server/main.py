"""正式 UI 后端：FastAPI 包装 agent_core，提供 REST + SSE 流式 API。

- 数据源固定后端配置：默认 ProjectCsvProvider（data/sample）；设 USE_MYSQL=1 用 MySQLProvider（读 .env DB_*）
- SSE /api/chat：分事件推送 intent → running → result/step → answer → done
- 生产模式：web/dist 存在时托管静态前端（单端口）
"""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from agent_core.attribution import run_attribution  # noqa: E402
from agent_core.data_provider import MySQLProvider, ProjectCsvProvider  # noqa: E402
from agent_core.deep_validation import analyze_deep_validation  # noqa: E402
from agent_core.intent import Intent  # noqa: E402
from agent_core.llm import MockLLM, create_llm  # noqa: E402
from agent_core.loop import ReActLoop  # noqa: E402
from agent_core.query_analysis import analyze_query_question  # noqa: E402
from agent_core.semantic import SemanticLayer  # noqa: E402
from agent_core.statistical_analysis import analyze_statistical_question  # noqa: E402

app = FastAPI(title="Olist 智能问数 Agent API", version="2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- 归因结果缓存（全量统计耗时长，缓存避免重复计算） ----------
_ATTR_CACHE: dict[str, tuple[float, dict]] = {}
_ATTR_CACHE_TTL = 300          # 5 分钟


def _cached_attribution(question: str) -> dict:
    key = question or "default"
    now = time.time()
    hit = _ATTR_CACHE.get(key)
    if hit and now - hit[0] < _ATTR_CACHE_TTL:
        return hit[1]
    provider = get_provider()
    try:
        res = run_attribution(provider, get_semantic(), question=question or None)
    finally:
        provider.close()
    _ATTR_CACHE[key] = (now, res)
    return res


# ---------- 数据源（固定后端配置） ----------
def get_semantic() -> SemanticLayer:
    return SemanticLayer()


def get_provider():
    """按后端配置创建数据源：USE_MYSQL=1 用 MySQL，否则演示样本 CSV。"""
    if os.environ.get("USE_MYSQL") == "1":
        try:
            return MySQLProvider(allow_tables=get_semantic().allowed_tables())
        except Exception as e:
            raise RuntimeError(f"MySQL 连接失败: {e}（USE_MYSQL=1 但连接不可用）")
    return ProjectCsvProvider()


def _clean(obj):
    """递归把 numpy 标量 / datetime / 非有限浮点（inf/nan）转为 JSON 可表达类型。

    注意：json.dumps 的 default 回调不会处理 inf（inf 是合法 float，dumps 直接抛错），
    因此必须在序列化前递归清理。
    """
    if hasattr(obj, "item"):          # numpy 标量 → Python 标量
        obj = obj.item()
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None   # inf/nan → null
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (int, bool)) or obj is None:
        return obj
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)


def _json(res: dict) -> JSONResponse:
    return JSONResponse(content=_clean(res))


class QuestionBody(BaseModel):
    question: str


# ---------- REST API ----------
@app.post("/api/intent")
def api_intent(body: QuestionBody):
    return {"intent": Intent(get_semantic()).classify(body.question)}


@app.post("/api/query")
def api_query(body: QuestionBody):
    provider = get_provider()
    try:
        return _json(analyze_query_question(provider, get_semantic(), body.question))
    finally:
        provider.close()


@app.post("/api/statistical")
def api_statistical(body: QuestionBody):
    provider = get_provider()
    try:
        return _json(analyze_statistical_question(provider, body.question))
    finally:
        provider.close()


@app.post("/api/attribution")
def api_attribution(body: QuestionBody):
    return _json(_cached_attribution(body.question or ""))


@app.post("/api/deep-validation")
def api_deep_validation(body: QuestionBody):
    provider = get_provider()
    try:
        return _json(analyze_deep_validation(provider, body.question))
    finally:
        provider.close()


@app.get("/api/meta")
def api_meta():
    s = get_semantic()
    provider_label = "MySQL" if os.environ.get("USE_MYSQL") == "1" else "演示样本(CSV)"
    return _json({
        "source_label": provider_label,
        "tables": {
            t: {
                "desc": s.tables[t].get("desc", ""),
                "metrics": s.get_metrics(t),
                "dimensions": s.get_dimensions(t),
                "filters": s.get_filters(t),
            }
            for t in s.table_names()
        },
        "guards": s.guards,
    })


# ---------- SSE 流式对话 ----------
def _sse(event: str, data) -> str:
    payload = json.dumps(_clean(data), ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


@app.post("/api/chat")
async def api_chat(body: QuestionBody):
    async def gen() -> AsyncGenerator[str, None]:
        semantic = get_semantic()
        intent = Intent(semantic).classify(body.question)
        yield _sse("intent", {"intent": intent})
        provider = get_provider()
        try:
            if intent == "query":
                yield _sse("running", {"stage": "执行指标查询…"})
                res = analyze_query_question(provider, semantic, body.question)
                yield _sse("result", res)
            elif intent == "statistical":
                yield _sse("running", {"stage": "进行统计检验…"})
                res = analyze_statistical_question(provider, body.question)
                yield _sse("result", res)
            elif intent == "attribution":
                yield _sse("running", {"stage": "进行低评分关联因素分析…"})
                res = _cached_attribution(body.question)
                yield _sse("result", res)
            elif intent == "deep_validation":
                yield _sse("running", {"stage": "进行深度验证…"})
                res = analyze_deep_validation(provider, body.question)
                yield _sse("result", res)
            else:
                yield _sse("running", {"stage": "模型推理中…"})
                try:
                    llm = create_llm()
                except RuntimeError as e:
                    llm = MockLLM(
                        tool_call={"tool": "query_mart",
                                   "args": {"table": "mart_order_delivery",
                                            "metrics": ["low_score_rate"]}},
                        answer="（未配置 DEEPSEEK_API_KEY，Mock 演示）已查询低评分率。")
                    yield _sse("warning", {"message": str(e)})
                loop = ReActLoop(llm, provider, semantic)
                res = loop.run(body.question)
                for t in res.get("trace", []):      # 回放工具执行过程
                    yield _sse("step", t)
                yield _sse("answer", {"answer": res.get("answer", ""),
                                      "ok": res.get("ok", False)})
        except Exception as e:
            yield _sse("error", {"error": str(e), "type": type(e).__name__})
        finally:
            provider.close()
        yield _sse("done", {})

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------- 生产：托管前端静态产物 ----------
_WEB_DIST = ROOT / "web" / "dist"
if _WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=str(_WEB_DIST), html=True), name="web")
