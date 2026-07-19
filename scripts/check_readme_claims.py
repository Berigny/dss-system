#!/usr/bin/env python3
"""Check that every public claim in README.md is registered in claims_registry.yaml.

Usage:
    python3 scripts/check_readme_claims.py [--readme PATH] [--registry PATH]

Exit code 0 if all extractable claims are registered, 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml


# Patterns that identify a measurable/assertion sentence in README.md.
# Descriptive sentences (e.g. "Vector similarity retrieves...") are intentionally
# excluded; only claims with numbers, benchmarks, or strong technical assertions
# must be registered.
CLAIM_PATTERNS = (
    r"Recall@\d+",
    r"full-chain@\d+",
    r"\b\d+(?:\.\d+)?%",
    r"O\([^)]+\)",
    r"\d+/\d+\s+(?:gates|pass|completed)",
    r"zero\s+(?:false|rejections|hits)",
    r"abstains?\s+rather\s+than\s+guesses",
    r"deterministic\s+(?:encode|decode|round-trip|recall)",
    r"structural\s+self-validation\s+gates",
    r"checksum\s+invariant",
    r"quaternary-gate",
    r"swap\s+models",
    r"node\s+recall",
)


def load_registry(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def registered_claim_texts(registry: dict) -> set[str]:
    """Return a normalized set of registered claim quotes and paraphrases."""
    texts: set[str] = set()
    for claim in registry.get("claims", []):
        for key in ("quote", "claim"):
            value = claim.get(key)
            if value:
                texts.add(_normalize(value))
    return texts


def _normalize(text: str) -> str:
    """Lowercase, collapse whitespace, and strip markdown formatting."""
    text = re.sub(r"[`\*\$]+", "", text)
    text = text.replace("\\", "")
    return re.sub(r"\s+", " ", text.strip().lower())


def _significant_words(text: str) -> set[str]:
    """Return normalized words longer than 2 chars, ignoring punctuation."""
    return {
        word.strip(".,;:!?()[]{}") for word in _normalize(text).split()
        if len(word.strip(".,;:!?()[]{}")) > 2
    }


def extract_claim_sentences(readme_text: str) -> list[str]:
    """Split README into sentences and return those that look like claims.

    Markdown tables are removed first because their rows contain many numbers
    and benchmarks in tabular form; their claims should be registered through
    the surrounding prose instead.
    """
    # Remove markdown tables (any block of lines starting with '|').
    cleaned_lines: list[str] = []
    in_table = False
    for line in readme_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            in_table = True
            continue
        if in_table and not stripped:
            in_table = False
            continue
        cleaned_lines.append(line)
    cleaned_text = "\n".join(cleaned_lines)

    sentences: list[str] = []
    for raw in re.split(r"(?<=[.!?])\s+", cleaned_text):
        sentence = raw.strip()
        if not sentence:
            continue
        if any(re.search(pat, sentence, re.IGNORECASE) for pat in CLAIM_PATTERNS):
            sentences.append(sentence)
    return sentences


def main() -> int:
    parser = argparse.ArgumentParser(description="Check README claims against registry")
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    parser.add_argument("--registry", type=Path, default=Path("eval/claims_registry.yaml"))
    args = parser.parse_args()

    if not args.registry.exists():
        print(f"ERROR: claims registry not found: {args.registry}")
        return 1

    registry = load_registry(args.registry)
    registered = registered_claim_texts(registry)

    if not args.readme.exists():
        print(f"ERROR: README not found: {args.readme}")
        return 1

    readme_text = args.readme.read_text(encoding="utf-8")
    sentences = extract_claim_sentences(readme_text)

    unregistered: list[str] = []
    for sentence in sentences:
        sentence_words = _significant_words(sentence)
        if not sentence_words:
            continue
        # A claim is considered registered if any registered quote/claim shares
        # at least 60% of its significant words with the sentence.
        matched = any(
            len(_significant_words(reg) & sentence_words)
            / max(len(_significant_words(reg)), 1)
            >= 0.6
            for reg in registered
        )
        if not matched:
            unregistered.append(sentence)

    if unregistered:
        print(f"ERROR: {len(unregistered)} README claim(s) lack a registry entry:")
        for sentence in unregistered:
            print(f"  - {sentence[:140]}")
        return 1

    print(f"OK: all {len(sentences)} extractable README claim(s) are registered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
