"""数据字典：表和字段的业务含义。

为什么单独做成一个模块，而且优先读文件：

客户的库通常是**只读**的，我们不能像演示库那样在里面建 `_meta_tables` 两张表。
所以字典必须能放在库外面——一个 YAML 文件，跟着项目走。

加载顺序：
1. `data/schema_meta.yaml`（推荐，客户自己写）
2. 库里的 `_meta_tables` / `_meta_columns`（演示库用，向后兼容）
3. 都没有 → 空字典，靠字段名硬猜

文件格式：

    tables:
      orders:
        description: 订单主表，一行一个订单
        columns:
          pay_amount: 实付金额（原价减优惠）
          status: 订单状态：已支付/已完成/已取消/已退款
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

META_TABLE_PREFIX = "_meta_"


@dataclass
class SchemaDictionary:
    tables: dict[str, str] = field(default_factory=dict)
    columns: dict[tuple[str, str], str] = field(default_factory=dict)
    source: str = "<empty>"

    def table(self, name: str) -> str:
        return self.tables.get(name, "")

    def column(self, table: str, column: str) -> str:
        return self.columns.get((table, column), "")

    @property
    def is_empty(self) -> bool:
        """一条有效说明都没有，就算空。

        注意不能只看"有没有键"：初始化时生成的模板里键全在、值全是空字符串，
        如果按键判断，它会盖掉库里原有的字典，把说明全清空。
        """
        return not any(self.tables.values()) and not any(self.columns.values())

    # ---------- 加载 ----------

    @classmethod
    def from_yaml(cls, path: str | Path) -> "SchemaDictionary":
        file = Path(path)
        if not file.exists():
            return cls()
        if yaml is None:
            raise RuntimeError("读取字典需要 PyYAML：pip install pyyaml")
        raw: dict[str, Any] = yaml.safe_load(file.read_text(encoding="utf-8")) or {}

        tables: dict[str, str] = {}
        columns: dict[tuple[str, str], str] = {}
        for table_name, payload in (raw.get("tables") or {}).items():
            if not isinstance(payload, dict):
                continue
            tables[table_name] = str(payload.get("description", "") or "")
            for column_name, description in (payload.get("columns") or {}).items():
                columns[(table_name, column_name)] = str(description or "")
        return cls(tables=tables, columns=columns, source=str(file))

    @classmethod
    def from_connector(cls, connector) -> "SchemaDictionary":
        """从库里的字典表读。没有就返回空字典，不报错。"""
        tables: dict[str, str] = {}
        columns: dict[tuple[str, str], str] = {}
        try:
            names = {name.lower() for name in connector.list_tables(include_meta=True)}
        except TypeError:
            return cls()
        except Exception:  # noqa: BLE001 - 字典读不到不该让主流程崩
            return cls()

        if f"{META_TABLE_PREFIX}tables" in names:
            result = connector.execute(f"SELECT table_name, description FROM {META_TABLE_PREFIX}tables")
            if result.ok:
                for name, desc in result.rows:
                    tables[str(name)] = str(desc or "")
        if f"{META_TABLE_PREFIX}columns" in names:
            result = connector.execute(
                f"SELECT table_name, column_name, description FROM {META_TABLE_PREFIX}columns"
            )
            if result.ok:
                for table, column, desc in result.rows:
                    columns[(str(table), str(column))] = str(desc or "")
        return cls(tables=tables, columns=columns, source="<database>")


def load_dictionary(connector, path: str | Path | None = None) -> SchemaDictionary:
    """按优先级加载字典：文件优先，库里兜底。"""
    if path:
        file_dict = SchemaDictionary.from_yaml(path)
        if not file_dict.is_empty:
            return file_dict
    return SchemaDictionary.from_connector(connector)


def dump_template(connector, path: str | Path) -> Path:
    """根据库结构生成一份字典模板，说明留空等人填。

    初始化时用：客户跑一下，得到一个填好的骨架，只需要补业务含义。
    """
    if yaml is None:
        raise RuntimeError("生成模板需要 PyYAML：pip install pyyaml")
    payload: dict[str, Any] = {"tables": {}}
    for name in connector.list_tables():
        payload["tables"][name] = {
            "description": "",
            "columns": {column.name: "" for column in connector.columns(name)},
        }
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return file
