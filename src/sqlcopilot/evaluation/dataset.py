"""评测数据集。

每一条测试题包含五样东西：
- 问题
- 标准语句（必须能真的跑通，否则评测没有意义）
- 涉及的表（用来算检索召回率，这是区分"检索错"和"生成错"的关键）
- 难度（单表 / 多表 / 嵌套）
- 考察点（便于事后归因）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DIFFICULTIES = ("single", "multi", "nested")


@dataclass
class Case:
    id: str
    question: str
    sql: str
    tables: list[str] = field(default_factory=list)
    difficulty: str = "single"
    note: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "Case":
        return cls(
            id=str(data["id"]),
            question=data["question"],
            sql=data["sql"],
            tables=list(data.get("tables", [])),
            difficulty=data.get("difficulty", "single"),
            note=data.get("note", ""),
        )


def load_dataset(path: str | Path) -> list[Case]:
    file = Path(path)
    cases: list[Case] = []
    with file.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                cases.append(Case.from_dict(json.loads(line)))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{file} 第 {line_no} 行不是合法 JSON：{exc}") from exc
    return cases
