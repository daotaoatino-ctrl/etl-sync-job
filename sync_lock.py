"""
sync_lock.py
=============
Khóa file đơn giản để tránh 2 tiến trình sync cùng ghi 1 lúc vào cùng bảng
RAW (VD: sync_approval_details_bq.py chạy trong batch hàng ngày và
sync_realtime_poll.py chạy mỗi 5 phút cùng ghi RAW_1OFFICE_APPROVAL_DETAILS).

Đã từng gặp lỗi hỏng dữ liệu 2 lần trong quá trình phát triển vì 2 tiến
trình ghi đè cùng file tạm cùng lúc — khóa này ngăn việc đó lặp lại.

Dùng như context manager:
    with sync_lock.acquire(timeout=0):
        ... code ghi RAW_1OFFICE_APPROVAL_DETAILS ...

Nếu không lấy được khóa (đang có tiến trình khác chạy), raise LockBusyError —
caller nên bắt lỗi này và thoát êm (không phải lỗi thật, chỉ là "bỏ qua lần
này, lần sau chạy tiếp").
"""

import os
import time
import contextlib
from datetime import datetime, timezone

LOCK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync.lock")

# Nếu lock file cũ hơn ngưỡng này, coi như tiến trình cũ đã chết bất thường
# (crash, bị kill cứng) và tự dọn - tránh khóa treo vĩnh viễn.
STALE_LOCK_SECONDS = 60 * 60  # 1 giờ - đủ lớn hơn thời gian chạy dài nhất (sync_approvals_list_bq.py)


class LockBusyError(Exception):
    pass


def _is_stale(lock_path: str) -> bool:
    try:
        age = time.time() - os.path.getmtime(lock_path)
        return age > STALE_LOCK_SECONDS
    except OSError:
        return True


def _acquire_raw(owner: str = "unknown") -> None:
    """Tạo file khóa. Raise LockBusyError nếu đang có khóa còn hiệu lực."""
    if os.path.exists(LOCK_PATH):
        if _is_stale(LOCK_PATH):
            try:
                os.remove(LOCK_PATH)
            except OSError:
                pass
        else:
            raise LockBusyError(f"Dang co tien trinh khac giu khoa ({LOCK_PATH}).")

    try:
        with open(LOCK_PATH, "w", encoding="utf-8") as f:
            f.write(f"{owner}|{datetime.now(timezone.utc).isoformat()}\n")
    except OSError:
        raise LockBusyError(f"Khong tao duoc file khoa ({LOCK_PATH}).")


def _release_raw() -> None:
    try:
        os.remove(LOCK_PATH)
    except OSError:
        pass


@contextlib.contextmanager
def acquire(owner: str = "unknown"):
    """Dùng trong 1 process Python duy nhất (with sync_lock.acquire('x'): ...).
    Cho .bat gọi nhiều process riêng lẻ nối tiếp nhau, dùng CLI 'acquire'/'release'
    ở dưới thay vì context manager này (khóa cần sống xuyên suốt nhiều lần gọi)."""
    _acquire_raw(owner)
    try:
        yield
    finally:
        _release_raw()


def _cli():
    import sys as _sys
    if len(_sys.argv) < 2:
        print("Dung: python sync_lock.py acquire <owner> | python sync_lock.py release")
        _sys.exit(2)

    action = _sys.argv[1]
    if action == "acquire":
        owner = _sys.argv[2] if len(_sys.argv) > 2 else "unknown"
        try:
            _acquire_raw(owner)
            print(f"OK: da lay khoa cho '{owner}'.")
            _sys.exit(0)
        except LockBusyError as e:
            print(f"BUSY: {e}")
            _sys.exit(1)
    elif action == "release":
        _release_raw()
        print("OK: da nha khoa.")
        _sys.exit(0)
    else:
        print(f"Khong nhan dien duoc lenh '{action}'. Dung: acquire|release")
        _sys.exit(2)


if __name__ == "__main__":
    _cli()
