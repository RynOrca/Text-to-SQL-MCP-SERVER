"""薄链路与厚链路对比。

厚链路的数字直接读 `reports/eval_llm.json`（同一天、同一模型、同一套 200 题），
这里只跑薄链路，避免重复消耗一遍调用。

用法：
    python scripts/compare_chains.py
    python scripts/compare_chains.py --limit 40     # 先跑 40 题看趋势
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlcopilot.core import load_config  # noqa: E402
from sqlcopilot.evaluation import execution_match, load_dataset  # noqa: E402
from sqlcopilot.evaluation.thin_chain import run_thin  # noqa: E402
from sqlcopilot.pipeline import AskPipeline  # noqa: E402


def load_thick_accuracy(ids: list[str] | None = None) -> float | None:
    """从已保存的厚链路评测结果里取准确率。

    传了 ids 就只统计这些题，保证和薄链路跑的是同一批题——
    否则拿 60 题的结果去比 200 题的总体，没有可比性。
    """
    path = ROOT / "reports" / "eval_llm.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases", [])
    if ids is not None:
        wanted = set(ids)
        cases = [c for c in cases if c.get("id") in wanted]
    if not cases:
        return None
    return sum(1 for c in cases if c.get("correct")) / len(cases)


def main() -> None:
    parser = argparse.ArgumentParser(description="薄厚链路对比")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    cfg = load_config(ROOT / "config" / "default.yaml")
    cases = load_dataset(ROOT / cfg.get("evaluation.dataset"))
    selected = cases[: args.limit] if args.limit else cases

    pipeline = AskPipeline(cfg)

    correct = 0
    rounds: list[int] = []
    calls: list[int] = []
    errors = 0
    started = time.perf_counter()

    for index, case in enumerate(selected, start=1):
        gold = pipeline.connector.execute(case.sql)
        if not gold.ok:
            raise ValueError(f"测试题 {case.id} 的标准语句跑不通：{gold.error}")

        thin = run_thin(pipeline, case.question)
        pred = pipeline.connector.execute(thin.sql) if thin.sql else None
        ok = bool(pred and pred.ok) and execution_match(
            pred.columns, pred.rows, gold.columns, gold.rows
        )
        correct += 1 if ok else 0
        rounds.append(thin.rounds)
        calls.append(len(thin.tool_calls))
        if thin.error:
            errors += 1
        mark = "通过" if ok else "失败"
        print(f"[{index:>3}/{len(selected)}] {case.id:<6}{mark:<4}{thin.rounds} 轮 "
              f"{len(thin.tool_calls)} 次工具  {case.question}", flush=True)

    elapsed = time.perf_counter() - started
    thin_acc = correct / len(selected)
    thick_acc = load_thick_accuracy([c.id for c in selected])
    provider = os.environ.get("LLM_PROVIDER") or cfg.get("llm.provider", "mock")

    # 错误率过高说明这一轮不是"模型不会"，而是"接口挂了"。
    # 第一次通宵跑批就在这里拿到过 6% 的假结果——不设这道闸，脏数字会直接进报告。
    error_rate = errors / len(selected) if selected else 0.0
    trustworthy = error_rate <= 0.2

    lines = [
        "# 薄链路与厚链路对比",
        "",
        f"> 题量 {len(selected)}，模型 {provider}，同一批题。",
        f"> 结果状态：{'可信' if trustworthy else '**不可信**'}（未收敛 {errors} 题，"
        f"错误率 {error_rate:.1%}）。",
        "",
        "| 链路 | 执行准确率 | 平均轮数 | 平均工具调用 | 说明 |",
        "|---|---|---|---|---|",
    ]
    if thick_acc is not None:
        lines.append(
            f"| 厚链路（服务端全包） | {thick_acc:.2%} | 1.0 | 0 | 同一批题、已保存的评测结果 |"
        )
    lines.append(
        f"| 薄链路（模型自己调工具） | {thin_acc:.2%} | "
        f"{statistics.mean(rounds):.2f} | {statistics.mean(calls):.2f} | 本次实测 |"
    )
    lines += [
        "",
        "## 说明",
        "",
        f"- 薄链路里 {errors} 道题在最大轮数内没给出最终语句。",
        "- 厚链路把检索、口径、校验、修复都放在服务端，客户端只问一句。",
        "- 薄链路的优势是灵活（模型可以自己探索），代价是质量不可控、调用次数多。",
        "",
    ]
    out = ROOT / "reports" / "chains.md"
    out.write_text("\n".join(lines), encoding="utf-8")

    print("\n" + "=" * 52)
    if thick_acc is not None:
        print(f"厚链路：{thick_acc:.2%}（1 轮，0 次工具调用）")
    print(f"薄链路：{thin_acc:.2%}（平均 {statistics.mean(rounds):.2f} 轮，"
          f"{statistics.mean(calls):.2f} 次工具调用，{errors} 道未收敛）")
    print(f"总耗时：{elapsed:.0f} 秒")
    print("报告：reports/chains.md")
    if not trustworthy:
        print(f"⚠ 错误率 {error_rate:.1%} 过高，本轮结果不可信，请重跑。")
    print("=" * 52)
    if not trustworthy:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
