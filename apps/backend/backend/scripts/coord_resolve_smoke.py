#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any
from urllib.request import Request, urlopen

from backend.utils.coord import normalise_coord, namespace_candidates


@dataclass
class Case:
    name: str
    coord: str
    kind: str | None
    canonical: str | None = None


CASES = [
    Case("bare_turn", "WX-7F2A91B3", "turn"),
    Case("namespaced_turn", "chat-demo-session:WX-7F2A91B3", "turn"),
    Case("bare_attachment", "ATT-21d4853e", "attachment"),
    Case("namespaced_attachment", "chat-demo-session:ATT-21d4853e", "attachment"),
    Case("text_part", "ATT-21d4853e-T002", "part"),
    Case("image_part", "ATT-21d4853e-I001", "part"),
    Case("legacy_part", "ATT-21d4853e-P003", "part", "ATT-21d4853e-T003"),
    Case("overlay", "PL-Conv-00027", "overlay"),
    Case("web4", "481579", "web4"),
    Case("web4_wrapped", "W4-481579", "web4"),
]


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    failures: list[str] = []

    print("== Normalise ==")
    for case in CASES:
        normalized = normalise_coord(case.coord)
        kind = normalized.get("kind")
        if case.kind and kind != case.kind:
            failures.append(f"{case.name}: kind={kind} expected={case.kind}")
        if case.canonical and normalized.get("bare") != case.canonical:
            failures.append(f"{case.name}: canonical_bare={normalized.get('bare')} expected={case.canonical}")
        print(case.name, normalized)

    print("\n== Namespace candidates ==")
    print(namespace_candidates())

    base_url = os.getenv("COORD_TEST_BASE_URL")
    if base_url:
        base_url = base_url.rstrip("/")
        print("\n== Resolve (/web4/decode) ==")
        for case in CASES:
            try:
                payload = {"coordinate": case.coord}
                response = _post_json(f"{base_url}/web4/decode", payload)
                status = response.get("status")
                print(case.name, "status=", status or "no-status", "kind=", response.get("kind"))
            except Exception as exc:
                failures.append(f"{case.name}: /web4/decode failed ({exc})")

        print("\n== Resolve (/api/chat/web4/decode) ==")
        for case in CASES:
            try:
                payload = {"coordinate": case.coord, "entity": "chat-demo-session"}
                response = _post_json(f"{base_url}/api/chat/web4/decode", payload)
                status = response.get("status")
                print(case.name, "status=", status or "no-status", "kind=", response.get("kind"))
            except Exception as exc:
                failures.append(f"{case.name}: /api/chat/web4/decode failed ({exc})")

    if failures:
        print("\nFailures:")
        for failure in failures:
            print("-", failure)
        return 1

    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
