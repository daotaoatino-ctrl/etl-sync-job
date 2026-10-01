-- ============================================================
-- stg_exception_status.sql
-- Mục đích: Chuẩn hóa trạng thái xử lý case ngoại lệ (mục 14 BRD)
-- Nguồn: Exception_Status_Tracking.xlsx — HR/KSNB điền tay, không có
--        nguồn tự động nào từ 1Office.
-- ============================================================

with source as (
    select * from {{ source('dwh_1office', 'raw_exception_status_tracking') }}
),
renamed as (
    select
        MA_CASE as case_id,
        MA_NV as personnel_code,
        SAFE_CAST(NGAY AS DATE) as case_date,
        LOAI_NGOAI_LE as exception_type_note,
        COALESCE(NULLIF(TRIM(TRANG_THAI_XU_LY), ''), 'Chưa xử lý') as processing_status,
        NGUOI_XU_LY as handled_by,
        GHI_CHU as note
    from source
    where MA_CASE is not null and TRIM(MA_CASE) != ''
)
select * from renamed
-- Loại bỏ trùng lặp Mã Case, giữ dòng cuối cùng trong file Excel
qualify row_number() over (partition by case_id order by case_id) = 1
