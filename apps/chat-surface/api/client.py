"""HTTP client for the ourIP.AI backend API."""

from __future__ import annotations

import asyncio
import hashlib
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

import json

import httpx

# Ensure these settings exist in your config.settings
from config.settings import (
    API_BASE,
    API_KEY,
    DEFAULT_LEDGER,
    FRONTEND_CONTEXT_ID,
    FRONTEND_PRINCIPAL_ID,
    FRONTEND_PRINCIPAL_TYPE,
    FRONTEND_TENANT_ID,
    HTTP_TIMEOUT,
)


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
            metadata=payload["metadata"] if "metadata" in payload else {},
            appraisal=payload.get("appraisal"),
            tokens=payload.get("tokens"),
            model=payload.get("model"),
            cost_usd=payload.get("cost_usd"),
            error=payload.get("error"),
            unverified=bool(payload.get("unverified", False)),
        )




_REQUEST_SESSION_TOKEN: ContextVar[str | None] = ContextVar("request_session_token", default=None)


def set_request_session_token(token: str | None) -> Token[str | None]:
    return _REQUEST_SESSION_TOKEN.set((token or "").strip() or None)


def reset_request_session_token(token: Token[str | None]) -> None:
    _REQUEST_SESSION_TOKEN.reset(token)


def get_request_session_token() -> str:
    return (_REQUEST_SESSION_TOKEN.get() or "").strip()


class APIClient:
    """Async client wrapper for backend interactions."""

    def __init__(
        self,
        base_url: str = API_BASE,
        api_key: str = API_KEY,
        ledger_id: str = DEFAULT_LEDGER,
        context_id: str = FRONTEND_CONTEXT_ID,
        principal_id: str = FRONTEND_PRINCIPAL_ID,
        principal_type: str = FRONTEND_PRINCIPAL_TYPE,
        tenant_id: str = FRONTEND_TENANT_ID,
        timeout: float = 60.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.ledger_id = ledger_id
        self.context_id = (context_id or "").strip()
        self.principal_id = (principal_id or "").strip()
        self.principal_type = (principal_type or "").strip()
        self.tenant_id = (tenant_id or "").strip()
        # Force 60s minimum timeout to handle LLM generation times
        self.timeout = max(timeout, 60.0)
        # Avoid forcing Content-Type on GET requests; some middleware routes
        # treat that header path differently and can error.
        self._base_headers: dict[str, str] = {}

    @property
    def headers(self) -> dict[str, str]:
        """Return request headers with explicit ledger context."""
        headers = dict(self._base_headers)
        ledger_id = (self.ledger_id or "").strip()
        if ledger_id:
            headers["x-ledger-id"] = ledger_id
            digest = hashlib.sha256(ledger_id.encode("utf-8")).digest()
            headers["x-ledger-id-h64"] = digest[:8].hex()
        if self.context_id:
            headers["x-context-id"] = self.context_id
        if self.principal_id:
            headers["x-principal-id"] = self.principal_id
        if self.principal_type:
            headers["x-principal-type"] = self.principal_type
        if self.tenant_id:
            headers["x-tenant-id"] = self.tenant_id
        token = get_request_session_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def _request_json_with_retries(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        retries: int = 2,
        retry_delay_s: float = 0.35,
    ) -> Any:
        """Retry transient transport failures (e.g., cold-start connect errors)."""
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    if method.upper() == "GET":
                        resp = await client.get(url, headers=self.headers, params=params)
                    else:
                        resp = await client.request(
                            method.upper(),
                            url,
                            headers=self.headers,
                            params=params,
                            json=payload,
                        )
                if resp.status_code in {502, 503, 504} and attempt < retries:
                    await asyncio.sleep(retry_delay_s * (attempt + 1))
                    continue
                resp.raise_for_status()
                return resp.json()
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                last_exc = exc
                if attempt >= retries:
                    raise
                await asyncio.sleep(retry_delay_s * (attempt + 1))
        if last_exc:
            raise last_exc
        return {}

    def set_ledger(self, ledger_id: str) -> None:
        """Update the active ledger for subsequent requests."""
        if ledger_id:
            self.ledger_id = ledger_id

    def set_context(self, context_id: str) -> None:
        """Update the active context id for subsequent requests."""
        self.context_id = (context_id or "").strip()

    # --- Core Ledger Methods ---

    async def get_history(self, entity: str, limit: int = 50) -> list[dict]:
        """Fetch chat history from the ledger."""
        safe_entity = httpx.URL(path=f"/{entity}").path.lstrip("/")
        url = f"{self.base_url}/ledger/history/{safe_entity}"
        params = {"limit": limit}
        payload = await self._request_json_with_retries("GET", url, params=params)
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
        return await self._request_json_with_retries("GET", url, params=params)

    async def get_history_entities(self, limit: int = 200) -> dict:
        """Fetch chat/history entities suitable for UI entity selectors."""
        url = f"{self.base_url}/ledger/history_entities"
        params = {"limit": max(1, min(int(limit), 5000))}
        payload = await self._request_json_with_retries("GET", url, params=params)
        return payload if isinstance(payload, dict) else {}

    async def ledger_metrics(self, entity: str) -> dict:
        """Fetch summary metrics for a specific namespace."""
        url = f"{self.base_url}/ledger/summary/{entity}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self.headers)
            if resp.status_code == 404:
                return {} 
            resp.raise_for_status()
            return resp.json()

    async def session_stats(self, session_id: str) -> dict:
        url = f"{self.base_url}/stats/session"
        params = {"session_id": session_id}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, params=params, headers=self.headers)
            if resp.status_code == 404:
                # Frontend deployments may point API_BASE to middleware, which serves
                # session stats via /api/stats instead of /stats/session.
                fallback = await client.get(f"{self.base_url}/api/stats", headers=self.headers)
                if fallback.status_code == 404:
                    return {}
                fallback.raise_for_status()
                payload = fallback.json()
                return payload if isinstance(payload, dict) else {}
            resp.raise_for_status()
            payload = resp.json()
            return payload if isinstance(payload, dict) else {}

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
                fallback = await client.get(f"{self.base_url}/api/stats/global", headers=self.headers)
                if fallback.status_code == 404:
                    return {}
                fallback.raise_for_status()
                payload = fallback.json()
                return payload if isinstance(payload, dict) else {}
            resp.raise_for_status()
            payload = resp.json()
            return payload if isinstance(payload, dict) else {}

    async def emit_telemetry(self, payload: dict[str, Any]) -> dict:
        """Record turn telemetry via backend /stats/telemetry."""
        url = f"{self.base_url}/stats/telemetry"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=self.headers)
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
        context_coords: list[str] | None = None,
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
        if isinstance(context_coords, list):
            payload["context_coords"] = [
                str(coord).strip()
                for coord in context_coords
                if isinstance(coord, str) and str(coord).strip()
            ]
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=self.headers)
            resp.raise_for_status()
            # Returns full JSON including 'knowledge_tree' and 'web4_key'
            return ChatResponse.from_json(resp.json())

    async def assess_chat(
        self,
        *,
        user_message: str,
        assistant_reply: str,
        entity: str,
    ) -> dict:
        """Run guardian assessment via backend /api/chat/assess."""
        url = f"{self.base_url}/api/chat/assess"
        payload = {
            "user_message": user_message,
            "assistant_reply": assistant_reply,
            "entity": entity,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=self.headers)
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
    ) -> dict:
        """Persist an answer via backend /api/chat/commit-answer."""
        url = f"{self.base_url}/api/chat/commit-answer"
        payload: dict[str, Any] = {
            "entity": entity,
            "user_message": message,
            "assistant_reply": reply,
            "metadata": metadata or {},
        }
        if precomputed_appraisal is not None:
            payload["precomputed_appraisal"] = precomputed_appraisal
            payload["metadata"]["precomputed_appraisal"] = precomputed_appraisal
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=self.headers)
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
    ) -> dict:
        """Resolve a ledger coordinate via backend decode routes.

        Preferred route is `/api/decode_coordinate` (middleware/frontend compatible),
        with fallback to `/chat/web4/decode` for legacy deployments.
        """
        payload: dict[str, Any] = {"coordinate": coordinate}
        if entity:
            payload["entity"] = entity
        if session_id:
            payload["session_id"] = session_id

        candidate_urls = [
            f"{self.base_url}/api/decode_coordinate",
            f"{self.base_url}/chat/web4/decode",
        ]
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            last_exc: Exception | None = None
            for idx, url in enumerate(candidate_urls):
                try:
                    resp = await client.post(url, json=payload, headers=self.headers)
                    if resp.status_code == 404 and idx < len(candidate_urls) - 1:
                        continue
                    resp.raise_for_status()
                    return resp.json()
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code if exc.response is not None else None
                    if status == 404 and idx < len(candidate_urls) - 1:
                        last_exc = exc
                        continue
                    raise
                except Exception as exc:
                    last_exc = exc
            if last_exc is not None:
                raise last_exc
            raise RuntimeError("decode_coordinate failed: no candidate endpoints available")

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
    ) -> dict:
        """Fetch runtime introspection via backend /api/chat/introspect."""
        url = f"{self.base_url}/api/chat/introspect"
        params: dict[str, Any] = {}
        if entity:
            params["entity"] = entity
        if session_id:
            params["session_id"] = session_id
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, params=params, headers=self.headers)
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
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def write_walk(self, payload: dict[str, Any]) -> dict:
        """Persist a coord-walk event via backend /chat/walk/write."""
        url = f"{self.base_url}/chat/walk/write"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=self.headers)
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
    ) -> dict:
        """Upload a file to the backend ingest endpoint."""
        url = f"{self.base_url}/api/ingest/file"
        payload = {
            "entity": entity,
            "kind": kind,
            "metadata": json.dumps(metadata or {}),
        }
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
        context_coords: list[str] | None = None,
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
        if isinstance(context_coords, list):
            payload["context_coords"] = [
                str(coord).strip()
                for coord in context_coords
                if isinstance(coord, str) and str(coord).strip()
            ]
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    # --- Utility Methods ---

    async def billing_openrouter(self) -> dict:
        url = f"{self.base_url}/billing/openrouter"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self.headers)
            return resp.json() if resp.status_code == 200 else {}

    async def list_ledgers(self) -> list[str]:
        urls = [f"{self.base_url}/api/ledgers", f"{self.base_url}/admin/ledgers"]
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for idx, url in enumerate(urls):
                resp = await client.get(url, headers=self.headers)
                if resp.status_code == 404 and idx < len(urls) - 1:
                    continue
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, dict):
                    ledgers = data.get("ledgers")
                    if isinstance(ledgers, list):
                        return ledgers
                if isinstance(data, list):
                    return data
                return []
        return []

    async def create_or_switch_ledger(self, ledger_id: str) -> dict:
        payload = {"ledger_id": ledger_id, "name": ledger_id, "namespace": ledger_id}
        urls = [f"{self.base_url}/api/ledgers", f"{self.base_url}/admin/ledgers"]
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for idx, url in enumerate(urls):
                resp = await client.post(url, json=payload, headers=self.headers)
                if resp.status_code == 404 and idx < len(urls) - 1:
                    continue
                resp.raise_for_status()
                data = resp.json()
                return data if isinstance(data, dict) else {"response": data}
        return {"ledger_id": ledger_id}

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

    # --- Onboarding / Account Methods ---

    async def get_model_library(self) -> dict:
        """Fetch the model library from middleware /account/current/model-library."""
        url = f"{self.base_url}/account/current/model-library"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def select_model(self, provider: str, model_id: str, api_key: str | None = None, base_url: str | None = None) -> dict:
        """Select a model via middleware /account/current/model-library/select."""
        url = f"{self.base_url}/account/current/model-library/select"
        payload = {
            "provider": provider,
            "model_id": model_id,
            "api_key": api_key,
            "base_url": base_url,
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def get_account_principals(self) -> dict:
        """Fetch principals from middleware /account/current/principals."""
        url = f"{self.base_url}/account/current/principals"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def bootstrap_agent_principal(self) -> dict:
        """Bootstrap agent principal via middleware /account/current/principals/agent/bootstrap."""
        url = f"{self.base_url}/account/current/principals/agent/bootstrap"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def get_connections(self) -> dict:
        """Fetch principal connection graph from middleware /account/current/connections."""
        url = f"{self.base_url}/account/current/connections"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def get_onboarding_status(self) -> dict:
        """Fetch onboarding status from middleware /account/current/onboarding."""
        url = f"{self.base_url}/account/current/onboarding"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def get_setup_prompt(self) -> dict:
        """Fetch setup prompt from middleware /account/current/setup-prompt."""
        url = f"{self.base_url}/account/current/setup-prompt"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def dismiss_setup_prompt(self, mode: str, snoozed_until: str | None = None) -> dict:
        """Dismiss setup prompt via middleware /account/current/setup-prompt/dismiss."""
        url = f"{self.base_url}/account/current/setup-prompt/dismiss"
        payload = {"mode": mode}
        if snoozed_until:
            payload["snoozed_until"] = snoozed_until
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def get_wallet_credential_offer(self, session_id: str, wallet_provider: str = "microsoft_authenticator") -> dict:
        """Fetch credential offer from middleware /wallet/credential-offer."""
        url = f"{self.base_url}/wallet/credential-offer"
        params = {"session_id": session_id, "wallet_provider": wallet_provider}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, params=params, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def get_wallet_did_document(self, wallet_id: str) -> dict:
        """Fetch DID document from middleware /wallet/{wallet_id}/did.json."""
        url = f"{self.base_url}/wallet/{wallet_id}/did.json"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def get_wallet_providers(self) -> dict:
        """Fetch supported wallet providers from middleware /wallet/providers."""
        url = f"{self.base_url}/wallet/providers"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def get_admin_provisioning_job(self, job_id: str) -> dict:
        """Fetch admin provisioning job inspection from middleware /admin/provisioning/jobs/{job_id}."""
        url = f"{self.base_url}/admin/provisioning/jobs/{job_id}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def get_admin_provisioning_job_steps(self, job_id: str) -> dict:
        """Fetch admin provisioning job steps from middleware /admin/provisioning/jobs/{job_id}/steps."""
        url = f"{self.base_url}/admin/provisioning/jobs/{job_id}/steps"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def submit_onboarding(self, payload: dict) -> dict:
        """Submit onboarding form via middleware /account/current/onboarding."""
        url = f"{self.base_url}/account/current/onboarding"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def get_provisioning_status(self) -> dict:
        """Fetch provisioning status from middleware /account/current/provisioning."""
        url = f"{self.base_url}/account/current/provisioning"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def run_provisioning(self) -> dict:
        """Trigger provisioning via middleware /account/current/provisioning/run."""
        url = f"{self.base_url}/account/current/provisioning/run"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def start_wallet_link(self, provider: str | None = None) -> dict:
        """Start wallet linking via middleware /account/current/identity/wallet-link/start."""
        url = f"{self.base_url}/account/current/identity/wallet-link/start"
        payload = {"provider": provider} if provider else {}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def complete_wallet_link(self, provider: str | None = None, wallet_did: str | None = None) -> dict:
        """Complete wallet linking via middleware /account/current/identity/wallet-link/complete."""
        url = f"{self.base_url}/account/current/identity/wallet-link/complete"
        payload: dict[str, Any] = {}
        if provider:
            payload["provider"] = provider
        if wallet_did:
            payload["wallet_did"] = wallet_did
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def get_account_summary(self) -> dict:
        """Fetch account summary from middleware /account/current."""
        url = f"{self.base_url}/account/current"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def get_account_subscription(self) -> dict:
        """Fetch subscription summary from middleware /account/current/subscription."""
        url = f"{self.base_url}/account/current/subscription"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def get_setup_checklist(self) -> dict:
        """Fetch setup checklist from middleware /account/current/setup-checklist."""
        url = f"{self.base_url}/account/current/setup-checklist"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def get_surfaces(self) -> dict:
        """Fetch surfaces from middleware /account/current/surfaces."""
        url = f"{self.base_url}/account/current/surfaces"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def get_identity(self) -> dict:
        """Fetch identity status from middleware /account/current/identity."""
        url = f"{self.base_url}/account/current/identity"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

# Singleton instance used by app.py
api = APIClient()
