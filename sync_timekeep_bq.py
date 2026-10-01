import requests
import json
import argparse
import time
import os
import sys
from datetime import datetime, timedelta, date
from google.cloud import bigquery
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# ✅ Đọc credentials từ config.py (không hardcode)
from config import ACCESS_TOKEN, TIMEKEEP_URL as API_GETS_URL, PROJECT_ID, DATASET_ID
from logger import get_logger, log_run_separator, validate_bq_row_count
from notifier import notify_success, notify_failure

log = get_logger("sync_timekeep")

@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=2, max=20),
    retry=retry_if_exception_type((requests.exceptions.RequestException, ValueError)),
    reraise=True
)
def fetch_data_with_retry(params):
    response = requests.get(API_GETS_URL, params=params, timeout=(15, 90))
    if response.status_code == 429:
        log.warning("Rate limit 429. Retrying...")
        raise ValueError("Rate limit exceeded")
    response.raise_for_status()
    data = response.json()
    if data.get("error") == True or data.get("code") == "token_not_valid":
        log.error(f"  [LOI API] {data.get('message')}")
        raise ValueError(f"API Error: {data.get('message')}")
    return data

log = get_logger("sync_timekeep")
FULL_TABLE_ID = f"{PROJECT_ID}.{DATASET_ID}.RAW_1OFFICE_TIMEKEEP_LIST"
TEMP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_timekeep.ndjson")

# Mặc định quét lại N ngày gần nhất (giờ VN) thay vì từ ngày 1 đầu tháng: nhanh hơn cuối
# tháng và không bỏ sót những ngày cuối tháng trước khi chạy vào đầu tháng (vd 01/10
# trước đây không quét lại chiều 30/09). Ngày cũ hơn giữ nguyên bản đã có trong BigQuery
# (BUOC 3) — cần quét lại cả tháng thì chạy với --start-date YYYY-MM-01.
DEFAULT_DAYS_BACK = 3
VN_TZ_OFFSET = timedelta(hours=7)


def default_start_date(days_back: int = DEFAULT_DAYS_BACK, now_utc: datetime = None) -> str:
    """Ngày bắt đầu mặc định = hôm nay theo giờ VN trừ days_back ngày (YYYY-MM-DD)."""
    now_utc = now_utc or datetime.utcnow()
    today_vn = (now_utc + VN_TZ_OFFSET).date()
    return (today_vn - timedelta(days=days_back)).strftime("%Y-%m-%d")


def get_date_range(start_date_str: str):
    start = datetime.strptime(start_date_str, "%Y-%m-%d")
    end = datetime.now()
    dates = []
    current = start
    while current <= end:
        dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return dates

def main():
    log_run_separator("sync_timekeep", log)
    parser = argparse.ArgumentParser(description="Sync dữ liệu chấm công từ 1Office vào BigQuery")
    parser.add_argument(
        "--start-date",
        default=None,
        help="Ngày bắt đầu YYYY-MM-DD (mặc định: hôm nay giờ VN trừ --days-back ngày)."
    )
    parser.add_argument(
        "--days-back", type=int, default=DEFAULT_DAYS_BACK,
        help=f"Số ngày quét lại khi không truyền --start-date (mặc định {DEFAULT_DAYS_BACK})."
    )
    args = parser.parse_args()
    start_date = args.start_date or default_start_date(args.days_back)

    try:
        log.info(f"=== BUOC 1: Ket noi BigQuery ===")
        client = bigquery.Client()
        
        # Kiểm tra và tạo bảng nếu chưa có
        schema = [
            bigquery.SchemaField("SYNC_TIME", "TIMESTAMP", mode="NULLABLE", default_value_expression="CURRENT_TIMESTAMP()"),
            bigquery.SchemaField("SYNC_DATE", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("RAW_DATA", "JSON", mode="NULLABLE"),
        ]
        try:
            client.get_table(FULL_TABLE_ID)
        except Exception:
            log.info("Tao bang Timekeep...")
            table = bigquery.Table(FULL_TABLE_ID, schema=schema)
            client.create_table(table, exists_ok=True)

        log.info(f"=== BUOC 2: Quet du lieu cham cong tung ngay tu {start_date} ===")
        date_list = get_date_range(start_date)
        total_records = 0

        with open(TEMP_FILE, "w", encoding="utf-8") as f:
            for d in date_list:
                page = 1
                day_count = 0
                while True:
                    params = {
                        "access_token": ACCESS_TOKEN,
                        "date": d,
                        "limit": 200,
                        "page": page
                    }
                    try:
                        data = fetch_data_with_retry(params)
                    except Exception as e:
                        log.error(f"Lỗi khi gọi API sau nhiều lần thử: {e}")
                        raise

                    items = data.get("data", [])
                    if not items:
                        break

                    # Ghi ra NDJSON file thay vi INSERT cham tung dong
                    row = {"SYNC_DATE": d, "RAW_DATA": {"data": items}}
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                    
                    day_count += len(items)
                    total_item = data.get("total_item", 0)
                    # ⚠️ API 1Office luôn giới hạn cứng ~90-100 bản ghi/request
                    # bất kể limit truyền vào — KHÔNG được dùng len(items) < 200
                    # để quyết định dừng, nếu không sẽ luôn dừng ở trang 1.
                    if day_count >= total_item:
                        break
                    page += 1
                    if page > 50:
                        log.warning(f"  Ngay {d}: vuot qua 50 trang (day_count={day_count}/{total_item}), dung de tranh vong lap vo han")
                        break

                if day_count > 0:
                    log.info(f"  Ngay {d}: {day_count} ban ghi")
                total_records += day_count

        log.info(f"=== BUOC 3: Doc du lieu cu (truoc {start_date}) tu BigQuery ===")
        old_rows = []
        try:
            q = f"SELECT SYNC_DATE, TO_JSON_STRING(RAW_DATA) as raw_data_str FROM `{FULL_TABLE_ID}` WHERE SYNC_DATE < '{start_date}'"
            for row in client.query(q).result():
                old_rows.append({"SYNC_DATE": row.SYNC_DATE, "RAW_DATA": json.loads(row.raw_data_str)})
            log.info(f"  -> Da lay {len(old_rows)} ban ghi cu.")
        except Exception as e:
            log.warning(f"  -> Khong the lay du lieu cu (co the bang chua ton tai): {e}")

        log.info("=== BUOC 4: Load toan bo du lieu (Cu + Moi) vao BigQuery (TRUNCATE) ===")
        # ⚠️ Xoa va tao lai bang de "reset dong ho" whole-table expiration cua
        # BigQuery Sandbox (gioi han cung 60 ngay ke tu luc TAO bang, khong
        # phai ke tu lan load gan nhat). An toan vi old_rows da duoc doc het
        # o BUOC 3 va se duoc ghi lai day du ben duoi cung voi du lieu moi.
        client.delete_table(FULL_TABLE_ID, not_found_ok=True)
        table = bigquery.Table(FULL_TABLE_ID, schema=schema)
        client.create_table(table, exists_ok=True)

        # Ghi thêm dữ liệu cũ vào đầu file tạm (chế độ append file cục bộ)
        temp_combined = TEMP_FILE + ".combined"
        with open(temp_combined, "w", encoding="utf-8") as f_out:
            # 1. Ghi dữ liệu cũ
            for r in old_rows:
                f_out.write(json.dumps(r, ensure_ascii=False) + "\n")
            
            # 2. Ghi dữ liệu mới
            if os.path.exists(TEMP_FILE):
                with open(TEMP_FILE, "r", encoding="utf-8") as f_in:
                    for line in f_in:
                        f_out.write(line)

        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            schema=schema
        )
        with open(temp_combined, "rb") as source_file:
            job = client.load_table_from_file(source_file, FULL_TABLE_ID, job_config=job_config)
        job.result()
        
        log.info(f"=== HOAN TAT! Tong cong: {job.output_rows} ban ghi (bao gom {len(old_rows)} cu) ===")
        if os.path.exists(TEMP_FILE):
            os.remove(TEMP_FILE)
        if os.path.exists(temp_combined):
            os.remove(temp_combined)

        # ✅ Validate
        log.info("BUOC 5: Validate row count...")
        actual = validate_bq_row_count(client, FULL_TABLE_ID, 5, log)
        
        notify_success("sync_timekeep", rows=actual, extra_info=f"Đã sync từ ngày {start_date}")

    except Exception as e:
        log.error(f"=== LOI! Chi tiet: {e} ===", exc_info=True)
        notify_failure("sync_timekeep", error=str(e))
        sys.exit(1)

if __name__ == "__main__":
    main()
