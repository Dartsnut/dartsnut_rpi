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


def test_request_set_game_status_only_publishes_target_game(monkeypatch):
    sent = []
    monkeypatch.setattr(rsb._ssb, "_remote_game_ids", None)
    rsb._ssb.invalidate_published_game_status("chess")

    monkeypatch.setattr(
        "game_lifecycle.get_games_summary",
        lambda: [
            {"id": "chess", "version": "1.0.0", "status": "ready"},
            {"id": "pong", "version": "2.1.0", "status": "ready"},
        ],
    )
    monkeypatch.setattr(
        rsb._ssb,
        "publish_device_state_update",
        lambda payload: sent.append(payload),
    )

    rsb.request_set_game_status("chess", "playing")

    assert sent == [{"games": [{"id": "chess", "version": "1.0.0", "status": "playing"}]}]


def test_request_set_all_games_ready_publishes_remote_ids_even_when_local_missing(
    monkeypatch,
):
    sent = []
    monkeypatch.setattr("game_lifecycle.get_games_summary", lambda: [])
    monkeypatch.setattr(rsb._ssb, "_remote_game_ids", {"chess"})
    monkeypatch.setattr(
        rsb._ssb, "_remote_games_by_id", {"chess": {"id": "chess", "version": "1.0.0"}}
    )
    monkeypatch.setattr(
        rsb._ssb,
        "publish_device_state_update",
        lambda payload: sent.append(payload),
    )

    rsb.request_set_all_games_ready()

    assert sent == [{"games": [{"id": "chess", "version": "1.0.0", "status": "ready"}]}]


def test_request_set_all_games_ready_prefers_local_version_over_remote(monkeypatch):
    sent = []
    monkeypatch.setattr(
        "game_lifecycle.get_games_summary",
        lambda: [{"id": "chess", "version": "2.0.0", "status": "ready"}],
    )
    monkeypatch.setattr(rsb._ssb, "_remote_game_ids", {"chess"})
    monkeypatch.setattr(
        rsb._ssb, "_remote_games_by_id", {"chess": {"id": "chess", "version": "1.0.0"}}
    )
    monkeypatch.setattr(
        rsb._ssb,
        "publish_device_state_update",
        lambda payload: sent.append(payload),
    )

    rsb.request_set_all_games_ready()

    assert sent == [{"games": [{"id": "chess", "version": "2.0.0", "status": "ready"}]}]


def test_request_set_all_games_ready_rebuilds_only_remembered_remote_ids(
    monkeypatch,
):
    sent = []
    monkeypatch.setattr(
        "game_lifecycle.get_games_summary",
        lambda: [
            {"id": "flipdarts", "version": "1.0.0", "status": "ready"},
            {"id": "01dartgame", "version": "1.0.1", "status": "ready"},
            {"id": "pico8", "version": "1.0.2", "status": "ready"},
            {"id": "cricket", "version": "2.0.0", "status": "ready"},
            {"id": "splashgame", "version": "3.0.0", "status": "ready"},
        ],
    )
    monkeypatch.setattr(
        rsb._ssb,
        "_remote_game_ids",
        {"flipdarts", "01dartgame", "pico8"},
    )
    monkeypatch.setattr(
        rsb._ssb,
        "_remote_games_by_id",
        {
            "flipdarts": {"id": "flipdarts", "version": "1.0.0"},
            "01dartgame": {"id": "01dartgame", "version": "1.0.1"},
            "pico8": {"id": "pico8", "version": "1.0.2"},
        },
    )
    monkeypatch.setattr(
        rsb._ssb,
        "publish_device_state_update",
        lambda payload: sent.append(payload),
    )

    rsb.request_set_all_games_ready()

    assert sent == [
        {
            "games": [
                {"id": "01dartgame", "version": "1.0.1", "status": "ready"},
                {"id": "flipdarts", "version": "1.0.0", "status": "ready"},
                {"id": "pico8", "version": "1.0.2", "status": "ready"},
            ]
        }
    ]


def test_request_set_game_status_publishes_higher_local_version(monkeypatch):
    sent = []

    monkeypatch.setattr(
        "game_lifecycle.get_games_summary",
        lambda: [{"id": "chess", "version": "1.0.0", "status": "ready"}],
    )
    monkeypatch.setattr(
        "game_lifecycle.get_local_game_version",
        lambda _gid: "2.5.0",
    )
    monkeypatch.setattr(
        rsb._ssb,
        "publish_device_state_update",
        lambda payload: sent.append(payload),
    )
    rsb._ssb._remember_remote_game_ids(
        {"games": [{"id": "chess", "status": "downloading", "version": "2.0.0"}]}
    )

    rsb.request_set_game_status("chess", "ready")

    assert sent == [
        {"games": [{"id": "chess", "version": "2.5.0", "status": "ready"}]}
    ]


def test_request_set_all_games_ready_skips_when_remote_ids_unknown(monkeypatch):
    sent = []
    monkeypatch.setattr(rsb._ssb, "_remote_game_ids", None)
    monkeypatch.setattr("game_lifecycle.get_games_summary", lambda: [{"id": "chess"}])
    monkeypatch.setattr(
        rsb._ssb,
        "publish_device_state_update",
        lambda payload: sent.append(payload),
    )

    rsb.request_set_all_games_ready()

    assert sent == []
