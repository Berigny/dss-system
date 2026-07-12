"""Vercel / Docker entrypoint shim for the DSS middleware.

The FastAPI application lives in `fastapi_app.py` at the component root.  This
module imports and re-exports it so deployment platforms can point at a stable
`src/main.py` path.
"""

import fastapi_app as _app

app = _app.app
