"""关键字召回（BM25）。

BM25 是信息检索里最经典的排序函数。它做三件事：

1. 词频饱和：一个词出现十次，不该比出现一次重要十倍（k1 控制饱和速度）
2. 文档长度惩罚：长文档天然更容易命中词，要按长度归一（b 控制惩罚强度）
3. 逆文档频率：越少见的词越有区分度（"订单"到处都是，权重低；"退款"少见，权重高）

在本项目里，它的作用是**专有名词的精确匹配**——当用户说"退款表"这个词时，
语义向量可能被"钱""售后"等词带偏，但关键字匹配会稳稳命中 refunds。
"""

from __future__ import annotations

import math
from collections import Counter

from .embedder import tokenize


class BM25Index:
    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._docs: dict[str, list[str]] = {}
        self._doc_len: dict[str, int] = {}
        self._avg_len: float = 0.0
        self._df: Counter[str] = Counter()
        self._tokens: dict[str, Counter[str]] = {}

    def __len__(self) -> int:
        return len(self._docs)

    def fit(self, documents: dict[str, str]) -> "BM25Index":
        self._docs = {doc_id: tokenize(text) for doc_id, text in documents.items()}
        self._tokens = {doc_id: Counter(toks) for doc_id, toks in self._docs.items()}
        self._doc_len = {doc_id: len(toks) for doc_id, toks in self._docs.items()}
        self._avg_len = (sum(self._doc_len.values()) / len(self._doc_len)) if self._doc_len else 0.0
        self._df = Counter()
        for tokens in self._tokens.values():
            self._df.update(tokens.keys())
        return self

    def _idf(self, token: str) -> float:
        n = len(self._docs)
        df = self._df.get(token, 0)
        # 加一平滑，避免出现负数权重
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        query_tokens = tokenize(query)
        if not query_tokens or not self._docs:
            return []
        scores: list[tuple[str, float]] = []
        for doc_id, tf in self._tokens.items():
            length = self._doc_len[doc_id]
            score = 0.0
            for token in query_tokens:
                freq = tf.get(token, 0)
                if freq == 0:
                    continue
                denom = freq + self.k1 * (1 - self.b + self.b * length / (self._avg_len or 1))
                score += self._idf(token) * freq * (self.k1 + 1) / denom
            if score > 0:
                scores.append((doc_id, score))
        scores.sort(key=lambda item: item[1], reverse=True)
        return scores[:k]
