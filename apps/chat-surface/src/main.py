"""Vercel / Docker entrypoint shim for the DSS chat surface.

The legacy application lives in `app.py` at the component root.  This module
imports and re-exports the FastHTML/Starlette application so that deployment
platforms can point at a predictable `src/main.py` path without restructuring
thousands of lines of working code.
"""

import app as _app

app = _app.app
