"""Tests for shared_types.did_models."""

from __future__ import annotations

from shared_types.did_models import DIDDocument, Principal, Service, VerificationMethod


def test_verification_method_camel_case_dump() -> None:
    vm = VerificationMethod(
        id="did:web:example.com#key-1",
        type="JsonWebKey2020",
        controller="did:web:example.com",
        public_key_jwk={"kty": "EC", "crv": "secp256k1", "x": "x", "y": "y"},
    )
    dumped = vm.model_dump(by_alias=True)
    assert dumped["id"] == "did:web:example.com#key-1"
    assert dumped["publicKeyJwk"] == {"kty": "EC", "crv": "secp256k1", "x": "x", "y": "y"}


def test_did_document_camel_case_dump() -> None:
    vm = VerificationMethod(
        id="did:web:example.com#key-1",
        type="JsonWebKey2020",
        controller="did:web:example.com",
        public_key_jwk={"kty": "EC"},
    )
    service = Service(
        id="did:web:example.com#resolver",
        type="DSSResolverService",
        service_endpoint="https://example.com/v1/resolve",
    )
    doc = DIDDocument(
        id="did:web:example.com",
        verification_method=[vm],
        authentication=["did:web:example.com#key-1"],
        assertion_method=["did:web:example.com#key-1"],
        service=[service],
    )
    dumped = doc.model_dump(by_alias=True)
    assert dumped["@context"] == ["https://www.w3.org/ns/did/v1"]
    assert dumped["id"] == "did:web:example.com"
    assert dumped["verificationMethod"][0]["publicKeyJwk"] == {"kty": "EC"}
    assert dumped["service"][0]["serviceEndpoint"] == "https://example.com/v1/resolve"


def test_principal_defaults() -> None:
    p = Principal(principal_id="user-1", principal_type="user")
    assert p.principal_did is None
    assert p.source == "legacy_header"
