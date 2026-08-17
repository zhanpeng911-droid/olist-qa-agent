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
ATTRIBUTION_HINTS = ["归因", "为什么", "原因", "优先治理", "改善建议", "哪个因素"]


class Intent:
    """轻量意图识别。"""

    def __init__(self, semantic: SemanticLayer) -> None:
        self._s = semantic

    def classify(self, question: str) -> str:
        # “低评分归因”现在本身就会自动完成单变量筛查与调整模型。
        # 因此用户明确写出“归因”时，不应再被“调整后/控制混杂”等泛化
        # 深度词抢占；只有明确写“深度验证”才进入独立补充验证入口。
        if "深度验证" in question:
            return "deep_validation"
        if "归因" in question:
            if is_statistical_question(question):
                return "statistical"
            return "attribution"
        if is_deep_validation_question(question):
            return "deep_validation"
        if is_statistical_question(question):
            return "statistical"
        if any(h in question for h in ATTRIBUTION_HINTS):
            return "attribution"   # 归因诊断（L2）
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
