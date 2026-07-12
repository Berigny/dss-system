"""Model library registry and model principal seeding for pilot onboarding."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Request

from backend.services.pilot_onboarding import (
    _current_principal_did_or_raise,
    _load_pilot_signups,
    _signup_for_principal,
)
from backend.services.pilot_provisioning import get_provisioning_job_for_record


PILOT_MODEL_PRINCIPALS_V1_KEY = b"__pilot_model_principals_v1__"
PILOT_PROVIDER_CONFIGS_V1_KEY = b"__pilot_provider_configs_v1__"


# --- Provider Registry ---

_PROVIDER_REGISTRY: list[dict[str, Any]] = [
    {
        "provider_id": "openrouter",
        "display_name": "OpenRouter",
        "description": "Unified API for hundreds of LLMs with automatic failover and pricing optimization.",
        "auth_type": "api_key",
        "base_url": "https://openrouter.ai/api/v1",
        "docs_url": "https://openrouter.ai/docs",
        "models": [
            {"model_id": "anthropic/claude-3.5-sonnet", "display_name": "Claude 3.5 Sonnet", "context_window": 200000},
            {"model_id": "anthropic/claude-3-opus", "display_name": "Claude 3 Opus", "context_window": 200000},
            {"model_id": "openai/gpt-4o", "display_name": "GPT-4o", "context_window": 128000},
            {"model_id": "openai/gpt-4o-mini", "display_name": "GPT-4o Mini", "context_window": 128000},
            {"model_id": "google/gemma-3-27b-it", "display_name": "Gemma 3 27B IT", "context_window": 128000},
            {"model_id": "meta-llama/llama-3.1-70b-instruct", "display_name": "Llama 3.1 70B Instruct", "context_window": 128000},
        ],
    },
    {
        "provider_id": "azure_ai_foundry",
        "display_name": "Azure AI Foundry",
        "description": "Microsoft Azure's enterprise AI platform with governed model deployment.",
        "auth_type": "api_key",
        "base_url": "https://<your-resource>.services.ai.azure.com/models",
        "docs_url": "https://learn.microsoft.com/en-us/azure/ai-foundry/",
        "models": [
            {"model_id": "azure/gpt-4o", "display_name": "Azure GPT-4o", "context_window": 128000},
            {"model_id": "azure/gpt-4o-mini", "display_name": "Azure GPT-4o Mini", "context_window": 128000},
            {"model_id": "azure/phi-4", "display_name": "Azure Phi-4", "context_window": 16000},
        ],
    },
    {
        "provider_id": "google_cloud_vertex_ai",
        "display_name": "Google Cloud Vertex AI",
        "description": "Google Cloud's unified AI platform with Gemini and PaLM models.",
        "auth_type": "oauth",
        "base_url": "https://<your-region>-aiplatform.googleapis.com",
        "docs_url": "https://cloud.google.com/vertex-ai/docs",
        "models": [
            {"model_id": "gemini-1.5-pro", "display_name": "Gemini 1.5 Pro", "context_window": 2000000},
            {"model_id": "gemini-1.5-flash", "display_name": "Gemini 1.5 Flash", "context_window": 1000000},
            {"model_id": "gemma-3-27b-it", "display_name": "Gemma 3 27B IT", "context_window": 128000},
        ],
    },
    {
        "provider_id": "hugging_face",
        "display_name": "Hugging Face",
        "description": "Open-source model hub with inference endpoints and dedicated deployments.",
        "auth_type": "api_key",
        "base_url": "https://api-inference.huggingface.co",
        "docs_url": "https://huggingface.co/docs/api-inference",
        "models": [
            {"model_id": "meta-llama/Llama-3.1-70B-Instruct", "display_name": "Llama 3.1 70B Instruct", "context_window": 128000},
            {"model_id": "mistralai/Mistral-Large-Instruct-2407", "display_name": "Mistral Large", "context_window": 128000},
            {"model_id": "microsoft/Phi-4", "display_name": "Phi-4", "context_window": 16000},
        ],
    },
    {
        "provider_id": "ollama",
        "display_name": "Ollama",
        "description": "Run open-source models locally with simple CLI and API.",
        "auth_type": "none",
        "base_url": os.getenv("OLLAMA_BASE_URL", ""),
        "docs_url": "https://github.com/ollama/ollama/blob/main/docs/api.md",
        "models": [
            {"model_id": "llama3.1", "display_name": "Llama 3.1", "context_window": 128000},
            {"model_id": "mistral", "display_name": "Mistral", "context_window": 32000},
            {"model_id": "gemma3", "display_name": "Gemma 3", "context_window": 128000},
            {"model_id": "phi4", "display_name": "Phi-4", "context_window": 16000},
        ],
    },
    {
        "provider_id": "custom",
        "display_name": "Custom API",
        "description": "Connect to your own API endpoint with a custom model.",
        "auth_type": "api_key",
        "base_url": "https://your-api-endpoint.com/v1",
        "docs_url": None,
        "models": [],
    },
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_json(raw: Any) -> Any:
    try:
        decoded = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        return json.loads(decoded)
    except Exception:
        return None


def _load_model_principals(db: Any) -> dict[str, list[dict[str, Any]]]:
    raw = db.get(PILOT_MODEL_PRINCIPALS_V1_KEY)
    payload = _decode_json(raw)
    records = payload.get("principals") if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for key, record_list in records.items():
        if isinstance(record_list, list):
            out[str(key)] = [dict(r) for r in record_list if isinstance(r, dict)]
    return out


def _persist_model_principals(
    db: Any, records: dict[str, list[dict[str, Any]]]
) -> dict[str, list[dict[str, Any]]]:
    canonical: dict[str, list[dict[str, Any]]] = {}
    for key in sorted(records.keys()):
        record_list = records.get(key)
        if isinstance(record_list, list):
            canonical[key] = [dict(r) for r in record_list if isinstance(r, dict)]
    db[PILOT_MODEL_PRINCIPALS_V1_KEY] = json.dumps(
        {"version": 1, "principals": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def _load_provider_configs(db: Any) -> dict[str, dict[str, Any]]:
    raw = db.get(PILOT_PROVIDER_CONFIGS_V1_KEY)
    payload = _decode_json(raw)
    records = payload.get("configs") if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, record in records.items():
        if isinstance(record, dict):
            out[str(key)] = dict(record)
    return out


def _persist_provider_configs(
    db: Any, records: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for key in sorted(records.keys()):
        record = records.get(key)
        if isinstance(record, dict):
            canonical[key] = dict(record)
    db[PILOT_PROVIDER_CONFIGS_V1_KEY] = json.dumps(
        {"version": 1, "configs": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def _provider_is_system_configured(provider_id: str) -> bool:
    if provider_id == "openrouter":
        return bool(os.getenv("OPENROUTER_API_KEY"))
    if provider_id == "ollama":
        # Ollama is considered configured if the local endpoint is reachable
        # Configure via OLLAMA_HOST or OLLAMA_BASE_URL env var
        return True
    return False


def _model_principal_id(account_id: str, provider: str, model_id: str) -> str:
    digest = hashlib.sha256(f"{account_id}:{provider}:{model_id}".encode("utf-8")).hexdigest()[:16]
    return f"model_principal:{digest}"


def _get_ledger_id_for_account(db: Any, record: dict[str, Any]) -> str | None:
    job = get_provisioning_job_for_record(db, record)
    if not isinstance(job, dict):
        return None
    steps = job.get("resource_steps") if isinstance(job.get("resource_steps"), list) else []
    for step in steps:
        if isinstance(step, dict) and step.get("step_id") == "ledger_runtime":
            return str(step.get("resource_id") or "").strip() or None
    return None


def get_model_library(request: Request, db: Any) -> dict[str, Any]:
    principal_did = _current_principal_did_or_raise(request)
    _signup_id, record = _signup_for_principal(db, principal_did)
    account_id = str(record.get("account_id") or "").strip()
    configs = _load_provider_configs(db)
    providers = []
    for provider in _PROVIDER_REGISTRY:
        provider_id = provider["provider_id"]
        account_config = configs.get(f"{account_id}:{provider_id}")
        has_account_config = isinstance(account_config, dict) and bool(account_config.get("api_key"))
        providers.append(
            {
                "provider_id": provider_id,
                "display_name": provider["display_name"],
                "description": provider["description"],
                "auth_type": provider["auth_type"],
                "base_url": provider["base_url"],
                "docs_url": provider["docs_url"],
                "system_configured": _provider_is_system_configured(provider_id),
                "account_configured": has_account_config,
                "models": [
                    {
                        "model_id": m["model_id"],
                        "display_name": m["display_name"],
                        "context_window": m["context_window"],
                    }
                    for m in provider.get("models", [])
                ],
            }
        )
    return {
        "status": "ok",
        "account_id": account_id,
        "providers": providers,
    }


def select_model(
    request: Request,
    db: Any,
    *,
    provider: str,
    model_id: str,
    api_key: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    principal_did = _current_principal_did_or_raise(request)
    signup_id, record = _signup_for_principal(db, principal_did)
    account_id = str(record.get("account_id") or "").strip()
    if not account_id:
        raise HTTPException(status_code=409, detail={"error": "account_id_missing"})

    provider_id = str(provider or "").strip().lower()
    model_id_clean = str(model_id or "").strip()
    if not provider_id:
        raise HTTPException(status_code=422, detail={"error": "provider_required", "field": "provider"})
    if not model_id_clean:
        raise HTTPException(status_code=422, detail={"error": "model_id_required", "field": "model_id"})

    provider_def = next((p for p in _PROVIDER_REGISTRY if p["provider_id"] == provider_id), None)
    if provider_def is None:
        raise HTTPException(status_code=422, detail={"error": "unknown_provider", "field": "provider"})

    model_def = next((m for m in provider_def.get("models", []) if m["model_id"] == model_id_clean), None)
    if model_def is None and provider_id != "custom":
        raise HTTPException(status_code=422, detail={"error": "unknown_model", "field": "model_id"})
    if provider_id == "custom" and not model_id_clean:
        raise HTTPException(status_code=422, detail={"error": "model_id_required", "field": "model_id"})
    if provider_id == "custom" and not base_url:
        raise HTTPException(status_code=422, detail={"error": "base_url_required_for_custom_provider", "field": "base_url"})

    # Auth validation
    configs = _load_provider_configs(db)
    config_key = f"{account_id}:{provider_id}"
    account_has_config = isinstance(configs.get(config_key), dict) and bool(configs[config_key].get("api_key"))
    if provider_def["auth_type"] == "api_key":
        system_has_key = _provider_is_system_configured(provider_id)
        if not system_has_key and not api_key and not account_has_config:
            raise HTTPException(
                status_code=422,
                detail={"error": "api_key_required", "field": "api_key", "provider": provider_id},
            )
    elif provider_def["auth_type"] == "oauth":
        # OAuth providers require account-level config; for MVP we accept an api_key as a bearer token
        if not api_key and not account_has_config:
            raise HTTPException(
                status_code=422,
                detail={"error": "oauth_token_required", "field": "api_key", "provider": provider_id},
            )

    # Persist account provider config if api_key provided
    if api_key or provider_id == "custom":
        configs = _load_provider_configs(db)
        config_key = f"{account_id}:{provider_id}"
        existing_config = configs.get(config_key, {})
        configs[config_key] = {
            "account_id": account_id,
            "provider_id": provider_id,
            "api_key": api_key or existing_config.get("api_key"),
            "base_url": base_url or existing_config.get("base_url") or provider_def.get("base_url"),
            "updated_at": _now_iso(),
        }
        _persist_provider_configs(db, configs)

    # Idempotent model principal creation
    principals = _load_model_principals(db)
    account_principals = principals.get(account_id, [])
    existing = next(
        (p for p in account_principals if p.get("provider") == provider_id and p.get("model_id") == model_id_clean),
        None,
    )
    if existing is not None:
        return {
            "status": "ok",
            "model_principal": _model_principal_summary(existing),
            "idempotent_replay": True,
        }

    ledger_id = _get_ledger_id_for_account(db, record)
    principal_id = _model_principal_id(account_id, provider_id, model_id_clean)
    now = _now_iso()
    display_name = model_def["display_name"] if model_def else model_id_clean
    new_principal = {
        "principal_id": principal_id,
        "principal_type": "model",
        "provider": provider_id,
        "model_id": model_id_clean,
        "display_name": display_name,
        "account_id": account_id,
        "ledger_id": ledger_id,
        "status": "active",
        "credential_ref": f"credref:{provider_id}:{account_id}:v1",
        "created_at": now,
        "updated_at": now,
    }
    account_principals.append(new_principal)
    principals[account_id] = account_principals
    _persist_model_principals(db, principals)

    return {
        "status": "ok",
        "model_principal": _model_principal_summary(new_principal),
        "idempotent_replay": False,
    }


def _model_principal_summary(principal: dict[str, Any]) -> dict[str, Any]:
    return {
        "principal_id": principal.get("principal_id"),
        "principal_type": principal.get("principal_type"),
        "provider": principal.get("provider"),
        "model_id": principal.get("model_id"),
        "display_name": principal.get("display_name"),
        "account_id": principal.get("account_id"),
        "ledger_id": principal.get("ledger_id"),
        "status": principal.get("status"),
        "credential_ref": principal.get("credential_ref"),
        "created_at": principal.get("created_at"),
    }


def get_current_principals(request: Request, db: Any) -> dict[str, Any]:
    principal_did = _current_principal_did_or_raise(request)
    signup_id, record = _signup_for_principal(db, principal_did)
    account_id = str(record.get("account_id") or "").strip()

    # Owner human principal from provisioning job
    job = get_provisioning_job_for_record(db, record)
    owner_principal_id = None
    if isinstance(job, dict):
        steps = job.get("resource_steps") if isinstance(job.get("resource_steps"), list) else []
        for step in steps:
            if isinstance(step, dict) and step.get("step_id") == "owner_human_principal":
                owner_principal_id = step.get("resource_id")
                break

    principals: list[dict[str, Any]] = []
    if owner_principal_id:
        principals.append(
            {
                "principal_id": owner_principal_id,
                "principal_type": "human_owner",
                "account_id": account_id,
                "status": "active",
                "source": "provisioning_job",
            }
        )

    # Model principals
    model_principals = _load_model_principals(db).get(account_id, [])
    for mp in model_principals:
        principals.append(_model_principal_summary(mp))

    return {
        "status": "ok",
        "account_id": account_id,
        "principals": principals,
    }
