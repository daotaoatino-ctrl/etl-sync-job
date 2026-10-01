-- ============================================================
-- mart_attendance_events.sql
-- Mục đích: Bảng sự kiện chấm công cho Power BI
-- Source: stg_timekeep + stg_profiles (không dùng mart_profiles — fix cross-mart dep)
-- ============================================================

{{ config(
    materialized='table',
    partition_by={
      "field": "event_date",
      "data_type": "date",
      "granularity": "day"
    },
    cluster_by="personnel_code"
) }}

with timekeep as (
    select * from {{ ref('stg_timekeep') }}
),

-- ✅ Fix: Dùng stg_profiles thay vì mart_profiles để tránh cross-mart dependency
profiles as (
    select
        code                                                    as personnel_code,
        name,
        COALESCE(NULLIF(TRIM(department_full), ''), 'Chưa phân bổ') as department_full,
        COALESCE(NULLIF(TRIM(position_title), ''), 'Chưa cập nhật') as position_title
    from {{ ref('stg_profiles') }}
)

select
    SAFE_CAST(t.attendance_date AS DATE)                        as event_date,
    t.personnel_code,
    COALESCE(p.name, 'Chưa xác định')                          as name,
    COALESCE(p.department_full, 'Chưa phân bổ')                as department_full,
    COALESCE(p.position_title, 'Chưa cập nhật')                as position_title,

    -- Các trường giờ công
    t.cal_workday,
    t.cal_workhour,
    t.total_cal_workhour,
    t.total_cal_workday,

    -- Các trường đi muộn / về sớm
    t.late_minute,
    t.soon_minute,
    t.fine_late,
    t.fine_soon,

    -- Các trường nghỉ phép
    t.leave_p,
    t.leave_co,
    t.leave_ts,
    t.leave_dh,
    t.leave_nvsc,
    t.leave_nkl,

    -- Các trường tăng ca (OT)
    t.workhour_overtime,
    t.ot_normal_light,
    t.ot_normal_night,
    t.ot_dayoff_light,
    t.ot_holiday_light,

    -- Giữ lại các cột metadata
    'Timekeep'                                                  as event_category,
    t.job_status                                                as event_status,
    t.time_start                                                as start_time,
    t.time_end                                                  as end_time,
    t.total_cal_workhour                                        as hours_amount

from timekeep t
left join profiles p on t.personnel_code = p.personnel_code
where t.personnel_code is not null
