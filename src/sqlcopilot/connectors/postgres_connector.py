"""PostgreSQL 连接器。

结构和 MySQL 版一致：会话只读、语句前缀检查、服务端超时。

**未在本机验证**——这台机器上没有 PostgreSQL 服务，也没有驱动。
代码按 PostgreSQL 的语义写，真正接入前请先跑 `python scripts/setup.py --check`。

依赖 psycopg2（`pip install psycopg2-binary`）。
"""

from __future__ import annotations

import re
import time
from urllib.parse import unquote, urlparse

from ..core.interfaces import QueryResult
from .base import ColumnInfo, ForeignKeyInfo

_ALLOWED = re.compile(r"^\s*(select|with|show|explain|table)\b", re.IGNORECASE)
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|truncate|grant|revoke|call|copy)\b",
    re.IGNORECASE,
)


class PostgresConnector:
    dialect = "postgres"

    def __init__(self, dsn: str, timeout_s: float = 10.0, max_rows: int = 500) -> None:
        self.dsn = dsn
        self.timeout_s = timeout_s
        self.max_rows = max_rows
        self._schema = None

    # ---------- 连接 ----------

    def _connect(self):
        try:
            import psycopg2
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "PostgreSQL 连接需要 psycopg2：pip install psycopg2-binary"
            ) from exc

        conn = psycopg2.connect(self.dsn, connect_timeout=5)
        conn.autocommit = True
        cursor = conn.cursor()
        timeout_ms = int(self.timeout_s * 1000)
        for statement in (
            "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
            f"SET statement_timeout = {timeout_ms}",
        ):
            try:
                cursor.execute(statement)
            except Exception:  # noqa: BLE001
                pass
        return conn

    # ---------- 执行 ----------

    def execute(self, sql: str, max_rows: int | None = None) -> QueryResult:
        limit = self.max_rows if max_rows is None else max_rows
        started = time.perf_counter()

        statement = sql.strip().rstrip(";")
        if ";" in statement:
            return QueryResult(error="一次只能执行一条语句")
        if not _ALLOWED.match(statement):
            return QueryResult(error="只允许查询语句（SELECT / WITH / EXPLAIN）")
        forbidden = _FORBIDDEN.search(statement)
        if forbidden:
            return QueryResult(error=f"不允许出现 {forbidden.group(0).upper()} 这类写操作")

        conn = None
        try:
            conn = self._connect()
            cursor = conn.cursor()
            cursor.execute(statement)
            columns = [d[0] for d in cursor.description] if cursor.description else []
            fetched = cursor.fetchmany(limit + 1)
            return QueryResult(
                columns=columns,
                rows=[tuple(row) for row in fetched[:limit]],
                truncated=len(fetched) > limit,
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
        except Exception as exc:  # noqa: BLE001
            message = str(exc)
            if "statement timeout" in message.lower() or "canceling statement" in message.lower():
                message = f"查询超时（超过 {self.timeout_s} 秒），已中断"
            elif "read-only" in message.lower():
                message = "被拒绝了：当前会话是只读的"
            return QueryResult(
                elapsed_ms=(time.perf_counter() - started) * 1000, error=message
            )
        finally:
            if conn is not None:
                conn.close()

    # ---------- 元数据 ----------

    def quote(self, name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    def _schema_name(self) -> str:
        if self._schema is None:
            result = self.execute("SELECT current_schema()")
            self._schema = str(result.rows[0][0]) if result.ok and result.rows else "public"
        return self._schema

    def list_tables(self, include_meta: bool = False) -> list[str]:
        result = self.execute(
            "SELECT table_name FROM information_schema.tables "
            f"WHERE table_schema = '{self._schema_name()}' AND table_type = 'BASE TABLE' "
            "ORDER BY table_name"
        )
        names = [str(row[0]) for row in result.rows] if result.ok else []
        if include_meta:
            return names
        return [n for n in names if not n.startswith("_meta_")]

    def columns(self, table: str) -> list[ColumnInfo]:
        # 用 EXISTS 判断主键，而不是 JOIN 约束表：
        # 一个字段可能同时参与主键和外键，JOIN 会把同一行放大成多行。
        schema = self._schema_name()
        result = self.execute(
            "SELECT c.column_name, c.data_type, c.is_nullable, "
            "EXISTS ("
            "  SELECT 1 FROM information_schema.table_constraints tc "
            "  JOIN information_schema.key_column_usage k "
            "    ON k.constraint_name = tc.constraint_name "
            "   AND k.table_schema = tc.table_schema "
            "  WHERE tc.constraint_type = 'PRIMARY KEY' "
            f"   AND tc.table_schema = '{schema}' AND tc.table_name = '{table}' "
            "   AND k.column_name = c.column_name"
            ") AS is_pk "
            "FROM information_schema.columns c "
            f"WHERE c.table_schema = '{schema}' AND c.table_name = '{table}' "
            "ORDER BY c.ordinal_position"
        )
        if not result.ok:
            return []
        return [
            ColumnInfo(
                name=str(row[0]),
                type=str(row[1]).upper(),
                nullable=str(row[2]).upper() == "YES",
                is_primary_key=bool(row[3]),
            )
            for row in result.rows
        ]

    def foreign_keys(self, table: str) -> list[ForeignKeyInfo]:
        result = self.execute(
            "SELECT kcu.column_name, ccu.table_name, ccu.column_name "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu ON kcu.constraint_name = tc.constraint_name "
            "JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name = tc.constraint_name "
            f"WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = '{self._schema_name()}' "
            f"AND tc.table_name = '{table}'"
        )
        if not result.ok:
            return []
        return [
            ForeignKeyInfo(column=str(r[0]), ref_table=str(r[1]), ref_column=str(r[2]))
            for r in result.rows
        ]

    def count_rows(self, table: str) -> int:
        result = self.execute(f"SELECT COUNT(*) FROM {self.quote(table)}")
        return int(result.rows[0][0]) if result.ok and result.rows else 0

    def sample_values(self, table: str, column: str, limit: int = 10) -> list[str]:
        sql = (
            f"SELECT DISTINCT {self.quote(column)} FROM {self.quote(table)} "
            f"WHERE {self.quote(column)} IS NOT NULL LIMIT {int(limit)}"
        )
        result = self.execute(sql)
        return [str(r[0]) for r in result.rows] if result.ok else []
