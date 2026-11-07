"""通宵跑批：消融 R3 + 完整 200 题的薄厚链路对比。

单独做成一个脚本，是为了能被"脱离会话"的方式启动：
用 cmd 链式命令时，引号和编码很容易出问题；用 Python 直接调用子进程更稳。

日志写到 logs/overnight.log，全部结束后生成 logs/overnight.done。

用法：
    python scripts/run_overnight.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "logs" / "overnight.log"
DONE = ROOT / "logs" / "overnight.done"


def log(message: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%H:%M:%S")
    line = f"[{stamp}] {message}"
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    print(line, flush=True)


STEPS = [
    ("消融 R3（大模型重排，200 题）", ["scripts/run_ablation.py", "--only", "R3"]),
    ("薄厚链路对比（完整 200 题）", ["scripts/compare_chains.py"]),
]


def main() -> None:
    DONE.unlink(missing_ok=True)
    log("=" * 50)
    log("通宵任务开始")
    for name, args in STEPS:
        log(f"开始：{name}")
        started = time.time()
        # 用 Popen 逐行读，而不是 run 完之后一次性拿结果——
        # 跑批要能实时看到进度，否则中途卡住也发现不了
        proc = subprocess.Popen(
            [sys.executable, *args],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="ignore",
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                log("  " + line)
        proc.wait()
        elapsed = time.time() - started
        if proc.returncode != 0:
            log(f"失败：{name}（退出码 {proc.returncode}），耗时 {elapsed:.0f} 秒")
            log("中断")
            return
        log(f"完成：{name}，耗时 {elapsed:.0f} 秒")

    DONE.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    log("全部完成")


if __name__ == "__main__":
    main()
