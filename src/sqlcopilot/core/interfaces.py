"""组件契约（接口）。

这里只规定"长什么样"，不规定"怎么做"。

这样做的原因：项目的每一层都可能有多种实现（比如嵌入向量可以来自真实模型，
也可以来自离线的简易哈希），只要它们满足同一个契约，就可以通过配置文件互换，
而不需要改动调用方的代码。这也让"消融实验"变成改一行配置的事。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence, runtime_checkable


@dataclass
class QueryResult:
    """一次查询的结果。

    columns     结果集的列名
    rows        结果集的行，每行是一个元组
    truncated   是否因为行数上限被截断（用于提示"你看到的不是全部"）
    elapsed_ms  执行耗时
    error       出错时的错误信息；为 None 表示成功
    """

    columns: list[str] = field(default_factory=list)
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    truncated: bool = False
    elapsed_ms: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def as_dicts(self) -> list[dict[str, Any]]:
        """转成字典列表，方便打印或序列化。"""
        return [dict(zip(self.columns, row)) for row in self.rows]


@runtime_checkable
class Connector(Protocol):
    """数据库连接器。只读是硬约束，任何实现都不允许写入。"""

    def list_tables(self) -> list[str]:
        """返回库里所有业务表的名字（不含元数据表）。"""
        ...

    def execute(self, sql: str, max_rows: int = 500) -> QueryResult:
        """在只读沙箱里执行一条查询。"""
        ...


@runtime_checkable
class Embedder(Protocol):
    """把文本变成向量。"""

    dimension: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        ...


@runtime_checkable
class Reranker(Protocol):
    """对候选做精排。返回按相关性降序的下标列表。"""

    def rerank(self, query: str, candidates: Sequence[str], top_n: int) -> list[int]:
        ...


@runtime_checkable
class VectorStore(Protocol):
    """向量索引。"""

    def add(
        self,
        ids: Sequence[str],
        vectors: Sequence[Sequence[float]],
        payloads: Sequence[dict[str, Any]],
    ) -> None:
        ...

    def search(self, vector: Sequence[float], k: int) -> list[tuple[str, float]]:
        """返回 (id, 相似度) 列表，按相似度降序。"""
        ...


@runtime_checkable
class Llm(Protocol):
    """大模型客户端。"""

    name: str

    def complete(self, system: str, user: str) -> str:
        """给定系统提示词与用户提示词，返回模型生成的文本。"""
        ...
