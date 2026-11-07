"""数据库连接器：把"怎么连数据库"这件事隔离出来。

支持的数据库：
- sqlite：单文件，零依赖，开箱即用
- mysql / mariadb：需要 pymysql
- postgres：需要 psycopg2

选哪个由配置里的 `database.dialect` 决定，代码不用改。
"""

from __future__ import annotations

from ..core.config import Config
from .base import ColumnInfo, Connector, ForeignKeyInfo
from .mysql_connector import MySQLConnector
from .postgres_connector import PostgresConnector
from .sqlite_connector import SQLiteConnector

__all__ = [
    "ColumnInfo",
    "Connector",
    "ForeignKeyInfo",
    "MySQLConnector",
    "PostgresConnector",
    "SQLiteConnector",
    "build_connector",
]

DIALECTS = ("sqlite", "mysql", "mariadb", "postgres", "postgresql")


def build_connector(cfg: Config) -> Connector:
    """按配置造一个连接器。"""
    dialect = str(cfg.get("database.dialect", "sqlite")).lower()
    timeout_s = float(cfg.get("executor.timeout_s", 10.0))
    max_rows = int(cfg.get("executor.max_rows", 500))

    if dialect == "sqlite":
        return SQLiteConnector(cfg.get("database.path"), timeout_s, max_rows)

    dsn = cfg.get("database.dsn") or cfg.get("database.path")
    if not dsn:
        raise RuntimeError(f"{dialect} 需要配置 database.dsn")

    if dialect in {"mysql", "mariadb"}:
        return MySQLConnector(str(dsn), timeout_s, max_rows)
    if dialect in {"postgres", "postgresql"}:
        return PostgresConnector(str(dsn), timeout_s, max_rows)

    raise ValueError(f"不支持的数据库类型：{dialect}，可选 {DIALECTS}")
