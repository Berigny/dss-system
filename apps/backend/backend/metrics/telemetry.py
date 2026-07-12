"""Telemetry models for tracking turn-level metrics."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class RetrievalPath(str, Enum):
    """Where retrieval for a turn occurred."""

    NONE = "none"
    SEARCH = "search"
    MEMORY = "memory"
    HYBRID = "hybrid"


class TelemetryIds(BaseModel):
    session_id: str
    namespace: str
    entity: Optional[str] = None
    turn_id: str
    timestamp: datetime


class TelemetrySearchFlags(BaseModel):
    requested: Optional[bool] = None
    used: Optional[bool] = None
    succeeded: Optional[bool] = None


class TelemetryReferences(BaseModel):
    emitted_refs: int = 0
    resolve_attempts: int = 0
    resolve_successes: int = 0


class TurnTelemetry(BaseModel):
    """Telemetry payload for a single turn."""

    ids: TelemetryIds
    request_id: Optional[str] = None
    tenant_id: Optional[str] = None
    surface: Optional[str] = None
    mode: Optional[str] = None
    build_sha: Optional[str] = None
    principal_id: Optional[str] = None
    principal_hash: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    policy: Optional[str] = None
    retrieval_path: RetrievalPath = RetrievalPath.NONE
    search: TelemetrySearchFlags = Field(default_factory=TelemetrySearchFlags)
    references: TelemetryReferences = Field(default_factory=TelemetryReferences)
    gen_cost: Optional[float] = None
    gen_input_tokens: Optional[int] = None
    gen_output_tokens: Optional[int] = None
    cost: Optional[float] = None
    memory_cost: Optional[float] = None
    memory_tokens: Optional[int] = None
    ingest_words: Optional[int] = None
    latency_ms: Optional[float] = None
    e6_mode: Optional[int] = None
    e6_route: Optional[int] = None
    e6_quality_tier: Optional[str] = None
    e6_bridge_allowed: Optional[bool] = None
    e6_promotion_allowed: Optional[bool] = None
    e6_v_int_mean_3: Optional[float] = None
    e6_v_int_std_3: Optional[float] = None
    quarantine_write: Optional[bool] = None
    quarantine_reason: Optional[str] = None
    eq9_eval_source: Optional[str] = None
    meta_patch_status: Optional[str] = None
    meta_patch_reason: Optional[str] = None
    authz_denied: Optional[bool] = None
    authz_reason: Optional[str] = None
    authz_principal_source: Optional[str] = None
    authz_principal_mode: Optional[str] = None
    auth_error_class: Optional[str] = None
    auth_token_validation_failed: Optional[bool] = None
