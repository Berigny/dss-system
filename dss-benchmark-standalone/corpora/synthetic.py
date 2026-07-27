"""Deterministic generators for adversarial distractors and needle corpora."""
import random
from typing import Dict, List, Tuple


def _rng(seed: int) -> random.Random:
    return random.Random(seed)


def make_needle_corpus(seed: int = 42, num_documents: int = 100, needle_every: int = 20) -> Dict:
    """Create a corpus containing regularly spaced needle facts."""
    rng = _rng(seed)
    documents: List[Dict] = []
    for i in range(num_documents):
        if i % needle_every == 0:
            text = (
                f"Confirmed fact {i}: the system registry reports version "
                f"{rng.choice(['alpha', 'beta', 'gamma'])}-{rng.randint(1000, 9999)}."
            )
            label = "needle"
        else:
            words = ["the", "quick", "brown", "fox", "lazy", "dog", "jumps", "over"]
            text = " ".join(rng.choice(words) for _ in range(rng.randint(5, 15)))
            label = "distractor"
        documents.append({
            "id": f"doc-{i:04d}",
            "text": text,
            "metadata": {"label": label, "index": i, "seed": seed},
        })
    return {"documents": documents, "seed": seed}


def make_adversarial_distractors(seed: int = 42, num_distractors: int = 20) -> Dict:
    """Create distractor documents that are semantically close but structurally invalid."""
    rng = _rng(seed)
    templates = [
        "The registry reports version {word}-{num} but the checksum is missing.",
        "Version {word}-{num} appears in the log with no provenance record.",
        "A user claimed version {word}-{num} yet the signature field is empty.",
        "The document mentions {word}-{num} without a corresponding claim id.",
    ]
    documents: List[Dict] = []
    for i in range(num_distractors):
        text = rng.choice(templates).format(
            word=rng.choice(["alpha", "beta", "gamma"]),
            num=rng.randint(1000, 9999),
        )
        documents.append({
            "id": f"distractor-{i:03d}",
            "text": text,
            "metadata": {"label": "incoherent", "index": i, "seed": seed},
        })
    return {"documents": documents, "seed": seed}


def make_poison_corpus(seed: int = 42) -> Dict:
    """Create a corpus with pre-injected conflict pairs for the poisoning suite.

    Returns a corpus plus a manifest describing the injected conflicts.
    """
    rng = _rng(seed)
    documents: List[Dict] = [
        {"id": "base-001", "text": "System owner is Alice.", "metadata": {"tag": "owner", "seed": seed}},
        {"id": "base-002", "text": "Policy version is 1.0.", "metadata": {"tag": "policy", "seed": seed}},
        {"id": "base-003", "text": "Retention period is 30 days.", "metadata": {"tag": "retention", "seed": seed}},
    ]

    conflicts: List[Dict] = []

    # Compatible-coordinate conflict: same structural tag, conflicting payload.
    compat_id = f"compat-{rng.randint(1000, 9999)}"
    documents.append({
        "id": compat_id,
        "text": "System owner is Alice.",
        "metadata": {"tag": "owner", "seed": seed, "conflict": "compatible"},
    })
    documents.append({
        "id": f"{compat_id}-conflict",
        "text": "System owner is Bob.",
        "metadata": {"tag": "owner", "seed": seed, "conflict": "compatible"},
    })
    conflicts.append({"type": "compatible", "tag": "owner", "ids": [compat_id, f"{compat_id}-conflict"]})

    # Incompatible-coordinate conflict: different structural tag, same identifier.
    shared_id = f"shared-{rng.randint(1000, 9999)}"
    documents.append({
        "id": shared_id,
        "text": "Policy version is 1.0.",
        "metadata": {"tag": "policy", "seed": seed, "conflict": "incompatible"},
    })
    documents.append({
        "id": shared_id,
        "text": "Retention period is 90 days.",
        "metadata": {"tag": "retention", "seed": seed, "conflict": "incompatible"},
    })
    conflicts.append({"type": "incompatible", "id": shared_id, "tags": ["policy", "retention"]})

    # Same-identifier overwrite.
    overwrite_id = f"overwrite-{rng.randint(1000, 9999)}"
    documents.append({
        "id": overwrite_id,
        "text": "Original value: 100.",
        "metadata": {"tag": "value", "seed": seed, "conflict": "overwrite", "version": 1},
    })
    documents.append({
        "id": overwrite_id,
        "text": "Overwritten value: 999.",
        "metadata": {"tag": "value", "seed": seed, "conflict": "overwrite", "version": 2},
    })
    conflicts.append({"type": "overwrite", "id": overwrite_id})

    return {"documents": documents, "conflicts": conflicts, "seed": seed}


def make_abstention_corpus(seed: int = 42) -> Tuple[Dict, Dict, Dict, Dict]:
    """Create corpora and query sets for absent, borderline, and present queries."""
    rng = _rng(seed)
    present = [
        {"id": "pres-001", "text": "The capital of France is Paris.", "metadata": {"label": "present"}},
        {"id": "pres-002", "text": "Water boils at 100 degrees Celsius at sea level.", "metadata": {"label": "present"}},
        {"id": "pres-003", "text": "The speed of light in vacuum is approximately 299792458 m/s.", "metadata": {"label": "present"}},
    ]
    absent = [
        {"id": "abs-001", "text": "The capital of Atlantis is Poseidonis.", "metadata": {"label": "absent"}},
        {"id": "abs-002", "text": "Unicorns have two horns in adult specimens.", "metadata": {"label": "absent"}},
        {"id": "abs-003", "text": "The DSS-4301 benchmark was released in 1985.", "metadata": {"label": "absent"}},
    ]
    borderline = [
        {"id": "bord-001", "text": "Paris is a city in France with a famous tower.", "metadata": {"label": "borderline"}},
        {"id": "bord-002", "text": "Water can boil at temperatures other than 100 Celsius.", "metadata": {"label": "borderline"}},
    ]

    queries = {
        "present": [
            ("What is the capital of France?", "pres-001"),
            ("At what temperature does water boil at sea level?", "pres-002"),
            ("What is the speed of light in vacuum?", "pres-003"),
        ],
        "absent": [
            "What language do penguins speak?",
            "How many horns do adult unicorns have?",
            "When was DSS-4301 released?",
        ],
        "borderline": [
            "Which city in France has a famous tower?",
            "Can water boil at temperatures other than 100 Celsius?",
        ],
    }

    # Shuffle deterministically for variety while keeping assertions stable.
    rng.shuffle(present)
    rng.shuffle(absent)
    rng.shuffle(borderline)

    # Absent facts are intentionally excluded from the corpus; the suite uses
    # them as queries that a correct system should decline to answer.
    all_documents = present + borderline
    return {"documents": all_documents, "seed": seed}, queries
