"""
Test gia hạn bảng RAW_* sắp hết hạn (renew_tables.py). Dùng BigQuery client giả — không gọi GCP.

Chạy: python -m pytest tests/ -v
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from google.api_core.exceptions import NotFound

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import renew_tables as rt  # noqa: E402

DS = "proj.ds"
NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


class FakeBQ:
    """Giả lập các bảng: {tên đầy đủ: (số dòng, ngày hết hạn)} + ghi lại thứ tự thao tác."""

    def __init__(self, tables):
        self.tables = dict(tables)
        self.calls = []

    def list_tables(self, dataset):
        return [mock.Mock(table_id=n.split(".")[-1]) for n in self.tables]

    def get_table(self, table_id):
        if table_id not in self.tables:
            raise NotFound(table_id)
        rows, expires = self.tables[table_id]
        return mock.Mock(num_rows=rows, expires=expires)

    def copy_table(self, src, dst, job_config=None):
        self.calls.append(("copy", src, dst))
        rows, _ = self.tables[src]
        self.tables[dst] = (rows, NOW + timedelta(days=60))
        return mock.Mock()

    def delete_table(self, table_id, not_found_ok=False):
        self.calls.append(("delete", table_id))
        self.tables.pop(table_id, None)


def soon(days=10):
    return NOW + timedelta(days=days)


class TestRenew(unittest.TestCase):
    def test_renews_only_expiring_raw_tables(self):
        bq = FakeBQ({
            f"{DS}.RAW_WEB_USERS": (1, soon(10)),
            f"{DS}.RAW_1OFFICE_PROFILE": (3, soon(55)),   # còn lâu mới hết hạn
            f"{DS}._t": (0, soon(5)),                      # bảng không phải RAW_
        })
        renewed = rt.renew_expiring_tables(bq, DS, 30, now=NOW, log=lambda m: None)
        self.assertEqual(renewed, ["RAW_WEB_USERS"])
        self.assertEqual(bq.tables[f"{DS}.RAW_WEB_USERS"], (1, NOW + timedelta(days=60)))
        self.assertNotIn(f"{DS}.RAW_WEB_USERS{rt.TEMP_SUFFIX}", bq.tables)

    def test_copy_before_delete_so_data_never_only_in_memory(self):
        bq = FakeBQ({f"{DS}.RAW_X": (5, soon(3))})
        rt.renew_expiring_tables(bq, DS, 30, now=NOW, log=lambda m: None)
        t, x = f"{DS}.RAW_X{rt.TEMP_SUFFIX}", f"{DS}.RAW_X"
        self.assertEqual(bq.calls, [("copy", x, t), ("delete", x), ("copy", t, x), ("delete", t)])

    def test_row_count_mismatch_keeps_original(self):
        bq = FakeBQ({f"{DS}.RAW_X": (5, soon(3))})
        real_copy = bq.copy_table

        def bad_copy(src, dst, job_config=None):
            real_copy(src, dst, job_config)
            bq.tables[dst] = (4, bq.tables[dst][1])  # bản sao thiếu dòng
            return mock.Mock()

        bq.copy_table = bad_copy
        renewed = rt.renew_expiring_tables(bq, DS, 30, now=NOW, log=lambda m: None)
        self.assertEqual(renewed, [])
        self.assertEqual(bq.tables[f"{DS}.RAW_X"][0], 5)          # bảng gốc còn nguyên
        self.assertNotIn(("delete", f"{DS}.RAW_X"), bq.calls)

    def test_recovers_table_lost_mid_run(self):
        # Lần trước đã xoá bảng gốc nhưng chưa kịp chép ngược lại
        bq = FakeBQ({f"{DS}.RAW_X{rt.TEMP_SUFFIX}": (7, soon(60))})
        rt.renew_expiring_tables(bq, DS, 30, now=NOW, log=lambda m: None)
        self.assertEqual(bq.tables[f"{DS}.RAW_X"][0], 7)
        self.assertNotIn(f"{DS}.RAW_X{rt.TEMP_SUFFIX}", bq.tables)

    def test_drops_stale_temp_when_original_exists(self):
        bq = FakeBQ({f"{DS}.RAW_X": (5, soon(50)), f"{DS}.RAW_X{rt.TEMP_SUFFIX}": (5, soon(60))})
        rt.renew_expiring_tables(bq, DS, 30, now=NOW, log=lambda m: None)
        self.assertEqual(set(bq.tables), {f"{DS}.RAW_X"})


if __name__ == "__main__":
    unittest.main()
