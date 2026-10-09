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
        self.assertEqual(self.mod.REQUEST_TIMEOUT, (10, 120))

    def test_four_attempts_then_raise(self):
        session = mock.Mock()
        session.get.side_effect = requests.exceptions.ReadTimeout("read timed out")
        with self.assertRaises(requests.exceptions.ReadTimeout):
            self.mod.fetch_data_with_retry(session, {"page": 1})
        self.assertEqual(session.get.call_count, 4)
        self.assertEqual(session.get.call_args.kwargs["timeout"], (10, 120))

    def test_success_after_one_timeout(self):
        ok = mock.Mock(status_code=200)
        ok.json.return_value = {"data": [], "total_item": 0}
        ok.raise_for_status.return_value = None
        session = mock.Mock()
        session.get.side_effect = [requests.exceptions.ConnectTimeout("connect"), ok]
        self.assertEqual(self.mod.fetch_data_with_retry(session, {"page": 1}), {"data": [], "total_item": 0})
        self.assertEqual(session.get.call_count, 2)

    def test_worst_case_under_ten_minutes(self):
        connect, read = self.mod.REQUEST_TIMEOUT
        waits = self.mod.fetch_data_with_retry.retry.wait
        lo, hi = self.mod.RETRY_WAIT_MIN, self.mod.RETRY_WAIT_MAX
        max_wait = sum(min(hi, max(lo, 5 * 2 ** (n - 1))) for n in range(1, self.mod.MAX_ATTEMPTS))
        self.assertLess(self.mod.MAX_ATTEMPTS * (connect + read) + max_wait, 10 * 60)
        self.assertIsNotNone(waits)


def page(*dates):
    return {"data": [{"ID": i, "date_created": f"{d} 08:00"} for i, d in enumerate(dates)]}


class TestApprovalsListRecent(unittest.TestCase):
    def setUp(self):
        self.mod = load("sync_approvals_list_bq")
        self.today = datetime(2026, 10, 9).date()

    def run_pages(self, pages, days_back=30):
        with mock.patch.object(self.mod, "fetch_data_with_retry", side_effect=pages) as fetch,                 mock.patch.object(self.mod.time, "sleep"):
            items = self.mod.fetch_recent_items(mock.Mock(), days_back, today=self.today)
        return items, fetch

    def test_newest_first_without_deep_pages(self):
        # Trước đây đơn mới nằm ở trang ~450 (trang sâu hay treo > 120s)
        items, fetch = self.run_pages([page("09/10/2026", "08/10/2026")] + [page()])
        params = fetch.call_args_list[0].args[1]
        self.assertEqual(params["page"], 1)
        self.assertEqual((params["sort_by"], params["sort_type"]), ("date_created", "desc"))
        self.assertEqual(len(items), 2)

    def test_stops_after_page_older_than_cutoff(self):
        pages = [page("09/10/2026", "20/09/2026"), page("15/09/2026", "05/09/2026"), page("01/09/2026")]
        items, fetch = self.run_pages(pages, days_back=30)
        self.assertEqual(fetch.call_count, 2)  # trang 2 đã có đơn trước 09/09 -> dừng
        self.assertEqual(len(items), 4)

    def test_raises_if_api_ignores_sort(self):
        with self.assertRaises(ValueError):
            self.run_pages([page("01/01/2023", "02/01/2023")])

    def test_parse_vn_date(self):
        self.assertEqual(self.mod.parse_vn_date("08/10/2026 11:19"), datetime(2026, 10, 8).date())
        self.assertIsNone(self.mod.parse_vn_date(None))
        self.assertIsNone(self.mod.parse_vn_date("abc"))


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
