"""Live signature verification for signature-required verifier portals."""

from __future__ import annotations

import base64
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

from backend.services.verifier_portals import get_verifier_portal
from backend.services.verifier_public_keys import get_verifier_public_key
from backend.services.verifier_signature_checks import upsert_verifier_signature_check


def _b64u_decode(value: str) -> bytes:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("missing base64url payload")
    padding = "=" * ((4 - len(raw) % 4) % 4)
    return base64.urlsafe_b64decode(raw + padding)


def canonical_attestation_payload(
    *,
    evidence_ref: str,
    actor_id: str,
    actor_type: str,
    rating: int,
    reason: str | None,
    source: str | None,
    verifier_portal: str,
    verifier_identity: str,
    verification_signature_ref: str,
    verification_proof_ref: str | None,
) -> bytes:
    payload = {
        "actor_id": str(actor_id or "").strip(),
        "actor_type": str(actor_type or "").strip(),
        "evidence_ref": str(evidence_ref or "").strip(),
        "rating": int(rating),
        "reason": str(reason or "").strip() or None,
        "source": str(source or "").strip() or None,
        "verification_proof_ref": str(verification_proof_ref or "").strip() or None,
        "verification_signature_ref": str(verification_signature_ref or "").strip(),
        "verifier_identity": str(verifier_identity or "").strip(),
        "verifier_portal": str(verifier_portal or "").strip(),
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def verify_live_signature_for_attestation(
    db: Any,
    *,
    evidence_ref: str,
    actor_id: str,
    actor_type: str,
    rating: int,
    reason: str | None,
    source: str | None,
    verifier_portal: str,
    verifier_identity: str,
    verification_signature_ref: str,
    verification_signature_b64u: str,
    verification_proof_ref: str | None = None,
) -> dict[str, Any]:
    portal = get_verifier_portal(db, verifier_portal)
    if not isinstance(portal, dict):
        raise ValueError("verifier portal is not registered")
    public_key_ref = str(portal.get("public_key_ref") or "").strip()
    if not public_key_ref:
        raise ValueError("verifier portal is missing public_key_ref")
    public_key_record = get_verifier_public_key(db, public_key_ref)
    if not isinstance(public_key_record, dict):
        raise ValueError("verifier public key is not registered")
    if str(public_key_record.get("status") or "").strip().lower() != "active":
        raise ValueError("verifier public key is not active")
    public_key_pem = str(public_key_record.get("public_key_pem") or "").strip()
    algorithm = str(public_key_record.get("algorithm") or "").strip().lower()
    signature = _b64u_decode(verification_signature_b64u)
    payload = canonical_attestation_payload(
        evidence_ref=evidence_ref,
        actor_id=actor_id,
        actor_type=actor_type,
        rating=rating,
        reason=reason,
        source=source,
        verifier_portal=verifier_portal,
        verifier_identity=verifier_identity,
        verification_signature_ref=verification_signature_ref,
        verification_proof_ref=verification_proof_ref,
    )
    public_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    try:
        if algorithm == "ecdsa-p256":
            if not isinstance(public_key, ec.EllipticCurvePublicKey):
                raise ValueError("public key does not match ecdsa-p256")
            public_key.verify(signature, payload, ec.ECDSA(hashes.SHA256()))
        elif algorithm == "ed25519":
            if not isinstance(public_key, ed25519.Ed25519PublicKey):
                raise ValueError("public key does not match ed25519")
            public_key.verify(signature, payload)
        else:
            raise ValueError("unsupported verifier public key algorithm")
        verification_status = "verified"
    except InvalidSignature:
        verification_status = "failed"
    return upsert_verifier_signature_check(
        db,
        signature_ref=verification_signature_ref,
        public_key_ref=public_key_ref,
        portal_id=str(portal.get("portal_id") or "").strip() or None,
        verifier_identity=verifier_identity,
        verification_status=verification_status,
        signature_hash=payload.hex(),
        trust_root_ref=str(public_key_record.get("trust_root_ref") or "").strip() or None,
    )
