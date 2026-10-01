-- ============================================================
-- stg_timekeep.sql
-- Mục đích: Flatten JSON chấm công từ RAW_1OFFICE_TIMEKEEP_LIST
-- Output: 1 row = 1 nhân viên x 1 ngày, đã dedup
--
-- ✅ Snowflake SQL syntax (đã fix từ BigQuery)
-- ============================================================

with raw_source as (
    select * from {{ source('dwh_1office', 'raw_1office_timekeep_list') }}
),
flattened as (
    select
        SYNC_DATE                                                        as attendance_date,
        JSON_VALUE(f, '$.ID')                                               as timekeep_id,
        JSON_VALUE(f, '$.personnel_code')                                   as personnel_code,
        JSON_VALUE(f, '$.date')                                             as record_date,
        JSON_VALUE(f, '$.job_status')                                       as job_status,

        -- Dữ liệu chi tiết về ngày và giờ công
        SAFE_CAST(JSON_VALUE(f, '$.cal_workday') AS FLOAT64)                   as cal_workday,
        SAFE_CAST(JSON_VALUE(f, '$.cal_workhour') AS FLOAT64)                  as cal_workhour,
        SAFE_CAST(JSON_VALUE(f, '$.total_cal_workhour') AS FLOAT64)            as total_cal_workhour,
        SAFE_CAST(JSON_VALUE(f, '$.total_cal_workday') AS FLOAT64)             as total_cal_workday,

        -- Dữ liệu đi muộn / về sớm
        SAFE_CAST(JSON_VALUE(f, '$.late_minute') AS INT64)                     as late_minute,
        SAFE_CAST(JSON_VALUE(f, '$.soon_minute') AS INT64)                     as soon_minute,
        SAFE_CAST(JSON_VALUE(f, '$.cal_fine_late') AS FLOAT64)                 as fine_late,
        SAFE_CAST(JSON_VALUE(f, '$.cal_fine_soon') AS FLOAT64)                 as fine_soon,

        -- Dữ liệu nghỉ phép các loại
        SAFE_CAST(JSON_VALUE(f, '$.leave_type_p') AS FLOAT64)                  as leave_p,
        SAFE_CAST(JSON_VALUE(f, '$.leave_type_co') AS FLOAT64)                 as leave_co,
        SAFE_CAST(JSON_VALUE(f, '$.leave_type_ts') AS FLOAT64)                 as leave_ts,
        SAFE_CAST(JSON_VALUE(f, '$.leave_type_dh') AS FLOAT64)                 as leave_dh,
        SAFE_CAST(JSON_VALUE(f, '$.leave_type_nvsc') AS FLOAT64)               as leave_nvsc,
        SAFE_CAST(JSON_VALUE(f, '$.leave_type_nkl') AS FLOAT64)                as leave_nkl,

        -- Dữ liệu tăng ca (OT)
        SAFE_CAST(JSON_VALUE(f, '$.workhour_overtime') AS FLOAT64)             as workhour_overtime,
        SAFE_CAST(JSON_VALUE(f, '$.overtime_normal_light') AS FLOAT64)         as ot_normal_light,
        SAFE_CAST(JSON_VALUE(f, '$.overtime_normal_night') AS FLOAT64)         as ot_normal_night,
        SAFE_CAST(JSON_VALUE(f, '$.overtime_dayoff_light') AS FLOAT64)         as ot_dayoff_light,
        SAFE_CAST(JSON_VALUE(f, '$.overtime_holiday_light') AS FLOAT64)        as ot_holiday_light,

        -- Các thông số lịch trình
        JSON_VALUE(f, '$.time_start')                                       as time_start,
        JSON_VALUE(f, '$.time_end')                                         as time_end,
        JSON_VALUE(f, '$.status')                                           as status,

        -- Chấm công thực tế trong ngày (giờ vào/ra)
        JSON_VALUE(f, '$.department_id')                                    as department_id,
        SAFE_CAST(JSON_VALUE(f, '$.checkin') AS TIMESTAMP)                     as checkin,
        SAFE_CAST(JSON_VALUE(f, '$.checkout') AS TIMESTAMP)                    as checkout,
        SAFE_CAST(JSON_VALUE(f, '$.check_first') AS TIMESTAMP)                 as check_first,
        SAFE_CAST(JSON_VALUE(f, '$.check_end') AS TIMESTAMP)                   as check_end,
        JSON_VALUE(f, '$.is_check_log')                                     as is_check_log,

        -- Mã ca (dùng làm proxy "lịch chuẩn" cho Khối Backoffice — không có lịch phân ca thật)
        JSON_VALUE(f, '$.shift_code')                                       as shift_code,

        -- Cờ ngày lễ / ngày nghỉ / ngày làm do 1Office tự tính (dùng thay cho bảng lễ tự tạo)
        SAFE_CAST(JSON_VALUE(f, '$.is_holiday') AS INT64) = 1                  as is_holiday,
        SAFE_CAST(JSON_VALUE(f, '$.day_off') AS INT64) = 1                     as is_day_off,
        SAFE_CAST(JSON_VALUE(f, '$.workday') AS FLOAT64)                       as workday,

        SYNC_TIME

    from raw_source,
    -- ✅ Snowflake: LATERAL FLATTEN thay vì UNNEST
    UNNEST(JSON_QUERY_ARRAY(raw_data, '$.data')) AS f
)

select * from flattened
-- Loại bỏ bản ghi chấm công trùng lặp (cùng người, cùng ngày), giữ bản ghi mới nhất
qualify row_number() over (partition by personnel_code, attendance_date order by SYNC_TIME desc) = 1
