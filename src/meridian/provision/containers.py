"""Docker Compose stack lifecycle utilities.

Shared deploy pattern: mkdir -> .env -> compose.yml -> pull -> up -> health check.
Used by Deploy3xui and future container deployment steps.
"""

from __future__ import annotations

import shlex
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ComposeDeployResult:
    """Outcome of a compose stack deployment."""

    changed: bool
    detail: str = ""
    logs: str = ""  # Container logs on failure


def deploy_compose_stack(
    conn: Any,  # ServerConnection
    work_dir: str,
    compose_content: str,
    *,
    env_content: str = "",
    dirs: list[str] | None = None,
    dir_mode: str = "700",
    health_check: Callable[[], bool] | None = None,
    health_timeout: float = 60,
    health_interval: float = 3,
    pull_retries: int = 3,
    pull_retry_delay: float = 10,
    pull_timeout: int = 300,
    service_name: str = "",
) -> ComposeDeployResult:
    """Deploy a Docker Compose stack idempotently.

    Sequence: mkdir -> .env (optional) -> compose.yml -> pull -> up -> health check.

    Args:
        conn: SSH connection (ServerConnection or MockConnection).
        work_dir: Directory for compose files (e.g. ``/opt/3x-ui``).
        compose_content: Full docker-compose.yml content.
        env_content: Optional .env file content. Skipped when empty.
        dirs: Extra directories to create (work_dir is always created).
        dir_mode: chmod mode for created directories.
        health_check: Callable returning True when the stack is healthy.
            Called in a polling loop until it returns True or timeout.
        health_timeout: Max seconds to wait for health_check.
        health_interval: Seconds between health_check polls.
        pull_retries: Number of ``docker compose pull`` attempts.
        pull_retry_delay: Seconds between pull retries.
        pull_timeout: Timeout in seconds for each pull attempt.
        service_name: Label for log collection on failure (e.g. "3x-ui").

    Returns:
        ComposeDeployResult with changed=True on success, changed=False on failure.
    """
    q_dir = shlex.quote(work_dir)

    # -- Create directories --
    all_dirs = [work_dir] + (dirs or [])
    for d in all_dirs:
        q_d = shlex.quote(d)
        result = conn.run(f"mkdir -p {q_d} && chmod {dir_mode} {q_d}", timeout=15)
        if result.returncode != 0:
            return ComposeDeployResult(
                changed=False,
                detail=f"failed to create {d}: {result.stderr.strip()[:200]}",
            )

    # -- Write .env (optional) --
    if env_content:
        env_cmd = (
            f"cat > {q_dir}/.env << 'MERIDIAN_EOF'\n"
            + env_content
            + "MERIDIAN_EOF"
        )
        result = conn.run(env_cmd, timeout=15)
        if result.returncode != 0:
            return ComposeDeployResult(
                changed=False,
                detail=f"failed to write .env: {result.stderr.strip()[:200]}",
            )
        conn.run(f"chmod 600 {q_dir}/.env", timeout=15)

    # -- Write docker-compose.yml --
    compose_cmd = (
        f"cat > {q_dir}/docker-compose.yml << 'MERIDIAN_EOF'\n"
        + compose_content
        + "MERIDIAN_EOF"
    )
    result = conn.run(compose_cmd, timeout=15)
    if result.returncode != 0:
        return ComposeDeployResult(
            changed=False,
            detail=f"failed to write docker-compose.yml: {result.stderr.strip()[:200]}",
        )
    conn.run(f"chmod 644 {q_dir}/docker-compose.yml", timeout=15)

    # -- Pull images (with retries) --
    pull_ok = False
    for attempt in range(pull_retries):
        result = conn.run(f"cd {q_dir} && docker compose pull", timeout=pull_timeout)
        if result.returncode == 0:
            pull_ok = True
            break
        if attempt < pull_retries - 1:
            time.sleep(pull_retry_delay)

    if not pull_ok:
        return ComposeDeployResult(
            changed=False,
            detail=f"docker compose pull failed after {pull_retries} attempts: {result.stderr.strip()[:200]}",
        )

    # -- Bring stack up --
    result = conn.run(f"cd {q_dir} && docker compose up -d", timeout=120)
    if result.returncode != 0:
        logs = _collect_logs(conn, work_dir, service_name)
        return ComposeDeployResult(
            changed=False,
            detail=(
                f"docker compose up failed: {result.stderr.strip()[:200]}\n"
                f"Container logs:\n{logs}\n"
                f"Common fixes: check port conflicts (ss -tlnp), "
                f"disk space (df -h), Docker status (systemctl status docker)"
            ),
            logs=logs,
        )

    # -- Health check (optional) --
    if health_check is not None:
        deadline = time.monotonic() + health_timeout
        healthy = False
        while time.monotonic() < deadline:
            if health_check():
                healthy = True
                break
            time.sleep(health_interval)

        if not healthy:
            logs = _collect_logs(conn, work_dir, service_name)
            label = service_name or "stack"
            return ComposeDeployResult(
                changed=False,
                detail=f"{label} did not become healthy after {health_timeout:.0f}s",
                logs=logs,
            )

    return ComposeDeployResult(changed=True)


def _collect_logs(conn: Any, work_dir: str, service_name: str) -> str:
    """Collect recent container logs for diagnostics."""
    q_dir = shlex.quote(work_dir)
    result = conn.run(f"cd {q_dir} && docker compose logs --tail 50", timeout=15)
    if result.returncode == 0:
        return result.stdout.strip()[:500]
    return "no logs available"
