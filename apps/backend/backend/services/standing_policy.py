from __future__ import annotations

from typing import Any, Mapping


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _clean_scope(value: Any) -> str:
    return str(value or "").strip().lower()


def _clean_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _clean_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _clean_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _authority_policy_from_state(authority_state: Mapping[str, Any] | None) -> dict[str, Any]:
    state = _as_dict(authority_state)
    sanctions = state.get("active_sanctions") if isinstance(state.get("active_sanctions"), list) else []
    active_sanctions = [str(item).strip() for item in sanctions if str(item).strip()]
    probation_status = str(state.get("probation_status") or "").strip().lower()
    trust_class = str(state.get("trust_class") or "").strip().upper()
    posture_class = str(state.get("posture_class") or "").strip().upper()

    if active_sanctions or trust_class == "T0" or posture_class == "P0":
        return {
            "tool_scope": "none",
            "retrieval_scope": "none",
            "max_output_tokens": 256,
            "write_commit_allowed": False,
        }
    if probation_status == "probation":
        return {
            "tool_scope": "restricted",
            "retrieval_scope": "tenant",
            "max_output_tokens": 900,
            "write_commit_allowed": False,
        }
    return {
        "tool_scope": "standard",
        "retrieval_scope": "tenant",
        "max_output_tokens": 1200,
        "write_commit_allowed": True,
    }


_TOOL_SCOPE_ORDER = {"none": 0, "restricted": 1, "standard": 2}
_RETRIEVAL_SCOPE_ORDER = {"none": 0, "session": 1, "tenant": 2}


def _restrict_scope(left: str, right: str, *, order: dict[str, int], default: str) -> str:
    left_clean = left if left in order else default
    right_clean = right if right in order else default
    return left_clean if order[left_clean] <= order[right_clean] else right_clean


def resolve_standing_policy(
    *,
    metadata: Mapping[str, Any] | None = None,
    standing_envelope: Mapping[str, Any] | None = None,
    authority_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metadata_map = _as_dict(metadata)
    direct_envelope = _as_dict(standing_envelope)
    model_auth = _as_dict(metadata_map.get("model_auth_context"))
    model_auth_envelope = _as_dict(model_auth.get("standing_envelope"))

    envelope = direct_envelope or model_auth_envelope or _as_dict(metadata_map.get("standing_envelope"))
    authority_state_map = _as_dict(authority_state)
    authority_policy = _authority_policy_from_state(authority_state_map) if authority_state_map else {}

    envelope_tool_scope = _clean_scope(envelope.get("tool_scope")) or "standard"
    envelope_retrieval_scope = _clean_scope(envelope.get("retrieval_scope")) or "tenant"
    envelope_max_output_tokens = _clean_int(envelope.get("max_output_tokens"))
    envelope_write_commit_allowed = _clean_bool(envelope.get("write_commit_allowed"))

    authority_tool_scope = _clean_scope(authority_policy.get("tool_scope")) or envelope_tool_scope
    authority_retrieval_scope = _clean_scope(authority_policy.get("retrieval_scope")) or envelope_retrieval_scope
    authority_max_output_tokens = _clean_int(authority_policy.get("max_output_tokens"))
    authority_write_commit_allowed = _clean_bool(authority_policy.get("write_commit_allowed"))

    tool_scope = _restrict_scope(
        envelope_tool_scope,
        authority_tool_scope,
        order=_TOOL_SCOPE_ORDER,
        default="standard",
    )
    retrieval_scope = _restrict_scope(
        envelope_retrieval_scope,
        authority_retrieval_scope,
        order=_RETRIEVAL_SCOPE_ORDER,
        default="tenant",
    )
    if envelope_max_output_tokens is None:
        max_output_tokens = authority_max_output_tokens
    elif authority_max_output_tokens is None:
        max_output_tokens = envelope_max_output_tokens
    else:
        max_output_tokens = min(envelope_max_output_tokens, authority_max_output_tokens)
    if envelope_write_commit_allowed is None:
        write_commit_allowed = True if authority_write_commit_allowed is None else authority_write_commit_allowed
    elif authority_write_commit_allowed is None:
        write_commit_allowed = envelope_write_commit_allowed
    else:
        write_commit_allowed = bool(envelope_write_commit_allowed and authority_write_commit_allowed)

    source_parts: list[str] = []
    if direct_envelope:
        source_parts.append("standing_envelope")
    elif model_auth_envelope:
        source_parts.append("model_auth_context")
    elif envelope:
        source_parts.append("metadata")
    else:
        source_parts.append("default")
    if authority_state_map:
        source_parts.append("authority_state")

    return {
        "source": "+".join(source_parts),
        "standing_envelope": envelope or None,
        "authority_subject_id": _clean_str(authority_state_map.get("authority_subject_id")),
        "authority_state": authority_state_map or None,
        "tool_scope": tool_scope,
        "retrieval_scope": retrieval_scope,
        "max_output_tokens": max_output_tokens,
        "write_commit_allowed": True if write_commit_allowed is None else write_commit_allowed,
        "retrieval_allowed": retrieval_scope in {"session", "tenant"},
        "trust_class": _clean_str(authority_state_map.get("trust_class")),
        "posture_class": _clean_str(authority_state_map.get("posture_class")),
        "probation_status": _clean_str(authority_state_map.get("probation_status")),
        "active_sanctions": [
            str(item).strip()
            for item in (authority_state_map.get("active_sanctions") or [])
            if str(item).strip()
        ],
        "credential_ref": _clean_str(authority_state_map.get("credential_ref")),
        "standing_envelope_ref": _clean_str(authority_state_map.get("standing_envelope_ref")),
    }


def clamp_max_tokens(*, requested: int | None, standing_cap: int | None) -> int | None:
    if requested is None:
        return standing_cap
    if standing_cap is None:
        return requested
    return min(requested, standing_cap)
