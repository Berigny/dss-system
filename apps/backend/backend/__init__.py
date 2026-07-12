"""Runtime backend package for the Dual-Substrate live console."""

from .main import (
    PCM_ROUTE,
    WS_ROUTE,
    app,
    configure,
    configure_from_env,
    get_state,
    set_state,
)

__all__ = [
    "PCM_ROUTE",
    "WS_ROUTE",
    "app",
    "configure",
    "configure_from_env",
    "get_state",
    "set_state",
]
