import sys
import types
from types import SimpleNamespace
from unittest.mock import patch

assets_stub = types.ModuleType("assets")
assets_stub.create_loading_image = lambda: object()
sys.modules.setdefault("assets", assets_stub)

from runtime.bootstrap import start_background_subsystems


class FakeDartsnut:
    def update_frame_buffer(self, _image):
        pass


class FakeRemoteSync:
    def set_connectivity_callback(self, _callback):
        pass

    def start_sync_if_available(
        self,
        _device_info,
        _reload_config,
        _apply_remote_config,
        _on_sync_game_ready,
    ):
        pass


def test_startup_applies_device_json_brightness_to_hardware():
    normal_brightness_values = []
    startup_brightness_values = []
    volume_values = []

    with patch("runtime.bootstrap.threading.Thread"), patch(
        "runtime.bootstrap.get_remote_sync", return_value=FakeRemoteSync()
    ):
        start_background_subsystems(
            dartsnut=FakeDartsnut(),
            device_info={"brightness": "80", "volume": "30"},
            get_version=lambda: {"version": "test"},
            set_volume=lambda value: volume_values.append(value),
            start_ble_server=lambda *_args, **_kwargs: None,
            locate_device=lambda: None,
            start_websocket_server=lambda *_args, **_kwargs: None,
            set_brightness=lambda value: normal_brightness_values.append(value),
            reload_config=lambda: None,
            set_time_zone=lambda _value: None,
            get_widgets_framebuffer=lambda: None,
            start_game_from_websocket=lambda _game_id: False,
            trigger_dim_check=lambda: None,
            check_connection_loop=lambda: None,
            network_state_remote_loop=lambda: None,
            apply_remote_config=lambda _config: None,
            on_remote_connectivity_changed=lambda _connected: None,
            request_network_state_refresh=lambda: None,
            remote_config_runtime=SimpleNamespace(),
            set_startup_brightness=lambda value: startup_brightness_values.append(
                value
            ),
        )

    assert volume_values == [30]
    assert normal_brightness_values == []
    assert startup_brightness_values == [80]
