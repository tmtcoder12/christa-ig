begin;

create extension if not exists pgtap with schema extensions;
select plan(4);

insert into public.webhook_jobs (
  id, provider, external_event_id, event_type, status, attempt_count, available_at, locked_until, locked_by
)
values
  (
    '20000000-0000-0000-0000-000000000001', 'meta', 'queued-event', 'dm-related',
    'queued', 0, now() - interval '1 minute', null, null
  ),
  (
    '20000000-0000-0000-0000-000000000002', 'meta', 'expired-lease', 'comment-related',
    'processing', 1, now() - interval '2 minutes', now() - interval '1 minute', 'old-worker'
  ),
  (
    '20000000-0000-0000-0000-000000000003', 'meta', 'active-lease', 'dm-related',
    'processing', 1, now() - interval '2 minutes', now() + interval '5 minutes', 'active-worker'
  );

set local role service_role;
select set_config('request.jwt.claim.role', 'service_role', true);

select results_eq(
  $$ select external_event_id from public.claim_webhook_jobs(10, 'test-worker', 300) order by external_event_id $$,
  $$ values ('expired-lease'::text), ('queued-event'::text) $$,
  'queued jobs and abandoned leases are claimed atomically'
);
select is(
  (select attempt_count from public.webhook_jobs where external_event_id = 'queued-event'),
  1,
  'claim increments the queued attempt count'
);
select is(
  (select attempt_count from public.webhook_jobs where external_event_id = 'expired-lease'),
  2,
  'reclaimed work increments its attempt count'
);
select is(
  (select locked_by from public.webhook_jobs where external_event_id = 'active-lease'),
  'active-worker',
  'an active lease is not stolen'
);

select * from finish();
rollback;
