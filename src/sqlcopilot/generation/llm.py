"""大模型接入。

只做两件事：把提示词发出去，把文本收回来。

第一版提供两个后端：
- `mock`：返回一句固定内容，用于离线跑通链路、写单元测试
- `openai_compatible`：任何兼容 OpenAI 接口的服务都能用（含本地部署的模型）

用标准库 urllib 发请求，不装任何第三方客户端——少一个依赖，就少一类环境问题。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from ..core.config import Config

_MOCK_ANSWER = "SELECT 1 AS placeholder"

# 上下文超限必须是**独立的一类错误**，不能混进普通的调用失败。
# 原因：评测里"模型写错了"和"模型根本没看到完整上下文"是两件事，
# 混在一起会把服务端的容量限制记到项目头上，做出错误的优化决策。
_CONTEXT_LIMIT_HINTS = (
    "context",
    "too long",
    "max_token",
    "maximum context",
    "single_question_limit",
    "prompt is too",
    "length limit",
)


class ModelLimitError(RuntimeError):
    """上下文超限。属于部署容量问题，不是提示词或链路的缺陷。"""


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数。

    只在服务端没返回 usage 时兜底。中文按 1.4 字/token、ASCII 按 4 字符/token，
    两者都往大了估一点，避免成本报告偏乐观。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return int(cjk / 1.4 + other / 4 + 0.5)


@dataclass
class LlmUsage:
    """一次或多次调用的成本账本。

    为什么单独建一个类：token 数原来在 `_post` 里解完 JSON 就丢了，
    导致"检索到底省了多少 token"这个问题在项目里无法回答。
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    calls: int = 0
    retries: int = 0
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, prompt: int, completion: int, cached: int = 0) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.cached_tokens += cached

    def to_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cached_tokens": self.cached_tokens,
            "calls": self.calls,
            "retries": self.retries,
            "estimated": self.estimated,
        }


class MockLlm:
    """离线占位模型。它的存在是为了让整条链路可以在没有密钥的机器上跑测试。"""

    name = "mock"

    def __init__(self, answer: str = _MOCK_ANSWER) -> None:
        self._answer = answer
        self.calls = 0
        self.retries = 0
        self.usage = LlmUsage(estimated=True)

    def complete(self, system: str, user: str) -> str:
        self.calls += 1
        self.usage.calls += 1
        self.usage.estimated = True
        self.usage.add(estimate_tokens(system + user), estimate_tokens(self._answer))
        return self._answer


class OpenAICompatibleLlm:
    name = "openai_compatible"

    # 这些状态码是"再试一次可能就好了"的，和 401（密钥错）、404（地址错）不同
    RETRYABLE_STATUS = {429, 500, 502, 503, 504}

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.0,
        timeout: float = 60.0,
        max_retries: int = 3,
        retry_base_s: float = 1.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_base_s = retry_base_s
        self.calls = 0
        self.retries = 0
        self.usage = LlmUsage()

    def complete(self, system: str, user: str) -> str:
        """带指数退避的重试。

        为什么要重试：跑一次 200 题的评测就是 200 次调用，服务端偶发 503 的概率不低。
        没有重试的话，一次抖动就废掉整轮实验（本项目第一次跑消融就是这么挂的）。
        只重试"可能自愈"的错误，配置类错误立刻抛出，避免把 401 重试三遍还浪费一分钟。
        """
        last_error: RuntimeError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._post(system, user)
            except _Retryable as exc:
                last_error = RuntimeError(str(exc))
                if attempt >= self.max_retries:
                    break
                self.retries += 1
                self.usage.retries += 1
                time.sleep(self.retry_base_s * (2**attempt))
        raise last_error or RuntimeError("模型调用失败")

    def _post(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        self.calls += 1
        self.usage.calls += 1
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")[:300]
            message = f"模型接口返回 {exc.code}：{detail}"
            lowered = detail.lower()
            if any(hint in lowered for hint in _CONTEXT_LIMIT_HINTS):
                raise ModelLimitError(message) from exc
            if exc.code in self.RETRYABLE_STATUS:
                raise _Retryable(message) from exc
            raise RuntimeError(message) from exc
        except urllib.error.URLError as exc:
            raise _Retryable(f"连不上模型接口：{exc.reason}") from exc
        except json.JSONDecodeError as exc:
            # 响应读了一半就断了，JSON 不完整
            raise _Retryable(f"模型返回的不是完整 JSON（响应可能被截断）：{exc}") from exc
        except (TimeoutError, ConnectionError, OSError) as exc:
            # 连接建立后读取响应时超时。这类错误不会走 URLError 分支，
            # 第一次跑消融就是死在这里（read operation timed out）。
            raise _Retryable(f"读取响应失败（超时或连接中断）：{exc}") from exc

        try:
            content = body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            raise RuntimeError(f"模型返回格式不符合预期：{str(body)[:300]}") from exc

        self._record_usage(body.get("usage") or {}, system, user, content)
        return content

    def _record_usage(self, usage: dict, system: str, user: str, content: str) -> None:
        """把服务端返回的 usage 记账；服务端没给就按字符估算并打标。"""
        prompt = usage.get("prompt_tokens")
        completion = usage.get("completion_tokens")
        if isinstance(prompt, int) and isinstance(completion, int):
            cached = 0
            details = usage.get("prompt_tokens_details") or usage.get("cached_tokens")
            if isinstance(details, dict):
                cached = int(details.get("cached_tokens") or 0)
            elif isinstance(details, int):
                cached = details
            self.usage.add(prompt, completion, cached)
            return
        self.usage.estimated = True
        self.usage.add(estimate_tokens(system + user), estimate_tokens(content))


class _Retryable(Exception):
    """标记"可以再试一次"的失败。"""


def build_llm(cfg: Config) -> object:
    # 允许用环境变量临时覆盖配置文件，方便在同一份配置下切换后端做对比实验
    provider = str(os.environ.get("LLM_PROVIDER") or cfg.get("llm.provider", "mock")).lower()

    if provider == "mock":
        return MockLlm()

    if provider in {"openai_compatible", "openai"}:
        base_url = (
            os.environ.get("OPENAI_BASE_URL")
            or cfg.get("llm.base_url")
            or "https://api.openai.com/v1"
        )
        env_name = cfg.get("llm.api_key_env", "OPENAI_API_KEY")
        api_key = os.environ.get(env_name, "")
        model = os.environ.get("OPENAI_MODEL") or cfg.get("llm.model") or ""
        if not api_key:
            raise RuntimeError(
                f"没有找到密钥。请在项目根目录建立 .env（可从 .env.example 复制），"
                f"填入 {env_name}=你的密钥；或把 llm.provider 改回 mock。"
            )
        if not model:
            raise RuntimeError(
                "没有指定模型名。请在 .env 里填 OPENAI_MODEL，"
                "或设置 config 里的 llm.model。"
            )
        temperature = float(
            os.environ.get("OPENAI_TEMPERATURE") or cfg.get("llm.temperature", 0.0)
        )
        return OpenAICompatibleLlm(
            base_url=base_url,
            api_key=api_key,
            model=model,
            temperature=temperature,
            # 本地推理的单题耗时可能在 15~60 秒，默认 60 秒会把"慢"误判成"挂"。
            # 留成环境变量，评测和线上可以各自设合适的值。
            timeout=float(os.environ.get("LLM_TIMEOUT") or cfg.get("llm.timeout_s", 60.0)),
            max_retries=int(
                os.environ.get("LLM_MAX_RETRIES") or cfg.get("llm.max_retries", 3)
            ),
        )

    raise ValueError(f"不认识的模型后端：{provider}")
