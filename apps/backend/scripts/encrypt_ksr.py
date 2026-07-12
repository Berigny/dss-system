#!/usr/bin/env python3
"""Build-time helper: encrypt backend/kernel/semantic_registry.yaml.

Usage:
    DSS_KSR_PASSWORD=<secret> python scripts/encrypt_ksr.py

If DSS_KSR_PASSWORD is not set, the script falls back to a deterministic test
password and prints a loud warning. This is only for local development and
CI; production steward keys must never be committed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.kernel.ksr_crypto import encrypt_ksr  # noqa: E402


def main() -> int:
    repo_root = Path(__file__).parent.parent
    ksr_yaml = repo_root / "backend" / "kernel" / "semantic_registry.yaml"
    whitepaper_hash_path = repo_root / "backend" / "kernel" / ".ksr" / "whitepaper.hash"
    out_path = repo_root / "backend" / "kernel" / "semantic_registry.enc"

    if not ksr_yaml.exists():
        print(f"ERROR: KSR source not found: {ksr_yaml}", file=sys.stderr)
        return 1

    password = os.getenv("DSS_KSR_PASSWORD")
    if not password:
        print(
            "WARNING: DSS_KSR_PASSWORD not set; using deterministic test password. "
            "Never use this in production.",
            file=sys.stderr,
        )
        password = "DSS-KSR-TEST-195"

    whitepaper_hash = whitepaper_hash_path.read_text().strip()
    plaintext = ksr_yaml.read_bytes()
    envelope = encrypt_ksr(plaintext, password, whitepaper_hash=whitepaper_hash)
    envelope.write(out_path)
    print(f"Wrote encrypted KSR to {out_path}")
    print(f"KSR SHA-256: {envelope.ksr_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
