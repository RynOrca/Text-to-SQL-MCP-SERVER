"""诊断：为什么交叉编码器重排（R2）比不重排更差。

只做离线分析，不调模型。对比两套配置在每道题上的：
  1. 前 2 名种子表是否相同（图扩展的出发点）
  2. 前 5 名候选集合是否相同
  3. 逐题对错（读已保存的评测结果）

用法：python scripts/diagnose_rerank.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from sqlcopilot.connectors import SQLiteConnector  # noqa: E402
from sqlcopilot.core import load_config  # noqa: E402
from sqlcopilot.evaluation import load_dataset  # noqa: E402
from sqlcopilot.retrieval import SchemaRetriever, build_value_index  # noqa: E402
from sqlcopilot.schema import introspect_from_path  # noqa: E402


def load_results(name: str) -> dict[str, bool]:
    path = ROOT / "reports" / "ablation" / f"eval_llm_{name}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return {item["id"]: bool(item["correct"]) for item in data["cases"]}


def main() -> None:
    cfg = load_config(ROOT / "config" / "default.yaml")
    db_path = ROOT / cfg.get("database.path")
    cases = load_dataset(ROOT / cfg.get("evaluation.dataset"))
    schema = introspect_from_path(db_path)
    connector = SQLiteConnector(db_path)
    values = build_value_index(connector, schema)

    base = SchemaRetriever(schema, cfg, values)
    re_ranked = SchemaRetriever(
        schema,
        cfg.with_overrides({"retriever": {"rerank": {"provider": "cross_encoder"}}}),
        values,
    )

    base_ok = load_results("base")
    ce_ok = load_results("R2")

    stats = Counter()
    changed_and_broken = 0
    changed_and_fixed = 0
    same_and_broken = 0

    for case in cases:
        if case.id not in base_ok or case.id not in ce_ok:
            continue
        b = base.retrieve(case.question)
        c = re_ranked.retrieve(case.question)

        b_top2 = [n for n, _ in b.ranked][:2]
        c_top2 = [n for n, _ in c.ranked][:2]
        seeds_changed = set(b_top2) != set(c_top2)
        top5_changed = {n for n, _ in b.ranked} != {n for n, _ in c.ranked}

        stats["total"] += 1
        if seeds_changed:
            stats["种子集合变了"] += 1
        if top5_changed:
            stats["前5集合变了"] += 1

        broken = base_ok[case.id] and not ce_ok[case.id]
        fixed = (not base_ok[case.id]) and ce_ok[case.id]
        if broken and seeds_changed:
            changed_and_broken += 1
        if fixed and seeds_changed:
            changed_and_fixed += 1
        if broken and not seeds_changed:
            same_and_broken += 1

    n = stats["total"]
    print(f"题量：{n}")
    print(f"种子集合变化：{stats['种子集合变了']} 题（{stats['种子集合变了'] / n:.1%}）")
    print(f"前 5 候选集合变化：{stats['前5集合变了']} 题（{stats['前5集合变了'] / n:.1%}）")
    print()
    print(f"base 对、R2 错（恶化的题）：{sum(1 for i in base_ok if base_ok[i] and not ce_ok.get(i, True))} 题")
    print(f"  · 其中种子集合变了：{changed_and_broken} 题")
    print(f"  · 种子没变、纯属模型波动：{same_and_broken} 题")
    print(f"base 错、R2 对（改善的题）：{sum(1 for i in ce_ok if ce_ok[i] and not base_ok.get(i, True))} 题")
    print(f"  · 其中种子集合变了：{changed_and_fixed} 题")


if __name__ == "__main__":
    main()
