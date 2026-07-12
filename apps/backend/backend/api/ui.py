"""
UI-generating endpoints that return HTML fragments for use with HTMX.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from backend.api.history_utils import history_sort_key
from backend.api.ui_utils import format_entry_as_html, transform_and_filter
from backend.services.ledger_service import LedgerService

router = APIRouter(prefix="/ui", tags=["ui"])
LOGGER = logging.getLogger(__name__)


@router.get("/history/{entity}", response_class=HTMLResponse)
async def get_chat_history_html(
    request: Request,
    entity: str,
    limit: int = 50,
):
    """
    Retrieve and render chat history for a specific entity as an HTML fragment.
    """
    service = LedgerService.from_request(request)

    if not entity.startswith("chat-"):
        entity = f"chat-{entity}"

    store = service.store
    
    try:
        # 1. Fetch raw entries from the database
        fetch_limit = max(limit * 20, limit)
        raw_entries = store.list_by_namespace(entity, limit=fetch_limit, reverse=True)
        
        # 2. Transform raw entries into a consistent "message" format
        filtered_entries = []
        for entry in raw_entries:
            meta = entry.state.metadata or {}
            if (
                meta.get("attachment")
                or meta.get("attachment_part")
                or meta.get("attachment_summary")
                or meta.get("role") == "attachment"
            ):
                continue
            filtered_entries.append(entry)
            if len(filtered_entries) >= limit:
                break
        messages = transform_and_filter(filtered_entries)
        messages.sort(key=history_sort_key)

        # 3. Build the HTML fragment
        html_fragments = [format_entry_as_html(msg) for msg in messages]
        
        # If no valid messages were found to display
        if not html_fragments:
            return "<p>No conversation history found for this entity.</p>"
            
        return "".join(html_fragments)

    except Exception as e:
        LOGGER.error(f"Failed to render HTML for ledger history: {e}", exc_info=e)
        # Return an error message as part of the HTML
        return f"""
        <div style="color: red; border: 1px solid red; padding: 10px;">
            <strong>Error:</strong> Could not load chat history.
            <p>{e}</p>
        </div>
        """
