"""
sync_realtime_poll.py
=======================
Dò các đơn từ MỚI trên 1Office gần theo thời gian thực, bổ sung cho batch
hàng ngày (CAP_NHAT_HANG_NGAY_AUTO.bat) vốn chỉ chạy 1 lần/ngày lúc 07:30.

Cách hoạt động: ID đơn từ trên 1Office tăng dần liên tục trên toàn công ty
(đã xác nhận qua test API thật). Thay vì kéo lại toàn bộ ~43.000 đơn (chậm,
hay timeout — đúng nguyên nhân batch hàng ngày hay treo ở bước này), script
này chỉ dò tiếp từ ID lớn nhất đã biết, dùng endpoint lấy 1 đơn theo ID
(/api/approval/approval/item) — nhanh hơn nhiều (0.3-0.8s/lần).

Không thay thế batch hàng ngày — đơn mới dò được có thể thiếu vài trường
phụ (VD tên người duyệt đầy đủ) cho tới khi batch hàng ngày chạy qua bổ
sung từ danh sách đầy đủ.

Chạy: py -3.12 -u sync_realtime_poll.py  (nên gọi qua POLL_REALTIME.bat)
"""

import os
import sys
import json
import time
import tempfile

import requests
from google.cloud import bigquery

from config import ACCESS_TOKEN, APPROVAL_DETAIL_URL, PROJECT_ID, DATASET_ID
from logger import get_logger
from notifier import notify_failure
import sync_lock

log = get_logger("sync_realtime_poll")

TABLE_ID = f"{PROJECT_ID}.{DATASET_ID}.RAW_1OFFICE_APPROVAL_DETAILS"
MAX_IDS_PER_RUN = 200
DELAY_SECONDS = 0.8          # giữa các lần gọi API - an toàn và nhanh hơn
STOP_AFTER_CONSECUTIVE_MISSES = 3  # coi như đã bắt kịp ID mới nhất

SCHEMA = [
    bigquery.SchemaField("APPROVAL_ID", "INTEGER", mode="NULLABLE"),
    bigquery.SchemaField("RAW_DATA", "JSON", mode="NULLABLE"),
]

DBT_SELECT = (
    "stg_approval_details+ mart_approvals+ mart_compliance_schedule_daily+ "
    "mart_compliance_approval_timing+ mart_compliance_exception_detail+ "
    "mart_compliance_kpi_overview+ mart_compliance_kpi_by_dept+"
)


def fetch_detail(approval_id: int):
    for attempt in range(3):
        try:
            resp = requests.get(
                APPROVAL_DETAIL_URL,
                params={"access_token": ACCESS_TOKEN, "id": approval_id},
                timeout=10,
            )
            if resp.status_code == 429:
                time.sleep(3 * (attempt + 1))
                continue
            data = resp.json()
            if data.get("code") == "token_not_valid":
                return None
            if data.get("error") is True and data.get("message") == "Post not found":
                return "NOT_FOUND"
            if data.get("error") is True:
                # Lỗi khác (VD rate limit dạng message text) - thử lại
                time.sleep(2 * (attempt + 1))
                continue
            return data
        except Exception as e:
            log.warning(f"  Loi goi API id={approval_id} (lan {attempt+1}/3): {e}")
            time.sleep(2)
    return None


def get_start_id(client) -> int:
    q = f"SELECT MAX(APPROVAL_ID) as max_id FROM `{TABLE_ID}`"
    try:
        rows = list(client.query(q).result())
        max_id = rows[0].max_id
        return int(max_id) if max_id is not None else 0
    except Exception as e:
        log.warning(f"  Khong lay duoc MAX(APPROVAL_ID) (co the bang chua ton tai): {e}")
        return 0


def load_existing_rows(client):
    rows = []
    try:
        for r in client.query(f"SELECT APPROVAL_ID, TO_JSON_STRING(RAW_DATA) as raw_data_str FROM `{TABLE_ID}`").result():
            rows.append({"APPROVAL_ID": r.APPROVAL_ID, "RAW_DATA": json.loads(r.raw_data_str)})
    except Exception as e:
        log.warning(f"  Khong doc duoc du lieu cu (co the bang chua ton tai): {e}")
    return rows


def write_rows(client, rows: list):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ndjson", delete=False, encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        temp_path = f.name

    try:
        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            schema=SCHEMA,
        )
        with open(temp_path, "rb") as source_file:
            job = client.load_table_from_file(source_file, TABLE_ID, job_config=job_config)
        job.result()
    finally:
        os.remove(temp_path)


def deduplicate_rows(rows: list) -> list:
    """Loại bỏ các bản ghi trùng APPROVAL_ID trong RAW_1OFFICE_APPROVAL_DETAILS,
    ưu tiên bản ghi đã duyệt/từ chối hoặc có ngày duyệt."""
    best_by_id = {}
    for r in rows:
        aid = r["APPROVAL_ID"]
        if aid not in best_by_id:
            best_by_id[aid] = r
        else:
            prev_status = best_by_id[aid].get("RAW_DATA", {}).get("data", {}).get("app_approval_status", "")
            curr_status = r.get("RAW_DATA", {}).get("data", {}).get("app_approval_status", "")
            if prev_status == "Chờ duyệt" and curr_status != "Chờ duyệt":
                best_by_id[aid] = r
    return list(best_by_id.values())


def append_to_approvals_list(client, records: dict):
    """Bổ sung các đơn mới dò được hoặc đơn cập nhật trạng thái vào bảng RAW_1OFFICE_APPROVALS_LIST."""
    items = [data["data"] for data in records.values() if isinstance(data, dict) and "data" in data]
    if not items:
        return
    row = {"RAW_DATA": {"data": items, "error": False}}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ndjson", delete=False, encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        temp_path = f.name
    try:
        table_list_id = f"{PROJECT_ID}.{DATASET_ID}.RAW_1OFFICE_APPROVALS_LIST"
        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            schema=[
                bigquery.SchemaField("SYNC_TIME", "TIMESTAMP", mode="NULLABLE", default_value_expression="CURRENT_TIMESTAMP()"),
                bigquery.SchemaField("RAW_DATA", "JSON", mode="NULLABLE"),
            ],
        )
        with open(temp_path, "rb") as source_file:
            job = client.load_table_from_file(source_file, table_list_id, job_config=job_config)
        job.result()
        log.info(f"  Da append {len(items)} ban ghi moi/cap nhat vao {table_list_id}.")
    except Exception as e:
        log.warning(f"  Khong the append vao RAW_1OFFICE_APPROVALS_LIST: {e}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def get_pending_approval_ids(client, days_back=30, max_count=50) -> list:
    """Lấy danh sách các đơn đang ở trạng thái 'Chờ duyệt' trong khoảng ngày gần đây,
    ưu tiên các đơn mới nhất (id DESC)."""
    q = f"""
    WITH combined AS (
      SELECT 
        APPROVAL_ID as id,
        JSON_VALUE(RAW_DATA, '$.data.app_approval_status') as status,
        SAFE.PARSE_DATE('%d/%m/%Y', JSON_VALUE(RAW_DATA, '$.data.date_created')) as created_date
      FROM `{TABLE_ID}`
      WHERE RAW_DATA IS NOT NULL
      UNION ALL
      SELECT 
        CAST(JSON_VALUE(item, '$.ID') AS INT64) as id,
        JSON_VALUE(item, '$.app_approval_status') as status,
        SAFE.PARSE_DATE('%d/%m/%Y', JSON_VALUE(item, '$.date_created')) as created_date
      FROM `{PROJECT_ID}.{DATASET_ID}.RAW_1OFFICE_APPROVALS_LIST`,
      UNNEST(JSON_QUERY_ARRAY(RAW_DATA, '$.data')) as item
      WHERE item IS NOT NULL
    ),
    deduped AS (
      SELECT id, status, created_date
      FROM combined
      WHERE id IS NOT NULL
      QUALIFY ROW_NUMBER() OVER(PARTITION BY id ORDER BY CASE WHEN status != 'Chờ duyệt' THEN 0 ELSE 1 END) = 1
    )
    SELECT id
    FROM deduped
    WHERE status = 'Chờ duyệt'
      AND created_date >= DATE_SUB(CURRENT_DATE(), INTERVAL {days_back} DAY)
    ORDER BY id DESC
    LIMIT {max_count}
    """
    try:
        rows = [int(r.id) for r in client.query(q).result()]
        return rows
    except Exception as e:
        log.warning(f"  Khong lay duoc danh sach don cho duyet: {e}")
        return []


def recheck_pending_orders(pending_ids: list) -> dict:
    """Quét lại trạng thái các đơn đang chờ duyệt trên 1Office.
    Nếu trạng thái đã chuyển (Đã duyệt, Từ chối, Không duyệt, Đã hủy...), trả về dict {id: data}."""
    updated = {}
    if not pending_ids:
        return updated

    log.info(f"  Kiem tra lai {len(pending_ids)} don dang 'Cho duyet' gan day...")
    for aid in pending_ids:
        data = fetch_detail(aid)
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], dict):
            item = data["data"]
            curr_status = item.get("app_approval_status")
            if curr_status and curr_status != "Chờ duyệt":
                updated[aid] = data
                # Repo public → log công khai: KHÔNG ghi tên người, chỉ mã đơn và trạng thái
                log.info(f"  🎯 Don ID {aid}: Chờ duyệt -> '{curr_status}'")
        time.sleep(0.2)
    return updated


def update_web_sync_status(new_count=0, updated_count=0):
    """Repo sync-only: không có web_app/ để cập nhật sync_status.json hay xoá cache — bỏ qua."""
    return None


def rebuild_compliance_marts():
    import subprocess
    # ⚠️ Bắt buộc --full-refresh: mart_approvals materialized='incremental',
    # nếu không full-refresh dbt dùng lệnh MERGE (DML) — bị chặn vì project
    # đang ở BigQuery Sandbox (billing bị revert, xem ghi chú trong auth.py).
    # --full-refresh dùng CREATE OR REPLACE TABLE (DDL), không bị chặn.
    import sys
    py = sys.executable
    cmd = f'"{py}" -u dbt_wrapper.py run --full-refresh --select {DBT_SELECT}'
    log.info(f"  Chay: {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=os.path.dirname(os.path.abspath(__file__)))
    if result.returncode != 0:
        raise RuntimeError(f"dbt build that bai, ma loi {result.returncode}")


def main():
    log.info("=== Bat dau tien trinh dong bo realtime (don moi & re-check cho duyet) ===")
    try:
        with sync_lock.acquire("sync_realtime_poll"):
            client = bigquery.Client()
            start_id = get_start_id(client)
            if start_id == 0:
                log.warning("  Chua co du lieu RAW_1OFFICE_APPROVAL_DETAILS - bo qua lan nay, cho batch hang ngay chay truoc.")
                return

            # --- PHẦN 1: Dò các đơn ID MỚI ---
            log.info(f"  [1/2] Do don moi tu ID {start_id + 1}, toi da {MAX_IDS_PER_RUN} ID, cach nhau {DELAY_SECONDS}s")
            new_records = {}
            consecutive_misses = 0
            checked = 0
            current_id = start_id

            while checked < MAX_IDS_PER_RUN and consecutive_misses < STOP_AFTER_CONSECUTIVE_MISSES:
                current_id += 1
                checked += 1
                data = fetch_detail(current_id)

                if data == "NOT_FOUND":
                    consecutive_misses += 1
                elif data is None:
                    # Loi mang/API sau retry - dung lai, khong danh dau la het ID (khac voi "khong ton tai")
                    log.warning(f"  ID {current_id}: khong lay duoc sau 3 lan thu, dung lai lan nay.")
                    break
                else:
                    consecutive_misses = 0
                    new_records[current_id] = data
                    log.info(f"  ✅ Tim thay don moi: ID {current_id}")

                time.sleep(DELAY_SECONDS)

            # --- PHẦN 2: Re-check các đơn đang 'Chờ duyệt' gần đây ---
            log.info(f"  [2/2] Quet lai cac don dang 'Cho duyet' trong 30 ngay gan nhat...")
            pending_ids = get_pending_approval_ids(client, days_back=30, max_count=50)
            updated_records = recheck_pending_orders(pending_ids)

            all_changed = {**new_records, **updated_records}

            if not all_changed:
                log.info("=== Khong co don moi va khong co don doi trang thai. Hoan tat. ===")
                update_web_sync_status(0, 0)
                return

            log.info(f"  Tong cong: {len(new_records)} don moi, {len(updated_records)} don doi trang thai.")
            existing_rows = load_existing_rows(client)

            combined = [r for r in existing_rows if r["APPROVAL_ID"] not in all_changed]
            for aid, data in all_changed.items():
                combined.append({"APPROVAL_ID": aid, "RAW_DATA": data})

            combined = deduplicate_rows(combined)
            write_rows(client, combined)
            log.info(f"  Da ghi {len(combined)} ban ghi vao {TABLE_ID}.")

            # Bổ sung các đơn mới/cập nhật vào bảng danh sách RAW_1OFFICE_APPROVALS_LIST
            append_to_approvals_list(client, all_changed)

        # Rebuild dbt SAU khi da nha khoa (khong giu khoa lau, dbt build khong dung RAW_1OFFICE_APPROVAL_DETAILS
        # theo kieu ghi - chi doc qua stg view, an toan de chay ngoai pham vi khoa)
        rebuild_compliance_marts()

        # Cập nhật trạng thái đồng bộ lên web và làm mới cache
        update_web_sync_status(len(new_records), len(updated_records))
        log.info(f"=== HOAN TAT! Da cap nhat {len(new_records)} don moi, {len(updated_records)} don doi trang thai len dashboard va web. ===")

    except sync_lock.LockBusyError as e:
        log.info(f"  Dang co tien trinh sync khac chay ({e}) - bo qua lan nay, se thu lai lan sau.")
    except Exception as e:
        log.error(f"=== LOI: {e} ===", exc_info=True)
        notify_failure("sync_realtime_poll", error=str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
