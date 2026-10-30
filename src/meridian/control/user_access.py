"""Keep declared people's credential mutations with the deployer, checked inside each write."""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import TYPE_CHECKING

from meridian.control.errors import ServiceError
from meridian.control.tokens import Caller
from meridian.control.ui_views import is_declared

if TYPE_CHECKING:
    from meridian.control.owner import OwnerSession
    from meridian.control.service import ControlService


def refuse_declared(
    service: ControlService,
    username: str,
    command: str,
    caller: Caller | None,
    owner_session: OwnerSession | None,
) -> AbstractContextManager[None]:
    """Admin API tokens may mutate; people and owner callers cannot move a declared connection page."""
    if owner_session is None and (caller is None or caller.scope != "people"):
        return nullcontext()

    def undeclared() -> None:
        if is_declared(service, username):
            raise ServiceError(
                "declared_user", f"The fleet file declares {username}; run `{command} {username}` instead.", 409
            )

    return service.conditions(undeclared)
