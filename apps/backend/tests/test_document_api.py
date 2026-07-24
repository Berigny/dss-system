"""Smoke tests for the Document surface API contract."""

import importlib.util
import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


spec = importlib.util.spec_from_file_location("document_api", REPO_ROOT / "backend" / "api" / "document.py")
document_api = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(document_api)


def test_router_has_document_routes() -> None:
    routes = [route.path for route in document_api.router.routes]
    assert "/v1/documents" in routes
    assert "/v1/documents/{doc_id}" in routes
    assert "/v1/documents/{doc_id}/chunks" in routes
    assert "/v1/documents/chunks/{chunk_coord}/reprompt" in routes
    assert "/v1/documents/chunks/{chunk_coord}" in routes
    assert "/v1/documents/chunks/{chunk_coord}/versions" in routes
    assert "/v1/documents/{doc_id}/export" in routes


def _fake_request(*, principal: str | None = "did:web:test", db: dict | None = None) -> MagicMock:
    request = MagicMock()
    request.app.state.db = db if db is not None else {}
    request.state = MagicMock()
    if principal:
        request.state.auth_claim_principal_did = principal
    return request


@pytest.fixture
def fresh_db() -> dict:
    return {}


@pytest.fixture
def patched_generate():
    with patch.object(document_api, "_generate_text", new=AsyncMock(return_value="generated text")):
        yield


@pytest.fixture
def patched_surface():
    with patch.object(document_api, "assert_surface_ledger_access", return_value=None):
        yield


def test_coord_parse_doc_and_meta() -> None:
    from backend.utils.coord import normalise_coord
    doc = normalise_coord("DOC-doc1-C1")
    assert doc["kind"] == "document"
    meta = normalise_coord("MD-DocState-doc1-C1")
    assert meta["kind"] == "meta"


@pytest.mark.usefixtures("patched_generate", "patched_surface")
def test_create_document_and_chunk(fresh_db: dict) -> None:
    request = _fake_request(db=fresh_db)
    import asyncio
    doc = asyncio.run(document_api.create_document(request, document_api.CreateDocumentRequest(title="Test")))
    assert "doc_id" in doc

    chunk = asyncio.run(
        document_api.create_chunk(request, doc["doc_id"], document_api.CreateChunkRequest(prompt="hello"))
    )
    assert chunk["chunk_coord"].startswith("DOC-")
    assert chunk["version_coord"].endswith("-T001")
    assert chunk["text"] == "generated text"


@pytest.mark.usefixtures("patched_generate", "patched_surface")
def test_reprompt_appends_version(fresh_db: dict) -> None:
    request = _fake_request(db=fresh_db)
    import asyncio
    doc = asyncio.run(document_api.create_document(request, document_api.CreateDocumentRequest()))
    chunk = asyncio.run(
        document_api.create_chunk(request, doc["doc_id"], document_api.CreateChunkRequest(prompt="hello"))
    )
    chunk_coord = chunk["chunk_coord"]

    with patch.object(document_api, "_generate_text", new=AsyncMock(return_value="reprompted text")):
        result = asyncio.run(
            document_api.reprompt_chunk(request, chunk_coord, document_api.RepromptChunkRequest(prompt="again"))
        )
    assert result["version_coord"].endswith("-T002")


@pytest.mark.usefixtures("patched_generate", "patched_surface")
def test_patch_selection_refused_if_out_of_bounds(fresh_db: dict) -> None:
    request = _fake_request(db=fresh_db)
    import asyncio
    doc = asyncio.run(document_api.create_document(request, document_api.CreateDocumentRequest()))
    chunk = asyncio.run(
        document_api.create_chunk(request, doc["doc_id"], document_api.CreateChunkRequest(prompt="hello"))
    )
    chunk_coord = chunk["chunk_coord"]

    with pytest.raises(document_api.HTTPException) as exc_info:
        asyncio.run(
            document_api.patch_chunk(
                request,
                chunk_coord,
                document_api.PatchChunkRequest(sel_start=0, sel_end=99999),
            )
        )
    assert exc_info.value.status_code == 400


@pytest.mark.usefixtures("patched_generate", "patched_surface")
def test_delete_sets_visible_false(fresh_db: dict) -> None:
    request = _fake_request(db=fresh_db)
    import asyncio
    doc = asyncio.run(document_api.create_document(request, document_api.CreateDocumentRequest()))
    chunk = asyncio.run(
        document_api.create_chunk(request, doc["doc_id"], document_api.CreateChunkRequest(prompt="hello"))
    )
    chunk_coord = chunk["chunk_coord"]

    asyncio.run(
        document_api.patch_chunk(request, chunk_coord, document_api.PatchChunkRequest(visible=False))
    )

    projection = asyncio.run(document_api.get_document(request, doc["doc_id"]))
    assert len(projection["chunks"]) == 0


@pytest.mark.usefixtures("patched_generate", "patched_surface")
def test_export_is_deterministic(fresh_db: dict) -> None:
    request = _fake_request(db=fresh_db)
    import asyncio
    doc = asyncio.run(document_api.create_document(request, document_api.CreateDocumentRequest()))
    asyncio.run(
        document_api.create_chunk(request, doc["doc_id"], document_api.CreateChunkRequest(prompt="hello"))
    )

    export1 = asyncio.run(document_api.export_document(request, doc["doc_id"]))
    export2 = asyncio.run(document_api.export_document(request, doc["doc_id"]))
    assert export1["text"] == export2["text"]


@pytest.mark.usefixtures("patched_generate", "patched_surface")
def test_reorder_emits_event(fresh_db: dict) -> None:
    request = _fake_request(db=fresh_db)
    import asyncio
    doc = asyncio.run(document_api.create_document(request, document_api.CreateDocumentRequest()))
    chunk = asyncio.run(
        document_api.create_chunk(request, doc["doc_id"], document_api.CreateChunkRequest(prompt="hello"))
    )
    chunk_coord = chunk["chunk_coord"]

    asyncio.run(
        document_api.patch_chunk(request, chunk_coord, document_api.PatchChunkRequest(position=5))
    )

    state = document_api._load_state(fresh_db)
    events = state[doc["doc_id"]]["events"]
    position_events = [e for e in events if e.get("type") == "state" and "position" in e]
    assert len(position_events) >= 1
