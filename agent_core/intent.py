"""意图识别骨架（M1 轻量版）。

M1 聚焦问数流程，这里提供：
- 意图分类：区分「问数 / 口径询问 / 其他」
- 简单的指标/维度关键词映射（用于演示与辅助，非强制）

真正的参数抽取在 ReAct 循环中由 LLM 依据语义字典完成。
"""
from __future__ import annotations

from .deep_validation import is_deep_validation_question
from .semantic import SemanticLayer
from .statistical_analysis import is_statistical_question

QUERY_HINTS = [
    "多少", "率", "最高", "最低", "top", "排名", "对比", "分布",
    "趋势", "延迟", "评分", "评分率", "金额", "订单", "占比", "拆解",
    "用时", "几天", "多久", "晚到", "低分", "三星", "前五", "前十",
]
META_HINTS = ["口径", "含义", "定义", "指标是什么意思", "怎么算"]
# 明确归因动作
ATTRIBUTION_ACTION = ["归因", "优先治理", "改善建议"]
# “为什么/原因/哪些因素”等归因词，需围绕低评分主题才归因，否则交给开放式（LLM）
ATTRIBUTION_REASON = ["为什么", "原因", "哪些因素", "什么因素", "影响因素", "是什么导致", "导致"]
LOWSCORE_THEME = ("低评分", "低分", "评分", "延迟", "晚到", "星级")


class Intent:
    """轻量意图识别。"""

    def __init__(self, semantic: SemanticLayer) -> None:
        self._s = semantic

    def classify(self, question: str) -> str:
        # 明确写“归因/优先治理/改善建议”→ 低评分归因
        if any(h in question for h in ATTRIBUTION_ACTION):
            if is_statistical_question(question):
                return "statistical"
            return "attribution"
        # 深度验证
        if "深度验证" in question or is_deep_validation_question(question):
            return "deep_validation"
        # 统计问题
        if is_statistical_question(question):
            return "statistical"
        # “为什么/哪些因素”等：只有围绕低评分主题才归因；否则开放式（LLM 解释）
        if any(h in question for h in ATTRIBUTION_REASON):
            if any(t in question for t in LOWSCORE_THEME):
                return "attribution"
            return "other"
        if any(h in question for h in META_HINTS):
            return "meta"          # 口径询问
        if any(h in question.lower() for h in QUERY_HINTS):
            return "query"         # 数据问数
        return "other"

    def suggest_params(self, question: str, table: str) -> dict:
        """从问题中尽量抽取指标名（命中语义字典则返回）。"""
        metrics = []
        for name in self._s.get_metrics(table):
            desc = self._s.get_metrics(table)[name].get("desc", "")
            if name in question or desc in question:
                metrics.append(name)
        return {"metrics": metrics}
