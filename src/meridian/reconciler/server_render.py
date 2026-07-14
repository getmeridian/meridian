"""Pure rendering for V4 Realm and nginx server artifacts."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from meridian.compiler.models import NginxArtifactPayload, NginxRouteSpec, RealmHopPayload
from meridian.core.errors import MeridianError


class ServerArtifactError(MeridianError):
    """A reviewed server artifact cannot be rendered safely."""


def artifact_token(logical_id: str) -> str:
    return hashlib.sha256(logical_id.encode("utf-8")).hexdigest()[:16]


def realm_config_path(logical_id: str) -> str:
    return f"/etc/meridian/realm/{artifact_token(logical_id)}.toml"


def realm_service_name(logical_id: str) -> str:
    return f"meridian-realm-{artifact_token(logical_id)}"


def render_realm_config(
    payload: RealmHopPayload,
    addresses: Mapping[str, str],
) -> str:
    listen_host = "[::]" if ":" in _address(addresses, payload.server_ref) else "0.0.0.0"
    target = _endpoint(_address(addresses, payload.target_server_ref), payload.target_port)
    return (
        "[network]\n"
        "no_tcp = false\n"
        "use_udp = false\n"
        "\n"
        "[[endpoints]]\n"
        f'listen = "{listen_host}:{payload.listen_port}"\n'
        f'remote = "{target}"\n'
    )


def render_realm_unit(logical_id: str) -> str:
    config_path = realm_config_path(logical_id)
    return (
        "[Unit]\n"
        f"Description=Meridian Realm hop {logical_id}\n"
        "After=network.target\n"
        "StartLimitIntervalSec=300\n"
        "StartLimitBurst=5\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart=/usr/local/bin/realm -c {config_path}\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "LimitNOFILE=65535\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def nginx_artifact_path(logical_id: str, layer: str) -> str:
    token = artifact_token(logical_id)
    if layer == "stream":
        return f"/etc/nginx/stream.d/meridian-v4-{token}.conf"
    return f"/etc/nginx/sites-available/meridian-v4-{token}.conf"


def certificate_directory(hostname: str) -> str:
    token = hashlib.sha256(hostname.encode("utf-8")).hexdigest()[:16]
    return f"/etc/ssl/meridian/certs/{token}"


def render_nginx_artifact(
    logical_id: str,
    payload: NginxArtifactPayload,
    addresses: Mapping[str, str],
) -> str:
    if payload.layer == "stream":
        return _render_stream(logical_id, payload, addresses)
    return _render_http(logical_id, payload, addresses)


def _render_stream(
    logical_id: str,
    payload: NginxArtifactPayload,
    addresses: Mapping[str, str],
) -> str:
    token = artifact_token(logical_id)
    variable = f"meridian_backend_{token}"
    target_names: dict[tuple[str, int], str] = {}
    for route in payload.routes:
        target = (route.backend_server_ref, route.backend_port)
        target_names.setdefault(target, f"meridian_upstream_{token}_{len(target_names)}")

    map_lines: list[str] = []
    for route in payload.routes:
        upstream = target_names[(route.backend_server_ref, route.backend_port)]
        for server_name in route.server_names:
            map_lines.append(f"    {server_name} {upstream};")

    fallback_name = f"meridian_fallback_{token}"
    reject_name = f"meridian_reject_{token}"
    map_lines.append(f"    default {fallback_name if payload.fallback_server_name else reject_name};")
    upstreams = [
        _render_stream_upstream(
            name,
            _backend_address(payload.server_ref, server_ref, addresses),
            port,
        )
        for (server_ref, port), name in target_names.items()
    ]
    if payload.fallback_server_name:
        upstreams.append(
            _render_stream_upstream(
                fallback_name,
                payload.fallback_server_name,
                443,
            )
        )
    else:
        upstreams.append(_render_stream_upstream(reject_name, "127.0.0.1", 9))

    return (
        "# Managed by Meridian v4. Manual edits will be replaced.\n"
        f"map $ssl_preread_server_name ${variable} {{\n"
        + "\n".join(map_lines)
        + "\n}\n\n"
        + "\n".join(upstreams)
        + "\n"
        "server {\n"
        f"    listen {payload.listener_port};\n"
        f"    listen [::]:{payload.listener_port};\n"
        "    ssl_preread on;\n"
        f"    proxy_pass ${variable};\n"
        "    proxy_connect_timeout 5s;\n"
        "    proxy_timeout 30m;\n"
        "    proxy_socket_keepalive on;\n"
        "}\n"
    )


def _render_stream_upstream(name: str, host: str, port: int) -> str:
    return f"upstream {name} {{\n    server {_endpoint(host, port)};\n}}\n"


def _render_http(
    logical_id: str,
    payload: NginxArtifactPayload,
    addresses: Mapping[str, str],
) -> str:
    token = artifact_token(logical_id)
    certificate_dir = certificate_directory(payload.tls_hostname)
    upstreams: list[str] = []
    locations: list[str] = []
    for index, route in enumerate(payload.routes):
        upstream = f"meridian_http_{token}_{index}"
        host = _backend_address(payload.server_ref, route.backend_server_ref, addresses)
        upstreams.append(f"upstream {upstream} {{\n    server {_endpoint(host, route.backend_port)};\n}}\n")
        locations.append(_render_http_route(route, upstream))
    return (
        "# Managed by Meridian v4. Manual edits will be replaced.\n" + "\n".join(upstreams) + "\n"
        "server {\n"
        f"    listen 127.0.0.1:{payload.listener_port} ssl;\n"
        f"    server_name {payload.tls_hostname};\n"
        "    server_tokens off;\n"
        f"    ssl_certificate {certificate_dir}/fullchain.pem;\n"
        f"    ssl_certificate_key {certificate_dir}/key.pem;\n"
        + "\n".join(locations)
        + "\n    location / { return 404; }\n"
        "}\n"
    )


def _render_http_route(route: NginxRouteSpec, upstream: str) -> str:
    path = route.path if route.path.startswith("/") else f"/{route.path}"
    if route.protocol == "wss":
        return (
            f"    location {path} {{\n"
            f"        proxy_pass http://{upstream};\n"
            "        proxy_http_version 1.1;\n"
            "        proxy_set_header Upgrade $http_upgrade;\n"
            '        proxy_set_header Connection "upgrade";\n'
            "        proxy_read_timeout 360s;\n"
            "    }\n"
        )
    return (
        f"    location = {path} {{\n"
        f"        proxy_pass http://{upstream};\n"
        "        proxy_http_version 1.1;\n"
        '        proxy_set_header Connection "";\n'
        "        proxy_read_timeout 86400s;\n"
        "        proxy_send_timeout 86400s;\n"
        "        proxy_buffering off;\n"
        "        proxy_request_buffering off;\n"
        "    }\n"
        f"    location {path}/ {{\n"
        f"        proxy_pass http://{upstream};\n"
        "        proxy_http_version 1.1;\n"
        '        proxy_set_header Connection "";\n'
        "        proxy_read_timeout 86400s;\n"
        "        proxy_send_timeout 86400s;\n"
        "        proxy_buffering off;\n"
        "        proxy_request_buffering off;\n"
        "    }\n"
    )


def _backend_address(
    artifact_server_ref: str,
    backend_server_ref: str,
    addresses: Mapping[str, str],
) -> str:
    if artifact_server_ref == backend_server_ref:
        return "127.0.0.1"
    return _address(addresses, backend_server_ref)


def _address(addresses: Mapping[str, str], server_ref: str) -> str:
    value = addresses.get(server_ref, "")
    if not value:
        raise ServerArtifactError(
            f"Server {server_ref} has no resolved address for artifact rendering.",
            category="user",
        )
    return value


def _endpoint(host: str, port: int) -> str:
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
