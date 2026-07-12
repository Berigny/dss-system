"""Utilities for formatting message coordinates.

Re-exported from ``shared_types.coord_schema`` so the middleware and other
apps share a single canonical formatter.
"""

from __future__ import annotations

from shared_types.coord_schema import format_coordinate

__all__ = ["format_coordinate"]
