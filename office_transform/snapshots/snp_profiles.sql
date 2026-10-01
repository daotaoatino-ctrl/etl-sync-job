{% snapshot snp_profiles %}

{{
    config(
      target_schema='MART',
      unique_key='ID',
      strategy='check',
      check_cols=['department_id', 'department_full', 'position_id', 'position_title', 'job_status', 'job_title', 'work_place']
    )
}}

select * from {{ ref('stg_profiles') }}

{% endsnapshot %}
