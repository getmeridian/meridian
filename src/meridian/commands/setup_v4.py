"""User-facing entry point for the resumable V4 setup journey."""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic import ValidationError

from meridian.commands.setup_wizard import SetupWizard
from meridian.config import (
    SERVER_PROFILES_FILE,
    SETUP_DRAFT_FILE,
)
from meridian.console import error_context, fail
from meridian.core.errors import LocalStateError
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

        wizard = SetupWizard(
            service=service,
            shelf=shelf,
            runtime=SetupRuntime(registry),
            register_server=_register_server,
            auto_approve=yes,
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
