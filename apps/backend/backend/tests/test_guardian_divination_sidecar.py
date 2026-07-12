from backend.fieldx_kernel.guardian import _apply_teleology_to_latest_entry
from backend.fieldx_kernel.models import ContinuousState, LedgerEntry, LedgerKey
from backend.fieldx_kernel.substrate import LedgerStoreV2


def test_teleology_sidecar_persists_configurational_foresight() -> None:
    store = LedgerStoreV2({})
    entry = LedgerEntry(
        key=LedgerKey(namespace="chat-demo", identifier="WX-001"),
        state=ContinuousState({}, "chat", {"kind": "chat"}),
    )
    store.write(entry)

    foresight = {
        "quality": "favourable",
        "advisory_score": 0.82,
        "advisory_only": True,
        "veto_allowed": False,
    }
    _apply_teleology_to_latest_entry(
        store,
        entity="chat-demo",
        teleology_alignment=0.91,
        configurational_foresight=foresight,
    )

    rows = store.list_by_namespace("chat-demo", limit=5)
    sidecars = [row for row in rows if row.key.identifier.startswith("TEL-")]
    assert sidecars
    metadata = sidecars[0].state.metadata
    assert metadata.get("teleology_alignment") == 0.91
    assert metadata.get("configurational_foresight") == foresight
