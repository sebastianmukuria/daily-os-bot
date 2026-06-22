---
title: Job Search Funnel
---

<Alert status=info>
Demo dashboard built on <b>synthetic data</b> — no real companies or outcomes. The same Evidence project runs against the live Snowflake marts locally.
</Alert>

```sql kpis
select
  count(*) as total_apps,
  count(*) filter (where furthest_stage <> 'Applied')::float / count(*) as response_rate,
  count(*) filter (where furthest_stage in ('Interviewing','Final Round','Offer')) as interviews,
  count(*) filter (where status = 'Offer') as offers
from jobsearch.applications
```

<BigValue data={kpis} value=total_apps title="Applications" />
<BigValue data={kpis} value=response_rate title="Response rate" fmt='0%' />
<BigValue data={kpis} value=interviews title="Interviews" />
<BigValue data={kpis} value=offers title="Offers" />

## The funnel

```sql funnel
with stages(stage, stage_rank) as (
  values ('Applied', 1), ('Recruiter Screen', 2), ('Interviewing', 3), ('Final Round', 4), ('Offer', 5)
),
apps as (
  select a.furthest_stage, s.stage_rank as furthest_rank
  from jobsearch.applications a
  join stages s on s.stage = a.furthest_stage
),
counts as (
  select
    s.stage,
    s.stage_rank,
    count(*) filter (where a.furthest_rank >= s.stage_rank) as reached
  from stages s
  cross join apps a
  group by s.stage, s.stage_rank
)
select
  stage,
  stage_rank,
  reached,
  round(100.0 * reached / (select count(*) from jobsearch.applications), 0) as pct_of_applied,
  round(100.0 * reached / lag(reached) over (order by stage_rank), 0) as conv_from_prev
from counts
order by stage_rank
```

_Furthest stage ever reached, from full status history._

<DataTable data={funnel} rows=all>
  <Column id=stage title="Stage" />
  <Column id=reached title="Applications reached" contentType=bar barColor=#3b82f6 />
  <Column id=pct_of_applied title="% of applied" fmt='0"%"' />
  <Column id=conv_from_prev title="Conversion from prev" fmt='0"%"' />
</DataTable>

## Activity & breakdowns

```sql over_time
select
  date_trunc('month', applied_date::date) as month,
  count(*) as applications
from jobsearch.applications
group by 1
order by 1
```

#### Applications over time

<DataTable data={over_time} rows=all>
  <Column id=month title="Month" fmt="mmm yyyy" />
  <Column id=applications title="Applications" contentType=bar barColor=#3b82f6 />
</DataTable>

```sql by_status
select status, count(*) as applications
from jobsearch.applications
group by 1
order by applications desc
```

#### Current status

<DataTable data={by_status} rows=all>
  <Column id=status title="Status" />
  <Column id=applications title="Applications" contentType=bar barColor=#60a5fa />
</DataTable>

```sql by_source
select
  source,
  count(*) as applications,
  count(*) filter (where furthest_stage <> 'Applied')::float / count(*) as response_rate
from jobsearch.applications
group by 1
order by applications desc
```

<DataTable data={by_source} rows=all>
  <Column id=source title="Source" />
  <Column id=applications title="Applications" />
  <Column id=response_rate title="Response rate" fmt='0%' contentType=colorscale />
</DataTable>
