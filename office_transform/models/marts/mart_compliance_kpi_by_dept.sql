-- ============================================================
-- mart_compliance_kpi_by_dept.sql
-- Mục đích: KPI theo Phòng ban / Quản lý trực tiếp — BRD mục 9,
-- đã thay "Cửa hàng/QLCH/GSM/ASM" (không áp dụng Khối Backoffice)
-- bằng "Phòng ban/Quản lý trực tiếp". Grain: 1 phòng ban x 1 tháng.
-- ============================================================

{{ config(materialized='table') }}

with detail as (
    select
        *,
        DATE_TRUNC(exception_date, MONTH) as period_month
    from {{ ref('mart_compliance_exception_detail') }}
),

headcount_by_dept as (
    select department, count(distinct personnel_code) as headcount
    from {{ ref('mart_profiles') }}
    where department_group in unnest({{ var('compliance_backoffice_dept_groups') }})
      and is_active = true
    group by department
),

by_dept as (
    select
        period_month,
        department,
        count(distinct personnel_code) as personnel_with_record,
        countif(leave_approval_status is not null) as total_luot_nghi,
        countif(exception_type not like 'Bình thường%') as total_ngoai_le,
        countif(exception_type like 'Bình thường%') as total_hop_le,
        countif(flag_duyet_hoi_to) as so_don_duyet_hoi_to
    from detail
    group by period_month, department
)

select
    d.period_month,
    d.department,
    h.headcount,
    d.total_luot_nghi,
    d.total_ngoai_le,
    d.total_hop_le,
    SAFE_DIVIDE(d.total_hop_le, d.total_hop_le + d.total_ngoai_le) as ty_le_tuan_thu,
    SAFE_DIVIDE(d.total_ngoai_le * 100, h.headcount) as ngoai_le_tren_100_nhan_su,
    d.so_don_duyet_hoi_to,
    RANK() OVER (PARTITION BY d.period_month ORDER BY SAFE_DIVIDE(d.total_ngoai_le * 100, h.headcount) ASC)
        as xep_hang_tuan_thu -- hạng 1 = tuân thủ tốt nhất (ít ngoại lệ/100 NS nhất)
from by_dept d
left join headcount_by_dept h on d.department = h.department
