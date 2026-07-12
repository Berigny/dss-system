"""Pydantic schemas bridging API requests to Field-X models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.fieldx_kernel.models import ContinuousState, LedgerEntry, LedgerKey
from shared_types.coord_schema import parse_bigint


class LedgerKeySchema(BaseModel):
    """API representation of a ledger key."""

    model_config = ConfigDict(extra="ignore")

    namespace: str
    identifier: str
    precision: int | None = Field(default=None, ge=1)

    def to_model(self) -> LedgerKey:
        """Convert the schema to the internal dataclass."""

        return LedgerKey(namespace=self.namespace, identifier=self.identifier)


class ContinuousStateSchema(BaseModel):
    """API payload describing a continuous state."""

    model_config = ConfigDict(extra="ignore")

    coordinates: Dict[str, float] = Field(default_factory=dict)
    phase: str | None = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_model(self) -> ContinuousState:
        """Convert the schema into a ``ContinuousState`` instance."""

        state = ContinuousState(
            coordinates=dict(self.coordinates),
            phase=self.phase,
            metadata=dict(self.metadata),
        )
        return state


class LedgerEntrySchema(BaseModel):
    """Schema capturing a full ledger entry with metadata."""

    model_config = ConfigDict(extra="ignore")

    key: LedgerKeySchema
    state: ContinuousStateSchema
    created_at: datetime | None = None
    notes: str | None = None
    pinned: bool = False

    def to_model(self) -> LedgerEntry:
        """Convert the schema into a ``LedgerEntry`` record."""

        created = self.created_at or datetime.utcnow()
        return LedgerEntry(
            key=self.key.to_model(),
            state=self.state.to_model(),
            created_at=created,
            notes=self.notes,
            pinned=self.pinned,
        )

    @classmethod
    def from_model(cls, entry: LedgerEntry) -> "LedgerEntrySchema":
        """Build the schema from a ledger dataclass."""

        return cls(
            key=LedgerKeySchema(**entry.key.__dict__),
            state=ContinuousStateSchema(
                coordinates=entry.state.coordinates,
                phase=entry.state.phase,
                metadata=entry.state.metadata,
            ),
            created_at=entry.created_at,
            notes=entry.notes,
            pinned=entry.pinned,
        )


class ActionRequestSchema(BaseModel):
    """Schema describing an action to evaluate for safety and coherence."""

    model_config = ConfigDict(extra="ignore")

    actor: str = Field(..., description="Agent or system requesting the action")
    action: str = Field(..., description="Name of the requested action")
    key: LedgerKeySchema | None = Field(
        default=None, description="Optional ledger key associated with the action"
    )
    parameters: Mapping[str, float] = Field(
        default_factory=dict, description="Action parameters for analysis"
    )


class PolicyDecisionSchema(BaseModel):
    """Ethics layer decision combining lawfulness and grace."""

    model_config = ConfigDict(extra="ignore")

    action: str
    key: LedgerKeySchema | None = None
    lawfulness: float
    grace: float
    permitted: bool


class CoherenceResponseSchema(BaseModel):
    """Response describing coherence analysis for an action request."""

    model_config = ConfigDict(extra="ignore")

    action: str
    coherence_score: float
    lattice_point: Sequence[int]
    steps: Sequence[int]


class ChatRequest(BaseModel):
    session_id: str = Field(..., description="Unique session identifier.")
    turn_id: str | None = Field(None, description="Optional turn identifier for telemetry.")
    context_id: str | None = Field(None, description="Optional context identifier.")
    principal_did: str | None = Field(None, description="Optional DID-backed principal identity.")
    principal_key_id: str | None = Field(None, description="Optional principal verification key identifier.")
    session_jti: str | None = Field(None, description="Optional auth session/token nonce identifier.")
    ledger_id: str | None = Field(
        None,
        description="Explicit ledger identifier for authorization/scoping.",
    )
    entity: str | None = Field(
        None, description="Optional entity/namespace override for ledger operations."
    )
    message: str = Field(..., description="User's latest message.")
    history: List[Mapping[str, Any]] = Field(default_factory=list)
    provider: str = Field("openai", description="LLM provider to use.")
    enable_ledger: bool = Field(True)
    persist_conversation: bool = Field(
        True,
        description="Whether to persist the raw conversation transcript in the ledger.",
    )
    metadata: Dict[str, Any] | None = Field(
        default_factory=dict,
        description="Optional request metadata including model auth and standing envelope.",
    )
    standing_envelope: Dict[str, Any] | None = Field(
        default_factory=dict,
        description="Optional standing envelope forwarded by middleware.",
    )
    eligible_for_search: bool | None = Field(
        None, description="Whether the client is eligible for search enrichment."
    )
    search_used: bool | None = Field(
        None, description="Whether search was used for this turn."
    )
    query_primes: List[int] | None = Field(
        None, description="Optional pre-computed token primes for p-adic retrieval."
    )
    hardening_level: int | None = Field(
        None, description="Optional p-adic hardening level for this request."
    )
    include_padic_diagnostics: bool | None = Field(
        True, description="Whether to include p-adic diagnostics in the response."
    )
    qp_pure: bool | None = Field(
        None,
        description="Optional per-request override for the QP_PURE_ENABLED retrieval flag.",
    )


class ChatResponse(BaseModel):
    text: str
    latency_ms: int
    memories_used: int
    cost_usd: float
    unverified: bool | None = Field(
        None, description="True when persistence/ledger write failed for this turn."
    )
    grace_note: str | None = Field(
        None, description="Optional notice when applying additional care constraints."
    )
    appraisal: Dict[str, Any] | None = Field(
        None, description="Appraisal scores for the turn if available."
    )
    knowledge_tree: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Reference keys/coordinates associated with the response.",
    )
    session_cost_usd: float | None = Field(
        None, description="Total cost accumulated for the current session."
    )
    session_avg_response_ms: float | None = Field(
        None, description="Average response time for the current session in milliseconds."
    )
    coordinate: str | None = Field(
        None, description="Prime coordinate for the assistant reply in the ledger."
    )
    web4_key: str | None = Field(
        None, description="The deterministic Web4 coordinate for this chat turn."
    )
    fallback_coordinate: bool | None = Field(
        None,
        description="True if the coordinate was replaced with a deterministic fallback.",
    )
    audit_mode: Dict[str, Any] | None = Field(
        None,
        description="Audit mode payload when governance gate blocks persistence.",
    )
    resolve_summary: Dict[str, Any] | None = Field(
        None,
        description="Per-turn coordinate resolution capability and outcome summary.",
    )
    candidate_trace: List[Dict[str, Any]] | None = Field(
        None,
        description="Top ranked canonical COORD candidates for this turn.",
    )
    autonomy_decision: Dict[str, Any] | None = Field(
        None,
        description="Per-turn autonomy policy decision and chosen action.",
    )
    consistency_check: Dict[str, Any] | None = Field(
        None,
        description="Post-answer consistency check and retry status when resolved context exists.",
    )
    eval_contract: Dict[str, Any] | None = Field(
        None,
        description="Deterministic Kernel Ladder/eval contract payload for this response.",
    )
    posture_policy: Dict[str, Any] | None = Field(
        None,
        description="Deterministic posture-aware policy decision envelope for this response.",
    )


class ChatAssessmentRequest(BaseModel):
    """Schema describing a chat turn to assess with Guardian."""

    model_config = ConfigDict(extra="ignore")

    entity: str = Field(..., description="Entity identifier for the conversation.")
    user_message: str = Field(..., description="User message to assess.")
    assistant_reply: str = Field(..., description="Assistant reply to assess.")


class ChatAssessmentResponse(BaseModel):
    """Response describing the Guardian appraisal for a chat turn."""

    model_config = ConfigDict(extra="ignore")

    status: str = Field(..., description="Status describing Guardian availability.")
    appraisal: Dict[str, float] | None = Field(
        None, description="Guardian appraisal scores for the turn."
    )


class ChatGroundingGuardRequest(BaseModel):
    """Schema describing a response grounding guard check."""

    model_config = ConfigDict(extra="ignore")

    user_message: str = Field(..., description="Original user message.")
    assistant_reply: str = Field(..., description="Candidate assistant reply.")
    memories: Dict[str, Any] | None = Field(
        None,
        description="Optional resolved context payload used for grounding checks.",
    )
    metadata: Dict[str, Any] | None = Field(
        None,
        description="Optional metadata payload containing eq9/introspect signals.",
    )


class ChatGroundingGuardResponse(BaseModel):
    """Response describing grounding guard transformation results."""

    model_config = ConfigDict(extra="ignore")

    assistant_reply: str = Field(..., description="Guarded or original assistant reply.")
    applied: bool = Field(..., description="Whether grounding override was applied.")
    reason: str | None = Field(None, description="Reason code if override applied.")


class ChatCommitRequest(BaseModel):
    """Schema describing a chat turn to persist with optional appraisal."""

    model_config = ConfigDict(extra="ignore")

    entity: str
    context_id: str | None = Field(None, description="Optional context identifier.")
    principal_did: str | None = Field(None, description="Optional DID-backed principal identity.")
    principal_key_id: str | None = Field(None, description="Optional principal verification key identifier.")
    session_jti: str | None = Field(None, description="Optional auth session/token nonce identifier.")
    ledger_id: str | None = Field(
        None,
        description="Explicit ledger identifier for authorization/scoping.",
    )
    user_message: str
    assistant_reply: str
    persist_conversation: bool = Field(
        True,
        description="Whether to persist the raw conversation transcript in the ledger.",
    )
    metadata: Dict[str, Any] | None = None
    precomputed_appraisal: Dict[str, Any] | None = None


class ResolveSkimSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    one_line: str
    relevance: float
    reasons: List[str]
    recommended: List[str]
    budgets: Dict[str, int]


class ResolveRefItemSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    coord: str
    type: str


class ResolveRefsSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    inputs: List[ResolveRefItemSchema] = Field(default_factory=list)
    evidence: List[ResolveRefItemSchema] = Field(default_factory=list)
    context: List[ResolveRefItemSchema] = Field(default_factory=list)
    overlays: List[ResolveRefItemSchema] = Field(default_factory=list)
    governance: List[ResolveRefItemSchema] = Field(default_factory=list)
    walk_traces: List[ResolveRefItemSchema] = Field(default_factory=list)
    web4: List[ResolveRefItemSchema] = Field(default_factory=list)


class ResolvePayloadSegmentSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    kind: str
    blob_ref: str
    tokens_est: int
    skippable: bool | None = None


class ResolvePayloadSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    segments: List[ResolvePayloadSegmentSchema] = Field(default_factory=list)
    blobs: Dict[str, str] = Field(default_factory=dict)
    parts: List[Dict[str, Any]] = Field(default_factory=list)


class ResolveInterpretationSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    topics: List[Dict[str, Any]] = Field(default_factory=list)
    claims: List[Dict[str, Any]] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)


class ResolveGovernanceSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    appraisal: Dict[str, Any] = Field(default_factory=dict)


class ResolveMetaSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    namespace_used: str | None = None
    identifier: str | None = None
    created_at: str | None = None
    pinned: bool | None = None
    feedback_rollup: Dict[str, Any] | None = None
    prime_multiplicative_value: int | str | None = None
    body_prime: int | str | None = None
    token_primes: List[int | str] | None = None
    p_adic_coordinate: Dict[str, Any] | None = None

    @field_validator("prime_multiplicative_value", "body_prime", mode="before")
    @classmethod
    def _parse_bigint_scalar(cls, value: Any) -> int | None:
        if value is None:
            return None
        return parse_bigint(value)

    @field_validator("token_primes", mode="before")
    @classmethod
    def _parse_bigint_list(cls, value: Any) -> List[int] | None:
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValueError("token_primes must be a list")
        return [parse_bigint(item) for item in value]
    taxonomy_topology_ref: str | None = None
    taxonomy_mode: str | None = None
    configurational_foresight: Dict[str, Any] | None = None


class ResolveResponseSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")

    coord: str
    type: str
    skim: ResolveSkimSchema
    walk: Dict[str, Any] | None = None
    refs: ResolveRefsSchema
    payload: ResolvePayloadSchema
    interpretation: ResolveInterpretationSchema
    governance: ResolveGovernanceSchema
    meta: ResolveMetaSchema


class ChatCommitResponse(BaseModel):
    """Response describing persisted turn metadata."""

    model_config = ConfigDict(extra="ignore")

    status: str = Field(..., description="Status describing persistence outcome.")
    coordinate: str | None = Field(
        None, description="Prime coordinate for the assistant reply in the ledger."
    )
    metadata: Dict[str, Any] | None = Field(
        None, description="Metadata persisted alongside the assistant reply."
    )
    eval_contract: Dict[str, Any] | None = Field(
        None,
        description="Deterministic Kernel Ladder/eval contract payload for the committed turn.",
    )
    posture_policy: Dict[str, Any] | None = Field(
        None,
        description="Deterministic posture-aware policy decision envelope for the committed turn.",
    )
