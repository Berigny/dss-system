#!/usr/bin/env python3
"""
Generate eval/corpus/novel_v0.1.jsonl for KSR-EVAL Phase 2.

Produces 150 fresh sentences stratified across 6 strata (25 each):
concrete, abstract, negation, quantifier, temporal, relational.
Each record includes an encode_seed: a list of KSR concept names that the
sentence is intended to evoke. Deterministic given the seed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path


STRATA = ["concrete", "abstract", "negation", "quantifier", "temporal", "relational"]
PER_STRATUM = 25

# Deterministic concept palettes per stratum.
CONCEPT_PALETTES = {
    "concrete": [
        ["Eq0", "K0", "000"],
        ["Eq1", "K1", "022"],
        ["Eq2", "K2", "202"],
        ["Eq3", "K3", "220"],
        ["Eq4", "K4", "002"],
        ["Eq5", "K5", "020"],
        ["Eq6", "K6", "200"],
        ["Eq7", "K7", "222"],
        ["Eq8", "C", "111"],
        ["Eq9", "C_reset", "333"],
    ],
    "abstract": [
        ["Eq0", "Eq1", "Novelty"],
        ["Eq2", "Eq3", "Connection"],
        ["Eq4", "Eq5", "Potential"],
        ["Eq6", "Eq7", "Mastery"],
        ["Eq8", "Eq9", "Law", "Grace"],
        ["Eq0", "Eq4", "Origin", "Journey"],
        ["Eq1", "Eq5", "Boundary", "Process"],
        ["Eq2", "Eq6", "Change", "Audit"],
        ["Eq3", "Eq7", "Closure", "Justice"],
        ["Eq8", "Eq6", "Constraint", "Awareness"],
    ],
    "negation": [
        ["Eq0", "Eq8", "not", "Origin"],
        ["Eq1", "Eq8", "no", "Boundary"],
        ["Eq2", "Eq8", "never", "Temporalization"],
        ["Eq3", "Eq8", "nothing", "Closure"],
        ["Eq4", "Eq8", "none", "Coupling"],
        ["Eq5", "Eq8", "without", "Persistence"],
        ["Eq6", "Eq8", "neither", "Audit"],
        ["Eq7", "Eq8", "nor", "Coherence"],
        ["Eq9", "Eq8", "absence", "Relaxation"],
        ["Eq0", "Eq9", "void", "Null State"],
    ],
    "quantifier": [
        ["Eq0", "Eq4", "all", "Origin"],
        ["Eq1", "Eq4", "some", "Boundary"],
        ["Eq2", "Eq4", "many", "Temporalization"],
        ["Eq3", "Eq4", "few", "Closure"],
        ["Eq4", "Eq5", "most", "Coupling"],
        ["Eq5", "Eq6", "every", "Persistence"],
        ["Eq6", "Eq7", "each", "Audit"],
        ["Eq7", "Eq8", "several", "Coherence"],
        ["Eq8", "Eq9", "any", "Constraint"],
        ["Eq9", "Eq0", "none", "Relaxation"],
    ],
    "temporal": [
        ["Eq2", "Eq0", "before", "Temporalization"],
        ["Eq2", "Eq1", "after", "Boundary"],
        ["Eq2", "Eq3", "then", "Closure"],
        ["Eq2", "Eq4", "now", "Coupling"],
        ["Eq2", "Eq5", "later", "Persistence"],
        ["Eq2", "Eq6", "always", "Audit"],
        ["Eq2", "Eq7", "never", "Coherence"],
        ["Eq2", "Eq8", "until", "Constraint"],
        ["Eq2", "Eq9", "since", "Relaxation"],
        ["Eq0", "Eq2", "beginning", "Origin"],
    ],
    "relational": [
        ["Eq1", "Eq4", "connects", "Boundary"],
        ["Eq1", "Eq5", "between", "Boundary"],
        ["Eq4", "Eq6", "depends", "Coupling"],
        ["Eq5", "Eq7", "follows", "Persistence"],
        ["Eq6", "Eq8", "requires", "Audit"],
        ["Eq7", "Eq9", "allows", "Coherence"],
        ["Eq8", "Eq9", "balances", "Constraint"],
        ["Eq0", "Eq7", "measures", "Origin"],
        ["Eq3", "Eq4", "contains", "Closure"],
        ["Eq2", "Eq5", "transforms", "Temporalization"],
    ],
}

TEMPLATES = {
    "concrete": [
        "The {noun} rests on the {surface}.",
        "A {noun} falls through the {medium}.",
        "The {noun} reflects the {light_source}.",
        "A {noun} blocks the {path}.",
        "The {noun} melts into the {container}.",
        "A {noun} rolls across the {surface}.",
        "The {noun} splits the {material} cleanly.",
        "A {noun} floats above the {liquid}.",
        "The {noun} sinks beneath the {weight}.",
        "A {noun} casts a shadow on the {wall}.",
        "The {noun} shatters against the {barrier}.",
        "A {noun} rises from the {source}.",
        "The {noun} descends into the {depth}.",
        "A {noun} clings to the {support}.",
        "The {noun} bursts through the {enclosure}.",
        "A {noun} settles into the {hollow}.",
        "The {noun} stretches toward the {opening}.",
        "A {noun} hovers at the {edge}.",
        "The {noun} pierces the {layer}.",
        "A {noun} drifts along the {current}.",
        "The {noun} hardens in the {cold}.",
        "A {noun} ignites near the {spark}.",
        "The {noun} dissolves within the {solution}.",
        "A {noun} balances on the {point}.",
        "The {noun} emerges from the {fissure}.",
    ],
    "abstract": [
        "{concept} arises from the silence of possibility.",
        "The idea of {concept} binds many fragments into one.",
        "{concept} remains invisible yet structures every choice.",
        "A theory of {concept} must account for its own limits.",
        "{concept} is the medium through which meaning travels.",
        "The dignity of {concept} depends on its resistance to reduction.",
        "{concept} dissolves when pressed too hard.",
        "Every model of {concept} carries an unstated assumption.",
        "{concept} returns whenever the old forms exhaust themselves.",
        "The value of {concept} lies in what it refuses to capture.",
        "{concept} is neither cause nor effect but their interval.",
        "A culture without {concept} loses its capacity for reflection.",
        "{concept} intensifies at the boundary between known and unknown.",
        "The history of {concept} is a history of forgotten questions.",
        "{concept} persists because it names an irreducible tension.",
        "To approach {concept} is to accept asymptotic understanding.",
        "{concept} operates as a quiet constraint on imagination.",
        "The fragility of {concept} makes it worth defending.",
        "{concept} is learned more by failure than by proof.",
        "A definition of {concept} is always also a decision.",
        "{concept} gathers weight through repeated refusal.",
        "The absence of {concept} is felt before it is named.",
        "{concept} migrates between disciplines without losing itself.",
        "To doubt {concept} is already to participate in it.",
        "{concept} ends not with answers but with better questions.",
    ],
    "negation": [
        "The {noun} is not {adj}.",
        "No {noun} appeared at the {place}.",
        "Never does the {noun} remain {state}.",
        "Nothing in the {noun} suggests {quality}.",
        "None of the {noun_plural} reached the {destination}.",
        "Without {noun}, the {system} collapses.",
        "Neither {noun_a} nor {noun_b} survived the {event}.",
        "The {noun} is neither {adj_a} nor {adj_b}.",
        "Absence of {noun} defined the entire {period}.",
        "There is no {noun} beneath the {surface}.",
        "The {noun} did not respond to the {stimulus}.",
        "Not even the {noun} could prevent the {outcome}.",
        "No amount of {noun} restores the {balance}.",
        "The {noun} never crosses the {threshold}.",
        "Nothing about {noun} implies the {conclusion}.",
        "Without a {noun}, the {process} halts.",
        "Neither {adj_a} nor {adj_b} describes the {noun}.",
        "The {noun} shows no sign of {change}.",
        "Not once did the {noun} leave the {region}.",
        "Absence makes the {noun} more {adj}.",
        "There was no {noun} to guide the {actor}.",
        "The {noun} cannot exist without its {complement}.",
        "Never has {noun} been so entirely {adj}.",
        "None could call the {noun} {adj}.",
        "The {noun} refuses, and so does not become {state}.",
    ],
    "quantifier": [
        "All {noun_plural} share one {property}.",
        "Some {noun_plural} exceed the {limit}.",
        "Many {noun_plural} resist the {force}.",
        "Few {noun_plural} reach the {state}.",
        "Most {noun_plural} follow the {pattern}.",
        "Every {noun} carries a hidden {feature}.",
        "Each {noun} differs in its {aspect}.",
        "Several {noun_plural} converge at the {point}.",
        "Any {noun} might trigger the {event}.",
        "None of the {noun_plural} satisfied the {condition}.",
        "All but one {noun} failed the {test}.",
        "Some of the {noun_plural} vanished before the {moment}.",
        "Many a {noun} has fallen to the {cause}.",
        "Fewer {noun_plural} remain than the {count} suggests.",
        "Most of the {noun} dissolved into the {medium}.",
        "Every single {noun} retained its {quality}.",
        "Each of the {noun_plural} marked a distinct {phase}.",
        "Several {noun_plural} together outweigh one {noun_b}.",
        "Any of the {noun_plural} could serve as the {anchor}.",
        "None but the {noun} understood the {rule}.",
        "All {noun_plural} eventually lose their {edge}.",
        "Some {noun_plural} grow stronger under {pressure}.",
        "Many {noun_plural} return to the same {origin}.",
        "Few {noun_plural} survive the {transition}.",
        "Most {noun_plural} obey the {principle}.",
    ],
    "temporal": [
        "Before the {event}, the {noun} was {state}.",
        "After the {event}, the {noun} became {state}.",
        "Then the {noun} began to {verb}.",
        "Now the {noun} rests in the {place}.",
        "Later, the {noun} will reach the {destination}.",
        "Always, the {noun} returns to the {origin}.",
        "Never has the {noun} stayed so {state}.",
        "Until the {event}, the {noun} remains {state}.",
        "Since the {event}, the {noun} has {verb}.",
        "At the beginning, only the {noun} existed.",
        "The {noun} existed long before the {event}.",
        "Afterward, the {noun} found a new {place}.",
        "Then and only then did the {noun} {verb}.",
        "Right now, the {noun} is at the {point}.",
        "Soon the {noun} will cross the {boundary}.",
        "Always the {noun} returns, always changed.",
        "Never again will the {noun} be so {state}.",
        "Until dawn the {noun} kept its {posture}.",
        "Since yesterday the {noun} has lost its {quality}.",
        "In the beginning the {noun} knew no {limit}.",
        "Before long the {noun} reached the {state}.",
        "After the storm the {noun} stood {state}.",
        "Then the {noun} and the {noun_b} diverged.",
        "Now is when the {noun} must {verb}.",
        "Eventually every {noun} reaches the {end}.",
    ],
    "relational": [
        "The {noun_a} connects to the {noun_b}.",
        "Between the {noun_a} and the {noun_b} lies the {noun_c}.",
        "The {noun_a} depends on the {noun_b}.",
        "The {noun_a} follows the {noun_b}.",
        "The {noun_a} requires the {noun_b}.",
        "The {noun_a} allows the {noun_b} to {verb}.",
        "The {noun_a} balances the {noun_b}.",
        "The {noun_a} measures the {noun_b}.",
        "The {noun_a} contains the {noun_b}.",
        "The {noun_a} transforms into the {noun_b}.",
        "The {noun_a} links the {noun_b} to the {noun_c}.",
        "Between them, the {noun_a} and {noun_b} form a {structure}.",
        "The {noun_a} cannot exist unless the {noun_b} persists.",
        "Where the {noun_a} ends, the {noun_b} begins.",
        "The {noun_a} yields to the {noun_b} under {pressure}.",
        "The {noun_a} supports the {noun_b} at the {point}.",
        "The {noun_a} mirrors the {noun_b} across the {axis}.",
        "The {noun_a} exceeds the {noun_b} in {quality}.",
        "The {noun_a} replaces the {noun_b} during the {phase}.",
        "The {noun_a} restrains the {noun_b} from the {action}.",
        "The {noun_a} channels the {noun_b} toward the {goal}.",
        "The {noun_a} answers to the {noun_b} alone.",
        "The {noun_a} divides the {noun_b} from the {noun_c}.",
        "The {noun_a} receives the {noun_b} as its {role}.",
        "The {noun_a} completes the {noun_b} without merging.",
    ],
}

FILLERS = {
    "noun": ["stone", "vessel", "flame", "thread", "root", "mirror", "seed", "wheel", "shell", "lens", "crystal", "bowl", "feather", "anchor", "column", "coil", "ridge", "orb", "vein", "bridge", "gate", "pool", "horn", "kiln", "spire"],
    "noun_plural": ["stones", "vessels", "flames", "threads", "roots", "mirrors", "seeds", "wheels", "shells", "lenses", "crystals", "bowls", "feathers", "anchors", "columns", "coils", "ridges", "orbs", "veins", "bridges", "gates", "pools", "horns", "kilns", "spires"],
    "noun_a": ["river", "thread", "voice", "shadow", "current", "signal", "pillar", "pattern", "field", "agent", "layer", "source", "mask", "chain", "index", "vector", "node", "beam", "trace", "thread"],
    "noun_b": ["bank", "needle", "ear", "light", "shore", "receiver", "arch", "noise", "force", "patient", "base", "drain", "face", "link", "term", "origin", "edge", "screen", "mark", "loom"],
    "noun_c": ["valley", "knot", "silence", "echo", "channel", "decoder", "keystone", "signal", "boundary", "instrument", "joint", "filter", "gap", "relay", "context", "threshold", "gate", "pulse", "relation", "weft"],
    "surface": ["table", "ice", "sand", "skin", "glass", "cloth", "metal", "water", "bark", "tile"],
    "medium": ["air", "water", "smoke", "gel", "soil", "light", "mist", "resin", "clay", "oil"],
    "light_source": ["sun", "lamp", "moon", "fire", "glow", "spark", "beam", "flare", "gleam", "radiance"],
    "path": ["road", "vein", "beam", "track", "wire", "channel", "trail", "groove", "line", "current"],
    "container": ["jar", "room", "shell", "cave", "vessel", "pocket", "well", "cell", "frame", "bowl"],
    "liquid": ["water", "oil", "mercury", "sap", "ink", "honey", "melted wax", "brine", "resin", "tar"],
    "weight": ["stone", "load", "anchor", "hand", "chain", "pressure", "gravity", "mass", "burden", "column"],
    "wall": ["cliff", "facade", "screen", "canvas", "shield", "plate", "panel", "membrane", "slab", "veil"],
    "barrier": ["wall", "door", "shield", "dam", "membrane", "gate", "fence", "crust", "shell", "lid"],
    "source": ["spring", "flame", "seed", "origin", "core", "mouth", "well", "root", "spark", "vent"],
    "depth": ["pit", "sea", "cave", "valley", "well", "abyss", "core", "trench", "fold", "night"],
    "support": ["branch", "arch", "wire", "stem", "rod", "frame", "root", "pillar", "tendon", "spine"],
    "enclosure": ["cage", "shell", "wall", "skin", "fence", "net", "bag", "ring", "coil", "dome"],
    "hollow": ["cup", "bowl", "cave", "socket", "valley", "pore", "well", "groove", "niche", "crater"],
    "opening": ["door", "window", "mouth", "pore", "gap", "valve", "channel", "iris", "vent", "fissure"],
    "edge": ["rim", "brink", "lip", "shore", "border", "margin", "verge", "threshold", "crest", "limit"],
    "layer": ["film", "crust", "veil", "coat", "sheet", "lamina", "membrane", "skin", "sediment", "glaze"],
    "current": ["stream", "wind", "tide", "drift", "flux", "flow", "current", "pulse", "surge", "wake"],
    "cold": ["frost", "ice", "winter", "metal", "night", "depth", "shadow", "clay", "stone", "void"],
    "spark": ["flint", "ember", "wire", "crystal", "lens", "catalyst", "node", "friction", "charge", "impulse"],
    "solution": ["water", "acid", "brine", "solvent", "lye", "oil", "sap", "melt", "tincture", "electrolyte"],
    "point": ["tip", "apex", "pin", "needle", "peak", "pivot", "fulcrum", "node", "vertex", "speck"],
    "fissure": ["crack", "rift", "fault", "split", "crevice", "gap", "vent", "seam", "tear", "fracture"],
    "material": ["wood", "stone", "glass", "clay", "metal", "ice", "wax", "bone", "fiber", "resin"],
    "concept": ["beauty", "justice", "freedom", "meaning", "truth", "identity", "responsibility", "presence", "limit", "becoming", "order", "chaos", "integrity", "memory", "attention", "care", "trust", "doubt", "resolve", "grace", "suffering", "wonder", "erosion", "novelty", "coherence"],
    "adj": ["heavy", "bright", "silent", "sharp", "smooth", "brittle", "dense", "fragile", "taut", "raw", "hollow", "solid", "faint", "rigid", "soft", "rough", "still", "hot", "cold", "dry"],
    "adj_a": ["large", "old", "fast", "hard", "deep", "rough", "tall", "warm", "loud", "thick"],
    "adj_b": ["small", "young", "slow", "soft", "shallow", "smooth", "short", "cool", "quiet", "thin"],
    "place": ["doorway", "basin", "ridge", "niche", "threshold", " alcove", "terrace", "cavern", "clearing", "plaza"],
    "state": ["still", "open", "closed", "whole", "broken", "ripe", "raw", "calm", "tense", "empty"],
    "quality": ["color", "weight", "texture", "tone", "temperature", "density", "rhythm", "shape", "grain", "luster"],
    "destination": ["harbor", "summit", "chamber", "clearing", "shore", "gate", "platform", "valley", "nest", "terminus"],
    "system": ["machine", "organism", "network", "structure", "field", "chain", "weave", "architecture", "arrangement", "economy"],
    "event": ["storm", "drought", "flood", "fire", "quake", "impact", "collapse", "awakening", "shift", "rupture"],
    "stimulus": ["touch", "sound", "light", "heat", "pressure", "scent", "current", "motion", "signal", "impulse"],
    "outcome": ["fall", "return", "split", "merge", "dissolution", "ignition", "growth", "decay", "release", "arrest"],
    "balance": ["scale", "tension", "equilibrium", "proportion", "rhythm", "poise", "symmetry", "ratio", "measure", "harmony"],
    "change": ["motion", "growth", "decay", "color", "phase", "position", "temperature", "pressure", "density", "orientation"],
    "region": ["basin", "range", "plain", "delta", "arc", "zone", "sector", "quadrant", "corridor", "domain"],
    "period": ["era", "epoch", "season", "cycle", "age", "decade", "century", "moment", "interval", "duration"],
    "complement": ["shadow", "counterweight", "echo", "pair", "twin", "opposite", "match", "mirror", "companion", "inverse"],
    "property": ["mass", "charge", "spin", "valence", "density", "polarity", "tension", "resonance", "symmetry", "phase"],
    "limit": ["threshold", "ceiling", "boundary", "cap", "rim", "maximum", "minimum", "edge", "terminus", "brink"],
    "threshold": ["threshold", "brink", "verge", "border", "limit", "edge", "margin", "boundary", "crossing", "divide"],
    "force": ["gravity", "magnetism", "tension", "pressure", "current", "wind", "tide", "friction", "inertia", "impulse"],
    "pattern": ["spiral", "grid", "wave", "lattice", "cycle", "branch", "network", "pulse", "fracture", "weave"],
    "feature": ["mark", "trace", "scar", "groove", "node", "pore", "ridge", "fold", "kernel", "vein"],
    "aspect": ["form", "color", "duration", "intensity", "polarity", "scope", "density", "orientation", "phase", "register"],
    "point_rel": ["junction", "vertex", "nexus", "origin", "terminus", "fulcrum", "threshold", "pivot", "crossing", "apex"],
    "anchor": ["root", "keystone", "pivot", "fulcrum", "datum", "base", "origin", "touchstone", "constant", "reference"],
    "count": ["tally", " census", "measure", "estimate", "account", "record", "inventory", "index", "sum", "census"],
    "test": ["trial", "assay", "ordeal", "probe", "check", "examination", "verification", "challenge", "stress", "audit"],
    "moment": ["instant", "interval", "beat", "pulse", "phase", "tick", "juncture", "crossing", "threshold", "beat"],
    "phase": ["stage", "season", "cycle", "episode", "chapter", "interval", "passage", "turn", "period", "segment"],
    "pressure": ["weight", "stress", "strain", "load", "tension", "compression", "urgency", "demand", "force", "burden"],
    "principle": ["law", "rule", "axiom", "canon", "constant", "order", "pattern", "regularity", "truth", "norm"],
    "verb": ["move", "turn", "settle", "rise", "fall", "open", "close", "spread", "gather", "dissolve", "shift", "settle", "bend", "extend", "recede"],
    "end": ["close", "limit", "finish", "terminus", "boundary", "horizon", "edge", "omega", "finale", "resolution"],
    "posture": ["vigil", "silence", "watch", "stance", "guard", "rest", "wait", "readiness", "stillness", "attention"],
    "structure": ["bridge", "lattice", "arch", "weave", "knot", "network", "fabric", "matrix", "scaffold", "web"],
    "axis": ["line", "pole", "spine", "diagonal", "diameter", "meridian", "equator", "radial", "cross", "plumb"],
    "goal": ["target", "end", "aim", "purpose", "objective", "destination", "outcome", "result", "terminus", "resolve"],
    "role": ["host", "vessel", "medium", "channel", "receiver", "stage", "frame", "matrix", "ground", "support"],
    "action": ["fall", "escape", "expansion", "return", "release", "contact", "ignition", "dissolution", "arrest", "flow"],
    "actor": ["traveler", "witness", "builder", "keeper", "seeker", "messenger", "guardian", "stranger", "host", "guest"],
    "boundary": ["edge", "limit", "border", "threshold", "margin", "frontier", "rim", "verge", "boundary", "dividing line"],
    "cause": ["weight", "heat", "loss", "impact", "time", "pressure", "desire", "error", "friction", "absence"],
    "conclusion": ["result", "outcome", "verdict", "finding", "judgment", "resolution", "end", "consequence", "determination", "summary"],
    "condition": ["state", "situation", "circumstance", "status", "posture", "phase", "mode", "form", "order", "disorder"],
    "origin": ["source", "root", "beginning", "spring", "seed", "birth", "cradle", "foundation", "origin", "starting point"],
    "process": ["process", "procedure", "sequence", "operation", "mechanism", "cycle", "flow", "transformation", "reaction", "progression"],
    "rule": ["rule", "law", "principle", "command", "edict", "protocol", "canon", "norm", "order", "decree"],
    "transition": ["transition", "passage", "shift", "changeover", "crossing", "transformation", "turning", "metamorphosis", "evolution", "gradation"],
}


def generate_corpus(seed: int = 253) -> list[dict]:
    rng = random.Random(seed)
    records: list[dict] = []
    palette_indices = {s: 0 for s in STRATA}

    for stratum in STRATA:
        templates = TEMPLATES[stratum]
        palette = CONCEPT_PALETTES[stratum]
        for i in range(PER_STRATUM):
            template = templates[i % len(templates)]
            # Fill template slots deterministically.
            slots = {}
            possible_slots = [s for s in FILLERS.keys() if f"{{{s}}}" in template]
            for slot in possible_slots:
                slots[slot] = rng.choice(FILLERS[slot])
            # Ensure unique-ish text by adding a stratum-specific modifier word.
            text = template.format(**slots)
            # Assign concept seed cycling through palette.
            concepts = palette[i % len(palette)].copy()
            rng.shuffle(concepts)
            records.append({
                "id": f"n{stratum[0]}{i+1:03d}",
                "stratum": stratum,
                "text": text,
                "encode_seed": concepts,
            })

    # Final deterministic shuffle so stratum order is not preserved.
    rng.shuffle(records)
    # Stable sort by id for canonical ordering.
    records.sort(key=lambda r: r["id"])
    return records


def corpus_sha256(records: list[dict]) -> str:
    canonical = json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("eval/corpus/novel_v0.1.jsonl"))
    parser.add_argument("--seed", type=int, default=253)
    args = parser.parse_args()

    records = generate_corpus(args.seed)
    sha = corpus_sha256(records)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"corpus_sha256": sha, "count": len(records), "strata": STRATA, "per_stratum": PER_STRATUM}) + "\n")
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    print(f"Wrote {len(records)} records to {args.output}")
    print(f"Corpus sha256: {sha}")


if __name__ == "__main__":
    main()
