"""Vercel / Docker entrypoint shim for the DSS control plane.

The legacy application lives in `app.py` at the component root.  This module
simply imports and re-exports the Starlette application so that deployment
platforms can point at a predictable `src/main.py` path without restructuring
thousands of lines of working code.
"""

import app as _app

app = _app.app
