To make **spec ⇄ code ⇄ governance** line up (and prevent “hidden side-bridges”), you want **one authoritative topology**, plus **two extra enforcement layers**:

1. **Static topology assertions** (fail fast if anyone adds edges like `1→4`, `3→6`, `5→0`, `7→2`).
2. **Governance-time substrate guard at C** (so you *can’t* “teleport” from S1 sink → C → S2 odd and accidentally create a bridge-at-C).

Below is the clean patch set for `backend/fieldx_kernel/flow_rules.py`.

---

## A) Make topology non-negotiable (static assertions)

Add these constants + assertions right after `ADJACENCY_RULES`:

```python
# --- TOPOLOGY LOCK --------------------------------------------------------

ALLOWED_BRIDGES = {(3, 4), (7, 0)}          # cross-substrate exits only
ALLOWED_TERMINAL_WRAPS = {(3, 0), (7, 4)}   # stay-in-substrate wraps only

S1_ODDS = {1, 3}
S2_ODDS = {5, 7}

def _is_cross_substrate_edge(a: int, b: int) -> bool:
    return (a in S1_NODES and b in S2_NODES) or (a in S2_NODES and b in S1_NODES)

def _assert_topology_locked() -> None:
    # Even sinks must go ONLY to C
    for e in EVEN_SINKS:
        assert ADJACENCY_RULES.get(e) == {C_NODE}, f"Sink {e} must go only to C."

    # C must go ONLY to odds (all odds are *reachable* in the abstract graph)
    assert ADJACENCY_RULES.get(C_NODE) == ODD_BRANCHES, "C must branch only to odd nodes."

    # Non-terminal odds must be single-forward
    assert ADJACENCY_RULES.get(1) == {2}, "1 must go only to 2."
    assert ADJACENCY_RULES.get(5) == {6}, "5 must go only to 6."

    # Terminal odds must be exactly the fork set {stay, bridge}
    assert ADJACENCY_RULES.get(3) == {0, 4}, "3 must fork only to {0,4}."
    assert ADJACENCY_RULES.get(7) == {4, 0}, "7 must fork only to {4,0}."

    # No hidden cross-substrate edges
    for a, outs in ADJACENCY_RULES.items():
        for b in outs:
            if _is_cross_substrate_edge(a, b):
                assert (a, b) in ALLOWED_BRIDGES, f"Illegal bridge edge present: {a}->{b}"

    # No hidden backward moves inside a substrate (except terminal wraps)
    for a, outs in ADJACENCY_RULES.items():
        for b in outs:
            if _same_substrate(a, b) and b < a:
                assert (a, b) in ALLOWED_TERMINAL_WRAPS, f"Illegal backflow edge present: {a}->{b}"

_assert_topology_locked()
```

This guarantees there are **no side-bridges** and **no backflow** beyond `{3→0, 7→4}`.

---

## B) Fix the current mediator function (it’s effectively “always GRACE”)

Your current `update_dynamic_mediator()` is logically redundant and (as pasted) also looks mis-indented. If your intent is “low coherence ⇒ LAW, otherwise ⇒ GRACE”, make it explicit:

```python
def update_dynamic_mediator(current_mediator: int, coherence_norm: float) -> int:
    COHERENCE_THRESHOLD = 0.98
    return LAW_PRIME if coherence_norm < COHERENCE_THRESHOLD else GRACE_PRIME
```

(If you later want hysteresis on mediator switching, add it here—*not* scattered elsewhere.)

---

## C) Enforce “bridges only at 3/7” at **governance time** (C inherits substrate)

This is the big one. Because C is a single node, adjacency alone allows `0→C→5` unless you **track substrate context**.

Patch `run_full_check()` so that when you see `C → odd`, you enforce:

* last known substrate S1 ⇒ next odd must be `{1,3}`
* last known substrate S2 ⇒ next odd must be `{5,7}`

Add this near the top of `run_full_check()`:

```python
def _substrate_of(node: int) -> int | None:
    if node in S1_NODES:
        return 0
    if node in S2_NODES:
        return 1
    return None
```

Then inside `run_full_check()` before the adjacency check, maintain `last_substrate`:

```python
last_substrate: int | None = None

for i in range(len(prime_sequence) - 1):
    curr_p = prime_sequence[i]
    next_p = prime_sequence[i + 1]

    if curr_p == next_p:
        continue

    curr_node = _get_node(curr_p)
    next_node = _get_node(next_p)

    # Track substrate whenever we're on a real substrate node
    s = _substrate_of(curr_node)
    if s is not None:
        last_substrate = s

    # Enforce "no cross at C": C must emit odds within the current substrate context
    if curr_node == C_NODE and next_node in ODD_BRANCHES:
        if last_substrate == 0 and next_node not in S1_ODDS:
            return False, f"FLOW VIOLATION (C-cross): C cannot route to S2 odd {next_node} from S1 context.", LAW_PRIME, LAW_UNLAWFUL
        if last_substrate == 1 and next_node not in S2_ODDS:
            return False, f"FLOW VIOLATION (C-cross): C cannot route to S1 odd {next_node} from S2 context.", LAW_PRIME, LAW_UNLAWFUL
        if last_substrate is None:
            return False, "FLOW VIOLATION (C-context): C routed to an odd without substrate context.", LAW_PRIME, LAW_UNLAWFUL
```

Now the **only** way to switch S1↔S2 is via the terminal bridges `3→4` and `7→0`, exactly as your spec demands.

---

## D) Important note on your later “cw picks S2 odds directly”

If you map `cw` to `(7,3,13,19)` and allow that choice at C regardless of current context, you *reintroduce* an implicit bridge: `S1 sink → C → S2 odd`. That violates your own “bridges only at 3/7” constraint.

So the clean rule is:

* **At C:** use `cw.b0` to choose **Air vs Earth odd within the current substrate**.
* **At terminals 3/7:** use `cw.b1` to choose **stay vs bridge**.

That keeps the topology pure and the control word cheap.

---