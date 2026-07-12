"""
Field-X kernel package.

This module exposes:
- The informational schema (primes, tiers, node mappings)
- The Kernel Origin Equations (Eq0–Eq9)
- Flow rules and mediator behaviour
- The memory/persistence subsystem (LedgerStoreV2, DualProcessMemory, etc.)
- The genuine-Qp field element (QpElement) and circulation-aware coordinate
  (QpCoordinate / DigitSymbol) placeholders for DS-REVIEW-192.

This allows the entire dualsubstrate system to import from a single unified kernel
without duplicating definitions or scattering constants.
"""

# -----------------------------
#  MEMORY / LEDGER SUBSYSTEM
# -----------------------------
from .models import ContinuousState, LedgerEntry, LedgerKey
from .substrate import LedgerStoreV2  # assumes this module exists
from .s1_s2_memory import DualProcessMemory
from .strain_register import StrainRegister
from .orchestrator import (
    assemble_context,
    build_chat_messages,
    complete_chat,
    enrich_turn,
)

# -----------------------------
#  GENUINE Qp / CIRCULATION LAYER
# -----------------------------
from .qp_arithmetic import QpElement
from .qp_coordinate import DigitSymbol, QpCoordinate

# -----------------------------
#  INFORMATIONAL SCHEMA
# -----------------------------
from .schema import (
    SOURCE_TIERS,
    TARGET_TIERS,
    MEDIATOR_TIERS,        # <-- fixed name here
    MEDIATOR_TWIN_PRIMES,
    PRIME_SCHEMA,
    LAW_PRIME,
    GRACE_PRIME,
    S_PRIMES,
    A_PRIMES,
    B_PRIMES,
    C_PRIMES,
    get_node_index,
)

# -----------------------------
#  KERNEL ORIGIN EQUATIONS (Eq0–Eq9)
# -----------------------------
from .kernel_origin_equations import (
    equation_0_paradox,
    equation_1_substrate_kernel_origin,
    equation_2_temporalization,
    equation_3_geometry,
    equation_6_consciousness,
    equation_6_consciousness_with_hysteresis,
    equation_7_coherence_mandate,
    equation_7_coherence_mandate_with_hysteresis,
    equation_9_teleology,
    calculate_alpha_from_primes,
    calculate_gravity_from_geometry,
    solve_ethics,
)
from .metrics import (
    CODATA_ALPHA_INV,
    compute_delta_sub,
    correlate_residual_to_sim_metrics,
)

# -----------------------------
#  FLOW RULES
# -----------------------------
from .flow_rules import (
    update_dynamic_mediator,
    run_full_check,
)

__all__ = [
    # memory subsystem
    "ContinuousState",
    "LedgerEntry",
    "LedgerKey",
    "LedgerStoreV2",
    "DualProcessMemory",
    "StrainRegister",
    # genuine Qp / circulation layer
    "QpElement",
    "DigitSymbol",
    "QpCoordinate",
    # orchestration helpers
    "assemble_context",
    "build_chat_messages",
    "complete_chat",
    "enrich_turn",
    # schema
    "SOURCE_TIERS",
    "TARGET_TIERS",
    "MEDIATOR_TIERS",
    "MEDIATOR_TWIN_PRIMES",
    "PRIME_SCHEMA",
    "LAW_PRIME",
    "GRACE_PRIME",
    "S_PRIMES",
    "A_PRIMES",
    "B_PRIMES",
    "C_PRIMES",
    "get_node_index",
    # equations
    "equation_0_paradox",
    "equation_1_substrate_kernel_origin",
    "equation_2_temporalization",
    "equation_3_geometry",
    "equation_6_consciousness",
    "equation_6_consciousness_with_hysteresis",
    "equation_7_coherence_mandate",
    "equation_7_coherence_mandate_with_hysteresis",
    "equation_9_teleology",
    "calculate_alpha_from_primes",
    "calculate_gravity_from_geometry",
    "solve_ethics",
    "CODATA_ALPHA_INV",
    "compute_delta_sub",
    "correlate_residual_to_sim_metrics",
    # flow rules
    "update_dynamic_mediator",
    "run_full_check",
]
