"""SQLite 连接器与只读沙箱。

这个模块要解决的核心问题：**模型生成的语句是不可信的**。

模型可能生成删除语句、可能生成死循环式的笛卡尔连接、可能一次返回一千万行。
所以执行之前必须上三道锁：

1. `PRAGMA query_only`：让这个连接在数据库层面拒绝任何写操作。
2. 授权回调（authorizer）：在语句"准备阶段"逐个动作审批，只放行查询相关的动作。
3. 进度回调（progress handler）：执行过程中按时间检查，超时立刻中断。

三道锁的分工不同：第一道管"写"，第二道管"越权"，第三道管"跑太久"。
只上一道都不够——这正是面试里最容易被追问的地方。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from ..core.interfaces import QueryResult
from .base import ColumnInfo, ForeignKeyInfo

# 只放行与"读"有关的授权动作。
# 其他动作（插入、更新、删除、建表、删表、事务、附加数据库……）一律拒绝。
_ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,   # 准备执行一条查询
    sqlite3.SQLITE_READ,     # 读取某个表的某个字段
    sqlite3.SQLITE_FUNCTION, # 调用内置函数
}
if hasattr(sqlite3, "SQLITE_RECURSIVE"):  # 公共表表达式（WITH 语句）需要
    _ALLOWED_ACTIONS.add(sqlite3.SQLITE_RECURSIVE)


def _authorizer(action: int, arg1, arg2, dbname, trigger) -> int:
    return sqlite3.SQLITE_OK if action in _ALLOWED_ACTIONS else sqlite3.SQLITE_DENY


class SQLiteConnector:
    """一个只读的 SQLite 连接器。

    两类连接：
    - `execute()` 里开的是**沙箱连接**，跑用户/模型给的语句；
    - `open_raw()` 开的是**内部连接**，只给库结构内省用，绝不执行外部输入。
    """

    def __init__(self, db_path: str | Path, timeout_s: float = 10.0, max_rows: int = 500) -> None:
        self.dialect = "sqlite"
        self.db_path = Path(db_path)
        self.timeout_s = timeout_s
        self.max_rows = max_rows

    # ---------- 内部连接 ----------

    def open_raw(self) -> sqlite3.Connection:
        """不加任何限制的连接，仅供内省读取元数据使用。"""
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"数据库不存在：{self.db_path}。请先运行 python scripts/init_db.py"
            )
        return sqlite3.connect(str(self.db_path))

    # ---------- 沙箱连接 ----------

    def _open_sandbox(self) -> sqlite3.Connection:
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"数据库不存在：{self.db_path}。请先运行 python scripts/init_db.py"
            )
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.execute("PRAGMA query_only = ON")  # 第一道锁：拒绝写
        conn.set_authorizer(_authorizer)        # 第二道锁：逐动作审批
        return conn

    def _install_timeout(self, conn: sqlite3.Connection) -> None:
        """第三道锁：每执行一万条虚拟机指令检查一次时间，超时就返回非零值中断。"""
        deadline = time.monotonic() + self.timeout_s

        def _handler() -> int:
            return 1 if time.monotonic() > deadline else 0

        conn.set_progress_handler(_handler, 10_000)

    # ---------- 对外接口 ----------

    def list_tables(self, include_meta: bool = False) -> list[str]:
        sql = (
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        rows = self.execute(sql).rows
        names = [str(r[0]) for r in rows]
        if include_meta:
            return names
        return [n for n in names if not n.startswith("_meta_")]

    def execute(self, sql: str, max_rows: int | None = None) -> QueryResult:
        limit = self.max_rows if max_rows is None else max_rows
        started = time.perf_counter()
        conn = self._open_sandbox()
        try:
            self._install_timeout(conn)
            cur = conn.execute(sql)
            columns = [d[0] for d in cur.description] if cur.description else []
            # 多取一行用来判断"是否被截断"
            fetched = cur.fetchmany(limit + 1)
            truncated = len(fetched) > limit
            return QueryResult(
                columns=columns,
                rows=fetched[:limit],
                truncated=truncated,
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
        except sqlite3.Error as exc:
            message = str(exc)
            if "interrupted" in message:
                message = f"查询超时（超过 {self.timeout_s} 秒），已中断"
            elif "not authorized" in message:
                message = "被拒绝了：这条语句包含只读沙箱不允许的操作"
            return QueryResult(
                elapsed_ms=(time.perf_counter() - started) * 1000,
                error=message,
            )
        finally:
            conn.close()

    # ---------- 便捷方法 ----------

    def quote(self, name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    def columns(self, table: str) -> list[ColumnInfo]:
        conn = self.open_raw()
        try:
            rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        finally:
            conn.close()
        return [
            ColumnInfo(
                name=row[1],
                type=(row[2] or "").upper() or "TEXT",
                nullable=not row[3],
                is_primary_key=row[5] > 0,
            )
            for row in rows
        ]

    def foreign_keys(self, table: str) -> list[ForeignKeyInfo]:
        conn = self.open_raw()
        try:
            rows = conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
        finally:
            conn.close()
        return [ForeignKeyInfo(column=r[3], ref_table=r[2], ref_column=r[4]) for r in rows]

    def count_rows(self, table: str) -> int:
        result = self.execute(f'SELECT COUNT(*) FROM "{table}"')
        return int(result.rows[0][0]) if result.ok and result.rows else 0

    def sample_values(self, table: str, column: str, limit: int = 10) -> list[str]:
        sql = (
            f'SELECT DISTINCT "{column}" FROM "{table}" '
            f'WHERE "{column}" IS NOT NULL LIMIT {int(limit)}'
        )
        result = self.execute(sql)
        return [str(r[0]) for r in result.rows] if result.ok else []
