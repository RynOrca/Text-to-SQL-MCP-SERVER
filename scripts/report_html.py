"""把评测结果渲染成一张静态页面。

为什么不做成 Streamlit 面板：那需要额外装依赖，而且面试官打开链接时你还得开着服务。
一个自包含的 HTML 文件可以随手发出去、放进仓库、截图进简历，成本几乎为零。

运行：python scripts/report_html.py
产物：reports/report.html
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports"

CSS = """
:root { --bg:#0f1115; --panel:#181b22; --line:#2a2f3a; --text:#e6e8ee; --muted:#9aa3b2;
        --good:#3fb950; --bad:#f85149; --warn:#d29922; --accent:#58a6ff; }
* { box-sizing:border-box; }
body { margin:0; padding:40px 24px; background:var(--bg); color:var(--text);
       font:15px/1.65 -apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }
.wrap { max-width:1040px; margin:0 auto; }
h1 { font-size:26px; margin:0 0 6px; }
h2 { font-size:17px; margin:38px 0 12px; padding-left:10px; border-left:3px solid var(--accent); }
.sub { color:var(--muted); font-size:13px; margin-bottom:8px; }
.cards { display:flex; gap:14px; flex-wrap:wrap; margin-top:18px; }
.card { flex:1 1 180px; background:var(--panel); border:1px solid var(--line);
        border-radius:10px; padding:16px 18px; }
.card .k { color:var(--muted); font-size:12px; }
.card .v { font-size:26px; font-weight:600; margin-top:6px; }
table { width:100%; border-collapse:collapse; background:var(--panel);
        border:1px solid var(--line); border-radius:10px; overflow:hidden; }
th,td { padding:10px 14px; text-align:left; border-bottom:1px solid var(--line); font-size:14px; }
th { color:var(--muted); font-weight:500; font-size:12px; letter-spacing:.04em; }
tr:last-child td { border-bottom:none; }
.pos { color:var(--good); } .neg { color:var(--bad); } .flat { color:var(--muted); }
code { background:#22273255; padding:2px 6px; border-radius:4px; font-size:13px;
       font-family:ui-monospace,Consolas,monospace; }
.bad-case { background:var(--panel); border:1px solid var(--line); border-radius:10px;
            padding:14px 16px; margin-bottom:12px; }
.bad-case .q { font-weight:600; margin-bottom:8px; }
.note { color:var(--muted); font-size:13px; border-top:1px solid var(--line);
        margin-top:40px; padding-top:16px; }
"""


def load(name: str):
    path = REPORT_DIR / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _table_rows(markdown: str, header_word: str) -> list[list[str]]:
    """从 Markdown 里把以 header_word 开头的那张表抠出来。"""
    rows: list[list[str]] = []
    inside = False
    for line in markdown.splitlines():
        if line.startswith("|") and header_word in line:
            inside = True
            continue
        if inside:
            if not line.startswith("|"):
                break
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= {"-", ":"} for c in cells):
                continue
            rows.append(cells)
    return rows


def load_ablation():
    """优先读 JSON；没有就从 Markdown 报告里解析，避免中间产物被覆盖导致丢结果。"""
    data = load("ablation.json")
    # 只有一个实验的 JSON 是中间产物（比如单跑某一组），不足以当报告用
    if data and len(data) >= 2:
        return data
    single = REPORT_DIR / "ablation_single.md"
    if not single.exists():
        return None
    text = single.read_text(encoding="utf-8")
    difficulty = {row[0]: row[1:] for row in _table_rows(text, "| 实验 | single")}
    results = []
    for index, row in enumerate(_table_rows(text, "| 实验 | 执行准确率")):
        title = row[0]
        lo = difficulty.get(title, ["", "", ""])
        results.append(
            {
                "key": f"k{index}",
                "title": title,
                "accuracy": float(row[1].strip("%")) / 100,
                "recall": float(row[3].strip("%")) / 100,
                "context": float(row[4]),
                "by_difficulty": {
                    "single": {"execution_accuracy": float(lo[0].strip("%")) / 100 if lo[0] else 0},
                    "multi": {"execution_accuracy": float(lo[1].strip("%")) / 100 if lo[1] else 0},
                    "nested": {"execution_accuracy": float(lo[2].strip("%")) / 100 if lo[2] else 0},
                },
            }
        )
    results[0]["key"] = "base"
    return results or None


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def delta_class(value: float) -> str:
    if value > 0.005:
        return "pos"
    if value < -0.005:
        return "neg"
    return "flat"


def card(label: str, value: str) -> str:
    return f'<div class="card"><div class="k">{html.escape(label)}</div><div class="v">{html.escape(value)}</div></div>'


def section_baseline(llm, keyword) -> str:
    rows = []
    if llm:
        r = llm["report"]
        rows.append(("大模型（当前配置）", pct(r["execution_accuracy"]), pct(r["schema_recall"]),
                     str(r["avg_context_tables"]), f'{r["avg_latency_ms"]:.0f} ms'))
    if keyword:
        r = keyword["report"]
        rows.append(("规则基线（不用模型）", pct(r["execution_accuracy"]), pct(r["schema_recall"]),
                     str(r["avg_context_tables"]), f'{r["avg_latency_ms"]:.0f} ms'))
    body = "".join(
        f"<tr><td>{html.escape(name)}</td><td>{acc}</td><td>{rec}</td><td>{ctx}</td><td>{lat}</td></tr>"
        for name, acc, rec, ctx, lat in rows
    )
    return (
        "<h2>基线对比</h2>"
        '<table><tr><th>方案</th><th>执行准确率</th><th>检索召回率</th><th>上下文表数</th><th>平均耗时</th></tr>'
        f"{body}</table>"
    )


def section_difficulty(llm) -> str:
    if not llm:
        return ""
    labels = {"single": "单表", "multi": "多表连接", "nested": "嵌套子查询"}
    rows = "".join(
        f"<tr><td>{labels.get(level, level)}</td><td>{data['count']}</td>"
        f"<td>{pct(data['execution_accuracy'])}</td></tr>"
        for level, data in llm["report"]["by_difficulty"].items()
    )
    return (
        "<h2>分难度表现</h2>"
        "<table><tr><th>难度</th><th>题量</th><th>执行准确率</th></tr>"
        f"{rows}</table>"
    )


def section_reasons(llm) -> str:
    if not llm:
        return ""
    reasons = llm["report"]["reasons"]
    rows = "".join(
        f"<tr><td><code>{html.escape(name)}</code></td><td>{count}</td></tr>"
        for name, count in reasons.items()
    )
    return (
        "<h2>失败归因</h2>"
        "<table><tr><th>原因码</th><th>数量</th></tr>"
        f"{rows}</table>"
    )


def section_ablation(ablation) -> str:
    if not ablation:
        return ""
    base = next((item for item in ablation if item["key"] == "base"), ablation[0])
    rows = ""
    for item in ablation:
        delta = item["accuracy"] - base["accuracy"]
        sign = "+" if delta >= 0 else ""
        rows += (
            f"<tr><td>{html.escape(item['title'])}</td>"
            f"<td>{pct(item['accuracy'])}</td>"
            f'<td class="{delta_class(delta)}">{sign}{pct(delta)}</td>'
            f"<td>{pct(item['recall'])}</td><td>{item['context']}</td></tr>"
        )
    return (
        "<h2>消融实验</h2>"
        '<div class="sub">每组只关掉一个模块。差值小于单次波动幅度时不能下结论。</div>'
        "<table><tr><th>实验</th><th>执行准确率</th><th>相对基准</th><th>检索召回</th><th>上下文表数</th></tr>"
        f"{rows}</table>"
    )


def section_bad_cases(llm, limit: int = 8) -> str:
    if not llm:
        return ""
    wrong = [case for case in llm["cases"] if not case["correct"]][:limit]
    if not wrong:
        return ""
    blocks = []
    for case in wrong:
        blocks.append(
            '<div class="bad-case">'
            f'<div class="q">{html.escape(case["id"])} · {html.escape(case["reason"])} · {html.escape(case["question"])}</div>'
            f'<div class="sub">标准</div><code>{html.escape(case["gold_sql"])}</code>'
            f'<div class="sub" style="margin-top:8px">生成</div><code>{html.escape(case["pred_sql"])}</code>'
            "</div>"
        )
    return "<h2>错题样例</h2>" + "".join(blocks)


def main() -> None:
    llm = load("eval_llm.json")
    keyword = load("eval_keyword.json")
    ablation = load_ablation()

    cards = ""
    if llm:
        r = llm["report"]
        cards = (
            card("题量", str(r["total"]))
            + card("执行准确率", pct(r["execution_accuracy"]))
            + card("检索召回率", pct(r["schema_recall"]))
            + card("平均上下文表数", str(r["avg_context_tables"]))
        )

    body = (
        "<h1>问数 · 评测报告</h1>"
        f'<div class="sub">生成时间 {datetime.now().strftime("%Y-%m-%d %H:%M")} · '
        "数据来自 reports/ 下的评测结果</div>"
        f'<div class="cards">{cards}</div>'
        + section_baseline(llm, keyword)
        + section_difficulty(llm)
        + section_reasons(llm)
        + section_ablation(ablation)
        + section_bad_cases(llm)
        + '<div class="note">执行准确率 = 生成语句与标准语句的执行结果集完全一致的比例。'
          "检索召回率 = 标准语句用到的表进入候选集的比例。"
          "上下文表数 = 实际塞进提示词的表数量，它是召回率的代价。</div>"
    )

    out = REPORT_DIR / "report.html"
    out.write_text(
        f"<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        f"<title>问数 · 评测报告</title><style>{CSS}</style></head>"
        f"<body><div class='wrap'>{body}</div></body></html>",
        encoding="utf-8",
    )
    print(f"已生成：{out}")


if __name__ == "__main__":
    main()
