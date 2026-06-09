import game_lifecycle as gl


class _Ctx:
    remote_menu_ready_game_ids = None


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
    ctx.remote_menu_ready_game_ids = None
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
    ctx.remote_menu_ready_game_ids = frozenset()
    assert [c["id"] for c in gl.load_menu_game_list(ctx)] == ["a"]


def test_load_menu_game_list_ignores_remote_ready_filter_and_sort(monkeypatch):
    games = [
        {"id": "a", "name": "Zebra", "status": "ready"},
        {"id": "b", "name": "Apple", "status": "ready"},
        {"id": "c", "name": "Cut", "status": "ready"},
        {"id": "d", "name": "Download", "status": "downloading"},
    ]
    monkeypatch.setattr(gl, "load_game_list", lambda: list(games))
    monkeypatch.setattr(
        gl,
        "_load_user_data",
        lambda: {"game_playtimes": {"a": 100, "b": 100, "c": 50}},
    )
    ctx = _Ctx()
    ctx.remote_menu_ready_game_ids = frozenset({"a", "b"})
    result = gl.load_menu_game_list(ctx)
    # a and b tie at 100; sort by name. c remains because menu is local-only.
    assert [c["id"] for c in result] == ["b", "a", "c"]


class _RefreshCtx:
    def __init__(self):
        self.remote_menu_ready_game_ids = None
        self.reload_game_menu = False
        self.load_game_list = None
        self.game_list = []
        self.game_index = 0
        self.game_preview_index = 0


def test_refresh_menu_game_list_replaces_and_clears_flag():
    ctx = _RefreshCtx()
    ctx.reload_game_menu = True
    ctx.load_game_list = lambda: [{"id": "x", "preview": [bytearray(3)]}]
    gl.refresh_menu_game_list_if_requested(ctx)
    assert [g["id"] for g in ctx.game_list] == ["x"]
    assert ctx.reload_game_menu is False


def test_refresh_menu_game_list_clamps_index_when_list_shrinks():
    ctx = _RefreshCtx()
    ctx.reload_game_menu = True
    ctx.load_game_list = lambda: [{"id": "only", "preview": [bytearray(1), bytearray(2)]}]
    ctx.game_index = 5
    ctx.game_preview_index = 99
    gl.refresh_menu_game_list_if_requested(ctx)
    assert ctx.game_index == 0
    assert ctx.game_preview_index == 1


def test_refresh_menu_game_list_empty_resets_indices():
    ctx = _RefreshCtx()
    ctx.reload_game_menu = True
    ctx.load_game_list = lambda: []
    ctx.game_index = 3
    ctx.game_preview_index = 2
    gl.refresh_menu_game_list_if_requested(ctx)
    assert ctx.game_list == []
    assert ctx.game_index == 0
    assert ctx.game_preview_index == 0


def test_refresh_menu_game_list_noop_when_flag_false():
    ctx = _RefreshCtx()
    ctx.reload_game_menu = False
    called = []
    ctx.load_game_list = lambda: called.append(1) or []
    gl.refresh_menu_game_list_if_requested(ctx)
    assert called == []


def test_refresh_menu_game_list_no_loader_clears_flag():
    ctx = _RefreshCtx()
    ctx.reload_game_menu = True
    ctx.load_game_list = None
    gl.refresh_menu_game_list_if_requested(ctx)
    assert ctx.reload_game_menu is False


def test_refresh_menu_game_list_missing_preview_clamps_preview_index():
    ctx = _RefreshCtx()
    ctx.reload_game_menu = True
    ctx.load_game_list = lambda: [{"id": "n", "name": "N"}]
    ctx.game_preview_index = 5
    gl.refresh_menu_game_list_if_requested(ctx)
    assert ctx.game_preview_index == 0
