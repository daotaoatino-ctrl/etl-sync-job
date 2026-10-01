"""
Test check_secrets.py (pre-commit hook chặn secret viết cứng).
Mọi giá trị dưới đây là giả, chỉ để test — file này nằm trong commit nên mỗi dòng
chứa secret giả đều có marker bỏ qua.

Chạy: python -m pytest tests/ -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import check_secrets as cs  # noqa: E402

# Marker được nối chuỗi để chính file test không tự bỏ qua dòng khi chạy scan_line
IGNORE = "secret-scan: " + "ignore"


def kinds(line):
    return [name for name, _ in cs.scan_line(line)]


class TestBlocked(unittest.TestCase):
    def test_app_secret_literal(self):
        self.assertEqual(kinds('LARK_APP_SECRET = os.getenv("LARK_APP_SECRET", "Zx9fakefakefakefake1234")'), ["getenv default"])  # secret-scan: ignore
        self.assertEqual(kinds('payload = {"app_secret": "Zx9fakefakefake12"}'), ["app_secret"])  # secret-scan: ignore
        self.assertEqual(kinds("LARK_APP_SECRET=Zx9fakefakefakefake1234"), ["app_secret"])  # secret-scan: ignore

    def test_password_literal(self):
        self.assertEqual(kinds('cfg = {"password": "hunter2-fake"}'), ["password="])  # secret-scan: ignore
        self.assertEqual(kinds('password = "hunter2-fake"'), ["password="])  # secret-scan: ignore
        self.assertEqual(kinds("POWERBI_PASSWORD=hunter2-fake"), ["password="])  # secret-scan: ignore
        self.assertEqual(kinds("cur.execute(\"CREATE USER U PASSWORD = 'hunter2-fake'\")"), ["password="])  # secret-scan: ignore

    def test_bearer_literal(self):
        self.assertEqual(kinds('h = "Bearer abcdefghijklmnopqrstuvwxyz123456"'), ["Bearer"])  # secret-scan: ignore

    def test_private_key(self):
        self.assertEqual(kinds("-----BEGIN PRIVATE KEY-----"), ["private_key"])  # secret-scan: ignore
        self.assertEqual(kinds('"private_key": "-----BEGIN RSA..."'), ["private_key"])  # secret-scan: ignore

    def test_generic_token(self):
        self.assertEqual(kinds('ACCESS_TOKEN = "abcd1234abcd1234abcd"'), ["secret/token"])  # secret-scan: ignore

    def test_value_is_masked(self):
        (_, shown), = cs.scan_line('password = "hunter2-fake"')  # secret-scan: ignore
        self.assertNotIn("hunter2-fake", shown)  # secret-scan: ignore


class TestAllowed(unittest.TestCase):
    def test_env_references(self):
        for line in (
            'LARK_APP_SECRET = os.getenv("LARK_APP_SECRET")',
            '"password": os.environ["SNOWFLAKE_PASSWORD"],',
            'json={"app_id": LARK_APP_ID, "app_secret": LARK_APP_SECRET}',
            "app_secret: appSecret",
            'headers={"Authorization": f"Bearer {token}"}',
            "LARK_APP_SECRET: ${{ secrets.LARK_APP_SECRET }}",
            "cur.execute(f\"CREATE USER U PASSWORD = '{password_sql}'\")",
        ):
            with self.subTest(line=line):
                self.assertEqual(kinds(line), [])

    def test_placeholders(self):
        for line in (
            "LARK_APP_SECRET=your_lark_app_secret",
            "LARK_APP_SECRET=<your_lark_app_secret>",
            "WEB_ADMIN_PASSWORD=",
            "CRON_SECRET=generate_with_openssl_rand_hex_32",
            'password = "..."',
        ):
            with self.subTest(line=line):
                self.assertEqual(kinds(line), [])

    def test_ignore_marker(self):
        self.assertEqual(kinds(f'password = "hunter2-fake"  # {IGNORE}'), [])  # secret-scan: ignore


class TestRepoIsClean(unittest.TestCase):
    def test_no_secrets_in_tracked_files(self):
        lines = cs.all_tracked_lines()
        hits = [(p, n, name) for p, n, text in lines for name, _ in cs.scan_line(text)]
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
