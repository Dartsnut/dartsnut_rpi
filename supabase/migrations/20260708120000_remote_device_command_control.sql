alter table public.remote_device_commands
  add column if not exists command_token text not null default '',
  add column if not exists running_command_token text not null default '',
  add column if not exists started_at timestamptz,
  add column if not exists stop_requested_at timestamptz;

