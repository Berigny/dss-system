"""Reverse lattice parser: reconstruct coordinate paths from prose summaries.

This module provides a lightweight, dependency-free parser that scans a prose
description of a lattice reading and recovers the coordinate path and semantic
tags that produced it.  It is intended as a round-trip sanity check for the
multi-reading kernel output formatter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from backend.kernel.coord_enrichment import COORD_REGISTRY, CoordEnrichmentCard
from backend.kernel.output_formatter import LatticeReadingOutput


@dataclass(frozen=True)
class ParsedUnit:
    """Result of parsing a prose description back into lattice coordinates."""

    source_type: str
    source_label: str
    coordinate_path: Tuple[str, ...]
    prose: str
    confidence: float
    matched_tokens: FrozenSet[str] = field(default_factory=frozenset)

    def to_dict(self) -> Dict[str, object]:
        return {
            "source_type": self.source_type,
            "source_label": self.source_label,
            "coordinate_path": list(self.coordinate_path),
            "prose": self.prose,
            "confidence": self.confidence,
            "matched_tokens": sorted(self.matched_tokens),
        }


class ReverseLatticeParser:
    """Parse prose summaries into coordinate paths using enrichment metadata."""

    def __init__(self) -> None:
        self._token_to_coords: Dict[str, Set[str]] = {}
        self._build_vocab()

    def _add_mapping(self, token: str, coord: str) -> None:
        token = token.strip().lower()
        if not token:
            return
        self._token_to_coords.setdefault(token, set()).add(coord)

    def _build_vocab(self) -> None:
        """Index every coordinate identifier and human-readable label."""
        for coord, card in COORD_REGISTRY.items():
            self._add_mapping(coord, coord)
            # The synthetic reset card shares labels with Aleph (000); skip its
            # human-readable tokens to avoid phantom matches in prose.
            if coord == "000_reset":
                continue
            self._add_mapping(card.hebrew_letter, coord)
            self._add_mapping(card.hebrew_name, coord)
            # Support "Kaf" and "Kaf Sofit" as separate searchable tokens.
            for part in card.hebrew_name.split():
                self._add_mapping(part, coord)
            if card.kernel_label:
                self._add_mapping(card.kernel_label, coord)
            self._add_mapping(card.structural_role, coord)
            self._add_mapping(card.structural_role_short, coord)
            if card.element:
                self._add_mapping(card.element, coord)
            if card.iching_trigram:
                self._add_mapping(card.iching_trigram, coord)
            if card.iching_name:
                self._add_mapping(card.iching_name, coord)

    def _extract_tokens(self, prose: str) -> List[str]:
        """Split prose into searchable tokens, preserving 3-digit coordinates."""
        tokens: List[str] = []
        # First pull out explicit coordinate strings.
        tokens.extend(re.findall(r"[0-2]{3}", prose))
        # Then pull words (including Hebrew letters).
        tokens.extend(re.findall(r"[\wא-ת]+(?:\s+Sofit)?", prose))
        return [t.strip() for t in tokens if t.strip()]

    def parse_prose(
        self,
        prose: str,
        source_type: str = "reverse_parsed",
        source_label: str = "prose",
    ) -> ParsedUnit:
        """Reconstruct a coordinate path from ``prose``.

        The returned path preserves the order in which matching tokens first
        appear in the text.
        """
        tokens = self._extract_tokens(prose)
        matched_tokens: Set[str] = set()
        coord_order: List[str] = []
        seen: Set[str] = set()

        for raw_token in tokens:
            token = raw_token.lower()
            if token in self._token_to_coords:
                matched_tokens.add(raw_token)
                for coord in sorted(self._token_to_coords[token]):
                    if coord not in seen:
                        coord_order.append(coord)
                        seen.add(coord)

        confidence = len(matched_tokens) / max(len(tokens), 1)
        return ParsedUnit(
            source_type=source_type,
            source_label=source_label,
            coordinate_path=tuple(coord_order),
            prose=prose,
            confidence=round(confidence, 4),
            matched_tokens=frozenset(matched_tokens),
        )

    def validate_reconstruction(
        self,
        original: LatticeReadingOutput,
        parsed: ParsedUnit,
    ) -> float:
        """Return Jaccard overlap between ``original.coordinates`` and ``parsed``.

        A value of ``1.0`` means perfect recovery of the coordinate set;
        ``0.0`` means no overlap.
        """
        original_set: Set[str] = set(original.coordinates)
        parsed_set: Set[str] = set(parsed.coordinate_path)
        if not original_set and not parsed_set:
            return 1.0
        intersection = original_set & parsed_set
        union = original_set | parsed_set
        return len(intersection) / len(union)

    def parse_units(
        self,
        output: LatticeReadingOutput,
    ) -> Tuple[ParsedUnit, ...]:
        """Parse each unit's prose independently and return the results."""
        parsed: List[ParsedUnit] = []
        for unit in output.unit_readings:
            parsed.append(
                self.parse_prose(
                    unit.prose or unit.raw_input,
                    source_type=unit.source_type,
                    source_label=unit.source_label,
                )
            )
        return tuple(parsed)


__all__ = (
    "ParsedUnit",
    "ReverseLatticeParser",
)
