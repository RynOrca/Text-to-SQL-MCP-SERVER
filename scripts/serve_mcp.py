"""启动模型上下文协议服务（标准输入输出方式）。

用法：
    python scripts/serve_mcp.py

客户端配置示例（Claude Desktop 的 claude_desktop_config.json）：

    {
      "mcpServers": {
        "askdata": {
          "command": "python",
          "args": ["E:/code/Working-on-it/项目/scripts/serve_mcp.py"]
        }
      }
    }

配置好之后重启客户端，就能在对话里直接让模型查库了。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlcopilot.core import load_config  # noqa: E402
from sqlcopilot.mcp_server import create_server  # noqa: E402
from sqlcopilot.pipeline import AskPipeline  # noqa: E402


def main() -> None:
    cfg = load_config(ROOT / "config" / "default.yaml")
    db_path = ROOT / cfg.get("database.path")
    if not db_path.exists():
        raise SystemExit(f"数据库不存在：{db_path}。请先运行 python scripts/init_db.py")

    # 注意：标准输入输出是协议通道，任何调试打印都会污染它，所以这里一句 print 都不能有
    pipeline = AskPipeline(cfg)
    server = create_server(pipeline)
    server.run()


if __name__ == "__main__":
    main()
