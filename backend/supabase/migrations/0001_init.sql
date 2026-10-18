-- Overfield: accounts, farm profiles, developer keys, terms and the delivery log.
-- Run once in the Supabase SQL editor (or `supabase db push`). Every table has row-level security.
-- Design: docs/superpowers/specs/2026-10-18-product-accounts-api-design.md

create schema if not exists extensions;
create extension if not exists pgcrypto with schema extensions;   -- Supabase keeps pgcrypto here

-- ---------------------------------------------------------------- profiles (the roster)
create sequence if not exists public.roster_seq start 1;

create table if not exists public.profiles (
  id             uuid primary key references auth.users (id) on delete cascade,
  roster_id      text unique not null default ('R' || lpad(nextval('public.roster_seq')::text, 4, '0')),
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  name           text not null default '',
  phone          text unique,                                   -- E.164: digits with a leading +
  email          text,
  language       char(2) not null default 'en',
  role           text not null default 'farmer' check (role in ('farmer', 'officer', 'admin')),
  farm_id        text,                                          -- field_id in fields.geojson once the address matched
  address        text,
  lon            double precision,
  lat            double precision,
  send_time      time not null default '06:30',
  timezone       text not null default 'America/Chicago',
  channels       text[] not null default '{sms,voice}',
  sms_consent_at timestamptz,                                   -- set when the person ticked the consent box
  paused         boolean not null default false
);
comment on table public.profiles is 'One row per signed-in person. Rows with a farm_id, a phone or email, and paused = false are the morning roster.';

create index if not exists profiles_farm_id_idx on public.profiles (farm_id);
create index if not exists profiles_role_idx on public.profiles (role);

-- ---------------------------------------------------------------- helpers
create or replace function public.is_staff() returns boolean
language sql stable security definer set search_path = public, extensions as $$
  select exists (select 1 from public.profiles where id = auth.uid() and role in ('officer', 'admin'));
$$;

create or replace function public.is_admin() returns boolean
language sql stable security definer set search_path = public, extensions as $$
  select exists (select 1 from public.profiles where id = auth.uid() and role = 'admin');
$$;

create or replace function public.current_terms_version() returns text
language sql immutable as $$ select 'developer-v1'::text $$;

-- Create the profile row when a user signs up (email or phone from auth.users).
create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public, extensions as $$
begin
  insert into public.profiles (id, email, phone, name)
  values (new.id, new.email, new.phone, coalesce(new.raw_user_meta_data ->> 'name', ''))
  on conflict (id) do nothing;
  return new;
end;
$$;
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

-- Nobody promotes themselves: role and roster_id change only through an admin.
create or replace function public.protect_profile_fields() returns trigger
language plpgsql security definer set search_path = public, extensions as $$
begin
  if (new.role is distinct from old.role or new.roster_id is distinct from old.roster_id) and not public.is_admin() then
    raise exception 'role and roster_id can only be changed by an admin';
  end if;
  if new.phone is not null then
    new.phone := nullif(regexp_replace(new.phone, '[^0-9+]', '', 'g'), '');   -- digits and a leading +; the site adds the country code
  end if;
  new.updated_at := now();
  return new;
end;
$$;
drop trigger if exists profiles_protect on public.profiles;
create trigger profiles_protect before update on public.profiles
  for each row execute function public.protect_profile_fields();

-- ---------------------------------------------------------------- terms
create table if not exists public.terms_acceptances (
  id          bigserial primary key,
  user_id     uuid not null references public.profiles (id) on delete cascade,
  version     text not null,
  kind        text not null default 'developer' check (kind in ('developer', 'farmer')),
  accepted_at timestamptz not null default now(),
  user_agent  text
);
create index if not exists terms_user_idx on public.terms_acceptances (user_id, kind, version);

create or replace function public.accept_terms(p_version text, p_kind text default 'developer', p_user_agent text default null)
returns timestamptz language plpgsql security definer set search_path = public, extensions as $$
declare t timestamptz;
begin
  if auth.uid() is null then raise exception 'sign in first'; end if;
  insert into public.terms_acceptances (user_id, version, kind, user_agent)
  values (auth.uid(), p_version, p_kind, left(p_user_agent, 300)) returning accepted_at into t;
  return t;
end;
$$;

-- ---------------------------------------------------------------- developer keys
create table if not exists public.api_keys (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references public.profiles (id) on delete cascade,
  name         text not null default 'default',
  key_prefix   text not null,                       -- first 12 characters, for the dashboard
  key_hash     text not null unique,                -- sha256 of the full key; the key itself is never stored
  scopes       text[] not null default '{read}',
  daily_limit  integer not null default 1000,
  created_at   timestamptz not null default now(),
  last_used_at timestamptz,
  revoked_at   timestamptz
);
create index if not exists api_keys_user_idx on public.api_keys (user_id);

create table if not exists public.api_usage (
  id      bigserial primary key,
  key_id  uuid not null references public.api_keys (id) on delete cascade,
  at      timestamptz not null default now(),
  path    text not null,
  status  integer not null
);
create index if not exists api_usage_key_at_idx on public.api_usage (key_id, at desc);

-- Returns the new key once. Needs a sign-in and an acceptance of the current developer terms; at most 5 live keys.
create or replace function public.issue_api_key(p_name text default 'default')
returns text language plpgsql security definer set search_path = public, extensions as $$
declare k text; live integer;
begin
  if auth.uid() is null then raise exception 'sign in first'; end if;
  if not exists (select 1 from public.terms_acceptances
                 where user_id = auth.uid() and kind = 'developer' and version = public.current_terms_version()) then
    raise exception 'accept the developer terms (%) first', public.current_terms_version();
  end if;
  select count(*) into live from public.api_keys where user_id = auth.uid() and revoked_at is null;
  if live >= 5 then raise exception 'at most 5 live keys; revoke one first'; end if;
  k := 'ovf_' || encode(extensions.gen_random_bytes(20), 'hex');
  insert into public.api_keys (user_id, name, key_prefix, key_hash)
  values (auth.uid(), left(coalesce(nullif(trim(p_name), ''), 'default'), 60), left(k, 12), encode(extensions.digest(k, 'sha256'), 'hex'));
  return k;
end;
$$;

create or replace function public.revoke_api_key(p_id uuid) returns boolean
language plpgsql security definer set search_path = public, extensions as $$
begin
  update public.api_keys set revoked_at = now()
  where id = p_id and revoked_at is null and (user_id = auth.uid() or public.is_admin());
  return found;
end;
$$;

-- For the edge function (service role): who owns this key and how much has it used today.
create or replace function public.verify_api_key(p_key text)
returns table (key_id uuid, user_id uuid, daily_limit integer, used_today bigint)
language sql stable security definer set search_path = public, extensions as $$
  select k.id, k.user_id, k.daily_limit,
         (select count(*) from public.api_usage u where u.key_id = k.id and u.at > now() - interval '24 hours')
  from public.api_keys k
  where k.key_hash = encode(extensions.digest(p_key, 'sha256'), 'hex') and k.revoked_at is null;
$$;

create or replace function public.log_api_use(p_key_id uuid, p_path text, p_status integer) returns void
language sql security definer set search_path = public, extensions as $$
  insert into public.api_usage (key_id, path, status) values (p_key_id, left(p_path, 200), p_status);
  update public.api_keys set last_used_at = now() where id = p_key_id;
$$;

-- ---------------------------------------------------------------- deliveries (the morning log)
create table if not exists public.deliveries (
  id         bigserial primary key,
  run_date   date not null,
  roster_id  text not null,
  farm_id    text,
  role       text not null,
  channel    text not null,
  status     text not null,
  detail     text,
  dry_run    boolean not null default true,
  sent_at    timestamptz,
  created_at timestamptz not null default now()
);
create index if not exists deliveries_run_idx on public.deliveries (run_date desc, roster_id);

-- ---------------------------------------------------------------- account removal
create or replace function public.delete_my_account() returns void
language plpgsql security definer set search_path = public, extensions as $$
begin
  if auth.uid() is null then raise exception 'sign in first'; end if;
  delete from auth.users where id = auth.uid();         -- profiles, keys, terms and usage go by cascade
end;
$$;

-- ---------------------------------------------------------------- admin views
create or replace view public.signups_per_day with (security_invoker = true) as
  select date_trunc('day', created_at)::date as day, count(*) as signups
  from public.profiles group by 1 order by 1;

-- ---------------------------------------------------------------- row-level security
alter table public.profiles          enable row level security;
alter table public.terms_acceptances enable row level security;
alter table public.api_keys          enable row level security;
alter table public.api_usage         enable row level security;
alter table public.deliveries        enable row level security;

drop policy if exists profiles_own_read   on public.profiles;
drop policy if exists profiles_own_update on public.profiles;
drop policy if exists profiles_own_insert on public.profiles;
drop policy if exists profiles_staff_read on public.profiles;
drop policy if exists profiles_admin_update on public.profiles;
create policy profiles_own_read     on public.profiles for select using (id = auth.uid());
create policy profiles_own_update   on public.profiles for update using (id = auth.uid()) with check (id = auth.uid());
create policy profiles_own_insert   on public.profiles for insert with check (id = auth.uid());
create policy profiles_staff_read   on public.profiles for select using (public.is_staff());
create policy profiles_admin_update on public.profiles for update using (public.is_admin());

drop policy if exists terms_own_read on public.terms_acceptances;
drop policy if exists terms_admin_read on public.terms_acceptances;
create policy terms_own_read   on public.terms_acceptances for select using (user_id = auth.uid());
create policy terms_admin_read on public.terms_acceptances for select using (public.is_admin());

drop policy if exists keys_own_read on public.api_keys;
drop policy if exists keys_admin_read on public.api_keys;
create policy keys_own_read   on public.api_keys for select using (user_id = auth.uid());
create policy keys_admin_read on public.api_keys for select using (public.is_admin());
-- inserts and revocations go through issue_api_key() and revoke_api_key(); the hash column is never selected by the site
revoke select (key_hash) on public.api_keys from authenticated, anon;

drop policy if exists usage_own_read on public.api_usage;
drop policy if exists usage_admin_read on public.api_usage;
create policy usage_own_read   on public.api_usage for select using (exists (select 1 from public.api_keys k where k.id = key_id and k.user_id = auth.uid()));
create policy usage_admin_read on public.api_usage for select using (public.is_admin());

drop policy if exists deliveries_own_read on public.deliveries;
drop policy if exists deliveries_staff_read on public.deliveries;
create policy deliveries_own_read   on public.deliveries for select using (roster_id = (select p.roster_id from public.profiles p where p.id = auth.uid()));
create policy deliveries_staff_read on public.deliveries for select using (public.is_staff());
-- deliveries are written only by the Python queue with the service role, which bypasses RLS

grant usage on schema public to anon, authenticated;
grant select, update on public.profiles to authenticated;
grant select on public.terms_acceptances, public.api_keys, public.api_usage, public.deliveries, public.signups_per_day to authenticated;
grant execute on function public.accept_terms(text, text, text), public.issue_api_key(text), public.revoke_api_key(uuid), public.delete_my_account() to authenticated;
revoke execute on function public.verify_api_key(text), public.log_api_use(uuid, text, integer) from anon, authenticated;
