"""
notifier.py — Gửi email alert khi ETL pipeline thất bại.

Cách dùng:
    from notifier import notify_success, notify_failure

    # Trong ETL script:
    try:
        # ... logic sync ...
        notify_success("sync_approvals_list", rows_inserted=1234)
    except Exception as e:
        notify_failure("sync_approvals_list", error=str(e))
        raise

Cấu hình trong .env:
    ALERT_EMAIL_FROM=yourgmail@gmail.com
    ALERT_EMAIL_PASSWORD=<gmail-app-password>   # Không phải mật khẩu Gmail thường!
    ALERT_EMAIL_TO=admin@company.com             # Có thể nhiều email, cách nhau bằng ","
    ALERT_ENABLED=true                           # Set false để tắt hoàn toàn

Hướng dẫn tạo Gmail App Password:
    1. Vào https://myaccount.google.com/security
    2. Bật 2-Step Verification
    3. Chọn "App passwords" → "Mail" → "Windows Computer"
    4. Copy 16-ký-tự password đó vào ALERT_EMAIL_PASSWORD trong .env
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Load .env nếu chưa load
load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=False)


def _is_enabled() -> bool:
    """Kiểm tra có bật alert không. Mặc định: TẮT (phải explicitly set ALERT_ENABLED=true)."""
    return os.getenv("ALERT_ENABLED", "false").lower() == "true"


def _send_email(subject: str, html_body: str) -> bool:
    """
    Gửi email qua Gmail SMTP SSL.
    Trả về True nếu gửi thành công, False nếu không.
    """
    if not _is_enabled():
        return False

    gmail_user = os.getenv("ALERT_EMAIL_FROM", "")
    gmail_pass = os.getenv("ALERT_EMAIL_PASSWORD", "")
    to_raw     = os.getenv("ALERT_EMAIL_TO", "")

    if not all([gmail_user, gmail_pass, to_raw]):
        # Thiếu config → skip silently (không raise lỗi để pipeline không fail)
        return False

    to_list = [addr.strip() for addr in to_raw.split(",") if addr.strip()]

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[1Office ETL] {subject}"
        msg["From"]    = gmail_user
        msg["To"]      = ", ".join(to_list)

        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, to_list, msg.as_string())

        return True

    except Exception:
        # Alert fail KHÔNG được làm pipeline fail
        return False


def notify_success(script_name: str, rows: int = 0, extra_info: str = "") -> None:
    """Gửi email thông báo pipeline chạy thành công."""
    subject   = f"✅ SUCCESS: {script_name}"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    html = f"""
    <html><body style="font-family: Arial, sans-serif; color: #333;">
    <h2 style="color: #2e7d32;">✅ Pipeline Thành Công</h2>
    <table style="border-collapse: collapse; width: 100%; max-width: 500px;">
        <tr><td style="padding: 8px; background: #f5f5f5; font-weight: bold;">Script</td>
            <td style="padding: 8px;">{script_name}</td></tr>
        <tr><td style="padding: 8px; background: #f5f5f5; font-weight: bold;">Thời gian</td>
            <td style="padding: 8px;">{timestamp}</td></tr>
        <tr><td style="padding: 8px; background: #f5f5f5; font-weight: bold;">Rows đã sync</td>
            <td style="padding: 8px;">{rows:,}</td></tr>
        {"<tr><td style='padding:8px;background:#f5f5f5;font-weight:bold;'>Ghi chú</td><td style='padding:8px;'>" + extra_info + "</td></tr>" if extra_info else ""}
    </table>
    </body></html>
    """
    _send_email(subject, html)


def notify_failure(script_name: str, error: str, extra_info: str = "") -> None:
    """Gửi email cảnh báo pipeline thất bại."""
    subject   = f"🚨 FAILED: {script_name}"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    html = f"""
    <html><body style="font-family: Arial, sans-serif; color: #333;">
    <h2 style="color: #c62828;">🚨 Pipeline Thất Bại</h2>
    <p style="color: #c62828;">Pipeline <strong>{script_name}</strong> đã thất bại lúc {timestamp}.</p>
    <p>Dữ liệu có thể chưa được cập nhật. Vui lòng kiểm tra và chạy lại thủ công.</p>
    <hr/>
    <h3>Chi tiết lỗi:</h3>
    <pre style="background: #fce4e4; padding: 12px; border-left: 4px solid #c62828; overflow-x: auto;">{error}</pre>
    {"<h3>Thông tin thêm:</h3><pre>" + extra_info + "</pre>" if extra_info else ""}
    <hr/>
    <p style="color: #666; font-size: 12px;">Log file: logs/{timestamp[:10]}.log</p>
    </body></html>
    """
    _send_email(subject, html)
