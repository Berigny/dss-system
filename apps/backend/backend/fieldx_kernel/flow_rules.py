"""
Directed flow rules for the S1/S2 Metatron cube with centroid C (99).

Core rules:
- Even nodes are sinks: {0,2,4,6} must go to C.
- C branches only to odd nodes: {1,3,5,7}.
- Odd nodes emit forward to evens:
    1 -> 2
    5 -> 6
    3 -> {0,4}  (fork: stay S1 or bridge to S2)
    7 -> {4,0}  (fork: stay S2 or bridge to S1)
- Backflow is illegal (e.g. 2->1), except terminal wraps: 3->0 and 7->4.
"""

from typing import List, Tuple, Set, Dict

from backend.fieldx_kernel.schema import LAW_PRIME, GRACE_PRIME

# --- TOPOLOGICAL CONSTANTS ------------------------------------------------

PRIME_TO_NODE: Dict[int, int] = {
    # S1 (Day)
    2: 0,
    3: 1,
    5: 2,
    7: 3,

    # S2 (Night)
    11: 4,
    13: 5,
    17: 6,
    19: 7,

    # Centroid (Law/Grace share the same node index)
    137: 99,
    139: 99,
}

C_NODE = 99
S1_NODES = {0, 1, 2, 3}
S2_NODES = {4, 5, 6, 7}
EVEN_SINKS = {0, 2, 4, 6}
ODD_BRANCHES = {1, 3, 5, 7}

# Topology lock constraints
ALLOWED_BRIDGES = {(3, 4), (7, 0)}          # cross-substrate exits only
ALLOWED_TERMINAL_WRAPS = {(3, 0), (7, 4)}   # stay-in-substrate wraps only
S1_ODDS = {1, 3}
S2_ODDS = {5, 7}

# The Adjacency Matrix (Directed Graph) — aligned to latest spec
ADJACENCY_RULES: Dict[int, Set[int]] = {
    # Even sinks -> C only
    0: {C_NODE},
    2: {C_NODE},
    4: {C_NODE},
    6: {C_NODE},

    # Centroid -> odd only (base-4 choice)
    C_NODE: {1, 3, 5, 7},

    # Odd emissions
    1: {2},
    5: {6},

    # Terminal forks (stay vs bridge)
    3: {0, 4},  # stay S1 or bridge to S2
    7: {4, 0},  # stay S2 or bridge to S1
}

# --- LAWFULNESS LEVELS ----------------------------------------------------
LAW_FULL = 3
LAW_CONDITIONAL = 2
LAW_MARGINAL = 1
LAW_UNLAWFUL = 0

# --- DYNAMIC MEDIATOR LOGIC ----------------------------------------------

def update_dynamic_mediator(current_mediator: int, coherence_norm: float) -> int:
    """
    Switches the Centroid prime between Law (137) and Grace (139).
    High coherence allows the system to hold the 'Grace' state.
    """
    COHERENCE_THRESHOLD = 0.98
    return LAW_PRIME if coherence_norm < COHERENCE_THRESHOLD else GRACE_PRIME


# --- FLOW VALIDATION ENGINE -----------------------------------------------

def _get_node(prime: int) -> int:
    """Safely resolve a prime to its node index, defaulting to -1 (Unknown)."""
    return PRIME_TO_NODE.get(prime, -1)

def _same_substrate(a: int, b: int) -> bool:
    return (a in S1_NODES and b in S1_NODES) or (a in S2_NODES and b in S2_NODES)

def _is_cross_substrate_edge(a: int, b: int) -> bool:
    return (a in S1_NODES and b in S2_NODES) or (a in S2_NODES and b in S1_NODES)

def _substrate_of(node: int) -> int | None:
    if node in S1_NODES:
        return 0
    if node in S2_NODES:
        return 1
    return None

def _assert_topology_locked() -> None:
    # Even sinks must go only to C.
    for e in EVEN_SINKS:
        assert ADJACENCY_RULES.get(e) == {C_NODE}, f"Sink {e} must go only to C."

    # C must branch only to odd nodes.
    assert ADJACENCY_RULES.get(C_NODE) == ODD_BRANCHES, "C must branch only to odd nodes."

    # Non-terminal odd routes are single-forward.
    assert ADJACENCY_RULES.get(1) == {2}, "1 must go only to 2."
    assert ADJACENCY_RULES.get(5) == {6}, "5 must go only to 6."

    # Terminal odds fork as stay/bridge only.
    assert ADJACENCY_RULES.get(3) == {0, 4}, "3 must fork only to {0,4}."
    assert ADJACENCY_RULES.get(7) == {4, 0}, "7 must fork only to {4,0}."

    # No hidden cross-substrate edges.
    for a, outs in ADJACENCY_RULES.items():
        for b in outs:
            if _is_cross_substrate_edge(a, b):
                assert (a, b) in ALLOWED_BRIDGES, f"Illegal bridge edge present: {a}->{b}"

    # No hidden backflow except terminal wraps.
    for a, outs in ADJACENCY_RULES.items():
        for b in outs:
            if _same_substrate(a, b) and b < a:
                assert (a, b) in ALLOWED_TERMINAL_WRAPS, f"Illegal backflow edge present: {a}->{b}"

_assert_topology_locked()

def _is_backflow(curr_node: int, next_node: int) -> bool:
    """
    Backflow = decreasing order within a substrate,
    except explicitly allowed terminal wraps (3->0, 7->4).
    """
    if not _same_substrate(curr_node, next_node):
        return False
    if (curr_node, next_node) in ALLOWED_TERMINAL_WRAPS:
        return False
    return next_node < curr_node


def get_valid_successors(current_prime: int) -> Set[int]:
    """
    Returns the set of lawful next primes for a given current prime.
    Useful for 'next step' prediction agents.
    """
    node = _get_node(current_prime)
    if node == -1:
        return set()

    valid_next_nodes = ADJACENCY_RULES.get(node, set())
    
    # Map back nodes -> primes (Reverse lookup)
    # Note: This returns ALL primes that map to the valid nodes.
    valid_primes = set()
    for p, n in PRIME_TO_NODE.items():
        if n in valid_next_nodes:
            valid_primes.add(p)
    
    return valid_primes


def run_full_check(prime_sequence: List[int], current_coherence: float) -> Tuple[bool, str, int, int]:
    """
    Validates a sequence of primes against the 12-Fold Topology.
    
    Args:
        prime_sequence: List of integer primes representing the flow path.
        current_coherence: Float [0..1] representing system stability.

    Returns:
        (is_lawful, diagnostic_message, active_mediator_prime)
    """
    active_mediator = update_dynamic_mediator(LAW_PRIME, current_coherence)

    if not prime_sequence:
        return True, "Empty sequence (Neutral)", active_mediator, LAW_FULL

    lawfulness_level = LAW_FULL

    # Validate step-by-step
    last_substrate: int | None = None
    for i in range(len(prime_sequence) - 1):
        curr_p = prime_sequence[i]
        next_p = prime_sequence[i+1]

        # 1. Allow Self-Loops (Stuttering/Holding Pattern)
        if curr_p == next_p:
            continue
        
        # 2. Check Adjacency
        curr_node = _get_node(curr_p)
        next_node = _get_node(next_p)

        # CASE A: Known -> Known (Strict Topology)
        if curr_node != -1 and next_node != -1:
            substrate = _substrate_of(curr_node)
            if substrate is not None:
                last_substrate = substrate

            # C emits odds only within inherited substrate context.
            if curr_node == C_NODE and next_node in ODD_BRANCHES:
                if last_substrate == 0 and next_node not in S1_ODDS:
                    return (
                        False,
                        f"FLOW VIOLATION (C-cross): C cannot route to S2 odd {next_node} from S1 context.",
                        LAW_PRIME,
                        LAW_UNLAWFUL,
                    )
                if last_substrate == 1 and next_node not in S2_ODDS:
                    return (
                        False,
                        f"FLOW VIOLATION (C-cross): C cannot route to S1 odd {next_node} from S2 context.",
                        LAW_PRIME,
                        LAW_UNLAWFUL,
                    )
                if last_substrate is None:
                    return (
                        False,
                        "FLOW VIOLATION (C-context): C routed to an odd without substrate context.",
                        LAW_PRIME,
                        LAW_UNLAWFUL,
                    )

            if _is_backflow(curr_node, next_node):
                return (
                    False,
                    f"FLOW VIOLATION (Backflow): {curr_p}[{curr_node}] -> {next_p}[{next_node}] is illegal.",
                    LAW_PRIME,
                    LAW_UNLAWFUL,
                )
            if _is_cross_substrate_edge(curr_node, next_node) and (curr_node, next_node) not in ALLOWED_BRIDGES:
                return (
                    False,
                    f"FLOW VIOLATION (Bridge): {curr_p}[{curr_node}] -> {next_p}[{next_node}] is not an allowed bridge.",
                    LAW_PRIME,
                    LAW_UNLAWFUL,
                )
            allowed = ADJACENCY_RULES.get(curr_node, set())
            if next_node not in allowed:
                return (
                    False,
                    f"FLOW VIOLATION (Adjacency): {curr_p}[{curr_node}] cannot flow to {next_p}[{next_node}]. "
                    f"Valid targets: {sorted(list(allowed))}",
                    LAW_PRIME,
                    LAW_UNLAWFUL,
                )
            continue

        # CASE B: Known -> Body (Creation)
        if curr_node != -1 and next_node == -1:
            lawfulness_level = min(lawfulness_level, LAW_CONDITIONAL)
            continue

        # CASE C: Body -> Known (Re-entry)
        if curr_node == -1 and next_node != -1:
            lawfulness_level = min(lawfulness_level, LAW_MARGINAL)
            continue

        # CASE D: Body -> Body (Elaboration)
        if curr_node == -1 and next_node == -1:
            lawfulness_level = min(lawfulness_level, LAW_CONDITIONAL)
            continue

    return True, f"Flow sequence lawful (L{lawfulness_level}).", active_mediator, lawfulness_level
