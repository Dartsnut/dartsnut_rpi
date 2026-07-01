from pathlib import Path

import pytest

import pico8_sync


GHOSTWAVE_HTML = """
<script id=cart_data_script>
    pdat=[
    ['148521', 142306, `Ghost Wave`,"/bbs/thumbs/pico8_ghostwave-0.png",256,170.66666666667,"2024-05-17 06:46:20",63521,"Conor","2024-05-17 11:41:34",0,"",37,3,0,7,2,'0',[],2,4368,7,`ghostwave`,``],
    ];
</script>
"""


class _Response:
    def __init__(self, *, text="", content=b"", status_code=200):
        self.text = text
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Session:
    def __init__(self, responses, cookies=None):
        self.responses = list(responses)
        self.cookies = cookies or {}
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        if not self.responses:
            raise AssertionError(f"unexpected GET {url}")
        return self.responses.pop(0)


def test_sync_pico8_favourites_writes_list_and_carts(tmp_path, monkeypatch):
    session = _Session(
        [
            _Response(text="<html>account</html>"),
            _Response(text=GHOSTWAVE_HTML),
            _Response(
                text=(
                    "lid:ghostwave-0\n"
                    "mid:ghostwave\n"
                    "title:ghostwave\n"
                    "author:Conor\n"
                    "ts:2024-05-17 06:38:11\n"
                    "catsub:1794\n"
                )
            ),
            _Response(content=b"\x89PNG\r\n\x1a\ncart-bytes"),
        ],
        cookies={"s_uid": "112036"},
    )
    monkeypatch.setattr(pico8_sync.requests, "Session", lambda: session)

    result = pico8_sync.sync_pico8_favourites("secret-key", appdata_dir=tmp_path)

    assert result.ok is True
    assert result.cart_count == 1
    favs = (tmp_path / "favourites.txt").read_text(encoding="utf-8")
    assert favs == (
        "|ghostwave-0          |ghostwave            |1794   |Conor            "
        "|Ghost Wave           |2024-05-17 06:38:11\n"
    )
    cart_path = tmp_path / "bbs" / "carts" / "ghostwave-0.p8.png"
    mirror_path = tmp_path / "bbs" / "gh" / "ghostwave-0.p8.png"
    assert cart_path.read_bytes() == b"\x89PNG\r\n\x1a\ncart-bytes"
    assert mirror_path.read_bytes() == b"\x89PNG\r\n\x1a\ncart-bytes"


def test_sync_pico8_favourites_empty_page_writes_empty_list(tmp_path, monkeypatch):
    session = _Session(
        [
            _Response(text="<html>account</html>"),
            _Response(text="<script>pdat=[];</script>"),
        ],
        cookies={"s_uid": "112036"},
    )
    monkeypatch.setattr(pico8_sync.requests, "Session", lambda: session)

    result = pico8_sync.sync_pico8_favourites("secret-key", appdata_dir=tmp_path)

    assert result.ok is True
    assert result.cart_count == 0
    assert (tmp_path / "favourites.txt").read_text(encoding="utf-8") == ""


def test_sync_pico8_favourites_requires_login_cookie(tmp_path, monkeypatch):
    session = _Session([_Response(text="<html>account</html>")], cookies={})
    monkeypatch.setattr(pico8_sync.requests, "Session", lambda: session)

    result = pico8_sync.sync_pico8_favourites("secret-key", appdata_dir=tmp_path)

    assert result.ok is False
    assert "s_uid" in result.message


def test_sync_pico8_favourites_failed_nfo_writes_no_partial_cart(tmp_path, monkeypatch):
    session = _Session(
        [
            _Response(text="<html>account</html>"),
            _Response(text=GHOSTWAVE_HTML),
            _Response(status_code=500),
        ],
        cookies={"s_uid": "112036"},
    )
    monkeypatch.setattr(pico8_sync.requests, "Session", lambda: session)

    result = pico8_sync.sync_pico8_favourites("secret-key", appdata_dir=tmp_path)

    assert result.ok is False
    assert result.cart_count == 0
    assert (tmp_path / "favourites.txt").read_text(encoding="utf-8") == ""
    assert not (tmp_path / "bbs" / "carts" / "ghostwave-0.p8.png").exists()


def test_sync_pico8_favourites_failed_cart_download_writes_no_partial_cart(
    tmp_path, monkeypatch
):
    session = _Session(
        [
            _Response(text="<html>account</html>"),
            _Response(text=GHOSTWAVE_HTML),
            _Response(
                text=(
                    "lid:ghostwave-0\n"
                    "mid:ghostwave\n"
                    "title:ghostwave\n"
                    "author:Conor\n"
                    "ts:2024-05-17 06:38:11\n"
                    "catsub:1794\n"
                )
            ),
            _Response(status_code=404),
            _Response(status_code=500),
        ],
        cookies={"s_uid": "112036"},
    )
    monkeypatch.setattr(pico8_sync.requests, "Session", lambda: session)

    result = pico8_sync.sync_pico8_favourites("secret-key", appdata_dir=tmp_path)

    assert result.ok is False
    assert result.cart_count == 0
    assert (tmp_path / "favourites.txt").read_text(encoding="utf-8") == ""
    assert not (tmp_path / "bbs" / "carts" / "ghostwave-0.p8.png").exists()


def test_sync_pico8_favourites_zero_byte_cart_keeps_existing_file(tmp_path, monkeypatch):
    existing = tmp_path / "bbs" / "carts" / "ghostwave-0.p8.png"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"old-cart")
    session = _Session(
        [
            _Response(text="<html>account</html>"),
            _Response(text=GHOSTWAVE_HTML),
            _Response(
                text=(
                    "lid:ghostwave-0\n"
                    "mid:ghostwave\n"
                    "title:ghostwave\n"
                    "author:Conor\n"
                    "ts:2024-05-17 06:38:11\n"
                    "catsub:1794\n"
                )
            ),
            _Response(content=b""),
            _Response(content=b""),
        ],
        cookies={"s_uid": "112036"},
    )
    monkeypatch.setattr(pico8_sync.requests, "Session", lambda: session)

    result = pico8_sync.sync_pico8_favourites("secret-key", appdata_dir=tmp_path)

    assert result.ok is False
    assert existing.read_bytes() == b"old-cart"
