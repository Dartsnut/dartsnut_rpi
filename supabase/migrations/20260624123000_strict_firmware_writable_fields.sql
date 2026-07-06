-- Enforce the current firmware writable-field contract for v2 patches.
-- Legacy apply_remote_device_patch remains unchanged for older firmware.

create or replace function public.merge_games_array_by_id(
  p_existing jsonb,
  p_incoming jsonb
)
returns jsonb
language plpgsql
immutable
as $$
declare
  v_existing jsonb := coalesce(p_existing, '[]'::jsonb);
  v_incoming jsonb := coalesce(p_incoming, '[]'::jsonb);
  v_result jsonb := '[]'::jsonb;
  v_item jsonb;
  v_id text;
  v_existing_item jsonb;
  v_patch jsonb;
  v_i int;
begin
  if jsonb_typeof(v_existing) <> 'array' then
    v_existing := '[]'::jsonb;
  end if;
  if jsonb_typeof(v_incoming) <> 'array' or jsonb_array_length(v_incoming) = 0 then
    return v_existing;
  end if;

  for v_i in 0 .. jsonb_array_length(v_existing) - 1 loop
    v_existing_item := v_existing -> v_i;
    v_id := nullif(trim(both from coalesce(v_existing_item ->> 'id', '')), '');
    v_patch := null;

    if v_id is not null then
      for v_item in select * from jsonb_array_elements(v_incoming) loop
        if coalesce(v_item ->> 'id', '') = v_id then
          v_patch := '{}'::jsonb;
          if v_item ? 'status' then
            v_patch := v_patch || jsonb_build_object('status', v_item -> 'status');
          end if;
          if v_item ? 'version' then
            v_patch := v_patch || jsonb_build_object('version', v_item -> 'version');
          end if;
          exit;
        end if;
      end loop;
    end if;

    v_result := v_result || jsonb_build_array(v_existing_item || coalesce(v_patch, '{}'::jsonb));
  end loop;

  return v_result;
end;
$$;

create or replace function public.sanitize_firmware_device_patch(
  p_patch jsonb,
  p_source text default null
)
returns jsonb
language plpgsql
immutable
as $$
declare
  v_patch jsonb := coalesce(p_patch, '{}'::jsonb);
  v_result jsonb := '{}'::jsonb;
  v_device_info jsonb := '{}'::jsonb;
  v_firmware jsonb := '{}'::jsonb;
  v_source text := coalesce(nullif(trim(p_source), ''), '');
begin
  if jsonb_typeof(v_patch) <> 'object' then
    return '{}'::jsonb;
  end if;

  if v_patch ? 'ssid' then
    v_result := v_result || jsonb_build_object('ssid', v_patch -> 'ssid');
  end if;
  if v_patch ? 'ip_address' then
    v_result := v_result || jsonb_build_object('ip_address', v_patch -> 'ip_address');
  end if;
  if v_patch ? 'device_updated_at' then
    v_result := v_result || jsonb_build_object('device_updated_at', v_patch -> 'device_updated_at');
  end if;
  if v_patch ? 'pages' then
    v_result := v_result || jsonb_build_object('pages', v_patch -> 'pages');
  end if;
  if v_patch ? 'pages_updated_at' then
    v_result := v_result || jsonb_build_object('pages_updated_at', v_patch -> 'pages_updated_at');
  end if;
  if v_patch ? 'volume' then
    v_result := v_result || jsonb_build_object('volume', v_patch -> 'volume');
  end if;
  if v_patch ? 'brightness' then
    v_result := v_result || jsonb_build_object('brightness', v_patch -> 'brightness');
  end if;
  if v_patch ? 'bluetooth' then
    v_result := v_result || jsonb_build_object('bluetooth', v_patch -> 'bluetooth');
  end if;
  if v_source = 'supabase_bridge_init' and v_patch ? 'dim_window' then
    v_result := v_result || jsonb_build_object('dim_window', v_patch -> 'dim_window');
  end if;

  if jsonb_typeof(v_patch -> 'device_info') = 'object' then
    if (v_patch -> 'device_info') ? 'id' then
      v_device_info := v_device_info || jsonb_build_object('id', v_patch -> 'device_info' -> 'id');
    end if;
    if (v_patch -> 'device_info') ? 'sn' then
      v_device_info := v_device_info || jsonb_build_object('sn', v_patch -> 'device_info' -> 'sn');
    end if;
    if (v_patch -> 'device_info') ? 'model' then
      v_device_info := v_device_info || jsonb_build_object('model', v_patch -> 'device_info' -> 'model');
    end if;
    if (v_patch -> 'device_info') ? 'hardware_version' then
      v_device_info := v_device_info || jsonb_build_object('hardware_version', v_patch -> 'device_info' -> 'hardware_version');
    end if;
    if v_device_info <> '{}'::jsonb then
      v_result := v_result || jsonb_build_object('device_info', v_device_info);
    end if;
  end if;

  if jsonb_typeof(v_patch -> 'firmware') = 'object' then
    if (v_patch -> 'firmware') ? 'version' then
      v_firmware := v_firmware || jsonb_build_object('version', v_patch -> 'firmware' -> 'version');
    end if;
    if (v_patch -> 'firmware') ? 'update' then
      v_firmware := v_firmware || jsonb_build_object('update', v_patch -> 'firmware' -> 'update');
    end if;
    if v_firmware <> '{}'::jsonb then
      v_result := v_result || jsonb_build_object('firmware', v_firmware);
    end if;
  end if;

  return v_result;
end;
$$;

create or replace function public.apply_remote_device_patch_v2(
  p_device_id text,
  p_patch jsonb,
  p_full boolean default false,
  p_source text default 'bridge'
)
returns public.remote_devices
language plpgsql
set search_path = public
as $$
declare
  v_row public.remote_devices;
  v_existing_state jsonb;
  v_merged_state jsonb;
  v_patch jsonb := coalesce(p_patch, '{}'::jsonb);
  v_sanitized_patch jsonb;
begin
  select state
    into v_existing_state
    from public.remote_devices
   where device_id = p_device_id;

  if p_full then
    v_merged_state := v_patch;
  else
    if v_existing_state is null then
      return null;
    end if;

    v_sanitized_patch := public.sanitize_firmware_device_patch(v_patch, p_source);
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
    coalesce(nullif(trim(p_source), ''), 'bridge')
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

  return v_row;
end;
$$;

grant execute on function public.sanitize_firmware_device_patch(jsonb, text) to anon, authenticated, service_role;
