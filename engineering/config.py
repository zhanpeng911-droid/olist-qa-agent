from __future__ import annotations

import os
import re
from pathlib import Path

import pymysql
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
DATASET = os.getenv('OLIST_SOURCE_DIR', r'C:\ShouldStudySoStudy\internship\Brazilian E-Commerce Public Dataset\dataset')
ARTIFACTS = Path(os.getenv('OLIST_ARTIFACTS_DIR', str(ROOT / 'artifacts' / 'engineering')))
DATABASE = os.getenv('DB_NAME', 'olist_ecommerce')


def ident(value: str) -> str:
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', value):
        raise ValueError('数据库或表名不符合命名规则')
    return f'`{value}`'


def connect(database: str | None = None, *, stream=False, read_only=False):
    return pymysql.connect(
        host=os.getenv('DB_HOST', '127.0.0.1'), port=int(os.getenv('DB_PORT', '3306')),
        user=os.getenv('QUERY_DB_USER') if read_only and os.getenv('QUERY_DB_USER') else os.getenv('DB_USER', 'root'),
        password=os.getenv('QUERY_DB_PASSWORD', '') if read_only and os.getenv('QUERY_DB_USER') else os.getenv('DB_PASSWORD', ''),
        database=database, charset='utf8mb4', autocommit=True,
        connect_timeout=5, read_timeout=600, write_timeout=600,
        cursorclass=pymysql.cursors.SSDictCursor if stream else pymysql.cursors.DictCursor,
    )
