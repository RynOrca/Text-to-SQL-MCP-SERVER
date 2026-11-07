"""库结构内省：把数据库"读"成内存对象。

内省（introspection）指程序在运行时主动询问数据库"你有哪些表、每个表有哪些字段"，
而不是由人把表结构写死在代码里。这样做的好处是：换一个库，代码不用改。

**这一版已经不关心具体是哪种数据库了**——字段、外键、行数都从连接器接口拿，
SQLite / MySQL / PostgreSQL 各实现一份，这里的逻辑完全复用。

字段的中文说明来自数据字典（默认 `data/schema_meta.yaml`），
因为字段名往往看不出业务含义——`amt` 到底是原价、实付还是优惠后的金额，只有字典知道。
而这段说明恰恰是模型能不能写对语句的关键信息之一。
"""

from __future__ import annotations

from pathlib import Path

from ..connectors.base import Connector
from .dictionary import SchemaDictionary, load_dictionary
from .models import Column, ForeignKey, Schema, Table


def introspect(connector: Connector, dictionary: SchemaDictionary | None = None) -> Schema:
    """读取整个库的结构。"""
    dict_ = dictionary if dictionary is not None else load_dictionary(connector)
    schema = Schema()
    for name in connector.list_tables():
        columns = [
            Column(
                name=info.name,
                type=info.type,
                nullable=info.nullable,
                is_primary_key=info.is_primary_key,
                description=dict_.column(name, info.name),
            )
            for info in connector.columns(name)
        ]
        foreign_keys = [
            ForeignKey(column=fk.column, ref_table=fk.ref_table, ref_column=fk.ref_column)
            for fk in connector.foreign_keys(name)
        ]
        schema.tables[name] = Table(
            name=name,
            columns=columns,
            description=dict_.table(name),
            row_count=connector.count_rows(name),
            foreign_keys=foreign_keys,
        )
    return schema


def introspect_from_path(db_path: str | Path, dictionary_path: str | Path | None = None) -> Schema:
    """便捷入口：从 SQLite 文件路径直接建结构，供调试脚本用。"""
    from ..connectors.sqlite_connector import SQLiteConnector

    connector = SQLiteConnector(db_path)
    return introspect(connector, load_dictionary(connector, dictionary_path))
