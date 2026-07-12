
"""cgr_kernel_tests_v1_3_beta_fixed.py
CGR Ladder Regression Suite v1.3-beta (Hardened) — FIXED

Fixes vs the draft you pasted:
- Do NOT overwrite curr.provenance_commit inside compute_metrics (kernel must verify, not mutate).
- Ensure ledger_hash is computed AFTER provenance_commit is set (since fingerprint includes it).
- Make "bad provenance" test actually fail P by setting a mismatching commit.
- Make bridge-success test use V_history consistent with the current computed V.

Run:
  python cgr_kernel_tests_v1_3_beta_fixed.py
"""
from __future__ import annotations
import hashlib
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, List, Tuple, Any
import numpy as np

class Node(IntEnum):
    S1_FIRE = 0
    S1_AIR = 1
    S1_WATER = 2
    S1_EARTH = 3
    S2_FIRE = 4
    S2_AIR = 5
    S2_WATER = 6
    S2_EARTH = 7

class TransitionViolation(Exception):
    pass

@dataclass
class CGRState:
    E: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=float))
    B: np.ndarray = field(default_factory=lambda: np.zeros(3, dtype=float))
    Theta: np.ndarray = field(default_factory=lambda: np.zeros((32, 32), dtype=float))
    ledger_hash: str = "genesis"
    provenance_commit: str = ""  # commitment hash (inputs/schema/version binding)
    mismatch_history: List[float] = field(default_factory=list)
    V_history: List[float] = field(default_factory=list)  # for V-momentum gating
    node_pos: Tuple[int, int] = (16, 16)

class CGRKernel:
    def __init__(self, grid_size: int = 32):
        self.c = 1.0
        self.N = grid_size
        self.thresholds = {
            "theta_L": 0.85,
            "theta_H": 0.70,
            "theta_V": 0.45,
            "theta_A": 0.20,
            "theta_infinity": 0.70,
            "allowed_dW": [-1, 0, 1],
            "catastrophic_dI": 1.5,
            "V_std_max": 0.1,
        }
        # Norm-based causality caps
        self.max_dE = 1.5
        self.max_dB = 1.5 / self.c

        self.alpha = [0.1, 0.1, 0.5, 1.0]
        self.mu_h = 5.0
        self.h_windows = [5, 12, 20]

    # -------------------------
    # Physics / topology
    # -------------------------
    def compute_invariants(self, state: CGRState) -> Tuple[float, float]:
        E2 = float(np.sum(state.E ** 2))
        B2 = float(np.sum(state.B ** 2))
        I1 = E2 - (self.c**2) * B2
        I2 = 2.0 * self.c * float(np.dot(state.E, state.B))
        return I1, I2

    def compute_W(self, state: CGRState) -> int:
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

    # -------------------------
    # Hard validation (before metrics)
    # -------------------------
    def enforce_field_bounds(self, prev: CGRState, curr: CGRState) -> None:
        dE_norm = float(np.linalg.norm(curr.E - prev.E))
        dB_norm = float(np.linalg.norm(curr.B - prev.B))
        if dE_norm > self.max_dE:
            raise TransitionViolation(f"Field jump exceeds bound: ||dE||={dE_norm:.3f} > {self.max_dE}")
        if dB_norm > self.max_dB:
            raise TransitionViolation(f"Field jump exceeds bound: ||dB||={dB_norm:.3f} > {self.max_dB:.3f}")

    def enforce_topological_continuity(self, prev: CGRState, curr: CGRState) -> None:
        dW = int(self.compute_W(curr) - self.compute_W(prev))
        if abs(dW) > 1:
            raise TransitionViolation(f"Topological discontinuity: dW={dW}")

    def validate_transition(self, prev: CGRState, curr: CGRState) -> None:
        self.enforce_field_bounds(prev, curr)
        self.enforce_topological_continuity(prev, curr)

    # -------------------------
    # Ledger / provenance
    # -------------------------
    def compute_provenance_commit(self, inputs_stub: bytes, schema_hash: str, version: str) -> str:
        h = hashlib.sha256()
        h.update(inputs_stub)
        h.update(schema_hash.encode())
        h.update(version.encode())
        return h.hexdigest()[:16]

    def _state_fingerprint(self, state: CGRState) -> str:
        # Fingerprint binds to provenance_commit (so a different commit changes the ledger hash).
        h = hashlib.sha256()
        h.update(state.E.tobytes())
        h.update(state.B.tobytes())
        h.update(state.Theta.tobytes())
        h.update(state.provenance_commit.encode())
        return h.hexdigest()[:16]

    def expected_ledger_hash(self, prev_hash: str, payload: str, state: CGRState) -> str:
        fp = self._state_fingerprint(state)
        return hashlib.sha256(f"{prev_hash}:{payload}:{fp}".encode()).hexdigest()[:16]

    def compute_K(self, state: CGRState, prev_hash: str, payload: str) -> int:
        expected = self.expected_ledger_hash(prev_hash, payload, state)
        return 1 if state.ledger_hash == expected else 0

    def compute_P(self, state: CGRState, expected_commit: str) -> int:
        return 1 if state.provenance_commit == expected_commit else 0

    # -------------------------
    # Scores
    # -------------------------
    def compute_L(self, curr: CGRState, prev: CGRState, dW: int) -> float:
        I1, I2 = self.compute_invariants(curr)
        pI1, pI2 = self.compute_invariants(prev)
        dI1, dI2 = abs(I1 - pI1), abs(I2 - pI2)
        L_phys = float(np.exp(-0.05 * dI1 - 0.05 * dI2))
        L_top = 1.0 if dW in self.thresholds["allowed_dW"] else 0.0
        L_ledger = 1.0 if isinstance(curr.ledger_hash, str) and len(curr.ledger_hash) > 0 else 0.0
        return float(L_phys * L_top * L_ledger)

    def compute_H(self, state: CGRState) -> float:
        max_w = max(self.h_windows)
        if len(state.mismatch_history) < max_w:
            return 0.0
        hs = []
        for w in self.h_windows:
            recent = np.asarray(state.mismatch_history[-w:], dtype=float)
            var = float(np.var(recent))
            hs.append(np.exp(-self.mu_h * var))
        return float(min(hs))

    def compute_A(self, E_pred: float, E_baseline: float) -> float:
        eps = 1e-9
        imp = 1.0 - (E_pred / (E_baseline + eps))
        return float(np.clip(imp, 0.0, 1.0))

    def compute_U(self, curr: CGRState, prev: CGRState, dW: int, K: int) -> float:
        I1, I2 = self.compute_invariants(curr)
        pI1, pI2 = self.compute_invariants(prev)
        dI1, dI2 = abs(I1 - pI1), abs(I2 - pI2)
        a1, a2, a3, a4 = self.alpha
        D = a1 * dI1 + a2 * dI2 + a3 * abs(dW) + a4 * (1 - K)
        return float(np.exp(-D))

    def check_E(self, metrics: Dict) -> int:
        if metrics["K"] != 1: return 0
        if metrics["L"] < self.thresholds["theta_L"]: return 0
        if metrics.get("dW", 0) not in self.thresholds["allowed_dW"]: return 0
        if metrics["H"] < self.thresholds["theta_H"]: return 0
        if metrics["P"] != 1: return 0
        if metrics["A"] < self.thresholds["theta_A"]: return 0
        if metrics["U"] < self.thresholds["theta_infinity"]: return 0
        if abs(metrics.get("dI1", 0.0)) > self.thresholds["catastrophic_dI"] or \
           abs(metrics.get("dI2", 0.0)) > self.thresholds["catastrophic_dI"]:
            return 0
        return 1

    def compute_V(self, A: float, U: float, E: int) -> float:
        return float(A * U * float(E))

    def infinity_gate_allowed(self, metrics: Dict) -> bool:
        return bool(metrics["U"] >= self.thresholds["theta_infinity"] and
                    metrics["L"] >= self.thresholds["theta_L"] and
                    metrics["K"] == 1)

    def bridge_allowed(self, metrics: Dict, state: CGRState) -> bool:
        V = self.compute_V(metrics["A"], metrics["U"], metrics["E"])
        # Evaluate momentum with "history + current" so the current tick participates,
        # but without relying on callers to pre-append correctly.
        recent = (state.V_history + [V])[-3:]
        if len(recent) < 3:
            state.V_history.append(V)
            return False
        mean_V = float(np.mean(recent))
        std_V = float(np.std(recent))
        state.V_history.append(V)
        return bool(mean_V >= self.thresholds["theta_V"] and
                    std_V < self.thresholds["V_std_max"] and
                    metrics["L"] >= self.thresholds["theta_L"] and
                    metrics["H"] >= self.thresholds["theta_H"] and
                    metrics["P"] == 1 and metrics["E"] == 1)

class CGRTests:
    def __init__(self, seed: int = 0):
        self.kernel = CGRKernel()
        self.results: Dict[str, Dict] = {}
        self.rng = np.random.default_rng(seed)

    def _make_state(self, E_mag: float = 0.0, B_mag: float = 0.0, W_seed: int = 0) -> CGRState:
        state = CGRState()
        state.E = self.rng.standard_normal(3) * float(E_mag)
        state.B = self.rng.standard_normal(3) * float(B_mag)
        x = np.linspace(0, 2 * np.pi, self.kernel.N, endpoint=False)
        y = np.linspace(0, 2 * np.pi, self.kernel.N, endpoint=False)
        X, Y = np.meshgrid(x, y)
        state.Theta = (X + Y) % (2 * np.pi)
        if W_seed != 0:
            cx, cy = state.node_pos
            N = self.kernel.N
            for i in range(N):
                for j in range(N):
                    dx, dy = i - cx, j - cy
                    angle = np.arctan2(dy, dx)
                    state.Theta[i, j] = (state.Theta[i, j] + W_seed * angle) % (2 * np.pi)
        return state

    def _append_mismatch(self, curr: CGRState, prev: CGRState, dW: int) -> None:
        I1, I2 = self.kernel.compute_invariants(curr)
        pI1, pI2 = self.kernel.compute_invariants(prev)
        dI1, dI2 = abs(I1 - pI1), abs(I2 - pI2)
        mismatch = float(dI1 + dI2 + 0.5 * abs(dW))
        curr.mismatch_history.append(mismatch)

    def compute_metrics(
        self,
        curr: CGRState,
        prev: CGRState,
        prev_hash: str,
        payload: str,
        E_pred: float,
        E_baseline: float,
        inputs_stub: bytes = b"stub",
        schema_hash: str = "schema123",
        version: str = "1.3-beta",
        validate: bool = True,
    ) -> Dict[str, Any]:
        if validate:
            try:
                self.kernel.validate_transition(prev, curr)
            except TransitionViolation as e:
                return {"violation": str(e), "valid": False}

        Wc = self.kernel.compute_W(curr)
        Wp = self.kernel.compute_W(prev)
        dW = int(Wc - Wp)

        I1, I2 = self.kernel.compute_invariants(curr)
        pI1, pI2 = self.kernel.compute_invariants(prev)
        dI1, dI2 = float(I1 - pI1), float(I2 - pI2)

        self._append_mismatch(curr, prev, dW)

        expected_commit = self.kernel.compute_provenance_commit(inputs_stub, schema_hash, version)

        K = self.kernel.compute_K(curr, prev_hash, payload)
        L = self.kernel.compute_L(curr, prev, dW)
        H = self.kernel.compute_H(curr)
        P = self.kernel.compute_P(curr, expected_commit)
        A = self.kernel.compute_A(E_pred, E_baseline)
        U = self.kernel.compute_U(curr, prev, dW, K)

        metrics = {
            "valid": True,
            "W": Wc, "dW": dW,
            "I1": I1, "I2": I2, "dI1": dI1, "dI2": dI2,
            "K": K, "L": L, "H": H, "P": P, "A": A, "U": U,
        }
        metrics["E"] = self.kernel.check_E(metrics)
        metrics["V"] = self.kernel.compute_V(metrics["A"], metrics["U"], metrics["E"])
        return metrics

    # -------------------------
    # Tests
    # -------------------------
    def test_1_null_loop(self):
        payload = "T1"
        prev_hash = "genesis"
        states: List[CGRState] = []
        for angle in np.linspace(0, 2 * np.pi, 10, endpoint=False):
            s = self._make_state()
            s.E = np.array([np.cos(angle), np.sin(angle), 0.0])
            s.B = np.array([-np.sin(angle), np.cos(angle), 0.0])
            x = np.linspace(0, 2 * np.pi, self.kernel.N, endpoint=False)
            y = np.linspace(0, 2 * np.pi, self.kernel.N, endpoint=False)
            X, Y = np.meshgrid(x, y)
            s.Theta = (X + Y) % (2 * np.pi)
            states.append(s)

        # Seed long history so multi-scale hysteresis is active
        states[0].mismatch_history = [0.1] * 20
        states[0].V_history = []

        Ls: List[float] = []
        W0 = self.kernel.compute_W(states[0])
        W_stable = True

        inputs_stub, schema_hash, version = b"stub", "schema123", "1.3-beta"

        for idx in range(1, len(states)):
            prev = states[idx - 1]
            curr = states[idx]

            curr.mismatch_history = list(prev.mismatch_history)
            curr.V_history = list(prev.V_history)
            curr.Theta = prev.Theta.copy()

            # Set commit FIRST, then ledger hash (fingerprint binds to commit)
            curr.provenance_commit = self.kernel.compute_provenance_commit(inputs_stub, schema_hash, version)
            curr.ledger_hash = self.kernel.expected_ledger_hash(prev_hash, payload, curr)

            metrics = self.compute_metrics(curr, prev, prev_hash, payload, 0.1, 0.5,
                                           inputs_stub=inputs_stub, schema_hash=schema_hash, version=version)
            if not metrics["valid"]:
                self.results["T1_NullLoop"] = {"pass": False, "error": metrics.get("violation")}
                return

            Ls.append(metrics["L"])
            W_stable = W_stable and (metrics["W"] == W0)
            prev_hash = curr.ledger_hash

        self.results["T1_NullLoop"] = {
            "pass": W_stable and (min(Ls) > 0.95),
            "W_stable": W_stable,
            "min_L": float(min(Ls)) if Ls else 0.0,
        }

    def test_2_non_null_defect(self):
        state0 = self._make_state(W_seed=0)
        state1 = self._make_state(W_seed=1)
        try:
            self.kernel.enforce_topological_continuity(state0, state1)
            valid = True
        except TransitionViolation:
            valid = False
        dW = int(self.kernel.compute_W(state1) - self.kernel.compute_W(state0))
        self.results["T2_Defect"] = {"pass": valid and (dW == 1), "dW": dW, "valid": valid}

    def test_3_hysteresis(self):
        stable = self._make_state()
        jitter = self._make_state()
        stable.mismatch_history = [0.2] * 20
        jitter.mismatch_history = [0.2] * 5 + [0.9] * 7 + [0.2] * 8
        H_A = self.kernel.compute_H(stable)
        H_B = self.kernel.compute_H(jitter)
        self.results["T3_Hysteresis"] = {
            "pass": (H_A > H_B) and (H_A >= self.kernel.thresholds["theta_H"]) and (H_B < self.kernel.thresholds["theta_H"]),
            "H_stable": float(H_A), "H_unstable": float(H_B),
        }

    def test_4_invariant_tear(self):
        payload = "T4"
        prev_hash = "genesis"
        inputs_stub, schema_hash, version = b"stub", "schema123", "1.3-beta"

        prev = self._make_state(E_mag=0.3)
        prev.mismatch_history = [0.1] * 20
        prev.provenance_commit = self.kernel.compute_provenance_commit(inputs_stub, schema_hash, version)
        prev.ledger_hash = self.kernel.expected_ledger_hash(prev_hash, payload, prev)

        curr = self._make_state()
        curr.mismatch_history = list(prev.mismatch_history)
        curr.provenance_commit = prev.provenance_commit
        curr.E = prev.E + np.array([2.0, 0.0, 0.0])  # Exceed norm cap
        curr.B = prev.B.copy()
        curr.Theta = prev.Theta.copy()
        curr.ledger_hash = self.kernel.expected_ledger_hash(prev.ledger_hash, payload, curr)

        metrics = self.compute_metrics(curr, prev, prev.ledger_hash, payload, 0.1, 0.5,
                                       inputs_stub=inputs_stub, schema_hash=schema_hash, version=version)
        if not metrics["valid"]:
            self.results["T4_InvariantTear"] = {"pass": True, "caught_by": "hard_cap", "violation": metrics.get("violation", "")}
        else:
            caught_by_threshold = metrics["E"] == 0 and abs(metrics["dI1"]) > 1.5
            self.results["T4_InvariantTear"] = {"pass": caught_by_threshold, "caught_by": "catastrophic_threshold" if caught_by_threshold else "none"}

    def test_5_replay_break(self):
        payload = "T5"
        prev_hash = "genesis"
        inputs_stub, schema_hash, version = b"stub", "schema123", "1.3-beta"

        prev = self._make_state()
        prev.mismatch_history = [0.1] * 20
        prev.provenance_commit = self.kernel.compute_provenance_commit(inputs_stub, schema_hash, version)
        prev.ledger_hash = self.kernel.expected_ledger_hash(prev_hash, payload, prev)

        curr = self._make_state()
        curr.mismatch_history = list(prev.mismatch_history)
        curr.provenance_commit = prev.provenance_commit
        curr.Theta = prev.Theta.copy()
        curr.ledger_hash = "tampered_hash"  # deliberately wrong

        metrics = self.compute_metrics(curr, prev, prev.ledger_hash, payload, 0.1, 0.5,
                                       inputs_stub=inputs_stub, schema_hash=schema_hash, version=version)
        allowed = self.kernel.infinity_gate_allowed(metrics) if metrics.get("valid") else False
        self.results["T5_ReplayBreak"] = {"pass": (not allowed) and (metrics["K"] == 0), "K": int(metrics["K"])}

    def test_6_bridge_no_ethics(self):
        payload = "T6"
        prev_hash = "genesis"
        inputs_stub, schema_hash, version = b"stub", "schema123", "1.3-beta"

        prev = self._make_state()
        prev.mismatch_history = [0.1] * 20
        prev.provenance_commit = self.kernel.compute_provenance_commit(inputs_stub, schema_hash, version)
        prev.ledger_hash = self.kernel.expected_ledger_hash(prev_hash, payload, prev)

        curr = self._make_state()
        curr.mismatch_history = list(prev.mismatch_history)
        curr.V_history = list(prev.V_history)
        curr.E = prev.E.copy()
        curr.B = prev.B.copy()
        curr.Theta = prev.Theta.copy()

        # Attacker sets a different provenance_commit (bad schema/version/etc.)
        curr.provenance_commit = self.kernel.compute_provenance_commit(inputs_stub, "BAD_SCHEMA", version)
        # Ledger hash is still internally consistent with that (so K can be 1),
        # but P must fail under the verifier's expected_commit.
        curr.ledger_hash = self.kernel.expected_ledger_hash(prev.ledger_hash, payload, curr)

        metrics = self.compute_metrics(curr, prev, prev.ledger_hash, payload, 0.1, 0.5,
                                       inputs_stub=inputs_stub, schema_hash=schema_hash, version=version)
        allowed = self.kernel.bridge_allowed(metrics, curr) if metrics.get("valid") else False
        self.results["T6_BridgeNoEthics"] = {"pass": (not allowed) and (metrics["P"] == 0) and (metrics["V"] == 0.0),
                                            "P": int(metrics["P"]), "V": float(metrics["V"])}

    def test_7_bridge_success(self):
        payload = "T7"
        prev_hash = "genesis"
        inputs_stub, schema_hash, version = b"stub", "schema123", "1.3-beta"

        prev = self._make_state()
        prev.mismatch_history = [0.1] * 20
        prev.provenance_commit = self.kernel.compute_provenance_commit(inputs_stub, schema_hash, version)
        prev.ledger_hash = self.kernel.expected_ledger_hash(prev_hash, payload, prev)

        # For determinism: keep fields identical so U≈1 and V≈A (A=0.8)
        prev.V_history = [0.8, 0.8]  # two prior stable ticks above theta_V

        curr = self._make_state()
        curr.mismatch_history = list(prev.mismatch_history)
        curr.V_history = list(prev.V_history)
        curr.E = prev.E.copy()
        curr.B = prev.B.copy()
        curr.Theta = prev.Theta.copy()
        curr.provenance_commit = prev.provenance_commit
        curr.ledger_hash = self.kernel.expected_ledger_hash(prev.ledger_hash, payload, curr)

        metrics = self.compute_metrics(curr, prev, prev.ledger_hash, payload, 0.1, 0.5,
                                       inputs_stub=inputs_stub, schema_hash=schema_hash, version=version)
        allowed = self.kernel.bridge_allowed(metrics, curr) if metrics.get("valid") else False
        self.results["T7_BridgeSuccess"] = {"pass": bool(allowed) and (metrics["V"] >= self.kernel.thresholds["theta_V"]),
                                            "V": float(metrics["V"])}

    def test_8_recursion(self):
        E_base = 0.5
        A_good = self.kernel.compute_A(0.1, E_base)
        A_bad = self.kernel.compute_A(0.6, E_base)
        self.results["T8_Recursion"] = {"pass": (A_good > A_bad) and (A_good >= 0.5),
                                        "A_good": float(A_good), "A_bad": float(A_bad)}

    def test_9_topological_smuggling(self):
        state0 = self._make_state(W_seed=0)
        state1 = self._make_state(W_seed=2)
        try:
            self.kernel.enforce_topological_continuity(state0, state1)
            caught = False
        except TransitionViolation:
            caught = True
        self.results["T9_TopoSmuggling"] = {"pass": caught, "caught": caught}

    def test_10_ethical_sprint(self):
        payload = "T10"
        prev_hash = "genesis"
        inputs_stub, schema_hash, version = b"stub", "schema123", "1.3-beta"

        prev = self._make_state()
        prev.mismatch_history = [0.1] * 20
        prev.provenance_commit = self.kernel.compute_provenance_commit(inputs_stub, schema_hash, version)
        prev.ledger_hash = self.kernel.expected_ledger_hash(prev_hash, payload, prev)

        curr = self._make_state()
        curr.mismatch_history = list(prev.mismatch_history)
        # Sprint pattern: low-low-high
        curr.V_history = [0.1, 0.2]
        curr.E = prev.E.copy()
        curr.B = prev.B.copy()
        curr.Theta = prev.Theta.copy()
        curr.provenance_commit = prev.provenance_commit
        curr.ledger_hash = self.kernel.expected_ledger_hash(prev.ledger_hash, payload, curr)

        metrics = self.compute_metrics(curr, prev, prev.ledger_hash, payload, 0.1, 0.5,
                                       inputs_stub=inputs_stub, schema_hash=schema_hash, version=version)
        allowed = self.kernel.bridge_allowed(metrics, curr) if metrics.get("valid") else False
        recent = curr.V_history[-3:]
        self.results["T10_EthicalSprint"] = {
            "pass": not allowed,
            "allowed": bool(allowed),
            "mean_V": float(np.mean(recent)),
            "std_V": float(np.std(recent)),
        }

    def run_all(self) -> bool:
        print("Running CGR Kernel Regression Suite v1.3-beta (Hardened) — FIXED")
        print("=" * 65)
        print("Defenses: Norm caps, Provenance commits, Multi-H, V-momentum")
        print("-" * 65)

        self.test_1_null_loop()
        self.test_2_non_null_defect()
        self.test_3_hysteresis()
        self.test_4_invariant_tear()
        self.test_5_replay_break()
        self.test_6_bridge_no_ethics()
        self.test_7_bridge_success()
        self.test_8_recursion()
        self.test_9_topological_smuggling()
        self.test_10_ethical_sprint()

        print(f"{'Test':<25} {'Result':<8} {'Key Metric'}")
        print("-" * 65)
        for name, data in self.results.items():
            status = "PASS" if data.get("pass") else "FAIL"
            if "min_L" in data:
                metric = f"L={data['min_L']:.3f}"
            elif "dW" in data:
                metric = f"dW={data['dW']}"
            elif "H_stable" in data:
                metric = f"ΔH={abs(data['H_stable']-data['H_unstable']):.3f}"
            elif "caught_by" in data:
                metric = f"caught:{data['caught_by']}"
            elif "caught" in data:
                metric = f"caught:{data['caught']}"
            elif "V" in data:
                metric = f"V={data['V']:.3f}"
            elif "K" in data:
                metric = f"K={data['K']}"
            elif "P" in data:
                metric = f"P={data['P']}, V={data.get('V',0.0):.3f}"
            elif "A_good" in data:
                metric = f"A={data['A_good']:.3f}"
            elif "allowed" in data:
                metric = f"allowed:{data['allowed']}, std_V={data['std_V']:.3f}"
            else:
                metric = ""
            print(f"{name:<25} {status:<8} {metric}")

        total = len(self.results)
        passed = sum(1 for r in self.results.values() if r.get("pass"))
        print("-" * 65)
        print(f"Summary: {passed}/{total} tests passed")
        print("Status:", "HARDENED (v1.3-beta)" if passed == total else "SOFT (vulnerabilities detected)")
        return passed == total

if __name__ == "__main__":
    suite = CGRTests(seed=0)
    ok = suite.run_all()
    raise SystemExit(0 if ok else 1)
