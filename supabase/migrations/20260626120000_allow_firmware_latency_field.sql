-- Allow firmware heartbeats to publish latest known REST latency.

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
  if v_patch ? 'latency' then
    v_result := v_result || jsonb_build_object('latency', v_patch -> 'latency');
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

grant execute on function public.sanitize_firmware_device_patch(jsonb, text) to anon, authenticated, service_role;
