"""
Remote sync bridge compatibility module backed by Supabase bridge.
"""

import supabase_sync_bridge as _ssb


_client = getattr(_ssb, "_client", None)
_bridge_proc = getattr(_ssb, "_bridge_proc", None)


def _build_initial_state(device_info):
    return _ssb._build_initial_state(device_info)


def _normalize_config_payload(payload):
    return _ssb._normalize_config_payload(payload)


def _merge_remote_and_local(payload):
    return _ssb._merge_remote_and_local(payload)


def notify_device_state_update(partial_state):
    return _ssb.publish_device_state_update(partial_state)


def request_set_game_status(game_id, status):
    return _ssb.request_set_game_status(game_id, status)


def request_device_reset_state():
    return _ssb.request_device_reset_state()


def request_set_dim_window(config):
    return _ssb.request_set_dim_window(config)


def request_set_pages(pages):
    return _ssb.request_set_pages(pages)


def request_set_device_name(name):
    return _ssb.request_set_device_name(name)


def request_set_all_games_ready():
    return _ssb.request_set_all_games_ready()


def is_remote_connected():
    return _ssb.is_supabase_connected()


def set_remote_connectivity_callback(callback):
    return _ssb.set_supabase_connectivity_callback(callback)


def start_remote_sync_if_available(device_info, reload_config, on_config_updated):
    return _ssb.start_supabase_sync_if_available(
        device_info, reload_config, on_config_updated
    )


def ensure_remote_sync_running(device_info, reload_config, on_config_updated):
    return _ssb.start_supabase_sync_if_available(
        device_info, reload_config, on_config_updated
    )


def restart_remote_sync(device_info, reload_config, on_config_updated):
    return _ssb.restart_supabase_sync(device_info, reload_config, on_config_updated)


def stop_remote_sync():
    return _ssb.stop_supabase_sync()


def is_remote_bridge_active():
    return _ssb.is_supabase_bridge_active()
