"""Remnawave panel provisioning step.

Deploys the Remnawave backend, PostgreSQL, Valkey (Redis), and
subscription page as Docker containers on the server. The panel listens
on 127.0.0.1:3000 and is never exposed directly — nginx reverse-proxies
to it. The subscription page listens on 127.0.0.1:3020.

Four containers, named in ``meridian.host_runtime`` (neutral names, so the
host's ``docker ps`` shows nothing telling):
  - backend (NestJS, port 3000 app + 3001 metrics)
  - database (PostgreSQL 17, data in the ``data/`` bind mount)
  - cache (Valkey 9, Unix socket only)
  - subscription page (subscription frontend, port 3020)
"""

from __future__ import annotations

import secrets
import shlex
from typing import TYPE_CHECKING

from meridian.config import (
    REMNAWAVE_BACKEND_IMAGE,
    REMNAWAVE_PANEL_PORT,
    REMNAWAVE_POSTGRES_IMAGE,
    REMNAWAVE_SUBSCRIPTION_PAGE_PORT,
    REMNAWAVE_VALKEY_IMAGE,
)
from meridian.host_runtime import (
    PANEL_BACKEND,
    PANEL_CACHE,
    PANEL_CORE,
    PANEL_DATABASE,
    PANEL_DIR,
    PANEL_PROJECT,
    SUBSCRIPTION_PAGE,
)
from meridian.provision.runtime_migration import adopt_legacy_panel
from meridian.provision.steps import ProvisionContext, StepResult
from meridian.remnawave.versions import (
    effective_image,
    find_image,
    image_below_floor,
    parse_image,
    resolve_data_image,
)
from meridian.ssh import ServerConnection

if TYPE_CHECKING:
    from meridian.cluster import TelegramConfig

# Container names double as Compose service names (and so as hostnames on the network).
_PANEL_CONTAINER = PANEL_BACKEND.name
_DB_CONTAINER = PANEL_DATABASE.name
_REDIS_CONTAINER = PANEL_CACHE.name
_SUBSCRIPTION_PAGE_CONTAINER = SUBSCRIPTION_PAGE.name
_NETWORK = "internal"

_METRICS_PORT = 3001
_SUBSCRIPTION_PAGE_INTERNAL_PORT = 3010  # container-internal port


def render_panel_compose(
    image: str,
    panel_port: int,
    subscription_page_image: str,
    subscription_page_host_port: int,
    postgres_image: str = REMNAWAVE_POSTGRES_IMAGE,
    valkey_image: str = REMNAWAVE_VALKEY_IMAGE,
) -> str:
    """Render the docker-compose.yml for the Remnawave panel stack.

    Based on: https://github.com/remnawave/backend/blob/main/docker-compose-prod.yml
    The project name is pinned, so Docker object names never follow the directory.
    PostgreSQL keeps its data in the ``./data`` bind mount beside this file, never in
    a named volume, so moving the directory moves the database with it.
    """
    return f"""\
# Managed by Meridian. Manual edits will be overwritten on next run.
name: {PANEL_PROJECT}
services:
  {_PANEL_CONTAINER}:
    image: {image}
    container_name: {_PANEL_CONTAINER}
    restart: always
    depends_on:
      {_DB_CONTAINER}:
        condition: service_healthy
      {_REDIS_CONTAINER}:
        condition: service_healthy
    networks:
      - {_NETWORK}
    volumes:
      - valkey-socket:/var/run/valkey
    ports:
      - "127.0.0.1:{panel_port}:{panel_port}"
      - "127.0.0.1:{_METRICS_PORT}:{_METRICS_PORT}"
    ulimits:
      nofile:
        soft: 1048576
        hard: 1048576
    env_file:
      - .env
    healthcheck:
      test: ["CMD-SHELL", "curl -f http://localhost:{_METRICS_PORT}/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s
    logging:
      driver: json-file
      options:
        max-size: "100m"
        max-file: "5"

  {_DB_CONTAINER}:
    image: {postgres_image}
    container_name: {_DB_CONTAINER}
    restart: always
    networks:
      - {_NETWORK}
    volumes:
      - ./data:/var/lib/postgresql/data
    environment:
      - TZ=UTC
    ulimits:
      nofile:
        soft: 1048576
        hard: 1048576
    env_file:
      - .env
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"]
      interval: 3s
      timeout: 10s
      retries: 3
    logging:
      driver: json-file
      options:
        max-size: "100m"
        max-file: "5"

  {_REDIS_CONTAINER}:
    image: {valkey_image}
    container_name: {_REDIS_CONTAINER}
    restart: always
    networks:
      - {_NETWORK}
    volumes:
      - valkey-socket:/var/run/valkey
    command: >
      valkey-server
      --save ""
      --appendonly no
      --maxmemory-policy noeviction
      --loglevel warning
      --unixsocket /var/run/valkey/valkey.sock
      --unixsocketperm 777
      --port 0
    healthcheck:
      test: ["CMD", "valkey-cli", "-s", "/var/run/valkey/valkey.sock", "ping"]
      interval: 3s
      timeout: 3s
      retries: 3
    logging:
      driver: json-file
      options:
        max-size: "100m"
        max-file: "5"

  {_SUBSCRIPTION_PAGE_CONTAINER}:
    image: {subscription_page_image}
    container_name: {_SUBSCRIPTION_PAGE_CONTAINER}
    restart: always
    depends_on:
      {_PANEL_CONTAINER}:
        condition: service_healthy
    networks:
      - {_NETWORK}
    ports:
      - "127.0.0.1:{subscription_page_host_port}:{_SUBSCRIPTION_PAGE_INTERNAL_PORT}"
    env_file:
      - .env.subscription
    logging:
      driver: json-file
      options:
        max-size: "100m"
        max-file: "5"

networks:
  {_NETWORK}:
    driver: bridge

volumes:
  valkey-socket:
    driver: local
"""


def _render_panel_env(
    panel_port: int,
    db_password: str,
    app_secret: str,
    front_end_domain: str,
    sub_public_domain: str,
    metrics_password: str,
    telegram: TelegramConfig | None = None,
) -> str:
    """Render the .env file for the Remnawave panel."""
    # Telegram: enabled when bot_token is configured
    tg_enabled = bool(telegram and telegram.bot_token)

    # The database name and role live inside PostgreSQL's data directory; they keep
    # their first-install values (only the service hostname follows the neutral names).
    lines = f"""\
# Remnawave Panel environment
# Managed by Meridian. Manual edits will be overwritten on next run.

APP_PORT={panel_port}
METRICS_PORT={_METRICS_PORT}
API_INSTANCES=1

DATABASE_URL=postgresql://meridian:{db_password}@{_DB_CONTAINER}:5432/remnawave

REDIS_SOCKET=/var/run/valkey/valkey.sock

APP_SECRET={app_secret}

PANEL_DOMAIN={front_end_domain}
FRONT_END_DOMAIN=*
SUB_PUBLIC_DOMAIN={sub_public_domain}

METRICS_USER=meridian
METRICS_PASS={metrics_password}

IS_TELEGRAM_NOTIFICATIONS_ENABLED={str(tg_enabled).lower()}

WEBHOOK_ENABLED=false

POSTGRES_USER=meridian
POSTGRES_PASSWORD={db_password}
POSTGRES_DB=remnawave
"""
    if tg_enabled and telegram:
        lines += f"\nTELEGRAM_BOT_TOKEN={telegram.bot_token}\n"
        if telegram.notify_users:
            lines += f"TELEGRAM_NOTIFY_USERS={telegram.notify_users}\n"
        if telegram.notify_nodes:
            lines += f"TELEGRAM_NOTIFY_NODES={telegram.notify_nodes}\n"
        if telegram.notify_crm:
            lines += f"TELEGRAM_NOTIFY_CRM={telegram.notify_crm}\n"
        if telegram.notify_service:
            lines += f"TELEGRAM_NOTIFY_SERVICE={telegram.notify_service}\n"
        if telegram.notify_tblocker:
            lines += f"TELEGRAM_NOTIFY_TBLOCKER={telegram.notify_tblocker}\n"
    return lines


_PERSISTENT_PANEL_SECRETS = ("POSTGRES_PASSWORD", "APP_SECRET", "METRICS_PASS")
_TOKEN_KEY = "REMNAWAVE_API_TOKEN"


def _parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.lstrip().startswith("#"):
            values[key.strip()] = value.strip()
    return values


def _env_value(text: str, key: str) -> str:
    return _parse_env(text).get(key, "")


def _load_existing_panel_secrets(conn: ServerConnection, panel_dir: str) -> dict[str, str] | StepResult | None:
    """Return secrets from an existing panel ``.env`` so restarts never rotate them.

    PostgreSQL keeps the password it was initialised with and every API token is
    signed with ``APP_SECRET``, so regenerating them orphans the database.
    Remnawave 2.x signed tokens with ``JWT_AUTH_SECRET``; it becomes
    ``APP_SECRET`` so existing API tokens stay valid across the upgrade.
    Returns None for a genuinely fresh install, or a failed StepResult when
    panel data exists but its secrets cannot be recovered.
    """
    env_path = f"{panel_dir}/.env"
    if conn.run(f"test -f {shlex.quote(env_path)}", timeout=15).returncode != 0:
        data_dir = shlex.quote(f"{panel_dir}/data")
        has_data = conn.run(f'test -d {data_dir} && test -n "$(ls -A {data_dir})"', timeout=15)
        if has_data.returncode == 0:
            return StepResult(
                name=DeployRemnawavePanel.name,
                status="failed",
                detail=(
                    f"{panel_dir}/data exists but {env_path} is missing; refusing to create new "
                    f"database credentials. Restore {env_path}, or remove {panel_dir} to start fresh."
                ),
            )
        return None

    current = conn.get_text(env_path, timeout=15)
    values = _parse_env(current.stdout) if current.returncode == 0 else {}
    if not values.get("APP_SECRET") and values.get("JWT_AUTH_SECRET"):
        values["APP_SECRET"] = values["JWT_AUTH_SECRET"]
    missing = [key for key in _PERSISTENT_PANEL_SECRETS if not values.get(key)]
    if missing:
        return StepResult(
            name=DeployRemnawavePanel.name,
            status="failed",
            detail=f"{env_path} is unreadable or missing {', '.join(missing)}; refusing to rotate panel secrets.",
        )
    return values


def render_subscription_env(
    panel_url: str = f"http://{_PANEL_CONTAINER}:{REMNAWAVE_PANEL_PORT}",
    api_token: str = "",
) -> str:
    """Render the .env.subscription file for the Remnawave subscription page."""
    return f"""\
# Remnawave Subscription Page environment
# Managed by Meridian. Manual edits will be overwritten on next run.

APP_PORT={_SUBSCRIPTION_PAGE_INTERNAL_PORT}
REMNAWAVE_PANEL_URL={panel_url}
REMNAWAVE_API_TOKEN={api_token}
"""


def configure_subscription_page(
    conn: ServerConnection,
    api_token: str,
    panel_dir: str = PANEL_DIR,
) -> bool:
    """Write the subscription page .env with a real API token and restart the container.

    Called after the API token is created. The subscription page container
    starts with an empty token (from the initial compose up) and is recreated
    here with the valid token.

    Returns True on success, False on failure (non-fatal — logged as warning).
    """
    import logging

    logger = logging.getLogger("meridian.provision")

    env_content = render_subscription_env(api_token=api_token)
    env_path = f"{panel_dir}/.env.subscription"
    result = conn.put_text(
        env_path,
        env_content,
        mode="600",
        sensitive=True,
        timeout=15,
        operation_name="write subscription page env",
    )
    if result.returncode != 0:
        logger.warning("Failed to write subscription page .env: %s", result.stderr.strip()[:200])
        return False

    # `restart` keeps the environment the container was created with; only a
    # recreate reads the new token from env_file.
    result = conn.run(
        f"docker compose up -d --no-deps --force-recreate {_SUBSCRIPTION_PAGE_CONTAINER}",
        cwd=panel_dir,
        timeout=60,
    )
    if result.returncode != 0:
        logger.warning("Failed to recreate subscription page: %s", result.stderr.strip()[:200])
        return False
    return True


class DeployRemnawavePanel:
    """Deploy Remnawave panel stack as Docker containers.

    Includes backend, PostgreSQL, Valkey, and subscription page.
    Idempotency: skipped when all core containers are already running. When
    restarting a stopped or broken stack, secrets from the existing ``.env``
    are reused, never regenerated.
    """

    name = "Deploy Remnawave panel"

    def __init__(
        self,
        front_end_domain: str = "",
        sub_public_domain: str = "",
    ) -> None:
        self.front_end_domain = front_end_domain
        self.sub_public_domain = sub_public_domain

    def run(self, conn: ServerConnection, ctx: ProvisionContext) -> StepResult:
        panel_dir = PANEL_DIR
        # First, before reading anything from the directory: a pre-rename stack
        # moves here with its data and secrets, then deploys as a restart.
        migration_error = adopt_legacy_panel(conn)
        if migration_error:
            return StepResult(name=self.name, status="failed", detail=migration_error)
        panel_port = REMNAWAVE_PANEL_PORT
        sub_page_host_port = REMNAWAVE_SUBSCRIPTION_PAGE_PORT
        # Keep operator-upgraded images; pinned tags only replace older ones.
        current_compose = conn.get_text(f"{panel_dir}/docker-compose.yml", timeout=15)
        compose_text = current_compose.stdout if current_compose.returncode == 0 else None
        image = effective_image(ctx.backend_image, compose_text)
        sub_page_image = effective_image(ctx.subscription_page_image, compose_text)
        data_images = {
            # A PostgreSQL major needs a dump and restore; never move one in place.
            "postgres_image": resolve_data_image(
                REMNAWAVE_POSTGRES_IMAGE,
                find_image(compose_text, parse_image(REMNAWAVE_POSTGRES_IMAGE).repository),
                cross_major=False,
            ),
            "valkey_image": resolve_data_image(
                REMNAWAVE_VALKEY_IMAGE,
                find_image(compose_text, parse_image(REMNAWAVE_VALKEY_IMAGE).repository),
            ),
        }
        below_floor = image_below_floor(find_image(compose_text, parse_image(REMNAWAVE_BACKEND_IMAGE).repository))

        front_end_domain = self.front_end_domain or ctx.domain or ctx.ip
        host = ctx.domain or ctx.ip
        # Subscription links are handed to end users, so they live under the
        # connection-page path (nginx proxies only /api/sub/ there), never
        # under the admin panel's secret path.
        if self.sub_public_domain:
            sub_public_domain = self.sub_public_domain
        elif ctx.info_page_path:
            sub_public_domain = f"{host}/{ctx.info_page_path}/sub"
        else:
            sub_public_domain = host

        # -- Idempotency: are all core containers already running? --
        core_running = True
        for name in (container.name for container in PANEL_CORE):
            check = conn.run(
                f"docker inspect -f '{{{{.State.Running}}}}' {name} 2>/dev/null",
                timeout=15,
            )
            if check.returncode != 0 or check.stdout.strip() != "true":
                core_running = False
                break

        # A running panel older than the supported floor is upgraded in place
        # (Prisma migrates the database on start) instead of being skipped.
        if core_running and not below_floor:
            # Check if subscription page also running (upgrade from pre-subscription deploys)
            sub_check = conn.run(
                f"docker inspect -f '{{{{.State.Running}}}}' {_SUBSCRIPTION_PAGE_CONTAINER} 2>/dev/null",
                timeout=15,
            )
            if sub_check.returncode == 0 and sub_check.stdout.strip() == "true":
                return StepResult(
                    name=self.name,
                    status="skipped",
                    detail="containers already running",
                )

            # Core containers running but subscription page missing — add it
            # without regenerating secrets (upgrade path).
            compose_content = render_panel_compose(
                image=image,
                panel_port=panel_port,
                subscription_page_image=sub_page_image,
                subscription_page_host_port=sub_page_host_port,
                **data_images,
            )
            compose_path = f"{panel_dir}/docker-compose.yml"
            write_compose = conn.put_text(
                compose_path,
                compose_content,
                mode="644",
                timeout=15,
                operation_name="write remnawave panel compose",
            )
            if write_compose.returncode != 0:
                return StepResult(
                    name=self.name,
                    status="failed",
                    detail=f"failed to write docker-compose.yml: {write_compose.stderr.strip()[:200]}",
                )

            # Write placeholder subscription env if not present
            sub_env_path = f"{panel_dir}/.env.subscription"
            sub_env_check = conn.run(f"test -f {shlex.quote(sub_env_path)}", timeout=15)
            if sub_env_check.returncode != 0:
                sub_env_content = render_subscription_env()
                write_sub_env = conn.put_text(
                    sub_env_path,
                    sub_env_content,
                    mode="600",
                    sensitive=True,
                    timeout=15,
                    operation_name="write subscription page env",
                )
                if write_sub_env.returncode != 0:
                    return StepResult(
                        name=self.name,
                        status="failed",
                        detail=f"failed to write .env.subscription: {write_sub_env.stderr.strip()[:200]}",
                    )

            conn.run("docker compose up -d", cwd=panel_dir, timeout=120)
            return StepResult(
                name=self.name,
                status="changed",
                detail="added subscription page to existing panel",
            )

        # -- (Re)start via deploy_compose_stack, never rotating existing secrets --
        existing = _load_existing_panel_secrets(conn, panel_dir)
        if isinstance(existing, StepResult):
            return existing
        if existing is None:
            db_password = secrets.token_hex(16)
            app_secret = secrets.token_hex(32)
            metrics_password = secrets.token_hex(8)
        else:
            db_password = existing["POSTGRES_PASSWORD"]
            app_secret = existing["APP_SECRET"]
            metrics_password = existing["METRICS_PASS"]

        env_content = _render_panel_env(
            panel_port=panel_port,
            db_password=db_password,
            app_secret=app_secret,
            front_end_domain=front_end_domain,
            sub_public_domain=sub_public_domain,
            metrics_password=metrics_password,
            telegram=ctx.cluster.telegram if ctx.cluster else None,
        )
        # Keep the subscription page's real API token if bootstrap already wrote one;
        # the panel URL is re-rendered because it names the backend service.
        sub_env_content = render_subscription_env()
        if existing is not None:
            current_sub_env = conn.get_text(f"{panel_dir}/.env.subscription", timeout=15)
            if current_sub_env.returncode == 0:
                sub_env_content = render_subscription_env(api_token=_env_value(current_sub_env.stdout, _TOKEN_KEY))
        compose_content = render_panel_compose(
            image=image,
            panel_port=panel_port,
            subscription_page_image=sub_page_image,
            subscription_page_host_port=sub_page_host_port,
            **data_images,
        )

        from meridian.provision.containers import EnvFile, deploy_compose_stack

        _health_url = f"http://127.0.0.1:{_METRICS_PORT}/health"
        _q_health_url = shlex.quote(_health_url)

        def _panel_healthy() -> bool:
            r = conn.run(
                f"curl -sf -o /dev/null -w '%{{http_code}}' {_q_health_url}",
                timeout=15,
            )
            code = r.stdout.strip()
            return r.returncode == 0 and code in ("200", "204")

        deploy_result = deploy_compose_stack(
            conn,
            panel_dir,
            compose_content,
            env_files=[
                EnvFile(filename=".env", content=env_content, sensitive=True),
                EnvFile(filename=".env.subscription", content=sub_env_content, sensitive=True),
            ],
            dirs=[f"{panel_dir}/data"],
            health_check=_panel_healthy,
            health_timeout=120,
            health_interval=3.0,
            service_name="Remnawave panel",
            stop_first=True,
        )

        if not deploy_result.changed:
            return StepResult(
                name=self.name,
                status="failed",
                detail=deploy_result.detail,
            )

        return StepResult(name=self.name, status="changed")
