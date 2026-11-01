"""Credential-rotation command schemas, separate from the main catalog."""

from pydantic import BaseModel

from meridian.core.client_credentials import ClientCredentialRotationResult
from meridian.core.schema_envelopes import make_command_envelope


def credential_command_schemas(base: type[BaseModel], empty: type[BaseModel]) -> dict[str, type[BaseModel]]:
    _, _, envelope = make_command_envelope(
        "ClientRotateCredentials",
        "client.rotate-credentials",
        ClientCredentialRotationResult,
        envelope_base=base,
        failure_data_type=empty,
        doc="Envelope schema for `meridian client rotate-credentials --json`.",
    )
    return {"client-rotate-credentials": ClientCredentialRotationResult, "client-rotate-credentials-envelope": envelope}
