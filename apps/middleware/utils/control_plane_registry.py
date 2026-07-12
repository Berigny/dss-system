"""JSON-backed control-plane registry for governed ledgers, surfaces, and relationships."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ControlPlaneRegistry:
    """Simple JSON-backed registry for control-plane governed records."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            fallback = Path("/tmp/ds-middleware/control_plane_registry.json")
            fallback.parent.mkdir(parents=True, exist_ok=True)
            self.path = fallback

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "ledgers": {}, "surfaces": {}, "relationships": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"version": 1, "ledgers": {}, "surfaces": {}, "relationships": {}}
        if not isinstance(payload, dict):
            return {"version": 1, "ledgers": {}, "surfaces": {}, "relationships": {}}
        for key in ("ledgers", "surfaces", "relationships"):
            if not isinstance(payload.get(key), dict):
                payload[key] = {}
        payload.setdefault("version", 1)
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)

    def list(self, kind: str) -> list[dict[str, Any]]:
        with self._lock:
            payload = self._read()
            rows = payload.get(kind)
            if not isinstance(rows, dict):
                return []
            return [dict(item) for _, item in sorted(rows.items()) if isinstance(item, dict)]

    def get(self, kind: str, record_id: str) -> dict[str, Any] | None:
        target = str(record_id or "").strip()
        if not target:
            return None
        with self._lock:
            payload = self._read()
            rows = payload.get(kind)
            if not isinstance(rows, dict):
                return None
            item = rows.get(target)
            return dict(item) if isinstance(item, dict) else None

    def upsert(self, kind: str, key_field: str, record: dict[str, Any]) -> dict[str, Any]:
        key = str(record.get(key_field) or "").strip()
        if not key:
            raise ValueError(f"{key_field} is required")
        now_iso = _utc_now_iso()
        with self._lock:
            payload = self._read()
            rows = payload.get(kind)
            if not isinstance(rows, dict):
                rows = {}
                payload[kind] = rows
            existing = rows.get(key) if isinstance(rows.get(key), dict) else {}
            next_record = dict(existing)
            next_record.update(record)
            next_record[key_field] = key
            next_record.setdefault("created_at", now_iso)
            next_record["updated_at"] = now_iso
            rows[key] = next_record
            self._write(payload)
            return dict(next_record)

    def activate(self, kind: str, key_field: str, record_id: str, *, status: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        target = str(record_id or "").strip()
        if not target:
            raise ValueError(f"{key_field} is required")
        with self._lock:
            payload = self._read()
            rows = payload.get(kind)
            if not isinstance(rows, dict):
                rows = {}
                payload[kind] = rows
            existing = rows.get(target)
            if not isinstance(existing, dict):
                raise KeyError(target)
            next_record = dict(existing)
            next_record[key_field] = target
            next_record["status"] = str(status or "active").strip().lower() or "active"
            if isinstance(extra, dict):
                next_record.update(extra)
            next_record["updated_at"] = _utc_now_iso()
            rows[target] = next_record
            self._write(payload)
            return dict(next_record)
