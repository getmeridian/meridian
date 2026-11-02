"""Explicit API grants, compatible scope presets, and token metadata contracts.

Capability grants are flat and immutable: a preset is expanded once at issuance
or migration. Resource bounds can later compose with the grant without changing
the scope hierarchy or adding an owner/tenant role.
"""

from typing import Literal, TypeAlias, get_args

from pydantic import Field, model_validator
from pydantic_core import PydanticCustomError

from meridian.core.agent_types import OpaqueToken, Timestamp
from meridian.core.languages import Language
from meridian.core.models import CoreModel

TokenScope = Literal["read", "propose", "approve", "people", "admin"]
TOKEN_SCOPES: tuple[TokenScope, ...] = get_args(TokenScope)
ScopeRequirement: TypeAlias = TokenScope | tuple[TokenScope, ...]
TokenCapability = Literal[
    "api.read",
    "fleet.read",
    "metrics.read",
    "topology.read",
    "fleet.apply",
    "nodes.enroll",
    "people.read",
    "people.query",
    "people.batch",
    "people.private",
    "people.edit",
    "people.delivery",
    "delivery.read",
    "delivery.explain",
    "people.credentials",
    "people.reset",
    "people.reissue",
    "people.remove",
    "people.devices",
    "people.bulk",
    "commands.read",
    "commands.restart",
    "commands.run",
    "commands.cut_off",
    "backup.read",
    "backup.edit",
    "settings.edit",
    "tokens.read",
    "tokens.issue",
    "tokens.revoke",
    "revisions.read",
    "revisions.propose",
    "revisions.approve",
    "revisions.approve_own",
    "owner.read",
    "console.read",
]
TOKEN_CAPABILITIES: tuple[TokenCapability, ...] = get_args(TokenCapability)
Permission: TypeAlias = TokenCapability | Literal["root"]
PermissionRequirement: TypeAlias = Permission | tuple[Permission, ...]
DEFAULT_TOKEN_DAYS = 90


def scope_covers(held: TokenScope, needed: TokenScope) -> bool:
    """Compatibility presets: people is separate from read < propose < approve; admin covers both."""
    if held == "admin" or held == needed:
        return True
    if held == "people" or needed == "people" or needed == "admin":
        return False
    return TOKEN_SCOPES.index(held) >= TOKEN_SCOPES.index(needed)


class CapabilityGrant(CoreModel):
    """One caller's permissions; later resource bounds compose here, rather than in scope names."""

    capabilities: frozenset[TokenCapability]


class TokenCreate(CoreModel):
    """Choose one compatible preset or an explicit nonempty grant; new tokens expire by default."""

    name: str = Field(min_length=1, max_length=64, pattern=r"^[^\x00-\x1f\x7f]+$")
    scope: TokenScope | None = Field(default=None, exclude_if=lambda value: value is None)
    capabilities: list[TokenCapability] | None = Field(
        default=None, min_length=1, max_length=len(TOKEN_CAPABILITIES), exclude_if=lambda value: value is None
    )
    expires_at: Timestamp | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Future UTC expiry instead of expires_in_days.",
    )
    expires_in_days: int | None = Field(
        default=DEFAULT_TOKEN_DAYS,
        ge=1,
        le=3650,
        strict=True,
        exclude_if=lambda value: value is None,
        description="Whole days from issuance; defaults to 90. Null needs never_expires=true.",
    )
    never_expires: bool = Field(
        default=False,
        exclude_if=lambda value: not value,
        description="Explicitly opt out of expiry; cannot accompany an expiry value.",
    )

    @model_validator(mode="after")
    def choices(self) -> "TokenCreate":
        if (self.scope is None) == (self.capabilities is None):
            raise PydanticCustomError("token_grant_choice", "Choose a scope preset or explicit capabilities, not both.")
        if self.capabilities is not None:
            if len(set(self.capabilities)) != len(self.capabilities):
                raise PydanticCustomError("token_duplicate_capability", "List each capability once.")
            object.__setattr__(self, "capabilities", sorted(self.capabilities))
        explicit_days = "expires_in_days" in self.model_fields_set and self.expires_in_days is not None
        if self.expires_at is not None or self.never_expires:
            if explicit_days or (self.expires_at is not None and self.never_expires):
                raise PydanticCustomError(
                    "token_expiry_choice", "Choose expires_at, expires_in_days, or never_expires."
                )
            object.__setattr__(self, "expires_in_days", None)
        elif self.expires_in_days is None:
            raise PydanticCustomError("token_expiry_required", "Set an expiry or explicitly choose never_expires=true.")
        return self


class TokenRecord(CoreModel):
    """Metadata only. A null scope is an explicit grant; stored grants never expand implicitly."""

    id: str = Field(pattern=r"^[0-9a-f]{12}$")
    name: str
    scope: TokenScope | None
    capabilities: list[TokenCapability] = Field(default_factory=list)
    created_at: Timestamp
    last_used_at: Timestamp | None = Field(default=None, description="Coarse: updated at most once a minute.")
    revoked_at: Timestamp | None = None
    expires_at: Timestamp | None = None


class IssuedToken(TokenRecord):
    """Only this producing response contains the token secret."""

    token: OpaqueToken


class TokenList(CoreModel):
    tokens: list[TokenRecord]


class CapabilityPermission(CoreModel):
    capability: TokenCapability
    labels: dict[Language, str]


class CapabilityOperation(CoreModel):
    method: str
    path: str
    conditional: bool = False


class CapabilityRecord(CapabilityPermission):
    operations: list[CapabilityOperation]
    console_delegable: bool


class CapabilityCatalog(CoreModel):
    capabilities: list[CapabilityRecord]
    presets: dict[TokenScope, list[TokenCapability]]


class TokenPreview(CoreModel):
    """Effective permissions and expiry, without a token or any write."""

    scope: TokenScope | None
    capabilities: list[TokenCapability]
    permissions: list[CapabilityPermission]
    expires_at: Timestamp | None


class TokenCreateResult(CoreModel):
    """A CLI preview and, after confirmation, its one-time issued credential."""

    preview: TokenPreview
    issued: IssuedToken | None = None
