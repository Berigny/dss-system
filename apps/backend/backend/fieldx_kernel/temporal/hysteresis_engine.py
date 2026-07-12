"""Hysteresis-driven temporal memory system for EQ2/EQ6/EQ7."""

from __future__ import annotations

import math
from collections import OrderedDict
from threading import RLock
from typing import List, Tuple

import numpy as np

from backend.fieldx_kernel.p_adic import PAdicInteger


class CoherentHysteresisEngine:
    """
    EQ 2 + Hysteresis - Temporal memory system that enforces irreversible learning.

    H = 1/ln2 (Landauer limit), used to price memory updates.
    """

    HYSTERESIS_CONSTANT = 1.0 / math.log(2.0)

    def __init__(self, memory_capacity: int = 8, epsilon: float = 9 / 256) -> None:
        self.memory_capacity = memory_capacity
        self.epsilon = epsilon
        self.activation_threshold = self.HYSTERESIS_CONSTANT * epsilon
        self.memory_buffer: List[float] = []
        self.coherence_history: List[float] = []
        self.total_update_energy = 0.0
        self.total_reconciliation_energy = 0.0
        self.temporal_state = 0

    def _append_memory(self, value: float) -> None:
        self.memory_buffer.append(value)
        if len(self.memory_buffer) > self.memory_capacity:
            self.memory_buffer.pop(0)

    def should_update_memory(self, new_value: float, old_value: float) -> Tuple[bool, float]:
        delta = abs(new_value - old_value)

        if delta > self.activation_threshold:
            update_cost = self.HYSTERESIS_CONSTANT * math.log(1.0 + delta)
            return True, update_cost

        deletion_cost = self.HYSTERESIS_CONSTANT
        reconcile_cost = delta * self.epsilon

        _ = deletion_cost  # Placeholder for future policy decisions.
        return False, reconcile_cost

    def update_memory(self, value: float, force_update: bool = False) -> float:
        if not self.memory_buffer:
            self._append_memory(value)
            return value

        old_value = self.memory_buffer[-1]
        should_update, cost = self.should_update_memory(value, old_value)

        if force_update or should_update:
            self._append_memory(value)
            self.total_update_energy += cost
            return value

        reconciled = (old_value + value) / 2.0
        self._append_memory(reconciled)
        self.total_reconciliation_energy += cost
        return reconciled

    def equation_2_temporalization(
        self,
        state: int,
        *,
        p: int = 3,
        N: int = 3,
        cycle_step: str | None = None,
        cycle_steps: int = 1,
        cycle_block_size: int = 1,
    ) -> int:
        """
        Finite-precision p-adic shift map on ``Z / p^N Z``.

        ``x_{t+1} = x_t + p^{v_p(x_t) + nudge} (mod p^N)`` where the nudge is
        derived from the engine's epsilon hysteresis parameter.  Zero is a
        fixed point.

        When ``cycle_step`` is provided, the named p-adic cycle automorphism is
        applied after the valuation shift.  Supported steps are
        ``"digit_rotation"``, ``"orientation_reversal"``, and
        ``"block_rotation"``.
        """
        padic = PAdicInteger.from_int(p, int(state), N)
        v = padic.valuation()
        if v == math.inf:
            return 0

        nudge = max(0, int(round(self.epsilon * N)))
        shift_exponent = min(int(v) + nudge, N - 1)
        increment = PAdicInteger.from_int(p, p**shift_exponent, N)
        result = padic + increment

        if cycle_step == "digit_rotation":
            result = result.digit_rotation(cycle_steps)
        elif cycle_step == "orientation_reversal":
            result = result.orientation_reversal()
        elif cycle_step == "block_rotation":
            result = result.block_rotation(cycle_block_size, cycle_steps)
        elif cycle_step is not None:
            raise ValueError(f"unsupported cycle_step: {cycle_step!r}")

        return result._value()


    def calculate_memory_coherence(self) -> float:
        if len(self.memory_buffer) < 2:
            return 1.0

        values = np.array(self.memory_buffer, dtype=float)
        max_abs = float(np.max(np.abs(values))) if values.size else 1.0
        if max_abs == 0.0:
            return 1.0
        memory_norm = values / max_abs
        distances = np.abs(memory_norm - np.roll(memory_norm, 1))
        avg_distance = float(np.mean(distances[1:])) if len(distances) > 1 else 0.0
        coherence = 1.0 / (1.0 + avg_distance)
        self.coherence_history.append(coherence)
        return coherence

    def get_temporal_arrow_strength(self) -> float:
        total_energy = self.total_update_energy + self.total_reconciliation_energy
        if len(self.memory_buffer) < 2:
            return 0.0
        strength = total_energy / len(self.memory_buffer)
        return strength / self.HYSTERESIS_CONSTANT


_ENGINE_CACHE: "OrderedDict[str, CoherentHysteresisEngine]" = OrderedDict()
_ENGINE_LOCK = RLock()


def get_entity_engine(
    entity: str,
    *,
    max_entries: int = 256,
    memory_capacity: int = 8,
    epsilon: float = 9 / 256,
) -> CoherentHysteresisEngine:
    key = (entity or "default").strip()
    with _ENGINE_LOCK:
        engine = _ENGINE_CACHE.get(key)
        if engine is not None:
            _ENGINE_CACHE.move_to_end(key)
            return engine
        engine = CoherentHysteresisEngine(memory_capacity=memory_capacity, epsilon=epsilon)
        _ENGINE_CACHE[key] = engine
        if len(_ENGINE_CACHE) > max_entries:
            _ENGINE_CACHE.popitem(last=False)
        return engine
