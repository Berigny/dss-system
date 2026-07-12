"""CLI entry-point tests."""

from __future__ import annotations

import json

import pytest

from dss_ledger.cli import main


def test_cli_parse(capsys):
    assert main(["parse", "--text", "autonomy action mastery"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "PARSED"


def test_cli_encode(capsys):
    assert (
        main(
            ["encode", "--slots", '{"agent":"autonomy","verb":"action","patient":"mastery"}']
        )
        == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert out["pid"] > 0


def test_cli_query_rejects_unknown(capsys):
    assert main(["query", "--text", "autonomy action mastery"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["valid"] is False
    assert out["validate"]["error"] == "PROCESS_NOT_FOUND"


def test_cli_append_and_validate(tmp_path, capsys):
    ledger_dir = str(tmp_path / "ledger")
    assert (
        main(
            [
                "append",
                "--ledger-dir",
                ledger_dir,
                "--slots",
                '{"agent":"autonomy","verb":"action","patient":"mastery"}',
            ]
        )
        == 0
    )
    append_out = json.loads(capsys.readouterr().out)
    pid = append_out["encoded"]["pid"]

    assert main(["validate", "--ledger-dir", ledger_dir, "--pid", str(pid)]) == 0
    validate_out = json.loads(capsys.readouterr().out)
    assert validate_out["valid"] is True


def test_cli_missing_text(capsys):
    assert main(["parse"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert "error" in out
