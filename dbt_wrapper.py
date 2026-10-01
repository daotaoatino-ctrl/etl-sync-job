import os
import re
import subprocess
import sys
from pathlib import Path
from dotenv import load_dotenv
from logger import get_logger
from notifier import notify_failure, notify_success

log = get_logger("dbt_wrapper")

# Dòng tổng kết dbt in ra cuối mỗi lần chạy, vd:
# "Done. PASS=24 WARN=0 ERROR=1 SKIP=0 TOTAL=25"
_SUMMARY_RE = re.compile(r"^Done\. PASS=\d+ WARN=\d+ ERROR=\d+ SKIP=\d+.*TOTAL=\d+", re.MULTILINE)
_FAILURE_RE = re.compile(r"^Failure in test \S+.*$", re.MULTILINE)


def main():
    # Load .env variables into os.environ
    env_path = Path(__file__).parent / ".env"
    load_dotenv(dotenv_path=env_path, override=True)
    
    # Force UTF-8 encoding for Python / dbt on Windows
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    
    if os.getenv("GCP_KEY_PATH"):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.getenv("GCP_KEY_PATH")

    dbt_dir = Path(__file__).parent / "office_transform"
    profiles_dir = Path.home() / ".dbt"
    
    # We pass the arguments to dbt. E.g. `py dbt_wrapper.py run`
    user_args = sys.argv[1:] if len(sys.argv) > 1 else ["run"]
    
    cmd = ["dbt"] + user_args + ["--project-dir", str(dbt_dir), "--profiles-dir", str(profiles_dir)]
    
    log.info(f"Chạy: {' '.join(cmd)}")

    dbt_subcommand = user_args[0] if user_args else "run"

    try:
        # Chạy dbt, bắt lại toàn bộ output để vừa in ra (giữ nguyên log hiện có do
        # các file .bat đang redirect stdout) vừa parse kết quả cho notifier.
        result = subprocess.run(
            cmd,
            cwd=str(dbt_dir),
            env=os.environ,
            capture_output=True,
            text=True
        )
        output = (result.stdout or "") + (result.stderr or "")
        print(output, end="")

        summary_match = _SUMMARY_RE.search(output)
        summary = summary_match.group(0) if summary_match else ""
        failures = "\n".join(_FAILURE_RE.findall(output))

        if result.returncode != 0:
            log.error(f"❌ dbt thất bại với mã lỗi {result.returncode}.")
            notify_failure(
                f"dbt {dbt_subcommand}",
                error=failures or summary or f"Exit code {result.returncode}",
                extra_info=summary
            )
            sys.exit(result.returncode)

        log.info("✅ dbt chạy thành công.")
        if dbt_subcommand in ("test", "build") and summary:
            notify_success(f"dbt {dbt_subcommand}", extra_info=summary)
        sys.exit(0)
    except FileNotFoundError:
        log.error("❌ Không tìm thấy lệnh 'dbt'. Bạn đã cài dbt-bigquery chưa?")
        notify_failure(f"dbt {dbt_subcommand}", error="Không tìm thấy lệnh 'dbt' trên máy.")
        sys.exit(1)

if __name__ == "__main__":
    main()

