"""LLM 客户端抽象。

- DeepSeekLLM: 通过 OpenAI 兼容接口调用 DeepSeek（读取 DEEPSEEK_API_KEY 环境变量）
- MockLLM:    无 key 时用于验证 ReAct 流程（返回预设的工具调用/答案）

工厂 create_llm() 无 key 时自动回退到 MockLLM，保证 M1 骨架可独立运行。
"""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod


class LLMClient(ABC):
    """大模型客户端接口（对话补全）。"""

    @abstractmethod
    def chat(self, messages: list[dict]) -> str:
        """传入 [{role, content}, ...]，返回助手回复文本。"""


class MockLLM(LLMClient):
    """测试用实现：按消息轮次返回预设输出，验证 ReAct 端到端流程。

    第一轮（尚未执行过工具）返回一个工具调用，之后返回最终答案。
    """

    def __init__(self, tool_call: dict, answer: str) -> None:
        self._tool_call = tool_call
        self._answer = answer
        self.calls = 0

    def chat(self, messages: list[dict]) -> str:
        self.calls += 1
        if self.calls == 1:
            return json.dumps(
                {"action": "tool", "tool": self._tool_call["tool"],
                 "args": self._tool_call["args"]},
                ensure_ascii=False,
            )
        return json.dumps({"action": "answer", "content": self._answer},
                          ensure_ascii=False)


class DeepSeekLLM(LLMClient):
    """通过 OpenAI SDK 调用 DeepSeek（其 API 兼容 OpenAI 协议）。

    需要环境变量 DEEPSEEK_API_KEY；可选 DEEPSEEK_BASE_URL / DEEPSEEK_MODEL。
    """

    def __init__(self, model: str | None = None, timeout: float | None = None) -> None:
        import openai  # 延迟导入，避免无 key 时强依赖

        key = os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise ValueError("缺少 DEEPSEEK_API_KEY 环境变量")
        base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        self._model = model or os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        request_timeout = timeout or float(os.environ.get("DEEPSEEK_TIMEOUT_SECONDS", "45"))
        self._client = openai.OpenAI(
            api_key=key,
            base_url=base_url,
            timeout=request_timeout,
            max_retries=1,
        )

    def chat(self, messages: list[dict]) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=0.2,
        )
        return resp.choices[0].message.content or ""


def create_llm() -> LLMClient:
    """返回 DeepSeekLLM；未配置 key 时回退到可用的 MockLLM（需预设）。"""
    if os.environ.get("DEEPSEEK_API_KEY"):
        return DeepSeekLLM()
    raise RuntimeError(
        "未配置 DeepSeek API 密钥（DEEPSEEK_API_KEY），无法调用大模型"
    )
