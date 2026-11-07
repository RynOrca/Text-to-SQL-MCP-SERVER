"""配置加载。

设计要点：
1. 所有可调参数集中在一个 YAML 文件里，改后端、改阈值都不用动代码。
2. 代码里内置一份默认值，配置文件只写"要覆盖的部分"，
   所以少写一项不会导致程序崩溃（缺省即默认）。
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

from .dotenv import load_dotenv

try:
    import yaml
except ImportError:  # pragma: no cover - 环境没装 PyYAML 时退化为"只读默认值"
    yaml = None


DEFAULTS: dict[str, Any] = {
    "app": {
        "name": "问数",
        "log_level": "INFO",
    },
    "database": {
        "dialect": "sqlite",
        "path": "data/demo_shop.db",
        "dsn": "",
    },
    "dictionary": {
        "path": "data/schema_meta.yaml",
    },
    "executor": {
        "read_only": True,
        "timeout_s": 10.0,
        "max_rows": 500,
    },
    "retriever": {
        "dense_top_k": 20,
        "sparse_top_k": 20,
        "fusion": "rrf",
        "rrf_k": 60,
        "use_dense": True,
        "use_sparse": True,
        "rerank": {"provider": "none", "top_n": 5, "score_margin": 0.0},
        "graph_expand": {"enabled": True, "max_hops": 1, "seed_count": 2},
        "seed_strategy": "rank",
        "max_tables": 0,
    },
    "embedder": {
        "provider": "hash",
        "dimension": 256,
    },
    "llm": {
        "provider": "mock",
        "model": "",
        "temperature": 0.0,
        "base_url": "",
        "api_key_env": "OPENAI_API_KEY",
        "max_retries": 3,
    },
    "generation": {
        "max_repair_rounds": 2,
    },
    "evaluation": {
        "dataset": "data/golden_set.jsonl",
        "report_dir": "reports",
    },
}


# 环境变量 → 配置项的映射。
# 有了它，客户端只要在自己的配置里写 env，就能决定连哪个库、用哪个模型，
# 我们这边一个文件都不用改——这是"换客户只改一行 JSON"的实现方式。
ENV_OVERRIDES = {
    "ASKDATA_DIALECT": "database.dialect",
    "ASKDATA_DB": "database.path",
    "ASKDATA_DSN": "database.dsn",
    "ASKDATA_DICT": "dictionary.path",
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并：override 里有值的键覆盖 base，没有的保留 base。"""
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class Config:
    """用点号路径读取的配置对象，例如 cfg.get("retriever.dense_top_k")。"""

    def __init__(self, data: dict[str, Any], source: str = "<defaults>") -> None:
        self._data = data
        self.source = source

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        # 先把 .env 装进环境变量，密钥只存在于内存里，不落进配置文件
        load_dotenv()
        path = Path(path) if path else Path("config/default.yaml")
        if not path.exists():
            config = cls(copy.deepcopy(DEFAULTS), source="<defaults>")
        else:
            if yaml is None:
                raise RuntimeError("读取 YAML 配置需要 PyYAML，请先安装：pip install pyyaml")
            with path.open("r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            config = cls(_deep_merge(DEFAULTS, raw), source=str(path))

        # 环境变量覆盖：让整套配置可以通过客户端配置里的 env 传进来，
        # 换一个客户不用改任何文件，只改客户端那段 JSON。
        for env_name, dotted in ENV_OVERRIDES.items():
            value = os.environ.get(env_name)
            if value:
                config.set(dotted, value)
        return config

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        node = self._data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)

    def with_overrides(self, overrides: dict[str, Any]) -> "Config":
        """返回一份被覆盖过的配置副本，原配置不动。消融实验靠它切换开关。"""
        return Config(_deep_merge(self._data, overrides), source=f"{self.source}+overrides")


def load_config(path: str | Path | None = None) -> Config:
    return Config.load(path)
