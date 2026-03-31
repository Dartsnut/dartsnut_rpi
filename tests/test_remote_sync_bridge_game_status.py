import remote_sync_bridge as rsb


# Game status update flow (existing -> append -> remote-removal guard)
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


def test_request_set_game_status_does_not_readd_remote_removed_game(monkeypatch):
    sent = []

    monkeypatch.setattr(
        "game_lifecycle.get_games_summary",
        lambda: [
            {"id": "g-removed", "version": "1.0.0", "status": "ready"},
            {"id": "g-install", "version": "2.0.0", "status": "ready"},
        ],
    )
    monkeypatch.setattr(
        rsb._ssb,
        "publish_device_state_update",
        lambda payload: sent.append(payload),
    )
    rsb._ssb._remember_remote_game_ids(
        {"games": [{"id": "g-install", "status": "downloading", "version": "2.0.0"}]}
    )

    rsb.request_set_game_status("g-install", "downloading")

    assert sent == [
        {"games": [{"id": "g-install", "version": "2.0.0", "status": "downloading"}]}
    ]
