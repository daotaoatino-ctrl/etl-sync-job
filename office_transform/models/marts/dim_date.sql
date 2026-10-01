-- models/marts/dim_date.sql
{{ config(
    materialized='table'
) }}

WITH date_spine AS (
    SELECT d AS date_day
    FROM UNNEST(GENERATE_DATE_ARRAY('2020-01-01', '2030-12-31', INTERVAL 1 DAY)) AS d
)

SELECT
    date_day AS date,
    EXTRACT(YEAR FROM date_day) AS year,
    EXTRACT(MONTH FROM date_day) AS month,
    EXTRACT(DAY FROM date_day) AS day,
    EXTRACT(QUARTER FROM date_day) AS quarter,
    
    -- BigQuery DAYOFWEEK: 1=Sunday...7=Saturday
    -- ISO DAYOFWEEK: 1=Monday...7=Sunday
    CASE EXTRACT(DAYOFWEEK FROM date_day)
        WHEN 1 THEN 7
        ELSE EXTRACT(DAYOFWEEK FROM date_day) - 1
    END AS day_of_week, 

    FORMAT_DATE('%Y-%m', date_day) AS year_month,
    
    -- Tên tháng
    CASE EXTRACT(MONTH FROM date_day)
        WHEN 1 THEN 'Tháng 1'
        WHEN 2 THEN 'Tháng 2'
        WHEN 3 THEN 'Tháng 3'
        WHEN 4 THEN 'Tháng 4'
        WHEN 5 THEN 'Tháng 5'
        WHEN 6 THEN 'Tháng 6'
        WHEN 7 THEN 'Tháng 7'
        WHEN 8 THEN 'Tháng 8'
        WHEN 9 THEN 'Tháng 9'
        WHEN 10 THEN 'Tháng 10'
        WHEN 11 THEN 'Tháng 11'
        WHEN 12 THEN 'Tháng 12'
    END AS month_name,
    
    -- Tên thứ trong tuần
    CASE EXTRACT(DAYOFWEEK FROM date_day)
        WHEN 1 THEN 'Chủ nhật'
        WHEN 2 THEN 'Thứ 2'
        WHEN 3 THEN 'Thứ 3'
        WHEN 4 THEN 'Thứ 4'
        WHEN 5 THEN 'Thứ 5'
        WHEN 6 THEN 'Thứ 6'
        WHEN 7 THEN 'Thứ 7'
    END AS day_name,
    
    -- Phân loại ngày làm việc (Giả sử Thứ 7, Chủ Nhật là ngày nghỉ)
    CASE 
        WHEN EXTRACT(DAYOFWEEK FROM date_day) IN (1, 7) THEN FALSE 
        ELSE TRUE 
    END AS is_weekday

FROM date_spine
