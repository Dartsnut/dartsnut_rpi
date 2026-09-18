"""Sync Lexaloffle PICO-8 favourites into Splore's favourites.txt.

PICO-8 0.2.7+ downloads any cart that appears in a Splore list, so this
module only consolidates the list. It does not fetch .p8.png carts.
"""

from __future__ import annotations

import ast
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

_log = logging.getLogger(__name__)

LEXALOFFLE_BASE = "https://www.lexaloffle.com"
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
    pid: str = ""


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


def _valid_cart_lid(value: Any) -> str:
    lid = str(value or "").strip()
    if not lid or lid == "0":
        return ""
    return lid


def _mid_from_nfo(lid: str, mid: str) -> str:
    mid = str(mid or "").strip()
    if mid.isdigit() or not mid:
        stem = str(lid or "").rsplit("-", 1)[0].strip()
        return stem or mid
    return mid


def _lid_from_thumb(thumb: str) -> str:
    name = Path(str(thumb or "").replace("\\", "/")).name
    stem = name.rsplit(".", 1)[0].strip()
    if stem.startswith("pico8_"):
        return _valid_cart_lid(stem[6:])
    if stem.startswith("pico") and stem[4:].isdigit():
        return stem[4:]
    return ""


def _is_cart_lid(value: str) -> bool:
    lid = _valid_cart_lid(value)
    return bool(lid) and " " not in lid


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
        display_mid = str(row[22] or "").strip()
        version = str(row[17] or "").strip()
        thumb_lid = _lid_from_thumb(row[3] if len(row) > 3 else "")
        lid = thumb_lid or (
            f"{display_mid}-{version}" if display_mid and version else display_mid
        )
        if not lid:
            continue
        mid = _mid_from_nfo(lid, "" if thumb_lid else display_mid)
        title = str(row[2] or display_mid or lid).strip()
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
                pid=str(row[0] or "").strip(),
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
    nfo_lid = _valid_cart_lid(data.get("lid"))
    if not nfo_lid:
        return None
    mid = _mid_from_nfo(nfo_lid, data.get("mid") or "")
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
        mid=_mid_from_nfo(nfo.lid, nfo.mid) or page.mid,
        title=page.title or nfo.title,
        author=nfo.author or page.author,
        ts=nfo.ts or page.ts,
        catsub=nfo.catsub or page.catsub,
        pid=page.pid or nfo.pid,
    )


def _favourites_txt(favourites: list[Pico8Favourite]) -> bytes:
    # Native columns: lid | mid | catsub | author | timestamp | title.
    # Splore uses the last field as the cart name.
    lines = [
        "|%-20s |%-20s |%-6d |%-16s |%-20s |%s\n"
        % (fav.lid, fav.mid, fav.catsub, fav.author, fav.ts, fav.title)
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
    started = time.monotonic()
    _log.info("pico8 sync: consolidating favourites into %s", root)
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
            _log.warning("pico8 sync: login missing s_uid after %.2fs", time.monotonic() - started)
            return Pico8SyncResult(False, message="missing s_uid login cookie")
        _log.info(
            "pico8 sync: logged in uid=%s elapsed=%.2fs",
            uid,
            time.monotonic() - started,
        )

        favs_resp = session.get(
            f"{LEXALOFFLE_BASE}/bbs/",
            params={"uid": uid, "mode": "carts", "list": "user_favourites"},
            timeout=HTTP_TIMEOUT,
        )
        favs_resp.raise_for_status()
        parsed = parse_favourites_from_html(favs_resp.text)
        _log.info(
            "pico8 sync: parsed %d favourites from BBS elapsed=%.2fs",
            len(parsed),
            time.monotonic() - started,
        )

        favourites: list[Pico8Favourite] = []
        total = len(parsed)
        nfo_needed = 0
        for index, parsed_fav in enumerate(parsed, start=1):
            if _is_cart_lid(parsed_fav.lid):
                favourite = parsed_fav
                if not favourite.mid:
                    favourite = Pico8Favourite(
                        lid=parsed_fav.lid,
                        mid=_mid_from_nfo(parsed_fav.lid, parsed_fav.mid),
                        title=parsed_fav.title,
                        author=parsed_fav.author,
                        ts=parsed_fav.ts,
                        catsub=parsed_fav.catsub,
                        pid=parsed_fav.pid,
                    )
                favourites.append(favourite)
                continue
            nfo_needed += 1
            lookup = parsed_fav.pid or parsed_fav.lid
            nfo_started = time.monotonic()
            _log.info(
                "pico8 sync: metadata lookup %d/%d pid=%s title=%s",
                index,
                total,
                lookup,
                parsed_fav.title,
            )
            try:
                nfo = _fetch_nfo(session, lookup)
                if nfo is None:
                    _log.warning(
                        "pico8 sync: missing metadata %d/%d lookup=%s title=%s elapsed=%.2fs",
                        index,
                        total,
                        lookup,
                        parsed_fav.title,
                        time.monotonic() - nfo_started,
                    )
                    continue
                favourites.append(_merge_page_and_nfo(parsed_fav, nfo))
            except Exception as e:
                _log.warning(
                    "pico8 sync: failed metadata %d/%d lookup=%s title=%s elapsed=%.2fs: %s",
                    index,
                    total,
                    lookup,
                    parsed_fav.title,
                    time.monotonic() - nfo_started,
                    e,
                )
        if nfo_needed:
            _log.info(
                "pico8 sync: used BBS thumbs for %d/%d carts; metadata lookups=%d",
                total - nfo_needed,
                total,
                nfo_needed,
            )

        _atomic_write(root / "favourites.txt", _favourites_txt(favourites))
        result = Pico8SyncResult(len(favourites) == len(parsed), cart_count=len(favourites))
        _log.info(
            "pico8 sync: wrote %d/%d favourites ok=%s elapsed=%.2fs path=%s",
            result.cart_count,
            total,
            result.ok,
            time.monotonic() - started,
            root / "favourites.txt",
        )
        return result
    except Exception as e:
        _log.warning("pico8 sync: failed after %.2fs: %s", time.monotonic() - started, e)
        return Pico8SyncResult(False, message=str(e))
