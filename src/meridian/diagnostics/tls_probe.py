"""Stage-timed TLS flow probe: connect, handshake, then read until EOF or a stall.

Inspection only — certificates are never verified. Each probe runs in a child
interpreter (``network.run_json_helper``) so the wall-clock bound holds and
SIGINT never waits on a socket.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from meridian.diagnostics.network import run_json_helper

TlsFlowStage = Literal["unavailable", "connect", "handshake", "transfer", "complete"]
# ``tls_alert`` means the peer answered at the TLS layer (an alert or non-TLS bytes), so it is reachable.
TlsFlowErrorKind = Literal["", "refused", "timeout", "reset", "tls_alert", "closed", "other"]

_FLOW_HELPER = r"""
import errno, json, socket, ssl, sys, time
request = json.loads(sys.stdin.read())
started = time.monotonic()
deadline = started + float(request["total_timeout"])
out = {"stage": "connect", "error_kind": "", "error": "", "connect_ms": None, "handshake_ms": None,
       "first_byte_ms": None, "transfer_ms": None, "bytes_received": 0, "stalled": False, "head": ""}

def ms():
    return int((time.monotonic() - started) * 1000)

def remaining(limit):
    return max(0.05, min(float(limit), deadline - time.monotonic()))

def classify(exc, connected=True):
    if isinstance(exc, ConnectionRefusedError):
        return "refused"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
        return "reset"
    if isinstance(exc, ssl.SSLEOFError):
        return "closed"
    if isinstance(exc, ssl.SSLError):
        return "tls_alert"
    # macOS reports a socket reset before the handshake as EINVAL/ENOTCONN from getpeername().
    if connected and getattr(exc, "errno", None) in (errno.EINVAL, errno.ENOTCONN, errno.ECONNRESET):
        return "reset"
    return "other"

sock = None
tls = None
try:
    try:
        address = (request["ip"], int(request["port"]))
        sock = socket.create_connection(address, timeout=remaining(request["connect_timeout"]))
    except OSError as exc:
        out["error_kind"], out["error"] = classify(exc, connected=False), str(exc)[:160]
        raise SystemExit
    out["connect_ms"] = ms()
    out["stage"] = "handshake"
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.set_alpn_protocols(["http/1.1"])
    try:
        sock.settimeout(remaining(request["handshake_timeout"]))
        tls = context.wrap_socket(sock, server_hostname=request["sni"] or None)
        sock = None
    except OSError as exc:
        out["error_kind"], out["error"] = classify(exc), str(exc)[:160]
        raise SystemExit
    out["handshake_ms"] = ms()
    payload = request["payload"].encode("ascii")
    if not payload:
        out["stage"] = "complete"
        raise SystemExit
    out["stage"] = "transfer"
    head = b""
    try:
        tls.settimeout(remaining(request["stall_timeout"]))
        tls.sendall(payload)
        limit = int(request["max_bytes"])
        while out["bytes_received"] < limit:
            if time.monotonic() >= deadline:
                out["stalled"] = True
                out["error_kind"], out["error"] = "timeout", "total time limit reached"
                break
            tls.settimeout(remaining(request["stall_timeout"]))
            try:
                chunk = tls.recv(16384)
            except (socket.timeout, TimeoutError):
                out["stalled"] = True
                out["error_kind"], out["error"] = "timeout", "no data before the stall timeout"
                break
            if not chunk:
                out["stage"] = "complete"
                break
            if out["first_byte_ms"] is None:
                out["first_byte_ms"] = ms()
            if len(head) < 256:
                head += chunk[: 256 - len(head)]
            out["bytes_received"] += len(chunk)
        else:
            out["stage"] = "complete"
    except OSError as exc:
        out["error_kind"], out["error"] = classify(exc), str(exc)[:160]
    out["transfer_ms"] = ms()
    out["head"] = head.decode("latin-1")
except SystemExit:
    pass
finally:
    for item in (tls, sock):
        if item is not None:
            try:
                item.close()
            except OSError:
                pass
print(json.dumps(out))
"""


@dataclass(frozen=True)
class TlsFlowResult:
    """What one TLS flow reached, how long each stage took, and how many bytes arrived."""

    ip: str
    port: int
    sni: str
    stage: TlsFlowStage
    error_kind: TlsFlowErrorKind = ""
    error: str = ""
    connect_ms: int | None = None
    handshake_ms: int | None = None
    first_byte_ms: int | None = None
    transfer_ms: int | None = None
    bytes_received: int = 0
    stalled: bool = False
    http_status: int = 0

    @property
    def complete(self) -> bool:
        return self.stage == "complete"

    @property
    def server_answered_tls(self) -> bool:
        """The server (or whatever sits on its address) spoke TLS back: a handshake or an alert."""
        return self.handshake_ms is not None or (self.stage == "handshake" and self.error_kind == "tls_alert")

    @property
    def silenced(self) -> bool:
        """Connect or handshake died the way a filtering middlebox kills flows: timeout or reset."""
        return self.stage in {"connect", "handshake"} and self.error_kind in {"timeout", "reset", "closed"}

    def describe(self) -> str:
        """One plain line naming where the flow ended."""
        if self.stage == "unavailable":
            return "the local probe could not run"
        if self.stage == "connect":
            return f"TCP connect failed ({_ERROR_TEXT.get(self.error_kind, self.error_kind or 'error')})"
        if self.stage == "handshake":
            return f"TLS handshake failed ({_ERROR_TEXT.get(self.error_kind, self.error_kind or 'error')})"
        kib = self.bytes_received / 1024
        if self.stage == "transfer":
            reason = "froze" if self.stalled else _ERROR_TEXT.get(self.error_kind, "failed")
            return f"transfer {reason} after {kib:.1f} KB"
        if self.transfer_ms is not None:
            return f"received {kib:.1f} KB"
        return f"TLS handshake completed in {self.handshake_ms or 0} ms"


_ERROR_TEXT: dict[str, str] = {
    "refused": "connection refused",
    "timeout": "timed out",
    "reset": "connection reset",
    "tls_alert": "the server refused the TLS session",
    "closed": "connection closed",
    "other": "error",
}


def probe_tls_flow(
    ip: str,
    sni: str = "",
    *,
    port: int = 443,
    request: bytes = b"",
    timeout: float = 5,
    stall_timeout: float | None = None,
    max_bytes: int = 1 << 20,
) -> TlsFlowResult:
    """Connect, handshake, optionally send ``request`` and read until EOF, a stall, or ``max_bytes``.

    An empty ``sni`` sends no server name. ``timeout`` bounds connect and handshake
    each; ``stall_timeout`` (default ``timeout``) is the longest silence tolerated
    mid-transfer.
    """
    stall = float(stall_timeout if stall_timeout is not None else timeout)
    total = float(timeout) * 2 + (stall * 2 + 10 if request else 0)
    payload = {
        "ip": ip,
        "port": port,
        "sni": sni,
        "payload": request.decode("ascii"),
        "connect_timeout": float(timeout),
        "handshake_timeout": float(timeout),
        "stall_timeout": stall,
        "total_timeout": total,
        "max_bytes": max_bytes,
    }
    answer = run_json_helper(_FLOW_HELPER, payload, timeout=total + 3)
    if answer is None:
        return TlsFlowResult(ip=ip, port=port, sni=sni, stage="unavailable")
    try:
        return TlsFlowResult(
            ip=ip,
            port=port,
            sni=sni,
            stage=_stage(answer.get("stage")),
            error_kind=_error_kind(answer.get("error_kind")),
            error=str(answer.get("error", "")),
            connect_ms=_optional_int(answer.get("connect_ms")),
            handshake_ms=_optional_int(answer.get("handshake_ms")),
            first_byte_ms=_optional_int(answer.get("first_byte_ms")),
            transfer_ms=_optional_int(answer.get("transfer_ms")),
            bytes_received=int(str(answer.get("bytes_received", 0))),
            stalled=bool(answer.get("stalled", False)),
            http_status=_http_status(str(answer.get("head", ""))),
        )
    except (TypeError, ValueError):
        return TlsFlowResult(ip=ip, port=port, sni=sni, stage="unavailable")


def http_get_request(path: str, host: str) -> bytes:
    """A plain HTTP/1.1 GET that asks for an uncompressed body and a closed connection."""
    lines = [
        f"GET {path} HTTP/1.1",
        f"Host: {host}",
        "User-Agent: Mozilla/5.0",
        "Accept: */*",
        "Accept-Encoding: identity",
        "Connection: close",
        "",
        "",
    ]
    return "\r\n".join(lines).encode("ascii")


def _stage(value: object) -> TlsFlowStage:
    if value in {"connect", "handshake", "transfer", "complete"}:
        return value  # type: ignore[return-value]
    return "unavailable"


def _error_kind(value: object) -> TlsFlowErrorKind:
    if value in {"refused", "timeout", "reset", "tls_alert", "closed", "other"}:
        return value  # type: ignore[return-value]
    return ""


def _optional_int(value: object) -> int | None:
    return None if value is None else int(str(value))


def _http_status(head: str) -> int:
    parts = head.split(" ", 2)
    if len(parts) >= 2 and parts[0].startswith("HTTP/") and parts[1].isdigit():
        return int(parts[1])
    return 0
