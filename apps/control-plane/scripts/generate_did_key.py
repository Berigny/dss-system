#!/usr/bin/env python3
"""Generate an Ed25519 keypair for DID document use.

Outputs the public key as a JWK JSON object suitable for the
DID_PUBLIC_JWK_JSON environment variable.

Usage:
    python scripts/generate_did_key.py

Then copy the printed DID_PUBLIC_JWK_JSON value into your .env.local
and Vercel environment variables.
"""
import base64
import json
import os

from nacl.signing import SigningKey


def generate_ed25519_keypair() -> tuple[bytes, bytes]:
    """Generate a random Ed25519 signing key and return (private_key, public_key)."""
    signing_key = SigningKey.generate()
    private_key = bytes(signing_key)
    public_key = bytes(signing_key.verify_key)
    return private_key, public_key


def public_key_to_jwk(public_key: bytes) -> dict:
    """Encode a 32-byte Ed25519 public key as a JWK dict."""
    if len(public_key) != 32:
        raise ValueError("Ed25519 public key must be 32 bytes")
    x = base64.urlsafe_b64encode(public_key).rstrip(b"=").decode("ascii")
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "x": x,
    }


def main() -> None:
    private_key, public_key = generate_ed25519_keypair()
    jwk = public_key_to_jwk(public_key)
    jwk_json = json.dumps(jwk, separators=(",", ":"))

    print("=" * 60)
    print("Generated Ed25519 keypair for DID document")
    print("=" * 60)
    print()
    print("PRIVATE KEY (hex) — store securely, never commit:")
    print(private_key.hex())
    print()
    print("PUBLIC KEY JWK (for DID_PUBLIC_JWK_JSON env var):")
    print(jwk_json)
    print()
    print("-" * 60)
    print("Setup instructions:")
    print("-" * 60)
    print("1. Add to .env.local:")
    print(f'   DID_PUBLIC_JWK_JSON={jwk_json}')
    print()
    print("2. Add to Vercel project settings:")
    print(f'   DID_PUBLIC_JWK_JSON={jwk_json}')
    print()
    print("3. Keep the private key hex safe — it is needed for signing.")
    print("   Store it in a password manager or secure vault.")
    print()
    print("NOTE: This key is for development/testing. For production,")
    print("consider extracting the public key from your Entra/Azure Key Vault")
    print("signing key so the DID document matches the VC issuer key.")
    print("=" * 60)


if __name__ == "__main__":
    main()
