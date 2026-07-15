"""Principal registry for middleware IAM bootstrap (P1-01)."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_email(value: str | None) -> str:
    return str(value or "").strip().lower()


def _normalize_phone(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    keep_plus = raw.startswith("+")
    digits = re.sub(r"\D+", "", raw)
    if not digits:
        return ""
    return f"+{digits}" if keep_plus or raw.startswith("00") else digits


_CANONICAL_ACTOR_TYPES = {"human", "model", "agent", "service", "organisation", "device"}
_ACTOR_TYPE_ALIASES = {
    "application": "service",
    "node": "agent",
    "organization": "organisation",
}


class PrincipalRegistry:
    """Simple JSON-backed principal registry keyed by principal_did."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            fallback = Path('/tmp/ds-middleware/principal_registry.json')
            fallback.parent.mkdir(parents=True, exist_ok=True)
            self.path = fallback

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "principals": {}, "subject_events": [], "standing_events": [], "binding_events": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"version": 1, "principals": {}, "subject_events": [], "standing_events": [], "binding_events": []}
        if not isinstance(payload, dict):
            return {"version": 1, "principals": {}, "subject_events": [], "standing_events": [], "binding_events": []}
        principals = payload.get("principals")
        if not isinstance(principals, dict):
            payload["principals"] = {}
        subject_events = payload.get("subject_events")
        if not isinstance(subject_events, list):
            payload["subject_events"] = []
        standing_events = payload.get("standing_events")
        if not isinstance(standing_events, list):
            payload["standing_events"] = []
        binding_events = payload.get("binding_events")
        if not isinstance(binding_events, list):
            payload["binding_events"] = []
        payload.setdefault("version", 1)
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)

    @staticmethod
    def _normalize_actor_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(metadata)
        actor_type = str(normalized.get("actor_type") or "").strip().lower()
        if actor_type:
            actor_type = _ACTOR_TYPE_ALIASES.get(actor_type, actor_type)
            if actor_type not in _CANONICAL_ACTOR_TYPES:
                raise ValueError(
                    "metadata.actor_type must be one of human, model, agent, service, organisation, device"
                )
            normalized["actor_type"] = actor_type
        vc_status = str(normalized.get("vc_status") or "").strip().lower()
        if vc_status:
            if vc_status not in {"none", "bound", "verified", "revoked", "expired"}:
                raise ValueError("metadata.vc_status must be one of none, bound, verified, revoked, expired")
            normalized["vc_status"] = vc_status
        wallet_capable = normalized.get("wallet_capable")
        if wallet_capable is not None:
            normalized["wallet_capable"] = bool(wallet_capable)
        return normalized

    @staticmethod
    def normalize_key_ref(value: str) -> str:
        ref = str(value or "").strip()
        if not ref:
            raise ValueError("principal_key_ref is required")
        prefix, sep, suffix = ref.partition(":")
        if not sep:
            raise ValueError("principal_key_ref must be namespaced")
        namespace = prefix.strip().lower()
        body = suffix.strip()
        if namespace == "did":
            if not body:
                raise ValueError("principal_key_ref body is required")
            return f"did:{body}"
        lowered = ref.lower()
        for known_prefix in (
            "github:user:",
            "openrouter:model:",
            "openrouter:provider:",
            "openrouter:agent:",
            "ollama:model:",
            "ollama:agent:",
            "openai:agent:",
            "mcp:server:",
            "node:key:",
        ):
            if lowered.startswith(known_prefix):
                normalized_body = ref[len(known_prefix):].strip().lower()
                if not normalized_body:
                    raise ValueError("principal_key_ref body is required")
                return f"{known_prefix}{normalized_body}"
        if lowered.startswith("node:url:") or lowered.startswith("service:url:"):
            url_prefix = "node:url:" if lowered.startswith("node:url:") else "service:url:"
            raw_url = ref[len(url_prefix):].strip()
            parsed = urlsplit(raw_url)
            if not parsed.scheme or not parsed.netloc:
                raise ValueError(f"{url_prefix[:-1]} binding must include absolute URL")
            path = parsed.path.rstrip("/")
            normalized_url = urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, "", ""))
            return f"{url_prefix}{normalized_url}"
        if lowered.startswith("wallet:"):
            normalized_body = ref[len("wallet:"):].strip().lower()
            if not normalized_body:
                raise ValueError("principal_key_ref body is required")
            return f"wallet:{normalized_body}"
        raise ValueError("unsupported principal_key_ref namespace")

    def _normalize_key_refs(self, refs: list[str] | None) -> list[str]:
        merged_refs: list[str] = []
        seen_refs: set[str] = set()
        for item in refs or []:
            ref = self.normalize_key_ref(item)
            if ref in seen_refs:
                continue
            seen_refs.add(ref)
            merged_refs.append(ref)
        return merged_refs

    @staticmethod
    def _canonical_subject_from_inputs(
        *,
        principal_did: str,
        actor_metadata: dict[str, Any],
        key_refs: list[str],
    ) -> tuple[str, str]:
        wallet_capable = bool(actor_metadata.get("wallet_capable"))
        actor_type = str(actor_metadata.get("actor_type") or "").strip().lower()
        if wallet_capable or actor_type == "human":
            return str(principal_did).strip(), "principal_did"

        candidates: list[tuple[int, str, str]] = []
        for ref in key_refs:
            text = str(ref or "").strip()
            lowered = text.lower()
            if lowered.startswith("openrouter:model:"):
                candidates.append((100, f"openrouter:model:{text[len('openrouter:model:'):]}", "binding:openrouter:model"))
            elif lowered.startswith("ollama:model:"):
                candidates.append((100, f"ollama:model:{text[len('ollama:model:'):]}", "binding:ollama:model"))
            elif lowered.startswith("node:key:"):
                candidates.append((95, f"node:key:{text[len('node:key:'):]}", "binding:node:key"))
            elif lowered.startswith("node:url:"):
                candidates.append((90, f"node:url:{text[len('node:url:'):]}", "binding:node:url"))
            elif lowered.startswith("mcp:server:"):
                candidates.append((85, f"mcp:server:{text[len('mcp:server:'):]}", "binding:mcp:server"))
            elif lowered.startswith("github:user:"):
                candidates.append((80, f"github:user:{text[len('github:user:'):]}", "binding:github:user"))
            elif lowered.startswith("openrouter:provider:"):
                candidates.append((70, f"openrouter:provider:{text[len('openrouter:provider:'):]}", "binding:openrouter:provider"))

        if candidates:
            top_priority = max(priority for priority, _, _ in candidates)
            top_subjects = {(subject, source) for priority, subject, source in candidates if priority == top_priority}
            if len({subject for subject, _ in top_subjects}) != 1:
                raise ValueError("canonical subject is ambiguous across bound identities")
            subject, source = next(iter(top_subjects))
            return subject, source

        return str(principal_did).strip(), "principal_did"

    @staticmethod
    def _key_ref_conflict_message(ref: str) -> str:
        return f"principal_key_ref already bound: {ref}"

    def _ensure_key_ref_uniqueness(
        self,
        principals: dict[str, Any],
        *,
        principal_did: str,
        tenant_id: str,
        key_refs: list[str],
    ) -> None:
        for did, row in principals.items():
            if did == principal_did or not isinstance(row, dict):
                continue
            if str(row.get("tenant_id") or "").strip() != tenant_id:
                continue
            if str(row.get("status") or "active").strip().lower() != "active":
                continue
            other_refs = row.get("principal_key_refs")
            if not isinstance(other_refs, list):
                continue
            other_set = {str(item).strip() for item in other_refs if str(item).strip()}
            for ref in key_refs:
                if ref in other_set:
                    raise RuntimeError(self._key_ref_conflict_message(ref))

    def _ensure_canonical_subject_uniqueness(
        self,
        principals: dict[str, Any],
        *,
        principal_did: str,
        tenant_id: str,
        canonical_subject: str,
    ) -> None:
        for did, row in principals.items():
            if did == principal_did or not isinstance(row, dict):
                continue
            if str(row.get("tenant_id") or "").strip() != tenant_id:
                continue
            if str(row.get("status") or "active").strip().lower() != "active":
                continue
            other_subject = str(row.get("canonical_subject") or "").strip()
            if other_subject and other_subject == canonical_subject:
                raise RuntimeError(f"canonical_subject already bound: {canonical_subject}")

    def list(
        self,
        *,
        status: str | None = None,
        tenant_id: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        status_filter = (status or "").strip().lower()
        tenant_filter = (tenant_id or "").strip()
        safe_limit = max(1, min(int(limit), 1000))
        safe_offset = max(0, int(offset))

        with self._lock:
            payload = self._read()
            principals = payload.get("principals")
            rows: list[dict[str, Any]] = []
            if isinstance(principals, dict):
                for did in sorted(principals.keys()):
                    row = principals.get(did)
                    if not isinstance(row, dict):
                        continue
                    if status_filter in {"active", "disabled"}:
                        row_status = str(row.get("status") or "active").strip().lower()
                        if row_status != status_filter:
                            continue
                    if tenant_filter:
                        row_tenant = str(row.get("tenant_id") or "").strip()
                        if row_tenant != tenant_filter:
                            continue
                    rows.append(dict(row))
            return rows[safe_offset : safe_offset + safe_limit]

    def get(self, principal_did: str) -> dict[str, Any] | None:
        did = str(principal_did or "").strip()
        if not did:
            return None
        with self._lock:
            payload = self._read()
            principals = payload.get("principals")
            if not isinstance(principals, dict):
                return None
            row = principals.get(did)
            if not isinstance(row, dict):
                return None
            return dict(row)

    @staticmethod
    def _default_standing_view() -> dict[str, Any]:
        return {
            "trust_class": "T1",
            "posture_class": "P1",
            "operator_profile": None,
            "probation_status": "probation",
            "active_sanctions": [],
            "last_event_id": None,
            "last_event_type": None,
            "last_reason_code": None,
            "credential_ref": None,
            "standing_envelope_ref": None,
            "updated_at": None,
        }

    def get_standing_view(self, principal_did: str) -> dict[str, Any] | None:
        record = self.get(principal_did)
        if not isinstance(record, dict):
            return None
        standing_view = record.get("standing_view")
        if isinstance(standing_view, dict):
            return dict(standing_view)
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        fallback = self._default_standing_view()
        probation_status = str(metadata.get("probation_status") or "").strip()
        if probation_status:
            fallback["probation_status"] = probation_status
        fallback["updated_at"] = str(record.get("updated_at") or "").strip() or None
        return fallback

    def _append_subject_event(
        self,
        payload: dict[str, Any],
        *,
        principal_did: str,
        canonical_subject: str,
        tenant_id: str,
        event_type: str,
        reason: str | None = None,
        issuer: str | None = None,
        evidence_refs: list[str] | None = None,
        prior_subject: str | None = None,
        resulting_subject: str | None = None,
        standing_carryover: str | None = None,
        credential_carryover: str | None = None,
    ) -> dict[str, Any]:
        subject_events = payload.setdefault("subject_events", [])
        if not isinstance(subject_events, list):
            subject_events = []
            payload["subject_events"] = subject_events
        event = {
            "event_id": f"sevt:{uuid.uuid4().hex}",
            "principal_did": principal_did,
            "canonical_subject": canonical_subject,
            "tenant_id": tenant_id,
            "event_type": event_type,
            "reason": str(reason or "").strip(),
            "issuer": str(issuer or "").strip() or "system",
            "evidence_refs": [str(item).strip() for item in (evidence_refs or []) if str(item).strip()],
            "prior_subject": str(prior_subject or canonical_subject).strip() or canonical_subject,
            "resulting_subject": str(resulting_subject or canonical_subject).strip() or canonical_subject,
            "standing_carryover": str(standing_carryover or "inherit").strip() or "inherit",
            "credential_carryover": str(credential_carryover or "inherit").strip() or "inherit",
            "created_at": _utc_now_iso(),
        }
        subject_events.append(event)
        return event

    def list_subject_events(self, principal_did: str, *, limit: int = 50) -> list[dict[str, Any]]:
        did = str(principal_did or "").strip()
        safe_limit = max(1, min(int(limit), 200))
        with self._lock:
            payload = self._read()
            rows = payload.get("subject_events")
            if not isinstance(rows, list):
                return []
            filtered = [
                dict(row)
                for row in rows
                if isinstance(row, dict) and str(row.get("principal_did") or "").strip() == did
            ]
            return filtered[-safe_limit:]

    def list_standing_events(self, principal_did: str, *, limit: int = 50) -> list[dict[str, Any]]:
        did = str(principal_did or "").strip()
        safe_limit = max(1, min(int(limit), 200))
        with self._lock:
            payload = self._read()
            rows = payload.get("standing_events")
            if not isinstance(rows, list):
                return []
            filtered = [
                dict(row)
                for row in rows
                if isinstance(row, dict) and str(row.get("principal_did") or "").strip() == did
            ]
            return filtered[-safe_limit:]

    @staticmethod
    def _validate_standing_event_type(event_type: str) -> str:
        target = str(event_type or "").strip().lower()
        allowed = {
            "sanction",
            "repair",
            "decay",
            "probation",
            "trust_adjustment",
        }
        if target not in allowed:
            raise ValueError("unsupported standing event_type")
        return target

    @staticmethod
    def _validate_standing_issuer(issuer: str, event_type: str) -> str:
        normalized = str(issuer or "").strip()
        if not normalized:
            raise ValueError("issuer is required")
        lowered = normalized.lower()
        event = str(event_type or "").strip().lower()
        if lowered.startswith("self:"):
            raise ValueError("self-issued standing events are forbidden")
        if event in {"repair", "trust_adjustment"} and lowered.startswith("advisory:"):
            raise ValueError("advisory issuer cannot directly grant repair or trust adjustment")
        return normalized

    @staticmethod
    def _validate_reason_code(reason_code: str) -> str:
        normalized = str(reason_code or "").strip().lower()
        if not normalized:
            raise ValueError("reason_code is required")
        return normalized

    def _ensure_standing_event_idempotency(
        self,
        standing_events: list[dict[str, Any]],
        *,
        principal_did: str,
        issuer: str,
        idempotency_key: str,
    ) -> None:
        for row in standing_events:
            if not isinstance(row, dict):
                continue
            if str(row.get("principal_did") or "").strip() != principal_did:
                continue
            if str(row.get("issuer") or "").strip() != issuer:
                continue
            if str(row.get("idempotency_key") or "").strip() == idempotency_key:
                raise RuntimeError(f"standing_event already recorded: {issuer}:{idempotency_key}")

    def append_standing_event(
        self,
        *,
        principal_did: str,
        event_type: str,
        issuer: str,
        reason_code: str,
        delta: dict[str, Any] | None = None,
        evidence_refs: list[str] | None = None,
        idempotency_key: str,
        credential_ref: str | None = None,
        standing_envelope_ref: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        did = str(principal_did or "").strip()
        if not did:
            raise ValueError("principal_did is required")
        normalized_event_type = self._validate_standing_event_type(event_type)
        normalized_issuer = self._validate_standing_issuer(issuer, normalized_event_type)
        normalized_reason_code = self._validate_reason_code(reason_code)
        nonce = str(idempotency_key or "").strip()
        if not nonce:
            raise ValueError("idempotency_key is required")

        with self._lock:
            payload = self._read()
            principals = payload.get("principals")
            if not isinstance(principals, dict):
                principals = {}
                payload["principals"] = principals
            row = principals.get(did)
            if not isinstance(row, dict):
                raise KeyError("principal not found")

            standing_events = payload.setdefault("standing_events", [])
            if not isinstance(standing_events, list):
                standing_events = []
                payload["standing_events"] = standing_events
            self._ensure_standing_event_idempotency(
                standing_events,
                principal_did=did,
                issuer=normalized_issuer,
                idempotency_key=nonce,
            )

            canonical_subject = str(row.get("canonical_subject") or did).strip() or did
            tenant_id = str(row.get("tenant_id") or "").strip()
            event = {
                "event_id": f"stevt:{uuid.uuid4().hex}",
                "principal_did": did,
                "canonical_subject": canonical_subject,
                "tenant_id": tenant_id,
                "event_type": normalized_event_type,
                "issuer": normalized_issuer,
                "reason_code": normalized_reason_code,
                "delta": dict(delta or {}),
                "evidence_refs": [str(item).strip() for item in (evidence_refs or []) if str(item).strip()],
                "idempotency_key": nonce,
                "credential_ref": str(credential_ref or "").strip() or None,
                "standing_envelope_ref": str(standing_envelope_ref or "").strip() or None,
                "created_at": _utc_now_iso(),
            }
            standing_events.append(event)

            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            metadata = dict(metadata)
            metadata["last_standing_event_id"] = event["event_id"]
            metadata["last_standing_event_type"] = normalized_event_type
            metadata["last_standing_reason_code"] = normalized_reason_code
            standing_view = row.get("standing_view") if isinstance(row.get("standing_view"), dict) else self._default_standing_view()
            standing_view = dict(standing_view)
            delta_dict = event.get("delta") if isinstance(event.get("delta"), dict) else {}
            if isinstance(delta_dict.get("trust_class"), str) and str(delta_dict.get("trust_class")).strip():
                standing_view["trust_class"] = str(delta_dict.get("trust_class")).strip()
            if isinstance(delta_dict.get("posture_class"), str) and str(delta_dict.get("posture_class")).strip():
                standing_view["posture_class"] = str(delta_dict.get("posture_class")).strip()
            if isinstance(delta_dict.get("operator_profile"), str):
                operator_profile = str(delta_dict.get("operator_profile") or "").strip().lower()
                standing_view["operator_profile"] = operator_profile or None
            probation_override_raw = delta_dict.get("probation_status")
            if isinstance(probation_override_raw, str):
                probation_override = str(probation_override_raw or "").strip().lower()
                if probation_override in {"", "none", "clear", "cleared", "active"}:
                    standing_view["probation_status"] = None
                elif probation_override == "probation":
                    standing_view["probation_status"] = "probation"
            active_sanctions = standing_view.get("active_sanctions") if isinstance(standing_view.get("active_sanctions"), list) else []
            active_sanctions = [str(item).strip() for item in active_sanctions if str(item).strip()]
            if normalized_event_type == "sanction":
                if normalized_reason_code not in active_sanctions:
                    active_sanctions.append(normalized_reason_code)
                standing_view["probation_status"] = "probation"
            elif normalized_event_type == "repair":
                active_sanctions = [item for item in active_sanctions if item != normalized_reason_code]
                standing_view["probation_status"] = "probation"
            elif normalized_event_type == "probation":
                standing_view["probation_status"] = "probation"
            standing_view["active_sanctions"] = active_sanctions
            standing_view["last_event_id"] = event["event_id"]
            standing_view["last_event_type"] = normalized_event_type
            standing_view["last_reason_code"] = normalized_reason_code
            standing_view["credential_ref"] = event.get("credential_ref")
            standing_view["standing_envelope_ref"] = event.get("standing_envelope_ref")
            standing_view["updated_at"] = event["created_at"]
            probation_status = str(standing_view.get("probation_status") or "").strip()
            if probation_status:
                metadata["probation_status"] = probation_status
            else:
                metadata.pop("probation_status", None)
                metadata.pop("probation_reason", None)
            row = dict(row)
            row["metadata"] = metadata
            row["standing_view"] = standing_view
            row["updated_at"] = _utc_now_iso()
            principals[did] = row
            self._write(payload)
            return dict(row), event

    def upsert(
        self,
        *,
        principal_did: str,
        principal_key_refs: list[str] | None = None,
        tenant_id: str | None = None,
        display_name: str | None = None,
        metadata: dict[str, Any] | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        did = str(principal_did or "").strip()
        if not did:
            raise ValueError("principal_did is required")
        if not did.startswith("did:"):
            raise ValueError("principal_did must start with did:")

        now = _utc_now_iso()
        safe_status = str(status or "active").strip().lower()
        if safe_status not in {"active", "disabled"}:
            safe_status = "active"

        with self._lock:
            payload = self._read()
            principals = payload.setdefault("principals", {})
            if not isinstance(principals, dict):
                principals = {}
                payload["principals"] = principals

            existing = principals.get(did)
            created_at = now
            if isinstance(existing, dict) and isinstance(existing.get("created_at"), str):
                created_at = str(existing.get("created_at"))

            merged_metadata: dict[str, Any] = {}
            if isinstance(existing, dict) and isinstance(existing.get("metadata"), dict):
                merged_metadata.update(existing.get("metadata") or {})
            if isinstance(metadata, dict):
                merged_metadata.update(metadata)
            merged_metadata = self._normalize_actor_metadata(merged_metadata)
            email_normalized = _normalize_email(merged_metadata.get("email"))
            if email_normalized:
                merged_metadata["email_normalized"] = email_normalized
            phone_normalized = _normalize_phone(merged_metadata.get("phone"))
            if phone_normalized:
                merged_metadata["phone_normalized"] = phone_normalized
            existing_refs = existing.get("principal_key_refs") if isinstance(existing, dict) and isinstance(existing.get("principal_key_refs"), list) else []
            keys = self._normalize_key_refs([*existing_refs, *(principal_key_refs or [])])
            tenant_value = str(tenant_id or (existing.get("tenant_id") if isinstance(existing, dict) else "") or "tenant:unknown").strip() or "tenant:unknown"
            self._ensure_key_ref_uniqueness(
                principals,
                principal_did=did,
                tenant_id=tenant_value,
                key_refs=keys,
            )
            canonical_subject, canonical_subject_source = self._canonical_subject_from_inputs(
                principal_did=did,
                actor_metadata=merged_metadata,
                key_refs=keys,
            )
            self._ensure_canonical_subject_uniqueness(
                principals,
                principal_did=did,
                tenant_id=tenant_value,
                canonical_subject=canonical_subject,
            )

            record = {
                "principal_did": did,
                "principal_key_refs": keys,
                "canonical_subject": canonical_subject,
                "canonical_subject_source": canonical_subject_source,
                "tenant_id": tenant_value,
                "display_name": str(display_name or did).strip() or did,
                "status": safe_status,
                "created_at": created_at,
                "updated_at": now,
                "metadata": merged_metadata,
                "actor_type": merged_metadata.get("actor_type"),
                "standing_view": dict(existing.get("standing_view"))
                if isinstance(existing, dict) and isinstance(existing.get("standing_view"), dict)
                else self._default_standing_view(),
            }
            if not isinstance(existing, dict):
                probation_meta = record["metadata"] if isinstance(record.get("metadata"), dict) else {}
                probation_meta.setdefault("probation_status", "probation")
                probation_meta.setdefault("probation_reason", "fresh_subject_created")
                record["metadata"] = probation_meta
                record["standing_view"]["probation_status"] = "probation"
                record["standing_view"]["updated_at"] = now
            principals[did] = record
            if not isinstance(existing, dict):
                self._append_subject_event(
                    payload,
                    principal_did=did,
                    canonical_subject=canonical_subject,
                    tenant_id=tenant_value,
                    event_type="fresh_subject_created",
                    reason="principal created",
                    issuer="system",
                    standing_carryover="none",
                    credential_carryover="none",
                )
            self._write(payload)
            return dict(record)

    def resolve_key_ref(self, principal_key_ref: str, *, tenant_id: str | None = None) -> dict[str, Any]:
        try:
            key_ref = self.normalize_key_ref(principal_key_ref)
        except ValueError:
            return {
                "outcome": "not_found",
                "principal_key_ref": str(principal_key_ref or "").strip(),
                "canonical_principal_key_ref": None,
                "tenant_id": str(tenant_id or "").strip() or None,
                "principal": None,
                "conflicting_principals": [],
            }
        tenant_filter = str(tenant_id or "").strip()
        active_matches: list[dict[str, Any]] = []
        with self._lock:
            payload = self._read()
            principals = payload.get("principals")
            if not isinstance(principals, dict):
                principals = {}
            for did in sorted(principals.keys()):
                row = principals.get(did)
                if not isinstance(row, dict):
                    continue
                if tenant_filter and str(row.get("tenant_id") or "").strip() != tenant_filter:
                    continue
                if str(row.get("status") or "active").strip().lower() != "active":
                    continue
                refs = row.get("principal_key_refs") if isinstance(row.get("principal_key_refs"), list) else []
                if key_ref in {str(item).strip() for item in refs if str(item).strip()}:
                    active_matches.append(dict(row))
        result = {
            "principal_key_ref": str(principal_key_ref or "").strip(),
            "canonical_principal_key_ref": key_ref,
            "tenant_id": tenant_filter or None,
        }
        if not active_matches:
            result.update({"outcome": "not_found", "principal": None, "conflicting_principals": []})
            return result
        if len(active_matches) > 1:
            result.update({
                "outcome": "conflict",
                "principal": None,
                "conflicting_principals": [
                    {
                        "principal_did": str(row.get("principal_did") or "").strip(),
                        "tenant_id": str(row.get("tenant_id") or "").strip() or None,
                        "status": str(row.get("status") or "").strip() or None,
                    }
                    for row in active_matches
                ],
            })
            return result
        result.update({"outcome": "resolved", "principal": active_matches[0], "conflicting_principals": []})
        return result

    def _append_binding_event(
        self,
        payload: dict[str, Any],
        *,
        principal_did: str,
        tenant_id: str,
        principal_key_ref: str,
        issuer: str | None = None,
        evidence_refs: list[str] | None = None,
        reason: str | None = None,
        idempotency_key: str | None = None,
        lifecycle_state: str = "active",
    ) -> dict[str, Any]:
        events = payload.get("binding_events")
        if not isinstance(events, list):
            events = []
            payload["binding_events"] = events
        normalized_idempotency = str(idempotency_key or "").strip()
        normalized_evidence = [str(item).strip() for item in (evidence_refs or []) if str(item).strip()]
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "principal_did": principal_did,
                    "tenant_id": tenant_id,
                    "principal_key_ref": principal_key_ref,
                    "issuer": str(issuer or "system").strip() or "system",
                    "reason": str(reason or "").strip() or None,
                    "evidence_refs": normalized_evidence,
                    "lifecycle_state": lifecycle_state,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if normalized_idempotency:
            for event in events:
                if not isinstance(event, dict):
                    continue
                if str(event.get("idempotency_key") or "").strip() != normalized_idempotency:
                    continue
                if str(event.get("fingerprint") or "").strip() != fingerprint:
                    raise RuntimeError("binding event idempotency_key already used for a different transition")
                return dict(event)
        event = {
            "event_id": f"binding-event:{uuid.uuid4().hex}",
            "principal_did": principal_did,
            "tenant_id": tenant_id or None,
            "principal_key_ref": principal_key_ref,
            "event_type": "binding_activated",
            "lifecycle_state": lifecycle_state,
            "issuer": str(issuer or "system").strip() or "system",
            "reason": str(reason or "").strip() or None,
            "evidence_refs": normalized_evidence,
            "idempotency_key": normalized_idempotency or None,
            "fingerprint": fingerprint,
            "created_at": _utc_now_iso(),
        }
        events.append(event)
        return dict(event)

    def list_binding_events(self, principal_did: str, *, limit: int = 50) -> list[dict[str, Any]]:
        did = str(principal_did or "").strip()
        if not did:
            return []
        with self._lock:
            payload = self._read()
            events = payload.get("binding_events")
            if not isinstance(events, list):
                return []
            rows = [dict(event) for event in events if isinstance(event, dict) and str(event.get("principal_did") or "").strip() == did]
            return rows[-max(1, int(limit)):]

    def find_by_key_ref(self, principal_key_ref: str, *, tenant_id: str | None = None) -> dict[str, Any] | None:
        resolved = self.resolve_key_ref(principal_key_ref, tenant_id=tenant_id)
        principal = resolved.get("principal")
        return dict(principal) if isinstance(principal, dict) else None

    def find_by_contact(
        self,
        *,
        email: str | None = None,
        phone: str | None = None,
        tenant_id: str | None = None,
        status: str = "active",
    ) -> list[dict[str, Any]]:
        email_normalized = _normalize_email(email)
        phone_normalized = _normalize_phone(phone)
        tenant_filter = str(tenant_id or "").strip()
        status_filter = str(status or "").strip().lower()
        rows: list[dict[str, Any]] = []
        with self._lock:
            payload = self._read()
            principals = payload.get("principals")
            if not isinstance(principals, dict):
                return []
            for did in sorted(principals.keys()):
                row = principals.get(did)
                if not isinstance(row, dict):
                    continue
                if status_filter and str(row.get("status") or "").strip().lower() != status_filter:
                    continue
                if tenant_filter and str(row.get("tenant_id") or "").strip() != tenant_filter:
                    continue
                metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                row_email = _normalize_email(metadata.get("email_normalized") or metadata.get("email"))
                row_phone = _normalize_phone(metadata.get("phone_normalized") or metadata.get("phone"))
                if email_normalized and row_email == email_normalized:
                    rows.append(dict(row))
                    continue
                if phone_normalized and row_phone == phone_normalized:
                    rows.append(dict(row))
            return rows

    def link_github_identity(
        self,
        *,
        principal_did: str,
        github_user_id: str,
        github_login: str | None = None,
        github_email: str | None = None,
    ) -> dict[str, Any]:
        did = str(principal_did or "").strip()
        user_id = str(github_user_id or "").strip()
        if not did:
            raise ValueError("principal_did is required")
        if not user_id:
            raise ValueError("github_user_id is required")
        key_ref = self.normalize_key_ref(f"github:user:{user_id}")
        with self._lock:
            payload = self._read()
            principals = payload.get("principals")
            if not isinstance(principals, dict):
                principals = {}
                payload["principals"] = principals
            row = principals.get(did)
            if not isinstance(row, dict):
                raise KeyError("principal not found")
            refs = row.get("principal_key_refs") if isinstance(row.get("principal_key_refs"), list) else []
            merged_refs = self._normalize_key_refs([*refs, key_ref])
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            metadata = dict(metadata)
            metadata["auth_provider"] = "github"
            metadata["github_user_id"] = user_id
            if github_login:
                metadata["github_login"] = str(github_login).strip()
            if github_email:
                metadata["github_email"] = _normalize_email(github_email)
            metadata["github_link_status"] = "linked"
            metadata["github_linked_at"] = _utc_now_iso()
            email_normalized = _normalize_email(metadata.get("email"))
            if email_normalized:
                metadata["email_normalized"] = email_normalized
            phone_normalized = _normalize_phone(metadata.get("phone"))
            if phone_normalized:
                metadata["phone_normalized"] = phone_normalized
            self._ensure_key_ref_uniqueness(
                principals,
                principal_did=did,
                tenant_id=str(row.get("tenant_id") or "").strip(),
                key_refs=merged_refs,
            )
            row = dict(row)
            row["principal_key_refs"] = merged_refs
            row["metadata"] = metadata
            row["actor_type"] = metadata.get("actor_type")
            canonical_subject, canonical_subject_source = self._canonical_subject_from_inputs(
                principal_did=did,
                actor_metadata=metadata,
                key_refs=merged_refs,
            )
            self._ensure_canonical_subject_uniqueness(
                principals,
                principal_did=did,
                tenant_id=str(row.get("tenant_id") or "").strip(),
                canonical_subject=canonical_subject,
            )
            row["canonical_subject"] = canonical_subject
            row["canonical_subject_source"] = canonical_subject_source
            row["updated_at"] = _utc_now_iso()
            principals[did] = row
            self._write(payload)
            return dict(row)

    def bind_key_ref(
        self,
        *,
        principal_did: str,
        principal_key_ref: str,
        tenant_id: str | None = None,
        binding_metadata: dict[str, Any] | None = None,
        issuer: str | None = None,
        evidence_refs: list[str] | None = None,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        did = str(principal_did or "").strip()
        if not did:
            raise ValueError("principal_did is required")
        key_ref = self.normalize_key_ref(principal_key_ref)
        with self._lock:
            payload = self._read()
            principals = payload.get("principals")
            if not isinstance(principals, dict):
                principals = {}
                payload["principals"] = principals
            row = principals.get(did)
            if not isinstance(row, dict):
                raise KeyError("principal not found")
            row_tenant = str(row.get("tenant_id") or "").strip()
            tenant_filter = str(tenant_id or "").strip()
            if tenant_filter and tenant_filter != row_tenant:
                raise ValueError("tenant_id does not match principal tenant")
            refs = row.get("principal_key_refs") if isinstance(row.get("principal_key_refs"), list) else []
            merged_refs = self._normalize_key_refs([*refs, key_ref])
            self._ensure_key_ref_uniqueness(
                principals,
                principal_did=did,
                tenant_id=row_tenant,
                key_refs=merged_refs,
            )
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            metadata = dict(metadata)
            if isinstance(binding_metadata, dict):
                metadata.update(binding_metadata)
            metadata = self._normalize_actor_metadata(metadata)
            canonical_subject, canonical_subject_source = self._canonical_subject_from_inputs(
                principal_did=did,
                actor_metadata=metadata,
                key_refs=merged_refs,
            )
            self._ensure_canonical_subject_uniqueness(
                principals,
                principal_did=did,
                tenant_id=row_tenant,
                canonical_subject=canonical_subject,
            )
            row = dict(row)
            row["principal_key_refs"] = merged_refs
            row["metadata"] = metadata
            row["actor_type"] = metadata.get("actor_type")
            row["canonical_subject"] = canonical_subject
            row["canonical_subject_source"] = canonical_subject_source
            row["updated_at"] = _utc_now_iso()
            principals[did] = row
            event = self._append_binding_event(
                payload,
                principal_did=did,
                tenant_id=row_tenant,
                principal_key_ref=key_ref,
                issuer=issuer,
                evidence_refs=evidence_refs,
                reason=reason,
                idempotency_key=idempotency_key,
            )
            self._write(payload)
            return dict(row), event

    def append_subject_event(
        self,
        *,
        principal_did: str,
        event_type: str,
        reason: str | None = None,
        issuer: str | None = None,
        evidence_refs: list[str] | None = None,
        standing_carryover: str | None = None,
        credential_carryover: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        did = str(principal_did or "").strip()
        if not did:
            raise ValueError("principal_did is required")
        target = str(event_type or "").strip().lower()
        allowed = {
            "subject_reset_requested",
            "subject_reset_granted",
            "binding_transfer",
            "binding_succession",
            "fresh_subject_created",
        }
        if target not in allowed:
            raise ValueError("unsupported subject event_type")

        with self._lock:
            payload = self._read()
            principals = payload.get("principals")
            if not isinstance(principals, dict):
                principals = {}
                payload["principals"] = principals
            row = principals.get(did)
            if not isinstance(row, dict):
                raise KeyError("principal not found")

            canonical_subject = str(row.get("canonical_subject") or did).strip() or did
            tenant_id = str(row.get("tenant_id") or "").strip()
            event = self._append_subject_event(
                payload,
                principal_did=did,
                canonical_subject=canonical_subject,
                tenant_id=tenant_id,
                event_type=target,
                reason=reason,
                issuer=issuer,
                evidence_refs=evidence_refs,
                standing_carryover=standing_carryover,
                credential_carryover=credential_carryover,
            )

            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            metadata = dict(metadata)
            if target in {"subject_reset_requested", "subject_reset_granted", "binding_transfer", "binding_succession"}:
                metadata["probation_status"] = "probation"
                metadata["probation_reason"] = target
            row = dict(row)
            row["metadata"] = metadata
            row["updated_at"] = _utc_now_iso()
            principals[did] = row
            self._write(payload)
            return dict(row), event

    def set_status(self, principal_did: str, *, status: str, reason: str | None = None) -> dict[str, Any]:
        did = str(principal_did or "").strip()
        if not did:
            raise ValueError("principal_did is required")
        target = str(status or "").strip().lower()
        if target not in {"active", "disabled"}:
            raise ValueError("status must be active or disabled")

        with self._lock:
            payload = self._read()
            principals = payload.get("principals")
            if not isinstance(principals, dict):
                principals = {}
                payload["principals"] = principals
            row = principals.get(did)
            if not isinstance(row, dict):
                raise KeyError("principal not found")

            row["status"] = target
            row["updated_at"] = _utc_now_iso()
            meta = row.get("metadata")
            if not isinstance(meta, dict):
                meta = {}
            if reason:
                meta["status_reason"] = str(reason).strip()
            row["metadata"] = meta
            principals[did] = row
            self._write(payload)
            return dict(row)
