-- Remote command slot per device.
-- The Rust bridge owns Supabase transport; Python workers only receive commands
-- over a local Unix socket and return completion metadata.

create table if not exists public.remote_device_commands (
  device_id text primary key,
  command text not null default '',
  status_code integer,
  log_filename text not null default '',
  updated_at timestamptz not null default timezone('utc'::text, now()),
  last_update_source text not null default '',
  constraint remote_device_commands_device_id_not_empty check (char_length(trim(device_id)) > 0)
);

create index if not exists remote_device_commands_updated_at_idx
  on public.remote_device_commands (updated_at desc);

create or replace function public.touch_remote_device_commands_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = timezone('utc'::text, now());
  return new;
end;
$$;

drop trigger if exists remote_device_commands_touch_updated_at
  on public.remote_device_commands;
create trigger remote_device_commands_touch_updated_at
  before update on public.remote_device_commands
  for each row
  execute function public.touch_remote_device_commands_updated_at();

grant usage on schema public to anon, authenticated, service_role;
grant select, insert, update on table public.remote_device_commands to anon, authenticated, service_role;

alter table public.remote_device_commands enable row level security;

drop policy if exists remote_device_commands_select_policy on public.remote_device_commands;
create policy remote_device_commands_select_policy
  on public.remote_device_commands
  for select
  using (true);

drop policy if exists remote_device_commands_insert_policy on public.remote_device_commands;
create policy remote_device_commands_insert_policy
  on public.remote_device_commands
  for insert
  with check (true);

drop policy if exists remote_device_commands_update_policy on public.remote_device_commands;
create policy remote_device_commands_update_policy
  on public.remote_device_commands
  for update
  using (true)
  with check (true);

alter publication supabase_realtime add table public.remote_device_commands;
alter table public.remote_device_commands replica identity full;
