-- ============================================================
-- mart_compliance_exception_detail.sql
-- Mục đích: Bảng chi tiết ngoại lệ cho Power BI — BRD mục 14.
-- Kết hợp: mart_compliance_schedule_daily (lịch/chấm công/loại ngoại lệ)
--        + mart_compliance_approval_timing (giờ tạo/duyệt đơn)
--        + stg_exception_status (trạng thái xử lý — HR/KSNB điền tay)
-- ⚠️ Phạm vi Khối Backoffice — "Cửa hàng/QLCH" trong BRD được thay bằng
--    "Phòng ban/Quản lý trực tiếp" (khái niệm QLCH không áp dụng backoffice).
-- ============================================================

{{ config(
    materialized='table',
    partition_by={
      "field": "exception_date",
      "data_type": "date",
      "granularity": "day"
    },
    cluster_by="personnel_code"
) }}

with schedule as (
    select * from {{ ref('mart_compliance_schedule_daily') }}
),

profiles as (
    select
        p.personnel_code,
        p.name,
        p.department,
        p.position_title,
        COALESCE(
            NULLIF(TRIM(sp.live_manager_id), ''),
            CONCAT('Phụ trách ', p.department)
        ) as manager_name,
        sp.live_manager_code as manager_code
    from {{ ref('mart_profiles') }} p
    left join {{ ref('stg_profiles') }} sp on p.personnel_code = sp.code
),

timing as (
    select * from {{ ref('mart_compliance_approval_timing') }}
),

status_tracking as (
    select * from {{ ref('stg_exception_status') }}
),

joined as (
    select
        s.schedule_date as exception_date,
        s.personnel_code,
        p.name,
        p.department,
        p.position_title,
        p.manager_name,
        p.manager_code,

        CASE WHEN s.is_scheduled_workday THEN 'Ngày làm' ELSE 'Ngày nghỉ (cuối tuần/lễ)' END as scheduled_day_label,

        -- Thông tin đơn (nếu có)
        t.approval_id,
        t.approval_type as leave_approval_type,
        t.approval_status as leave_approval_status,
        t.reason as leave_reason,
        t.time_created,
        t.time_approved,
        t.minutes_created_to_leave_start,
        t.minutes_leave_start_to_approved,
        t.flag_xin_nghi_sat_gio,
        t.flag_tao_don_hoi_to,
        t.flag_duyet_hoi_to,
        t.flag_don_ton_cho_duyet_qua_han,

        -- Chấm công thực tế
        s.has_checkin,
        s.checkin,
        s.checkout,
        s.shift_code,
        s.cal_workhour,
        s.late_minute,

        -- Kết quả phân loại (BRD mục 7, 15)
        s.exception_type,
        s.severity_level,
        (s.severity_level is not null) as is_exception,

        -- ⚠️ Độ tin cậy chấm công của nhân sự này (xem ghi chú ở
        -- mart_compliance_schedule_daily) — lọc/loại trừ nhóm này trước khi
        -- kết luận "vắng không phép" là thật, cần HR xác nhận.
        s.personnel_checkin_rate_overall,
        s.is_low_checkin_reliability,

        -- Mã case để đối chiếu với file Excel tracking trạng thái xử lý
        CONCAT(s.personnel_code, '_', FORMAT_DATE('%Y%m%d', s.schedule_date)) as case_id

    from schedule s
    left join profiles p on s.personnel_code = p.personnel_code
    left join timing t
      on s.personnel_code = t.personnel_code and s.schedule_date = t.date_record
)

select
    j.*,
    COALESCE(st.processing_status, CASE WHEN j.is_exception THEN 'Chưa xử lý' ELSE null END) as processing_status,
    st.handled_by,
    st.note as processing_note
from joined j
left join status_tracking st on j.case_id = st.case_id
