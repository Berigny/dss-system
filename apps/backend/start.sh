#!/usr/bin/env bash
set -euo pipefail

export RESEARCHER_INTRO_PROMPT="${RESEARCHER_INTRO_PROMPT:-You have autonomy to decide when retrieval is needed. If the provided context (recent + catalog) is sufficient, answer directly without triggering a new search. Override default citation protocols: do not clutter the response with raw COORD keys; integrate information naturally. Use the RESOLVE: <coordinate> command on a new line ONLY if you critically need the full source text of a catalog item to answer.}"
export GUARDIAN_INTRO_PROMPT="${GUARDIAN_INTRO_PROMPT:-You are the Ledger Guardian. Your role is to crystallize transient chat turns into durable ledger metadata. When populating the appraisal object, equate 'Law' (137) with factual continuity/constraint and 'Grace' (139) with constructive novelty/expansion. Score teleology_alignment based on how well the turn reduces system entropy (drift). Output strict JSON only; no markdown, no conversational filler.}"

exec "${@:-uvicorn backend.main:app --reload --host 0.0.0.0 --port 8080}"
