"""看一眼演示库的结构，确认内省可用。

运行：python scripts/inspect_schema.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlcopilot.connectors import SQLiteConnector  # noqa: E402
from sqlcopilot.core import load_config  # noqa: E402
from sqlcopilot.schema import introspect_from_path  # noqa: E402


def main() -> None:
    cfg = load_config(ROOT / "config" / "default.yaml")
    db_path = ROOT / cfg.get("database.path")
    schema = introspect_from_path(db_path)

    print(f"数据库：{db_path}")
    print(f"共 {len(schema)} 张表\n")
    for name in schema.names():
        table = schema.get(name)
        assert table is not None
        desc = table.description[:34]
        print(f"  {name:<20}{len(table.columns):>3} 字段{table.row_count:>8} 行   {desc}")

    print("\n外键扩展：从 orders 出发走一跳 →")
    print("  ", sorted(schema.expand(["orders"], hops=1)))

    print("\n交给模型的结构片段（截取 orders）：")
    print("-" * 60)
    print(schema.render(["orders"]))
    print("-" * 60)

    print("\n只读沙箱自测：")
    conn = SQLiteConnector(db_path, timeout_s=5, max_rows=5)
    ok = conn.execute("SELECT COUNT(*) FROM orders")
    print(f"  正常查询 → ok={ok.ok}, 结果={ok.rows}")

    bad = conn.execute("DELETE FROM orders")
    print(f"  删除语句 → ok={bad.ok}, 错误={bad.error}")

    heavy = conn.execute(
        "SELECT COUNT(*) FROM orders a, orders b, orders c, orders d"
    )
    print(f"  慢查询   → ok={heavy.ok}, 错误={heavy.error}")


if __name__ == "__main__":
    main()
