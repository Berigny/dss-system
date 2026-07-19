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

You don't need a better vector database; you need a reliable memory lane. **DSS maintains a continuous, persistent context** across sessions and models — and when a candidate memory fails structural verification, the system **abstains instead of returning a plausible near-miss**.

---

## The What: The Dual-Substrate System (DSS)

DSS is an open-source, ledger-oriented framework built to fix how AI memory feels, functions, and is governed. It separates probabilistic model inference from durable system memory.

Instead of relying solely on vector search, DSS introduces **vectorless Qp-Native Retrieval**.

* **Deterministic Resolution:** Memory is resolved through prime-factorized metric addresses with deterministic geometric embeddings.

* **Structural Invariance:** If a retrieved memory does not logically align with your established constraints (enforced via a dual-circuit DAG), it is rejected. There is no "partial credit" for near misses — the system returns nothing rather than guessing.

* **Deterministic recall on synthetic micro-corpora:** In the current benchmark harness, DSS achieves Recall@1 of 1.00 at 517K tokens, reported as a distribution across a pinned seed set (see `eval/reports/`). Two scope limits apply: the corpora are **synthetic micro-corpora**, and the comparators are **deterministic stand-ins** that hold documented vector failure modes constant — not live FAISS/Milvus runs. The harness measures deterministic structural filtering given coordinates; label-blind ingestion and live embedding baselines are tracked future work ([issue #1](https://github.com/Berigny/dss-system/issues/1)). Full caveat: whitepaper §12.

**Maturity:** P1 (ledger integrity) and P4 (identity governance) are High/Stable and defensible for immediate engineering use. P2/P3 (coordinate-based coherence overlays) are at Prototype maturity. We do not yet claim generalisability to unstructured enterprise datasets without further pipeline hardening (whitepaper §11.3).

While the immediate value is a continuous, unbroken conversational state, the architectural byproducts are enterprise-grade: native auditability, immutable hash-chained provenance, and strict non-human identity governance.

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

## Evaluation & Limitations

* All benchmark figures derive from **synthetic micro-corpora**; the hotpotqa, musique, and 2wiki datasets are present in the repo as unintegrated targets for future work.
* Retrieval comparators are **deterministic stand-ins** that encode documented failure distributions of dense-retrieval systems — reproducible by design, but not live vector-database runs (whitepaper §12, Threats to Validity).
* Current figures measure **structural filtering given coordinates**, not evidence discovery from raw text. A label-blind ingestion path is specified in `eval/label_blind_ingestion_spec.md` and targeted for v0.5 / Hugo PR; matched-information baselines and counterfactual shuffle tests are implemented in `tools/counterfactual_harness.py` and tracked under [issue #1](https://github.com/Berigny/dss-system/issues/1).
* Every benchmark run emits a versioned artifact and a flat KSR-EVAL v0.4 manifest — seed, commit, dataset, configuration, and metrics with confidence intervals (whitepaper Appendix A). Artifacts and manifests live in `eval/reports/`.
* The KSR kernel ships with a 16-gate structural self-validation suite (`tools/ksr_validate.py`): current status **16/16 PASS** on `ksr-core 1.3.1`, with adversarial trap adjudication at precision/recall 1.0 and fail-closed non-compensatory governance gates. Concept-level decode: **0.96 node recall** (900/900 completed trials, transport-clean protocol, kimi-k3). See `eval/` for the full KSR-EVAL evidence chain.

---

**[Explore the Setup Guide & Sandbox](/DSS-Basic-Setup-Guide.md)** | **[Read the Whitepaper](https://www.researchgate.net/publication/408995995_Beyond_the_Context_Window_A_Ledger-Oriented_Architecture_for_Provable_AI_Memory_Dual-Substrate_System)**.

---

## License

DSS is available under a custom non-commercial license — see [LICENSE](LICENSE). It is free for research and non-commercial use.

For commercial licensing inquiries, please contact the maintainer via [Google Forms](https://forms.gle/hV4ejmk3i5J411am9).
