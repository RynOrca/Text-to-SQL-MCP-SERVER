"""内存向量索引。

为什么自己写而不用向量数据库？
几十张表的规模下，暴力计算余弦相似度只要几毫秒；引入一个向量数据库，
带来的部署成本和"黑盒感"远大于收益。而且自己写一遍，才讲得清
"向量检索就是归一化点积排序"这句话。

代价：十万级以上向量会明显变慢。所以它实现了 VectorStore 接口，
将来换成任何后端，调用方一行都不用改。
"""

from __future__ import annotations

from typing import Any, Sequence

from .embedder import cosine


class InMemoryVectorStore:
    def __init__(self) -> None:
        self._ids: list[str] = []
        self._vectors: list[list[float]] = []
        self._payloads: list[dict[str, Any]] = []
        self._index: dict[str, int] = {}

    def __len__(self) -> int:
        return len(self._ids)

    def add(
        self,
        ids: Sequence[str],
        vectors: Sequence[Sequence[float]],
        payloads: Sequence[dict[str, Any]],
    ) -> None:
        for item_id, vector, payload in zip(ids, vectors, payloads):
            vec = list(vector)
            if item_id in self._index:
                # 同 id 覆盖，避免重复建索引时出现两份
                position = self._index[item_id]
                self._vectors[position] = vec
                self._payloads[position] = payload
                continue
            self._index[item_id] = len(self._ids)
            self._ids.append(item_id)
            self._vectors.append(vec)
            self._payloads.append(payload)

    def search(self, vector: Sequence[float], k: int) -> list[tuple[str, float]]:
        if not self._ids:
            return []
        scores = [cosine(vector, v) for v in self._vectors]
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
        return [(self._ids[i], scores[i]) for i in order]

    def payload(self, item_id: str) -> dict[str, Any]:
        return self._payloads[self._index[item_id]]
