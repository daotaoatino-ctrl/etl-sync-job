"""
logger.py — Logging tập trung cho tất cả ETL scripts của dự án BI 1Office.

Cách dùng:
    from logger import get_logger
    log = get_logger("sync_approvals_list")
    log.info("Bắt đầu sync...")
    log.warning("Đợi API...")
    log.error(f"Lỗi: {e}")

Log files được ghi vào: ./logs/YYYY-MM-DD.log
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

# Đảm bảo console Windows hỗ trợ in UTF-8 (emojis, tiếng Việt) không bị lỗi charmap
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def get_logger(script_name: str) -> logging.Logger:
    """
    Tạo logger với 2 handler:
      - FileHandler: ghi vào logs/YYYY-MM-DD.log (UTF-8)
      - StreamHandler: in ra console (stdout)

    Gọi get_logger() nhiều lần với cùng script_name sẽ trả về cùng instance
    (không tạo duplicate handlers).
    """
    logger = logging.getLogger(script_name)
    logger.setLevel(logging.INFO)

    # Tránh thêm handlers trùng lặp nếu gọi nhiều lần
    if logger.handlers:
        return logger

    # Format: [2026-07-30 07:32:15] [INFO ] [sync_approvals_list] Nội dung message
    fmt = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)-5s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # --- Handler 1: Ra file ---
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    log_file = log_dir / f"{today}.log"

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # --- Handler 2: Ra console (stdout) ---
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger


def log_run_separator(script_name: str, logger: logging.Logger | None = None) -> None:
    """In separator line để dễ phân biệt các lần chạy trong cùng 1 log file."""
    if logger is None:
        logger = get_logger(script_name)
    logger.info("=" * 60)
    logger.info(f"  BẮT ĐẦU CHẠY: {script_name.upper()}")
    logger.info(f"  Thời gian: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)


def validate_row_count(cursor, table_name: str, expected_min: int, logger: logging.Logger) -> int:
    """Kiểm tra số dòng trong bảng sau khi load dữ liệu."""
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    actual = cursor.fetchone()[0]
    
    if actual < expected_min:
        logger.error(
            f"❌ VALIDATION FAIL: {table_name} chỉ có {actual} rows "
            f"(tối thiểu cần {expected_min})"
        )
        raise ValueError(f"Row count too low for {table_name}: {actual} < {expected_min}")
    
    logger.info(f"✅ Validation OK: {table_name} có {actual} rows (>= {expected_min})")
    return actual

def validate_bq_row_count(client, full_table_id: str, expected_min: int, logger: logging.Logger) -> int:
    """Kiểm tra số dòng trong bảng BigQuery sau khi load dữ liệu."""
    query = f"SELECT COUNT(*) as cnt FROM `{full_table_id}`"
    try:
        results = list(client.query(query).result())
        actual = results[0].cnt
    except Exception as e:
        logger.error(f"❌ Không thể validate row count BQ: {e}")
        return 0
        
    if actual < expected_min:
        logger.error(
            f"❌ VALIDATION FAIL: {full_table_id} chỉ có {actual} rows "
            f"(tối thiểu cần {expected_min})"
        )
        raise ValueError(f"Row count too low for {full_table_id}: {actual} < {expected_min}")
    
    logger.info(f"✅ Validation OK: {full_table_id} có {actual} rows (>= {expected_min})")
    return actual
