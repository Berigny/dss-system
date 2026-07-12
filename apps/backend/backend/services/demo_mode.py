"""Demo-mode feature flags shared across services."""

from __future__ import annotations

import os


def demo_god_mode_enabled() -> bool:
    """Return True when demo-wide god mode is enabled."""
    value = os.getenv("DEMO_GOD_MODE", "false").strip().lower()
    return value in {"1", "true", "yes", "on"}


def demo_default_ledger() -> str:
    """Fallback ledger used when scope is omitted in god mode."""
    return (os.getenv("DEMO_GOD_DEFAULT_LEDGER", "default") or "default").strip() or "default"
