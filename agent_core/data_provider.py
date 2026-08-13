"""数据访问抽象。

M1 使用 SampleProvider（把样例 CSV 加载进 SQLite 内存库），
让工具层基于"模板 SQL"工作，与真实数据库逻辑一致；
将来连真库只需新增 MySQLProvider，工具层无需改动。
"""
from __future__ import annotations

import csv
import os
import re
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SAMPLE_DIR = _PROJECT_ROOT / "sample_data"


class DataProvider(ABC):
    """数据访问接口：执行一段只读 SQL，返回行列表。"""

    @abstractmethod
    def execute(self, sql: str) -> list[dict]:
        """执行只读查询，返回 list[dict]（每行一个 dict）。"""

    @abstractmethod
    def close(self) -> None:
        ...


def _table_from_csv(conn: sqlite3.Connection, name: str, path: Path) -> None:
    """把 CSV 载入 SQLite 表。数值列尽量转成 REAL，便于聚合。"""
    with open(path, encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)

    def _to_sql(v: str):
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

    placeholders = ", ".join("?" * len(header))
    cols = ", ".join(f'"{c}"' for c in header)
    conn.execute(f'CREATE TABLE "{name}" ({cols})')
    conn.executemany(
        f'INSERT INTO "{name}" VALUES ({placeholders})',
        [[_to_sql(c) for c in row] for row in rows],
    )


class SampleProvider(DataProvider):
    """基于样例 CSV 的 SQLite 内存实现（M1）。"""

    def __init__(self, sample_dir: Path | str = _SAMPLE_DIR) -> None:
        self._conn = sqlite3.connect(":memory:")
        self._conn.row_factory = sqlite3.Row
        self._tables: dict[str, str] = {}  # name -> csv filename
        self._load(sample_dir)

    def _load(self, sample_dir: Path | str) -> None:
        d = Path(sample_dir)
        for csv_file in sorted(d.glob("*.csv")):
            name = csv_file.stem
            _table_from_csv(self._conn, name, csv_file)
            self._tables[name] = csv_file.name

    @property
    def table_names(self) -> list[str]:
        return list(self._tables)

    def execute(self, sql: str) -> list[dict]:
        try:
            cur = self._conn.execute(sql)
        except sqlite3.Error as e:
            raise RuntimeError(f"SQL 执行失败: {e}\nSQL: {sql}") from e
        return [dict(r) for r in cur.fetchall()]

    def close(self) -> None:
        self._conn.close()


class MySQLProvider(DataProvider):
    """基于真实 MySQL 8 的实现。

    安全约束：
    - 仅执行只读 SELECT（执行前检查是否为 SELECT，拦截 DML/DDL）
    - 表名必须在白名单（guards.allow_tables）
    - 强制带 LIMIT（guards.max_rows）
    连接信息从环境变量读取：DB_HOST / DB_PORT / DB_USER / DB_PASSWORD / DB_NAME。
    """

    SELECT_START = "select"

    def __init__(self, host=None, port=None, user=None, password=None,
                 database=None, allow_tables: list[str] | None = None,
                 max_rows: int = 10000) -> None:
        import pymysql  # 延迟导入

        self._host = host or os.environ.get("DB_HOST")
        self._port = int(port or os.environ.get("DB_PORT", "3306"))
        self._user = user or os.environ.get("DB_USER")
        self._password = password or os.environ.get("DB_PASSWORD")
        self._database = database or os.environ.get("DB_NAME")
        if not (self._host and self._user and self._database):
            raise RuntimeError(
                "MySQL 连接信息不完整：请在 .env 配置 DB_HOST/DB_USER/DB_PASSWORD/DB_NAME。"
            )
        self._allow_tables = allow_tables or []
        self._max_rows = max_rows
        self._conn = pymysql.connect(
            host=self._host, port=self._port, user=self._user,
            password=self._password, database=self._database,
            charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
        )

    def _check_sql(self, sql: str) -> None:
        stmt = sql.strip().lstrip("(").lower()
        if not stmt.startswith(self.SELECT_START):
            raise RuntimeError("MySQLProvider 仅允许只读 SELECT")
        # 提取 FROM/JOIN 后的表名，校验白名单
        for t in re.findall(r"\b(?:from|join)\s+([a-zA-Z0-9_`]+)", stmt):
            t = t.strip("`")
            if self._allow_tables and t not in self._allow_tables:
                raise RuntimeError(f"访问了白名单外的表: {t}")
        if "limit" not in stmt:
            raise RuntimeError("MySQLProvider 要求查询必须带 LIMIT")

    def execute(self, sql: str) -> list[dict]:
        self._check_sql(sql)
        with self._conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()
