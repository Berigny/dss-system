"""Lightweight text normalisation helpers for substrate ingestion."""

from __future__ import annotations

import re
from collections import Counter
from typing import Dict, List


STOPWORDS = {
    "the",
    "and",
    "for",
    "that",
    "with",
    "this",
    "have",
    "from",
    "your",
    "about",
    "into",
    "there",
    "their",
    "would",
    "could",
    "should",
}


def _extract_title(lines: List[str]) -> str | None:
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith("title:"):
            return stripped.split(":", 1)[1].strip() or None
        return stripped
    return None


def _extract_author(text: str) -> str | None:
    author_match = re.search(r"by\s+([A-Z][A-Za-z\s]+)", text)
    if author_match:
        return author_match.group(1).strip()

    for line in text.splitlines():
        if line.lower().startswith("author:"):
            return line.split(":", 1)[1].strip() or None
    return None


def _extract_year(text: str) -> int | None:
    match = re.search(r"\b(19|20)\d{2}\b", text)
    return int(match.group(0)) if match else None


def _extract_topics(tokens: List[str]) -> List[str]:
    filtered = [t.lower() for t in tokens if len(t) > 3 and t.lower() not in STOPWORDS]
    counts = Counter(filtered)
    return [topic for topic, _ in counts.most_common(8)]


def _extract_quotes(text: str) -> List[str]:
    quotes: List[str] = []
    for line in text.splitlines():
        if line.strip().startswith(">"):  # markdown-style quote
            quotes.append(line.strip().lstrip("> "))

    inline_quotes = re.findall(r"\"([^\"]{3,}?)\"", text)
    for quote in inline_quotes:
        cleaned = quote.strip()
        if cleaned:
            quotes.append(cleaned)
    return quotes


def normalise_text(raw: str) -> Dict[str, object]:
    """Return structured normalisation metadata for ``raw`` text."""

    lines = raw.splitlines()
    tokens = re.findall(r"[A-Za-z0-9]+", raw)

    title = _extract_title(lines)
    author = _extract_author(raw)
    year = _extract_year(raw)
    topics = _extract_topics(tokens)
    quotes = _extract_quotes(raw)

    return {
        "raw": raw,
        "title": title,
        "author": author,
        "year": year,
        "topics": topics,
        "tags": topics,
        "quotes": quotes,
    }


__all__ = ["normalise_text"]
