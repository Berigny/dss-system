#!/usr/bin/env python3
"""
KSR-VALIDATE v0.1 - structural self-validation for semantic_registry.yaml

Usage:
    python3 ksr_validate.py [REGISTRY] [--repo-root PATH] [--known PATH]

    REGISTRY      path to semantic_registry.yaml
                  (default: apps/backend/backend/kernel/semantic_registry.yaml)
    --repo-root   repo root for surface_policy path checks
    --known       JSON manifest of known failures, {"G02": "KSR-1.2.0-002", ...}

Exit code: 0 if no NEW failures, 1 otherwise.
Deps: PyYAML only.
"""
import argparse
import json
import math
import os
import sys
from collections import Counter

import yaml

CONF_TIERS = {"S", "A", "E", "P", "H"}
NODE_INDEX_SENTINEL = 99          # documented sentinel for non-corner primes
STEWARD_FLAGS = ("steward_only", "steward")


def is_prime(n):
    return isinstance(n, int) and n > 1 and all(n % i for i in range(2, int(math.isqrt(n)) + 1))


def load_with_dup_check(path):
    """Parse YAML, collecting duplicate keys (PyYAML silently overwrites)."""
    dups = []

    class DupLoader(yaml.SafeLoader):
        pass

    def no_dup(loader, node, deep=False):
        mapping = {}
        for k_node, v_node in node.value:
            key = loader.construct_object(k_node, deep=True)
            if key in mapping:
                dups.append(f"line {node.start_mark.line + 1}: duplicate key {key!r}")
            mapping[key] = loader.construct_object(v_node, deep=True)
        return mapping

    DupLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, no_dup)
    with open(path) as fh:
        text = fh.read()
    return yaml.load(text, Loader=DupLoader), dups


class Gate:
    def __init__(self, gid, name):
        self.gid, self.name = gid, name
        self.status = "PASS"
        self.detail = ""

    def fail(self, detail):
        self.status, self.detail = "FAIL", detail

    def note(self, detail):
        self.detail = detail


def run_gates(d, dups, registry_path, repo_root):
    G = []

    # G01 duplicate keys ------------------------------------------------------
    g = Gate("G01", "YAML duplicate keys")
    if dups:
        g.fail(f"{len(dups)} | first: {dups[0]}")
    G.append(g)

    digits = d.get("digit_registry", {})
    primes = d.get("prime_registry", {})
    lat = d.get("lattice_registry", {})
    corner = lat.get("corner_map", {})
    edges = lat.get("bridge_edges", [])
    qg = d.get("quaternary_gate_registry", {})
    ft = d.get("flow_topology", {})
    glos = d.get("glossary", [])
    syn = d.get("synonym_registry", {})
    strip = d.get("stripping_priority", {})
    cdr = d.get("cross_domain_registry", {})
    spol = d.get("surface_policy", {})

    # G02 checksum_336 consistency --------------------------------------------
    g = Gate("G02", "checksum_336 consistency")
    cs = d.get("checksum_invariant", {})
    val = cs.get("value")
    eq_sum = sum(v.get("value", 0) for k, v in digits.items() if k.startswith("Eq"))
    prod = None
    try:
        prod = digits["Eq6"]["value"] * digits["Eq7"]["value"] * digits["Eq8"]["value"]
    except KeyError:
        pass
    desc = str(cs.get("description", "")).lower()
    formula = str(qg.get("semantic_checksum", {}).get("formula", ""))
    problems = []
    if "sum" in desc and val != eq_sum:
        problems.append(f"description claims 'sum' but sum(Eq0..9)={eq_sum} != value={val}")
    if val != prod:
        problems.append(f"value={val} != Eq6*Eq7*Eq8={prod}")
    if "eq6" not in formula.lower():
        problems.append("quaternary semantic_checksum formula missing eq6 factor")
    if problems:
        g.fail(" | ".join(problems))
    else:
        g.note(f"value={val} == Eq6*Eq7*Eq8; description matches")
    G.append(g)

    # G03 digit_registry schema ------------------------------------------------
    g = Gate("G03", "digit_registry schema")
    req = ("symbol", "value", "tier", "aliases", "engineering_role")
    bad = [f"{k} missing {f}" for k, v in digits.items() for f in req if f not in v]
    vals = [v.get("value") for v in digits.values()]
    if len(vals) != len(set(vals)):
        bad.append("values not unique")
    tiers = {v.get("tier") for v in digits.values()}
    if not tiers <= {"S1", "S2", "C", "boundary"}:
        bad.append(f"unknown tiers {tiers - {'S1', 'S2', 'C', 'boundary'}}")
    if bad:
        g.fail("; ".join(bad))
    else:
        g.note(f"{len(digits)} entries OK")
    G.append(g)

    # G04 prime_registry integrity ---------------------------------------------
    g = Gate("G04", "prime_registry integrity")
    bad = [f"non-prime key {p}" for p in primes if not is_prime(p)]
    req = ("name", "tier", "node_index", "engineering_dimension", "confidence", "relation_type")
    rel_valid = set(d.get("relation_types", []))
    for p, v in primes.items():
        bad += [f"prime {p} missing {f}" for f in req if f not in v]
        if v.get("confidence") not in CONF_TIERS:
            bad.append(f"prime {p} confidence {v.get('confidence')}")
        if v.get("relation_type") not in rel_valid:
            bad.append(f"prime {p} relation_type {v.get('relation_type')}")
    ni = Counter(v.get("node_index") for v in primes.values())
    dupe_ni = {k: n for k, n in ni.items() if n > 1 and k != NODE_INDEX_SENTINEL}
    if dupe_ni:
        bad.append(f"node_index duplicates outside sentinel {NODE_INDEX_SENTINEL}: {dupe_ni}")
    if bad:
        g.fail("; ".join(bad))
    else:
        g.note(f"{len(primes)} primes; sentinel {NODE_INDEX_SENTINEL} used for "
               f"{[p for p, v in primes.items() if v.get('node_index') == NODE_INDEX_SENTINEL]}")
    G.append(g)

    # G05 corner_map consistency ------------------------------------------------
    g = Gate("G05", "corner_map consistency")
    issues = []
    if len(corner) != 8:
        issues.append(f"{len(corner)} corners (expected 8)")
    for coord, v in corner.items():
        k, sp = v.get("kernel", ""), v.get("structural_prime")
        if k.startswith("K") and k[1:].isdigit():
            ki = int(k[1:])
            if sp in primes and primes[sp].get("node_index") != ki:
                issues.append(f"{k}: prime {sp} node_index {primes[sp].get('node_index')} != {ki}")
        comp = v.get("dual_complement")
        comp_e = next((cv for cv in corner.values() if cv.get("kernel") == comp), None)
        if comp and comp_e and comp_e.get("dual_complement") != k:
            issues.append(f"{k} dual_complement {comp} asymmetric")
    days = [v.get("day") for v in corner.values()]
    if len(days) != len(set(days)):
        issues.append(f"corner days duplicated {sorted(days)}")
    if issues:
        g.fail("; ".join(issues))
    G.append(g)

    # G06 bridge_edges integrity -------------------------------------------------
    g = Gate("G06", "bridge_edges integrity")
    knames = {v.get("kernel") for v in corner.values()} | {"C", "C_reset"}
    undef = [(e.get("from"), e.get("to")) for e in edges
             if e.get("from") not in knames or e.get("to") not in knames]
    coords = Counter(e.get("coordinate") for e in edges)
    dupc = {c: n for c, n in coords.items() if n > 1}
    edays = Counter(e.get("day") for e in edges)
    dupd = {c: n for c, n in edays.items() if n > 1}
    if undef or dupc or dupd:
        g.fail(f"undefined={undef or 'none'} dup_coords={dupc or 'none'} dup_days={dupd or 'none'}")
    else:
        g.note(f"{len(edges)} edges OK")
    G.append(g)

    # G07 lattice 27-node coverage ------------------------------------------------
    g = Gate("G07", "lattice 27-node coverage")
    all_coords = {f"{i}{j}{k}" for i in "012" for j in "012" for k in "012"}
    declared = set(corner.keys()) | {e.get("coordinate") for e in edges}
    declared.add(lat.get("centroid_coordinate", "111"))
    missing = sorted(all_coords - declared)
    declared_days = ({v.get("day") for v in corner.values()}
                     | {e.get("day") for e in edges}
                     | {lat.get("centroid", {}).get("day")})
    missing_days = sorted(set(range(int(lat.get("total_nodes", 27)))) - declared_days)
    reset = lat.get("reset_node", {})
    problems = []
    if missing:
        problems.append(f"undeclared coords {missing}")
    if missing_days:
        problems.append(f"unassigned days {missing_days}")
    if reset.get("day", 0) > 26:
        problems.append(f"reset day={reset.get('day')} outside 0..26")
    if reset.get("coordinate") in corner:
        problems.append(f"reset coordinate {reset.get('coordinate')} collides with corner "
                        f"{corner[reset['coordinate']].get('kernel')} (undocumented)")
    if problems:
        g.fail("; ".join(problems))
    G.append(g)

    # G08 day-field overload (patches vs lattice) ---------------------------------
    g = Gate("G08", "'day' field semantic overload")
    patches = d.get("commandment_patch_registry", {}).get("patches", {})
    patch_days = {p.get("day") for p in patches.values()}
    lattice_days = {v.get("day") for v in corner.values()} | {e.get("day") for e in edges}
    overlap = sorted(patch_days & lattice_days)
    if overlap:
        g.fail(f"patch days and lattice days share one field; colliding values {overlap}")
    G.append(g)

    # G09 eq->prime wiring (metric_prime_map vs quaternary gates) -------------------
    g = Gate("G09", "eq->prime wiring consistency")
    mpm = {k.lower(): v for k, v in ft.get("metric_prime_map", {}).items()}
    mism = []
    for gname, gv in qg.get("gates", {}).items():
        ek = str(gv.get("equation_key", "")).lower()
        if ek in mpm and gv.get("prime") != mpm[ek]:
            mism.append(f"{gname}/{ek}: gate prime {gv.get('prime')} != metric_prime_map {mpm[ek]}")
    # corner_map agreement with metric_prime_map
    for coord, v in corner.items():
        eq = str(v.get("eq_node", "")).lower()
        if eq in mpm and v.get("structural_prime") != mpm[eq]:
            mism.append(f"{v.get('kernel')}/{eq}: corner prime {v.get('structural_prime')} "
                        f"!= metric_prime_map {mpm[eq]}")
    if mism:
        g.fail("; ".join(mism))
    G.append(g)

    # G10 glossary <-> synonym_registry agreement ------------------------------------
    g = Gate("G10", "glossary <-> synonym_registry agreement")
    div = []
    syn_norm = {k: {str(x).lower() for x in (v if isinstance(v, list) else [v])}
                for k, v in syn.items()}
    glos_terms = set()
    for e in glos:
        t, r = e.get("term"), e.get("replacement")
        glos_terms.add(str(t).lower())
        if r not in syn_norm:
            div.append(f"'{t}'->'{r}': replacement not a synonym key")
        elif str(t).lower() not in syn_norm[r]:
            div.append(f"'{t}'->'{r}': term absent from synonym list")
    for k, forms in syn_norm.items():
        for f in forms:
            if f not in glos_terms:
                div.append(f"surface '{f}' ({k}): no glossary entry")
    if div:
        g.fail(f"{len(div)} divergences | first: {div[0]}")
    G.append(g)

    # G11 priority tier agreement ------------------------------------------------------
    g = Gate("G11", "glossary.priority <-> stripping_priority")
    sp_tier = {t.lower(): tier for tier, terms in strip.items() for t in (terms or [])}
    mism = [(e["term"], e.get("priority"), sp_tier.get(str(e["term"]).lower()))
            for e in glos
            if e.get("priority") and sp_tier.get(str(e["term"]).lower())
            and e["priority"] != sp_tier[str(e["term"]).lower()]]
    if mism:
        g.fail(f"tier mismatches {mism}")
    G.append(g)

    # G12 synonym ambiguity --------------------------------------------------------------
    g = Gate("G12", "synonym/symbol ambiguity")
    owner = {}
    for canon, forms in syn.items():
        for f in (forms if isinstance(forms, list) else [forms]):
            owner.setdefault(str(f), set()).add(canon)
    amb = {s: sorted(c) for s, c in owner.items() if len(c) > 1}
    sym_clash = [v["symbol"] for v in digits.values() if v.get("symbol", "").lower()
                 in {k.lower() for k in syn}]
    if amb or sym_clash:
        g.fail(f"multi-owner surfaces {amb or 'none'}; digit symbols also synonym keys {sym_clash or 'none'}")
    G.append(g)

    # G13 steward-only enforcement (P/H tiers) ---------------------------------------------
    g = Gate("G13", "steward-only enforcement (P/H)")
    ph_unmarked = []

    def walk(o, path=""):
        if isinstance(o, dict):
            conf = o.get("confidence")
            if conf in ("P", "H") and not any(o.get(f) for f in STEWARD_FLAGS):
                ph_unmarked.append(path or "<root>")
            for k, v in o.items():
                walk(v, f"{path}.{k}" if path else str(k))
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, f"{path}[{i}]")

    walk(cdr, "cross_domain_registry")
    walk([e for e in glos if e.get("confidence") in ("P", "H")
          and not any(e.get(f) for f in STEWARD_FLAGS)], "glossary_unmarked")
    n_glos = sum(1 for e in glos if e.get("confidence") in ("P", "H")
                 and not any(e.get(f) for f in STEWARD_FLAGS))
    if ph_unmarked or n_glos:
        g.fail(f"P/H nodes without steward flag: cross_domain={len(ph_unmarked)}, glossary={n_glos}")
    G.append(g)

    # G14 surface_policy covers actual registry path -----------------------------------------
    g = Gate("G14", "surface_policy private_paths coverage")
    priv = spol.get("private_paths", [])
    if repo_root:
        rel = os.path.relpath(os.path.abspath(registry_path), os.path.abspath(repo_root))
    else:
        rel = registry_path
    rel = rel.replace(os.sep, "/")
    if rel not in priv:
        g.fail(f"actual registry path '{rel}' not an exact entry in private_paths {priv}")
    missing_on_disk = []
    if repo_root:
        for p in priv:
            if p in (".git", "__pycache__", ".venv", "node_modules"):
                continue
            if not os.path.exists(os.path.join(repo_root, p)):
                missing_on_disk.append(p)
        if missing_on_disk:
            g.fail((g.detail + " | " if g.detail else "")
                   + f"declared private_paths absent on disk: {missing_on_disk}")
    G.append(g)

    # G15 cross_domain relation_type validity --------------------------------------------------
    g = Gate("G15", "cross_domain relation_type validity")
    rels = Counter()

    def walk2(o):
        if isinstance(o, dict):
            if "relation_type" in o:
                rels[o["relation_type"]] += 1
            for v in o.values():
                walk2(v)
        elif isinstance(o, list):
            for v in o:
                walk2(v)

    walk2(cdr)
    bad_rels = {r: n for r, n in rels.items() if r not in rel_valid}
    if bad_rels:
        g.fail(f"invalid relation_types {bad_rels}")
    else:
        g.note(f"usage {dict(rels)}")
    G.append(g)

    return G


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("registry", nargs="?",
                    default="apps/backend/backend/kernel/semantic_registry.yaml")
    ap.add_argument("--repo-root", default=None)
    ap.add_argument("--known", default=None, help="known-failures JSON manifest")
    args = ap.parse_args()

    d, dups = load_with_dup_check(args.registry)
    gates = run_gates(d, dups, args.registry, args.repo_root)

    known = {}
    if args.known and os.path.exists(args.known):
        known = json.load(open(args.known))

    print(f"KSR-VALIDATE v0.1 | registry: {args.registry}")
    print(f"ksr_version: {d.get('ksr_version')} | gates: {len(gates)}\n")
    new_fail = 0
    for g in gates:
        if g.status == "PASS" and g.gid in known:
            tag = f"FIXED (was {known[g.gid]} - remove from manifest)"
        elif g.status == "FAIL" and g.gid in known:
            tag = f"KNOWN-FAIL ({known[g.gid]})"
        elif g.status == "FAIL":
            tag = "NEW-FAIL"
            new_fail += 1
        else:
            tag = "PASS"
        line = f"[{g.gid}] {g.name:<42} {tag}"
        print(line)
        if g.detail:
            print(f"      {g.detail}")
    print(f"\nNEW failures: {new_fail}")
    sys.exit(1 if new_fail else 0)


if __name__ == "__main__":
    main()
