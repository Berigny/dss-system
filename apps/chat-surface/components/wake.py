"""Hidden HTMX trigger to warm the backend on page load."""

from fasthtml.common import Div


def wake_trigger():
    """Return a hidden div that silently pings the wake endpoint on load."""
    return Div(
        hx_get="/api/wake",
        hx_trigger="load",
        hx_swap="none",
        style="display: none;",
    )
