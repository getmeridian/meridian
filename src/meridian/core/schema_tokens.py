"""API and CLI token contract family."""

from typing import Literal

from pydantic import BaseModel

from meridian.core.schema_envelopes import make_command_envelope
from meridian.core.token_models import (
    CapabilityCatalog,
    IssuedToken,
    TokenCreate,
    TokenCreateResult,
    TokenList,
    TokenPreview,
    TokenRecord,
)

TOKEN_SCHEMAS: dict[str, type[BaseModel]] = {
    "token-create": TokenCreate,
    "token-record": TokenRecord,
    "token-issued": IssuedToken,
    "token-list": TokenList,
    "token-preview": TokenPreview,
    "token-capabilities": CapabilityCatalog,
    "token-create-result": TokenCreateResult,
}


def token_command_schemas(base: type[BaseModel], empty: type[BaseModel]) -> dict[str, type[BaseModel]]:
    result: dict[str, type[BaseModel]] = {}
    for name, model in {
        "capabilities": CapabilityCatalog,
        "list": TokenList,
        "create": TokenCreateResult,
        "revoke": TokenRecord,
    }.items():
        statuses = Literal["ok", "changed"] if name == "create" else Literal["ok"]
        _success, _terminal, envelope = make_command_envelope(
            "Token" + name.title(),
            "token." + name,
            model,
            envelope_base=base,
            failure_data_type=empty,
            success_statuses=statuses,
            doc=f"Envelope schema for meridian token {name} --json.",
        )
        result[f"token-{name}-envelope"] = envelope
    return result
