"""Shared pytest fixtures and test-wide environment defaults."""

import os


# DSS-232: source code no longer hardcodes deployment-specific hosts/URLs.
# The frontend app reads several settings at import time, so these defaults
# must be present before `app.py` is imported by the test modules.
os.environ.setdefault("CONTROL_PLANE_BASE", "https://id.dualsubstrate.com")
os.environ.setdefault("BASE_DOMAIN", "dualsubstrate.com")
