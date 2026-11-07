"""读取 .env 文件。

为什么不用 python-dotenv？因为整个项目的核心链路刻意做到零第三方依赖，
而这件事只有三十行代码。少一个依赖，就少一类"在我机器上跑不起来"的问题。

规则（与业界通行做法一致）：
- 以 # 开头的行是注释
- 支持 KEY=VALUE，也支持 export KEY=VALUE
- 值两端的引号会被去掉
- **已经存在的环境变量不会被覆盖**，所以临时用命令行覆盖 .env 里的值仍然有效
- 密钥只进内存，任何地方都不打印
"""

from __future__ import annotations

import os
from pathlib import Path


def parse_env(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            result[key] = value
    return result


def find_dotenv(start: str | Path | None = None, max_up: int = 3) -> Path | None:
    current = Path(start or Path.cwd()).resolve()
    for _ in range(max_up + 1):
        candidate = current / ".env"
        if candidate.exists():
            return candidate
        if current.parent == current:
            break
        current = current.parent
    return None


def load_dotenv(path: str | Path | None = None, override: bool = False) -> Path | None:
    """把 .env 里的键值对装进环境变量。返回实际读取的文件路径，没找到则返回 None。"""
    file = Path(path) if path else find_dotenv()
    if file is None or not file.exists():
        return None
    for key, value in parse_env(file.read_text(encoding="utf-8")).items():
        if override or key not in os.environ:
            os.environ[key] = value
    return file


def mask_secret(value: str | None) -> str:
    """把密钥变成可以安全打印的形态：只保留长度和首尾各两位。"""
    if not value:
        return "（未设置）"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}（共 {len(value)} 位）"
