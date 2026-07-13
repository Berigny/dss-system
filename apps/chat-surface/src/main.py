"""Vercel / Docker entrypoint shim for the DSS chat surface."""

import sys
import traceback
from pathlib import Path

_vendor = Path(__file__).resolve().parent.parent / "vendor" / "shared-types"
if str(_vendor) not in sys.path:
    sys.path.insert(0, str(_vendor))

app = None  # noqa: F841

try:
    import app as _app  # noqa: E402
    app = _app.app
except Exception as _exc:  # pragma: no cover - debugging helper for Vercel cold-start
    _tb = traceback.format_exc()
    _exc_str = str(_exc)

    async def _debug_app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 500,
                "headers": [[b"content-type", b"text/plain; charset=utf-8"]],
            }
        )
        body = f"Import error in chat-surface: {_exc_str}\n\n{_tb}".encode("utf-8")
        await send({"type": "http.response.body", "body": body})

    app = _debug_app
