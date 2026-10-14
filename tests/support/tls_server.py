"""Local TLS servers on 127.0.0.1 that misbehave the way censors make real servers misbehave."""

from __future__ import annotations

import shutil
import socket
import ssl
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

import pytest

Behavior = Literal["serve", "stall", "silent", "reset", "alert"]


def self_signed_cert(directory: Path) -> tuple[Path, Path]:
    """Create a throwaway self-signed certificate, skipping when openssl is absent."""
    if shutil.which("openssl") is None:
        pytest.skip("openssl is required for the local TLS server fixture")
    cert = directory / "cert.pem"
    key = directory / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


@contextmanager
def tls_server(
    cert: Path,
    key: Path,
    *,
    behavior: Behavior = "serve",
    body_bytes: int = 64 * 1024,
    stall_after: int = 16 * 1024,
    status: int = 200,
) -> Iterator[int]:
    """Serve one behavior on an ephemeral 127.0.0.1 port and yield the port.

    ``serve`` answers any request with ``body_bytes`` and closes; ``stall`` sends
    ``stall_after`` bytes then goes silent; ``silent`` accepts TCP and never
    speaks TLS; ``reset`` accepts TCP and resets it; ``alert`` refuses every
    handshake with a TLS alert.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    if behavior == "alert":
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.set_alpn_protocols(["h2-only-never-offered"])
        context.set_servername_callback(lambda *_: ssl.ALERT_DESCRIPTION_UNRECOGNIZED_NAME)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    stop = threading.Event()
    held: list[socket.socket] = []

    def handle(conn: socket.socket) -> None:
        try:
            if behavior == "silent":
                held.append(conn)
                stop.wait(30)
                return
            if behavior == "reset":
                conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, b"\x01\x00\x00\x00\x00\x00\x00\x00")
                conn.close()
                return
            tls = context.wrap_socket(conn, server_side=True)
            tls.settimeout(5)
            try:
                tls.recv(4096)
            except (TimeoutError, OSError):
                tls.close()
                return
            body = b"x" * body_bytes
            head = f"HTTP/1.1 {status} OK\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
            payload = head + body
            if behavior == "stall":
                tls.sendall(payload[:stall_after])
                held.append(tls)
                stop.wait(30)
                return
            tls.sendall(payload)
            tls.close()
        except (ssl.SSLError, OSError):
            conn.close()

    def accept_loop() -> None:
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except (TimeoutError, OSError):
                continue
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    thread = threading.Thread(target=accept_loop, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1]
    finally:
        stop.set()
        thread.join(timeout=2)
        listener.close()
        for item in held:
            try:
                item.close()
            except OSError:
                pass


def closed_port() -> int:
    """Return a 127.0.0.1 port that nothing listens on."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port
