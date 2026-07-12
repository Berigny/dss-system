# DSS: Non-Vector Retrieval with 100% Recall

**A retrieval engine based on structural constraints, not similarity search.**

**Supported By:**

[![GitHub](https://img.shields.io/badge/GitHub-berigny%2Fdss--system-181717?logo=github)](https://github.com/berigny/dss-system)
[![License](https://img.shields.io/github/license/berigny/dss-system)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue?logo=python&logoColor=white)]()
[![Tests](https://github.com/berigny/dss-system/actions/workflows/ci.yml/badge.svg)](https://github.com/berigny/dss-system/actions)
[![Fly.io](https://img.shields.io/badge/deployed-Fly.io-7B3FE4?logo=flydotio&logoColor=white)]()
[![Vercel](https://img.shields.io/badge/deployed-Vercel-000000?logo=vercel&logoColor=white)]()
[![Whitepaper](https://img.shields.io/badge/Whitepaper-ResearchGate-00CCBB?logo=researchgate&logoColor=white)](https://www.researchgate.net/publication/408995995_Beyond_the_Context_Window_A_Ledger-Oriented_Architecture_for_Provable_AI_Memory_Dual-Substrate_System)



DSS (Dual-Substrate System) is the first practical implementation of constraint-based retrieval. It solves the problem that vector search has struggled with for years, namely that similarity is not the same as logical coherence.

## The Problem with Vector Search

Vector search finds things that are near each other. This works for many tasks, but it breaks when you need to follow a chain of reasoning or find a specific fact buried in noise. Retrieval-Augmented Generation (RAG) systems that rely on vector search frequently hallucinate, lose context, and fail on multi-step queries.

## How DSS Works Differently

DSS does not rank by similarity. It filters by structural invariants. In practical terms, it enforces the logical rules that the data must obey, and only then retrieves the most relevant results. This is like checking that a sentence is grammatical before you translate it, rather than simply matching words from a dictionary.

## Benchmark Results

We tested DSS against standard vector similarity search on two difficult retrieval tasks.

| Task | DSS | Vector Search |
|------|-----|---------------|
| Multi-hop reasoning | 100% | 0% |
| Needle-in-haystack | 100% | 14% |

Both results are statistically significant (p = 0.003 and p = 0.033 respectively). DSS does not simply perform better; it performs a different kind of retrieval altogether.

## Quick Start

The easiest way to try DSS is with the pre-built demo container. This runs a small test dataset and shows the retrieval process step by step.

```bash
docker run -p 8000:8000 berigny/dss-demo
```

Then open your browser at `http://localhost:8000`. The demo includes the benchmarks above so you can verify the results yourself.

For a full installation, please see the [Installation Guide](link).

## Why This Matters

- **No hallucinations from false similarity**. DSS only returns candidates that satisfy the structural rules of the data.
- **Logical chains stay intact**. Multi-step reasoning does not break down.
- **Adversarial noise has little effect**. The constraints filter out noise before ranking begins.

## Project Status

DSS is under active development. The core engine is stable and has been tested on standard benchmarks. We are now working on integration with popular LLM frameworks.

## Roadmap

- Q3 2026: LLM integration (LangChain, LlamaIndex)
- Q4 2026: Multi-modal support (text + structured data)
- Q1 2027: Distributed retrieval cluster

## License

DSS is available under the AGPLv3 licence with a Commons Clause. This means it is free for research and non-commercial use. For commercial licensing, please contact the maintainer.

## Contributing

Contributions are welcome. Please read the [Contributing Guide](link) before opening a pull request. We particularly need help with:

- Additional benchmarks
- API documentation
- Example notebooks
- Community moderation

## Community

Coming soon

## Citation

If you use DSS in your research, please cite the original paper:

```
Berigny, D. (2025). A Dual-Substrate Field Model: A Computational Exploration of Emergent Structure from a Null Axiom. [link]
```
---

## License

Apache 2.0 — see [LICENSE](LICENSE).
