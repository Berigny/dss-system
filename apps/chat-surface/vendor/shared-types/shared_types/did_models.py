"""Shared DID data models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


def _to_camel(snake: str) -> str:
    """Convert ``snake_case`` to ``lowerCamelCase``."""
    parts = snake.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


class VerificationMethod(BaseModel):
    """A DID verification method."""

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: str
    type: str
    controller: str
    public_key_jwk: dict


class Service(BaseModel):
    """A DID service endpoint descriptor."""

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    id: str
    type: str
    service_endpoint: str


class DIDDocument(BaseModel):
    """A did:web document."""

    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    context: list[str] = Field(
        default_factory=lambda: ["https://www.w3.org/ns/did/v1"],
        alias="@context",
    )
    id: str
    verification_method: list[VerificationMethod]
    authentication: list[str]
    assertion_method: list[str]
    service: list[Service] | None = None


class Principal(BaseModel):
    """A DSS principal identity."""

    principal_id: str
    principal_type: str
    principal_did: str | None = None
    principal_key_id: str | None = None
    session_jti: str | None = None
    source: str = "legacy_header"
