"""实验分支：三种表级重排的实现。

**这些代码不属于产品路径，默认配置不会用到它们。** 保留在这里的唯一目的，
是让 `scripts/run_ablation.py` 里的 R / R2 / R3 三组实验可以被复现。

结论（200 题，详见 docs/项目方案.md 附录）：

| 版本 | 相对基准 | 原因 |
|---|---|---|
| 词面重叠 | −1.00 | 噪声边缘，且改变图扩展起点 |
| 交叉编码器 | −4.50 | 相关性 ≠ 连接可行性，改坏了扩展起点 |
| 大模型重排 | +1.00 | 唯一非负，但在噪声内，每题多一次调用 |

把整条链路改成"先扩展后精排"也没救回来（−6.00），因为它压低了检索召回。
所以最终形态里 `rerank.provider = none`。

如果只想用产品路径，这个文件可以整个删掉——只要同时把 `run_ablation.py` 里
R / R2 / R3 / V2 四组实验也删掉。
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from sqlcopilot.retrieval.embedder import tokenize

# 优先用项目内的本地模型目录：这台机器上 Python 的证书链认不了模型仓库，
# 只能用系统工具先把文件下下来，再从这里加载。
_MODEL_DIR = Path(__file__).resolve().parents[1] / "models" / "bge-reranker-base"


class OverlapReranker:
    """词面覆盖率重排：问题的词元有多少比例在候选文档里出现。"""

    name = "overlap"

    def rerank(self, query: str, candidates: Sequence[str], top_n: int) -> list[int]:
        query_tokens = set(tokenize(query))
        if not query_tokens:
            return list(range(min(top_n, len(candidates))))

        scored: list[tuple[float, int]] = []
        for index, text in enumerate(candidates):
            doc_tokens = set(tokenize(text))
            if not doc_tokens:
                scored.append((0.0, index))
                continue
            overlap = len(query_tokens & doc_tokens)
            coverage = overlap / len(query_tokens)
            precision = overlap / len(doc_tokens)
            scored.append((coverage + 0.3 * precision, index))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [index for _, index in scored[:top_n]]


class CrossEncoderReranker:
    """交叉编码器重排：把问题和表描述拼成一对送进模型，直接输出相关性分数。

    惰性加载：权重几个 G，只有真正用到时才加载，没装 torch 的环境不受影响。
    """

    name = "cross_encoder"

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-base",
        batch_size: int = 8,
        max_length: int = 320,
    ) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        self.max_length = max_length
        self._model = None
        self._tokenizer = None
        self._torch = None

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "交叉编码器需要 torch 和 transformers。装不上就改用 overlap 或 llm 重排。"
            ) from exc

        self._torch = torch
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        self._model = AutoModelForSequenceClassification.from_pretrained(self.model_name)
        self._model.eval()
        for param in self._model.parameters():
            param.requires_grad_(False)

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        self._load()
        torch = self._torch
        assert torch is not None
        scores: list[float] = []
        for start in range(0, len(documents), self.batch_size):
            batch = list(documents[start : start + self.batch_size])
            pairs = [[query, doc] for doc in batch]
            encoded = self._tokenizer(
                pairs,
                padding=True,
                truncation=True,
                max_length=self.max_length,
                return_tensors="pt",
            )
            with torch.no_grad():
                logits = self._model(**encoded).logits.view(-1)
            scores.extend(float(x) for x in logits)
        return scores

    def rerank(self, query: str, candidates: Sequence[str], top_n: int) -> list[int]:
        if not candidates:
            return []
        scores = self.score(query, candidates)
        order = sorted(range(len(candidates)), key=lambda i: scores[i], reverse=True)
        return order[:top_n]


class LlmReranker:
    """让大模型直接给候选表排序。每题多一次调用，但在本项目里是唯一非负的一种。"""

    name = "llm"

    def __init__(self, llm) -> None:
        self.llm = llm

    def rerank(self, query: str, candidates: Sequence[str], top_n: int) -> list[int]:
        if not candidates:
            return []
        listing = "\n".join(
            f"[{i}] {line.strip()[:160]}" for i, line in enumerate(candidates)
        )
        system = (
            "你是一个数据库专家。用户会给出一个问题和若干候选表，"
            "请判断哪些表是回答这个问题真正需要的。只输出编号，用逗号分隔，不要解释。"
        )
        user = f"问题：{query}\n\n候选表：\n{listing}\n\n请输出最相关的 {top_n} 个编号。"
        try:
            raw = self.llm.complete(system, user)
        except Exception:  # noqa: BLE001 - 重排失败时退回原顺序，不影响主流程
            return list(range(min(top_n, len(candidates))))

        picked: list[int] = []
        for chunk in raw.replace("，", ",").split(","):
            digits = "".join(ch for ch in chunk if ch.isdigit())
            if not digits:
                continue
            index = int(digits)
            if 0 <= index < len(candidates) and index not in picked:
                picked.append(index)
        for index in range(len(candidates)):
            if len(picked) >= top_n:
                break
            if index not in picked:
                picked.append(index)
        return picked[:top_n]


_encoder_cache: dict[str, CrossEncoderReranker] = {}


def build(provider: str, llm=None):
    """构造实验用重排器。产品路径不会调用这里。"""
    if provider == "overlap":
        return OverlapReranker()
    if provider == "cross_encoder":
        if "default" not in _encoder_cache:
            model = str(_MODEL_DIR) if _MODEL_DIR.exists() else "BAAI/bge-reranker-base"
            _encoder_cache["default"] = CrossEncoderReranker(model_name=model)
        return _encoder_cache["default"]
    if provider == "llm" and llm is not None:
        return LlmReranker(llm)
    raise ValueError(f"experiments.build 不支持：{provider}")
