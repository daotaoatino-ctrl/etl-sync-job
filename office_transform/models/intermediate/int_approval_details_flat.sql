-- ============================================================
-- int_approval_details_flat.sql
-- Mục đích: Intermediate model — flatten từng loại sub-array trong approval details
-- Nguồn: stg_approval_details
-- Output: 1 row = 1 event chi tiết (overtime/inout/absence/leave)
--
-- ✅ BigQuery SQL syntax 
-- ============================================================

with base as (
    select
        ID                                                   as approval_id,
        sync_time,
        app_sub_object,
        JSON_QUERY(overtime, '$[0].detail')                  as overtime_details,
        JSON_QUERY(inout, '$[0].inoutinfo')                  as inout_details,
        JSON_QUERY(absence, '$[0].detail')                   as absence_details,
        JSON_QUERY(leave_data, '$[0].detail')                as leave_details
    from {{ ref('stg_approval_details') }}
),

-- Bóc tách overtime
overtime_flat as (
    select
        approval_id,
        'overtime'                                           as detail_type,
        SAFE.PARSE_DATE('%Y-%m-%d', JSON_VALUE(f, '$.date')) as date_record,
        JSON_VALUE(f, '$.start_time')                          as start_time,
        JSON_VALUE(f, '$.end_time')                            as end_time,
        CAST(JSON_VALUE(f, '$.hours') AS FLOAT64)            as hours_number,
        JSON_VALUE(f, '$.app_approval_status')                 as detail_status
    from base
    LEFT JOIN UNNEST(JSON_QUERY_ARRAY(overtime_details)) as f
    where overtime_details is not null
),

-- Bóc tách inout
inout_flat as (
    select
        approval_id,
        'inout'                                              as detail_type,
        SAFE.PARSE_DATE('%Y-%m-%d', JSON_VALUE(f, '$.date')) as date_record,
        JSON_VALUE(f, '$.time')                                as start_time,
        CAST(NULL AS STRING)                                 as end_time,
        CAST(NULL AS FLOAT64)                                as hours_number,
        JSON_VALUE(f, '$.app_approval_status')                 as detail_status
    from base
    LEFT JOIN UNNEST(JSON_QUERY_ARRAY(inout_details)) as f
    where inout_details is not null
),

-- Bóc tách absence
absence_flat as (
    select
        approval_id,
        'absence'                                            as detail_type,
        SAFE.PARSE_DATE('%Y-%m-%d', JSON_VALUE(f, '$.date_start')) as date_record,
        JSON_VALUE(f, '$.time_start')                          as start_time,
        JSON_VALUE(f, '$.time_end')                            as end_time,
        CAST(JSON_VALUE(f, '$.hour_number') AS FLOAT64)      as hours_number,
        JSON_VALUE(f, '$.app_approval_status')                 as detail_status
    from base
    LEFT JOIN UNNEST(JSON_QUERY_ARRAY(absence_details)) as f
    where absence_details is not null
),

-- Bóc tách leave
leave_flat as (
    select
        approval_id,
        'leave'                                              as detail_type,
        SAFE.PARSE_DATE('%Y-%m-%d', JSON_VALUE(f, '$.date_start')) as date_record,
        JSON_VALUE(f, '$.time_start')                          as start_time,
        JSON_VALUE(f, '$.time_end')                            as end_time,
        CAST(JSON_VALUE(f, '$.day_number') AS FLOAT64) * 8  as hours_number,
        JSON_VALUE(f, '$.app_approval_status')                 as detail_status
    from base
    LEFT JOIN UNNEST(JSON_QUERY_ARRAY(leave_details)) as f
    where leave_details is not null
),

combined_details as (
    select * from overtime_flat
    union all
    select * from inout_flat
    union all
    select * from absence_flat
    union all
    select * from leave_flat
)

select * from combined_details
