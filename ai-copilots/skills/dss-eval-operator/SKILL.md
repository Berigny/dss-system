---
name: dss-eval-operator
description: >-
  Operate the DSS-EVAL public benchmark repository; load when installing
  dependencies, running eval or eval-fast, matrix/leaderboard targets, or
  changing claims and report artifacts.
---

# DSS-EVAL operator

Use this skill when an agent must set up, run, or extend the public DSS-EVAL
benchmark suite in this repository.

## Operating contract

**CONSTRAINT:** Run repository commands from the `dss-system` git root (the
directory that contains the top-level `Makefile`).

- Enforcement: confirm `test -f Makefile` and `test -d dss-benchmark-standalone`
- Violation: STOP, change to the repository root, and re-verify

**CONSTRAINT:** Use the existing Make targets for named project workflows.

- Enforcement: inspect `make help` and prefer `make install`, `make compile`,
  `make eval-fast`, `make eval`, `make matrix`, `make leaderboard`, or
  `make clean`
- Violation: STOP, use the existing target or explain why a direct command is
  required

CORRECT:

```bash
make install
make eval-fast
```

PROHIBITED:

```bash
cd dss-benchmark-standalone && python harness/runner.py
# without documenting why Make is insufficient
```

**CONSTRAINT:** Prefer `make eval-fast` for local smoke checks. Use `make eval`
or `make matrix` only when the requested coverage needs the full suite.

- Enforcement: match the requested purpose to the Make target before running
- Violation: STOP, choose the lighter target when it satisfies the ask

**CONSTRAINT:** Treat generated reports under `eval/reports/` and
`dss-benchmark-standalone/eval/reports/` as output artifacts, not source inputs,
unless the change explicitly updates a checked-in fixture.

- Enforcement: inspect `.gitignore` and `git status --short` after eval runs
- Violation: STOP, remove unintended generated files from the change

**CONSTRAINT:** Keep claims machine-enforced. Active claims live under
`dss-benchmark-standalone/eval/` (for example `claims_registry.yaml` /
`claims_registry.active.yaml`). MUST NOT invent unregistered claim IDs for CI
gates.

- Enforcement: open the claims registry before changing gate thresholds
- Violation: STOP, register or update the claim in the owning YAML first

**CONSTRAINT:** MUST NOT commit secrets, API keys, or private host paths into
this public repository.

- Enforcement: scan staged files for credentials and private absolute paths
- Violation: STOP, remove secrets; keep docs portable

## Workflow

1. Read [reference.md](reference.md) for targets and paths.
2. Run `make install` when dependencies are missing.
3. Run `make eval-fast` for a smoke pass.
4. Run the heavier target only when required.
5. Check `git status --short` for generated output before handoff.
