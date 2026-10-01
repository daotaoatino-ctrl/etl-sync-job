-- ============================================================
-- stg_approval_details.sql
-- Mục đích: Extract chi tiết từng đơn từ RAW_1OFFICE_APPROVAL_DETAILS
-- Output: 1 row = 1 đơn từ với các sub-arrays (inout, overtime, leave, absence)
--
-- ✅ Snowflake SQL syntax (đã fix từ BigQuery)
-- ⚠️  sync_time dùng LOADED_AT (từ RAW) thay vì CURRENT_TIMESTAMP()
--     để QUALIFY ROW_NUMBER có kết quả deterministic
-- ============================================================

with raw_source as (
    select * from {{ source('dwh_1office', 'raw_1office_approval_details') }}
),
flattened as (
    select
        -- ✅ Dùng SYNC_TIME từ insert lúc load vào RAW (không dùng CURRENT_TIMESTAMP())
        -- vì view được compute lại mỗi lần query → CURRENT_TIMESTAMP() luôn thay đổi
        -- khiến QUALIFY ROW_NUMBER() không deterministic
        COALESCE(
            CAST(JSON_VALUE(raw_data, '$.data._loaded_at') AS TIMESTAMP),
            CURRENT_TIMESTAMP()
        )                                                                  as sync_time,
        APPROVAL_ID                                                        as ID,
        CAST(JSON_VALUE(raw_data, '$.data.ID') AS INT64)                           as approval_id_check,
        JSON_VALUE(raw_data, '$.data.app_approval_current_id')                     as app_approval_current_id,
        JSON_VALUE(raw_data, '$.data.app_approval_current_node_deadline')          as app_approval_current_node_deadline,
        JSON_VALUE(raw_data, '$.data.app_approval_ids')                            as app_approval_ids,
        JSON_VALUE(raw_data, '$.data.app_approval_status')                         as app_approval_status,
        CAST(JSON_VALUE(raw_data, '$.data.app_approval_version_id') AS INT64)      as app_approval_version_id,
        JSON_VALUE(raw_data, '$.data.app_sub_object')                              as app_sub_object,
        CAST(JSON_VALUE(raw_data, '$.data.app_sub_object_id') AS INT64)            as app_sub_object_id,
        JSON_VALUE(raw_data, '$.data.comment_approved')                            as comment_approved,
        JSON_VALUE(raw_data, '$.data.created_by_id')                               as created_by_id,
        SAFE.PARSE_DATE('%d/%m/%Y', JSON_VALUE(raw_data, '$.data.date_approve'))    as date_approve,
        SAFE.PARSE_DATE('%d/%m/%Y', JSON_VALUE(raw_data, '$.data.date_created'))    as date_created,
        SAFE.PARSE_DATE('%d/%m/%Y', JSON_VALUE(raw_data, '$.data.date_end'))        as date_end,
        SAFE.PARSE_DATE('%d/%m/%Y', JSON_VALUE(raw_data, '$.data.date_start'))      as date_start,
        JSON_VALUE(raw_data, '$.data.department_id')                               as department_id,
        -- Sub-arrays lưu dưới dạng VARIANT để int_approval_details_flat có thể FLATTEN
        JSON_QUERY(raw_data, '$.data.files')                                       as files,
        JSON_QUERY(raw_data, '$.data.inout')                                       as inout,
        JSON_QUERY(raw_data, '$.data.overtime')                                    as overtime,
        JSON_QUERY(raw_data, '$.data.absence')                                     as absence,
        JSON_QUERY(raw_data, '$.data.leave')                                       as leave_data,
        JSON_VALUE(raw_data, '$.data.job_title')                                   as job_title,
        CAST(JSON_VALUE(raw_data, '$.data.num_file') AS INT64)                     as num_file,
        JSON_VALUE(raw_data, '$.data.personnel_code')                              as personnel_code,
        JSON_VALUE(raw_data, '$.data.personnel_name')                              as personnel_name,
        JSON_VALUE(raw_data, '$.data.position_id')                                 as position_id,
        JSON_VALUE(raw_data, '$.data.reason')                                      as reason,
        JSON_VALUE(raw_data, '$.data.tag_ids')                                     as tag_ids,
        JSON_VALUE(raw_data, '$.data.time_approve')                                as time_approve,
        JSON_VALUE(raw_data, '$.data.user_id')                                     as user_id

    from raw_source
    -- Lọc bỏ records có error từ API
    where JSON_VALUE(raw_data, '$.error') != 'true'
       or JSON_VALUE(raw_data, '$.error') is null
)

select * from flattened
-- Giữ bản ghi mới nhất cho mỗi đơn
qualify row_number() over (partition by ID order by sync_time desc) = 1
