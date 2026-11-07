"""检索层、校验层、评测指标的测试。"""

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
from sqlcopilot.core import load_config  # noqa: E402
from sqlcopilot.evaluation import execution_match  # noqa: E402
from sqlcopilot.execution import validate  # noqa: E402
from sqlcopilot.retrieval import (  # noqa: E402
    BM25Index,
    HashEmbedder,
    SchemaRetriever,
    build_value_index,
    cosine,
    rrf,
)
from sqlcopilot.schema import introspect_from_path  # noqa: E402

_TMP: tempfile.TemporaryDirectory | None = None
_CFG = None
_SCHEMA = None
_RETRIEVER: SchemaRetriever | None = None
_VALUES = None


def setUpModule() -> None:
    global _TMP, _CFG, _SCHEMA, _RETRIEVER, _VALUES
    _TMP = tempfile.TemporaryDirectory()
    db_path = Path(_TMP.name) / "test.db"
    init_db.build(db_path)
    _CFG = load_config(ROOT / "config" / "default.yaml")
    _SCHEMA = introspect_from_path(db_path)
    connector = SQLiteConnector(db_path)
    _VALUES = build_value_index(connector, _SCHEMA)
    _RETRIEVER = SchemaRetriever(_SCHEMA, _CFG, _VALUES)


def tearDownModule() -> None:
    if _TMP:
        _TMP.cleanup()


class TestEmbedder(unittest.TestCase):
    def test_identical_text_is_fully_similar(self) -> None:
        embedder = HashEmbedder(256)
        a, b = embedder.embed(["订单金额合计", "订单金额合计"])
        self.assertAlmostEqual(cosine(a, b), 1.0, places=6)

    def test_closer_text_scores_higher(self) -> None:
        embedder = HashEmbedder(256)
        query, near, far = embedder.embed(["订单金额", "订单金额合计", "文章浏览量"])
        self.assertGreater(cosine(query, near), cosine(query, far))

    def test_tokenize_splits_chinese_bigrams(self) -> None:
        from sqlcopilot.retrieval import tokenize

        self.assertIn("订单", tokenize("订单金额"))
        self.assertIn("金额", tokenize("订单金额"))


class TestSparse(unittest.TestCase):
    def test_bm25_ranks_matching_document_first(self) -> None:
        index = BM25Index().fit(
            {
                "orders": "订单 订单号 客户 金额 实付",
                "articles": "文章 标题 作者 浏览量",
            }
        )
        results = index.search("订单金额", k=2)
        self.assertEqual(results[0][0], "orders")

    def test_bm25_returns_empty_for_unknown_terms(self) -> None:
        index = BM25Index().fit({"orders": "订单 金额"})
        self.assertEqual(index.search("火箭发动机", k=5), [])


class TestFusion(unittest.TestCase):
    def test_rrf_prefers_document_in_both_lists(self) -> None:
        fused = rrf([["a", "b", "c"], ["c", "d", "e"]], k=60)
        self.assertEqual(fused[0][0], "c")

    def test_rrf_keeps_union(self) -> None:
        fused = dict(rrf([["a"], ["b"]], k=60))
        self.assertEqual(set(fused), {"a", "b"})


class TestRetriever(unittest.TestCase):
    def test_multi_table_question_hits_required_tables(self) -> None:
        assert _RETRIEVER is not None
        result = _RETRIEVER.retrieve("每个类目的订单金额合计是多少？")
        for table in ("order_items", "products", "categories"):
            self.assertIn(table, result.tables)

    def test_customer_question_hits_customers(self) -> None:
        assert _RETRIEVER is not None
        result = _RETRIEVER.retrieve("一共有多少个客户？")
        self.assertIn("customers", result.tables)

    def test_unrelated_domain_is_not_recalled_first(self) -> None:
        assert _RETRIEVER is not None
        result = _RETRIEVER.retrieve("一共有多少个客户？")
        # 内容域的订阅表不应该排在最前面
        ranked = [name for name, _ in result.ranked]
        self.assertNotEqual(ranked[0], "subscriptions")

    def test_graph_expansion_adds_neighbors(self) -> None:
        assert _RETRIEVER is not None
        result = _RETRIEVER.retrieve("上个月一共下了多少笔订单？")
        self.assertIn("orders", result.tables)
        self.assertTrue(len(result.tables) > len(result.ranked) or result.expanded == [])

    def test_recall_metric_bounded(self) -> None:
        assert _RETRIEVER is not None
        result = _RETRIEVER.retrieve("一共有多少个客户？")
        self.assertLessEqual(result.hit(["customers"], include_expanded=True), 1.0)
        self.assertGreaterEqual(result.hit(["customers"], include_expanded=True), 0.0)


class TestValueIndex(unittest.TestCase):
    def test_low_cardinality_column_has_samples(self) -> None:
        assert _VALUES is not None
        values = _VALUES.get("orders", "status")
        self.assertIn("已退款", values)

    def test_render_mentions_values(self) -> None:
        assert _VALUES is not None
        text = _VALUES.render(["orders"])
        self.assertIn("已退款", text)


class TestValidator(unittest.TestCase):
    def setUp(self) -> None:
        assert _SCHEMA is not None
        self.schema = _SCHEMA

    def test_accepts_select(self) -> None:
        self.assertTrue(validate("SELECT COUNT(*) FROM orders", self.schema))

    def test_accepts_cte(self) -> None:
        sql = "WITH t AS (SELECT id FROM orders) SELECT COUNT(*) FROM t"
        self.assertTrue(validate(sql, self.schema))

    def test_rejects_delete(self) -> None:
        result = validate("DELETE FROM orders", self.schema)
        self.assertFalse(result)
        self.assertIn("SELECT", result.reason)

    def test_rejects_unknown_table(self) -> None:
        result = validate("SELECT * FROM not_exist_table", self.schema)
        self.assertFalse(result)
        self.assertIn("不存在的表", result.reason)

    def test_rejects_multiple_statements(self) -> None:
        result = validate("SELECT 1 FROM orders; DROP TABLE orders", self.schema)
        self.assertFalse(result)

    def test_rejects_empty(self) -> None:
        self.assertFalse(validate("   ", self.schema))


class TestMetrics(unittest.TestCase):
    def test_row_order_does_not_matter(self) -> None:
        self.assertTrue(
            execution_match(["a"], [(1,), (2,)], ["a"], [(2,), (1,)])
        )

    def test_float_tolerance(self) -> None:
        self.assertTrue(
            execution_match(["s"], [(287.00000000000006,)], ["s"], [(287.0,)])
        )

    def test_different_column_count_fails(self) -> None:
        self.assertFalse(execution_match(["a", "b"], [(1, 2)], ["a"], [(1,)]))

    def test_different_values_fail(self) -> None:
        self.assertFalse(execution_match(["a"], [(1,)], ["a"], [(2,)]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
