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
# “为什么/原因/哪些因素”等归因词，需围绕三个受控目标才归因，否则交给开放式（LLM）
ATTRIBUTION_REASON = ["为什么", "原因", "哪些因素", "什么因素", "影响因素", "是什么导致", "导致"]
ATTRIBUTION_SCREENING = ["筛查", "筛选", "关联因素"]
ATTRIBUTION_TARGET_THEME = (
    "低评分", "低分", "差评", "评分", "延迟", "延误", "晚到", "星级",
    "交接超期", "晚交接", "发货超期", "揽收超期",
)

WRITE_ACTION_HINTS = (
    "删除数据库", "删除表", "删表", "清空数据库", "清空表", "重建数据库",
    "重新建表", "重建表", "创建表", "修改表", "写入数据库", "更新数据库",
    "drop database", "drop table", "truncate table", "delete from",
    "create table", "alter table", "insert into", "update ",
)

WRITE_VERBS = ("删除", "清空", "重建", "创建", "修改", "写入", "更新", "插入", "覆盖")
WRITE_OBJECTS = ("数据库", "数据表", "表结构", "表", "字段", "列", "订单", "记录", "数据", "索引", "视图")


def is_write_request(question: str) -> bool:
    """识别超出只读分析权限的数据库变更请求。"""
    q = question.lower().strip()
    if any(hint in q for hint in WRITE_ACTION_HINTS):
        return True
    # 自然语言通常不会写出完整 SQL，例如“删除最近一个月的订单记录”。
    # 同时要求出现写动作和数据对象，避免把“如何修改分析方法”等问题误判为数据写入。
    return any(verb in q for verb in WRITE_VERBS) and any(obj in q for obj in WRITE_OBJECTS)


class Intent:
    """轻量意图识别。"""

    def __init__(self, semantic: SemanticLayer) -> None:
        self._s = semantic

    def classify(self, question: str) -> str:
        # 用户明确写“深度验证”时优先进入补充验证。
        if "深度验证" in question:
            return "deep_validation"
        # 明确写“归因/优先治理/改善建议”→ 完整两层归因。归因结果本身会包含
        # “调整后验证”，不能仅因这几个字再次误路由到补充验证模块。
        if any(h in question for h in ATTRIBUTION_ACTION):
            if is_statistical_question(question):
                return "statistical"
            return "attribution"
        # 未明确写“归因”，但出现调整后、控制混杂、留出验证等表达时进入补充验证。
        if is_deep_validation_question(question):
            return "deep_validation"
        # “筛查低评分关联因素”属于受控归因筛选，不应被“关联”二字截成双变量统计。
        if any(h in question for h in ATTRIBUTION_SCREENING) \
                and any(t in question for t in ATTRIBUTION_TARGET_THEME):
            return "attribution"
        # 统计问题
        if is_statistical_question(question):
            return "statistical"
        # “为什么/哪些因素”等：只有围绕受控目标才归因；否则开放式（LLM 解释）
        if any(h in question for h in ATTRIBUTION_REASON):
            if any(t in question for t in ATTRIBUTION_TARGET_THEME):
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
