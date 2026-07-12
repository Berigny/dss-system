"""Application configuration and constants."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

# Load environment files for local development.
_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env", override=False)
# Do not override externally supplied runtime env (e.g. Fly/Vercel secrets).
load_dotenv(_ROOT / ".env.local", override=False)


def _apply_hardening_profile() -> None:
    """Apply parent hardening profile to child env flags.

    Profile levels:
    - 0: most loose
    - 1: light hardening
    - 2: medium hardening
    - 3: most hardened
    - 4: max introspection + zero hard caps
    """

    raw_profile = os.getenv("HARDENING_PROFILE")
    if raw_profile is None:
        return

    try:
        profile = int(str(raw_profile).strip())
    except (TypeError, ValueError):
        profile = 3

    if profile < 0:
        profile = 0
    elif profile > 4:
        profile = 4

    mapping = {
        0: {"NO_CAPS": "1", "EQ9_CONTROL_DIAL": "0", "CHAT_HARDENING_LEVEL": "0"},
        1: {"NO_CAPS": "0", "EQ9_CONTROL_DIAL": "1", "CHAT_HARDENING_LEVEL": "1"},
        2: {"NO_CAPS": "0", "EQ9_CONTROL_DIAL": "2", "CHAT_HARDENING_LEVEL": "2"},
        3: {"NO_CAPS": "0", "EQ9_CONTROL_DIAL": "3", "CHAT_HARDENING_LEVEL": "3"},
        4: {
            "NO_CAPS": "1",
            "EQ9_CONTROL_DIAL": "0",
            "CHAT_HARDENING_LEVEL": "0",
            "ENABLE_INTROSPECT": "1",
            "TIMING_DEBUG": "1",
            "RESOLVE_SNIPPET_DEBUG": "1",
        },
    }

    for key, value in mapping[profile].items():
        os.environ[key] = value


_apply_hardening_profile()


@dataclass
class Settings:
    """Runtime configuration loaded from environment variables."""

    API_BASE: str = os.getenv(
        "DUALSUBSTRATE_API", os.getenv("API_BASE", "")
    )
    API_KEY: str = os.getenv("DUALSUBSTRATE_API_KEY", "")
    # After the wipe, ignore any stale deployment env still pointing at the old ledger.
    _DEFAULT_LEDGER: str = os.getenv("DUALSUBSTRATE_LEDGER") or os.getenv("DEMO_LEDGER_ID") or "LOAM"
    if (_DEFAULT_LEDGER or "").strip().lower() == "chat-demo":
        _DEFAULT_LEDGER = "LOAM"
    DEFAULT_LEDGER: str = os.getenv("DEFAULT_LEDGER_ID") or _DEFAULT_LEDGER
    DEFAULT_LEDGER_ID: str = _DEFAULT_LEDGER
    DEFAULT_SESSION_ID: str = os.getenv("DEFAULT_SESSION_ID", "demo-session")
    CHAT_SURFACE_ID: str = os.getenv("CHAT_SURFACE_ID", "surface:chat:primary")
    HTTP_TIMEOUT: float = float(os.getenv("HTTP_TIMEOUT", "10.0"))
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
    MCP_PUBLIC_BASE_URL: str = os.getenv("MCP_PUBLIC_BASE_URL", "").strip().rstrip("/")
    MCP_AUTH_TOKEN: str = os.getenv("MCP_AUTH_TOKEN", "").strip()
    MCP_AUTH_REQUIRED: bool = os.getenv("MCP_AUTH_REQUIRED", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    CHAT_HARDENING_LEVEL: int = int(os.getenv("CHAT_HARDENING_LEVEL", "0") or 0)
    QP_PURE_ENABLED: bool = os.getenv("QP_PURE_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    DEMO_OVERRIDE_MODE: bool = os.getenv(
        "DEMO_OVERRIDE_MODE", "false"
    ).lower() in {"1", "true", "yes", "on"}
    DEMO_OVERRIDE_DEFAULT_LEDGER: str = (
        os.getenv("DEMO_OVERRIDE_DEFAULT_LEDGER", "s2").strip()
        or "s2"
    )

    # Adaptive execution governor
    ADAPTIVE_EXECUTION_ENABLED: bool = os.getenv("ADAPTIVE_EXECUTION_ENABLED", "false").lower() == "true"
    ADAPTIVE_EXECUTION_FORCE_PROFILE: str = os.getenv("ADAPTIVE_EXECUTION_FORCE_PROFILE", "")
    ADAPTIVE_EXECUTION_LOCAL_PROVIDER_MARKERS: str = os.getenv(
        "ADAPTIVE_EXECUTION_LOCAL_PROVIDER_MARKERS", "ollama,llama,local"
    )


settings = Settings()

# Preserve existing named constants for compatibility
API_BASE: str = settings.API_BASE
API_KEY: str = settings.API_KEY
DEFAULT_LEDGER: str = settings.DEFAULT_LEDGER
DEFAULT_LEDGER_ID: str = settings.DEFAULT_LEDGER_ID
DEFAULT_SESSION_ID: str = settings.DEFAULT_SESSION_ID
HTTP_TIMEOUT: float = settings.HTTP_TIMEOUT
LLM_MODEL: str = settings.LLM_MODEL
LLM_MAX_TOKENS: int = settings.LLM_MAX_TOKENS
LLM_PROVIDER: str = settings.LLM_PROVIDER
ENABLE_LOCAL_LLM: bool = settings.ENABLE_LOCAL_LLM
ENABLE_LEDGER_MANAGEMENT: bool = settings.ENABLE_LEDGER_MANAGEMENT
USE_BACKEND_STREAM: bool = settings.USE_BACKEND_STREAM
MCP_PUBLIC_BASE_URL: str = settings.MCP_PUBLIC_BASE_URL
MCP_AUTH_TOKEN: str = settings.MCP_AUTH_TOKEN
MCP_AUTH_REQUIRED: bool = settings.MCP_AUTH_REQUIRED
QP_PURE_ENABLED: bool = settings.QP_PURE_ENABLED
DEMO_OVERRIDE_MODE: bool = settings.DEMO_OVERRIDE_MODE
DEMO_OVERRIDE_DEFAULT_LEDGER: str = settings.DEMO_OVERRIDE_DEFAULT_LEDGER
ADAPTIVE_EXECUTION_ENABLED: bool = settings.ADAPTIVE_EXECUTION_ENABLED
ADAPTIVE_EXECUTION_FORCE_PROFILE: str = settings.ADAPTIVE_EXECUTION_FORCE_PROFILE
ADAPTIVE_EXECUTION_LOCAL_PROVIDER_MARKERS: str = settings.ADAPTIVE_EXECUTION_LOCAL_PROVIDER_MARKERS
OPENROUTER_API_KEY: str = settings.OPENROUTER_API_KEY
ATTACHMENT_MAX_BYTES: int = settings.ATTACHMENT_MAX_BYTES
STATIC_ASSET_VERSION: str = settings.STATIC_ASSET_VERSION

# Load a persisted per-installation override for the OpenRouter API key after the
# dataclass instance is created so that runtime updates take effect immediately.
try:
    from utils.openrouter_config import get_api_key as _get_openrouter_override

    _openrouter_override = (_get_openrouter_override() or "").strip()
    if _openrouter_override:
        settings.OPENROUTER_API_KEY = _openrouter_override
        os.environ["OPENROUTER_API_KEY"] = _openrouter_override
except Exception:
    _openrouter_override = ""