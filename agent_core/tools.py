"""工具层：把结构化参数翻译成安全 SQL 并执行。

核心是 query_mart —— 模型通过结构化参数（指标/维度/筛选/排序/limit）调用，
SQL 由这里用模板生成，而不是让模型自由书写，从而杜绝语法错误与 join 风险。
口径由语义字典锁死：指标表达式必须来自 metrics_dict.yaml。
"""
from __future__ import annotations

from typing import Any

from .data_provider import DataProvider
from .semantic import SemanticLayer

DEFAULT_LIMIT = 100
MAX_LIMIT = 10000


def _quote(v: Any) -> str:
    """把字面值安全地拼进 SQL（白名单式：数值直接、字符串加引号）。"""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def _filters_sql(filters: dict) -> str:
    """拼接筛选条件。键为列名，值为字面量或 {op, value} 结构。"""
    clauses = []
    for k, v in filters.items():
        if isinstance(v, dict):
            op = v.get("op", "=")
            clauses.append(f'{k} {op} {_quote(v["value"])}')
        else:
            clauses.append(f"{k} = {_quote(v)}")
    return " AND ".join(clauses)


class Tools:
    """封装语义层校验 + 工具执行。所有方法返回 {ok, ...} 字典便于对账。"""

    def __init__(self, provider: DataProvider, semantic: SemanticLayer) -> None:
        self._p = provider
        self._s = semantic

    # ---- 元信息 ----
    def list_metrics(self, table: str) -> dict:
        if table not in self._s.allowed_tables():
            return {"ok": False, "error": f"未知表: {table}"}
        return {"ok": True, "table": table, "metrics": self._s.get_metrics(table)}

    def list_dimensions(self, table: str) -> dict:
        if table not in self._s.allowed_tables():
            return {"ok": False, "error": f"未知表: {table}"}
        return {"ok": True, "table": table, "dimensions": self._s.get_dimensions(table)}

    # ---- 主查询 ----
    def query_mart(
        self,
        table: str,
        metrics: list[str] | None = None,
        dimensions: list[str] | None = None,
        filters: dict | None = None,
        order_by: str | None = None,
        limit: int = DEFAULT_LIMIT,
        use_valid_sample: bool = True,
    ) -> dict:
        """结构化参数 → 安全 SQL。

        - metrics: 指标名（必须存在于语义字典）
        - dimensions: 维度列（必须存在于语义字典）
        - filters: {列: 值} 或 {列: {op, value}}，作用于 mart 表原始列
        - order_by: 指标名（用于排序）
        - use_valid_sample: 是否自动附加配送分析有效样本口径
        """
        if table not in self._s.allowed_tables():
            return {"ok": False, "error": f"未知表: {table}"}

        metrics = metrics or []
        dimensions = dimensions or []
        if not metrics:
            return {"ok": False, "error": "至少需要一个指标"}

        # 校验指标与维度均来自语义字典，锁死口径
        selects, aliases = [], []
        for m in metrics:
            expr = self._s.check_metric(table, m)
            if expr is None:
                return {"ok": False, "error": f"指标不存在: {m}"}
            alias = f"_m_{m}"
            selects.append(f"{expr} AS {alias}")
            aliases.append(alias)

        for d in dimensions:
            if not self._s.check_dimension(table, d):
                return {"ok": False, "error": f"维度不存在: {d}"}
            selects.append(d)

        sql = f'SELECT {", ".join(selects)} FROM {table}'

        # WHERE：语义预置筛选 + 有效样本口径 + 用户筛选
        where = []
        for name, expr in self._s.get_filters(table).items():
            if use_valid_sample and name == "valid_sample":
                where.append(expr)
        if filters:
            where.append(_filters_sql(filters))
        if where:
            sql += " WHERE " + " AND ".join(where)

        if dimensions:
            sql += " GROUP BY " + ", ".join(dimensions)

        # 排序：order_by 传指标名，映射到本次查询生成的别名
        if order_by:
            alias = f"_m_{order_by}" if order_by in metrics else None
            if alias not in aliases:
                return {"ok": False, "error": f"order_by 必须是查询指标之一: {order_by}"}
            sql += f" ORDER BY {alias} DESC"

        sql += f" LIMIT {min(int(limit), MAX_LIMIT)}"

        try:
            rows = self._p.execute(sql)
        except RuntimeError as e:
            return {"ok": False, "error": str(e), "sql": sql}

        return {
            "ok": True,
            "table": table,
            "metrics": metrics,
            "dimensions": dimensions,
            "sql": sql,           # 附 SQL 供对账
            "rows": rows,
            "row_count": len(rows),
        }

    # ---- 排名 ----
    def top_n(
        self,
        table: str,
        metric: str,
        dimension: str,
        n: int = 5,
        asc: bool = False,
    ) -> dict:
        """按某指标对某维度排名取 Top-N（默认降序）。"""
        res = self.query_mart(
            table=table,
            metrics=[metric],
            dimensions=[dimension],
            order_by=metric if not asc else None,
            limit=n,
        )
        if not res["ok"]:
            return res
        if asc:
            # 升序：重新按指标别名升序排
            res = self.query_mart(
                table=table, metrics=[metric], dimensions=[dimension], limit=MAX_LIMIT
            )
            if not res["ok"]:
                return res
            res["rows"] = sorted(
                res["rows"], key=lambda r: r[f"_m_{metric}"], reverse=False
            )[:n]
        return res
