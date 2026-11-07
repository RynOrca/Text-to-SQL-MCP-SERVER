"""验证 PostgreSQL 连接器：建库、灌数据、跑一遍全部能力。

需要一台可连的 PostgreSQL。本机验证时这样起一个临时实例（不装服务、不碰系统）：

    initdb -D _pgtest/data -U postgres --auth=trust -E UTF8
    pg_ctl -D _pgtest/data -o "-p 5433" start

然后：
    python scripts/verify_postgres.py --dsn "postgresql://postgres:@127.0.0.1:5433/askdata_demo"

检查和 MySQL 版一致：列 / 外键 / 行数 / 取值样本 / 只读防护 / 行数截断 / 字典加载。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

SQLITE_DB = ROOT / "data" / "demo_shop.db"
TABLES = ["regions", "customers", "orders", "order_items", "refunds"]

DDL = [
    """CREATE TABLE regions (
        id INT PRIMARY KEY, name VARCHAR(64) NOT NULL, level INT NOT NULL,
        parent_id INT REFERENCES regions(id)
    )""",
    """CREATE TABLE customers (
        id INT PRIMARY KEY, name VARCHAR(64) NOT NULL, gender VARCHAR(8),
        birth_date VARCHAR(16), register_date VARCHAR(16) NOT NULL,
        region_id INT REFERENCES regions(id), channel VARCHAR(32)
    )""",
    """CREATE TABLE orders (
        id INT PRIMARY KEY, order_no VARCHAR(32) NOT NULL,
        customer_id INT REFERENCES customers(id),
        order_date VARCHAR(16) NOT NULL, status VARCHAR(16),
        total_amount NUMERIC(12,2), discount_amount NUMERIC(12,2), pay_amount NUMERIC(12,2),
        campaign_id INT
    )""",
    """CREATE TABLE order_items (
        id INT PRIMARY KEY, order_id INT REFERENCES orders(id), product_id INT,
        quantity INT, unit_price NUMERIC(12,2), amount NUMERIC(12,2)
    )""",
    """CREATE TABLE refunds (
        id INT PRIMARY KEY, order_id INT REFERENCES orders(id), reason VARCHAR(32),
        amount NUMERIC(12,2), created_at VARCHAR(32), status VARCHAR(16)
    )""",
    """CREATE TABLE _meta_tables (table_name VARCHAR(64) PRIMARY KEY, description VARCHAR(255))""",
    """CREATE TABLE _meta_columns (
        table_name VARCHAR(64), column_name VARCHAR(64), description VARCHAR(255),
        PRIMARY KEY (table_name, column_name)
    )""",
]


def copy_data(dsn: str) -> None:
    import psycopg2
    from psycopg2.extras import execute_values

    source = sqlite3.connect(str(SQLITE_DB))
    target = psycopg2.connect(dsn)
    target.set_client_encoding("UTF8")
    cursor = target.cursor()
    for statement in DDL:
        cursor.execute(statement)

    for table in TABLES + ["_meta_tables", "_meta_columns"]:
        rows = source.execute(f'SELECT * FROM "{table}"').fetchall()
        if not rows:
            continue
        execute_values(
            cursor,
            f'INSERT INTO "{table}" VALUES %s',
            rows,
            page_size=1000,
        )
        print(f"  灌入 {table:<16}{len(rows):>6} 行")
    target.commit()
    target.close()
    source.close()


def check(connector) -> int:
    failures = 0

    tables = connector.list_tables()
    print(f"\n[1] 表清单：{len(tables)} 张 -> {tables}")
    if "_meta_tables" in tables:
        print("    ✗ 字典表没有被过滤掉")
        failures += 1

    columns = connector.columns("orders")
    print(f"[2] orders 字段：{[c.name for c in columns]}")
    if not columns or not any(c.is_primary_key for c in columns):
        print("    ✗ 没有读出主键")
        failures += 1

    fks = connector.foreign_keys("orders")
    print(f"[3] orders 外键：{[(f.column, f.ref_table) for f in fks]}")
    if not fks:
        print("    ✗ 没有读出外键")
        failures += 1

    count = connector.count_rows("orders")
    print(f"[4] orders 行数：{count}")
    if count <= 0:
        print("    ✗ 行数为 0")
        failures += 1

    values = connector.sample_values("orders", "status", 10)
    print(f"[5] status 取值样本：{values}")
    if not values:
        print("    ✗ 没取到样本值")
        failures += 1

    result = connector.execute("SELECT status, COUNT(*) FROM orders GROUP BY status")
    print(f"[6] 聚合查询：{result.rows}")
    if not result.ok or not result.rows:
        print(f"    ✗ {result.error}")
        failures += 1

    guard = connector.execute("DELETE FROM orders")
    print(f"[7] 删除语句被拦：{guard.error}")
    if guard.ok:
        print("    ✗ 删除没有被拦住")
        failures += 1

    multi = connector.execute("SELECT 1 FROM orders; DROP TABLE orders")
    print(f"[8] 多语句被拦：{multi.error}")
    if multi.ok:
        print("    ✗ 多语句没有被拦住")
        failures += 1

    limited = connector.execute("SELECT id FROM orders", max_rows=5)
    if len(limited.rows) != 5 or not limited.truncated:
        print("    ✗ 行数截断没生效")
        failures += 1
    else:
        print(f"[9] 行数截断：返回 {len(limited.rows)} 行，truncated={limited.truncated}")

    from sqlcopilot.schema import introspect

    schema = introspect(connector)
    orders = schema.get("orders")
    print(f"[10] 内省：{len(schema)} 张表，orders 说明=『{(orders.description or '')[:24]}…』")
    if not orders or not orders.description:
        print("    ✗ 字典说明没有带进来")
        failures += 1

    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dsn", default="postgresql://postgres:@127.0.0.1:5433/askdata_demo")
    parser.add_argument("--reuse", action="store_true", help="跳过建库灌数据")
    args = parser.parse_args()

    from sqlcopilot.connectors.postgres_connector import PostgresConnector

    if not args.reuse:
        import psycopg2

        admin = psycopg2.connect(args.dsn.rsplit("/", 1)[0] + "/postgres")
        admin.autocommit = True
        admin.cursor().execute("DROP DATABASE IF EXISTS askdata_demo")
        admin.cursor().execute("CREATE DATABASE askdata_demo WITH ENCODING 'UTF8'")
        admin.close()
        print("建库 askdata_demo，开始灌数据……")
        copy_data(args.dsn)

    connector = PostgresConnector(args.dsn, timeout_s=10, max_rows=500)
    failures = check(connector)
    print("\n" + ("全部通过" if failures == 0 else f"{failures} 项失败"))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
