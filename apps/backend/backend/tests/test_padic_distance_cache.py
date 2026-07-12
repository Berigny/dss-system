from __future__ import annotations

from backend.fieldx_kernel.p_adic import (
    _cached_factor_int_value,
    _factor_int_value,
    p_adic_distance_for_factors,
)


def test_factor_int_value_uses_cache() -> None:
    factors = [{"prime": 2, "delta": 3}, {"prime": 3, "delta": 2}]

    # Prime the cache.
    first = _factor_int_value(factors)
    assert first == (2**3) * (3**2)

    info_before = _cached_factor_int_value.cache_info()
    second = _factor_int_value(factors)
    info_after = _cached_factor_int_value.cache_info()

    assert second == first
    # The second call should hit the cache rather than recompute.
    assert info_after.hits > info_before.hits


def test_p_adic_distance_caches_factor_values() -> None:
    a = [{"prime": 2, "delta": 1}, {"prime": 3, "delta": 2}]
    b = [{"prime": 5, "delta": 1}, {"prime": 2, "delta": 1}]

    p_adic_distance_for_factors(a, b, metric_prime=5)
    info_before = _cached_factor_int_value.cache_info()

    p_adic_distance_for_factors(a, b, metric_prime=5)
    info_after = _cached_factor_int_value.cache_info()

    # Both the a-value and b-value should be cache hits on the second call.
    assert info_after.hits - info_before.hits == 2


def test_factor_fingerprint_normalises_order_and_extra_fields() -> None:
    a = [{"prime": 3, "delta": 1}, {"prime": 2, "delta": 1}]
    b = [{"prime": 2, "delta": 1}, {"prime": 3, "delta": 1}]
    c = [{"prime": 2, "delta": 1}, {"prime": 3, "delta": 1, "extra": "ignored"}]

    assert _factor_int_value(a) == _factor_int_value(b) == _factor_int_value(c)


def test_p_adic_distance_cache_respects_metric_prime() -> None:
    factors = [{"prime": 3, "delta": 1}]

    d5, _ = p_adic_distance_for_factors(factors, factors, metric_prime=5)
    d3, _ = p_adic_distance_for_factors(factors, factors, metric_prime=3)

    assert d5 == 0.0
    assert d3 == 0.0
