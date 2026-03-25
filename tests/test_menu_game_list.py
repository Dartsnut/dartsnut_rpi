import game_lifecycle as gl


class _Ctx:
    firestore_menu_ready_game_ids = None


def test_load_menu_game_list_none_cache_filters_local_ready(monkeypatch):
    games = [
        {"id": "a", "name": "A", "status": "ready"},
        {"id": "b", "name": "B", "status": "downloading"},
    ]
    monkeypatch.setattr(gl, "load_game_list", lambda: list(games))
    monkeypatch.setattr(
        gl,
        "_load_user_data",
        lambda: {"game_playtimes": {"a": 10, "b": 5}},
    )
    ctx = _Ctx()
    ctx.firestore_menu_ready_game_ids = None
    result = gl.load_menu_game_list(ctx)
    assert [c["id"] for c in result] == ["a"]


def test_load_menu_game_list_empty_frozenset(monkeypatch):
    monkeypatch.setattr(
        gl,
        "load_game_list",
        lambda: [{"id": "a", "name": "A", "status": "ready"}],
    )
    monkeypatch.setattr(gl, "_load_user_data", lambda: {"game_playtimes": {}})
    ctx = _Ctx()
    ctx.firestore_menu_ready_game_ids = frozenset()
    assert gl.load_menu_game_list(ctx) == []


def test_load_menu_game_list_firestore_ready_only_and_sort(monkeypatch):
    games = [
        {"id": "a", "name": "Zebra", "status": "ready"},
        {"id": "b", "name": "Apple", "status": "ready"},
        {"id": "c", "name": "Cut", "status": "ready"},
    ]
    monkeypatch.setattr(gl, "load_game_list", lambda: list(games))
    monkeypatch.setattr(
        gl,
        "_load_user_data",
        lambda: {"game_playtimes": {"a": 100, "b": 100, "c": 50}},
    )
    ctx = _Ctx()
    ctx.firestore_menu_ready_game_ids = frozenset({"a", "b"})
    result = gl.load_menu_game_list(ctx)
    # a and b tie at 100; sort by name: Apple before Zebra; c excluded (not in Firestore ready set)
    assert [c["id"] for c in result] == ["b", "a"]
