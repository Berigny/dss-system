"""Placeholder DID data models.

Populated in DSS-240.
"""

from __future__ import annotations

from pydantic import BaseModel


class VerificationMethod(BaseModel):
    id: str
    type: str
    controller: str
    public_key_jwk: dict


class DIDDocument(BaseModel):
    id: str
    verification_method: list[VerificationMethod]
    authentication: list[str]
    assertion_method: list[str]
