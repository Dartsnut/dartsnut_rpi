"""Sync Lexaloffle PICO-8 favourites into the local Splore cache."""

from __future__ import annotations

import ast
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

_log = logging.getLogger(__name__)

LEXALOFFLE_BASE = "https://www.lexaloffle.com"
CARTS_BASE = "https://carts.lexaloffle.com"
PICO8_VERSION = "0.2.7"
HTTP_TIMEOUT = (5, 15)


@dataclass(frozen=True)
class Pico8Favourite:
    lid: str
    mid: str
    title: str
    author: str
    ts: str
    catsub: int


@dataclass(frozen=True)
class Pico8SyncResult:
    ok: bool
    cart_count: int = 0
    message: str = ""


def _default_appdata_dir() -> Path:
    return Path(os.path.expanduser("~/.lexaloffle/pico-8"))


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _parse_nfo(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in str(text or "").splitlines():
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        out[key.strip().lower()] = value.strip()
    return out


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value or "").strip())
    except (TypeError, ValueError):
        return default


def _pdat_array_text(html: str) -> str:
    marker = "pdat="
    start = html.find(marker)
    if start < 0:
        return "[]"
    start = html.find("[", start)
    if start < 0:
        return "[]"
    depth = 0
    quote = ""
    escaped = False
    for idx in range(start, len(html)):
        ch = html[idx]
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = ""
            continue
        if ch in {"'", '"', "`"}:
            quote = ch
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return html[start : idx + 1]
    return "[]"


def _literal_pdat(text: str) -> list[Any]:
    normalized = re.sub(r"`([^`\\]*(?:\\.[^`\\]*)*)`", lambda m: repr(m.group(1)), text)
    try:
        value = ast.literal_eval(normalized)
    except Exception:
        return []
    return value if isinstance(value, list) else []


def parse_favourites_from_html(html: str) -> list[Pico8Favourite]:
    favourites: list[Pico8Favourite] = []
    for row in _literal_pdat(_pdat_array_text(str(html or ""))):
        if not isinstance(row, list) or len(row) < 23:
            continue
        if _to_int(row[15]) != 7:
            continue
        mid = str(row[22] or "").strip()
        if not mid:
            continue
        version = str(row[17] or "").strip()
        lid = f"{mid}-{version}" if version else mid
        title = str(row[2] or mid).strip()
        author = str(row[8] or "").strip()
        ts = str(row[6] or row[9] or "").strip()
        catsub = _to_int(row[20])
        favourites.append(
            Pico8Favourite(
                lid=lid,
                mid=mid,
                title=title,
                author=author,
                ts=ts,
                catsub=catsub,
            )
        )
    return favourites


def _fetch_nfo(session: requests.Session, lid: str) -> Pico8Favourite | None:
    resp = session.get(
        f"{LEXALOFFLE_BASE}/bbs/cpost_lister3.php",
        params={"nfo": "1", "version": PICO8_VERSION, "lid": lid},
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    data = _parse_nfo(resp.text)
    nfo_lid = data.get("lid") or lid
    mid = data.get("mid") or nfo_lid.rsplit("-", 1)[0]
    if not nfo_lid:
        return None
    return Pico8Favourite(
        lid=nfo_lid,
        mid=mid,
        title=data.get("title") or mid,
        author=data.get("author") or "",
        ts=data.get("ts") or "",
        catsub=_to_int(data.get("catsub")),
    )


def _merge_page_and_nfo(page: Pico8Favourite, nfo: Pico8Favourite) -> Pico8Favourite:
    return Pico8Favourite(
        lid=nfo.lid,
        mid=nfo.mid,
        title=page.title or nfo.title,
        author=nfo.author or page.author,
        ts=nfo.ts or page.ts,
        catsub=nfo.catsub or page.catsub,
    )


def _download_cart(session: requests.Session, lid: str) -> bytes:
    urls = [
        f"{CARTS_BASE}/{lid}.p8.png",
        f"{LEXALOFFLE_BASE}/bbs/cposts/{lid[:2]}/{lid}.p8.png",
    ]
    last_error: Exception | None = None
    for url in urls:
        try:
            resp = session.get(url, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            if resp.content:
                return resp.content
        except Exception as e:
            last_error = e
    if last_error is not None:
        raise last_error
    raise ValueError("empty cart response")


def _favourites_txt(favourites: list[Pico8Favourite]) -> bytes:
    lines = [
        "|%-20s |%-20s |%-6d |%-16s |%-20s |%s\n"
        % (fav.lid, fav.mid, fav.catsub, fav.author, fav.title, fav.ts)
        for fav in favourites
    ]
    return "".join(lines).encode("utf-8")


def sync_pico8_favourites(
    key: str,
    *,
    appdata_dir: str | os.PathLike[str] | None = None,
) -> Pico8SyncResult:
    secret = str(key or "").strip()
    if not secret:
        return Pico8SyncResult(False, message="missing PICO-8 key")

    root = Path(appdata_dir) if appdata_dir is not None else _default_appdata_dir()
    session = requests.Session()
    try:
        login = session.get(
            f"{LEXALOFFLE_BASE}/games.php",
            params={"page": "my", "key": secret},
            timeout=HTTP_TIMEOUT,
        )
        login.raise_for_status()
        uid = str(session.cookies.get("s_uid") or "").strip()
        if not uid:
            return Pico8SyncResult(False, message="missing s_uid login cookie")

        favs_resp = session.get(
            f"{LEXALOFFLE_BASE}/bbs/",
            params={"uid": uid, "mode": "carts", "list": "user_favourites"},
            timeout=HTTP_TIMEOUT,
        )
        favs_resp.raise_for_status()
        parsed = parse_favourites_from_html(favs_resp.text)

        favourites: list[Pico8Favourite] = []
        for parsed_fav in parsed:
            try:
                nfo = _fetch_nfo(session, parsed_fav.lid)
                if nfo is None:
                    _log.warning("pico8 sync: missing nfo for lid=%s", parsed_fav.lid)
                    continue
                favourite = _merge_page_and_nfo(parsed_fav, nfo)
                content = _download_cart(session, nfo.lid)
                cart_path = root / "bbs" / "carts" / f"{nfo.lid}.p8.png"
                mirror_path = root / "bbs" / nfo.lid[:2] / f"{nfo.lid}.p8.png"
                _atomic_write(cart_path, content)
                _atomic_write(mirror_path, content)
                favourites.append(favourite)
            except Exception as e:
                _log.warning("pico8 sync: failed to sync favourite lid=%s: %s", parsed_fav.lid, e)

        _atomic_write(root / "favourites.txt", _favourites_txt(favourites))
        return Pico8SyncResult(len(favourites) == len(parsed), cart_count=len(favourites))
    except Exception as e:
        _log.warning("pico8 sync: failed: %s", e)
        return Pico8SyncResult(False, message=str(e))
