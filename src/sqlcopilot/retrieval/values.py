"""取值索引：把"库里到底存了什么"也变成可检索的信息。

为什么需要它：用户说"华东区"，库里存的是 `regions.name = '华东'`；
用户说"退过款"，库里存的是 `refunds.status = '已同意'`。
模型看不到这些取值，就会写 `WHERE region = '华东区'`——语法正确、结果为零，
而且**不会报错**。这是最危险的一类错误。

做法：只给"取值很少"的文本字段建样本（状态、渠道、城市名这类），
日期、编号这类高基数字段直接跳过，否则样本会撑爆提示词。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..schema.models import Schema


class _ConnectorLike(Protocol):
    def sample_values(self, table: str, column: str, limit: int = 10) -> list[str]: ...


@dataclass
class ValueIndex:
    samples: dict[tuple[str, str], list[str]] = field(default_factory=dict)

    def get(self, table: str, column: str) -> list[str]:
        return self.samples.get((table, column), [])

    def render(self, tables: list[str]) -> str:
        lines: list[str] = []
        for table in tables:
            for (t, column), values in self.samples.items():
                if t != table:
                    continue
                lines.append(f"- {table}.{column}：{' / '.join(values[:12])}")
        if not lines:
            return ""
        return "字段取值参考（写条件时请使用这些原始值）：\n" + "\n".join(lines)

    def render_for(self, question: str, tables: list[str]) -> str:
        """只注入"问题里真的提到"的取值。

        为什么不再全量渲染：实测发现，把每张候选表的所有取值样本都塞进提示词，
        会让准确率下降约 3 个百分点——噪声盖过了那点有用的信息。
        取值索引要解决的只是"用户说的词"和"库里存的字面值"之间的对不上，
        所以只有问题里出现的取值才值得注入。
        """
        lines: list[str] = []
        for (table, column), values in self.samples.items():
            if table not in tables:
                continue
            matched = [v for v in values if v and v in question]
            if matched:
                lines.append(f"- {table}.{column}：{' / '.join(matched)}")
        if not lines:
            return ""
        return "问题中提到的取值，在库里的实际写法是：\n" + "\n".join(lines)

    def search_terms(self, text: str) -> list[tuple[str, str, str]]:
        """找出文本里提到了哪些取值，返回 (表, 字段, 取值)。"""
        hits: list[tuple[str, str, str]] = []
        for (table, column), values in self.samples.items():
            for value in values:
                if value and value in text:
                    hits.append((table, column, value))
        return hits


def build_value_index(
    connector: _ConnectorLike,
    schema: Schema,
    max_distinct: int = 30,
) -> ValueIndex:
    samples: dict[tuple[str, str], list[str]] = {}
    for table_name, table in schema.tables.items():
        for column in table.columns:
            if column.is_primary_key or column.type != "TEXT":
                continue
            values = connector.sample_values(table_name, column.name, max_distinct + 1)
            # 只保留"取值少且稳定"的字段；高基数字段（日期、编号）不建样本
            if 0 < len(values) <= max_distinct:
                samples[(table_name, column.name)] = sorted(values)
    return ValueIndex(samples=samples)
