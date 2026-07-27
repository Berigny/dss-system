"""Configuration loader for adapter-specific settings.

Settings live in ``config/adapters.yaml``.  The loader expands
``${VAR}`` and ``${VAR:-default}`` syntax in string values so API
endpoints and secrets can be injected from the environment.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "adapters.yaml"

_cache: dict[Path, dict[str, Any]] = {}


def _expand_env(value: Any) -> Any:
    """Recursively expand ``${VAR}`` / ``${VAR:-default}`` in a value."""
    if isinstance(value, str):
        pattern = re.compile(r"\$\{([^}]+)\}")

        def repl(match: re.Match) -> str:
            inner = match.group(1)
            if ":-" in inner:
                var, default = inner.split(":-", 1)
                return os.environ.get(var, default)
            return os.environ.get(inner, "")

        return pattern.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """Load and cache the adapter configuration file."""
    path = Path(path or DEFAULT_CONFIG_PATH)
    if path in _cache:
        return _cache[path]

    if not path.exists():
        data: dict[str, Any] = {}
    else:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    data = _expand_env(data)
    _cache[path] = data
    return data


def get_adapter_config(
    adapter_name: str,
    path: Path | str | None = None,
) -> dict[str, Any]:
    """Return the configuration block for a single adapter."""
    return load_config(path).get("adapters", {}).get(adapter_name, {})
