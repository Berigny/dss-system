"""Backend runtime configuration."""

from __future__ import annotations

import contextvars
import os


QP_PURE_ENABLED: bool = os.getenv("QP_PURE_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
"""Whether the genuine Qp pure-retrieval path is enabled.

This flag is introduced in DS-REVIEW-192. Phase 1 kill gates (field axioms,
rational round-trip, Hensel stability, immutability) are now covered by the
unit/benchmark suite, so the default is true. Set QP_PURE_ENABLED=false to
fall back to the mixed-signal ranker.
"""

QP_PRECISION_LOSS_WARNING: bool = os.getenv("QP_PRECISION_LOSS_WARNING", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
"""Whether to emit a warning when a ``QpElement`` precision is reduced.

Precision reduction is allowed but must be explicit and named.  When this flag
is enabled, ``QpElement.to_precision`` emits a ``PrecisionLossWarning`` so
callers can audit every truncation.
"""

# Per-request override for the global QP_PURE_ENABLED flag.  This lets
# middleware or other callers toggle the pure-Qp path for a single request
# without mutating the process-wide setting.
QP_PURE_OVERRIDE: contextvars.ContextVar[bool | None] = contextvars.ContextVar(
    "QP_PURE_OVERRIDE", default=None
)

# Deterministic execution mode for benchmarks and coordinate logic.
# Set DSS_DETERMINISTIC=true and optionally DSS_DETERMINISTIC_SEED=<int>.
DETERMINISTIC_MODE: bool = os.getenv("DSS_DETERMINISTIC", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
DEFAULT_DETERMINISTIC_SEED: int = int(
    os.getenv("DSS_DETERMINISTIC_SEED", "42").strip() or "42"
)


def qp_pure_enabled() -> bool:
    """Return the effective Qp-pure flag for the current request context."""
    override = QP_PURE_OVERRIDE.get()
    if override is not None:
        return bool(override)
    return bool(QP_PURE_ENABLED)
