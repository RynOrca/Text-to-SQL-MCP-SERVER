"""跑七维评测集。

用法：
    python scripts/run_suite.py                        # 跑全部
    python scripts/run_suite.py --dimension security   # 只跑一个维度
    python scripts/run_suite.py --only C01,C02         # 只跑指定用例
    python scripts/run_suite.py --priority P0          # 只跑必测核心

为什么要按维度/优先级切片：本地模型很慢，全量一次要十几分钟。
先跑 P0 拿到"能不能上线"的结论，再跑 P1/P2 定位细节，是更实用的顺序。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlcopilot.core import load_config  # noqa: E402
from sqlcopilot.evaluation.suite import (  # noqa: E402
    BLOCKED,
    ERROR,
    load_suite,
    run_case,
    summarize,
    to_markdown,
)

MARK = {"PASS": "通过", "FAIL": "失败", "BLOCKED": "阻塞", "ERROR": "错误"}


def main() -> int:
    parser = argparse.ArgumentParser(description="七维评测集")
    parser.add_argument("--suite", default="data/eval_suite_v1.jsonl")
    parser.add_argument("--config", default="config/default.yaml")
    parser.add_argument("--report-dir", default="reports")
    parser.add_argument("--tag", default="")
    parser.add_argument("--dimension", default=None)
    parser.add_argument("--priority", default=None)
    parser.add_argument("--only", default=None, help="逗号分隔的用例 ID")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-repair", type=int, default=None, help="覆盖修复轮数，用于控制单题成本")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    cfg = load_config(ROOT / args.config)
    cases = load_suite(ROOT / args.suite)
    if args.dimension:
        cases = [c for c in cases if c.get("dimension") == args.dimension]
    if args.priority:
        cases = [c for c in cases if c.get("priority") == args.priority]
    if args.only:
        wanted = {item.strip() for item in args.only.split(",") if item.strip()}
        cases = [c for c in cases if c["id"] in wanted]
    if args.limit:
        cases = cases[: args.limit]

    if not cases:
        print("没有匹配的用例")
        return 1

    effective = os.environ.get("LLM_PROVIDER") or cfg.get("llm.provider")
    model = os.environ.get("OPENAI_MODEL") or cfg.get("llm.model") or "-"
    if args.max_repair is not None:
        cfg = cfg.with_overrides({"generation": {"max_repair_rounds": args.max_repair}})
    print(f"用例 {len(cases)} 条，模型后端 {effective}（{model}）")
    print("-" * 78, flush=True)

    outcomes = []
    for index, case in enumerate(cases, start=1):
        outcome = run_case(cfg, case)
        outcomes.append(outcome)
        if not args.quiet:
            detail = outcome.detail.replace("\n", " ")[:60]
            print(
                f"[{index:>2}/{len(cases)}] {outcome.id:<4} {MARK.get(outcome.status, outcome.status):<4} "
                f"{outcome.dimension:<12} {detail}",
                flush=True,
            )

    report = summarize(outcomes)
    out_dir = ROOT / args.report_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"suite{args.tag}"
    (out_dir / f"{stem}.json").write_text(
        json.dumps(
            {"report": report, "cases": [o.to_dict() for o in outcomes]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    markdown = to_markdown(report, outcomes)
    (out_dir / f"{stem}.md").write_text(markdown, encoding="utf-8")

    print("-" * 78)
    print(f"通过 {report['pass']} / 失败 {report['fail']} / 阻塞 {report['blocked']}（共 {report['total']}）")
    print(f"可执行通过率 {report['pass_rate_executable']:.2%}")
    print(f"必测核心通过率 {report['core_pass_rate']:.2%}（{report['core_pass']}/{report['core_total']}）")
    if report["causes"]:
        print("阻塞原因：" + ", ".join(f"{k}={v}" for k, v in report["causes"].items()))
    if report["tags"]:
        print("问题标签：" + ", ".join(f"{k}={v}" for k, v in report["tags"].items()))
    print(f"报告：{out_dir / f'{stem}.md'}")

    return 0 if report["fail"] == 0 and report["blocked"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
