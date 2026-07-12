"""Helpers for normalizing coordinate decoder payloads.

Re-exported from ``shared_types.coord_schema`` so the middleware and backend
share a single canonical normalizer.
"""

from __future__ import annotations

from shared_types.coord_schema import normalize_coordinate_payload

__all__ = ["normalize_coordinate_payload"]
