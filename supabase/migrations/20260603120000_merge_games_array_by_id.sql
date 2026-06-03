-- Add v2 sync RPCs for new firmware without redefining the legacy
-- apply_remote_device_patch RPC used by older machines. Stored
-- remote_devices.state JSON schema is unchanged.

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
  v_found boolean;
  v_i int;
  v_j int;
begin
  if jsonb_typeof(v_existing) <> 'array' then
    v_existing := '[]'::jsonb;
  end if;
  if jsonb_typeof(v_incoming) <> 'array' or jsonb_array_length(v_incoming) = 0 then
    return v_existing;
  end if;

  v_result := v_existing;

  for v_i in 0 .. jsonb_array_length(v_incoming) - 1 loop
    v_item := v_incoming -> v_i;
    v_id := nullif(trim(both from coalesce(v_item ->> 'id', '')), '');
    if v_id is null then
      continue;
    end if;
    v_found := false;
    for v_j in 0 .. greatest(jsonb_array_length(v_result) - 1, 0) loop
      if v_j > jsonb_array_length(v_result) - 1 then
        exit;
      end if;
      if coalesce(v_result -> v_j ->> 'id', '') = v_id then
        v_result := jsonb_set(v_result, array[v_j::text], v_item, true);
        v_found := true;
        exit;
      end if;
    end loop;
    if not v_found then
      v_result := v_result || jsonb_build_array(v_item);
    end if;
  end loop;

  return v_result;
end;
$$;

-- New firmware calls v2. This keeps the legacy RPC stable while making partial
-- game status writes resilient under bad networks.
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
begin
  select state
    into v_existing_state
    from public.remote_devices
   where device_id = p_device_id;

  if p_full then
    v_merged_state := v_patch;
  else
    v_merged_state := coalesce(v_existing_state, '{}'::jsonb) || v_patch;
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

grant execute on function public.merge_games_array_by_id(jsonb, jsonb) to anon, authenticated, service_role;
grant execute on function public.apply_remote_device_patch_v2(text, jsonb, boolean, text) to anon, authenticated, service_role;
