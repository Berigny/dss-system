# Dual-Layer Non-Commutative Ledger

This package implements the process layer described in
`backlog_reqs/paper/cumul.md` and `refactor_analysis.md` as an overlay on the
`ds-backend-local` kernel. The Python package is `dss_ledger`; the on-disk
directory mirrors the proposal's `kimi-ledger/` layout.

## Config

Config files under `config/` are generated from the kernel by
`scripts/generate_ledger_config.py`. Do not edit them by hand; re-run the
script after kernel changes.

- `ontology.json` — process concepts derived from kernel value nodes and
  engineering dimensions. Process primes are disjoint from quaternary and slot
  primes.
- `slots.json` — fixed positional bases for agent/verb/patient/result/context.
- `relations.json` — hard-gate relation markers derived from kernel patches.
- `weights.json` — decay/activation rules derived from value-node balance.

## Runtime

- `ledger/causal_graph.json` — mutable, append-only graph of validated PIDs.
- `ledger/history.log` — append-only human-readable log.

## Usage

```bash
python -m dss_ledger.cli parse --text "autonomy enables mastery"
python -m dss_ledger.cli query --text "autonomy enables mastery"
python -m dss_ledger.cli append -s '{"agent":"autonomy","verb":"enables","patient":"mastery"}'
python -m dss_ledger.cli validate --pid <pid>
```
