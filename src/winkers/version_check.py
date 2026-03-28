"""Check PyPI for newer Winkers versions, with a 24-hour cache."""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

_PYPI_URL = "https://pypi.org/pypi/winkers/json"
_CACHE_PATH = Path.home() / ".cache" / "winkers" / "version_check.json"
_CACHE_TTL = 86400  # 24 hours
_TIMEOUT = 2  # seconds


def _parse_version(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.split("."))
    except Exception:
        return (0,)


def _cached_latest() -> str | None:
    try:
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        if time.time() - data.get("checked_at", 0) < _CACHE_TTL:
            return data.get("latest")
    except Exception:
        pass
    return None


def _fetch_latest() -> str | None:
    try:
        with urllib.request.urlopen(_PYPI_URL, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read())
        latest = data["info"]["version"]
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(
            json.dumps({"latest": latest, "checked_at": time.time()}),
            encoding="utf-8",
        )
        return latest
    except Exception:
        return None


def newer_version_available(current: str) -> str | None:
    """Return latest version string if newer than *current*, else None.

    Uses a 24-hour local cache so the network is rarely hit.
    Never raises — all errors are silently ignored.
    """
    latest = _cached_latest() or _fetch_latest()
    if latest and _parse_version(latest) > _parse_version(current):
        return latest
    return None
