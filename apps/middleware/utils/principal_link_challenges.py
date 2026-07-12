from __future__ import annotations

import hashlib
import json
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


class PrincipalLinkChallenges:
    """Simple JSON-backed GitHub link challenge store."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            fallback = Path("/tmp/ds-middleware/principal_link_challenges.json")
            fallback.parent.mkdir(parents=True, exist_ok=True)
            self.path = fallback

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "challenges": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"version": 1, "challenges": {}}
        if not isinstance(payload, dict):
            return {"version": 1, "challenges": {}}
        challenges = payload.get("challenges")
        if not isinstance(challenges, dict):
            payload["challenges"] = {}
        payload.setdefault("version", 1)
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)

    def create(
        self,
        *,
        principal_did: str,
        github_user_id: str,
        github_login: str,
        github_email: str | None,
        contact_channel: str,
        contact_value: str,
        ttl_seconds: int = 600,
    ) -> dict[str, Any]:
        code = f"{secrets.randbelow(1_000_000):06d}"
        challenge_id = secrets.token_urlsafe(24)
        now = _utc_now()
        record = {
            "challenge_id": challenge_id,
            "principal_did": str(principal_did).strip(),
            "github_user_id": str(github_user_id).strip(),
            "github_login": str(github_login).strip(),
            "github_email": str(github_email or "").strip() or None,
            "contact_channel": str(contact_channel).strip(),
            "contact_value": str(contact_value).strip(),
            "code_sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
            "attempt_count": 0,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=max(60, ttl_seconds))).isoformat(),
        }
        with self._lock:
            payload = self._read()
            challenges = payload.get("challenges")
            if not isinstance(challenges, dict):
                challenges = {}
                payload["challenges"] = challenges
            challenges[challenge_id] = record
            self._write(payload)
        result = dict(record)
        result["code"] = code
        return result

    def verify(self, *, challenge_id: str, code: str) -> dict[str, Any]:
        cid = str(challenge_id or "").strip()
        provided = str(code or "").strip()
        if not cid or not provided:
            raise ValueError("challenge_id and code are required")
        with self._lock:
            payload = self._read()
            challenges = payload.get("challenges")
            if not isinstance(challenges, dict):
                raise KeyError("challenge not found")
            record = challenges.get(cid)
            if not isinstance(record, dict):
                raise KeyError("challenge not found")
            expires_at = str(record.get("expires_at") or "").strip()
            if expires_at:
                try:
                    expires_dt = datetime.fromisoformat(expires_at)
                except Exception:
                    expires_dt = _utc_now() - timedelta(seconds=1)
                if expires_dt <= _utc_now():
                    challenges.pop(cid, None)
                    self._write(payload)
                    raise TimeoutError("challenge expired")
            hashed = hashlib.sha256(provided.encode("utf-8")).hexdigest()
            if not secrets.compare_digest(str(record.get("code_sha256") or ""), hashed):
                record = dict(record)
                record["attempt_count"] = int(record.get("attempt_count") or 0) + 1
                challenges[cid] = record
                self._write(payload)
                raise PermissionError("invalid code")
            challenges.pop(cid, None)
            self._write(payload)
            return dict(record)
