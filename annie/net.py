"""HTTP partagé (keep-alive, gzip) + rate limiting."""

from __future__ import annotations

import gzip
import json
import threading
import time
import urllib.error
import urllib.request

_lock = threading.Lock()
_opener: urllib.request.OpenerDirector | None = None


class TokenBucket:
    """Limiteur de débit partagé (Nyaa, Jikan, AniList)."""

    def __init__(self, rate: float, burst: int) -> None:
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._updated_at = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._updated_at
                self._updated_at = now
                self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                time.sleep((1.0 - self._tokens) / self._rate)


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


def fetch_json_post(
    url: str,
    *,
    body: dict,
    user_agent: str,
    timeout: float = 25,
    extra_headers: dict[str, str] | None = None,
) -> dict:
    raw = json.dumps(body).encode("utf-8")
    headers = {
        "User-Agent": user_agent,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    }
    if extra_headers:
        headers.update(extra_headers)
    request = urllib.request.Request(
        url,
        data=raw,
        headers=headers,
        method="POST",
    )
    with _opener_get().open(request, timeout=timeout) as response:
        data = response.read()
        if response.headers.get("Content-Encoding", "").lower() == "gzip":
            data = gzip.decompress(data)
        return json.loads(data.decode("utf-8"))
