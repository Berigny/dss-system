from __future__ import annotations

import logging

from backend.api.logging_utils import log_operation


def test_log_operation_renames_reserved_logrecord_keys() -> None:
    logger = logging.getLogger("test.log_operation")
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        with log_operation(logger, "admin_create_ledger", created=True) as ctx:
            ctx["created"] = True
            ctx["ledger"] = "gate-alpha"
    finally:
        logger.removeHandler(handler)

    assert records, "expected at least one log record"
    record = records[-1]
    assert getattr(record, "context_created", None) is True
    assert getattr(record, "ledger", None) == "gate-alpha"

