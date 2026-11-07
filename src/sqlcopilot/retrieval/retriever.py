"""表级检索：把"该给模型看哪几张表"这件事做对。

一次检索走五步：

1. 向量召回（dense）：按语义相近度取前 N 张表
2. 关键字召回（sparse）：按词面精确匹配取前 N 张表
3. 融合（fusion）：用倒数排名把两路结果合并
4. 重排（rerank）：用更细的打分留下 top_n 张
5. 图扩展（expand）：沿外键补一跳邻接表

第 5 步是 Text-to-SQL 相对文档检索最特殊的地方：
**语义最像的表，往往不是需要连接的表。**
问"复购率"时，`orders` 语义最像，但答案需要 `orders ⋈ customers`；
只靠向量，`customers` 永远进不了候选集。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.config import Config
from ..schema.models import Schema, Table
from .embedder import HashEmbedder
from .fusion import rrf
from .rerank import build_reranker
from .sparse import BM25Index
from .values import ValueIndex
from .vector_store import InMemoryVectorStore


@dataclass
class RetrievalResult:
    """一次检索的完整记录，既是结果也是痕迹（便于归因）。"""

    tables: list[str] = field(default_factory=list)
    ranked: list[tuple[str, float]] = field(default_factory=list)
    dense: list[str] = field(default_factory=list)
    sparse: list[str] = field(default_factory=list)
    expanded: list[str] = field(default_factory=list)

    def hit(self, gold_tables: list[str], include_expanded: bool = True) -> float:
        """检索召回率：标准答案用到的表，有多少进了候选。

        `include_expanded=False` 时只统计"重排后直接选中的表"，
        用来区分"检索本身找没找对"和"图扩展补没补上"——
        这两个数字的差值，就是图扩展的真实贡献。
        """
        if not gold_tables:
            return 1.0
        candidates = set(self.tables if include_expanded else [n for n, _ in self.ranked])
        return len(set(gold_tables) & candidates) / len(set(gold_tables))


def _document(table: Table, values: ValueIndex | None) -> str:
    """给一张表生成用于向量化的描述文本。

    把表名、中文说明、字段名与字段说明拼在一起，
    这样"订单""退款""库存"这类问题才能和表对上。
    如果建了取值索引，再把取值拼进去——"华东""已退款"这些词就能直接命中。
    """
    parts = [table.name, table.description]
    for column in table.columns:
        parts.append(f"{column.name} {column.description}")
        if values is not None:
            parts.extend(values.get(table.name, column.name)[:12])
    return " ".join(p for p in parts if p)


class SchemaRetriever:
    def __init__(
        self,
        schema: Schema,
        cfg: Config,
        values: ValueIndex | None = None,
        llm=None,
    ) -> None:
        self.schema = schema
        self.cfg = cfg
        self.values = values

        self.dimension = int(cfg.get("embedder.dimension", 256))
        self.embedder = HashEmbedder(self.dimension)
        self.store = InMemoryVectorStore()
        self.bm25 = BM25Index()
        self.reranker = build_reranker(cfg.get("retriever.rerank.provider", "none"), llm=llm)

        self.dense_top_k = int(cfg.get("retriever.dense_top_k", 20))
        self.sparse_top_k = int(cfg.get("retriever.sparse_top_k", 20))
        self.use_dense = bool(cfg.get("retriever.use_dense", True))
        self.use_sparse = bool(cfg.get("retriever.use_sparse", True))
        self.rrf_k = int(cfg.get("retriever.rrf_k", 60))
        self.top_n = int(cfg.get("retriever.rerank.top_n", 8))
        self.graph_enabled = bool(cfg.get("retriever.graph_expand.enabled", True))
        self.graph_hops = int(cfg.get("retriever.graph_expand.max_hops", 1))
        self.graph_seed = int(cfg.get("retriever.graph_expand.seed_count", 2))

        # 以下三项是"适配交叉编码器"的开关，默认关闭，不影响原有行为
        self.seed_strategy = str(cfg.get("retriever.seed_strategy", "rank"))
        self.score_margin = float(cfg.get("retriever.rerank.score_margin", 0.0))
        self.max_tables = int(cfg.get("retriever.max_tables", 0))
        self.expand_first = bool(cfg.get("retriever.expand_first", False))

        self._build()

    def _build(self) -> None:
        names = self.schema.names()
        documents = [_document(self.schema.get(n), self.values) for n in names]  # type: ignore[arg-type]
        vectors = self.embedder.embed(documents)
        self.store.add(names, vectors, [{"name": n} for n in names])
        self.bm25.fit(dict(zip(names, documents)))

    def retrieve(self, question: str, top_n: int | None = None) -> RetrievalResult:
        limit = self.top_n if top_n is None else top_n

        query_vector = self.embedder.embed([question])[0]
        dense = self.store.search(query_vector, self.dense_top_k) if self.use_dense else []
        sparse = self.bm25.search(question, self.sparse_top_k) if self.use_sparse else []
        if not dense and not sparse:
            # 两路都关掉时退化为"按表名顺序全给"，保证链路不至于空转
            dense = [(name, 0.0) for name in self.schema.names()]

        fused = rrf(
            [[doc for doc, _ in dense], [doc for doc, _ in sparse]],
            k=self.rrf_k,
        )
        candidates = [doc for doc, _ in fused]

        if self.expand_first:
            return self._retrieve_expand_first(
                question, candidates, dense, sparse, limit
            )

        documents = [_document(self.schema.get(n), self.values) for n in candidates]  # type: ignore[arg-type]

        order = self.reranker.rerank(question, documents, limit)

        # 用分数阈值截断，而不是固定取前 N 张。
        # 交叉编码器给的是可比的绝对分数，"比第一名差多少"是有意义的信号；
        # 固定取前 N 会强行保留明显不相关的表，也会砍掉并列相关的表。
        if self.score_margin > 0 and hasattr(self.reranker, "score"):
            scores = self.reranker.score(question, documents)
            if order:
                best = max(scores[i] for i in order)
                order = [i for i in order if scores[i] >= best - self.score_margin]

        ranked_names = [candidates[i] for i in order]

        expanded: list[str] = []
        if self.graph_enabled:
            # 选择扩展的起点。
            # 默认按排名取前两张；但"最相关的表"未必是"连接计划的入口"——
            # 实测发现交叉编码器把入口换掉之后，扩展会把上下文带到无关的业务域。
            # 所以提供了第二种策略：按邻接度挑"连接中枢"。
            seeds = self._pick_seeds(ranked_names)
            for name in seeds:
                for neighbor in sorted(self.schema.neighbors(name)):
                    if self.max_tables and len(ranked_names) + len(expanded) >= self.max_tables:
                        break
                    if neighbor not in ranked_names and neighbor not in expanded:
                        expanded.append(neighbor)

        if self.max_tables:
            expanded = expanded[: max(0, self.max_tables - len(ranked_names))]

        return RetrievalResult(
            tables=ranked_names + expanded,
            ranked=[(candidates[i], 0.0) for i in order],
            dense=[doc for doc, _ in dense],
            sparse=[doc for doc, _ in sparse],
            expanded=expanded,
        )

    def _retrieve_expand_first(
        self,
        question: str,
        candidates: list[str],
        dense: list[tuple[str, float]],
        sparse: list[tuple[str, float]],
        limit: int,
    ) -> RetrievalResult:
        """先扩展、后重排。

        这是为交叉编码器设计的顺序：让它在一个"已经包含所有可能需要的表"的池子里
        做最终裁决，而不是由它的排序去决定图扩展从哪里出发。

        原来的顺序是"先重排、后扩展"，扩展的起点依赖排名；
        换了重排器，起点就变了，整条连接链跟着变——这正是 R2 掉点的原因。
        """
        pool = list(candidates)
        for name in candidates[: self.graph_seed]:
            for neighbor in sorted(self.schema.neighbors(name)):
                if neighbor not in pool:
                    pool.append(neighbor)

        documents = [_document(self.schema.get(n), self.values) for n in pool]  # type: ignore[arg-type]
        order = self.reranker.rerank(question, documents, limit)

        if self.score_margin > 0 and hasattr(self.reranker, "score"):
            scores = self.reranker.score(question, documents)
            if order:
                best = max(scores[i] for i in order)
                order = [i for i in order if scores[i] >= best - self.score_margin]

        if self.max_tables:
            order = order[: self.max_tables]

        ranked_names = [pool[i] for i in order]
        expanded = [name for name in ranked_names if name not in candidates]

        return RetrievalResult(
            tables=ranked_names,
            ranked=[(name, 0.0) for name in ranked_names],
            dense=[doc for doc, _ in dense],
            sparse=[doc for doc, _ in sparse],
            expanded=expanded,
        )

    def _pick_seeds(self, ranked_names: list[str]) -> list[str]:
        """挑出图扩展的起点。

        rank  ：按排名取前几张（默认，简单直接）
        degree：按邻接表数量取"连接中枢"——入口表通常是外键最多的表，
                而不是和问题最像的表
        """
        if self.seed_strategy != "degree":
            return ranked_names[: self.graph_seed]
        ranked = sorted(
            ranked_names,
            key=lambda name: len(self.schema.neighbors(name)),
            reverse=True,
        )
        return ranked[: self.graph_seed]
