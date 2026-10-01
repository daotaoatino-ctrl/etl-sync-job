with profiles as (
    select * from {{ ref('mart_profiles') }}
),
joins as (
    select
        profile_id,
        personnel_code,
        name,
        department,
        department_group,
        position_title,
        job_date_join as event_date,
        'Tuyển mới' as event_type
    from profiles
    where job_date_join is not null
),
resignations as (
    select
        profile_id,
        personnel_code,
        name,
        department,
        department_group,
        position_title,
        job_date_out as event_date,
        'Nghỉ việc' as event_type
    from profiles
    where job_date_out is not null
)
select * from joins
union all
select * from resignations
