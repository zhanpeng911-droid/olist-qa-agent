"""语义层：加载语义字典 metrics_dict.yaml，作为指标/维度/口径的唯一真相源。

模型只能在这里"选"预置的指标与维度，不能自创，从而锁死业务口径。
"""
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "semantics" / "metrics_dict.yaml"


class SemanticLayer:
    """封装语义字典，提供表/指标/维度/筛选的读取与校验。"""

    def __init__(self, yaml_path: Path | str = _DEFAULT_PATH) -> None:
        with open(yaml_path, encoding="utf-8") as f:
            self._data: dict[str, Any] = yaml.safe_load(f)
        self.tables = self._data["tables"]
        self.guards = self._data.get("guards", {})

    # ---- 读取 ----
    def table_names(self) -> list[str]:
        return list(self.tables.keys())

    def get_metrics(self, table: str) -> dict[str, dict]:
        return self.tables[table].get("metrics", {})

    def get_dimensions(self, table: str) -> list[str]:
        return self.tables[table].get("dimensions", [])

    def get_filters(self, table: str) -> dict[str, str]:
        return self.tables[table].get("filters", {})

    def allowed_tables(self) -> list[str]:
        return self.guards.get("allow_tables", list(self.tables.keys()))

    # ---- 校验 ----
    def check_metric(self, table: str, metric: str) -> str | None:
        """返回指标 SQL 表达式；若指标不存在返回 None。"""
        return self.get_metrics(table).get(metric, {}).get("expr")

    def check_dimension(self, table: str, dim: str) -> bool:
        return dim in self.get_dimensions(table)

    # ---- 描述（供 LLM 提示词用）----
    def describe(self, table: str) -> str:
        """生成表的指标+维度描述文本，供 ReAct 的 system prompt 注入。"""
        t = self.tables[table]
        lines = [f"[{table}] {t.get('desc', '')}", "  指标:"]
        for name, m in self.get_metrics(table).items():
            lines.append(f"    - {name}: {m.get('desc', '')}")
        lines.append("  维度: " + ", ".join(self.get_dimensions(table)))
        return "\n".join(lines)

    def describe_all(self) -> str:
        return "\n\n".join(self.describe(t) for t in self.table_names())
