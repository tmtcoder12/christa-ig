-- Supabase/Postgres schema for a multi-tenant Instagram LLM engagement app.
-- Intended to be run on a fresh database or adapted into a migration.

create extension if not exists pgcrypto;
create extension if not exists vector;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text,
  created_at timestamp with time zone not null default now()
);

create table if not exists public.businesses (
  id uuid primary key default gen_random_uuid(),
  slug text unique,
  name text,
  created_at timestamp with time zone not null default now(),
  updated_at timestamp with time zone not null default now()
);

create table if not exists public.business_users (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id) on delete cascade,
  user_id uuid not null references public.profiles(id) on delete cascade,
  role text not null check (role = any (array['owner', 'manager', 'staff'])),
  created_at timestamp with time zone not null default now(),
  unique (business_id, user_id)
);

create table if not exists public.audit_events (
  id uuid primary key default gen_random_uuid(),
  business_id uuid references public.businesses(id) on delete cascade,
  event_type text not null,
  actor text,
  details jsonb not null default '{}'::jsonb,
  created_at timestamp with time zone not null default now()
);

create table if not exists public.business_subscriptions (
  business_id uuid primary key references public.businesses(id) on delete cascade,
  stripe_customer_id text,
  stripe_subscription_id text,
  stripe_payment_link_id text,
  stripe_checkout_session_id text,
  client_reference_id text,
  stripe_price_id text,
  stripe_product_id text,
  stripe_subscription_status text not null check (
    stripe_subscription_status = any (
      array[
        'active',
        'trialing',
        'past_due',
        'canceled',
        'unpaid',
        'incomplete',
        'incomplete_expired',
        'paused'
      ]
    )
  ),
  current_period_start timestamp with time zone,
  current_period_end timestamp with time zone,
  cancel_at timestamp with time zone,
  canceled_at timestamp with time zone,
  ended_at timestamp with time zone,
  last_checkout_completed_at timestamp with time zone,
  last_synced_at timestamp with time zone not null default now(),
  created_at timestamp with time zone not null default now(),
  updated_at timestamp with time zone not null default now()
);

create table if not exists public.user_auth_events (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles(id) on delete cascade,
  event_type text not null check (event_type = any (array['login', 'logout'])),
  client_info jsonb not null default '{}'::jsonb,
  created_at timestamp with time zone not null default now()
);

create table if not exists public.instagram_accounts (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references public.businesses(id) on delete cascade,
  instagram_user_id text not null,
  username text,
  name text,
  profile_picture_url text,
  access_token_secret_ref text,
  system_prompt text not null default 'You are a helpful assistant responding to Instagram direct messages.',
  status text not null default 'connected' check (
    status = any (array['connected', 'disconnected', 'error'])
  ),
  connected_at timestamp with time zone not null default now(),
  created_at timestamp with time zone not null default now(),
  updated_at timestamp with time zone not null default now(),
  unique (id, business_id),
  unique (business_id, instagram_user_id)
);

create table if not exists public.knowledge_chunks (
  id uuid primary key default gen_random_uuid(),
  instagram_account_id uuid not null references public.instagram_accounts(id) on delete cascade,
  text text not null,
  type text,
  source_url text,
  page_path text,
  title text,
  meta_description text,
  extra_metadata jsonb not null default '{}'::jsonb,
  content_hash text,
  embedding public.vector(1536) not null,
  created_at timestamp with time zone not null default now()
);

create table if not exists public.deleted_knowledge_chunks (
  id uuid primary key default gen_random_uuid(),
  original_chunk_id text not null,
  instagram_account_id uuid not null references public.instagram_accounts(id) on delete cascade,
  deleted_by uuid not null references public.profiles(id) on delete restrict,
  deleted_at timestamp with time zone not null default now(),
  delete_reason text,
  text text not null,
  type text,
  source_url text,
  page_path text,
  title text,
  meta_description text,
  extra_metadata jsonb not null default '{}'::jsonb,
  content_hash text,
  embedding public.vector(1536) not null,
  image_url text
);

create table if not exists public.ingest_runs (
  id uuid primary key default gen_random_uuid(),
  instagram_account_id uuid not null references public.instagram_accounts(id) on delete cascade,
  model text not null,
  source_name text not null,
  total_chunks integer not null,
  embedded_chunks integer not null default 0,
  status text not null check (
    status = any (array['running', 'success', 'error'])
  ),
  error_message text,
  created_at timestamp with time zone not null default now(),
  updated_at timestamp with time zone not null default now()
);

create table if not exists public.ig_contacts (
  id uuid primary key default gen_random_uuid(),
  instagram_account_id uuid not null references public.instagram_accounts(id) on delete cascade,
  instagram_user_id text not null,
  username text,
  display_name text,
  profile_picture_url text,
  first_seen_at timestamp with time zone not null default now(),
  last_seen_at timestamp with time zone not null default now(),
  extra_metadata jsonb not null default '{}'::jsonb,
  created_at timestamp with time zone not null default now(),
  updated_at timestamp with time zone not null default now(),
  unique (id, instagram_account_id),
  unique (instagram_account_id, instagram_user_id)
);

create table if not exists public.ig_dm_sessions (
  id uuid primary key default gen_random_uuid(),
  instagram_account_id uuid not null references public.instagram_accounts(id) on delete cascade,
  contact_id uuid not null references public.ig_contacts(id) on delete cascade,
  status text not null default 'open' check (
    status = any (array['open', 'closed', 'archived'])
  ),
  last_activity_at timestamp with time zone not null default now(),
  language text,
  client_meta jsonb not null default '{}'::jsonb,
  created_at timestamp with time zone not null default now(),
  updated_at timestamp with time zone not null default now(),
  foreign key (contact_id, instagram_account_id)
    references public.ig_contacts(id, instagram_account_id)
    on delete cascade,
  unique (id, contact_id),
  unique (instagram_account_id, contact_id)
);

create table if not exists public.ig_dm_messages (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references public.ig_dm_sessions(id) on delete cascade,
  contact_id uuid not null references public.ig_contacts(id) on delete cascade,
  role text not null check (role = any (array['user', 'assistant', 'system'])),
  direction text not null check (direction = any (array['inbound', 'outbound', 'internal'])),
  content text not null,
  instagram_message_id text,
  delivery_status text not null default 'complete' check (
    delivery_status = any (
      array[
        'received',
        'queued',
        'sent',
        'delivered',
        'read',
        'complete',
        'failed',
        'error'
      ]
    )
  ),
  query_type text,
  sources jsonb,
  latency_ms integer check (latency_ms is null or latency_ms >= 0),
  model text,
  token_usage jsonb not null default '{}'::jsonb,
  error_message text,
  created_at timestamp with time zone not null default now(),
  foreign key (session_id, contact_id)
    references public.ig_dm_sessions(id, contact_id)
    on delete cascade
);

create table if not exists public.ig_dm_session_state (
  session_id uuid primary key references public.ig_dm_sessions(id) on delete cascade,
  last_response_id text,
  last_discussed_item_ids jsonb not null default '[]'::jsonb,
  last_candidate_item_ids jsonb not null default '[]'::jsonb,
  last_intent text,
  active_constraints jsonb not null default '{}'::jsonb,
  summary text,
  created_at timestamp with time zone not null default now(),
  updated_at timestamp with time zone not null default now()
);

create table if not exists public.ig_posts (
  id uuid primary key default gen_random_uuid(),
  instagram_account_id uuid not null references public.instagram_accounts(id) on delete cascade,
  instagram_media_id text not null,
  caption text,
  media_type text,
  media_url text,
  permalink text,
  posted_at timestamp with time zone,
  post_type text not null default 'regular' check (
    post_type = any (array['regular', 'promotional'])
  ),
  automation_enabled boolean not null default false,
  trigger_keywords jsonb not null default '[]'::jsonb,
  comment_reply_text text not null default 'Sent you a DM!',
  dm_prompt text,
  promotion_metadata jsonb not null default '{}'::jsonb,
  like_count integer not null default 0 check (like_count >= 0),
  comment_count integer not null default 0 check (comment_count >= 0),
  extra_metadata jsonb not null default '{}'::jsonb,
  created_at timestamp with time zone not null default now(),
  updated_at timestamp with time zone not null default now(),
  unique (id, instagram_account_id),
  unique (instagram_account_id, instagram_media_id)
);

create table if not exists public.ig_comments (
  id uuid primary key default gen_random_uuid(),
  instagram_account_id uuid not null references public.instagram_accounts(id) on delete cascade,
  post_id uuid not null references public.ig_posts(id) on delete cascade,
  contact_id uuid references public.ig_contacts(id) on delete set null,
  instagram_comment_id text not null,
  parent_comment_id uuid references public.ig_comments(id) on delete set null,
  text text not null,
  like_count integer not null default 0 check (like_count >= 0),
  hidden boolean not null default false,
  replied_to boolean not null default false,
  automation_status text not null default 'not_applicable' check (
    automation_status = any (
      array[
        'not_applicable',
        'pending',
        'sent',
        'duplicate',
        'comment_reply_failed',
        'private_reply_failed',
        'openai_failed',
        'error'
      ]
    )
  ),
  matched_keyword text,
  public_reply_comment_id text,
  private_reply_message_id text,
  automation_error text,
  created_at_ig timestamp with time zone,
  extra_metadata jsonb not null default '{}'::jsonb,
  created_at timestamp with time zone not null default now(),
  updated_at timestamp with time zone not null default now(),
  foreign key (post_id, instagram_account_id)
    references public.ig_posts(id, instagram_account_id)
    on delete cascade,
  unique (instagram_account_id, instagram_comment_id)
);

create table if not exists public.ig_promo_codes (
  id uuid primary key default gen_random_uuid(),
  instagram_account_id uuid not null references public.instagram_accounts(id) on delete cascade,
  post_id uuid not null references public.ig_posts(id) on delete cascade,
  contact_id uuid not null references public.ig_contacts(id) on delete cascade,
  comment_id uuid references public.ig_comments(id) on delete set null,
  code text not null,
  status text not null default 'issued' check (
    status = any (array['issued', 'redeemed', 'void'])
  ),
  redeemed_at timestamp with time zone,
  extra_metadata jsonb not null default '{}'::jsonb,
  created_at timestamp with time zone not null default now(),
  updated_at timestamp with time zone not null default now(),
  foreign key (post_id, instagram_account_id)
    references public.ig_posts(id, instagram_account_id)
    on delete cascade,
  foreign key (contact_id, instagram_account_id)
    references public.ig_contacts(id, instagram_account_id)
    on delete cascade,
  constraint ig_promo_codes_post_contact_key unique (post_id, contact_id),
  constraint ig_promo_codes_account_code_key unique (instagram_account_id, code)
);

create table if not exists public.ig_comment_classifications (
  id uuid primary key default gen_random_uuid(),
  comment_id uuid not null references public.ig_comments(id) on delete cascade,
  business_id uuid not null references public.businesses(id) on delete cascade,
  model text not null,
  classification jsonb not null default '{}'::jsonb,
  confidence numeric check (confidence is null or (confidence >= 0 and confidence <= 1)),
  reasoning text,
  status text not null default 'success' check (
    status = any (array['pending', 'success', 'error'])
  ),
  error_message text,
  classified_at timestamp with time zone not null default now()
);

create table if not exists public.meta_webhook_events (
  event_id text primary key,
  business_id uuid not null references public.businesses(id) on delete cascade,
  instagram_account_id uuid references public.instagram_accounts(id) on delete set null,
  event_type text not null,
  processing_status text not null default 'received' check (
    processing_status = any (array['received', 'processed', 'failed', 'ignored'])
  ),
  payload jsonb not null default '{}'::jsonb,
  error_message text,
  processed_at timestamp with time zone,
  created_at timestamp with time zone not null default now()
);

create index if not exists business_users_user_business_idx
  on public.business_users (user_id, business_id);

create index if not exists audit_events_business_created_idx
  on public.audit_events (business_id, created_at desc);

create index if not exists user_auth_events_user_created_idx
  on public.user_auth_events (user_id, created_at desc);

create index if not exists knowledge_chunks_embedding_ivfflat_idx
  on public.knowledge_chunks
  using ivfflat (embedding vector_cosine_ops)
  with (lists = 100);

create index if not exists knowledge_chunks_instagram_account_idx
  on public.knowledge_chunks (instagram_account_id);

create index if not exists knowledge_chunks_instagram_account_created_at_idx
  on public.knowledge_chunks (instagram_account_id, created_at desc);

create index if not exists knowledge_chunks_source_url_idx
  on public.knowledge_chunks (source_url);

create index if not exists deleted_knowledge_chunks_instagram_account_deleted_at_idx
  on public.deleted_knowledge_chunks (instagram_account_id, deleted_at desc);

create index if not exists deleted_knowledge_chunks_original_chunk_id_idx
  on public.deleted_knowledge_chunks (original_chunk_id);

create index if not exists deleted_knowledge_chunks_deleted_by_idx
  on public.deleted_knowledge_chunks (deleted_by);

create index if not exists ingest_runs_instagram_account_created_at_idx
  on public.ingest_runs (instagram_account_id, created_at desc);

create index if not exists instagram_accounts_business_idx
  on public.instagram_accounts (business_id);

create index if not exists ig_contacts_account_user_idx
  on public.ig_contacts (instagram_account_id, instagram_user_id);

create index if not exists ig_dm_sessions_account_contact_activity_idx
  on public.ig_dm_sessions (instagram_account_id, contact_id, last_activity_at desc);

create index if not exists ig_dm_messages_session_created_idx
  on public.ig_dm_messages (session_id, created_at desc);

create unique index if not exists ig_dm_messages_instagram_message_id_key
  on public.ig_dm_messages (instagram_message_id)
  where instagram_message_id is not null;

create index if not exists ig_posts_account_posted_idx
  on public.ig_posts (instagram_account_id, posted_at desc);

create index if not exists ig_comments_post_created_idx
  on public.ig_comments (post_id, created_at_ig desc);

create index if not exists ig_comments_account_comment_idx
  on public.ig_comments (instagram_account_id, instagram_comment_id);

create unique index if not exists ig_comments_one_automation_per_post_contact_idx
  on public.ig_comments (post_id, contact_id)
  where contact_id is not null
    and automation_status = any (
      array[
        'pending',
        'sent',
        'comment_reply_failed',
        'private_reply_failed',
        'openai_failed',
        'error'
      ]
    );

create index if not exists ig_promo_codes_account_status_idx
  on public.ig_promo_codes (instagram_account_id, status);

create index if not exists ig_promo_codes_contact_created_idx
  on public.ig_promo_codes (contact_id, created_at desc);

create index if not exists ig_promo_codes_comment_idx
  on public.ig_promo_codes (comment_id);

create index if not exists ig_comment_classifications_comment_classified_idx
  on public.ig_comment_classifications (comment_id, classified_at desc);

create index if not exists ig_comment_classifications_business_classified_idx
  on public.ig_comment_classifications (business_id, classified_at desc);

create index if not exists meta_webhook_events_business_created_idx
  on public.meta_webhook_events (business_id, created_at desc);

create index if not exists meta_webhook_events_account_created_idx
  on public.meta_webhook_events (instagram_account_id, created_at desc);

create or replace function public.match_knowledge_chunks(
  p_instagram_account_id uuid,
  p_query_embedding public.vector(1536),
  p_match_count integer default 5
)
returns table (
  id uuid,
  text text,
  type text,
  source_url text,
  page_path text,
  title text,
  meta_description text,
  extra_metadata jsonb,
  similarity double precision
)
language sql
stable
as $$
  select
    kc.id,
    kc.text,
    kc.type,
    kc.source_url,
    kc.page_path,
    kc.title,
    kc.meta_description,
    kc.extra_metadata,
    1 - (kc.embedding <=> p_query_embedding) as similarity
  from public.knowledge_chunks kc
  where kc.instagram_account_id = p_instagram_account_id
  order by kc.embedding <=> p_query_embedding
  limit greatest(coalesce(p_match_count, 5), 0)
$$;

create or replace view public.latest_ig_comment_classifications
with (security_invoker = true)
as
select distinct on (comment_id)
  id,
  comment_id,
  business_id,
  model,
  classification,
  confidence,
  reasoning,
  status,
  error_message,
  classified_at
from public.ig_comment_classifications
order by comment_id, classified_at desc;

create trigger set_businesses_updated_at
before update on public.businesses
for each row execute function public.set_updated_at();

create trigger set_business_subscriptions_updated_at
before update on public.business_subscriptions
for each row execute function public.set_updated_at();

create trigger set_ingest_runs_updated_at
before update on public.ingest_runs
for each row execute function public.set_updated_at();

create trigger set_instagram_accounts_updated_at
before update on public.instagram_accounts
for each row execute function public.set_updated_at();

create trigger set_ig_contacts_updated_at
before update on public.ig_contacts
for each row execute function public.set_updated_at();

create trigger set_ig_dm_sessions_updated_at
before update on public.ig_dm_sessions
for each row execute function public.set_updated_at();

create trigger set_ig_dm_session_state_updated_at
before update on public.ig_dm_session_state
for each row execute function public.set_updated_at();

create trigger set_ig_posts_updated_at
before update on public.ig_posts
for each row execute function public.set_updated_at();

create trigger set_ig_comments_updated_at
before update on public.ig_comments
for each row execute function public.set_updated_at();

create trigger set_ig_promo_codes_updated_at
before update on public.ig_promo_codes
for each row execute function public.set_updated_at();

create or replace function public.current_user_business_role(target_business_id uuid)
returns text
language sql
stable
security definer
set search_path = public
as $$
  select bu.role
  from public.business_users bu
  where bu.business_id = target_business_id
    and bu.user_id = auth.uid()
  limit 1
$$;

create or replace function public.user_has_business_access(target_business_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.business_users bu
    where bu.business_id = target_business_id
      and bu.user_id = auth.uid()
  )
$$;

create or replace function public.user_can_manage_business(target_business_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select coalesce(
    public.current_user_business_role(target_business_id) = any (array['owner', 'manager']),
    false
  )
$$;

create or replace function public.user_is_business_owner(target_business_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select coalesce(public.current_user_business_role(target_business_id) = 'owner', false)
$$;

create or replace function public.business_has_members(target_business_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.business_users bu
    where bu.business_id = target_business_id
  )
$$;

alter table public.profiles enable row level security;
alter table public.businesses enable row level security;
alter table public.business_users enable row level security;
alter table public.audit_events enable row level security;
alter table public.business_subscriptions enable row level security;
alter table public.knowledge_chunks enable row level security;
alter table public.deleted_knowledge_chunks enable row level security;
alter table public.ingest_runs enable row level security;
alter table public.user_auth_events enable row level security;
alter table public.instagram_accounts enable row level security;
alter table public.ig_contacts enable row level security;
alter table public.ig_dm_sessions enable row level security;
alter table public.ig_dm_messages enable row level security;
alter table public.ig_dm_session_state enable row level security;
alter table public.ig_posts enable row level security;
alter table public.ig_comments enable row level security;
alter table public.ig_promo_codes enable row level security;
alter table public.ig_comment_classifications enable row level security;
alter table public.meta_webhook_events enable row level security;

grant usage on schema public to authenticated;

grant select, insert, update on public.profiles to authenticated;
grant select, insert, update, delete on public.businesses to authenticated;
grant select, insert, update, delete on public.business_users to authenticated;
grant select on public.audit_events to authenticated;
grant select on public.business_subscriptions to authenticated;
grant select on public.knowledge_chunks to authenticated;
grant select on public.deleted_knowledge_chunks to authenticated;
grant select on public.ingest_runs to authenticated;
grant select on public.user_auth_events to authenticated;
grant select, insert, update, delete on public.instagram_accounts to authenticated;
grant select on public.ig_contacts to authenticated;
grant select on public.ig_dm_sessions to authenticated;
grant select on public.ig_dm_messages to authenticated;
grant select on public.ig_dm_session_state to authenticated;
grant select on public.ig_posts to authenticated;
grant select on public.ig_comments to authenticated;
grant select on public.ig_promo_codes to authenticated;
grant select on public.ig_comment_classifications to authenticated;
grant select on public.meta_webhook_events to authenticated;
grant select on public.latest_ig_comment_classifications to authenticated;

create policy "profiles select own"
on public.profiles for select
to authenticated
using (id = auth.uid());

create policy "profiles insert own"
on public.profiles for insert
to authenticated
with check (id = auth.uid());

create policy "profiles update own"
on public.profiles for update
to authenticated
using (id = auth.uid())
with check (id = auth.uid());

create policy "businesses select members"
on public.businesses for select
to authenticated
using (public.user_has_business_access(id));

create policy "businesses insert authenticated"
on public.businesses for insert
to authenticated
with check (auth.uid() is not null);

create policy "businesses update managers"
on public.businesses for update
to authenticated
using (public.user_can_manage_business(id))
with check (public.user_can_manage_business(id));

create policy "businesses delete owners"
on public.businesses for delete
to authenticated
using (public.user_is_business_owner(id));

create policy "business users select members"
on public.business_users for select
to authenticated
using (public.user_has_business_access(business_id));

create policy "business users insert owners"
on public.business_users for insert
to authenticated
with check (
  public.user_is_business_owner(business_id)
  or (
    user_id = auth.uid()
    and role = 'owner'
    and not public.business_has_members(business_id)
  )
);

create policy "business users update owners"
on public.business_users for update
to authenticated
using (public.user_is_business_owner(business_id))
with check (public.user_is_business_owner(business_id));

create policy "business users delete owners"
on public.business_users for delete
to authenticated
using (public.user_is_business_owner(business_id));

create policy "audit events select members"
on public.audit_events for select
to authenticated
using (
  business_id is not null
  and public.user_has_business_access(business_id)
);

create policy "business subscriptions select members"
on public.business_subscriptions for select
to authenticated
using (public.user_has_business_access(business_id));

create policy "knowledge chunks select members"
on public.knowledge_chunks for select
to authenticated
using (
  exists (
    select 1
    from public.instagram_accounts ia
    where ia.id = knowledge_chunks.instagram_account_id
      and public.user_has_business_access(ia.business_id)
  )
);

create policy "deleted knowledge chunks select members"
on public.deleted_knowledge_chunks for select
to authenticated
using (
  exists (
    select 1
    from public.instagram_accounts ia
    where ia.id = deleted_knowledge_chunks.instagram_account_id
      and public.user_has_business_access(ia.business_id)
  )
);

create policy "ingest runs select members"
on public.ingest_runs for select
to authenticated
using (
  exists (
    select 1
    from public.instagram_accounts ia
    where ia.id = ingest_runs.instagram_account_id
      and public.user_has_business_access(ia.business_id)
  )
);

create policy "user auth events select own"
on public.user_auth_events for select
to authenticated
using (user_id = auth.uid());

create policy "instagram accounts select members"
on public.instagram_accounts for select
to authenticated
using (public.user_has_business_access(business_id));

create policy "instagram accounts insert managers"
on public.instagram_accounts for insert
to authenticated
with check (public.user_can_manage_business(business_id));

create policy "instagram accounts update managers"
on public.instagram_accounts for update
to authenticated
using (public.user_can_manage_business(business_id))
with check (public.user_can_manage_business(business_id));

create policy "instagram accounts delete managers"
on public.instagram_accounts for delete
to authenticated
using (public.user_can_manage_business(business_id));

create policy "ig contacts select members"
on public.ig_contacts for select
to authenticated
using (
  exists (
    select 1
    from public.instagram_accounts ia
    where ia.id = ig_contacts.instagram_account_id
      and public.user_has_business_access(ia.business_id)
  )
);

create policy "ig dm sessions select members"
on public.ig_dm_sessions for select
to authenticated
using (
  exists (
    select 1
    from public.instagram_accounts ia
    where ia.id = ig_dm_sessions.instagram_account_id
      and public.user_has_business_access(ia.business_id)
  )
);

create policy "ig dm messages select members"
on public.ig_dm_messages for select
to authenticated
using (
  exists (
    select 1
    from public.ig_dm_sessions s
    join public.instagram_accounts ia on ia.id = s.instagram_account_id
    where s.id = ig_dm_messages.session_id
      and public.user_has_business_access(ia.business_id)
  )
);

create policy "ig dm session state select members"
on public.ig_dm_session_state for select
to authenticated
using (
  exists (
    select 1
    from public.ig_dm_sessions s
    join public.instagram_accounts ia on ia.id = s.instagram_account_id
    where s.id = ig_dm_session_state.session_id
      and public.user_has_business_access(ia.business_id)
  )
);

create policy "ig posts select members"
on public.ig_posts for select
to authenticated
using (
  exists (
    select 1
    from public.instagram_accounts ia
    where ia.id = ig_posts.instagram_account_id
      and public.user_has_business_access(ia.business_id)
  )
);

create policy "ig comments select members"
on public.ig_comments for select
to authenticated
using (
  exists (
    select 1
    from public.instagram_accounts ia
    where ia.id = ig_comments.instagram_account_id
      and public.user_has_business_access(ia.business_id)
  )
);

create policy "ig promo codes select members"
on public.ig_promo_codes for select
to authenticated
using (
  exists (
    select 1
    from public.instagram_accounts ia
    where ia.id = ig_promo_codes.instagram_account_id
      and public.user_has_business_access(ia.business_id)
  )
);

create policy "ig comment classifications select members"
on public.ig_comment_classifications for select
to authenticated
using (public.user_has_business_access(business_id));

create policy "meta webhook events select members"
on public.meta_webhook_events for select
to authenticated
using (public.user_has_business_access(business_id));
