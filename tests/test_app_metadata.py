import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.app_metadata import read_app_metadata, write_app_metadata


def test_write_and_read_app_metadata_round_trip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps" / "chess").mkdir(parents=True)

    write_app_metadata(
        "chess",
        {
            "id": "backend-chess",
            "type": "game",
            "version": "2.0.0",
            "name": "Chess",
            "preview_urls": ["covers/chess.png"],
            "download_url": "https://example.com/chess.tar.gz",
            "download_md5": "abc123",
        },
    )

    data = read_app_metadata("chess")
    assert data["id"] == "backend-chess"
    assert data["type"] == "game"
    assert data["version"] == "2.0.0"
    assert data["preview_urls"] == ["covers/chess.png"]
    assert data["download_md5"] == "abc123"
    assert data["updated_at"]


def test_read_app_metadata_returns_empty_for_missing_or_invalid_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "apps" / "broken"
    app_dir.mkdir(parents=True)

    assert read_app_metadata("missing") == {}

    (app_dir / ".dartsnut_backend.json").write_text("{invalid", encoding="utf-8")
    assert read_app_metadata("broken") == {}


def test_app_metadata_rejects_unsafe_app_ids(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    for unsafe in ("../chess", ".hidden", "bad/name", "bad\\name", ""):
        try:
            read_app_metadata(unsafe)
            assert False, f"expected ValueError for {unsafe!r}"
        except ValueError:
            pass

        try:
            write_app_metadata(unsafe, {"id": "x"})
            assert False, f"expected ValueError for {unsafe!r}"
        except ValueError:
            pass


def test_write_app_metadata_does_not_preserve_unknown_fields(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app_dir = tmp_path / "apps" / "chess"
    app_dir.mkdir(parents=True)

    write_app_metadata("chess", {"id": "chess", "type": "game", "extra": "ignored"})

    raw = json.loads((app_dir / ".dartsnut_backend.json").read_text(encoding="utf-8"))
    assert "extra" not in raw
    assert set(raw) == {
        "id",
        "type",
        "version",
        "name",
        "preview_urls",
        "download_url",
        "download_md5",
        "updated_at",
    }
