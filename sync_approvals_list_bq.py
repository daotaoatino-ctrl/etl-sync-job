import requests
import json
import sys
import os
import time
import argparse
from datetime import datetime, timedelta
from google.cloud import bigquery
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, before_sleep_log
import logging

# ✅ Đọc credentials từ config.py (không hardcode)
from config import ACCESS_TOKEN, APPROVAL_LIST_URL as API_URL, PROJECT_ID, DATASET_ID
from logger import get_logger, log_run_separator, validate_bq_row_count
from notifier import notify_success, notify_failure

log = get_logger("sync_approvals_list")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

# Timeout rõ ràng + giới hạn tổng thời gian: 01/10 endpoint này treo, 5 lần × 90s + chờ = 12,4 phút
# chỉ để thất bại. 08/10 cả 3 lần × 60s đều timeout (192s) nên nới read lên 120s và 4 lần thử,
# chờ giữa các lần dài hơn để 1Office kịp hồi. Tệ nhất ~9,5 phút; Bước 5 (sync_realtime_poll)
# vẫn bù đơn mới nếu bước này vẫn thất bại.
REQUEST_TIMEOUT = (10, 120)  # (connect, read) giây
MAX_ATTEMPTS = 4
RETRY_WAIT_MIN, RETRY_WAIT_MAX = 10, 30  # giây


@retry(
    stop=stop_after_attempt(MAX_ATTEMPTS),
    wait=wait_exponential(multiplier=5, min=RETRY_WAIT_MIN, max=RETRY_WAIT_MAX),
    retry=retry_if_exception_type((requests.exceptions.RequestException, ValueError)),
    before_sleep=before_sleep_log(log, logging.WARNING),
    reraise=True
)
def fetch_data_with_retry(session, params):
    response = session.get(API_URL, params=params, headers=DEFAULT_HEADERS, timeout=REQUEST_TIMEOUT)
    if response.status_code == 429:
        log.warning("Rate limit 429. Retrying...")
        raise ValueError("Rate limit exceeded")
    response.raise_for_status()
    data = response.json()
    if data.get("error") == True or data.get("code") == "token_not_valid":
        err_msg = data.get('message', 'Unknown API Error')
        log.error(f"API ERROR: {err_msg}")
        raise ValueError(f"API Error: {err_msg}")
    return data

FULL_TABLE_ID = f"{PROJECT_ID}.{DATASET_ID}.RAW_1OFFICE_APPROVALS_LIST"

def main():
    log_run_separator("sync_approvals_list", log)
    parser = argparse.ArgumentParser()
    parser.add_argument("--days-back", type=int, default=30, help="Số ngày nhìn lại (mặc định: 30 ngày)")
    parser.add_argument("--full", action="store_true", help="Kéo toàn bộ lịch sử từ trang 1 đến hết (WRITE_TRUNCATE)")
    args = parser.parse_args()

    try:
        log.info(f"BUOC 1: Lay don tu tu API (Mode: {'FULL REFRESH' if args.full else 'RECENT SMART SYNC'})...")
        session = requests.Session()
        limit = 100

        # Lấy trang đầu tiên để biết tổng số items và pages
        first_page_data = fetch_data_with_retry(session, {"access_token": ACCESS_TOKEN, "limit": limit, "page": 1})
        total_item = first_page_data.get("total_item", 0)
        total_pages = (total_item + limit - 1) // limit if limit else 1
        log.info(f"  -> Tong so don tu tren 1Office: {total_item:,} ({total_pages} trang)")

        all_items = []

        if args.full:
            # Mode toàn bộ: từ trang 1 đến hết
            all_items.extend(first_page_data.get("data", []))
            start_page = 2
            log.info(f"  -> Dang tai toan bo tu trang 2 den trang {total_pages}...")
            for page in range(start_page, total_pages + 1):
                params = {"access_token": ACCESS_TOKEN, "limit": limit, "page": page}
                data = fetch_data_with_retry(session, params)
                items = data.get("data", [])
                if not items:
                    break
                all_items.extend(items)
                if page % 20 == 0 or page == total_pages:
                    log.info(f"  -> Trang {page}/{total_pages}: da tai {len(all_items):,} items")
                time.sleep(0.2)
            write_disp = bigquery.WriteDisposition.WRITE_TRUNCATE
        else:
            # Mode nhanh hàng ngày (Smart Sync):
            # 1Office sắp xếp đơn tăng dần theo ID (trang 1 là 2023, trang cuối là hôm nay 2026).
            # Trong 30 ngày chỉ có khoảng 1.500 - 2.500 đơn (~25 trang cuối).
            # Tải 25 trang cuối chỉ mất ~20-30 giây thay vì 80+ phút!
            recent_pages_count = max(25, int(args.days_back * 1.5))
            start_page = max(1, total_pages - recent_pages_count + 1)
            log.info(f"  -> [Smart Daily Sync] Dang tai {total_pages - start_page + 1} trang cuoi (trang {start_page} -> {total_pages})...")
            
            for page in range(start_page, total_pages + 1):
                params = {"access_token": ACCESS_TOKEN, "limit": limit, "page": page}
                data = fetch_data_with_retry(session, params)
                items = data.get("data", [])
                if items:
                    all_items.extend(items)
                time.sleep(0.2)
            write_disp = bigquery.WriteDisposition.WRITE_APPEND

        log.info(f"-> Da thu thap duoc {len(all_items):,} don tu can nạp.")

        log.info(f"BUOC 2: Ket noi BigQuery va chuan bi du lieu...")
        client = bigquery.Client()

        # Tạo file tạm NDJSON chứa các chunk
        chunk_size = 1000
        temp_file = "temp_approvals_list.ndjson"
        with open(temp_file, "w", encoding="utf-8") as f:
            for i in range(0, len(all_items), chunk_size):
                chunk = all_items[i : i + chunk_size]
                row = {"RAW_DATA": {"data": chunk, "error": False}}
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

        log.info(f"BUOC 3: Load du lieu vao BigQuery ({FULL_TABLE_ID}) voi disposition={write_disp}...")
        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=write_disp,
            schema=[
                bigquery.SchemaField("SYNC_TIME", "TIMESTAMP", mode="NULLABLE", default_value_expression="CURRENT_TIMESTAMP()"),
                bigquery.SchemaField("RAW_DATA", "JSON", mode="NULLABLE"),
            ],
        )

        with open(temp_file, "rb") as source_file:
            job = client.load_table_from_file(source_file, FULL_TABLE_ID, job_config=job_config)

        job.result()  # Đợi job hoàn thành
        log.info(f"THANH CONG! Da ghi {job.output_rows} chunks vào BigQuery.")

        if os.path.exists(temp_file):
            os.remove(temp_file)
        
        # ✅ Validate
        actual = validate_bq_row_count(client, FULL_TABLE_ID, 10, log)
        notify_success("sync_approvals_list", rows=actual)

    except Exception as e:
        log.error(f"LOI: {e}", exc_info=True)
        notify_failure("sync_approvals_list", error=str(e))
        sys.exit(1)

if __name__ == "__main__":
    main()
