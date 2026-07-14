"""Administrative endpoints for managing search indexes and ledgers."""

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Set
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.api.http import get_db
from backend.api.logging_utils import log_operation
from backend.search.reindex import reindex_all
from backend.fieldx_kernel.guardian import guardian_consolidate
from backend.services.authority_events import (
    append_authority_event,
    get_authority_event,
    get_authority_state,
    load_authority_events,
    load_authority_state,
    replay_authority_state,
)
from backend.services.evidence_manifests import (
    get_evidence_manifest,
    load_evidence_manifests,
    upsert_evidence_manifest,
)
from backend.services.issuer_authorities import (
    get_issuer_authority,
    load_issuer_authorities,
    upsert_issuer_authority,
)
from backend.services.live_identity_checks import (
    get_live_identity_check,
    load_live_identity_checks,
    upsert_live_identity_check,
)
from backend.services.credential_status_checks import (
    get_credential_status_check,
    load_credential_status_checks,
    upsert_credential_status_check,
)
from backend.services.public_objects import (
    get_public_object,
    load_public_objects,
    public_object_replay_export,
    upsert_public_object,
)
from backend.services.verifier_portals import (
    get_verifier_portal,
    load_verifier_portals,
    upsert_verifier_portal,
)
from backend.services.verifier_proof_checks import (
    get_verifier_proof_check,
    load_verifier_proof_checks,
    upsert_verifier_proof_check,
)
from backend.services.verifier_signature_checks import (
    get_verifier_signature_check,
    load_verifier_signature_checks,
    upsert_verifier_signature_check,
)
from backend.services.verifier_public_keys import (
    get_verifier_public_key,
    load_verifier_public_keys,
    upsert_verifier_public_key,
)
from backend.services.authz import LedgerAction, authorize_or_raise, principal_from_request
from backend.services.ledger_service import LedgerService
from backend.governance.ontology import ONTOLOGY, OntologyError, validate_relationship_type
from backend.governance.impact import calculate_removal_impact
from backend.services.pilot_account import (
    DEFAULT_ACCOUNT_ID,
    extend_pilot_trial,
    get_admin_account_inspection,
    pilot_now_from_request,
)
from backend.services.pilot_provisioning import _load_jobs, _job_summary
from backend.services.subject_events import append_subject_event, get_subject_event, load_authority_subjects, load_subject_events
from backend.services.benchmark_publication_jobs import (
    benchmark_publication_runtime_config,
    get_benchmark_publication_job,
    get_canonical_publication_snapshot,
    enqueue_benchmark_publication_job,
    run_benchmark_publication_job,
)
from backend.services.session_tokens import apply_session_token_claims_or_raise


router = APIRouter(prefix="/admin", tags=["admin"])
public_router = APIRouter(prefix="/public", tags=["public"])
control_plane_router = APIRouter(prefix="/api/control-plane", tags=["control-plane"])
LOGGER = logging.getLogger(__name__)

LEDGER_REGISTRY_KEY = b"__ledgers__"
LEDGER_REGISTRY_V1_KEY = b"__ledgers_v1__"
TENANT_REGISTRY_V1_KEY = b"__tenants_v1__"
PRINCIPAL_REGISTRY_V1_KEY = b"__principals_v1__"
SURFACE_REGISTRY_V1_KEY = b"__surfaces_v1__"
RELATIONSHIP_REGISTRY_V1_KEY = b"__relationships_v1__"
PROVIDER_CREDENTIAL_REGISTRY_V1_KEY = b"__provider_credentials_v1__"
MODEL_BINDING_REGISTRY_V1_KEY = b"__model_bindings_v1__"
CONTROL_PLANE_MUTATION_REGISTRY_V1_KEY = b"__control_plane_mutations_v1__"
CONTROL_PLANE_SUBMISSION_REGISTRY_V1_KEY = b"__control_plane_submissions_v1__"
BINDING_EVENT_REGISTRY_V1_KEY = b"__principal_binding_events_v1__"
PILOT_SIGNUPS_V1_KEY = b"__pilot_signups_v1__"
_ENTITY_PREFIX = "entity:"
_TOKEN_PREFIXES = ("tp:", "ix:")
_PINNED_PREFIX = "bucket:"
_ENTITY_HEX_PATTERN = re.compile(r"^[0-9a-f]{8}:[0-9a-f]{8}$", re.IGNORECASE)
_RESERVED_NAMESPACE_KEYS = {
    LEDGER_REGISTRY_KEY.decode(),
    LEDGER_REGISTRY_V1_KEY.decode(),
    TENANT_REGISTRY_V1_KEY.decode(),
}
_BENCHMARK_PUBLICATION_BOOTSTRAP_OPERATOR_DIDS = {
    "did:key:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK",
}


class LedgerCreateRequest(BaseModel):
    """Accept lightweight creation payloads from the Streamlit UI."""

    model_config = ConfigDict(extra="ignore")

    ledger_id: str | None = Field(None, description="Canonical ledger identifier.")
    name: str | None = Field(None, description="Human-friendly ledger name")
    namespace: str | None = Field(None, description="Namespace used when writing entries")
    canonical_subject: str | None = Field(None, description="Canonical DID/subject for the ledger resource.")
    canonical_subject_source: str | None = Field(None, description="How canonical_subject was derived.")
    tenant_id: str | None = Field(None, description="Tenant identifier for ownership scoping.")
    owner_principal_id: str | None = Field(None, description="Optional explicit owner principal id.")
    owner_principal_type: str | None = Field(None, description="Optional explicit owner principal type.")
    policy_profile: str = Field("standard", description="Provisioning policy profile id.")
    status: str | None = Field(None, description="Optional lifecycle status override.")
    provisioning_source: str | None = Field(None, description="Optional provisioning source marker.")
    idempotency_key: str | None = Field(None, description="Optional idempotency key for retry-safe writes.")
    submission_ref: str | None = Field(None, description="Optional submission reference for governed writes.")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Optional provisioning metadata.")
    founding_constitution_name: str | None = Field(None, description="Optional ledger self-name used in the founding constitution.")
    founding_constitution_personality: str | None = Field(None, description="Optional starter personality or seed identity text.")
    founding_constitution_purpose: str | None = Field(None, description="Optional compact purpose statement for the ledger.")

    def resolved_name(self) -> str:
        candidate = (self.ledger_id or self.namespace or "").strip()
        return _canonicalize_control_plane_ledger_id(candidate)


class LedgerConsolidationRequest(BaseModel):
    """Governed consolidation payload for accidental split ledgers."""

    model_config = ConfigDict(extra="ignore")

    canonical_ledger_id: str = Field(..., min_length=2, description="Surviving canonical ledger id.")
    superseded_ledger_ids: list[str] = Field(default_factory=list, description="Ledger ids to consolidate into the canonical survivor.")
    reason: str | None = Field(None, description="Operator-supplied explanation for the consolidation.")
    idempotency_key: str | None = Field(None, description="Optional idempotency key for retry-safe writes.")


def _is_valid_control_plane_ledger_id(value: str) -> bool:
    text = _canonicalize_control_plane_ledger_id(value)
    if not text:
        return False
    if "," in text:
        return False
    if text == "pending":
        return False
    return True


def _canonicalize_control_plane_ledger_id(value: str) -> str:
    text = str(value or "").strip()
    while text.startswith("ledger:"):
        text = text[len("ledger:") :].strip()
    return text


def _prefixed_control_plane_ledger_alias(value: str) -> str | None:
    text = _canonicalize_control_plane_ledger_id(value)
    if not text:
        return None
    return f"ledger:{text}"


def _ledger_visible_aliases(canonical_ledger_id: str, *values: Any) -> list[str]:
    aliases: list[str] = []
    canonical = _canonicalize_control_plane_ledger_id(canonical_ledger_id)
    prefixed = _prefixed_control_plane_ledger_alias(canonical)
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        if text in {canonical, prefixed} or text in aliases:
            continue
        aliases.append(text)
    return aliases


def _normalize_related_ledger_id(value: Any) -> str | None:
    text = _canonicalize_control_plane_ledger_id(value)
    return text or None


class TenantCreateRequest(BaseModel):
    """Tenant bootstrap payload for SaaS-style provisioning."""

    model_config = ConfigDict(extra="ignore")

    tenant_id: str | None = Field(None, description="Canonical tenant id, e.g. tenant:acme")
    name: str | None = Field(None, description="Display name used when tenant_id is omitted")
    owner_principal_id: str | None = Field(None, description="Explicit owner principal id")
    owner_principal_type: str | None = Field(None, description="Explicit owner principal type")
    policy_profile: str = Field("standard", description="Provisioning policy profile id")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Optional tenant metadata")
    ledger_ids: list[str] = Field(
        default_factory=list,
        description="Optional explicit ledger ids to provision for this tenant",
    )

    def resolved_tenant_id(self) -> str:
        raw = (self.tenant_id or "").strip()
        if not raw:
            fallback = (self.name or "").strip().lower().replace(" ", "-")
            fallback = re.sub(r"[^a-z0-9._-]", "-", fallback).strip("-")
            if fallback:
                fallback = hashlib.sha256(fallback.encode("utf-8")).hexdigest()[:16]
            raw = f"tenant:{fallback or 'default'}"
        if raw.startswith("tenant:"):
            return raw
        return f"tenant:{raw}"


class PrincipalCreateRequest(BaseModel):
    """Principal bootstrap payload for DID-keyed identity records."""

    model_config = ConfigDict(extra="ignore")

    principal_did: str = Field(..., min_length=5, description="Canonical principal DID")
    tenant_id: str | None = Field(None, description="Tenant scope for the principal record")
    display_name: str | None = Field(None, description="Optional principal display name")
    actor_type: str | None = Field(None, description="Explicit actor class alias for metadata.actor_type")
    key_references: list[str] = Field(
        default_factory=list,
        description="Associated verification key references, e.g. did:key:...#k1",
    )
    principal_key_refs: list[str] | None = Field(
        None,
        description="Canonical principal identity bindings aligned with middleware naming",
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="Optional principal metadata")
    status: str | None = Field(None, description="Optional lifecycle status override")
    provisioning_source: str | None = Field(None, description="Optional provisioning source marker")
    idempotency_key: str | None = Field(None, description="Optional idempotency key for retry-safe writes.")
    submission_ref: str | None = Field(None, description="Optional submission reference for governed writes.")

    def resolved_principal_did(self) -> str:
        return self.principal_did.strip()

    def resolved_principal_key_refs(self) -> list[str]:
        if isinstance(self.principal_key_refs, list) and self.principal_key_refs:
            return list(self.principal_key_refs)
        return list(self.key_references)


class CodexPrincipalProvisionRequest(BaseModel):
    """Provision the stable Codex principal with explicit delegated authority scope."""

    model_config = ConfigDict(extra="ignore")

    tenant_id: str | None = Field(None, description="Tenant scope for the Codex principal")
    ledger_id: str | None = Field(None, description="Optional governed ledger scope for delegated prompting")
    surface_ids: list[str] = Field(default_factory=list, description="Optional governed surface scope for delegated prompting")
    display_name: str | None = Field(None, description="Optional display label override for the Codex principal")
    delegated_by_principal_did: str | None = Field(None, description="Optional explicit delegating principal DID")
    delegated_by_principal_id: str | None = Field(None, description="Optional explicit delegating principal id")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Optional additional metadata to merge into the principal record")
    idempotency_key: str | None = Field(None, description="Optional idempotency key for retry-safe writes.")
    submission_ref: str | None = Field(None, description="Optional submission reference for governed writes.")


class BenchmarkPublicationJobRequest(BaseModel):
    domain_key: str = Field(..., description="Operator benchmark publication domain key.")


class PrincipalDisableRequest(BaseModel):
    """Disable payload for emergency revocation controls."""

    model_config = ConfigDict(extra="ignore")

    reason: str | None = Field(None, description="Optional disable reason for auditability")


class PrincipalStatusRequest(BaseModel):
    """Set principal lifecycle status explicitly."""

    model_config = ConfigDict(extra="ignore")

    status: str = Field(..., min_length=3)
    reason: str | None = None


class AccountRequestDecision(BaseModel):
    """Operator decision on a wallet-verified account request."""

    model_config = ConfigDict(extra="ignore")

    decision: str = Field(..., min_length=1)
    reason: str | None = None


class SurfaceUpsertRequest(BaseModel):
    """Control-plane surface provisioning payload."""

    model_config = ConfigDict(extra="ignore")

    surface_id: str = Field(..., min_length=2)
    display_name: str | None = None
    surface_type: str = Field("custom")
    status: str = Field("pending")
    ledger_id: str | None = None
    principal_did: str | None = None
    binding_ref: str | None = None
    runtime_context_id: str | None = None
    endpoint: str | None = None
    canonical_subject: str | None = None
    canonical_subject_source: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    provisioning_source: str | None = None
    idempotency_key: str | None = None
    submission_ref: str | None = None


class TrialExtensionRequest(BaseModel):
    """Admin payload for extending the pilot trial clock."""

    model_config = ConfigDict(extra="ignore")

    days: int = Field(..., ge=1, le=365)
    actor: str = Field("admin", min_length=1)
    reason: str | None = None


class ProviderCredentialUpsertRequest(BaseModel):
    """Control-plane provider credential payload."""

    model_config = ConfigDict(extra="ignore")

    provider_id: str = Field(..., min_length=3)
    provider_type: str = Field(..., min_length=2)
    credential_ref: str | None = None
    owner_scope: str = Field("shared")
    status: str = Field("planned")
    base_url: str | None = None
    deployment_targets: list[str] = Field(default_factory=list)
    default_model: str | None = None
    readiness_note: str | None = None
    secret_material: str | None = None
    secret_ref: str | None = None
    canonical_subject: str | None = None
    canonical_subject_source: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    submission_ref: str | None = None


class ModelBindingUpsertRequest(BaseModel):
    """Control-plane model binding payload."""

    model_config = ConfigDict(extra="ignore")

    binding_id: str = Field(..., min_length=3)
    name: str | None = None
    provider_id: str | None = None
    provider_ref: str | None = None
    credential_ref: str | None = None
    provider_type: str = Field(..., min_length=2)
    model_id: str = Field(..., min_length=2)
    linked_model_principal: str | None = None
    scope: str = Field("shared")
    status: str = Field("planned")
    app_surfaces: list[str] = Field(default_factory=list)
    policy_profile: str = Field("default")
    source: str = Field("control-plane")
    canonical_subject: str | None = None
    canonical_subject_source: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    submission_ref: str | None = None


class PublicObjectUpsertRequest(BaseModel):
    """Versioned public object lifecycle payload."""

    model_config = ConfigDict(extra="ignore")

    public_object_id: str = Field(..., min_length=4)
    object_kind: str = Field(..., min_length=2)
    object_id: str = Field(..., min_length=2)
    subject_id: str = Field(..., min_length=2)
    issuer_id: str = Field(..., min_length=2)
    content_digest: str = Field(..., min_length=4)
    coord_ref: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    status_ref: str | None = None
    previous_version_id: str | None = None
    superseded_by: str | None = None
    lifecycle_state: str = Field("current")
    invalidation_reason: str | None = None
    revoked_at: str | None = None
    shareability: str | None = None
    artifact_identity: dict[str, Any] = Field(default_factory=dict)


class RelationshipUpsertRequest(BaseModel):
    """Control-plane relationship mutation payload."""

    model_config = ConfigDict(extra="ignore")

    relationship_id: str | None = None
    subject_entity_type: str = Field(..., min_length=2)
    subject_entity_id: str = Field(..., min_length=2)
    object_entity_type: str = Field(..., min_length=2)
    object_entity_id: str = Field(..., min_length=2)
    relationship_type: str = Field("related_to")
    status: str = Field("active")
    enabled_state: str = Field("enabled")
    permission_scope: str = Field("full")
    permission_payload: dict[str, Any] = Field(default_factory=dict)
    ledger_id: str | None = None
    start_at: str | None = None
    end_at: str | None = None
    evidence_ref: str | None = None
    provenance_ref: str | None = None
    canonical_subject: str | None = None
    canonical_subject_source: str | None = None
    idempotency_key: str | None = None
    submission_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EntityRemoveRequest(BaseModel):
    """Remove a control-plane entity and its explicit references."""

    model_config = ConfigDict(extra="ignore")

    entity_type: str = Field(..., min_length=2)
    entity_id: str = Field(..., min_length=2)


class ImpactAnalysisRequest(BaseModel):
    """Request a dry-run impact analysis for removing an entity from a ledger."""

    model_config = ConfigDict(extra="ignore")

    entity_type: str = Field(..., min_length=2)
    entity_id: str = Field(..., min_length=2)
    ledger_id: str = Field(..., min_length=2)


class ConnectionRemoveRequest(BaseModel):
    """Confirm removal of an entity from a ledger."""

    model_config = ConfigDict(extra="ignore")

    entity_type: str = Field(..., min_length=2)
    entity_id: str = Field(..., min_length=2)
    ledger_id: str = Field(..., min_length=2)
    confirmation_token: str = Field(..., min_length=2)


class ControlPlaneEntityActivationRequest(BaseModel):
    """Activate a control-plane governed entity."""

    model_config = ConfigDict(extra="ignore")

    entity_type: str = Field(..., min_length=2)
    entity_id: str = Field(..., min_length=2)
    status: str = Field("active")
    tenant_id: str | None = None
    ledger_id: str | None = None
    idempotency_key: str | None = None
    submission_ref: str | None = None


class ControlPlaneSubmissionRequest(BaseModel):
    """Persist a governed mutation request for later approval."""

    model_config = ConfigDict(extra="ignore")

    mutation_kind: str = Field(..., min_length=3)
    target_path: str = Field(..., min_length=3)
    target_entity_type: str | None = None
    target_entity_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None
    submitted_by: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    idempotency_key: str | None = None
    submission_ref: str | None = None


class ControlPlaneSubmissionReviewRequest(BaseModel):
    """Approve or reject a queued governed mutation."""

    model_config = ConfigDict(extra="ignore")

    action: str = Field("approve", min_length=3)
    reviewer_note: str | None = None


class PrincipalKeyRefBindRequest(BaseModel):
    """Bind a normalized key reference onto an existing principal."""

    model_config = ConfigDict(extra="ignore")

    principal_key_ref: str = Field(..., min_length=3)
    tenant_id: str | None = None
    binding_metadata: dict[str, Any] = Field(default_factory=dict)
    issuer: str | None = None
    reason: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    idempotency_key: str | None = None


class PrincipalGithubLinkRequest(BaseModel):
    """Attach GitHub identity metadata and binding onto an existing principal."""

    model_config = ConfigDict(extra="ignore")

    github_user_id: str = Field(..., min_length=1)
    github_login: str | None = None
    github_email: str | None = None


class SubjectEventCreateRequest(BaseModel):
    """Append-only backend subject-event payload."""

    model_config = ConfigDict(extra="ignore")

    event_type: str = Field(..., min_length=3)
    issuer: str = Field(..., min_length=2)
    resulting_authority_subject_id: str = Field(..., min_length=3)
    principal_did: str | None = None
    canonical_subject: str | None = None
    prior_authority_subject_id: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    standing_carryover: str | None = None
    credential_carryover: str | None = None
    event_id: str | None = None


class AuthorityEventCreateRequest(BaseModel):
    """Append-only backend authority standing event payload."""

    model_config = ConfigDict(extra="ignore")

    authority_subject_id: str = Field(..., min_length=3)
    event_type: str = Field(..., min_length=3)
    issuer: str = Field(..., min_length=2)
    reason_code: str = Field(..., min_length=2)
    idempotency_key: str = Field(..., min_length=1)
    delta: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    principal_did: str | None = None
    canonical_subject: str | None = None
    credential_ref: str | None = None
    standing_envelope_ref: str | None = None
    subject_transition_event_ref: str | None = None
    event_id: str | None = None


class IssuerAuthorityUpsertRequest(BaseModel):
    """Issuer-authority registry payload."""

    model_config = ConfigDict(extra="ignore")

    issuer: str = Field(..., min_length=2)
    issuer_class: str = Field(..., min_length=2)
    allowed_event_types: list[str] = Field(default_factory=list)
    evidence_requirement: str = Field("required")
    credential_ref: str | None = None
    issuer_did: str | None = None
    identity_anchor_ref: str | None = None
    trust_basis: str | None = None
    verification_state: str = Field("registry_only")
    policy_ref: str | None = None
    policy_verdict: str | None = None
    policy_scope: list[str] = Field(default_factory=list)
    verifier_policy_ref: str | None = None
    vc_type: str | None = None
    vc_id: str | None = None
    vc_envelope: dict[str, Any] | None = None
    credential_status_ref: str | None = None
    credential_status_state: str = Field("active")
    credential_status_checked_at: str | None = None
    vc_verification_method: str | None = None
    vc_verification_status: str = Field("unverified")
    vc_verification_checked_at: str | None = None
    vc_verification_proof_ref: str | None = None
    status: str = Field("active")
    notes: str | None = None


class EvidenceManifestUpsertRequest(BaseModel):
    """Evidence-manifest payload."""

    model_config = ConfigDict(extra="ignore")

    issuer: str = Field(..., min_length=2)
    evidence_refs: list[str] = Field(default_factory=list)
    authority_subject_id: str | None = None
    manifest_ref: str | None = None
    package_type: str = Field("hashed_manifest")
    signature_ref: str | None = None
    signature_status: str = Field("unsigned")
    verification_method: str | None = None
    verification_status: str | None = None
    verification_checked_at: str | None = None
    verification_proof_ref: str | None = None
    status: str = Field("active")


class VerifierPortalUpsertRequest(BaseModel):
    """External verifier portal registry payload."""

    model_config = ConfigDict(extra="ignore")

    portal_id: str = Field(..., min_length=2)
    portal_type: str = Field(..., min_length=2)
    trust_basis: str = Field(..., min_length=2)
    verification_mode: str = Field(..., min_length=2)
    trusted_identities: list[str] = Field(default_factory=list)
    allowed_sources: list[str] = Field(default_factory=list)
    resolver_ref: str | None = None
    public_key_ref: str | None = None
    status: str = Field("active")
    notes: str | None = None


class VerifierProofCheckUpsertRequest(BaseModel):
    """Resolver-backed proof verification payload."""

    model_config = ConfigDict(extra="ignore")

    proof_ref: str = Field(..., min_length=2)
    resolver_ref: str = Field(..., min_length=2)
    portal_id: str | None = None
    verifier_identity: str | None = None
    verification_status: str = Field("verified")
    checked_at: str | None = None
    proof_hash: str | None = None
    trust_root_ref: str | None = None
    notes: str | None = None


class VerifierSignatureCheckUpsertRequest(BaseModel):
    """Signature verification payload for signature-required portals."""

    model_config = ConfigDict(extra="ignore")

    signature_ref: str = Field(..., min_length=2)
    public_key_ref: str = Field(..., min_length=2)
    portal_id: str | None = None
    verifier_identity: str | None = None
    verification_status: str = Field("verified")
    checked_at: str | None = None
    signature_hash: str | None = None
    trust_root_ref: str | None = None
    notes: str | None = None


class VerifierPublicKeyUpsertRequest(BaseModel):
    """Verifier portal public key payload."""

    model_config = ConfigDict(extra="ignore")

    public_key_ref: str = Field(..., min_length=2)
    algorithm: str = Field(..., min_length=2)
    public_key_pem: str = Field(..., min_length=16)
    trust_root_ref: str | None = None
    status: str = Field("active")
    notes: str | None = None


class LiveIdentityCheckUpsertRequest(BaseModel):
    """Live identity resolution payload."""

    model_config = ConfigDict(extra="ignore")

    subject_ref: str = Field(..., min_length=2)
    subject_type: str = Field(..., min_length=2)
    resolver_ref: str = Field(..., min_length=2)
    resolution_status: str = Field("verified")
    resolved_identity: str | None = None
    authority_binding_ref: str | None = None
    identity_anchor_ref: str | None = None
    checked_at: str | None = None
    trust_root_ref: str | None = None
    evidence_ref: str | None = None
    notes: str | None = None


class CredentialStatusCheckUpsertRequest(BaseModel):
    """Authoritative credential-status payload."""

    model_config = ConfigDict(extra="ignore")

    credential_status_ref: str = Field(..., min_length=2)
    credential_id: str | None = None
    resolver_ref: str = Field(..., min_length=2)
    status_state: str = Field("active")
    checked_at: str | None = None
    proof_ref: str | None = None
    trust_root_ref: str | None = None
    issuer: str | None = None
    notes: str | None = None


def _load_registered_ledgers(db) -> Set[str]:
    raw = db.get(LEDGER_REGISTRY_KEY)
    if raw is None:
        return set()

    try:
        payload = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
    except Exception:  # pragma: no cover - defensive guard
        return set()

    return {
        candidate
        for item in payload
        for candidate in [str(item).strip()]
        if candidate and candidate not in _RESERVED_NAMESPACE_KEYS
    }


def _persist_registered_ledgers(db, ledgers: Iterable[str]) -> list[str]:
    cleaned = sorted(
        {
            ledger.strip()
            for ledger in ledgers
            if ledger and ledger.strip() and ledger.strip() not in _RESERVED_NAMESPACE_KEYS
        }
    )
    db[LEDGER_REGISTRY_KEY] = json.dumps(cleaned).encode()
    return cleaned


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_ENTITY_ALLOWED_STATUSES = {"pending", "active", "disabled", "retired"}
_RELATIONSHIP_ALLOWED_STATUSES = {"pending", "active", "disabled", "expired", "retired"}
_ENABLED_STATES = {"enabled", "disabled"}
_PERMISSION_SCOPES = {"full", "custom"}
_ENTITY_ALLOWED_TRANSITIONS = {
    "pending": {"active", "retired"},
    "active": {"disabled", "retired"},
    "disabled": {"active", "retired"},
    "retired": set(),
}
_RELATIONSHIP_ALLOWED_TRANSITIONS = {
    "pending": {"active", "retired"},
    "active": {"disabled", "expired", "retired"},
    "disabled": {"active", "retired"},
    "expired": set(),
    "retired": set(),
}


def _payload_fingerprint(action: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps({"action": action, "payload": payload}, separators=(",", ":"), sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _load_control_plane_mutations_v1(db) -> dict[str, dict[str, Any]]:
    raw = db.get(CONTROL_PLANE_MUTATION_REGISTRY_V1_KEY)
    registry: dict[str, dict[str, Any]] = {}
    if raw is None:
        return registry
    try:
        payload = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
    except Exception:
        return registry
    records = payload.get("mutations", payload) if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return registry
    for mutation_key, record in records.items():
        normalized_key = str(mutation_key).strip()
        if not normalized_key or not isinstance(record, dict):
            continue
        registry[normalized_key] = dict(record)
    return registry


def _persist_control_plane_mutations_v1(db, registry: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for mutation_key in sorted(registry.keys()):
        record = registry.get(mutation_key)
        if not isinstance(record, dict):
            continue
        canonical[mutation_key] = dict(record)
    db[CONTROL_PLANE_MUTATION_REGISTRY_V1_KEY] = json.dumps(
        {"version": 1, "mutations": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def _replay_control_plane_mutation(
    db,
    *,
    idempotency_key: str | None,
    fingerprint: str,
) -> dict[str, Any] | None:
    normalized = str(idempotency_key or "").strip()
    if not normalized:
        return None
    registry = _load_control_plane_mutations_v1(db)
    existing = registry.get(normalized)
    if not isinstance(existing, dict):
        return None
    if str(existing.get("fingerprint") or "").strip() != fingerprint:
        raise HTTPException(status_code=409, detail="idempotency_key already used for a different control-plane mutation")
    response = existing.get("response")
    if not isinstance(response, dict):
        raise HTTPException(status_code=409, detail="idempotent mutation record is invalid")
    return response


def _store_control_plane_mutation(
    db,
    *,
    idempotency_key: str | None,
    fingerprint: str,
    response: dict[str, Any],
) -> None:
    normalized = str(idempotency_key or "").strip()
    if not normalized:
        return
    registry = _load_control_plane_mutations_v1(db)
    registry[normalized] = {
        "idempotency_key": normalized,
        "fingerprint": fingerprint,
        "response": dict(response),
        "recorded_at": _now_iso(),
    }
    _persist_control_plane_mutations_v1(db, registry)


def _load_binding_events_v1(db) -> list[dict[str, Any]]:
    raw = db.get(BINDING_EVENT_REGISTRY_V1_KEY)
    if raw is None:
        return []
    try:
        payload = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
    except Exception:
        return []
    rows = payload.get("binding_events", payload) if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _persist_binding_events_v1(db, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    canonical = [dict(row) for row in events if isinstance(row, dict)]
    db[BINDING_EVENT_REGISTRY_V1_KEY] = json.dumps(
        {"version": 1, "binding_events": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def _append_binding_event_v1(
    db,
    *,
    principal_did: str,
    tenant_id: str,
    principal_key_ref: str,
    issuer: str | None = None,
    reason: str | None = None,
    evidence_refs: list[str] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    normalized_evidence = [str(item).strip() for item in (evidence_refs or []) if str(item).strip()]
    normalized_issuer = str(issuer or "system").strip() or "system"
    normalized_reason = str(reason or "").strip() or None
    fingerprint = _payload_fingerprint(
        "principal_binding_event",
        {
            "principal_did": principal_did,
            "tenant_id": tenant_id,
            "principal_key_ref": principal_key_ref,
            "issuer": normalized_issuer,
            "reason": normalized_reason,
            "evidence_refs": normalized_evidence,
            "event_type": "binding_activated",
        },
    )
    normalized_idempotency = str(idempotency_key or "").strip()
    events = _load_binding_events_v1(db)
    if normalized_idempotency:
        for event in events:
            if str(event.get("idempotency_key") or "").strip() != normalized_idempotency:
                continue
            if str(event.get("fingerprint") or "").strip() != fingerprint:
                raise HTTPException(status_code=409, detail="binding event idempotency_key already used for a different transition")
            return dict(event)
    event = {
        "event_id": f"binding-event:{uuid4().hex}",
        "event_type": "binding_activated",
        "lifecycle_state": "active",
        "principal_did": principal_did,
        "tenant_id": tenant_id or None,
        "principal_key_ref": principal_key_ref,
        "issuer": normalized_issuer,
        "reason": normalized_reason,
        "evidence_refs": normalized_evidence,
        "idempotency_key": normalized_idempotency or None,
        "fingerprint": fingerprint,
        "created_at": _now_iso(),
    }
    events.append(event)
    _persist_binding_events_v1(db, events)
    return dict(event)


def _list_binding_events_v1(db, *, principal_did: str, limit: int = 50) -> list[dict[str, Any]]:
    rows = [row for row in _load_binding_events_v1(db) if str(row.get("principal_did") or "").strip() == str(principal_did or "").strip()]
    return rows[-max(1, int(limit)):]


def _resolve_principal_by_key_ref(
    registry: dict[str, dict[str, Any]],
    *,
    principal_key_ref: str,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    normalized = _normalize_principal_key_reference(principal_key_ref)
    tenant_filter = str(tenant_id or "").strip()
    candidates: list[dict[str, Any]] = []
    for principal_did in sorted(registry.keys()):
        row = registry.get(principal_did)
        if not isinstance(row, dict):
            continue
        if tenant_filter and str(row.get("tenant_id") or "").strip() != tenant_filter:
            continue
        if str(row.get("status") or "active").strip().lower() != "active":
            continue
        refs = (
            row.get("principal_key_refs")
            if isinstance(row.get("principal_key_refs"), list)
            else row.get("key_references")
            if isinstance(row.get("key_references"), list)
            else []
        )
        if normalized in {str(item).strip() for item in refs if str(item).strip()}:
            candidates.append(dict(row))
    result = {
        "principal_key_ref": str(principal_key_ref or "").strip(),
        "canonical_principal_key_ref": normalized,
        "tenant_id": tenant_filter or None,
    }
    if not candidates:
        result.update({"outcome": "not_found", "principal": None, "conflicting_principals": []})
        return result
    if len(candidates) > 1:
        result.update({
            "outcome": "conflict",
            "principal": None,
            "conflicting_principals": [
                {
                    "principal_did": str(row.get("principal_did") or "").strip(),
                    "tenant_id": str(row.get("tenant_id") or "").strip() or None,
                    "status": str(row.get("status") or "").strip() or None,
                }
                for row in candidates
            ],
        })
        return result
    result.update({"outcome": "resolved", "principal": candidates[0], "conflicting_principals": []})
    return result


def _load_control_plane_submissions_v1(db) -> dict[str, dict[str, Any]]:
    raw = db.get(CONTROL_PLANE_SUBMISSION_REGISTRY_V1_KEY)
    registry: dict[str, dict[str, Any]] = {}
    if raw is None:
        return registry
    try:
        payload = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
    except Exception:
        return registry
    records = payload.get("submissions", payload) if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return registry
    for submission_ref, record in records.items():
        normalized_ref = str(submission_ref).strip()
        if not normalized_ref or not isinstance(record, dict):
            continue
        registry[normalized_ref] = dict(record)
    return registry


def _persist_control_plane_submissions_v1(db, registry: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for submission_ref in sorted(registry.keys()):
        record = registry.get(submission_ref)
        if not isinstance(record, dict):
            continue
        canonical[submission_ref] = dict(record)
    db[CONTROL_PLANE_SUBMISSION_REGISTRY_V1_KEY] = json.dumps(
        {"version": 1, "submissions": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def _submission_lifecycle_event(*, status: str, actor: str | None, note: str | None = None, detail: Any | None = None) -> dict[str, Any]:
    event = {
        "status": str(status or "").strip().lower(),
        "changed_at": _now_iso(),
        "changed_by": str(actor or "").strip() or None,
    }
    if str(note or "").strip():
        event["note"] = str(note).strip()
    if detail is not None:
        event["detail"] = detail
    return event


def _build_control_plane_submission_record(
    *,
    request: Request,
    payload: ControlPlaneSubmissionRequest,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    timestamp = _now_iso()
    current = dict(existing or {})
    mutation = _control_plane_mutation_metadata(request=request, submission_ref=payload.submission_ref, existing=current)
    actor = str(payload.submitted_by or current.get("submitted_by") or principal_from_request(request).principal_id or "").strip() or None
    submission_ref = str(payload.submission_ref or current.get("submission_ref") or "").strip() or f"cps:{uuid4().hex}"
    current_lifecycle = list(current.get("lifecycle") or []) if isinstance(current.get("lifecycle"), list) else []
    lifecycle = current_lifecycle or [_submission_lifecycle_event(status="submitted", actor=actor)]
    return {
        "submission_ref": submission_ref,
        "mutation_kind": str(payload.mutation_kind or current.get("mutation_kind") or "").strip(),
        "target_path": str(payload.target_path or current.get("target_path") or "").strip(),
        "target_entity_type": str(payload.target_entity_type or current.get("target_entity_type") or "").strip() or None,
        "target_entity_id": str(payload.target_entity_id or current.get("target_entity_id") or "").strip() or None,
        "payload": dict(payload.payload or current.get("payload") or {}),
        "reason": str(payload.reason or current.get("reason") or "").strip() or None,
        "submitted_by": actor,
        "evidence_refs": list(payload.evidence_refs or current.get("evidence_refs") or []),
        "idempotency_key": str(payload.idempotency_key or current.get("idempotency_key") or "").strip() or None,
        "status": "submitted",
        "execution_mode": "submitted_for_approval",
        "submission_status": "submitted",
        "created_at": str(current.get("created_at") or "").strip() or timestamp,
        "updated_at": timestamp,
        "created_by_principal_id": mutation["created_by_principal_id"],
        "last_changed_by_principal_id": mutation["last_changed_by_principal_id"],
        "submitted_by_principal_id": actor,
        "submission_ref_source": "control_plane_submission_api_v1",
        "lifecycle": lifecycle,
    }


def _classify_submission_failure(*, status_code: int, detail: Any) -> str:
    if status_code == 409:
        return "conflict_or_supersession_failure"
    if status_code == 403:
        return "governance_rejection"
    if status_code == 422:
        return "validation_failure"
    return "apply_time_failure"


def _apply_control_plane_submission(
    *,
    request: Request,
    submission_ref: str,
    record: dict[str, Any],
    db,
) -> dict[str, Any]:
    target_path = str(record.get("target_path") or "").strip()
    payload = dict(record.get("payload") or {})
    payload["submission_ref"] = submission_ref
    payload["idempotency_key"] = None
    if target_path == "/api/control-plane/relationships":
        mutation = RelationshipUpsertRequest.model_validate(payload)
        return control_plane_upsert_relationship(request, mutation, db)
    if target_path == "/api/control-plane/entities/activate":
        mutation = ControlPlaneEntityActivationRequest.model_validate(payload)
        return control_plane_activate_entity(request, mutation, db)
    raise HTTPException(status_code=422, detail=f"submission target_path {target_path} is not supported for review")


def _validate_status(status_value: str, *, allowed: set[str], field_name: str) -> str:
    normalized = str(status_value or "").strip().lower()
    if normalized not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise HTTPException(status_code=422, detail=f"{field_name} must be one of {allowed_values}")
    return normalized


def _validate_lifecycle_transition(
    *,
    current_status: str | None,
    target_status: str,
    transitions: dict[str, set[str]],
    field_name: str,
) -> None:
    current = str(current_status or "").strip().lower()
    target = str(target_status or "").strip().lower()
    if not current or current == target:
        return
    allowed_targets = transitions.get(current, set())
    if target not in allowed_targets:
        raise HTTPException(status_code=422, detail=f"{field_name} cannot transition from {current} to {target}")


def _control_plane_mutation_metadata(*, request: Request, submission_ref: str | None, existing: dict[str, Any] | None = None) -> dict[str, str | None]:
    principal = principal_from_request(request)
    current = dict(existing or {})
    return {
        "created_by_principal_id": str(current.get("created_by_principal_id") or principal.principal_id).strip() or None,
        "last_changed_by_principal_id": str(principal.principal_id or "").strip() or None,
        "submission_ref": str(submission_ref or current.get("submission_ref") or "").strip() or None,
    }


_OPERATOR_SOURCE_PRECEDENCE = [
    "backend_canonical_record",
    "middleware_governed_envelope",
    "dashboard_render_model",
    "display_alias",
]



def _reference_shareability(ref: str | None) -> str:
    value = str(ref or "").strip()
    if not value:
        return "not-shareable"
    if value.startswith(("did:web:", "https://", "http://")):
        return "share-ready"
    if value.startswith(("cps:", "cpm:", "WX-", "ATT-", "EV-", "did:key:")):
        return "internal-only"
    return "fallback-only"


def _lifecycle_aware_shareability(record: dict[str, Any], preferred: str | None) -> str:
    lifecycle_state = str(
        record.get("lifecycle_state")
        or record.get("object_lifecycle_state")
        or record.get("status")
        or ""
    ).strip().lower()
    explicit = str(record.get("shareability") or "").strip().lower()
    if explicit in {"share-ready", "internal-only", "fallback-only", "not-shareable"}:
        base = explicit
    else:
        base = _reference_shareability(preferred)
    if lifecycle_state == "revoked":
        return "not-shareable"
    if lifecycle_state == "superseded" and base == "share-ready":
        return "fallback-only"
    return base



def _control_plane_primary_identifier(record: dict[str, Any], kind: str) -> str | None:
    fields_by_kind = {
        "public_object": ["current_public_object_id", "public_object_id", "status_ref"],
        "ledger": ["canonical_subject", "ledger_id", "namespace"],
        "principal": ["canonical_subject", "principal_did"],
        "surface": ["canonical_subject", "surface_id"],
        "provider": ["canonical_subject", "provider_id", "credential_ref"],
        "model_binding": ["canonical_subject", "binding_id", "linked_model_principal"],
        "relationship": ["canonical_subject", "relationship_id"],
        "submission": ["submission_ref", "mutation_ref", "target_entity_id"],
    }
    for field in fields_by_kind.get(kind, []):
        value = str(record.get(field) or "").strip()
        if value:
            return value
    return None



def _control_plane_row_family(kind: str) -> str:
    if kind == "public_object":
        return "public_object"
    if kind == "submission":
        return "governance"
    if kind == "relationship":
        return "relationship"
    if kind == "principal":
        return "identity"
    return "interaction"



def _control_plane_detail_panels(kind: str) -> list[str]:
    if kind == "public_object":
        return ["overview", "lifecycle", "governance", "provenance", "payload"]
    if kind == "submission":
        return ["overview", "governance", "provenance", "payload"]
    if kind == "relationship":
        return ["overview", "permission_or_access", "governance", "provenance", "payload"]
    if kind == "principal":
        return ["overview", "governance", "standing_or_trust", "provenance", "payload"]
    return ["overview", "governance", "provenance", "payload"]



def _derive_ledger_self_description(record: dict[str, Any]) -> dict[str, Any]:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    founding = metadata.get("founding_constitution") if isinstance(metadata.get("founding_constitution"), dict) else {}
    seed_identity = {
        "name": str(founding.get("name") or record.get("display_name") or record.get("ledger_id") or "").strip() or None,
        "personality": str(founding.get("personality") or "").strip() or None,
        "purpose": str(founding.get("purpose") or "").strip() or None,
        "source": str(founding.get("source") or "control_plane_operator").strip() or "control_plane_operator",
    }

    verified_traits: list[dict[str, Any]] = []
    topology = str(metadata.get("ledger_topology") or "").strip()
    if topology:
        verified_traits.append({
            "trait": "topology",
            "summary": f"{topology.title()} topology is configured for this ledger.",
            "evidence": [{"field": "metadata.ledger_topology", "value": topology}],
        })
    provisioning_state = str(metadata.get("provisioning_state") or "").strip()
    if provisioning_state:
        verified_traits.append({
            "trait": "provisioning_state",
            "summary": f"Provisioning state is {provisioning_state.replace('_', ' ')}.",
            "evidence": [{"field": "metadata.provisioning_state", "value": provisioning_state}],
        })
    status = str(record.get("status") or "").strip()
    if status:
        verified_traits.append({
            "trait": "lifecycle_status",
            "summary": f"Lifecycle status is {status}.",
            "evidence": [{"field": "status", "value": status}],
        })
    policy_profile = str(record.get("policy_profile") or "").strip()
    if policy_profile and policy_profile != "unknown":
        verified_traits.append({
            "trait": "policy_profile",
            "summary": f"Policy profile is {policy_profile}.",
            "evidence": [{"field": "policy_profile", "value": policy_profile}],
        })
    alias_history = metadata.get("ledger_alias_history") if isinstance(metadata.get("ledger_alias_history"), list) else []
    alias_values = [str(item).strip() for item in alias_history if isinstance(item, str) and str(item).strip()]
    if alias_values:
        verified_traits.append({
            "trait": "alias_and_supersession_history",
            "summary": f"{len(alias_values)} historical ledger alias reference(s) remain attributable to this governed memory boundary.",
            "evidence": [{"field": "metadata.ledger_alias_history", "value": value} for value in alias_values[:6]],
        })
    consolidation_history = metadata.get("ledger_consolidation_history") if isinstance(metadata.get("ledger_consolidation_history"), list) else []
    if consolidation_history:
        verified_traits.append({
            "trait": "consolidation_history",
            "summary": f"{len([item for item in consolidation_history if item])} consolidation event(s) are recorded for this ledger.",
            "evidence": [{"field": "metadata.ledger_consolidation_history", "value": f"{len([item for item in consolidation_history if item])} event(s)"}],
        })

    speculative_raw = metadata.get("speculative_overlay")
    speculative_overlay = None
    if isinstance(speculative_raw, dict):
        speculative_overlay = dict(speculative_raw)
    elif isinstance(speculative_raw, str) and speculative_raw.strip():
        speculative_overlay = {"summary": speculative_raw.strip()}

    runtime_foundation_identity = {
        "available": any(
            bool(str(founding.get(field) or "").strip())
            for field in ("name", "personality", "purpose", "source")
        ),
        "fields": {
            "name": str(founding.get("name") or "").strip() or None,
            "personality": str(founding.get("personality") or "").strip() or None,
            "purpose": str(founding.get("purpose") or "").strip() or None,
            "source": str(founding.get("source") or "").strip() or None,
        },
        "structured_runtime_surface_required": True,
    }
    resolved_constitution_context = {
        "present": False,
        "basis": [],
        "coord_resolved_access_is_not_runtime_foundation_identity": True,
    }

    return {
        "seed_identity": seed_identity,
        "verified_ledger_traits": verified_traits,
        "resolved_constitution_context": resolved_constitution_context,
        "runtime_foundation_identity": runtime_foundation_identity,
        "speculative_overlay": speculative_overlay,
        "boundary_rule": "seed_identity_and_speculative_overlay_must_not_be_presented_as_verified_history",
    }


def _control_plane_aliases(record: dict[str, Any], kind: str, preferred: str | None) -> list[dict[str, Any]]:
    alias_fields = {
        "public_object": ["public_object_id", "status_ref", "object_id", "previous_version_id", "superseded_by"],
        "ledger": ["ledger_id", "namespace"],
        "principal": ["principal_did", "display_name"],
        "surface": ["surface_id", "display_name"],
        "provider": ["provider_id", "credential_ref"],
        "model_binding": ["binding_id", "model_id", "linked_model_principal"],
        "relationship": ["relationship_id", "subject_entity_id", "object_entity_id"],
        "submission": ["submission_ref", "mutation_ref", "target_entity_id"],
    }
    seen: set[str] = set()
    aliases: list[dict[str, Any]] = []
    for field in alias_fields.get(kind, []):
        value = str(record.get(field) or "").strip()
        if not value or value == preferred or value in seen:
            continue
        seen.add(value)
        aliases.append({
            "value": value,
            "field": field,
            "role": "supporting_alias",
            "shareability": _lifecycle_aware_shareability(record, value),
        })
    return aliases



def _annotate_control_plane_row(record: dict[str, Any], *, kind: str) -> dict[str, Any]:
    annotated = dict(record)
    if kind == "ledger":
        annotated["ledger_self_description"] = _derive_ledger_self_description(annotated)
        metadata = annotated.get("metadata") if isinstance(annotated.get("metadata"), dict) else {}
        tier_contract = metadata.get("memory_tier_contract") if isinstance(metadata.get("memory_tier_contract"), dict) else {}
        annotated["memory_tier_classification"] = {
            "retention_tier": str(metadata.get("retention_tier") or "Clay"),
            "contract": dict(tier_contract),
        }
    preferred_value = _control_plane_primary_identifier(annotated, kind)
    shareability = _lifecycle_aware_shareability(annotated, preferred_value)
    annotated["row_family"] = _control_plane_row_family(kind)
    annotated["source_precedence"] = {
        "current_source": "backend_canonical_record",
        "order": list(_OPERATOR_SOURCE_PRECEDENCE),
        "dashboard_inference_allowed": False,
    }
    annotated["preferred_reference"] = {
        "value": preferred_value,
        "shareability": shareability,
        "kind": kind,
        "copy_role": "primary",
    }
    annotated["shareability"] = shareability
    annotated["reference_aliases"] = _control_plane_aliases(annotated, kind, preferred_value)
    annotated["detail_panels"] = _control_plane_detail_panels(kind)
    return annotated



def _build_control_plane_response(
    *,
    resource_key: str,
    record: dict[str, Any],
    entity_type: str | None = None,
    previous_status: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    applied_at = str(record.get("updated_at") or record.get("created_at") or _now_iso()).strip() or _now_iso()
    mutation_ref = str(record.get("mutation_ref") or "").strip() or f"cpm:{uuid4().hex}"
    response = {
        "status": "ok",
        "execution_mode": "direct_write",
        "submission_status": "applied",
        "mutation_ref": mutation_ref,
        "submission_ref": str(record.get("submission_ref") or "").strip() or None,
        "applied_at": applied_at,
        "idempotency_key": str(idempotency_key or "").strip() or None,
        resource_key: _annotate_control_plane_row(record, kind=resource_key),
    }
    if entity_type:
        response["entity_type"] = entity_type
        response["previous_status"] = str(previous_status or record.get("status") or "").strip().lower() or None
        response["current_status"] = str(record.get("status") or "").strip().lower() or None
    return response


def _default_tenant_id(request: Request, owner_principal_id: str) -> str:
    header_tenant = request.headers.get("x-tenant-id")
    if isinstance(header_tenant, str) and header_tenant.strip():
        return header_tenant.strip()
    owner = owner_principal_id.strip()
    if owner:
        slug = hashlib.sha256(owner.encode("utf-8")).hexdigest()[:16]
        return f"tenant:{slug}"
    return "tenant:default"


def _ledger_memory_tier_contract() -> dict[str, Any]:
    return {
        "ledger_record_tier": "Clay",
        "durable_classes": [
            "founding_constitution",
            "ledger_alias_history",
            "ledger_supersession_history",
            "ledger_consolidation_history",
        ],
        "future_silt_classes": [
            "active_continuity_state",
            "working_profile_state",
        ],
        "future_sand_classes": [
            "multimodal_stream_ingress",
            "surface_recognition_windows",
        ],
        "s1_s2_topology_acknowledged": True,
    }


def _apply_ledger_memory_tier_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    normalized = dict(metadata or {})
    normalized.setdefault("ledger_alias_history", [])
    normalized.setdefault("ledger_supersession_history", [])
    normalized.setdefault("ledger_consolidation_history", [])
    normalized["retention_tier"] = "Clay"
    normalized["memory_tier_contract"] = _ledger_memory_tier_contract()
    return normalized


def _unique_string_list(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def _ledger_history_aliases(record: dict[str, Any]) -> list[str]:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    aliases = metadata.get("ledger_alias_history") if isinstance(metadata.get("ledger_alias_history"), list) else []
    return _unique_string_list(
        [
            *(aliases or []),
            record.get("ledger_id"),
            record.get("namespace"),
            metadata.get("display_alias"),
        ]
    )


def _rebind_surface_ledgers(
    registry: dict[str, dict[str, Any]],
    *,
    from_ids: set[str],
    canonical_ledger_id: str,
    timestamp: str,
) -> int:
    count = 0
    for surface_id, record in list(registry.items()):
        if not isinstance(record, dict):
            continue
        if str(record.get("ledger_id") or "").strip() not in from_ids:
            continue
        updated = dict(record)
        updated["ledger_id"] = canonical_ledger_id
        updated["updated_at"] = timestamp
        registry[surface_id] = updated
        count += 1
    return count


def _rebind_principal_ledgers(
    registry: dict[str, dict[str, Any]],
    *,
    from_ids: set[str],
    canonical_ledger_id: str,
    timestamp: str,
) -> int:
    count = 0
    for principal_did, record in list(registry.items()):
        if not isinstance(record, dict):
            continue
        metadata = dict(record.get("metadata") or {}) if isinstance(record.get("metadata"), dict) else {}
        top_level = str(record.get("ledger_id") or "").strip()
        metadata_ledger = str(metadata.get("ledger_id") or "").strip()
        if top_level not in from_ids and metadata_ledger not in from_ids:
            continue
        updated = dict(record)
        updated["updated_at"] = timestamp
        if top_level in from_ids:
            updated["ledger_id"] = canonical_ledger_id
        if metadata_ledger in from_ids:
            metadata["ledger_id"] = canonical_ledger_id
        updated["metadata"] = metadata
        registry[principal_did] = updated
        count += 1
    return count


def _rebind_relationship_ledgers(
    registry: dict[str, dict[str, Any]],
    *,
    from_ids: set[str],
    canonical_ledger_id: str,
    timestamp: str,
) -> int:
    count = 0
    rebound: dict[str, dict[str, Any]] = {}
    for relationship_id, record in registry.items():
        if not isinstance(record, dict):
            continue
        updated = dict(record)
        mutated = False
        if str(updated.get("ledger_id") or "").strip() in from_ids:
            updated["ledger_id"] = canonical_ledger_id
            mutated = True
        if str(updated.get("subject_entity_type") or "").strip().lower() == "ledger" and str(updated.get("subject_entity_id") or "").strip() in from_ids:
            updated["subject_entity_id"] = canonical_ledger_id
            mutated = True
        if str(updated.get("object_entity_type") or "").strip().lower() == "ledger" and str(updated.get("object_entity_id") or "").strip() in from_ids:
            updated["object_entity_id"] = canonical_ledger_id
            mutated = True
        new_relationship_id = _control_plane_relationship_id(
            subject_entity_type=str(updated.get("subject_entity_type") or ""),
            subject_entity_id=str(updated.get("subject_entity_id") or ""),
            object_entity_type=str(updated.get("object_entity_type") or ""),
            object_entity_id=str(updated.get("object_entity_id") or ""),
        )
        updated["relationship_id"] = new_relationship_id
        if mutated:
            updated["updated_at"] = timestamp
            count += 1
        rebound[new_relationship_id] = updated
    registry.clear()
    registry.update(rebound)
    return count


def _build_registry_record(
    *,
    request: Request,
    ledger_id: str,
    payload: LedgerCreateRequest,
) -> dict[str, Any]:
    ledger_id = _canonicalize_control_plane_ledger_id(ledger_id) or "default"
    principal = principal_from_request(request)
    owner_principal_id = (payload.owner_principal_id or principal.principal_id).strip() or "anonymous"
    owner_principal_type = (payload.owner_principal_type or principal.principal_type).strip() or "service"
    tenant_id = (payload.tenant_id or _default_tenant_id(request, owner_principal_id)).strip() or "tenant:default"
    policy_profile = (payload.policy_profile or "standard").strip() or "standard"
    timestamp = _now_iso()
    mutation = _control_plane_mutation_metadata(request=request, submission_ref=payload.submission_ref)
    requested_ledger_id = str(payload.ledger_id or "").strip()
    requested_namespace = str(payload.namespace or "").strip()
    namespace = _canonicalize_control_plane_ledger_id(requested_namespace or requested_ledger_id or ledger_id) or ledger_id
    canonical_subject = str(payload.canonical_subject or "").strip() or _stable_ledger_did(request=request, ledger_id=ledger_id)
    canonical_subject_source = str(payload.canonical_subject_source or "").strip() or "did:web:ledger"
    metadata = dict(payload.metadata or {})
    founding_name = str(payload.founding_constitution_name or "").strip()
    founding_personality = str(payload.founding_constitution_personality or "").strip()
    founding_purpose = str(payload.founding_constitution_purpose or "").strip()
    if founding_name or founding_personality or founding_purpose:
        founding_name = founding_name or (payload.name or ledger_id).strip() or ledger_id
        existing_constitution = metadata.get("founding_constitution") if isinstance(metadata.get("founding_constitution"), dict) else {}
        metadata["founding_constitution"] = {
            **existing_constitution,
            **({"name": founding_name} if founding_name else {}),
            **({"personality": founding_personality} if founding_personality else {}),
            **({"purpose": founding_purpose} if founding_purpose else {}),
            "source": "control_plane_operator",
        }
    alias_candidates = _unique_string_list(
        [
            metadata.get("display_alias"),
            requested_ledger_id if requested_ledger_id != ledger_id else None,
            requested_namespace if requested_namespace and requested_namespace != namespace else None,
            _prefixed_control_plane_ledger_alias(ledger_id),
        ]
    )
    if alias_candidates:
        metadata["ledger_alias_history"] = _unique_string_list(
            [
                *(
                    metadata.get("ledger_alias_history")
                    if isinstance(metadata.get("ledger_alias_history"), list)
                    else []
                ),
                *alias_candidates,
            ]
        )
    metadata.setdefault("provisioning_state", "pending_provisioning")
    metadata = _apply_ledger_memory_tier_metadata(metadata)
    return {
        "ledger_id": ledger_id,
        "display_name": (payload.name or ledger_id).strip() or ledger_id,
        "namespace": namespace,
        "tenant_id": tenant_id,
        "owner_principal_id": owner_principal_id,
        "owner_principal_type": owner_principal_type,
        "policy_profile": policy_profile,
        "status": _validate_status(str(payload.status or "active").strip().lower() or "active", allowed=_ENTITY_ALLOWED_STATUSES, field_name="status"),
        "canonical_subject": canonical_subject,
        "canonical_subject_source": canonical_subject_source,
        "created_at": timestamp,
        "updated_at": timestamp,
        "metadata": metadata,
        "provisioning_source": str(payload.provisioning_source or "admin_api_v1").strip() or "admin_api_v1",
        "created_by_principal_id": mutation["created_by_principal_id"],
        "last_changed_by_principal_id": mutation["last_changed_by_principal_id"],
        "submission_ref": mutation["submission_ref"],
    }


def _load_registered_ledgers_v1(db) -> dict[str, dict[str, Any]]:
    raw = db.get(LEDGER_REGISTRY_V1_KEY)
    registry: dict[str, dict[str, Any]] = {}
    if raw is not None:
        try:
            payload = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
            if isinstance(payload, dict):
                records = payload.get("ledgers", payload)
                if isinstance(records, dict):
                    for ledger_id, record in records.items():
                        raw_ledger_key = str(ledger_id).strip()
                        ledger_key = _canonicalize_control_plane_ledger_id(ledger_id)
                        if not ledger_key or not isinstance(record, dict):
                            continue
                        normalized = dict(record)
                        normalized["ledger_id"] = ledger_key
                        metadata = normalized.get("metadata") if isinstance(normalized.get("metadata"), dict) else {}
                        raw_ledger_id = str(record.get("ledger_id") or "").strip()
                        raw_namespace = str(record.get("namespace") or "").strip()
                        alias_candidates = _unique_string_list(
                            [
                                *(metadata.get("ledger_alias_history") if isinstance(metadata.get("ledger_alias_history"), list) else []),
                                raw_ledger_id if raw_ledger_id and raw_ledger_id != ledger_key else None,
                                raw_namespace if raw_namespace and raw_namespace != ledger_key else None,
                                _prefixed_control_plane_ledger_alias(ledger_key),
                            ]
                        )
                        if alias_candidates:
                            metadata = dict(metadata)
                            metadata["ledger_alias_history"] = alias_candidates
                            normalized["metadata"] = metadata
                        normalized["namespace"] = _canonicalize_control_plane_ledger_id(raw_namespace or ledger_key) or ledger_key
                        existing = registry.get(ledger_key)
                        if isinstance(existing, dict):
                            prefer_existing = str(existing.get("ledger_id") or "").strip() == ledger_key and raw_ledger_key != ledger_key
                            if prefer_existing:
                                existing_metadata = existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}
                                merged_metadata = dict(existing_metadata)
                                merged_metadata["ledger_alias_history"] = _unique_string_list(
                                    [
                                        *(existing_metadata.get("ledger_alias_history") if isinstance(existing_metadata.get("ledger_alias_history"), list) else []),
                                        *(metadata.get("ledger_alias_history") if isinstance(metadata, dict) and isinstance(metadata.get("ledger_alias_history"), list) else []),
                                    ]
                                )
                                existing["metadata"] = merged_metadata
                                registry[ledger_key] = existing
                                continue
                        registry[ledger_key] = normalized
        except Exception:
            registry = {}

    if registry:
        return registry

    # Backward-compatible migration path from legacy registry list.
    migrated: dict[str, dict[str, Any]] = {}
    for ledger_id in _load_registered_ledgers(db):
        canonical_ledger_id = _canonicalize_control_plane_ledger_id(ledger_id)
        migrated[str(canonical_ledger_id)] = {
            "ledger_id": str(canonical_ledger_id),
            "display_name": str(ledger_id),
            "namespace": str(canonical_ledger_id),
            "tenant_id": "tenant:legacy",
            "owner_principal_id": "legacy",
            "owner_principal_type": "legacy",
            "policy_profile": "legacy",
            "status": "active",
            "canonical_subject": _stable_ledger_did(request=None, ledger_id=str(canonical_ledger_id)),
            "canonical_subject_source": "did:web:ledger",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "metadata": _apply_ledger_memory_tier_metadata(
                {
                    "ledger_alias_history": _unique_string_list(
                        [str(ledger_id)] if str(ledger_id) != str(canonical_ledger_id) else []
                    )
                }
            ),
            "provisioning_source": "legacy_registry_migration",
        }
    return migrated


def _persist_registered_ledgers_v1(db, registry: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for ledger_id in sorted(registry.keys()):
        record = registry.get(ledger_id)
        if not isinstance(record, dict):
            continue
        ledger_key = _canonicalize_control_plane_ledger_id(ledger_id)
        normalized = dict(record)
        normalized["ledger_id"] = ledger_key
        normalized["namespace"] = _canonicalize_control_plane_ledger_id(normalized.get("namespace") or ledger_key) or ledger_key
        normalized["canonical_subject"] = str(normalized.get("canonical_subject") or _stable_ledger_did(request=None, ledger_id=ledger_key)).strip()
        normalized["canonical_subject_source"] = str(normalized.get("canonical_subject_source") or "did:web:ledger").strip() or "did:web:ledger"
        metadata = normalized.get("metadata") if isinstance(normalized.get("metadata"), dict) else {}
        metadata = _apply_ledger_memory_tier_metadata(metadata)
        metadata["ledger_alias_history"] = _unique_string_list(
            [
                *(metadata.get("ledger_alias_history") if isinstance(metadata.get("ledger_alias_history"), list) else []),
                str(record.get("ledger_id") or "").strip() if str(record.get("ledger_id") or "").strip() != ledger_key else None,
                str(record.get("namespace") or "").strip() if str(record.get("namespace") or "").strip() not in {ledger_key, normalized["namespace"]} else None,
                _prefixed_control_plane_ledger_alias(ledger_key),
            ]
        )
        normalized["metadata"] = metadata
        existing = canonical.get(ledger_key)
        if isinstance(existing, dict):
            merged = dict(existing)
            merged_metadata = dict(existing.get("metadata") or {}) if isinstance(existing.get("metadata"), dict) else {}
            merged_metadata["ledger_alias_history"] = _unique_string_list(
                [
                    *(merged_metadata.get("ledger_alias_history") if isinstance(merged_metadata.get("ledger_alias_history"), list) else []),
                    *(metadata.get("ledger_alias_history") if isinstance(metadata.get("ledger_alias_history"), list) else []),
                ]
            )
            merged["metadata"] = _apply_ledger_memory_tier_metadata(merged_metadata)
            if not str(merged.get("display_name") or "").strip() and str(normalized.get("display_name") or "").strip():
                merged["display_name"] = normalized["display_name"]
            canonical[ledger_key] = merged
            continue
        canonical[ledger_key] = normalized
    db[LEDGER_REGISTRY_V1_KEY] = json.dumps(
        {"version": 1, "ledgers": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def _load_control_plane_surfaces_v1(db) -> dict[str, dict[str, Any]]:
    raw = db.get(SURFACE_REGISTRY_V1_KEY)
    registry: dict[str, dict[str, Any]] = {}
    if raw is None:
        return registry
    try:
        payload = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
    except Exception:
        return registry
    records = payload.get("surfaces", payload) if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return registry
    for surface_id, record in records.items():
        surface_key = str(surface_id).strip()
        if not surface_key or not isinstance(record, dict):
            continue
        normalized = dict(record)
        normalized["surface_id"] = surface_key
        registry[surface_key] = normalized
    return registry


def _is_legacy_canonical_subject(subject: Any) -> bool:
    value = str(subject or "").strip()
    return value.startswith("did:web:legacy.local:")


def _normalized_canonical_subject(
    *,
    request: Request | None,
    entity_type: str,
    entity_id: str,
    canonical_subject: Any,
) -> str:
    candidate = str(canonical_subject or "").strip()
    if candidate and not _is_legacy_canonical_subject(candidate):
        return candidate
    return _canonical_entity_subject(request=request, entity_type=entity_type, entity_id=entity_id)


def _persist_control_plane_surfaces_v1(db, registry: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for surface_id in sorted(registry.keys()):
        record = registry.get(surface_id)
        if not isinstance(record, dict):
            continue
        normalized = dict(record)
        normalized["surface_id"] = surface_id
        metadata = dict(normalized.get("metadata") or {})
        canonical_subject = _normalized_canonical_subject(
            request=None,
            entity_type="surface",
            entity_id=surface_id,
            canonical_subject=normalized.get("canonical_subject") or metadata.get("canonical_subject"),
        )
        normalized["canonical_subject"] = canonical_subject
        normalized["canonical_subject_source"] = str(normalized.get("canonical_subject_source") or metadata.get("canonical_subject_source") or "did:web:surface").strip() or "did:web:surface"
        metadata["canonical_subject"] = canonical_subject
        metadata["canonical_subject_source"] = normalized["canonical_subject_source"]
        normalized["metadata"] = metadata
        canonical[surface_id] = normalized
    db[SURFACE_REGISTRY_V1_KEY] = json.dumps(
        {"version": 1, "surfaces": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def _load_control_plane_relationships_v1(db) -> dict[str, dict[str, Any]]:
    raw = db.get(RELATIONSHIP_REGISTRY_V1_KEY)
    registry: dict[str, dict[str, Any]] = {}
    if raw is None:
        return registry
    try:
        payload = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
    except Exception:
        return registry
    records = payload.get("relationships", payload) if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return registry
    for relationship_id, record in records.items():
        relationship_key = str(relationship_id).strip()
        if not relationship_key or not isinstance(record, dict):
            continue
        normalized = dict(record)
        normalized["relationship_id"] = relationship_key
        registry[relationship_key] = normalized
    return registry


def _persist_control_plane_relationships_v1(db, registry: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for relationship_id in sorted(registry.keys()):
        record = registry.get(relationship_id)
        if not isinstance(record, dict):
            continue
        normalized = dict(record)
        normalized_relationship_id = _control_plane_relationship_id(
            subject_entity_type=str(normalized.get("subject_entity_type") or ""),
            subject_entity_id=(
                _normalize_related_ledger_id(normalized.get("subject_entity_id"))
                if str(normalized.get("subject_entity_type") or "").strip().lower() == "ledger"
                else str(normalized.get("subject_entity_id") or "")
            ),
            object_entity_type=str(normalized.get("object_entity_type") or ""),
            object_entity_id=(
                _normalize_related_ledger_id(normalized.get("object_entity_id"))
                if str(normalized.get("object_entity_type") or "").strip().lower() == "ledger"
                else str(normalized.get("object_entity_id") or "")
            ),
        )
        normalized["relationship_id"] = normalized_relationship_id
        normalized["subject_entity_id"] = (
            _normalize_related_ledger_id(normalized.get("subject_entity_id"))
            if str(normalized.get("subject_entity_type") or "").strip().lower() == "ledger"
            else str(normalized.get("subject_entity_id") or "").strip()
        )
        normalized["object_entity_id"] = (
            _normalize_related_ledger_id(normalized.get("object_entity_id"))
            if str(normalized.get("object_entity_type") or "").strip().lower() == "ledger"
            else str(normalized.get("object_entity_id") or "").strip()
        )
        normalized["canonical_subject"] = _normalized_canonical_subject(
            request=None,
            entity_type="relationship",
            entity_id=normalized_relationship_id,
            canonical_subject=normalized.get("canonical_subject"),
        )
        normalized["canonical_subject_source"] = str(normalized.get("canonical_subject_source") or "did:web:relationship").strip() or "did:web:relationship"
        canonical[normalized_relationship_id] = normalized
    db[RELATIONSHIP_REGISTRY_V1_KEY] = json.dumps(
        {"version": 1, "relationships": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def _control_plane_relationship_id(
    *,
    subject_entity_type: str,
    subject_entity_id: str,
    object_entity_type: str,
    object_entity_id: str,
) -> str:
    return "::".join(
        [
            str(subject_entity_type or "").strip().lower(),
            str(subject_entity_id or "").strip(),
            str(object_entity_type or "").strip().lower(),
            str(object_entity_id or "").strip(),
        ]
    )


def _derived_relationship_record(
    *,
    subject_entity_type: str,
    subject_entity_id: str,
    object_entity_type: str,
    object_entity_id: str,
    relationship_type: str,
    permission_scope: str = "full",
    permission_payload: dict[str, Any] | None = None,
    ledger_id: str | None = None,
    evidence_ref: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    relationship_id = _control_plane_relationship_id(
        subject_entity_type=subject_entity_type,
        subject_entity_id=subject_entity_id,
        object_entity_type=object_entity_type,
        object_entity_id=object_entity_id,
    )
    return {
        "relationship_id": relationship_id,
        "subject_entity_type": str(subject_entity_type or "").strip().lower(),
        "subject_entity_id": str(subject_entity_id or "").strip(),
        "object_entity_type": str(object_entity_type or "").strip().lower(),
        "object_entity_id": str(object_entity_id or "").strip(),
        "relationship_type": str(relationship_type or "related_to").strip().lower() or "related_to",
        "status": "active",
        "enabled_state": "enabled",
        "permission_scope": str(permission_scope or "full").strip().lower() or "full",
        "permission_payload": dict(permission_payload or {}),
        "ledger_id": str(ledger_id or "").strip() or None,
        "start_at": None,
        "end_at": None,
        "evidence_ref": str(evidence_ref or "").strip() or None,
        "provenance_ref": None,
        "metadata": dict(metadata or {}),
        "created_at": None,
        "updated_at": None,
        "submitted_by": None,
        "created_by_principal_id": None,
        "last_changed_by_principal_id": None,
        "submission_ref": None,
    }


def _relationship_effective_enabled(
    record: dict[str, Any], today_iso: str | None = None
) -> bool:
    """Return True when a relationship is enabled and within its date window."""
    if today_iso is None:
        today_iso = datetime.now(timezone.utc).date().isoformat()
    enabled = str(record.get("enabled_state") or "enabled").strip().lower() != "disabled"
    if not enabled:
        return False
    start = str(record.get("start_at") or record.get("start_date") or "").strip()
    end = str(record.get("end_at") or record.get("end_date") or "").strip()
    if start and start > today_iso:
        return False
    if end and end < today_iso:
        return False
    return True


def _effective_control_plane_relationships(db) -> dict[str, dict[str, Any]]:
    explicit = _load_control_plane_relationships_v1(db)
    effective: dict[str, dict[str, Any]] = {
        relationship_id: dict(record)
        for relationship_id, record in explicit.items()
        if isinstance(record, dict)
    }
    surfaces = _load_control_plane_surfaces_v1(db)
    bindings = _load_model_bindings_v1(db)

    for surface_id, surface in surfaces.items():
        if not isinstance(surface, dict):
            continue
        ledger_id = str(surface.get("ledger_id") or "").strip()
        principal_did = str(surface.get("principal_did") or "").strip()
        surface_status = str(surface.get("status") or "").strip().lower()
        if surface_status in {"disabled", "expired", "retired"}:
            continue

        if ledger_id:
            record = _derived_relationship_record(
                subject_entity_type="surface",
                subject_entity_id=surface_id,
                object_entity_type="ledger",
                object_entity_id=ledger_id,
                relationship_type="surface_bound_to_ledger",
                ledger_id=ledger_id,
                metadata={"derived": True, "derivation_source": "surface_registry_v1"},
            )
            effective.setdefault(record["relationship_id"], record)

        if principal_did:
            record = _derived_relationship_record(
                subject_entity_type="principal",
                subject_entity_id=principal_did,
                object_entity_type="surface",
                object_entity_id=surface_id,
                relationship_type="can_access_surface",
                ledger_id=ledger_id,
                metadata={"derived": True, "derivation_source": "surface_registry_v1"},
            )
            effective.setdefault(record["relationship_id"], record)

    for binding_id, binding in bindings.items():
        if not isinstance(binding, dict):
            continue
        linked_model_principal = str(binding.get("linked_model_principal") or "").strip()
        if not linked_model_principal:
            continue
        binding_status = str(binding.get("status") or "").strip().lower()
        if binding_status in {"disabled", "expired", "retired"}:
            continue
        app_surfaces = [
            str(item).strip()
            for item in (binding.get("app_surfaces") or [])
            if str(item).strip()
        ]
        for surface_id in app_surfaces:
            surface = surfaces.get(surface_id) if isinstance(surfaces.get(surface_id), dict) else {}
            ledger_id = str(surface.get("ledger_id") or "").strip()
            admin_principal = str(surface.get("principal_did") or "").strip()

            model_surface = _derived_relationship_record(
                subject_entity_type="principal",
                subject_entity_id=linked_model_principal,
                object_entity_type="surface",
                object_entity_id=surface_id,
                relationship_type="can_access_surface",
                ledger_id=ledger_id,
                metadata={"derived": True, "derivation_source": "model_binding_registry_v1", "binding_id": binding_id},
            )
            effective.setdefault(model_surface["relationship_id"], model_surface)

            if ledger_id:
                model_ledger = _derived_relationship_record(
                    subject_entity_type="principal",
                    subject_entity_id=linked_model_principal,
                    object_entity_type="ledger",
                    object_entity_id=ledger_id,
                    relationship_type="writes_to_ledger",
                    ledger_id=ledger_id,
                    metadata={"derived": True, "derivation_source": "model_binding_registry_v1", "binding_id": binding_id, "surface_id": surface_id},
                )
                effective.setdefault(model_ledger["relationship_id"], model_ledger)

            if admin_principal:
                model_admin = _derived_relationship_record(
                    subject_entity_type="principal",
                    subject_entity_id=linked_model_principal,
                    object_entity_type="principal",
                    object_entity_id=admin_principal,
                    relationship_type="administered_by",
                    ledger_id=ledger_id,
                    metadata={"derived": True, "derivation_source": "surface_and_binding_registry_v1", "binding_id": binding_id, "surface_id": surface_id},
                )
                effective.setdefault(model_admin["relationship_id"], model_admin)

    ledgers = _load_registered_ledgers_v1(db)
    principals = _load_registered_principals_v1(db)
    placeholder_owners = {"", "legacy", "anonymous"}

    for ledger_id, ledger in ledgers.items():
        if not isinstance(ledger, dict):
            continue
        ledger_status = str(ledger.get("status") or "").strip().lower()
        if ledger_status in {"disabled", "expired", "retired", "superseded"}:
            continue
        owner = str(ledger.get("owner_principal_id") or "").strip()
        created_by = str(ledger.get("created_by_principal_id") or "").strip()
        for principal_id in {owner, created_by}:
            if not principal_id or principal_id in placeholder_owners:
                continue
            record = _derived_relationship_record(
                subject_entity_type="principal",
                subject_entity_id=principal_id,
                object_entity_type="ledger",
                object_entity_id=ledger_id,
                relationship_type="member_of_ledger",
                ledger_id=ledger_id,
                metadata={"derived": True, "derivation_source": "ledger_registry_v1:owner"},
            )
            effective.setdefault(record["relationship_id"], record)

    for principal_did, principal in principals.items():
        if not isinstance(principal, dict):
            continue
        principal_status = str(principal.get("status") or "").strip().lower()
        if principal_status in {"disabled", "expired", "retired"}:
            continue
        metadata = principal.get("metadata") if isinstance(principal.get("metadata"), dict) else {}
        ledger_id = str(metadata.get("ledger_id") or "").strip()
        if not ledger_id:
            continue
        record = _derived_relationship_record(
            subject_entity_type="principal",
            subject_entity_id=principal_did,
            object_entity_type="ledger",
            object_entity_id=ledger_id,
            relationship_type="member_of_ledger",
            ledger_id=ledger_id,
            metadata={"derived": True, "derivation_source": "principal_registry_v1:metadata"},
        )
        effective.setdefault(record["relationship_id"], record)

    today_iso = datetime.now(timezone.utc).date().isoformat()
    for record in effective.values():
        if not _relationship_effective_enabled(record, today_iso):
            record["enabled_state"] = "disabled"

    return effective


def _load_provider_credentials_v1(db) -> dict[str, dict[str, Any]]:
    raw = db.get(PROVIDER_CREDENTIAL_REGISTRY_V1_KEY)
    registry: dict[str, dict[str, Any]] = {}
    if raw is None:
        return registry
    try:
        payload = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
    except Exception:
        return registry
    records = payload.get("providers", payload) if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return registry
    for provider_id, record in records.items():
        provider_key = str(provider_id).strip()
        if not provider_key or not isinstance(record, dict):
            continue
        normalized = dict(record)
        normalized["provider_id"] = provider_key
        registry[provider_key] = normalized
    return registry


def _persist_provider_credentials_v1(db, registry: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for provider_id in sorted(registry.keys()):
        record = registry.get(provider_id)
        if not isinstance(record, dict):
            continue
        normalized = dict(record)
        normalized["provider_id"] = provider_id
        normalized["canonical_subject"] = _normalized_canonical_subject(
            request=None,
            entity_type="provider",
            entity_id=provider_id,
            canonical_subject=normalized.get("canonical_subject"),
        )
        normalized["canonical_subject_source"] = str(normalized.get("canonical_subject_source") or "did:web:provider").strip() or "did:web:provider"
        canonical[provider_id] = normalized
    db[PROVIDER_CREDENTIAL_REGISTRY_V1_KEY] = json.dumps(
        {"version": 1, "providers": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def _load_model_bindings_v1(db) -> dict[str, dict[str, Any]]:
    raw = db.get(MODEL_BINDING_REGISTRY_V1_KEY)
    registry: dict[str, dict[str, Any]] = {}
    if raw is None:
        return registry
    try:
        payload = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
    except Exception:
        return registry
    records = payload.get("model_bindings", payload) if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return registry
    for binding_id, record in records.items():
        binding_key = str(binding_id).strip()
        if not binding_key or not isinstance(record, dict):
            continue
        normalized = dict(record)
        normalized["binding_id"] = binding_key
        registry[binding_key] = normalized
    return registry


def _persist_model_bindings_v1(db, registry: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for binding_id in sorted(registry.keys()):
        record = registry.get(binding_id)
        if not isinstance(record, dict):
            continue
        normalized = dict(record)
        normalized["binding_id"] = binding_id
        normalized["canonical_subject"] = _normalized_canonical_subject(
            request=None,
            entity_type="binding",
            entity_id=binding_id,
            canonical_subject=normalized.get("canonical_subject"),
        )
        normalized["canonical_subject_source"] = str(normalized.get("canonical_subject_source") or "did:web:binding").strip() or "did:web:binding"
        canonical[binding_id] = normalized
    db[MODEL_BINDING_REGISTRY_V1_KEY] = json.dumps(
        {"version": 1, "model_bindings": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def _load_registered_tenants_v1(db) -> dict[str, dict[str, Any]]:
    raw = db.get(TENANT_REGISTRY_V1_KEY)
    registry: dict[str, dict[str, Any]] = {}
    if raw is None:
        return registry
    try:
        payload = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
    except Exception:
        return registry
    records = payload.get("tenants", payload) if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return registry
    for tenant_id, record in records.items():
        tenant_key = str(tenant_id).strip()
        if not tenant_key or not isinstance(record, dict):
            continue
        normalized = dict(record)
        normalized["tenant_id"] = tenant_key
        registry[tenant_key] = normalized
    return registry


def _persist_registered_tenants_v1(db, registry: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for tenant_id in sorted(registry.keys()):
        record = registry.get(tenant_id)
        if not isinstance(record, dict):
            continue
        normalized = dict(record)
        normalized["tenant_id"] = tenant_id
        canonical[tenant_id] = normalized
    db[TENANT_REGISTRY_V1_KEY] = json.dumps(
        {"version": 1, "tenants": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def _load_registered_principals_v1(db) -> dict[str, dict[str, Any]]:
    raw = db.get(PRINCIPAL_REGISTRY_V1_KEY)
    registry: dict[str, dict[str, Any]] = {}
    if raw is None:
        return registry
    try:
        payload = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
    except Exception:
        return registry
    records = payload.get("principals", payload) if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return registry
    for principal_did, record in records.items():
        principal_key = str(principal_did).strip()
        if not principal_key or not isinstance(record, dict):
            continue
        normalized = dict(record)
        normalized["principal_did"] = principal_key
        metadata = normalized.get("metadata") if isinstance(normalized.get("metadata"), dict) else {}
        if metadata.get("ledger_id") is not None:
            metadata = dict(metadata)
            metadata["ledger_id"] = _normalize_related_ledger_id(metadata.get("ledger_id"))
            normalized["metadata"] = metadata
        registry[principal_key] = normalized
    return registry


def _persist_registered_principals_v1(db, registry: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for principal_did in sorted(registry.keys()):
        record = registry.get(principal_did)
        if not isinstance(record, dict):
            continue
        normalized = dict(record)
        normalized["principal_did"] = principal_did
        normalized["tenant_id"] = _normalize_principal_tenant_id(normalized.get("tenant_id"))
        principal_key_refs = _normalize_key_references(
            normalized.get("principal_key_refs")
            if isinstance(normalized.get("principal_key_refs"), list)
            else normalized.get("key_references")
        )
        normalized["principal_key_refs"] = principal_key_refs
        normalized["key_references"] = list(principal_key_refs)
        metadata = _normalize_principal_metadata(normalized.get("metadata"))
        if metadata.get("ledger_id") is not None:
            metadata["ledger_id"] = _normalize_related_ledger_id(metadata.get("ledger_id"))
        provider_type = str(metadata.get("provider_type") or normalized.get("provider_type") or "").strip()
        model_id = str(metadata.get("model_id") or normalized.get("model_id") or "").strip()
        if provider_type and model_id:
            stable_principal = _stable_model_principal_did(request=None, provider_type=provider_type, model_id=model_id)
            normalized["principal_did"] = stable_principal
            principal_did = stable_principal
            normalized["canonical_subject"] = stable_principal
            normalized["canonical_subject_source"] = "did:web:model-principal"
            metadata["provider_type"] = provider_type
            metadata["model_id"] = model_id
        else:
            normalized["canonical_subject"] = str(normalized.get("canonical_subject") or principal_did).strip() or principal_did
            normalized["canonical_subject_source"] = (
                str(normalized.get("canonical_subject_source") or "principal_did").strip() or "principal_did"
            )
        normalized["metadata"] = metadata
        normalized["actor_type"] = metadata.get("actor_type") if isinstance(metadata, dict) else None
        if isinstance(normalized["metadata"], dict):
            normalized["metadata"].setdefault("probation_status", "probation")
            normalized["metadata"].setdefault("probation_reason", "fresh_subject_created")
        standing_view = normalized.get("standing_view")
        if isinstance(standing_view, dict):
            merged_standing = _default_principal_standing_view()
            merged_standing.update(standing_view)
            normalized["standing_view"] = merged_standing
        else:
            normalized["standing_view"] = _default_principal_standing_view()
        canonical[principal_did] = normalized
    db[PRINCIPAL_REGISTRY_V1_KEY] = json.dumps(
        {"version": 1, "principals": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def _load_pilot_signups_v1(db) -> dict[str, dict[str, Any]]:
    raw = db.get(PILOT_SIGNUPS_V1_KEY)
    if raw is None:
        return {}
    try:
        payload = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
    except Exception:
        return {}
    records = payload.get("signups") if isinstance(payload, dict) else None
    if not isinstance(records, dict):
        return {}
    return {str(k): dict(v) for k, v in records.items() if isinstance(v, dict)}


def _persist_pilot_signups_v1(db, records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    canonical: dict[str, dict[str, Any]] = {}
    for key in sorted(records.keys()):
        record = records.get(key)
        if isinstance(record, dict):
            canonical[key] = dict(record)
    db[PILOT_SIGNUPS_V1_KEY] = json.dumps(
        {"version": 1, "signups": canonical},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return canonical


def _build_tenant_record(*, request: Request, tenant_id: str, payload: TenantCreateRequest) -> dict[str, Any]:
    principal = principal_from_request(request)
    owner_principal_id = (payload.owner_principal_id or principal.principal_id).strip() or "anonymous"
    owner_principal_type = (payload.owner_principal_type or principal.principal_type).strip() or "service"
    timestamp = _now_iso()
    return {
        "tenant_id": tenant_id,
        "display_name": (payload.name or tenant_id).strip(),
        "owner_principal_id": owner_principal_id,
        "owner_principal_type": owner_principal_type,
        "policy_profile": (payload.policy_profile or "standard").strip() or "standard",
        "status": "active",
        "created_at": timestamp,
        "updated_at": timestamp,
        "metadata": dict(payload.metadata or {}),
        "provisioning_source": "admin_tenant_api_v1",
    }


def _normalize_key_references(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        key = _normalize_principal_key_reference(item)
        if not key or key in seen:
            continue
        out.append(key)
        seen.add(key)
    return out


def _normalize_principal_tenant_id(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "tenant:unknown"
    if raw.startswith("tenant:"):
        return raw
    return f"tenant:{raw}"


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


def _normalize_principal_key_reference(value: Any) -> str:
    ref = str(value or "").strip()
    if not ref:
        return ""
    prefix, sep, suffix = ref.partition(":")
    if not sep:
        raise HTTPException(status_code=422, detail="principal_key_ref must be namespaced")
    namespace = prefix.strip().lower()
    if namespace == "did":
        body = suffix.strip()
        if not body:
            raise HTTPException(status_code=422, detail="principal_key_ref body is required")
        return f"did:{body}"
    lowered = ref.lower()
    for known_prefix in (
        "github:user:",
        "openai:agent:",
        "openrouter:model:",
        "openrouter:provider:",
        "ollama:model:",
        "mcp:server:",
        "node:key:",
    ):
        if lowered.startswith(known_prefix):
            normalized_body = ref[len(known_prefix):].strip().lower()
            if not normalized_body:
                raise HTTPException(status_code=422, detail="principal_key_ref body is required")
            return f"{known_prefix}{normalized_body}"
    if lowered.startswith("node:url:") or lowered.startswith("service:url:"):
        url_prefix = "node:url:" if lowered.startswith("node:url:") else "service:url:"
        raw_url = ref[len(url_prefix):].strip()
        parsed = urlsplit(raw_url)
        if not parsed.scheme or not parsed.netloc:
            raise HTTPException(status_code=422, detail=f"{url_prefix[:-1]} binding must include absolute URL")
        path = parsed.path.rstrip("/")
        normalized_url = urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, "", ""))
        return f"{url_prefix}{normalized_url}"
    if lowered.startswith("wallet:"):
        normalized_body = ref[len("wallet:"):].strip().lower()
        if not normalized_body:
            raise HTTPException(status_code=422, detail="principal_key_ref body is required")
        return f"wallet:{normalized_body}"
    raise HTTPException(status_code=422, detail="unsupported principal_key_ref namespace")


_CANONICAL_ACTOR_TYPES = {"human", "model", "agent", "service", "organisation", "device"}
_ACTOR_TYPE_ALIASES = {
    "application": "service",
    "node": "agent",
    "organization": "organisation",
}


def _merge_principal_actor_type(raw: Any, actor_type: str | None) -> dict[str, Any]:
    metadata = dict(raw or {}) if isinstance(raw, dict) else {}
    explicit_actor_type = str(actor_type or "").strip()
    if explicit_actor_type:
        metadata["actor_type"] = explicit_actor_type
    return metadata


def _normalize_principal_metadata(raw: Any) -> dict[str, Any]:
    metadata = dict(raw or {}) if isinstance(raw, dict) else {}
    actor_type = str(metadata.get("actor_type") or "").strip().lower()
    if actor_type:
        actor_type = _ACTOR_TYPE_ALIASES.get(actor_type, actor_type)
        if actor_type not in _CANONICAL_ACTOR_TYPES:
            raise HTTPException(
                status_code=422,
                detail="metadata.actor_type must be one of human, model, agent, service, organisation, device",
            )
        metadata["actor_type"] = actor_type
    vc_status = str(metadata.get("vc_status") or "").strip().lower()
    if vc_status:
        if vc_status not in {"none", "bound", "verified", "revoked", "expired"}:
            raise HTTPException(
                status_code=422,
                detail="metadata.vc_status must be one of none, bound, verified, revoked, expired",
            )
        metadata["vc_status"] = vc_status
    if "wallet_capable" in metadata:
        metadata["wallet_capable"] = bool(metadata.get("wallet_capable"))
    email_normalized = _normalize_email(metadata.get("email"))
    if email_normalized:
        metadata["email_normalized"] = email_normalized
    phone_normalized = _normalize_phone(metadata.get("phone"))
    if phone_normalized:
        metadata["phone_normalized"] = phone_normalized
    return metadata


def _default_principal_standing_view() -> dict[str, Any]:
    return {
        "authority_subject_id": None,
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
        "evidence_manifest_ref": None,
        "evidence_manifest_hash": None,
        "subject_transition_event_ref": None,
        "current_validation_status": "active",
        "current_invalidation_reasons": [],
        "principal_did": None,
        "canonical_subject": None,
        "updated_at": None,
    }


def _resolve_principal_for_authority_surface(
    registry: dict[str, dict[str, Any]],
    *,
    principal_did: str | None = None,
    canonical_subject: str | None = None,
) -> str | None:
    principal_key = str(principal_did or "").strip()
    if principal_key and isinstance(registry.get(principal_key), dict):
        return principal_key

    canonical_key = str(canonical_subject or "").strip()
    if not canonical_key:
        return None

    matches = [
        did
        for did, row in registry.items()
        if isinstance(row, dict) and str(row.get("canonical_subject") or "").strip() == canonical_key
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _materialize_principal_standing_view(
    record: dict[str, Any],
    *,
    authority_subject_id: str | None = None,
    standing_view: dict[str, Any] | None = None,
    subject_event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    updated = dict(record)
    metadata = updated.get("metadata") if isinstance(updated.get("metadata"), dict) else {}
    merged_metadata = dict(metadata)
    existing_view = updated.get("standing_view") if isinstance(updated.get("standing_view"), dict) else {}
    merged_view = _default_principal_standing_view()
    if isinstance(existing_view, dict):
        merged_view.update(existing_view)

    normalized_subject_id = str(authority_subject_id or "").strip()
    if normalized_subject_id:
        merged_view["authority_subject_id"] = normalized_subject_id

    if isinstance(subject_event, dict):
        event_id = str(subject_event.get("event_id") or "").strip()
        event_created_at = str(subject_event.get("created_at") or "").strip() or None
        subject_canonical = str(subject_event.get("canonical_subject") or "").strip() or None
        subject_principal = str(subject_event.get("principal_did") or "").strip() or None
        if event_id:
            merged_view["subject_transition_event_ref"] = event_id
        if subject_canonical:
            merged_view["canonical_subject"] = subject_canonical
        if subject_principal:
            merged_view["principal_did"] = subject_principal
        if event_created_at:
            merged_view["updated_at"] = event_created_at

    if isinstance(standing_view, dict):
        for key in (
            "authority_subject_id",
            "trust_class",
            "posture_class",
            "operator_profile",
            "probation_status",
            "active_sanctions",
            "last_event_id",
            "last_event_type",
            "last_reason_code",
            "credential_ref",
            "standing_envelope_ref",
            "evidence_manifest_ref",
            "evidence_manifest_hash",
            "subject_transition_event_ref",
            "current_validation_status",
            "current_invalidation_reasons",
            "principal_did",
            "canonical_subject",
            "updated_at",
        ):
            if key in standing_view:
                merged_view[key] = standing_view.get(key)

    probation_status = str(merged_view.get("probation_status") or "").strip()
    if probation_status:
        merged_metadata["probation_status"] = probation_status
    else:
        merged_metadata.pop("probation_status", None)

    updated["metadata"] = merged_metadata
    updated["standing_view"] = merged_view
    updated["updated_at"] = str(merged_view.get("updated_at") or _now_iso()).strip()
    return updated


def _sync_principal_from_subject_event(
    db,
    registry: dict[str, dict[str, Any]],
    *,
    authority_subject_id: str,
    subject_event: dict[str, Any],
) -> None:
    principal_key = _resolve_principal_for_authority_surface(
        registry,
        principal_did=subject_event.get("principal_did"),
        canonical_subject=subject_event.get("canonical_subject"),
    )
    if not principal_key:
        return
    registry[principal_key] = _materialize_principal_standing_view(
        registry[principal_key],
        authority_subject_id=authority_subject_id,
        subject_event=subject_event,
    )


def _sync_principal_from_authority_state(
    db,
    registry: dict[str, dict[str, Any]],
    *,
    authority_subject_id: str,
    standing_view: dict[str, Any],
) -> None:
    authority_subject = load_authority_subjects(db).get(str(authority_subject_id or "").strip())
    subject_row = authority_subject if isinstance(authority_subject, dict) else {}
    principal_key = _resolve_principal_for_authority_surface(
        registry,
        principal_did=standing_view.get("principal_did") or subject_row.get("principal_did"),
        canonical_subject=standing_view.get("canonical_subject") or subject_row.get("canonical_subject"),
    )
    if not principal_key:
        return
    registry[principal_key] = _materialize_principal_standing_view(
        registry[principal_key],
        authority_subject_id=authority_subject_id,
        standing_view=standing_view,
    )


def _authority_timeline_subject_events(db, *, authority_subject_id: str) -> list[dict[str, Any]]:
    subject_id = str(authority_subject_id or "").strip()
    rows: list[dict[str, Any]] = []
    for event_id in sorted(load_subject_events(db).keys()):
        row = load_subject_events(db).get(event_id)
        if not isinstance(row, dict):
            continue
        resulting = str(row.get("resulting_authority_subject_id") or "").strip()
        prior = str(row.get("prior_authority_subject_id") or "").strip()
        if subject_id not in {resulting, prior}:
            continue
        rows.append({
            "family": "subject",
            "event_id": str(row.get("event_id") or event_id).strip(),
            "event_type": str(row.get("event_type") or "").strip() or None,
            "created_at": str(row.get("created_at") or "").strip() or None,
            "issuer": str(row.get("issuer") or "").strip() or None,
            "principal_did": str(row.get("principal_did") or "").strip() or None,
            "canonical_subject": str(row.get("canonical_subject") or "").strip() or None,
            "prior_authority_subject_id": prior or None,
            "resulting_authority_subject_id": resulting or None,
            "evidence_refs": [str(item).strip() for item in (row.get("evidence_refs") or []) if str(item).strip()],
        })
    return rows


def _authority_timeline_authority_events(db, *, authority_subject_id: str) -> list[dict[str, Any]]:
    subject_id = str(authority_subject_id or "").strip()
    rows: list[dict[str, Any]] = []
    for event_id in sorted(load_authority_events(db).keys()):
        row = load_authority_events(db).get(event_id)
        if not isinstance(row, dict):
            continue
        if str(row.get("authority_subject_id") or "").strip() != subject_id:
            continue
        rows.append({
            "family": "authority",
            "event_id": str(row.get("event_id") or event_id).strip(),
            "event_type": str(row.get("event_type") or "").strip() or None,
            "created_at": str(row.get("created_at") or "").strip() or None,
            "issuer": str(row.get("issuer") or "").strip() or None,
            "authority_subject_id": subject_id,
            "principal_did": str(row.get("principal_did") or "").strip() or None,
            "canonical_subject": str(row.get("canonical_subject") or "").strip() or None,
            "reason_code": str(row.get("reason_code") or "").strip() or None,
            "subject_transition_event_ref": str(row.get("subject_transition_event_ref") or "").strip() or None,
            "evidence_refs": [str(item).strip() for item in (row.get("evidence_refs") or []) if str(item).strip()],
            "current_validation_status": str(row.get("current_validation_status") or "").strip() or None,
        })
    return rows


def _build_unified_authority_view(db, *, authority_subject_id: str) -> dict[str, Any]:
    subject_id = str(authority_subject_id or "").strip()
    if not subject_id:
        raise HTTPException(status_code=400, detail="authority_subject_id is required")
    authority_subject = load_authority_subjects(db).get(subject_id)
    current_subject = dict(authority_subject) if isinstance(authority_subject, dict) else None
    current_standing = get_authority_state(db, subject_id)
    if not isinstance(current_standing, dict):
        replayed = replay_authority_state(db, authority_subject_id=subject_id)
        current_standing = replayed.get(subject_id) if isinstance(replayed.get(subject_id), dict) else None
    if not isinstance(current_subject, dict) and not isinstance(current_standing, dict):
        raise HTTPException(status_code=404, detail="authority subject not found")
    timeline = _authority_timeline_subject_events(db, authority_subject_id=subject_id) + _authority_timeline_authority_events(db, authority_subject_id=subject_id)
    timeline.sort(key=lambda row: (str(row.get("created_at") or ""), str(row.get("event_id") or "")))
    diagnostics = {
        "authority_subject_id": subject_id,
        "principal_did": str((current_standing or {}).get("principal_did") or (current_subject or {}).get("principal_did") or "").strip() or None,
        "canonical_subject": str((current_standing or {}).get("canonical_subject") or (current_subject or {}).get("canonical_subject") or "").strip() or None,
        "subject_event_count": len([row for row in timeline if row.get("family") == "subject"]),
        "authority_event_count": len([row for row in timeline if row.get("family") == "authority"]),
        "timeline_count": len(timeline),
        "last_subject_event_id": str((current_subject or {}).get("last_event_id") or "").strip() or None,
        "last_authority_event_id": str((current_standing or {}).get("last_event_id") or "").strip() or None,
        "current_validation_status": str((current_standing or {}).get("current_validation_status") or "").strip() or None,
        "current_invalidation_reasons": list((current_standing or {}).get("current_invalidation_reasons") or []),
        "active_sanctions_count": len(list((current_standing or {}).get("active_sanctions") or [])),
        "materialized_from_backend_replay": True,
    }
    return {
        "authority_subject": current_subject,
        "current_subject": current_subject,
        "current_standing": current_standing,
        "timeline": timeline,
        "diagnostics": diagnostics,
    }


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
            raise HTTPException(status_code=422, detail="canonical subject is ambiguous across bound identities")
        subject, source = next(iter(top_subjects))
        return subject, source
    return str(principal_did).strip(), "principal_did"


def _control_plane_public_base_url(request: Request | None = None) -> str:
    configured = str(os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if configured:
        return configured
    if request is not None:
        base = str(request.base_url or "").strip().rstrip("/")
        if base:
            return base
    return os.getenv("CONTROL_PLANE_PUBLIC_BASE_URL", "")


def _trust_anchor_public_config(request: Request | None = None) -> dict[str, str]:
    public_base_url = _control_plane_public_base_url(request)
    issuer_did = (os.getenv("TRUST_ANCHOR_ISSUER_DID") or os.getenv("DEFAULT_ISSUER_DID", "")).strip()
    did_document_url = f"{public_base_url}/.well-known/did.json" if public_base_url else ""
    organisation_name = (os.getenv("TRUST_ANCHOR_ORGANISATION_NAME") or "Dual Substrate").strip()
    organisation_uri = (os.getenv("TRUST_ANCHOR_ORGANISATION_URI") or os.getenv("DEFAULT_ORGANISATION_URI", "")).strip()
    organisation_registration_ref = (os.getenv("TRUST_ANCHOR_ORGANISATION_REGISTRATION_REF") or "").strip()
    return {
        "public_base_url": public_base_url,
        "issuer_did": issuer_did,
        "did_document_url": did_document_url,
        "organisation_name": organisation_name,
        "organisation_uri": organisation_uri,
        "organisation_registration_ref": organisation_registration_ref,
    }


def _trust_anchor_public_subject(config: dict[str, str]) -> dict[str, Any]:
    issuer_did = str(config.get("issuer_did") or "").strip()
    return {
        "id": issuer_did,
        "type": "IssuerAuthoritySubject",
        "issuer_did": issuer_did,
        "organisation_name": str(config.get("organisation_name") or "").strip() or None,
        "organisation_uri": str(config.get("organisation_uri") or "").strip() or None,
        "organisation_registration_ref": str(config.get("organisation_registration_ref") or "").strip() or None,
    }


def _parse_iso_utc(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _public_status_max_age_seconds() -> int:
    raw = str(os.getenv("PUBLIC_STATUS_MAX_AGE_SECONDS") or "").strip()
    try:
        parsed = int(raw)
    except ValueError:
        parsed = 86400
    return max(parsed, 60)


def _public_status_freshness(checked_at: str | None) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    checked_dt = _parse_iso_utc(checked_at)
    max_age_seconds = _public_status_max_age_seconds()
    expires_dt = checked_dt + timedelta(seconds=max_age_seconds) if checked_dt is not None else None
    is_fresh = bool(checked_dt is not None and expires_dt is not None and expires_dt >= now)
    return {
        "checked_at": checked_dt.isoformat().replace("+00:00", "Z") if checked_dt is not None else None,
        "expires_at": expires_dt.isoformat().replace("+00:00", "Z") if expires_dt is not None else None,
        "max_age_seconds": max_age_seconds,
        "is_fresh": is_fresh,
    }


def _public_status_invalidation(status_state: str | None, freshness: dict[str, Any]) -> dict[str, Any]:
    normalized_status = str(status_state or "").strip().lower() or "unverifiable"
    reasons: list[str] = []
    if normalized_status in {"suspended", "revoked", "unverifiable"}:
        reasons.append(f"credential_status_{normalized_status}")
    if freshness.get("checked_at") and not freshness.get("is_fresh"):
        reasons.append("status_stale")
    return {
        "is_invalidated": bool(reasons),
        "reasons": reasons,
        "status_state": normalized_status,
    }


def _select_trust_anchor_issuer_record(db: Any, config: dict[str, str]) -> dict[str, Any] | None:
    issuer_did = str(config.get("issuer_did") or "").strip()
    anchor_ref = str(config.get("did_document_url") or "").strip()
    selected: dict[str, Any] | None = None
    for issuer in sorted(load_issuer_authorities(db).keys()):
        row = load_issuer_authorities(db).get(issuer)
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "").strip().lower() != "active":
            continue
        if issuer_did and str(row.get("issuer_did") or "").strip() == issuer_did:
            return dict(row)
        if anchor_ref and str(row.get("identity_anchor_ref") or "").strip() == anchor_ref:
            selected = dict(row)
    return selected


def _build_public_credential_status_document(
    *,
    request: Request | None,
    config: dict[str, str],
    status_check: dict[str, Any],
    issuer_authority: dict[str, Any] | None = None,
) -> dict[str, Any]:
    public_base_url = str(config.get("public_base_url") or "").rstrip("/")
    issuer_did = str(config.get("issuer_did") or "").strip()
    credential_status_ref = str(status_check.get("credential_status_ref") or "").strip()
    freshness = _public_status_freshness(status_check.get("checked_at"))
    invalidation = _public_status_invalidation(status_check.get("status_state"), freshness)
    document_id = (
        f"{public_base_url}/api/trust-anchor/credential-status/{credential_status_ref}"
        if public_base_url and credential_status_ref
        else credential_status_ref
    )
    return {
        "id": document_id or None,
        "type": "DssCredentialStatus",
        "status_type": "CredentialStatusStatement",
        "format": "dss-public-credential-status-v1",
        "credential_family": "status",
        "issuer": {
            "id": issuer_did or str(status_check.get("issuer") or "").strip() or None,
            "type": "IssuerAuthority",
        },
        "subject": {
            "credential_id": str(status_check.get("credential_id") or "").strip() or None,
            "credential_status_ref": credential_status_ref or None,
        },
        "status": {
            "current": str(status_check.get("status_state") or "").strip().lower() or "unverifiable",
            "resolver_ref": str(status_check.get("resolver_ref") or "").strip() or None,
            "proof_ref": str(status_check.get("proof_ref") or "").strip() or None,
            "trust_root_ref": str(status_check.get("trust_root_ref") or "").strip() or None,
        },
        "freshness": freshness,
        "invalidation": invalidation,
        "authority_binding": {
            "issuer_ref": str(issuer_authority.get("issuer") or "").strip() or None if isinstance(issuer_authority, dict) else None,
            "authority_status_ref": f"{public_base_url}/.well-known/issuer-authority-status.json" if public_base_url else None,
        },
        "updated_at": str(status_check.get("updated_at") or status_check.get("checked_at") or "").strip() or None,
    }


def _build_public_trust_anchor_documents(db: Any, request: Request | None = None) -> dict[str, Any]:
    config = _trust_anchor_public_config(request)
    public_base_url = str(config.get("public_base_url") or "").rstrip("/")
    issuer_did = str(config.get("issuer_did") or "").strip()
    did_document_url = str(config.get("did_document_url") or "").strip()
    issuer_authority_url = f"{public_base_url}/.well-known/issuer-authority.json" if public_base_url else ""
    issuer_authority_status_url = f"{public_base_url}/.well-known/issuer-authority-status.json" if public_base_url else ""
    verifier_policy_url = f"{public_base_url}/.well-known/verifier-policy.json" if public_base_url else ""
    bundle_url = f"{public_base_url}/.well-known/trust-anchor.json" if public_base_url else ""
    status_url = f"{public_base_url}/api/trust-anchor/status" if public_base_url else ""
    authority_subject = _trust_anchor_public_subject(config)
    issuer_authority = _select_trust_anchor_issuer_record(db, config)
    if not isinstance(issuer_authority, dict):
        raise HTTPException(status_code=404, detail="issuer authority not found")
    credential_status_ref = str(issuer_authority.get("credential_status_ref") or "").strip()
    status_check = get_credential_status_check(db, credential_status_ref) if credential_status_ref else None
    if not isinstance(status_check, dict):
        status_check = {
            "credential_status_ref": credential_status_ref or None,
            "credential_id": str(issuer_authority.get("vc_id") or issuer_authority.get("credential_ref") or "").strip() or None,
            "resolver_ref": None,
            "status_state": str(issuer_authority.get("credential_status_state") or "unverifiable").strip().lower() or "unverifiable",
            "checked_at": str(issuer_authority.get("credential_status_checked_at") or "").strip() or None,
            "proof_ref": None,
            "trust_root_ref": None,
            "issuer": str(issuer_authority.get("issuer") or "").strip() or None,
            "updated_at": str(issuer_authority.get("updated_at") or issuer_authority.get("created_at") or "").strip() or None,
        }
    credential_status_document = _build_public_credential_status_document(
        request=request,
        config=config,
        status_check=status_check,
        issuer_authority=issuer_authority,
    )
    authority_status = {
        "authority_active": str(issuer_authority.get("status") or "").strip().lower() == "active",
        "binding_anchored": str(issuer_authority.get("verification_state") or "").strip().lower() in {"anchored", "verified"},
        "vc_verified": str(issuer_authority.get("vc_verification_status") or "").strip().lower() == "verified",
        "live_identity_verified": True,
        "credential_ref": issuer_authority.get("credential_ref"),
        "vc_type": issuer_authority.get("vc_type"),
        "vc_id": issuer_authority.get("vc_id"),
        "vc_verification_proof_ref": issuer_authority.get("vc_verification_proof_ref"),
        "credential_status_ref": credential_status_ref or None,
        "credential_status": credential_status_document.get("status"),
    }
    public_issuer_authority = {
        "id": issuer_authority_url or f"{issuer_did}#issuer-authority",
        "type": "DssIssuerAuthority",
        "statement_type": "IssuerAuthorityStatement",
        "format": "dss-public-authority-statement-v1",
        "credential_family": "authority",
        "issuer_did": issuer_did,
        "issuer": {
            "id": issuer_did,
            "type": "IssuerAuthority",
        },
        "subject": authority_subject,
        "issued_at": issuer_authority.get("updated_at") or issuer_authority.get("created_at"),
        "not_a_verifiable_credential": True,
        "authority_identity": {
            "issuer_did": issuer_did,
            "identity_anchor_ref": issuer_authority.get("identity_anchor_ref") or did_document_url,
            "verification_state": issuer_authority.get("verification_state"),
            "issuer_class": issuer_authority.get("issuer_class"),
        },
        "organisation_identity": {
            "name": authority_subject.get("organisation_name"),
            "homepage": authority_subject.get("organisation_uri"),
            "registration_ref": authority_subject.get("organisation_registration_ref"),
            "status": "partial",
        },
        "policy": {
            "policy_ref": issuer_authority.get("policy_ref"),
            "policy_verdict": issuer_authority.get("policy_verdict"),
            "policy_scope": issuer_authority.get("policy_scope") or [],
            "verifier_policy_ref": issuer_authority.get("verifier_policy_ref") or bundle_url,
        },
        "status": authority_status,
        "status_discovery": {
            "authority_status_ref": issuer_authority_status_url or status_url,
            "credential_status_ref": credential_status_document.get("id"),
            "freshness_model": "bounded_status_document",
        },
        "discovery": {
            "did_document": did_document_url,
            "trust_anchor_status": status_url,
            "trust_anchor_bundle": bundle_url,
        },
    }
    public_issuer_authority_status = {
        "id": issuer_authority_status_url or f"{issuer_did}#issuer-authority-status",
        "type": "DssIssuerAuthorityStatus",
        "status_type": "IssuerAuthorityStatusStatement",
        "format": "dss-public-authority-status-v1",
        "credential_family": "status",
        "issuer": {
            "id": issuer_did,
            "type": "IssuerAuthority",
        },
        "subject": authority_subject,
        "not_a_verifiable_credential": True,
        "status": authority_status,
        "credential_status": credential_status_document,
        "freshness": credential_status_document.get("freshness"),
        "invalidation": credential_status_document.get("invalidation"),
        "policy": {
            "policy_ref": issuer_authority.get("policy_ref"),
            "policy_verdict": issuer_authority.get("policy_verdict"),
            "policy_scope": issuer_authority.get("policy_scope") or [],
            "verifier_policy_ref": issuer_authority.get("verifier_policy_ref") or bundle_url,
        },
        "discovery": {
            "authority_statement": issuer_authority_url,
            "credential_status": credential_status_document.get("id"),
            "trust_anchor_bundle": bundle_url,
        },
        "updated_at": issuer_authority.get("updated_at") or issuer_authority.get("created_at"),
    }
    public_verifier_policy = {
        "id": verifier_policy_url or f"{issuer_did}#verifier-policy",
        "type": "DssVerifierPolicy",
        "policy_type": "VerifierPolicyStatement",
        "format": "dss-public-verifier-policy-v1",
        "issuer": {
            "id": issuer_did,
            "type": "IssuerAuthority",
        },
        "subject": authority_subject,
        "not_a_verifiable_credential": True,
        "policy": {
            "policy_ref": issuer_authority.get("policy_ref"),
            "policy_verdict": issuer_authority.get("policy_verdict"),
            "policy_scope": issuer_authority.get("policy_scope") or [],
            "verifier_policy_ref": issuer_authority.get("verifier_policy_ref") or bundle_url,
        },
        "verification_expectations": {
            "resolve_issuer_did_first": True,
            "inspect_authority_statement": True,
            "inspect_authority_status_statement": True,
            "inspect_status_statement": True,
        },
        "discovery": {
            "authority_statement": issuer_authority_url,
            "authority_status_statement": issuer_authority_status_url or status_url,
            "credential_status": credential_status_document.get("id"),
            "trust_anchor_bundle": bundle_url,
        },
        "updated_at": issuer_authority.get("updated_at") or issuer_authority.get("created_at"),
    }
    bundle = {
        "issuer_did": issuer_did,
        "did_document_url": did_document_url,
        "service_endpoints": {
            "trust_anchor_status": status_url,
            "trust_anchor_bundle": bundle_url,
            "issuer_authority_object": issuer_authority_url,
            "issuer_authority_status_object": issuer_authority_status_url,
            "verifier_policy_object": verifier_policy_url,
            "credential_status_object": credential_status_document.get("id"),
        },
        "public_issuer_authority": public_issuer_authority,
        "public_issuer_authority_status": public_issuer_authority_status,
        "public_verifier_policy": public_verifier_policy,
        "public_credential_status": credential_status_document,
        "publication_intent": {
            "profile": "dss-public-trust-discovery-v1",
            "current_publication_state": "minimum_live",
            "published_now": [
                "issuer_authority_statement",
                "issuer_authority_status_statement",
                "verifier_policy_reference",
                "credential_status_statement",
            ],
        },
    }
    return {
        "config": config,
        "issuer_authority": issuer_authority,
        "credential_status": credential_status_document,
        "public_issuer_authority": public_issuer_authority,
        "public_issuer_authority_status": public_issuer_authority_status,
        "public_verifier_policy": public_verifier_policy,
        "bundle": bundle,
    }


def _public_object_dereference(record: dict[str, Any]) -> dict[str, Any]:
    lifecycle_state = str(record.get("lifecycle_state") or "").strip().lower() or "current"
    if lifecycle_state == "revoked":
        return {
            "outcome": "revoked",
            "current": False,
            "status": "revoked",
            "successor": None,
            "invalidation_reason": str(record.get("invalidation_reason") or "").strip() or None,
        }
    if lifecycle_state == "superseded":
        return {
            "outcome": "superseded",
            "current": False,
            "status": "superseded",
            "successor": str(record.get("superseded_by") or "").strip() or None,
            "invalidation_reason": None,
        }
    if lifecycle_state == "historical":
        return {
            "outcome": "historical",
            "current": False,
            "status": "historical",
            "successor": str(record.get("superseded_by") or "").strip() or None,
            "invalidation_reason": None,
        }
    return {
        "outcome": "current",
        "current": True,
        "status": "current",
        "successor": None,
        "invalidation_reason": None,
    }


def _public_object_status_document(record: dict[str, Any]) -> dict[str, Any]:
    dereference = _public_object_dereference(record)
    return {
        "id": str(record.get("status_ref") or "").strip() or None,
        "type": "DssPublicObjectStatus",
        "status_type": "PublicObjectStatusStatement",
        "format": "dss-public-object-status-v1",
        "subject": {
            "public_object_id": str(record.get("public_object_id") or "").strip() or None,
            "object_id": str(record.get("object_id") or "").strip() or None,
        },
        "lifecycle": {
            "state": str(record.get("lifecycle_state") or "").strip() or "current",
            "previous_version_id": str(record.get("previous_version_id") or "").strip() or None,
            "superseded_by": str(record.get("superseded_by") or "").strip() or None,
            "revoked_at": str(record.get("revoked_at") or "").strip() or None,
            "invalidation_reason": str(record.get("invalidation_reason") or "").strip() or None,
        },
        "dereference": dereference,
        "updated_at": str(record.get("updated_at") or record.get("created_at") or "").strip() or None,
    }


def _public_object_document(record: dict[str, Any]) -> dict[str, Any]:
    lifecycle_state = str(record.get("lifecycle_state") or "").strip().lower() or "current"
    current_public_object_id = (
        str(record.get("superseded_by") or "").strip()
        if lifecycle_state == "superseded" and str(record.get("superseded_by") or "").strip()
        else str(record.get("public_object_id") or "").strip()
    )
    status_document = _public_object_status_document(record)
    object_kind = str(record.get("object_kind") or "").strip() or None
    artifact_identity = record.get("artifact_identity") if isinstance(record.get("artifact_identity"), dict) else None
    document = {
        "id": str(record.get("public_object_id") or "").strip() or None,
        "type": "DssPublicObject",
        "object_type": "PublicObjectStatement",
        "format": "dss-public-object-v1",
        "object_kind": object_kind,
        "object_id": str(record.get("object_id") or "").strip() or None,
        "subject_id": str(record.get("subject_id") or "").strip() or None,
        "issuer_id": str(record.get("issuer_id") or "").strip() or None,
        "content_digest": str(record.get("content_digest") or "").strip() or None,
        "evidence_refs": list(record.get("evidence_refs") or []),
        "coord_ref_withheld": bool(str(record.get("coord_ref") or "").strip()),
        "status_ref": str(record.get("status_ref") or "").strip() or None,
        "lifecycle": {
            "state": lifecycle_state,
            "previous_version_id": str(record.get("previous_version_id") or "").strip() or None,
            "superseded_by": str(record.get("superseded_by") or "").strip() or None,
            "current_public_object_id": current_public_object_id or None,
            "revoked_at": str(record.get("revoked_at") or "").strip() or None,
            "invalidation_reason": str(record.get("invalidation_reason") or "").strip() or None,
        },
        "dereference": status_document.get("dereference"),
        "shareability": _lifecycle_aware_shareability(record, current_public_object_id or str(record.get("public_object_id") or "").strip() or None),
        "preferred_reference": {
            "value": current_public_object_id or None,
            "shareability": _lifecycle_aware_shareability(record, current_public_object_id or None),
            "copy_role": "primary",
        },
        "reference_aliases": _control_plane_aliases(
            {
                **record,
                "current_public_object_id": current_public_object_id or None,
            },
            "public_object",
            current_public_object_id or None,
        ),
        "created_at": str(record.get("created_at") or "").strip() or None,
        "updated_at": str(record.get("updated_at") or "").strip() or None,
    }
    if object_kind == "decision-artifact" and artifact_identity:
        document["decision_artifact_identity"] = artifact_identity
        document["decision_record_replay"] = {
            "schema": "dss-decision-record-replay-v1",
            "overlay_event_count": len(record.get("overlay_events") or []),
            "replay_ref": f"/public/objects/{object_kind}/{document.get('object_id')}/replay",
            "append_only_scope": "overlay_events_only",
            "current_materialized_view_mutable": True,
        }
    return document


def _canonical_model_provider_namespace(provider_type: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", str(provider_type or "model").strip().lower()).strip("-") or "model"


def _canonical_model_key_ref(provider_type: str, model_id: str) -> str:
    provider_namespace = _canonical_model_provider_namespace(provider_type)
    model_ref = str(model_id or "").strip().lower()
    return _normalize_principal_key_reference(f"{provider_namespace}:model:{model_ref}")


def _stable_model_principal_did(*, request: Request | None, provider_type: str, model_id: str) -> str:
    base_url = _control_plane_public_base_url(request)
    public_host = str(urlsplit(base_url).hostname or "").strip().lower() or os.getenv("DEFAULT_HOST", "")
    provider_key = _canonical_model_provider_namespace(provider_type)
    model_key = re.sub(r"[^a-z0-9]+", "-", str(model_id or "model").strip().lower()).strip("-") or "model"
    return f"did:web:{public_host}:principals:model:{provider_key}:{model_key}"


def _canonical_agent_key_ref(provider_type: str, agent_id: str) -> str:
    provider_namespace = _canonical_model_provider_namespace(provider_type)
    agent_ref = str(agent_id or "").strip().lower()
    return _normalize_principal_key_reference(f"{provider_namespace}:agent:{agent_ref}")


def _stable_agent_principal_did(*, request: Request | None, provider_type: str, agent_id: str) -> str:
    provider_key = _canonical_model_provider_namespace(provider_type)
    agent_key = re.sub(r"[^a-z0-9]+", "-", str(agent_id or "agent").strip().lower()).strip("-") or "agent"
    if provider_key == "openai" and agent_key == "codex":
        public_host = os.getenv("DEFAULT_HOST", "")
    else:
        base_url = _control_plane_public_base_url(request)
        public_host = str(urlsplit(base_url).hostname or "").strip().lower() or os.getenv("DEFAULT_HOST", "")
    return f"did:web:{public_host}:principals:agent:{provider_key}:{agent_key}"


def _stable_ledger_did(*, request: Request | None, ledger_id: str) -> str:
    base_url = _control_plane_public_base_url(request)
    public_host = str(urlsplit(base_url).hostname or "").strip().lower() or os.getenv("DEFAULT_HOST", "")
    ledger_key = re.sub(r"[^a-z0-9]+", "-", str(ledger_id or "ledger").strip().lower()).strip("-") or "ledger"
    return f"did:web:{public_host}:ledgers:{ledger_key}"


def _stable_surface_did(*, request: Request | None, surface_id: str) -> str:
    base_url = _control_plane_public_base_url(request)
    public_host = str(urlsplit(base_url).hostname or "").strip().lower() or os.getenv("DEFAULT_HOST", "")
    surface_key = re.sub(r"[^a-z0-9]+", "-", str(surface_id or "surface").strip().lower()).strip("-") or "surface"
    return f"did:web:{public_host}:surfaces:{surface_key}"


def _stable_provider_did(*, request: Request | None, provider_id: str) -> str:
    base_url = _control_plane_public_base_url(request)
    public_host = str(urlsplit(base_url).hostname or "").strip().lower() or os.getenv("DEFAULT_HOST", "")
    provider_key = re.sub(r"[^a-z0-9]+", "-", str(provider_id or "provider").strip().lower()).strip("-") or "provider"
    return f"did:web:{public_host}:providers:{provider_key}"


def _stable_binding_did(*, request: Request | None, binding_id: str) -> str:
    base_url = _control_plane_public_base_url(request)
    public_host = str(urlsplit(base_url).hostname or "").strip().lower() or os.getenv("DEFAULT_HOST", "")
    binding_key = re.sub(r"[^a-z0-9]+", "-", str(binding_id or "binding").strip().lower()).strip("-") or "binding"
    return f"did:web:{public_host}:bindings:{binding_key}"


def _stable_relationship_did(*, request: Request | None, relationship_id: str) -> str:
    base_url = _control_plane_public_base_url(request)
    public_host = str(urlsplit(base_url).hostname or "").strip().lower() or os.getenv("DEFAULT_HOST", "")
    relationship_key = re.sub(r"[^a-z0-9]+", "-", str(relationship_id or "relationship").strip().lower()).strip("-") or "relationship"
    return f"did:web:{public_host}:relationships:{relationship_key}"


def _canonical_entity_subject(*, request: Request | None, entity_type: str, entity_id: str) -> str:
    base_url = _control_plane_public_base_url(request)
    entity_key = str(entity_type or "").strip().lower()
    identifier = str(entity_id or "").strip()
    if entity_key == "ledger":
        return _stable_ledger_did(request=request, ledger_id=identifier)
    if entity_key == "surface":
        return _stable_surface_did(request=request, surface_id=identifier)
    if entity_key == "provider":
        return _stable_provider_did(request=request, provider_id=identifier)
    if entity_key == "binding":
        return _stable_binding_did(request=request, binding_id=identifier)
    if entity_key == "relationship":
        return _stable_relationship_did(request=request, relationship_id=identifier)
    return f"{base_url}/entities/{entity_key}/{identifier}"


def _ensure_registry_canonical_subject_uniqueness(
    *,
    registry: dict[str, Any],
    record_key: str,
    canonical_subject: str,
    key_field: str,
) -> None:
    canonical_key = str(canonical_subject or "").strip()
    if not canonical_key:
        return
    for existing_key, existing_record in registry.items():
        if str(existing_key).strip() == str(record_key).strip() or not isinstance(existing_record, dict):
            continue
        if str(existing_record.get("canonical_subject") or "").strip() != canonical_key:
            continue
        other_key = str(existing_record.get(key_field) or existing_key).strip() or str(existing_key).strip()
        raise HTTPException(status_code=409, detail=f"canonical_subject already bound: {canonical_key} ({other_key})")


def _ensure_model_principal_for_binding(
    *,
    request: Request,
    registry: dict[str, dict[str, Any]],
    provider_type: str,
    model_id: str,
    linked_model_principal: str | None = None,
    binding_id: str | None = None,
    tenant_id: str | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], bool]:
    key_ref = _canonical_model_key_ref(provider_type, model_id)
    stable_did = _stable_model_principal_did(request=request, provider_type=provider_type, model_id=model_id)
    tenant_scope = _normalize_principal_tenant_id(tenant_id)
    binding_value = str(binding_id or "").strip() or None
    linked_value = str(linked_model_principal or "").strip() or None
    metadata = {
        "actor_type": "model",
        "wallet_capable": False,
        "provider_type": str(provider_type or "").strip(),
        "model_id": str(model_id or "").strip(),
    }
    if binding_value:
        metadata["binding_id"] = binding_value
    payload = PrincipalCreateRequest(
        principal_did=stable_did,
        tenant_id=tenant_scope,
        display_name=str(model_id or stable_did).strip() or stable_did,
        principal_key_refs=[key_ref],
        metadata=metadata,
        status="active",
        provisioning_source="control_plane_model_binding_v1",
    )

    stable_existing = registry.get(stable_did)
    key_ref_existing = _find_principal_by_key_ref(registry, principal_key_ref=key_ref, tenant_id=tenant_scope)
    legacy_did = str(key_ref_existing.get("principal_did") or "").strip() if isinstance(key_ref_existing, dict) else ""
    linked_existing = registry.get(linked_value) if linked_value else None
    source_existing = (
        stable_existing
        if isinstance(stable_existing, dict)
        else linked_existing
        if isinstance(linked_existing, dict)
        else key_ref_existing
        if isinstance(key_ref_existing, dict)
        else None
    )

    mutated = False
    if isinstance(source_existing, dict):
        target_record = _upsert_principal_record(
            existing=source_existing,
            principal_did=stable_did,
            payload=payload,
        )
    else:
        target_record = _upsert_principal_record(
            existing=None,
            principal_did=stable_did,
            payload=payload,
        )

    if source_existing is not stable_existing and isinstance(source_existing, dict):
        legacy_source_did = str(source_existing.get("principal_did") or linked_value or legacy_did).strip()
        if legacy_source_did and legacy_source_did != stable_did and legacy_source_did in registry:
            registry.pop(legacy_source_did, None)
            mutated = True

    if legacy_did and legacy_did != stable_did and legacy_did in registry:
        registry.pop(legacy_did, None)
        mutated = True
    if linked_value and linked_value != stable_did and linked_value in registry:
        registry.pop(linked_value, None)
        mutated = True

    _ensure_principal_registry_uniqueness(
        registry,
        principal_did=stable_did,
        tenant_id=str(target_record.get("tenant_id") or tenant_scope).strip(),
        key_references=target_record.get("principal_key_refs")
        if isinstance(target_record.get("principal_key_refs"), list)
        else target_record.get("key_references")
        if isinstance(target_record.get("key_references"), list)
        else [],
        canonical_subject=str(target_record.get("canonical_subject") or "").strip(),
    )
    if not isinstance(stable_existing, dict) or dict(stable_existing) != dict(target_record):
        mutated = True
    registry[stable_did] = target_record
    return registry, target_record, mutated


def _ensure_codex_principal(
    *,
    request: Request,
    registry: dict[str, dict[str, Any]],
    payload: CodexPrincipalProvisionRequest,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], bool]:
    stable_did = _stable_agent_principal_did(request=request, provider_type="openai", agent_id="codex")
    key_ref = _canonical_agent_key_ref("openai", "codex")
    tenant_scope = _normalize_principal_tenant_id(payload.tenant_id)
    normalized_ledger_id = _normalize_related_ledger_id(payload.ledger_id)
    normalized_surface_ids = sorted({str(item).strip() for item in payload.surface_ids if str(item).strip()})
    delegated_by_principal_did = (
        str(payload.delegated_by_principal_did or principal_from_request(request).principal_did or "").strip() or None
    )
    delegated_by_principal_id = (
        str(payload.delegated_by_principal_id or principal_from_request(request).principal_id or "").strip() or None
    )
    metadata = dict(payload.metadata or {})
    metadata.update(
        {
            "actor_type": "agent",
            "wallet_capable": False,
            "provider_type": "openai",
            "agent_id": "codex",
            "agent_runtime": "external_cli",
            "delegated_authority": {
                "delegation_mode": "delegated_only",
                "delegated_prompt_execution": "explicit_cli_request_required",
                "hidden_operator_alias": False,
                "revocable": True,
                "revocation_mode": "control_plane_operator",
                "ledger_scope": [normalized_ledger_id] if normalized_ledger_id else [],
                "surface_scope": normalized_surface_ids,
                "delegated_by_principal_did": delegated_by_principal_did,
                "delegated_by_principal_id": delegated_by_principal_id,
            },
        }
    )
    if normalized_ledger_id:
        metadata["ledger_id"] = normalized_ledger_id
    provision_payload = PrincipalCreateRequest(
        principal_did=stable_did,
        tenant_id=tenant_scope,
        display_name=str(payload.display_name or "OpenAI Codex").strip() or "OpenAI Codex",
        principal_key_refs=[key_ref],
        metadata=metadata,
        status="active",
        provisioning_source="control_plane_codex_principal_v1",
        idempotency_key=payload.idempotency_key,
        submission_ref=payload.submission_ref,
    )

    stable_existing = registry.get(stable_did)
    key_ref_existing = _find_principal_by_key_ref(registry, principal_key_ref=key_ref, tenant_id=tenant_scope)
    legacy_did = str(key_ref_existing.get("principal_did") or "").strip() if isinstance(key_ref_existing, dict) else ""
    source_existing = stable_existing if isinstance(stable_existing, dict) else key_ref_existing if isinstance(key_ref_existing, dict) else None

    mutated = False
    target_record = _upsert_principal_record(
        existing=source_existing if isinstance(source_existing, dict) else None,
        principal_did=stable_did,
        payload=provision_payload,
    )

    if source_existing is not stable_existing and isinstance(source_existing, dict):
        legacy_source_did = str(source_existing.get("principal_did") or legacy_did).strip()
        if legacy_source_did and legacy_source_did != stable_did and legacy_source_did in registry:
            registry.pop(legacy_source_did, None)
            mutated = True

    if legacy_did and legacy_did != stable_did and legacy_did in registry:
        registry.pop(legacy_did, None)
        mutated = True

    _ensure_principal_registry_uniqueness(
        registry,
        principal_did=stable_did,
        tenant_id=str(target_record.get("tenant_id") or tenant_scope).strip(),
        key_references=target_record.get("principal_key_refs")
        if isinstance(target_record.get("principal_key_refs"), list)
        else target_record.get("key_references")
        if isinstance(target_record.get("key_references"), list)
        else [],
        canonical_subject=str(target_record.get("canonical_subject") or "").strip(),
    )
    if not isinstance(stable_existing, dict) or dict(stable_existing) != dict(target_record):
        mutated = True
    registry[stable_did] = target_record
    return registry, target_record, mutated


def _ensure_principal_registry_uniqueness(
    registry: dict[str, dict[str, Any]],
    *,
    principal_did: str,
    tenant_id: str,
    key_references: list[str],
    canonical_subject: str,
) -> None:
    for existing_did, record in registry.items():
        if existing_did == principal_did or not isinstance(record, dict):
            continue
        if str(record.get("tenant_id") or "").strip() != tenant_id:
            continue
        if str(record.get("status") or "active").strip().lower() != "active":
            continue
        other_refs = (
            record.get("principal_key_refs")
            if isinstance(record.get("principal_key_refs"), list)
            else record.get("key_references")
            if isinstance(record.get("key_references"), list)
            else []
        )
        other_ref_set = {str(item).strip() for item in other_refs if str(item).strip()}
        for ref in key_references:
            if ref in other_ref_set:
                raise HTTPException(status_code=409, detail=f"principal_key_ref already bound: {ref}")
        if str(record.get("canonical_subject") or "").strip() == canonical_subject:
            raise HTTPException(status_code=409, detail=f"canonical_subject already bound: {canonical_subject}")


def _build_principal_record(*, principal_did: str, payload: PrincipalCreateRequest) -> dict[str, Any]:
    timestamp = _now_iso()
    metadata = _normalize_principal_metadata(_merge_principal_actor_type(payload.metadata, payload.actor_type))
    if metadata.get("ledger_id") is not None:
        metadata["ledger_id"] = _normalize_related_ledger_id(metadata.get("ledger_id"))
    metadata.setdefault("probation_status", "probation")
    metadata.setdefault("probation_reason", "fresh_subject_created")
    principal_key_refs = _normalize_key_references(payload.resolved_principal_key_refs())
    canonical_subject, canonical_subject_source = _canonical_subject_from_inputs(
        principal_did=principal_did,
        actor_metadata=metadata,
        key_refs=principal_key_refs,
    )
    return {
        "principal_did": principal_did,
        "tenant_id": _normalize_principal_tenant_id(payload.tenant_id),
        "display_name": (payload.display_name or principal_did).strip(),
        "status": _validate_status(str(payload.status or "active").strip().lower() or "active", allowed=_ENTITY_ALLOWED_STATUSES, field_name="status"),
        "principal_key_refs": principal_key_refs,
        "key_references": list(principal_key_refs),
        "canonical_subject": canonical_subject,
        "canonical_subject_source": canonical_subject_source,
        "created_at": timestamp,
        "updated_at": timestamp,
        "disabled_at": None,
        "disable_reason": None,
        "metadata": metadata,
        "actor_type": metadata.get("actor_type"),
        "standing_view": {**_default_principal_standing_view(), "updated_at": timestamp},
        "provisioning_source": "admin_principal_api_v1",
        "created_by_principal_id": None,
        "last_changed_by_principal_id": None,
        "submission_ref": str(payload.submission_ref or "").strip() or None,
    }


def _upsert_principal_record(
    *,
    existing: dict[str, Any] | None,
    principal_did: str,
    payload: PrincipalCreateRequest,
) -> dict[str, Any]:
    if not isinstance(existing, dict):
        return _build_principal_record(principal_did=principal_did, payload=payload)

    timestamp = _now_iso()
    merged_metadata = {}
    if isinstance(existing.get("metadata"), dict):
        merged_metadata.update(existing.get("metadata") or {})
    if isinstance(payload.metadata, dict):
        merged_metadata.update(payload.metadata)
    merged_metadata = _normalize_principal_metadata(merged_metadata)
    if merged_metadata.get("ledger_id") is not None:
        merged_metadata["ledger_id"] = _normalize_related_ledger_id(merged_metadata.get("ledger_id"))

    existing_refs = existing.get("principal_key_refs")
    if not isinstance(existing_refs, list):
        existing_refs = existing.get("key_references") if isinstance(existing.get("key_references"), list) else []
    principal_key_refs = _normalize_key_references([*existing_refs, *payload.resolved_principal_key_refs()])
    canonical_subject, canonical_subject_source = _canonical_subject_from_inputs(
        principal_did=principal_did,
        actor_metadata=merged_metadata,
        key_refs=principal_key_refs,
    )

    created_at = str(existing.get("created_at") or "").strip() or timestamp
    standing_view = (
        dict(existing.get("standing_view"))
        if isinstance(existing.get("standing_view"), dict)
        else {**_default_principal_standing_view(), "updated_at": timestamp}
    )

    return {
        "principal_did": principal_did,
        "tenant_id": _normalize_principal_tenant_id(payload.tenant_id or existing.get("tenant_id")),
        "display_name": str(
            payload.display_name or existing.get("display_name") or principal_did
        ).strip()
        or principal_did,
        "status": _validate_status(
            str(payload.status or existing.get("status") or "pending").strip().lower() or "pending",
            allowed=_ENTITY_ALLOWED_STATUSES,
            field_name="status",
        ),
        "principal_key_refs": principal_key_refs,
        "key_references": list(principal_key_refs),
        "canonical_subject": canonical_subject,
        "canonical_subject_source": canonical_subject_source,
        "created_at": created_at,
        "updated_at": timestamp,
        "disabled_at": existing.get("disabled_at"),
        "disable_reason": existing.get("disable_reason"),
        "metadata": merged_metadata,
        "actor_type": merged_metadata.get("actor_type"),
        "standing_view": standing_view,
        "provisioning_source": str(existing.get("provisioning_source") or "admin_principal_api_v1").strip()
        or "admin_principal_api_v1",
        "created_by_principal_id": str(existing.get("created_by_principal_id") or "").strip() or None,
        "last_changed_by_principal_id": str(existing.get("last_changed_by_principal_id") or "").strip() or None,
        "submission_ref": str(payload.submission_ref or existing.get("submission_ref") or "").strip() or None,
    }


def _build_control_plane_surface_record(
    *,
    request: Request,
    payload: SurfaceUpsertRequest,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    timestamp = _now_iso()
    current = dict(existing or {})
    metadata = dict(current.get("metadata") or {}) if isinstance(current.get("metadata"), dict) else {}
    metadata.update(payload.metadata or {})
    surface_id = str(payload.surface_id or current.get("surface_id") or "").strip()
    display_name = str(payload.display_name or current.get("display_name") or surface_id).strip() or surface_id
    canonical_subject = (
        str(payload.canonical_subject or current.get("canonical_subject") or "").strip()
        or _canonical_entity_subject(request=request, entity_type="surface", entity_id=surface_id)
    )
    canonical_subject_source = (
        str(payload.canonical_subject_source or current.get("canonical_subject_source") or "").strip()
        or "did:web:surface"
    )
    mutation = _control_plane_mutation_metadata(request=request, submission_ref=payload.submission_ref, existing=current)
    return {
        "surface_id": surface_id,
        "display_name": display_name,
        "surface_type": str(payload.surface_type or current.get("surface_type") or "custom").strip().lower() or "custom",
        "status": _validate_status(
            str(payload.status or current.get("status") or "pending").strip().lower() or "pending",
            allowed=_ENTITY_ALLOWED_STATUSES,
            field_name="status",
        ),
        "ledger_id": _normalize_related_ledger_id(payload.ledger_id or current.get("ledger_id")),
        "principal_did": str(payload.principal_did or current.get("principal_did") or "").strip() or None,
        "binding_ref": str(payload.binding_ref or current.get("binding_ref") or "").strip() or None,
        "runtime_context_id": str(payload.runtime_context_id or current.get("runtime_context_id") or "").strip() or None,
        "endpoint": str(payload.endpoint or current.get("endpoint") or "").strip() or None,
        "canonical_subject": canonical_subject,
        "canonical_subject_source": canonical_subject_source,
        "metadata": metadata,
        "provisioning_source": str(
            payload.provisioning_source or current.get("provisioning_source") or "control_plane_api_v1"
        ).strip()
        or "control_plane_api_v1",
        "created_at": str(current.get("created_at") or "").strip() or timestamp,
        "updated_at": timestamp,
        "submitted_by": str(current.get("submitted_by") or principal_from_request(request).principal_id).strip() or None,
        "created_by_principal_id": mutation["created_by_principal_id"],
        "last_changed_by_principal_id": mutation["last_changed_by_principal_id"],
        "submission_ref": mutation["submission_ref"],
    }


def _build_control_plane_relationship_record(
    *,
    request: Request,
    payload: RelationshipUpsertRequest,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    timestamp = _now_iso()
    current = dict(existing or {})
    metadata = dict(current.get("metadata") or {}) if isinstance(current.get("metadata"), dict) else {}
    metadata.update(payload.metadata or {})
    subject_entity_type = str(payload.subject_entity_type or current.get("subject_entity_type") or "").strip().lower()
    object_entity_type = str(payload.object_entity_type or current.get("object_entity_type") or "").strip().lower()
    subject_entity_id = (
        _normalize_related_ledger_id(payload.subject_entity_id or current.get("subject_entity_id"))
        if subject_entity_type == "ledger"
        else str(payload.subject_entity_id or current.get("subject_entity_id") or "").strip()
    )
    object_entity_id = (
        _normalize_related_ledger_id(payload.object_entity_id or current.get("object_entity_id"))
        if object_entity_type == "ledger"
        else str(payload.object_entity_id or current.get("object_entity_id") or "").strip()
    )
    relationship_id = str(payload.relationship_id or current.get("relationship_id") or "").strip()
    if not relationship_id:
        relationship_id = "::".join(
            [
                subject_entity_type,
                str(subject_entity_id or "").strip(),
                object_entity_type,
                str(object_entity_id or "").strip(),
            ]
        )
    permission_scope = _validate_status(
        str(payload.permission_scope or current.get("permission_scope") or "full").strip().lower() or "full",
        allowed=_PERMISSION_SCOPES,
        field_name="permission_scope",
    )
    permission_payload = dict(payload.permission_payload or current.get("permission_payload") or {})
    if permission_scope == "custom" and not permission_payload:
        raise HTTPException(status_code=422, detail="permission_payload is required when permission_scope is custom")
    canonical_subject = (
        str(payload.canonical_subject or current.get("canonical_subject") or "").strip()
        or _canonical_entity_subject(request=request, entity_type="relationship", entity_id=relationship_id)
    )
    canonical_subject_source = (
        str(payload.canonical_subject_source or current.get("canonical_subject_source") or "").strip()
        or "did:web:relationship"
    )
    try:
        subject_subtype = ""
        if subject_entity_type == "principal":
            subject_meta = dict(payload.metadata or current.get("metadata") or {})
            subject_subtype = str(subject_meta.get("actor_type") or "").strip().lower()
        validate_relationship_type(
            str(payload.relationship_type or current.get("relationship_type") or "related_to").strip().lower() or "related_to",
            subject_entity_type,
            object_entity_type,
            subject_subtype=subject_subtype or None,
        )
    except OntologyError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    mutation = _control_plane_mutation_metadata(request=request, submission_ref=payload.submission_ref, existing=current)
    return {
        "relationship_id": relationship_id,
        "subject_entity_type": subject_entity_type,
        "subject_entity_id": subject_entity_id,
        "object_entity_type": object_entity_type,
        "object_entity_id": object_entity_id,
        "relationship_type": str(payload.relationship_type or current.get("relationship_type") or "related_to").strip().lower() or "related_to",
        "status": _validate_status(
            str(payload.status or current.get("status") or "active").strip().lower() or "active",
            allowed=_RELATIONSHIP_ALLOWED_STATUSES,
            field_name="status",
        ),
        "enabled_state": _validate_status(
            str(payload.enabled_state or current.get("enabled_state") or "enabled").strip().lower() or "enabled",
            allowed=_ENABLED_STATES,
            field_name="enabled_state",
        ),
        "permission_scope": permission_scope,
        "permission_payload": permission_payload,
        "ledger_id": _normalize_related_ledger_id(payload.ledger_id or current.get("ledger_id")),
        "start_at": str(payload.start_at or current.get("start_at") or "").strip() or None,
        "end_at": str(payload.end_at or current.get("end_at") or "").strip() or None,
        "evidence_ref": str(payload.evidence_ref or current.get("evidence_ref") or "").strip() or None,
        "provenance_ref": str(payload.provenance_ref or current.get("provenance_ref") or "").strip() or None,
        "canonical_subject": canonical_subject,
        "canonical_subject_source": canonical_subject_source,
        "metadata": metadata,
        "created_at": str(current.get("created_at") or "").strip() or timestamp,
        "updated_at": timestamp,
        "submitted_by": str(current.get("submitted_by") or principal_from_request(request).principal_id).strip() or None,
        "created_by_principal_id": mutation["created_by_principal_id"],
        "last_changed_by_principal_id": mutation["last_changed_by_principal_id"],
        "submission_ref": mutation["submission_ref"],
    }


def _provider_public_view(record: dict[str, Any]) -> dict[str, Any]:
    public = dict(record)
    secret_material = str(public.pop("secret_material", "") or "").strip()
    public["secret_present"] = bool(secret_material or str(public.get("secret_ref") or "").strip())
    return public


def _build_provider_credential_record(
    *,
    request: Request,
    payload: ProviderCredentialUpsertRequest,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    timestamp = _now_iso()
    current = dict(existing or {})
    metadata = dict(current.get("metadata") or {}) if isinstance(current.get("metadata"), dict) else {}
    metadata.update(payload.metadata or {})
    mutation = _control_plane_mutation_metadata(request=request, submission_ref=payload.submission_ref, existing=current)
    incoming_secret = str(payload.secret_material or "").strip()
    secret_material = incoming_secret or str(current.get("secret_material") or "").strip()
    provider_id = str(payload.provider_id or current.get("provider_id") or "").strip()
    canonical_subject = (
        str(payload.canonical_subject or current.get("canonical_subject") or "").strip()
        or _canonical_entity_subject(request=request, entity_type="provider", entity_id=provider_id)
    )
    canonical_subject_source = (
        str(payload.canonical_subject_source or current.get("canonical_subject_source") or "").strip()
        or "did:web:provider"
    )
    record = {
        "provider_id": provider_id,
        "provider_type": str(payload.provider_type or current.get("provider_type") or "").strip(),
        "credential_ref": str(payload.credential_ref or current.get("credential_ref") or "").strip() or None,
        "owner_scope": str(payload.owner_scope or current.get("owner_scope") or "shared").strip() or "shared",
        "status": str(payload.status or current.get("status") or "planned").strip() or "planned",
        "base_url": str(payload.base_url or current.get("base_url") or "").strip() or None,
        "deployment_targets": [str(item).strip() for item in (payload.deployment_targets or current.get("deployment_targets") or []) if str(item).strip()],
        "default_model": str(payload.default_model or current.get("default_model") or "").strip() or None,
        "readiness_note": str(payload.readiness_note or current.get("readiness_note") or "").strip() or None,
        "secret_ref": str(payload.secret_ref or current.get("secret_ref") or "").strip() or None,
        "secret_material": secret_material or None,
        "secret_updated_at": timestamp if incoming_secret else str(current.get("secret_updated_at") or "").strip() or None,
        "canonical_subject": canonical_subject,
        "canonical_subject_source": canonical_subject_source,
        "metadata": metadata,
        "created_at": str(current.get("created_at") or "").strip() or timestamp,
        "updated_at": timestamp,
        "created_by_principal_id": mutation["created_by_principal_id"],
        "last_changed_by_principal_id": mutation["last_changed_by_principal_id"],
        "submission_ref": mutation["submission_ref"],
    }
    return record


def _build_model_binding_record(
    *,
    request: Request,
    payload: ModelBindingUpsertRequest,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    timestamp = _now_iso()
    current = dict(existing or {})
    metadata = dict(current.get("metadata") or {}) if isinstance(current.get("metadata"), dict) else {}
    metadata.update(payload.metadata or {})
    mutation = _control_plane_mutation_metadata(request=request, submission_ref=payload.submission_ref, existing=current)
    binding_id = str(payload.binding_id or current.get("binding_id") or "").strip()
    canonical_subject = (
        str(payload.canonical_subject or current.get("canonical_subject") or "").strip()
        or _canonical_entity_subject(request=request, entity_type="binding", entity_id=binding_id)
    )
    canonical_subject_source = (
        str(payload.canonical_subject_source or current.get("canonical_subject_source") or "").strip()
        or "did:web:binding"
    )
    return {
        "binding_id": binding_id,
        "name": str(payload.name or current.get("name") or payload.binding_id).strip() or str(payload.binding_id).strip(),
        "provider_id": str(payload.provider_id or current.get("provider_id") or "").strip() or None,
        "provider_ref": str(payload.provider_ref or current.get("provider_ref") or "").strip() or None,
        "credential_ref": str(payload.credential_ref or current.get("credential_ref") or "").strip() or None,
        "provider_type": str(payload.provider_type or current.get("provider_type") or "").strip(),
        "model_id": str(payload.model_id or current.get("model_id") or "").strip(),
        "linked_model_principal": str(payload.linked_model_principal or current.get("linked_model_principal") or "").strip() or None,
        "scope": str(payload.scope or current.get("scope") or "shared").strip() or "shared",
        "status": str(payload.status or current.get("status") or "planned").strip() or "planned",
        "app_surfaces": [str(item).strip() for item in (payload.app_surfaces or current.get("app_surfaces") or []) if str(item).strip()],
        "policy_profile": str(payload.policy_profile or current.get("policy_profile") or "default").strip() or "default",
        "source": str(payload.source or current.get("source") or "control-plane").strip() or "control-plane",
        "canonical_subject": canonical_subject,
        "canonical_subject_source": canonical_subject_source,
        "metadata": metadata,
        "created_at": str(current.get("created_at") or "").strip() or timestamp,
        "updated_at": timestamp,
        "created_by_principal_id": mutation["created_by_principal_id"],
        "last_changed_by_principal_id": mutation["last_changed_by_principal_id"],
        "submission_ref": mutation["submission_ref"],
    }


def _find_principal_by_key_ref(
    registry: dict[str, dict[str, Any]],
    *,
    principal_key_ref: str,
    tenant_id: str | None = None,
) -> dict[str, Any] | None:
    normalized = _normalize_principal_key_reference(principal_key_ref)
    if not normalized:
        return None
    tenant_filter = str(tenant_id or "").strip()
    for principal_did in sorted(registry.keys()):
        row = registry.get(principal_did)
        if not isinstance(row, dict):
            continue
        if tenant_filter and str(row.get("tenant_id") or "").strip() != tenant_filter:
            continue
        refs = (
            row.get("principal_key_refs")
            if isinstance(row.get("principal_key_refs"), list)
            else row.get("key_references")
            if isinstance(row.get("key_references"), list)
            else []
        )
        if normalized in {str(item).strip() for item in refs if str(item).strip()}:
            return dict(row)
    return None


def _find_principals_by_contact(
    registry: dict[str, dict[str, Any]],
    *,
    email: str | None = None,
    phone: str | None = None,
    tenant_id: str | None = None,
    status_filter: str = "active",
) -> list[dict[str, Any]]:
    email_normalized = _normalize_email(email)
    phone_normalized = _normalize_phone(phone)
    tenant_scope = str(tenant_id or "").strip()
    normalized_status = str(status_filter or "").strip().lower()
    rows: list[dict[str, Any]] = []
    for principal_did in sorted(registry.keys()):
        row = registry.get(principal_did)
        if not isinstance(row, dict):
            continue
        if normalized_status and str(row.get("status") or "").strip().lower() != normalized_status:
            continue
        if tenant_scope and str(row.get("tenant_id") or "").strip() != tenant_scope:
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


def _bind_key_ref_to_principal(
    registry: dict[str, dict[str, Any]],
    *,
    principal_did: str,
    principal_key_ref: str,
    tenant_id: str | None = None,
    binding_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    did = str(principal_did or "").strip()
    if not did:
        raise HTTPException(status_code=400, detail="principal_did is required")
    row = registry.get(did)
    if not isinstance(row, dict):
        raise HTTPException(status_code=404, detail="principal not found")

    normalized_ref = _normalize_principal_key_reference(principal_key_ref)
    if not normalized_ref:
        raise HTTPException(status_code=422, detail="principal_key_ref is required")
    row_tenant = str(row.get("tenant_id") or "").strip()
    tenant_filter = str(tenant_id or "").strip()
    if tenant_filter and tenant_filter != row_tenant:
        raise HTTPException(status_code=422, detail="tenant_id does not match principal tenant")

    refs = (
        row.get("principal_key_refs")
        if isinstance(row.get("principal_key_refs"), list)
        else row.get("key_references")
        if isinstance(row.get("key_references"), list)
        else []
    )
    merged_refs = _normalize_key_references([*refs, normalized_ref])
    _ensure_principal_registry_uniqueness(
        registry,
        principal_did=did,
        tenant_id=row_tenant,
        key_references=merged_refs,
        canonical_subject=str(row.get("canonical_subject") or did).strip() or did,
    )

    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    merged_metadata = dict(metadata)
    if isinstance(binding_metadata, dict):
        merged_metadata.update(binding_metadata)
    merged_metadata = _normalize_principal_metadata(merged_metadata)

    canonical_subject, canonical_subject_source = _canonical_subject_from_inputs(
        principal_did=did,
        actor_metadata=merged_metadata,
        key_refs=merged_refs,
    )
    _ensure_principal_registry_uniqueness(
        registry,
        principal_did=did,
        tenant_id=row_tenant,
        key_references=merged_refs,
        canonical_subject=canonical_subject,
    )

    updated = dict(row)
    updated["principal_key_refs"] = merged_refs
    updated["key_references"] = list(merged_refs)
    updated["metadata"] = merged_metadata
    updated["canonical_subject"] = canonical_subject
    updated["canonical_subject_source"] = canonical_subject_source
    updated["updated_at"] = _now_iso()
    return updated


def _link_github_identity_to_principal(
    registry: dict[str, dict[str, Any]],
    *,
    principal_did: str,
    github_user_id: str,
    github_login: str | None = None,
    github_email: str | None = None,
) -> dict[str, Any]:
    user_id = str(github_user_id or "").strip()
    if not user_id:
        raise HTTPException(status_code=422, detail="github_user_id is required")
    metadata: dict[str, Any] = {
        "auth_provider": "github",
        "github_user_id": user_id,
        "github_link_status": "linked",
        "github_linked_at": _now_iso(),
    }
    if github_login:
        metadata["github_login"] = str(github_login).strip()
    if github_email:
        metadata["github_email"] = _normalize_email(github_email)
    return _bind_key_ref_to_principal(
        registry,
        principal_did=principal_did,
        principal_key_ref=f"github:user:{user_id}",
        binding_metadata=metadata,
    )


def _default_tenant_ledgers(tenant_id: str) -> list[str]:
    slug = tenant_id.split(":", 1)[1] if ":" in tenant_id else tenant_id
    slug = slug.strip().lower()
    slug = re.sub(r"[^a-z0-9._-]", "-", slug).strip("-")
    return [f"chat-{slug or 'default'}"]


def _discover_ledgers(db) -> Set[str]:
    namespaces: set[str] = set()
    try:
        with db.iter() as iterator:  # type: ignore[attr-defined]
            for raw_key, _ in iterator:
                try:
                    decoded = raw_key.decode() if isinstance(raw_key, (bytes, bytearray)) else str(raw_key)
                except Exception:  # pragma: no cover - defensive
                    continue

                if decoded in {
                    LEDGER_REGISTRY_KEY.decode(),
                    LEDGER_REGISTRY_V1_KEY.decode(),
                    TENANT_REGISTRY_V1_KEY.decode(),
                }:
                    continue

                namespace = decoded.rsplit(":", 1)[0]
                if namespace:
                    namespaces.add(namespace)
    except Exception:  # pragma: no cover - fallback if iterator unavailable
        try:
            for raw_key in db.keys():  # type: ignore[attr-defined]
                decoded = raw_key.decode() if isinstance(raw_key, (bytes, bytearray)) else str(raw_key)
                if decoded in {
                    LEDGER_REGISTRY_KEY.decode(),
                    LEDGER_REGISTRY_V1_KEY.decode(),
                    TENANT_REGISTRY_V1_KEY.decode(),
                }:
                    continue
                namespace = decoded.rsplit(":", 1)[0]
                if namespace:
                    namespaces.add(namespace)
        except Exception:
            namespaces = set()

    return namespaces


def _admin_include_discovered_ledgers() -> bool:
    return os.getenv("LEDGER_ADMIN_INCLUDE_DISCOVERED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _require_admin(request: Request) -> None:
    token = os.getenv("ADMIN_TOKEN")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ADMIN_TOKEN is not configured",
        )

    header_token = request.headers.get("x-admin-token") or ""
    auth_header = request.headers.get("authorization") or ""
    if auth_header.lower().startswith("bearer "):
        header_token = auth_header.split(" ", 1)[1].strip()

    if not header_token or header_token != token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid admin token")


def _require_control_plane_operator(request: Request) -> None:
    principal = principal_from_request(request)
    principal_id = str(principal.principal_id or "").strip()
    principal_did = str(principal.principal_did or "").strip()

    # Authenticated admin principals pass immediately
    try:
        _require_admin_principal_type(request)
    except HTTPException:
        pass  # Fall through to token check below
    else:
        if principal_id != "anonymous" or principal_did:
            return

    # Token bypass for anonymous requests (CI, headless operators)
    token = os.getenv("ADMIN_TOKEN")
    header_token = request.headers.get("x-admin-token") or ""
    auth_header = request.headers.get("authorization") or ""
    if auth_header.lower().startswith("bearer "):
        header_token = auth_header.split(" ", 1)[1].strip()
    if token and header_token and header_token == token:
        return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "error": "forbidden",
            "reason": "control_plane_operator_required",
        },
    )


def _benchmark_publication_operator_dids() -> set[str]:
    configured = {
        item.strip()
        for item in str(os.getenv("BENCHMARK_PUBLICATION_OPERATOR_DIDS") or "").split(",")
        if item.strip()
    }
    return configured | set(_BENCHMARK_PUBLICATION_BOOTSTRAP_OPERATOR_DIDS)


def _require_benchmark_publication_operator(request: Request) -> None:
    principal = principal_from_request(request)
    principal_did = str(principal.principal_did or "").strip()
    if principal_did and principal_did in _benchmark_publication_operator_dids():
        return
    _require_control_plane_operator(request)


def _require_control_plane_authenticated(request: Request) -> None:
    token = os.getenv("ADMIN_TOKEN")
    header_token = request.headers.get("x-admin-token") or ""
    auth_header = request.headers.get("authorization") or ""
    if auth_header.lower().startswith("bearer "):
        header_token = auth_header.split(" ", 1)[1].strip()
    if token and header_token and header_token == token:
        return
    claims = apply_session_token_claims_or_raise(request)
    if not isinstance(claims, dict):
        raise HTTPException(status_code=401, detail={"error": "authentication_required"})
    principal_did = str(claims.get("sub") or "").strip()
    if not principal_did:
        raise HTTPException(status_code=401, detail={"error": "principal_did_required"})


def _authorize_admin_scope(request: Request, *, ledger_id: str, action: LedgerAction) -> None:
    authorize_or_raise(
        request,
        ledger_id=ledger_id,
        action=action,
        explicit_context=True,
    )


def _require_admin_principal_type(request: Request) -> None:
    principal = principal_from_request(request)
    allowed = {
        item.strip().lower()
        for item in os.getenv("LEDGER_AUTHZ_ADMIN_PRINCIPAL_TYPES", "admin,service").split(",")
        if item.strip()
    }
    principal_type = (principal.principal_type or "").strip().lower()
    if principal_type in allowed:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "error": "forbidden",
            "reason": "admin_principal_required",
            "principal_type": principal.principal_type,
        },
    )


def _iter_db_keys(db):
    try:
        with db.iter() as iterator:  # type: ignore[attr-defined]
            for raw_key, _ in iterator:
                yield raw_key
        return
    except Exception:
        pass

    try:
        for raw_key in db.keys():  # type: ignore[attr-defined]
            yield raw_key
    except Exception:
        return


def _decode_key(raw_key: object) -> str:
    if isinstance(raw_key, (bytes, bytearray)):
        try:
            return raw_key.decode()
        except Exception:
            return ""
    return str(raw_key)


def _is_chat_history_entity(namespace: str) -> bool:
    text = (namespace or "").strip()
    if not text:
        return False
    if text.startswith("chat-"):
        return True
    return bool(_ENTITY_HEX_PATTERN.match(text))


def _compact_entity_name(namespace: str) -> str:
    if namespace.startswith("chat-"):
        return namespace[len("chat-") :]
    return namespace


@router.get("/reindex")
def trigger_reindex(
    request: Request,
    entity: str | None = Query(None, description="Optional logical entity context."),
    db=Depends(get_db),
):
    """Rebuild the token index and refreshed metadata for all ledger entries."""

    with log_operation(
        logger=LOGGER,
        operation="admin_reindex",
        request=request,
        entity=entity,
    ) as ctx:
        _authorize_admin_scope(
            request,
            ledger_id=(entity.strip() if isinstance(entity, str) and entity.strip() else "default"),
            action="ledger.write",
        )
        try:
            result = reindex_all(request.app, entity=entity)
        except Exception as exc:  # pragma: no cover - defensive guardrail
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        ctx.update(result)
        return result


@router.post("/clear_ledger")
def clear_ledger(  # <--- Remove the spaces so it aligns with @router
    request: Request,
    confirm: bool = Query(False, description="Set true to confirm data deletion"),
    entity: str | None = Query(None, description="Limit deletion to a specific namespace"),
    dry_run: bool = Query(False, description="If true, only report what would be deleted"),
    reindex: bool = Query(True, description="Rebuild token index after entity-scoped deletes"),
    db=Depends(get_db),
):
    """Clear ledger, substrate, and index keys from the database."""
    try:
        _require_admin(request)
    except HTTPException:
        _require_control_plane_operator(request)
    _authorize_admin_scope(
        request,
        ledger_id=(entity.strip() if isinstance(entity, str) and entity.strip() else "default"),
        action="ledger.write",
    )
    if not confirm and not dry_run:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="confirm=true is required to clear the ledger",
        )

    entity_value = entity.strip() if entity else None

    with log_operation(
        LOGGER,
        "admin_clear_ledger",
        request=request,
        entity=entity_value or "all",
        dry_run=dry_run,
        reindex=reindex,
    ) as ctx:
        keys_to_delete: list[object] = []
        counts = {
            "entity_keys": 0,
            "ledger_entries": 0,
            "token_index_keys": 0,
            "pinned_keys": 0,
            "registry_keys": 0,
            "skipped_keys": 0,
        }

        pinned_index_key = f"{_PINNED_PREFIX}{23}:index"
        pinned_index_raw_key: object | None = None

        for raw_key in _iter_db_keys(db):
            if raw_key in {LEDGER_REGISTRY_KEY, LEDGER_REGISTRY_V1_KEY, TENANT_REGISTRY_V1_KEY}:
                if entity_value is None:
                    keys_to_delete.append(raw_key)
                    counts["registry_keys"] += 1
                continue

            decoded = _decode_key(raw_key)
            if not decoded:
                counts["skipped_keys"] += 1
                continue

            if decoded.startswith(_ENTITY_PREFIX):
                if entity_value is None or decoded.startswith(f"{_ENTITY_PREFIX}{entity_value}:"):
                    keys_to_delete.append(raw_key)
                    counts["entity_keys"] += 1
                continue

            if decoded.startswith(_TOKEN_PREFIXES):
                if entity_value is None or reindex:
                    keys_to_delete.append(raw_key)
                    counts["token_index_keys"] += 1
                continue

            if decoded.startswith(_PINNED_PREFIX):
                if decoded == pinned_index_key:
                    pinned_index_raw_key = raw_key
                    continue
                if entity_value is None:
                    keys_to_delete.append(raw_key)
                    counts["pinned_keys"] += 1
                    continue
                parts = decoded.split(":", 2)
                entry_id = parts[2] if len(parts) == 3 else ""
                if entry_id.startswith(f"{entity_value}:"):
                    keys_to_delete.append(raw_key)
                    counts["pinned_keys"] += 1
                continue

            # LedgerStoreV2 splits entries into overlay/body/state prefixes.
            # Strip those prefixes so entity-scoped wipes hit the actual namespace.
            stripped_namespace = None
            for storage_prefix in ("overlay:", "body:", "state:"):
                if decoded.startswith(storage_prefix):
                    inner = decoded[len(storage_prefix) :]
                    if ":" in inner:
                        stripped_namespace = inner.split(":", 1)[0]
                    else:
                        stripped_namespace = inner
                    break

            if stripped_namespace is not None:
                if entity_value is None or stripped_namespace == entity_value:
                    keys_to_delete.append(raw_key)
                    counts["ledger_entries"] += 1
                continue

            if ":" in decoded:
                namespace = decoded.rsplit(":", 1)[0]
                if entity_value is None or namespace == entity_value:
                    keys_to_delete.append(raw_key)
                    counts["ledger_entries"] += 1
                continue

            counts["skipped_keys"] += 1

        if entity_value and pinned_index_raw_key is not None:
            raw_index = db.get(pinned_index_raw_key)
            if raw_index is not None:
                try:
                    decoded_index = raw_index.decode() if isinstance(raw_index, (bytes, bytearray)) else raw_index
                    items = json.loads(decoded_index)
                    pinned_ids = {str(item) for item in items}
                except Exception:
                    pinned_ids = set()
                if pinned_ids:
                    remaining = {entry_id for entry_id in pinned_ids if not entry_id.startswith(f"{entity_value}:")}
                    if remaining != pinned_ids:
                        if remaining:
                            db[pinned_index_raw_key] = json.dumps(sorted(remaining)).encode()
                        else:
                            keys_to_delete.append(pinned_index_raw_key)
                            counts["pinned_keys"] += 1

        if entity_value:
            registry = _load_registered_ledgers(db)
            if entity_value in registry:
                registry.discard(entity_value)
                if registry:
                    db[LEDGER_REGISTRY_KEY] = json.dumps(sorted(registry)).encode()
                else:
                    keys_to_delete.append(LEDGER_REGISTRY_KEY)
                    counts["registry_keys"] += 1
            registry_v1 = _load_registered_ledgers_v1(db)
            if entity_value in registry_v1:
                del registry_v1[entity_value]
                if registry_v1:
                    _persist_registered_ledgers_v1(db, registry_v1)
                else:
                    keys_to_delete.append(LEDGER_REGISTRY_V1_KEY)
                    counts["registry_keys"] += 1

        if dry_run:
            ctx.update({"deleted": counts, "deleted_total": len(keys_to_delete)})
            return {
                "status": "dry_run",
                "deleted": counts,
                "deleted_total": len(keys_to_delete),
                "entity": entity_value,
                "reindex": reindex,
            }

        for raw_key in keys_to_delete:
            try:
                del db[raw_key]
            except KeyError:
                continue

        reindexed = False
        if entity_value and reindex:
            reindex_all(request.app)
            reindexed = True

        ctx.update(
            {
                "deleted": counts,
                "deleted_total": len(keys_to_delete),
                "entity": entity_value,
                "reindex": reindexed,
            }
        )
        return {
            "status": "ok",
            "deleted": counts,
            "deleted_total": len(keys_to_delete),
            "entity": entity_value,
            "reindex": reindexed,
        }


@router.post("/guardian/consolidate")
async def consolidate_guardian(
    request: Request,
    namespace: str = Query(..., description="Namespace to consolidate"),
    limit: int = Query(50, description="Number of recent entries to scan"),
    dry_run: bool = Query(False, description="If true, do not persist updates"),
):
    _require_admin(request)
    _authorize_admin_scope(
        request,
        ledger_id=namespace,
        action="ledger.write",
    )
    service = LedgerService.from_request(request)
    store = service.store
    ledger = service.memory_ledger()
    substrate = service.memory_substrate()

    entries = store.list_by_namespace(namespace, limit=limit, reverse=True)
    if dry_run:
        return {
            "status": "dry_run",
            "namespace": namespace,
            "entry_count": len(entries),
        }

    result = await guardian_consolidate(
        entity=namespace,
        entries=entries,
        ledger=ledger,
        substrate=substrate,
    )

    if result is None:
        return {"status": "no_op", "namespace": namespace, "entry_count": len(entries)}

    return {
        "status": "ok",
        "namespace": namespace,
        "entry_count": len(entries),
        "summary_prime": result.summary_prime,
        "teleology_alignment": result.payload.teleology_alignment,
    }


@router.get("/ledgers")
def list_ledgers(request: Request, db=Depends(get_db)):
    """Return known ledger namespaces for the UI sidebar."""
    _require_admin(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")

    with log_operation(LOGGER, "admin_list_ledgers", request=request) as ctx:
        registry_v1 = _load_registered_ledgers_v1(db)
        registered = set(registry_v1.keys()) | _load_registered_ledgers(db)
        discovered: set[str] = set()
        if _admin_include_discovered_ledgers():
            discovered = _discover_ledgers(db)
        ledgers = sorted(registered | discovered | {"default"})
        ledger_records: list[dict[str, Any]] = []
        for ledger_id in ledgers:
            record = registry_v1.get(ledger_id)
            if isinstance(record, dict):
                ledger_records.append(record)
                continue
            ledger_records.append(
                {
                    "ledger_id": ledger_id,
                    "display_name": ledger_id,
                    "namespace": ledger_id,
                    "tenant_id": "tenant:unknown",
                    "owner_principal_id": "unknown",
                    "owner_principal_type": "unknown",
                    "policy_profile": "unknown",
                    "status": "active",
                    "canonical_subject": _stable_ledger_did(request=request, ledger_id=ledger_id),
                    "canonical_subject_source": "did:web:ledger",
                    "created_at": None,
                    "updated_at": None,
                    "metadata": {},
                    "provisioning_source": "discovered_or_legacy",
                }
            )

        ctx.update({"ledger_count": len(ledgers)})
        return {"ledgers": ledgers, "ledger_records": ledger_records}


@router.get("/ledgers/{ledger_id}/purpose")
def get_ledger_founding_purpose(request: Request, ledger_id: str, db=Depends(get_db)):
    """Return the founding purpose statement for a ledger."""
    _require_admin(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")

    service = LedgerService(db)
    boundary = service.get_ledger_library_boundary(ledger_id)
    foundation = boundary.get("foundation_identity") or {}
    purpose = str(foundation.get("purpose") or "").strip() or None
    name = str(foundation.get("name") or boundary.get("canonical_ledger_id") or ledger_id).strip()
    source = str(foundation.get("source") or "").strip() or None

    return JSONResponse(
        {
            "ledger_id": boundary.get("canonical_ledger_id") or ledger_id,
            "requested_ledger_id": ledger_id,
            "purpose": purpose,
            "name": name,
            "source": source,
        }
    )


@router.post("/ledgers")
def create_ledger(request: Request, payload: LedgerCreateRequest, db=Depends(get_db)):
    """Record a ledger namespace for clients that create ledgers dynamically."""
    _require_admin(request)
    # Bootstrap creation must not require the target ledger to already exist.
    # Authorize this admin action against the default scope, then persist the new ledger id.
    _authorize_admin_scope(
        request,
        ledger_id="default",
        action="ledger.write",
    )

    with log_operation(LOGGER, "admin_create_ledger", request=request) as ctx:
        name = payload.resolved_name()
        if not _is_valid_control_plane_ledger_id(name):
            raise HTTPException(status_code=400, detail="invalid ledger_id")
        registry_v1 = _load_registered_ledgers_v1(db)
        created = False
        if name not in registry_v1:
            registry_v1[name] = _build_registry_record(
                request=request,
                ledger_id=name,
                payload=payload,
            )
            created = True
        canonical_registry = _persist_registered_ledgers_v1(db, registry_v1)
        registered = set(canonical_registry.keys()) | _load_registered_ledgers(db)
        discovered = _discover_ledgers(db)
        ledgers = registered | discovered | {name or "default"}
        persisted = _persist_registered_ledgers(db, ledgers)

        ctx.update({"ledger": name, "ledger_count": len(persisted), "created": created})
        return {
            "status": "ok",
            "ledger": name,
            "created": created,
            "ledger_record": canonical_registry.get(name),
            "ledgers": persisted,
        }


@router.get("/tenants")
def list_tenants(request: Request, db=Depends(get_db)):
    """Return tenant registry records for provisioning visibility."""
    _require_admin(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")

    with log_operation(LOGGER, "admin_list_tenants", request=request) as ctx:
        tenants_v1 = _load_registered_tenants_v1(db)
        rows: list[dict[str, Any]] = []
        for tenant_id in sorted(tenants_v1.keys()):
            record = tenants_v1.get(tenant_id)
            if isinstance(record, dict):
                rows.append(record)
        ctx.update({"tenant_count": len(rows)})
        return {"tenants": rows}


@router.get("/accounts/{account_id}")
def inspect_account(account_id: str):
    try:
        return get_admin_account_inspection(account_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="unknown_account") from exc


@router.post("/accounts/{account_id}/trial/extend")
def extend_account_trial(account_id: str, payload: TrialExtensionRequest, request: Request):
    if account_id != DEFAULT_ACCOUNT_ID:
        raise HTTPException(status_code=404, detail="unknown_account")
    try:
        return extend_pilot_trial(
            days=payload.days,
            actor=payload.actor,
            reason=payload.reason,
            now=pilot_now_from_request(request),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/tenants")
def create_tenant(request: Request, payload: TenantCreateRequest, db=Depends(get_db)):
    """Idempotently provision tenant metadata and tenant-default ledger(s)."""
    _require_admin(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")

    with log_operation(LOGGER, "admin_create_tenant", request=request) as ctx:
        tenant_id = payload.resolved_tenant_id()

        tenants_v1 = _load_registered_tenants_v1(db)
        tenant_created = False
        if tenant_id not in tenants_v1:
            tenants_v1[tenant_id] = _build_tenant_record(
                request=request,
                tenant_id=tenant_id,
                payload=payload,
            )
            tenant_created = True
        canonical_tenants = _persist_registered_tenants_v1(db, tenants_v1)

        explicit_ledgers = [item.strip() for item in payload.ledger_ids if isinstance(item, str) and item.strip()]
        if "ledger_ids" in payload.model_fields_set:
            target_ledgers = explicit_ledgers
        else:
            target_ledgers = explicit_ledgers or _default_tenant_ledgers(tenant_id)
        ledger_payload = LedgerCreateRequest(
            tenant_id=tenant_id,
            owner_principal_id=payload.owner_principal_id,
            owner_principal_type=payload.owner_principal_type,
            policy_profile=payload.policy_profile,
            metadata=dict(payload.metadata or {}),
        )

        registry_v1 = _load_registered_ledgers_v1(db)
        ledger_creates: dict[str, bool] = {}
        for ledger_id in target_ledgers:
            if ledger_id not in registry_v1:
                registry_v1[ledger_id] = _build_registry_record(
                    request=request,
                    ledger_id=ledger_id,
                    payload=ledger_payload,
                )
                ledger_creates[ledger_id] = True
            else:
                ledger_creates[ledger_id] = False

        canonical_registry = _persist_registered_ledgers_v1(db, registry_v1)
        ledgers = set(canonical_registry.keys()) | _load_registered_ledgers(db)
        if _admin_include_discovered_ledgers():
            ledgers |= _discover_ledgers(db)
        persisted_ledgers = _persist_registered_ledgers(db, ledgers)

        ctx.update(
            {
                "tenant_id": tenant_id,
                "tenant_created": tenant_created,
                "ledger_count": len(target_ledgers),
            }
        )
        return {
            "status": "ok",
            "tenant": canonical_tenants.get(tenant_id),
            "tenant_created": tenant_created,
            "ledger_creates": ledger_creates,
            "ledger_records": {ledger_id: canonical_registry.get(ledger_id) for ledger_id in target_ledgers},
            "ledgers": persisted_ledgers,
        }


@control_plane_router.get("/ledgers")
def control_plane_list_ledgers(request: Request, db=Depends(get_db)):
    _require_control_plane_operator(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    registry_v1 = _load_registered_ledgers_v1(db)
    return {"status": "ok", "ledgers": [_annotate_control_plane_row(registry_v1[key], kind="ledger") for key in sorted(registry_v1.keys())]}


@control_plane_router.post("/ledgers")
def control_plane_upsert_ledger(request: Request, payload: LedgerCreateRequest, db=Depends(get_db)):
    _require_control_plane_operator(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")
    ledger_id = payload.resolved_name()
    if not ledger_id:
        raise HTTPException(status_code=400, detail="ledger_id is required")
    if not _is_valid_control_plane_ledger_id(ledger_id):
        raise HTTPException(status_code=400, detail="invalid ledger_id")
    fingerprint = _payload_fingerprint(
        "control_plane_upsert_ledger",
        {
            "ledger_id": ledger_id,
            "payload": payload.model_dump(mode="json"),
        },
    )
    replay = _replay_control_plane_mutation(db, idempotency_key=payload.idempotency_key, fingerprint=fingerprint)
    if replay is not None:
        return replay
    registry_v1 = _load_registered_ledgers_v1(db)
    existing = registry_v1.get(ledger_id)
    record = _build_registry_record(request=request, ledger_id=ledger_id, payload=payload)
    if isinstance(existing, dict):
        _validate_lifecycle_transition(
            current_status=existing.get("status"),
            target_status=record["status"],
            transitions=_ENTITY_ALLOWED_TRANSITIONS,
            field_name="status",
        )
        record["created_at"] = str(existing.get("created_at") or "").strip() or record["created_at"]
        record["metadata"] = {
            **(existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}),
            **(payload.metadata or {}),
        }
        existing_metadata = existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}
        existing_constitution = (
            existing_metadata.get("founding_constitution")
            if isinstance(existing_metadata.get("founding_constitution"), dict)
            else {}
        )
        visible_aliases = _ledger_visible_aliases(
            ledger_id,
            existing.get("display_name"),
            existing_constitution.get("name"),
            payload.name,
            payload.founding_constitution_name,
        )
        if visible_aliases:
            record["metadata"]["ledger_alias_history"] = _unique_string_list(
                [
                    *(
                        record["metadata"].get("ledger_alias_history")
                        if isinstance(record["metadata"].get("ledger_alias_history"), list)
                        else []
                    ),
                    *visible_aliases,
                ]
            )
        record["provisioning_source"] = (
            str(payload.provisioning_source or "").strip()
            or str(existing.get("provisioning_source") or record["provisioning_source"]).strip()
            or record["provisioning_source"]
        )
        record["created_by_principal_id"] = str(existing.get("created_by_principal_id") or record.get("created_by_principal_id") or "").strip() or None
    record["last_changed_by_principal_id"] = str(principal_from_request(request).principal_id or "").strip() or None
    record["provisioning_source"] = str(payload.provisioning_source or record.get("provisioning_source") or "control_plane_api_v1").strip() or "control_plane_api_v1"
    registry_v1[ledger_id] = record
    canonical = _persist_registered_ledgers_v1(db, registry_v1)
    response = _build_control_plane_response(
        resource_key="ledger",
        record=canonical.get(ledger_id) or record,
        entity_type="ledger",
        previous_status=str(existing.get("status") or "").strip().lower() if isinstance(existing, dict) else None,
        idempotency_key=payload.idempotency_key,
    )
    _store_control_plane_mutation(db, idempotency_key=payload.idempotency_key, fingerprint=fingerprint, response=response)
    return response


@control_plane_router.post("/ledgers/consolidate")
def control_plane_consolidate_ledgers(request: Request, payload: LedgerConsolidationRequest, db=Depends(get_db)):
    _require_control_plane_operator(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")
    canonical_ledger_id = _canonicalize_control_plane_ledger_id(payload.canonical_ledger_id)
    superseded_ledger_ids = _unique_string_list(_canonicalize_control_plane_ledger_id(item) for item in payload.superseded_ledger_ids)
    superseded_ledger_ids = [item for item in superseded_ledger_ids if item != canonical_ledger_id]
    if not canonical_ledger_id or not _is_valid_control_plane_ledger_id(canonical_ledger_id):
        raise HTTPException(status_code=400, detail="valid canonical_ledger_id is required")
    if not superseded_ledger_ids:
        raise HTTPException(status_code=400, detail="at least one superseded_ledger_id is required")
    fingerprint = _payload_fingerprint(
        "control_plane_consolidate_ledgers",
        {
            "canonical_ledger_id": canonical_ledger_id,
            "superseded_ledger_ids": superseded_ledger_ids,
            "reason": str(payload.reason or "").strip(),
        },
    )
    replay = _replay_control_plane_mutation(db, idempotency_key=payload.idempotency_key, fingerprint=fingerprint)
    if replay is not None:
        return replay

    registry_v1 = _load_registered_ledgers_v1(db)
    canonical = registry_v1.get(canonical_ledger_id) if isinstance(registry_v1.get(canonical_ledger_id), dict) else None
    if not isinstance(canonical, dict):
        raise HTTPException(status_code=404, detail="canonical ledger not found")
    missing = [item for item in superseded_ledger_ids if not isinstance(registry_v1.get(item), dict)]
    if missing:
        raise HTTPException(status_code=404, detail={"error": "superseded_ledgers_not_found", "ledger_ids": missing})

    timestamp = _now_iso()
    operator_id = str(principal_from_request(request).principal_id or "").strip() or None
    reason = str(payload.reason or "").strip() or "ledger_split_consolidation"
    canonical_record = dict(canonical)
    canonical_metadata = _apply_ledger_memory_tier_metadata(
        canonical_record.get("metadata") if isinstance(canonical_record.get("metadata"), dict) else {}
    )
    from_ids = set(superseded_ledger_ids)
    alias_history = _ledger_history_aliases(canonical_record)
    supersession_history = _unique_string_list(canonical_metadata.get("ledger_supersession_history") or [])
    consolidation_history = list(canonical_metadata.get("ledger_consolidation_history") or [])
    preserved_ledgers: list[str] = []

    for ledger_id in superseded_ledger_ids:
        source = dict(registry_v1.get(ledger_id) or {})
        preserved_ledgers.append(ledger_id)
        source_metadata = _apply_ledger_memory_tier_metadata(
            source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
        )
        source_aliases = _ledger_history_aliases(source)
        alias_history.extend(source_aliases)
        supersession_history.extend(source_aliases or [ledger_id])
        source_metadata["superseded_by_ledger_id"] = canonical_ledger_id
        source_metadata["canonical_ledger_id"] = canonical_ledger_id
        source_metadata["ledger_alias_history"] = _unique_string_list(_ledger_history_aliases(source))
        source_history = list(source_metadata.get("ledger_consolidation_history") or [])
        source_history.append(
            {
                "event": "superseded_by_consolidation",
                "canonical_ledger_id": canonical_ledger_id,
                "timestamp": timestamp,
                "reason": reason,
                "operator_principal_id": operator_id,
            }
        )
        source_metadata["ledger_consolidation_history"] = source_history
        source["status"] = "superseded"
        source["updated_at"] = timestamp
        source["metadata"] = source_metadata
        registry_v1[ledger_id] = source

    canonical_metadata["ledger_alias_history"] = _unique_string_list(alias_history)
    canonical_metadata["ledger_supersession_history"] = _unique_string_list(supersession_history)
    consolidation_history.append(
        {
            "event": "ledger_split_consolidated",
            "superseded_ledger_ids": preserved_ledgers,
            "timestamp": timestamp,
            "reason": reason,
            "operator_principal_id": operator_id,
        }
    )
    canonical_metadata["ledger_consolidation_history"] = consolidation_history
    canonical_metadata["canonical_ledger_id"] = canonical_ledger_id
    canonical_record["metadata"] = canonical_metadata
    canonical_record["updated_at"] = timestamp
    registry_v1[canonical_ledger_id] = canonical_record

    surfaces_v1 = _load_control_plane_surfaces_v1(db)
    principals_v1 = _load_registered_principals_v1(db)
    relationships_v1 = _load_control_plane_relationships_v1(db)
    surfaces_updated = _rebind_surface_ledgers(surfaces_v1, from_ids=from_ids, canonical_ledger_id=canonical_ledger_id, timestamp=timestamp)
    principals_updated = _rebind_principal_ledgers(principals_v1, from_ids=from_ids, canonical_ledger_id=canonical_ledger_id, timestamp=timestamp)
    relationships_updated = _rebind_relationship_ledgers(relationships_v1, from_ids=from_ids, canonical_ledger_id=canonical_ledger_id, timestamp=timestamp)

    canonical_registry = _persist_registered_ledgers_v1(db, registry_v1)
    _persist_control_plane_surfaces_v1(db, surfaces_v1)
    _persist_registered_principals_v1(db, principals_v1)
    _persist_control_plane_relationships_v1(db, relationships_v1)

    response = {
        "status": "ok",
        "execution_mode": "direct_write",
        "submission_status": "applied",
        "mutation_ref": f"cpm:{uuid4().hex}",
        "applied_at": timestamp,
        "idempotency_key": str(payload.idempotency_key or "").strip() or None,
        "consolidation": {
            "canonical_ledger_id": canonical_ledger_id,
            "superseded_ledger_ids": preserved_ledgers,
            "reason": reason,
            "preserve_history": True,
            "silent_destructive_merge_forbidden": True,
            "rebind_counts": {
                "surfaces": surfaces_updated,
                "principals": principals_updated,
                "relationships": relationships_updated,
            },
            "ledger": _annotate_control_plane_row(canonical_registry.get(canonical_ledger_id) or canonical_record, kind="ledger"),
            "runtime_continuity": {
                "alias_aware_coord_history_lookup": True,
                "surviving_governed_memory_boundary": canonical_ledger_id,
                "full_available_history_visible_across_aliases": True,
                "foundation_identity_available_after_consolidation": bool(
                    (
                        ((_annotate_control_plane_row(canonical_registry.get(canonical_ledger_id) or canonical_record, kind="ledger").get("ledger_self_description") or {}).get("runtime_foundation_identity") or {}).get("available")
                    )
                ),
            },
            "superseded_ledgers": [
                _annotate_control_plane_row(canonical_registry.get(ledger_id) or registry_v1.get(ledger_id) or {}, kind="ledger")
                for ledger_id in preserved_ledgers
                if isinstance(canonical_registry.get(ledger_id) or registry_v1.get(ledger_id), dict)
            ],
        },
    }
    _store_control_plane_mutation(db, idempotency_key=payload.idempotency_key, fingerprint=fingerprint, response=response)
    return response


@control_plane_router.get("/principals")
def control_plane_list_principals(
    request: Request,
    tenant_id: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
):
    _require_control_plane_operator(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")

    principals_v1 = _load_registered_principals_v1(db)
    rows: list[dict[str, Any]] = []
    tenant_filter = str(tenant_id or "").strip()
    normalized_status = str(status_filter or "").strip().lower()
    for principal_did in sorted(principals_v1.keys()):
        record = principals_v1.get(principal_did)
        if not isinstance(record, dict):
            continue
        if tenant_filter and str(record.get("tenant_id") or "").strip() != tenant_filter:
            continue
        if normalized_status and str(record.get("status") or "").strip().lower() != normalized_status:
            continue
        rows.append(_annotate_control_plane_row(record, kind="principal"))
    sliced = rows[offset : offset + limit]
    return {"status": "ok", "principals": sliced, "count": len(sliced), "total_count": len(rows)}


@router.get("/principals")
def list_principals(
    request: Request,
    tenant_id: str | None = Query(None),
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db=Depends(get_db),
):
    """Return actor-registry principal rows keyed by principal DID."""
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")

    with log_operation(LOGGER, "admin_list_principals", request=request) as ctx:
        principals_v1 = _load_registered_principals_v1(db)
        rows: list[dict[str, Any]] = []
        tenant_filter = str(tenant_id or "").strip()
        normalized_status = str(status_filter or "").strip().lower()
        for principal_did in sorted(principals_v1.keys()):
            record = principals_v1.get(principal_did)
            if not isinstance(record, dict):
                continue
            if tenant_filter and str(record.get("tenant_id") or "").strip() != tenant_filter:
                continue
            if normalized_status and str(record.get("status") or "").strip().lower() != normalized_status:
                continue
            rows.append(_annotate_control_plane_row(record, kind="principal"))
        sliced = rows[offset : offset + limit]
        ctx.update({"principal_count": len(sliced), "total_count": len(rows)})
        return {"principals": sliced, "count": len(sliced), "total_count": len(rows)}


@router.get("/principals/lookup/by-key-ref")
def lookup_principal_by_key_ref(
    request: Request,
    principal_key_ref: str = Query(..., min_length=3),
    tenant_id: str | None = Query(None),
    db=Depends(get_db),
):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")

    principals_v1 = _load_registered_principals_v1(db)
    resolution = _resolve_principal_by_key_ref(
        principals_v1,
        principal_key_ref=principal_key_ref,
        tenant_id=tenant_id,
    )
    outcome = str(resolution.get("outcome") or "not_found")
    if outcome == "not_found":
        raise HTTPException(status_code=404, detail=resolution)
    if outcome == "conflict":
        raise HTTPException(status_code=409, detail=resolution)
    return resolution


@router.get("/principals/lookup/by-contact")
def lookup_principals_by_contact(
    request: Request,
    email: str | None = Query(None),
    phone: str | None = Query(None),
    tenant_id: str | None = Query(None),
    status_filter: str = Query("active", alias="status"),
    db=Depends(get_db),
):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")

    if not str(email or "").strip() and not str(phone or "").strip():
        raise HTTPException(status_code=400, detail="email or phone is required")

    principals_v1 = _load_registered_principals_v1(db)
    rows = _find_principals_by_contact(
        principals_v1,
        email=email,
        phone=phone,
        tenant_id=tenant_id,
        status_filter=status_filter,
    )
    return {"principals": rows}


@router.get("/principals/{principal_did}")
def get_principal(principal_did: str, request: Request, db=Depends(get_db)):
    """Return a single actor-registry principal record by DID."""
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")

    principal_key = principal_did.strip()
    if not principal_key:
        raise HTTPException(status_code=400, detail="principal_did is required")

    principals_v1 = _load_registered_principals_v1(db)
    record = principals_v1.get(principal_key)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="principal not found")
    return {"principal": record}


@router.post("/principals")
def create_principal(request: Request, payload: PrincipalCreateRequest, db=Depends(get_db)):
    """Idempotently create or update actor-registry principal metadata keyed by principal DID."""
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")

    with log_operation(LOGGER, "admin_create_principal", request=request) as ctx:
        principal_did = payload.resolved_principal_did()
        if not principal_did:
            raise HTTPException(status_code=400, detail="principal_did is required")

        principals_v1 = _load_registered_principals_v1(db)
        existing = principals_v1.get(principal_did)
        record = _upsert_principal_record(
            existing=existing if isinstance(existing, dict) else None,
            principal_did=principal_did,
            payload=payload,
        )
        _ensure_principal_registry_uniqueness(
            principals_v1,
            principal_did=principal_did,
            tenant_id=str(record.get("tenant_id") or "").strip(),
            key_references=record.get("principal_key_refs")
            if isinstance(record.get("principal_key_refs"), list)
            else record.get("key_references")
            if isinstance(record.get("key_references"), list)
            else [],
            canonical_subject=str(record.get("canonical_subject") or "").strip(),
        )
        principals_v1[principal_did] = record
        created = not isinstance(existing, dict)
        updated = isinstance(existing, dict)
        canonical = _persist_registered_principals_v1(db, principals_v1)
        record = canonical.get(principal_did)
        ctx.update({"principal_did": principal_did, "created": created, "updated": updated})
        return {
            "status": "ok",
            "created": created,
            "updated": updated,
            "principal": record,
        }


@control_plane_router.post("/principals")
def control_plane_upsert_principal(request: Request, payload: PrincipalCreateRequest, db=Depends(get_db)):
    _require_control_plane_operator(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")

    principal_did = payload.resolved_principal_did()
    if not principal_did:
        raise HTTPException(status_code=400, detail="principal_did is required")
    fingerprint = _payload_fingerprint(
        "control_plane_upsert_principal",
        {
            "principal_did": principal_did,
            "payload": payload.model_dump(mode="json"),
        },
    )
    replay = _replay_control_plane_mutation(db, idempotency_key=payload.idempotency_key, fingerprint=fingerprint)
    if replay is not None:
        return replay

    principals_v1 = _load_registered_principals_v1(db)
    existing = principals_v1.get(principal_did)
    record = _upsert_principal_record(
        existing=existing if isinstance(existing, dict) else None,
        principal_did=principal_did,
        payload=payload,
    )
    if isinstance(existing, dict):
        _validate_lifecycle_transition(
            current_status=existing.get("status"),
            target_status=record["status"],
            transitions=_ENTITY_ALLOWED_TRANSITIONS,
            field_name="status",
        )
        record["created_by_principal_id"] = str(existing.get("created_by_principal_id") or "").strip() or None
    else:
        record["created_by_principal_id"] = str(principal_from_request(request).principal_id or "").strip() or None
    record["last_changed_by_principal_id"] = str(principal_from_request(request).principal_id or "").strip() or None
    record["provisioning_source"] = str(payload.provisioning_source or record.get("provisioning_source") or "control_plane_api_v1").strip() or "control_plane_api_v1"
    target_status = str(payload.status or "").strip().lower()
    if target_status in {"pending", "queued", "provisioning"}:
        record["status"] = "pending"
        metadata = dict(record.get("metadata") or {}) if isinstance(record.get("metadata"), dict) else {}
        metadata["provisioning_state"] = "pending_provisioning"
        record["metadata"] = metadata
    elif target_status in {"disabled"}:
        record["status"] = "disabled"
    elif target_status in {"active", "ready", "provisioned", "completed"}:
        record["status"] = "active"
        metadata = dict(record.get("metadata") or {}) if isinstance(record.get("metadata"), dict) else {}
        metadata["provisioning_state"] = "active"
        record["metadata"] = metadata
    principals_v1[principal_did] = record
    canonical = _persist_registered_principals_v1(db, principals_v1)
    persisted = canonical.get(principal_did)
    response = _build_control_plane_response(
        resource_key="principal",
        record=persisted or record,
        entity_type="principal",
        previous_status=str(existing.get("status") or "").strip().lower() if isinstance(existing, dict) else None,
        idempotency_key=payload.idempotency_key,
    )
    _store_control_plane_mutation(db, idempotency_key=payload.idempotency_key, fingerprint=fingerprint, response=response)
    return response


@control_plane_router.post("/principals/codex/provision")
def control_plane_provision_codex_principal(request: Request, payload: CodexPrincipalProvisionRequest, db=Depends(get_db)):
    _require_control_plane_operator(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")

    stable_did = _stable_agent_principal_did(request=request, provider_type="openai", agent_id="codex")
    fingerprint = _payload_fingerprint(
        "control_plane_provision_codex_principal",
        {
            "principal_did": stable_did,
            "payload": payload.model_dump(mode="json"),
        },
    )
    replay = _replay_control_plane_mutation(db, idempotency_key=payload.idempotency_key, fingerprint=fingerprint)
    if replay is not None:
        return replay

    principals_v1 = _load_registered_principals_v1(db)
    existing = principals_v1.get(stable_did)
    principals_v1, record, _ = _ensure_codex_principal(request=request, registry=principals_v1, payload=payload)
    if isinstance(existing, dict):
        record["created_by_principal_id"] = str(existing.get("created_by_principal_id") or "").strip() or None
    else:
        record["created_by_principal_id"] = str(principal_from_request(request).principal_id or "").strip() or None
    record["last_changed_by_principal_id"] = str(principal_from_request(request).principal_id or "").strip() or None
    principals_v1[stable_did] = record
    canonical = _persist_registered_principals_v1(db, principals_v1)
    persisted = canonical.get(stable_did)
    response = _build_control_plane_response(
        resource_key="principal",
        record=persisted or record,
        entity_type="principal",
        previous_status=str(existing.get("status") or "").strip().lower() if isinstance(existing, dict) else None,
        idempotency_key=payload.idempotency_key,
    )
    _store_control_plane_mutation(db, idempotency_key=payload.idempotency_key, fingerprint=fingerprint, response=response)
    return response


@control_plane_router.get("/surfaces")
def control_plane_list_surfaces(request: Request, db=Depends(get_db)):
    _require_control_plane_operator(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    registry_v1 = _load_control_plane_surfaces_v1(db)
    return {"status": "ok", "surfaces": [_annotate_control_plane_row(registry_v1[key], kind="surface") for key in sorted(registry_v1.keys())]}


@control_plane_router.get("/providers")
def control_plane_list_providers(request: Request, db=Depends(get_db)):
    _require_control_plane_operator(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    registry_v1 = _load_provider_credentials_v1(db)
    return {"status": "ok", "providers": [_annotate_control_plane_row(_provider_public_view(registry_v1[key]), kind="provider") for key in sorted(registry_v1.keys())]}


@control_plane_router.post("/providers")
def control_plane_upsert_provider(request: Request, payload: ProviderCredentialUpsertRequest, db=Depends(get_db)):
    _require_control_plane_operator(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")
    provider_id = str(payload.provider_id or "").strip()
    if not provider_id:
        raise HTTPException(status_code=400, detail="provider_id is required")
    fingerprint = _payload_fingerprint(
        "control_plane_upsert_provider",
        {"provider_id": provider_id, "payload": payload.model_dump(mode="json")},
    )
    replay = _replay_control_plane_mutation(db, idempotency_key=payload.idempotency_key, fingerprint=fingerprint)
    if replay is not None:
        return replay
    registry_v1 = _load_provider_credentials_v1(db)
    existing = registry_v1.get(provider_id) if isinstance(registry_v1.get(provider_id), dict) else None
    record = _build_provider_credential_record(request=request, payload=payload, existing=existing)
    _ensure_registry_canonical_subject_uniqueness(
        registry=registry_v1,
        record_key=provider_id,
        canonical_subject=str(record.get("canonical_subject") or "").strip(),
        key_field="provider_id",
    )
    registry_v1[provider_id] = record
    canonical = _persist_provider_credentials_v1(db, registry_v1)
    public_record = _provider_public_view(canonical.get(provider_id) or record)
    response = _build_control_plane_response(
        resource_key="provider",
        record=public_record,
        idempotency_key=payload.idempotency_key,
    )
    _store_control_plane_mutation(db, idempotency_key=payload.idempotency_key, fingerprint=fingerprint, response=response)
    return response


@control_plane_router.get("/model-bindings")
def control_plane_list_model_bindings(request: Request, db=Depends(get_db)):
    _require_control_plane_operator(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    registry_v1 = _load_model_bindings_v1(db)
    surface_id = str(request.query_params.get("surface_id") or "").strip()
    if surface_id:
        relationships = _effective_control_plane_relationships(db)
        allowed_principals = {
            str(rel.get("subject_entity_id") or "").strip()
            for rel in relationships.values()
            if isinstance(rel, dict)
            and str(rel.get("subject_entity_type") or "").strip().lower() == "principal"
            and str(rel.get("object_entity_type") or "").strip().lower() == "surface"
            and str(rel.get("object_entity_id") or "").strip() == surface_id
            and str(rel.get("relationship_type") or "").strip().lower() == "can_access_surface"
            and str(rel.get("enabled_state") or "").strip().lower() == "enabled"
        }
        filtered: dict[str, dict[str, Any]] = {}
        for binding_id, binding in registry_v1.items():
            if not isinstance(binding, dict):
                continue
            linked_model_principal = str(binding.get("linked_model_principal") or "").strip()
            app_surfaces = {
                str(item).strip()
                for item in (binding.get("app_surfaces") or [])
                if str(item).strip()
            }
            if linked_model_principal:
                if linked_model_principal in allowed_principals:
                    filtered[binding_id] = binding
            elif surface_id in app_surfaces:
                filtered[binding_id] = binding
        registry_v1 = filtered
    return {"status": "ok", "model_bindings": [_annotate_control_plane_row(registry_v1[key], kind="model_binding") for key in sorted(registry_v1.keys())]}


@control_plane_router.post("/model-bindings")
def control_plane_upsert_model_binding(request: Request, payload: ModelBindingUpsertRequest, db=Depends(get_db)):
    _require_control_plane_operator(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")
    binding_id = str(payload.binding_id or "").strip()
    if not binding_id:
        raise HTTPException(status_code=400, detail="binding_id is required")
    fingerprint = _payload_fingerprint(
        "control_plane_upsert_model_binding",
        {"binding_id": binding_id, "payload": payload.model_dump(mode="json")},
    )
    replay = _replay_control_plane_mutation(db, idempotency_key=payload.idempotency_key, fingerprint=fingerprint)
    if replay is not None:
        return replay
    registry_v1 = _load_model_bindings_v1(db)
    principals_v1 = _load_registered_principals_v1(db)
    existing = registry_v1.get(binding_id) if isinstance(registry_v1.get(binding_id), dict) else None
    principals_v1, model_principal, _ = _ensure_model_principal_for_binding(
        request=request,
        registry=principals_v1,
        provider_type=payload.provider_type,
        model_id=payload.model_id,
        linked_model_principal=payload.linked_model_principal or (existing.get("linked_model_principal") if isinstance(existing, dict) else None),
        binding_id=binding_id,
    )
    record = _build_model_binding_record(request=request, payload=payload, existing=existing)
    record["linked_model_principal"] = str(model_principal.get("principal_did") or "").strip() or None
    _ensure_registry_canonical_subject_uniqueness(
        registry=registry_v1,
        record_key=binding_id,
        canonical_subject=str(record.get("canonical_subject") or "").strip(),
        key_field="binding_id",
    )
    _persist_registered_principals_v1(db, principals_v1)
    registry_v1[binding_id] = record
    canonical = _persist_model_bindings_v1(db, registry_v1)
    response = _build_control_plane_response(
        resource_key="model_binding",
        record=canonical.get(binding_id) or record,
        idempotency_key=payload.idempotency_key,
    )
    _store_control_plane_mutation(db, idempotency_key=payload.idempotency_key, fingerprint=fingerprint, response=response)
    return response


@control_plane_router.post("/surfaces")
def control_plane_upsert_surface(request: Request, payload: SurfaceUpsertRequest, db=Depends(get_db)):
    _require_control_plane_operator(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")
    surface_id = str(payload.surface_id or "").strip()
    if not surface_id:
        raise HTTPException(status_code=400, detail="surface_id is required")
    fingerprint = _payload_fingerprint(
        "control_plane_upsert_surface",
        {
            "surface_id": surface_id,
            "payload": payload.model_dump(mode="json"),
        },
    )
    replay = _replay_control_plane_mutation(db, idempotency_key=payload.idempotency_key, fingerprint=fingerprint)
    if replay is not None:
        return replay
    registry_v1 = _load_control_plane_surfaces_v1(db)
    existing = registry_v1.get(surface_id) if isinstance(registry_v1.get(surface_id), dict) else None
    record = _build_control_plane_surface_record(
        request=request,
        payload=payload,
        existing=existing,
    )
    _ensure_registry_canonical_subject_uniqueness(
        registry=registry_v1,
        record_key=surface_id,
        canonical_subject=str(record.get("canonical_subject") or "").strip(),
        key_field="surface_id",
    )
    if isinstance(existing, dict):
        _validate_lifecycle_transition(
            current_status=existing.get("status"),
            target_status=record["status"],
            transitions=_ENTITY_ALLOWED_TRANSITIONS,
            field_name="status",
        )
    registry_v1[surface_id] = record
    canonical = _persist_control_plane_surfaces_v1(db, registry_v1)
    response = _build_control_plane_response(
        resource_key="surface",
        record=canonical.get(surface_id) or record,
        entity_type="surface",
        previous_status=str(existing.get("status") or "").strip().lower() if isinstance(existing, dict) else None,
        idempotency_key=payload.idempotency_key,
    )
    _store_control_plane_mutation(db, idempotency_key=payload.idempotency_key, fingerprint=fingerprint, response=response)
    return response


@control_plane_router.get("/relationships")
def control_plane_list_relationships(request: Request, db=Depends(get_db)):
    _require_control_plane_operator(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    registry_v1 = _effective_control_plane_relationships(db)
    return {"status": "ok", "relationships": [_annotate_control_plane_row(registry_v1[key], kind="relationship") for key in sorted(registry_v1.keys())]}


@control_plane_router.post("/relationships")
def control_plane_upsert_relationship(request: Request, payload: RelationshipUpsertRequest, db=Depends(get_db)):
    _require_control_plane_operator(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")
    subject_entity_type = str(payload.subject_entity_type or "").strip().lower()
    object_entity_type = str(payload.object_entity_type or "").strip().lower()
    normalized_subject_entity_id = (
        _normalize_related_ledger_id(payload.subject_entity_id)
        if subject_entity_type == "ledger"
        else str(payload.subject_entity_id or "").strip()
    )
    normalized_object_entity_id = (
        _normalize_related_ledger_id(payload.object_entity_id)
        if object_entity_type == "ledger"
        else str(payload.object_entity_id or "").strip()
    )
    normalized_ledger_id = _normalize_related_ledger_id(payload.ledger_id)
    relationship_id = str(payload.relationship_id or "").strip()
    if not relationship_id:
        relationship_id = "::".join(
            [
                subject_entity_type,
                str(normalized_subject_entity_id or "").strip(),
                object_entity_type,
                str(normalized_object_entity_id or "").strip(),
            ]
        )
        payload = payload.model_copy(
            update={
                "relationship_id": relationship_id,
                "subject_entity_id": normalized_subject_entity_id,
                "object_entity_id": normalized_object_entity_id,
                "ledger_id": normalized_ledger_id,
            }
        )
    fingerprint = _payload_fingerprint(
        "control_plane_upsert_relationship",
        {
            "relationship_id": relationship_id,
            "payload": payload.model_dump(mode="json"),
        },
    )
    replay = _replay_control_plane_mutation(db, idempotency_key=payload.idempotency_key, fingerprint=fingerprint)
    if replay is not None:
        return replay
    registry_v1 = _load_control_plane_relationships_v1(db)
    existing = registry_v1.get(relationship_id) if isinstance(registry_v1.get(relationship_id), dict) else None
    record = _build_control_plane_relationship_record(
        request=request,
        payload=payload,
        existing=existing,
    )
    if isinstance(existing, dict):
        _validate_lifecycle_transition(
            current_status=existing.get("status"),
            target_status=record["status"],
            transitions=_RELATIONSHIP_ALLOWED_TRANSITIONS,
            field_name="status",
        )
    registry_v1[relationship_id] = record
    canonical = _persist_control_plane_relationships_v1(db, registry_v1)
    response = _build_control_plane_response(
        resource_key="relationship",
        record=canonical.get(relationship_id) or record,
        idempotency_key=payload.idempotency_key,
    )
    _store_control_plane_mutation(db, idempotency_key=payload.idempotency_key, fingerprint=fingerprint, response=response)
    return response


@control_plane_router.post("/impact-analysis")
def control_plane_impact_analysis(request: Request, payload: ImpactAnalysisRequest, db=Depends(get_db)):
    _require_control_plane_operator(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    relationships = _effective_control_plane_relationships(db)
    surfaces = _load_control_plane_surfaces_v1(db)
    principals = _load_registered_principals_v1(db)
    ledgers = _load_registered_ledgers_v1(db)
    bindings = _load_model_bindings_v1(db)
    caller = principal_from_request(request)
    report = calculate_removal_impact(
        payload.entity_type,
        payload.entity_id,
        payload.ledger_id,
        relationships=relationships,
        surfaces=surfaces,
        principals=principals,
        ledgers=ledgers,
        bindings=bindings,
        caller_principal_id=caller.principal_id if caller else None,
    )
    return JSONResponse(
        {
            "status": "ok",
            "impact": report.as_dict(),
            "confirmation_token": report.confirmation_token,
        },
        status_code=200,
    )


@control_plane_router.post("/connections/remove")
def control_plane_remove_connection(request: Request, payload: ConnectionRemoveRequest, db=Depends(get_db)):
    _require_control_plane_operator(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")

    entity_type = str(payload.entity_type or "").strip().lower()
    entity_id = str(payload.entity_id or "").strip()
    ledger_id = str(payload.ledger_id or "").strip()
    if entity_type not in {"principal", "ledger", "surface"} or not entity_id or not ledger_id:
        raise HTTPException(status_code=400, detail="entity_type, entity_id, and ledger_id are required")

    relationships = _effective_control_plane_relationships(db)
    surfaces = _load_control_plane_surfaces_v1(db)
    principals = _load_registered_principals_v1(db)
    ledgers = _load_registered_ledgers_v1(db)
    bindings = _load_model_bindings_v1(db)
    report = calculate_removal_impact(
        entity_type,
        entity_id,
        ledger_id,
        relationships=relationships,
        surfaces=surfaces,
        principals=principals,
        ledgers=ledgers,
        bindings=bindings,
    )
    if report.confirmation_token != str(payload.confirmation_token or "").strip():
        return JSONResponse(
            {
                "status": "stale",
                "error": "Impact has changed; review the updated impact report and confirm again.",
                "impact": report.as_dict(),
                "confirmation_token": report.confirmation_token,
            },
            status_code=409,
        )

    if any("LAST_T1_OPERATOR" in w for w in report.critical_warnings):
        raise HTTPException(status_code=422, detail="Cannot remove last T1 operator without transfer")

    explicit_relationships = _load_control_plane_relationships_v1(db)
    filtered: dict[str, dict[str, Any]] = {}
    removed_relationship_ids: list[str] = []
    for relationship_id, record in explicit_relationships.items():
        if not isinstance(record, dict):
            continue
        rel_type = str(record.get("relationship_type") or "").strip().lower()
        subject_type = str(record.get("subject_entity_type") or "").strip().lower()
        subject_id = str(record.get("subject_entity_id") or "").strip()
        object_type = str(record.get("object_entity_type") or "").strip().lower()
        object_id = str(record.get("object_entity_id") or "").strip()

        remove = False
        if rel_type in {"member_of", "member_of_ledger", "related_to", "surface_bound_to_ledger"}:
            if subject_type == entity_type and subject_id == entity_id and object_type == "ledger" and object_id == ledger_id:
                remove = True
        elif rel_type == "links_to" and entity_type == "ledger":
            if subject_type == entity_type and subject_id == entity_id and object_type == "ledger" and object_id == ledger_id:
                remove = True
            if object_type == entity_type and object_id == entity_id and subject_type == "ledger" and subject_id == ledger_id:
                remove = True
        elif rel_type == "access_grant":
            # Remove access grants that involve the affected entity/ledger pair.
            if entity_type == "principal" and subject_type == "surface" and object_type == "ledger" and object_id == ledger_id:
                # Grant tied to the removed ledger; drop it if the hosted principal is affected.
                hosted_by_surface = {
                    str(r.get("subject_entity_id") or "").strip()
                    for r in explicit_relationships.values()
                    if isinstance(r, dict)
                    and str(r.get("relationship_type") or "").strip().lower() == "can_access_surface"
                    and str(r.get("object_entity_type") or "").strip().lower() == "surface"
                    and str(r.get("object_entity_id") or "").strip() == subject_id
                }
                if entity_id in hosted_by_surface:
                    remove = True
            elif entity_type == "surface" and subject_type == "surface" and subject_id == entity_id and object_type == "ledger" and object_id == ledger_id:
                remove = True

        if remove:
            removed_relationship_ids.append(relationship_id)
            continue
        filtered[relationship_id] = record

    if len(filtered) != len(explicit_relationships):
        _persist_control_plane_relationships_v1(db, filtered)

    if entity_type == "surface":
        surfaces_v1 = _load_control_plane_surfaces_v1(db)
        if entity_id in surfaces_v1:
            del surfaces_v1[entity_id]
            _persist_control_plane_surfaces_v1(db, surfaces_v1)

    return JSONResponse(
        {
            "status": "ok",
            "committed_at": _now_iso(),
            "broken_relations": report.broken_relations,
            "removed_relationship_ids": removed_relationship_ids,
        },
        status_code=200,
    )


@control_plane_router.post("/entities/remove")
def control_plane_remove_entity(request: Request, payload: EntityRemoveRequest, db=Depends(get_db)):
    _require_control_plane_operator(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")

    entity_type = str(payload.entity_type or "").strip().lower()
    entity_id = str(payload.entity_id or "").strip()
    if entity_type not in {"ledger", "principal", "surface"} or not entity_id:
        raise HTTPException(status_code=400, detail="entity_type and entity_id are required")

    timestamp = _now_iso()
    relationships_removed = 0
    surfaces_updated = 0
    principals_updated = 0
    model_bindings_updated = 0
    clear_result: dict[str, Any] | None = None

    explicit_relationships = _load_control_plane_relationships_v1(db)
    filtered_relationships: dict[str, dict[str, Any]] = {}
    for relationship_id, record in explicit_relationships.items():
        if not isinstance(record, dict):
            continue
        subject_type = str(record.get("subject_entity_type") or "").strip().lower()
        subject_id = str(record.get("subject_entity_id") or "").strip()
        object_type = str(record.get("object_entity_type") or "").strip().lower()
        object_id = str(record.get("object_entity_id") or "").strip()
        if (subject_type == entity_type and subject_id == entity_id) or (object_type == entity_type and object_id == entity_id):
            relationships_removed += 1
            continue
        filtered_relationships[relationship_id] = record
    if len(filtered_relationships) != len(explicit_relationships):
        _persist_control_plane_relationships_v1(db, filtered_relationships)

    if entity_type == "ledger":
        ledgers_v1 = _load_registered_ledgers_v1(db)
        if not isinstance(ledgers_v1.get(entity_id), dict):
            raise HTTPException(status_code=404, detail="ledger not found")
        clear_result = clear_ledger(request, confirm=True, entity=entity_id, dry_run=False, reindex=False, db=db)

        surfaces_v1 = _load_control_plane_surfaces_v1(db)
        mutated_surfaces = False
        for surface_id, record in list(surfaces_v1.items()):
            if not isinstance(record, dict):
                continue
            if str(record.get("ledger_id") or "").strip() != entity_id:
                continue
            updated = dict(record)
            updated["ledger_id"] = None
            updated["updated_at"] = timestamp
            surfaces_v1[surface_id] = updated
            mutated_surfaces = True
            surfaces_updated += 1
        if mutated_surfaces:
            _persist_control_plane_surfaces_v1(db, surfaces_v1)

        principals_v1 = _load_registered_principals_v1(db)
        mutated_principals = False
        for principal_did, record in list(principals_v1.items()):
            if not isinstance(record, dict):
                continue
            metadata = dict(record.get("metadata") or {}) if isinstance(record.get("metadata"), dict) else {}
            if str(record.get("ledger_id") or "").strip() != entity_id and str(metadata.get("ledger_id") or "").strip() != entity_id:
                continue
            updated = dict(record)
            updated["updated_at"] = timestamp
            if str(updated.get("ledger_id") or "").strip() == entity_id:
                updated["ledger_id"] = None
            if str(metadata.get("ledger_id") or "").strip() == entity_id:
                metadata.pop("ledger_id", None)
            updated["metadata"] = metadata
            principals_v1[principal_did] = updated
            mutated_principals = True
            principals_updated += 1
        if mutated_principals:
            _persist_registered_principals_v1(db, principals_v1)

        return {
            "status": "ok",
            "removed": True,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "relationships_removed": relationships_removed,
            "surfaces_updated": surfaces_updated,
            "principals_updated": principals_updated,
            "model_bindings_updated": model_bindings_updated,
            "clear_result": clear_result or {"status": "ok", "entity": entity_id},
        }

    if entity_type == "principal":
        principals_v1 = _load_registered_principals_v1(db)
        if not isinstance(principals_v1.get(entity_id), dict):
            raise HTTPException(status_code=404, detail="principal not found")
        del principals_v1[entity_id]
        _persist_registered_principals_v1(db, principals_v1)

        surfaces_v1 = _load_control_plane_surfaces_v1(db)
        mutated_surfaces = False
        for surface_id, record in list(surfaces_v1.items()):
            if not isinstance(record, dict):
                continue
            if str(record.get("principal_did") or "").strip() != entity_id:
                continue
            updated = dict(record)
            updated["principal_did"] = None
            updated["updated_at"] = timestamp
            surfaces_v1[surface_id] = updated
            mutated_surfaces = True
            surfaces_updated += 1
        if mutated_surfaces:
            _persist_control_plane_surfaces_v1(db, surfaces_v1)

        bindings_v1 = _load_model_bindings_v1(db)
        mutated_bindings = False
        for binding_id, record in list(bindings_v1.items()):
            if not isinstance(record, dict):
                continue
            if str(record.get("linked_model_principal") or "").strip() != entity_id:
                continue
            updated = dict(record)
            updated["linked_model_principal"] = None
            updated["updated_at"] = timestamp
            bindings_v1[binding_id] = updated
            mutated_bindings = True
            model_bindings_updated += 1
        if mutated_bindings:
            _persist_model_bindings_v1(db, bindings_v1)

    elif entity_type == "surface":
        surfaces_v1 = _load_control_plane_surfaces_v1(db)
        if not isinstance(surfaces_v1.get(entity_id), dict):
            raise HTTPException(status_code=404, detail="surface not found")
        del surfaces_v1[entity_id]
        _persist_control_plane_surfaces_v1(db, surfaces_v1)

        bindings_v1 = _load_model_bindings_v1(db)
        mutated_bindings = False
        for binding_id, record in list(bindings_v1.items()):
            if not isinstance(record, dict):
                continue
            app_surfaces = [str(item).strip() for item in (record.get("app_surfaces") or []) if str(item).strip()]
            if entity_id not in app_surfaces:
                continue
            updated = dict(record)
            updated["app_surfaces"] = [item for item in app_surfaces if item != entity_id]
            updated["updated_at"] = timestamp
            bindings_v1[binding_id] = updated
            mutated_bindings = True
            model_bindings_updated += 1
        if mutated_bindings:
            _persist_model_bindings_v1(db, bindings_v1)

    return {
        "status": "ok",
        "removed": True,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "relationships_removed": relationships_removed,
        "surfaces_updated": surfaces_updated,
        "principals_updated": principals_updated,
        "model_bindings_updated": model_bindings_updated,
    }


@control_plane_router.get("/submissions")
def control_plane_list_submissions(request: Request, db=Depends(get_db)):
    _require_control_plane_operator(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    registry_v1 = _load_control_plane_submissions_v1(db)
    return {"status": "ok", "submissions": [_annotate_control_plane_row(registry_v1[key], kind="submission") for key in sorted(registry_v1.keys())]}


@control_plane_router.get("/submissions/{submission_ref}")
def control_plane_get_submission(request: Request, submission_ref: str, db=Depends(get_db)):
    _require_control_plane_operator(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    registry_v1 = _load_control_plane_submissions_v1(db)
    record = registry_v1.get(str(submission_ref or "").strip())
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="submission not found")
    return {"status": "ok", "submission": record}


@control_plane_router.post("/submissions")
def control_plane_submit_mutation(
    request: Request,
    payload: ControlPlaneSubmissionRequest,
    db=Depends(get_db),
):
    _require_control_plane_operator(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")
    fingerprint = _payload_fingerprint(
        "control_plane_submit_mutation",
        payload.model_dump(mode="json"),
    )
    replay = _replay_control_plane_mutation(db, idempotency_key=payload.idempotency_key, fingerprint=fingerprint)
    if replay is not None:
        return replay
    registry_v1 = _load_control_plane_submissions_v1(db)
    existing = None
    submission_ref = str(payload.submission_ref or "").strip()
    if submission_ref and isinstance(registry_v1.get(submission_ref), dict):
        existing = registry_v1.get(submission_ref)
    record = _build_control_plane_submission_record(request=request, payload=payload, existing=existing)
    submission_ref = str(record.get("submission_ref") or "").strip()
    registry_v1[submission_ref] = record
    canonical = _persist_control_plane_submissions_v1(db, registry_v1)
    response = {
        "status": "ok",
        "execution_mode": "submitted_for_approval",
        "submission_status": "submitted",
        "mutation_ref": str(canonical.get(submission_ref, {}).get("submission_ref") or submission_ref),
        "submission_ref": submission_ref,
        "submitted_at": str(canonical.get(submission_ref, {}).get("created_at") or _now_iso()),
        "idempotency_key": str(payload.idempotency_key or "").strip() or None,
        "submission": _annotate_control_plane_row(canonical.get(submission_ref) or record, kind="submission"),
    }
    _store_control_plane_mutation(db, idempotency_key=payload.idempotency_key, fingerprint=fingerprint, response=response)
    return response


@control_plane_router.post("/submissions/{submission_ref}/review")
def control_plane_review_submission(
    request: Request,
    submission_ref: str,
    payload: ControlPlaneSubmissionReviewRequest,
    db=Depends(get_db),
):
    _require_control_plane_operator(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")
    normalized_ref = str(submission_ref or "").strip()
    if not normalized_ref:
        raise HTTPException(status_code=400, detail="submission_ref is required")
    action = str(payload.action or "approve").strip().lower()
    if action not in {"approve", "reject"}:
        raise HTTPException(status_code=422, detail="action must be approve or reject")
    registry_v1 = _load_control_plane_submissions_v1(db)
    existing = registry_v1.get(normalized_ref)
    if not isinstance(existing, dict):
        raise HTTPException(status_code=404, detail="submission not found")
    current_status = str(existing.get("submission_status") or existing.get("status") or "").strip().lower()
    updated = dict(existing)
    updated["reviewed_at"] = _now_iso()
    updated["reviewed_by_principal_id"] = str(principal_from_request(request).principal_id or "").strip() or None
    if str(payload.reviewer_note or "").strip():
        updated["reviewer_note"] = str(payload.reviewer_note).strip()

    reviewer = str(principal_from_request(request).principal_id or "").strip() or None

    if action == "reject":
        updated["status"] = "rejected"
        updated["submission_status"] = "rejected"
        updated["rejected_at"] = _now_iso()
        updated["updated_at"] = _now_iso()
        updated["last_changed_by_principal_id"] = reviewer
        lifecycle = list(updated.get("lifecycle") or []) if isinstance(updated.get("lifecycle"), list) else []
        lifecycle.append(_submission_lifecycle_event(status="rejected", actor=reviewer, note=str(payload.reviewer_note or "").strip() or None))
        updated["lifecycle"] = lifecycle
        registry_v1[normalized_ref] = updated
        canonical = _persist_control_plane_submissions_v1(db, registry_v1)
        return {
            "status": "ok",
            "execution_mode": "submitted_for_approval",
            "submission_status": "rejected",
            "submission_ref": normalized_ref,
            "rejected_at": updated["rejected_at"],
            "submission": _annotate_control_plane_row(canonical.get(normalized_ref) or updated, kind="submission"),
        }

    if current_status == "applied":
        return {
            "status": "ok",
            "execution_mode": "submitted_for_approval",
            "submission_status": "applied",
            "submission_ref": normalized_ref,
            "submission": _annotate_control_plane_row(existing, kind="submission"),
        }
    if current_status in {"rejected", "failed"}:
        raise HTTPException(status_code=409, detail=f"submission has already been {current_status}")

    lifecycle = list(updated.get("lifecycle") or []) if isinstance(updated.get("lifecycle"), list) else []
    approved_at = _now_iso()
    lifecycle.append(_submission_lifecycle_event(status="approved", actor=reviewer, note=str(payload.reviewer_note or "").strip() or None))
    updated["status"] = "approved"
    updated["submission_status"] = "approved"
    updated["approved_at"] = approved_at
    updated["updated_at"] = approved_at
    updated["last_changed_by_principal_id"] = reviewer
    updated["lifecycle"] = lifecycle
    registry_v1[normalized_ref] = updated
    _persist_control_plane_submissions_v1(db, registry_v1)

    try:
        result = _apply_control_plane_submission(request=request, submission_ref=normalized_ref, record=updated, db=db)
    except HTTPException as exc:
        failed = dict(updated)
        failed_at = _now_iso()
        failed["status"] = "failed"
        failed["submission_status"] = "failed"
        failed["failed_at"] = failed_at
        failed["updated_at"] = failed_at
        failed["failure_class"] = _classify_submission_failure(status_code=exc.status_code, detail=exc.detail)
        failed["failure_detail"] = exc.detail
        failed["last_changed_by_principal_id"] = reviewer
        failed_lifecycle = list(failed.get("lifecycle") or []) if isinstance(failed.get("lifecycle"), list) else []
        failed_lifecycle.append(_submission_lifecycle_event(status="failed", actor=reviewer, detail=exc.detail))
        failed["lifecycle"] = failed_lifecycle
        registry_v1[normalized_ref] = failed
        canonical = _persist_control_plane_submissions_v1(db, registry_v1)
        return JSONResponse(
            {
                "status": "error",
                "execution_mode": "submitted_for_approval",
                "submission_status": "failed",
                "submission_ref": normalized_ref,
                "approved_at": approved_at,
                "failed_at": failed_at,
                "failure": {
                    "failure_class": failed["failure_class"],
                    "detail": exc.detail,
                },
                "submission": _annotate_control_plane_row(canonical.get(normalized_ref) or failed, kind="submission"),
            },
            status_code=exc.status_code,
        )

    updated["status"] = "applied"
    updated["submission_status"] = "applied"
    updated["updated_at"] = _now_iso()
    updated["applied_at"] = str(result.get("applied_at") or _now_iso()).strip() or _now_iso()
    updated["mutation_ref"] = str(result.get("mutation_ref") or "").strip() or None
    updated["applied_result"] = dict(result)
    updated["last_changed_by_principal_id"] = reviewer
    applied_lifecycle = list(updated.get("lifecycle") or []) if isinstance(updated.get("lifecycle"), list) else []
    applied_lifecycle.append(_submission_lifecycle_event(status="applied", actor=reviewer, detail={"mutation_ref": updated["mutation_ref"]}))
    updated["lifecycle"] = applied_lifecycle
    registry_v1[normalized_ref] = updated
    canonical = _persist_control_plane_submissions_v1(db, registry_v1)
    return {
        "status": "ok",
        "execution_mode": "submitted_for_approval",
        "submission_status": "applied",
        "submission_ref": normalized_ref,
        "approved_at": approved_at,
        "mutation_ref": str(result.get("mutation_ref") or "").strip() or None,
        "applied_at": str(result.get("applied_at") or _now_iso()).strip() or _now_iso(),
        "submission": _annotate_control_plane_row(canonical.get(normalized_ref) or updated, kind="submission"),
        "result": result,
    }


@control_plane_router.post("/entities/activate")
def control_plane_activate_entity(
    request: Request,
    payload: ControlPlaneEntityActivationRequest,
    db=Depends(get_db),
):
    _require_control_plane_operator(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")
    entity_type = str(payload.entity_type or "").strip().lower()
    entity_id = str(payload.entity_id or "").strip()
    target_status = str(payload.status or "active").strip().lower() or "active"
    if entity_type not in {"ledger", "principal", "surface"}:
        raise HTTPException(status_code=422, detail="entity_type must be ledger, principal, or surface")
    if not entity_id:
        raise HTTPException(status_code=400, detail="entity_id is required")
    if target_status != "active":
        raise HTTPException(status_code=422, detail="status must be active")
    fingerprint = _payload_fingerprint(
        "control_plane_activate_entity",
        {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "payload": payload.model_dump(mode="json"),
        },
    )
    replay = _replay_control_plane_mutation(db, idempotency_key=payload.idempotency_key, fingerprint=fingerprint)
    if replay is not None:
        return replay

    if entity_type == "ledger":
        registry_v1 = _load_registered_ledgers_v1(db)
        record = registry_v1.get(entity_id)
        if not isinstance(record, dict):
            raise HTTPException(status_code=404, detail="ledger not found")
        _validate_lifecycle_transition(
            current_status=record.get("status"),
            target_status=target_status,
            transitions=_ENTITY_ALLOWED_TRANSITIONS,
            field_name="status",
        )
        updated = dict(record)
        previous_status = str(record.get("status") or "").strip().lower() or None
        updated["status"] = target_status
        updated["updated_at"] = _now_iso()
        metadata = dict(updated.get("metadata") or {}) if isinstance(updated.get("metadata"), dict) else {}
        metadata["activation_state"] = target_status
        metadata["ledger_access_ready"] = target_status == "active"
        updated["metadata"] = metadata
        updated["last_changed_by_principal_id"] = str(principal_from_request(request).principal_id or "").strip() or None
        updated["submission_ref"] = str(payload.submission_ref or updated.get("submission_ref") or "").strip() or None
        registry_v1[entity_id] = updated
        canonical = _persist_registered_ledgers_v1(db, registry_v1)
        response = _build_control_plane_response(
            resource_key="ledger",
            record=canonical.get(entity_id) or updated,
            entity_type="ledger",
            previous_status=previous_status,
            idempotency_key=payload.idempotency_key,
        )
        _store_control_plane_mutation(db, idempotency_key=payload.idempotency_key, fingerprint=fingerprint, response=response)
        return response

    if entity_type == "principal":
        principals_v1 = _load_registered_principals_v1(db)
        record = principals_v1.get(entity_id)
        if not isinstance(record, dict):
            raise HTTPException(status_code=404, detail="principal not found")
        _validate_lifecycle_transition(
            current_status=record.get("status"),
            target_status=target_status,
            transitions=_ENTITY_ALLOWED_TRANSITIONS,
            field_name="status",
        )
        updated = dict(record)
        previous_status = str(record.get("status") or "").strip().lower() or None
        updated["status"] = "active"
        updated["updated_at"] = _now_iso()
        metadata = dict(updated.get("metadata") or {}) if isinstance(updated.get("metadata"), dict) else {}
        metadata["provisioning_state"] = "active"
        if str(payload.ledger_id or "").strip():
            metadata["ledger_id"] = _normalize_related_ledger_id(payload.ledger_id)
        updated["metadata"] = metadata
        updated["last_changed_by_principal_id"] = str(principal_from_request(request).principal_id or "").strip() or None
        updated["submission_ref"] = str(payload.submission_ref or updated.get("submission_ref") or "").strip() or None
        principals_v1[entity_id] = updated
        canonical = _persist_registered_principals_v1(db, principals_v1)
        response = _build_control_plane_response(
            resource_key="principal",
            record=canonical.get(entity_id) or updated,
            entity_type="principal",
            previous_status=previous_status,
            idempotency_key=payload.idempotency_key,
        )
        _store_control_plane_mutation(db, idempotency_key=payload.idempotency_key, fingerprint=fingerprint, response=response)
        return response

    registry_v1 = _load_control_plane_surfaces_v1(db)
    record = registry_v1.get(entity_id)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="surface not found")
    _validate_lifecycle_transition(
        current_status=record.get("status"),
        target_status=target_status,
        transitions=_ENTITY_ALLOWED_TRANSITIONS,
        field_name="status",
    )
    updated = dict(record)
    previous_status = str(record.get("status") or "").strip().lower() or None
    updated["status"] = target_status
    updated["updated_at"] = _now_iso()
    metadata = dict(updated.get("metadata") or {}) if isinstance(updated.get("metadata"), dict) else {}
    metadata["onboarding_state"] = target_status
    updated["metadata"] = metadata
    updated["last_changed_by_principal_id"] = str(principal_from_request(request).principal_id or "").strip() or None
    updated["submission_ref"] = str(payload.submission_ref or updated.get("submission_ref") or "").strip() or None
    registry_v1[entity_id] = updated
    canonical = _persist_control_plane_surfaces_v1(db, registry_v1)
    response = _build_control_plane_response(
        resource_key="surface",
        record=canonical.get(entity_id) or updated,
        entity_type="surface",
        previous_status=previous_status,
        idempotency_key=payload.idempotency_key,
    )
    _store_control_plane_mutation(db, idempotency_key=payload.idempotency_key, fingerprint=fingerprint, response=response)
    return response


@control_plane_router.post("/benchmarks/publication-jobs")
def control_plane_enqueue_benchmark_publication_job(
    request: Request,
    payload: BenchmarkPublicationJobRequest,
    background_tasks: BackgroundTasks,
    db=Depends(get_db),
):
    _require_benchmark_publication_operator(request)
    principal = principal_from_request(request)
    try:
        job = enqueue_benchmark_publication_job(
            db,
            domain_key=payload.domain_key,
            operator_identity={
                "principal_id": principal.principal_id,
                "principal_type": principal.principal_type,
                "principal_did": principal.principal_did,
            },
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": "unsupported_domain", "reason": str(exc)}) from exc
    background_tasks.add_task(run_benchmark_publication_job, db, job_id=str(job.get("job_id") or ""))
    return {
        "status": "accepted",
        "job": job,
    }


@control_plane_router.get("/benchmarks/publication-jobs/{job_id}")
def control_plane_get_benchmark_publication_job(
    request: Request,
    job_id: str,
    db=Depends(get_db),
):
    _require_benchmark_publication_operator(request)
    job = get_benchmark_publication_job(db, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail={"error": "job_not_found"})
    return {"job": job}


@control_plane_router.get("/benchmarks/publication")
def control_plane_get_canonical_benchmark_publication(
    request: Request,
):
    _require_control_plane_authenticated(request)
    snapshot = get_canonical_publication_snapshot()
    if str(snapshot.get("status") or "").strip() not in {"ok", "unpublished"}:
        return JSONResponse(snapshot, status_code=503)
    return snapshot


@router.post("/principals/{principal_did}/disable")
def disable_principal(
    principal_did: str,
    request: Request,
    payload: PrincipalDisableRequest,
    db=Depends(get_db),
):
    """Disable an existing principal registry record."""
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")

    principal_key = principal_did.strip()
    if not principal_key:
        raise HTTPException(status_code=400, detail="principal_did is required")

    principals_v1 = _load_registered_principals_v1(db)
    record = principals_v1.get(principal_key)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="principal not found")

    timestamp = _now_iso()
    updated = dict(record)
    updated["status"] = "disabled"
    updated["updated_at"] = timestamp
    updated["disabled_at"] = timestamp
    reason = (payload.reason or "").strip()
    updated["disable_reason"] = reason or "disabled_by_admin"
    principals_v1[principal_key] = updated
    canonical = _persist_registered_principals_v1(db, principals_v1)

    return {
        "status": "ok",
        "principal": canonical.get(principal_key),
    }


@router.post("/principals/{principal_did}/status")
def set_principal_status(
    principal_did: str,
    request: Request,
    payload: PrincipalStatusRequest,
    db=Depends(get_db),
):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")

    principal_key = principal_did.strip()
    if not principal_key:
        raise HTTPException(status_code=400, detail="principal_did is required")

    target_status = str(payload.status or "").strip().lower()
    if target_status not in {"active", "disabled"}:
        raise HTTPException(status_code=422, detail="status must be active or disabled")

    principals_v1 = _load_registered_principals_v1(db)
    record = principals_v1.get(principal_key)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="principal not found")

    updated = dict(record)
    updated["status"] = target_status
    updated["updated_at"] = _now_iso()
    if target_status == "disabled":
        updated["disabled_at"] = str(record.get("disabled_at") or "").strip() or _now_iso()
        updated["disable_reason"] = str(payload.reason or "").strip() or "status_set_disabled"
    else:
        updated["disabled_at"] = None
        updated["disable_reason"] = None

    principals_v1[principal_key] = updated
    canonical = _persist_registered_principals_v1(db, principals_v1)
    return {"status": "ok", "principal": canonical.get(principal_key)}


@router.get("/account-requests")
def list_account_requests(
    request: Request,
    status_filter: str | None = Query(None, alias="status"),
    db=Depends(get_db),
):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")

    signups = _load_pilot_signups_v1(db)
    normalized_status = str(status_filter or "").strip().lower()
    rows: list[dict[str, Any]] = []
    for signup_id in sorted(signups.keys()):
        record = signups.get(signup_id)
        if not isinstance(record, dict):
            continue
        signup_method = str(record.get("signup_method") or "").strip()
        if not signup_method:
            continue
        if normalized_status:
            # Map signup/provisioning status to request status
            request_status = _account_request_status(record)
            if request_status != normalized_status:
                continue
        rows.append(dict(record))
    return {"requests": rows, "count": len(rows)}


@router.post("/account-requests/{signup_id}/decide")
def decide_account_request(
    signup_id: str,
    request: Request,
    payload: AccountRequestDecision,
    db=Depends(get_db),
):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")

    signup_key = signup_id.strip()
    if not signup_key:
        raise HTTPException(status_code=400, detail="signup_id is required")

    decision = str(payload.decision or "").strip().lower()
    if decision not in {"approve", "reject"}:
        raise HTTPException(status_code=422, detail="decision must be approve or reject")

    signups = _load_pilot_signups_v1(db)
    signup = signups.get(signup_key)
    if not isinstance(signup, dict):
        raise HTTPException(status_code=404, detail="account request not found")

    principal_did = str(signup.get("principal_did") or "").strip()
    if not principal_did:
        raise HTTPException(status_code=400, detail="signup has no principal_did")

    principals_v1 = _load_registered_principals_v1(db)
    principal = principals_v1.get(principal_did)
    if not isinstance(principal, dict):
        raise HTTPException(status_code=404, detail="principal not found")

    now_iso = _now_iso()
    updated_signup = dict(signup)
    updated_principal = dict(principal)

    credential_offer = None
    if decision == "approve":
        updated_principal["status"] = "active"
        updated_principal["updated_at"] = now_iso
        if isinstance(updated_principal.get("metadata"), dict):
            updated_principal["metadata"] = dict(updated_principal["metadata"])
            updated_principal["metadata"]["operator_approved_at"] = now_iso
        updated_signup["onboarding_status"] = "not_started"
        updated_signup["approval_status"] = "approved"
        updated_signup["updated_at"] = now_iso

        # Generate credential offer for VC issuance (DSS-148)
        wallet = signup.get("wallet")
        wallet_provider = "altme"
        if isinstance(wallet, dict):
            wallet_provider = str(wallet.get("provider") or "altme").strip().lower() or "altme"
        from backend.api.wallet import _build_credential_offer
        credential_offer = _build_credential_offer(signup_key, wallet_provider)
        updated_signup["credential_offer"] = credential_offer
    else:
        updated_principal["status"] = "rejected"
        updated_principal["updated_at"] = now_iso
        if isinstance(updated_principal.get("metadata"), dict):
            updated_principal["metadata"] = dict(updated_principal["metadata"])
            updated_principal["metadata"]["operator_rejected_at"] = now_iso
            updated_principal["metadata"]["rejection_reason"] = str(payload.reason or "").strip() or "operator_rejected"
        updated_signup["approval_status"] = "rejected"
        updated_signup["updated_at"] = now_iso

    principals_v1[principal_did] = updated_principal
    _persist_registered_principals_v1(db, principals_v1)

    signups[signup_key] = updated_signup
    _persist_pilot_signups_v1(db, signups)

    response = {
        "status": "ok",
        "decision": decision,
        "signup_id": signup_key,
        "principal_did": principal_did,
        "principal_status": updated_principal["status"],
    }
    if credential_offer is not None:
        response["credential_offer"] = credential_offer
    return response


def _account_request_status(record: dict[str, Any]) -> str:
    approval_status = str(record.get("approval_status") or "").strip().lower()
    if approval_status == "rejected":
        return "rejected"
    if approval_status == "approved":
        return "approved"
    onboarding_status = str(record.get("onboarding_status") or "").strip().lower()
    provisioning_status = str(record.get("provisioning_status") or "").strip().lower()
    if provisioning_status in {"succeeded", "complete", "completed"}:
        return "approved"
    if onboarding_status in {"submitted", "accepted", "complete", "completed"}:
        return "approved"
    return "pending"


@router.post("/account-requests/{signup_id}/migrate-account")
def migrate_account_request(
    signup_id: str,
    request: Request,
    db=Depends(get_db),
):
    """Migrate an existing pilot signup from acct_pilot_default to a unique per-wallet account."""
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")

    signup_key = signup_id.strip()
    signups = _load_pilot_signups_v1(db)
    signup = signups.get(signup_key)
    if not isinstance(signup, dict):
        raise HTTPException(status_code=404, detail="account request not found")

    principal_did = str(signup.get("principal_did") or "").strip()
    if not principal_did:
        raise HTTPException(status_code=400, detail="signup has no principal_did")

    wallet = signup.get("wallet")
    wallet_did = principal_did
    if isinstance(wallet, dict):
        wallet_did = str(wallet.get("did") or principal_did).strip() or principal_did

    import hashlib
    digest = hashlib.sha256(wallet_did.encode("utf-8")).hexdigest()[:16]
    new_account_id = f"acct_pilot_{digest}"
    new_signup_id = f"pilot_signup:{digest}"

    updated_signup = dict(signup)
    old_account_id = str(updated_signup.get("account_id") or "").strip()
    updated_signup["account_id"] = new_account_id
    updated_signup["signup_id"] = new_signup_id
    updated_signup["updated_at"] = _now_iso()

    principals_v1 = _load_registered_principals_v1(db)
    principal = principals_v1.get(principal_did)
    if isinstance(principal, dict):
        updated_principal = dict(principal)
        metadata = dict(updated_principal.get("metadata") or {})
        metadata["account_id"] = new_account_id
        metadata["migrated_from_account_id"] = old_account_id or "acct_pilot_default"
        metadata["migrated_at"] = _now_iso()
        updated_principal["metadata"] = metadata
        updated_principal["updated_at"] = _now_iso()
        principals_v1[principal_did] = updated_principal
        _persist_registered_principals_v1(db, principals_v1)

    # Remove old signup key and add under new key
    if signup_key in signups:
        del signups[signup_key]
    signups[new_signup_id] = updated_signup
    _persist_pilot_signups_v1(db, signups)

    return {
        "status": "ok",
        "signup_id": new_signup_id,
        "old_signup_id": signup_key,
        "principal_did": principal_did,
        "old_account_id": old_account_id,
        "new_account_id": new_account_id,
    }


@router.post("/principals/{principal_did}/bind-key-ref")
def bind_principal_key_ref(
    principal_did: str,
    request: Request,
    payload: PrincipalKeyRefBindRequest,
    db=Depends(get_db),
):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")

    principals_v1 = _load_registered_principals_v1(db)
    updated = _bind_key_ref_to_principal(
        principals_v1,
        principal_did=principal_did,
        principal_key_ref=payload.principal_key_ref,
        tenant_id=payload.tenant_id,
        binding_metadata=payload.binding_metadata,
    )
    principals_v1[str(principal_did).strip()] = updated
    canonical = _persist_registered_principals_v1(db, principals_v1)
    event = _append_binding_event_v1(
        db,
        principal_did=str(principal_did).strip(),
        tenant_id=str(updated.get("tenant_id") or "").strip(),
        principal_key_ref=_normalize_principal_key_reference(payload.principal_key_ref),
        issuer=payload.issuer,
        reason=payload.reason,
        evidence_refs=payload.evidence_refs,
        idempotency_key=payload.idempotency_key,
    )
    return {"status": "ok", "principal": canonical.get(str(principal_did).strip()), "binding_event": event}


@router.get("/principals/{principal_did}/binding-events")
def list_principal_binding_events(
    principal_did: str,
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    db=Depends(get_db),
):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")

    principal_key = principal_did.strip()
    principals_v1 = _load_registered_principals_v1(db)
    record = principals_v1.get(principal_key)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="principal not found")
    events = _list_binding_events_v1(db, principal_did=principal_key, limit=limit)
    return {"principal": record, "binding_events": events, "count": len(events)}


@router.post("/principals/{principal_did}/link-github")
def link_principal_github(
    principal_did: str,
    request: Request,
    payload: PrincipalGithubLinkRequest,
    db=Depends(get_db),
):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")

    principals_v1 = _load_registered_principals_v1(db)
    updated = _link_github_identity_to_principal(
        principals_v1,
        principal_did=principal_did,
        github_user_id=payload.github_user_id,
        github_login=payload.github_login,
        github_email=payload.github_email,
    )
    principals_v1[str(principal_did).strip()] = updated
    canonical = _persist_registered_principals_v1(db, principals_v1)
    return {"status": "ok", "principal": canonical.get(str(principal_did).strip())}


@router.get("/subject-events")
def list_subject_events_admin(
    request: Request,
    authority_subject_id: str | None = Query(None),
    principal_did: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db=Depends(get_db),
):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")

    authority_filter = (authority_subject_id or "").strip()
    principal_filter = (principal_did or "").strip()
    rows = []
    for event_id in sorted(load_subject_events(db).keys()):
        row = load_subject_events(db).get(event_id)
        if not isinstance(row, dict):
            continue
        if authority_filter and str(row.get("resulting_authority_subject_id") or "").strip() != authority_filter:
            continue
        if principal_filter and str(row.get("principal_did") or "").strip() != principal_filter:
            continue
        rows.append(dict(row))
    return {"events": rows[-limit:]}


@router.get("/subject-events/{event_id}")
def get_subject_event_admin(event_id: str, request: Request, db=Depends(get_db)):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    record = get_subject_event(db, event_id)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="subject event not found")
    return {"event": record}


@router.post("/subject-events")
def create_subject_event_admin(request: Request, payload: SubjectEventCreateRequest, db=Depends(get_db)):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")
    try:
        event = append_subject_event(
            db,
            event_type=payload.event_type,
            issuer=payload.issuer,
            resulting_authority_subject_id=payload.resulting_authority_subject_id,
            principal_did=payload.principal_did,
            canonical_subject=payload.canonical_subject,
            prior_authority_subject_id=payload.prior_authority_subject_id,
            evidence_refs=payload.evidence_refs,
            standing_carryover=payload.standing_carryover,
            credential_carryover=payload.credential_carryover,
            event_id=payload.event_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    principals_v1 = _load_registered_principals_v1(db)
    _sync_principal_from_subject_event(
        db,
        principals_v1,
        authority_subject_id=str(event.get("resulting_authority_subject_id") or "").strip(),
        subject_event=event,
    )
    _persist_registered_principals_v1(db, principals_v1)
    return {"status": "ok", "event": event}


@router.get("/authority-subjects")
def list_authority_subjects_admin(
    request: Request,
    principal_did: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db=Depends(get_db),
):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    principal_filter = (principal_did or "").strip()
    rows = []
    for subject_id in sorted(load_authority_subjects(db).keys()):
        row = load_authority_subjects(db).get(subject_id)
        if not isinstance(row, dict):
            continue
        if principal_filter and str(row.get("principal_did") or "").strip() != principal_filter:
            continue
        rows.append(dict(row))
    return {"subjects": rows[-limit:]}


@router.get("/authority-subjects/{authority_subject_id:path}")
def get_authority_subject_admin(authority_subject_id: str, request: Request, db=Depends(get_db)):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    record = load_authority_subjects(db).get((authority_subject_id or "").strip())
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="authority subject not found")
    return {"subject": record}


@router.get("/authority-events")
def list_authority_events_admin(
    request: Request,
    authority_subject_id: str | None = Query(None),
    issuer: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db=Depends(get_db),
):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")

    authority_filter = (authority_subject_id or "").strip()
    issuer_filter = (issuer or "").strip()
    rows = []
    for event_id in sorted(load_authority_events(db).keys()):
        row = load_authority_events(db).get(event_id)
        if not isinstance(row, dict):
            continue
        if authority_filter and str(row.get("authority_subject_id") or "").strip() != authority_filter:
            continue
        if issuer_filter and str(row.get("issuer") or "").strip() != issuer_filter:
            continue
        rows.append(dict(row))
    return {"events": rows[-limit:]}


@router.get("/issuer-authorities")
def list_issuer_authorities_admin(
    request: Request,
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    db=Depends(get_db),
):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")

    normalized_status = (status_filter or "").strip().lower()
    rows = []
    for issuer in sorted(load_issuer_authorities(db).keys()):
        row = load_issuer_authorities(db).get(issuer)
        if not isinstance(row, dict):
            continue
        if normalized_status and str(row.get("status") or "").strip().lower() != normalized_status:
            continue
        rows.append(dict(row))
    return {"issuers": rows[-limit:]}


@router.get("/issuer-authorities/{issuer:path}")
def get_issuer_authority_admin(issuer: str, request: Request, db=Depends(get_db)):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    record = get_issuer_authority(db, issuer)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="issuer authority not found")
    return {"issuer": record}


@router.post("/issuer-authorities")
def upsert_issuer_authority_admin(request: Request, payload: IssuerAuthorityUpsertRequest, db=Depends(get_db)):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")
    try:
        record = upsert_issuer_authority(
            db,
            issuer=payload.issuer,
            issuer_class=payload.issuer_class,
            allowed_event_types=payload.allowed_event_types,
            evidence_requirement=payload.evidence_requirement,
            credential_ref=payload.credential_ref,
            issuer_did=payload.issuer_did,
            identity_anchor_ref=payload.identity_anchor_ref,
            trust_basis=payload.trust_basis,
            verification_state=payload.verification_state,
            policy_ref=payload.policy_ref,
            policy_verdict=payload.policy_verdict,
            policy_scope=payload.policy_scope,
            verifier_policy_ref=payload.verifier_policy_ref,
            vc_type=payload.vc_type,
            vc_id=payload.vc_id,
            vc_envelope=payload.vc_envelope,
            credential_status_ref=payload.credential_status_ref,
            credential_status_state=payload.credential_status_state,
            credential_status_checked_at=payload.credential_status_checked_at,
            vc_verification_method=payload.vc_verification_method,
            vc_verification_status=payload.vc_verification_status,
            vc_verification_checked_at=payload.vc_verification_checked_at,
            vc_verification_proof_ref=payload.vc_verification_proof_ref,
            status=payload.status,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "ok", "issuer": record}


@router.get("/live-identity-checks")
def list_live_identity_checks_admin(
    request: Request,
    subject_type: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db=Depends(get_db),
):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    normalized_subject_type = (subject_type or "").strip().lower()
    rows = []
    for subject_ref in sorted(load_live_identity_checks(db).keys()):
        row = load_live_identity_checks(db).get(subject_ref)
        if not isinstance(row, dict):
            continue
        if normalized_subject_type and str(row.get("subject_type") or "").strip().lower() != normalized_subject_type:
            continue
        rows.append(dict(row))
    return {"checks": rows[-limit:]}


@router.get("/live-identity-checks/{subject_ref:path}")
def get_live_identity_check_admin(subject_ref: str, request: Request, db=Depends(get_db)):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    record = get_live_identity_check(db, subject_ref)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="live identity check not found")
    return {"check": record}


@router.post("/live-identity-checks")
def upsert_live_identity_check_admin(request: Request, payload: LiveIdentityCheckUpsertRequest, db=Depends(get_db)):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")
    try:
        record = upsert_live_identity_check(
            db,
            subject_ref=payload.subject_ref,
            subject_type=payload.subject_type,
            resolver_ref=payload.resolver_ref,
            resolution_status=payload.resolution_status,
            resolved_identity=payload.resolved_identity,
            authority_binding_ref=payload.authority_binding_ref,
            identity_anchor_ref=payload.identity_anchor_ref,
            checked_at=payload.checked_at,
            trust_root_ref=payload.trust_root_ref,
            evidence_ref=payload.evidence_ref,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "ok", "check": record}


@router.get("/credential-status-checks")
def list_credential_status_checks_admin(
    request: Request,
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    db=Depends(get_db),
):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    normalized_status = (status_filter or "").strip().lower()
    rows = []
    for credential_status_ref in sorted(load_credential_status_checks(db).keys()):
        row = load_credential_status_checks(db).get(credential_status_ref)
        if not isinstance(row, dict):
            continue
        if normalized_status and str(row.get("status_state") or "").strip().lower() != normalized_status:
            continue
        rows.append(dict(row))
    return {"checks": rows[-limit:]}


@router.get("/credential-status-checks/{credential_status_ref:path}")
def get_credential_status_check_admin(credential_status_ref: str, request: Request, db=Depends(get_db)):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    record = get_credential_status_check(db, credential_status_ref)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="credential status check not found")
    return {"check": record}


@router.post("/credential-status-checks")
def upsert_credential_status_check_admin(
    request: Request,
    payload: CredentialStatusCheckUpsertRequest,
    db=Depends(get_db),
):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")
    try:
        record = upsert_credential_status_check(
            db,
            credential_status_ref=payload.credential_status_ref,
            credential_id=payload.credential_id,
            resolver_ref=payload.resolver_ref,
            status_state=payload.status_state,
            checked_at=payload.checked_at,
            proof_ref=payload.proof_ref,
            trust_root_ref=payload.trust_root_ref,
            issuer=payload.issuer,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "ok", "check": record}


@public_router.get("/trust-anchor/issuer-authority")
def get_public_trust_anchor_issuer_authority(request: Request, db=Depends(get_db)):
    documents = _build_public_trust_anchor_documents(db, request)
    return documents["public_issuer_authority"]


@public_router.get("/trust-anchor/issuer-authority-status")
def get_public_trust_anchor_issuer_authority_status(request: Request, db=Depends(get_db)):
    documents = _build_public_trust_anchor_documents(db, request)
    return documents["public_issuer_authority_status"]


@public_router.get("/trust-anchor/verifier-policy")
def get_public_trust_anchor_verifier_policy(request: Request, db=Depends(get_db)):
    documents = _build_public_trust_anchor_documents(db, request)
    return documents["public_verifier_policy"]


@public_router.get("/trust-anchor/bundle")
def get_public_trust_anchor_bundle(request: Request, db=Depends(get_db)):
    documents = _build_public_trust_anchor_documents(db, request)
    return documents["bundle"]


@public_router.get("/status/{credential_status_ref:path}")
def get_public_credential_status(credential_status_ref: str, request: Request, db=Depends(get_db)):
    config = _trust_anchor_public_config(request)
    status_check = get_credential_status_check(db, credential_status_ref)
    if not isinstance(status_check, dict):
        raise HTTPException(status_code=404, detail="credential status check not found")
    issuer_authority = None
    for issuer in sorted(load_issuer_authorities(db).keys()):
        candidate = load_issuer_authorities(db).get(issuer)
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("credential_status_ref") or "").strip() == str(credential_status_ref or "").strip():
            issuer_authority = dict(candidate)
            break
    return _build_public_credential_status_document(
        request=request,
        config=config,
        status_check=status_check,
        issuer_authority=issuer_authority,
    )


@router.get("/public-objects")
def list_public_objects_admin(request: Request, db=Depends(get_db)):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    rows = []
    for object_id in sorted(load_public_objects(db).keys()):
        row = load_public_objects(db).get(object_id)
        if isinstance(row, dict):
            rows.append(_annotate_control_plane_row(_public_object_document(row), kind="public_object"))
    return {"objects": rows}


@router.post("/public-objects")
def upsert_public_object_admin(request: Request, payload: PublicObjectUpsertRequest, db=Depends(get_db)):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")
    try:
        record = upsert_public_object(
            db,
            public_object_id=payload.public_object_id,
            object_kind=payload.object_kind,
            object_id=payload.object_id,
            subject_id=payload.subject_id,
            issuer_id=payload.issuer_id,
            content_digest=payload.content_digest,
            coord_ref=payload.coord_ref,
            evidence_refs=payload.evidence_refs,
            status_ref=payload.status_ref,
            previous_version_id=payload.previous_version_id,
            superseded_by=payload.superseded_by,
            lifecycle_state=payload.lifecycle_state,
            invalidation_reason=payload.invalidation_reason,
            revoked_at=payload.revoked_at,
            shareability=payload.shareability,
            artifact_identity=payload.artifact_identity,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "ok", "object": _annotate_control_plane_row(_public_object_document(record), kind="public_object")}


@public_router.get("/objects/{object_kind}/{object_id:path}")
def get_public_object_document_route(object_kind: str, object_id: str, db=Depends(get_db)):
    normalized_kind = str(object_kind or "").strip().lower()
    raw_object_id = str(object_id or "").strip()
    wants_status = raw_object_id.endswith("/status")
    wants_replay = raw_object_id.endswith("/replay")
    if wants_status:
        lookup_object_id = raw_object_id[:-len("/status")]
    elif wants_replay:
        lookup_object_id = raw_object_id[:-len("/replay")]
    else:
        lookup_object_id = raw_object_id
    lookup = None
    for public_id in sorted(load_public_objects(db).keys()):
        candidate = load_public_objects(db).get(public_id)
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("object_kind") or "").strip().lower() != normalized_kind:
            continue
        if str(candidate.get("object_id") or "").strip() != lookup_object_id:
            continue
        lookup = dict(candidate)
        break
    if not isinstance(lookup, dict):
        raise HTTPException(status_code=404, detail={"outcome": "not_found", "object_kind": normalized_kind, "object_id": object_id})
    if wants_status:
        return _public_object_status_document(lookup)
    if wants_replay:
        return public_object_replay_export(lookup)
    document = _public_object_document(lookup)
    if document.get("shareability") == "not-shareable":
        raise HTTPException(
            status_code=403,
            detail={
                "outcome": "not_authorized",
                "object_kind": normalized_kind,
                "object_id": object_id,
                "lifecycle_state": str(lookup.get("lifecycle_state") or "").strip().lower() or None,
            },
        )
    return document


@public_router.get("/objects/{object_kind}/{object_id:path}/status")
def get_public_object_status_route(object_kind: str, object_id: str, db=Depends(get_db)):
    normalized_kind = str(object_kind or "").strip().lower()
    lookup = None
    for public_id in sorted(load_public_objects(db).keys()):
        candidate = load_public_objects(db).get(public_id)
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("object_kind") or "").strip().lower() != normalized_kind:
            continue
        if str(candidate.get("object_id") or "").strip() != str(object_id or "").strip():
            continue
        lookup = dict(candidate)
        break
    if not isinstance(lookup, dict):
        raise HTTPException(status_code=404, detail={"outcome": "not_found", "object_kind": normalized_kind, "object_id": object_id})
    return _public_object_status_document(lookup)


@router.get("/verifier-portals")
def list_verifier_portals_admin(
    request: Request,
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    db=Depends(get_db),
):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    normalized_status = (status_filter or "").strip().lower()
    rows = []
    for portal_id in sorted(load_verifier_portals(db).keys()):
        row = load_verifier_portals(db).get(portal_id)
        if not isinstance(row, dict):
            continue
        if normalized_status and str(row.get("status") or "").strip().lower() != normalized_status:
            continue
        rows.append(dict(row))
    return {"portals": rows[-limit:]}


@router.get("/verifier-portals/{portal_id:path}")
def get_verifier_portal_admin(portal_id: str, request: Request, db=Depends(get_db)):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    record = get_verifier_portal(db, portal_id)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="verifier portal not found")
    return {"portal": record}


@router.post("/verifier-portals")
def upsert_verifier_portal_admin(request: Request, payload: VerifierPortalUpsertRequest, db=Depends(get_db)):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")
    try:
        record = upsert_verifier_portal(
            db,
            portal_id=payload.portal_id,
            portal_type=payload.portal_type,
            trust_basis=payload.trust_basis,
            verification_mode=payload.verification_mode,
            trusted_identities=payload.trusted_identities,
            allowed_sources=payload.allowed_sources,
            resolver_ref=payload.resolver_ref,
            public_key_ref=payload.public_key_ref,
            status=payload.status,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "ok", "portal": record}


@router.get("/verifier-proof-checks")
def list_verifier_proof_checks_admin(
    request: Request,
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    db=Depends(get_db),
):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    normalized_status = (status_filter or "").strip().lower()
    rows = []
    for proof_ref in sorted(load_verifier_proof_checks(db).keys()):
        row = load_verifier_proof_checks(db).get(proof_ref)
        if not isinstance(row, dict):
            continue
        if normalized_status and str(row.get("verification_status") or "").strip().lower() != normalized_status:
            continue
        rows.append(dict(row))
    return {"proofs": rows[-limit:]}


@router.get("/verifier-proof-checks/{proof_ref:path}")
def get_verifier_proof_check_admin(proof_ref: str, request: Request, db=Depends(get_db)):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    record = get_verifier_proof_check(db, proof_ref)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="verifier proof check not found")
    return {"proof": record}


@router.post("/verifier-proof-checks")
def upsert_verifier_proof_check_admin(request: Request, payload: VerifierProofCheckUpsertRequest, db=Depends(get_db)):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")
    try:
        record = upsert_verifier_proof_check(
            db,
            proof_ref=payload.proof_ref,
            resolver_ref=payload.resolver_ref,
            portal_id=payload.portal_id,
            verifier_identity=payload.verifier_identity,
            verification_status=payload.verification_status,
            checked_at=payload.checked_at,
            proof_hash=payload.proof_hash,
            trust_root_ref=payload.trust_root_ref,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "ok", "proof": record}


@router.get("/verifier-signature-checks")
def list_verifier_signature_checks_admin(
    request: Request,
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    db=Depends(get_db),
):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    normalized_status = (status_filter or "").strip().lower()
    rows = []
    for signature_ref in sorted(load_verifier_signature_checks(db).keys()):
        row = load_verifier_signature_checks(db).get(signature_ref)
        if not isinstance(row, dict):
            continue
        if normalized_status and str(row.get("verification_status") or "").strip().lower() != normalized_status:
            continue
        rows.append(dict(row))
    return {"signatures": rows[-limit:]}


@router.get("/verifier-signature-checks/{signature_ref:path}")
def get_verifier_signature_check_admin(signature_ref: str, request: Request, db=Depends(get_db)):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    record = get_verifier_signature_check(db, signature_ref)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="verifier signature check not found")
    return {"signature": record}


@router.post("/verifier-signature-checks")
def upsert_verifier_signature_check_admin(request: Request, payload: VerifierSignatureCheckUpsertRequest, db=Depends(get_db)):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")
    try:
        record = upsert_verifier_signature_check(
            db,
            signature_ref=payload.signature_ref,
            public_key_ref=payload.public_key_ref,
            portal_id=payload.portal_id,
            verifier_identity=payload.verifier_identity,
            verification_status=payload.verification_status,
            checked_at=payload.checked_at,
            signature_hash=payload.signature_hash,
            trust_root_ref=payload.trust_root_ref,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "ok", "signature": record}


@router.get("/verifier-public-keys")
def list_verifier_public_keys_admin(
    request: Request,
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    db=Depends(get_db),
):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    normalized_status = (status_filter or "").strip().lower()
    rows = []
    for public_key_ref in sorted(load_verifier_public_keys(db).keys()):
        row = load_verifier_public_keys(db).get(public_key_ref)
        if not isinstance(row, dict):
            continue
        if normalized_status and str(row.get("status") or "").strip().lower() != normalized_status:
            continue
        rows.append(dict(row))
    return {"keys": rows[-limit:]}


@router.get("/verifier-public-keys/{public_key_ref:path}")
def get_verifier_public_key_admin(public_key_ref: str, request: Request, db=Depends(get_db)):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    record = get_verifier_public_key(db, public_key_ref)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="verifier public key not found")
    return {"key": record}


@router.post("/verifier-public-keys")
def upsert_verifier_public_key_admin(request: Request, payload: VerifierPublicKeyUpsertRequest, db=Depends(get_db)):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")
    try:
        record = upsert_verifier_public_key(
            db,
            public_key_ref=payload.public_key_ref,
            algorithm=payload.algorithm,
            public_key_pem=payload.public_key_pem,
            trust_root_ref=payload.trust_root_ref,
            status=payload.status,
            notes=payload.notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "ok", "key": record}


@router.get("/evidence-manifests")
def list_evidence_manifests_admin(
    request: Request,
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    db=Depends(get_db),
):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    normalized_status = (status_filter or "").strip().lower()
    rows = []
    for ref in sorted(load_evidence_manifests(db).keys()):
        row = load_evidence_manifests(db).get(ref)
        if not isinstance(row, dict):
            continue
        if normalized_status and str(row.get("status") or "").strip().lower() != normalized_status:
            continue
        rows.append(dict(row))
    return {"manifests": rows[-limit:]}


@router.get("/evidence-manifests/{manifest_ref:path}")
def get_evidence_manifest_admin(manifest_ref: str, request: Request, db=Depends(get_db)):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    record = get_evidence_manifest(db, manifest_ref)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="evidence manifest not found")
    return {"manifest": record}


@router.post("/evidence-manifests")
def upsert_evidence_manifest_admin(request: Request, payload: EvidenceManifestUpsertRequest, db=Depends(get_db)):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")
    try:
        record = upsert_evidence_manifest(
            db,
            issuer=payload.issuer,
            evidence_refs=payload.evidence_refs,
            authority_subject_id=payload.authority_subject_id,
            manifest_ref=payload.manifest_ref,
            package_type=payload.package_type,
            signature_ref=payload.signature_ref,
            signature_status=payload.signature_status,
            verification_method=payload.verification_method,
            verification_status=payload.verification_status,
            verification_checked_at=payload.verification_checked_at,
            verification_proof_ref=payload.verification_proof_ref,
            status=payload.status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "ok", "manifest": record}


@router.get("/authority-events/{event_id}")
def get_authority_event_admin(event_id: str, request: Request, db=Depends(get_db)):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    record = get_authority_event(db, event_id)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="authority event not found")
    return {"event": record}


@router.post("/authority-events")
def create_authority_event_admin(request: Request, payload: AuthorityEventCreateRequest, db=Depends(get_db)):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")
    try:
        standing, event = append_authority_event(
            db,
            authority_subject_id=payload.authority_subject_id,
            event_type=payload.event_type,
            issuer=payload.issuer,
            reason_code=payload.reason_code,
            delta=payload.delta,
            evidence_refs=payload.evidence_refs,
            idempotency_key=payload.idempotency_key,
            principal_did=payload.principal_did,
            canonical_subject=payload.canonical_subject,
            credential_ref=payload.credential_ref,
            standing_envelope_ref=payload.standing_envelope_ref,
            subject_transition_event_ref=payload.subject_transition_event_ref,
            event_id=payload.event_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    principals_v1 = _load_registered_principals_v1(db)
    _sync_principal_from_authority_state(
        db,
        principals_v1,
        authority_subject_id=str(standing.get("authority_subject_id") or payload.authority_subject_id).strip(),
        standing_view=standing,
    )
    _persist_registered_principals_v1(db, principals_v1)
    return {"status": "ok", "event": event, "standing": standing}


@router.get("/authority-state")
def list_authority_state_admin(
    request: Request,
    principal_did: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db=Depends(get_db),
):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    principal_filter = (principal_did or "").strip()
    rows = []
    for subject_id in sorted(load_authority_state(db).keys()):
        row = load_authority_state(db).get(subject_id)
        if not isinstance(row, dict):
            continue
        if principal_filter and str(row.get("principal_did") or "").strip() != principal_filter:
            continue
        rows.append(dict(row))
    return {"subjects": rows[-limit:]}


@router.get("/authority-state/{authority_subject_id:path}")
def get_authority_state_admin(authority_subject_id: str, request: Request, db=Depends(get_db)):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    record = get_authority_state(db, authority_subject_id)
    if not isinstance(record, dict):
        raise HTTPException(status_code=404, detail="authority state not found")
    return {"subject": record}


@router.get("/authority-unified/{authority_subject_id:path}")
def get_unified_authority_admin(authority_subject_id: str, request: Request, db=Depends(get_db)):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")
    return _build_unified_authority_view(db, authority_subject_id=authority_subject_id)


@router.post("/authority-events/replay")
def replay_authority_events_admin(
    request: Request,
    authority_subject_id: str | None = Query(None),
    db=Depends(get_db),
):
    _require_admin(request)
    _require_admin_principal_type(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.write")
    replayed = replay_authority_state(
        db,
        authority_subject_id=(authority_subject_id or "").strip() or None,
    )
    principals_v1 = _load_registered_principals_v1(db)
    for subject_id, standing in replayed.items():
        if not isinstance(standing, dict):
            continue
        _sync_principal_from_authority_state(
            db,
            principals_v1,
            authority_subject_id=str(subject_id).strip(),
            standing_view=standing,
        )
    _persist_registered_principals_v1(db, principals_v1)
    return {
        "status": "ok",
        "replayed_subjects": list(sorted(replayed.keys())),
        "count": len(replayed),
    }


@router.get("/history/audit")
def history_audit_export(
    request: Request,
    limit: int = Query(100, ge=1, le=1000, description="Max entities to include in the export."),
    coord_limit: int = Query(5, ge=1, le=20, description="Max sample coordinates per entity."),
    db=Depends(get_db),
):
    """Export chat-like entities with entry counts and sample coordinates for recovery audits."""
    _require_admin(request)
    _authorize_admin_scope(request, ledger_id="default", action="ledger.read")

    with log_operation(LOGGER, "admin_history_audit_export", request=request) as ctx:
        grouped: dict[str, dict[str, Any]] = {}
        scanned_keys = 0

        for raw_key in _iter_db_keys(db):
            scanned_keys += 1
            decoded = _decode_key(raw_key)
            if not decoded:
                continue
            if decoded in {
                LEDGER_REGISTRY_KEY.decode(),
                LEDGER_REGISTRY_V1_KEY.decode(),
                TENANT_REGISTRY_V1_KEY.decode(),
            }:
                continue
            namespace, sep, identifier = decoded.rpartition(":")
            if not sep or not namespace or not identifier:
                continue
            if not _is_chat_history_entity(namespace):
                continue

            compact = _compact_entity_name(namespace)
            bucket = grouped.setdefault(
                compact,
                {
                    "entity": compact,
                    "namespaces": set(),
                    "entry_count": 0,
                    "sample_coordinates": [],
                },
            )
            bucket["entry_count"] += 1
            bucket["namespaces"].add(namespace)
            coords = bucket["sample_coordinates"]
            if len(coords) < coord_limit:
                coords.append(decoded)

        rows: list[dict[str, Any]] = []
        for record in grouped.values():
            namespaces = sorted(record.get("namespaces") or [])
            coordinates = sorted(record.get("sample_coordinates") or [], reverse=True)[:coord_limit]
            rows.append(
                {
                    "entity": record.get("entity"),
                    "entry_count": int(record.get("entry_count") or 0),
                    "namespaces": namespaces,
                    "sample_coordinates": coordinates,
                }
            )

        rows.sort(key=lambda item: (-int(item.get("entry_count") or 0), str(item.get("entity") or "")))
        rows = rows[:limit]
        total_entries = sum(int(item.get("entry_count") or 0) for item in rows)

        payload = {
            "entity_count": len(rows),
            "entry_count": total_entries,
            "limit": limit,
            "coord_limit": coord_limit,
            "scanned_keys": scanned_keys,
            "entities": rows,
            "generated_at": _now_iso(),
        }
        ctx.update(
            {
                "entity_count": payload["entity_count"],
                "entry_count": payload["entry_count"],
                "scanned_keys": scanned_keys,
            }
        )
        return payload


@router.get("/provisioning/jobs/{job_id}")
def admin_provisioning_job_inspection(
    request: Request,
    job_id: str,
    db=Depends(get_db),
) -> dict[str, Any]:
    """Read-only admin inspection of a provisioning job."""
    _require_admin(request)

    jobs = _load_jobs(db)
    job = jobs.get(job_id)
    if not isinstance(job, dict):
        raise HTTPException(status_code=404, detail={"error": "provisioning_job_not_found"})

    summary = _job_summary(job)
    if not isinstance(summary, dict):
        raise HTTPException(status_code=500, detail={"error": "provisioning_job_summary_failed"})

    return {
        "status": "ok",
        "inspection": {
            "job": summary,
            "read_only": True,
            "rescue_recommendation": _rescue_recommendation(summary),
        },
    }


@router.get("/provisioning/jobs/{job_id}/steps")
def admin_provisioning_job_steps_inspection(
    request: Request,
    job_id: str,
    db=Depends(get_db),
) -> dict[str, Any]:
    """Read-only admin inspection of per-step provisioning progress."""
    _require_admin(request)

    jobs = _load_jobs(db)
    job = jobs.get(job_id)
    if not isinstance(job, dict):
        raise HTTPException(status_code=404, detail={"error": "provisioning_job_not_found"})

    steps = job.get("resource_steps") if isinstance(job.get("resource_steps"), list) else []
    step_summaries = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_summaries.append(
            {
                "step_id": step.get("step_id"),
                "resource_type": step.get("resource_type"),
                "resource_id": step.get("resource_id"),
                "status": step.get("status"),
                "retry_eligible": step.get("retry_eligible"),
                "failure_reason": step.get("failure_reason"),
                "created_at": step.get("created_at"),
                "updated_at": step.get("updated_at"),
                "metadata": step.get("metadata") if isinstance(step.get("metadata"), dict) else {},
            }
        )

    return {
        "status": "ok",
        "inspection": {
            "job_id": job_id,
            "account_id": job.get("account_id"),
            "job_status": job.get("status"),
            "steps": step_summaries,
            "read_only": True,
            "step_counts": {
                "total": len(step_summaries),
                "succeeded": len([s for s in step_summaries if s["status"] in {"succeeded", "skipped_existing"}]),
                "failed": len([s for s in step_summaries if s["status"] in {"failed", "requires_admin"}]),
                "pending": len([s for s in step_summaries if s["status"] == "pending"]),
            },
        },
    }


def _rescue_recommendation(summary: dict[str, Any]) -> dict[str, Any]:
    failed = int(summary.get("resource_counts", {}).get("failed") or 0)
    total = int(summary.get("resource_counts", {}).get("total") or 0)
    succeeded = int(summary.get("resource_counts", {}).get("succeeded") or 0)
    if failed == 0 and succeeded == total:
        return {"action": "none", "reason": "all_steps_succeeded"}
    if failed > 0 and succeeded == 0:
        return {"action": "retry", "reason": "all_steps_failed"}
    if failed > 0:
        return {"action": "inspect_steps", "reason": "partial_failure"}
    return {"action": "wait", "reason": "in_progress"}


__all__ = ["router"]
