"""库结构的数据模型。

这里的对象是"数据库在内存里的样子"，不是数据库本身。
有了它，检索层可以拿表当对象排序、图扩展可以按外键走、提示词可以按需渲染。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    nullable: bool = True
    is_primary_key: bool = False
    description: str = ""

    def render(self) -> str:
        tags = []
        if self.is_primary_key:
            tags.append("主键")
        if not self.nullable:
            tags.append("非空")
        suffix = f"（{'，'.join(tags)}）" if tags else ""
        desc = f"：{self.description}" if self.description else ""
        return f"  - {self.name} {self.type}{suffix}{desc}"


@dataclass(frozen=True)
class ForeignKey:
    column: str
    ref_table: str
    ref_column: str

    def render(self) -> str:
        return f"{self.column} → {self.ref_table}.{self.ref_column}"


@dataclass
class Table:
    name: str
    columns: list[Column]
    description: str = ""
    row_count: int = 0
    foreign_keys: list[ForeignKey] = field(default_factory=list)

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def column(self, name: str) -> Column | None:
        for c in self.columns:
            if c.name == name:
                return c
        return None

    def render(self) -> str:
        """渲染成提示词里的一段文本。

        注意这里刻意用了"表名 + 中文说明 + 字段清单 + 外键"的固定格式：
        格式越稳定，模型越不容易漏读；也方便评测时做字符串比对。
        """
        head = f"### 表 {self.name}"
        if self.description:
            head += f"（{self.description}）"
        if self.row_count:
            head += f"，约 {self.row_count} 行"
        lines = [head, "字段："]
        lines.extend(c.render() for c in self.columns)
        if self.foreign_keys:
            lines.append("外键：" + "，".join(fk.render() for fk in self.foreign_keys))
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "row_count": self.row_count,
            "columns": [
                {
                    "name": c.name,
                    "type": c.type,
                    "nullable": c.nullable,
                    "is_primary_key": c.is_primary_key,
                    "description": c.description,
                }
                for c in self.columns
            ],
            "foreign_keys": [
                {"column": fk.column, "ref_table": fk.ref_table, "ref_column": fk.ref_column}
                for fk in self.foreign_keys
            ],
        }


@dataclass
class Schema:
    tables: dict[str, Table] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.tables)

    def __contains__(self, name: str) -> bool:
        return name in self.tables

    def get(self, name: str) -> Table | None:
        return self.tables.get(name)

    def names(self) -> list[str]:
        return sorted(self.tables)

    def neighbors(self, table_name: str) -> set[str]:
        """沿外键走一跳，返回所有直接相连的表名。

        为什么需要：向量检索按"语义像不像"召回，但"语义最像的表"往往不是
        "需要连接的表"。连接需求由外键关系决定，是另一个维度的信息。
        """
        table = self.tables.get(table_name)
        if not table:
            return set()
        out: set[str] = set()
        for fk in table.foreign_keys:
            if fk.ref_table in self.tables:
                out.add(fk.ref_table)
        # 反向：别的表指向我，也算相邻
        for other in self.tables.values():
            for fk in other.foreign_keys:
                if fk.ref_table == table_name:
                    out.add(other.name)
        out.discard(table_name)
        return out

    def expand(self, names: Iterable[str], hops: int = 1) -> list[str]:
        """从一组表出发，沿外键扩展若干跳，返回去重后的表名列表。"""
        seen: list[str] = []
        frontier = [n for n in names if n in self.tables]
        for _ in range(max(0, hops)):
            nxt: list[str] = []
            for name in frontier:
                for nb in self.neighbors(name):
                    if nb not in seen and nb not in frontier:
                        nxt.append(nb)
            seen.extend(frontier)
            frontier = nxt
            if not frontier:
                break
        for name in frontier:
            if name not in seen:
                seen.append(name)
        return seen

    def render(self, table_names: Iterable[str] | None = None) -> str:
        """把若干张表渲染成一段文本，供提示词使用。"""
        names = list(table_names) if table_names is not None else self.names()
        blocks = []
        for name in names:
            table = self.tables.get(name)
            if table:
                blocks.append(table.render())
        return "\n\n".join(blocks)

    def to_dict(self) -> dict[str, Any]:
        return {"tables": [t.to_dict() for t in self.tables.values()]}
