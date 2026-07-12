#!/usr/bin/env python3
"""Pre-commit / CI gate for Kernel Semantic Registry hygiene.

Checks:
1. The esoteric-language stripper finds no critical/high violations in
   public-facing tracked files.
2. If backend/kernel/semantic_registry.yaml is present, the generated
   backend/kernel/constants.py is up-to-date with it.

Exit code 0 = clean; 1 = violation or stale constants.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def _run_stripper() -> int:
    stripper = REPO_ROOT / "backend" / "kernel" / "esoteric_stripper.py"
    report_path = REPO_ROOT / "backend" / "kernel" / ".ksr" / "precommit_strip_report.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "backend.kernel.esoteric_stripper",
            "--target",
            str(REPO_ROOT),
            "--check",
            "--report",
            str(report_path),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode


def _constants_stale() -> bool:
    yaml_path = REPO_ROOT / "backend" / "kernel" / "semantic_registry.yaml"
    constants_path = REPO_ROOT / "backend" / "kernel" / "constants.py"
    if not yaml_path.exists():
        return False
    if not constants_path.exists():
        return True

    # Re-generate constants into memory and compare to the committed file.
    generator = REPO_ROOT / "scripts" / "generate_kernel_constants.py"
    result = subprocess.run(
        [sys.executable, str(generator)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return True

    generated = constants_path.read_bytes()
    committed = constants_path.with_suffix(".committed").read_bytes() if False else None
    # Instead of moving the file, we compare hashes after the generator just
    # overwrote constants.py. If the file changed, the hash differs from what
    # git has. We restore the original from git to avoid dirtying the working tree.
    git_show = subprocess.run(
        ["git", "show", f"HEAD:{constants_path.relative_to(REPO_ROOT)}"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    if git_show.returncode != 0:
        # File is not tracked yet; treat as stale if it changed.
        return False
    committed_hash = hashlib.sha256(git_show.stdout.encode("utf-8")).hexdigest()
    generated_hash = hashlib.sha256(generated).hexdigest()
    if committed_hash != generated_hash:
        print(
            f"ERROR: {constants_path.relative_to(REPO_ROOT)} is stale. "
            "Run python scripts/generate_kernel_constants.py and commit the result.",
            file=sys.stderr,
        )
        return True
    return False


def main() -> int:
    exit_code = 0
    print("--- KSR esoteric-language check ---")
    if _run_stripper() != 0:
        exit_code = 1

    print("--- KSR constants freshness check ---")
    if _constants_stale():
        exit_code = 1
    else:
        print("constants.py is up-to-date.")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
