"""连接器与数据字典的测试。

不打真实数据库：SQLite 用临时文件，MySQL 只验证连接串解析和工厂选择。
真正的 MySQL 端到端验证在 `scripts/verify_mysql.py` 里。
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
from sqlcopilot.connectors import (  # noqa: E402
    MySQLConnector,
    PostgresConnector,
    SQLiteConnector,
    build_connector,
)
from sqlcopilot.connectors.mysql_connector import parse_dsn  # noqa: E402
from sqlcopilot.core import load_config  # noqa: E402
from sqlcopilot.schema import SchemaDictionary, load_dictionary  # noqa: E402


class TestDsn(unittest.TestCase):
    def test_parses_full_dsn(self) -> None:
        params = parse_dsn("mysql://alice:s3cret@db.example.com:3307/shop")
        self.assertEqual(params["host"], "db.example.com")
        self.assertEqual(params["port"], 3307)
        self.assertEqual(params["user"], "alice")
        self.assertEqual(params["password"], "s3cret")
        self.assertEqual(params["database"], "shop")

    def test_defaults_port(self) -> None:
        self.assertEqual(parse_dsn("mysql://root:@127.0.0.1/demo")["port"], 3306)

    def test_urldecoded_credentials(self) -> None:
        params = parse_dsn("mysql://user:p%40ss%3Aword@127.0.0.1:3306/db")
        self.assertEqual(params["password"], "p@ss:word")

    def test_rejects_plain_string(self) -> None:
        with self.assertRaises(ValueError):
            parse_dsn("127.0.0.1:3306/shop")


class TestFactory(unittest.TestCase):
    def _cfg(self, **overrides):
        base = load_config(ROOT / "config" / "default.yaml")
        return base.with_overrides({"database": overrides})

    def test_sqlite(self) -> None:
        connector = build_connector(self._cfg(dialect="sqlite", path="data/demo_shop.db"))
        self.assertIsInstance(connector, SQLiteConnector)

    def test_mysql(self) -> None:
        connector = build_connector(
            self._cfg(dialect="mysql", dsn="mysql://root:@127.0.0.1:3307/x")
        )
        self.assertIsInstance(connector, MySQLConnector)

    def test_postgres(self) -> None:
        connector = build_connector(
            self._cfg(dialect="postgres", dsn="postgresql://postgres:@127.0.0.1/x")
        )
        self.assertIsInstance(connector, PostgresConnector)

    def test_unknown_dialect(self) -> None:
        with self.assertRaises(ValueError):
            build_connector(self._cfg(dialect="oracle"))


class TestDictionary(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db = self.tmp / "test.db"
        init_db.build(self.db)
        self.connector = SQLiteConnector(self.db)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_reads_dictionary_from_database(self) -> None:
        dictionary = SchemaDictionary.from_connector(self.connector)
        self.assertIn("订单", dictionary.table("orders"))
        self.assertTrue(dictionary.column("orders", "pay_amount"))

    def test_meta_tables_hidden_but_listable(self) -> None:
        self.assertNotIn("_meta_tables", self.connector.list_tables())
        self.assertIn("_meta_tables", self.connector.list_tables(include_meta=True))

    def test_yaml_takes_precedence(self) -> None:
        path = self.tmp / "dict.yaml"
        path.write_text(
            "tables:\n  orders:\n    description: 来自文件的说明\n    columns:\n      pay_amount: 实付\n",
            encoding="utf-8",
        )
        dictionary = load_dictionary(self.connector, path)
        self.assertEqual(dictionary.table("orders"), "来自文件的说明")
        self.assertEqual(dictionary.source, str(path))

    def test_blank_template_falls_back_to_database(self) -> None:
        # 初始化生成的模板全是空值，不能让它覆盖库里已有的说明
        path = self.tmp / "blank.yaml"
        path.write_text("tables:\n  orders:\n    description: ''\n", encoding="utf-8")
        dictionary = load_dictionary(self.connector, path)
        self.assertIn("订单", dictionary.table("orders"))
        self.assertEqual(dictionary.source, "<database>")

    def test_dump_template_is_blank(self) -> None:
        from sqlcopilot.schema import dump_template

        path = self.tmp / "template.yaml"
        dump_template(self.connector, path)
        text = path.read_text(encoding="utf-8")
        self.assertIn("orders", text)
        self.assertNotIn("订单主表", text)


class TestSqliteSchemaApi(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "test.db"
        init_db.build(self.db)
        self.connector = SQLiteConnector(self.db)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_columns_expose_primary_key(self) -> None:
        columns = {c.name: c for c in self.connector.columns("orders")}
        self.assertTrue(columns["id"].is_primary_key)
        self.assertFalse(columns["pay_amount"].nullable is None)

    def test_foreign_keys_parsed(self) -> None:
        refs = {(fk.column, fk.ref_table) for fk in self.connector.foreign_keys("orders")}
        self.assertIn(("customer_id", "customers"), refs)
        self.assertIn(("campaign_id", "campaigns"), refs)

    def test_quote_escapes(self) -> None:
        self.assertEqual(self.connector.quote('a"b'), '"a""b"')


if __name__ == "__main__":
    unittest.main(verbosity=2)
