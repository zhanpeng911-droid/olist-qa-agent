"""数据访问抽象。

提供两种只读数据源：
- ProjectCsvProvider：从完整分析宽表截取的演示样本，用于本地功能检查与回归测试；
- MySQLProvider：完整业务数据库中的分析宽表（Mart），用于全量分析。

CSV/数据库字段会在读取层补齐第一版所需的兼容别名，原始数据不会被修改。
"""
from __future__ import annotations

import csv
import os
import re
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

import pymysql

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_SAMPLE_DIR = _PROJECT_ROOT / "data" / "sample"
_configured_sample_dir = os.environ.get("PROJECT_DATA_DIR")
if _configured_sample_dir:
    _sample_path = Path(_configured_sample_dir).expanduser()
    _PROJECT_CSV_DIR = (
        _sample_path if _sample_path.is_absolute()
        else _PROJECT_ROOT / _sample_path
    )
else:
    _PROJECT_CSV_DIR = _DEFAULT_SAMPLE_DIR

# 统一的外部显示名称。类名和内部表名保持不变，避免用户看到工程代号。
SAMPLE_SOURCE_LABEL = "演示样本（截取数据）"
DATABASE_SOURCE_LABEL = "完整业务数据库（MySQL）"

_REQUIRED_ATTRIBUTION_TABLES = {
    "mart_order_delivery",
    "mart_order_seller_delivery",
}


class DataProvider(ABC):
    """数据访问接口：执行一段只读 SQL，返回行列表。"""

    @abstractmethod
    def execute(self, sql: str) -> list[dict]:
        """执行只读查询，返回 list[dict]（每行一个 dict）。"""

    @abstractmethod
    def close(self) -> None:
        ...


def _delay_bucket(value) -> str | None:
    """根据 late_days 生成统一延迟分档。"""
    try:
        days = float(value)
    except (TypeError, ValueError):
        return None
    if days <= 0:
        return "按时"
    if days <= 3:
        return "1-3天"
    if days <= 7:
        return "4-7天"
    if days <= 14:
        return "8-14天"
    return "15天+"


def _optional_float(value) -> float | None:
    """兼容 CSV 中的空字符串、None 与文本 NULL。"""
    if value is None or str(value).strip().upper() in ("", "NULL", "NONE", "NAN"):
        return None
    return float(value)


def _is_missing(value) -> bool:
    return value is None or str(value).strip().upper() in ("", "NULL", "NONE", "NAN")


def _duration_days(start, end) -> float | None:
    """将两个 Mart 时间戳转换为天数；无效或缺失时间返回 None。"""
    if _is_missing(start) or _is_missing(end):
        return None
    try:
        started = datetime.fromisoformat(str(start).strip())
        ended = datetime.fromisoformat(str(end).strip())
    except (TypeError, ValueError):
        return None
    return (ended - started).total_seconds() / 86400


def _compatibility_values(table: str, row: dict[str, str]) -> dict[str, object]:
    """把真实 Mart 字段映射为第一版内部口径，避免改写原 CSV。"""
    additions: dict[str, object] = {}
    if table == "mart_order_delivery":
        purchase_ts = row.get("order_purchase_timestamp", "")
        additions = {
            "order_month": purchase_ts[:7] if purchase_ts else None,
            "promised_delivery_days": _duration_days(
                row.get("order_purchase_timestamp"),
                row.get("order_estimated_delivery_date"),
            ),
            "is_multi_seller_order": (
                1 if (v := _optional_float(row.get("distinct_seller_count")))
                is not None and v > 1 else 0
            ),
            "delay_bucket": _delay_bucket(row.get("late_days")),
            "approval_days": (
                v / 24 if (v := _optional_float(row.get("payment_approval_hours")))
                is not None else None
            ),
            "fulfillment_days": (
                v / 24 if (v := _optional_float(row.get("total_fulfillment_hours")))
                is not None else None
            ),
            "price_total": row.get("product_value"),
            "freight_total": row.get("freight_value"),
            "payment_value": row.get("payment_total"),
        }
    elif table == "mart_order_seller_delivery":
        seller_state = row.get("seller_state")
        customer_state = row.get("customer_state")
        additions = {
            "order_month": (
                row.get("order_purchase_timestamp", "")[:7] or None
            ),
            "route": (
                f"{seller_state}→{customer_state}"
                if seller_state and customer_state else None
            ),
            "cross_state": row.get("is_cross_state"),
            "seller_items": row.get("seller_item_count"),
            "seller_price": row.get("seller_product_value"),
            "seller_freight": row.get("seller_freight_value"),
            "fulfillment_days": _duration_days(
                row.get("order_purchase_timestamp"),
                row.get("order_delivered_customer_date"),
            ),
            "promised_delivery_days": _duration_days(
                row.get("order_purchase_timestamp"),
                row.get("order_estimated_delivery_date"),
            ),
            "has_review_record": (
                1 if "review_score" not in row
                or not _is_missing(row.get("review_score")) else 0
            ),
            "is_delivery_analysis_eligible": row.get(
                "is_delivery_analysis_eligible", 1
            ),
        }
    return {k: v for k, v in additions.items() if k not in row}


def _table_from_csv(conn: sqlite3.Connection, name: str, path: Path) -> int:
    """把 CSV 分批载入 SQLite；返回数据行数。"""
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        source_header = list(reader.fieldnames or [])
        if not source_header:
            raise RuntimeError(f"CSV 缺少表头: {path}")
        first_row = next(reader, None)
    additions = _compatibility_values(name, first_row or {})
    header = source_header + [c for c in additions if c not in source_header]

    def _to_sql(v: str):
        if _is_missing(v):
            return None
        if not isinstance(v, str):
            return v
        v = v.strip()
        try:
            return int(v)
        except ValueError:
            pass
        try:
            return float(v)
        except ValueError:
            pass
        return v

    placeholders = ", ".join("?" for _ in header)
    cols = ", ".join(f'"{c}"' for c in header)
    conn.execute(f'CREATE TABLE "{name}" ({cols})')
    insert_sql = f'INSERT INTO "{name}" VALUES ({placeholders})'

    def _prepared(row: dict[str, str]) -> list[object]:
        enriched = dict(row)
        enriched.update(_compatibility_values(name, row))
        return [_to_sql(enriched.get(c)) for c in header]

    count = 0
    batch: list[list[object]] = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            batch.append(_prepared(row))
            if len(batch) >= 5000:
                conn.executemany(insert_sql, batch)
                count += len(batch)
                batch.clear()
    if batch:
        conn.executemany(insert_sql, batch)
        count += len(batch)
    return count


class CsvProvider(DataProvider):
    """把指定目录中的 Mart CSV 加载到只读分析用 SQLite。"""

    def __init__(self, data_dir: Path | str) -> None:
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._tables: dict[str, str] = {}  # name -> csv filename
        self._row_counts: dict[str, int] = {}
        self.source_name = "CSV"
        self._load(data_dir)

    def _load(self, data_dir: Path | str) -> None:
        d = Path(data_dir)
        for csv_file in sorted(d.glob("*.csv")):
            name = csv_file.stem
            self._row_counts[name] = _table_from_csv(self._conn, name, csv_file)
            self._tables[name] = csv_file.name
        if {"mart_order_delivery", "mart_order_item_delivery"} <= set(self._tables):
            self._conn.execute("""
                CREATE VIEW mart_order_item_analysis AS
                SELECT i.*,
                       o.has_review_record,
                       o.review_score,
                       o.is_low_score,
                       o.is_strict_negative_score,
                       o.is_late_delivery,
                       o.late_days,
                       o.delivery_variance_days,
                       o.fulfillment_days,
                       o.approval_days,
                       o.is_delivery_analysis_eligible
                FROM mart_order_item_delivery i
                JOIN mart_order_delivery o ON i.order_id = o.order_id
            """)
            self._tables["mart_order_item_analysis"] = (
                "mart_order_item_delivery.csv + mart_order_delivery.csv"
            )
            self._row_counts["mart_order_item_analysis"] = self._conn.execute(
                "SELECT COUNT(*) FROM mart_order_item_analysis"
            ).fetchone()[0]

    @property
    def table_names(self) -> list[str]:
        return list(self._tables)

    @property
    def row_counts(self) -> dict[str, int]:
        return dict(self._row_counts)

    def execute(self, sql: str) -> list[dict]:
        try:
            cur = self._conn.execute(sql)
        except sqlite3.Error as e:
            raise RuntimeError(f"SQL 执行失败: {e}\nSQL: {sql}") from e
        return [dict(r) for r in cur.fetchall()]

    def close(self) -> None:
        self._conn.close()


class ProjectCsvProvider(CsvProvider):
    """加载 ``data/sample`` 中的 Mart 截取 CSV，并校验所需主表。"""

    def __init__(self, data_dir: Path | str = _PROJECT_CSV_DIR) -> None:
        super().__init__(data_dir)
        self.source_name = SAMPLE_SOURCE_LABEL
        missing = sorted(_REQUIRED_ATTRIBUTION_TABLES - set(self.table_names))
        if missing:
            self.close()
            raise RuntimeError(
                "演示样本缺少分析所需的数据表：" + ", ".join(missing)
            )


class MySQLProvider(DataProvider):
    """基于完整 MySQL 8 业务数据库的实现。

    安全约束：
    - 仅执行只读 SELECT（执行前检查是否为 SELECT，拦截 DML/DDL）
    - 表名必须在白名单（guards.allow_tables）
    - 强制带 LIMIT（guards.max_rows）
    连接信息从环境变量读取：DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME。
    """

    SELECT_START = "select"

    def __init__(self, host=None, port=None, user=None, password=None,
                 database=None, item_table=None,
                 allow_tables: list[str] | None = None,
                 max_rows: int = 10000) -> None:
        self._host = host or os.environ.get("DB_HOST")
        self._port = int(port or os.environ.get("DB_PORT", "3306"))
        self._user = user or os.environ.get("DB_USER")
        self._password = password or os.environ.get("DB_PASSWORD")
        self._database = database or os.environ.get("DB_NAME")
        self._item_table = item_table or os.environ.get(
            "DB_ITEM_TABLE", "mart_order_item_business"
        )
        if not re.fullmatch(r"[A-Za-z0-9_]+", self._item_table):
            raise RuntimeError("商品项表名只能包含字母、数字和下划线")
        if not (self._host and self._user and self._database):
            raise RuntimeError(
                "MySQL 连接信息不完整：请在 .env 配置 DB_HOST/DB_USER/DB_PASSWORD/DB_NAME。"
            )
        self._allow_tables = allow_tables or []
        self._max_rows = max_rows
        self._conn = self._connect_raw()
        self.source_name = DATABASE_SOURCE_LABEL

    def _check_sql(self, sql: str) -> None:
        stmt = sql.strip().lstrip("(")
        if ";" in stmt:
            raise RuntimeError("MySQLProvider 不允许多语句 SQL")
        lowered = stmt.lower()
        if not lowered.startswith(self.SELECT_START):
            raise RuntimeError("MySQLProvider 仅允许只读 SELECT")
        # 先移除注释再提取表名，避免 FROM/JOIN 被注释干扰而绕过白名单
        cleaned = re.sub(r"/\*.*?\*/", " ", lowered, flags=re.DOTALL)
        cleaned = re.sub(r"--[^\n]*", " ", cleaned)
        for t in re.findall(r"\b(?:from|join)\s+([a-zA-Z0-9_`]+)", cleaned):
            t = t.strip("`")
            if self._allow_tables and t not in self._allow_tables:
                raise RuntimeError(f"访问了白名单外的表: {t}")
        if "limit" not in lowered:
            raise RuntimeError("MySQLProvider 要求查询必须带 LIMIT")

    def execute(self, sql: str) -> list[dict]:
        self._check_sql(sql)
        sql = self._compatibility_sql(sql)
        return self._execute_with_retry(sql)

    def _execute_with_retry(self, sql: str) -> list[dict]:
        """执行 SQL；连接在长任务中被服务端断开（InterfaceError）时自动重连重试一次。

        全量归因等长任务会复用同一连接跑大量 SQL，若中途连接失效（如 wait_timeout、
        服务端回收），后续查询会全部失败。检测到连接错误时重连并重试当前语句。
        """
        try:
            return self._run(sql)
        except pymysql.InterfaceError as exc:
            if "timed out" not in str(exc).lower():
                # 连接中断：重连后重试当前语句一次
                self._conn.close()
                self._conn = self._connect_raw()
                return self._run(sql)
            raise
        except pymysql.OperationalError as exc:
            code = getattr(exc, "args", [None])[0]
            if code in (2006, 2013, 1927):   # server has gone away / lost connection
                self._conn.close()
                self._conn = self._connect_raw()
                return self._run(sql)
            raise

    def _run(self, sql: str) -> list[dict]:
        with self._conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
        return [dict(r) for r in rows]

    def _connect_raw(self):
        return pymysql.connect(
            host=self._host, port=self._port, user=self._user,
            password=self._password, database=self._database,
            charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5, read_timeout=180, write_timeout=30,
            autocommit=True,
        )

    def close(self) -> None:
        self._conn.close()

    def inspect_marts(self) -> dict:
        """只读检查三张物理 Mart 表，并返回行数与字段缺失情况。"""
        required = {
            "mart_order_delivery": {
                "order_id", "order_purchase_timestamp", "product_value",
                "freight_value", "payment_total", "review_score",
                "is_low_score", "is_late_delivery", "late_days",
                "payment_approval_hours", "total_fulfillment_hours",
                "is_delivery_analysis_eligible", "has_review_record",
            },
            "mart_order_seller_delivery": {
                "order_id", "seller_id", "seller_state", "customer_state",
                "seller_item_count", "seller_product_value",
                "seller_freight_value", "is_cross_state", "review_score",
                "is_low_score", "is_late_delivery",
                "is_delivery_analysis_eligible", "is_multi_seller_order",
            },
            self._item_table: {
                "order_id", "order_item_id", "category_name", "product_id",
                "seller_id", "seller_state", "customer_state", "item_price",
                "item_freight_value", "item_freight_ratio",
            },
        }
        result = {"database": self._database, "tables": {}}
        with self._conn.cursor() as cur:
            for table, expected in required.items():
                cur.execute(f"SHOW COLUMNS FROM `{table}`")
                columns = {row["Field"] for row in cur.fetchall()}
                cur.execute(f"SELECT COUNT(*) AS n FROM `{table}`")
                row_count = int(cur.fetchone()["n"])
                result["tables"][table] = {
                    "row_count": row_count,
                    "column_count": len(columns),
                    "missing_columns": sorted(expected - columns),
                }
        missing = {
            t: info["missing_columns"] for t, info in result["tables"].items()
            if info["missing_columns"]
        }
        if missing:
            details = "；".join(f"{t}: {', '.join(cols)}" for t, cols in missing.items())
            raise RuntimeError(f"分析数据表字段检查未通过：{details}")
        return result

    def _compatibility_sql(self, sql: str) -> str:
        """用只读派生表补齐兼容字段，不在 MySQL 中创建或修改对象。"""
        order_view = """(
            SELECT m.*,
                   DATE_FORMAT(m.order_purchase_timestamp, '%Y-%m') AS order_month,
                   TIMESTAMPDIFF(
                       SECOND, m.order_purchase_timestamp,
                       m.order_estimated_delivery_date
                   ) / 86400.0 AS promised_delivery_days,
                   CASE WHEN m.distinct_seller_count > 1 THEN 1 ELSE 0 END
                       AS is_multi_seller_order,
                   CASE WHEN m.late_days <= 0 THEN '按时'
                        WHEN m.late_days <= 3 THEN '1-3天'
                        WHEN m.late_days <= 7 THEN '4-7天'
                        WHEN m.late_days <= 14 THEN '8-14天'
                        ELSE '15天+' END AS delay_bucket,
                   m.payment_approval_hours / 24.0 AS approval_days,
                   m.total_fulfillment_hours / 24.0 AS fulfillment_days,
                   m.product_value AS price_total,
                   m.freight_value AS freight_total,
                   m.payment_total AS payment_value
            FROM mart_order_delivery m
        ) AS mart_order_delivery"""
        seller_view = """(
            SELECT s.*,
                   DATE_FORMAT(s.order_purchase_timestamp, '%Y-%m') AS order_month,
                   CONCAT(s.seller_state, '→', s.customer_state) AS route,
                   s.is_cross_state AS cross_state,
                   s.seller_item_count AS seller_items,
                   s.seller_product_value AS seller_price,
                   s.seller_freight_value AS seller_freight,
                   TIMESTAMPDIFF(
                       SECOND, s.order_purchase_timestamp,
                       s.order_delivered_customer_date
                   ) / 86400.0 AS fulfillment_days,
                   TIMESTAMPDIFF(
                       SECOND, s.order_purchase_timestamp,
                       s.order_estimated_delivery_date
                   ) / 86400.0 AS promised_delivery_days,
                   CASE WHEN s.review_score IS NULL THEN 0 ELSE 1 END AS has_review_record
            FROM mart_order_seller_delivery s
        ) AS mart_order_seller_delivery"""
        item_view = """(
            SELECT i.*,
                   o.has_review_record,
                   o.review_score,
                   o.is_low_score,
                   o.is_strict_negative_score,
                   o.is_late_delivery,
                   o.late_days,
                   o.delivery_variance_days,
                   o.total_fulfillment_hours / 24.0 AS fulfillment_days,
                   o.payment_approval_hours / 24.0 AS approval_days,
                   o.is_delivery_analysis_eligible
            FROM {item_table} i
            JOIN mart_order_delivery o ON i.order_id = o.order_id
        ) AS mart_order_item_analysis""".format(item_table=self._item_table)
        sql = re.sub(r"\bFROM\s+mart_order_delivery\b", "FROM " + order_view,
                     sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bFROM\s+mart_order_seller_delivery\b", "FROM " + seller_view,
                     sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bFROM\s+mart_order_item_analysis\b", "FROM " + item_view,
                     sql, flags=re.IGNORECASE)
        return sql
