"""在同一批题上比较"本地模型"和"历史基线"。

为什么需要这个脚本：项目里有 200 题主集，但它是用云端模型跑的（平均 2 秒/题），
换成 8GB 显卡上的 27B 量化模型之后，"准确率还剩多少"这个问题不能靠感觉回答。

做法是**分层抽样 + 配对比较**：
1. 按难度（single/multi/nested）各抽 N 题，固定随机种子，保证可复现；
2. 在同一批题上跑本地模型；
3. 把逐题结果和历史报告里的逐题结果按 ID 配对，算出"两边都对 / 只有本地对 / 只有基线对"。

配对的意义：同样是 80%，在一个"基线只有 60%"的子集上，含金量完全不同。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlcopilot.core import load_config, load_dotenv  # noqa: E402
from sqlcopilot.evaluation import load_dataset  # noqa: E402
from sqlcopilot.evaluation.runner import run_case  # noqa: E402
from sqlcopilot.pipeline import AskPipeline  # noqa: E402

LEVELS = ("single", "multi", "nested")


def sample(cases: list, per_level: int, seed: int) -> list:
    rng = random.Random(seed)
    picked: list = []
    for level in LEVELS:
        pool = [case for case in cases if case.difficulty == level]
        picked.extend(rng.sample(pool, min(per_level, len(pool))))
    return picked


def main() -> int:
    parser = argparse.ArgumentParser(description="本地模型 vs 历史基线的配对比较")
    parser.add_argument("--per-level", type=int, default=12)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--config", default="config/default.yaml")
    parser.add_argument("--baseline", default="reports/eval_llm.json")
    parser.add_argument("--tag", default="local")
    parser.add_argument("--max-repair", type=int, default=0)
    parser.add_argument("--out", default="reports/benchmark_local.json")
    args = parser.parse_args()

    load_dotenv()
    cfg = load_config(ROOT / args.config).with_overrides(
        {"generation": {"max_repair_rounds": args.max_repair}}
    )
    dataset = load_dataset(ROOT / "data" / "golden_set.jsonl")
    cases = sample(dataset, args.per_level, args.seed)

    baseline_path = ROOT / args.baseline
    baseline: dict[str, bool] = {}
    if baseline_path.exists():
        payload = json.loads(baseline_path.read_text(encoding="utf-8"))
        baseline = {c["id"]: bool(c["correct"]) for c in payload["cases"]}

    pipeline = AskPipeline(cfg)
    llm = pipeline.llm
    rows = []
    print(f"抽样 {len(cases)} 题（每档 {args.per_level}，seed={args.seed}），每题最多 1 轮生成", flush=True)

    for index, case in enumerate(cases, start=1):
        before = getattr(getattr(llm, "usage", None), "total_tokens", 0)
        started = time.perf_counter()
        record = {
            "id": case.id,
            "difficulty": case.difficulty,
            "question": case.question,
            "baseline_correct": baseline.get(case.id),
        }
        try:
            outcome = run_case(pipeline, case, mode="llm")
            record.update(
                {
                    "correct": bool(outcome.correct),
                    "pred_sql": outcome.pred_sql,
                    "reason": outcome.reason,
                    "attempts": outcome.attempts,
                    "error": outcome.error,
                    "blocked": False,
                }
            )
        except BaseException as exc:  # noqa: BLE001 - 超时/超限算阻塞，不算答错
            record.update(
                {
                    "correct": False,
                    "pred_sql": "",
                    "reason": "BLOCKED",
                    "attempts": 0,
                    "error": f"{type(exc).__name__}: {exc}"[:200],
                    "blocked": True,
                }
            )
        record["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
        record["tokens"] = getattr(getattr(llm, "usage", None), "total_tokens", 0) - before
        rows.append(record)
        flag = "阻塞" if record["blocked"] else ("对" if record["correct"] else "错")
        print(
            f"[{index:>2}/{len(cases)}] {case.id:<5} {case.difficulty:<7} {flag:<3} "
            f"{record['latency_ms']:>7.0f}ms {record['tokens']:>5}tok  {case.question[:32]}",
            flush=True,
        )

    graded = [r for r in rows if not r["blocked"]]
    blocked = [r for r in rows if r["blocked"]]

    def accuracy(subset: list, key: str = "correct") -> float:
        return round(sum(1 for r in subset if r[key]) / len(subset), 4) if subset else 0.0

    paired = [r for r in graded if r["baseline_correct"] is not None]
    both = sum(1 for r in paired if r["correct"] and r["baseline_correct"])
    only_local = sum(1 for r in paired if r["correct"] and not r["baseline_correct"])
    only_base = sum(1 for r in paired if not r["correct"] and r["baseline_correct"])
    neither = sum(1 for r in paired if not r["correct"] and not r["baseline_correct"])

    summary = {
        "tag": args.tag,
        "per_level": args.per_level,
        "seed": args.seed,
        "sampled": len(rows),
        "blocked": len(blocked),
        "graded": len(graded),
        "local_accuracy_graded": accuracy(graded),
        "baseline_accuracy_paired": accuracy(paired, "baseline_correct"),
        "local_accuracy_paired": accuracy(paired),
        "by_difficulty": {
            level: {
                "n": len([r for r in graded if r["difficulty"] == level]),
                "local": accuracy([r for r in graded if r["difficulty"] == level]),
            }
            for level in LEVELS
        },
        "paired": {"both": both, "only_local": only_local, "only_baseline": only_base, "neither": neither},
        "latency_ms": sorted(r["latency_ms"] for r in rows),
        "tokens": sorted(r["tokens"] for r in rows),
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "cases": rows}, ensure_ascii=False, indent=2), encoding="utf-8")

    lat = summary["latency_ms"]
    def pct(values: list, q: float) -> float:
        if not values:
            return 0.0
        pos = (len(values) - 1) * q
        lo = int(pos)
        hi = min(lo + 1, len(values) - 1)
        return round(values[lo] * (1 - (pos - lo)) + values[hi] * (pos - lo), 1)

    print("\n" + "=" * 62)
    print(f"抽样 {summary['sampled']} 题，阻塞 {summary['blocked']}，有效 {summary['graded']}")
    print(f"本地模型准确率（有效题）：{summary['local_accuracy_graded']:.2%}")
    if paired:
        print(f"配对子集 {len(paired)} 题：本地 {summary['local_accuracy_paired']:.2%} vs 基线 {summary['baseline_accuracy_paired']:.2%}")
        print(f"配对四格：都对 {both} / 只有本地对 {only_local} / 只有基线对 {only_base} / 都错 {neither}")
    for level, data in summary["by_difficulty"].items():
        print(f"  {level:<8} n={data['n']:<3} 本地 {data['local']:.2%}")
    print(f"延迟 P50 {pct(lat,0.5):.0f}ms / P95 {pct(lat,0.95):.0f}ms")
    print(f"报告：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
