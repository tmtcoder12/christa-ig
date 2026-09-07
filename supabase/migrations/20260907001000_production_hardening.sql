-- Additive production-hardening migration.
-- Safe for existing installations: no existing table or column is removed.

create table if not exists public.webhook_jobs (
  id uuid primary key default gen_random_uuid(),
  provider text not null check (provider = any (array['meta'])),
  external_event_id text not null,
  event_type text not null check (event_type = any (array['dm-related', 'comment-related'])),
  account_external_id text,
  payload jsonb not null default '{}'::jsonb,
  status text not null default 'queued' check (
    status = any (array['queued', 'processing', 'succeeded', 'ignored', 'failed'])
  ),
  attempt_count integer not null default 0 check (attempt_count >= 0),
  available_at timestamp with time zone not null default now(),
  locked_at timestamp with time zone,
  locked_until timestamp with time zone,
  locked_by text,
  error_code text,
  error_message text,
  request_id text,
  completed_at timestamp with time zone,
  created_at timestamp with time zone not null default now(),
  updated_at timestamp with time zone not null default now(),
  constraint webhook_jobs_provider_event_key unique (provider, external_event_id)
);

alter table public.ig_promotion_setups
  add column if not exists next_poll_at timestamp with time zone default now(),
  add column if not exists locked_at timestamp with time zone,
  add column if not exists locked_until timestamp with time zone,
  add column if not exists locked_by text,
  add column if not exists worker_attempt_count integer not null default 0,
  add column if not exists last_worker_error text;

create index if not exists webhook_jobs_claim_idx
  on public.webhook_jobs (status, available_at, locked_until, created_at);

create index if not exists webhook_jobs_completed_idx
  on public.webhook_jobs (status, completed_at)
  where completed_at is not null;

create index if not exists ig_promotion_setups_worker_claim_idx
  on public.ig_promotion_setups (status, next_poll_at, locked_until)
  where status = any (array['pending', 'polling']);

drop trigger if exists set_webhook_jobs_updated_at on public.webhook_jobs;
create trigger set_webhook_jobs_updated_at
before update on public.webhook_jobs
for each row execute function public.set_updated_at();

alter table public.webhook_jobs enable row level security;
revoke all on public.webhook_jobs from public, anon, authenticated;
grant select, insert, update, delete on public.webhook_jobs to service_role;

create or replace function public.claim_webhook_jobs(
  p_batch_size integer,
  p_worker_id text,
  p_lease_seconds integer default 300
)
returns setof public.webhook_jobs
language plpgsql
security definer
set search_path = public
as $$
begin
  if auth.role() <> 'service_role' then
    raise exception 'service_role required';
  end if;

  return query
  with candidates as (
    select job.id
    from public.webhook_jobs as job
    where (
      job.status = 'queued'
      and job.available_at <= now()
    ) or (
      job.status = 'processing'
      and job.locked_until < now()
    )
    order by job.available_at, job.created_at
    for update skip locked
    limit greatest(least(coalesce(p_batch_size, 1), 100), 1)
  )
  update public.webhook_jobs as job
  set
    status = 'processing',
    attempt_count = job.attempt_count + 1,
    locked_at = now(),
    locked_until = now() + make_interval(secs => greatest(coalesce(p_lease_seconds, 300), 1)),
    locked_by = p_worker_id,
    error_code = null,
    error_message = null
  from candidates
  where job.id = candidates.id
  returning job.*;
end;
$$;

revoke all on function public.claim_webhook_jobs(integer, text, integer) from public, anon, authenticated;
grant execute on function public.claim_webhook_jobs(integer, text, integer) to service_role;

create or replace function public.claim_promotion_setups(
  p_batch_size integer,
  p_worker_id text,
  p_lease_seconds integer default 300
)
returns setof public.ig_promotion_setups
language plpgsql
security definer
set search_path = public
as $$
begin
  if auth.role() <> 'service_role' then
    raise exception 'service_role required';
  end if;

  return query
  with candidates as (
    select setup.id
    from public.ig_promotion_setups as setup
    where setup.status = any (array['pending', 'polling'])
      and coalesce(setup.next_poll_at, now()) <= now()
      and (setup.locked_until is null or setup.locked_until < now())
    order by coalesce(setup.next_poll_at, setup.created_at), setup.created_at
    for update skip locked
    limit greatest(least(coalesce(p_batch_size, 1), 20), 1)
  )
  update public.ig_promotion_setups as setup
  set
    status = 'polling',
    worker_attempt_count = setup.worker_attempt_count + 1,
    locked_at = now(),
    locked_until = now() + make_interval(secs => greatest(coalesce(p_lease_seconds, 300), 1)),
    locked_by = p_worker_id,
    last_worker_error = null,
    poll_started_at = coalesce(setup.poll_started_at, now())
  from candidates
  where setup.id = candidates.id
  returning setup.*;
end;
$$;

revoke all on function public.claim_promotion_setups(integer, text, integer) from public, anon, authenticated;
grant execute on function public.claim_promotion_setups(integer, text, integer) to service_role;
