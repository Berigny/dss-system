"""Wallet interoperability endpoints for OIDC4VCI cross-wallet support (DSS-144)."""

from __future__ import annotations

import hashlib
import os
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from backend.api.http import get_db
from backend.services.pilot_identity import _default_wallet, _load_pilot_signups
from shared_types.did_models import DIDDocument, VerificationMethod
from backend.services.pilot_onboarding import (
    _current_principal_did_or_raise,
    _signup_for_principal,
)
from backend.services.session_tokens import apply_session_token_claims_or_raise


router = APIRouter(prefix="/wallet", tags=["wallet"])


# --- Supported wallet providers ---

SUPPORTED_WALLET_PROVIDERS = {
    "microsoft_authenticator",
    "mattr",
    "altme",
}

DEFAULT_WALLET_PROVIDER = "microsoft_authenticator"


# --- DID Document ---

def _build_did_document(wallet_id: str, issuer_did: str | None = None) -> dict[str, Any]:
    """Build a minimal did:web document for wallet verification."""
    base_issuer = issuer_did or os.getenv("DEFAULT_ISSUER_DID", "")
    verification_method_id = f"{base_issuer}#key-1"
    doc = DIDDocument(
        id=base_issuer,
        verification_method=[
            VerificationMethod(
                id=verification_method_id,
                type="JsonWebKey2020",
                controller=base_issuer,
                public_key_jwk={
                    "kty": "EC",
                    "crv": "secp256k1",
                    "x": "placeholder_x_coordinate",
                    "y": "placeholder_y_coordinate",
                },
            )
        ],
        authentication=[verification_method_id],
        assertion_method=[verification_method_id],
    )
    return doc.model_dump(by_alias=True)


@router.get("/{wallet_id}/did.json")
async def get_wallet_did_document(
    request: Request,
    wallet_id: str,
    db=Depends(get_db),
) -> JSONResponse:
    """Return a publicly resolvable DID document for the given wallet.

    Requirements from VC-DID-UNTP-DSS.md Phase 1:
    - Content-Type: application/did+json
    - Access-Control-Allow-Origin: *
    """
    if wallet_id not in SUPPORTED_WALLET_PROVIDERS and wallet_id != "default":
        raise HTTPException(status_code=404, detail="unknown_wallet_provider")

    doc = _build_did_document(wallet_id)
    return JSONResponse(
        content=doc,
        headers={
            "Content-Type": "application/did+json",
            "Access-Control-Allow-Origin": "*",
        },
    )


# --- Credential Offer ---

def _build_credential_offer(
    session_id: str,
    wallet_provider: str,
    issuer_did: str | None = None,
) -> dict[str, Any]:
    """Build an OIDC4VCI credential_offer payload.

    For Microsoft Authenticator, the offer is shaped around Entra Verified ID.
    For MATTR and other standard wallets, we return a pure OIDC4VCI offer.
    """
    pre_auth_code = hashlib.sha256(f"{session_id}:{wallet_provider}:{secrets.token_hex(8)}".encode()).hexdigest()[:24]
    base_offer: dict[str, Any] = {
        "credential_issuer": issuer_did or os.getenv("DEFAULT_ISSUER_DID", ""),
        "credential_configuration_ids": ["DssSupplyChainIdentity"],
        "grants": {
            "urn:ietf:params:oauth:grant-type:pre-authorized_code": {
                "pre-authorized_code": pre_auth_code,
                "tx_code": {
                    "length": 4,
                    "input_mode": "numeric",
                    "description": "Please enter the code displayed on the screen",
                },
            },
        },
    }

    if wallet_provider == "microsoft_authenticator":
        # Microsoft Authenticator uses a proprietary wrapper; we keep the base
        # offer but signal that the wallet should use the Entra issuance path.
        base_offer["_microsoft_entra_hint"] = True

    return base_offer


@router.get("/credential-offer")
async def get_wallet_credential_offer(
    request: Request,
    session_id: str = Query(..., min_length=1),
    wallet_provider: str = Query(default=DEFAULT_WALLET_PROVIDER),
    db=Depends(get_db),
) -> dict[str, Any]:
    """Return a standard OIDC4VCI credential_offer for the requested wallet.

    Query params:
      - session_id: required
      - wallet_provider: default "microsoft_authenticator", also supports "mattr"
    """
    provider = str(wallet_provider or DEFAULT_WALLET_PROVIDER).strip().lower()
    if provider not in SUPPORTED_WALLET_PROVIDERS:
        raise HTTPException(
            status_code=422,
            detail={"error": "unsupported_wallet_provider", "supported": list(SUPPORTED_WALLET_PROVIDERS)},
        )

    # Validate session by checking the signup record exists for the authenticated principal
    try:
        principal_did = _current_principal_did_or_raise(request)
        _signup_id, record = _signup_for_principal(db, principal_did)
    except HTTPException:
        # Allow unauthenticated credential-offer retrieval for QR-code scanning
        pass

    offer = _build_credential_offer(session_id, provider)
    return {
        "status": "ok",
        "wallet_provider": provider,
        "credential_offer": offer,
    }


# --- Approved signup credential offer ---

@router.get("/credential-offer/{signup_id}")
async def get_signup_credential_offer(
    request: Request,
    signup_id: str,
    db=Depends(get_db),
) -> dict[str, Any]:
    """Return the credential offer for an approved signup.

    This endpoint is public so approved users can retrieve their offer
    without needing an active session.
    """
    signups = _load_pilot_signups(db)
    signup = signups.get(signup_id)
    if not isinstance(signup, dict):
        raise HTTPException(status_code=404, detail={"error": "signup_not_found"})

    credential_offer = signup.get("credential_offer")
    if not isinstance(credential_offer, dict):
        raise HTTPException(
            status_code=404,
            detail={"error": "credential_offer_not_ready", "reason": "signup_not_yet_approved"},
        )

    wallet = signup.get("wallet")
    wallet_provider = "altme"
    if isinstance(wallet, dict):
        wallet_provider = str(wallet.get("provider") or "altme").strip().lower() or "altme"

    return {
        "status": "ok",
        "wallet_provider": wallet_provider,
        "credential_offer": credential_offer,
    }


# --- Wallet Provider Status ---

@router.get("/providers")
async def list_wallet_providers() -> dict[str, Any]:
    """List supported wallet providers with their capabilities."""
    return {
        "status": "ok",
        "providers": [
            {
                "provider_id": "microsoft_authenticator",
                "display_name": "Microsoft Authenticator",
                "supported_protocols": ["microsoft_entra_verified_id"],
                "qr_code_scheme": "https",
                "default": True,
            },
            {
                "provider_id": "mattr",
                "display_name": "MATTR Wallet",
                "supported_protocols": ["oidc4vci"],
                "qr_code_scheme": "openid-credential-offer",
                "default": False,
            },
            {
                "provider_id": "altme",
                "display_name": "Altme",
                "supported_protocols": ["oidc4vci"],
                "qr_code_scheme": "openid-credential-offer",
                "default": False,
            },
        ],
    }


# --- CORS preflight ---

@router.options("/{path:path}")
async def wallet_cors_preflight() -> JSONResponse:
    return JSONResponse(
        content={},
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Accept",
        },
    )
