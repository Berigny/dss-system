"""Wake route to warm backend instances on load."""

from starlette.responses import JSONResponse

from api.client import api


async def _poke_backend():
    """Call a lightweight backend endpoint to trigger cold start wake-up."""
    try:
        await api.list_ledgers()
        return True
    except Exception:
        return False


def register_wake_routes(rt):
    @rt("/api/wake")
    async def wake_backend():
        """Silent ping to warm the backend when the frontend loads."""
        is_awake = await _poke_backend()
        status = "awake" if is_awake else "waking"
        return JSONResponse({"status": status})
