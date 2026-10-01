"""
Test cấu hình sync: timeout/số lần thử của danh sách đơn, ngày bắt đầu mặc định của chấm công.
Thay config/logger/notifier bằng module giả — không gọi 1Office, không cần .env hay key.

Chạy: python -m pytest tests/ -v
"""

import importlib
import os
import sys
import types
import unittest
from datetime import datetime
from unittest import mock

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def fake_deps():
    config = types.ModuleType("config")
    config.ACCESS_TOKEN = "fake"
    config.APPROVAL_LIST_URL = "https://example.invalid/approvals"
    config.TIMEKEEP_URL = "https://example.invalid/timekeep"
    config.PROJECT_ID, config.DATASET_ID = "proj", "ds"
    logger = types.ModuleType("logger")
    logger.get_logger = lambda name: mock.Mock()
    logger.log_run_separator = mock.Mock()
    logger.validate_bq_row_count = mock.Mock()
    notifier = types.ModuleType("notifier")
    notifier.notify_success = mock.Mock()
    notifier.notify_failure = mock.Mock()
    return {"config": config, "logger": logger, "notifier": notifier}


def load(module_name):
    patcher = mock.patch.dict(sys.modules, fake_deps())
    patcher.start()
    sys.modules.pop(module_name, None)
    module = importlib.import_module(module_name)
    patcher.stop()
    sys.modules.pop(module_name, None)
    return module


class TestApprovalsListRetry(unittest.TestCase):
    def setUp(self):
        self.mod = load("sync_approvals_list_bq")
        # Không chờ thật giữa các lần thử
        self.mod.fetch_data_with_retry.retry.sleep = lambda seconds: None

    def test_explicit_timeouts(self):
        self.assertEqual(self.mod.REQUEST_TIMEOUT, (10, 60))

    def test_three_attempts_then_raise(self):
        session = mock.Mock()
        session.get.side_effect = requests.exceptions.ReadTimeout("read timed out")
        with self.assertRaises(requests.exceptions.ReadTimeout):
            self.mod.fetch_data_with_retry(session, {"page": 1})
        self.assertEqual(session.get.call_count, 3)
        self.assertEqual(session.get.call_args.kwargs["timeout"], (10, 60))

    def test_success_after_one_timeout(self):
        ok = mock.Mock(status_code=200)
        ok.json.return_value = {"data": [], "total_item": 0}
        ok.raise_for_status.return_value = None
        session = mock.Mock()
        session.get.side_effect = [requests.exceptions.ConnectTimeout("connect"), ok]
        self.assertEqual(self.mod.fetch_data_with_retry(session, {"page": 1}), {"data": [], "total_item": 0})
        self.assertEqual(session.get.call_count, 2)

    def test_worst_case_under_four_minutes(self):
        connect, read = self.mod.REQUEST_TIMEOUT
        waits = self.mod.fetch_data_with_retry.retry.wait
        max_wait = sum(min(20, max(5, 2 * 2 ** (n - 1))) for n in range(1, self.mod.MAX_ATTEMPTS))
        self.assertLess(self.mod.MAX_ATTEMPTS * (connect + read) + max_wait, 4 * 60)
        self.assertIsNotNone(waits)


class TestTimekeepStartDate(unittest.TestCase):
    def setUp(self):
        self.mod = load("sync_timekeep_bq")

    def test_default_is_three_days_back_vn(self):
        self.assertEqual(self.mod.DEFAULT_DAYS_BACK, 3)
        # 01/10 03:00 UTC = 01/10 10:00 VN → 28/09
        self.assertEqual(self.mod.default_start_date(now_utc=datetime(2026, 10, 1, 3, 0)), "2026-09-28")

    def test_crosses_month_boundary(self):
        # Trước đây chạy ngày 01/10 chỉ quét từ 01/10 → bỏ sót chiều 30/09
        start = self.mod.default_start_date(now_utc=datetime(2026, 10, 1, 3, 0))
        self.assertLess(start, "2026-10-01")
        self.assertIn("2026-09-30", self.mod.get_date_range(start))

    def test_uses_vietnam_date_not_utc(self):
        # 30/09 22:00 UTC = 01/10 05:00 VN (giờ chạy sáng) → hôm nay VN là 01/10
        self.assertEqual(self.mod.default_start_date(now_utc=datetime(2026, 9, 30, 22, 0)), "2026-09-28")

    def test_custom_days_back(self):
        self.assertEqual(self.mod.default_start_date(7, now_utc=datetime(2026, 10, 1, 3, 0)), "2026-09-24")


if __name__ == "__main__":
    unittest.main()
