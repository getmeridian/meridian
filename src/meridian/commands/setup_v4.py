"""User-facing entry point for the resumable setup journey."""

from __future__ import annotations

import sys
from functools import partial
from pathlib import Path

from pydantic import ValidationError

from meridian import i18n
from meridian.commands.apply_progress import apply_progress
from meridian.commands.setup_wizard import SetupWizard
from meridian.config import (
    SERVER_PROFILES_FILE,
    SETUP_DRAFT_FILE,
)
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
        registry = ServerRegistry(SERVER_PROFILES_FILE)
        store = SetupDraftStore(SETUP_DRAFT_FILE)
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
                register_server=_register_server,
                auto_approve=yes,
                ask_language=language is None and not yes and (draft is None or not draft.completed_stages),
                on_language=_remember_language,
                suggest_sni=partial(_suggest_sni, registry),
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


def _suggest_sni(registry: ServerRegistry, server_ref: str) -> str:
    """First camouflage candidate the exit server reaches over TLS 1.3 + HTTP/2 ('' if none or unknown)."""
    from meridian.diagnostics.preflight import reachable_reality_targets
    from meridian.resolve import connection_for_entry

    entry = registry.find(server_ref)
    if entry is None:
        return ""
    try:
        targets = reachable_reality_targets(connection_for_entry(entry))
    except (MeridianError, OSError):
        return ""
    return targets[0] if targets else ""


def _remember_language(code: str) -> None:
    locale = i18n.normalize_locale(code) or i18n.DEFAULT_LOCALE
    i18n.set_locale(locale)
    i18n.save_locale(locale)


def _register_server(
    host: str,
    title: str,
    user: str,
    port: int,
) -> None:
    from meridian.commands.server import run_add

    run_add(
        ip=host,
        name=title,
        user=user,
        ssh_port=port,
    )
