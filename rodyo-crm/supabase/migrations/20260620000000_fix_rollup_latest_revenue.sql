create or replace view public.store_monthly_profile as
with revenue_totals as (
  select
    store_id,
    sum(revenue) as revenue_total
  from public.monthly_revenue
  group by store_id
),
active_revenue as (
  select
    store_id,
    revenue_month,
    revenue,
    row_number() over (partition by store_id order by revenue_month desc) as active_rank
  from public.monthly_revenue
  where revenue > 0
),
latest_revenue as (
  select distinct on (store_id)
    store_id,
    revenue_month as latest_month,
    revenue as latest_month_revenue
  from active_revenue
  order by store_id, revenue_month desc
),
last_active as (
  select
    store_id,
    max(revenue_month) as k_savage_last_active
  from active_revenue
  group by store_id
),
last_active_amount as (
  select distinct on (store_id)
    store_id,
    revenue_month as k_savage_last_active_month,
    revenue as k_savage_last_active_revenue
  from active_revenue
  order by store_id, revenue_month desc
),
run_rate as (
  select
    store_id,
    avg(revenue) as k_savage_monthly_run_rate
  from active_revenue
  where active_rank <= 3
  group by store_id
)
select
  s.id as store_id,
  coalesce(rt.revenue_total, 0) as revenue_total,
  lr.latest_month,
  coalesce(lr.latest_month_revenue, 0) as latest_month_revenue,
  la.k_savage_last_active,
  laa.k_savage_last_active_month,
  coalesce(laa.k_savage_last_active_revenue, 0) as k_savage_last_active_revenue,
  coalesce(rr.k_savage_monthly_run_rate, 0) as k_savage_monthly_run_rate
from public.stores s
left join revenue_totals rt on rt.store_id = s.id
left join latest_revenue lr on lr.store_id = s.id
left join last_active la on la.store_id = s.id
left join last_active_amount laa on laa.store_id = s.id
left join run_rate rr on rr.store_id = s.id;

create or replace view public.crm_store_rollup as
with base as (
  select
    s.id as store_id,
    s.license,
    s.license_key,
    s.store_name,
    l.city,
    l.state,
    l.zip,
    l.county,
    l.latitude,
    l.longitude,
    coalesce(l.market_sales_last_month, 0) as market_sales_last_month,
    r.initials as territory_rep,
    coalesce(mp.revenue_total, 0) as revenue_total,
    coalesce(mp.latest_month_revenue, 0) as latest_month_revenue,
    mp.k_savage_last_active,
    coalesce(mp.k_savage_last_active_revenue, 0) as k_savage_last_active_revenue,
    coalesce(mp.k_savage_monthly_run_rate, 0) as k_savage_monthly_run_rate,
    coalesce(ba.orders, 0) as orders,
    coalesce(ba.brand_revenue, 0) as brand_revenue,
    coalesce(ba.k_savage_active_revenue, 0) as k_savage_active_revenue,
    coalesce(ba.mayfield_active_revenue, 0) as mayfield_active_revenue,
    coalesce(ba.leisure_land_active_revenue, 0) as leisure_land_active_revenue,
    coalesce(ba.k_savage_historical_revenue, 0) as k_savage_historical_revenue,
    ba.last_order_at,
    ba.last_order_number,
    ba.k_savage_last_order_at,
    exists (
      select 1 from public.contact_logs cl
      where cl.store_id = s.id
         or (cl.license_key is not null and cl.license_key = s.license_key)
    ) as has_contact_ever,
    exists (
      select 1 from public.contact_logs cl
      where (cl.store_id = s.id or (cl.license_key is not null and cl.license_key = s.license_key))
        and date_trunc('month', coalesce(cl.date_contacted, cl.saved_at::date)) = date_trunc('month', current_date)
    ) as has_contact_this_month,
    exists (
      select 1 from public.contact_logs cl
      where (cl.store_id = s.id or (cl.license_key is not null and cl.license_key = s.license_key))
        and coalesce(cl.date_contacted, cl.saved_at::date) >= date_trunc('week', current_date)::date
        and coalesce(cl.date_contacted, cl.saved_at::date) < (date_trunc('week', current_date)::date + interval '7 days')
    ) as has_contact_this_week
  from public.stores s
  left join public.store_locations l on l.store_id = s.id
  left join public.reps r on r.id = s.rep_id
  left join public.store_monthly_profile mp on mp.store_id = s.id
  left join public.store_brand_activity_120d ba on ba.store_id = s.id
),
flags as (
  select
    *,
    ((k_savage_active_revenue > 0) or (latest_month_revenue > 0)) as carries_k_savage,
    (mayfield_active_revenue > 0) as carries_mayfield,
    (leisure_land_active_revenue > 0) as carries_leisure_land,
    ((revenue_total > 0 or k_savage_historical_revenue > 0) and not ((k_savage_active_revenue > 0) or (latest_month_revenue > 0))) as k_savage_lapsed
  from base
),
recommendations as (
  select
    *,
    case
      when latitude is null or longitude is null then 'Needs location'
      when k_savage_lapsed then 'K Savage Lapsed'
      when carries_mayfield then 'Mayfield placed'
      when carries_k_savage then 'Maintain K. Savage'
      else 'Open lane'
    end as recommendation
  from flags
),
priority_inputs as (
  select
    *,
    case
      when recommendation = 'K Savage Lapsed' then greatest(k_savage_monthly_run_rate, k_savage_last_active_revenue, k_savage_historical_revenue)
      when recommendation = 'Open lane' then market_sales_last_month
      else 0
    end as priority_value
  from recommendations
),
priority_scores as (
  select
    *,
    case
      when recommendation in ('K Savage Lapsed', 'Open lane') then
        case
          when count(*) over (partition by recommendation) = 1 then 1
          else percent_rank() over (partition by recommendation order by priority_value)
        end
      else 0
    end as priority_score
  from priority_inputs
),
categorized as (
  select
    *,
    case
      when priority_score >= 0.75 then 'High'
      when priority_score >= 0.40 then 'Medium'
      when recommendation in ('K Savage Lapsed', 'Open lane') then 'Low'
      else ''
    end as priority_level
  from priority_scores
)
select
  *,
  case
    when recommendation = 'Needs location' then 'Needs location'
    when carries_k_savage then 'Carries K. Savage'
    when recommendation = 'K Savage Lapsed' then 'K Savage Lapsed - ' || priority_level || ' Priority'
    when carries_leisure_land then 'Leisure Land Placed'
    when recommendation = 'Mayfield placed' then 'Mayfield placed'
    when recommendation = 'Maintain K. Savage' then 'Maintain K. Savage'
    when recommendation = 'Open lane' then 'Open Lane - ' || priority_level || ' Priority'
    when carries_mayfield then 'Carries Mayfield'
    else 'No recent brand'
  end as map_category
from categorized;
