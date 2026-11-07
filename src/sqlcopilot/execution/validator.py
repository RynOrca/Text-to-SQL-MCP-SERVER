"""语句校验：在执行之前先挡一次。

为什么执行层已经有沙箱了，还要在应用层再校验一遍？

因为两者的目的不同：
- 沙箱的目的是**安全**，它只需要回答"允许不允许"；
- 校验的目的是**可解释**，它要回答"哪里错了、该怎么改"，这样才能把话喂给模型重写。

沙箱只会说"被拒绝了"，模型看了也不知道怎么改；校验能说"你用了不存在的字段 pay_money"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..schema.models import Schema

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|truncate|attach|detach|pragma|vacuum)\b",
    re.IGNORECASE,
)
_LEADING = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_TABLE_REF = re.compile(
    r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
    re.IGNORECASE,
)


@dataclass
class ValidationResult:
    ok: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.ok


def validate(sql: str, schema: Schema) -> ValidationResult:
    statement = sql.strip()
    if not statement:
        return ValidationResult(False, "语句为空")

    if ";" in statement.rstrip(";"):
        return ValidationResult(False, "一次只能执行一条语句")

    if not _LEADING.match(statement):
        return ValidationResult(False, "语句必须以 SELECT 或 WITH 开头")

    forbidden = _FORBIDDEN.search(statement)
    if forbidden:
        return ValidationResult(False, f"不允许出现 {forbidden.group(0).upper()} 这类写操作")

    unknown = _unknown_tables(statement, schema)
    if unknown:
        return ValidationResult(
            False,
            f"引用了不存在的表：{', '.join(sorted(set(unknown)))}。"
            f"可用的表有：{', '.join(schema.names())}",
        )

    return ValidationResult(True)


_CTE = re.compile(r"([a-zA-Z_][a-zA-Z0-9_]*)\s+as\s*\(", re.IGNORECASE)


def _unknown_tables(statement: str, schema: Schema) -> list[str]:
    """找出语句里引用了、但库里不存在的表名。

    注意要排除公共表表达式（WITH 定义的临时名字），否则 `WITH t AS (...) SELECT * FROM t`
    会被误判成"引用了不存在的表"。
    """
    valid = {name.lower() for name in schema.names()}
    if statement.lstrip().lower().startswith("with"):
        valid |= {m.group(1).lower() for m in _CTE.finditer(statement)}
    return [n for n in _TABLE_REF.findall(statement) if n.lower() not in valid]
