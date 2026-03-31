from __future__ import annotations

import pytest

from runtime.websocket_ports import WebsocketEndpointConfig


@pytest.mark.integration
def test_volume_action_updates_local_and_publishes_remote(
    run_action, websocket_registry, fake_remote_sync
):
    state = {"volume": None}
    endpoint_config = WebsocketEndpointConfig(
        set_volume=lambda v: state.__setitem__("volume", int(v))
        or fake_remote_sync.publish_partial_state({"volume": int(v)})
    )

    result = run_action(
        message={"action": "set_volume", "req_id": 410, "volume": 61},
        registry=websocket_registry,
        endpoint_config=endpoint_config,
    )

    assert result["req_id"] == 410
    assert result["payload"]["action"] == "set_volume"
    assert result["payload"]["message"] == "Success"
    assert state["volume"] == 61
    assert fake_remote_sync.published[-1] == {"volume": 61}


@pytest.mark.integration
def test_external_change_from_supabase_updates_machine_volume(remote_config_harness):
    apply = remote_config_harness["apply"]
    machine_state = remote_config_harness["machine_state"]
    events = remote_config_harness["events"]

    apply({"volume": 27})

    assert events["reload_called"] is True
    assert machine_state.volume == 27
