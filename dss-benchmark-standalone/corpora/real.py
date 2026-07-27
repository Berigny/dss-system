"""Placeholder loader for real validation corpora.

HotpotQA and NarrativeQA splits are intentionally *not* bundled to avoid large
downloads. This module provides a small pinned stub and a loader function that
reads local JSON/CSV files when the user places them in ``corpora/real_data/``.
"""
import json
import os
from pathlib import Path
from typing import Dict, List, Optional


REAL_DATA_DIR = Path(__file__).resolve().parent / "real_data"


def _stub_real_corpus(name: str, seed: int = 42) -> Dict:
    """Return a tiny deterministic placeholder corpus."""
    return {
        "name": name,
        "documents": [
            {
                "id": f"{name}-stub-001",
                "text": f"This is a placeholder document for {name}.",
                "metadata": {"source": "stub", "seed": seed},
            }
        ],
        "seed": seed,
        "stub": True,
    }


def load_hotpot_qa(split: str = "validation", max_items: Optional[int] = None) -> Dict:
    """Load HotpotQA validation split if present, else return a stub."""
    path = REAL_DATA_DIR / f"hotpot_{split}_v1.json"
    if not path.exists():
        return _stub_real_corpus("hotpotqa")

    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    documents: List[Dict] = []
    for item in (data if max_items is None else data[:max_items]):
        context = "\n".join(
            f"{title}: {' '.join(sents)}" for title, sents in item.get("context", [])
        )
        documents.append({
            "id": item.get("_id", f"hotpot-{len(documents):06d}"),
            "text": context,
            "metadata": {
                "question": item.get("question", ""),
                "answer": item.get("answer", ""),
                "type": item.get("type", "unknown"),
                "source": "hotpotqa",
            },
        })
    return {"name": "hotpotqa", "documents": documents, "split": split}


def load_narrative_qa(split: str = "valid", max_items: Optional[int] = None) -> Dict:
    """Load NarrativeQA validation split if present, else return a stub."""
    path = REAL_DATA_DIR / f"narrativeqa_{split}.json"
    if not path.exists():
        return _stub_real_corpus("narrativeqa")

    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    documents: List[Dict] = []
    for item in (data if max_items is None else data[:max_items]):
        documents.append({
            "id": item.get("document_id", f"narrative-{len(documents):06d}"),
            "text": item.get("text", ""),
            "metadata": {
                "question": item.get("question", ""),
                "answer": item.get("answer", ""),
                "source": "narrativeqa",
            },
        })
    return {"name": "narrativeqa", "documents": documents, "split": split}
