"""薄链路：把工具交给客户端模型，让它自己探索、自己写语句。

厚链路（`pipeline.ask`）是我们把检索、口径、校验、修复全包了，客户端只问一句"答案是什么"。
薄链路相反：我们只提供零件，由模型自己决定看哪张表、写什么语句、要不要改。

两种链路的差别不是"哪个更好"，而是**控制权放在谁手里**：
- 厚链路：质量可控，但能力被框死在我们预设的流程里
- 薄链路：灵活，但质量完全取决于客户端模型的水平

这个模块就是为了让这两条链路能在同一套题上比一次。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TOOL_GUIDE = """你可以使用以下工具来了解数据库：

list_tables(keyword)                 列出表；keyword 为空则列出全部
describe_table(table)                查看某张表的字段、类型、说明
sample_values(table, column)         查看某个字段实际存了哪些值
run_sql(sql)                         在只读沙箱里执行一条查询

工作方式：
1. 先想清楚要查什么，按需调用工具了解表结构；
2. 信息够了就输出最终语句，格式为 FINAL: <一条查询语句>
3. 需要调工具时，只输出一行：CALL 工具名(参数1, 参数2)

注意：一次只能调用一个工具；最多调用 5 次；最终必须给出 FINAL。
"""

SYSTEM = """你是一名数据分析师，要通过工具了解数据库结构，然后写出正确的 SQLite 查询语句。

""" + TOOL_GUIDE


@dataclass
class ThinResult:
    question: str
    sql: str = ""
    rounds: int = 0
    tool_calls: list[str] = field(default_factory=list)
    transcript: list[str] = field(default_factory=list)
    error: str = ""


_CALL = re.compile(r"CALL\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*)\)", re.IGNORECASE)
_FINAL = re.compile(r"FINAL\s*[:：]\s*(.+)", re.IGNORECASE | re.DOTALL)


def _split_args(raw: str) -> list[str]:
    if not raw.strip():
        return []
    return [part.strip().strip("'\"") for part in raw.split(",")]


def _call_tool(pipeline, name: str, args: list[str]) -> str:
    name = name.lower()
    try:
        if name == "list_tables":
            keyword = args[0] if args else ""
            names = pipeline.schema.names()
            if keyword:
                names = [n for n in names if keyword in n]
            return "可用表：" + "，".join(names)
        if name == "describe_table":
            table = pipeline.schema.get(args[0]) if args else None
            return table.render() if table else f"没有这张表：{args[:1]}"
        if name == "sample_values":
            if len(args) < 2:
                return "参数不足，需要表名和字段名"
            values = pipeline.connector.sample_values(args[0], args[1], 10)
            return f"{args[0]}.{args[1]} 的取值：" + "，".join(values)
        if name == "run_sql":
            result = pipeline.connector.execute(args[0] if args else "")
            if not result.ok:
                return f"执行失败：{result.error}"
            preview = result.rows[:5]
            return f"返回 {result.row_count} 行，前几行：{preview}"
        return f"没有这个工具：{name}"
    except Exception as exc:  # noqa: BLE001 - 工具出错不能让整条链路断掉
        return f"工具出错：{type(exc).__name__}: {exc}"


def run_thin(pipeline, question: str, max_rounds: int = 5) -> ThinResult:
    """跑一次薄链路。返回最终语句与调用痕迹。"""
    result = ThinResult(question=question)
    history: list[str] = []

    for round_index in range(1, max_rounds + 1):
        result.rounds = round_index
        context = "\n".join(history) if history else "（还没有调用任何工具）"
        user = f"用户问题：{question}\n\n到目前为止的探索记录：\n{context}\n\n下一步："
        try:
            raw = pipeline.llm.complete(SYSTEM, user)
        except Exception as exc:  # noqa: BLE001
            result.error = f"模型调用失败：{exc}"
            return result

        final = _FINAL.search(raw)
        if final:
            result.sql = final.group(1).strip().strip("`").strip()
            result.transcript.append(raw)
            return result

        call = _CALL.search(raw)
        if call:
            name, raw_args = call.group(1), call.group(2)
            args = _split_args(raw_args)
            observation = _call_tool(pipeline, name, args)
            result.tool_calls.append(f"{name}({', '.join(args)})")
            history.append(f"你调用了 {name}({', '.join(args)})")
            history.append(f"工具返回：{observation[:1200]}")
            result.transcript.append(raw)
            continue

        # 既没调工具也没给最终答案，把它当成语句试试
        text = raw.strip().strip("`")
        if text.upper().startswith(("SELECT", "WITH")):
            result.sql = text
            result.transcript.append(raw)
            return result

        history.append(f"你上一条回复无法识别：{raw[:200]}")
        result.transcript.append(raw)

    result.error = "达到最大轮数仍未给出最终语句"
    last = _CALL.sub("", result.transcript[-1]) if result.transcript else ""
    result.sql = last.strip().strip("`").strip()
    return result
