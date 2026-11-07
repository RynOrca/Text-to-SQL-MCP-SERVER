"""把真正发给模型的提示词打印出来。

提示词是这个项目里最难调、也最值得看的东西。它不在代码里"某一行"，
而是由检索结果、数据字典、取值样本、修复反馈四部分拼出来的。
不亲眼看一次，很难理解模型为什么会答错。

用法：
    python scripts/show_prompt.py "华东区一共有多少客户？"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlcopilot.core import load_config  # noqa: E402
from sqlcopilot.generation import MockLlm, PromptContext, build_prompt  # noqa: E402
from sqlcopilot.pipeline import AskPipeline  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="查看提示词")
    parser.add_argument("question")
    parser.add_argument("--config", default="config/default.yaml")
    args = parser.parse_args()

    cfg = load_config(ROOT / args.config)
    # 用模拟模型，避免这里也需要密钥
    pipeline = AskPipeline(cfg, llm=MockLlm())

    retrieved = pipeline.retriever.retrieve(args.question)
    schema_text = pipeline.schema.render(retrieved.tables)
    value_text = pipeline.values.render_for(args.question, retrieved.tables)
    system, user = build_prompt(args.question, PromptContext(schema_text, value_text))

    print("=" * 70)
    print("检索到的表：", retrieved.tables)
    print("=" * 70)
    print("【系统提示词】")
    print(system)
    print("=" * 70)
    print("【用户提示词】")
    print(user)
    print("=" * 70)
    print(f"系统提示词 {len(system)} 字，用户提示词 {len(user)} 字，合计 {len(system) + len(user)} 字")


if __name__ == "__main__":
    main()
