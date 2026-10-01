with source as (
    select * from {{ source('dwh_1office', 'raw_hr_master_data') }}
),
renamed as (
    select
        MA_NV as personnel_code,
        TEN_NHAN_VIEN as name,
        KHOI as department_block,
        PHONG_BAN as department,
        CAP_BAC as job_level,
        VI_TRI_CHUC_DANH as job_title,
        TINH_TRANG as status,
        HINH_THUC as contract_type,
        NGAY_VAO_NGAY_KY_H_TV as date_join,
        NGAY_KET_THUC_H_TV as date_end_probation,
        NGAY_KY_H_KV as date_sign_contract_1,
        NGAY_KET_THUC_H_KV as date_end_contract_1,
        NGAY_KY_H_L_LAN_1 as date_sign_contract_2,
        KET_THUC_H_LAN_1 as date_end_contract_2,
        NGAY_NGHI_VIEC as date_resign,
        THAM_NIEN as tenure_months,
        THANG_SINH as birth_month,
        O_TUOI as age_group,
        GIOI_TINH as gender,
        CCCD_HO_CHIEU as has_id_card,
        SHK_XAC_NHAN_CU_TRU as has_residence_proof,
        BAN_SAO_BANG_CAP as has_degree_copy,
        SO_YEU_LY_LICH as has_resume,
        GIAY_KHAI_SINH as has_birth_certificate,
        GIAY_KHAM_SUC_KHOE as has_health_certificate
    from source
)
select * from renamed
-- Loại bỏ các dòng bị trùng lặp Mã NV trong file Master Data
qualify row_number() over (partition by personnel_code order by date_join desc nulls last) = 1
