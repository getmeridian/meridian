"""Docker Compose stack lifecycle utilities.

Shared deploy pattern: mkdir -> env files -> compose.yml -> pull -> up -> health check.
Used by DeployRemnawavePanel, deploy_node_container, and future container steps.
"""

from __future__ import annotations

import logging
import shlex
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class EnvFile:
    """An environment file to write alongside docker-compose.yml."""

    filename: str  # e.g. ".env" or ".env.subscription"
    content: str
    sensitive: bool = True
    mode: str = "600"


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
    env_files: list[EnvFile] | None = None,
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
    stop_first: bool = False,
) -> ComposeDeployResult:
    """Deploy a Docker Compose stack idempotently.

    Sequence: mkdir -> env files -> compose.yml -> (stop) -> pull -> up -> health check.

    File writes use ``conn.put_text()`` (never heredocs) per project convention.

    Args:
        conn: SSH connection (ServerConnection or MockConnection).
        work_dir: Directory for compose files (e.g. ``/opt/remnawave``).
        compose_content: Full docker-compose.yml content.
        env_files: List of EnvFile objects for environment files.
        env_content: Simple .env content (shorthand for a single env file).
        dirs: Extra directories to create (work_dir is always created).
        dir_mode: chmod mode for created directories.
        health_check: Callable returning True when the stack is healthy.
        health_timeout: Max seconds to wait for health_check.
        health_interval: Seconds between health_check polls.
        pull_retries: Number of ``docker compose pull`` attempts.
        pull_retry_delay: Seconds between pull retries.
        pull_timeout: Timeout in seconds for each pull attempt.
        service_name: Label for log collection on failure.
        stop_first: Run ``docker compose down`` before pull+up.

    Returns:
        ComposeDeployResult with changed=True on success, changed=False on failure.
    """
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

    # -- Build env file list --
    all_env_files: list[EnvFile] = list(env_files or [])
    if env_content and not any(ef.filename == ".env" for ef in all_env_files):
        all_env_files.insert(0, EnvFile(filename=".env", content=env_content))

    # -- Write env files --
    for ef in all_env_files:
        env_path = f"{work_dir}/{ef.filename}"
        result = conn.put_text(
            env_path,
            ef.content,
            mode=ef.mode,
            sensitive=ef.sensitive,
            timeout=15,
            operation_name=f"write {ef.filename}",
        )
        if result.returncode != 0:
            return ComposeDeployResult(
                changed=False,
                detail=f"failed to write {ef.filename}: {result.stderr.strip()[:200]}",
            )

    # -- Write docker-compose.yml --
    compose_path = f"{work_dir}/docker-compose.yml"
    result = conn.put_text(
        compose_path,
        compose_content,
        mode="644",
        timeout=15,
        operation_name=f"write {service_name or 'stack'} compose",
    )
    if result.returncode != 0:
        return ComposeDeployResult(
            changed=False,
            detail=f"failed to write docker-compose.yml: {result.stderr.strip()[:200]}",
        )

    # -- Stop old containers if requested --
    if stop_first:
        conn.run("docker compose down 2>/dev/null", cwd=work_dir, timeout=60)

    # -- Pull images (with retries) --
    pull_result = conn.run(
        "docker compose pull",
        cwd=work_dir,
        timeout=pull_timeout,
        retries=pull_retries,
        retry_delay=pull_retry_delay,
        operation_name=f"pull {service_name or 'stack'} images",
    )
    if pull_result.returncode != 0:
        return ComposeDeployResult(
            changed=False,
            detail=f"docker compose pull failed after {pull_retries} attempts: {pull_result.stderr.strip()[:200]}",
        )

    # -- Bring stack up --
    result = conn.run("docker compose up -d", cwd=work_dir, timeout=120)
    if result.returncode != 0:
        logs = _collect_logs(conn, work_dir, service_name)
        return ComposeDeployResult(
            changed=False,
            detail=(f"docker compose up failed: {result.stderr.strip()[:200]}\nContainer logs:\n{logs}"),
            logs=logs,
        )

    # -- Health check (optional) --
    if health_check is not None:
        from meridian.health import ReadinessTimeout, poll_until_ready

        try:
            poll_until_ready(
                health_check,
                timeout=health_timeout,
                interval=health_interval,
                description=service_name or "stack",
            )
        except ReadinessTimeout:
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
    result = conn.run("docker compose logs --tail 50", cwd=work_dir, timeout=15)
    if result.returncode == 0:
        return result.stdout.strip()[:500]
    return "no logs available"
