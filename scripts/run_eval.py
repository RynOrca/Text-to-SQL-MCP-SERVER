"""跑评测。

用法：
    python scripts/run_eval.py                    # 用当前配置的模型跑一遍
    python scripts/run_eval.py --repeat 3         # 跑三遍，看波动
    python scripts/run_eval.py --mode keyword     # 不用模型的规则基线
    python scripts/run_eval.py --limit 10         # 只跑前 10 题，快速冒烟

为什么要支持 --repeat：
大模型的输出并不是严格确定的。即使把温度设为 0，同一个问题两次调用也可能
生成不同的语句（服务端批处理、混合专家路由等因素）。如果只跑一次就下结论，
两个方案的差距可能完全来自运气。跑三次报均值，才敢说"A 比 B 好"。
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlcopilot.core import load_config  # noqa: E402
from sqlcopilot.evaluation import load_dataset, run_evaluation  # noqa: E402
from sqlcopilot.generation import MockLlm  # noqa: E402
from sqlcopilot.pipeline import AskPipeline  # noqa: E402


def print_report(report: dict) -> None:
    print("\n" + "=" * 52)
    print(f"执行准确率：{report['execution_accuracy']:.2%}")
    print(f"检索召回率（含图扩展）：{report['schema_recall']:.2%}")
    print(f"检索召回率（仅重排结果）：{report['schema_recall_core']:.2%}")
    print(f"平均上下文表数：{report['avg_context_tables']}")
    print(f"首轮成功率：{report['first_try_rate']:.2%}")
    print(f"平均尝试次数：{report['avg_attempts']}")
    print(f"平均耗时：{report['avg_latency_ms']} ms")
    print("\n分难度：")
    for level, data in report["by_difficulty"].items():
        print(f"  {level:<8}{data['count']:>3} 题   {data['execution_accuracy']:.2%}")
    print("\n失败归因：")
    for reason, count in report["reasons"].items():
        print(f"  {reason:<18}{count}")
    print("=" * 52)


def main() -> None:
    parser = argparse.ArgumentParser(description="批量评测")
    parser.add_argument("--mode", default="llm", choices=["llm", "keyword"])
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--repeat", type=int, default=1, help="重复跑几遍，用于观察波动")
    parser.add_argument("--config", default="config/default.yaml")
    args = parser.parse_args()

    cfg = load_config(ROOT / args.config)
    db_path = ROOT / cfg.get("database.path")
    dataset_path = ROOT / cfg.get("evaluation.dataset")
    report_dir = ROOT / cfg.get("evaluation.report_dir", "reports")

    cases = load_dataset(dataset_path)
    print(f"模型后端：{cfg.get('llm.provider')}  题量：{len(cases)}  模式：{args.mode}")

    # 规则基线不需要模型，显式传一个模拟后端，避免因为没配密钥而跑不起来
    pipeline = AskPipeline(cfg, llm=MockLlm() if args.mode == "keyword" else None)

    if args.repeat <= 1:
        report, _ = run_evaluation(
            pipeline, cases, mode=args.mode, report_dir=report_dir, limit=args.limit
        )
        print_report(report)
        return

    scores = []
    for index in range(1, args.repeat + 1):
        report, _ = run_evaluation(
            pipeline,
            cases,
            mode=args.mode,
            report_dir=report_dir,
            limit=args.limit,
            verbose=False,
            tag=f"_run{index}",
        )
        scores.append(report["execution_accuracy"])
        print(f"  第 {index} 次：{report['execution_accuracy']:.2%}")

    mean = statistics.mean(scores)
    spread = max(scores) - min(scores)
    print("\n" + "=" * 52)
    print(f"{args.repeat} 次运行：均值 {mean:.2%}，区间 [{min(scores):.2%}, {max(scores):.2%}]，极差 {spread:.2%}")
    print(f"单次结果不可直接比较，差值小于 {spread:.2%} 时不能说明方案有优劣。")
    print("=" * 52)


if __name__ == "__main__":
    main()
