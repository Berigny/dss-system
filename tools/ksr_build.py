#!/usr/bin/env python3
"""
KSR-BUILD v0.3 — deterministic partition of semantic_registry.yaml into core + packs.

Canonical public artifacts are written under ``ksr/core/`` and ``ksr/pack/``.

Usage:
    python3 tools/ksr_build.py --source private/semantic_registry.yaml --output ksr
    python3 tools/ksr_build.py --source private/semantic_registry.yaml --output ksr --emit-public --public-dir /tmp/ksr-public
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml


def load_registry(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def registry_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def build_core(registry: dict[str, Any]) -> dict[str, Any]:
    """Build ksr-core (public, load-bearing engineering fields only)."""
    _ESOTERIC_CATEGORIES = {"esoteric", "religious"}
    public_glossary = [
        entry for entry in registry.get("glossary", [])
        if isinstance(entry, dict)
        and not entry.get("steward_only")
        and str(entry.get("category", "")).lower() not in _ESOTERIC_CATEGORIES
    ]
    public_glossary_terms = {entry["term"] for entry in public_glossary if isinstance(entry, dict) and entry.get("term")}
    core: dict[str, Any] = {
        "ksr_version": registry.get("ksr_version"),
        "reference_documents": registry.get("reference_documents", {}),
        "digit_registry": registry.get("digit_registry", {}),
        "prime_registry": _strip_prime_registry_esoterica(registry.get("prime_registry", {})),
        "prime_groups": registry.get("prime_groups", {}),
        "lattice_registry": _strip_lattice_esoterica(registry.get("lattice_registry", {})),
        "checksum_invariant": registry.get("checksum_invariant", {}),
        "check_component": registry.get("check_component", {}),
        "quaternary_gate_registry": registry.get("quaternary_gate_registry", {}),
        "flow_topology": registry.get("flow_topology", {}),
        "octave_registry": registry.get("octave_registry", {}),
        "dual_pair_registry": registry.get("dual_pair_registry", {}),
        "glossary": public_glossary,
        "synonym_registry": _filter_term_lists(registry.get("synonym_registry", {}), public_glossary_terms),
        "stripping_priority": _filter_term_lists(registry.get("stripping_priority", {}), public_glossary_terms),
        "constraint_layer_registry": _strip_patch_esoterica(
            registry.get("constraint_layer_registry", {}), public_glossary_terms
        ),
        "value_node_registry": registry.get("value_node_registry", {}),
        "personality_type_overlay": registry.get("personality_type_overlay", {}),
        "cross_domain_registry": _filter_cross_domain_public(registry.get("cross_domain_registry", {})),
        "surface_policy": _core_surface_policy(registry.get("surface_policy", {})),
        "confidence_taxonomy": registry.get("confidence_taxonomy", {}),
        "relation_types": registry.get("relation_types", {}),
        "ksr_scope_statement": registry.get("ksr_scope_statement", ""),
    }
    return core


def _core_surface_policy(spol: dict[str, Any]) -> dict[str, Any]:
    """Surface policy for the public core artifact: core is public; packs and source are private."""
    updated = dict(spol)
    updated["public_paths"] = [
        "ksr/core/ksr-core-*.yaml",
    ]
    updated["private_paths"] = [
        "private/semantic_registry.yaml",
        "private/*",
        "ksr/pack/*",
    ]
    return updated


def _filter_term_lists(data: dict[str, Any], public_terms: set[str]) -> dict[str, Any]:
    """Strip list entries whose values are terms removed from the public glossary.

    Recurses into nested dicts so that structures like synonym_registry.PATCH.terms
    are cleaned of steward-only string synonyms.
    """
    public: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, list):
            filtered: list[Any] = []
            for item in value:
                if isinstance(item, dict):
                    if item.get("term") in public_terms:
                        filtered.append(item)
                elif isinstance(item, str):
                    if item in public_terms:
                        filtered.append(item)
                else:
                    filtered.append(item)
            if filtered:
                public[key] = filtered
        elif isinstance(value, dict):
            nested = _filter_term_lists(value, public_terms)
            if nested:
                public[key] = nested
        else:
            public[key] = value
    return public


def _filter_cross_domain_public(cdr: dict[str, Any]) -> dict[str, Any]:
    """Retain only public-optional A/E tier cross-domain nodes in core."""
    public: dict[str, Any] = {}
    for key, value in cdr.items():
        if isinstance(value, list):
            filtered = [node for node in value if _is_domain_tier(node)]
            if filtered:
                public[key] = filtered
        elif isinstance(value, dict) and _is_domain_tier(value):
            public[key] = value
    return public


# Esoteric fields that must not appear in the public ksr-core artifact.
_ESOTERIC_FIELDS = {
    "hebrew_letter",
    "hebrew_char",
    "esoteric",
    "i_ching_trigram",
    "mnemonic",
    "conceptual_state",
    "esoteric_names",
    "commandment_number",
    "commandment_text",
    "commandment_day",
    "system_patch",
}


def _strip_esoteric_fields(obj: Any) -> Any:
    """Recursively remove esoteric fields from dicts and lists."""
    if isinstance(obj, dict):
        return {
            k: _strip_esoteric_fields(v)
            for k, v in obj.items()
            if k not in _ESOTERIC_FIELDS
        }
    if isinstance(obj, list):
        return [_strip_esoteric_fields(v) for v in obj]
    return obj


def _strip_lattice_esoterica(lattice: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the lattice registry with steward-only fields removed."""
    return _strip_esoteric_fields(lattice)


def _strip_patch_esoterica(patch_reg: dict[str, Any], public_terms: set[str] | None = None) -> dict[str, Any]:
    """Return a copy of the constraint-layer patch registry with steward text removed."""
    public = _strip_esoteric_fields(patch_reg)
    syn = public.get("synonym_registry")
    if isinstance(syn, dict) and public_terms is not None:
        public["synonym_registry"] = _filter_term_lists(syn, public_terms)
    return public


def _strip_prime_registry_esoterica(prime_reg: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of the prime registry with steward-only fields removed."""
    return _strip_esoteric_fields(prime_reg)


def build_pack_domains(registry: dict[str, Any]) -> dict[str, Any]:
    """Build ksr-pack-domains (public-optional A/E tier cross-domain nodes)."""
    cross = registry.get("cross_domain_registry", {})
    filtered: dict[str, Any] = {}
    for key, value in cross.items():
        if isinstance(value, list):
            filtered[key] = [node for node in value if _is_domain_tier(node)]
        elif isinstance(value, dict) and _is_domain_tier(value):
            filtered[key] = value
    return {
        "ksr_pack": "domains",
        "cross_domain_registry": filtered,
    }


def build_pack_steward(registry: dict[str, Any]) -> dict[str, Any]:
    """Build ksr-pack-steward (private P/H tier and esoteric overlays)."""
    cross = registry.get("cross_domain_registry", {})
    steward_nodes: dict[str, Any] = {}
    for key, value in cross.items():
        if isinstance(value, list):
            nodes = [node for node in value if _is_steward_tier(node)]
            if nodes:
                steward_nodes[key] = nodes
        elif isinstance(value, dict) and _is_steward_tier(value):
            steward_nodes[key] = value

    return {
        "ksr_pack": "steward",
        "cross_domain_registry": steward_nodes,
        "personality_type_overlay": registry.get("personality_type_overlay", {}),
        "ledger_foundation": registry.get("ledger_foundation", {}),
    }


def _is_domain_tier(node: Any) -> bool:
    """Return True if node is tier A or E (public-optional)."""
    if not isinstance(node, dict):
        return False
    tier = str(node.get("tier", "")).upper()
    return tier in ("A", "E")


def _is_steward_tier(node: Any) -> bool:
    """Return True if node is tier P or H (steward-only)."""
    if not isinstance(node, dict):
        return False
    tier = str(node.get("tier", "")).upper()
    return tier in ("P", "H") or node.get("steward_only") is True


def add_header(artifact: dict[str, Any], source_sha: str, artifact_name: str) -> dict[str, Any]:
    """Prepend provenance header to an artifact."""
    wrapped = {
        "ksr_artifact": artifact_name,
        "source_registry_sha256": source_sha,
        "artifact": artifact,
    }
    return wrapped


def artifact_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def write_yaml(path: Path, data: dict[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    path.write_text(text, encoding="utf-8")
    return artifact_sha256(text)


def build_all(source: Path, output: Path) -> dict[str, Any]:
    """Build core + packs and write to ``output/core/`` and ``output/pack/`` subdirectories."""
    registry = load_registry(source)
    source_sha = registry_sha256(source)

    core = build_core(registry)
    domains = build_pack_domains(registry)
    steward = build_pack_steward(registry)

    core_dir = output / "core"
    pack_dir = output / "pack"
    core_dir.mkdir(parents=True, exist_ok=True)
    pack_dir.mkdir(parents=True, exist_ok=True)

    core_path = core_dir / f"ksr-core-{registry.get('ksr_version', 'unknown')}.yaml"
    domains_path = pack_dir / f"ksr-pack-domains-{registry.get('ksr_version', 'unknown')}.yaml"
    steward_path = pack_dir / f"ksr-pack-steward-{registry.get('ksr_version', 'unknown')}.yaml"

    core_sha = write_yaml(core_path, add_header(core, source_sha, "ksr-core"))
    domains_sha = write_yaml(domains_path, add_header(domains, source_sha, "ksr-pack-domains"))
    steward_sha = write_yaml(steward_path, add_header(steward, source_sha, "ksr-pack-steward"))

    return {
        "source": str(source),
        "source_sha256": source_sha,
        "ksr_version": registry.get("ksr_version"),
        "artifacts": {
            "core": {"path": str(core_path), "sha256": core_sha},
            "domains": {"path": str(domains_path), "sha256": domains_sha},
            "steward": {"path": str(steward_path), "sha256": steward_sha},
        },
    }


def emit_public(source: Path, public_dir: Path, manifest: dict[str, Any]) -> None:
    """Generate a public repo tree from the private source of truth."""
    if public_dir.exists():
        shutil.rmtree(public_dir)
    public_dir.mkdir(parents=True, exist_ok=True)

    # Copy core artifact.
    core_src = Path(manifest["artifacts"]["core"]["path"])
    ksr_dir = public_dir / "ksr" / "core"
    ksr_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(core_src, ksr_dir / core_src.name)

    # Copy tools.
    tools_src = Path("tools")
    tools_dst = public_dir / "tools"
    tools_dst.mkdir(parents=True, exist_ok=True)
    for tool in ("ksr_validate.py", "encode.py", "decode.py", "ksr_build.py"):
        src = tools_src / tool
        if src.exists():
            shutil.copy2(src, tools_dst / tool)

    # Copy eval briefs, corpus, reports, check vectors.
    eval_src = Path("eval")
    eval_dst = public_dir / "eval"
    eval_dst.mkdir(parents=True, exist_ok=True)
    for name in ("KSR-EVAL_v0.1.md", "KSR-EVAL_v0.3.md", "corpus", "reports", "check_vectors.json"):
        src = eval_src / name
        if src.exists():
            if src.is_dir():
                shutil.copytree(src, eval_dst / name)
            else:
                shutil.copy2(src, eval_dst / name)

    # Copy docs if they exist.
    docs_src = Path("docs")
    docs_dst = public_dir / "docs"
    if docs_src.exists():
        shutil.copytree(docs_src, docs_dst, dirs_exist_ok=True)

    # Write README and LICENSE.
    (public_dir / "README.md").write_text(_public_readme(), encoding="utf-8")
    (public_dir / "LICENSE").write_text(_apache_license(), encoding="utf-8")


def _public_readme() -> str:
    return """# Kernel Semantic Registry (KSR)

Engineering register for deterministic structural verification of LLM-output claims.

## Verified properties

- 16/16 structural self-validation gates pass on `ksr-core`.
- 336-derived checksum invariant and quaternary-gate wiring verified.
- 336-derived check-digit: error detection >= 0.98 on decimal-digit mutations, zero false rejections on valid encodings.
- Deterministic encode/decode round-trip on checked encodings.

## Reproduction

```bash
python3 tools/ksr_validate.py --mode core ksr/core/ksr-core-*.yaml
python3 tools/encode.py --concepts Eq0 Eq1 --check
python3 tools/decode.py --number <checked-number> --check
```

## Scope

This public artifact contains only the load-bearing engineering kernel. Symbolic,
esoteric, and steward-only overlays are maintained separately and are not required
for the verified use-cases above.

## Provenance

This tree is a generated build output. The private source-of-truth registry remains
in the upstream monorepo. See `ksr-core-*.yaml` header for source SHA.
"""


def _apache_license() -> str:
    return """Apache License
Version 2.0, January 2004
http://www.apache.org/licenses/

Copyright 2026 Berigny / DSS contributors

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="KSR deterministic build tool")
    parser.add_argument("--source", type=Path, default=Path("private/semantic_registry.yaml"))
    parser.add_argument("--output", type=Path, default=Path("ksr"))
    parser.add_argument("--emit-public", action="store_true", help="Generate public repo tree")
    parser.add_argument("--public-dir", type=Path, default=Path("/tmp/ksr-public"))
    parser.add_argument("--manifest", type=Path, help="Write build manifest JSON")
    args = parser.parse_args()

    manifest = build_all(args.source, args.output)

    if args.emit_public:
        emit_public(args.source, args.public_dir, manifest)
        manifest["public_dir"] = str(args.public_dir)
        print(f"Public tree emitted to {args.public_dir}")

    manifest_json = json.dumps(manifest, indent=2, ensure_ascii=False)
    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(manifest_json, encoding="utf-8")
        print(f"Manifest written to {args.manifest}")

    print(manifest_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
