#!/usr/bin/env python3
"""LOAM Voice v0.1 ship-gate harness.

Exercises the voice pipeline and measures round-trip latency:

    POST /v1/voice/session -> WS /v1/voice/stream/{id}
    -> send opus frames -> receive response opus frames

Usage:
    # Direct backend test (recommended for gate automation)
    python eval/voice_ship_gate.py --backend https://dss-system-backend.fly.dev --runs 5

    # Local backend with mock mode
    VOICE_MOCK_MODE=true python -m backend.main  # in apps/backend
    python eval/voice_ship_gate.py --backend ws://localhost:8000 --runs 5

Acceptance:
    - Round-trip latency < 1500 ms (acceptable), target < 800 ms.
    - At least 5 runs with consistent results.

Outputs:
    - eval/reports/voice_ship_gate_results.json
    - Console summary
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import statistics
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

import aiohttp
import websockets

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
LOGGER = logging.getLogger("voice_ship_gate")

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MOCK_WAV = REPO_ROOT / "apps" / "backend" / "backend" / "assets" / "mock_tone.wav"
DEFAULT_OUTPUT = REPO_ROOT / "eval" / "reports" / "voice_ship_gate_results.json"

# Acceptance criteria from the v0.1 spec.
TARGET_MS = 800
ACCEPTABLE_MS = 1500


def _ffmpeg_installed() -> bool:
    return shutil.which("ffmpeg") is not None


def _wav_to_opus(wav_path: Path) -> bytes:
    """Convert a WAV file to opus frames using ffmpeg."""
    if not _ffmpeg_installed():
        raise RuntimeError("ffmpeg not installed")

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "wav",
        "-i", str(wav_path),
        "-c:a", "libopus",
        "-b:a", "24k",
        "-ar", "48000",
        "-ac", "1",
        "-f", "opus",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg encode failed: {proc.stderr.decode()[:200]}")
    return proc.stdout


def _build_urls(backend: str) -> tuple[str, str]:
    """Return (http_base, ws_base) from a backend URL string."""
    backend = backend.rstrip("/")
    parsed = urllib.parse.urlparse(backend)
    if parsed.scheme in ("http", "https"):
        http_base = backend
        ws_scheme = "wss" if parsed.scheme == "https" else "ws"
        ws_base = f"{ws_scheme}://{parsed.netloc}"
    elif parsed.scheme in ("ws", "wss"):
        ws_base = backend
        http_scheme = "https" if parsed.scheme == "wss" else "http"
        http_base = f"{http_scheme}://{parsed.netloc}"
    else:
        # Treat as host-only, default to https/wss.
        http_base = f"https://{backend}"
        ws_base = f"wss://{backend}"
    return http_base, ws_base


async def _create_session(http_base: str) -> tuple[str, str]:
    """Create a voice session and return (session_id, stream_url)."""
    url = f"{http_base}/v1/voice/session"
    async with aiohttp.ClientSession() as session:
        async with session.post(url) as resp:
            resp.raise_for_status()
            data = await resp.json()
    return data["session_id"], data.get("stream_url", "")


async def _run_single(
    http_base: str,
    ws_base: str,
    session_id: str,
    opus_bytes: bytes,
    chunk_size: int = 1200,
    response_silence_ms: float = 500.0,
) -> dict[str, Any]:
    """Run one end-to-end voice session and return latency metrics.

    Key measurement is "flush-to-first" latency: time from sending the flush
    control message (utterance complete) to the first byte of the synthesized
    response arriving. This is the closest proxy to the v0.1 round-trip latency
    target of <800 ms.
    """
    ws_url = f"{ws_base}/v1/voice/stream/{session_id}"
    LOGGER.info("Connecting to %s", ws_url)

    connect_started_at = time.perf_counter()
    connected_at: float | None = None
    flushed_at: float | None = None
    first_response_at: float | None = None
    last_response_at: float | None = None
    response_bytes = 0

    try:
        async with websockets.connect(ws_url, open_timeout=10, close_timeout=10) as ws:
            connected_at = time.perf_counter()

            # Stream opus bytes in chunks.
            for i in range(0, len(opus_bytes), chunk_size):
                await ws.send(opus_bytes[i : i + chunk_size])

            # Flush to force processing (utterance complete).
            await ws.send("flush")
            flushed_at = time.perf_counter()

            # Collect response frames until silence threshold or max wait.
            silence_timeout = response_silence_ms / 1000.0
            max_wait = 30.0
            deadline = time.perf_counter() + max_wait
            while time.perf_counter() < deadline:
                remaining = deadline - time.perf_counter()
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=min(silence_timeout, remaining))
                except asyncio.TimeoutError:
                    # No new bytes for the silence threshold; response is done.
                    break

                if isinstance(msg, bytes):
                    response_bytes += len(msg)
                    now = time.perf_counter()
                    if first_response_at is None:
                        first_response_at = now
                    last_response_at = now
                elif isinstance(msg, str):
                    try:
                        payload = json.loads(msg)
                        if "error" in payload:
                            raise RuntimeError(f"Stream error: {payload['error']}")
                    except json.JSONDecodeError:
                        pass

    except websockets.exceptions.InvalidStatusCode as exc:
        raise RuntimeError(f"WebSocket connection rejected: {exc.status_code}") from exc

    if first_response_at is None or flushed_at is None:
        raise RuntimeError("No audio response received")

    flush_to_first_ms = (first_response_at - flushed_at) * 1000
    flush_to_last_ms = (last_response_at - flushed_at) * 1000 if last_response_at else None
    connect_ms = (connected_at - connect_started_at) * 1000 if connected_at else None

    return {
        "session_id": session_id,
        "sent_bytes": len(opus_bytes),
        "response_bytes": response_bytes,
        "connect_ms": round(connect_ms, 2) if connect_ms else None,
        "flush_to_first_ms": round(flush_to_first_ms, 2),
        "flush_to_last_ms": round(flush_to_last_ms, 2) if flush_to_last_ms else None,
        "success": True,
    }


async def _delete_session(http_base: str, session_id: str) -> None:
    url = f"{http_base}/v1/voice/session/{session_id}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.delete(url) as resp:
                await resp.read()
    except Exception as exc:
        LOGGER.warning("Failed to delete session %s: %s", session_id, exc)


async def run_gate(args: argparse.Namespace) -> dict[str, Any]:
    http_base, ws_base = _build_urls(args.backend)
    wav_path = Path(args.wav)
    if not wav_path.exists():
        raise FileNotFoundError(f"Mock tone not found: {wav_path}")

    opus_bytes = _wav_to_opus(wav_path)
    LOGGER.info("Encoded mock tone: %d bytes -> %d opus bytes", wav_path.stat().st_size, len(opus_bytes))

    # Optional warm-up run to avoid counting cold-start latency.
    warm_up_results: list[dict[str, Any]] = []
    if args.warmup:
        LOGGER.info("Running %d warm-up session(s)...", args.warmup)
        for i in range(args.warmup):
            session_id, _ = await _create_session(http_base)
            try:
                metric = await _run_single(http_base, ws_base, session_id, opus_bytes, args.chunk_size)
                warm_up_results.append(metric)
                LOGGER.info(
                    "Warm-up %d/%d: flush-to-first %.1f ms",
                    i + 1,
                    args.warmup,
                    metric["flush_to_first_ms"],
                )
            finally:
                await _delete_session(http_base, session_id)

    results: list[dict[str, Any]] = []
    for i in range(args.runs):
        session_id, stream_url = await _create_session(http_base)
        LOGGER.info("Run %d/%d: session %s", i + 1, args.runs, session_id)
        try:
            metric = await _run_single(http_base, ws_base, session_id, opus_bytes, args.chunk_size)
            results.append(metric)
            LOGGER.info(
                "Run %d/%d: flush-to-first %.1f ms, flush-to-last %.1f ms",
                i + 1,
                args.runs,
                metric["flush_to_first_ms"],
                metric["flush_to_last_ms"] or 0.0,
            )
        finally:
            await _delete_session(http_base, session_id)

    flush_to_first = [r["flush_to_first_ms"] for r in results]

    summary = {
        "backend": http_base,
        "ws_base": ws_base,
        "warmup_runs": len(warm_up_results),
        "measured_runs": len(results),
        "mock_mode": args.mock,
        "target_ms": TARGET_MS,
        "acceptable_ms": ACCEPTABLE_MS,
        "flush_to_first_ms": {
            "min": round(min(flush_to_first), 2),
            "max": round(max(flush_to_first), 2),
            "mean": round(statistics.mean(flush_to_first), 2),
            "median": round(statistics.median(flush_to_first), 2),
            "stdev": round(statistics.stdev(flush_to_first), 2) if len(flush_to_first) > 1 else 0.0,
        },
        "gate_passed": all(r["flush_to_first_ms"] < ACCEPTABLE_MS for r in results),
        "target_passed": all(r["flush_to_first_ms"] < TARGET_MS for r in results),
        "raw_results": results,
        "warm_up_results": warm_up_results,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "note": "Measurements use the mock-tone pipeline (no OpenAI STT/LLM/TTS billing). Live latency will be higher.",
    }
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LOAM Voice v0.1 ship gate harness")
    parser.add_argument(
        "--backend",
        default=os.getenv("VOICE_BACKEND", "https://dss-system-backend.fly.dev"),
        help="Backend base URL (http/https or ws/wss).",
    )
    parser.add_argument(
        "--gateway",
        default=os.getenv("VOICE_GATEWAY", ""),
        help="Signal/Media Gateway base URL (optional, not yet implemented).",
    )
    parser.add_argument("--runs", type=int, default=5, help="Number of measured end-to-end runs.")
    parser.add_argument(
        "--warmup",
        type=int,
        default=1,
        help="Number of warm-up runs before measurement (excluded from gate stats).",
    )
    parser.add_argument(
        "--wav",
        default=str(DEFAULT_MOCK_WAV),
        help="Path to input WAV file to use as the utterance.",
    )
    parser.add_argument("--chunk-size", type=int, default=1200, help="Opus chunk size in bytes.")
    parser.add_argument(
        "--mock",
        action="store_true",
        default=True,
        help="Use the backend mock tone path (default).",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Path to write the canonical results artifact.",
    )
    args = parser.parse_args(argv)

    if args.gateway:
        LOGGER.warning("Gateway-mode testing is not yet automated; use the manual runbook.")

    summary = asyncio.run(run_gate(args))

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    LOGGER.info("Wrote results to %s", output_path)

    print("\n=== LOAM Voice v0.1 Ship Gate ===")
    print(f"Backend: {summary['backend']}")
    print(f"Mock mode: {summary['mock_mode']}")
    print(f"Warm-up runs: {summary['warmup_runs']}")
    print(f"Measured runs: {summary['measured_runs']}")
    print(f"Flush-to-first latency (ms): mean={summary['flush_to_first_ms']['mean']}, median={summary['flush_to_first_ms']['median']}, min={summary['flush_to_first_ms']['min']}, max={summary['flush_to_first_ms']['max']}")
    print(f"Acceptable gate (<{ACCEPTABLE_MS} ms): {'PASS' if summary['gate_passed'] else 'FAIL'}")
    print(f"Target gate (<{TARGET_MS} ms): {'PASS' if summary['target_passed'] else 'FAIL'}")
    print(f"Note: {summary['note']}")

    return 0 if summary["gate_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
