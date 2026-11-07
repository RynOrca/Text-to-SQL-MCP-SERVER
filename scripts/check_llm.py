"""检查模型接口是否畅通。

这是接上密钥后的第一件事：先确认"能不能聊上一句"，再去跑 42 道题。
否则评测跑完发现全是失败，你无法判断是模型不行还是配置错了。

运行：python scripts/check_llm.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlcopilot.core import load_config, mask_secret  # noqa: E402
from sqlcopilot.core.dotenv import find_dotenv  # noqa: E402
from sqlcopilot.generation import build_llm  # noqa: E402

HINTS = """
排查顺序：
1. .env 文件是不是在项目根目录（和 README.md 同级）？
2. OPENAI_BASE_URL 结尾通常要带 /v1，填错会报 404。
3. 模型名是否属于这个服务商？名字写错通常报 400 或 model not found。
4. 密钥是否过期或余额不足？通常报 401 / 402 / 429。
5. 公司网络是否需要代理？连不上会报 "连不上模型接口"。
"""


def main() -> int:
    cfg = load_config(ROOT / "config" / "default.yaml")
    dotenv_path = find_dotenv()
    effective_provider = os.environ.get("LLM_PROVIDER") or cfg.get("llm.provider", "mock")

    print("=" * 56)
    print(f".env 文件：{dotenv_path or '（没找到，用的是系统环境变量）'}")
    print(f"后端类型：{effective_provider}")
    print(f"接口地址：{os.environ.get('OPENAI_BASE_URL') or '（未设置）'}")
    print(f"模型名称：{os.environ.get('OPENAI_MODEL') or '（未设置）'}")
    print(f"密钥：{mask_secret(os.environ.get('OPENAI_API_KEY'))}")
    print("=" * 56)

    try:
        llm = build_llm(cfg)
    except RuntimeError as exc:
        print(f"[配置错误] {exc}")
        print(HINTS)
        return 1

    if getattr(llm, "name", "") == "mock":
        print("[提示] 当前是离线模拟后端，它只会返回占位语句，不会真的调用模型。")
        print("       如果要接真实模型，请在 .env 里加一行 LLM_PROVIDER=openai_compatible")
        return 0

    print("正在发送一个最小请求……")
    started = time.perf_counter()
    try:
        text = llm.complete("你是一个简洁的助手。", "只回复两个字：可用")
    except RuntimeError as exc:
        print(f"[调用失败] {exc}")
        print(HINTS)
        return 1

    elapsed = (time.perf_counter() - started) * 1000
    print(f"[成功] 耗时 {elapsed:.0f} 毫秒")
    print(f"模型回复：{text.strip()[:200]}")
    print("\n下一步：python scripts/run_eval.py --limit 5   （先跑 5 题看看）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
