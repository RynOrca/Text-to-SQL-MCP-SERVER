"""评测指标。

核心是"执行准确率"：把生成语句和标准语句都跑一遍，看结果集是否一致。

为什么不用字符串比对？同一个结果有无穷多种写法：连接顺序可以换、表可以起别名、
子查询可以改写成连接。字符串比对会把这些正确写法统统判错，指标立刻失真，
优化方向也会跟着走偏。

比对时必须处理三件事：
1. 行顺序不同——排序后再比
2. 浮点误差——四舍五入后再比（求和的浮点累加会产生 1e-12 级别的偏差）
3. 列数不同——列数不一样说明问的不是同一件事，直接判错
"""

from __future__ import annotations

import itertools
from typing import Any, Sequence


def _normalize_value(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return value


def normalize_rows(rows: Sequence[Sequence[Any]]) -> list[tuple[Any, ...]]:
    normalized = [tuple(_normalize_value(v) for v in row) for row in rows]
    return sorted(normalized, key=lambda row: tuple(str(v) for v in row))


def _as_multiset(rows: Sequence[Sequence[Any]]) -> list[tuple[Any, ...]]:
    return normalize_rows(rows)


def execution_match(
    pred_columns: Sequence[str],
    pred_rows: Sequence[Sequence[Any]],
    gold_columns: Sequence[str],
    gold_rows: Sequence[Sequence[Any]],
) -> bool:
    """严格口径：列数一致 + 行多重集一致。"""
    if len(pred_columns) != len(gold_columns):
        return False
    if len(pred_rows) != len(gold_rows):
        return False
    return normalize_rows(pred_rows) == normalize_rows(gold_rows)


def subset_match(
    pred_columns: Sequence[str],
    pred_rows: Sequence[Sequence[Any]],
    gold_columns: Sequence[str],
    gold_rows: Sequence[Sequence[Any]],
) -> bool:
    """数据一致口径：允许预测多输出列，只要能找到一个"列子集"与标准答案等价。

    为什么需要它：14 道错题里 8 道是"多输出一列 id / 计数"，数值其实全对。
    严格口径把这类判错，会让人以为模型算错了，优化方向直接跑偏。
    反过来，只报宽松口径又会掩盖"答非所问"。两个口径必须并列报告。

    实现上不依赖列名（标准语句的列名常是 `COUNT(*)` 这类表达式，按名字对不上），
    而是**暴力枚举列子集**：列数很少（≤8），组合数完全可控。
    """
    if len(pred_columns) < len(gold_columns):
        return False
    if len(pred_rows) != len(gold_rows):
        return False
    target = _as_multiset(gold_rows)
    for indices in itertools.combinations(range(len(pred_columns)), len(gold_columns)):
        projected = [[row[i] for i in indices] for row in pred_rows]
        if _as_multiset(projected) == target:
            return True
    return False


def extra_columns(
    pred_columns: Sequence[str], gold_columns: Sequence[str]
) -> int:
    """多输出了几列。负数表示少输出。"""
    return len(pred_columns) - len(gold_columns)


def percentile(values: Sequence[float], q: float) -> float:
    """线性插值分位数。q 取 0~100。

    原来只报平均值，长尾（模型偶发 60s 超时）会被均值抹平，
    报 P95 才能看出"用户每 20 次会卡一次"。
    """
    data = sorted(float(v) for v in values)
    if not data:
        return 0.0
    if len(data) == 1:
        return data[0]
    position = (len(data) - 1) * (q / 100.0)
    lower = int(position)
    upper = min(lower + 1, len(data) - 1)
    weight = position - lower
    return round(data[lower] * (1 - weight) + data[upper] * weight, 3)


def percentiles(values: Sequence[float]) -> dict[str, float]:
    return {
        "p50": percentile(values, 50),
        "p90": percentile(values, 90),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "mean": round(sum(values) / len(values), 3) if values else 0.0,
    }


def ratio(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0
