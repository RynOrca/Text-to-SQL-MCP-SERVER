"""库结构层的单元测试。

测试跑在临时目录里的独立数据库上，不会污染 data/demo_shop.db。
运行：python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import init_db  # noqa: E402
from sqlcopilot.connectors import SQLiteConnector  # noqa: E402
from sqlcopilot.schema import introspect_from_path  # noqa: E402


class TestSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls._tmp.name) / "test.db"
        init_db.build(cls.db_path)
        cls.schema = introspect_from_path(cls.db_path)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    # ---------- 内省 ----------

    def test_table_count(self) -> None:
        # 14 张订单域 + 10 张内容域
        self.assertEqual(len(self.schema), 24)

    def test_meta_tables_are_hidden(self) -> None:
        names = self.schema.names()
        self.assertNotIn("_meta_tables", names)
        self.assertNotIn("_meta_columns", names)

    def test_orders_columns(self) -> None:
        orders = self.schema.get("orders")
        assert orders is not None
        self.assertEqual(len(orders.columns), 9)
        id_column = orders.column("id")
        assert id_column is not None
        self.assertTrue(id_column.is_primary_key)

    def test_description_comes_from_dictionary(self) -> None:
        orders = self.schema.get("orders")
        assert orders is not None
        self.assertIn("订单", orders.description)
        pay_amount = orders.column("pay_amount")
        assert pay_amount is not None
        self.assertTrue(pay_amount.description)

    def test_foreign_key_parsed(self) -> None:
        orders = self.schema.get("orders")
        assert orders is not None
        refs = {(fk.column, fk.ref_table) for fk in orders.foreign_keys}
        self.assertIn(("customer_id", "customers"), refs)

    def test_render_contains_key_parts(self) -> None:
        text = self.schema.render(["orders"])
        self.assertIn("### 表 orders", text)
        self.assertIn("外键：", text)

    # ---------- 外键图 ----------

    def test_neighbors_both_directions(self) -> None:
        # categories 没有指向外的外键，但 products 指向它，所以仍应相邻
        neighbors = self.schema.neighbors("categories")
        self.assertIn("products", neighbors)
        self.assertNotIn("categories", neighbors)

    def test_expand_two_hops(self) -> None:
        # 一跳只能到 orders；payments 与 refunds 没有直接外键，需要两跳
        one = set(self.schema.expand(["refunds"], hops=1))
        self.assertIn("orders", one)
        self.assertNotIn("payments", one)

        two = set(self.schema.expand(["refunds"], hops=2))
        self.assertIn("orders", two)
        self.assertIn("payments", two)

    def test_expand_zero_hop_returns_input(self) -> None:
        self.assertEqual(self.schema.expand(["orders"], hops=0), ["orders"])

    # ---------- 只读沙箱 ----------

    def test_sandbox_blocks_write(self) -> None:
        conn = SQLiteConnector(self.db_path)
        result = conn.execute("DELETE FROM orders")
        self.assertFalse(result.ok)
        self.assertIn("拒绝", result.error or "")

    def test_sandbox_blocks_ddl(self) -> None:
        conn = SQLiteConnector(self.db_path)
        result = conn.execute("DROP TABLE orders")
        self.assertFalse(result.ok)

    def test_sandbox_allows_select(self) -> None:
        conn = SQLiteConnector(self.db_path)
        result = conn.execute("SELECT COUNT(*) FROM orders")
        self.assertTrue(result.ok)
        self.assertEqual(result.rows[0][0], init_db.N_ORDERS)

    def test_row_limit_truncation(self) -> None:
        conn = SQLiteConnector(self.db_path, max_rows=3)
        result = conn.execute("SELECT id FROM orders")
        self.assertTrue(result.truncated)
        self.assertEqual(len(result.rows), 3)


class TestDataIntegrity(unittest.TestCase):
    """数据本身要自洽，否则基于它的评测结论不可信。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls._tmp.name) / "test.db"
        init_db.build(cls.db_path)
        cls.conn = SQLiteConnector(cls.db_path)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_demo_order_no_duplicates(self) -> None:
        result = self.conn.execute(
            "SELECT COUNT(*) - COUNT(DISTINCT order_no) FROM orders"
        )
        self.assertEqual(result.rows[0][0], 0)

    def test_pay_amount_consistent(self) -> None:
        result = self.conn.execute(
            "SELECT COUNT(*) FROM orders "
            "WHERE ABS(total_amount - discount_amount - pay_amount) > 0.01"
        )
        self.assertEqual(result.rows[0][0], 0)

    def test_every_refund_has_order(self) -> None:
        result = self.conn.execute(
            "SELECT COUNT(*) FROM refunds r LEFT JOIN orders o ON r.order_id = o.id "
            "WHERE o.id IS NULL"
        )
        self.assertEqual(result.rows[0][0], 0)

    def test_region_tree_has_three_levels(self) -> None:
        result = self.conn.execute(
            "SELECT level, COUNT(*) FROM regions GROUP BY level ORDER BY level"
        )
        levels = [row[0] for row in result.rows]
        self.assertEqual(levels, [1, 2, 3])


if __name__ == "__main__":
    unittest.main(verbosity=2)
