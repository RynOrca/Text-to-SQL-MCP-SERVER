"""抽取层的测试。

这一组测试是**评测集逼出来的回归锁**：T03/F04 两条用例证明，
模型输出"查询成功，共 3 条记录"时，旧实现会把整段话当成语句返回，
于是对外展示变成"语句：查询成功……"。修复后必须保证：

1. 散文 → 空串（可见的失败），而不是伪装成语句；
2. 正常语句、代码块、带前缀的语句仍然能抽出来；
3. 多语句只取第一条。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlcopilot.generation.prompt import extract_sql  # noqa: E402


class TestExtractSql(unittest.TestCase):
    def test_plain_select(self) -> None:
        self.assertEqual(extract_sql("SELECT COUNT(*) FROM orders"), "SELECT COUNT(*) FROM orders")

    def test_lowercase_and_whitespace(self) -> None:
        self.assertEqual(extract_sql("  select 1  "), "select 1")

    def test_code_fence(self) -> None:
        raw = "```sql\nSELECT id FROM orders\n```"
        self.assertEqual(extract_sql(raw), "SELECT id FROM orders")

    def test_prefix_stripped(self) -> None:
        self.assertEqual(extract_sql("语句：SELECT 1"), "SELECT 1")

    def test_multiple_statements_take_first(self) -> None:
        self.assertEqual(extract_sql("SELECT 1; SELECT 2"), "SELECT 1")

    def test_with_cte(self) -> None:
        raw = "WITH t AS (SELECT 1) SELECT * FROM t"
        self.assertEqual(extract_sql(raw), raw)

    def test_prose_only_returns_empty(self) -> None:
        """纯散文不能被当成语句。"""
        self.assertEqual(extract_sql("查询成功，共 3 条记录"), "")

    def test_prose_prefixed_sql_returns_empty(self) -> None:
        """散文 + 语句混在一起时，宁可报"没有解析出语句"，也不能把散文当语句。"""
        raw = "查询成功，共 10000 条记录，环比增长 12%\nSELECT pay_money FROM orders"
        self.assertEqual(extract_sql(raw), "")

    def test_empty(self) -> None:
        self.assertEqual(extract_sql(""), "")
        self.assertEqual(extract_sql("   "), "")


if __name__ == "__main__":
    unittest.main()
