-- Track rough lifetime online time from successful bridge RPC activity.

create table if not exists public.device_uptime (
  device_id text primary key,
  online_seconds bigint not null default 0,
  last_seen_at timestamptz,
  updated_at timestamptz not null default timezone('utc'::text, now()),
  constraint device_uptime_device_id_not_empty check (char_length(trim(device_id)) > 0),
  constraint device_uptime_online_seconds_nonnegative check (online_seconds >= 0)
);

grant usage on schema public to anon, authenticated, service_role;
grant select on table public.device_uptime to anon, authenticated, service_role;
revoke insert, update, delete, truncate, references, trigger
  on table public.device_uptime
  from public, anon, authenticated, service_role;

alter table public.device_uptime enable row level security;

drop policy if exists device_uptime_select_policy on public.device_uptime;
create policy device_uptime_select_policy
  on public.device_uptime
  for select
  using (true);

drop policy if exists device_uptime_insert_policy on public.device_uptime;
drop policy if exists device_uptime_update_policy on public.device_uptime;

create or replace function public.record_device_presence(
  p_device_id text,
  p_seen_at timestamptz
)
returns void
language plpgsql
security definer
set search_path = public, pg_catalog
as $$
declare
  v_seen_at timestamptz := coalesce(p_seen_at, now());
begin
  if nullif(trim(p_device_id), '') is null then
    raise exception 'device_id must not be empty';
  end if;

  insert into public.device_uptime (device_id, online_seconds, last_seen_at, updated_at)
  values (p_device_id, 0, v_seen_at, v_seen_at)
  on conflict (device_id) do update set
    online_seconds = device_uptime.online_seconds +
      case
        when device_uptime.last_seen_at is not null
          and v_seen_at > device_uptime.last_seen_at
          and v_seen_at - device_uptime.last_seen_at <= interval '90 seconds'
        then round(extract(epoch from (v_seen_at - device_uptime.last_seen_at)))::bigint
        else 0
      end,
    last_seen_at = v_seen_at,
    updated_at = v_seen_at;
end;
$$;

-- Presence is an internal side effect of the bridge RPC, never a public write API.
revoke all on function public.record_device_presence(text, timestamptz)
  from public, anon, authenticated, service_role;

create or replace function public.apply_remote_device_patch_v2(
  p_device_id text,
  p_patch jsonb,
  p_full boolean default false,
  p_source text default 'bridge'
)
returns public.remote_devices
language plpgsql
security definer
set search_path = public, pg_catalog
as $$
declare
  v_row public.remote_devices;
  v_existing_state jsonb;
  v_merged_state jsonb;
  v_patch jsonb := coalesce(p_patch, '{}'::jsonb);
  v_sanitized_patch jsonb;
  v_source text := coalesce(nullif(trim(p_source), ''), 'bridge');
  v_now timestamptz := now();
  v_is_bridge_source boolean;
begin
  v_is_bridge_source := v_source in (
    'supabase_bridge',
    'supabase_bridge_init',
    'supabase_bridge_heartbeat'
  );

  select state
    into v_existing_state
    from public.remote_devices
   where device_id = p_device_id;

  if p_full then
    v_merged_state := v_patch;
  else
    if v_existing_state is null then
      -- Preserve v2's partial-update/no-row behavior. The successful RPC still
      -- proves bridge reachability, so account it as presence below.
      if v_is_bridge_source then
        perform public.record_device_presence(p_device_id, v_now);
      end if;
      return null;
    end if;

    v_sanitized_patch := public.sanitize_firmware_device_patch(v_patch, v_source);
    v_merged_state := coalesce(v_existing_state, '{}'::jsonb) || v_sanitized_patch;
    if v_sanitized_patch ? 'device_info' then
      v_merged_state := jsonb_set(
        v_merged_state,
        '{device_info}',
        coalesce(coalesce(v_existing_state, '{}'::jsonb) -> 'device_info', '{}'::jsonb)
          || (v_sanitized_patch -> 'device_info'),
        true
      );
    end if;
    if v_sanitized_patch ? 'firmware' then
      v_merged_state := jsonb_set(
        v_merged_state,
        '{firmware}',
        coalesce(coalesce(v_existing_state, '{}'::jsonb) -> 'firmware', '{}'::jsonb)
          || (v_sanitized_patch -> 'firmware'),
        true
      );
    end if;
    if v_patch ? 'games' then
      v_merged_state := jsonb_set(
        v_merged_state,
        '{games}',
        public.merge_games_array_by_id(
          coalesce(v_existing_state, '{}'::jsonb) -> 'games',
          v_patch -> 'games'
        ),
        true
      );
    end if;
  end if;

  insert into public.remote_devices (device_id, state, last_update_source)
  values (
    p_device_id,
    v_merged_state,
    v_source
  )
  on conflict (device_id)
  do update set
    state = excluded.state,
    updated_at = timezone('utc'::text, now()),
    last_update_source = excluded.last_update_source
  where excluded.state is distinct from public.remote_devices.state
     or excluded.last_update_source is distinct from public.remote_devices.last_update_source
  returning * into v_row;

  if v_row is null then
    select *
      into v_row
      from public.remote_devices
     where device_id = p_device_id;
  end if;

  if v_is_bridge_source then
    perform public.record_device_presence(p_device_id, v_now);
  end if;

  return v_row;
end;
$$;

revoke all on function public.apply_remote_device_patch_v2(text, jsonb, boolean, text)
  from public;
grant execute on function public.apply_remote_device_patch_v2(text, jsonb, boolean, text)
  to anon, authenticated, service_role;
