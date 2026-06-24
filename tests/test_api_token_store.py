import json
import os

from runtime import api_token_store


def test_preserve_remote_user_token_writes_and_reads_token(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    api_token_store.preserve_remote_user_token({"token": "  abc  "})

    assert api_token_store.get_api_token() == "abc"
    assert api_token_store.build_api_headers({"X-Test": "1"}) == {
        "X-Test": "1",
        "Token": "abc",
    }
    mode = os.stat(api_token_store.token_file_path()).st_mode & 0o777
    assert mode == 0o600


def test_missing_user_or_token_leaves_existing_file_unchanged(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    api_token_store.preserve_remote_user_token({"token": "abc"})

    api_token_store.preserve_remote_user_token(None)
    api_token_store.preserve_remote_user_token({})

    assert api_token_store.get_api_token() == "abc"


def test_empty_or_null_token_deletes_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    api_token_store.preserve_remote_user_token({"token": "abc"})

    api_token_store.preserve_remote_user_token({"token": "   "})

    assert api_token_store.get_api_token() == ""
    assert not os.path.exists(api_token_store.token_file_path())

    api_token_store.preserve_remote_user_token({"token": "abc"})
    api_token_store.preserve_remote_user_token({"token": None})

    assert api_token_store.get_api_token() == ""
    assert not os.path.exists(api_token_store.token_file_path())


def test_invalid_file_returns_empty_token(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    os.makedirs(".dartsnut", exist_ok=True)
    with open(api_token_store.token_file_path(), "w", encoding="utf-8") as f:
        json.dump({"token": 123}, f)

    assert api_token_store.get_api_token() == ""
    assert api_token_store.build_api_headers() == {}
