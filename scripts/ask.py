"""问一个问题，看它怎么答。

用法：
    python scripts/ask.py "上个月一共有多少笔订单？"
    python scripts/ask.py --show-context "华东区有多少客户？"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlcopilot.core import load_config  # noqa: E402
from sqlcopilot.pipeline import AskPipeline  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="单次提问")
    parser.add_argument("question")
    parser.add_argument("--show-context", action="store_true", help="打印检索到的表与结构")
    parser.add_argument("--config", default="config/default.yaml")
    args = parser.parse_args()

    cfg = load_config(ROOT / args.config)
    pipeline = AskPipeline(cfg)

    answer = pipeline.ask(args.question)

    if args.show_context and answer.retrieval:
        print("检索到的表：", answer.retrieval.tables)
        print("向量召回：", answer.retrieval.dense)
        print("关键字召回：", answer.retrieval.sparse)
        print("-" * 60)

    print(answer.render())
    print("-" * 60)
    print(answer.trace.summary())


if __name__ == "__main__":
    main()
