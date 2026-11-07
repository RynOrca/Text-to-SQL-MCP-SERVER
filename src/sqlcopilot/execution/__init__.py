"""执行层：校验、只读沙箱、结果处理。"""

from .validator import ValidationResult, validate

__all__ = ["ValidationResult", "validate"]
