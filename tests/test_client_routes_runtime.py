"""Real Xray sniffing over loopback; opt in with MERIDIAN_TEST_XRAY=/path/to/xray.

CI supplies the checksum-verified pinned runtime. No downloads happen in these
tests, and both DIRECT and the stand-in VPN outbound terminate on loopback.
"""

from __future__ import annotations

import copy
import json
import os
import select
import socket
import struct
import subprocess
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

import pytest

from meridian.compiler.delivery import xray_json_template
from meridian.config import XRAY_VERSION

_HANDSHAKE = b"\x13BitTorrent protocol" + bytes(8) + bytes(range(20)) + b"-MR0001-123456789012"
_DHT_PING = b"d1:ad2:id20:abcdefghij0123456789e1:q4:ping1:t2:aa1:y1:qe"


@pytest.fixture(scope="module")
def xray_binary() -> Path:
    configured = os.environ.get("MERIDIAN_TEST_XRAY")
    if not configured:
        pytest.skip("set MERIDIAN_TEST_XRAY to run real Xray routing tests")
    binary = Path(configured).resolve()
    version = subprocess.run([str(binary), "version"], capture_output=True, text=True, check=True, timeout=10)
    assert version.stdout.startswith(f"Xray {XRAY_VERSION} "), version.stdout
    return binary


def _bound_socket(kind: socket.SocketKind) -> socket.socket:
    sock = socket.socket(socket.AF_INET, kind)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(3)
    if kind == socket.SOCK_STREAM:
        sock.listen()
    return sock


def _read_exact(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        assert chunk, f"connection closed after {len(data)}/{size} bytes"
        data += chunk
    return data


def _socks_request(sock: socket.socket, command: int, address: bytes, port: int) -> tuple[str, int]:
    sock.sendall(b"\x05\x01\x00")
    assert _read_exact(sock, 2) == b"\x05\x00"
    sock.sendall(bytes([5, command, 0]) + address + struct.pack("!H", port))
    assert _read_exact(sock, 4) == b"\x05\x00\x00\x01"
    return socket.inet_ntoa(_read_exact(sock, 4)), struct.unpack("!H", _read_exact(sock, 2))[0]


@contextmanager
def _running_xray(binary: Path, tmp_path: Path, kind: socket.SocketKind) -> Iterator[tuple[dict, list, Path]]:
    with ExitStack() as stack:
        peers = [stack.enter_context(_bound_socket(kind)) for _ in range(2)]
        config = copy.deepcopy(xray_json_template())
        config.pop("remnawave")
        config.pop("burstObservatory")  # Never probe external destinations.
        # Keep every generated rule, including the final balancer rule. Replace
        # only health-based selection: the sole local stand-in needs no probe.
        config["routing"]["balancers"][0]["strategy"] = {"type": "random"}
        config["routing"]["balancers"][0].pop("fallbackTag")
        config["outbounds"][0]["settings"] = {"redirect": f"127.0.0.1:{peers[0].getsockname()[1]}"}
        config["outbounds"].append(
            {
                "tag": "MERIDIAN_PROXY_TEST",
                "protocol": "freedom",
                "settings": {"redirect": f"127.0.0.1:{peers[1].getsockname()[1]}"},
            }
        )
        ports = {}
        # Reserve distinct ephemeral ports until both have been assigned.
        with _bound_socket(socket.SOCK_STREAM) as socks, _bound_socket(socket.SOCK_STREAM) as http:
            for inbound, reserved in zip(config["inbounds"], (socks, http), strict=True):
                inbound["port"] = reserved.getsockname()[1]
                ports[inbound["tag"]] = inbound["port"]
        access = tmp_path / "access.log"
        config["log"] = {"loglevel": "debug", "access": str(access)}
        config_file = tmp_path / "xray.json"
        config_file.write_text(json.dumps(config), encoding="utf-8")
        env = {**os.environ, "XRAY_LOCATION_ASSET": str(binary.parent)}
        validation = subprocess.run(
            [str(binary), "run", "-test", "-c", str(config_file)],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert validation.returncode == 0, validation.stdout + validation.stderr
        log = stack.enter_context((tmp_path / "xray.log").open("w+", encoding="utf-8"))
        process = subprocess.Popen(
            [str(binary), "run", "-c", str(config_file)], env=env, stdout=log, stderr=subprocess.STDOUT
        )
        try:
            deadline = time.monotonic() + 5
            while True:
                assert process.poll() is None, (tmp_path / "xray.log").read_text()
                try:
                    with socket.create_connection(("127.0.0.1", ports["SOCKS"]), timeout=0.1):
                        break
                except OSError:
                    assert time.monotonic() < deadline, "Xray did not open SOCKS listener"
                    time.sleep(0.02)
            yield ports, peers, access
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.parametrize(
    "inbound,payload,expected",
    [
        ("SOCKS", _HANDSHAKE, 0),
        ("HTTP", _HANDSHAKE, 0),
        ("SOCKS", b"ordinary non-BitTorrent TCP payload", 1),
        ("HTTP", b"ordinary non-BitTorrent TCP payload", 1),
    ],
    ids=["socks-bittorrent-direct", "http-bittorrent-direct", "socks-other-vpn", "http-other-vpn"],
)
def test_tcp_handshake_uses_direct_while_other_traffic_uses_vpn(xray_binary, tmp_path, inbound, payload, expected):
    with _running_xray(xray_binary, tmp_path, socket.SOCK_STREAM) as (ports, peers, _):
        with socket.create_connection(("127.0.0.1", ports[inbound]), timeout=3) as client:
            # A domain avoids the private-IP bypass. The final rule matches
            # before IPIfNonMatch DNS lookup; freedom redirects to loopback.
            if inbound == "SOCKS":
                _socks_request(client, 1, b"\x03\x10peer.example.org", 6881)
            else:
                client.sendall(b"CONNECT peer.example.org:6881 HTTP/1.1\r\nHost: peer.example.org:6881\r\n\r\n")
                response = b""
                while not response.endswith(b"\r\n\r\n"):
                    response += _read_exact(client, 1)
                assert b" 200 " in response
            client.sendall(payload)
            ready, _, _ = select.select(peers, [], [], 3)
            assert ready == [peers[expected]], "payload used the wrong outbound or never arrived"
            with ready[0].accept()[0] as remote:
                remote.settimeout(3)
                assert _read_exact(remote, len(payload)) == payload
                remote.sendall(b"received")
            assert _read_exact(client, 8) == b"received"


@pytest.mark.parametrize("packet,port,blocked", [("dht", 6881, False), ("utp", 6881, False), ("dht", 443, True)])
def test_udp_limitations_and_existing_quic_block(xray_binary, tmp_path, packet, port, blocked):
    # BEP 29: ST_SYN/version 1, no extension, uint32 microsecond timestamp.
    payload = (
        struct.pack("!BBHIIIHH", 0x41, 0, 1, (time.time_ns() // 1000) & 0xFFFFFFFF, 0, 65536, 1, 0)
        if packet == "utp"
        else _DHT_PING
    )
    with _running_xray(xray_binary, tmp_path, socket.SOCK_DGRAM) as (ports, peers, access):
        with socket.create_connection(("127.0.0.1", ports["SOCKS"]), timeout=3) as control:
            relay = _socks_request(control, 3, b"\x01\x00\x00\x00\x00", 0)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                client.sendto(b"\x00\x00\x00\x03\x10peer.example.org" + struct.pack("!H", port) + payload, relay)
                ready, _, _ = select.select(peers, [], [], 1 if blocked else 3)
                if blocked:
                    assert not ready
                    assert "[SOCKS -> BLOCK]" in access.read_text(encoding="utf-8")
                else:
                    assert ready == [peers[1]], "pinned Xray UDP behavior changed; review the documented limit"
                    assert peers[1].recvfrom(4096)[0] == payload
