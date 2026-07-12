import asyncio
import os
from typing import Any

import httpx


LOCAL_API = os.getenv("LOCAL_API", "")
CLOUD_API = os.getenv("CLOUD_API", "")
SYNC_INTERVAL_SECONDS = int(os.getenv("SYNC_INTERVAL_SECONDS", "60"))
SYNC_BATCH_LIMIT = int(os.getenv("SYNC_BATCH_LIMIT", "200"))
SYNC_LEDGER_ID_H64 = (os.getenv("SYNC_LEDGER_ID_H64", "").strip().lower() or "0000000000000000")
SYNC_PEER_ID = os.getenv("SYNC_PEER_ID", "middleware-sync-daemon")


async def _pull_v0(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    cursors: dict[str, int],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    resp = await client.post(
        f"{base_url}/sync/v0/pull",
        json={
            "peer_id": SYNC_PEER_ID,
            "ledger_id_h64": SYNC_LEDGER_ID_H64,
            "cursors": cursors,
            "limit": SYNC_BATCH_LIMIT,
        },
    )
    if resp.status_code != 200:
        raise RuntimeError(f"pull failed ({base_url}): {resp.status_code} {resp.text}")
    body = resp.json() if resp.content else {}
    items_raw: list[Any]
    if isinstance(body, dict) and isinstance(body.get("items"), list):
        items_raw = body.get("items", [])
    else:
        items_raw = []
    items: list[dict[str, Any]] = [item for item in items_raw if isinstance(item, dict)]

    next_cursors_raw: dict[str, Any]
    if isinstance(body, dict) and isinstance(body.get("next_cursors"), dict):
        next_cursors_raw = body.get("next_cursors", {})
    else:
        next_cursors_raw = dict(cursors)
    normalized_cursors: dict[str, int] = {}
    for key, value in next_cursors_raw.items():
        try:
            normalized_cursors[str(key)] = int(value)
        except Exception:
            continue
    return items, normalized_cursors


async def _push_v0(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    push_items = []
    for item in items:
        envelope_hex = str(item.get("envelope_hex") or "").strip()
        if not envelope_hex:
            continue
        push_items.append({"envelope_hex": envelope_hex, "allow_backfill": False})

    if not push_items:
        return {"status": "ok", "accepted": 0, "duplicate": 0, "quarantine": 0}

    resp = await client.post(
        f"{base_url}/sync/v0/push",
        json={
            "peer_id": SYNC_PEER_ID,
            "ledger_id_h64": SYNC_LEDGER_ID_H64,
            "items": push_items,
        },
    )
    if resp.status_code != 200:
        raise RuntimeError(f"push failed ({base_url}): {resp.status_code} {resp.text}")
    body = resp.json() if resp.content else {}
    return body if isinstance(body, dict) else {}


async def sync_loop() -> None:
    print(
        "Sync Daemon Started (v0)",
        {
            "local": LOCAL_API,
            "cloud": CLOUD_API,
            "ledger_id_h64": SYNC_LEDGER_ID_H64,
            "interval_s": SYNC_INTERVAL_SECONDS,
            "batch_limit": SYNC_BATCH_LIMIT,
        },
    )

    cursors_local_to_cloud: dict[str, int] = {}
    cursors_cloud_to_local: dict[str, int] = {}

    timeout = httpx.Timeout(30.0)
    while True:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                # Local -> Cloud
                local_items, local_next = await _pull_v0(
                    client=client,
                    base_url=LOCAL_API,
                    cursors=cursors_local_to_cloud,
                )
                if local_items:
                    result = await _push_v0(client=client, base_url=CLOUD_API, items=local_items)
                    quarantined = int(result.get("quarantine") or 0)
                    accepted = int(result.get("accepted") or 0)
                    duplicates = int(result.get("duplicate") or 0)
                    print(
                        "sync local->cloud",
                        {
                            "pulled": len(local_items),
                            "accepted": accepted,
                            "duplicate": duplicates,
                            "quarantine": quarantined,
                        },
                    )
                    # Advance source cursor only when destination accepted cleanly.
                    if quarantined == 0:
                        cursors_local_to_cloud = local_next

                # Cloud -> Local
                cloud_items, cloud_next = await _pull_v0(
                    client=client,
                    base_url=CLOUD_API,
                    cursors=cursors_cloud_to_local,
                )
                if cloud_items:
                    result = await _push_v0(client=client, base_url=LOCAL_API, items=cloud_items)
                    quarantined = int(result.get("quarantine") or 0)
                    accepted = int(result.get("accepted") or 0)
                    duplicates = int(result.get("duplicate") or 0)
                    print(
                        "sync cloud->local",
                        {
                            "pulled": len(cloud_items),
                            "accepted": accepted,
                            "duplicate": duplicates,
                            "quarantine": quarantined,
                        },
                    )
                    if quarantined == 0:
                        cursors_cloud_to_local = cloud_next

        except Exception as exc:
            print(f"Sync Error: {exc}")

        await asyncio.sleep(SYNC_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(sync_loop())
