from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class VerifiedIDRequests:
    """Simple JSON-backed store for Entra Verified ID request state."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            fallback = Path('/tmp/ds-middleware/verified_id_requests.json')
            fallback.parent.mkdir(parents=True, exist_ok=True)
            self.path = fallback

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "requests": {}}
        try:
            payload = json.loads(self.path.read_text(encoding='utf-8'))
        except Exception:
            return {"version": 1, "requests": {}}
        if not isinstance(payload, dict):
            return {"version": 1, "requests": {}}
        requests = payload.get('requests')
        if not isinstance(requests, dict):
            payload['requests'] = {}
        payload.setdefault('version', 1)
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        tmp_path = self.path.with_suffix(self.path.suffix + '.tmp')
        tmp_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding='utf-8')
        tmp_path.replace(self.path)

    def create(
        self,
        *,
        state: str,
        request_id: str,
        principal_did: str,
        mode: str,
        request_payload: dict[str, Any],
        response_payload: dict[str, Any],
    ) -> dict[str, Any]:
        safe_state = str(state or '').strip()
        safe_request_id = str(request_id or '').strip()
        if not safe_state:
            raise ValueError('state is required')
        if not safe_request_id:
            raise ValueError('request_id is required')
        now = _utc_now_iso()
        record = {
            'state': safe_state,
            'request_id': safe_request_id,
            'principal_did': str(principal_did or '').strip(),
            'mode': str(mode or '').strip() or 'presentation',
            'status': 'request_created',
            'request_payload': dict(request_payload or {}),
            'response_payload': dict(response_payload or {}),
            'request_url': str(response_payload.get('url') or '').strip() or None,
            'request_qr_code': str(response_payload.get('qrCode') or '').strip() or None,
            'expiry': response_payload.get('expiry'),
            'created_at': now,
            'updated_at': now,
            'callback_events': [],
            'finalization': None,
        }
        with self._lock:
            payload = self._read()
            requests = payload.setdefault('requests', {})
            if not isinstance(requests, dict):
                requests = {}
                payload['requests'] = requests
            requests[safe_state] = record
            self._write(payload)
        return dict(record)

    def get(self, state: str) -> dict[str, Any] | None:
        safe_state = str(state or '').strip()
        if not safe_state:
            return None
        with self._lock:
            payload = self._read()
            requests = payload.get('requests')
            if not isinstance(requests, dict):
                return None
            row = requests.get(safe_state)
            if not isinstance(row, dict):
                return None
            return dict(row)

    def update_callback(self, *, state: str, request_id: str | None, callback_payload: dict[str, Any]) -> dict[str, Any]:
        safe_state = str(state or '').strip()
        safe_request_id = str(request_id or '').strip()
        if not safe_state:
            raise KeyError('state not found')
        with self._lock:
            payload = self._read()
            requests = payload.get('requests')
            if not isinstance(requests, dict):
                raise KeyError('state not found')
            row = requests.get(safe_state)
            if not isinstance(row, dict):
                raise KeyError('state not found')
            existing_request_id = str(row.get('request_id') or '').strip()
            if safe_request_id and existing_request_id and safe_request_id != existing_request_id:
                raise KeyError('request_id mismatch')
            status = str(callback_payload.get('requestStatus') or '').strip() or 'callback_received'
            now = _utc_now_iso()
            events = row.get('callback_events') if isinstance(row.get('callback_events'), list) else []
            events.append(dict(callback_payload))
            row['callback_events'] = events
            row['status'] = status
            row['updated_at'] = now
            row['last_callback'] = dict(callback_payload)
            row['subject'] = str(callback_payload.get('subject') or '').strip() or row.get('subject')
            if isinstance(callback_payload.get('verifiedCredentialsData'), list):
                row['verified_credentials_data'] = callback_payload.get('verifiedCredentialsData')
            if 'receipt' in callback_payload:
                row['receipt'] = callback_payload.get('receipt')
            requests[safe_state] = row
            self._write(payload)
            return dict(row)

    def mark_finalization(self, *, state: str, finalization: dict[str, Any]) -> dict[str, Any]:
        safe_state = str(state or '').strip()
        if not safe_state:
            raise KeyError('state not found')
        with self._lock:
            payload = self._read()
            requests = payload.get('requests')
            if not isinstance(requests, dict):
                raise KeyError('state not found')
            row = requests.get(safe_state)
            if not isinstance(row, dict):
                raise KeyError('state not found')
            row['finalization'] = dict(finalization or {})
            row['updated_at'] = _utc_now_iso()
            requests[safe_state] = row
            self._write(payload)
            return dict(row)
