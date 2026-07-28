from runtime import bluetooth_identity


def test_build_bluetooth_local_name_uses_model_and_normalized_mac_suffix():
    assert bluetooth_identity.build_bluetooth_local_name(
        {"model": "PixelDart"},
        "AA:BB:CC:DD:EE:FF",
    ) == "PixelDart-eeff"
    assert bluetooth_identity.build_bluetooth_local_name(
        {"model": "PixelBoard"},
        "AABBCCDDEEFF",
    ) == "PixelBoard-eeff"


def test_build_bluetooth_local_name_falls_back_to_dartsnut_and_zero_suffix():
    assert bluetooth_identity.build_bluetooth_local_name({}, "bad") == "Dartsnut-0000"
    assert bluetooth_identity.build_bluetooth_local_name(
        {"model": "  "},
        "AA:BB:CC:DD:EE:FF",
    ) == "Dartsnut-eeff"


def test_normalize_bluetooth_adapter_address_accepts_common_formats():
    assert bluetooth_identity.normalize_bluetooth_adapter_address(
        "aa:bb:cc:dd:ee:ff"
    ) == "AA:BB:CC:DD:EE:FF"
    assert bluetooth_identity.normalize_bluetooth_adapter_address(
        "AA-BB-CC-DD-EE-FF"
    ) == "AA:BB:CC:DD:EE:FF"
    assert bluetooth_identity.normalize_bluetooth_adapter_address(
        "AABBCCDDEEFF"
    ) == "AA:BB:CC:DD:EE:FF"
    assert bluetooth_identity.normalize_bluetooth_adapter_address("not-a-mac") is None


def test_resolve_bluetooth_device_id_prefers_device_mac_then_adapter(monkeypatch):
    monkeypatch.setattr(
        bluetooth_identity,
        "get_bluetooth_adapter_address",
        lambda: "11:22:33:44:55:66",
    )

    assert bluetooth_identity.resolve_bluetooth_device_id(
        {"ble_mac": "aa:bb:cc:dd:ee:ff"}
    ) == "AA:BB:CC:DD:EE:FF"
    assert bluetooth_identity.resolve_bluetooth_device_id(
        {"mac_address": "AA-BB-CC-DD-EE-00"}
    ) == "AA:BB:CC:DD:EE:00"
    assert bluetooth_identity.resolve_bluetooth_device_id(
        {"id": "AA:BB:CC:DD:EE:01"}
    ) == "AA:BB:CC:DD:EE:01"
    assert bluetooth_identity.resolve_bluetooth_device_id({}) == (
        "11:22:33:44:55:66"
    )


def test_resolve_bluetooth_local_name_prefers_device_ble_mac(monkeypatch):
    monkeypatch.setattr(
        bluetooth_identity,
        "get_bluetooth_adapter_address",
        lambda: "11:22:33:44:55:66",
    )
    assert bluetooth_identity.resolve_bluetooth_local_name(
        {"model": "PixelDart", "ble_mac": "AA:BB:CC:DD:EE:FF"}
    ) == "PixelDart-eeff"


def test_resolve_bluetooth_local_name_returns_none_without_adapter(monkeypatch):
    monkeypatch.setattr(
        bluetooth_identity,
        "get_bluetooth_adapter_address",
        lambda: None,
    )
    assert bluetooth_identity.resolve_bluetooth_local_name({"model": "PixelDart"}) is None
