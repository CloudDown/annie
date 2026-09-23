"""Keep-alive HTTP et erreurs, sans sortir de la machine."""

from __future__ import annotations

import gzip
import json
import socket
import threading
import unittest
import urllib.error

from annie.net import (
    fetch_bytes,
    fetch_json_post,
    fetch_text,
    pool_connections_opened,
    reset_connection_pool,
)


def _read_request(conn: socket.socket, buf: bytes) -> tuple[bytes, bytes] | None:
    while b"\r\n\r\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            return None
        buf += chunk
    head, rest = buf.split(b"\r\n\r\n", 1)
    length = 0
    for line in head.split(b"\r\n")[1:]:
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1].strip())
    while len(rest) < length:
        chunk = conn.recv(4096)
        if not chunk:
            break
        rest += chunk
    return head, rest[length:]


def _start_server(handler) -> tuple[int, dict, threading.Event]:
    result: dict = {"accepts": 0}
    done = threading.Event()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    port = srv.getsockname()[1]

    def run() -> None:
        srv.settimeout(3)
        try:
            handler(srv, result)
        finally:
            try:
                srv.close()
            except OSError:
                pass
            done.set()

    threading.Thread(target=run, daemon=True).start()
    return port, result, done


def _serve_keepalive(requests: int = 2) -> tuple[int, dict, threading.Event]:
    def handler(srv: socket.socket, result: dict) -> None:
        conn, _ = srv.accept()
        result["accepts"] += 1
        conn.settimeout(3)
        buf = b""
        try:
            for _ in range(requests):
                parsed = _read_request(conn, buf)
                if parsed is None:
                    return
                _head, buf = parsed
                result["requests"] = result.get("requests", 0) + 1
                body = b"ok"
                conn.sendall(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Length: 2\r\n"
                    b"Connection: keep-alive\r\n"
                    b"\r\n" + body
                )
            srv.settimeout(0.3)
            try:
                extra, _ = srv.accept()
                result["accepts"] += 1
                extra.close()
            except socket.timeout:
                pass
        finally:
            conn.close()

    return _start_server(handler)


class KeepAliveTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_connection_pool()

    def tearDown(self) -> None:
        reset_connection_pool()

    def test_second_request_reuses_the_connection(self) -> None:
        port, result, done = _serve_keepalive()
        url = f"http://127.0.0.1:{port}/a"
        self.assertEqual(fetch_text(url, user_agent="test"), "ok")
        self.assertEqual(fetch_text(url, user_agent="test"), "ok")
        self.assertTrue(done.wait(3))
        self.assertEqual(result["accepts"], 1)
        self.assertEqual(result["requests"], 2)
        self.assertEqual(pool_connections_opened(), 1)

    def test_dead_connection_is_retried(self) -> None:
        def handler(srv: socket.socket, result: dict) -> None:
            first, _ = srv.accept()
            result["accepts"] += 1
            first.close()
            second, _ = srv.accept()
            result["accepts"] += 1
            second.settimeout(3)
            parsed = _read_request(second, b"")
            if parsed is None:
                second.close()
                return
            second.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\nConnection: close\r\n\r\n"
                b"fine"
            )
            second.close()

        port, result, done = _start_server(handler)
        body = fetch_bytes(f"http://127.0.0.1:{port}/x", user_agent="test", timeout=2)
        self.assertEqual(body, b"fine")
        self.assertTrue(done.wait(3))
        self.assertEqual(result["accepts"], 2)

    def test_http_error_keeps_the_status_code(self) -> None:
        def handler(srv: socket.socket, result: dict) -> None:
            conn, _ = srv.accept()
            result["accepts"] += 1
            conn.settimeout(3)
            parsed = _read_request(conn, b"")
            if parsed is not None:
                conn.sendall(
                    b"HTTP/1.1 429 Too Many\r\nContent-Length: 0\r\n"
                    b"Connection: close\r\n\r\n"
                )
            conn.close()

        port, _result, done = _start_server(handler)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            fetch_bytes(f"http://127.0.0.1:{port}/limited", user_agent="test")
        self.assertEqual(caught.exception.code, 429)
        self.assertTrue(done.wait(3))

    def test_gzip_and_redirect(self) -> None:
        payload = gzip.compress(b'{"ok": true}')

        def handler(srv: socket.socket, result: dict) -> None:
            conn, _ = srv.accept()
            result["accepts"] += 1
            conn.settimeout(3)
            buf = b""
            try:
                for _ in range(2):
                    parsed = _read_request(conn, buf)
                    if parsed is None:
                        return
                    head, buf = parsed
                    path = head.split(b"\r\n", 1)[0].split(b" ")[1]
                    if path == b"/go":
                        body = b""
                        raw = (
                            b"HTTP/1.1 302 Found\r\n"
                            b"Location: /done\r\n"
                            b"Content-Length: 0\r\n"
                            b"Connection: keep-alive\r\n\r\n"
                        )
                        conn.sendall(raw)
                        continue
                    raw = (
                        b"HTTP/1.1 200 OK\r\n"
                        b"Content-Type: application/json\r\n"
                        b"Content-Encoding: gzip\r\n"
                        b"Content-Length: " + str(len(payload)).encode() + b"\r\n"
                        b"Connection: close\r\n\r\n"
                    )
                    conn.sendall(raw + payload)
                    return
            finally:
                conn.close()

        port, result, done = _start_server(handler)
        data = fetch_json_post(
            f"http://127.0.0.1:{port}/go",
            body={"q": 1},
            user_agent="test",
        )
        self.assertEqual(data, {"ok": True})
        self.assertTrue(done.wait(3))
        self.assertEqual(result["accepts"], 1)
        self.assertIsInstance(json.dumps(data), str)


if __name__ == "__main__":
    unittest.main()
