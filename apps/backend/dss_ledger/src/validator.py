"""Process ledger validator and append-only causal graph manager."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class ProcessLedger:
    """Mutable runtime store for validated process entries."""

    def __init__(self, ledger_dir: str | Path) -> None:
        self._ledger_dir = Path(ledger_dir)
        self._ledger_dir.mkdir(parents=True, exist_ok=True)
        self._graph_path = self._ledger_dir / "causal_graph.json"
        self._history_path = self._ledger_dir / "history.log"

        if not self._graph_path.exists():
            self._graph_path.write_text("{}\n")
        if not self._history_path.exists():
            self._history_path.write_text("")

    def _load_graph(self) -> dict[str, Any]:
        try:
            return json.loads(self._graph_path.read_text())
        except Exception:
            return {}

    def _save_graph(self, graph: dict[str, Any]) -> None:
        self._graph_path.write_text(json.dumps(graph, indent=2, sort_keys=True) + "\n")

    @property
    def graph(self) -> dict[str, Any]:
        return self._load_graph()

    def validate(
        self,
        pid: int,
        expected_result: str | None = None,
    ) -> dict[str, Any]:
        """Check if a PID exists in the causal graph and matches expected result."""
        pid_str = str(int(pid))
        graph = self._load_graph()

        if pid_str not in graph:
            return {
                "valid": False,
                "certainty": 0.0,
                "error": "PROCESS_NOT_FOUND",
                "message": "This causal chain is not in the ledger.",
            }

        entry = graph[pid_str]
        certainty = float(entry.get("certainty", 1.0))

        if expected_result is not None:
            actual = entry.get("canonical_result")
            if actual != expected_result:
                return {
                    "valid": False,
                    "certainty": 0.0,
                    "error": "RESULT_MISMATCH",
                    "message": f"Expected {expected_result}, but ledger shows {actual}",
                    "actual": actual,
                }

        return {
            "valid": True,
            "certainty": certainty,
            "canonical": entry.get("canonical", ""),
            "domain": entry.get("domain", "unknown"),
            "message": "Process validated against ledger.",
        }

    def append(self, pid: int, entry: dict[str, Any]) -> dict[str, Any]:
        """Append a validated process entry to the causal graph.

        Existing entries are never mutated.
        """
        pid_str = str(int(pid))
        graph = self._load_graph()

        if pid_str in graph:
            return {"status": "EXISTS", "message": "Entry already in ledger."}

        graph[pid_str] = dict(entry)
        self._save_graph(graph)

        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._history_path.open("a") as f:
            f.write(
                f"{timestamp} APPEND {pid_str} {entry.get('canonical', '')}\n"
            )

        return {"status": "APPENDED", "pid": pid_str}
