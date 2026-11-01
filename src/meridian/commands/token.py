"""Review explicit token grants through the same service preview as the console."""

import re
from collections.abc import Iterator
from contextlib import contextmanager

import typer
from pydantic import ValidationError
from rich.markup import escape

from meridian.commands._helpers import load_topology_cluster
from meridian.console import confirm, err_console, error_context, fail, is_json_mode
from meridian.control.client import ControlClient, ControlServiceError
from meridian.control.token_capabilities import capability_catalog, permission
from meridian.core.models import CoreModel, Summary
from meridian.core.output import OperationContext, command_envelope
from meridian.core.token_models import DEFAULT_TOKEN_DAYS, TokenCreate, TokenCreateResult, TokenPreview
from meridian.i18n import current_locale, isolate, t
from meridian.renderers import emit_json


@contextmanager
def token_client() -> Iterator[ControlClient]:
    cluster = load_topology_cluster()
    with ControlClient(cluster.panel.url, cluster.panel.api_token) as client:
        yield client


def emit(command: str, result: CoreModel, operation: OperationContext, *, changed: bool = False) -> bool:
    if not is_json_mode():
        return False
    emit_json(
        command_envelope(
            command=command,
            data=result.to_data(),
            status="changed" if changed else "ok",
            summary=Summary(text="API token permissions", changed=changed),
            timer=operation.timer,
        ),
        reveal=frozenset({"data.issued.token"}) if command == "token.create" else frozenset(),
    )
    return True


def show_preview(preview: TokenPreview) -> None:
    err_console.print(escape(t("token.permissions")))
    for item in preview.permissions:
        err_console.print("  " + escape(item.labels[current_locale()]))
    expiry = preview.expires_at or t("token.never")
    err_console.print(escape(t("token.expiry", when=expiry)))


def run_capabilities() -> None:
    operation = OperationContext()
    with error_context("token.capabilities", timer=operation.timer):
        catalog = capability_catalog()
        if emit("token.capabilities", catalog, operation):
            return
        for item in catalog.capabilities:
            err_console.print(f"  {escape(item.capability)} — {escape(item.labels[current_locale()])}")


def run_list() -> None:
    operation = OperationContext()
    with error_context("token.list", timer=operation.timer):
        try:
            with token_client() as client:
                result = client.list_tokens()
        except ControlServiceError as exc:
            fail(exc)
        from meridian.core.token_models import TokenList

        if emit("token.list", TokenList(tokens=result), operation):
            return
        if not result:
            err_console.print(escape(t("token.empty")))
        for token in result:
            err_console.print(f"  {token.id}  {escape(isolate(token.name))}")
            for capability in token.capabilities:
                err_console.print("    " + escape(permission(capability).labels[current_locale()]))
            err_console.print("    " + escape(t("token.expiry", when=token.expires_at or t("token.never"))))
            if token.revoked_at:
                err_console.print("    " + escape(t("token.revoked", when=token.revoked_at)))


def run_create(
    name: str,
    *,
    capabilities: list[str] | None,
    scope: str | None,
    expires_days: int | None,
    expires_at: str | None,
    never_expires: bool,
    dry_run: bool,
    yes: bool,
) -> None:
    operation = OperationContext()
    with error_context("token.create", timer=operation.timer):
        days = (
            expires_days if expires_days is not None or expires_at is not None or never_expires else DEFAULT_TOKEN_DAYS
        )
        try:
            request = TokenCreate.model_validate(
                {
                    "name": name,
                    "capabilities": capabilities,
                    "scope": scope,
                    "expires_in_days": days,
                    "expires_at": expires_at,
                    "never_expires": never_expires,
                }
            )
        except ValidationError as exc:
            error = exc.errors(include_input=False, include_url=False)[0]
            kind = str(error["type"])
            field = error["loc"][0] if error["loc"] else ""
            if kind.startswith("token_"):
                message = t("token." + kind.removeprefix("token_"))
            elif field == "capabilities":
                message = t("token.invalid_capabilities")
            elif field == "scope":
                message = t("token.invalid_scope")
            elif field == "name":
                message = t("token.invalid_name")
            else:
                message = t("token.invalid_expiry")
            fail(message, hint_type="user")
        if is_json_mode() and not dry_run and not yes:
            fail(t("token.confirm_json"), hint_type="user")
        try:
            with token_client() as client:
                preview = client.preview_token(request)
                issued = None
                if not is_json_mode():
                    show_preview(preview)
                if not dry_run:
                    if not yes and not confirm(t("token.confirm_create")):
                        raise typer.Exit(1)
                    reviewed = TokenCreate(
                        name=request.name,
                        scope=request.scope,
                        capabilities=request.capabilities,
                        expires_at=preview.expires_at,
                        never_expires=preview.expires_at is None,
                    )
                    issued = client.issue_token(reviewed)
                result = TokenCreateResult(preview=preview, issued=issued)
        except ControlServiceError as exc:
            fail(exc)
        if emit("token.create", result, operation, changed=issued is not None):
            return
        if issued is None:
            err_console.print(escape(t("token.preview_only")))
        else:
            err_console.print(escape(t("token.secret_once")))
            err_console.print(escape(issued.token))


def run_revoke(token_id: str, *, yes: bool) -> None:
    operation = OperationContext()
    with error_context("token.revoke", timer=operation.timer):
        try:
            if not re.fullmatch(r"[0-9a-f]{12}", token_id):
                fail(t("token.invalid_id"), hint_type="user")
            if is_json_mode() and not yes:
                fail(t("token.confirm_json"), hint_type="user")
            with token_client() as client:
                if not yes and not confirm(t("token.confirm_revoke", name=isolate(token_id))):
                    raise typer.Exit(1)
                result = client.revoke_token(token_id)
        except ControlServiceError as exc:
            fail(exc)
        if not emit("token.revoke", result, operation):
            err_console.print(escape(t("token.revoked", when=result.revoked_at)))
