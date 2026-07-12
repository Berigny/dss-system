"""High-level service orchestrating the dual-layer ledger pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dss_ledger.schema import LedgerSchema
from dss_ledger.src.decoder import LedgerDecoder
from dss_ledger.src.encoder import LedgerEncoder
from dss_ledger.src.parser import ConstrainedParser
from dss_ledger.src.validator import ProcessLedger


class ProcessService:
    """End-to-end process layer: parse → encode → validate → append."""

    def __init__(
        self,
        config_dir: str | Path | None = None,
        ledger_dir: str | Path | None = None,
    ) -> None:
        self._schema = LedgerSchema.from_config_dir(config_dir)
        if ledger_dir is None:
            ledger_dir = Path(__file__).parent / "ledger"
        self._ledger = ProcessLedger(ledger_dir)
        self._encoder = LedgerEncoder(self._schema)
        self._decoder = LedgerDecoder(self._schema)
        self._parser = ConstrainedParser(self._schema)

    def encode(self, slots: dict[str, str]) -> dict[str, Any]:
        """Encode structured slots into a PID."""
        return self._encoder.encode_process(**slots)

    def parse(self, text: str) -> dict[str, Any]:
        """Parse natural language into structured slots."""
        return self._parser.parse(text)

    def query(self, text: str, expected_result: str | None = None) -> dict[str, Any]:
        """Full pipeline: text → parse → encode → validate."""
        parsed = self._parser.parse(text)
        if parsed["status"] == "REJECT":
            return {
                "pipeline": ["parse"],
                "parse": parsed,
                "valid": False,
                "error": parsed["reason"],
                "message": parsed["message"],
            }

        encoded = self._encoder.encode_process(**parsed["slots"])
        validated = self._ledger.validate(
            encoded["pid"], expected_result=expected_result
        )

        return {
            "pipeline": ["parse", "encode", "validate"],
            "parse": parsed,
            "encode": encoded,
            "validate": validated,
            "valid": validated.get("valid", False),
        }

    def append_slots(self, slots: dict[str, str]) -> dict[str, Any]:
        """Encode slots and append the resulting process to the ledger."""
        encoded = self._encoder.encode_process(**slots)
        entry = {
            "canonical": encoded["canonical"],
            "canonical_result": slots.get("result", "unknown"),
            "domain": "kernel",
            "certainty": 1.0,
            "source": "manual",
        }
        return {
            "encoded": encoded,
            "append": self._ledger.append(encoded["pid"], entry),
        }

    def append_text(self, text: str) -> dict[str, Any]:
        """Parse text and append the resulting process to the ledger."""
        parsed = self._parser.parse(text)
        if parsed["status"] == "REJECT":
            return {"parse": parsed, "append": None}
        return self.append_slots(parsed["slots"])

    def factor(self, pid: int) -> dict[str, Any]:
        """Factor a PID back into slots and canonical text."""
        return self._decoder.factor(pid)
