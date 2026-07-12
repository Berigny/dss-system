"""Shared pytest fixtures and test-wide environment defaults."""

import os


# DSS-232: source code no longer hardcodes deployment-specific hosts/DIDs.
# The middleware app reads several constants at import time, so these defaults
# must be present before `app.py` is imported by the test modules.
os.environ.setdefault("PUBLIC_BASE_URL", "https://id.dualsubstrate.com")
os.environ.setdefault("TRUST_ANCHOR_PUBLIC_BASE_URL", "https://id.dualsubstrate.com")
os.environ.setdefault("DEFAULT_ISSUER_DID", "did:web:id.dualsubstrate.com")
os.environ.setdefault("TRUST_ANCHOR_ISSUER_DID", "did:web:id.dualsubstrate.com")
os.environ.setdefault("WALT_ID_ISSUER_DID", "did:web:id.dualsubstrate.com")
os.environ.setdefault("DEFAULT_ORGANISATION_URI", "https://dualsubstrate.com")
os.environ.setdefault("TRUST_ANCHOR_ORGANISATION_URI", "https://dualsubstrate.com")
os.environ.setdefault("BASE_DOMAIN", "dualsubstrate.com")
os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
