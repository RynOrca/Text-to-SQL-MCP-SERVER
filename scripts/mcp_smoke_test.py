"""服务冒烟测试：不起传输层，直接调用工具函数。

为什么需要它：真跑一个客户端（Claude Desktop）来验证很麻烦，
但工具本身是不是通的、返回格式对不对，可以在这里一秒验证。

运行：python scripts/mcp_smoke_test.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlcopilot.core import load_config  # noqa: E402
from sqlcopilot.mcp_server import create_server  # noqa: E402
from sqlcopilot.pipeline import AskPipeline  # noqa: E402


def _text(payload) -> str:
    """兼容不同版本返回结构：可能是字符串，也可能是内容块列表。"""
    if isinstance(payload, tuple):
        payload = payload[0]
    if isinstance(payload, str):
        return payload
    if isinstance(payload, list):
        parts = []
        for item in payload:
            parts.append(getattr(item, "text", str(item)))
        return "\n".join(parts)
    return str(payload)


async def main() -> None:
    cfg = load_config(ROOT / "config" / "default.yaml")
    pipeline = AskPipeline(cfg)
    server = create_server(pipeline)

    tools = await server.list_tools()
    print(f"已注册 {len(tools)} 个工具：")
    for tool in tools:
        print(f"  - {tool.name}")
    print()

    cases = [
        ("list_tables", {"keyword": "订单"}),
        ("describe_table", {"table": "orders"}),
        ("sample_values", {"table": "orders", "column": "status"}),
        ("search_schema", {"question": "上个月每个大区的销售额是多少？"}),
        ("validate_sql", {"sql": "DELETE FROM orders"}),
        ("run_sql", {"sql": "SELECT status, COUNT(*) FROM orders GROUP BY status"}),
        ("ask_data", {"question": "一共有多少个客户？"}),
    ]

    ok = 0
    for name, args in cases:
        try:
            result = await server.call_tool(name, args)
            text = _text(result).strip().replace("\n", " ")
            print(f"[OK] {name:<14}{text[:110]}")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {name:<14}{type(exc).__name__}: {exc}")

    print(f"\n{ok}/{len(cases)} 个工具可用")


if __name__ == "__main__":
    asyncio.run(main())
