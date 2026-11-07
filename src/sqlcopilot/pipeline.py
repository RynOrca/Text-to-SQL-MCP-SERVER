"""编排层：一次问答的完整生命周期。

    问题
      → 检索（该看哪几张表）
      → 组装上下文（表结构 + 字段取值）
      → 生成（模型写语句）
      → 校验（能不能执行、是不是只读）
      → 执行（只读沙箱）
      → 失败则把错误回喂，重写（最多两轮）

注意这里的顺序：**先校验，再执行**。
校验失败不会浪费一次数据库查询，而且给出的错误信息更具体，模型更容易改对。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .connectors import build_connector
from .core import Config, QueryResult, Tracer
from .execution import validate
from .generation import PromptContext, build_llm, build_prompt, extract_sql
from .retrieval import RetrievalResult, SchemaRetriever, ValueIndex, build_value_index
from .schema import Schema, introspect, load_dictionary


def _tokens(llm) -> tuple[int, int]:
    """读取模型客户端的累计 token 账本。

    用增量差分的方式取，而不是每次重置——这样即使外部复用了同一个客户端，
    也不会把上一题的消耗算到这一题头上。
    """
    usage = getattr(llm, "usage", None)
    if usage is None:
        return (0, 0)
    return (
        int(getattr(usage, "prompt_tokens", 0)),
        int(getattr(usage, "completion_tokens", 0)),
    )


@dataclass
class Answer:
    question: str
    sql: str = ""
    result: QueryResult | None = None
    attempts: int = 0
    first_try_ok: bool = False
    tables: list[str] = field(default_factory=list)
    retrieval: RetrievalResult | None = None
    trace: Tracer = field(default_factory=Tracer)
    error: str = ""
    context_chars: int = 0

    @property
    def ok(self) -> bool:
        return self.result is not None and self.result.ok

    @property
    def prompt_tokens(self) -> int:
        return self.trace.prompt_tokens

    @property
    def completion_tokens(self) -> int:
        return self.trace.completion_tokens

    def render(self) -> str:
        lines = [f"问题：{self.question}", f"语句：{self.sql}"]
        if self.result and self.result.ok:
            lines.append(f"列：{self.result.columns}")
            for row in self.result.rows[:20]:
                lines.append(f"  {row}")
            if self.result.truncated:
                lines.append("（结果已截断）")
        else:
            lines.append(f"失败：{self.error or (self.result.error if self.result else '未知错误')}")
        lines.append(f"尝试次数：{self.attempts}")
        return "\n".join(lines)


class AskPipeline:
    def __init__(
        self,
        cfg: Config,
        db_path: str | Path | None = None,
        llm=None,
        use_values: bool = True,
    ) -> None:
        self.cfg = cfg
        # 兼容老用法：显式传路径就等于覆盖掉配置里的连接串
        if db_path is not None:
            self.db_path = Path(db_path) if not str(db_path).startswith("mysql") else Path(".")
            cfg = cfg.with_overrides(
                {
                    "database": {
                        "dialect": "sqlite",
                        "path": str(db_path),
                    }
                }
            )
            self.cfg = cfg
        else:
            self.db_path = Path(str(cfg.get("database.path", "")))

        self.connector = build_connector(cfg)
        # 字典优先读文件（客户的库是只读的，不能往里建表），库里没有就退化成空字典
        self.schema: Schema = introspect(
            self.connector,
            load_dictionary(self.connector, cfg.get("dictionary.path")),
        )
        # 取值索引是消融实验里的一个开关：关掉它，"华东""已退款"这些词就进不了上下文
        self.values = build_value_index(self.connector, self.schema) if use_values else ValueIndex()
        # 先建模型客户端：大模型重排需要它，所以要在建检索器之前准备好
        self.llm = llm or build_llm(cfg)
        self.retriever = SchemaRetriever(self.schema, cfg, self.values, llm=self.llm)
        self.max_repair = int(cfg.get("generation.max_repair_rounds", 2))

    def ask(self, question: str, retrieval: RetrievalResult | None = None) -> Answer:
        tracer = Tracer()
        with tracer.span("retrieve", question=question):
            retrieved = retrieval or self.retriever.retrieve(question)

        schema_text = self.schema.render(retrieved.tables)
        value_text = self.values.render_for(question, retrieved.tables)

        answer = Answer(question=question, tables=retrieved.tables, retrieval=retrieved, trace=tracer)
        answer.context_chars = len(schema_text) + len(value_text)
        feedback = ""
        zero_row_retried = False

        for attempt in range(1, self.max_repair + 2):
            answer.attempts = attempt
            context = PromptContext(
                schema_text=schema_text,
                value_text=value_text,
                feedback=feedback,
            )
            system, user = build_prompt(question, context, dialect=self.connector.dialect)

            with tracer.span("generate", attempt=attempt) as span:
                before = _tokens(self.llm)
                raw = self.llm.complete(system, user)
                after = _tokens(self.llm)
                tracer.add_tokens(span, after[0] - before[0], after[1] - before[1])

            sql = extract_sql(raw)
            answer.sql = sql

            check = validate(sql, self.schema)
            if not check:
                # 语句为空时把模型原文也带上，否则重写时模型不知道自己错在哪
                feedback = f"语句：{sql or '（没有解析出可执行的语句）'}\n错误：{check.reason}"
                if not sql and raw.strip():
                    feedback += f"\n上一条回复原文：{raw.strip()[:300]}"
                answer.error = check.reason
                continue

            with tracer.span("execute", attempt=attempt):
                result = self.connector.execute(sql)
            answer.result = result

            if result.ok:
                # 语句能跑通，但一行都没返回。这在数据问答里几乎总是"条件里的取值写错了"，
                # 而且**不会报错**，是最容易骗过人的一类失败。所以专门再给一次机会。
                if result.row_count == 0 and not zero_row_retried and attempt <= self.max_repair:
                    zero_row_retried = True
                    answer.error = "查询成功但返回 0 行"
                    feedback = (
                        f"语句：{sql}\n"
                        f"错误：执行成功但返回 0 行。通常是条件里的取值与库中实际存储的不一致，"
                        f"请对照上面的字段取值参考，确认写法后再重写一次。"
                    )
                    continue

                answer.first_try_ok = attempt == 1
                answer.error = ""
                return answer

            answer.error = result.error or "执行失败"
            feedback = f"语句：{sql}\n错误：{answer.error}"

        return answer
