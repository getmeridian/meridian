"""Schema registration for service-owned delivery operations."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from meridian.control.models import ClientEndpoint
from meridian.core.languages import LanguageSettings, LanguageState
from meridian.core.notices import Notice, NoticeState, NoticeWrite, RenderedNotice
from meridian.core.schema_envelopes import make_command_envelope
from meridian.core.subscription_preview import (
    FormatSelection,
    PreviewInputs,
    SubscriptionPreview,
    SubscriptionPreviewRequest,
)


def service_delivery_schemas(
    *, envelope_base: type[BaseModel], failure_data_type: type[BaseModel]
) -> dict[str, type[BaseModel]]:
    schemas: dict[str, type[BaseModel]] = {
        "notice": Notice,
        "notice-state": NoticeState,
        "notice-write": NoticeWrite,
        "rendered-notice": RenderedNotice,
        "language-settings": LanguageSettings,
        "language-state": LanguageState,
        "client-endpoint": ClientEndpoint,
        "subscription-preview": SubscriptionPreview,
        "subscription-preview-request": SubscriptionPreviewRequest,
        "subscription-preview-inputs": PreviewInputs,
        "subscription-format-selection": FormatSelection,
    }
    for action in ("show", "set", "clear"):
        _, _, envelope = make_command_envelope(
            "FleetNotice" + action.capitalize(),
            "fleet.notice." + action,
            NoticeState,
            envelope_base=envelope_base,
            failure_data_type=failure_data_type,
            success_statuses=Literal["ok", "changed", "no_changes"],
            doc=f"Envelope schema for `meridian fleet notice {action} --json`.",
        )
        schemas[f"fleet-notice-{action}-envelope"] = envelope
    _, _, language_envelope = make_command_envelope(
        "FleetLanguage",
        "fleet.language",
        LanguageState,
        envelope_base=envelope_base,
        failure_data_type=failure_data_type,
        success_statuses=Literal["ok", "changed", "no_changes"],
        doc="Envelope schema for `meridian fleet language --json`.",
    )
    schemas["fleet-language-envelope"] = language_envelope
    _, _, preview_envelope = make_command_envelope(
        "ClientPreview",
        "client.preview",
        SubscriptionPreview,
        envelope_base=envelope_base,
        failure_data_type=failure_data_type,
        doc="Envelope schema for `meridian client preview --json`.",
    )
    schemas["client-preview-envelope"] = preview_envelope
    return schemas
