"""消融实验：每次只关掉一个模块，看指标掉多少。

这是整个项目里最有说服力的产物。它回答的是面试官最常问的那句话：
"你说这个模块有用，证据呢？"

用法：
    python scripts/run_ablation.py              # 全部实验各跑一遍
    python scripts/run_ablation.py --only A     # 只跑某一组
    python scripts/run_ablation.py --repeat 2   # 每组跑两遍，看波动

产物：reports/ablation.md
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
# 实验分支在项目根的 experiments/ 下，需要根目录也在路径里
sys.path.insert(0, str(ROOT))

from sqlcopilot.core import load_config  # noqa: E402
from sqlcopilot.evaluation import load_dataset, run_evaluation  # noqa: E402
from sqlcopilot.pipeline import AskPipeline  # noqa: E402


@dataclass
class Experiment:
    key: str
    title: str
    overrides: dict
    use_values: bool = True
    note: str = ""


EXPERIMENTS: list[Experiment] = [
    Experiment("base", "完整方案", {}, note="作为对照的基准"),
    Experiment(
        "A",
        "A 不检索，全量表结构",
        {"retriever": {"rerank": {"top_n": 24}, "graph_expand": {"enabled": False}}},
        note="验证决策三：检索裁剪是否必要",
    ),
    Experiment(
        "A2",
        "A2 只给 3 张表",
        {"retriever": {"rerank": {"top_n": 3}, "graph_expand": {"enabled": False}}},
        note="上下文更省，但漏表风险更高",
    ),
    Experiment(
        "B",
        "B 关闭外键图扩展",
        {"retriever": {"graph_expand": {"enabled": False}}},
        note="验证决策四：语义相似不等于需要连接",
    ),
    Experiment(
        "C",
        "C 关闭报错重写",
        {"generation": {"max_repair_rounds": 0}},
        note="验证决策五：修复循环值不值",
    ),
    Experiment(
        "D",
        "D 只用向量召回",
        {"retriever": {"use_sparse": False}},
        note="验证混合检索的必要性",
    ),
    Experiment(
        "E",
        "E 关闭取值索引",
        {},
        use_values=False,
        note="验证'华东区''已退款'这类取值提示的价值",
    ),
    Experiment(
        "R",
        "R 启用重叠重排",
        {"retriever": {"rerank": {"provider": "overlap"}}},
        note="项目默认关闭重排，这一组验证它到底有没有用",
    ),
    Experiment(
        "R2",
        "R2 启用交叉编码器重排",
        {"retriever": {"rerank": {"provider": "cross_encoder"}}},
        note="同时读问题和表描述的专用小模型，真重排",
    ),
    Experiment(
        "R3",
        "R3 启用大模型重排",
        {"retriever": {"rerank": {"provider": "llm"}}},
        note="把候选表交给大模型排序，每题多一次调用",
    ),
    Experiment(
        "V2",
        "V2 交叉编码器适配版（先扩展后精排）",
        {
            "retriever": {
                "rerank": {"provider": "cross_encoder", "score_margin": 0.0, "top_n": 6},
                "expand_first": True,
                "graph_expand": {"seed_count": 3},
                "max_tables": 6,
            }
        },
        note="把顺序反过来：先在候选池上做图扩展，再让交叉编码器在扩展后的池子里做最终裁决",
    ),
]


def run_one(exp: Experiment, cfg, cases, db_path, repeat: int) -> dict:
    scores = []
    last = None
    for _ in range(repeat):
        pipeline = AskPipeline(
            cfg.with_overrides(exp.overrides),
            db_path,
            use_values=exp.use_values,
        )
        report, _ = run_evaluation(
            pipeline,
            cases,
            mode="llm",
            report_dir=ROOT / "reports" / "ablation",
            verbose=False,
            tag=f"_{exp.key}",
        )
        scores.append(report["execution_accuracy"])
        last = report
    assert last is not None
    return {
        "key": exp.key,
        "title": exp.title,
        "note": exp.note,
        "accuracy": round(sum(scores) / len(scores), 4),
        "runs": scores,
        "recall": last["schema_recall"],
        "recall_core": last["schema_recall_core"],
        "context": last["avg_context_tables"],
        "by_difficulty": last["by_difficulty"],
    }


def to_markdown(results: list[dict], base: dict, repeat: int) -> str:
    lines = [
        "# 消融实验报告",
        "",
        f"> 每组跑 {repeat} 遍取均值。题量 42（单表 21 / 多表 12 / 嵌套 9）。",
        "> 单次结果的波动可能达到 2 到 5 个百分点，因此差值小于波动幅度时不下结论。",
        "",
        "## 总表",
        "",
        "| 实验 | 执行准确率 | 相对基准 | 检索召回 | 上下文表数 |",
        "|---|---|---|---|---|",
    ]
    for item in results:
        delta = item["accuracy"] - base["accuracy"]
        sign = "+" if delta >= 0 else ""
        lines.append(
            f"| {item['title']} | {item['accuracy']:.2%} | {sign}{delta:.2%} | "
            f"{item['recall']:.2%} | {item['context']} |"
        )

    lines += ["", "## 分难度", "", "| 实验 | single | multi | nested |", "|---|---|---|---|"]
    for item in results:
        d = item["by_difficulty"]
        lines.append(
            f"| {item['title']} | {d['single']['execution_accuracy']:.0%} | "
            f"{d['multi']['execution_accuracy']:.0%} | {d['nested']['execution_accuracy']:.0%} |"
        )

    lines += ["", "## 逐组说明", ""]
    for item in results:
        if item["key"] == "base":
            continue
        delta = item["accuracy"] - base["accuracy"]
        verdict = "掉点" if delta < -0.02 else ("提升" if delta > 0.02 else "基本持平")
        lines.append(f"### {item['title']}")
        lines.append(f"- 目的：{item['note']}")
        lines.append(f"- 结果：{item['accuracy']:.2%}（相对基准 {delta:+.2%}），判定为{verdict}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="消融实验")
    parser.add_argument("--only", default=None, help="只跑某一组，比如 A / B / base")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--force", action="store_true", help="忽略已保存的结果，全部重跑")
    args = parser.parse_args()

    cfg = load_config(ROOT / "config" / "default.yaml")
    db_path = ROOT / cfg.get("database.path")
    cases = load_dataset(ROOT / cfg.get("evaluation.dataset"))

    selected = [e for e in EXPERIMENTS if args.only is None or e.key == args.only]
    if not selected:
        raise SystemExit(f"没有找到实验：{args.only}")

    results: list[dict] = []
    base: dict | None = None
    out_dir = ROOT / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 断点续跑：已经跑完的组直接复用，避免因为一次网络抖动重跑几十分钟
    existing: dict[str, dict] = {}
    prior = out_dir / "ablation.json"
    if prior.exists() and not args.force:
        try:
            for item in json.loads(prior.read_text(encoding="utf-8")):
                existing[item["key"]] = item
        except (json.JSONDecodeError, KeyError):
            existing = {}

    # 用字典累积，最后按 EXPERIMENTS 的顺序输出。
    # 这样 "--only R" 只补跑一组时，不会把之前的七组结果覆盖掉。
    collected: dict[str, dict] = dict(existing) if args.only else {}

    for exp in selected:
        if exp.key in existing:
            print(f"[{exp.key}] 已完成，跳过（{existing[exp.key]['accuracy']:.2%}）", flush=True)
            collected[exp.key] = existing[exp.key]
            continue

        started = time.perf_counter()
        print(f"[{exp.key}] {exp.title} ……", flush=True)
        result = run_one(exp, cfg, cases, db_path, args.repeat)
        collected[exp.key] = result
        elapsed = time.perf_counter() - started
        print(f"    → 准确率 {result['accuracy']:.2%}  召回 {result['recall']:.2%}  "
              f"上下文 {result['context']} 张  用时 {elapsed:.0f}s", flush=True)

        # 每跑完一组就落盘。跑了四十分钟因为一次 503 全丢掉，是最亏的失败方式。
        ordered = [collected[e.key] for e in EXPERIMENTS if e.key in collected]
        baseline = collected.get("base") or ordered[0]
        (out_dir / "ablation.json").write_text(
            json.dumps(ordered, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out_dir / "ablation.md").write_text(
            to_markdown(ordered, baseline, args.repeat), encoding="utf-8"
        )

    results = [collected[e.key] for e in EXPERIMENTS if e.key in collected]
    baseline = collected.get("base") or results[0]
    print("\n报告已写入 reports/ablation.md")
    print("\n" + "| 实验 | 准确率 | 相对基准 | 上下文 |")
    print("|---|---|---|---|")
    for item in results:
        delta = item["accuracy"] - baseline["accuracy"]
        print(f"| {item['title']} | {item['accuracy']:.2%} | {delta:+.2%} | {item['context']} |")


if __name__ == "__main__":
    main()
