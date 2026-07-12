"""Esoteric language stripper for public surfaces.

Reads the Kernel Semantic Registry (KSR) glossary and synonym registry,
detects loaded/esoteric/religious/philosophical terminology in public-facing
files, and either replaces it with engineering terminology or flags it as a
violation. Private surfaces (encrypted KSR, steward docs, whitepaper) are
excluded by design.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .ksr_crypto import load_ksr_yaml


DEFAULT_PUBLIC_EXTENSIONS: frozenset[str] = frozenset({
    ".py", ".md", ".yaml", ".yml", ".json", ".js", ".ts", ".html", ".css",
    ".toml", ".txt", ".spl",
})

DEFAULT_PRIVATE_PATTERNS: tuple[str, ...] = (
    "backend/kernel/semantic_registry.yaml",
    "backend/kernel/semantic_registry.enc",
    "backend/kernel/.ksr/",
    "steward/",
    "backlog_reqs/paper/",
    ".git",
    "__pycache__",
    ".venv",
    "node_modules",
    "dist",
    "build",
)


@dataclass
class StripReport:
    files_processed: int = 0
    files_modified: int = 0
    replacements: dict[str, int] = field(default_factory=dict)
    violations: dict[str, dict[str, list[str]]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "files_processed": self.files_processed,
            "files_modified": self.files_modified,
            "replacements": self.replacements,
            "violations": self.violations,
        }


class EsotericLanguageStripper:
    """Strip/replace esoteric language in public files using the KSR glossary."""

    def __init__(
        self,
        ksr_data: dict[str, Any],
        public_extensions: frozenset[str] | None = None,
        private_patterns: tuple[str, ...] | None = None,
    ) -> None:
        self.ksr_data = ksr_data
        self.public_extensions = public_extensions or DEFAULT_PUBLIC_EXTENSIONS
        self.private_patterns = private_patterns or DEFAULT_PRIVATE_PATTERNS
        self.glossary = ksr_data.get("glossary", [])
        self.synonym_registry = ksr_data.get("synonym_registry", {})
        self.stripping_priority = ksr_data.get("stripping_priority", {})

        # Build ordered replacement map. Longer terms first so multi-word
        # phrases match before their constituent words.
        self._replacement_map: dict[str, str] = {}
        for entry in self.glossary:
            term = entry.get("term", "")
            replacement = entry.get("replacement", "")
            if term and replacement:
                self._replacement_map[term] = replacement

        # Synonyms point to canonical replacement strings. If a canonical is
        # itself a replacement key, resolve it.
        for canonical, synonyms in self.synonym_registry.items():
            replacement = self._replacement_map.get(canonical, canonical)
            for synonym in synonyms:
                self._replacement_map.setdefault(synonym, replacement)

        # Sort by descending length so "Omega Point" wins over "Omega".
        self._ordered_terms = sorted(
            self._replacement_map.keys(), key=len, reverse=True
        )

        # Set of prohibited terms that have no replacement and must be flagged.
        self._prohibited: set[str] = set()
        for entry in self.glossary:
            if not entry.get("replacement"):
                self._prohibited.add(entry["term"])

        # Confidence/relation metadata for reporting.
        self._term_confidence: dict[str, str] = {}
        self._term_relation: dict[str, str] = {}
        for entry in self.glossary:
            term = entry.get("term", "")
            if term:
                self._term_confidence[term] = entry.get("confidence", "H")
                self._term_relation[term] = entry.get("relation_type", "ANALOGY")

        # Priority sets for reporting.
        self._priority_map: dict[str, str] = {}
        for level, terms in self.stripping_priority.items():
            for term in terms:
                self._priority_map[term.lower()] = level

    def _is_private(self, path: Path) -> bool:
        rel = path.as_posix()
        for pattern in self.private_patterns:
            if pattern in rel:
                return True
        return False

    def _matches_extension(self, path: Path) -> bool:
        return path.suffix.lower() in self.public_extensions

    def _replacement_regex(self, term: str) -> re.Pattern[str]:
        # Phrase-aware matching: allow word boundaries around each word.
        # For single words, use \b on both sides. For phrases, keep flexible.
        escaped = re.escape(term)
        if " " in term:
            # Allow surrounding non-word boundaries as long as the phrase is
            # not embedded in another word. Case-insensitive.
            pattern = r"(?i)(?<![\w/])" + escaped + r"(?![\w/])"
        else:
            pattern = r"(?i)\b" + escaped + r"\b"
        return re.compile(pattern)

    def _process_text(self, text: str) -> tuple[str, dict[str, int], list[tuple[str, str, str]]]:
        """Return (new_text, replacements, violations).

        Violations are tuples of (term, priority, line_preview).
        """
        replacements: dict[str, int] = {}
        violations: list[tuple[str, str, str]] = []
        new_text = text

        for term in self._ordered_terms:
            replacement = self._replacement_map[term]
            regex = self._replacement_regex(term)
            count = 0
            def repl(match: re.Match[str]) -> str:
                nonlocal count
                count += 1
                return replacement
            new_text, subs = regex.subn(repl, new_text)
            if subs:
                replacements[term] = replacements.get(term, 0) + subs

        # Flag prohibited terms that have no replacement.
        for term in self._prohibited:
            regex = self._replacement_regex(term)
            for match in regex.finditer(new_text):
                line_start = new_text.rfind("\n", 0, match.start()) + 1
                line_end = new_text.find("\n", match.end())
                if line_end == -1:
                    line_end = len(new_text)
                preview = new_text[line_start:line_end].strip()
                priority = self._priority_map.get(term.lower(), "high")
                violations.append((term, priority, preview))

        return new_text, replacements, violations

    def process_file(self, path: Path, check_only: bool = False) -> tuple[bool, dict[str, Any]]:
        """Process a single file.

        Returns (modified, file_report).
        """
        text = path.read_text(encoding="utf-8")
        new_text, replacements, violations = self._process_text(text)
        modified = new_text != text

        if not check_only and modified:
            path.write_text(new_text, encoding="utf-8")

        file_report: dict[str, Any] = {
            "path": path.as_posix(),
            "replacements": {
                term: {
                    "count": count,
                    "confidence": self._term_confidence.get(term, "H"),
                    "relation_type": self._term_relation.get(term, "ANALOGY"),
                }
                for term, count in replacements.items()
            },
            "violations": [
                {
                    "term": v[0],
                    "priority": v[1],
                    "preview": v[2],
                    "confidence": self._term_confidence.get(v[0], "H"),
                    "relation_type": self._term_relation.get(v[0], "ANALOGY"),
                }
                for v in violations
            ],
        }
        return modified, file_report

    def scan_directory(
        self,
        target: str | Path,
        check_only: bool = False,
    ) -> StripReport:
        """Walk target directory and process all matching public files."""
        target_path = Path(target)
        report = StripReport()

        for path in target_path.rglob("*"):
            if not path.is_file():
                continue
            if self._is_private(path):
                continue
            if not self._matches_extension(path):
                continue

            report.files_processed += 1
            modified, file_report = self.process_file(path, check_only=check_only)
            if modified:
                report.files_modified += 1
            for term, details in file_report["replacements"].items():
                report.replacements[term] = report.replacements.get(term, 0) + details["count"]
            for violation in file_report["violations"]:
                priority = violation["priority"]
                term = violation["term"]
                report.violations.setdefault(priority, {}).setdefault(term, []).append(
                    file_report["path"]
                )

        return report

    def has_critical_or_high_violations(self, report: StripReport) -> bool:
        return bool(report.violations.get("critical") or report.violations.get("high"))

    def has_steward_only_violations(self, report: StripReport) -> bool:
        """Return True if any P/H (poetic/heuristic) term remains on a public surface."""
        for priority_bucket in report.violations.values():
            for term, paths in priority_bucket.items():
                if self._term_confidence.get(term, "H") in {"P", "H"}:
                    if paths:
                        return True
        return False


def _load_ksr_from_env_or_yaml(repo_root: Path) -> dict[str, Any]:
    yaml_path = repo_root / "backend" / "kernel" / "semantic_registry.yaml"
    if yaml_path.exists():
        return yaml.safe_load(yaml_path.read_text())
    password = os.getenv("DSS_KSR_PASSWORD")
    if not password:
        raise RuntimeError(
            "No plaintext KSR found and DSS_KSR_PASSWORD is not set."
        )
    return load_ksr_yaml(password)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Esoteric language stripper")
    parser.add_argument("--target", default=".", help="Root directory to scan")
    parser.add_argument("--check", action="store_true", help="Do not write files; just report violations")
    parser.add_argument("--report", default="strip_report.json", help="Path to write JSON report")
    parser.add_argument("--repo-root", default=".", help="Repository root for locating KSR")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    ksr_data = _load_ksr_from_env_or_yaml(repo_root)
    stripper = EsotericLanguageStripper(ksr_data)
    report = stripper.scan_directory(args.target, check_only=args.check)

    Path(args.report).write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    print(json.dumps(report.to_dict(), indent=2))

    if stripper.has_critical_or_high_violations(report):
        print("ERROR: critical or high-priority violations remain.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
