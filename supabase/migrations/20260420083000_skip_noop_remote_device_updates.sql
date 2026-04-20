-- Avoid no-op UPDATE churn from bridge writes where state/source do not change.
-- This prevents pointless realtime UPDATE events with "(no diff paths)".

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
    coalesce(p_patch, '{}'::jsonb),
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
  where (
    case
      when p_full then coalesce(excluded.state, '{}'::jsonb)
      else coalesce(public.remote_devices.state, '{}'::jsonb) || coalesce(excluded.state, '{}'::jsonb)
    end
  ) is distinct from public.remote_devices.state
  or excluded.last_update_source is distinct from public.remote_devices.last_update_source
  returning * into v_row;

  -- If conflict occurred but values were identical, no row is returned from
  -- the guarded UPDATE; return the existing row to keep RPC contract stable.
  if v_row is null then
    select *
      into v_row
      from public.remote_devices
     where device_id = p_device_id;
  end if;

  return v_row;
end;
$$;
