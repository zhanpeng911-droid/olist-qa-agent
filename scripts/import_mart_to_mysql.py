"""把 mart CSV 数据集导入本地 MySQL。

- 默认：data/sample 的 3 张演示表
- 全量：`--dir <导出目录>` 导入完整 mart 表（大文件，分批读 + 分批插入）
- 列类型推断：采样前 N 行（数值列 DOUBLE / 日期列 VARCHAR / 其余 VARCHAR）
- 幂等：先 DROP 同名表再建

用法:
  uv run python scripts/import_mart_to_mysql.py                          # 演示样本
  uv run python scripts/import_mart_to_mysql.py --dir <全量CSV目录>      # 全量
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

import pymysql
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

SAMPLE_DIR = ROOT / "data" / "sample"
SAMPLE_TABLES = ["mart_order_delivery.csv", "mart_order_item_delivery.csv",
                 "mart_order_seller_delivery.csv"]
DATE_HINTS = ("date", "timestamp")
TYPE_SAMPLE_ROWS = 3000
INSERT_BATCH = 5000

# 建表时按表名追加索引，避免 JOIN / GROUP BY 全表扫描（见 TEST_LOG §40）。
# 商品项表在样本与全量下文件名不同（delivery / business），故两处都列。
INDEX_DEFS = {
    "mart_order_delivery": ["PRIMARY KEY (order_id)"],
    "mart_order_item_delivery": [
        "INDEX idx_item_order_id (order_id)",
        "INDEX idx_item_product_id (product_id)",
        "INDEX idx_item_category_name (category_name)",
        "INDEX idx_item_seller_id (seller_id)",
    ],
    "mart_order_item_business": [
        "INDEX idx_item_order_id (order_id)",
        "INDEX idx_item_product_id (product_id)",
        "INDEX idx_item_category_name (category_name)",
        "INDEX idx_item_seller_id (seller_id)",
    ],
    "mart_order_seller_delivery": [
        "INDEX idx_seller_order_id (order_id)",
        "INDEX idx_seller_state (seller_state)",
        "INDEX idx_customer_state (customer_state)",
    ],
}


def _is_missing(v) -> bool:
    if v is None:
        return True
    s = str(v).strip()
    return s == "" or s.upper() in ("NULL", "NONE", "NAN")


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def _infer_types(headers: list[str], sample: list[dict]) -> dict[str, str]:
    types: dict[str, str] = {}
    for col in headers:
        low = col.lower()
        if any(k in low for k in DATE_HINTS):
            types[col] = "VARCHAR(64)"
            continue
        non_numeric = [
            r.get(col) for r in sample
            if not _is_missing(r.get(col)) and not _is_float(str(r.get(col)))
        ]
        types[col] = "DOUBLE" if not non_numeric else "VARCHAR(255)"
    return types


def _to_sql(v, ctype: str):
    if _is_missing(v):
        return None
    s = str(v).strip()
    if ctype == "DOUBLE":
        try:
            return float(s)
        except (TypeError, ValueError):
            return None
    return s


def import_csv(conn, csv_path: Path, table: str) -> int:
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        # 采样推断类型
        sample: list[dict] = []
        for _ in range(TYPE_SAMPLE_ROWS):
            try:
                sample.append(next(reader))
            except StopIteration:
                break
        if not headers:
            print(f"  跳过空文件: {csv_path.name}")
            return 0
        types = _infer_types(headers, sample)

        cols = ", ".join(f"`{c}` {types[c]}" for c in headers)
        index_defs = INDEX_DEFS.get(table, [])
        index_clause = (", " + ", ".join(index_defs)) if index_defs else ""
        with conn.cursor() as cur:
            cur.execute(f"DROP TABLE IF EXISTS `{table}`")
            cur.execute(f"CREATE TABLE `{table}` ({cols}{index_clause}) "
                        "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
        sql = (f"INSERT INTO `{table}` ({', '.join('`'+c+'`' for c in headers)}) "
               f"VALUES ({', '.join(['%s'] * len(headers))})")

        total = 0
        batch: list[list] = []
        for row in sample:
            batch.append([_to_sql(row.get(c), types[c]) for c in headers])
        # 继续读取剩余行
        for row in reader:
            batch.append([_to_sql(row.get(c), types[c]) for c in headers])
            if len(batch) >= INSERT_BATCH:
                with conn.cursor() as cur:
                    cur.executemany(sql, batch)
                conn.commit()
                total += len(batch)
                batch = []
        if batch:
            with conn.cursor() as cur:
                cur.executemany(sql, batch)
            conn.commit()
            total += len(batch)
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", help="全量 CSV 目录（不传则用 data/sample 演示样本）")
    args = ap.parse_args()

    host = os.environ.get("DB_HOST", "127.0.0.1")
    port = int(os.environ.get("DB_PORT", "3306"))
    user = os.environ.get("DB_USER", "root")
    password = os.environ.get("DB_PASSWORD", "")
    database = os.environ.get("DB_NAME", "olist_ecommerce")

    conn = pymysql.connect(host=host, port=port, user=user,
                           password=password, connect_timeout=10)
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS `{database}` "
                        "DEFAULT CHARSET utf8mb4")
        conn.select_db(database)
        print(f"已连接 {host}:{port}/{database}")

        if args.dir:
            d = Path(args.dir)
            files = sorted(d.glob("mart_order_*.csv"))
        else:
            d = SAMPLE_DIR
            files = [d / n for n in SAMPLE_TABLES]

        for path in files:
            if not path.exists():
                print(f"  缺少 {path.name}，跳过")
                continue
            table = path.stem  # 文件名即表名（mart_order_item_business.csv → mart_order_item_business）
            print(f"  导入 {path.name} → 表 {table} ...")
            n = import_csv(conn, path, table)
            print(f"    ✓ {n} 行")
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES")
            print("当前库表:", [r[0] for r in cur.fetchall()])
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
