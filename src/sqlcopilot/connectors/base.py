"""连接器的公共契约。

为什么要有这一层：不同数据库的 SQL 方言、只读机制、元数据查询方式完全不同，
但上层的检索、提示词、评测不应该关心这些差异。

所以定一组最小接口：执行、列表、字段、外键、行数、取值样本、标识符引用。
每个数据库实现一份，上层只跟这个接口打交道。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..core.interfaces import QueryResult


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    type: str
    nullable: bool = True
    is_primary_key: bool = False


@dataclass(frozen=True)
class ForeignKeyInfo:
    column: str
    ref_table: str
    ref_column: str


@runtime_checkable
class Connector(Protocol):
    """所有数据库连接器都要满足的接口。"""

    dialect: str

    def list_tables(self) -> list[str]:
        """返回业务表名（不含系统表和字典表）。"""
        ...

    def execute(self, sql: str, max_rows: int | None = None) -> QueryResult:
        """在只读沙箱里执行一条查询。"""
        ...

    def columns(self, table: str) -> list[ColumnInfo]:
        ...

    def foreign_keys(self, table: str) -> list[ForeignKeyInfo]:
        ...

    def row_count(self, table: str) -> int:
        ...

    def sample_values(self, table: str, column: str, limit: int = 10) -> list[str]:
        ...

    def quote(self, name: str) -> str:
        """给标识符加引号，防止表名里的特殊字符出问题。"""
        ...
