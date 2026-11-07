"""重排。

粗排（向量 + 关键字）追求"别漏"，所以候选多、噪声也多；
重排追求"排准"，把真正该给的几张表顶到前面。

**但本项目的实测结论是：表级重排在这个规模上不成立，产品路径只保留"不重排"。**

三种实现都试过，结果见 `docs/项目方案.md` 附录：

| 版本 | 相对基准 |
|---|---|
| 词面重叠 | −1.00 |
| 交叉编码器 | −4.50 |
| 大模型重排 | +1.00（噪声内，每题多一次调用） |

共同原因是"相关性"不等于"连接可行性"：重排改变的是图扩展的起点，
而图扩展恰恰是贡献最大的模块。

三种实验实现放在 `experiments/rerank_variants.py`，只为复现消融，
产品路径不会导入它们。
"""

from __future__ import annotations

from typing import Sequence


class NoopReranker:
    """不重排，直接截前 N 个。这是产品默认行为。"""

    name = "none"

    def rerank(self, query: str, candidates: Sequence[str], top_n: int) -> list[int]:
        return list(range(min(top_n, len(candidates))))


def build_reranker(provider: str, llm=None):
    """按配置构造重排器。

    产品路径只会走到 `none`。其余取值属于实验分支，惰性导入——
    这样即便把 `experiments/` 整个删掉，产品代码也能正常运行。
    """
    if provider in {"", "none", "noop", None}:
        return NoopReranker()

    if provider == "llm" and llm is None:
        raise RuntimeError("大模型重排需要传入模型客户端（llm 参数不能为空）")

    try:
        from experiments.rerank_variants import build as build_experimental
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            f"重排器 {provider} 属于实验分支，需要项目根目录下的 experiments 包：{exc}"
        ) from exc
    return build_experimental(provider, llm=llm)
