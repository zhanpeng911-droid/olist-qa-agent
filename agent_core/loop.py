"""自建 ReAct 循环（M1）。

流程：观察问题 → 由 LLM 决策工具调用 → 执行工具 → 观察结果回喂 → 再决策，
直到 LLM 输出最终答案，或达到最大步数。工具报错/输出格式错误时进行反思重试。

LLM 输出协议（JSON）：
  {"action": "tool",   "tool": "<工具名>", "args": {...}}
  {"action": "answer", "content": "..."}
"""
from __future__ import annotations

import json

from .attribution import run_attribution
from .data_provider import DataProvider
from .llm import LLMClient
from .semantic import SemanticLayer
from .tools import Tools

MAX_STEPS = 5
MAX_OBS_CHARS = 1500          # 工具结果回喂时截断，控制上下文


def _fmt_observation(result: dict) -> str:
    """把工具返回 dict 压成可回喂的文本（含 SQL 便于对账）。"""
    if not result.get("ok"):
        return f"[工具错误] {result.get('error', '未知错误')}"

    # 归因流程结果：输出基准 + 优先级摘要
    if "priorities" in result:
        base = result["baseline"]
        lines = [
            "【低评分归因结果】",
            f"订单级基准: 样本 {base['order']['sample']}，低评分率 "
            f"{base['order']['low_score_rate']:.2%}",
            f"卖家级基准(单卖家): 样本 {base['seller']['sample']}，低评分率 "
            f"{base['seller']['low_score_rate']:.2%}",
            "优先级(P0/P1/P2) Top:",
        ]
        for g in result["priorities"][:8]:
            lines.append(
                f"  {g['priority']} [{g['dimension']}={g['value']}] "
                f"样本{g['sample']} 率{g['low_score_rate']:.2%} "
                f"Lift{g['lift']:.2f} 超额{g['excess_low_score']:.0f}"
            )
        lines.append("注: " + " ".join(result.get("caveats", [])))
        return "\n".join(lines)

    parts = []
    if result.get("sql"):
        parts.append(f"SQL: {result['sql']}")
    rows = result.get("rows", [])
    parts.append(f"共 {result.get('row_count', len(rows))} 行")
    for r in rows[:10]:
        parts.append(str(r))
    text = "\n".join(parts)
    return text[:MAX_OBS_CHARS]


class ReActLoop:
    """执行 ReAct 循环，封装工具调度与反思。"""

    def __init__(
        self,
        llm: LLMClient,
        provider: DataProvider,
        semantic: SemanticLayer,
        max_steps: int = MAX_STEPS,
    ) -> None:
        self._llm = llm
        self._provider = provider
        self._tools = Tools(provider, semantic)
        self._semantic = semantic
        self._max_steps = max_steps
        self._tool_registry = {
            "query_mart": self._tools.query_mart,
            "top_n": self._tools.top_n,
            "list_metrics": self._tools.list_metrics,
            "list_dimensions": self._tools.list_dimensions,
            "run_attribution": self._run_attribution,
        }

    def _run_attribution(self) -> dict:
        """执行低评分描述性归因（固定顺序流程，无需参数）。"""
        return run_attribution(self._provider, self._semantic)

    def _system_prompt(self) -> str:
        return (
            "你是 Olist 电商履约分析的数据分析师 agent。\n"
            "可用的 mart 表与指标、维度如下（口径已由语义字典锁死，只能选这些，不能自创）：\n\n"
            + self._semantic.describe_all()
            + "\n\n可调用工具（输出 JSON）:\n"
            "  {\"action\":\"tool\",\"tool\":\"query_mart\",\"args\":{\"table\":\"...\",\"metrics\":[...],\"dimensions\":[...],\"filters\":{...},\"order_by\":\"...\",\"limit\":N}}\n"
            "  {\"action\":\"tool\",\"tool\":\"top_n\",\"args\":{\"table\":\"...\",\"metric\":\"...\",\"dimension\":\"...\",\"n\":N}}\n"
            "  {\"action\":\"tool\",\"tool\":\"run_attribution\",\"args\":{}}\n"
            "当用户要求对低评分做归因/找原因/优先治理时，调用 run_attribution（无需参数，自动完成订单级+卖家级扫描与优先级排序）。\n"
            "  {\"action\":\"tool\",\"tool\":\"list_metrics\",\"args\":{\"table\":\"...\"}}\n"
            "当已获得足够数据时，输出最终答案："
            "{\"action\":\"answer\",\"content\":\"你的结论，引用数字并注明来源 SQL 以保证可对账\"}\n"
            "只允许输出上述 JSON，不要输出其它文本。"
        )

    def run(self, question: str) -> dict:
        messages = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": question},
        ]
        trace: list[dict] = []
        steps = 0

        while steps < self._max_steps:
            steps += 1
            reply = self._llm.chat(messages)
            try:
                decision = json.loads(reply)
            except json.JSONDecodeError:
                # 反思：格式错误，要求重试
                messages.append(
                    {"role": "user",
                     "content": "你的输出不是合法 JSON，请严格按协议重新输出工具调用或答案。"}
                )
                trace.append({"step": steps, "event": "format_error", "reply": reply})
                continue

            if decision.get("action") == "answer":
                trace.append({"step": steps, "event": "answer", "reply": reply})
                return {"answer": decision.get("content", ""), "trace": trace,
                        "steps": steps, "ok": True}

            if decision.get("action") == "tool":
                tool = decision.get("tool")
                args = decision.get("args", {}) or {}
                fn = self._tool_registry.get(tool)
                if fn is None:
                    messages.append(
                        {"role": "user",
                         "content": f"未知工具 {tool}，可用工具: {list(self._tool_registry)}，请重试。"}
                    )
                    trace.append({"step": steps, "event": "unknown_tool", "tool": tool})
                    continue
                try:
                    result = fn(**args)
                except TypeError as e:
                    result = {"ok": False, "error": f"参数错误: {e}"}
                obs = _fmt_observation(result)
                messages.append({"role": "assistant", "content": reply})
                messages.append({"role": "user", "content": f"[工具 {tool} 结果]\n{obs}"})
                trace.append({"step": steps, "event": "tool", "tool": tool,
                              "args": args, "ok": result.get("ok", False)})
                continue

            messages.append(
                {"role": "user", "content": "无法识别 action，请输出合法的工具调用或答案 JSON。"}
            )
            trace.append({"step": steps, "event": "bad_action", "reply": reply})

        return {"answer": "", "trace": trace, "steps": steps,
                "ok": False, "error": "达到最大步数仍未给出答案"}
