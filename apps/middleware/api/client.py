"""HTTP client for the ourIP.AI backend API."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from typing import Any

import json

import httpx
from shared_types.coord_schema import normalize_coordinate_metadata

# Ensure these settings exist in your config.settings
from config.settings import API_BASE, API_KEY, DEFAULT_LEDGER, HTTP_TIMEOUT


@dataclass
class ChatResponse:
    """Container for chat responses from the backend."""

    reply: str | None = None
    text: str | None = None
    stats: dict[str, Any] = field(default_factory=dict)
    knowledge_tree: list[Any] = field(default_factory=list)
    coordinate: str | None = None
    web4_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    appraisal: dict[str, Any] | None = None
    tokens: dict[str, Any] | None = None
    model: str | None = None
    cost_usd: float | None = None
    error: str | None = None
    unverified: bool = False
    padic_diagnostics: dict[str, Any] | None = None
    p_adic_write_cost: float | None = None
    query_primes_used: list[int] | None = None

    @property
    def primary_text(self) -> str | None:
        """Prefer reply over text for downstream consumers."""
        return self.reply or self.text

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "ChatResponse":
        payload = payload or {}
        return cls(
            reply=payload.get("reply"),
            text=payload.get("text"),
            stats=payload.get("stats") or {},
            knowledge_tree=payload.get("knowledge_tree") or [],
            coordinate=payload.get("coordinate"),
            web4_key=payload.get("web4_key"),
            metadata=normalize_coordinate_metadata(
                payload["metadata"] if "metadata" in payload else {}
            ),
            appraisal=payload.get("appraisal"),
            tokens=payload.get("tokens"),
            model=payload.get("model"),
            cost_usd=payload.get("cost_usd"),
            error=payload.get("error"),
            unverified=bool(payload.get("unverified", False)),
            padic_diagnostics=payload.get("padic_diagnostics"),
            p_adic_write_cost=payload.get("p_adic_write_cost"),
            query_primes_used=payload.get("query_primes_used"),
        )


class BackendDecodeError(Exception):
    """Raised when the backend /chat/web4/decode endpoint returns a non-2xx response."""

    def __init__(
        self,
        status_code: int,
        body: dict[str, Any],
        message: str = "",
    ):
        self.status_code = status_code
        self.body = body
        self.message = message
        super().__init__(message or f"backend decode error {status_code}")


class APIClient:
    """Async client wrapper for backend interactions."""

    def __init__(
        self,
        base_url: str = API_BASE,
        api_key: str = API_KEY,
        ledger_id: str = DEFAULT_LEDGER,
        timeout: float = HTTP_TIMEOUT,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.ledger_id = ledger_id
        self.timeout = timeout
        self._base_headers = {"Content-Type": "application/json"}
        self.context_id = ""

    @property
    def headers(self) -> dict[str, str]:
        """Return request headers with explicit ledger context."""
        headers = dict(self._base_headers)
        ledger_id = (self.ledger_id or "").strip()
        if ledger_id:
            headers["x-ledger-id"] = ledger_id
            digest = hashlib.sha256(ledger_id.encode("utf-8")).digest()
            headers["x-ledger-id-h64"] = digest[:8].hex()
        context_id = (self.context_id or "").strip()
        if context_id:
            headers["x-context-id"] = context_id
        return headers

    def _request_headers(self, *, auth_headers: dict[str, str] | None = None) -> dict[str, str]:
        headers = dict(self.headers)
        if isinstance(auth_headers, dict):
            for key, value in auth_headers.items():
                key_clean = str(key or "").strip()
                value_clean = str(value or "").strip()
                if key_clean and value_clean:
                    headers[key_clean] = value_clean
        return headers

    @staticmethod
    def _inject_auth_claims(
        payload: dict[str, Any],
        auth_claims: dict[str, str] | None,
    ) -> dict[str, Any]:
        out = dict(payload)
        if isinstance(auth_claims, dict):
            for key in ("principal_did", "principal_key_id", "session_jti", "context_id"):
                value = auth_claims.get(key)
                if isinstance(value, str) and value.strip():
                    out[key] = value.strip()
        return out

    def set_ledger(self, ledger_id: str) -> None:
        """Update the active ledger for subsequent requests."""
        if ledger_id:
            self.ledger_id = ledger_id

    def set_context(self, context_id: str) -> None:
        """Update the active context for subsequent requests."""
        self.context_id = (context_id or "").strip()

    # --- Core Ledger Methods ---

    async def get_history(self, entity: str, limit: int = 50) -> list[dict]:
        """Fetch chat history from the ledger."""
        safe_entity = httpx.URL(path=f"/{entity}").path.lstrip("/")
        url = f"{self.base_url}/ledger/history/{safe_entity}"
        params = {"limit": limit}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self.headers, params=params)
            resp.raise_for_status()
            
            payload = resp.json()
            if isinstance(payload, list):
                return payload
            return payload.get("history") or payload.get("messages") or []

    # Alias for app.py compatibility
    async def thread(self, entity: str, limit: int = 50) -> list[dict]:
        return await self.get_history(entity, limit)

    async def get_all_entries(self, limit: int = 100) -> dict:
        """Fetch recent ledger entries across all namespaces."""
        url = f"{self.base_url}/ledger/all"
        params = {"limit": limit}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self.headers, params=params)
            resp.raise_for_status()
            return resp.json()

    async def ledger_metrics(self, entity: str) -> dict:
        """Fetch summary metrics for a specific namespace."""
        url = f"{self.base_url}/ledger/summary/{entity}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self.headers)
            if resp.status_code == 404:
                return {} 
            resp.raise_for_status()
            return resp.json()

    async def session_stats(
        self,
        session_id: str,
        *,
        auth_headers: dict[str, str] | None = None,
    ) -> dict:
        url = f"{self.base_url}/stats/session"
        params = {"session_id": session_id}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                url,
                params=params,
                headers=self._request_headers(auth_headers=auth_headers),
            )
            if resp.status_code == 404:
                return {}
            resp.raise_for_status()
            return resp.json()

    async def latest_event(self, session_id: str) -> dict:
        url = f"{self.base_url}/stats/latest"
        params = {"session_id": session_id}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, params=params, headers=self.headers)
            if resp.status_code == 404:
                return {}
            resp.raise_for_status()
            return resp.json()

    async def accuracy_stats(self, session_id: str) -> dict:
        url = f"{self.base_url}/stats/accuracy"
        params = {"session_id": session_id}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, params=params, headers=self.headers)
            if resp.status_code == 404:
                return {}
            resp.raise_for_status()
            return resp.json()

    async def global_stats(self) -> dict:
        url = f"{self.base_url}/stats/global"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self.headers)
            if resp.status_code == 404:
                return {}
            resp.raise_for_status()
            return resp.json()

    async def emit_telemetry(
        self,
        payload: dict[str, Any],
        *,
        auth_headers: dict[str, str] | None = None,
        auth_claims: dict[str, str] | None = None,
    ) -> dict:
        """Record turn telemetry via backend /stats/telemetry."""
        url = f"{self.base_url}/stats/telemetry"
        telemetry_payload = self._inject_auth_claims(payload, auth_claims)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                url,
                json=telemetry_payload,
                headers=self._request_headers(auth_headers=auth_headers),
            )
            if resp.status_code == 404:
                return {}
            resp.raise_for_status()
            return resp.json()

    # --- Chat & AI Methods ---

    async def chat(
        self,
        message: str,
        provider: str = "google/gemini-2.5-flash",
        enable_ledger: bool = True,
        session_id: str = "default-session",
        history: list | None = None,
        entity: str | None = None,
        auth_headers: dict[str, str] | None = None,
        auth_claims: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> ChatResponse:
        """Send a chat message to the backend /chat endpoint."""
        url = f"{self.base_url}/chat"
        payload = {
            "message": message,
            "provider": provider,
            "enable_ledger": enable_ledger,
            "session_id": session_id,
            "history": history or [],
        }
        if entity:
            payload["entity"] = entity
        payload = self._inject_auth_claims(payload, auth_claims)
        # Chat completion may involve LLM generation; keep a sensible floor.
        chat_timeout = timeout if timeout is not None else max(self.timeout, 60.0)
        async with httpx.AsyncClient(timeout=chat_timeout) as client:
            resp = await client.post(
                url,
                json=payload,
                headers=self._request_headers(auth_headers=auth_headers),
            )
            resp.raise_for_status()
            # Returns full JSON including 'knowledge_tree' and 'web4_key'
            return ChatResponse.from_json(resp.json())

    async def assess_chat(
        self,
        *,
        user_message: str,
        assistant_reply: str,
        entity: str,
        auth_headers: dict[str, str] | None = None,
        auth_claims: dict[str, str] | None = None,
    ) -> dict:
        """Run guardian assessment via backend /api/chat/assess."""
        url = f"{self.base_url}/api/chat/assess"
        payload = {
            "user_message": user_message,
            "assistant_reply": assistant_reply,
            "entity": entity,
        }
        payload = self._inject_auth_claims(payload, auth_claims)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                url,
                json=payload,
                headers=self._request_headers(auth_headers=auth_headers),
            )
            resp.raise_for_status()
            return resp.json()

    async def apply_grounding_guard(
        self,
        *,
        user_message: str,
        assistant_reply: str,
        memories: dict | None = None,
        metadata: dict | None = None,
        auth_headers: dict[str, str] | None = None,
        auth_claims: dict[str, str] | None = None,
    ) -> dict:
        """Apply backend grounding guard via /api/chat/grounding-guard."""
        url = f"{self.base_url}/api/chat/grounding-guard"
        payload = {
            "user_message": user_message,
            "assistant_reply": assistant_reply,
            "memories": memories or {},
            "metadata": metadata or {},
        }
        payload = self._inject_auth_claims(payload, auth_claims)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                url,
                json=payload,
                headers=self._request_headers(auth_headers=auth_headers),
            )
            resp.raise_for_status()
            return resp.json()

    async def commit_answer(
        self,
        *,
        entity: str,
        message: str,
        reply: str,
        precomputed_appraisal: dict | None = None,
        metadata: dict | None = None,
        auth_headers: dict[str, str] | None = None,
        auth_claims: dict[str, str] | None = None,
    ) -> dict:
        """Persist an answer via backend /api/chat/commit-answer."""
        url = f"{self.base_url}/api/chat/commit-answer"
        payload: dict[str, Any] = {
            "entity": entity,
            "user_message": message,
            "assistant_reply": reply,
            "metadata": metadata or {},
        }
        ledger_id = (self.ledger_id or "").strip()
        if ledger_id:
            payload["ledger_id"] = ledger_id
        context_id = (self.context_id or "").strip()
        if context_id:
            payload["context_id"] = context_id
        if precomputed_appraisal is not None:
            payload["precomputed_appraisal"] = precomputed_appraisal
            payload["metadata"]["precomputed_appraisal"] = precomputed_appraisal
        payload = self._inject_auth_claims(payload, auth_claims)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                url,
                json=payload,
                headers=self._request_headers(auth_headers=auth_headers),
            )
            resp.raise_for_status()
            return resp.json()

    async def search_any(
        self,
        query: str,
        limit: int = 10,
        namespace_filter: list[str] | None = None,
        namespace_mode: str = "any",
    ) -> dict:
        """Cross-namespace search via /search without entity constraint."""
        url = f"{self.base_url}/search"
        params = {
            "query": query,
            "limit": limit,
            "fuzzy": True,
            "semantic_weight": 0.45,
            "delta": 2,
            "namespace_mode": namespace_mode,
        }
        if namespace_filter:
            params["namespace_filter"] = ",".join(namespace_filter)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()

    async def decode_coordinate(
        self,
        coordinate: str,
        *,
        entity: str | None = None,
        session_id: str | None = None,
        auth_headers: dict[str, str] | None = None,
        auth_claims: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> dict:
        """Resolve a ledger coordinate via backend /chat/web4/decode."""
        url = f"{self.base_url}/chat/web4/decode"
        payload: dict[str, Any] = {"coordinate": coordinate}
        if entity:
            payload["entity"] = entity
        if session_id:
            payload["session_id"] = session_id
        payload = self._inject_auth_claims(payload, auth_claims)
        request_timeout = timeout if timeout is not None else self.timeout
        async with httpx.AsyncClient(timeout=request_timeout) as client:
            resp = await client.post(
                url,
                json=payload,
                headers=self._request_headers(auth_headers=auth_headers),
            )
            if resp.status_code >= 400:
                try:
                    body = resp.json()
                except Exception:
                    body = {"detail": resp.text[:1000]}
                raise BackendDecodeError(
                    status_code=resp.status_code,
                    body=body if isinstance(body, dict) else {"detail": body},
                    message=f"backend decode returned {resp.status_code}",
                )
            return resp.json()

    async def decode_web4(self, namespace: str, identifier: str) -> dict:
        """Resolve a Web4 key via backend /chat/web4/decode."""
        url = f"{self.base_url}/chat/web4/decode"
        payload = {"namespace": namespace, "identifier": identifier}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def introspect_runtime(
        self,
        *,
        entity: str | None = None,
        session_id: str | None = None,
        auth_headers: dict[str, str] | None = None,
    ) -> dict:
        """Fetch runtime introspection via backend /api/chat/introspect."""
        url = f"{self.base_url}/api/chat/introspect"
        params: dict[str, Any] = {}
        if entity:
            params["entity"] = entity
        if session_id:
            params["session_id"] = session_id
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                url,
                params=params,
                headers=self._request_headers(auth_headers=auth_headers),
            )
            if resp.status_code == 404:
                return {}
            resp.raise_for_status()
            return resp.json()

    async def coord_walk(
        self,
        *,
        start_coord: str,
        max_steps: int = 6,
        current_coherence: float = 0.5,
        namespace: str | None = None,
        auth_headers: dict[str, str] | None = None,
        auth_claims: dict[str, str] | None = None,
    ) -> dict:
        """Run a COORD walk via backend /chat/coord/walk."""
        url = f"{self.base_url}/chat/coord/walk"
        payload: dict[str, Any] = {
            "start_coord": start_coord,
            "max_steps": max_steps,
            "current_coherence": current_coherence,
        }
        if namespace:
            payload["namespace"] = namespace
        payload = self._inject_auth_claims(payload, auth_claims)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                url,
                json=payload,
                headers=self._request_headers(auth_headers=auth_headers),
            )
            resp.raise_for_status()
            return resp.json()

    async def write_walk(
        self,
        payload: dict[str, Any],
        *,
        auth_headers: dict[str, str] | None = None,
        auth_claims: dict[str, str] | None = None,
    ) -> dict:
        """Persist a coord-walk event via backend /chat/walk/write."""
        url = f"{self.base_url}/chat/walk/write"
        walk_payload = self._inject_auth_claims(payload, auth_claims)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                url,
                json=walk_payload,
                headers=self._request_headers(auth_headers=auth_headers),
            )
            resp.raise_for_status()
            return resp.json()

    async def ingest_file(
        self,
        *,
        entity: str,
        filename: str,
        content: bytes,
        content_type: str,
        kind: str = "attachment",
        metadata: dict[str, Any] | None = None,
        ledger_id: str | None = None,
        context_id: str | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
    ) -> dict:
        """Upload a file to the backend ingest endpoint."""
        url = f"{self.base_url}/api/ingest/file"
        payload = {
            "entity": entity,
            "kind": kind,
            "metadata": json.dumps(metadata or {}),
        }
        if ledger_id:
            payload["ledger_id"] = str(ledger_id).strip()
        if context_id:
            payload["context_id"] = str(context_id).strip()
        if session_id:
            payload["session_id"] = str(session_id).strip()
        if turn_id:
            payload["turn_id"] = str(turn_id).strip()
        files = {"file": (filename, content, content_type)}
        headers = {k: v for k, v in self.headers.items() if k.lower() != "content-type"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, data=payload, files=files, headers=headers)
            resp.raise_for_status()
            return resp.json()

    async def assemble(
        self,
        session_id: str,
        message: str | None = None,
        history: list | None = None,
        provider: str = "openai",
        enable_ledger: bool = True,
        k: int = 3,
        since: str | None = None,
        until: str | None = None,
        entity: str | None = None,
        auth_headers: dict[str, str] | None = None,
        auth_claims: dict[str, str] | None = None,
        query_primes: list[int] | None = None,
        hardening_level: int | None = None,
        include_padic_diagnostics: bool | None = None,
        qp_pure: bool | None = None,
        query_factors: list[dict[str, Any]] | None = None,
        padic_config: dict[str, Any] | None = None,
        mmf_domain: str | None = None,
    ) -> dict:
        """Call the backend /assemble endpoint to build context."""
        url = f"{self.base_url}/assemble"
        entity_value = entity if entity else f"chat-{session_id}"
        payload = {
            "entity": entity_value,
            "message": message,
            "history": history or [],
            "provider": provider,
            "enable_ledger": enable_ledger,
            "k": k,
            "since": since,
            "until": until,
        }
        if query_primes is not None:
            payload["query_primes"] = query_primes
        if hardening_level is not None:
            payload["hardening_level"] = hardening_level
        if include_padic_diagnostics is not None:
            payload["include_padic_diagnostics"] = include_padic_diagnostics
        if qp_pure is not None:
            payload["qp_pure"] = qp_pure
        if query_factors is not None:
            payload["query_factors"] = query_factors
        if padic_config is not None:
            payload["padic_config"] = padic_config
        if mmf_domain is not None:
            payload["mmf_domain"] = mmf_domain
        payload = self._inject_auth_claims(payload, auth_claims)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                url,
                json=payload,
                headers=self._request_headers(auth_headers=auth_headers),
            )
            resp.raise_for_status()
            return resp.json()

    # --- Utility Methods ---

    async def billing_openrouter(self) -> dict:
        url = f"{self.base_url}/billing/openrouter"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self.headers)
            return resp.json() if resp.status_code == 200 else {}

    async def list_ledgers(self) -> list[str]:
        url = f"{self.base_url}/admin/ledgers"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self.headers)
            data = resp.json()
            return data.get("ledgers", []) if isinstance(data, dict) else data

    async def create_or_switch_ledger(self, ledger_id: str) -> dict:
        url = f"{self.base_url}/admin/ledgers"
        payload = {"ledger_id": ledger_id, "name": ledger_id, "namespace": ledger_id}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=self.headers)
            return resp.json()

    async def get_ledger_purpose(self, ledger_id: str) -> dict:
        url = f"{self.base_url}/admin/ledgers/{ledger_id}/purpose"
        headers = dict(self.headers)
        admin_token = (os.getenv("ADMIN_TOKEN") or os.getenv("TRUST_ANCHOR_ADMIN_TOKEN") or "").strip()
        if admin_token:
            headers["x-admin-token"] = admin_token
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.json()

    # --- Enrichment ---

    async def enrich(
        self,
        *,
        entity: str,
        role: str,
        content: str,
        kind: str = "message",
        metadata: dict | None = None,
        prime: int | None = None,
    ) -> dict:
        """Persist a single enrichment event to the backend ledger.

        Maps to POST /enrich as described in the public docs. We map the fields
        we have into the documented shape:
        - entity: ledger namespace
        - prime: optional version marker (kept None by default)
        - body: message payload with role/content/kind/metadata for traceability
        - s2: metadata bucket so backend can store stats/aux values
        """

        url = f"{self.base_url}/enrich"
        payload = {
            "entity": entity,
            "prime": prime,
            "body": {
                "role": role,
                "content": content,
                "kind": kind,
                "metadata": metadata or {},
            },
            "s2": metadata or {},
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def enrich_guardian(
        self,
        *,
        entity: str,
        user_message: str,
        assistant_reply: str,
    ) -> dict:
        """Trigger guardian appraisal for the latest turn."""
        url = f"{self.base_url}/enrich/guardian"
        payload = {
            "entity": entity,
            "user_message": user_message,
            "assistant_reply": assistant_reply,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

# Singleton instance used by app.py
api = APIClient()
