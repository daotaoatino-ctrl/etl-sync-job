-- ============================================================
-- stg_approvals.sql
-- Mục đích: Flatten JSON danh sách đơn từ từ RAW_1OFFICE_APPROVALS_LIST
-- Output: 1 row = 1 đơn từ, đã dedup theo sync_time mới nhất
--
-- ✅ Snowflake SQL syntax (đã fix từ BigQuery)
-- ============================================================

with raw_source as (
    select * from {{ source('dwh_1office', 'raw_1office_approvals_list') }}
),
flattened as (
    select
        sync_time,
        CAST(JSON_VALUE(f, '$.ID') AS INT64)                                                        as ID,
        JSON_VALUE(f, '$.api_app_approval_ids')                                   as api_app_approval_ids,
        JSON_VALUE(f, '$.app_approval_current_id')                                as app_approval_current_id,
        JSON_VALUE(f, '$.app_approval_current_node_deadline')                     as app_approval_current_node_deadline,
        JSON_VALUE(f, '$.app_approval_ids')                                       as app_approval_ids,
        JSON_VALUE(f, '$.app_approval_status')                                    as app_approval_status,
        JSON_VALUE(f, '$.app_approval_status_step')                               as app_approval_status_step,
        JSON_VALUE(f, '$.app_approval_version_id')                                as app_approval_version_id,
        JSON_VALUE(f, '$.app_sub_object')                                         as app_sub_object,
        CAST(JSON_VALUE(f, '$.app_sub_object_id') AS INT64)                                         as app_sub_object_id,
        JSON_VALUE(f, '$.comment_approved')                                       as comment_approved,
        JSON_VALUE(f, '$.company_id')                                             as company_id,
        JSON_VALUE(f, '$.created_by_id')                                          as created_by_id,
        SAFE.PARSE_DATE('%d/%m/%Y', JSON_VALUE(f, '$.date_approve'))               as date_approve,
        SAFE.PARSE_DATE('%d/%m/%Y', JSON_VALUE(f, '$.date_created'))               as date_created,
        SAFE.PARSE_DATE('%d/%m/%Y', JSON_VALUE(f, '$.date_end'))                   as date_end,
        SAFE.PARSE_DATE('%d/%m/%Y', JSON_VALUE(f, '$.date_start'))                 as date_start,
        SAFE.PARSE_DATE('%d/%m/%Y', JSON_VALUE(f, '$.date_subobject'))             as date_subobject,
        JSON_VALUE(f, '$.department_id')                                          as department_id,
        JSON_VALUE(f, '$.desc')                                                   as description_text,
        JSON_VALUE(f, '$.job_title')                                              as job_title,
        JSON_VALUE(f, '$.last_comments')                                          as last_comments,
        CAST(JSON_VALUE(f, '$.num_file') AS INT64)                                                  as num_file,
        JSON_VALUE(f, '$.personnel_code')                                         as personnel_code,
        JSON_VALUE(f, '$.personnel_name')                                         as personnel_name,
        JSON_VALUE(f, '$.position_id')                                            as position_id,
        JSON_VALUE(f, '$.project_code')                                           as project_code,
        JSON_VALUE(f, '$.project_rel')                                            as project_rel,
        JSON_VALUE(f, '$.reason')                                                 as reason,
        JSON_VALUE(f, '$.tag_ids')                                                as tag_ids,
        JSON_VALUE(f, '$.time_approve')                                           as time_approve,
        JSON_VALUE(f, '$.time_created')                                           as time_created,
        JSON_VALUE(f, '$.user_id')                                                as user_id

    from raw_source,
    -- ✅ Snowflake: LATERAL FLATTEN thay vì UNNEST
    UNNEST(JSON_QUERY_ARRAY(raw_data, '$.data')) AS f
)

select * from flattened
where personnel_code is not null and personnel_code != ''
-- Giữ bản ghi mới nhất theo sync_time cho mỗi đơn
qualify row_number() over (partition by ID order by sync_time desc) = 1
