-- ============================================================
-- mart_headcount_snapshot.sql
-- Mục đích: Tính toán headcount theo tháng, phòng ban, và chức vụ.
--
-- ✅ Task 3.5: Fix mart_headcount_snapshot.sql thêm date grain
-- ============================================================

with months as (
    select distinct DATE_TRUNC(date, MONTH) as snapshot_month
    from {{ ref('dim_date') }}
    -- Lấy từ đầu năm 2020 đến tháng hiện tại
    where date >= '2020-01-01' and date <= current_date()
),

profiles as (
    select
        code as personnel_code,
        department_full as department,
        department_num_id as department_id,
        position_title,
        job_date_join,
        job_date_out
    from {{ ref('stg_profiles') }}
    where job_date_join is not null
)

select
    m.snapshot_month,
    p.department_id,
    p.department as department_full,
    p.position_title,
    
    -- Tổng số nhân viên đã từng tham gia tính đến tháng này (dù nghỉ hay không)
    count(p.personnel_code) as total_employees,
    
    -- Số nhân viên đang active trong tháng này (vào làm trước/trong tháng và chưa nghỉ việc hoặc nghỉ sau tháng đó)
    sum(case 
        when (p.job_date_out is null or DATE_TRUNC(p.job_date_out, MONTH) > m.snapshot_month) then 1
        else 0
    end) as active_employees

from months m
join profiles p 
  on DATE_TRUNC(p.job_date_join, MONTH) <= m.snapshot_month
group by 
    m.snapshot_month,
    p.department_id,
    p.department,
    p.position_title
