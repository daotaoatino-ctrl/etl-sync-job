"""
check_secrets.py
================
Chặn commit chứa secret viết cứng (app_secret, password=, Bearer <token>,
private key, ...). Chạy tự động qua pre-commit hook (.githooks/pre-commit).

Cách dùng:
  python check_secrets.py          # quét các dòng THÊM MỚI trong file đã `git add` (dùng cho hook)
  python check_secrets.py --all    # quét toàn bộ file đang được git theo dõi

Bật hook (1 lần cho mỗi bản clone):
  git config core.hooksPath .githooks

Bỏ qua 1 dòng là dữ liệu test/placeholder hợp lệ: thêm comment `secret-scan: ignore`.
Bỏ qua hook khẩn cấp (không khuyến khích): git commit --no-verify
"""

import re
import subprocess
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

IGNORE_MARKER = "secret-scan: ignore"

# File không quét: chính script này (chứa regex), file nhị phân/dữ liệu sinh tự động
SKIP_FILES = {"check_secrets.py"}
SKIP_EXTENSIONS = (
    ".xlsx", ".xls", ".docx", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
    ".db", ".sqlite", ".pyc", ".woff", ".woff2", ".ttf", ".zip", ".gz", ".lock",
)
SKIP_PREFIXES = ("office_transform/target/", "office_transform/dbt_packages/", "node_modules/")

# Giá trị trông như placeholder / tham chiếu biến môi trường → không phải secret
PLACEHOLDER = re.compile(
    r"^(?:$|<.*>|\[.*\]|your[_-]|changeme|example|placeholder|redacted|xxx|\*+$|\.\.\.|…"
    r"|generate|none$|null$|true$|false$|test$|\{.*\}$)"
    r"|os\.getenv|os\.environ|getenv\(|\$\{|\$\(|^\$[A-Z_]|secrets\.|process\.env|env\.",
    re.IGNORECASE,
)

# (tên, regex, nhóm chứa giá trị cần kiểm tra placeholder — None = luôn là secret)
PATTERNS = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), None),
    # os.getenv("X_SECRET", "<giá trị thật>") — giá trị mặc định viết cứng (kiểu lỗi đã từng làm lộ secret)
    ("getenv default", re.compile(
        r"""(?:getenv|environ\.get)\(\s*["'][A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_?KEY|PRIVATE_?KEY)[A-Z0-9_]*["']"""
        r"""\s*,\s*["']([^"']+)["']""", re.I), 1),
    ("private_key", re.compile(r"""["']private_key["']\s*:\s*["']([^"']+)["']""", re.I), 1),
    # Chỉ bắt giá trị literal trong nháy; `app_secret: appSecret` (tham chiếu biến) không tính
    ("app_secret", re.compile(r"""app[_-]?secret["']?\s*[:=]\s*["']([^"']+)["']""", re.I), 1),
    ("app_secret", re.compile(r"""^\s*[A-Z0-9_]*APP_SECRET\s*=\s*([^\s"'#]+)\s*$"""), 1),
    ("password=", re.compile(r"""(?:password|passwd|pwd)["']?\s*[:=]\s*["']([^"']+)["']""", re.I), 1),
    ("password=", re.compile(r"""^\s*[A-Z0-9_]*(?:PASSWORD|PASSWD|PWD)\s*=\s*([^\s"'#]+)\s*$"""), 1),
    ("password=", re.compile(r"""PASSWORD\s*=\s*'([^'{]+)'"""), 1),  # SQL: CREATE USER ... PASSWORD = '...'
    ("Bearer", re.compile(r"""Bearer\s+([A-Za-z0-9._~+/-]{20,}=*)"""), 1),
    ("secret/token", re.compile(
        r"""\b[A-Z0-9_]*(?:SECRET|TOKEN|API_?KEY)[A-Z0-9_]*["']?\s*[:=,]\s*["']([A-Za-z0-9_\-./+=]{16,})["']"""), 1),
    ("secret/token", re.compile(
        r"""^\s*[A-Z0-9_]*(?:SECRET|TOKEN|API_?KEY)[A-Z0-9_]*\s*=\s*([A-Za-z0-9_\-./+=]{16,})\s*$"""), 1),
]


def is_placeholder(value):
    value = value.strip()
    return bool(PLACEHOLDER.search(value)) or len(value) < 4


def mask(value):
    value = value.strip()
    return f"{value[:3]}…({len(value)} ký tự)" if len(value) > 3 else "…"


def scan_line(line):
    """Trả về [(tên pattern, giá trị đã che)] cho 1 dòng — tối đa 1 kết quả/dòng."""
    if IGNORE_MARKER in line:
        return []
    for name, rx, group in PATTERNS:
        for m in rx.finditer(line):
            if group is None:
                return [(name, m.group(0)[:30])]
            value = m.group(group)
            if not is_placeholder(value):
                return [(name, mask(value))]
    return []


def should_skip(path):
    return (
        path in SKIP_FILES
        or path.lower().endswith(SKIP_EXTENSIONS)
        or path.startswith(SKIP_PREFIXES)
    )


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=True).stdout


def staged_added_lines():
    """[(path, line_no, text)] — chỉ các dòng được THÊM trong staged diff."""
    diff = git("diff", "--cached", "--unified=0", "--no-color", "--diff-filter=ACMR")
    results, path, line_no = [], None, 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[6:] if raw.startswith("+++ b/") else None
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            line_no = int(m.group(1)) if m else 0
        elif raw.startswith("+") and path and not should_skip(path):
            results.append((path, line_no, raw[1:]))
            line_no += 1
    return results


def all_tracked_lines():
    results = []
    for path in git("ls-files").splitlines():
        if should_skip(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                for n, line in enumerate(f, 1):
                    results.append((path, n, line.rstrip("\n")))
        except OSError:
            continue
    return results


def main(argv):
    lines = all_tracked_lines() if "--all" in argv else staged_added_lines()
    hits = [(p, n, name, val) for p, n, text in lines for name, val in scan_line(text)]

    if not hits:
        return 0

    print("❌ Phát hiện secret viết cứng — commit bị chặn:\n")
    for path, n, name, val in hits:
        print(f"  {path}:{n}  [{name}]  {val}")
    print(
        "\nCách sửa: chuyển giá trị sang biến môi trường (.env / dashboard), dùng placeholder,"
        f"\nhoặc nếu là dữ liệu test hợp lệ thì thêm comment `{IGNORE_MARKER}` vào dòng đó."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
