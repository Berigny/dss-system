"""Application configuration and constants."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

# Load environment files for local development.
_ROOT = Path(__file__).resolve().parent.parent
_IS_VERCEL = bool((os.getenv("VERCEL") or "").strip())
load_dotenv(_ROOT / ".env", override=False)

if not _IS_VERCEL:
    load_dotenv(_ROOT / ".env.local", override=True)

_MIDDLEWARE_URL = (
    os.getenv("MIDDLEWARE_URL") or os.getenv("MIDDLEWARE_BASE_URL") or ""
)
if _IS_VERCEL:
    _API_BASE = os.getenv(
        "DUALSUBSTRATE_API",
        os.getenv("API_BASE", _MIDDLEWARE_URL),
    )
    _API_KEY = os.getenv("DUALSUBSTRATE_API_KEY", "")
else:
    _API_BASE = os.getenv(
        "DUALSUBSTRATE_API_LOCAL",
        os.getenv(
            "DUALSUBSTRATE_API",
            os.getenv("API_BASE", _MIDDLEWARE_URL),
        ),
    )
    _API_KEY = os.getenv("DUALSUBSTRATE_API_KEY_LOCAL", os.getenv("DUALSUBSTRATE_API_KEY", ""))


@dataclass
class Settings:
    """Runtime configuration loaded from environment variables."""

    API_BASE: str = _API_BASE
    API_KEY: str = _API_KEY
    # After the wipe, ignore any stale deployment env still pointing at the old ledger.
    _DEFAULT_LEDGER: str = os.getenv("DUALSUBSTRATE_LEDGER") or os.getenv("DEMO_LEDGER_ID") or "LOAM"
    if (_DEFAULT_LEDGER or "").strip().lower() == "chat-demo":
        _DEFAULT_LEDGER = "LOAM"
    DEFAULT_LEDGER: str = os.getenv("DEFAULT_LEDGER_ID") or _DEFAULT_LEDGER
    DEFAULT_LEDGER_ID: str = _DEFAULT_LEDGER
    DEFAULT_SESSION_ID: str = os.getenv("DEFAULT_SESSION_ID", "demo-session")
    HTTP_TIMEOUT: float = float(os.getenv("HTTP_TIMEOUT", "10.0"))
    BACKEND_ADMIN_BASE: str = os.getenv(
        "BACKEND_ADMIN_BASE",
        os.getenv("DUALSUBSTRATE_BACKEND_ADMIN_BASE", ""),
    )
    CONTROL_PLANE_BASE: str = os.getenv(
        "CONTROL_PLANE_BASE",
        os.getenv("DUALSUBSTRATE_CONTROL_PLANE_BASE", ""),
    )
    CHAT_SURFACE_ID: str = os.getenv("CHAT_SURFACE_ID", "surface:chat:primary")
    BACKEND_ADMIN_TOKEN: str = os.getenv("BACKEND_ADMIN_TOKEN", "")
    FRONTEND_CONTEXT_ID: str = os.getenv(
        "FRONTEND_CONTEXT_ID",
        "ctx:frontend:vercel" if _IS_VERCEL else "ctx:frontend:local",
    )
    FRONTEND_PRINCIPAL_ID: str = os.getenv(
        "FRONTEND_PRINCIPAL_ID",
        os.getenv("DEMO_OWNER_ID", "demo-user"),
    )
    FRONTEND_PRINCIPAL_TYPE: str = os.getenv("FRONTEND_PRINCIPAL_TYPE", "user")
    FRONTEND_TENANT_ID: str = os.getenv(
        "FRONTEND_TENANT_ID",
        os.getenv("DEMO_TENANT_ID", "tenant:demo"),
    )
    ATTACHMENT_MAX_BYTES: int = int(
        os.getenv("ATTACHMENT_MAX_BYTES", str(50 * 1024 * 1024))
    )
    STATIC_ASSET_VERSION: str = os.getenv("STATIC_ASSET_VERSION", "v2")

    # LLM (OpenRouter defaults)
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "openai/gpt-4o")
    LLM_MAX_TOKENS: int = int(
        os.getenv("OPENROUTER_MAX_TOKENS")
        or os.getenv("LLM_MAX_TOKENS")
        or "0"
    )
    # Default to OpenRouter to match the current client implementation.
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openrouter")
    ENABLE_LOCAL_LLM: bool = os.getenv("ENABLE_LOCAL_LLM", "false").lower() == "true"
    ENABLE_LEDGER_MANAGEMENT: bool = (
        os.getenv("ENABLE_LEDGER_MANAGEMENT", "true").lower() == "true"
    )
    USE_BACKEND_STREAM: bool = os.getenv("USE_BACKEND_STREAM", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


settings = Settings()

# Preserve existing named constants for compatibility
API_BASE: str = settings.API_BASE
API_KEY: str = settings.API_KEY
DEFAULT_LEDGER: str = settings.DEFAULT_LEDGER
DEFAULT_LEDGER_ID: str = settings.DEFAULT_LEDGER_ID
DEFAULT_SESSION_ID: str = settings.DEFAULT_SESSION_ID
HTTP_TIMEOUT: float = settings.HTTP_TIMEOUT
BACKEND_ADMIN_BASE: str = settings.BACKEND_ADMIN_BASE
BACKEND_ADMIN_TOKEN: str = settings.BACKEND_ADMIN_TOKEN
CONTROL_PLANE_BASE: str = settings.CONTROL_PLANE_BASE
CHAT_SURFACE_ID: str = settings.CHAT_SURFACE_ID
FRONTEND_CONTEXT_ID: str = settings.FRONTEND_CONTEXT_ID
FRONTEND_PRINCIPAL_ID: str = settings.FRONTEND_PRINCIPAL_ID
FRONTEND_PRINCIPAL_TYPE: str = settings.FRONTEND_PRINCIPAL_TYPE
FRONTEND_TENANT_ID: str = settings.FRONTEND_TENANT_ID
LLM_MODEL: str = settings.LLM_MODEL
LLM_MAX_TOKENS: int = settings.LLM_MAX_TOKENS
LLM_PROVIDER: str = settings.LLM_PROVIDER
ENABLE_LOCAL_LLM: bool = settings.ENABLE_LOCAL_LLM
ENABLE_LEDGER_MANAGEMENT: bool = settings.ENABLE_LEDGER_MANAGEMENT
USE_BACKEND_STREAM: bool = settings.USE_BACKEND_STREAM
OPENROUTER_API_KEY: str = settings.OPENROUTER_API_KEY
ATTACHMENT_MAX_BYTES: int = settings.ATTACHMENT_MAX_BYTES
STATIC_ASSET_VERSION: str = settings.STATIC_ASSET_VERSION
