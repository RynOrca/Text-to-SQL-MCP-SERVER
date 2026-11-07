"""评测执行器：批量跑题、汇总指标、生成报告。"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..core import QueryResult
from ..pipeline import AskPipeline
from .baselines import keyword_baseline
from .dataset import DIFFICULTIES, Case
from .metrics import execution_match, ratio

MODES = ("llm", "keyword")


@dataclass
class CaseOutcome:
    id: str
    difficulty: str
    question: str
    gold_sql: str
    pred_sql: str = ""
    correct: bool = False
    schema_recall: float = 0.0
    schema_recall_core: float = 0.0
    context_tables: int = 0
    attempts: int = 0
    first_try_ok: bool = False
    pred_columns: int = 0
    gold_columns: int = 0
    error: str = ""
    reason: str = ""
    latency_ms: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _attribute(outcome: CaseOutcome) -> str:
    """给错题打一个原因码。这是"失败归因"的落地实现。"""
    if outcome.correct:
        return "OK"
    # 用"含图扩展"的召回率判断：模型实际看到的是扩展后的上下文，
    # 只看重排结果会把"图扩展补上了、但模型还是写错"误判成检索未命中。
    if outcome.schema_recall < 1.0:
        return "RETRIEVAL_MISS"
    if outcome.error:
        return "EXEC_ERROR"
    if not outcome.first_try_ok:
        return "REPAIR_FAILED"
    if outcome.pred_columns != outcome.gold_columns:
        return "COLUMN_MISMATCH"
    return "SEMANTIC_ERROR"


def run_case(pipeline: AskPipeline, case: Case, mode: str = "llm") -> CaseOutcome:
    started = time.perf_counter()
    retrieval = pipeline.retriever.retrieve(case.question)

    gold = pipeline.connector.execute(case.sql)
    if not gold.ok:
        raise ValueError(f"测试题 {case.id} 的标准语句跑不通：{gold.error}\n{case.sql}")

    if mode == "keyword":
        sql = keyword_baseline(case.question, retrieval, pipeline.schema)
        pred = pipeline.connector.execute(sql)
        attempts = 1
        first_ok = pred.ok
        error = pred.error or ""
    else:
        answer = pipeline.ask(case.question, retrieval=retrieval)
        sql = answer.sql
        pred = answer.result or QueryResult(error=answer.error)
        attempts = answer.attempts
        first_ok = answer.first_try_ok
        error = answer.error or ("" if pred.ok else (pred.error or ""))

    correct = bool(pred.ok) and execution_match(pred.columns, pred.rows, gold.columns, gold.rows)

    outcome = CaseOutcome(
        id=case.id,
        difficulty=case.difficulty,
        question=case.question,
        gold_sql=case.sql,
        pred_sql=sql,
        correct=correct,
        schema_recall=retrieval.hit(case.tables, include_expanded=True),
        schema_recall_core=retrieval.hit(case.tables, include_expanded=False),
        context_tables=len(retrieval.tables),
        attempts=attempts,
        first_try_ok=first_ok,
        pred_columns=len(pred.columns),
        gold_columns=len(gold.columns),
        error=error,
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
    )
    outcome.reason = _attribute(outcome)
    return outcome


def summarize(outcomes: list[CaseOutcome], mode: str) -> dict:
    total = len(outcomes)
    correct = sum(1 for o in outcomes if o.correct)
    by_difficulty = {}
    for level in DIFFICULTIES:
        subset = [o for o in outcomes if o.difficulty == level]
        by_difficulty[level] = {
            "count": len(subset),
            "execution_accuracy": ratio(sum(1 for o in subset if o.correct), len(subset)),
        }

    reasons: dict[str, int] = {}
    for outcome in outcomes:
        reasons[outcome.reason] = reasons.get(outcome.reason, 0) + 1

    latencies = [o.latency_ms for o in outcomes] or [0.0]
    return {
        "mode": mode,
        "total": total,
        "execution_accuracy": ratio(correct, total),
        "schema_recall": round(statistics.mean(o.schema_recall for o in outcomes), 4) if outcomes else 0.0,
        "schema_recall_core": round(statistics.mean(o.schema_recall_core for o in outcomes), 4) if outcomes else 0.0,
        "avg_context_tables": round(statistics.mean(o.context_tables for o in outcomes), 2) if outcomes else 0.0,
        "first_try_rate": ratio(sum(1 for o in outcomes if o.first_try_ok), total),
        "avg_attempts": round(statistics.mean(o.attempts for o in outcomes), 2) if outcomes else 0.0,
        "avg_latency_ms": round(statistics.mean(latencies), 2),
        "by_difficulty": by_difficulty,
        "reasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
    }


def to_markdown(report: dict, outcomes: list[CaseOutcome]) -> str:
    lines = [
        f"# 评测报告（{report['mode']}）",
        "",
        "## 总览",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
        f"| 题量 | {report['total']} |",
        f"| 执行准确率 | {report['execution_accuracy']:.2%} |",
        f"| 检索召回率（含图扩展） | {report['schema_recall']:.2%} |",
        f"| 检索召回率（仅重排结果） | {report['schema_recall_core']:.2%} |",
        f"| 平均上下文表数 | {report['avg_context_tables']} |",
        f"| 首轮成功率 | {report['first_try_rate']:.2%} |",
        f"| 平均尝试次数 | {report['avg_attempts']} |",
        f"| 平均耗时 | {report['avg_latency_ms']} ms |",
        "",
        "## 分难度",
        "",
        "| 难度 | 题量 | 执行准确率 |",
        "|---|---|---|",
    ]
    for level, data in report["by_difficulty"].items():
        lines.append(f"| {level} | {data['count']} | {data['execution_accuracy']:.2%} |")

    lines += ["", "## 失败归因", "", "| 原因码 | 数量 |", "|---|---|"]
    for reason, count in report["reasons"].items():
        lines.append(f"| {reason} | {count} |")

    wrong = [o for o in outcomes if not o.correct][:20]
    if wrong:
        lines += ["", "## 错题样例（最多 20 条）", ""]
        for o in wrong:
            lines += [
                f"### {o.id} · {o.difficulty} · {o.reason}",
                f"- 问题：{o.question}",
                f"- 标准：`{o.gold_sql}`",
                f"- 生成：`{o.pred_sql}`",
            ]
            if o.error:
                lines.append(f"- 错误：{o.error}")
            lines.append("")
    return "\n".join(lines)


def run_evaluation(
    pipeline: AskPipeline,
    cases: list[Case],
    mode: str = "llm",
    report_dir: str | Path = "reports",
    limit: int | None = None,
    verbose: bool = True,
    tag: str = "",
) -> tuple[dict, list[CaseOutcome]]:
    if mode not in MODES:
        raise ValueError(f"未知模式：{mode}，可选 {MODES}")

    selected = cases[:limit] if limit else cases
    outcomes: list[CaseOutcome] = []
    for index, case in enumerate(selected, start=1):
        outcome = run_case(pipeline, case, mode=mode)
        outcomes.append(outcome)
        if verbose:
            mark = "通过" if outcome.correct else f"失败({outcome.reason})"
            print(f"[{index:>3}/{len(selected)}] {case.id:<8}{mark:<18}{case.question}")

    report = summarize(outcomes, mode)

    out_dir = Path(report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"eval_{mode}{tag}.json"
    md_path = out_dir / f"eval_{mode}{tag}.md"
    json_path.write_text(
        json.dumps({"report": report, "cases": [o.to_dict() for o in outcomes]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_path.write_text(to_markdown(report, outcomes), encoding="utf-8")
    return report, outcomes
