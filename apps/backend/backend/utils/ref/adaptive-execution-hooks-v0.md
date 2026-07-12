# Adaptive Execution Hooks (v0)

Reference policy file:

- `backend/utils/ref/adaptive-execution-policy-v0.json`

Goal: wire pressure-aware autonomy into middleware without weakening safety floors.

## Hook Points

1. `before_assemble`
- Compute live pressure and select profile.
- Apply retrieval and recursion caps before context expansion.

2. `before_walk`
- If profile escalated, stop/limit walk depth immediately.

3. `before_guardian`
- Decide pre-output guardian vs deferred async enrichment.

4. `post_stream_enqueue`
- If deferred, enqueue guardian enrichment after response is fully streamed.

## Core Runtime State

Keep a small in-memory control state object:

```python
ControlState = {
  "profile": "FULL|FAST|MINIMAL",
  "pressure": 0.0,
  "last_switch_ms": 0,
  "stable_since_ms": 0,
  "node_class": "low|mid|high",
  "network_class": "good|normal|poor"
}
```

## Pressure Evaluator Pseudocode

```python
def evaluate_pressure(metrics, policy, baseline):
    compute = pressure_from_compute(metrics, baseline.node_class, policy)
    queue = pressure_from_queue(metrics, baseline.node_class, policy)
    token = pressure_from_token_drop(metrics, policy)
    network = pressure_from_network(metrics, baseline.network_class, policy) if metrics.is_online else 0
    stability = pressure_from_e6(metrics.e6, policy)
    return max(compute, queue, token, network, stability)
```

## Profile Transition Pseudocode (with hysteresis)

```python
def choose_profile(state, pressure, now_ms, policy):
    current = state["profile"]
    dwell_ok = (now_ms - state["last_switch_ms"]) >= policy["sampling"]["profile_min_dwell_ms"]

    target = current
    if pressure >= 70:
        target = "MINIMAL"
    elif pressure >= 35:
        target = "FAST"
    else:
        target = "FULL"

    if target == current:
        return current

    # Escalate immediately.
    if (target == "FAST" and current == "FULL") or target == "MINIMAL":
        return target

    # Downgrade only if stable for recovery window.
    stable_ms = now_ms - state["stable_since_ms"]
    if dwell_ok and stable_ms >= policy["sampling"]["profile_recovery_stable_ms"]:
        return target
    return current
```

## Hook: before_assemble

```python
def before_assemble(ctx):
    pressure = evaluate_pressure(ctx.metrics, ctx.policy, ctx.baseline)
    next_profile = choose_profile(ctx.state, pressure, ctx.now_ms, ctx.policy)
    ctx.state.update({"pressure": pressure, "profile": next_profile})

    profile_cfg = ctx.policy["profiles"][next_profile]["actions"]
    ctx.assemble.recursive = (profile_cfg["recursive_assembly"] == "on")
    ctx.assemble.top_k = min(ctx.assemble.top_k, profile_cfg["retrieval_top_k"])
    ctx.budgets.tokens = int(ctx.budgets.tokens * profile_cfg["token_budget_scale"])
    ctx.timeouts.scale(profile_cfg["timeout_scale"])
```

## Hook: before_walk

```python
def before_walk(ctx):
    profile_cfg = ctx.policy["profiles"][ctx.state["profile"]]["actions"]
    max_hops = int(profile_cfg["walk_hops_max"])
    if max_hops <= 0:
        ctx.walk.abort(reason="profile_walk_disabled")
        return
    ctx.walk.max_hops = min(ctx.walk.max_hops, max_hops)
```

## Hook: before_guardian

```python
def before_guardian(ctx):
    profile_cfg = ctx.policy["profiles"][ctx.state["profile"]]["actions"]
    mode = profile_cfg["guardian_pre_output"]

    if mode == "off":
        return "defer"

    if mode == "conditional":
        flags_ok = ctx.e6.K and ctx.e6.P and ctx.e6.E
        law_ok = ctx.e6.law >= 2
        queue_ok = ctx.metrics.req_queue_depth <= ctx.thresholds.queue_warn
        if not (flags_ok and law_ok and queue_ok):
            return "defer"

    return "run_pre_output"
```

## Hook: post_stream_enqueue

```python
def post_stream_enqueue(ctx):
    if ctx.guardian_mode != "defer":
        return
    ctx.turn.metadata["pending_enrichment"] = True
    ctx.turn.metadata["defer_reason"] = f"profile:{ctx.state['profile']}"
    ctx.jobs.enqueue("guardian_enrich_turn", turn_id=ctx.turn.id, entity=ctx.turn.entity)
```

## Safety Floor Rule

Do not bypass these checks in any profile:

1. schema validation
2. provenance checks
3. signature/nonce checks
4. hard lawfulness blocks

## Suggested Initial Wiring in Middleware

1. Compute pressure in request loop right before context assembly.
2. Store selected profile in turn metadata and logs.
3. Gate walk depth and guardian mode via profile actions.
4. Emit metrics per profile and switch reason.

