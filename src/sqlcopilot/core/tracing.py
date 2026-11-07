"""全链路追踪。

为什么需要：数据问答系统一旦出错，可能是检索召回错了表、可能是提示词缺了字段、
可能是模型写错了语句、也可能是执行超时。没有每一段的中间记录，只能靠猜。

做法很朴素：把一次问答拆成若干个"片段"（span），每个片段记录输入、输出、耗时和异常，
最后整体导出。不依赖任何第三方追踪框架。
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class Span:
    name: str
    duration_ms: float = 0.0
    attributes: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    # token 账本挂到片段上：这样才能回答"哪一段最贵"，
    # 而不只是"整次问答花了多少"。
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "duration_ms": round(self.duration_ms, 3),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "attributes": self.attributes,
            "error": self.error,
        }


class Tracer:
    def __init__(self) -> None:
        self.spans: list[Span] = []
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def add_tokens(self, span: Span, prompt: int = 0, completion: int = 0) -> None:
        span.prompt_tokens += prompt
        span.completion_tokens += completion
        self.prompt_tokens += prompt
        self.completion_tokens += completion

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[Span]:
        span = Span(name=name, attributes=dict(attributes))
        started = time.perf_counter()
        try:
            yield span
        except Exception as exc:  # 记录后继续抛出，避免吞掉错误
            span.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            span.duration_ms = (time.perf_counter() - started) * 1000
            self.spans.append(span)

    def total_ms(self) -> float:
        return round(sum(s.duration_ms for s in self.spans), 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_ms": self.total_ms(),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "spans": [s.to_dict() for s in self.spans],
        }

    def summary(self) -> str:
        lines = [f"{s.name:<24}{s.duration_ms:>9.2f} ms" for s in self.spans]
        lines.append(f"{'合计':<24}{self.total_ms():>9.2f} ms")
        return "\n".join(lines)
