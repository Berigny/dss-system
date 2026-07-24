"""Vercel / Docker entrypoint shim for the DSS document surface."""

import sys
from pathlib import Path

# Make app imports resolvable when this file is used as the entrypoint.
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import app as _app  # noqa: E402

app = _app.app
