"""Persistent per-installation override for the OpenRouter API key."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _ROOT / "data" / "openrouter_config.json"

_LOCK = threading.RLock()


def _read_config() -> dict[str, Any]:
    if not _CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_config(config: dict[str, Any]) -> None:
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(config, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(_CONFIG_PATH)


def get_api_key() -> str | None:
    """Return the stored override key, or None if no override is set."""
    with _LOCK:
        value = str(_read_config().get("api_key") or "").strip()
    return value if value else None


def set_api_key(api_key: str) -> None:
    """Persist a new override key."""
    value = str(api_key or "").strip()
    with _LOCK:
        config = _read_config()
        if value:
            config["api_key"] = value
            config["updated_at"] = datetime.now(timezone.utc).isoformat()
        else:
            config.pop("api_key", None)
            config["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write_config(config)


def clear_api_key() -> None:
    """Remove any persisted override key."""
    with _LOCK:
        config = _read_config()
        config.pop("api_key", None)
        config["updated_at"] = datetime.now(timezone.utc).isoformat()
        _write_config(config)
