# backend/fieldx_kernel/schema.py
from typing import Dict, Final, Mapping, Tuple, FrozenSet

# --- TIERS / ROLES ---------------------------------------------------------

SOURCE_TIERS: Final[FrozenSet[str]] = frozenset({"S"})
TARGET_TIERS: Final[FrozenSet[str]] = frozenset({"A", "B"})
MEDIATOR_TIERS: Final[FrozenSet[str]] = frozenset({"C"})

# --- MEDIATOR (C) – twin primes for Law / Grace ---------------------------

MEDIATOR_TWIN_PRIMES: Final[Tuple[int, int]] = (137, 139)
LAW_PRIME: Final[int] = MEDIATOR_TWIN_PRIMES[0]   # Law, constraint, inner centroid
GRACE_PRIME: Final[int] = MEDIATOR_TWIN_PRIMES[1] # Grace, adaptation, outer centroid

# --- PRIME → NODE SCHEMA (authoritative informational map) ----------------

PRIME_SCHEMA: Final[Mapping[int, Dict[str, object]]] = {
    # S1 Substrate Factors (Flow Drivers) – Node 0–3
    2: {
        "name": "Novelty",
        "tier": "S",
        "mnemonic": "spark",
        "flow_role": "Source/S1",
        "node_index": 0,
        "conceptual_state": "Null / Eagle / Fire",
    },
    3: {
        "name": "Uniqueness",
        "tier": "S",
        "mnemonic": "spec",
        "flow_role": "Source/S1",
        "node_index": 1,
        "conceptual_state": "Electric / Lion / Air",
    },
    5: {
        "name": "Connection",
        "tier": "S",
        "mnemonic": "stitch",
        "flow_role": "Source/S1",
        "node_index": 2,
        "conceptual_state": "Magnetic / Ox / Water",
    },
    7: {
        "name": "Action",
        "tier": "S",
        "mnemonic": "step",
        "flow_role": "Source/S1",
        "node_index": 3,
        "conceptual_state": "Matter / Man / Earth",
    },

    # S2 Substrate Factors (Flow Destinations) – Node 4–7
    11: {
        "name": "Potential",
        "tier": "A",
        "mnemonic": "seed",
        "flow_role": "Target/S2",
        "node_index": 4,
        "conceptual_state": "Null / Output",
    },
    13: {
        "name": "Autonomy",
        "tier": "A",
        "mnemonic": "silo",
        "flow_role": "Target/S2",
        "node_index": 5,
        "conceptual_state": "Electric / Output",
    },
    17: {
        "name": "Context",
        "tier": "A",
        "mnemonic": "system",
        "flow_role": "Target/S2",
        "node_index": 6,
        "conceptual_state": "Magnetic / Output",
    },
    19: {
        "name": "Mastery",
        "tier": "A",
        "mnemonic": "standard",
        "flow_role": "Target/S2",
        "node_index": 7,
        "conceptual_state": "Matter / Output",
    },

    # Mediator (C)
    LAW_PRIME: {
        "name": "Law (Inner)",
        "tier": "C",
        "mnemonic": "ark",
        "flow_role": "Mediator/Law",
        "node_index": 99,
        "conceptual_state": "Aether / Contract (Descent)",
    },
    GRACE_PRIME: {
        "name": "Grace (Outer)",
        "tier": "C",
        "mnemonic": "grail",
        "flow_role": "Mediator/Grace",
        "node_index": 99,
        "conceptual_state": "Aether / Contract (Ascent)",
    },

    # Body primes (B tier – memory / recall etc). Node index 99 by design.
    23: {
        "name": "Recall",
        "tier": "B",
        "mnemonic": "scribe",
        "flow_role": "Target/B",
        "node_index": 99,
        "conceptual_state": "Consolidation",
    },
    # Add 29, 31, 37, etc. here when you’re ready.
}

# --- CONVENIENCE VIEWS -----------------------------------------------------

S_PRIMES: Final[Tuple[int, ...]] = tuple(
    p for p, meta in PRIME_SCHEMA.items() if meta["tier"] == "S"
)
A_PRIMES: Final[Tuple[int, ...]] = tuple(
    p for p, meta in PRIME_SCHEMA.items() if meta["tier"] == "A"
)
B_PRIMES: Final[Tuple[int, ...]] = tuple(
    p for p, meta in PRIME_SCHEMA.items() if meta["tier"] == "B"
)
C_PRIMES: Final[Tuple[int, ...]] = (LAW_PRIME, GRACE_PRIME)

FLOW_PRIMES: Final[FrozenSet[int]] = frozenset(S_PRIMES + A_PRIMES + C_PRIMES)
MIN_BODY_PRIME: Final[int] = 23



def get_node_index(prime: int) -> int:
    """
    Informational helper: map a prime to its u8 node index used in the Rust flow engine.
    Non-flow primes default to 99.
    """
    meta = PRIME_SCHEMA.get(prime)
    if meta is None:
        return 99
    node_idx = meta.get("node_index")
    return node_idx if isinstance(node_idx, int) else 99