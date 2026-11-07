"""验证 MySQL 连接器：建库、灌数据、跑一遍全部能力。

需要一台可连的 MySQL。本机验证时这样起一个临时实例（不碰系统服务）：

    mysqld --datadir=<数据目录> --port=3307 --skip-grant-tables --console

然后：
    python scripts/verify_mysql.py --dsn "mysql://root:@127.0.0.1:3307/askdata_demo"

它会做四件事：
1. 建库建表（含外键和字典表）
2. 从 SQLite 演示库灌数据
3. 检查 列 / 外键 / 行数 / 取值样本 是否读得对
4. 检查只读防护：删表、多语句、写操作是否都被拦住
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
        parent_id INT NULL, KEY idx_parent (parent_id),
        CONSTRAINT fk_region_parent FOREIGN KEY (parent_id) REFERENCES regions(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE customers (
        id INT PRIMARY KEY, name VARCHAR(64) NOT NULL, gender VARCHAR(8),
        birth_date VARCHAR(16), register_date VARCHAR(16) NOT NULL,
        region_id INT NULL, channel VARCHAR(32),
        KEY idx_region (region_id),
        CONSTRAINT fk_customer_region FOREIGN KEY (region_id) REFERENCES regions(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE orders (
        id INT PRIMARY KEY, order_no VARCHAR(32) NOT NULL, customer_id INT,
        order_date VARCHAR(16) NOT NULL, status VARCHAR(16),
        total_amount DECIMAL(12,2), discount_amount DECIMAL(12,2), pay_amount DECIMAL(12,2),
        campaign_id INT NULL,
        KEY idx_customer (customer_id), KEY idx_date (order_date),
        CONSTRAINT fk_order_customer FOREIGN KEY (customer_id) REFERENCES customers(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE order_items (
        id INT PRIMARY KEY, order_id INT, product_id INT,
        quantity INT, unit_price DECIMAL(12,2), amount DECIMAL(12,2),
        KEY idx_order (order_id),
        CONSTRAINT fk_item_order FOREIGN KEY (order_id) REFERENCES orders(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE refunds (
        id INT PRIMARY KEY, order_id INT, reason VARCHAR(32),
        amount DECIMAL(12,2), created_at VARCHAR(32), status VARCHAR(16),
        KEY idx_refund_order (order_id),
        CONSTRAINT fk_refund_order FOREIGN KEY (order_id) REFERENCES orders(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE _meta_tables (
        table_name VARCHAR(64) PRIMARY KEY, description VARCHAR(255)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE _meta_columns (
        table_name VARCHAR(64), column_name VARCHAR(64), description VARCHAR(255),
        PRIMARY KEY (table_name, column_name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
]


def copy_data(dsn_params: dict) -> None:
    import pymysql

    source = sqlite3.connect(str(SQLITE_DB))
    target = pymysql.connect(**dsn_params, charset="utf8mb4", autocommit=False)
    cursor = target.cursor()

    for statement in DDL:
        cursor.execute(statement)

    for table in TABLES + ["_meta_tables", "_meta_columns"]:
        rows = source.execute(f'SELECT * FROM "{table}"').fetchall()
        if not rows:
            continue
        placeholders = ",".join(["%s"] * len(rows[0]))
        cursor.executemany(
            f"INSERT INTO `{table}` VALUES ({placeholders})",
            rows,
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
    parser.add_argument("--dsn", default="mysql://root:@127.0.0.1:3307/askdata_demo")
    parser.add_argument("--reuse", action="store_true", help="跳过建库灌数据")
    args = parser.parse_args()

    from sqlcopilot.connectors.mysql_connector import MySQLConnector, parse_dsn

    params = parse_dsn(args.dsn)
    database = params.pop("database")
    if not args.reuse:
        import pymysql

        admin = pymysql.connect(
            host=params["host"], port=params["port"],
            user=params["user"], password=params["password"], charset="utf8mb4",
        )
        admin.cursor().execute(f"DROP DATABASE IF EXISTS `{database}`")
        admin.cursor().execute(f"CREATE DATABASE `{database}` DEFAULT CHARSET utf8mb4")
        admin.commit()
        admin.close()
        print(f"建库 {database}，开始灌数据……")
        copy_data({**params, "database": database})

    connector = MySQLConnector(args.dsn, timeout_s=10, max_rows=500)
    failures = check(connector)
    print("\n" + ("全部通过" if failures == 0 else f"{failures} 项失败"))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
