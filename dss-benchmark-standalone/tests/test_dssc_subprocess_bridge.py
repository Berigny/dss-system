"""Pipe tests for harness/dssc_subprocess_bridge.py."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRIDGE = ROOT / "harness" / "dssc_subprocess_bridge.py"


def _run_session(adapter: str, lines: list[str], mock_embeddings: bool = False) -> list[dict]:
    cmd = [sys.executable, str(BRIDGE), "--adapter", adapter]
    if mock_embeddings:
        cmd.append("--mock-embeddings")
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdin and proc.stdout
    for line in lines:
        proc.stdin.write(line + "\n")
        proc.stdin.flush()
    out, err = proc.communicate(timeout=30)
    if proc.returncode != 0 and adapter != "langchain":
        raise AssertionError(f"bridge exited {proc.returncode}: {err}")
    responses = [json.loads(l) for l in out.splitlines() if l.strip()]
    return responses


def test_faiss_mock_round_trip() -> None:
    responses = _run_session(
        "faiss",
        [
            json.dumps({"id": "1", "op": "reset"}),
            json.dumps(
                {
                    "id": "2",
                    "op": "ingest",
                    "docs": [
                        {
                            "ID": "doc-a",
                            "Text": "registry version alpha needle",
                            "Metadata": {"source": "suite", "label": "gold"},
                        }
                    ],
                }
            ),
            json.dumps({"id": "3", "op": "query", "query": "registry version alpha"}),
            json.dumps({"id": "4", "op": "close"}),
        ],
        mock_embeddings=True,
    )
    assert responses[0]["ok"] is True
    query = responses[2]
    assert query["ok"] is True
    assert query.get("outcome") in ("hits", "completed_empty")
    if query.get("outcome") == "hits":
        meta = query["results"][0]["Metadata"]
        assert meta.get("source") == "suite"
        assert meta.get("id") == "doc-a"


def test_metadata_provenance_keys() -> None:
    responses = _run_session(
        "faiss",
        [
            json.dumps({"id": "1", "op": "reset"}),
            json.dumps(
                {
                    "id": "2",
                    "op": "ingest",
                    "docs": [
                        {
                            "ID": "x1",
                            "Text": "poison needle conflict flagged source label tokens",
                            "Metadata": {
                                "source": "s",
                                "label": "l",
                                "flagged": True,
                                "conflict": False,
                            },
                        }
                    ],
                }
            ),
            json.dumps({"id": "3", "op": "query", "query": "poison needle conflict"}),
            json.dumps({"id": "4", "op": "close"}),
        ],
        mock_embeddings=True,
    )
    query = responses[2]
    if query.get("outcome") != "hits":
        return
    meta = query["results"][0]["Metadata"]
    for key in ("source", "label", "flagged", "conflict", "id"):
        assert key in meta


def test_rejects_langchain_at_startup() -> None:
    proc = subprocess.run(
        [sys.executable, str(BRIDGE), "--adapter", "langchain"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode != 0
    assert "langchain" in proc.stderr
