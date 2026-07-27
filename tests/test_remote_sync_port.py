import runtime.remote_sync_port as rsp


def test_create_default_remote_sync_matches_bridge_availability():
    try:
        import supabase_sync_bridge  # noqa: F401

        expect_bridge = True
    except ImportError:
        expect_bridge = False
    impl = rsp.create_default_remote_sync()
    if expect_bridge:
        assert isinstance(impl, rsp.SupabaseRemoteSync)
    else:
        assert isinstance(impl, rsp.NoOpRemoteSync)


def test_noop_remote_sync_request_methods_are_safe():
    n = rsp.NoOpRemoteSync()
    n.request_set_game_status("g", "ready")
    n.request_set_all_games_ready()
    n.request_device_reset_state()
    n.start_sync_if_available({}, lambda: None, lambda _c: None)
    n.restart_sync({}, lambda: None, lambda _c: None)
    assert n.get_device_id() == ""


def test_supabase_remote_sync_device_id_delegates(monkeypatch):
    monkeypatch.setattr(rsp._ssb, "get_supabase_device_id", lambda: "OVERRIDE-ID")

    assert rsp.SupabaseRemoteSync().get_device_id() == "OVERRIDE-ID"
