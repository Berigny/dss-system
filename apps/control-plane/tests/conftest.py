"""Shared pytest fixtures and test-wide environment defaults."""

import os
import sys

# Ensure the control-plane app directory is importable regardless of the
# pytest invocation/working directory (DSS-239).
_CONTROL_PLANE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CONTROL_PLANE_DIR not in sys.path:
    sys.path.insert(0, _CONTROL_PLANE_DIR)


# DSS-232: source code no longer hardcodes deployment-specific hosts/URLs.
# The dashboard app reads these constants at import time, so defaults must be
# present before `app.py` is imported by the test modules.
os.environ.setdefault("PUBLIC_BASE_URL", "https://id.dualsubstrate.com")
os.environ.setdefault("ISSUER_DID", "did:web:id.dualsubstrate.com")
os.environ.setdefault("DEFAULT_DID_HOST", "id.dualsubstrate.com")
os.environ.setdefault("DEFAULT_HOST", "id.dualsubstrate.com")
os.environ.setdefault("BACKEND_BASE_URL", "https://id.dualsubstrate.com")
os.environ.setdefault("MIDDLEWARE_BASE_URL", "https://middleware.dualsubstrate.com")
os.environ.setdefault("CHAT_BASE_URL", "https://chat.dualsubstrate.com")
os.environ.setdefault("BENCHMARK_DECODER_BASE_URL", "https://decoder.dualsubstrate.com")
os.environ.setdefault("COORD_DEMO_BASE_URL", "https://decode.dualsubstrate.com")
os.environ.setdefault("TRUST_ANCHOR_ORGANISATION_URI", "https://dualsubstrate.com")
os.environ.setdefault("ADMIN_TOKEN", "test-admin-token")
os.environ.setdefault("CODEX_PRINCIPAL_DID", "did:web:id.dualsubstrate.com:principals:agent:openai:codex")
