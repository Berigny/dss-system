import asyncio
import hashlib
import os

import httpx


LOCAL_API = os.getenv("LOCAL_API", "")
CLOUD_API = os.getenv("CLOUD_API", "")
SYNC_LEDGER_ID = os.getenv("SYNC_LEDGER_ID", os.getenv("DEMO_LEDGER_ID", "LOAM"))
SYNC_CONTEXT_ID = os.getenv("SYNC_CONTEXT_ID", os.getenv("FRONTEND_CONTEXT_ID", "ctx:frontend:local"))
SYNC_PRINCIPAL_ID = os.getenv("SYNC_PRINCIPAL_ID", os.getenv("DEMO_OWNER_ID", "demo-user"))
SYNC_PRINCIPAL_TYPE = os.getenv("SYNC_PRINCIPAL_TYPE", "user")
SYNC_PEER_ID = os.getenv("SYNC_PEER_ID", "frontend-sync-daemon")
SYNC_INTERVAL_SECONDS = int(os.getenv("SYNC_INTERVAL_SECONDS", "60"))
SYNC_LIMIT = int(os.getenv("SYNC_LEDGER_LIMIT", "500"))


def _ledger_h64(ledger_id: str) -> str:
    digest = hashlib.sha256((ledger_id or "").encode("utf-8")).digest()
    return digest[:8].hex()


def _headers(ledger_id: str) -> dict[str, str]:
    return {
        "x-ledger-id": ledger_id,
        "x-ledger-id-h64": _ledger_h64(ledger_id),
        "x-context-id": SYNC_CONTEXT_ID,
        "x-principal-id": SYNC_PRINCIPAL_ID,
        "x-principal-type": SYNC_PRINCIPAL_TYPE,
        "Content-Type": "application/json",
    }


async def _pull_batch(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    ledger_id: str,
    peer_id: str,
    cursors: dict[str, int],
) -> tuple[list[dict], dict[str, int]]:
    payload = {
        "peer_id": peer_id,
        "ledger_id_h64": _ledger_h64(ledger_id),
        "cursors": cursors,
        "limit": max(1, min(SYNC_LIMIT, 500)),
    }
    resp = await client.post(f"{base_url}/sync/v0/pull", json=payload, headers=_headers(ledger_id))
    if resp.status_code != 200:
        raise RuntimeError(f"pull failed {base_url} ({resp.status_code}): {resp.text[:240]}")
    data = resp.json() if resp.content else {}
    items = data.get("items") if isinstance(data, dict) else []
    next_cursors = data.get("next_cursors") if isinstance(data, dict) else {}
    if not isinstance(items, list):
        items = []
    if not isinstance(next_cursors, dict):
        next_cursors = dict(cursors)
    return items, {str(k): int(v) for k, v in next_cursors.items() if str(k)}


async def _push_batch(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    ledger_id: str,
    peer_id: str,
    items: list[dict],
) -> dict:
    envelopes = [
        {"envelope_hex": str(item.get("envelope_hex") or ""), "allow_backfill": False}
        for item in items
        if str(item.get("envelope_hex") or "").strip()
    ]
    payload = {
        "peer_id": peer_id,
        "ledger_id_h64": _ledger_h64(ledger_id),
        "items": envelopes,
    }
    resp = await client.post(f"{base_url}/sync/v0/push", json=payload, headers=_headers(ledger_id))
    if resp.status_code != 200:
        raise RuntimeError(f"push failed {base_url} ({resp.status_code}): {resp.text[:240]}")
    return resp.json() if resp.content else {}


async def sync_once(
    *,
    local_api: str,
    cloud_api: str,
    ledger_id: str,
    cursors_local_to_cloud: dict[str, int],
    cursors_cloud_to_local: dict[str, int],
) -> tuple[dict[str, int], dict[str, int]]:
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            local_items, local_next = await _pull_batch(
                client=client,
                base_url=local_api,
                ledger_id=ledger_id,
                peer_id=f"{SYNC_PEER_ID}:local",
                cursors=cursors_local_to_cloud,
            )
            if local_items:
                result = await _push_batch(
                    client=client,
                    base_url=cloud_api,
                    ledger_id=ledger_id,
                    peer_id=f"{SYNC_PEER_ID}:to-cloud",
                    items=local_items,
                )
                accepted = int(result.get("accepted") or 0)
                duplicate = int(result.get("duplicate") or 0)
                quarantine = int(result.get("quarantine") or 0)
                print(
                    f"v0 local->cloud items={len(local_items)} accepted={accepted} duplicate={duplicate} quarantine={quarantine}"
                )
            else:
                print("v0 local->cloud items=0")

            cloud_items, cloud_next = await _pull_batch(
                client=client,
                base_url=cloud_api,
                ledger_id=ledger_id,
                peer_id=f"{SYNC_PEER_ID}:cloud",
                cursors=cursors_cloud_to_local,
            )
            if cloud_items:
                result = await _push_batch(
                    client=client,
                    base_url=local_api,
                    ledger_id=ledger_id,
                    peer_id=f"{SYNC_PEER_ID}:to-local",
                    items=cloud_items,
                )
                accepted = int(result.get("accepted") or 0)
                duplicate = int(result.get("duplicate") or 0)
                quarantine = int(result.get("quarantine") or 0)
                print(
                    f"v0 cloud->local items={len(cloud_items)} accepted={accepted} duplicate={duplicate} quarantine={quarantine}"
                )
            else:
                print("v0 cloud->local items=0")
            return local_next, cloud_next
        except Exception as exc:
            text = str(exc)
            if "unknown_ledger" not in text and "forbidden" not in text:
                raise
            print(f"v0 sync unavailable ({text}); falling back to legacy /sync/push diff mode")

    async with httpx.AsyncClient(timeout=30) as client:
        local_db = await get_all_keys(local_api, ledger_id)
        cloud_db = await get_all_keys(cloud_api, ledger_id)

        local_keys = set(local_db.keys())
        cloud_keys = set(cloud_db.keys())

        to_push = [local_db[k] for k in local_keys - cloud_keys]
        if to_push:
            resp = await client.post(
                f"{cloud_api}/sync/push",
                json={"entries": to_push},
                headers=_headers(ledger_id),
            )
            print(f"legacy local->cloud entries={len(to_push)} status={resp.status_code}")
        else:
            print("legacy local->cloud entries=0")

        to_pull = [cloud_db[k] for k in cloud_keys - local_keys]
        if to_pull:
            resp = await client.post(
                f"{local_api}/sync/push",
                json={"entries": to_pull},
                headers=_headers(ledger_id),
            )
            print(f"legacy cloud->local entries={len(to_pull)} status={resp.status_code}")
        else:
            print("legacy cloud->local entries=0")

    return cursors_local_to_cloud, cursors_cloud_to_local


async def get_all_keys(base_url: str, ledger_id: str) -> dict[str, dict]:
    """Best-effort legacy visibility probe; not used for v0 sync transport."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{base_url}/ledger/all",
            params={"limit": max(1, min(SYNC_LIMIT, 5000))},
            headers=_headers(ledger_id),
        )
        if resp.status_code != 200:
            return {}
        data = resp.json()
        entries = data.get("entries", []) if isinstance(data, dict) else []
        return {
            f"{e['key']['namespace']}:{e['key']['identifier']}": e
            for e in entries
            if isinstance(e, dict) and isinstance(e.get("key"), dict)
        }


async def sync_loop() -> None:
    print(
        f"Sync Daemon Started (ledger={SYNC_LEDGER_ID}, local={LOCAL_API}, cloud={CLOUD_API}, interval={SYNC_INTERVAL_SECONDS}s)"
    )
    cursors_local_to_cloud: dict[str, int] = {}
    cursors_cloud_to_local: dict[str, int] = {}
    while True:
        try:
            cursors_local_to_cloud, cursors_cloud_to_local = await sync_once(
                local_api=LOCAL_API,
                cloud_api=CLOUD_API,
                ledger_id=SYNC_LEDGER_ID,
                cursors_local_to_cloud=cursors_local_to_cloud,
                cursors_cloud_to_local=cursors_cloud_to_local,
            )
        except Exception as exc:
            print(f"Sync Error: {exc}")

        await asyncio.sleep(SYNC_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(sync_loop())
