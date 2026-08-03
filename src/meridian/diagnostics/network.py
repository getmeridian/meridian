"""Low-level client-side HTTPS and certificate inspection helpers."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import subprocess
import sys

_HTTPS_HELPER = r"""
import base64, http.client, json, socket, ssl, sys
request = json.loads(sys.stdin.read())
sock = None
tls_sock = None
try:
    timeout = float(request["timeout"])
    sock = socket.create_connection((request["ip"], int(request["port"])), timeout=timeout)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    tls_sock = context.wrap_socket(sock, server_hostname=request["server_name"] or request["ip"])
    sock = None
    headers = {
        "Host": request["host"],
        "User-Agent": "Mozilla/5.0",
        "Connection": "close",
        **request["extra_headers"],
    }
    lines = [f'GET {request["path"]} HTTP/1.1', *(f"{key}: {value}" for key, value in headers.items()), "", ""]
    tls_sock.sendall("\r\n".join(lines).encode("ascii"))
    response = http.client.HTTPResponse(tls_sock)
    response.begin()
    body = response.read(4096)
    print(json.dumps({
        "status": response.status,
        "headers": {key.lower(): value for key, value in response.getheaders()},
        "body": base64.b64encode(body).decode("ascii"),
    }))
except Exception:
    print('{"status":0,"headers":{},"body":""}')
finally:
    if tls_sock is not None:
        tls_sock.close()
    if sock is not None:
        sock.close()
"""

_TLS_HELPER = r"""
import base64, json, socket, ssl, sys, warnings
request = json.loads(sys.stdin.read())
sock = None
tls_sock = None
try:
    timeout = float(request["timeout"])
    if request["verify"]:
        context = ssl.create_default_context()
    else:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    if request["alpn"]:
        context.set_alpn_protocols(request["alpn"])
    if request["tls_version"]:
        context.set_ciphers("ALL:@SECLEVEL=0")
        version = getattr(ssl.TLSVersion, request["tls_version"])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            context.minimum_version = version
            context.maximum_version = version
    sock = socket.create_connection((request["ip"], int(request["port"])), timeout=timeout)
    tls_sock = context.wrap_socket(sock, server_hostname=request["server_name"] or request["ip"])
    sock = None
    certificate = tls_sock.getpeercert(binary_form=True) if request["certificate"] else b""
    print(json.dumps({
        "status": "ok",
        "certificate": base64.b64encode(certificate or b"").decode("ascii"),
        "alpn": tls_sock.selected_alpn_protocol() or "",
        "version": tls_sock.version() or "",
        "error": "",
    }))
except ssl.SSLCertVerificationError as exc:
    print(json.dumps({"status":"invalid","error":exc.verify_message or str(exc)}))
except ssl.SSLError as exc:
    print(json.dumps({"status":"tls_error","error":str(exc)}))
except Exception:
    print('{"status":"unavailable","error":""}')
finally:
    if tls_sock is not None:
        tls_sock.close()
    if sock is not None:
        sock.close()
"""


def resolve_hostname(host: str, *, timeout: float = 5) -> str | None:
    """Resolve a hostname, returning empty for NXDOMAIN and None when unavailable."""
    script = (
        "import socket,sys\n"
        "try:\n"
        " items=socket.getaddrinfo(sys.argv[1],443,socket.AF_UNSPEC,socket.SOCK_STREAM)\n"
        "except socket.gaierror as exc:\n"
        " sys.exit(0 if exc.errno in {socket.EAI_NONAME,getattr(socket,'EAI_NODATA',socket.EAI_NONAME)} else 2)\n"
        "for item in items:\n"
        " print(item[4][0])\n"
    )
    output = _run_dns_helper(script, host, timeout=timeout)
    if output is None:
        return None
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for line in output.splitlines():
        try:
            address = ipaddress.ip_address(line.strip())
        except ValueError:
            continue
        if address not in addresses:
            addresses.append(address)
    addresses.sort(key=lambda address: (address.version, str(address)))
    return str(addresses[0]) if addresses else ""


def reverse_hostname(ip: str, *, timeout: float = 3) -> str | None:
    """Return a PTR hostname, empty for no record, or None when timed out."""
    script = "import socket,sys\nprint(socket.gethostbyaddr(sys.argv[1])[0])\n"
    try:
        result = subprocess.run(
            [sys.executable, "-c", script, ip],
            capture_output=True,
            text=True,
            timeout=max(0.1, float(timeout)),
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return None
    except (FileNotFoundError, OSError, ValueError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _run_dns_helper(script: str, value: str, *, timeout: float) -> str | None:
    try:
        result = subprocess.run(
            [sys.executable, "-c", script, value],
            capture_output=True,
            text=True,
            timeout=max(0.1, float(timeout)),
            stdin=subprocess.DEVNULL,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        return None
    return result.stdout if result.returncode == 0 else None


def https_get(
    ip: str,
    path: str,
    *,
    timeout: float = 5,
    server_name: str = "",
    host_header: str = "",
    extra_headers: dict[str, str] | None = None,
    port: int = 443,
) -> tuple[int, dict[str, str], bytes]:
    """Issue a wall-clock-bounded HTTPS request with explicit SNI and Host."""
    host = host_header or server_name or (f"[{ip}]" if ":" in ip else ip)
    deadline = max(0.1, float(timeout))
    request = {
        "ip": ip,
        "path": path,
        "timeout": deadline,
        "server_name": server_name,
        "host": host,
        "extra_headers": extra_headers or {},
        "port": port,
    }
    try:
        result = subprocess.run(
            [sys.executable, "-c", _HTTPS_HELPER],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=deadline,
        )
        payload = json.loads(result.stdout)
        status = int(payload.get("status", 0))
        headers = payload.get("headers", {})
        body = base64.b64decode(payload.get("body", ""), validate=True)
        if result.returncode != 0 or not isinstance(headers, dict):
            return 0, {}, b""
        return status, {str(key): str(value) for key, value in headers.items()}, body
    except (subprocess.TimeoutExpired, OSError, ValueError, TypeError, json.JSONDecodeError):
        return 0, {}, b""


def get_certificate_der(
    ip: str,
    server_name: str,
    *,
    port: int = 443,
    timeout: float = 5,
) -> bytes:
    """Return a peer certificate after a wall-clock-bounded TLS handshake."""
    payload = _tls_probe(ip, server_name, port=port, timeout=timeout, certificate=True)
    if payload is None or payload.get("status") != "ok":
        return b""
    try:
        return base64.b64decode(str(payload.get("certificate", "")), validate=True)
    except (ValueError, TypeError):
        return b""


def certificate_validation_error(
    ip: str,
    server_name: str,
    *,
    port: int = 443,
    timeout: float = 5,
) -> str | None:
    """Return an error, an empty valid result, or None for unavailable evidence."""
    payload = _tls_probe(ip, server_name, port=port, timeout=timeout, verify=True)
    if payload is None:
        return None
    if payload.get("status") == "ok":
        return ""
    if payload.get("status") == "invalid":
        return str(payload.get("error", "certificate validation failed"))
    return None


def negotiate_alpn(
    ip: str,
    server_name: str,
    protocols: list[str],
    *,
    port: int = 443,
    timeout: float = 5,
) -> str | None:
    """Return negotiated ALPN, empty for none, or None when unavailable."""
    payload = _tls_probe(ip, server_name, port=port, timeout=timeout, alpn=protocols)
    if payload is None or payload.get("status") != "ok":
        return None
    return str(payload.get("alpn", ""))


def probe_tls_version(
    ip: str,
    server_name: str,
    tls_version: str,
    *,
    port: int = 443,
    timeout: float = 5,
) -> str:
    """Return accepted, rejected, or unavailable for one TLS version."""
    payload = _tls_probe(
        ip,
        server_name,
        port=port,
        timeout=timeout,
        tls_version=tls_version,
    )
    if payload is None:
        return "unavailable"
    if payload.get("status") == "ok":
        return "accepted"
    reason = str(payload.get("error", "")).upper()
    if payload.get("status") == "tls_error" and (
        "ALERT_PROTOCOL_VERSION" in reason or "ALERT_HANDSHAKE_FAILURE" in reason
    ):
        return "rejected"
    return "unavailable"


def _tls_probe(
    ip: str,
    server_name: str,
    *,
    port: int,
    timeout: float,
    verify: bool = False,
    certificate: bool = False,
    alpn: list[str] | None = None,
    tls_version: str = "",
) -> dict[str, object] | None:
    deadline = max(0.1, float(timeout))
    request = {
        "ip": ip,
        "server_name": server_name,
        "port": port,
        "timeout": deadline,
        "verify": verify,
        "certificate": certificate,
        "alpn": alpn or [],
        "tls_version": tls_version,
    }
    try:
        result = subprocess.run(
            [sys.executable, "-c", _TLS_HELPER],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            timeout=deadline,
        )
        payload = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return payload if result.returncode == 0 and isinstance(payload, dict) else None


def certificate_identity(certificate: bytes) -> str:
    try:
        result = subprocess.run(
            ["openssl", "x509", "-inform", "DER", "-noout", "-subject", "-issuer"],
            input=certificate,
            capture_output=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().decode(errors="replace")
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return hashlib.sha256(certificate).hexdigest()


def certificate_text(certificate: bytes) -> str:
    try:
        result = subprocess.run(
            ["openssl", "x509", "-inform", "DER", "-text", "-noout"],
            input=certificate,
            capture_output=True,
            text=False,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.decode(errors="replace")
