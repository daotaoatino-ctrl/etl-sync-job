with profiles_raw as (
    select
        *
    from {{ ref('stg_profiles') }}
),
-- Lookup set ma nhan su da co trong profiles (dung cho JOIN thay vi NOT IN)
existing_codes as (
    select distinct code
    from profiles_raw
    where code is not null
),
missing_from_approvals as (
    -- LEFT JOIN thay vi NOT IN subquery -- nhanh hon 3-5x
    select distinct
        a.personnel_code as code,
        MAX(a.personnel_name) as name
    from {{ ref('stg_approvals') }} a
    left join existing_codes e on a.personnel_code = e.code
    where a.personnel_code is not null
      and e.code is null
    group by a.personnel_code
),
missing_from_timekeep as (
    -- LEFT JOIN 2 lan thay vi NOT IN long nhau
    select distinct
        t.personnel_code as code,
        'Chưa xác định' as name
    from {{ ref('stg_timekeep') }} t
    left join existing_codes e on t.personnel_code = e.code
    left join missing_from_approvals m on t.personnel_code = m.code
    where t.personnel_code is not null
      and e.code is null
      and m.code is null
),
missing_combined as (
    select code, name from missing_from_approvals
    union all
    select code, name from missing_from_timekeep
),
hr_master as (
    select * from {{ ref('stg_hr_master_data') }}
),
combined_profiles as (
    select
        ID, code, name, department_full, department_id, gender, position_title, 
        job_title_name, job_status, work_place, birthday, job_date_join, 
        job_date_out, salary_real, salary_total, json_insurance, json_degrees, 
        marital_status, mobile, email, sync_time
    from profiles_raw
    
    union all
    
    select
        -1 as ID,
        code,
        COALESCE(NULLIF(TRIM(name), ''), 'Chưa xác định') as name,
        'Chưa phân bổ' as department_full,
        null as department_id,
        null as gender,
        'Chưa cập nhật' as position_title,
        'Chưa cập nhật' as job_title_name,
        'Chưa cập nhật' as job_status,
        null as work_place,
        null as birthday,
        null as job_date_join,
        null as job_date_out,
        0 as salary_real,
        0 as salary_total,
        null as json_insurance,
        null as json_degrees,
        null as marital_status,
        null as mobile,
        null as email,
        current_timestamp() as sync_time
    from missing_combined
),
cleaned as (
    select
        c.ID as profile_id,
        c.code as personnel_code,
        c.name,
        
        -- Xử lý Null cho Chuỗi
        COALESCE(NULLIF(TRIM(c.gender), ''), 'Chưa cập nhật') as gender,
        
        -- Lấy 100% tên phòng ban gốc từ 1Office
        COALESCE(NULLIF(TRIM(c.department_full), ''), 'Chưa phân bổ') as department,
        
        -- Phân loại Khối Cửa hàng, Khối Kho, Khối Văn phòng, Khối Sản xuất (Đồng nhất tên gọi)
        CASE 
            -- Ép cứng các trường hợp ngoại lệ có chứa chữ lặp:
            WHEN LOWER(c.department_full) LIKE LOWER('%Kho Tổng Cửa hàng%') THEN 'Khối Kho'
            WHEN LOWER(c.department_full) LIKE LOWER('%Kinh doanh Cửa hàng%') THEN 'Khối Văn phòng'
            
            -- Ép cứng Khối Cửa hàng:
            WHEN LOWER(c.department_full) LIKE LOWER('%\\_CH%')
              OR LOWER(c.department_full) LIKE LOWER('%Cửa hàng%') THEN 'Khối Cửa hàng'

            -- Ép cứng Khối Sản xuất:
            WHEN LOWER(c.department_full) LIKE LOWER('%Kế hoạch Sản xuất%') 
              OR LOWER(c.department_full) LIKE LOWER('%Kho Nguyên phụ liệu%')
              OR LOWER(c.department_full) LIKE LOWER('%Kho NPL%')
              OR LOWER(c.department_full) LIKE LOWER('%Mua hàng%')
              OR LOWER(c.department_full) LIKE LOWER('%Cung ứng%')
              OR LOWER(c.department_full) LIKE LOWER('%Xưởng%') 
              THEN 'Khối Sản xuất'
              
            -- Ép cứng Khối Kho (Trừ các Kho đã đưa vào Khối Sản xuất ở trên):
            WHEN LOWER(c.department_full) LIKE LOWER('%Kho%') THEN 'Khối Kho'
            
            -- Khối Văn phòng (Các bộ phận còn lại):
            WHEN LOWER(c.department_full) LIKE LOWER('%Văn phòng%') THEN 'Khối Văn phòng'
            WHEN c.department_full IS NULL OR TRIM(c.department_full) = '' THEN 'Chưa phân bổ'
            ELSE 'Khối Văn phòng'
        END as department_group,
        
        c.department_id,
        COALESCE(NULLIF(TRIM(c.position_title), ''), 'Chưa cập nhật') as position_title,
        COALESCE(NULLIF(TRIM(c.job_title_name), ''), 'Chưa cập nhật') as job_title,
        COALESCE(NULLIF(TRIM(c.job_status), ''), 'Chưa cập nhật') as job_status,
        COALESCE(NULLIF(TRIM(c.work_place), ''), 'Chưa cập nhật') as work_place,
        
        -- Chuẩn hóa Trạng thái
        CASE 
            WHEN c.job_status IN ('Đang làm việc', 'Thử việc', 'Thực tập') THEN TRUE 
            ELSE FALSE 
        END as is_active,
        
        -- Ngày tháng
        c.birthday,
        c.job_date_join,
        CASE WHEN c.job_date_out < c.job_date_join THEN NULL ELSE c.job_date_out END as job_date_out,
        
        -- Tính Toán Chỉ Số Tuổi
        DATE_DIFF(current_date(), c.birthday, YEAR) as age,
        CASE
            WHEN c.birthday IS NULL THEN 'Chưa xác định'
            WHEN DATE_DIFF(current_date(), c.birthday, YEAR) < 18 THEN 'Lỗi dữ liệu'
            WHEN DATE_DIFF(current_date(), c.birthday, YEAR) < 25 THEN '< 25 tuổi'
            WHEN DATE_DIFF(current_date(), c.birthday, YEAR) BETWEEN 25 AND 30 THEN '25 - 30 tuổi'
            WHEN DATE_DIFF(current_date(), c.birthday, YEAR) BETWEEN 31 AND 40 THEN '31 - 40 tuổi'
            WHEN DATE_DIFF(current_date(), c.birthday, YEAR) > 40 THEN '> 40 tuổi'
            ELSE 'Chưa xác định'
        END as age_group,
        
        -- Tính Toán Thâm niên (Tháng)
        CASE
            WHEN c.job_date_join IS NULL THEN NULL
            ELSE DATE_DIFF(COALESCE(c.job_date_out, current_date()), c.job_date_join, MONTH)
        END as tenure_months,
        CASE
            WHEN c.job_date_join IS NULL THEN 'Chưa xác định'
            WHEN DATE_DIFF(COALESCE(c.job_date_out, current_date()), c.job_date_join, MONTH) < 6 THEN '< 6 tháng'
            WHEN DATE_DIFF(COALESCE(c.job_date_out, current_date()), c.job_date_join, MONTH) <= 12 THEN '6 - 12 tháng'
            WHEN DATE_DIFF(COALESCE(c.job_date_out, current_date()), c.job_date_join, MONTH) <= 36 THEN '1 - 3 năm'
            WHEN DATE_DIFF(COALESCE(c.job_date_out, current_date()), c.job_date_join, MONTH) > 36 THEN '> 3 năm'
            ELSE 'Chưa xác định'
        END as tenure_group,
        
        -- Xử lý Lương và Phụ cấp (Xóa Null)
        COALESCE(c.salary_real, 0) as salary_real,
        COALESCE(c.salary_total, 0) as salary_total,
        
        -- Trích xuất từ JSON
        CASE 
            WHEN c.json_insurance IS NOT NULL AND TO_JSON_STRING(c.json_insurance) != '[]' THEN TRUE 
            ELSE FALSE 
        END as has_insurance,
        
        CASE 
            WHEN c.json_degrees IS NOT NULL AND TO_JSON_STRING(c.json_degrees) != '[]' THEN TRUE
            ELSE FALSE
        END as has_degree,
        
        COALESCE(JSON_VALUE(JSON_QUERY_ARRAY(c.json_degrees)[SAFE_OFFSET(0)], '$.level_name'), 'Không có') as highest_degree_level,
        COALESCE(JSON_VALUE(JSON_QUERY_ARRAY(c.json_degrees)[SAFE_OFFSET(0)], '$.specialization'), 'Không có') as highest_degree_specialization,
        
        -- Trích xuất vài trường quan trọng khác
        COALESCE(NULLIF(TRIM(c.marital_status), ''), 'Chưa cập nhật') as marital_status,
        c.mobile,
        c.email,
        c.sync_time
        
    from combined_profiles c
    left join hr_master hr on c.code = hr.personnel_code
),
final as (
    select
        c.*,
        -- Region mapping: chưa có bảng nguồn, tạm thời để null
        CAST(NULL AS STRING) as region
    from cleaned c
)
select * from final
