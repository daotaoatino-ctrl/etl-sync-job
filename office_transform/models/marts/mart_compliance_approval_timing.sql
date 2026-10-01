-- ============================================================
-- mart_compliance_approval_timing.sql
-- Mục đích: Logic kiểm soát thời điểm tạo và duyệt đơn — BRD mục 4.
-- Grain: 1 đơn nghỉ/vắng x 1 ngày nghỉ cụ thể (khớp grain mart_approvals).
-- Phạm vi: chỉ đơn của nhân sự Khối Backoffice (Văn phòng/Kho/Sản xuất).
-- ============================================================

{{ config(materialized='table') }}

with backoffice_codes as (
    select personnel_code
    from {{ ref('mart_profiles') }}
    where department_group in unnest({{ var('compliance_backoffice_dept_groups') }})
),

base as (
    select
        a.approval_id,
        a.personnel_code,
        a.name,
        a.approval_type,
        a.approval_status,
        a.date_created,
        a.time_created,
        a.date_approved,
        a.time_approved,
        a.date_record,
        a.start_time,
        a.reason
    from {{ ref('mart_approvals') }} a
    join backoffice_codes b on a.personnel_code = b.personnel_code
    where a.approval_type in ('Đơn xin nghỉ', 'Đơn vắng mặt')
      and a.date_created is not null
      and a.date_record is not null
),

-- ⚠️ time_created/time_approved là "HH:MM" nhưng start_time (giờ bắt đầu
-- nghỉ, lấy từ mảng chi tiết đơn) lại là "HH:MM:SS" — 2 định dạng khác nhau
-- trong cùng nguồn 1Office. Chuẩn hóa về "HH:MM" bằng SUBSTR trước khi parse,
-- nếu không SAFE.PARSE_TIMESTAMP sẽ fail lặng lẽ ra NULL với "HH:MM:SS".
normalized as (
    select
        b.*,
        SUBSTR(COALESCE(NULLIF(b.time_created, ''), '00:00'), 1, 5) as time_created_norm,
        SUBSTR(COALESCE(NULLIF(b.time_approved, ''), '00:00'), 1, 5) as time_approved_norm,
        SUBSTR(COALESCE(NULLIF(b.start_time, ''), '00:00'), 1, 5) as start_time_norm
    from base b
),

timed as (
    select
        n.*,

        SAFE.PARSE_TIMESTAMP('%Y-%m-%d %H:%M',
            CONCAT(CAST(n.date_created AS STRING), ' ', n.time_created_norm)
        ) as created_datetime,

        SAFE.PARSE_TIMESTAMP('%Y-%m-%d %H:%M',
            CONCAT(CAST(n.date_approved AS STRING), ' ', n.time_approved_norm)
        ) as approved_datetime,

        -- Nếu đơn không có giờ bắt đầu nghỉ chi tiết (đơn nghỉ cả ngày, không
        -- qua bảng chi tiết theo phút), coi giờ bắt đầu nghỉ = 00:00 ngày nghỉ.
        SAFE.PARSE_TIMESTAMP('%Y-%m-%d %H:%M',
            CONCAT(CAST(n.date_record AS STRING), ' ', n.start_time_norm)
        ) as leave_start_datetime

    from normalized n
),

diffed as (
    select
        t.*,
        TIMESTAMP_DIFF(t.leave_start_datetime, t.created_datetime, MINUTE) as minutes_created_to_leave_start,
        CASE
            WHEN t.approved_datetime is not null
            THEN TIMESTAMP_DIFF(t.approved_datetime, t.leave_start_datetime, MINUTE)
            ELSE null
        END as minutes_leave_start_to_approved
    from timed t
)

select
    d.*,

    -- BRD mục 4: các flag kiểm soát thời điểm
    (d.minutes_created_to_leave_start BETWEEN 0 AND {{ var('compliance_sat_gio_threshold_minutes') }})
        as flag_xin_nghi_sat_gio,

    (d.minutes_created_to_leave_start < 0)
        as flag_tao_don_hoi_to,

    (d.approved_datetime is not null AND d.approved_datetime > d.leave_start_datetime)
        as flag_duyet_hoi_to,

    (d.approval_status = 'Chờ duyệt'
        AND DATE_DIFF(CURRENT_DATE(), d.date_created, DAY) > {{ var('compliance_qua_han_cho_duyet_days') }})
        as flag_don_ton_cho_duyet_qua_han

from diffed d
