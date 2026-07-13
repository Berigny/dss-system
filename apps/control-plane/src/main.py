"""Vercel / Docker entrypoint shim for the DSS control plane.

The legacy application lives in `app.py` at the component root.  This module
simply imports and re-exports the Starlette application so that deployment
platforms can point at a predictable `src/main.py` path without restructuring
thousands of lines of working code.
"""

import os
import sys
from pathlib import Path

# Make the vendored shared-types package importable on Vercel without an
# editable install (uv + rootDirectory can mis-resolve relative editable paths).
_vendor = Path(__file__).resolve().parent.parent / "vendor" / "shared-types"
if str(_vendor) not in sys.path:
    sys.path.insert(0, str(_vendor))

import app as _app  # noqa: E402

app = _app.app
