-- Remote sync backing schema for Dartsnut.
-- Stores one state document per device and supports partial JSONB patch merges.

create table if not exists public.remote_devices (
  device_id text primary key,
  state jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc'::text, now()),
  updated_at timestamptz not null default timezone('utc'::text, now()),
  last_update_source text not null default 'bridge',
  constraint remote_devices_device_id_not_empty check (char_length(trim(device_id)) > 0)
);

create index if not exists remote_devices_updated_at_idx
  on public.remote_devices (updated_at desc);

create index if not exists remote_devices_state_gin_idx
  on public.remote_devices
  using gin (state jsonb_path_ops);

create or replace function public.apply_remote_device_patch(
  p_device_id text,
  p_patch jsonb,
  p_full boolean default false,
  p_source text default 'bridge'
)
returns public.remote_devices
language plpgsql
as $$
declare
  v_row public.remote_devices;
begin
  insert into public.remote_devices (device_id, state, last_update_source)
  values (
    p_device_id,
    case
      when p_full then coalesce(p_patch, '{}'::jsonb)
      else coalesce(p_patch, '{}'::jsonb)
    end,
    coalesce(nullif(trim(p_source), ''), 'bridge')
  )
  on conflict (device_id)
  do update set
    state = case
      when p_full then coalesce(excluded.state, '{}'::jsonb)
      else coalesce(public.remote_devices.state, '{}'::jsonb) || coalesce(excluded.state, '{}'::jsonb)
    end,
    updated_at = timezone('utc'::text, now()),
    last_update_source = excluded.last_update_source
  returning * into v_row;

  return v_row;
end;
$$;

grant usage on schema public to anon, authenticated, service_role;
grant select, insert, update on table public.remote_devices to anon, authenticated, service_role;
grant execute on function public.apply_remote_device_patch(text, jsonb, boolean, text) to anon, authenticated, service_role;

alter table public.remote_devices enable row level security;

drop policy if exists remote_devices_select_policy on public.remote_devices;
create policy remote_devices_select_policy
  on public.remote_devices
  for select
  using (true);

drop policy if exists remote_devices_insert_policy on public.remote_devices;
create policy remote_devices_insert_policy
  on public.remote_devices
  for insert
  with check (true);

drop policy if exists remote_devices_update_policy on public.remote_devices;
create policy remote_devices_update_policy
  on public.remote_devices
  for update
  using (true)
  with check (true);

alter publication supabase_realtime add table public.remote_devices;
alter table public.remote_devices replica identity full;
