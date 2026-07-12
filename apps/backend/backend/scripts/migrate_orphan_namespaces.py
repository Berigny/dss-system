#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from rocksdict import Rdict

from backend.fieldx_kernel.models import LedgerEntry, LedgerKey
from backend.fieldx_kernel.substrate.ledger_store_v2 import LedgerStoreV2

DEFAULT_TARGET_NAMESPACE = "chat-demo"
DEFAULT_CLOUD_BASE_URL = os.getenv("DEFAULT_CLOUD_BASE_URL", "")


def _safe_slug(text: str) -> str:
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return h


def _migrated_identifier(source_namespace: str, source_identifier: str) -> str:
    return f"MIGR-{_safe_slug(source_namespace)}-{source_identifier}"


def _strip_chain_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(metadata or {})
    cleaned.pop("ledger_hash", None)
    cleaned.pop("ledger_prev_hash", None)
    return cleaned


def _build_migrated_entry(
    *,
    entry: LedgerEntry,
    source_namespace: str,
    target_namespace: str,
    mode: str,
) -> LedgerEntry:
    meta = _strip_chain_metadata(dict(entry.state.metadata or {}))
    migration = dict(meta.get("migration") or {})
    migration.update(
        {
            "source_namespace": source_namespace,
            "source_identifier": entry.key.identifier,
            "source_coordinate": entry.key.as_path(),
            "target_namespace": target_namespace,
            "migrated_at": datetime.now(timezone.utc).isoformat(),
            "mode": mode,
        }
    )
    meta["migration"] = migration
    meta["migrated_from_namespace"] = source_namespace

    new_entry = copy.deepcopy(entry)
    new_entry.key = LedgerKey(
        namespace=target_namespace,
        identifier=_migrated_identifier(source_namespace, entry.key.identifier),
    )
    new_entry.state.metadata = meta
    return new_entry


def _load_cloud_namespace_entries(client: httpx.Client, base_url: str, namespace: str) -> list[dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/ledger/all"
    resp = client.get(url, params={"namespace": namespace, "limit": 5000}, timeout=30.0)
    resp.raise_for_status()
    payload = resp.json() if resp.content else {}
    entries = payload.get("entries") if isinstance(payload, dict) else []
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def _cloud_existing_ids(client: httpx.Client, base_url: str, target_namespace: str) -> set[str]:
    entries = _load_cloud_namespace_entries(client, base_url, target_namespace)
    out: set[str] = set()
    for entry in entries:
        key = entry.get("key") if isinstance(entry, dict) else None
        if isinstance(key, dict):
            identifier = str(key.get("identifier") or "").strip()
            if identifier:
                out.add(identifier)
    return out


def migrate_cloud(
    *,
    base_url: str,
    source_namespaces: list[str],
    target_namespace: str,
    dry_run: bool,
) -> dict[str, Any]:
    summary: dict[str, Any] = {"mode": "cloud", "base_url": base_url, "target": target_namespace, "sources": {}}
    with httpx.Client(verify=False, timeout=30.0) as client:
        existing_ids = _cloud_existing_ids(client, base_url, target_namespace)

        for source_ns in source_namespaces:
            source_entries = _load_cloud_namespace_entries(client, base_url, source_ns)
            src_total = len(source_entries)
            written = 0
            skipped_existing = 0
            failed = 0

            for raw in source_entries:
                key = raw.get("key") if isinstance(raw, dict) else None
                state = raw.get("state") if isinstance(raw, dict) else None
                if not isinstance(key, dict) or not isinstance(state, dict):
                    failed += 1
                    continue

                source_identifier = str(key.get("identifier") or "").strip()
                if not source_identifier:
                    failed += 1
                    continue

                migrated_id = _migrated_identifier(source_ns, source_identifier)
                if migrated_id in existing_ids:
                    skipped_existing += 1
                    continue

                payload = {
                    "key": {
                        "namespace": target_namespace,
                        "identifier": migrated_id,
                    },
                    "state": {
                        "coordinates": dict(state.get("coordinates") or {}),
                        "phase": state.get("phase"),
                        "metadata": _strip_chain_metadata(dict(state.get("metadata") or {})),
                    },
                    "created_at": raw.get("created_at"),
                    "notes": raw.get("notes"),
                    "pinned": bool(raw.get("pinned", False)),
                }
                meta = payload["state"]["metadata"]
                migration = dict(meta.get("migration") or {})
                migration.update(
                    {
                        "source_namespace": source_ns,
                        "source_identifier": source_identifier,
                        "source_coordinate": f"{source_ns}:{source_identifier}",
                        "target_namespace": target_namespace,
                        "migrated_at": datetime.now(timezone.utc).isoformat(),
                        "mode": "cloud_api",
                    }
                )
                meta["migration"] = migration
                meta["migrated_from_namespace"] = source_ns

                if dry_run:
                    written += 1
                    continue

                try:
                    wr = client.post(f"{base_url.rstrip('/')}/ledger/write", json=payload, timeout=30.0)
                    wr.raise_for_status()
                    existing_ids.add(migrated_id)
                    written += 1
                except Exception:
                    failed += 1

            summary["sources"][source_ns] = {
                "source_entries": src_total,
                "written": written,
                "skipped_existing": skipped_existing,
                "failed": failed,
            }

    return summary


def migrate_local(
    *,
    db_path: Path,
    source_namespaces: list[str],
    target_namespace: str,
    dry_run: bool,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "mode": "local",
        "db_path": str(db_path),
        "target": target_namespace,
        "sources": {},
    }

    db = Rdict(str(db_path))
    store = LedgerStoreV2(db)

    existing_ids = {
        entry.key.identifier
        for entry in store.list_by_namespace(target_namespace, limit=None, reverse=False)
    }

    for source_ns in source_namespaces:
        source_entries = store.list_by_namespace(source_ns, limit=None, reverse=False)
        src_total = len(source_entries)
        written = 0
        skipped_existing = 0
        failed = 0

        for entry in source_entries:
            migrated_id = _migrated_identifier(source_ns, entry.key.identifier)
            if migrated_id in existing_ids:
                skipped_existing += 1
                continue

            try:
                migrated = _build_migrated_entry(
                    entry=entry,
                    source_namespace=source_ns,
                    target_namespace=target_namespace,
                    mode="local_db",
                )
                if dry_run:
                    written += 1
                    continue
                store.write(migrated)
                existing_ids.add(migrated_id)
                written += 1
            except Exception:
                failed += 1

        summary["sources"][source_ns] = {
            "source_entries": src_total,
            "written": written,
            "skipped_existing": skipped_existing,
            "failed": failed,
        }

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate orphan namespaces into a target namespace.")
    parser.add_argument("--target", default=DEFAULT_TARGET_NAMESPACE)
    parser.add_argument("--cloud-base-url", default=DEFAULT_CLOUD_BASE_URL)
    parser.add_argument("--local-db", default="./data/ledger.db")
    parser.add_argument("--source", action="append", required=True, help="Source namespace (repeatable)")
    parser.add_argument("--skip-cloud", action="store_true")
    parser.add_argument("--skip-local", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sources = [s.strip() for s in args.source if s and s.strip()]
    if not sources:
        raise SystemExit("No source namespaces provided")

    out: dict[str, Any] = {
        "target": args.target,
        "sources": sources,
        "dry_run": bool(args.dry_run),
        "results": {},
    }

    if not args.skip_cloud:
        out["results"]["cloud"] = migrate_cloud(
            base_url=args.cloud_base_url,
            source_namespaces=sources,
            target_namespace=args.target,
            dry_run=bool(args.dry_run),
        )

    if not args.skip_local:
        out["results"]["local"] = migrate_local(
            db_path=Path(args.local_db),
            source_namespaces=sources,
            target_namespace=args.target,
            dry_run=bool(args.dry_run),
        )

    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
