# E6 Ledger and Sync Specs

This folder contains draft normative specs for lightweight edge/offline-online
ledger operation and cross-repo synchronization.

Documents:

- `e6-header-v0.md`: fixed 128-bit hot-path header (already implemented).
- `e6-envelope-v0.md`: signed/MACed envelope that carries identity and proof.
- `e6-sync-v0.md`: pull/push protocol for multi-ledger, multi-repo sync.

Design intent:

- Keep edge hot path at 16-byte parse and route time.
- Move identity/provenance/authorization to an attached envelope.
- Make local/offline commits first-class and cloud sync eventual.
