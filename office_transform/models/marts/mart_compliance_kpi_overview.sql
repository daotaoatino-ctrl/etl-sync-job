-- ============================================================
-- mart_compliance_kpi_overview.sql
-- Mục đích: KPI tổng quan — BRD mục 8. Grain: 1 ngày (Power BI tự
-- gộp theo tuần/tháng qua dim_date, theo đúng mục 11 chiều phân tích).
-- Phạm vi Khối Backoffice.
-- ============================================================

{{ config(materialized='table') }}

with detail as (
    select * from {{ ref('mart_compliance_exception_detail') }}
),

timing as (
    select * from {{ ref('mart_compliance_approval_timing') }}
),

headcount as (
    select count(distinct personnel_code) as total_headcount
    from {{ ref('mart_profiles') }}
    where department_group in unnest({{ var('compliance_backoffice_dept_groups') }})
      and is_active = true
),

daily_exception_agg as (
    select
        exception_date,
        count(distinct personnel_code) as total_personnel_with_record,

        countif(exception_type not like 'Bình thường%') as total_ngoai_le,
        countif(exception_type like 'Bình thường%') as total_hop_le,

        countif(exception_type = 'Vắng không phép') as case_vang_khong_phep,
        countif(exception_type in ('Nghỉ khi chưa được duyệt')) as case_don_chua_duyet_da_nghi,
        countif(exception_type = 'Nghỉ sai quy định') as case_nghi_sai_quy_dinh,
        countif(exception_type = 'Nghỉ vượt đơn') as case_nghi_vuot_don,
        countif(processing_status = 'Chưa xử lý' and is_exception) as case_chua_xu_ly,

        countif(leave_approval_status is not null) as total_luot_nghi

    from detail
    group by exception_date
),

daily_timing_agg as (
    select
        date_record as exception_date,
        countif(flag_duyet_hoi_to) as so_don_duyet_hoi_to,
        countif(flag_tao_don_hoi_to) as so_don_tao_hoi_to,
        countif(flag_don_ton_cho_duyet_qua_han) as so_don_ton_qua_han
    from timing
    group by date_record
)

select
    e.exception_date,
    h.total_headcount,
    e.total_personnel_with_record,
    e.total_luot_nghi,
    e.total_ngoai_le,
    e.total_hop_le,
    SAFE_DIVIDE(e.total_hop_le, e.total_hop_le + e.total_ngoai_le) as ty_le_nghi_hop_le,
    e.case_vang_khong_phep,
    e.case_don_chua_duyet_da_nghi,
    e.case_nghi_sai_quy_dinh,
    e.case_nghi_vuot_don,
    e.case_chua_xu_ly,
    COALESCE(t.so_don_duyet_hoi_to, 0) as so_don_duyet_hoi_to,
    COALESCE(t.so_don_tao_hoi_to, 0) as so_don_tao_hoi_to,
    COALESCE(t.so_don_ton_qua_han, 0) as so_don_ton_qua_han
from daily_exception_agg e
cross join headcount h
left join daily_timing_agg t on e.exception_date = t.exception_date
