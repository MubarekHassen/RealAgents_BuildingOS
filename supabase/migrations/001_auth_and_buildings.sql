-- Migration 001: Auth profiles and buildings sync
-- Adds user_profiles (extends auth.users) and buildings tables with RLS.
-- Safe to run multiple times (idempotent where possible).

-- ============================================================
-- user_profiles: extends supabase auth.users with app metadata
-- ============================================================
create table if not exists public.user_profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  email text not null,
  name text,
  company text,
  roles jsonb not null default '[]'::jsonb,
  preferences jsonb not null default '{}'::jsonb,
  onboarding_json jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

drop trigger if exists set_user_profiles_updated_at on public.user_profiles;
create trigger set_user_profiles_updated_at
before update on public.user_profiles
for each row execute function public.set_updated_at();

alter table public.user_profiles enable row level security;

drop policy if exists "user_profiles_self_select" on public.user_profiles;
create policy "user_profiles_self_select" on public.user_profiles
  for select using (auth.uid() = id);

drop policy if exists "user_profiles_self_insert" on public.user_profiles;
create policy "user_profiles_self_insert" on public.user_profiles
  for insert with check (auth.uid() = id);

drop policy if exists "user_profiles_self_update" on public.user_profiles;
create policy "user_profiles_self_update" on public.user_profiles
  for update using (auth.uid() = id) with check (auth.uid() = id);

-- Auto-create a profile row when a new auth user is created.
create or replace function public.handle_new_auth_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.user_profiles (id, email, name, company)
  values (
    new.id,
    new.email,
    coalesce(new.raw_user_meta_data->>'name', ''),
    coalesce(new.raw_user_meta_data->>'company', '')
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
after insert on auth.users
for each row execute function public.handle_new_auth_user();

-- ============================================================
-- buildings: one row per building, owned by a user
-- JSONB blobs mirror the client-side bos_bp_{bid} / bos_eq_{bid} /
-- bos_captures_{bid} / bos_dm_* stores. Keeping as JSONB for phase 1
-- so we can ship cross-device sync without fragmenting the schema yet.
-- ============================================================
create table if not exists public.buildings (
  id text primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  name text not null,
  address text,
  use_type text,
  profile_json jsonb not null default '{}'::jsonb,
  equipment_json jsonb not null default '[]'::jsonb,
  captures_json jsonb not null default '[]'::jsonb,
  maintenance_json jsonb not null default '{}'::jsonb,
  units_json jsonb not null default '[]'::jsonb,
  extra_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create index if not exists buildings_owner_id_idx on public.buildings (owner_id);

drop trigger if exists set_buildings_updated_at on public.buildings;
create trigger set_buildings_updated_at
before update on public.buildings
for each row execute function public.set_updated_at();

alter table public.buildings enable row level security;

drop policy if exists "buildings_owner_select" on public.buildings;
create policy "buildings_owner_select" on public.buildings
  for select using (auth.uid() = owner_id);

drop policy if exists "buildings_owner_insert" on public.buildings;
create policy "buildings_owner_insert" on public.buildings
  for insert with check (auth.uid() = owner_id);

drop policy if exists "buildings_owner_update" on public.buildings;
create policy "buildings_owner_update" on public.buildings
  for update using (auth.uid() = owner_id) with check (auth.uid() = owner_id);

drop policy if exists "buildings_owner_delete" on public.buildings;
create policy "buildings_owner_delete" on public.buildings
  for delete using (auth.uid() = owner_id);
