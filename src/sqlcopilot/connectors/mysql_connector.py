"""MySQL / MariaDB 连接器。

只读是怎么保证的（三道，和 SQLite 版对应）：

1. `SET SESSION TRANSACTION READ ONLY` —— 会话级只读，写操作直接被拒
2. 语句前缀检查 —— 只放行 SELECT / WITH / SHOW / EXPLAIN
3. `MAX_EXECUTION_TIME` —— 超时由服务端中断，不依赖客户端计时

前两道防"改数据"，第三道防"跑太久"。

依赖 pymysql（`pip install pymysql`）。没装的话只有在真正用到时才会报错，
不影响其他数据库类型。
"""

from __future__ import annotations

import re
import time
from urllib.parse import unquote, urlparse

from ..core.interfaces import QueryResult
from .base import ColumnInfo, ForeignKeyInfo

_ALLOWED = re.compile(r"^\s*(select|with|show|explain|describe)\b", re.IGNORECASE)
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|truncate|grant|revoke|call)\b",
    re.IGNORECASE,
)


def parse_dsn(dsn: str) -> dict:
    """把 mysql://user:pass@host:3306/db 拆成连接参数。"""
    if "://" not in dsn:
        raise ValueError(
            "MySQL 连接串格式应为 mysql://用户:密码@主机:端口/库名"
        )
    parsed = urlparse(dsn)
    return {
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 3306,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": (parsed.path or "/").lstrip("/"),
    }


class MySQLConnector:
    dialect = "mysql"

    def __init__(self, dsn: str, timeout_s: float = 10.0, max_rows: int = 500) -> None:
        self.dsn = dsn
        self.params = parse_dsn(dsn)
        self.timeout_s = timeout_s
        self.max_rows = max_rows
        self._tables_cache: list[str] | None = None

    # ---------- 连接 ----------

    def _connect(self):
        try:
            import pymysql
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("MySQL 连接需要 pymysql：pip install pymysql") from exc

        conn = pymysql.connect(
            host=self.params["host"],
            port=self.params["port"],
            user=self.params["user"],
            password=self.params["password"],
            database=self.params["database"],
            charset="utf8mb4",
            connect_timeout=5,
            read_timeout=int(self.timeout_s) + 5,
            autocommit=True,
        )
        cursor = conn.cursor()
        # 第一道锁：会话只读。老版本不支持就跳过，靠第二道兜底。
        for statement in (
            "SET SESSION TRANSACTION READ ONLY",
            f"SET SESSION MAX_EXECUTION_TIME={int(self.timeout_s * 1000)}",
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
            return QueryResult(error="只允许查询语句（SELECT / WITH / SHOW / EXPLAIN）")
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
            if "3024" in message or "max_execution_time" in message.lower():
                message = f"查询超时（超过 {self.timeout_s} 秒），已中断"
            elif "read-only" in message.lower() or "read only" in message.lower():
                message = "被拒绝了：当前会话是只读的"
            return QueryResult(
                elapsed_ms=(time.perf_counter() - started) * 1000, error=message
            )
        finally:
            if conn is not None:
                conn.close()

    # ---------- 元数据 ----------

    def quote(self, name: str) -> str:
        return "`" + name.replace("`", "``") + "`"

    def list_tables(self, include_meta: bool = False) -> list[str]:
        if self._tables_cache is None:
            result = self.execute(
                "SELECT table_name FROM information_schema.tables "
                f"WHERE table_schema = '{self.params['database']}' AND table_type = 'BASE TABLE' "
                "ORDER BY table_name"
            )
            self._tables_cache = [str(row[0]) for row in result.rows] if result.ok else []
        names = list(self._tables_cache)
        if include_meta:
            return names
        return [n for n in names if not n.startswith("_meta_")]

    def columns(self, table: str) -> list[ColumnInfo]:
        result = self.execute(
            "SELECT column_name, column_type, is_nullable, column_key "
            "FROM information_schema.columns "
            f"WHERE table_schema = '{self.params['database']}' AND table_name = '{table}' "
            "ORDER BY ordinal_position"
        )
        if not result.ok:
            return []
        return [
            ColumnInfo(
                name=str(row[0]),
                type=str(row[1]).upper(),
                nullable=str(row[2]).upper() == "YES",
                is_primary_key=str(row[3]).upper() == "PRI",
            )
            for row in result.rows
        ]

    def foreign_keys(self, table: str) -> list[ForeignKeyInfo]:
        result = self.execute(
            "SELECT column_name, referenced_table_name, referenced_column_name "
            "FROM information_schema.key_column_usage "
            f"WHERE table_schema = '{self.params['database']}' AND table_name = '{table}' "
            "AND referenced_table_name IS NOT NULL"
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
