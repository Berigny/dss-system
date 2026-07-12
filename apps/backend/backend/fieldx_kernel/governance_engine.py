"""Operational governance engine for Coherent Genesis Ladder enforcement."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

import numpy as np

from backend.fieldx_kernel.flow_rules import run_full_check
from backend.fieldx_kernel.schema import FLOW_PRIMES, MIN_BODY_PRIME
from backend.kernel import constants
from backend.kernel.coherence_diagnostics import dreaming_check
from backend.kernel.coord_fsm import CoordFSM
from backend.kernel.value_node_balance import ValueNodeBalance


class CoherenceException(RuntimeError):
    def __init__(self, reason: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = dict(details) if details else {}

    def as_dict(self) -> dict[str, Any]:
        return {"blocked": True, "reason": self.reason, "details": self.details}


@dataclass
class GovernanceState:
    E: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=float))
    B: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=float))
    Theta: np.ndarray = field(default_factory=lambda: np.zeros((32, 32), dtype=float))
    ledger_hash: str = "genesis"
    provenance_commit: str = ""
    mismatch_history: List[float] = field(default_factory=list)
    V_history: List[float] = field(default_factory=list)
    node_pos: Tuple[int, int] = (16, 16)
    missing_invariants: bool = False


@dataclass
class GovernanceMetrics:
    valid: bool
    metrics: Dict[str, Any]
    state: GovernanceState


@dataclass
class PatchEvaluationResult:
    """Result of the ten non-compensatory system-patch checks."""

    status_map: Dict[str, bool]
    all_passed: bool
    checksum_336_pass: bool
    first_failure: str | None
    refusal: Dict[str, Any] | None
    balance_context: Dict[str, Any] | None = None
    dreaming_context: Dict[str, Any] | None = None


class GovernanceEngine:
    def __init__(self, grid_size: int = 32):
        self.value_node_balance = ValueNodeBalance()
        self.c = 1.0
        self.N = grid_size
        self.thresholds = {
            "theta_L": 0.85,
            "theta_H": 0.70,
            "theta_V": 0.45,
            "theta_A": 0.20,
            "theta_infinity": 0.70,
            "theta_self": float(os.getenv("EQ6_THETA_SELF", "0.6")),
            "kappa_self": float(os.getenv("EQ6_KAPPA_SELF", "0.5")),
            "allowed_dW": [-1, 0, 1],
            "catastrophic_dI": 1.5,
            "V_std_max": 0.1,
        }
        self.max_dE = 1.5
        self.max_dB = 1.5 / self.c
        self.alpha = [0.1, 0.1, 0.5, 1.0]
        self.mu_h = 5.0
        self.mu_h_trend = float(os.getenv("GOVERNANCE_H_TREND_MU", "2.0"))
        self.h_windows = [5, 12, 20]
        self.profile = os.getenv("GOVERNANCE_PROFILE", "strict").strip().lower()
        self.coord_fsm = CoordFSM()

    def check_coord_supercession(
        self,
        old_coord: str,
        new_coord: str,
        *,
        coord_topology: set[str] | None = None,
    ) -> dict[str, Any]:
        """Validate that ``new_coord`` is a valid FSM derivation from ``old_coord``.

        Returns a result dict with ``valid`` and ``reason`` keys.
        """
        fsm = CoordFSM(topology=coord_topology) if coord_topology else self.coord_fsm
        valid = fsm.supercession_valid(old_coord, new_coord)
        return {
            "valid": valid,
            "reason": (
                "valid FSM derivation" if valid else "COORD derivation invalid"
            ),
            "old_coord": old_coord,
            "new_coord": new_coord,
        }

    def _adaptive_thresholds(self, *, telos_score: float, ethics_gate: int) -> dict[str, Any]:
        adjustments: dict[str, Any] = {}
        if self.profile != "adaptive":
            return {
                "theta_V": self.thresholds["theta_V"],
                "V_std_max": self.thresholds["V_std_max"],
                "mu_h": self.mu_h,
                "adjustments": adjustments,
            }

        # Bounded softening based on telos + ethics
        telos = max(0.0, min(1.0, float(telos_score)))
        ethics_ok = bool(ethics_gate)

        # Allow more variance if telos is high
        v_std_max = self.thresholds["V_std_max"] * (1.0 + (0.8 * telos if ethics_ok else 0.0))
        v_std_max = min(max(v_std_max, 0.1), 0.2)

        # Allow slightly lower theta_V if telos is high and ethics pass
        theta_v = self.thresholds["theta_V"]
        if ethics_ok and telos >= 0.6:
            theta_v = max(0.40, min(theta_v - 0.03, theta_v))

        # Soften hysteresis sensitivity
        mu_h = self.mu_h
        if ethics_ok and telos >= 0.6:
            mu_h = max(1.5, min(self.mu_h * 0.5, self.mu_h))

        adjustments.update(
            {
                "profile": "adaptive",
                "telos": telos,
                "theta_V": theta_v,
                "V_std_max": v_std_max,
                "mu_h": mu_h,
            }
        )

        return {
            "theta_V": theta_v,
            "V_std_max": v_std_max,
            "mu_h": mu_h,
            "adjustments": adjustments,
        }

    def compute_invariants(self, state: GovernanceState) -> Tuple[float, float]:
        E2 = float(np.sum(state.E ** 2))
        B2 = float(np.sum(state.B ** 2))
        I1 = E2 - (self.c**2) * B2
        I2 = 2.0 * self.c * float(np.dot(state.E, state.B))
        return I1, I2

    def compute_W(self, state: GovernanceState) -> int:
        i, j = state.node_pos
        Theta = state.Theta
        N = self.N
        total_winding = 0
        offsets = [(-1, -1), (-1, 0), (0, -1), (0, 0)]
        for di, dj in offsets:
            i0, j0 = (i + di) % N, (j + dj) % N
            corners = [
                Theta[i0, j0],
                Theta[(i0 + 1) % N, j0],
                Theta[(i0 + 1) % N, (j0 + 1) % N],
                Theta[i0, (j0 + 1) % N],
            ]
            delta = 0.0
            for k in range(4):
                diff = corners[(k + 1) % 4] - corners[k]
                diff = ((diff + np.pi + 1e-12) % (2 * np.pi)) - np.pi
                delta += float(diff)
            total_winding += int(round(delta / (2 * np.pi)))
        return int(total_winding)

    def enforce_field_bounds(self, prev: GovernanceState, curr: GovernanceState) -> None:
        dE_norm = float(np.linalg.norm(curr.E - prev.E))
        dB_norm = float(np.linalg.norm(curr.B - prev.B))
        if dE_norm > self.max_dE:
            raise CoherenceException(
                "field_jump_exceeds_bound",
                details={"dE_norm": dE_norm, "max_dE": self.max_dE},
            )
        if dB_norm > self.max_dB:
            raise CoherenceException(
                "field_jump_exceeds_bound",
                details={"dB_norm": dB_norm, "max_dB": self.max_dB},
            )

    def enforce_topological_continuity(self, prev: GovernanceState, curr: GovernanceState) -> int:
        W_prev = self.compute_W(prev)
        W_curr = self.compute_W(curr)
        dW = int(W_curr - W_prev)
        if abs(dW) > 1:
            raise CoherenceException("topological_discontinuity", details={"dW": dW})
        return dW

    def _state_fingerprint(self, state: GovernanceState) -> str:
        h = hashlib.sha256()
        h.update(state.E.tobytes())
        h.update(state.B.tobytes())
        h.update(state.Theta.tobytes())
        h.update(state.provenance_commit.encode())
        return h.hexdigest()[:16]

    def expected_ledger_hash(self, prev_hash: str, payload: str, state: GovernanceState) -> str:
        fp = self._state_fingerprint(state)
        return hashlib.sha256(f"{prev_hash}:{payload}:{fp}".encode()).hexdigest()[:16]

    def compute_K(self, state: GovernanceState, prev_hash: str, payload: str) -> int:
        expected = self.expected_ledger_hash(prev_hash, payload, state)
        return 1 if state.ledger_hash == expected else 0

    def compute_provenance_commit(self, inputs_stub: bytes, schema_hash: str, version: str) -> str:
        h = hashlib.sha256()
        h.update(inputs_stub)
        h.update(schema_hash.encode())
        h.update(version.encode())
        return h.hexdigest()[:16]

    def compute_P(self, state: GovernanceState, expected_commit: str, *, replayable: bool) -> int:
        if not replayable:
            return 0
        return 1 if state.provenance_commit == expected_commit else 0

    def compute_L(self, curr: GovernanceState, prev: GovernanceState, dW: int, *, K: int) -> Tuple[float, float, float, float]:
        I1, I2 = self.compute_invariants(curr)
        pI1, pI2 = self.compute_invariants(prev)
        dI1, dI2 = abs(I1 - pI1), abs(I2 - pI2)
        L_phys = float(np.exp(-0.05 * dI1 - 0.05 * dI2))
        L_top = 1.0 if dW in self.thresholds["allowed_dW"] else 0.0
        L_ledger = 1.0 if K == 1 else 0.0
        L = float(L_phys * L_top * L_ledger)
        return L, L_phys, L_top, L_ledger

    def compute_H(self, state: GovernanceState) -> float:
        max_w = max(self.h_windows)
        if len(state.mismatch_history) < max_w:
            return 0.0
        hs: List[float] = []
        for w in self.h_windows:
            recent = np.asarray(state.mismatch_history[-w:], dtype=float)
            var = float(np.var(recent))
            hs.append(float(np.exp(-self.mu_h * var)))
        return float(min(hs))

    def compute_A(self, E_pred: float, E_baseline: float) -> float:
        eps = 1e-9
        imp = 1.0 - (E_pred / (E_baseline + eps))
        return float(np.clip(imp, 0.0, 1.0))

    def compute_A_self(self, violations_count: int) -> float:
        kappa = float(self.thresholds.get("kappa_self", 0.5))
        return float(np.exp(-kappa * max(0, int(violations_count))))

    def compute_U(self, curr: GovernanceState, prev: GovernanceState, dW: int, K: int) -> float:
        I1, I2 = self.compute_invariants(curr)
        pI1, pI2 = self.compute_invariants(prev)
        dI1, dI2 = abs(I1 - pI1), abs(I2 - pI2)
        a1, a2, a3, a4 = self.alpha
        D = a1 * dI1 + a2 * dI2 + a3 * abs(dW) + a4 * (1 - K)
        return float(np.exp(-D))

    def check_E(self, metrics: Dict[str, Any]) -> int:
        if metrics["K"] != 1:
            return 0
        if metrics["L"] < self.thresholds["theta_L"]:
            return 0
        if metrics.get("dW", 0) not in self.thresholds["allowed_dW"]:
            return 0
        if metrics["H"] < self.thresholds["theta_H"]:
            return 0
        if metrics["P"] != 1:
            return 0
        if metrics["A"] < self.thresholds["theta_A"]:
            return 0
        theta_self = float(self.thresholds.get("theta_self", 0.0))
        if float(metrics.get("A_self", 1.0)) < theta_self:
            return 0
        if metrics["U"] < self.thresholds["theta_infinity"]:
            return 0
        if abs(metrics.get("dI1", 0.0)) > self.thresholds["catastrophic_dI"] or \
           abs(metrics.get("dI2", 0.0)) > self.thresholds["catastrophic_dI"]:
            return 0
        return 1

    def compute_V(self, A: float, U: float, E: int) -> float:
        return float(A * U * E)

    def compute_ethics_gate(self, metadata: Mapping[str, Any]) -> int:
        ethics = metadata.get("ethics") if isinstance(metadata, Mapping) else None
        if isinstance(ethics, Mapping):
            admissible = ethics.get("admissible")
            if isinstance(admissible, bool):
                return 1 if admissible else 0
        safety_score = metadata.get("safety_score")
        if isinstance(safety_score, (int, float)):
            return 1 if float(safety_score) >= 0.0 else 0
        appraisal = metadata.get("appraisal")
        if isinstance(appraisal, Mapping):
            law = appraisal.get("law_score")
            grace = appraisal.get("grace_score")
            if isinstance(law, (int, float)) and isinstance(grace, (int, float)):
                return 1 if min(float(law), float(grace)) >= 0.0 else 0
        return 1

    def bridge_allowed(self, metrics: Dict[str, Any], state: GovernanceState) -> bool:
        V = self.compute_V(metrics["A"], metrics["U"], metrics["E"])
        state.V_history.append(float(V))

        if len(state.V_history) < 3:
            return False

        recent_V = np.asarray(state.V_history[-3:], dtype=float)
        mean_V = float(np.mean(recent_V))
        std_V = float(np.std(recent_V))

        theta_v = float(metrics.get("theta_V", self.thresholds["theta_V"]))
        v_std_max = float(metrics.get("V_std_max", self.thresholds["V_std_max"]))

        return bool(
            mean_V >= theta_v
            and std_V < v_std_max
            and metrics["L"] >= self.thresholds["theta_L"]
            and metrics["H"] >= self.thresholds["theta_H"]
            and metrics["P"] == 1
            and metrics["E"] == 1
        )

    def _patch_refusal(self, patch_id: str) -> Dict[str, Any]:
        """Return a refusal payload for the first failing patch."""
        meta = constants.PATCH_REGISTRY.get(patch_id, {})
        return {
            "patch_id": patch_id,
            "engineering_replacement": meta.get("engineering_replacement", patch_id),
            "category": meta.get("category", "unknown"),
            "hard_gate": meta.get("hard_gate", "Operation blocked by governance patch."),
        }

    def evaluate_patches(
        self,
        state: GovernanceState,
        metrics: Dict[str, Any],
        metadata: Mapping[str, Any] | None = None,
    ) -> PatchEvaluationResult:
        """Evaluate the ten non-compensatory system patches.

        Patches are evaluated in order; evaluation stops at the first failure
        (fail-closed cascade). The checksum-336 gate is satisfied only when all
        patches pass and the underlying coherence metrics are within bounds.
        """
        metadata = dict(metadata) if metadata else {}
        status_map: Dict[str, bool] = {pid: False for pid in constants.PATCH_IDS}
        first_failure: str | None = None
        refusal: Dict[str, Any] | None = None

        appraisal = metadata.get("appraisal") if isinstance(metadata, Mapping) else None
        law = float(appraisal.get("law_score", 1.0)) if isinstance(appraisal, Mapping) else 1.0
        grace = float(appraisal.get("grace_score", 1.0)) if isinstance(appraisal, Mapping) else 1.0

        # Value-node balance diagnostic is a supplementary signal for patches
        # 005, 008, 009, and 010.
        balance_context: Dict[str, Any] | None = None
        value_node_meta = metadata.get("value_node_context") if isinstance(metadata, Mapping) else None
        if isinstance(value_node_meta, Mapping):
            scores = self.value_node_balance.score(
                query_embedding=None,
                context=dict(value_node_meta),
            )
            balanced, diagnostics = self.value_node_balance.is_balanced(scores)
            balance_context = {
                "balanced": balanced,
                "scores": scores,
                "diagnostics": diagnostics,
            }

        # Dreaming / full-coherence diagnostic is a supplementary signal for
        # patches 008/009/010. It is computed from optional valuations in the
        # metadata and does not replace the 336 checksum or native ethics.
        dreaming_context: Dict[str, Any] | None = None
        dreaming_meta = metadata.get("dreaming_context") if isinstance(metadata, Mapping) else None
        dreaming_coherent: bool | None = None
        if isinstance(dreaming_meta, Mapping) and "valuations" in dreaming_meta:
            from backend.kernel.coherence_diagnostics import dreaming_check

            dreaming_result = dreaming_check(
                dreaming_meta["valuations"],
                strain=dreaming_meta.get("strain"),
            )
            dreaming_coherent = dreaming_result.coherent
            dreaming_context = {
                "coherent": dreaming_result.coherent,
                "dual_pairs_synced": dreaming_result.dual_pairs_synced,
                "centroid_balanced": dreaming_result.centroid_balanced,
                "zero_strain": dreaming_result.zero_strain,
                "details": dreaming_result.details,
            }

        def _patch_008_passes() -> bool:
            base = bool(metrics.get("U", 0.0) >= self.thresholds["theta_infinity"])
            if dreaming_coherent is None:
                return base
            return base and dreaming_coherent

        def _patch_009_passes() -> bool:
            base = bool(metrics.get("ethics_gate", 0) == 1)
            if dreaming_coherent is None:
                return base
            return base and dreaming_coherent

        def _patch_010_passes() -> bool:
            base = bool(
                metrics.get("V", 0.0) >= float(metrics.get("theta_V", self.thresholds["theta_V"]))
            )
            if dreaming_coherent is None:
                return base
            return base and dreaming_coherent

        checks = [
            ("patch_001", bool(metrics.get("eq0_distinction"))),
            ("patch_002", bool(metrics.get("eq1_dual_substrate"))),
            ("patch_003", bool(metrics.get("eq2_time_irreversible"))),
            ("patch_004", bool(metrics.get("eq3_geometry_closure"))),
            (
                "patch_005",
                bool(law > 0.0 and grace > 0.0 and 0.98 <= (law / max(grace, 1e-12)) <= 1.02),
            ),
            ("patch_006", bool(metrics.get("E", 0) == 1)),
            ("patch_007", bool(metrics.get("eq6_commit_allowed", True))),
            ("patch_008", _patch_008_passes()),
            ("patch_009", _patch_009_passes()),
            ("patch_010", _patch_010_passes()),
        ]

        for pid, passed in checks:
            status_map[pid] = passed
            if not passed:
                first_failure = pid
                refusal = self._patch_refusal(pid)
                break

        checksum_336_pass = all(status_map.values())
        all_passed = checksum_336_pass

        return PatchEvaluationResult(
            status_map=status_map,
            all_passed=all_passed,
            checksum_336_pass=checksum_336_pass,
            first_failure=first_failure,
            refusal=refusal,
            balance_context=balance_context,
            dreaming_context=dreaming_context,
        )

    def validate_schema_primes(self, primes: List[int]) -> None:
        for prime in primes:
            if prime in FLOW_PRIMES:
                raise CoherenceException("flow_prime_used_for_body_or_token", details={"prime": prime})
            if prime < MIN_BODY_PRIME:
                raise CoherenceException("body_prime_below_minimum", details={"prime": prime})

    def validate_flow_sequence(self, prime_sequence: List[int], current_coherence: float) -> None:
        flow_ok, _msg, _mediator, lawfulness = run_full_check(
            prime_sequence=prime_sequence,
            current_coherence=current_coherence,
        )
        if not flow_ok or lawfulness == 0:
            raise CoherenceException(
                "flow_unlawful",
                details={"lawfulness_level": lawfulness},
            )

    def evaluate(
        self,
        *,
        prev_state: GovernanceState,
        curr_state: GovernanceState,
        prev_hash: str,
        payload: str,
        E_pred: float,
        E_baseline: float,
        expected_commit: str,
        schema_complete: bool,
        inputs_logged: bool,
        version_pinned: bool,
        ethics_gate: int,
        violations_count: int | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> GovernanceMetrics:
        dW = self.enforce_topological_continuity(prev_state, curr_state)
        self.enforce_field_bounds(prev_state, curr_state)

        I1, I2 = self.compute_invariants(curr_state)
        pI1, pI2 = self.compute_invariants(prev_state)
        dI1, dI2 = float(I1 - pI1), float(I2 - pI2)
        W = self.compute_W(curr_state)
        theta_var = float(np.var(curr_state.Theta)) if curr_state.Theta.size else 0.0

        mismatch = float(abs(dI1) + abs(dI2) + 0.5 * abs(dW))
        curr_state.mismatch_history.append(mismatch)

        K = self.compute_K(curr_state, prev_hash, payload)
        L, L_phys, L_top, L_ledger = self.compute_L(curr_state, prev_state, dW, K=K)
        H = self.compute_H(curr_state)
        max_w = max(self.h_windows)
        h_series = curr_state.mismatch_history[-max_w:] if len(curr_state.mismatch_history) >= 2 else []
        H_var = float(np.var(h_series)) if h_series else 0.0
        H_slope = 0.0
        if len(h_series) >= 2:
            try:
                H_slope = float(np.polyfit(range(len(h_series)), h_series, 1)[0])
            except Exception:
                H_slope = 0.0
        if H_slope > 0.0:
            H *= float(np.exp(-self.mu_h_trend * H_slope))
        replayable = bool(K == 1)
        P = self.compute_P(curr_state, expected_commit, replayable=replayable)
        if not (schema_complete and inputs_logged and version_pinned):
            P = 0
        A_corr = self.compute_A(E_pred, E_baseline)
        A_self = self.compute_A_self(violations_count or 0)
        A = float(A_corr * A_self)
        U = self.compute_U(curr_state, prev_state, dW, K)

        adaptive = self._adaptive_thresholds(telos_score=U, ethics_gate=ethics_gate)
        self.mu_h = float(adaptive["mu_h"])
        metrics: Dict[str, Any] = {
            "W": W,
            "dW": dW,
            "I1": I1,
            "I2": I2,
            "dI1": dI1,
            "dI2": dI2,
            "K": K,
            "L": L,
            "L_phys": L_phys,
            "L_top": L_top,
            "L_ledger": L_ledger,
            "H": H,
            "H_var": H_var,
            "H_slope": H_slope,
            "P": P,
            "A": A,
            "A_corr": A_corr,
            "A_self": A_self,
            "theta_self": float(self.thresholds.get("theta_self", 0.0)),
            "violations_count": int(violations_count or 0),
            "U": U,
            "schema_complete": schema_complete,
            "inputs_logged": inputs_logged,
            "version_pinned": version_pinned,
            "replayable": replayable,
            "ethics_gate": int(ethics_gate),
            "theta_var": theta_var,
            "theta_V": adaptive["theta_V"],
            "V_std_max": adaptive["V_std_max"],
            "grace_adjustments": adaptive["adjustments"],
        }
        metrics["E"] = self.check_E(metrics)
        metrics["E"] = min(metrics["E"], ethics_gate)
        metrics["V"] = self.compute_V(metrics["A"], metrics["U"], metrics["E"])
        metrics["eq0_distinction"] = bool(W != 0 or (abs(I1) > 1e-9 or abs(I2) > 1e-9))
        metrics["eq1_dual_substrate"] = bool(
            (not curr_state.missing_invariants) and bool(curr_state.ledger_hash)
        )
        metrics["eq2_time_irreversible"] = bool(prev_hash and K == 1)
        metrics["eq3_geometry_closure"] = bool(theta_var > 1e-9 and abs(dW) <= 1)
        metrics["eq6_commit_allowed"] = True

        patch_result = self.evaluate_patches(curr_state, metrics, metadata=metadata)
        metrics["patch_status_map"] = patch_result.status_map
        metrics["patch_all_passed"] = patch_result.all_passed
        metrics["patch_checksum_336_pass"] = patch_result.checksum_336_pass
        metrics["patch_first_failure"] = patch_result.first_failure
        if patch_result.balance_context is not None:
            metrics["value_node_balance_context"] = patch_result.balance_context
        if patch_result.dreaming_context is not None:
            metrics["dreaming_context"] = patch_result.dreaming_context

        return GovernanceMetrics(valid=True, metrics=metrics, state=curr_state)


def _coerce_array(value: Any, shape: Tuple[int, ...]) -> np.ndarray | None:
    try:
        arr = np.asarray(value, dtype=float)
    except Exception:
        return None
    if arr.shape != shape:
        return None
    return arr


def build_state_from_metadata(
    metadata: Mapping[str, Any],
    *,
    default_grid: int,
) -> GovernanceState:
    state = GovernanceState()
    field_state = metadata.get("field_state") if isinstance(metadata, Mapping) else None
    if isinstance(field_state, Mapping):
        E = _coerce_array(field_state.get("E"), (3,))
        B = _coerce_array(field_state.get("B"), (3,))
        Theta = _coerce_array(field_state.get("Theta"), (default_grid, default_grid))
        node_pos = field_state.get("node_pos")
        if E is not None and B is not None and Theta is not None:
            state.E = E
            state.B = B
            state.Theta = Theta
            if isinstance(node_pos, (list, tuple)) and len(node_pos) == 2:
                try:
                    state.node_pos = (int(node_pos[0]), int(node_pos[1]))
                except Exception:
                    pass
        else:
            state.missing_invariants = True
    else:
        state.missing_invariants = True

    prior = metadata.get("governance_state")
    if isinstance(prior, Mapping):
        state.ledger_hash = str(prior.get("ledger_hash") or state.ledger_hash)
        state.provenance_commit = str(prior.get("provenance_commit") or state.provenance_commit)
        mismatch = prior.get("mismatch_history")
        if isinstance(mismatch, list):
            state.mismatch_history = [float(x) for x in mismatch if isinstance(x, (int, float))]
        vhist = prior.get("V_history")
        if isinstance(vhist, list):
            state.V_history = [float(x) for x in vhist if isinstance(x, (int, float))]

    return state
