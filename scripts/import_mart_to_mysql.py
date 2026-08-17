"""把 mart CSV 数据集导入本地 MySQL（olist 库）。

- 源：data/sample/mart_order_delivery.csv / mart_order_seller_delivery.csv / mart_order_item_delivery.csv
- 目标：MySQL olist 库，同名表
- 列类型推断：数值列 DOUBLE；日期列 VARCHAR(64)；其余 VARCHAR(255)
- 幂等：先 DROP 同名表再建（本脚本用于本地测试库）

用法: uv run python scripts/import_mart_to_mysql.py
"""
from __future__ import annotations

import csv
import os
from pathlib import Path

import pymysql
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data" / "sample"
TABLES = [
    "mart_order_delivery.csv",
    "mart_order_item_delivery.csv",
    "mart_order_seller_delivery.csv",
]
DATE_HINTS = ("date", "timestamp")


def _is_float(v: str) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _is_missing(v) -> bool:
    if v is None:
        return True
    s = str(v).strip()
    return s == "" or s.upper() in ("NULL", "NONE", "NAN")


def _infer_types(headers: list[str], rows: list[dict]) -> dict[str, str]:
    types: dict[str, str] = {}
    for col in headers:
        low = col.lower()
        if any(k in low for k in DATE_HINTS):
            types[col] = "VARCHAR(64)"
            continue
        non_numeric = [r.get(col) for r in rows
                       if not _is_missing(r.get(col)) and not _is_float(str(r.get(col)))]
        types[col] = "DOUBLE" if not non_numeric else "VARCHAR(255)"
    return types


def _to_sql(v, ctype: str):
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.upper() in ("NULL", "NONE", "NAN"):
        return None
    if ctype == "DOUBLE":
        try:
            return float(s)
        except (TypeError, ValueError):
            return None
    return s


def import_csv(conn, csv_path: Path) -> int:
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        headers = reader.fieldnames or []
    if not rows or not headers:
        print(f"  跳过空文件: {csv_path.name}")
        return 0

    types = _infer_types(headers, rows)
    table = csv_path.stem
    cols = ", ".join(f"`{c}` {types[c]}" for c in headers)
    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS `{table}`")
        cur.execute(f"CREATE TABLE `{table}` ({cols}) ENGINE=InnoDB "
                    "DEFAULT CHARSET=utf8mb4")

    sql = (f"INSERT INTO `{table}` ({', '.join('`'+c+'`' for c in headers)}) "
           f"VALUES ({', '.join(['%s'] * len(headers))})")
    batch = [
        [_to_sql(r.get(c), types[c]) for c in headers]
        for r in rows
    ]
    with conn.cursor() as cur:
        for i in range(0, len(batch), 200):
            cur.executemany(sql, batch[i:i + 200])
    conn.commit()
    return len(batch)


def main() -> int:
    host = os.environ.get("DB_HOST", "127.0.0.1")
    port = int(os.environ.get("DB_PORT", "3306"))
    user = os.environ.get("DB_USER", "root")
    password = os.environ.get("DB_PASSWORD", "")
    database = os.environ.get("DB_NAME", "olist")
    if not password:
        print("警告: DB_PASSWORD 为空，root 无密码可能导致认证失败")

    conn = pymysql.connect(host=host, port=port, user=user,
                           password=password, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{database}` "
                        "DEFAULT CHARSET utf8mb4")
        conn.select_db(database)
        print(f"已连接 {host}:{port}/{database}")
        for name in TABLES:
            path = DATA_DIR / name
            if not path.exists():
                print(f"  缺少 {name}，跳过")
                continue
            n = import_csv(conn, path)
            print(f"  {name}: 导入 {n} 行")
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES")
            print("当前库表:", [r[0] for r in cur.fetchall()])
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
