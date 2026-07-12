# DSS-CP-GOV-v1.0.0-alpha
"""Pure, side-effect-free impact calculator for Control Plane connection removal."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from backend.governance.ontology import ONTOLOGY, map_legacy_relationship


@dataclass
class ImpactReport:
    entity_type: str
    entity_id: str
    ledger_id: str
    broken_relations: list[str] = field(default_factory=list)
    affected_principals: list[str] = field(default_factory=list)
    affected_surfaces: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    affected_ledgers: list[str] = field(default_factory=list)
    orphaned_surfaces: list[str] = field(default_factory=list)
    critical_warnings: list[str] = field(default_factory=list)

    def to_deterministic_json(self) -> str:
        """Return a deterministic JSON serialization for confirmation-token hashing."""

        def sort_value(value: Any) -> Any:
            if isinstance(value, set):
                return sorted(value)
            if isinstance(value, dict):
                return {k: sort_value(v) for k, v in sorted(value.items())}
            if isinstance(value, list):
                return [sort_value(v) for v in value]
            return value

        return json.dumps(
            {
                "entity_type": self.entity_type,
                "entity_id": self.entity_id,
                "ledger_id": self.ledger_id,
                "broken_relations": sort_value(self.broken_relations),
                "affected_principals": sort_value(self.affected_principals),
                "affected_surfaces": sort_value(self.affected_surfaces),
                "affected_ledgers": sort_value(self.affected_ledgers),
                "orphaned_surfaces": sort_value(self.orphaned_surfaces),
                "critical_warnings": sort_value(self.critical_warnings),
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    @property
    def confirmation_token(self) -> str:
        return hashlib.sha256(self.to_deterministic_json().encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "ledger_id": self.ledger_id,
            "broken_relations": self.broken_relations,
            "affected_principals": self.affected_principals,
            "affected_surfaces": self.affected_surfaces,
            "affected_ledgers": self.affected_ledgers,
            "orphaned_surfaces": self.orphaned_surfaces,
            "critical_warnings": self.critical_warnings,
        }


def _id(*parts: str) -> str:
    return "::".join(str(p).strip() for p in parts if str(p).strip())


def _normalize_record(record: Any) -> dict[str, Any]:
    if isinstance(record, dict):
        return record
    return {}


def _entity_subtype(entity_type: str, entity_id: str, principals: dict[str, Any]) -> str:
    if entity_type != "principal":
        return ""
    principal = _normalize_record(principals.get(entity_id))
    metadata = _normalize_record(principal.get("metadata"))
    return str(metadata.get("actor_type") or "").strip().lower()


def _trust_class(principal_id: str, principals: dict[str, Any]) -> str:
    principal = _normalize_record(principals.get(principal_id))
    standing = _normalize_record(principal.get("standing_view"))
    return str(standing.get("trust_class") or "").strip().upper()


def _canonical_relationships(relationships: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize and map legacy relationship types into ontology form."""
    canonical: list[dict[str, Any]] = []
    for record in relationships.values():
        row = _normalize_record(record)
        subject_type = str(row.get("subject_entity_type") or "").strip().lower()
        subject_id = str(row.get("subject_entity_id") or "").strip()
        object_type = str(row.get("object_entity_type") or "").strip().lower()
        object_id = str(row.get("object_entity_id") or "").strip()
        rel_type = str(row.get("relationship_type") or "").strip().lower()
        if not subject_type or not subject_id or not object_type or not object_id or not rel_type:
            continue
        mapped_type, mapped_sub_type, mapped_sub_id, mapped_obj_type, mapped_obj_id = map_legacy_relationship(
            rel_type, subject_type, subject_id, object_type, object_id
        )
        canonical.append(
            {
                "relationship_id": str(row.get("relationship_id") or _id(mapped_sub_type, mapped_sub_id, mapped_obj_type, mapped_obj_id)),
                "relationship_type": mapped_type,
                "subject_type": mapped_sub_type,
                "subject_id": mapped_sub_id,
                "object_type": mapped_obj_type,
                "object_id": mapped_obj_id,
                "enabled_state": str(row.get("enabled_state") or "enabled").strip().lower(),
                "start_at": str(row.get("start_at") or row.get("start_date") or "").strip() or None,
                "end_at": str(row.get("end_at") or row.get("end_date") or "").strip() or None,
            }
        )
    return canonical


def _is_enabled(record: dict[str, Any]) -> bool:
    if record.get("enabled_state") == "disabled":
        return False
    return True


def _member_of_graph(canonical: list[dict[str, Any]]) -> dict[str, set[str]]:
    """entity_id -> set of ledger_ids where entity is a member."""
    graph: dict[str, set[str]] = {}
    for rel in canonical:
        if rel["relationship_type"] != "member_of":
            continue
        if not _is_enabled(rel):
            continue
        entity_id = rel["subject_id"]
        ledger_id = rel["object_id"]
        graph.setdefault(entity_id, set()).add(ledger_id)
    return graph


def _links_to_graph(canonical: list[dict[str, Any]]) -> dict[str, set[str]]:
    """ledger_id -> set of linked ledger_ids (bidirectional)."""
    graph: dict[str, set[str]] = {}
    for rel in canonical:
        if rel["relationship_type"] != "links_to":
            continue
        if not _is_enabled(rel):
            continue
        a = rel["subject_id"]
        b = rel["object_id"]
        graph.setdefault(a, set()).add(b)
        graph.setdefault(b, set()).add(a)
    return graph


def _hosts_graph(canonical: list[dict[str, Any]]) -> dict[str, set[str]]:
    """surface_id -> set of hosted principal_ids."""
    graph: dict[str, set[str]] = {}
    for rel in canonical:
        if rel["relationship_type"] != "hosts":
            continue
        if not _is_enabled(rel):
            continue
        surface_id = rel["subject_id"]
        principal_id = rel["object_id"]
        graph.setdefault(surface_id, set()).add(principal_id)
    return graph


def _access_grants(canonical: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
    """Set of (surface_id or empty, principal_id or empty, ledger_id) access grants."""
    grants: set[tuple[str, str, str]] = set()
    for rel in canonical:
        if rel["relationship_type"] != "access_grant":
            continue
        if not _is_enabled(rel):
            continue
        from_type = rel["subject_type"]
        from_id = rel["subject_id"]
        ledger_id = rel["object_id"]
        if from_type == "surface":
            grants.add((from_id, "", ledger_id))
        elif from_type == "principal":
            grants.add(("", from_id, ledger_id))
    return grants


def _held_entities(
    organisation_id: str,
    canonical: list[dict[str, Any]],
    principals: dict[str, Any],
) -> set[str]:
    """Recursively collect all principal IDs held by an organisation via 'holds'."""
    held: set[str] = set()
    stack = [organisation_id]
    visited = set()
    while stack:
        current = stack.pop()
        if current in visited:
            continue
        visited.add(current)
        for rel in canonical:
            if rel["relationship_type"] != "holds":
                continue
            if not _is_enabled(rel):
                continue
            if rel["subject_id"] == current and rel["object_type"] == "principal":
                child = rel["object_id"]
                held.add(child)
                stack.append(child)
    return held


def _linked_ledgers(ledger_id: str, links_to: dict[str, set[str]]) -> set[str]:
    """Transitively collect all ledgers linked to ledger_id."""
    linked: set[str] = set()
    stack = [ledger_id]
    visited = {ledger_id}
    while stack:
        current = stack.pop()
        for neighbor in links_to.get(current, set()):
            if neighbor in visited:
                continue
            visited.add(neighbor)
            linked.add(neighbor)
            stack.append(neighbor)
    return linked


def _has_access_to_ledger(
    principal_id: str,
    ledger_id: str,
    canonical: list[dict[str, Any]],
    member_of: dict[str, set[str]],
    hosts: dict[str, set[str]],
    exclude_membership: tuple[str, str] | None = None,
) -> bool:
    """Return True if principal has a valid path to ledger, excluding a specific membership."""
    # Direct membership
    memberships = set(member_of.get(principal_id, set()))
    if exclude_membership:
        memberships.discard(exclude_membership[1])
    if ledger_id in memberships:
        return True

    # Surface-hosted membership
    for surface_id, hosted in hosts.items():
        if principal_id not in hosted:
            continue
        for rel in canonical:
            if rel["relationship_type"] != "member_of":
                continue
            if not _is_enabled(rel):
                continue
            if rel["subject_type"] == "surface" and rel["subject_id"] == surface_id and rel["object_id"] == ledger_id:
                return True

    # Organisation-held indirect membership
    for rel in canonical:
        if rel["relationship_type"] != "holds":
            continue
        if not _is_enabled(rel):
            continue
        if rel["object_type"] == "principal" and rel["object_id"] == principal_id:
            if _has_access_to_ledger(rel["subject_id"], ledger_id, canonical, member_of, hosts, exclude_membership):
                return True

    return False


def calculate_removal_impact(
    entity_type: str,
    entity_id: str,
    ledger_id: str,
    *,
    relationships: dict[str, Any],
    surfaces: dict[str, Any],
    principals: dict[str, Any],
    ledgers: dict[str, Any],
    bindings: dict[str, Any] | None = None,
    caller_principal_id: str | None = None,
) -> ImpactReport:
    """Compute the transitive impact of removing `entity_id` from `ledger_id`.

    This function is pure: it does not read from or write to the database.
    """
    entity_type = str(entity_type or "").strip().lower()
    entity_id = str(entity_id or "").strip()
    ledger_id = str(ledger_id or "").strip()

    report = ImpactReport(entity_type=entity_type, entity_id=entity_id, ledger_id=ledger_id)

    canonical = _canonical_relationships(relationships)
    member_of = _member_of_graph(canonical)
    links_to = _links_to_graph(canonical)
    hosts = _hosts_graph(canonical)
    grants = _access_grants(canonical)

    # 1. Direct impact
    if entity_type == "ledger":
        report.broken_relations.append(_id("links_to", entity_id, "ledger", ledger_id))
    else:
        report.broken_relations.append(_id("member_of", entity_id, "ledger", ledger_id))

    # 2. Affected principals
    affected_principals: set[str] = set()
    if entity_type == "principal":
        affected_principals.add(entity_id)
        subtype = _entity_subtype(entity_type, entity_id, principals)
        if subtype == "organisation":
            affected_principals.update(_held_entities(entity_id, canonical, principals))
    elif entity_type == "surface":
        # Surface removal affects all principals hosted on it.
        affected_principals.update(hosts.get(entity_id, set()))

    # 3. Affected ledgers (direct + linked federation)
    affected_ledgers: set[str] = {ledger_id}
    linked = _linked_ledgers(ledger_id, links_to)
    for linked_ledger in linked:
        if linked_ledger == ledger_id:
            continue
        if entity_type == "principal":
            if not _has_access_to_ledger(entity_id, linked_ledger, canonical, member_of, hosts):
                affected_ledgers.add(linked_ledger)
        elif entity_type == "surface":
            # Surface removal from ledger only directly affects that ledger and linked ones
            # where no surface-hosted principal has independent membership.
            has_independent = any(
                _has_access_to_ledger(p, linked_ledger, canonical, member_of, hosts)
                for p in hosts.get(entity_id, set())
            )
            if not has_independent:
                affected_ledgers.add(linked_ledger)
        elif entity_type == "ledger":
            affected_ledgers.add(linked_ledger)

    # 4. Affected surfaces
    affected_surfaces: dict[str, dict[str, set[str]]] = {}

    if entity_type == "surface":
        surface_id = entity_id
        for principal_id in hosts.get(surface_id, set()):
            lost = set()
            for affected_ledger in affected_ledgers:
                # Determine if this principal had access to affected_ledger via this surface
                if (surface_id, "", affected_ledger) in grants:
                    if not _has_access_to_ledger(
                        principal_id,
                        affected_ledger,
                        canonical,
                        member_of,
                        hosts,
                        exclude_membership=(entity_id, ledger_id) if affected_ledger == ledger_id else None,
                    ):
                        lost.add(affected_ledger)
                elif affected_ledger == ledger_id and member_of.get(principal_id, set()) - {ledger_id}:
                    # Principal is member of ledger directly; removal of surface does not remove that.
                    pass
            if lost:
                affected_surfaces.setdefault(surface_id, {}).setdefault(principal_id, set()).update(lost)
    else:
        for surface_id, hosted in hosts.items():
            for principal_id in hosted:
                if principal_id not in affected_principals:
                    continue
                lost = set()
                for affected_ledger in affected_ledgers:
                    if (surface_id, "", affected_ledger) in grants:
                        if not _has_access_to_ledger(
                            principal_id,
                            affected_ledger,
                            canonical,
                            member_of,
                            hosts,
                            exclude_membership=(entity_id, ledger_id) if entity_type == "principal" and affected_ledger == ledger_id else None,
                        ):
                            lost.add(affected_ledger)
                if lost:
                    affected_surfaces.setdefault(surface_id, {}).setdefault(principal_id, set()).update(lost)

    # 5. Orphan check
    orphaned_surfaces: set[str] = set()
    for surface_id in affected_surfaces:
        remaining = False
        for principal_id in hosts.get(surface_id, set()):
            if principal_id not in affected_principals:
                # Hosted principal not directly affected; check if it still has any ledger access.
                if any(
                    _has_access_to_ledger(principal_id, lid, canonical, member_of, hosts)
                    for lid in member_of.get(principal_id, set())
                ):
                    remaining = True
                    break
            else:
                # Affected principal may still have access to other ledgers.
                other_ledgers = set(member_of.get(principal_id, set())) - affected_ledgers
                if other_ledgers:
                    remaining = True
                    break
        if not remaining:
            orphaned_surfaces.add(surface_id)

    # 6. Critical warnings
    if orphaned_surfaces:
        report.critical_warnings.append(
            f"ORPHAN_RISK: {len(orphaned_surfaces)} surface(s) will be orphaned"
        )

    # Last T1 operator check
    t1_principals = {
        pid
        for pid, principal in principals.items()
        if isinstance(principal, dict) and _trust_class(pid, principals) == "T1"
    }
    ledger_t1 = {
        pid
        for pid in t1_principals
        if ledger_id in member_of.get(pid, set())
    }
    if len(ledger_t1) == 1 and ledger_t1.issubset(affected_principals):
        report.critical_warnings.append(
            "LAST_T1_OPERATOR: Cannot remove last T1 operator without transfer"
        )

    if caller_principal_id and caller_principal_id == entity_id and entity_type == "principal":
        report.critical_warnings.append("SELF_REMOVAL: You are removing yourself")

    # Sort outputs deterministically
    report.affected_principals = sorted(affected_principals)
    report.affected_ledgers = sorted(affected_ledgers)
    report.affected_surfaces = {
        surface_id: {
            principal_id: sorted(lost)
            for principal_id, lost in sorted(surface_principals.items())
        }
        for surface_id, surface_principals in sorted(affected_surfaces.items())
    }
    report.orphaned_surfaces = sorted(orphaned_surfaces)

    return report
