begin;

create extension if not exists pgtap with schema extensions;
select plan(9);

insert into auth.users (id, email)
values
  ('00000000-0000-0000-0000-000000000001', 'owner@example.test'),
  ('00000000-0000-0000-0000-000000000002', 'manager@example.test'),
  ('00000000-0000-0000-0000-000000000003', 'staff@example.test'),
  ('00000000-0000-0000-0000-000000000004', 'outsider@example.test');

insert into public.profiles (id, email)
select id, email from auth.users where email like '%@example.test';

insert into public.businesses (id, name)
values
  ('10000000-0000-0000-0000-000000000001', 'Visible Business'),
  ('10000000-0000-0000-0000-000000000002', 'Other Business');

insert into public.business_users (business_id, user_id, role)
values
  ('10000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', 'owner'),
  ('10000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000002', 'manager'),
  ('10000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000003', 'staff');

set local role authenticated;

select set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000001', true);
select ok(public.user_has_business_access('10000000-0000-0000-0000-000000000001'), 'owner has business access');
select ok(public.user_can_manage_business('10000000-0000-0000-0000-000000000001'), 'owner can manage');
select ok(public.user_is_business_owner('10000000-0000-0000-0000-000000000001'), 'owner is recognized');

select set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000002', true);
select ok(public.user_can_manage_business('10000000-0000-0000-0000-000000000001'), 'manager can manage');
select isnt(public.current_user_business_role('10000000-0000-0000-0000-000000000001'), 'owner', 'manager is not owner');

select set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000003', true);
select ok(public.user_has_business_access('10000000-0000-0000-0000-000000000001'), 'staff has business access');
select isnt(public.user_can_manage_business('10000000-0000-0000-0000-000000000001'), true, 'staff cannot manage');

select set_config('request.jwt.claim.sub', '00000000-0000-0000-0000-000000000004', true);
select is((select count(*) from public.businesses), 0::bigint, 'cross-business rows are hidden');
select isnt(has_table_privilege('authenticated', 'public.webhook_jobs', 'select'), true, 'queue is hidden from browser roles');

select * from finish();
rollback;
