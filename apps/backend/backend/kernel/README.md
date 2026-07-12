# DSS Kernel Semantic Layer

> **KSR Scope Statement:** The DSS Kernel is a coherence protocol, not a knowledge base. It verifies structural consistency across domains using 27 geometric invariants, not factual accuracy. Knowledge itself lives in external vector stores, knowledge graphs, and language models; the kernel checks whether that knowledge violates geometric constraints.

## Confidence taxonomy

Every cross-domain mapping in the Kernel Semantic Registry (KSR) is labeled with one of five confidence levels:

| Level | Name | Meaning | Use in public surfaces |
|-------|------|---------|------------------------|
| **S** | Structural | Mathematical identity or provable isomorphism | Allowed |
| **A** | Archetypal | Independently observed across 3+ traditions | Allowed |
| **E** | Empirical | Verifiable within a domain discipline | Allowed |
| **P** | Poetic | Metaphorical resonance | Steward-only |
| **H** | Heuristic | Working hypothesis | Steward-only |

## Relation types

Cross-domain relations are explicitly typed as one of:

- `IDENTITY` — the same object under two names.
- `ISOMORPHISM` — structurally identical formal objects.
- `ANALOGY` — shared structural role across domains.
- `METAPHOR` — poetic resonance without structural identity.

## Gödel path encoding

Traversal paths through the 27-node lattice can be encoded as a single integer by assigning each node a deterministic prime and taking the product. See `backend/kernel/godel_path.py` for encode/decode, common-subpath detection via GCD, centroid detection via parity, and path-length computation via the prime omega function.

## Operational binding

`backend/kernel/constants.py` is generated from the plaintext KSR at build time and contains only strict engineering terminology. Runtime code imports these constants; the encrypted KSR is reserved for steward access and structural-integrity verification.
