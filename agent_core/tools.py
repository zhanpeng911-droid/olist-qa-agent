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
ALLOWED_FILTER_OPERATORS = {"=", "!=", "<>", ">", ">=", "<", "<=", "IN", "NOT IN"}
# 可作过滤条件的数值列（不参与 GROUP BY，仅用于 WHERE）
FILTERABLE_COLUMNS = {
    "late_days", "delivery_variance_days", "review_score", "price_total",
    "freight_total", "payment_value", "approval_days", "fulfillment_days",
}
REVIEW_METRICS = {
    "reviewed_orders", "low_score_count", "low_score_orders",
    "low_score_rate", "strict_negative_rate", "avg_review_score",
}


def _quote(v: Any) -> str:
    """把字面值安全地拼进 SQL（白名单式：数值直接、字符串加引号）。"""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def _filters_sql(filters: dict, allowed_columns: set[str]) -> str:
    """校验并拼接筛选条件；不接受语义字典之外的列或操作符。"""
    if not isinstance(filters, dict):
        raise ValueError("filters 必须是对象，格式为 {列: 值} 或 {列: {op, value}}")
    clauses = []
    for k, v in filters.items():
        if k not in allowed_columns:
            raise ValueError(f"筛选列不在当前表的维度白名单中: {k}")
        if isinstance(v, dict):
            if "value" not in v:
                raise ValueError(f"筛选条件 {k} 缺少 value；应使用 {{op, value}} 格式")
            op = str(v.get("op", "=")).strip().upper()
            if op not in ALLOWED_FILTER_OPERATORS:
                raise ValueError(f"筛选操作符不受支持: {op}")
            value = v["value"]
            if op in {"IN", "NOT IN"}:
                if not isinstance(value, (list, tuple)) or not value:
                    raise ValueError(f"筛选条件 {k} 使用 {op} 时 value 必须是非空数组")
                quoted = ", ".join(_quote(item) for item in value)
                clauses.append(f"{k} {op} ({quoted})")
            else:
                clauses.append(f"{k} {op} {_quote(value)}")
        else:
            if isinstance(v, (list, tuple, set, dict)):
                raise ValueError(f"筛选条件 {k} 的值格式不受支持")
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
        - order_by: 本次指标或维度，可带 ASC/DESC；指标默认降序，维度默认升序
        - use_valid_sample: 是否自动附加配送分析有效样本口径
        """
        if table not in self._s.allowed_tables():
            return {"ok": False, "error": f"未知表: {table}"}

        metrics = metrics or []
        dimensions = dimensions or []
        if not isinstance(metrics, list) or not all(isinstance(m, str) for m in metrics):
            return {"ok": False, "error": "metrics 必须是指标名数组"}
        if not isinstance(dimensions, list) or not all(isinstance(d, str) for d in dimensions):
            return {"ok": False, "error": "dimensions 必须是维度名数组"}
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

        # 维度 select 与 GROUP BY：表达式维度用原表达式（MySQL 不支持 GROUP BY 别名）
        dim_selects: list[str] = []
        dim_groups: list[str] = []
        for d in dimensions:
            if not self._s.check_dimension(table, d):
                return {"ok": False, "error": f"维度不存在: {d}"}
            expr = self._s.get_dimension_expr(table, d)
            dim_selects.append(f"{expr} AS {d}" if expr else d)
            dim_groups.append(expr or d)
        selects.extend(dim_selects)

        sql = f'SELECT {", ".join(selects)} FROM {table}'

        # WHERE：语义预置筛选 + 有效样本口径 + 用户筛选
        where = []
        for name, expr in self._s.get_filters(table).items():
            if use_valid_sample and name == "valid_sample":
                where.append(expr)
            if name == "reviewed_only" and REVIEW_METRICS.intersection(metrics):
                where.append(expr)
        if filters:
            filter_cols = set(self._s.get_dimensions(table)) | FILTERABLE_COLUMNS
            try:
                where.append(_filters_sql(filters, filter_cols))
            except (TypeError, ValueError) as exc:
                return {"ok": False, "error": f"筛选参数错误: {exc}"}
        if where:
            sql += " WHERE " + " AND ".join(where)

        if dimensions:
            sql += " GROUP BY " + ", ".join(dim_groups)

        # 排序：兼容多字段写法；每个字段仍须在本次查询白名单中。
        if order_by:
            if not isinstance(order_by, str):
                return {"ok": False, "error": "order_by 必须是字符串"}
            sort_terms = []
            sort_fields = []
            for raw_term in order_by.split(","):
                parts = raw_term.strip().split()
                if len(parts) == 1:
                    field = parts[0]
                    direction = "DESC" if field in metrics else "ASC"
                elif len(parts) == 2 and parts[1].upper() in {"ASC", "DESC"}:
                    field, direction = parts[0], parts[1].upper()
                else:
                    return {
                        "ok": False,
                        "error": "order_by 每项应为指标/维度名，可选 ASC 或 DESC",
                    }
                if field in metrics:
                    sort_expr = f"_m_{field}"
                elif field in dimensions:
                    sort_expr = field
                else:
                    return {
                        "ok": False,
                        "error": f"order_by 必须是本次查询的指标或维度之一: {field}",
                    }
                sort_terms.append(f"{sort_expr} {direction}")
                sort_fields.append(field)
            # 聚合指标经常并列；追加分组维度作为稳定的最终排序键，避免 Top-N 漂移。
            for dimension in dimensions:
                if dimension not in sort_fields:
                    sort_terms.append(f"{dimension} ASC")
            sql += " ORDER BY " + ", ".join(sort_terms)

        try:
            safe_limit = int(limit)
        except (TypeError, ValueError):
            return {"ok": False, "error": "limit 必须是整数"}
        if safe_limit < 1:
            return {"ok": False, "error": "limit 必须大于等于 1"}
        sql += f" LIMIT {min(safe_limit, MAX_LIMIT)}"

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
