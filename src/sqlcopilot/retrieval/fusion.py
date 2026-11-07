"""倒数排名融合（Reciprocal Rank Fusion）。

问题：向量召回给出的是余弦相似度（0 到 1 之间的小数），关键字召回给出的是
BM25 分数（可能是 0 到 30 的任意值）。两种分数**不可比**，直接相加等于让
BM25 单方面决定结果。

做法：不看分数，只看名次。第 1 名贡献 1/(k+1)，第 2 名贡献 1/(k+2)……
k 越大，名次的差距越平缓。这样两路结果被公平地合并。

这就是"融合"这个词的真实含义：**把不可比的分数，换成可比的名次。**
"""

from __future__ import annotations

from typing import Sequence


def rrf(rankings: Sequence[Sequence[str]], k: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def weighted_fusion(
    rankings: Sequence[Sequence[str]],
    weights: Sequence[float],
    score_maps: Sequence[dict[str, float]] | None = None,
) -> list[tuple[str, float]]:
    """加权融合：先把每一路分数归一化到 0 到 1，再按权重相加。

    保留它是为了做对照实验——"倒排名次"和"归一化分数"哪种更好，
    应该用数字回答，而不是拍脑袋。
    """
    combined: dict[str, float] = {}
    for index, ranking in enumerate(rankings):
        weight = weights[index] if index < len(weights) else 1.0
        if not ranking:
            continue
        raw = score_maps[index] if score_maps else {doc: 1.0 / (i + 1) for i, doc in enumerate(ranking)}
        top = max(raw.get(doc, 0.0) for doc in ranking) or 1.0
        for doc in ranking:
            combined[doc] = combined.get(doc, 0.0) + weight * raw.get(doc, 0.0) / top
    return sorted(combined.items(), key=lambda item: item[1], reverse=True)
