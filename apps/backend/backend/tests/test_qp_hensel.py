"""Hensel and Newton lifting tests for backend.fieldx_kernel.qp_arithmetic."""

from fractions import Fraction

import pytest

from backend.fieldx_kernel.qp_arithmetic import QpElement, hensel_lift, newton_lift


class TestHenselLift:
    def test_sqrt_2_mod_7(self):
        # x^2 - 2 has simple roots 3 and 4 modulo 7.
        f = lambda x: x * x - 2
        f_prime = lambda x: 2 * x

        root = hensel_lift(f, f_prime, 3, 7, 16)
        assert (root * root - 2) % (7**16) == 0
        assert root % 7 == 3

        root = hensel_lift(f, f_prime, 4, 7, 16)
        assert (root * root - 2) % (7**16) == 0
        assert root % 7 == 4

    def test_sqrt_minus_1_mod_5(self):
        # x^2 + 1 has simple roots 2 and 3 modulo 5.
        f = lambda x: x * x + 1
        f_prime = lambda x: 2 * x

        root = hensel_lift(f, f_prime, 2, 5, 16)
        assert (root * root + 1) % (5**16) == 0
        assert root % 5 == 2

        root = hensel_lift(f, f_prime, 3, 5, 16)
        assert (root * root + 1) % (5**16) == 0
        assert root % 5 == 3

    def test_degenerate_derivative_raises(self):
        # x^2 - 1 has derivative 2x; root 0 mod 2 is degenerate.
        f = lambda x: x * x - 1
        f_prime = lambda x: 2 * x
        with pytest.raises(ValueError):
            hensel_lift(f, f_prime, 1, 2, 8)

    def test_non_root_initial_guess_raises(self):
        f = lambda x: x * x - 2
        f_prime = lambda x: 2 * x
        with pytest.raises(ValueError):
            hensel_lift(f, f_prime, 2, 7, 8)

    def test_stability_suite(self):
        """Lift a simple root for several primes and precisions."""
        f = lambda x: x * x - 2
        f_prime = lambda x: 2 * x
        failures = 0
        total = 0
        for p in [3, 5, 7, 11, 13]:
            if pow(2, (p - 1) // 2, p) != 1:
                continue  # 2 is not a quadratic residue mod p
            a0 = next(a for a in range(1, p) if (a * a - 2) % p == 0)
            for N in [8, 16, 32]:
                total += 1
                try:
                    root = hensel_lift(f, f_prime, a0, p, N)
                    if (root * root - 2) % (p**N) != 0:
                        failures += 1
                except Exception:
                    failures += 1
        assert failures == 0, f"{failures}/{total} stability cases failed"


class TestNewtonLift:
    def test_fixed_point_identity(self):
        # g(x) = x is trivially a fixed point; newton_lift on g(x)-x requires g'(x)-1 != 0 mod p.
        # Use g(x) = x + x^2 - 2 so fixed points solve x^2 - 2 = 0 mod p.
        g = lambda x: x + x * x - 2
        g_prime = lambda x: 1 + 2 * x

        fixed = newton_lift(g, g_prime, 3, 7, 16)
        assert (fixed * fixed - 2) % (7**16) == 0

    def test_newton_degenerate_raises(self):
        g = lambda x: x  # every point is fixed, g'(x) - 1 = 0
        g_prime = lambda x: 1
        with pytest.raises(ValueError):
            newton_lift(g, g_prime, 1, 5, 8)


class TestHenselQpElementConsistency:
    def test_lifted_root_embeds_into_qp_element(self):
        f = lambda x: x * x - 2
        f_prime = lambda x: 2 * x
        root = hensel_lift(f, f_prime, 3, 7, 12)
        elem = QpElement.from_int(7, root, 12)
        # f(elem) should be zero to the working precision.
        assert (elem * elem - QpElement.from_int(7, 2, 12)).is_zero

