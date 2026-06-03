from runtime.sync.tokenizer import tokenize_snapshot


def test_tokenize_emits_game_ready_and_full_snapshot():
    config = {
        "updated_at": "2026-06-01T10:00:00Z",
        "last_update_source": "mobile_app",
        "games": [{"id": "g1", "status": "ready", "version": "1"}],
        "brightness": 80,
    }
    events = tokenize_snapshot(config)
    kinds = [type(e).__name__ for e in events]
    assert "FullSnapshot" in kinds
    assert "GameReadySetChanged" in kinds
    assert "DeviceSettingsChanged" in kinds


def test_tokenize_omits_game_ready_when_games_missing():
    config = {"updated_at": "2026-06-01T10:00:00Z", "brightness": 50}
    events = tokenize_snapshot(config, emit_full_snapshot=False)
    assert not any(type(e).__name__ == "GameReadySetChanged" for e in events)
