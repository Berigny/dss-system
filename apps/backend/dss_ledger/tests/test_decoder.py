"""PID decoder tests."""

from __future__ import annotations

import pytest

from dss_ledger.schema import LedgerSchema
from dss_ledger.src.decoder import LedgerDecoder
from dss_ledger.src.encoder import LedgerEncoder


def test_decoder_round_trip(schema: LedgerSchema):
    encoder = LedgerEncoder(schema)
    decoder = LedgerDecoder(schema)
    encoded = encoder.encode_process(
        "autonomy", "action", "mastery", result="potential", context="context"
    )
    decoded = decoder.factor(encoded["pid"])
    assert decoded["canonical"] == encoded["canonical"]
    assert decoded["slots"] == encoded["slots"]


def test_decoder_missing_slot_raises(schema: LedgerSchema):
    decoder = LedgerDecoder(schema)
    # 2^13 is a valid base-2 exponent but missing verb/patient.
    with pytest.raises(ValueError, match="missing required slot"):
        decoder.factor(2 ** 13)


def test_decoder_extraneous_factor_raises(schema: LedgerSchema):
    decoder = LedgerDecoder(schema)
    with pytest.raises(ValueError, match="factors outside slot bases"):
        decoder.factor(2 ** 13 * 83)
