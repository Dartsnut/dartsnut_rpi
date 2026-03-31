import remote_sync_bridge as rsb


def test_request_set_game_status_updates_existing_game(monkeypatch):
    sent = []

    monkeypatch.setattr(
        "game_lifecycle.get_games_summary",
        lambda: [{"id": "chess", "version": "1.0.0", "status": "ready"}],
    )
    monkeypatch.setattr(
        rsb._ssb,
        "publish_device_state_update",
        lambda payload: sent.append(payload),
    )

    rsb.request_set_game_status("chess", "playing")

    assert sent == [
        {"games": [{"id": "chess", "version": "1.0.0", "status": "playing"}]}
    ]


def test_request_set_game_status_appends_missing_game(monkeypatch):
    sent = []

    monkeypatch.setattr("game_lifecycle.get_games_summary", lambda: [])
    monkeypatch.setattr(
        rsb._ssb,
        "publish_device_state_update",
        lambda payload: sent.append(payload),
    )

    rsb.request_set_game_status("newgame", "downloading")

    assert sent == [
        {"games": [{"id": "newgame", "version": "", "status": "downloading"}]}
    ]
