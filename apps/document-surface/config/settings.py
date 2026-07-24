"""Application configuration for the DSS Document surface."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent
_IS_VERCEL = bool((os.getenv("VERCEL") or "").strip())
load_dotenv(_ROOT / ".env", override=False)
if not _IS_VERCEL:
    load_dotenv(_ROOT / ".env.local", override=True)

_MIDDLEWARE_URL = os.getenv("MIDDLEWARE_URL") or os.getenv("MIDDLEWARE_BASE_URL") or ""
if _IS_VERCEL:
    _API_BASE = os.getenv("DUALSUBSTRATE_API", os.getenv("API_BASE", _MIDDLEWARE_URL))
    _API_KEY = os.getenv("DUALSUBSTRATE_API_KEY", "")
else:
    _API_BASE = os.getenv(
        "DUALSUBSTRATE_API_LOCAL",
        os.getenv("DUALSUBSTRATE_API", os.getenv("API_BASE", _MIDDLEWARE_URL)),
    )
    _API_KEY = os.getenv("DUALSUBSTRATE_API_KEY_LOCAL", os.getenv("DUALSUBSTRATE_API_KEY", ""))


@dataclass
class Settings:
    """Runtime configuration loaded from environment variables."""

    API_BASE: str = _API_BASE
    API_KEY: str = _API_KEY
    DEFAULT_LEDGER_ID: str = os.getenv("DEFAULT_LEDGER_ID", os.getenv("DUALSUBSTRATE_LEDGER", "LOAM"))
    DOCUMENT_SURFACE_ID: str = os.getenv("DOCUMENT_SURFACE_ID", "surface:document:primary")
    BACKEND_ADMIN_BASE: str = os.getenv("BACKEND_ADMIN_BASE", os.getenv("DUALSUBSTRATE_BACKEND_ADMIN_BASE", ""))
    CONTROL_PLANE_BASE: str = os.getenv("CONTROL_PLANE_BASE", os.getenv("DUALSUBSTRATE_CONTROL_PLANE_BASE", ""))
    FRONTEND_CONTEXT_ID: str = os.getenv(
        "FRONTEND_CONTEXT_ID",
        "ctx:frontend:vercel" if _IS_VERCEL else "ctx:frontend:local",
    )
    STATIC_ASSET_VERSION: str = os.getenv("STATIC_ASSET_VERSION", "v1")
    HTTP_TIMEOUT: float = float(os.getenv("HTTP_TIMEOUT", "30.0"))
    BACKEND_SESSION_TOKEN_COOKIE: str = "ds_backend_session_token"


settings = Settings()
