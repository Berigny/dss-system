"""Docker / Fly entrypoint shim for the DSS backend.

The FastAPI application is defined in `backend.main`.  This module imports and
re-exports it so deployment platforms can point at a stable `src/main.py` path.
"""

from backend.main import app
