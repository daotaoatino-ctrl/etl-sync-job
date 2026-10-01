-- ==============================================================
-- MART_APPROVALS: Bảng đơn từ đã được flatten đầy đủ
-- Kết hợp từ 3 nguồn:
--   1. stg_approval_details  -> nội dung chi tiết đơn (FLATTEN 2 tầng)
--   2. stg_approvals         -> trạng thái + người duyệt
--   3. stg_profiles          -> phòng ban + chức danh chuẩn
-- ==============================================================

{{ config(
    materialized='table',
    partition_by={
      "field": "date_created",
      "data_type": "date",
      "granularity": "day"
    },
    cluster_by="personnel_code"
) }}

with details as (
    select * from {{ ref('stg_approval_details') }}
),

approvals_list as (
    select * from {{ ref('stg_approvals') }}
),

profiles as (
    select * from {{ ref('stg_profiles') }}
),

-- ---------------------------------------------------------------
-- TẦNG 1: Mở rộng mảng cấp 1 (inout[], overtime[], leave[], absence[])
-- ---------------------------------------------------------------

-- [A] Đơn làm thêm (OT): FLATTEN overtime[] -> mảng detail[] bên trong
overtime_l1 as (
    select
        d.ID as approval_id,
        d.personnel_code,
        d.date_created,
        d.date_approve,
        d.app_approval_status,
        SAFE_CAST(JSON_VALUE(f, '$.ID') AS INT64)               as sub_id,
        coalesce(JSON_VALUE(f, '$.desc'), JSON_VALUE(f, '$.description_text')) as description,
        SAFE_CAST(JSON_VALUE(f, '$.hours') AS FLOAT64)          as hours_amount,
        JSON_VALUE(f, '$.export_datetime')                      as ot_time_range,
        JSON_VALUE(f, '$.types')                                as ot_type,
        JSON_QUERY_ARRAY(f, '$.detail')                         as detail_array
    from details d
    LEFT JOIN UNNEST(IF(d.overtime IS NOT NULL, JSON_QUERY_ARRAY(d.overtime), [])) as f
    where d.overtime IS NOT NULL AND TO_JSON_STRING(d.overtime) != '[]'
),

-- [A] TẦNG 2: Mở rộng detail[] bên trong overtime để lấy từng ngày OT
overtime_flat as (
    select
        o.approval_id,
        'Đơn làm thêm'                          as approval_type,
        JSON_VALUE(d, '$.personnel_code')       as personnel_code,
        SAFE.PARSE_DATE('%Y-%m-%d', JSON_VALUE(d, '$.date')) as date_record,
        JSON_VALUE(d, '$.start_time')           as start_time,
        JSON_VALUE(d, '$.end_time')             as end_time,
        SAFE_CAST(JSON_VALUE(d, '$.hours') AS FLOAT64) as hours_amount,
        SAFE_CAST(NULL AS FLOAT64)              as days_amount,
        CAST(NULL AS STRING)                    as leave_type,
        CASE 
            WHEN o.ot_type = '0' THEN 'Ngày thường'
            WHEN o.ot_type = '1' THEN 'Ngày nghỉ'
            WHEN o.ot_type = '2' THEN 'Ngày lễ'
            WHEN o.ot_type IS NULL OR TRIM(o.ot_type) = '' THEN 'Chưa xác định'
            ELSE o.ot_type
        END                                     as ot_type,
        CAST(NULL AS STRING)                    as inout_reason,
        o.description                           as reason
    from overtime_l1 o
    LEFT JOIN UNNEST(o.detail_array) as d
    where o.detail_array IS NOT NULL
),

-- [B] Đơn checkin/out: FLATTEN inout[] -> inoutinfo[] bên trong
inout_l1 as (
    select
        d.ID as approval_id,
        d.personnel_code,
        JSON_VALUE(f, '$.personnel_code')       as actual_personnel_code,
        JSON_QUERY_ARRAY(f, '$.inoutinfo')      as inoutinfo_array,
        JSON_VALUE(f, '$.reason_name')          as reason_name,
        coalesce(JSON_VALUE(f, '$.desc'), JSON_VALUE(f, '$.description_text'))     as description
    from details d
    LEFT JOIN UNNEST(IF(d.inout IS NOT NULL, JSON_QUERY_ARRAY(d.inout), [])) as f
    where d.inout IS NOT NULL AND TO_JSON_STRING(d.inout) != '[]'
),

inout_flat as (
    select
        i.approval_id,
        'Đơn checkin/out'                       as approval_type,
        coalesce(i.actual_personnel_code, i.personnel_code) as personnel_code,
        SAFE.PARSE_DATE('%Y-%m-%d', JSON_VALUE(v, '$.date')) as date_record,
        JSON_VALUE(v, '$.time')                 as start_time,
        CAST(NULL AS STRING)                    as end_time,
        SAFE_CAST(NULL AS FLOAT64)              as hours_amount,
        SAFE_CAST(NULL AS FLOAT64)              as days_amount,
        CAST(NULL AS STRING)                    as leave_type,
        CAST(NULL AS STRING)                    as ot_type,
        coalesce(JSON_VALUE(v, '$.reason_name'), i.reason_name) as inout_reason,
        i.description                           as reason
    from inout_l1 i
    LEFT JOIN UNNEST(i.inoutinfo_array) as v
    where i.inoutinfo_array IS NOT NULL
    QUALIFY ROW_NUMBER() OVER (PARTITION BY i.approval_id, SAFE.PARSE_DATE('%Y-%m-%d', JSON_VALUE(v, '$.date')) ORDER BY i.approval_id) = 1
),

-- [C] Đơn nghỉ phép: FLATTEN leave[] -> detail[] bên trong
leave_l1 as (
    select
        d.ID as approval_id,
        d.personnel_code,
        JSON_VALUE(f, '$.personnel_code')       as actual_personnel_code,
        JSON_QUERY_ARRAY(f, '$.detail')         as detail_array,
        JSON_VALUE(f, '$.reason')               as leave_type_name,
        coalesce(JSON_VALUE(f, '$.desc'), JSON_VALUE(f, '$.description_text'))     as description
    from details d
    LEFT JOIN UNNEST(IF(d.leave_data IS NOT NULL, JSON_QUERY_ARRAY(d.leave_data), [])) as f
    where d.leave_data IS NOT NULL AND TO_JSON_STRING(d.leave_data) != '[]'
),

leave_flat as (
    select
        l.approval_id,
        'Đơn nghỉ phép'                         as approval_type,
        coalesce(l.actual_personnel_code, l.personnel_code) as personnel_code,
        SAFE.PARSE_DATE('%Y-%m-%d', JSON_VALUE(v, '$.date_start')) as date_record,
        JSON_VALUE(v, '$.time_start')           as start_time,
        JSON_VALUE(v, '$.time_end')             as end_time,
        SAFE_CAST(JSON_VALUE(v, '$.day_number') AS FLOAT64) * 8 as hours_amount,
        SAFE_CAST(JSON_VALUE(v, '$.day_number') AS FLOAT64)     as days_amount,
        l.leave_type_name                       as leave_type,
        CAST(NULL AS STRING)                    as ot_type,
        CAST(NULL AS STRING)                    as inout_reason,
        l.description                           as reason
    from leave_l1 l
    LEFT JOIN UNNEST(l.detail_array) as v
    where l.detail_array IS NOT NULL
    QUALIFY ROW_NUMBER() OVER (PARTITION BY l.approval_id, SAFE.PARSE_DATE('%Y-%m-%d', JSON_VALUE(v, '$.date_start')) ORDER BY l.approval_id) = 1
),

-- [D] Đơn vắng: FLATTEN absence[] -> detail[] bên trong
absence_l1 as (
    select
        d.ID as approval_id,
        d.personnel_code,
        JSON_VALUE(f, '$.personnel_code')       as actual_personnel_code,
        JSON_QUERY_ARRAY(f, '$.detail')         as detail_array,
        coalesce(JSON_VALUE(f, '$.desc'), JSON_VALUE(f, '$.description_text')) as description
    from details d
    LEFT JOIN UNNEST(IF(d.absence IS NOT NULL, JSON_QUERY_ARRAY(d.absence), [])) as f
    where d.absence IS NOT NULL AND TO_JSON_STRING(d.absence) != '[]'
),

absence_flat as (
    select
        a.approval_id,
        'Đơn vắng'                              as approval_type,
        coalesce(a.actual_personnel_code, a.personnel_code) as personnel_code,
        SAFE.PARSE_DATE('%Y-%m-%d', JSON_VALUE(v, '$.date_start')) as date_record,
        JSON_VALUE(v, '$.time_start')           as start_time,
        JSON_VALUE(v, '$.time_end')             as end_time,
        SAFE_CAST(JSON_VALUE(v, '$.hour_number') AS FLOAT64) as hours_amount,
        SAFE_CAST(NULL AS FLOAT64)              as days_amount,
        CAST(NULL AS STRING)                    as leave_type,
        CAST(NULL AS STRING)                    as ot_type,
        CAST(NULL AS STRING)                    as inout_reason,
        a.description                           as reason
    from absence_l1 a
    LEFT JOIN UNNEST(a.detail_array) as v
    where a.detail_array IS NOT NULL
    QUALIFY ROW_NUMBER() OVER (PARTITION BY a.approval_id, SAFE.PARSE_DATE('%Y-%m-%d', JSON_VALUE(v, '$.date_start')) ORDER BY a.approval_id) = 1
),

-- [E] Các loại đơn khác từ 1Office (Đơn thôi việc, Đơn công tác, Đơn đổi ca...)
other_approvals as (
    select
        al.ID as approval_id,
        coalesce(al.app_sub_object, 'Đơn khác') as approval_type,
        al.personnel_code,
        coalesce(al.date_start, al.date_created) as date_record,
        al.time_created as start_time,
        cast(null as string) as end_time,
        cast(null as float64) as hours_amount,
        cast(null as float64) as days_amount,
        cast(null as string) as leave_type,
        cast(null as string) as ot_type,
        cast(null as string) as inout_reason,
        coalesce(al.description_text, al.reason, 'Không có lý do') as reason
    from approvals_list al
    where al.ID not in (
        select distinct approval_id from overtime_l1
        union distinct select distinct approval_id from inout_l1
        union distinct select distinct approval_id from leave_l1
        union distinct select distinct approval_id from absence_l1
    )
),

-- ---------------------------------------------------------------
-- Gom tất cả các loại đơn lại (DISTINCT để loại trùng từ FLATTEN)
-- ---------------------------------------------------------------
all_events as (
    select DISTINCT * from overtime_flat
    union all
    select DISTINCT * from inout_flat
    union all
    select DISTINCT * from leave_flat
    union all
    select DISTINCT * from absence_flat
    union all
    select DISTINCT * from other_approvals
),

-- ---------------------------------------------------------------
-- JOIN với stg_approvals để lấy trạng thái và thông tin đơn
-- JOIN với stg_profiles để lấy phòng ban, chức danh chuẩn
-- ---------------------------------------------------------------
final as (
    select
        e.approval_id,
        CASE 
            WHEN LOWER(e.approval_type) LIKE '%thôi việc%' OR LOWER(e.approval_type) LIKE '%nghỉ việc%' THEN 'Đơn thôi việc'
            WHEN LOWER(e.approval_type) LIKE '%công tác%' THEN 'Đơn công tác'
            WHEN LOWER(e.approval_type) LIKE '%làm thêm%' OR LOWER(e.approval_type) LIKE '%tăng ca%' OR LOWER(e.approval_type) LIKE '%ot%' THEN 'Đơn làm thêm'
            WHEN LOWER(e.approval_type) LIKE '%checkin%' OR LOWER(e.approval_type) LIKE '%inout%' OR LOWER(e.approval_type) LIKE '%chấm công%' THEN 'Đơn checkin/out'
            WHEN LOWER(e.approval_type) LIKE '%vắng%' THEN 'Đơn vắng mặt'
            WHEN LOWER(e.approval_type) LIKE '%nghỉ%' OR LOWER(e.approval_type) LIKE '%phép%' THEN 'Đơn xin nghỉ'
            ELSE 'Đơn khác'
        END as approval_type,
        COALESCE(al.app_approval_status, 'Không xác định') as approval_status,
        COALESCE(al.date_created, e.date_record) as date_created,
        al.time_created                         as time_created,
        al.date_approve                         as date_approved,
        al.time_approve                         as time_approved,
        al.app_approval_current_id              as approver_name,

        -- Thông tin nhân sự (ưu tiên lấy từ profiles để đảm bảo chuẩn)
        e.personnel_code,
        coalesce(p.name, al.personnel_name)     as name,
        coalesce(p.department_full, al.department_id) as department_full,
        CASE
            WHEN LOWER(p.department_full) LIKE LOWER('%_CH%') OR LOWER(p.department_full) LIKE LOWER('%cửa hàng%')
            THEN 'Khối Cửa hàng'
            ELSE 'Khối Văn phòng'
        END as department_group,
        p.position_title,
        p.work_place,

        -- Nội dung đơn
        e.date_record,
        e.start_time,
        e.end_time,
        e.hours_amount,
        e.days_amount,
        COALESCE(NULLIF(TRIM(e.leave_type), ''), 'Chưa xác định') as leave_type,
        COALESCE(NULLIF(TRIM(e.ot_type), ''), 'Chưa xác định') as ot_type,
        COALESCE(NULLIF(TRIM(e.inout_reason), ''), 'Chưa xác định') as inout_reason,
        COALESCE(NULLIF(TRIM(e.reason), ''), 'Không có lý do') as reason

    from all_events e
    left join approvals_list al on e.approval_id = al.ID
    left join profiles p on e.personnel_code = p.code
    where e.personnel_code is not null
    
    {% if is_incremental() %}
        and COALESCE(al.date_created, e.date_record) >= (select coalesce(max(date_created), '1970-01-01') from {{ this }})
    {% endif %}
)

select * from final
-- ⚠️ ORDER BY đã xóa — đây là anti-pattern trong dbt materialized models
-- Sắp xếp nên để ở BI tool (Power BI), không phải trong warehouse
