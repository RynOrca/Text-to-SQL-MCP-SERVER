"""文本切分与哈希向量。

为什么中文要切成"二元组"而不是按空格切？
中文没有空格，按空格切等于整句一个词。按单字切会丢掉"订单"和"单订"的区别。
按相邻两字切（二元组）是中文检索里最省事又有效的做法：
"订单金额"会切成 订单、单金、金额，既能匹配"订单"，也能匹配"金额"。
"""

from __future__ import annotations

import math
import re
import zlib
from typing import Sequence

_WORD = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]+")


def tokenize(text: str) -> list[str]:
    """把一段文字切成检索用的最小单位。"""
    tokens: list[str] = []
    for match in _WORD.finditer(text.lower()):
        chunk = match.group()
        if "\u4e00" <= chunk[0] <= "\u9fff":
            if len(chunk) == 1:
                tokens.append(chunk)
            else:
                tokens.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))
        else:
            tokens.append(chunk)
    return tokens


def _stable_hash(token: str) -> int:
    """稳定的哈希。

    不能用 Python 内置的 hash()：它每个进程的种子不同，重启后向量就变了，
    评测结果无法复现。crc32 是确定性的。
    """
    return zlib.crc32(token.encode("utf-8"))


class HashEmbedder:
    """离线可用的简易嵌入器。

    做法是"特征哈希"：每个词元（token）通过哈希映射到向量的某一维，
    该维加一；同时用哈希的另一个比特决定加还是减，用来抵消碰撞带来的偏差。
    最后做归一化，让向量长度为一，这样点积就等于余弦相似度。

    它不是语义模型，只能捕捉"字面重叠"。但它的作用是把整条链路先跑通，
    后面换成真实嵌入模型时，只需要改配置里的一行。
    """

    def __init__(self, dimension: int = 256) -> None:
        self.dimension = dimension

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for token in tokenize(text):
            h = _stable_hash(token)
            index = h % self.dimension
            sign = 1.0 if (h >> 31) & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            return vector
        return [v / norm for v in vector]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """两个已归一化向量的点积即余弦相似度。"""
    return sum(x * y for x, y in zip(a, b))
