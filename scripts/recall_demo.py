"""看一次检索的四个阶段各自选出了什么。

    向量召回 → 关键字召回 → 融合与重排 → 外键图扩展

用法：
    python scripts/recall_demo.py "华东区一共有多少客户？"
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlcopilot.core import load_config  # noqa: E402
from sqlcopilot.generation import MockLlm  # noqa: E402
from sqlcopilot.pipeline import AskPipeline  # noqa: E402


def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else "华东区一共有多少客户？"
    cfg = load_config(ROOT / "config" / "default.yaml")
    pipeline = AskPipeline(cfg, llm=MockLlm())
    result = pipeline.retriever.retrieve(question)

    print(f"问题：{question}\n")
    print(f"① 向量召回（前 8）：{result.dense[:8]}")
    print(f"② 关键字召回（前 8）：{result.sparse[:8]}")
    print(f"③ 融合 + 重排（取前 {len(result.ranked)}）：{[name for name, _ in result.ranked]}")
    print(f"④ 图扩展补入：{result.expanded or '（无）'}")
    print(f"\n最终给模型的表（{len(result.tables)} 张）：{result.tables}")


if __name__ == "__main__":
    main()
