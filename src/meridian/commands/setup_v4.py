"""User-facing entry point for the resumable setup journey."""

from __future__ import annotations

import sys
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from meridian import config, i18n
from meridian.commands.apply_progress import apply_progress
from meridian.commands.setup_wizard import CamouflageSites, SetupWizard
from meridian.console import error_context, fail, has_tty
from meridian.core.errors import LocalStateError, MeridianError
from meridian.core.setup import SetupDraft
from meridian.core.topology import SetupIntent
from meridian.servers import ServerRegistry
from meridian.setup import (
    ServerShelf,
    SetupDraftService,
    SetupDraftStore,
    SetupRuntime,
)

if TYPE_CHECKING:
    from meridian.diagnostics.camouflage import OwnHostname


def run(
    *,
    intent_path: str = "",
    restart: bool = False,
    yes: bool = False,
) -> None:
    """Resume setup, or seed it from a complete typed intent."""
    try:
        _run(intent_path=intent_path, restart=restart, yes=yes)
    except MeridianError as exc:
        with error_context("setup"):
            fail(exc)


def _run(*, intent_path: str, restart: bool, yes: bool) -> None:
    with error_context("setup"):
        registry = ServerRegistry(config.SERVER_PROFILES_FILE)
        store = SetupDraftStore(config.SETUP_DRAFT_FILE)
        shelf = ServerShelf(registry)
        service = SetupDraftService(store, shelf)

        if restart:
            service.discard()
        if intent_path:
            existing = store.load()
            if existing is not None and existing.completed_stages:
                raise LocalStateError(
                    "A resumable setup draft already exists.",
                    hint=("Resume it without --intent, or pass --restart to replace it explicitly."),
                )
            store.save(SetupDraft.from_intent(_load_intent(intent_path)))

        draft = store.load()
        if not yes and not has_tty():
            fail(
                "`meridian setup` asks questions and needs a terminal.",
                hint="Run it in an interactive terminal, or pass --intent FILE --yes.",
                hint_type="user",
            )
        language = i18n.explicit_locale()
        if language is not None:
            i18n.set_locale(language)

        with apply_progress(registry=registry) as reporter:
            wizard = SetupWizard(
                service=service,
                shelf=shelf,
                runtime=SetupRuntime(registry, reporter=reporter),
                register_server=partial(_register_server, offer_key_setup=not yes),
                auto_approve=yes,
                ask_language=language is None and not yes and (draft is None or not draft.completed_stages),
                on_language=_remember_language,
                suggest_sni=partial(_suggest_sni, registry),
                check_own_domain=partial(_check_own_domain, registry),
            )
            wizard.run()


def _load_intent(source: str) -> SetupIntent:
    try:
        text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    except OSError as exc:
        raise LocalStateError(
            f"Cannot read setup intent {source!r}: {exc}.",
            hint="Check the path and file permissions.",
        ) from exc
    try:
        return SetupIntent.model_validate_json(text)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"])
        fail(
            f"Invalid setup intent at {location or 'document'}: {first['msg']}",
            hint_type="user",
        )


def _suggest_sni(registry: ServerRegistry, server_ref: str) -> CamouflageSites:
    """Sites in the exit's own subnet, like `deploy` finds, then the known targets it reaches over TLS 1.3."""
    from meridian.diagnostics.camouflage import neighbor_camouflage_targets
    from meridian.diagnostics.preflight import reachable_reality_targets
    from meridian.resolve import connection_for_entry

    entry = registry.find(server_ref)
    if entry is None:
        return CamouflageSites([], [])
    try:
        conn = connection_for_entry(entry)
        nearby = neighbor_camouflage_targets(conn, entry.host, limit=3)
        return CamouflageSites(nearby, reachable_reality_targets(conn))
    except (MeridianError, OSError):
        return CamouflageSites([], [])


def _check_own_domain(registry: ServerRegistry, server_ref: str, hostname: str) -> OwnHostname:
    """Where a self-steal domain resolves through the exit's own resolver; unknown when unreachable."""
    from meridian.diagnostics.camouflage import OwnHostname, own_hostname
    from meridian.resolve import connection_for_entry

    entry = registry.find(server_ref)
    if entry is None:
        return OwnHostname("unknown")
    try:
        return own_hostname(connection_for_entry(entry), hostname, entry.host)
    except (MeridianError, OSError):
        return OwnHostname("unknown")


def _remember_language(code: str) -> None:
    locale = i18n.normalize_locale(code) or i18n.DEFAULT_LOCALE
    i18n.set_locale(locale)
    i18n.save_locale(locale)


def _register_server(
    host: str,
    title: str,
    user: str,
    port: int,
    *,
    offer_key_setup: bool = False,
) -> None:
    from meridian.commands.server import run_add

    run_add(
        ip=host,
        name=title,
        user=user,
        ssh_port=port,
        offer_key_setup=offer_key_setup,
    )
