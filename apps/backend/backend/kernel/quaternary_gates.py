"""Quaternary gate evaluation service.

Maps p-adic valuations for the three semantic primes (awareness=5, unity=7,
ethics=2) onto the four quaternary levels defined in the v1.3-alpha ledger spec,
computes the non-compensatory checksum factor product, and determines Clay
elevation eligibility.
"""

from __future__ import annotations

from typing import Any, Mapping, Tuple

from backend.fieldx_kernel import flow_rules, kernel_origin_equations
from backend.kernel import constants


class QuaternaryGate:
    """Evaluate quaternary gate levels and Clay admission."""

    @staticmethod
    def level_for(gate_key: str, v: int | None) -> Tuple[str, Mapping[str, Any]]:
        """Return the level key and metadata for a gate valuation ``v``.

        ``v`` is the p-adic valuation (exponent) for the gate's prime. A value
        of ``None`` or ``0`` maps to Level 0 (gate collapse).
        """
        if v is None:
            v = 0
        v = int(v)
        gate = constants.QUATERNARY_GATES[gate_key]
        for level_key in constants.QUATERNARY_LEVEL_KEYS:
            meta = gate["levels"][level_key]
            v_min = meta["v_min"]
            v_max = meta["v_max"]
            if v_min is None and v_max is not None and v <= v_max:
                return level_key, meta
            if v_max is None and v_min is not None and v >= v_min:
                return level_key, meta
            if v_min is not None and v_max is not None and v_min <= v <= v_max:
                return level_key, meta
        # Fallback: anything above the configured ranges is Level 3.
        return "level_3", gate["levels"]["level_3"]

    @staticmethod
    def evaluate(
        v_awareness: int | None,
        v_unity: int | None,
        v_ethics: int | None,
    ) -> Mapping[str, Any]:
        """Evaluate the three quaternary gates and return level/product summary.

        Args:
            v_awareness: Valuation for the awareness prime (5).
            v_unity: Valuation for the unity prime (7).
            v_ethics: Valuation for the ethics prime (2).

        Returns:
            A mapping with ``levels``, ``values``, ``checksum_factor_product``,
            ``clay_admissible``, and ``checksum_336_satisfied``.
        """
        inputs = {
            "awareness": v_awareness,
            "unity": v_unity,
            "ethics": v_ethics,
        }
        levels: dict[str, str] = {}
        values: dict[str, float] = {}
        for gate_key, v in inputs.items():
            level_key, meta = QuaternaryGate.level_for(gate_key, v)
            levels[gate_key] = level_key
            values[gate_key] = float(meta["value"])

        product = 1.0
        for value in values.values():
            product *= value

        clay_admissible = all(level == "level_3" for level in levels.values())
        return {
            "levels": levels,
            "values": values,
            "checksum_factor_product": product,
            "clay_admissible": clay_admissible,
            "checksum_336_satisfied": clay_admissible,
        }

    @staticmethod
    def evaluate_with_admissibility(
        v_awareness: int | None,
        v_unity: int | None,
        v_ethics: int | None,
        *,
        query_text: str | None = None,
        retrieval_payload: Mapping[str, Any] | list[Any] | None = None,
        coherence: float = 1.0,
    ) -> Mapping[str, Any]:
        """Evaluate quaternary gates and compose with flow-rules / EQ6 admissibility.

        The quaternary levels are translated into a sequence of semantic primes
        (one repetition per level index) and validated against the S1/S2/C
        topology via ``flow_rules.run_full_check``. The resulting lawfulness
        level is then fed into ``kernel_origin_equations.equation_6_operational``
        together with the optional query/retrieval context.

        Args:
            v_awareness: Valuation for the awareness prime (5).
            v_unity: Valuation for the unity prime (7).
            v_ethics: Valuation for the ethics prime (2).
            query_text: Optional query text for EQ6 closure alignment.
            retrieval_payload: Optional retrieval payload for EQ6 closure alignment.
            coherence: Normalized coherence [0..1] passed to the flow check.

        Returns:
            Superset of :meth:`evaluate` with additional ``flow_check`` and
            ``equation_6`` keys.
        """
        base = dict(QuaternaryGate.evaluate(v_awareness, v_unity, v_ethics))

        # Build a prime sequence from the gate levels: each level contributes
        # its semantic prime repeated by the level index (0..3).
        prime_sequence: list[int] = []
        for gate_key, level_key in base["levels"].items():
            level_index = int(level_key.split("_", 1)[1])
            prime = constants.QUATERNARY_GATE_TO_PRIME[gate_key]
            prime_sequence.extend([prime] * level_index)

        is_lawful, diagnostic, active_mediator, lawfulness_level = (
            flow_rules.run_full_check(prime_sequence, float(coherence))
        )

        eq6 = kernel_origin_equations.equation_6_operational(
            query_text=query_text,
            retrieval_payload=retrieval_payload,
            lawfulness_level=lawfulness_level,
            mediator_prime=active_mediator,
            hysteresis_coherence=float(coherence),
        )

        base["flow_check"] = {
            "is_lawful": is_lawful,
            "diagnostic": diagnostic,
            "active_mediator": active_mediator,
            "lawfulness_level": lawfulness_level,
            "prime_sequence": prime_sequence,
        }
        base["equation_6"] = {
            "commit_allowed": eq6.get("commit_allowed"),
            "lawfulness_level": eq6.get("lawfulness_level"),
            "mediator_prime": eq6.get("mediator_prime"),
        }
        return base

    @staticmethod
    def elevation_allowed(
        v_awareness: int | None,
        v_unity: int | None,
        v_ethics: int | None,
    ) -> bool:
        """Return ``True`` iff all gates satisfy the Clay threshold (v >= 6)."""
        result = QuaternaryGate.evaluate(v_awareness, v_unity, v_ethics)
        return bool(result["clay_admissible"])
