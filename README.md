# DSS-EVAL Benchmark Suite

**Public, reproducible benchmark harness for RAG poisoning resistance, retrieval integrity, and abstention capacity.**

This repository is the public home of DSS-EVAL. It contains only the benchmark harness, evaluation corpora, claims registry, and reproducibility artifacts — not the private Dual-Substrate System application code.

> For the private application runtime, surfaces, control plane, and middleware, see [`dss-codebase`](https://github.com/berigny/dss-codebase) (private).

---

## What DSS-EVAL Measures

DSS-EVAL tests three core capabilities of memory-augmented language models:

1. **RAG Poisoning Resistance** — Can adversarial entries silently displace committed facts?
2. **Retrieval Integrity** — Does retrieval return structurally valid records rather than semantically adjacent distractors?
3. **Abstention Capacity** — Does the system decline to answer when it cannot verify structural alignment?

The harness is vendor-agnostic. Any retrieval system can be plugged in by implementing the thin `RetrievalAdapter` interface in `dss-benchmark-standalone/adapters/base.py`.

---

## Quick Start

```bash
# Full deterministic benchmark run
make eval

# Smoke pass with mocked embeddings, completes in < 60 seconds
make eval-fast
```

Reports are written to `dss-benchmark-standalone/eval/reports/` as JSON and Markdown artifacts.

---

## Repository Layout

- `dss-benchmark-standalone/` — standalone benchmark package
  - `adapters/` — vendor-agnostic adapter interface and reference implementations
  - `suites/` — poisoning, integrity, and abstention suites
  - `corpora/` — synthetic adversarial distractors and real QA splits
  - `harness/` — deterministic runner, claims registry, and reporter
  - `eval/claims_registry.yaml` — machine-readable claim registry
- `eval/` — legacy DSS-EVAL reports and milestone documents (retained for history)
- `LICENSE` — custom non-commercial license

---

## Claims Registry

Every public claim is bound to a metric and threshold in `dss-benchmark-standalone/eval/claims_registry.yaml`. CI enforces `unknown_claims_policy: fail_ci`, so any new claim must be registered before merge.

---

## Limitations

These benchmarks test structural integrity and abstention behavior, not general retrieval quality on unstructured corpora. See `dss-benchmark-standalone/README.md` for the full limitation disclosure and adapter authoring guide.

---

## License

DSS-EVAL is available under a custom non-commercial license — see [LICENSE](LICENSE). It is free for research and non-commercial use.
