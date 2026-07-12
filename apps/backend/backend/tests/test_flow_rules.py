from backend.fieldx_kernel.flow_rules import (
    GRACE_PRIME,
    LAW_PRIME,
    LAW_UNLAWFUL,
    run_full_check,
    update_dynamic_mediator,
)


def test_update_dynamic_mediator_threshold_behavior() -> None:
    assert update_dynamic_mediator(GRACE_PRIME, 0.97) == LAW_PRIME
    assert update_dynamic_mediator(LAW_PRIME, 0.99) == GRACE_PRIME


def test_run_full_check_allows_terminal_bridge_only() -> None:
    # 2(node0,S1 sink) -> C -> 7(node3,S1 terminal) -> 11(node4,S2) bridge
    ok, msg, mediator, lawfulness = run_full_check([2, 137, 7, 11], 1.0)
    assert ok is True
    assert "lawful" in msg.lower()
    assert mediator == GRACE_PRIME
    assert lawfulness >= 2


def test_run_full_check_blocks_cross_substrate_side_bridge() -> None:
    # 3(node1,S1 odd) -> 17(node6,S2 even) is not an allowed bridge.
    ok, msg, mediator, lawfulness = run_full_check([3, 17], 1.0)
    assert ok is False
    assert "bridge" in msg.lower()
    assert mediator == LAW_PRIME
    assert lawfulness == LAW_UNLAWFUL


def test_run_full_check_blocks_c_cross_from_s1_context() -> None:
    # 2(node0,S1) -> C -> 13(node5,S2 odd) must be rejected.
    ok, msg, mediator, lawfulness = run_full_check([2, 137, 13], 1.0)
    assert ok is False
    assert "c-cross" in msg.lower()
    assert mediator == LAW_PRIME
    assert lawfulness == LAW_UNLAWFUL


def test_run_full_check_blocks_c_without_substrate_context() -> None:
    # Starting at C has no substrate context for odd branch selection.
    ok, msg, mediator, lawfulness = run_full_check([137, 3], 1.0)
    assert ok is False
    assert "c-context" in msg.lower()
    assert mediator == LAW_PRIME
    assert lawfulness == LAW_UNLAWFUL

