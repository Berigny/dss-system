"""Tests for backend/kernel/esoteric_stripper.py."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("DSS_KSR_PBKDF2_ITERATIONS", "1000")

from backend.kernel.esoteric_stripper import EsotericLanguageStripper  # noqa: E402


@pytest.fixture
def ksr_data() -> dict:
    return {
        "glossary": [
            {"term": "Omega Point", "replacement": "terminal_coherence_state", "category": "esoteric", "priority": "critical"},
            {"term": "Override mode", "replacement": "override_mode", "category": "esoteric", "priority": "critical"},
            {"term": "Law", "replacement": "constraint", "category": "esoteric", "priority": "medium"},
        ],
        "synonym_registry": {
            "terminal_coherence_state": ["Omega", "omega point"],
        },
        "stripping_priority": {
            "critical": ["Omega Point", "Override mode", "YHVH"],
            "high": [],
            "medium": ["Law"],
            "low": [],
        },
        "surface_policy": {"public_extensions": [".py", ".md"], "private_paths": []},
    }


def test_replaces_esoteric_term(ksr_data: dict) -> None:
    stripper = EsotericLanguageStripper(ksr_data)
    text = "The Omega Point is reached when Omega conditions hold."
    new_text, replacements, violations = stripper._process_text(text)
    assert "Omega Point" not in new_text
    assert "terminal_coherence_state" in new_text
    assert replacements["Omega Point"] == 1
    assert replacements["Omega"] == 1
    assert not violations


def test_excludes_private_paths(ksr_data: dict, tmp_path: Path) -> None:
    ksr_data["surface_policy"]["private_paths"] = ["steward/"]
    stripper = EsotericLanguageStripper(ksr_data)
    public_file = tmp_path / "readme.md"
    public_file.write_text("Omega Point is critical.")
    private_file = tmp_path / "steward" / "notes.md"
    private_file.parent.mkdir()
    private_file.write_text("Omega Point is private.")

    report = stripper.scan_directory(tmp_path, check_only=True)
    assert report.files_processed == 1
    assert "Omega Point" in report.replacements


def test_prohibited_term_flagged(ksr_data: dict) -> None:
    ksr_data["glossary"].append({"term": "YHVH", "replacement": "", "category": "esoteric", "priority": "critical"})
    stripper = EsotericLanguageStripper(ksr_data)
    text = "The YHVH token appears here."
    new_text, replacements, violations = stripper._process_text(text)
    assert len(violations) == 1
    assert violations[0][0] == "YHVH"
    assert violations[0][1] == "critical"
