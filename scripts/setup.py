"""一键初始化：选数据库、测连接、生成字典模板、检查模型接口。

用法：

    python scripts/setup.py                 # 交互式，一路问到底
    python scripts/setup.py --check         # 只检查当前配置能不能用
    python scripts/setup.py --dialect sqlite --db data/demo_shop.db --yes
    python scripts/setup.py --dialect mysql --dsn "mysql://user:pass@127.0.0.1:3306/shop" --yes

做完之后：
- 生成/更新 `.env`（连接信息写在这里，不进代码）
- 生成 `data/schema_meta.yaml` 字典模板（填业务含义用）
- 打印下一步该干什么
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlcopilot.connectors import build_connector  # noqa: E402
from sqlcopilot.core import load_config  # noqa: E402
from sqlcopilot.schema import dump_template, load_dictionary  # noqa: E402

DIALECT_NAMES = {
    "1": "sqlite",
    "2": "mysql",
    "3": "postgres",
    "sqlite": "sqlite",
    "mysql": "mysql",
    "mariadb": "mysql",
    "postgres": "postgres",
    "postgresql": "postgres",
}


def ask(prompt: str, default: str = "") -> str:
    suffix = f"（默认 {default}）" if default else ""
    answer = input(f"{prompt}{suffix}：").strip()
    return answer or default


def choose_dialect() -> tuple[str, str]:
    print("请选择数据库类型：")
    print("  1) SQLite（单文件，零依赖，适合试用）")
    print("  2) MySQL / MariaDB（需要 pip install pymysql）")
    print("  3) PostgreSQL（需要 pip install psycopg2-binary）")
    choice = ask("输入序号", "1")
    dialect = DIALECT_NAMES.get(choice.lower(), "sqlite")

    if dialect == "sqlite":
        return dialect, ask("数据库文件路径", "data/demo_shop.db")
    if dialect == "mysql":
        return dialect, ask("连接串", "mysql://root:root@127.0.0.1:3306/shop")
    return dialect, ask("连接串", "postgresql://postgres:postgres@127.0.0.1:5432/shop")


def write_env(dialect: str, target: str) -> Path:
    env_path = ROOT / ".env"
    existing: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                existing[key.strip()] = value.strip()

    existing["ASKDATA_DIALECT"] = dialect
    if dialect == "sqlite":
        existing["ASKDATA_DB"] = target
        existing.pop("ASKDATA_DSN", None)
    else:
        existing["ASKDATA_DSN"] = target
        existing.pop("ASKDATA_DB", None)
    existing.setdefault("ASKDATA_DICT", "data/schema_meta.yaml")

    body = "\n".join(f"{key}={value}" for key, value in existing.items())
    env_path.write_text(body + "\n", encoding="utf-8")
    return env_path


def describe(connector) -> int:
    tables = connector.list_tables()
    print(f"\n连接成功，共 {len(tables)} 张表：")
    for name in tables[:20]:
        columns = connector.columns(name)
        print(f"  - {name}（{len(columns)} 字段）")
    if len(tables) > 20:
        print(f"  …… 还有 {len(tables) - 20} 张")
    return len(tables)


def main() -> int:
    parser = argparse.ArgumentParser(description="问数（AskData）初始化")
    parser.add_argument("--dialect", default=None, help="sqlite / mysql / postgres")
    parser.add_argument("--db", default=None, help="SQLite 文件路径")
    parser.add_argument("--dsn", default=None, help="MySQL / PostgreSQL 连接串")
    parser.add_argument("--check", action="store_true", help="只检查当前配置")
    parser.add_argument("--yes", action="store_true", help="不写 .env，只验证连接")
    args = parser.parse_args()

    if args.check:
        cfg = load_config(ROOT / "config" / "default.yaml")
        print(f"数据库类型：{cfg.get('database.dialect')}")
        connector = build_connector(cfg)
        describe(connector)
        dictionary = load_dictionary(connector, ROOT / cfg.get("dictionary.path", ""))
        if dictionary.is_empty:
            print("\n⚠ 数据字典是空的，模型不知道字段的业务含义，准确率会明显下降。")
            print("  跑一次 python scripts/setup.py 生成模板，再补上说明。")
        else:
            print(f"\n数据字典：{dictionary.source}（{len(dictionary.tables)} 张表有说明）")
        return 0

    if args.dialect:
        dialect = DIALECT_NAMES.get(args.dialect.lower(), "sqlite")
        target = args.db if dialect == "sqlite" else args.dsn
        if not target:
            print("缺少 --db（sqlite）或 --dsn（mysql/postgres）")
            return 1
    else:
        dialect, target = choose_dialect()

    print(f"\n正在连接（{dialect}）……")
    overrides = {"database": {"dialect": dialect, "path": target, "dsn": target}}
    cfg = load_config(ROOT / "config" / "default.yaml").with_overrides(overrides)
    try:
        connector = build_connector(cfg)
        count = describe(connector)
    except Exception as exc:  # noqa: BLE001
        print(f"\n连接失败：{exc}")
        print("\n排查顺序：")
        print("  1. 地址和端口对不对？MySQL 默认 3306，PostgreSQL 默认 5432")
        print("  2. 驱动装了没？MySQL 要 pymysql，PostgreSQL 要 psycopg2-binary")
        print("  3. 账号有没有该库的读权限？")
        return 1

    if count == 0:
        print("\n连上了，但一张表都没有——检查连接串里的库名对不对。")
        return 1

    if not args.yes:
        env_path = write_env(dialect, target)
        print(f"\n已写入 {env_path.name}")

    dict_path = ROOT / "data" / "schema_meta.yaml"
    if args.yes:
        print("\n（--yes：只验证连接，不写任何文件）")
    elif not dict_path.exists():
        dump_template(connector, dict_path)
        print(f"已生成字典模板：{dict_path.relative_to(ROOT)}")
        print("  下一步：打开它，把每张表和每个字段的中文含义补上。")
    else:
        print(f"字典已存在，未覆盖：{dict_path.relative_to(ROOT)}")

    print("\n接下来：")
    print("  1. python scripts/setup.py --check       确认配置可用")
    print("  2. python scripts/ask.py \"你的问题\"      试一个问题")
    print("  3. python scripts/serve_mcp.py           启动 MCP 服务给 Agent 用")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
