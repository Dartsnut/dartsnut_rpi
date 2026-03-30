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
            "p_patch": {"brightness": 70},
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
        params={"device_id": f"eq.{device_id}", "select": "device_id,state,last_update_source"},
        timeout=15,
    )
    assert query.status_code == 200, query.text
    rows = query.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["state"]["brightness"] == 70
    assert row["state"]["volume"] == 25
    assert row["last_update_source"] == "integration_test"
