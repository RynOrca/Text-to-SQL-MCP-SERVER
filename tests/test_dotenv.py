"""读取 .env 的解析逻辑测试。纯函数，不碰网络。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlcopilot.core.dotenv import load_dotenv, mask_secret, parse_env  # noqa: E402

SAMPLE = """
# 这是注释
OPENAI_API_KEY=sk-abcdefghijklmn
OPENAI_BASE_URL="https://example.com/v1"
export OPENAI_MODEL='qwen-plus'

没有等号的行会被忽略
EMPTY=
"""


class TestParseEnv(unittest.TestCase):
    def test_parses_plain_pair(self) -> None:
        self.assertEqual(parse_env(SAMPLE)["OPENAI_API_KEY"], "sk-abcdefghijklmn")

    def test_strips_double_quotes(self) -> None:
        self.assertEqual(parse_env(SAMPLE)["OPENAI_BASE_URL"], "https://example.com/v1")

    def test_supports_export_and_single_quotes(self) -> None:
        self.assertEqual(parse_env(SAMPLE)["OPENAI_MODEL"], "qwen-plus")

    def test_skips_comments_and_invalid_lines(self) -> None:
        parsed = parse_env(SAMPLE)
        self.assertNotIn("# 这是注释", parsed)
        self.assertEqual(len(parsed), 4)

    def test_keeps_empty_value(self) -> None:
        self.assertEqual(parse_env(SAMPLE)["EMPTY"], "")


class TestLoadDotenv(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / ".env"
        self.path.write_text("DSH_TEST_KEY=from_file\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()
        os.environ.pop("DSH_TEST_KEY", None)

    def test_loads_into_environment(self) -> None:
        os.environ.pop("DSH_TEST_KEY", None)
        load_dotenv(self.path)
        self.assertEqual(os.environ["DSH_TEST_KEY"], "from_file")

    def test_does_not_override_existing(self) -> None:
        os.environ["DSH_TEST_KEY"] = "from_shell"
        load_dotenv(self.path)
        self.assertEqual(os.environ["DSH_TEST_KEY"], "from_shell")


class TestMaskSecret(unittest.TestCase):
    def test_masks_middle(self) -> None:
        masked = mask_secret("sk-1234567890abcd")
        self.assertNotIn("234567890", masked)
        self.assertIn("sk", masked)

    def test_handles_missing(self) -> None:
        self.assertIn("未设置", mask_secret(None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
