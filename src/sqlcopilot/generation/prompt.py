"""提示词组装。

提示词不是"随便写一段话"，它有三个明确的职责：

1. **约束输出格式**：只要语句，不要解释、不要代码块，否则解析层要写一堆容错。
2. **约束可用素材**：表名和字段名必须来自给出的结构，禁止臆造。
3. **消除歧义**：告诉它金额字段有原价、优惠、实付三个，日期是文本格式。

第三点最容易被忽略，但它决定了"上个月销售额"这种问题会不会算错。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SYSTEM_PROMPT = """你是一名严谨的数据分析师，负责把用户的中文问题翻译成一条 {dialect} 查询语句。

必须遵守的规则：
1. 只输出一条查询语句，不要输出解释、不要输出 Markdown 代码块标记。
2. 只能使用下面给出的表名和字段名，禁止臆造不存在的字段。
3. 只允许查询，禁止任何修改数据的操作。
4. 日期字段是文本格式，形如 2026-03-01，可以直接用字符串比较。
5. 订单金额有三个口径：total_amount 是原价合计，discount_amount 是优惠金额，pay_amount 是实付金额。问"销售额""成交额"时默认使用 pay_amount。
6. 如果问题里有统计口径上的歧义，优先使用最常见的解释，并在语句里保持口径一致。

输出约定（很重要，请严格遵守）：
7. 结果列只包含回答问题所必需的列。除非问题明确要求唯一标识，不要额外输出 id 这类主键列。
8. 默认使用内连接（JOIN）。只有问题明确要求"包含没有××的"时，才使用左连接。
9. 统计金额时不按订单状态做隐式过滤，除非问题明确要求。
10. 只有在问题要求"最多/最少/前 N"时才加排序和行数限制。
"""


@dataclass
class PromptContext:
    schema_text: str
    value_text: str = ""
    examples: list[tuple[str, str]] | None = None
    feedback: str = ""


def build_user_prompt(question: str, context: PromptContext) -> str:
    blocks = ["【可用的表结构】", context.schema_text.strip()]

    if context.value_text.strip():
        blocks += ["", context.value_text.strip()]

    if context.examples:
        blocks += ["", "【相似问题的正确写法，供参考】"]
        for q, sql in context.examples[:3]:
            blocks += [f"问题：{q}", f"语句：{sql.strip()}"]
            blocks.append("")

    if context.feedback.strip():
        blocks += [
            "",
            "【上一次生成的语句执行失败，请根据错误信息修正】",
            context.feedback.strip(),
        ]

    blocks += ["", "【用户问题】", question.strip(), "", "【只输出一条查询语句】"]
    return "\n".join(blocks)


def build_prompt(question: str, context: PromptContext, dialect: str = "sqlite") -> tuple[str, str]:
    """组装系统提示词与用户提示词。

    方言要传进去：同一个问题在 SQLite 和 PostgreSQL 里写法可能不同
    （日期函数、字符串拼接、LIMIT 语法），写错方言会直接报错。
    """
    return SYSTEM_PROMPT.format(dialect=dialect), build_user_prompt(question, context)


_FENCE = re.compile(r"```(?:sql)?\s*(.+?)```", re.IGNORECASE | re.DOTALL)
_PREFIX = re.compile(r"^\s*(sql|语句|查询)\s*[:：]\s*", re.IGNORECASE)


def extract_sql(text: str) -> str:
    """从模型输出里把语句抠出来。

    模型很爱加代码块、加"这是查询："这种前缀。这一步就是把这些噪音去掉，
    避免下游因为一个反引号就判定失败。
    """
    if not text:
        return ""
    match = _FENCE.search(text)
    if match:
        text = match.group(1)
    text = _PREFIX.sub("", text).strip()
    text = text.strip("`").strip()

    # 只取第一条语句：从第一个分号处截断，但不去掉语句本身
    if ";" in text:
        head = text.split(";", 1)[0].strip()
        if head:
            text = head

    # 抽不出以 SELECT/WITH 开头的语句时，返回空串，**不要把散文当语句**。
    # 这条是评测逼出来的：模型输出了"查询成功，共 3 条记录"时，旧实现会把整段话
    # 塞进 Answer.sql，render() 于是把它印在"语句："后面——对外就变成了"查成功了"。
    # 空串会让校验层报"语句为空"，是可见的失败，而不是伪装的成果。
    if not re.match(r"(?is)^\s*(select|with)\b", text):
        return ""
    return text.strip()


def build_repair_feedback(sql: str, error: str) -> str:
    return f"语句：{sql}\n错误：{error}"
