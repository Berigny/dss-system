"""Deterministic execution utilities for DSS benchmarks and coordinate logic.

Provides a process-wide deterministic mode plus a context manager that seeds the
global pseudo-random generators used by the benchmark suite.  The goal is to make
coordinate walks, benchmark samples, and stochastic helpers reproducible across
runs that share the same seed.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import random
from typing import Generator


DETERMINISTIC_MODE: bool = os.getenv("DSS_DETERMINISTIC", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
"""Whether the process starts in deterministic mode.

Set ``DSS_DETERMINISTIC=true`` before importing benchmark code to enable seed-
controlled execution by default.
"""

DEFAULT_DETERMINISTIC_SEED: int = int(
    os.getenv("DSS_DETERMINISTIC_SEED", "42").strip() or "42"
)
"""Default seed used when deterministic mode is enabled without an explicit seed."""

_CURRENT_SEED: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "dss_deterministic_seed", default=None
)


def is_deterministic_mode() -> bool:
    """Return True if deterministic execution is currently active."""
    return bool(DETERMINISTIC_MODE) or _CURRENT_SEED.get() is not None


def current_seed() -> int | None:
    """Return the seed active in the current context, if any."""
    return _CURRENT_SEED.get()


def set_global_seed(seed: int) -> None:
    """Seed every global PRNG that DSS benchmark code depends on.

    Seeds the standard ``random`` module and, if present, ``numpy``.  Optional
    dependencies are seeded defensively so the function never fails.
    """
    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed)
    except Exception:
        pass


def reset_global_seed() -> None:
    """Reset global PRNGs to a fresh, non-deterministic state.

    Useful in tests that need to leave deterministic context and avoid
    cross-test contamination.
    """
    random.seed()
    try:
        import numpy as np

        np.random.seed()
    except Exception:
        pass


@contextlib.contextmanager
def deterministic_context(seed: int | None = None) -> Generator[int, None, None]:
    """Run a block of code under a fixed global seed.

    The previous random state is restored when the context exits, preventing
    cross-test or cross-run contamination.
    """
    active_seed = seed if seed is not None else DEFAULT_DETERMINISTIC_SEED
    token = _CURRENT_SEED.set(active_seed)

    # Preserve existing state so we can restore it after the block.
    prev_random_state = random.getstate()
    prev_numpy_state = None
    try:
        import numpy as np

        prev_numpy_state = np.random.get_state()
    except Exception:
        pass

    set_global_seed(active_seed)
    try:
        yield active_seed
    finally:
        random.setstate(prev_random_state)
        if prev_numpy_state is not None:
            try:
                import numpy as np

                np.random.set_state(prev_numpy_state)
            except Exception:
                pass
        _CURRENT_SEED.reset(token)


def ensure_deterministic(seed: int | None = None) -> int:
    """Activate deterministic mode if it is not already active and return the seed."""
    if _CURRENT_SEED.get() is not None:
        return _CURRENT_SEED.get()  # type: ignore[return-value]
    active_seed = seed if seed is not None else DEFAULT_DETERMINISTIC_SEED
    set_global_seed(active_seed)
    return active_seed


__all__ = (
    "DETERMINISTIC_MODE",
    "DEFAULT_DETERMINISTIC_SEED",
    "is_deterministic_mode",
    "current_seed",
    "set_global_seed",
    "reset_global_seed",
    "deterministic_context",
    "ensure_deterministic",
)
