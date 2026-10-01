# etl-sync-job

Job đồng bộ dữ liệu tự động: **1Office API → BigQuery (RAW) → dbt (STG/MART)**, chạy bằng GitHub Actions mỗi ngày lúc **05:00 giờ Việt Nam** (không cần máy cá nhân).

> ⚠️ **Repo này PUBLIC. Chỉ chứa code.** Không bao giờ commit dữ liệu nhân sự, file Excel/CSV, key, token hay log. Log của GitHub Actions cũng công khai — code chỉ ghi số lượng và mã đơn, không ghi tên người.

## Luồng (`daily_morning_sync.py`)

| Bước | Script | Ghi chú |
|---|---|---|
| 1 | `sync_profile_bq.py` | Hồ sơ nhân sự |
| 2 | `sync_timekeep_bq.py` | Chấm công, mặc định quét lại 3 ngày gần nhất (`--start-date` để quét lại cả tháng) |
| 3 | `sync_approvals_list_bq.py` | Danh sách đơn (timeout 10s/60s, thử lại tối đa 3 lần) |
| 4 | `sync_approval_details_bq.py` | Chi tiết đơn 7 ngày gần nhất |
| 5 | `sync_realtime_poll.py` | Bù đơn mới và đơn đổi trạng thái |
| 6 | `dbt_wrapper.py run` | Dựng bảng MART (dbt project: `office_transform/`) |
| 7 | `dbt_wrapper.py test` | Kiểm tra chất lượng, không chặn job |

## Cài đặt

1. **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Nội dung |
|---|---|
| `OFFICE_ACCESS_TOKEN` | Access token API 1Office |
| `OFFICE_API_BASE_URL` | URL gốc API 1Office (để dạng secret để không lộ tên miền công ty) |
| `GCP_PROJECT_ID` | ID project BigQuery |
| `GCP_SERVICE_ACCOUNT_JSON` | Toàn bộ nội dung file key của service account |

2. Service account chỉ cấp quyền tối thiểu: **BigQuery Job User** (project) và **BigQuery Data Editor** trên 3 dataset `DWH_1OFFICE`, `MART`, `STG`. Không dùng key quyền admin.
3. Chạy thử: **Actions → Daily 1Office sync → Run workflow**.

Tuỳ chọn: `ALERT_ENABLED=true` cùng `ALERT_EMAIL_FROM`, `ALERT_EMAIL_PASSWORD`, `ALERT_EMAIL_TO` để `notifier.py` gửi email khi lỗi (cần thêm vào `env` của workflow).

## Chạy cục bộ

```bash
pip install -r requirements.txt
cp office_transform/profiles.yml.example ~/.dbt/profiles.yml   # rồi sửa project, keyfile
# tạo .env (không commit): OFFICE_ACCESS_TOKEN, OFFICE_API_BASE_URL, GCP_PROJECT_ID, GCP_KEY_PATH
python daily_morning_sync.py
```

## Bảo mật

- `git config core.hooksPath .githooks` — hook chạy `check_secrets.py` chặn commit chứa secret (`python check_secrets.py --all` quét toàn repo).
- Workflow che riêng tên miền 1Office trong log, ghi key BigQuery ra file tạm và xoá ngay sau khi chạy.
- GitHub tự tắt lịch chạy của repo public sau 60 ngày không có hoạt động; workflow tự tạo commit rỗng khi commit cuối đã quá 45 ngày.
- Bảng trong BigQuery Sandbox tự xoá sau 60 ngày — bật thanh toán cho project để gỡ giới hạn này.

## Lưu ý đã biết

- `mart_profiles` đọc `RAW_HR_MASTER_DATA` (nạp từ file Excel HR, **không** nằm trong repo). Khi bảng này không tồn tại, bước dbt 6 báo lỗi ở `mart_profiles` và bỏ qua các model phụ thuộc.
