import requests
import json
import time
import sys
import os
import argparse
import concurrent.futures
from datetime import datetime, timedelta
from google.cloud import bigquery

# ✅ Đọc credentials từ config.py (không hardcode)
from config import ACCESS_TOKEN, APPROVAL_DETAIL_URL as API_URL, PROJECT_ID, DATASET_ID
from logger import get_logger, log_run_separator, validate_bq_row_count
from notifier import notify_success, notify_failure

TEMP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_approvals_detail.ndjson")
log = get_logger("sync_approval_details")

FULL_TABLE_LIST_ID = f"{PROJECT_ID}.{DATASET_ID}.RAW_1OFFICE_APPROVALS_LIST"
FULL_TABLE_DETAIL_ID = f"{PROJECT_ID}.{DATASET_ID}.RAW_1OFFICE_APPROVAL_DETAILS"

def fetch_detail(approval_id):
    for attempt in range(3):
        try:
            resp = requests.get(
                API_URL,
                params={"access_token": ACCESS_TOKEN, "id": approval_id},
                timeout=10
            )
            if resp.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == "token_not_valid":
                    return approval_id, None
                return approval_id, data
        except Exception:
            time.sleep(1)
    return approval_id, None

def main():
    log_run_separator("sync_approval_details", log)
    parser = argparse.ArgumentParser(description="Sync approval details từ 1Office vào BigQuery")
    parser.add_argument(
        "--days-back", type=int, default=7,
        help="Số ngày nhìn lại (mặc định: 7). VD: --days-back 30"
    )
    args = parser.parse_args()

    from_date = (datetime.now() - timedelta(days=args.days_back)).strftime("%Y-%m-%d")
    to_date   = datetime.now().strftime("%Y-%m-%d")

    try:
        log.info("=== BUOC 1: Ket noi BigQuery ===")
        client = bigquery.Client()

        # Kiểm tra/Tạo bảng nếu chưa có
        schema = [
            bigquery.SchemaField("APPROVAL_ID", "INTEGER", mode="NULLABLE"),
            bigquery.SchemaField("RAW_DATA", "JSON", mode="NULLABLE"),
        ]
        try:
            client.get_table(FULL_TABLE_DETAIL_ID)
        except Exception:
            log.info("Tao bang Detail...")
            table = bigquery.Table(FULL_TABLE_DETAIL_ID, schema=schema)
            client.create_table(table, exists_ok=True)

        log.info(f"=== BUOC 2: Lay danh sach don tu tu {from_date} den {to_date} con thieu ===")
        query_all = f"""
            SELECT DISTINCT CAST(JSON_EXTRACT_SCALAR(val, '$.ID') AS INT64) as approval_id
            FROM `{FULL_TABLE_LIST_ID}`,
            UNNEST(JSON_EXTRACT_ARRAY(RAW_DATA, '$.data')) AS val
            WHERE JSON_EXTRACT_SCALAR(val, '$.ID') IS NOT NULL
              AND SAFE.PARSE_DATE('%d/%m/%Y', JSON_EXTRACT_SCALAR(val, '$.date_created')) >= DATE('{from_date}')
              AND SAFE.PARSE_DATE('%d/%m/%Y', JSON_EXTRACT_SCALAR(val, '$.date_created')) <= DATE('{to_date}')
        """
        all_ids = set()
        try:
            all_ids = {row.approval_id for row in client.query(query_all).result()}
        except Exception as e:
            log.warning(f"Khong the lay danh sach don tu: {e}")

        log.info(f"  -> Tim thay {len(all_ids)} don tu trong {args.days_back} ngay gan nhat")

        # BỎ LOGIC TRỪ ĐI synced_ids:
        # Luôn luôn tải lại toàn bộ đơn trong khoảng thời gian (VD: 7 ngày)
        # để cập nhật các đơn vừa chuyển trạng thái (VD: Chờ duyệt -> Đã duyệt).
        # dbt (stg_approval_details) sẽ tự động dedup lấy bản ghi mới nhất theo SYNC_TIME.
        missing = list(all_ids)
        log.info(f"  -> Se tai moi/cap nhat lai: {len(missing)} don tu")

        if not missing:
            log.info("HOAN TAT! Tat ca don tu da duoc dong bo.")
            notify_success("sync_approval_details", rows=0, extra_info="Không có đơn mới cần tải")
            return

        log.info(f"=== BUOC 3: Tai {len(missing)} chi tiet qua API (10 luong song song) ===")
        results = {}
        done = 0
        total = len(missing)

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            future_map = {executor.submit(fetch_detail, aid): aid for aid in missing}
            for future in concurrent.futures.as_completed(future_map):
                aid, data = future.result()
                if data:
                    results[aid] = data
                done += 1
                if done % 50 == 0 or done == total:
                    log.info(f"  Da tai: {done}/{total} ({len(results)} thanh cong)")

        log.info(f"  -> Tai xong: {len(results)}/{total} don tu")

        if not results:
            log.warning("CANH BAO: Khong co don tu nao tai duoc!")
            notify_success("sync_approval_details", rows=0, extra_info="Không lấy được data chi tiết nào")
            return

        log.info("=== BUOC 4: Doc du lieu cu da co tu BigQuery (de khong bi mat khi xoa-tao lai bang) ===")
        old_rows = []
        try:
            q = f"SELECT APPROVAL_ID, TO_JSON_STRING(RAW_DATA) as raw_data_str FROM `{FULL_TABLE_DETAIL_ID}`"
            for row in client.query(q).result():
                old_rows.append({"APPROVAL_ID": row.APPROVAL_ID, "RAW_DATA": json.loads(row.raw_data_str)})
            log.info(f"  -> Da lay {len(old_rows)} ban ghi cu.")
        except Exception as e:
            log.warning(f"  -> Khong the lay du lieu cu (co the bang chua ton tai): {e}")

        log.info(f"=== BUOC 5: Ghi {len(old_rows)} ban ghi cu + {len(results)} ban ghi moi ra file tam NDJSON ===")
        with open(TEMP_FILE, 'w', encoding='utf-8') as f:
            for r in old_rows:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
            for aid, data in results.items():
                row = {"APPROVAL_ID": aid, "RAW_DATA": data}
                f.write(json.dumps(row, ensure_ascii=False) + '\n')

        file_size_mb = os.path.getsize(TEMP_FILE) / 1024 / 1024
        log.info(f"  -> Ghi xong: {file_size_mb:.1f} MB")

        log.info("=== BUOC 6: Xoa-tao lai bang va Upload (TRUNCATE) ===")
        # ⚠️ Xoa va tao lai bang (thay vi APPEND vao bang co san) de "reset
        # dong ho" whole-table expiration cua BigQuery Sandbox (gioi han cung
        # 60 ngay ke tu luc TAO bang). An toan vi du lieu cu da duoc doc het
        # o BUOC 4 va ghi lai day du cung du lieu moi o tren.
        client.delete_table(FULL_TABLE_DETAIL_ID, not_found_ok=True)
        table = bigquery.Table(FULL_TABLE_DETAIL_ID, schema=schema)
        client.create_table(table, exists_ok=True)

        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            schema=schema,
        )

        with open(TEMP_FILE, "rb") as source_file:
            job = client.load_table_from_file(source_file, FULL_TABLE_DETAIL_ID, job_config=job_config)

        job.result()
        log.info(f"  -> Upload xong! Tong cong {job.output_rows} ban ghi (cu + moi).")

        if os.path.exists(TEMP_FILE):
            os.remove(TEMP_FILE)
            
        log.info("BUOC 6: Validate row count...")
        actual = validate_bq_row_count(client, FULL_TABLE_DETAIL_ID, 10, log)
        
        notify_success("sync_approval_details", rows=actual, extra_info=f"Đã sync mới {len(results)} đơn từ")
        log.info(f"=== HOAN TAT! Da dong bo {len(results)} don tu vao BigQuery ===")

    except Exception as e:
        log.error(f"LOI: {e}", exc_info=True)
        notify_failure("sync_approval_details", error=str(e))
        sys.exit(1)

if __name__ == "__main__":
    main()
