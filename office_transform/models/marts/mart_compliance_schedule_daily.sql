-- ============================================================
-- mart_compliance_schedule_daily.sql
-- Mục đích: Đối chiếu Lịch chuẩn (proxy) × Chấm công × Đơn từ, theo
--           BRD mục 3 (TABLE 1), mục 5, mục 7, mục 15 — PHẠM VI: Khối
--           Backoffice (Văn phòng/Kho/Sản xuất). Khối Cửa hàng để Phase sau.
-- Grain: 1 nhân sự backoffice x 1 ngày lịch chuẩn (trong 60 ngày gần nhất,
--        khớp giới hạn retention của BigQuery Sandbox).
--
-- ⚠️ "Lịch chuẩn" là proxy thay cho lịch phân ca thật (1Office không cung
--    cấp roster). Ngày lễ công ty lấy từ cờ is_holiday do 1Office tự tính
--    trong dữ liệu chấm công — không tự tạo bảng lễ riêng để tránh sai lệch.
-- ============================================================

{{ config(
    materialized='table',
    partition_by={
      "field": "schedule_date",
      "data_type": "date",
      "granularity": "day"
    },
    cluster_by="personnel_code"
) }}

with backoffice_population as (
    select
        p.personnel_code,
        p.name,
        p.department,
        p.department_group,
        p.position_title,
        p.job_date_join,
        p.job_date_out,
        p.is_active,
        sp.live_manager_code,
        sp.live_manager_id
    from {{ ref('mart_profiles') }} p
    left join {{ ref('stg_profiles') }} sp on p.personnel_code = sp.code
    where p.department_group in unnest({{ var('compliance_backoffice_dept_groups') }})
      and p.personnel_code is not null
      -- ⚠️ Chỉ tính nhân sự đang thực sự làm việc — loại người đã nghỉ/nghỉ
      -- thai sản mà 1Office chưa cập nhật job_date_out (tránh báo "vắng
      -- không phép" giả cho người không còn lịch làm thực tế).
      and p.is_active = true
),

-- Tỷ lệ chấm công thực tế của từng người trong cả cửa sổ dữ liệu — dùng để
-- gắn cờ cảnh báo độ tin cậy: nhân sự gần như không bao giờ check-in (có
-- thể do vị trí đặc thù không bắt buộc chấm công) sẽ bị dashboard báo
-- "Vắng không phép" liên tục mỗi ngày làm, gây nhiễu nếu không được đánh
-- dấu riêng. Cần HR xác nhận nhóm này trước khi tin tưởng KPI vắng mặt.
checkin_reliability as (
    select
        personnel_code,
        COUNT(*) as total_synced_days,
        COUNTIF(checkin is not null) as days_with_checkin,
        SAFE_DIVIDE(COUNTIF(checkin is not null), COUNT(*)) as checkin_rate
    from {{ ref('stg_timekeep') }}
    group by personnel_code
),

date_bounds as (
    select
        personnel_code,
        department_group,
        -- Chỉ trải lịch trong 60 ngày gần nhất (khớp retention thực tế) và
        -- trong khoảng nhân sự có hợp đồng, không project ra tương lai.
        GREATEST(job_date_join, DATE_SUB(CURRENT_DATE(), INTERVAL 60 DAY)) as range_start,
        LEAST(COALESCE(job_date_out, CURRENT_DATE()), CURRENT_DATE()) as range_end
    from backoffice_population
    where job_date_join is not null
),

schedule_spine as (
    select
        b.personnel_code,
        b.department_group,
        d.date as schedule_date,
        -- ⚠️ Lịch làm việc Thứ 7:
        -- Khối Văn phòng: LUÂN PHIÊN 2 tuần — 1 tuần T2-T7 (6 ngày) xen kẽ 1 tuần T2-T6 (5 ngày).
        -- Khối Kho & Khối Sản xuất: CỐ ĐỊNH làm việc T2-T7 (6 ngày) MỖI TUẦN theo thực tế vận hành kho xưởng.
        MOD(
          DATE_DIFF(
            DATE_TRUNC(d.date, WEEK(MONDAY)),
            DATE('{{ var("compliance_six_day_week_anchor_monday") }}'),
            WEEK(MONDAY)
          ), 2
        ) = 0 as is_six_day_week,
        EXTRACT(DAYOFWEEK FROM d.date) as day_of_week  -- BQ: 1=CN, 7=T7
    from date_bounds b
    join {{ ref('dim_date') }} d
      on d.date between b.range_start and b.range_end
),

company_holidays as (
    -- Ngày lễ công ty: lấy từ cờ is_holiday do 1Office tự tính, áp dụng
    -- chung cho toàn công ty (không riêng theo người).
    select distinct SAFE_CAST(record_date AS DATE) as holiday_date
    from {{ ref('stg_timekeep') }}
    where is_holiday = true
),

schedule_proxy as (
    select
        s.personnel_code,
        s.schedule_date,
        -- Ngày làm chuẩn:
        -- Chủ nhật (day_of_week = 1) luôn nghỉ.
        -- Thứ 2 đến Thứ 6 (day_of_week 2..6) luôn làm việc.
        -- Thứ 7 (day_of_week = 7):
        --   + Khối Kho & Khối Sản xuất: Luôn là ngày làm việc
        --   + Khối Văn phòng: Chỉ làm nếu tuần đó là tuần 6 ngày (is_six_day_week = TRUE)
        (
          s.day_of_week != 1
          and (
            s.day_of_week != 7
            or s.department_group in ('Khối Kho', 'Khối Sản xuất')
            or s.is_six_day_week
          )
        ) as is_workweek_day,
        s.is_six_day_week,
        h.holiday_date is not null as is_company_holiday,
        (
          (
            s.day_of_week != 1
            and (
              s.day_of_week != 7
              or s.department_group in ('Khối Kho', 'Khối Sản xuất')
              or s.is_six_day_week
            )
          )
          and h.holiday_date is null
        ) as is_scheduled_workday
    from schedule_spine s
    left join company_holidays h on s.schedule_date = h.holiday_date
),

attendance as (
    select
        personnel_code,
        SAFE_CAST(record_date AS DATE) as record_date,
        checkin,
        checkout,
        shift_code,
        is_check_log,
        cal_workhour,
        late_minute,
        soon_minute,
        (checkin is not null or is_check_log = 'Có') as has_checkin
    from {{ ref('stg_timekeep') }}
),

-- Gom đơn nghỉ/vắng theo (nhân sự, ngày): 1 ngày có thể có nhiều đơn,
-- ưu tiên trạng thái theo thứ tự Đã duyệt > Chờ duyệt > Không duyệt.
leave_requests_raw as (
    select
        a.approval_id,
        a.personnel_code,
        a.date_record,
        a.approval_type,
        a.approval_status,
        a.hours_amount,
        a.days_amount,
        a.reason,
        a.start_time,
        a.end_time,
        CASE a.approval_status
            WHEN 'Đã duyệt' THEN 1
            WHEN 'Chờ duyệt' THEN 2
            WHEN 'Không duyệt' THEN 3
            ELSE 4
        END as status_rank
    from {{ ref('mart_approvals') }} a
    where a.approval_type in ('Đơn xin nghỉ', 'Đơn vắng mặt')
      and a.date_record is not null
),

leave_requests_by_date as (
    select
        personnel_code,
        date_record,
        ARRAY_AGG(approval_status ORDER BY status_rank LIMIT 1)[OFFSET(0)] as leave_status,
        ARRAY_AGG(approval_type ORDER BY status_rank LIMIT 1)[OFFSET(0)] as leave_approval_type,
        ARRAY_AGG(reason ORDER BY status_rank LIMIT 1)[OFFSET(0)] as leave_reason,
        SUM(COALESCE(hours_amount, 0)) as total_leave_hours,
        (SUM(COALESCE(hours_amount, 0)) < 8 AND SUM(COALESCE(hours_amount, 0)) > 0) as is_half_day_leave,
        STRING_AGG(DISTINCT CAST(approval_id AS STRING), ',') as leave_approval_ids
    from leave_requests_raw
    group by personnel_code, date_record
),

-- Đơn OT đã duyệt: dùng để phân biệt "phát sinh làm trong ngày nghỉ chuẩn"
-- hợp lệ (có đơn OT) với case cần kiểm tra (chấm công ngày nghỉ, không có đơn OT).
ot_approved_by_date as (
    select distinct personnel_code, date_record
    from {{ ref('mart_approvals') }}
    where approval_type = 'Đơn làm thêm' and approval_status = 'Đã duyệt'
),

joined as (
    select
        sp.personnel_code,
        sp.schedule_date,
        sp.is_workweek_day,
        sp.is_company_holiday,
        sp.is_scheduled_workday,
        -- COALESCE về false: không có bản ghi chấm công cho ngày này nghĩa
        -- là không check-in, không phải "chưa xác định" (tránh lọt qua CASE
        -- WHEN phía dưới thành "Chưa phân loại").
        COALESCE(att.has_checkin, false) as has_checkin,
        att.checkin,
        att.checkout,
        att.shift_code,
        att.cal_workhour,
        att.late_minute,
        att.soon_minute,
        lv.leave_status,
        lv.leave_approval_type,
        lv.leave_reason,
        lv.total_leave_hours,
        COALESCE(lv.is_half_day_leave, false) as is_half_day_leave,
        lv.leave_approval_ids,
        (ot.personnel_code is not null) as has_approved_ot,
        cr.checkin_rate as personnel_checkin_rate_overall,
        -- ⚠️ Cờ cảnh báo: nhân sự gần như không bao giờ check-in trong cả
        -- giai đoạn dữ liệu (< 20%) — "Vắng không phép" của nhóm này rất có
        -- thể là do không dùng hệ thống chấm công (vị trí đặc thù), KHÔNG
        -- phải nghỉ không phép thật. Cần HR xác nhận trước khi coi là ngoại
        -- lệ thật sự — xem thêm ghi chú ở mart_compliance_exception_detail.
        (COALESCE(cr.checkin_rate, 0) < 0.2) as is_low_checkin_reliability
    from schedule_proxy sp
    left join attendance att
      on sp.personnel_code = att.personnel_code and sp.schedule_date = att.record_date
    left join leave_requests_by_date lv
      on sp.personnel_code = lv.personnel_code and sp.schedule_date = lv.date_record
    left join ot_approved_by_date ot
      on sp.personnel_code = ot.personnel_code and sp.schedule_date = ot.date_record
    left join checkin_reliability cr
      on sp.personnel_code = cr.personnel_code
),

classified as (
    select
        j.*,

        -- BRD mục 7: Danh mục loại ngoại lệ (theo TABLE 1 + mục 5)
        CASE
            WHEN j.is_scheduled_workday AND j.leave_status = 'Đã duyệt' AND j.is_half_day_leave AND j.has_checkin
                THEN 'Bình thường (nghỉ nửa ngày hợp lệ)'
            WHEN j.is_scheduled_workday AND j.leave_status = 'Đã duyệt' AND j.is_half_day_leave AND NOT j.has_checkin
                THEN 'Nghỉ vượt đơn'
            WHEN j.is_scheduled_workday AND j.leave_status = 'Đã duyệt' AND NOT j.is_half_day_leave AND NOT j.has_checkin
                THEN 'Bình thường (nghỉ có phép)'
            WHEN j.is_scheduled_workday AND j.leave_status = 'Đã duyệt' AND NOT j.is_half_day_leave AND j.has_checkin
                THEN 'Có đơn nghỉ cả ngày (đã duyệt) nhưng vẫn chấm công đi làm'
            -- Đơn Chờ duyệt: nếu là ngày hiện tại (chưa hết ngày làm) thì đánh dấu chờ duyệt trong ngày
            WHEN j.is_scheduled_workday AND j.leave_status = 'Chờ duyệt' AND j.schedule_date >= CURRENT_DATE() AND NOT j.has_checkin
                THEN 'Đơn đang chờ duyệt (trong ngày)'
            -- Nếu ngày nghỉ đã qua mà vẫn Chờ duyệt -> Nghỉ khi chưa được duyệt (quá hạn)
            WHEN j.is_scheduled_workday AND j.leave_status = 'Chờ duyệt' AND j.schedule_date < CURRENT_DATE() AND NOT j.has_checkin
                THEN 'Nghỉ khi chưa được duyệt'
            WHEN j.is_scheduled_workday AND j.leave_status IS NOT NULL AND j.leave_status NOT IN ('Đã duyệt', 'Chờ duyệt') AND NOT j.has_checkin
                THEN 'Nghỉ sai quy định'
            WHEN j.is_scheduled_workday AND j.leave_status IS NOT NULL AND j.leave_status != 'Đã duyệt' AND j.has_checkin
                THEN 'Cần kiểm tra (đơn chưa hợp lệ nhưng vẫn chấm công)'
            WHEN j.is_scheduled_workday AND j.leave_status IS NULL AND NOT j.has_checkin
                THEN 'Vắng không phép'
            WHEN j.is_scheduled_workday AND j.leave_status IS NULL AND j.has_checkin
                THEN 'Bình thường'
            WHEN NOT j.is_scheduled_workday AND j.has_checkin AND j.has_approved_ot
                THEN 'Bình thường (OT ngày nghỉ)'
            WHEN NOT j.is_scheduled_workday AND j.has_checkin AND NOT j.has_approved_ot
                THEN 'Cần kiểm tra (chấm công ngày nghỉ không có đơn OT)'
            WHEN NOT j.is_scheduled_workday AND NOT j.has_checkin
                THEN 'Bình thường'
            ELSE 'Chưa phân loại'
        END as exception_type

    from joined j
)

select
    c.*,
    -- BRD mục 15: Phân mức độ cảnh báo (theo TABLE 2)
    CASE
        WHEN c.exception_type IN ('Vắng không phép', 'Nghỉ sai quy định', 'Nghỉ khi chưa được duyệt')
            THEN 'Cao'
        WHEN c.exception_type IN ('Nghỉ vượt đơn',
                                  'Có đơn nghỉ cả ngày (đã duyệt) nhưng vẫn chấm công đi làm',
                                  'Cần kiểm tra (đơn chưa hợp lệ nhưng vẫn chấm công)',
                                  'Cần kiểm tra (chấm công ngày nghỉ không có đơn OT)')
            THEN 'Trung bình'
        WHEN c.exception_type = 'Đơn đang chờ duyệt (trong ngày)'
            THEN 'Thấp'
        WHEN c.exception_type LIKE 'Bình thường%'
            THEN null
        ELSE 'Thấp'
    END as severity_level
from classified c
