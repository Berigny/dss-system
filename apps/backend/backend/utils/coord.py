from __future__ import annotations

import os
import re
from typing import Any

_PART_RE = re.compile(r"^(ATT-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)-([TIAVDP])(\d{3})$")


def namespace_candidates() -> list[str]:
    raw = os.getenv("COORD_DEFAULT_NAMESPACES", "default,chat-demo-session")
    return [item.strip() for item in raw.split(",") if item.strip()]


def normalise_coord(coord: str) -> dict[str, Any]:
    namespace = None
    bare = (coord or "").strip()

    if ":" in bare:
        namespace, bare = bare.rsplit(":", 1)
        namespace = namespace.strip() or None
        bare = bare.strip()

    if bare.startswith("W4-"):
        bare = bare[3:].strip()

    if bare.isdigit():
        return {
            "namespace": None,
            "bare": bare,
            "canonical": bare,
            "kind": "web4",
            "parent": None,
            "modality": None,
            "part_index": None,
        }

    kind = None
    parent = None
    modality = None
    part_index = None
    canonical_bare = bare

    part_match = _PART_RE.match(bare)
    if part_match:
        parent = part_match.group(1)
        suffix = part_match.group(2)
        part_index = int(part_match.group(3))
        kind = "part"
        if suffix == "T":
            modality = "text"
        elif suffix == "P":
            modality = "text"
        elif suffix == "I":
            modality = "image"
        elif suffix == "A":
            modality = "audio"
        elif suffix == "V":
            modality = "video"
        elif suffix == "D":
            modality = "data"

    if kind is None:
        if bare.startswith("WX-"):
            kind = "turn"
        elif bare.startswith("ATT-"):
            kind = "attachment"
        elif bare.startswith("PL-Conv-"):
            kind = "overlay"
        elif bare.startswith("PL-Claim-"):
            kind = "overlay"
        elif bare.startswith("PL-Taxon-"):
            kind = "overlay"
        elif bare.startswith("EV-WALK-"):
            kind = "coord_walk"
        elif bare.startswith("EV-"):
            kind = "event"
        elif bare.startswith("MD-Rule-"):
            kind = "meta"
        elif bare.startswith("MD-Run-"):
            kind = "meta"
        elif bare.startswith("MD-Reset-"):
            kind = "meta"

    canonical = canonical_bare
    if namespace:
        canonical = f"{namespace}:{canonical_bare}"

    return {
        "namespace": namespace,
        "bare": canonical_bare,
        "canonical": canonical,
        "kind": kind,
        "parent": parent,
        "modality": modality,
        "part_index": part_index,
    }


__all__ = ["normalise_coord", "namespace_candidates"]
