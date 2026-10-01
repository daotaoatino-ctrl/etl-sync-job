# ==========================================
# CONFIG TẬP TRUNG - config.py
# ==========================================
# ✅ TẤT CẢ secrets được đọc từ file .env
# ❌ KHÔNG được hardcode bất kỳ mật khẩu/token nào ở đây!
#
# Cách dùng trong ETL scripts:
#   from config import ACCESS_TOKEN, SNOWFLAKE_CONFIG, APPROVAL_LIST_URL

import os
from pathlib import Path
from dotenv import load_dotenv

# Tìm và nạp file .env (ưu tiên file .env cùng thư mục với config.py)
_BASE_DIR = Path(__file__).parent
_env_path = _BASE_DIR / ".env"
load_dotenv(dotenv_path=_env_path, override=True)


def _require(key: str) -> str:
    """Lấy biến môi trường, báo lỗi rõ ràng nếu thiếu."""
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(
            f"\n❌ Thiếu biến môi trường '{key}' trong file .env!\n"
            f"   Mở file: {_env_path}\n"
            f"   Và thêm dòng: {key}=<giá trị của bạn>\n"
        )
    return val


# ============================================================
# API 1OFFICE
# ============================================================
ACCESS_TOKEN = _require("OFFICE_ACCESS_TOKEN")
API_BASE_URL = _require("OFFICE_API_BASE_URL")

# URL từng endpoint — import thẳng trong ETL scripts thay vì hardcode URL
APPROVAL_LIST_URL   = f"{API_BASE_URL}/approval/approval/gets"
APPROVAL_DETAIL_URL = f"{API_BASE_URL}/approval/approval/item"
TIMEKEEP_URL        = f"{API_BASE_URL}/timekeep/attendancedetailday/gets"
PROFILE_URL         = f"{API_BASE_URL}/personnel/profile/gets"


# ============================================================
# GOOGLE BIGQUERY
# ============================================================
PROJECT_ID = _require("GCP_PROJECT_ID")
DATASET_ID = os.getenv("GCP_DATASET_ID", "DWH_1OFFICE")

# Tự động nạp service account key:
# 1. Từ file theo GCP_KEY_PATH
# 2. Hoặc từ biến GCP_SERVICE_ACCOUNT_JSON (dành cho GitHub Actions / Cloud)
# 3. Hoặc từ gcp-key.json ở thư mục gốc
KEY_PATH = os.getenv("GCP_KEY_PATH")
if not KEY_PATH or not os.path.exists(KEY_PATH):
    default_key = _BASE_DIR / "gcp-key.json"
    gcp_json = os.getenv("GCP_SERVICE_ACCOUNT_JSON")
    if gcp_json and not default_key.exists():
        with open(default_key, "w", encoding="utf-8") as f:
            f.write(gcp_json)
    if default_key.exists():
        KEY_PATH = str(default_key)

if KEY_PATH and os.path.exists(KEY_PATH):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = KEY_PATH
else:
    raise EnvironmentError(
        f"\n❌ Không tìm thấy file credentials BigQuery!\n"
        f"   Đường dẫn kiểm tra: {KEY_PATH or (_BASE_DIR / 'gcp-key.json')}\n"
    )


# ============================================================
# ĐƯỜNG DẪN FILE
# ============================================================
HR_MASTER_DATA_PATH = os.getenv(
    "HR_MASTER_DATA_PATH",
    str(_BASE_DIR / "Master_Data_HR.xlsx")
)

# ============================================================
# LOGGING CONFIGURATION
# ============================================================
import logging

def setup_logging(script_name: str):
    """Cấu hình logging tập trung ghi ra Console và File."""
    log_dir = _BASE_DIR / "logs"
    log_dir.mkdir(exist_ok=True)
    
    log_format = "%(asctime)s - %(name)s - [%(levelname)s] - %(message)s"
    
    logger = logging.getLogger(script_name)
    logger.setLevel(logging.INFO)
    
    # Tránh duplicate logs
    if not logger.handlers:
        c_handler = logging.StreamHandler()
        f_handler = logging.FileHandler(log_dir / f"{script_name}.log", encoding="utf-8")
        
        formatter = logging.Formatter(log_format)
        c_handler.setFormatter(formatter)
        f_handler.setFormatter(formatter)
        
        logger.addHandler(c_handler)
        logger.addHandler(f_handler)
        
    return logger
