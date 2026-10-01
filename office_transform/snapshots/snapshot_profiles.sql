{% snapshot snapshot_profiles %}

{{
    config(
      target_schema='STG',
      unique_key='id',
      strategy='check',
      check_cols=['department_full', 'position_title', 'job_status', 'salary_real']
    )
}}

select * from {{ ref('stg_profiles') }}

{% endsnapshot %}
