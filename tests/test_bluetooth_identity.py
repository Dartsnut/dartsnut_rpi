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
