"""校验测试集：每条标准语句都必须真的能跑通，否则评测没有意义。

运行：python scripts/validate_golden.py
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlcopilot.connectors import SQLiteConnector  # noqa: E402
from sqlcopilot.core import load_config  # noqa: E402
from sqlcopilot.evaluation import load_dataset  # noqa: E402
from sqlcopilot.schema import introspect_from_path  # noqa: E402


def main() -> int:
    cfg = load_config(ROOT / "config" / "default.yaml")
    db_path = ROOT / cfg.get("database.path")
    dataset_path = ROOT / cfg.get("evaluation.dataset")

    cases = load_dataset(dataset_path)
    schema = introspect_from_path(db_path)
    connector = SQLiteConnector(db_path, timeout_s=30, max_rows=1000)

    problems = 0
    styles = Counter(c.difficulty for c in cases)
    ids = Counter(c.id for c in cases)
    duplicates = [cid for cid, n in ids.items() if n > 1]

    print(f"题量：{len(cases)}  难度分布：{dict(styles)}")
    if duplicates:
        print(f"重复编号：{duplicates}")
        problems += len(duplicates)

    for case in cases:
        result = connector.execute(case.sql)
        if not result.ok:
            print(f"[ERROR] {case.id} 标准语句执行失败：{result.error}")
            print(f"        {case.sql}")
            problems += 1
            continue
        if result.row_count == 0:
            print(f"[WARN ] {case.id} 结果为空，可能是条件写错：{case.question}")
        unknown = [t for t in case.tables if t not in schema]
        if unknown:
            print(f"[ERROR] {case.id} tables 字段引用了不存在的表：{unknown}")
            problems += 1

    print(f"\n校验完成，问题数：{problems}")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
