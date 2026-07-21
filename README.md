
# DSS: Infinite, Deterministic AI Memory

**A deterministic memory substrate that bypasses the context window — and abstains rather than guesses when structural verification fails.**

[![GitHub](https://img.shields.io/badge/GitHub-berigny%2Fdss--system-181717?logo=github)](https://github.com/berigny/dss-system)
[![License](https://img.shields.io/github/license/berigny/dss-system)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue?logo=python&logoColor=white)]()
[![Tests](https://github.com/berigny/dss-system/actions/workflows/ci.yml/badge.svg)](https://github.com/berigny/dss-system/actions)
[![Fly.io](https://img.shields.io/badge/deployed-Fly.io-7B3FE4?logo=flydotio&logoColor=white)]()
[![Vercel](https://img.shields.io/badge/deployed-Vercel-000000?logo=vercel&logoColor=white)]()
[![Whitepaper](https://img.shields.io/badge/Whitepaper-ResearchGate-00CCBB?logo=researchgate&logoColor=white)](https://www.researchgate.net/publication/408995995_Beyond_the_Context_Window_A_Ledger-Oriented_Architecture_for_Provable_AI_Memory_Dual-Substrate_System)

## The Why: The Problem with Probabilistic Memory

Large Language Models are probabilistic engines, and standard Retrieval-Augmented Generation (RAG) relies on approximate vector similarity. This creates a critical infrastructure gap for complex, agentic workflows (like healthcare or financial decision flows):

* **Context Amnesia:** As conversation length grows, earlier turns are truncated or compressed, causing a total loss of factual commitments established at the start of a session.

* **Semantic Drift:** Vector similarity retrieves what is *lexically near*, not what is *structurally valid*. It cannot distinguish between a factual ledger record and a plausible-sounding distractor.

* **The Result:** AI amnesia, hallucinations, and broken workflows.

*DSS Control Plane:*

![DSS Control Plane](https://github.com/user-attachments/assets/38ff8214-faa0-476e-bc70-84154d577331)


You don't need a better vector database; you need a reliable memory lane. **DSS maintains a continuous, persistent context** across sessions and models — and when a candidate memory fails structural verification, the system **abstains instead of returning a plausible near-miss**.

---

## The What: The Dual-Substrate System (DSS)

DSS is an open-source, ledger-oriented framework built to fix how AI memory feels, functions, and is governed. It separates probabilistic model inference from durable system memory. Instead of relying solely on vector search, DSS introduces **vectorless Qp-Native Retrieval**.

**Four guarantees, each from a mechanism — not a model promise:**

* **Recalls exactly.** Verification precedes recall: a memory that fails structural checks is refused, so precision is a gate, not a score.

* **Cannot forget.** The ledger is append-only and hash-chained — nothing committed is ever truncated, compressed, or degraded. Turn 1 and turn 10,000 are equally retrievable.

* **Never bluffs.** When no verified memory exists, the system says so. Abstention is a first-class outcome, not a failure — no plausible near-miss is ever substituted.

* **Becomes yours.** The model is fungible — swap providers without losing a beat. The ledger is not: over time it becomes a singular, one-of-one record of *your* commitments, corrections, and context that no model can regenerate. Shared geometry, singular history.

**For organizations, that singularity is brand intelligence.** Not the static brand guidelines — the *practiced* brand: approved outputs, corrections and their reasons, promises made to customers, positions taken and refined. Encoded as verified memory, recalled exactly, never lost to turnover or vendor exits, and **enforced at the gate**: off-brand output fails admissibility the same way any violated constraint does — non-compensatory, no offsetting.

**Multimodal by contract.** Per-modality kernels — text, visual, audio — each reduce to one verification contract: semantic checks through derived meaning, modality-native checks through measurable signal (palette, typography, geometry, loudness, voice profile). One ledger for every modality the brand speaks in, under the same verify-or-refuse discipline.

* **Deterministic Resolution:** Memory is resolved through prime-factorized metric addresses with deterministic geometric embeddings.

* **Structural Invariance:** If a retrieved memory does not logically align with your established constraints (enforced via a dual-circuit DAG), it is rejected. There is no "partial credit" for near misses.

* **Deterministic recall on synthetic micro-corpora:** In the current benchmark harness, DSS achieves Recall@1 of 1.00 at 517K tokens, reported as a distribution across a pinned seed set (see `eval/reports/`). Two scope limits apply: the corpora are **synthetic micro-corpora**, and the harness measures deterministic structural filtering given coordinates — not evidence discovery from raw text. Label-blind ingestion is the active milestone ([spec](eval/label_blind_ingestion_spec.md), [issue #1](https://github.com/Berigny/dss-system/issues/1)). Full caveat: whitepaper §12.

**Maturity:** P1 (ledger integrity) and P4 (identity governance) are High/Stable and defensible for immediate engineering use. P2/P3 (coordinate-based coherence overlays) are at Prototype maturity. We do not yet claim generalisability to unstructured enterprise datasets without further pipeline hardening (whitepaper §11.3).

> **Request a DSS Demo** Durable memory, exact provenance, and governed recall across models, workflows, and time: **[Dual Substrate System](https://dualsubstrate.com)**.

---

## The How: Getting the Value

To integrate deterministic memory into your application, DSS operates as a middleware control plane independent of your underlying LLM.

### 1. Deploy the Dual-Lane Architecture

DSS splits your application into two lanes:

* **Continuous Inference Lane:** Your high-throughput, probabilistic LLM orchestrator.

* **Discrete Ledger Lane:** The DSS append-only, RocksDB-backed hash-chain where verified memories are durably stored.

### 2. Route Through the DSS Orchestrator

Instead of querying an approximate vector database, route your prompts through the DSS Middleware.

* The orchestrator derives a `QpCoordinate` by mapping query tokens to assigned metric primes.

* DSS locates compatible records using an $O(k \log n)$ composite prime lookup, validating structural constraints before any data reaches the LLM.

### 3. Let the Infrastructure Work

Once integrated, the system "just works."

* **Swap Models Seamlessly:** Switch between different AI models (via the OpenRouter gateway) without losing your ongoing context.

* **Trace Every Leap:** Deep memory lineage allows you to trace the exact structural context pulled into a response, providing a verifiable existence proof that cosine similarity cannot offer.

---

## Current Benchmarks

All figures derive from **synthetic micro-corpora**, reported as distributions across pinned seeds, with versioned artifacts (seed, commit, config, CI95) in `eval/reports/benchmarks/`.

| Benchmark | DSS | Comparators (pinned) | Reading |
|---|---|---|---|
| Needle, adversarial corpus | **1.00** recall@1 | real MiniLM (all-MiniLM-L6-v2): 0.171@1, 0.40@k · metadata filter: 1.00 | On corpora built to defeat lexical retrieval, structural filtering holds where embeddings fail. A plain metadata filter ties DSS here — on these corpora the geometry's edge over filtering is not yet differentiated (v0.5 adds compatible-but-wrong distractors). |
| Multi-hop synthetic | **1.00** full-chain@5 | real MiniLM: 1.00 | Parity. No differentiation claimed on this corpus. |
| Counterfactual shuffles | needle texts-shuffled: 1.00 · needle coords-shuffled: 0.00 | — | Confirms current needle retrieval is coordinate-driven, exactly as [issue #1](https://github.com/Berigny/dss-system/issues/1) diagnosed. Label-blind ingestion is the fix in flight. |

The v0.5 suite adds label-blind ingestion, real-data HotpotQA/NarrativeQA evaluation, deterministic citation checks, and an LLM surface policy; see `eval/v0.5-milestone.md`. The suite also publishes its own known failures and registry mismatches in `eval/known_failures.json` (e.g., DSS-297 citation gate currently failing on the synthetic sample; DSS-299 real-data claims require a non-dry-run 5-seed execution).

The KSR kernel additionally ships a 16-gate self-validation suite (`tools/ksr_validate.py`): **16/16 PASS** on `ksr-core 1.3.1`; adversarial trap adjudication precision/recall 1.0; non-compensatory governance gates fail closed; invariant check-digit detects 98–100% of corruptions (6% without it); live model retention smoke **0.980** on `ksr-core` alone. Full evidence chain in `eval/`.

---

**Here's a polished, professional section** you can add to (or replace parts of) your README.md. It fits naturally after the "Current Benchmarks" and "Evaluation & Limitations" sections:

---

## Claims & Evidence Registry

Every substantive claim in this README and the associated whitepaper is tracked in a public **claims registry**. This includes the exact quote, producing harness, measurement method, current status, evidence artifacts, and explicit caveats.

This registry enforces transparency and reproducibility:

- CI fails on untracked or overclaimed assertions.
- Statuses are updated with each benchmark run.
- All evidence lives in versioned artifacts under `eval/reports/`.

**[View the full Claims Registry](claims-registry.yaml)** 

**Current Claim Health Summary** (as of 2026-07-20):
- **Supported**: 20 claims
- **Partial**: 2 claims (including real-data Phase R track)
- **Pending**: 1 claim (seamless model switching — manual demos strong, automated test pending)
- **Failing**: 1 claim (citation integrity on current synthetic sample)

This approach reflects our commitment to verifiable progress rather than polished marketing. Synthetic micro-corpus results are strong where claimed, but real-world unstructured data performance remains an active focus (see DSS-298/299).

For full methodology, limitations, and reproduction instructions, see:
- `eval/v0.5-milestone.md`
- `eval/DSS-EVAL-v0.5-llm-surface-policy.md`
- Individual benchmark reports in `eval/reports/`

---

## Evaluation & Limitations

* The **v0.5 benchmark suite** (DSS-292 through DSS-299) runs in a fresh container with `make eval` and reproduces published artifacts. It includes known-unknown abstention, adversarial-poisoning checks, BM25/dense/HNSW/long-context baselines, latency/storage tables, deterministic citation faithfulness, label-blind ingestion, and a Phase R real-data track.
* **Label-blind ingestion** derives document and query coordinates independently and reaches coverage >= 0.8 on synthetic corpora (DSS-298); the Phase R real-data track evaluates HotpotQA and NarrativeQA validation splits with the same baselines, gated by the DSS-298 coverage gate and bounded by a token/embedding budget cap (DSS-299). See `eval/v0.5-milestone.md` for the pinned milestone.
* Retrieval comparators are **matched-information baselines** (BM25, real MiniLM, HNSW, long-context stand-in) that operate on the same documents and queries as DSS. They are reproducible by design, but live vector-database runs may differ (whitepaper §12, Threats to Validity).
* All v0.5 LLM-facing evals **default to the Kimi Code delegated agent** with OpenRouter as an explicit fallback and budget tracking; see `eval/DSS-EVAL-v0.5-llm-surface-policy.md`.
* Every benchmark run emits a versioned artifact and a flat KSR-EVAL v0.4 manifest — seed, commit, dataset, configuration, and metrics with confidence intervals (whitepaper Appendix A). Artifacts and manifests live in `eval/reports/`.
* The KSR kernel ships with a 16-gate structural self-validation suite (`tools/ksr_validate.py`): current status **16/16 PASS** on `ksr-core 1.3.1`, with adversarial trap adjudication at precision/recall 1.0 and fail-closed non-compensatory governance gates. Concept-level decode: **0.96 node recall** (900/900 completed trials, transport-clean protocol, kimi-k3). See `eval/` for the full KSR-EVAL evidence chain.
* The original issue #1 critique — that benchmarks encoded relevance in router-visible coordinates — is addressed by the v0.5 suite; a draft issue comment crediting hugooconnor is maintained in the ds-review outreach package.

---

**[Explore the Setup Guide & Sandbox](/DSS-Basic-Setup-Guide.md)** | **[Read the Whitepaper](https://www.researchgate.net/publication/408995995_Beyond_the_Context_Window_A_Ledger-Oriented_Architecture_for_Provable_AI_Memory_Dual-Substrate_System)**.

---

## License

DSS is available under a custom non-commercial license — see [LICENSE](LICENSE). It is free for research and non-commercial use.

For commercial licensing inquiries, please contact the maintainer via [Google Forms](https://forms.gle/hV4ejmk3i5J411am9).
