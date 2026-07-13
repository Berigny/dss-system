# DSS: Non-Vector Retrieval with 100% Recall

**A retrieval engine based on structural constraints, not similarity search. Supported By:**

[![GitHub](https://img.shields.io/badge/GitHub-berigny%2Fdss--system-181717?logo=github)](https://github.com/berigny/dss-system)
[![License](https://img.shields.io/github/license/berigny/dss-system)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue?logo=python&logoColor=white)]()
[![Tests](https://github.com/berigny/dss-system/actions/workflows/ci.yml/badge.svg)](https://github.com/berigny/dss-system/actions)
[![Fly.io](https://img.shields.io/badge/deployed-Fly.io-7B3FE4?logo=flydotio&logoColor=white)]()
[![Vercel](https://img.shields.io/badge/deployed-Vercel-000000?logo=vercel&logoColor=white)]()
[![Whitepaper](https://img.shields.io/badge/Whitepaper-ResearchGate-00CCBB?logo=researchgate&logoColor=white)](https://www.researchgate.net/publication/408995995_Beyond_the_Context_Window_A_Ledger-Oriented_Architecture_for_Provable_AI_Memory_Dual-Substrate_System)



* DSS (Dual-Substrate System) is the first practical implementation of constraint-based retrieval. It solves the problem that vector search has struggled with for years, namely that similarity is not the same as logical coherence.

## What is the problem?

Vector search finds things that are near each other. This works for many tasks, but it breaks when you need to follow a chain of reasoning or find a specific fact buried in noise. Retrieval-Augmented Generation (RAG) systems that rely on vector search frequently hallucinate, lose context, and fail on multi-step queries.

## How does DSS solve it?
DSS does not rank by similarity. It filters by structural invariants. In practical terms, it enforces the logical rules that the data must obey, and only then retrieves the most relevant results. This is like checking that a sentence is grammatical before you translate it, rather than simply matching words from a dictionary.

## What does it matter?

Instead of wrestling with context limits, black-box reasoning, and messy chat sidebars, DSS provides a fundamentally different user experience:

* **Threadless Coherence:** Stop hunting through a maze of old chat logs. The system remembers what you said, no matter when or where you said it.
* **Marathon Conversations:** Have ultra-long interactions without the AI getting confused, mixing up details, or experiencing "amnesia" just because you've been chatting for a while.
* **Multi-Model, Seamless Context:** Switch between different AI models on the fly without losing a single beat of your ongoing conversation.
* **Absolute Data Freedom & Secure Sharing:** Keep your conversation history and documents exactly where you want them—locally or in the cloud. You hold the cryptographic keys, meaning you can selectively share a single memory or your entire history with another person or model.
* **Deep Memory Lineage:** Never wonder where the AI got its reasoning. Trace the exact pieces of context the model pulled to form its answer, and look backward to see how those memories were created.

> **Read the Whitepaper:** For a complete deep dive into the mathematics, coordinate geometry, and distributed systems engineering behind DSS, read our open technical disclosure: **[Beyond the Context Window: A Ledger-Oriented Architecture for Provable AI Memory](https://www.researchgate.net/publication/408995995_Beyond_the_Context_Window_A_Ledger-Oriented_Architecture_for_Provable_AI_Memory_Dual-Substrate_System)**.


## What is the evidence?

We tested DSS against standard vector similarity search on two difficult retrieval tasks.

| Task | DSS | Vector Search |
|------|-----|---------------|
| Multi-hop reasoning | 100% | 0% |
| Needle-in-haystack | 100% | 14% |

Both results are statistically significant (p = 0.003 and p = 0.033 respectively). DSS does not simply perform better; it performs a different kind of retrieval altogether.

## Try it yourself..

The easiest way to try DSS is with the pre-built demo container. This runs a small test dataset and shows the retrieval process step by step.

```bash
docker run -p 8000:8000 berigny/dss-demo
```

Then open your browser at `http://localhost:8000`. The demo includes the benchmarks above so you can verify the results yourself.

For a more installation information, please see the [a more detailed README](README_long_form.md).


## Project Status

DSS is under active development. The core engine is stable and has been tested on standard benchmarks. We are now working on integration with popular LLM frameworks.

## Roadmap

- Q3 2026: LLM integration (LangChain, LlamaIndex)
- Q4 2026: Multi-modal support (text + structured data)
- Q1 2027: Distributed retrieval cluster

## License

DSS is available under the AGPLv3 licence with a Commons Clause. This means it is free for research and non-commercial use. For commercial licensing, please contact the maintainer.

## Contributing

Contributions are welcome. We particularly need help with:

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
