"""Bounded WebAuthn JSON at the console boundary; signed client bytes stay opaque."""

from typing import Annotated, Literal

from pydantic import Field, field_validator

from meridian.core.models import CoreModel

CredentialId = Annotated[str, Field(min_length=1, max_length=1366, pattern=r"^[A-Za-z0-9_-]+$")]
ChallengeId = Annotated[str, Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$")]
ClientData = Annotated[str, Field(min_length=1, max_length=10923, pattern=r"^[A-Za-z0-9_-]+$")]


class PasskeyName(CoreModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[^\x00-\x1f\x7f]+$")

    @field_validator("name")
    @classmethod
    def plain_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Enter a name for this passkey.")
        return value


class PasskeyRecord(PasskeyName):
    id: CredentialId
    rp_id: str
    created_at: int
    last_used_at: int | None = None


class PasskeyList(CoreModel):
    passkeys: list[PasskeyRecord]


class CredentialDescriptor(CoreModel):
    type: Literal["public-key"] = "public-key"
    id: CredentialId


class RelyingPartyOptions(CoreModel):
    id: str
    name: str = "Meridian"


class OwnerOptions(CoreModel):
    id: str
    name: str = "owner"
    display_name: str = Field(default="Meridian owner", alias="displayName")


class CredentialAlgorithm(CoreModel):
    type: Literal["public-key"] = "public-key"
    alg: Literal[-7, -8]


class AuthenticatorSelection(CoreModel):
    resident_key: Literal["preferred"] = Field(default="preferred", alias="residentKey")
    user_verification: Literal["required"] = Field(default="required", alias="userVerification")


class RegistrationOptions(CoreModel):
    challenge: str
    rp: RelyingPartyOptions
    user: OwnerOptions
    pub_key_cred_params: list[CredentialAlgorithm] = Field(serialization_alias="pubKeyCredParams")
    exclude_credentials: list[CredentialDescriptor] = Field(serialization_alias="excludeCredentials")
    authenticator_selection: AuthenticatorSelection = Field(
        default_factory=AuthenticatorSelection, alias="authenticatorSelection"
    )
    attestation: Literal["none"] = "none"
    timeout: int = 300000


class RegistrationBegin(CoreModel):
    challenge_id: ChallengeId
    public_key: RegistrationOptions


class RegistrationResponse(CoreModel):
    client_data_json: ClientData = Field(alias="clientDataJSON")
    attestation_object: str = Field(
        min_length=1, max_length=32768, pattern=r"^[A-Za-z0-9_-]+$", alias="attestationObject"
    )


class RegistrationCredential(CoreModel):
    id: CredentialId
    raw_id: CredentialId = Field(alias="rawId")
    type: Literal["public-key"]
    response: RegistrationResponse


class RegistrationCompletion(PasskeyName):
    challenge_id: ChallengeId
    credential: RegistrationCredential


class AuthenticationOptions(CoreModel):
    challenge: str
    rp_id: str = Field(serialization_alias="rpId")
    allow_credentials: list[CredentialDescriptor] = Field(serialization_alias="allowCredentials")
    user_verification: Literal["required"] = Field(default="required", alias="userVerification")
    timeout: int = 300000


class AuthenticationBegin(CoreModel):
    challenge_id: ChallengeId
    public_key: AuthenticationOptions


class AssertionResponse(CoreModel):
    client_data_json: ClientData = Field(alias="clientDataJSON")
    authenticator_data: str = Field(
        min_length=1, max_length=4096, pattern=r"^[A-Za-z0-9_-]+$", alias="authenticatorData"
    )
    signature: str = Field(min_length=1, max_length=192, pattern=r"^[A-Za-z0-9_-]+$")
    user_handle: str | None = Field(default=None, max_length=86, pattern=r"^[A-Za-z0-9_-]+$", alias="userHandle")


class AssertionCredential(CoreModel):
    id: CredentialId
    raw_id: CredentialId = Field(alias="rawId")
    type: Literal["public-key"]
    response: AssertionResponse


class AuthenticationCompletion(CoreModel):
    challenge_id: ChallengeId
    credential: AssertionCredential
