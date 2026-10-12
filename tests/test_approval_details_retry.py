"""
Test tải chi tiết đơn có thử lại các đơn lỗi mạng (sync_approval_details_bq.py).
Dùng module giả thay config/logger/notifier — không gọi 1Office.

Chạy: python -m pytest tests/ -v
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_sync_settings import load  # noqa: E402


class TestFetchMissingDetails(unittest.TestCase):
    def setUp(self):
        self.mod = load("sync_approval_details_bq")
        patcher = mock.patch.object(self.mod.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_retries_failed_ids_with_slower_settings(self):
        calls = []

        def fetch(aid, timeout=10):
            calls.append((aid, timeout))
            # đơn 2 lỗi ở vòng đầu, thành công ở vòng thử lại
            if aid == 2 and timeout == 10:
                return aid, None
            return aid, {"data": {"ID": aid}}

        results, failed = self.mod.fetch_missing_details([1, 2, 3], fetch=fetch)
        self.assertEqual(sorted(results), [1, 2, 3])
        self.assertEqual(failed, [])
        self.assertIn((2, 30), calls)          # vòng thử lại dùng timeout dài hơn
        self.assertNotIn((1, 30), calls)       # đơn đã thành công không bị tải lại

    def test_reports_ids_that_still_fail(self):
        fetch = lambda aid, timeout=10: (aid, None if aid == 5 else {"ok": 1})
        results, failed = self.mod.fetch_missing_details([4, 5], fetch=fetch)
        self.assertEqual(sorted(results), [4])
        self.assertEqual(failed, [5])

    def test_no_retry_when_all_succeed(self):
        fetch = mock.Mock(side_effect=lambda aid, timeout=10: (aid, {"ok": 1}))
        self.mod.fetch_missing_details([1, 2], fetch=fetch)
        self.assertEqual(fetch.call_count, 2)


if __name__ == "__main__":
    unittest.main()
