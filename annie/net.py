"""HTTP partagé (keep-alive, gzip)."""

from __future__ import annotations

import gzip
import json
import threading
import urllib.error
import urllib.request

_lock = threading.Lock()
_opener: urllib.request.OpenerDirector | None = None


def _opener_get() -> urllib.request.OpenerDirector:
    global _opener
    if _opener is None:
        with _lock:
            if _opener is None:
                _opener = urllib.request.build_opener()
    return _opener


def fetch_bytes(
    url: str,
    *,
    user_agent: str,
    timeout: float = 30,
) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip",
        },
    )
    with _opener_get().open(request, timeout=timeout) as response:
        data = response.read()
        if response.headers.get("Content-Encoding", "").lower() == "gzip":
            data = gzip.decompress(data)
        return data


def fetch_text(
    url: str,
    *,
    user_agent: str,
    timeout: float = 30,
) -> str:
    return fetch_bytes(url, user_agent=user_agent, timeout=timeout).decode(
        "utf-8",
        errors="replace",
    )


def fetch_json(
    url: str,
    *,
    user_agent: str,
    timeout: float = 25,
) -> dict:
    return json.loads(
        fetch_bytes(url, user_agent=user_agent, timeout=timeout).decode("utf-8")
    )
