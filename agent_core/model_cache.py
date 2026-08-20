"""模型矩阵磁盘缓存：缓存特征工程后的 DataFrame，跳过重复的取数与特征工程。

归因分析的数据源（Mart 宽表）是静态的，因此「拉数据 + 特征工程」的产物是确定的，
可跨请求复用。缓存 key 由「表名 + 排序列 + 行数指纹」构成；行数变化（数据更新）时
key 自动改变，旧缓存自然失效。

缓存文件落在 artifacts/model_cache/（已被 .gitignore 忽略）。
"""
from __future__ import annotations

import hashlib
import pickle
from pathlib import Path
from typing import Callable

import pandas as pd

from .data_provider import DataProvider
from .statistics import load_table

_CACHE_ROOT = Path(__file__).resolve().parent.parent / "artifacts" / "model_cache"
_FRAME_SCHEMA = "v1"


def _row_count(provider: DataProvider, table: str, where: str | None,
               sql_sink: list[str] | None) -> int:
    sql = f"SELECT COUNT(*) AS n FROM {table}"
    if where:
        sql += f" WHERE {where}"
    sql += " LIMIT 1"  # MySQLProvider 强制要求 LIMIT
    if sql_sink is not None:
        sql_sink.append(sql)
    rows = provider.execute(sql)
    return int(rows[0]["n"]) if rows else 0


def cached_frame(
    provider: DataProvider,
    table: str,
    columns: list[str],
    where: str | None,
    engineer: Callable[[pd.DataFrame], pd.DataFrame],
    sql_sink: list[str] | None = None,
) -> pd.DataFrame:
    """返回特征工程后的 DataFrame，命中磁盘缓存时跳过取数与特征工程。

    - 未命中：load_table 拉取原始行 → engineer(df) 特征工程 → 落盘缓存
    - 命中：直接读缓存（数据指纹按行数校验，行数变化自动失效）
    """
    n = _row_count(provider, table, where, sql_sink)
    raw_key = f"{_FRAME_SCHEMA}|{table}|{','.join(sorted(columns))}|{n}"
    key = hashlib.sha1(raw_key.encode("utf-8")).hexdigest()[:16]
    path = _CACHE_ROOT / f"{key}.pkl"

    if path.exists():
        try:
            return pd.read_pickle(path)
        except Exception:
            pass  # 缓存损坏则回退到重建

    df = load_table(provider, table, columns, where=where, sql_sink=sql_sink)
    df = engineer(df)
    try:
        _CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        df.to_pickle(path)
    except Exception:
        pass  # 写缓存失败不影响主流程
    return df
