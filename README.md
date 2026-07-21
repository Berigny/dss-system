# DSS: Infinite, Deterministic AI Memory

**A deterministic memory substrate that bypasses the context window — and abstains rather than guesses when structural verification fails.**

[![GitHub](https://img.shields.io/badge/GitHub-berigny%2Fdss--system-181717?logo=github)](https://github.com/berigny/dss-system)
[![License](https://img.shields.io/github/license/berigny/dss-system)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue?logo=python&logoColor=white)]()
[![Tests](https://github.com/berigny/dss-system/actions/workflows/ci.yml/badge.svg)](https://github.com/berigny/dss-system/actions)
[![Fly.io](https://img.shields.io/badge/deployed-Fly.io-7B3FE4?logo=flydotio&logoColor=white)]()
[![Vercel](https://img.shields.io/badge/deployed-Vercel-000000?logo=vercel&logoColor=white)]()
[![Whitepaper](https://img.shields.io/badge/Whitepaper-ResearchGate-00CCBB?logo=researchgate&logoColor=white)](https://www.researchgate.net/publication/408995995_Beyond_the_Context_Window_A_Ledger-Oriented_Architecture_for_Provable_AI_Memory_Dual-Substrate_System)

> DSS v0.5: structural filtering beats embeddings 1.00 to 0.17 on adversarial retrieval, validated on HotpotQA and NarrativeQA with 5-seed reproducibility.

## The Why: The Problem with Probabilistic Memory

Large Language Models are probabilistic engines, and standard Retrieval-Augmented Generation (RAG) relies on approximate vector similarity. This creates a critical infrastructure gap for complex, agentic workflows (like healthcare or financial decision flows):

* **Context Amnesia:** As conversation length grows, earlier turns are truncated or compressed, causing a total loss of factual commitments established at the start of a session.

* **Semantic Drift:** Vector similarity retrieves what is *lexically near*, not what is *structurally valid*. It cannot distinguish between a factual ledger record and a plausible-sounding distractor.

* **The Result:** AI amnesia, hallucinations, and broken workflows.

**The house is not a bigger hat.** You don't need a better vector database; you need a different foundation. DSS maintains a continuous, persistent context across sessions and models — and when a candidate memory fails structural verification, the system **abstains instead of returning a plausible near-miss**.

---

## Current Benchmarks

**v0.5: 7/7 suites passed. Reproducible from commit `840fcaa2`, 8 versioned artifacts in `eval/reports/benchmarks/`.**

| Benchmark | DSS | Main Comparators | Notes |
|---|---|---|---|
| Adversarial Needle | **1.00** Recall@1 | all-MiniLM-L6-v2: 0.171; metadata filter: 1.00 | Strongest win. Corpus built to defeat vector search. |
| Multi-hop Synthetic | **1.00** full-chain@5 | all-MiniLM-L6-v2: 1.00 | Parity — no differentiation claimed. |
| Counterfactual Shuffles | **1.00** (texts) / **0.00** (coords) | — | Proves retrieval is coordinate-driven, not text-memorisation. |
| Scale Stress (1k/10k/100k events) | **PASS** | HNSW, BM25, dense (MiniLM) | Holds at scale with real embeddings. |
| Real QA (HotpotQA + NarrativeQA) | **PASS** | HNSW, BM25, dense, long-context | Same datasets, same samples. Structural filtering validated on real multi-hop and narrative questions. |

**Claims registry:** C20 (scale stress) and C24 (real-data QA) **resolved**. C22 (label-blind ingestion) **failing** — documented, in flight. Citation faithfulness (DSS-297) scores **0.7778** on synthetic stress tests; targeted for v0.6 via provenance-tagging gates in the Law/Grace framework.

**KSR kernel self-validation:** 16/16 gates PASS on `ksr-core 1.3.1`; adversarial trap precision/recall 1.0; fail-closed governance; check-digit detects 98–100% of corruptions. Full evidence in `eval/`.

---


*DSS Control Plane:*

![DSS Control Plane](https://github.com/user-attachments/assets/38ff8214-faa0-476e-bc70-84154d577331)

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

**Maturity:** P1 (ledger integrity) and P4 (identity governance) are High/Stable and defensible for immediate engineering use. P2/P3 (coordinate-based coherence overlays) are at Prototype maturity. v0.5 validates real-data retrieval on HotpotQA and NarrativeQA with 5-seed reproducibility; v0.6 targets citation-faithfulness hardening and label-blind ingestion generalisation (whitepaper §11.3).

&gt; **Request a DSS Demo** Durable memory, exact provenance, and governed recall across models, workflows, and time: **[Dual Substrate System](https://dualsubstrate.com)**.

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

## Evaluation & Limitations

* The **v0.5 benchmark suite** (DSS-292 through DSS-299) runs in a fresh container with `make eval` and reproduces published artifacts from commit `840fcaa2`. It includes known-unknown abstention, adversarial-poisoning checks, BM25/dense/HNSW/long-context baselines, latency/storage tables, deterministic citation faithfulness, label-blind ingestion, and a Phase R real-data track.
* **Label-blind ingestion** derives document and query coordinates independently and reaches coverage &gt;= 0.8 on synthetic corpora (DSS-298); the Phase R real-data track evaluates HotpotQA and NarrativeQA validation splits with the same baselines, gated by the DSS-298 coverage gate and bounded by a token/embedding budget cap (DSS-299). See `eval/v0.5-milestone.md` for the pinned milestone.
* Retrieval comparators are **matched-information baselines** (BM25, real MiniLM, HNSW, long-context stand-in) that operate on the same documents and queries as DSS. They are reproducible by design, but live vector-database runs may differ (whitepaper §12, Threats to Validity).
* All v0.5 LLM-facing evals **default to the Kimi Code delegated agent** with OpenRouter as an explicit fallback and budget tracking; see `eval/DSS-EVAL-v0.5-llm-surface-policy.md`.
* Every benchmark run emits a versioned artifact and a flat KSR-EVAL v0.4 manifest — seed, commit, dataset, configuration, and metrics with confidence intervals (whitepaper Appendix A). Artifacts and manifests live in `eval/reports/`.
* The KSR kernel ships with a 16-gate structural self-validation suite (`tools/ksr_validate.py`): current status **16/16 PASS** on `ksr-core 1.3.1`, with adversarial trap adjudication at precision/recall 1.0 and fail-closed non-compensatory governance gates. Concept-level decode: **0.96 node recall** (900/900 completed trials, transport-clean protocol, kimi-k3). See `eval/` for the full KSR-EVAL evidence chain.
* The original issue #1 critique — that benchmarks encoded relevance in router-visible coordinates — is addressed by the v0.5 suite; a draft issue comment crediting hugooconnor is maintained in the ds-review outreach package.
* The **v0.6 target:** Citation faithfulness (DSS-297) scores 0.7778 on a 9-case synthetic stress sample. The retrieval layer is proven; the generation surface requires provenance-tagging constraints to harden attribution. Target: Contraint/Adaptability framework gates that enforce copy-over-paraphrase from attributed sources.

---

**[Explore the Setup Guide & Sandbox](/DSS-Basic-Setup-Guide.md)** | **[Read the Whitepaper](https://www.researchgate.net/publication/408995995_Beyond_the_Context_Window_A_Ledger-Oriented_Architecture_for_Provable_AI_Memory_Dual-Substrate_System)**.

---

## License

DSS is available under a custom non-commercial license — see [LICENSE](LICENSE). It is free for research and non-commercial use.

For commercial licensing inquiries, please contact the maintainer via [Google Forms](https://forms.gle/hV4ejmk3i5J411am9).
