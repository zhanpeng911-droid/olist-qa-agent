"""自建 ReAct 循环（M1）。

流程：观察问题 → 由 LLM 决策工具调用 → 执行工具 → 观察结果回喂 → 再决策，
直到 LLM 输出最终答案，或达到最大步数。工具报错/输出格式错误时进行反思重试。

LLM 输出协议（JSON）：
  {"action": "tool",   "tool": "<工具名>", "args": {...}}
  {"action": "answer", "content": "..."}
"""
from __future__ import annotations

import json
import re

from .attribution import run_attribution
from .data_provider import DataProvider
from .llm import LLMClient
from .semantic import SemanticLayer
from .tools import Tools

MAX_STEPS = 5
MAX_OBS_CHARS = 1500          # 工具结果回喂时截断，控制上下文


def parse_decision(reply: str) -> dict:
    """容忍 Markdown 代码围栏或 JSON 前后有少量说明文字。"""
    text = (reply or "").strip()
    if not text:
        raise json.JSONDecodeError("empty response", text, 0)
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text,
                       flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise json.JSONDecodeError("decision must be an object", text, 0)
    return value


def _fmt_observation(result: dict) -> str:
    """把工具返回 dict 压成可回喂的文本（含 SQL 便于对账）。"""
    if not result.get("ok"):
        return f"[工具错误] {result.get('error', '未知错误')}"

    # 关联因素分析结果：输出基准 + 优先级摘要
    if "priorities" in result:
        base = result["baseline"]
        lines = [
            "【低评分关联因素分析】",
            f"订单级基准: 样本 {base['order']['sample']}，低评分率 "
            f"{base['order']['low_score_rate']:.2%}",
            f"卖家级基准(单卖家): 样本 {base['seller']['sample']}，低评分率 "
            f"{base['seller']['low_score_rate']:.2%}",
            "描述性问题对象（P0/P1/P2，P0为最高排查优先级）:",
        ]
        for g in result["priorities"][:8]:
            lines.append(
                f"  {g['priority']} [{g['dimension']}={g['value']}] "
                f"样本{g['sample']} 率{g['low_score_rate']:.2%} "
                f"相对总体倍数{g['lift']:.2f} "
                f"高于总体水平的预计低评分数{g['excess_low_score']:.0f}"
            )
        lines.append("统计显著特征:")
        for test in result.get("significant_features", [])[:6]:
            lines.append(
                f"  {test['label']}: {test['method']} "
                f"p={test['p_used']:.3g} ({test['p_basis']})"
            )
        item_sig = (
            result.get("item_drilldown", {}).get("significance", {})
            .get("category", {}).get("significant_risk", [])
        )
        if item_sig:
            lines.append("商品品类显著对象:")
            for row in item_sig[:5]:
                lines.append(
                    f"  {row['value']}: 优势比（OR）={row['or']:.3g} "
                    f"FDR-p={row['p_adjusted']:.3g}"
                )
        lines.append("后续多变量验证:")
        for item in result.get("deep_validation_plan", [])[:4]:
            lines.append(
                f"  {item['label']}: {item['recommended_method']}"
            )
        lines.append("结论边界: 结果仅表示统计关联，不作因果判断或自动生成治理策略。")
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
        """执行低评分关联因素分析（固定顺序流程，无需参数）。"""
        return run_attribution(self._provider, self._semantic)

    def _system_prompt(self) -> str:
        return (
            "你是 Olist 电商履约分析的数据分析师 agent。\n"
            "可用的 mart 表与指标、维度如下（口径已由语义字典锁死，只能选这些，不能自创）：\n\n"
            + self._semantic.describe_all()
            + "\n\n可调用工具（输出 JSON）:\n"
            "  {\"action\":\"tool\",\"tool\":\"query_mart\",\"args\":{\"table\":\"...\",\"metrics\":[...],\"dimensions\":[...],\"filters\":{...},\"order_by\":\"...\",\"limit\":N}}\n"
            "query_mart 的 filters 只能使用该表已列出的维度，格式只能是 {\"列\": 值} 或 "
            "{\"列\": {\"op\": \"=|!=|>|>=|<|<=|IN|NOT IN\", \"value\": 值}}；"
            "不要省略 value。无筛选时省略 filters。\n"
            "order_by 可写本次指标或维度名，并可追加 ASC/DESC；多字段用逗号分隔，例如 "
            "late_rate DESC, low_score_rate DESC。\n"
            "  {\"action\":\"tool\",\"tool\":\"top_n\",\"args\":{\"table\":\"...\",\"metric\":\"...\",\"dimension\":\"...\",\"n\":N}}\n"
            "表选择规则：普通品类汇总使用 mart_order_delivery.primary_category_name；只有明确要求商品项、SKU、"
            "具体商品或商品项运费时才使用 mart_order_item_analysis。卖家州、线路、跨州/同州使用 "
            "mart_order_seller_delivery；线路必须优先直接使用 route 维度，不要拆成 seller_state 与 customer_state。"
            "排名问题优先直接调用 top_n。\n"
            "只查询回答问题所必需的最少指标与维度，不要自行追加无关指标。\n"
            "一次成功工具结果已经包含用户要求的指标和维度时，下一步必须输出 answer；"
            "不要为了改换排序、补取全部分组或重复核对而再次执行等价查询。\n"
            "  {\"action\":\"tool\",\"tool\":\"run_attribution\",\"args\":{}}\n"
            "当用户要求分析低评分关联因素或进行低评分归因时，调用 run_attribution（无需参数，自动完成订单级与订单-卖家级分析）。\n"
            "run_attribution 会完成单变量筛选、共线性处理和多变量Logistic调整；"
            "必须区分统计关联与因果关系，不得自动生成治理策略。\n"
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
            try:
                reply = self._llm.chat(messages)
            except Exception as exc:
                error = (
                    f"模型调用失败（{type(exc).__name__}）。请检查网络/API 配置后重试；"
                    "本次没有执行未确认的数据操作。"
                )
                trace.append({"step": steps, "event": "llm_error",
                              "error_type": type(exc).__name__})
                return {"answer": error, "trace": trace, "steps": steps,
                        "ok": False, "error": error}
            try:
                decision = parse_decision(reply)
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
                content = str(decision.get("content") or "").strip()
                if content:
                    return {"answer": content, "trace": trace,
                            "steps": steps, "ok": True}
                messages.append(
                    {"role": "user", "content": "答案内容为空。请根据已有工具结果给出非空结论。"}
                )
                trace.append({"step": steps, "event": "empty_answer"})
                continue

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
                    if not isinstance(args, dict):
                        raise TypeError("args 必须是 JSON 对象")
                    result = fn(**args)
                except TypeError as exc:
                    result = {"ok": False, "error": f"参数错误: {exc}"}
                except Exception as exc:
                    # 模型参数不能让整次问答或批量评测进程崩溃；错误会回喂给模型反思。
                    result = {
                        "ok": False,
                        "error": f"工具执行失败（{type(exc).__name__}）: {exc}",
                    }
                obs = _fmt_observation(result)
                messages.append({"role": "assistant", "content": reply})
                next_step = (
                    "\n[下一步约束] 若结果已包含问题要求的指标和维度，请立即输出 answer；"
                    "不要重复或改写等价查询。"
                    if result.get("ok") else ""
                )
                messages.append({
                    "role": "user",
                    "content": f"[工具 {tool} 结果]\n{obs}{next_step}",
                })
                trace.append({"step": steps, "event": "tool", "tool": tool,
                              "args": args, "ok": result.get("ok", False),
                              "error": result.get("error")})
                continue

            messages.append(
                {"role": "user", "content": "无法识别 action，请输出合法的工具调用或答案 JSON。"}
            )
            trace.append({"step": steps, "event": "bad_action", "reply": reply})

        error = "达到最大步数仍未给出有效答案；请缩小问题范围或重试。"
        return {"answer": error, "trace": trace, "steps": steps,
                "ok": False, "error": error}
