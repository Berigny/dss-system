"""Pilot plan entitlement contract for the commercial trial spine."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


CapabilityState = Literal["enabled", "disabled", "manual_only", "not_entitled"]

PILOT_PLAN_ID = "free_trial"
PILOT_TRIAL_DURATION_DAYS = 7


@dataclass(frozen=True)
class IncludedAssetLimits:
    ledgers: int
    chat_surfaces: int
    share_surfaces: int
    owner_human_principals: int
    authorised_rep_slots: int


@dataclass(frozen=True)
class PilotPlanEntitlements:
    plan_id: str
    trial_duration_days: int
    included: IncludedAssetLimits
    manual_only: tuple[str, ...]
    out_of_scope: tuple[str, ...]
    capability_states: tuple[CapabilityState, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "plan_id": self.plan_id,
            "trial_duration_days": self.trial_duration_days,
            "included": asdict(self.included),
            "manual_only": list(self.manual_only),
            "out_of_scope": list(self.out_of_scope),
            "capability_states": list(self.capability_states),
        }


FREE_TRIAL_ENTITLEMENTS = PilotPlanEntitlements(
    plan_id=PILOT_PLAN_ID,
    trial_duration_days=PILOT_TRIAL_DURATION_DAYS,
    included=IncludedAssetLimits(
        ledgers=1,
        chat_surfaces=1,
        share_surfaces=1,
        owner_human_principals=1,
        authorised_rep_slots=1,
    ),
    manual_only=(
        "document_surface",
        "telegram_setup",
        "advanced_agent_setups",
        "extra_surfaces",
        "extra_principals",
    ),
    out_of_scope=(
        "self_serve_paid_checkout",
        "self_serve_custom_plans",
        "automatic_telegram_fulfilment",
        "automatic_document_surface_enablement",
        "self_serve_agent_principal_creation",
    ),
    capability_states=("enabled", "disabled", "manual_only", "not_entitled"),
)


def get_pilot_plan_entitlements(plan_id: str = PILOT_PLAN_ID) -> PilotPlanEntitlements:
    normalized = str(plan_id or "").strip()
    if normalized != PILOT_PLAN_ID:
        raise KeyError(f"unknown pilot plan: {plan_id}")
    return FREE_TRIAL_ENTITLEMENTS


def get_pilot_plan_contract(plan_id: str = PILOT_PLAN_ID) -> dict[str, object]:
    return get_pilot_plan_entitlements(plan_id).to_dict()
