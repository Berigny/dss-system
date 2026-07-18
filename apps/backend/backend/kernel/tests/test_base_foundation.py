"""Tests for backend/kernel/base_foundation.py."""

from __future__ import annotations

import json

import pytest

from backend.kernel import constants
from backend.kernel.base_foundation import (
    BaseFoundationService,
    MissingFoundationError,
    bootstrap_test_ledger,
)


@pytest.fixture
def db() -> dict[bytes, bytes]:
    return {}


@pytest.fixture
def service(db: dict[bytes, bytes]) -> BaseFoundationService:
    return BaseFoundationService(db)


def test_new_ledger_has_no_foundation(service: BaseFoundationService) -> None:
    assert service.has_base_foundation("prov-1") is False


def test_write_foundation_creates_record(service: BaseFoundationService) -> None:
    record = service.write_foundation("prov-1")
    assert service.has_base_foundation("prov-1") is True
    assert record["provision_id"] == "prov-1"
    assert record["version"] == "1.1"
    assert record["ksr_version"] == constants.KSR_VERSION
    assert "public" in record
    assert record["public"]["provision_id"] == "prov-1"


def test_read_foundation_round_trip(service: BaseFoundationService) -> None:
    written = service.write_foundation("prov-1")
    read = service.read_foundation("prov-1")
    assert read == written


def test_deterministic_except_timestamp(service: BaseFoundationService) -> None:
    a = service.build_foundation("prov-1")
    b = service.build_foundation("prov-1")
    # Timestamps and salts differ by construction; everything else must match.
    a["origin_timestamp"] = ""
    b["origin_timestamp"] = ""
    a["public"]["origin_timestamp"] = ""
    b["public"]["origin_timestamp"] = ""
    a["public"]["origin_salt"] = ""
    b["public"]["origin_salt"] = ""
    assert a == b


def test_require_base_foundation_raises_when_missing(service: BaseFoundationService) -> None:
    with pytest.raises(MissingFoundationError):
        service.require_base_foundation("prov-missing")


def test_require_base_foundation_passes_when_present(service: BaseFoundationService) -> None:
    service.write_foundation("prov-ok")
    service.require_base_foundation("prov-ok")


def test_bootstrap_test_ledger_helper() -> None:
    db: dict[bytes, bytes] = {}
    record = bootstrap_test_ledger(db, "test-prov")
    assert record["provision_id"] == "test-prov"
    assert BaseFoundationService(db).has_base_foundation("test-prov") is True


def test_foundation_includes_kernel_cube_and_patches(service: BaseFoundationService) -> None:
    record = service.build_foundation("prov-1")
    public = record["public"]
    assert public["kernel_cube"]["cube_id"] == constants.LATTICE_CUBE_ID
    assert public["kernel_cube"]["total_nodes"] == 27
    assert len(public["patch_registry"]["patches"]) == 10
    assert public["checksum_336"]["value"] == 336


def test_foundation_includes_cross_domain_registry(service: BaseFoundationService) -> None:
    record = service.build_foundation("prov-1")
    public = record["public"]
    assert "cross_domain_registry" in public
    cdr = public["cross_domain_registry"]
    assert "version" in cdr
    assert "domains" in cdr
    # Core-only graceful degradation: domains may be empty when the runtime loads
    # ksr-core without the domains pack. Presence of the registry is sufficient.


def test_has_base_foundation_false_when_cross_domain_registry_missing(service: BaseFoundationService) -> None:
    record = service.write_foundation("prov-1")
    # Simulate a legacy record that lacks the cross-domain registry.
    record["public"].pop("cross_domain_registry", None)
    service._db[service._foundation_key("prov-1")] = (
        json.dumps(record, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    assert service.has_base_foundation("prov-1") is False


def test_foundation_record_has_private_layer_when_population_present(service: BaseFoundationService) -> None:
    record = service.build_foundation("prov-1")
    # The private layer is loaded from .ksr/Kernel/ledger_foundation.json when
    # that file exists; the test repo includes it.
    assert "private" in record
    assert "lattice" in record["private"]
    assert "patches" in record["private"]
    assert "values" in record["private"]
    assert "xdomain" in record["private"]


def test_ledger_service_refuses_write_without_foundation_when_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DSS_REQUIRE_BASE_FOUNDATION", "1")
    import importlib
    from datetime import datetime, timezone

    from backend.fieldx_kernel.models import ContinuousState, LedgerEntry, LedgerKey
    from backend.kernel.base_foundation import MissingFoundationError
    from backend.services import ledger_service

    importlib.reload(ledger_service)
    svc = ledger_service.LedgerService({})
    entry = LedgerEntry(
        key=LedgerKey(namespace="test-ns", identifier="1"),
        state=ContinuousState(),
        created_at=datetime.now(timezone.utc),
    )
    with pytest.raises(MissingFoundationError):
        svc.write_entry(entry)
