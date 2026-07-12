#!/usr/bin/env python3
"""CLI entry point for the dual-layer non-commutative ledger."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dss_ledger.service import ProcessService


def _json_out(data: object) -> None:
    print(json.dumps(data, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dual-Layer Non-Commutative Ledger")
    parser.add_argument(
        "command",
        choices=["parse", "encode", "validate", "query", "append", "factor"],
    )
    parser.add_argument("--text", "-t", help="Natural language input")
    parser.add_argument(
        "--slots", "-s", help='JSON slots: {"agent":"A","verb":"B","patient":"C"}'
    )
    parser.add_argument("--pid", "-p", type=int, help="Process ID")
    parser.add_argument("--expected", "-e", help="Expected result concept")
    parser.add_argument(
        "--ledger-dir", help="Directory for causal_graph.json and history.log"
    )

    args = parser.parse_args(argv)
    service = ProcessService(ledger_dir=args.ledger_dir)

    if args.command == "parse":
        if not args.text:
            _json_out({"error": "Missing --text"})
            return 1
        _json_out(service.parse(args.text))

    elif args.command == "encode":
        if not args.slots:
            _json_out({"error": "Missing --slots"})
            return 1
        slots = json.loads(args.slots)
        _json_out(service.encode(slots))

    elif args.command == "validate":
        if args.pid is None:
            _json_out({"error": "Missing --pid"})
            return 1
        ledger = service._ledger
        _json_out(ledger.validate(args.pid, expected_result=args.expected))

    elif args.command == "query":
        if not args.text:
            _json_out({"error": "Missing --text"})
            return 1
        _json_out(service.query(args.text, expected_result=args.expected))

    elif args.command == "append":
        if args.slots:
            slots = json.loads(args.slots)
            _json_out(service.append_slots(slots))
        elif args.text:
            _json_out(service.append_text(args.text))
        else:
            _json_out({"error": "Missing --slots or --text"})
            return 1

    elif args.command == "factor":
        if args.pid is None:
            _json_out({"error": "Missing --pid"})
            return 1
        _json_out(service.factor(args.pid))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
