"""Fleet language is operator service state, independent of CLI and browser preferences."""

from __future__ import annotations

from meridian.commands._helpers import load_topology_cluster
from meridian.console import error_context, fail, info, is_json_mode, ok
from meridian.control_access import access_panel
from meridian.core.languages import LANGUAGE_NAMES, LanguageError, LanguageSettings, parse_language
from meridian.core.models import Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.i18n import t
from meridian.remnawave import RemnawaveError
from meridian.renderers import emit_json


def run_language(language: str | None = None) -> None:
    operation = OperationContext()
    with error_context("fleet.language", timer=operation.timer):
        try:
            settings = LanguageSettings(language=parse_language(language)) if language is not None else None
        except LanguageError:
            fail(t("language.invalid"), hint_type="user")
        cluster = load_topology_cluster()
        try:
            with access_panel(cluster) as panel:
                state = panel.language()
                if settings is not None and settings.language != state.language:
                    state = panel.set_language(settings)
        except RemnawaveError as exc:
            fail(exc)
        if is_json_mode():
            emit_json(
                command_envelope(
                    command="fleet.language",
                    data=state.to_data(),
                    status="ok" if settings is None else "changed" if state.changed else "no_changes",
                    summary=Summary(text="Subscription language", changed=state.changed),
                    timer=operation.timer,
                )
            )
            return
        if state.changed:
            ok(t("language.saved", language=LANGUAGE_NAMES[state.language]))
        else:
            info(t("language.current", language=LANGUAGE_NAMES[state.language]))
