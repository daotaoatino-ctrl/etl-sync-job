"""
renew_tables.py
================
Gia hạn các bảng RAW_* sắp hết hạn trong BigQuery Sandbox.

Dataset đặt "tự xoá bảng sau 60 ngày" và Sandbox KHÔNG cho sửa hạn (lỗi 403 "Billing has not
been enabled"). Bảng nào được job tạo lại mỗi ngày thì hạn tự mới; bảng ít khi bị tạo lại
(RAW_HR_MASTER_DATA nạp tay từ Excel, RAW_WEB_USERS, RAW_EXCEPTION_STATUS_TRACKING,
RAW_1OFFICE_APPROVALS_LIST ghi nối thêm) thì bị xoá đúng ngày hết hạn — 25/09 mất
RAW_HR_MASTER_DATA nên 2 mart nhân sự đứng yên, mọi người vào/nghỉ tháng 10 không hiện.

Cách làm (không cần billing): sao chép bảng sang bảng tạm (bảng mới có hạn mới đủ 60 ngày),
kiểm tra số dòng khớp, xoá bảng gốc, sao chép ngược lại rồi xoá bảng tạm. Luôn còn ít nhất
một bản đầy đủ của dữ liệu; nếu job bị ngắt giữa chừng, lần chạy sau tự khôi phục.

Chạy: python renew_tables.py [--threshold-days 30]
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone

from google.api_core.exceptions import NotFound
from google.cloud import bigquery

TEMP_SUFFIX = "__renew"
DEFAULT_THRESHOLD_DAYS = 30


def _copy(client, src, dst):
    cfg = bigquery.CopyJobConfig(write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE)
    client.copy_table(src, dst, job_config=cfg).result()


def _exists(client, table_id):
    try:
        client.get_table(table_id)
        return True
    except NotFound:
        return False


def renew_table(client, table_id, log=print):
    """Tạo lại bảng với hạn mới. Trả True nếu thành công, False nếu bỏ qua vì lỗi an toàn."""
    tmp_id = table_id + TEMP_SUFFIX
    _copy(client, table_id, tmp_id)
    original, copy = client.get_table(table_id), client.get_table(tmp_id)
    if original.num_rows != copy.num_rows:
        log(f"  ! {table_id}: số dòng bản sao ({copy.num_rows}) khác bản gốc ({original.num_rows}) — bỏ qua, giữ nguyên")
        client.delete_table(tmp_id, not_found_ok=True)
        return False
    client.delete_table(table_id)
    _copy(client, tmp_id, table_id)  # nếu lỗi ở đây: bản đầy đủ vẫn nằm ở bảng tạm, lần sau khôi phục
    client.delete_table(tmp_id, not_found_ok=True)
    return True


def recover_leftovers(client, dataset_id, log=print):
    """Xử lý bảng tạm còn sót do lần chạy trước bị ngắt giữa chừng."""
    for item in list(client.list_tables(dataset_id)):
        if not item.table_id.endswith(TEMP_SUFFIX):
            continue
        tmp_id = f"{dataset_id}.{item.table_id}"
        base_id = tmp_id[: -len(TEMP_SUFFIX)]
        if _exists(client, base_id):
            client.delete_table(tmp_id, not_found_ok=True)  # bảng gốc còn nguyên -> bỏ bản tạm
            log(f"  - đã xoá bảng tạm còn sót {tmp_id}")
        else:
            _copy(client, tmp_id, base_id)  # bảng gốc đã bị xoá nhưng chưa kịp chép lại
            client.delete_table(tmp_id, not_found_ok=True)
            log(f"  - đã KHÔI PHỤC {base_id} từ bảng tạm")


def renew_expiring_tables(client, dataset_id, threshold_days=DEFAULT_THRESHOLD_DAYS, now=None, log=print):
    """Gia hạn mọi bảng RAW_* có hạn trong threshold_days ngày tới. Trả danh sách tên bảng đã gia hạn."""
    now = now or datetime.now(timezone.utc)
    deadline = now + timedelta(days=threshold_days)
    recover_leftovers(client, dataset_id, log)

    renewed = []
    for item in list(client.list_tables(dataset_id)):
        name = item.table_id
        if not name.startswith("RAW_") or name.endswith(TEMP_SUFFIX):
            continue
        table_id = f"{dataset_id}.{name}"
        expires = client.get_table(table_id).expires
        if expires is None or expires > deadline:
            continue
        log(f"  - {name}: hết hạn {expires:%d/%m/%Y} -> gia hạn")
        if renew_table(client, table_id, log):
            renewed.append(name)
    return renewed


def main():
    from config import PROJECT_ID, DATASET_ID
    from notifier import notify_failure

    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold-days", type=int, default=DEFAULT_THRESHOLD_DAYS)
    args = parser.parse_args()
    try:
        client = bigquery.Client(project=PROJECT_ID)
        renewed = renew_expiring_tables(client, f"{PROJECT_ID}.{DATASET_ID}", args.threshold_days)
        print(f"Đã gia hạn {len(renewed)} bảng: {', '.join(renewed) or '(không có bảng nào sắp hết hạn)'}")
    except Exception as e:
        notify_failure("renew_tables", error=str(e))
        raise


if __name__ == "__main__":
    sys.exit(main())
