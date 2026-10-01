import requests
import json
import time
import sys
import os
from datetime import datetime
from google.cloud import bigquery

# ✅ Đọc credentials từ config.py (không hardcode)
from config import ACCESS_TOKEN, PROFILE_URL as API_URL, PROJECT_ID, DATASET_ID
from logger import get_logger, log_run_separator, validate_bq_row_count
from notifier import notify_success, notify_failure

log = get_logger("sync_profile")

FULL_TABLE_ID = f"{PROJECT_ID}.{DATASET_ID}.RAW_1OFFICE_PROFILE"
TEMP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_profiles.ndjson")

def fetch_page(session, page: int, limit: int) -> dict:
    """Lấy 1 trang profiles từ API với retry 3 lần."""
    for attempt in range(5):
        try:
            params = {
                "access_token": ACCESS_TOKEN,
                "limit": limit,
                "page": page
            }
            response = session.get(API_URL, params=params, timeout=(15, 90))
            if response.status_code == 429:
                wait = (attempt + 1) * 3
                log.warning(f"  [429] Loi trang {page}, cho {wait}s...")
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response.json()
        except Exception as e:
            if attempt == 4:
                raise
            wait = (attempt + 1) * 3
            log.warning(f"  [Retry {attempt+1}/5] Loi trang {page}: {e}. Cho {wait}s...")
            time.sleep(wait)

def main():
    log_run_separator("sync_profile", log)
    try:
        log.info("1. Dang lay du lieu tu 1Office API (personnel/profile/gets)...")
        all_profiles = []
        page = 1
        limit = 100
        session = requests.Session()

        while True:
            log.info(f"  Dang tai trang {page}...")
            data = fetch_page(session, page, limit)

            if data.get("error") == True or data.get("code") == "token_not_valid":
                log.error(f"❌ LOI TU API 1OFFICE: {data.get('message', 'Token khong hop le.')}")
                raise ValueError(f"API Error: {data.get('message', 'Invalid Token')}")

            items = data.get("data", [])
            if not items:
                break

            all_profiles.extend(items)

            total_item = data.get("total_item", 0)
            if len(all_profiles) >= total_item:
                break

            page += 1
            time.sleep(0.2)

        log.info(f"-> Da lay thanh cong! Tong cong: {len(all_profiles)} ho so nhan su.")

        if not all_profiles:
            log.warning("Khong co du lieu de dong bo.")
            notify_success("sync_profile", rows=0, extra_info="Không có data nhân sự")
            return

        log.info("2. Dang ket noi toi BigQuery...")
        client = bigquery.Client()

        log.info("3. Xoa va tao lai bang RAW_1OFFICE_PROFILE...")
        schema = [
            bigquery.SchemaField("SYNC_TIME", "TIMESTAMP", mode="NULLABLE", default_value_expression="CURRENT_TIMESTAMP()"),
            bigquery.SchemaField("RAW_DATA", "JSON", mode="NULLABLE"),
        ]
        # ⚠️ Xoa va tao lai (khong chi kiem tra ton tai) de "reset dong ho"
        # whole-table expiration cua BigQuery Sandbox (gioi han cung 60 ngay
        # ke tu luc TAO bang). An toan vi du lieu duoc tai lai toan bo tu API
        # moi lan chay (khong mat du lieu).
        client.delete_table(FULL_TABLE_ID, not_found_ok=True)
        table = bigquery.Table(FULL_TABLE_ID, schema=schema)
        client.create_table(table, exists_ok=True)

        log.info("4. Ghi du lieu vao file tam NDJSON...")
        with open(TEMP_FILE, "w", encoding="utf-8") as f:
            chunk_size = 500
            for i in range(0, len(all_profiles), chunk_size):
                chunk = all_profiles[i:i + chunk_size]
                row = {"RAW_DATA": {"data": chunk}}
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

        log.info("5. Dang load du lieu vao BigQuery (WRITE_TRUNCATE)...")
        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            schema=schema,
        )

        with open(TEMP_FILE, "rb") as source_file:
            job = client.load_table_from_file(source_file, FULL_TABLE_ID, job_config=job_config)

        job.result()
        log.info("✅ THANH CONG! Da day du lieu Ho so nhan su len BigQuery.")

        if os.path.exists(TEMP_FILE):
            os.remove(TEMP_FILE)

        # ✅ Validate
        log.info("6. Validate row count...")
        actual = validate_bq_row_count(client, FULL_TABLE_ID, 1, log)
        notify_success("sync_profile", rows=actual)

    except Exception as e:
        log.error(f"❌ Loi he thong: {e}", exc_info=True)
        notify_failure("sync_profile", error=str(e))
        sys.exit(1)

if __name__ == "__main__":
    main()
