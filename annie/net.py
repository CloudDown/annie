"""HTTP partagé (keep-alive, gzip) + rate limiting."""

from __future__ import annotations

import gzip
import http.client
import io
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

_POOL_LOCK = threading.Lock()
_IDLE: dict[tuple[str, str, int], list[http.client.HTTPConnection]] = {}
_OPENED = 0
_MAX_IDLE = 8
_MAX_REDIRECTS = 5
_REDIRECTS = {301, 302, 303, 307, 308}
_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


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


def pool_connections_opened() -> int:
    """Connexions TCP ouvertes depuis le dernier reset (tests)."""
    return _OPENED


def reset_connection_pool() -> None:
    """Ferme les connexions en attente. Les compteurs repartent à zéro."""
    global _OPENED
    with _POOL_LOCK:
        for bucket in _IDLE.values():
            for conn in bucket:
                try:
                    conn.close()
                except Exception:
                    pass
        _IDLE.clear()
        _OPENED = 0


def _uses_proxy(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    if host.lower() in _LOOPBACK:
        return False
    if urllib.request.proxy_bypass(host):
        return False
    proxies = urllib.request.getproxies()
    if not proxies:
        return False
    return bool(
        proxies.get(parts.scheme) or proxies.get("all") or proxies.get("http")
    )


def _split_url(url: str) -> tuple[str, str, int, str]:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise urllib.error.URLError(f"unsupported url: {url}")
    port = parts.port
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    return parts.scheme, parts.hostname, port, path


def _new_connection(
    scheme: str, host: str, port: int, timeout: float
) -> http.client.HTTPConnection:
    global _OPENED
    if scheme == "https":
        conn: http.client.HTTPConnection = http.client.HTTPSConnection(
            host, port, timeout=timeout
        )
    else:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
    with _POOL_LOCK:
        _OPENED += 1
    return conn


def _borrow(
    scheme: str, host: str, port: int, timeout: float
) -> tuple[http.client.HTTPConnection, tuple[str, str, int]]:
    key = (scheme, host.lower(), port)
    conn: http.client.HTTPConnection | None = None
    with _POOL_LOCK:
        bucket = _IDLE.get(key)
        if bucket:
            conn = bucket.pop()
    if conn is None:
        return _new_connection(scheme, host, port, timeout), key
    conn.timeout = timeout
    sock = getattr(conn, "sock", None)
    if sock is not None:
        try:
            sock.settimeout(timeout)
        except OSError:
            _discard(conn)
            return _new_connection(scheme, host, port, timeout), key
    return conn, key


def _release(key: tuple[str, str, int], conn: http.client.HTTPConnection) -> None:
    with _POOL_LOCK:
        bucket = _IDLE.setdefault(key, [])
        if len(bucket) < _MAX_IDLE:
            bucket.append(conn)
            return
    _discard(conn)


def _discard(conn: http.client.HTTPConnection) -> None:
    try:
        conn.close()
    except Exception:
        pass


def _decode_body(payload: bytes, encoding: str) -> bytes:
    if encoding == "gzip" and payload:
        try:
            return gzip.decompress(payload)
        except (OSError, EOFError, gzip.BadGzipFile) as exc:
            raise urllib.error.URLError(exc) from exc
    return payload


def _exchange(
    url: str,
    *,
    user_agent: str,
    timeout: float,
    method: str,
    body: bytes | None,
    headers: dict[str, str] | None,
    redirects: int,
    reuse: bool,
    retry: bool,
) -> bytes:
    scheme, host, port, path = _split_url(url)
    key = (scheme, host.lower(), port)
    if reuse:
        conn, key = _borrow(scheme, host, port, timeout)
    else:
        conn = _new_connection(scheme, host, port, timeout)
    hdrs = {
        "User-Agent": user_agent,
        "Accept-Encoding": "gzip",
        "Connection": "keep-alive",
    }
    if headers:
        hdrs.update(headers)
    try:
        conn.request(method, path, body=body, headers=hdrs)
        response = conn.getresponse()
        payload = response.read()
    except Exception as exc:
        _discard(conn)
        if retry:
            return _exchange(
                url,
                user_agent=user_agent,
                timeout=timeout,
                method=method,
                body=body,
                headers=headers,
                redirects=redirects,
                reuse=False,
                retry=False,
            )
        if isinstance(exc, urllib.error.URLError):
            raise
        raise urllib.error.URLError(exc) from exc

    if response.will_close:
        _discard(conn)
    else:
        _release(key, conn)

    payload = _decode_body(
        payload, (response.getheader("Content-Encoding") or "").lower()
    )
    status = response.status
    if status in _REDIRECTS:
        if redirects >= _MAX_REDIRECTS:
            raise urllib.error.HTTPError(
                url, status, response.reason, response.headers, io.BytesIO(payload)
            )
        location = response.getheader("Location")
        if not location:
            raise urllib.error.HTTPError(
                url, status, response.reason, response.headers, io.BytesIO(payload)
            )
        next_method = method
        next_body = body
        next_headers = dict(headers or {})
        if status in {301, 302, 303} and method != "GET":
            next_method = "GET"
            next_body = None
            next_headers.pop("Content-Type", None)
        return _exchange(
            urllib.parse.urljoin(url, location),
            user_agent=user_agent,
            timeout=timeout,
            method=next_method,
            body=next_body,
            headers=next_headers or None,
            redirects=redirects + 1,
            reuse=True,
            retry=True,
        )
    if status >= 400:
        raise urllib.error.HTTPError(
            url, status, response.reason, response.headers, io.BytesIO(payload)
        )
    return payload


def _fetch_urllib(
    url: str,
    *,
    user_agent: str,
    timeout: float,
    method: str,
    body: bytes | None,
    headers: dict[str, str] | None,
) -> bytes:
    hdrs = {
        "User-Agent": user_agent,
        "Accept-Encoding": "gzip",
    }
    if headers:
        hdrs.update(headers)
    request = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    with urllib.request.build_opener().open(request, timeout=timeout) as response:
        data = response.read()
        if response.headers.get("Content-Encoding", "").lower() == "gzip":
            data = gzip.decompress(data)
        return data


def fetch_bytes(
    url: str,
    *,
    user_agent: str,
    timeout: float = 30,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> bytes:
    if _uses_proxy(url):
        return _fetch_urllib(
            url,
            user_agent=user_agent,
            timeout=timeout,
            method=method,
            body=body,
            headers=headers,
        )
    return _exchange(
        url,
        user_agent=user_agent,
        timeout=timeout,
        method=method,
        body=body,
        headers=headers,
        redirects=0,
        reuse=True,
        retry=True,
    )


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
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    raw = json.dumps(body).encode("utf-8")
    data = fetch_bytes(
        url,
        user_agent=user_agent,
        timeout=timeout,
        method="POST",
        body=raw,
        headers=headers,
    )
    return json.loads(data.decode("utf-8"))
