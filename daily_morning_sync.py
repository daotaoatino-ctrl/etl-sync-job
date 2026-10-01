"""
daily_morning_sync.py
=====================
Pipeline đồng bộ dữ liệu 1Office -> BigQuery -> dbt (chạy bằng GitHub Actions).
Được kích hoạt bởi:
  - GitHub Actions Cron (xem .github/workflows/daily_sync.yml)
  - Hoặc chạy thủ công: python daily_morning_sync.py
"""

import os
import sys
import time
import subprocess
from datetime import datetime
from pathlib import Path

# Đảm bảo UTF-8
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).parent.resolve()

def log(msg, level="INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prefix = {
        "INFO": "ℹ️ ",
        "SUCCESS": "✅",
        "WARN": "⚠️ ",
        "ERROR": "❌",
        "STEP": "🚀"
    }.get(level, "")
    print(f"[{ts}] {prefix} {msg}", flush=True)

def run_step(step_name, cmd):
    log(f"BẮT ĐẦU: {step_name}", level="STEP")
    start_time = time.time()
    try:
        res = subprocess.run(
            cmd,
            shell=True,
            cwd=str(BASE_DIR),
            check=True,
            text=True
        )
        dur = time.time() - start_time
        log(f"HOÀN THÀNH: {step_name} ({dur:.1f}s)", level="SUCCESS")
        return True
    except subprocess.CalledProcessError as e:
        dur = time.time() - start_time
        log(f"THẤT BẠI: {step_name} sau {dur:.1f}s (Mã lỗi: {e.returncode})", level="ERROR")
        return False

# Các mart phụ thuộc bảng nguồn RAW_HR_MASTER_DATA (nạp tay từ file Excel HR, không do job này tạo)
HR_MASTER_TABLE = "RAW_HR_MASTER_DATA"
HR_DEPENDENT_MARTS = ["mart_profiles", "mart_employee_events"]


def hr_master_exists():
    """True nếu bảng RAW_HR_MASTER_DATA tồn tại.
    Không kiểm tra được (lỗi quyền/mạng) → True: chạy dbt đầy đủ, không bỏ qua gì."""
    try:
        from google.cloud import bigquery
        from google.api_core.exceptions import NotFound
        from config import PROJECT_ID, DATASET_ID
        client = bigquery.Client(project=PROJECT_ID)
        try:
            client.get_table(f"{PROJECT_ID}.{DATASET_ID}.{HR_MASTER_TABLE}")
            return True
        except NotFound:
            return False
    except Exception as e:
        log(f"Không kiểm tra được bảng {HR_MASTER_TABLE} ({type(e).__name__}) — chạy dbt đầy đủ", level="WARN")
        return True


def dbt_exclude_args():
    """Chuỗi ' --exclude ...' cho lệnh dbt khi thiếu RAW_HR_MASTER_DATA, ngược lại chuỗi rỗng.
    Có bảng nguồn thì không bỏ qua gì; thiếu thì bỏ qua các mart phụ thuộc để job không báo đỏ,
    nhưng ghi rõ trong log + cảnh báo trên trang tóm tắt run."""
    if hr_master_exists():
        return ""
    msg = f"đã bỏ qua {len(HR_DEPENDENT_MARTS)} mart do thiếu {HR_MASTER_TABLE}"
    log(f"{msg}: {', '.join(HR_DEPENDENT_MARTS)} (giữ nguyên bản cũ trong BigQuery)", level="WARN")
    print(f"::warning title=dbt::{msg}", flush=True)
    return " --exclude " + " ".join(HR_DEPENDENT_MARTS)


def main():
    print("=" * 60)
    log("KHỞI ĐỘNG TIẾN TRÌNH ĐỒNG BỘ DỮ LIỆU TỰ ĐỘNG BUỔI SÁNG", level="STEP")
    print("=" * 60)
    
    py = sys.executable
    excl = dbt_exclude_args()

    # critical=False: lỗi ở bước này không làm cả job GitHub Actions báo đỏ.
    # Bước 3 gọi endpoint danh sách đơn (/approval/approval/gets) hay bị 1Office
    # timeout (xem logs/), nhưng sync_realtime_poll.py (Bước 5) tự dò & bù đắp
    # lại đơn mới/đổi trạng thái ngay trong cùng lần chạy — xem docstring của
    # sync_realtime_poll.py — nên lỗi ở đây không làm mất dữ liệu, chỉ cần cảnh báo.
    steps = [
        ("1/6. Kéo toàn bộ hồ sơ nhân sự (Biến động nhân sự) 1Office -> BigQuery",
         f'"{py}" sync_profile_bq.py', True),

        ("2/6. Kéo bảng chấm công chi tiết 1Office -> BigQuery",
         f'"{py}" sync_timekeep_bq.py', True),

        ("3/6. Kéo toàn bộ danh sách đơn từ 1Office -> BigQuery",
         f'"{py}" sync_approvals_list_bq.py', False),

        ("4/6. Kéo chi tiết các đơn mới phát sinh (OT, Phép, In/Out)",
         f'"{py}" sync_approval_details_bq.py --days-back 7', True),

        ("5/6. Quét kiểm tra & cập nhật các đơn vừa được duyệt",
         f'"{py}" sync_realtime_poll.py', True),

        ("6/7. Rebuild toàn bộ bảng MART (Đơn từ, Tuân thủ, Chấm công, Biến động nhân sự)",
         f'"{py}" dbt_wrapper.py run --select mart_approvals mart_compliance_schedule_daily mart_compliance_exception_detail mart_compliance_kpi_overview mart_compliance_kpi_by_dept mart_profiles mart_employee_events mart_headcount_snapshot mart_attendance_events{excl}', True),

        # critical=False: đơn giản là cảnh báo (qua notifier.py, xem dbt_wrapper.py) khi dữ liệu
        # không đạt chuẩn (not_null/unique/accepted_values...), không chặn job GitHub Actions -
        # MART đã build xong ở bước 6/7, website vẫn cập nhật, chỉ cần biết để kiểm tra thêm.
        ("7/7. Kiểm tra chất lượng dữ liệu các bảng MART vừa build (dbt test)",
         f'"{py}" dbt_wrapper.py test --select mart_approvals mart_compliance_schedule_daily mart_compliance_exception_detail mart_compliance_kpi_overview mart_compliance_kpi_by_dept mart_profiles mart_employee_events mart_headcount_snapshot mart_attendance_events{excl}', False)
    ]

    success_count = 0
    critical_failed = False
    for name, cmd, critical in steps:
        ok = run_step(name, cmd)
        if ok:
            success_count += 1
        elif critical:
            critical_failed = True
            log(f"Lỗi: Bước '{name}' (quan trọng) gặp lỗi, tiếp tục bước kế tiếp...", level="ERROR")
        else:
            log(f"Cảnh báo (không chặn job - đã có Bước 5 tự bù đắp): Bước '{name}' gặp lỗi, tiếp tục bước kế tiếp...", level="WARN")

    print("=" * 60)
    log(f"KẾT THÚC TIẾN TRÌNH BUỔI SÁNG: {success_count}/{len(steps)} bước thành công.", level="STEP")
    print("=" * 60)
    if critical_failed:
        sys.exit(1)

if __name__ == "__main__":
    main()
