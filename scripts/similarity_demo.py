"""看问题与每张表的相似度分数。

这个脚本存在的唯一目的，是把"向量检索"这件抽象的事变成屏幕上的一列数字。

用法：
    python scripts/similarity_demo.py "一共有多少客户？"
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlcopilot.core import load_config  # noqa: E402
from sqlcopilot.retrieval import HashEmbedder, cosine  # noqa: E402
from sqlcopilot.schema import introspect_from_path  # noqa: E402


def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else "一共有多少客户？"
    cfg = load_config(ROOT / "config" / "default.yaml")
    schema = introspect_from_path(ROOT / cfg.get("database.path"))

    embedder = HashEmbedder(int(cfg.get("embedder.dimension", 256)))
    query_vector = embedder.embed([question])[0]

    rows = []
    for name in schema.names():
        table = schema.get(name)
        text = f"{name} {table.description} " + " ".join(
            f"{c.name} {c.description}" for c in table.columns
        )
        vector = embedder.embed([text])[0]
        rows.append((cosine(query_vector, vector), name, table.description))

    rows.sort(reverse=True)
    print(f"问题：{question}\n")
    print(f"{'相似度':>8}  表名")
    print("-" * 60)
    for score, name, desc in rows:
        print(f"{score:>8.4f}  {name:<20}{desc[:28]}")


if __name__ == "__main__":
    main()
