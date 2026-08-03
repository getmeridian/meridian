"""Deployment-aware orchestration for client-side active probe checks."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from queue import Empty, Queue
from threading import Event, Thread
from typing import cast

from meridian.core.verification import VerificationCheck
from meridian.diagnostics.external import (
    check_domain_root,
    check_http2_support,
    check_http_response,
    check_internal_ports,
    check_legacy_tls,
    check_port_surface,
    check_proxy_paths,
    check_reverse_dns,
    check_root_indistinguishable,
    check_secret_paths,
    check_sni_camouflage,
    check_sni_consistency,
    check_tls_certificate,
    check_websocket_upgrade,
)
from meridian.diagnostics.surface import check_no_public_surface, check_udp_surface
from meridian.verification import DeploymentVerificationContext, VerificationHttpsRoute


def run_probe_checks(
    ip: str,
    context: DeploymentVerificationContext,
    *,
    requested_sni: str = "",
    timeout: float = 5,
) -> list[VerificationCheck]:
    """Run independent public checks concurrently and return stable ordering."""
    routes = _effective_routes(context, requested_sni=requested_sni)
    qualify_ids = len(routes) > 1
    jobs: list[Callable[[], VerificationCheck]] = [
        lambda: check_port_surface(ip, expected_tcp_ports=context.public_tcp_ports, timeout=min(timeout, 3)),
        lambda: check_reverse_dns(ip, timeout=min(timeout, 3)),
    ]

    def schedule(route: VerificationHttpsRoute, callback: Callable[[], VerificationCheck]) -> None:
        jobs.append(partial(_run_route_check, route, callback, qualify=qualify_ids))

    for route in routes:
        tls_sni = route.tls_sni or ip
        host_header = route.host_header
        managed = route.kind == "managed"
        camouflage = route.kind == "camouflage"
        schedule(
            route,
            partial(
                check_http_response,
                ip,
                meridian_mode=managed,
                camouflage_origin=camouflage,
                server_name=tls_sni,
                host_header=host_header,
                port=route.port,
                timeout=timeout,
            ),
        )
        schedule(
            route,
            partial(check_tls_certificate, ip, server_name=tls_sni, port=route.port, timeout=timeout),
        )
        schedule(
            route,
            partial(
                check_sni_consistency,
                ip,
                tls_sni,
                meridian_mode=managed,
                port=route.port,
                timeout=timeout,
            ),
        )
        schedule(
            route,
            partial(
                check_http2_support,
                ip,
                tls_sni,
                meridian_mode=managed,
                port=route.port,
                timeout=timeout,
            ),
        )
        schedule(
            route,
            partial(
                check_legacy_tls,
                ip,
                tls_sni,
                port=route.port,
                timeout=min(timeout, 3),
                route_kind=route.kind,
            ),
        )
        if managed:
            schedule(
                route,
                partial(
                    check_proxy_paths,
                    ip,
                    meridian_mode=True,
                    server_name=tls_sni,
                    host_header=host_header,
                    port=route.port,
                    timeout=timeout,
                ),
            )
            schedule(
                route,
                partial(
                    check_websocket_upgrade,
                    ip,
                    meridian_mode=True,
                    server_name=tls_sni,
                    host_header=host_header,
                    port=route.port,
                    timeout=timeout,
                ),
            )
            schedule(
                route,
                partial(
                    check_root_indistinguishable,
                    ip,
                    server_name=tls_sni,
                    host_header=host_header,
                    port=route.port,
                    timeout=timeout,
                ),
            )
            schedule(
                route,
                partial(
                    check_domain_root,
                    ip,
                    host_header or tls_sni,
                    server_name=tls_sni,
                    port=route.port,
                    timeout=timeout,
                ),
            )
            if context.panel_secret_path and route.panel:
                schedule(
                    route,
                    partial(
                        check_secret_paths,
                        ip,
                        context.panel_secret_path,
                        server_name=tls_sni,
                        host_header=host_header,
                        port=route.port,
                        timeout=timeout,
                    ),
                )
        elif camouflage and route.tls_sni:
            schedule(
                route,
                partial(check_sni_camouflage, ip, route.tls_sni, port=route.port, timeout=timeout),
            )

    if context.public_udp_ports:
        jobs.append(lambda: check_udp_surface(context.public_udp_ports))
    if not context.public_tcp_ports and not context.public_udp_ports:
        jobs.append(check_no_public_surface)
    if context.internal_ports:
        jobs.append(lambda: check_internal_ports(ip, context.internal_ports, timeout=min(timeout, 3)))
    return _run_interruptible_jobs(jobs)


def _run_interruptible_jobs(jobs: list[Callable[[], VerificationCheck]]) -> list[VerificationCheck]:
    """Run bounded daemon workers so SIGINT never waits for network timeouts."""
    if not jobs:
        return []
    pending: Queue[tuple[int, Callable[[], VerificationCheck]]] = Queue()
    completed: Queue[tuple[int, VerificationCheck | Exception]] = Queue()
    cancelled = Event()
    for index, job in enumerate(jobs):
        pending.put((index, job))

    def worker() -> None:
        while not cancelled.is_set():
            try:
                index, job = pending.get_nowait()
            except Empty:
                return
            try:
                outcome: VerificationCheck | Exception = job()
            except Exception as exc:
                outcome = exc
            completed.put((index, outcome))

    workers = [Thread(target=worker, daemon=True) for _ in range(min(8, len(jobs)))]
    for thread in workers:
        thread.start()

    results: list[VerificationCheck | None] = [None] * len(jobs)
    try:
        for _ in jobs:
            index, outcome = completed.get()
            if isinstance(outcome, Exception):
                cancelled.set()
                raise outcome
            results[index] = outcome
    except BaseException:
        cancelled.set()
        raise
    return [cast(VerificationCheck, result) for result in results]


def _effective_routes(
    context: DeploymentVerificationContext,
    *,
    requested_sni: str,
) -> tuple[VerificationHttpsRoute, ...]:
    routes = list(context.https_routes)
    if requested_sni:
        matches = [route for route in routes if requested_sni in {route.tls_sni, route.host_header}]
        if context.kind == "external":
            base = matches[0] if matches else routes[0] if routes else None
            routes = [
                VerificationHttpsRoute(
                    kind="generic",
                    port=base.port if base else 443,
                    tls_sni=requested_sni,
                    host_header=base.host_header if base else requested_sni,
                )
            ]
        elif not matches:
            port = context.public_tls_ports[0] if context.public_tls_ports else 443
            routes.append(
                VerificationHttpsRoute(
                    kind="generic",
                    port=port,
                    tls_sni=requested_sni,
                    host_header=requested_sni,
                )
            )
    named_ports = {route.port for route in routes if route.tls_sni or route.host_header}
    routes = [
        route
        for route in routes
        if route.kind != "generic" or route.tls_sni or route.host_header or route.port not in named_ports
    ]
    return tuple(sorted(set(routes), key=lambda route: (route.port, route.kind, route.tls_sni, route.host_header)))


def _run_route_check(
    route: VerificationHttpsRoute,
    callback: Callable[[], VerificationCheck],
    *,
    qualify: bool,
) -> VerificationCheck:
    check = callback()
    if not qualify:
        return check
    tls_identity = route.tls_sni or "ip"
    host_identity = route.host_header or tls_identity
    suffix = f"{route.kind}:{tls_identity}:{host_identity}:{route.port}"
    return check.model_copy(update={"id": f"{check.id}:{suffix}", "name": f"{check.name} ({suffix})"})
