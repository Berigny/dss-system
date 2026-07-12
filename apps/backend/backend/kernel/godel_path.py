"""Gödel path encoding for kernel lattice traversal.

Every kernel node is assigned a deterministic prime (node n uses the (n+2)th
prime; the centroid uses the even prime 2).  A traversal path is encoded as the
product of those primes, so the path state is a single integer with exact
factorization semantics:

- Uniqueness: Fundamental Theorem of Arithmetic guarantees no collision.
- Common subpaths: GCD of two states reveals shared nodes.
- Centroid flag: parity of the state (divisible by 2).
- Path length: prime omega function (sum of factor exponents).

This is an additive module: it does not replace the semantic prime registry or
the prime-lattice machinery used by retrieval.
"""

from __future__ import annotations

import math
from functools import reduce
from typing import Iterable

# Kernel geometry: 27 nodes plus the centroid at day 13.
CENTROID_NODE: int = 13
TOTAL_NODES: int = 27


def _primes_upto(n: int) -> list[int]:
    """Return a list of primes <= n using trial division."""
    if n < 2:
        return []
    primes: list[int] = [2]
    candidate = 3
    while candidate <= n:
        is_prime = True
        limit = int(math.isqrt(candidate))
        for p in primes:
            if p > limit:
                break
            if candidate % p == 0:
                is_prime = False
                break
        if is_prime:
            primes.append(candidate)
        candidate += 2
    return primes


def _ensure_node_primes() -> dict[int, int]:
    """Return the deterministic node -> prime mapping used by Gödel encoding.

    Node n (0..26) maps to the (n+2)th prime, reserving prime 2 for the centroid.
    """
    # The 28th prime is 107, enough for nodes 0..26.
    primes = _primes_upto(110)
    mapping: dict[int, int] = {}
    for node in range(TOTAL_NODES):
        if node == CENTROID_NODE:
            mapping[node] = 2
        else:
            # (n+2)th prime, 1-indexed.  Node 0 -> 3 (3rd prime, index 2).
            mapping[node] = primes[node + 1]
    return mapping


NODE_TO_GODEL_PRIME: dict[int, int] = _ensure_node_primes()
GODEL_PRIME_TO_NODE: dict[int, int] = {p: n for n, p in NODE_TO_GODEL_PRIME.items()}


def encode_path(node_sequence: Iterable[int]) -> int:
    """Encode a traversal path as a unique integer.

    Args:
        node_sequence: sequence of node indices (0..26). Revisits are allowed
            and are represented by prime multiplicity.

    Returns:
        The Gödel-encoded path state.

    Raises:
        ValueError: if any node index is outside 0..26.
    """
    state = 1
    for node in node_sequence:
        if node not in NODE_TO_GODEL_PRIME:
            raise ValueError(f"Invalid kernel node: {node!r}")
        state *= NODE_TO_GODEL_PRIME[node]
    return state


def _factorint(n: int) -> dict[int, int]:
    """Return prime factor exponents for a positive integer (trial division)."""
    if n < 1:
        return {}
    factors: dict[int, int] = {}
    remaining = n
    # Use the known Gödel primes first; any unknown prime would be invalid.
    for prime in sorted(GODEL_PRIME_TO_NODE.keys()):
        while remaining % prime == 0:
            factors[prime] = factors.get(prime, 0) + 1
            remaining //= prime
        if remaining == 1:
            break
    if remaining > 1:
        # Defensive: factor out any stray prime.
        candidate = 2
        while candidate * candidate <= remaining:
            while remaining % candidate == 0:
                factors[candidate] = factors.get(candidate, 0) + 1
                remaining //= candidate
            candidate += 1 if candidate == 2 else 2
        if remaining > 1:
            factors[remaining] = factors.get(remaining, 0) + 1
    return factors


def decode_path(state: int) -> list[int]:
    """Recover the multiset of nodes from a path state.

    Because multiplication is commutative, order is not preserved; the returned
    nodes are sorted by their assigned prime.
    """
    if state < 1:
        return []
    factors = _factorint(state)
    nodes: list[int] = []
    for prime in sorted(factors.keys()):
        node = GODEL_PRIME_TO_NODE.get(prime)
        if node is None:
            raise ValueError(f"Unknown prime factor {prime} in path state")
        nodes.extend([node] * factors[prime])
    return nodes


def path_overlap(state_a: int, state_b: int) -> list[int]:
    """Return the shared nodes between two paths via GCD."""
    common = math.gcd(state_a, state_b)
    return decode_path(common)


def centroid_visited(state: int) -> bool:
    """Return True if the centroid (node 13) is present in the path state."""
    return state % 2 == 0


def path_length(state: int) -> int:
    """Return the total number of node visits, including revisits."""
    return sum(_factorint(state).values())


def path_contains(state: int, node: int) -> bool:
    """Return True if ``node`` appears in the path state."""
    if node not in NODE_TO_GODEL_PRIME:
        return False
    return state % NODE_TO_GODEL_PRIME[node] == 0
