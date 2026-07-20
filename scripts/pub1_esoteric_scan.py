#!/usr/bin/env python3
"""PUB-1 esoteric-content scan for the public DSS tree.

Scans the repository for terms that belong in the steward-only KSR pack and
must not appear in public code, comments, docs, or commit messages. Exit code
is 0 when the tree is clean and 1 when any lexicon term is found.

Usage:
    python3 scripts/pub1_esoteric_scan.py [--root .] [--lexicon PATH]

The default lexicon lives at scripts/pub1_esoteric_lexicon.txt. Each line is a
case-insensitive phrase. Blank lines and lines starting with '#' are ignored.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_LEXICON = Path(__file__).with_name("pub1_esoteric_lexicon.txt")


def _load_gitignore(root: Path) -> list[str]:
    """Load .gitignore patterns if present."""
    path = root / ".gitignore"
    if not path.exists():
        return []
    patterns: list[str] = []
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            patterns.append(line)
    return patterns


def _matches_gitignore(rel: str, patterns: list[str]) -> bool:
    """Return True if rel matches any gitignore pattern (basic support)."""
    import fnmatch
    parts = Path(rel).parts
    for pat in patterns:
        if pat.endswith("/"):
            # Directory match: any component equals the directory name.
            dirname = pat.rstrip("/")
            if dirname in parts:
                return True
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(Path(rel).name, pat):
            return True
        # Also match if any path component matches.
        for part in parts:
            if fnmatch.fnmatch(part, pat):
                return True
    return False

# Paths that are allowed to contain esoteric content because they are explicitly
# private deploy artifacts, steward-only modules, or build outputs. These are
# skipped by the file scan.
DEFAULT_PRIVATE_PATHS = {
    "private",
    "ksr/pack",
    "apps/backend/backend/kernel/.ksr",
    "apps/backend/backend/kernel/semantic_registry.yaml",
    "apps/backend/backend/kernel/semantic_registry.enc",
    # Steward-only lattice enrichment modules (Hebrew / iChing overlays).
    "apps/backend/backend/kernel/coord_enrichment.py",
    "apps/backend/backend/kernel/embeddings.py",
    "apps/backend/backend/kernel/output_formatter.py",
    "apps/backend/backend/kernel/reverse_parser.py",
    "apps/backend/backend/kernel/tests/test_coord_enrichment.py",
    "apps/backend/backend/utils/ref",
    # Partition decision docs legitimately name steward-only field names when
    # documenting what was removed from the public artifact.
    "docs/decisions",
    "docs/load-path-inventory.md",
    # Tooling that legitimately references steward-only field names while
    # operating on ksr-core (field references are optional and gated).
    "scripts/pub1_esoteric_scan.py",
    "scripts/pub1_esoteric_lexicon.txt",
    "tools/ksr_build.py",
    "tools/decode.py",
    "tools/encode.py",
    "tools/eval_decode.py",
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".venv",
    ".venv-ksr",
    "node_modules",
}

# Historical commits that are allowed to name lexicon terms because they are
# themselves cleanup commits that *removed* those terms from the public tree.
# Rewriting public history is not an option, so we explicitly ignore them.
DEFAULT_IGNORED_COMMITS: dict[str, str] = {
    "4d94cebc3693e240df70090abb2311f22d64f283": (
        "DSS-290: removed 'god mode' / 'holy grail' references from public files"
    ),
}


def load_lexicon(path: Path) -> list[str]:
    """Load non-empty, non-comment phrases from the lexicon file."""
    if not path.exists():
        return []
    terms: list[str] = []
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            terms.append(line)
    return terms


def is_private_path(rel: str) -> bool:
    """Return True if a repository-relative path is a known private artifact."""
    parts = Path(rel).parts
    for private in DEFAULT_PRIVATE_PATHS:
        if rel.startswith(private) or private in parts:
            return True
    return False


def _term_pattern(term: str) -> re.Pattern[str]:
    """Return a case-insensitive regex for a lexicon term.

    Multi-word phrases are matched literally. Single-word terms are matched
    with word boundaries to avoid false positives inside larger words such as
    'diagnostics' containing 'gnostic'.
    """
    if " " in term:
        return re.compile(re.escape(term), re.IGNORECASE)
    return re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE)


def scan_file(path: Path, terms: list[str], patterns: list[re.Pattern[str] | None] | None = None) -> list[tuple[str, int, str]]:
    """Scan a single file for lexicon hits.

    Returns a list of (term, line_number, line_text) tuples.
    """
    if patterns is None:
        patterns = [_term_pattern(t) for t in terms]
    hits: list[tuple[str, int, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return hits
    for lineno, line in enumerate(text.splitlines(), start=1):
        for term, pat in zip(terms, patterns):
            if pat.search(line):
                hits.append((term, lineno, line.strip()))
    return hits


def scan_tree(root: Path, terms: list[str], gitignore_patterns: list[str] | None = None) -> dict[str, list[tuple[str, int, str]]]:
    """Scan every public file under root for lexicon hits."""
    patterns = [_term_pattern(t) for t in terms]
    gitignore = gitignore_patterns or _load_gitignore(root)
    findings: dict[str, list[tuple[str, int, str]]] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if is_private_path(rel):
            continue
        if _matches_gitignore(rel, gitignore):
            continue
        hits = scan_file(path, terms, patterns)
        if hits:
            findings[rel] = hits
    return findings


def scan_git_history(
    root: Path,
    terms: list[str],
    ignored_commits: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Scan all commit messages for lexicon hits.

    Returns a list of (commit_hash, matched_term) tuples.
    """
    patterns = [_term_pattern(t) for t in terms]
    ignored = ignored_commits or {}
    findings: list[tuple[str, str]] = []
    if not (root / ".git").exists():
        return findings
    try:
        proc = subprocess.run(
            ["git", "log", "--all", "--pretty=format:%H%x00%B%x00"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return findings
    if proc.returncode != 0:
        return findings
    commits = proc.stdout.split("\x00")
    # Format is hash, body, hash, body, ...
    for i in range(0, len(commits) - 1, 2):
        commit_hash = commits[i].strip()
        body = commits[i + 1]
        if not commit_hash:
            continue
        if commit_hash in ignored:
            continue
        for term, pat in zip(terms, patterns):
            if pat.search(body):
                findings.append((commit_hash, term))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="PUB-1 esoteric content scan")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--lexicon", type=Path, default=DEFAULT_LEXICON)
    parser.add_argument("--skip-history", action="store_true", help="Skip git commit message scan")
    args = parser.parse_args()

    terms = load_lexicon(args.lexicon)
    if not terms:
        print("PUB-1: no lexicon terms loaded; scan is a no-op.")
        return 0

    print(f"PUB-1 esoteric scan | lexicon: {args.lexicon} ({len(terms)} terms)")

    file_findings = scan_tree(args.root, terms)
    history_findings = (
        []
        if args.skip_history
        else scan_git_history(args.root, terms, ignored_commits=DEFAULT_IGNORED_COMMITS)
    )

    if not file_findings and not history_findings:
        print("PUB-1: clean (0 hits)")
        return 0

    print(f"PUB-1: {len(file_findings)} file(s) and {len(history_findings)} commit(s) with hits")
    for rel, hits in sorted(file_findings.items()):
        for term, lineno, line in hits:
            print(f"  {rel}:{lineno}: term='{term}'")
    for commit_hash, term in history_findings:
        print(f"  commit {commit_hash[:8]}: term='{term}'")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
