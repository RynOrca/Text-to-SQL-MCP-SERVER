"""对外暴露的模型上下文协议服务。

设计上刻意分了**两条调用链**，这不是为了炫技，而是这个项目里最值得对比的一个问题：

- 薄链路：把 list_tables / describe_table / sample_values / run_sql 这些**底层工具**
  交给客户端的大模型，让它自己探索库、自己写语句。
  优点：灵活，奇怪的问题也能处理。缺点：质量完全取决于客户端模型，你无法干预。

- 厚链路：只暴露一个 ask_data，检索、生成、校验、修复全在服务内部完成。
  优点：可控，能注入口径、能修复、能评测。缺点：能力被封死，复杂问题绕不过去。

同一个测试集比两条链路，才能回答"能力应该放在哪一层"。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..core.interfaces import QueryResult

if TYPE_CHECKING:  # 只在类型检查时导入，避免没装 mcp 的环境直接崩
    from ..pipeline import AskPipeline

MAX_PREVIEW_ROWS = 20


def _render(result: QueryResult) -> str:
    if not result.ok:
        return f"执行失败：{result.error}"
    if not result.rows:
        return "执行成功，但返回 0 行。通常是条件里的取值与库中存储的不一致。"
    header = "| " + " | ".join(result.columns) + " |"
    divider = "|" + "|".join(["---"] * len(result.columns)) + "|"
    body = [
        "| " + " | ".join(str(value) for value in row) + " |"
        for row in result.rows[:MAX_PREVIEW_ROWS]
    ]
    lines = [header, divider, *body]
    if len(result.rows) > MAX_PREVIEW_ROWS or result.truncated:
        lines.append(f"\n（仅显示前 {MAX_PREVIEW_ROWS} 行，实际 {result.row_count} 行）")
    return "\n".join(lines)


def create_server(pipeline: "AskPipeline"):
    """按需导入 mcp 包，返回一个配置好工具的服务器实例。"""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "没有安装 mcp 包。请先执行 pip install mcp，再启动服务。"
        ) from exc

    server = FastMCP("问数（AskData）")

    @server.tool()
    def list_tables(keyword: str = "") -> str:
        """列出库里的表。keyword 不为空时按名称或说明做模糊过滤。"""
        names = pipeline.schema.names()
        if keyword:
            lowered = keyword.lower()
            names = [
                name
                for name in names
                if lowered in name.lower()
                or lowered in (pipeline.schema.get(name).description or "").lower()
            ]
        if not names:
            return f"没有找到与「{keyword}」相关的表。"
        lines = [f"- `{name}`：{pipeline.schema.get(name).description}" for name in names]
        return f"共 {len(names)} 张表：\n" + "\n".join(lines)

    @server.tool()
    def describe_table(table: str) -> str:
        """查看一张表的字段、类型、中文说明、行数与外键。"""
        model = pipeline.schema.get(table)
        if model is None:
            return f"表 `{table}` 不存在。可用的表：{', '.join(pipeline.schema.names())}"
        return model.render()

    @server.tool()
    def sample_values(table: str, column: str, limit: int = 10) -> str:
        """抽样查看某个字段实际存了哪些值，用来确认过滤条件怎么写。"""
        if pipeline.schema.get(table) is None:
            return f"表 `{table}` 不存在。"
        if pipeline.schema.get(table).column(column) is None:
            return f"表 `{table}` 没有字段 `{column}`。"
        values = pipeline.connector.sample_values(table, column, limit)
        if not values:
            return f"`{table}.{column}` 没有取到样本值（可能是空表或字段全为空）。"
        return f"`{table}.{column}` 的取值样本：\n" + "\n".join(f"- {v}" for v in values)

    @server.tool()
    def search_schema(question: str) -> str:
        """根据问题检索最相关的几张表，并返回它们的结构。这是薄链路里最有用的一步。"""
        retrieved = pipeline.retriever.retrieve(question)
        text = pipeline.schema.render(retrieved.tables)
        note = f"共召回 {len(retrieved.tables)} 张表：{', '.join(retrieved.tables)}"
        if retrieved.expanded:
            note += f"\n其中由图扩展补充：{', '.join(retrieved.expanded)}"
        return f"{note}\n\n{text}"

    @server.tool()
    def validate_sql(sql: str) -> str:
        """执行之前先检查语句：是不是只读、有没有引用不存在的表。"""
        from ..execution import validate

        result = validate(sql, pipeline.schema)
        return "校验通过，可以执行。" if result.ok else f"校验未通过：{result.reason}"

    @server.tool()
    def run_sql(sql: str) -> str:
        """在只读沙箱里执行一条查询。只允许查询，超时与行数都有上限。"""
        result = pipeline.connector.execute(sql)
        if result.ok:
            return _render(result) + f"\n\n耗时 {result.elapsed_ms:.0f} 毫秒"
        return f"执行失败：{result.error}"

    @server.tool()
    def ask_data(question: str) -> str:
        """一步到位：理解问题、检索表、生成语句、校验、执行、必要时重写。"""
        answer = pipeline.ask(question)
        if answer.ok and answer.result is not None:
            body = _render(answer.result)
            return (
                f"{body}\n\n"
                f"使用的语句：\n```sql\n{answer.sql}\n```\n"
                f"检索到的表：{', '.join(answer.tables)}\n"
                f"尝试次数：{answer.attempts}"
            )
        return f"没能回答这个问题：{answer.error or '未知错误'}\n最后一次生成的语句：\n```sql\n{answer.sql}\n```"

    return server
