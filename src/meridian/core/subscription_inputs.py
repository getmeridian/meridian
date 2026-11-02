"""Bounded synthetic request inputs shared by subscription and response-rule previews."""

from __future__ import annotations

import unicodedata
from typing import Annotated

from pydantic import AfterValidator, Field

from meridian.core.models import CoreModel
from meridian.core.subscription_formats import PreviewSuffix

USER_AGENT_MAX_LENGTH = 512
ACCEPT_MAX_LENGTH = 256


def validate_preview_header(value: str) -> str:
    if any(unicodedata.category(char).startswith("C") for char in value) or any(
        char in value for char in "\u2028\u2029"
    ):
        raise ValueError("Use a single header line without control characters.")
    return value


PreviewHeader = Annotated[str, AfterValidator(validate_preview_header)]


class PreviewInputs(CoreModel):
    """Synthetic request headers are bounded and never stored or echoed."""

    user_agent: PreviewHeader = Field(default="", max_length=USER_AGENT_MAX_LENGTH)
    accept: PreviewHeader = Field(default="", max_length=ACCEPT_MAX_LENGTH)
    suffix: PreviewSuffix = ""
