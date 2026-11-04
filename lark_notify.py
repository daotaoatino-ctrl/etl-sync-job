"""
lark_notify.py
==============
Gửi notification qua Lark App Bot API (1-1 message).

Cách dùng:
  from lark_notify import send_lark_text
  send_lark_text("user_id_here", "Hello!", id_type="user_id")

Test:
  python lark_notify.py
"""

import os
import sys
import json
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Fix encoding UTF-8 trên Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

load_dotenv()

LARK_APP_ID = os.getenv("LARK_APP_ID")
LARK_APP_SECRET = os.getenv("LARK_APP_SECRET")
LARK_NOTIFY_USER_ID = os.getenv("LARK_NOTIFY_USER_ID")

# ---- Token Cache ----
_cached_token = None
_token_expire_time = None

def log(msg, level="INFO"):
    """Log với timestamp"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)

def get_tenant_token():
    """
    Lấy tenant_access_token từ Lark API.
    Cache token (sống ~2 giờ).
    """
    global _cached_token, _token_expire_time

    # Nếu token còn valid, dùng cache
    if _cached_token and _token_expire_time and datetime.now() < _token_expire_time:
        return _cached_token

    try:
        log("Lấy tenant token từ Lark API...")

        url = "https://open.larksuite.com/open-apis/auth/v3/tenant_access_token/internal"
        payload = {
            "app_id": LARK_APP_ID,
            "app_secret": LARK_APP_SECRET
        }

        response = requests.post(url, json=payload, timeout=10)
        data = response.json()

        if data.get("code") != 0:
            raise Exception(f"Lark API error: {data.get('msg', 'Unknown error')}")

        token = data["tenant_access_token"]
        expire_seconds = data.get("expire", 7200)

        # Cache token
        _cached_token = token
        _token_expire_time = datetime.now() + timedelta(seconds=expire_seconds - 300)

        log(f"✅ Token lấy thành công (sống {expire_seconds}s)")
        return token

    except Exception as e:
        log(f"❌ Lỗi lấy token: {e}", level="ERROR")
        raise

def send_lark_text(receive_id, text, id_type="user_id"):
    """
    Gửi tin nhắn text qua Lark API.

    Args:
        receive_id (str): User ID hoặc Email người nhận
        text (str): Nội dung tin nhắn
        id_type (str): Loại ID - "user_id" (mặc định) hoặc "email"

    Raises:
        Exception: Nếu gửi thất bại
    """
    try:
        token = get_tenant_token()

        log(f"Gửi tin nhắn tới {id_type}={receive_id}...")

        url = f"https://open.larksuite.com/open-apis/im/v1/messages?receive_id_type={id_type}"

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        # Content phải là STRING JSON, không phải object
        content_json = json.dumps({"text": text})

        payload = {
            "receive_id": receive_id,
            "msg_type": "text",
            "content": content_json
        }

        response = requests.post(url, json=payload, headers=headers, timeout=10)
        data = response.json()

        if data.get("code") != 0:
            error_msg = data.get("msg", "Unknown error")
            log(f"❌ Lark API error: {error_msg}", level="ERROR")
            raise Exception(f"Lark API ({data.get('code')}): {error_msg}")

        log(f"✅ Gửi thành công tới {id_type}={receive_id}")
        return True

    except Exception as e:
        log(f"❌ Lỗi gửi tin nhắn: {e}", level="ERROR")
        raise

def send_lark_card(receive_id, card, id_type="user_id"):
    """
    Gửi interactive card qua Lark API.

    Args:
        receive_id (str): User ID hoặc Email người nhận
        card (dict): Card schema 1.0
        id_type (str): Loại ID - "user_id" (mặc định) hoặc "email"

    Raises:
        Exception: Nếu gửi thất bại
    """
    token = get_tenant_token()

    log(f"Gửi card tới {id_type}={receive_id}...")

    url = f"https://open.larksuite.com/open-apis/im/v1/messages?receive_id_type={id_type}"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    payload = {
        "receive_id": receive_id,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False)
    }

    response = requests.post(url, json=payload, headers=headers, timeout=10)
    data = response.json()

    if data.get("code") != 0:
        raise Exception(f"Lark API ({data.get('code')}): {data.get('msg', 'Unknown error')}")

    log(f"✅ Gửi card thành công tới {id_type}={receive_id}")
    return True

def main():
    """Test mode"""
    if not LARK_APP_ID or not LARK_APP_SECRET or not LARK_NOTIFY_USER_ID:
        log("❌ Thiếu env vars: LARK_APP_ID, LARK_APP_SECRET, hoặc LARK_NOTIFY_USER_ID", level="ERROR")
        sys.exit(1)

    try:
        test_message = "🧪 Test Lark bot notification"
        log(f"Gửi test message: {test_message}")
        recipients = [u.strip() for u in LARK_NOTIFY_USER_ID.replace(";", ",").split(",") if u.strip()]
        for uid in recipients:
            send_lark_text(uid, test_message, id_type="user_id")
        log("✅ Test thành công! Bot hoạt động bình thường.")
    except Exception as e:
        log(f"❌ Test thất bại: {e}", level="ERROR")
        sys.exit(1)

if __name__ == "__main__":
    main()
