"""统计问题入口（兼容层）。

通用双变量统计检验已迁移到 bivariate_analysis.py。本模块保留原导入路径兼容，
外部代码 ``from agent_core.statistical_analysis import analyze_statistical_question``
等仍可直接使用。真正的实现与口径唯一来源见 bivariate_analysis.py 与 statistics.py。
"""
from .bivariate_analysis import (  # noqa: F401
    VARIABLE_SPECS,
    analyze_statistical_question,
    format_statistical_result,
    is_statistical_question,
    plan_statistical_question,
    supported_variables,
)
