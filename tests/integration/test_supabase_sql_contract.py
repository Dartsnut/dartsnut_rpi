from __future__ import annotations

import os
import uuid

import pytest
import requests


pytestmark = pytest.mark.contract


def _require_contract_env() -> tuple[str, str]:
    if os.getenv("RUN_SUPABASE_CONTRACT") != "1":
        pytest.skip("Set RUN_SUPABASE_CONTRACT=1 to run contract tests")
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_ANON_KEY") or ""
    if not url or not key:
        pytest.skip("SUPABASE_URL and SUPABASE_KEY/SUPABASE_ANON_KEY are required")
    return url, key


def test_apply_remote_device_patch_merges_and_sets_source():
    base_url, api_key = _require_contract_env()
    headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    device_id = f"ITEST-{uuid.uuid4()}"

    rpc_url = f"{base_url}/rest/v1/rpc/apply_remote_device_patch"
    row_url = f"{base_url}/rest/v1/remote_devices"

    first = requests.post(
        rpc_url,
        headers=headers,
        json={
            "p_device_id": device_id,
            "p_patch": {"brightness": 7},
            "p_full": False,
            "p_source": "integration_test",
        },
        timeout=15,
    )
    assert first.status_code in (200, 201), first.text

    second = requests.post(
        rpc_url,
        headers=headers,
        json={
            "p_device_id": device_id,
            "p_patch": {"volume": 25},
            "p_full": False,
            "p_source": "integration_test",
        },
        timeout=15,
    )
    assert second.status_code in (200, 201), second.text

    query = requests.get(
        row_url,
        headers=headers,
        params={"device_id": f"eq.{device_id}", "select": "state,last_update_source"},
        timeout=15,
    )
    assert query.status_code == 200, query.text
    rows = query.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["state"]["brightness"] == 7
    assert row["state"]["volume"] == 25
    assert row["last_update_source"] == "integration_test"


def test_apply_remote_device_patch_preserves_legacy_games_replace_behavior():
    base_url, api_key = _require_contract_env()
    headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    device_id = f"ITEST-GAMES-LEGACY-{uuid.uuid4()}"
    rpc_url = f"{base_url}/rest/v1/rpc/apply_remote_device_patch"
    row_url = f"{base_url}/rest/v1/remote_devices"

    seed = requests.post(
        rpc_url,
        headers=headers,
        json={
            "p_device_id": device_id,
            "p_patch": {
                "games": [
                    {"id": "g1", "status": "ready", "version": "1"},
                    {"id": "g2", "status": "ready", "version": "1"},
                ]
            },
            "p_full": False,
            "p_source": "integration_test",
        },
        timeout=15,
    )
    assert seed.status_code in (200, 201), seed.text

    partial = requests.post(
        rpc_url,
        headers=headers,
        json={
            "p_device_id": device_id,
            "p_patch": {
                "games": [{"id": "g1", "status": "playing", "version": "1"}]
            },
            "p_full": False,
            "p_source": "integration_test",
        },
        timeout=15,
    )
    assert partial.status_code in (200, 201), partial.text

    query = requests.get(
        row_url,
        headers=headers,
        params={"device_id": f"eq.{device_id}", "select": "state"},
        timeout=15,
    )
    assert query.status_code == 200, query.text
    games = query.json()[0]["state"]["games"]
    assert games == [{"id": "g1", "status": "playing", "version": "1"}]


def test_apply_remote_device_patch_v2_updates_existing_games_only_and_filters_fields():
    base_url, api_key = _require_contract_env()
    headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    device_id = f"ITEST-GAMES-{uuid.uuid4()}"
    rpc_url = f"{base_url}/rest/v1/rpc/apply_remote_device_patch_v2"
    row_url = f"{base_url}/rest/v1/remote_devices"

    seed = requests.post(
        rpc_url,
        headers=headers,
        json={
            "p_device_id": device_id,
            "p_patch": {
                "games": [
                    {"id": "g1", "name": "Game One", "status": "ready", "version": "1"},
                    {"id": "g2", "status": "ready", "version": "1"},
                ],
                "device_info": {"id": device_id, "name": "Remote Name"},
                "dim_window": {"dim_window_enabled": True},
            },
            "p_full": True,
            "p_source": "integration_test",
        },
        timeout=15,
    )
    assert seed.status_code in (200, 201), seed.text

    partial = requests.post(
        rpc_url,
        headers=headers,
        json={
            "p_device_id": device_id,
            "p_patch": {
                "games": [
                    {
                        "id": "g1",
                        "name": "Mutated Name",
                        "status": "playing",
                        "version": "2",
                    },
                    {"id": "g3", "status": "ready", "version": "1"},
                ],
                "device_info": {"id": device_id, "name": "Local Name"},
                "dim_window": {"dim_window_enabled": False},
                "latency": 123,
            },
            "p_full": False,
            "p_source": "integration_test",
        },
        timeout=15,
    )
    assert partial.status_code in (200, 201), partial.text

    query = requests.get(
        row_url,
        headers=headers,
        params={"device_id": f"eq.{device_id}", "select": "state"},
        timeout=15,
    )
    assert query.status_code == 200, query.text
    games = {g["id"]: g for g in query.json()[0]["state"]["games"]}
    assert set(games) == {"g1", "g2"}
    assert games["g1"]["status"] == "playing"
    assert games["g1"]["version"] == "2"
    assert games["g1"]["name"] == "Game One"
    assert games["g2"]["status"] == "ready"
    state = query.json()[0]["state"]
    assert state["device_info"] == {"id": device_id, "name": "Remote Name"}
    assert state["dim_window"] == {"dim_window_enabled": True}
    assert state["latency"] == 123


def test_apply_remote_device_patch_v2_partial_does_not_create_missing_row():
    base_url, api_key = _require_contract_env()
    headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    device_id = f"ITEST-PARTIAL-FIRST-{uuid.uuid4()}"
    rpc_url = f"{base_url}/rest/v1/rpc/apply_remote_device_patch_v2"
    row_url = f"{base_url}/rest/v1/remote_devices"

    partial = requests.post(
        rpc_url,
        headers=headers,
        json={
            "p_device_id": device_id,
            "p_patch": {
                "ssid": "Dartsnut",
                "ip_address": "192.168.31.37",
                "device_updated_at": "2026-06-24T05:49:06Z",
            },
            "p_full": False,
            "p_source": "supabase_bridge",
        },
        timeout=15,
    )
    assert partial.status_code in (200, 201, 204), partial.text

    query = requests.get(
        row_url,
        headers=headers,
        params={"device_id": f"eq.{device_id}", "select": "state"},
        timeout=15,
    )
    assert query.status_code == 200, query.text
    assert query.json() == []

    full = requests.post(
        rpc_url,
        headers=headers,
        json={
            "p_device_id": device_id,
            "p_patch": {
                "volume": 50,
                "brightness": 4,
                "games": [{"id": "g1", "status": "ready", "version": "1"}],
            },
            "p_full": True,
            "p_source": "supabase_bridge",
        },
        timeout=15,
    )
    assert full.status_code in (200, 201), full.text

    query = requests.get(
        row_url,
        headers=headers,
        params={"device_id": f"eq.{device_id}", "select": "state"},
        timeout=15,
    )
    assert query.status_code == 200, query.text
    assert query.json()[0]["state"]["games"] == [
        {"id": "g1", "status": "ready", "version": "1"}
    ]


def test_remote_device_commands_table_contract():
    base_url, api_key = _require_contract_env()
    headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    device_id = f"ITEST-CMD-{uuid.uuid4()}"
    row_url = f"{base_url}/rest/v1/remote_device_commands"

    inserted = requests.post(
        row_url,
        headers={**headers, "Prefer": "return=representation"},
        json={"device_id": device_id},
        timeout=15,
    )
    assert inserted.status_code in (200, 201), inserted.text
    row = inserted.json()[0]
    assert row["device_id"] == device_id
    assert row["command"] == ""
    assert row["command_token"] == ""
    assert row["running_command_token"] == ""
    assert row["started_at"] is None
    assert row["stop_requested_at"] is None
    assert row["status_code"] is None
    assert row["log_filename"] == ""
    assert row["last_update_source"] == ""
    assert row["updated_at"]

    updated = requests.patch(
        row_url,
        headers={**headers, "Prefer": "return=representation"},
        params={"device_id": f"eq.{device_id}"},
        json={
            "command": "ls",
            "command_token": "cmd-token-1",
            "stop_requested_at": "2026-07-08T01:02:03Z",
            "last_update_source": "integration_test",
        },
        timeout=15,
    )
    assert updated.status_code in (200, 204), updated.text

    query = requests.get(
        row_url,
        headers=headers,
        params={
            "device_id": f"eq.{device_id}",
            "select": "command,command_token,running_command_token,started_at,stop_requested_at,status_code,log_filename,last_update_source,updated_at",
        },
        timeout=15,
    )
    assert query.status_code == 200, query.text
    rows = query.json()
    assert len(rows) == 1
    assert rows[0]["command"] == "ls"
    assert rows[0]["command_token"] == "cmd-token-1"
    assert rows[0]["running_command_token"] == ""
    assert rows[0]["started_at"] is None
    assert rows[0]["stop_requested_at"].startswith("2026-07-08T01:02:03")
    assert rows[0]["last_update_source"] == "integration_test"
