"""基线策略：不用大模型的"笨办法"。

为什么要有它？
因为评测链路本身也需要被验证。如果一上来就用模型跑，得到 40%，
你无法确定是模型不行、还是评测代码写错了。

先跑一个注定很差的基线（比如 10%），确认整条链路能出数、归因能落地，
再换模型。这一步很多项目会跳过，然后在真正优化时才发现"指标算错了"。

它也提供了一个有意义的下界：任何模型方案如果赢不过这条规则，
说明问题不在模型。
"""

from __future__ import annotations

from ..schema.models import Schema
from ..retrieval.retriever import RetrievalResult

_COUNT_HINTS = ("多少", "几个", "几笔", "几单", "几条", "几人", "多少个", "多少笔")
_GROUP_HINTS = ("各", "分别", "每个")


def keyword_baseline(question: str, retrieval: RetrievalResult, schema: Schema) -> str:
    """纯规则：命中"多少"就数行，否则取前几行看看。

    它只做一件事：证明"没有理解能力"时能拿到什么分数。
    """
    if not retrieval.tables:
        return "SELECT 1"

    table = retrieval.tables[0]
    is_count = any(hint in question for hint in _COUNT_HINTS)
    is_group = any(hint in question for hint in _GROUP_HINTS)

    if is_count and not is_group:
        return f"SELECT COUNT(*) FROM {table}"
    return f"SELECT * FROM {table} LIMIT 10"
