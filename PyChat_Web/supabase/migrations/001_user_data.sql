-- One JSON document per authenticated user. The API validates the caller's
-- Supabase Auth access token before using the server-side service-role key.
create table if not exists public.user_data (
  user_id uuid primary key references auth.users(id) on delete cascade,
  data jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

alter table public.user_data enable row level security;

-- The browser currently talks to the application API, not this table directly.
-- These policies also protect the table if direct authenticated access is ever
-- enabled by a future client version.
drop policy if exists "Users can read own PyChat data" on public.user_data;
create policy "Users can read own PyChat data"
  on public.user_data for select to authenticated
  using (auth.uid() = user_id);

drop policy if exists "Users can insert own PyChat data" on public.user_data;
create policy "Users can insert own PyChat data"
  on public.user_data for insert to authenticated
  with check (auth.uid() = user_id);

drop policy if exists "Users can update own PyChat data" on public.user_data;
create policy "Users can update own PyChat data"
  on public.user_data for update to authenticated
  using (auth.uid() = user_id) with check (auth.uid() = user_id);

create index if not exists user_data_updated_at_idx on public.user_data(updated_at);

create or replace function public.set_pychat_user_data_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists pychat_user_data_updated_at on public.user_data;
create trigger pychat_user_data_updated_at
before update on public.user_data
for each row execute function public.set_pychat_user_data_updated_at();
