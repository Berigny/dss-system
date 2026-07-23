"""Voice API v0.1 for LOAM.

Exposes the contract used by the Signal/Media Gateway:
  POST /v1/voice/session
  WS  /v1/voice/stream/{session_id}
  DELETE /v1/voice/session/{session_id}

Pipeline per utterance:
  incoming opus frames -> ffmpeg decode to wav -> Whisper STT
  -> OpenAI chat inference -> OpenAI TTS (mp3)
  -> ffmpeg encode to opus -> outgoing opus frames
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import time
import json
import uuid
from io import BytesIO
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from openai import OpenAI

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/voice", tags=["voice"])

# In-memory session state. No persistence.
_sessions: dict[str, dict[str, Any]] = {}

VOICE_MODEL = os.getenv("VOICE_CHAT_MODEL", "gpt-4o-mini")
VOICE_SYSTEM_PROMPT = os.getenv(
    "VOICE_SYSTEM_PROMPT",
    "You are LOAM, a concise voice assistant. Keep answers short and natural.",
)
TTS_VOICE = os.getenv("VOICE_TTS_VOICE", "alloy")
VOICE_MOCK_MODE = os.getenv("VOICE_MOCK_MODE", "").strip().lower() in {"1", "true", "yes", "on"}

# Pre-baked mock tone used when OpenAI credit is unavailable or VOICE_MOCK_MODE is on.
_MOCK_WAV_PATH = os.path.join(os.path.dirname(__file__), "..", "assets", "mock_tone.wav")


def _get_openai_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not configured")
    return OpenAI(api_key=api_key)


def _ffmpeg_installed() -> bool:
    return shutil.which("ffmpeg") is not None


def _load_mock_opus() -> bytes:
    """Return a pre-generated 1 kHz tone as opus frames.

    Used as a fallback when OpenAI billing/API is unavailable. The tone is
    generated from backend/assets/mock_tone.wav at runtime via ffmpeg.
    """
    wav_path = os.path.abspath(_MOCK_WAV_PATH)
    if not os.path.exists(wav_path):
        raise RuntimeError(f"Mock tone not found: {wav_path}")

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "wav",
        "-i", wav_path,
        "-c:a", "libopus",
        "-b:a", "24k",
        "-ar", "48000",
        "-ac", "1",
        "-f", "opus",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg mock encode failed: {proc.stderr.decode()[:200]}")
    return proc.stdout


def _decode_opus_to_wav(opus_chunks: list[bytes]) -> bytes:
    """Decode a list of opus frames to mono 16-bit PCM WAV using ffmpeg."""
    if not _ffmpeg_installed():
        raise RuntimeError("ffmpeg not installed")

    data = b"".join(opus_chunks)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "opus",
        "-ar", "48000",
        "-ac", "1",
        "-i", "pipe:0",
        "-ar", "16000",
        "-ac", "1",
        "-f", "wav",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, input=data, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg opus decode failed: {proc.stderr.decode()[:200]}")
    return proc.stdout


def _encode_wav_to_opus(wav_bytes: bytes) -> bytes:
    """Encode WAV to Opus frames using ffmpeg."""
    if not _ffmpeg_installed():
        raise RuntimeError("ffmpeg not installed")

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "wav",
        "-i", "pipe:0",
        "-c:a", "libopus",
        "-b:a", "24k",
        "-ar", "48000",
        "-ac", "1",
        "-f", "opus",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, input=wav_bytes, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg opus encode failed: {proc.stderr.decode()[:200]}")
    return proc.stdout


def _transcribe(wav_bytes: bytes) -> str:
    client = _get_openai_client()
    audio = BytesIO(wav_bytes)
    audio.name = "utterance.wav"
    transcript = client.audio.transcriptions.create(model="whisper-1", file=audio)
    return (transcript.text or "").strip()


def _infer(text: str) -> str:
    client = _get_openai_client()
    response = client.chat.completions.create(
        model=VOICE_MODEL,
        messages=[
            {"role": "system", "content": VOICE_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        max_tokens=150,
    )
    return (response.choices[0].message.content or "").strip()


def _tts(text: str) -> bytes:
    client = _get_openai_client()
    response = client.audio.speech.create(
        model="tts-1",
        voice=TTS_VOICE,
        input=text,
    )
    return response.read()


def _mp3_to_opus(mp3_bytes: bytes) -> bytes:
    if not _ffmpeg_installed():
        raise RuntimeError("ffmpeg not installed")

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "error",
        "-f", "mp3",
        "-i", "pipe:0",
        "-c:a", "libopus",
        "-b:a", "24k",
        "-ar", "48000",
        "-ac", "1",
        "-f", "opus",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, input=mp3_bytes, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg mp3->opus failed: {proc.stderr.decode()[:200]}")
    return proc.stdout


@router.post("/session")
async def create_voice_session() -> dict[str, str]:
    session_id = str(uuid.uuid4())
    _sessions[session_id] = {
        "created_at": time.time(),
        "chunks": [],
        "lock": asyncio.Lock(),
    }
    LOGGER.info("Voice session created: %s", session_id)
    return {"session_id": session_id}


@router.delete("/session/{session_id}")
async def delete_voice_session(session_id: str) -> dict[str, bool]:
    if session_id in _sessions:
        del _sessions[session_id]
        LOGGER.info("Voice session deleted: %s", session_id)
    return {"deleted": True}


@router.websocket("/stream/{session_id}")
async def voice_stream(websocket: WebSocket, session_id: str) -> None:
    if session_id not in _sessions:
        await websocket.close(code=4004, reason="Unknown session")
        return

    await websocket.accept()
    session = _sessions[session_id]
    LOGGER.info("Voice stream connected: %s", session_id)

    try:
        while True:
            try:
                message = await websocket.receive()
            except WebSocketDisconnect:
                break

            if message.get("type") == "websocket.disconnect":
                break

            if "bytes" in message:
                async with session["lock"]:
                    session["chunks"].append(message["bytes"])

                    # Process when we have ~3 seconds of audio at 20ms frames (150 frames).
                    if len(session["chunks"]) >= 150:
                        await _process_utterance(websocket, session)
            elif "text" in message:
                text = message["text"]
                if text == "ping":
                    await websocket.send_text("pong")
                elif text == "flush":
                    async with session["lock"]:
                        await _process_utterance(websocket, session)

    except WebSocketDisconnect:
        pass
    finally:
        LOGGER.info("Voice stream disconnected: %s", session_id)


async def _process_utterance(websocket: WebSocket, session: dict[str, Any]) -> None:
    if not session["chunks"]:
        return

    chunks = session["chunks"]
    session["chunks"] = []

    if VOICE_MOCK_MODE:
        LOGGER.info("Voice mock mode: returning tone response")
        try:
            opus = await asyncio.get_running_loop().run_in_executor(None, _load_mock_opus)
            await _send_opus_chunks(websocket, opus)
        except Exception as exc:
            LOGGER.exception("Mock voice response failed")
            await websocket.send_text(json.dumps({"error": str(exc)}))
        return

    try:
        wav = await asyncio.get_running_loop().run_in_executor(
            None, _decode_opus_to_wav, chunks
        )
        transcript = await asyncio.get_running_loop().run_in_executor(
            None, _transcribe, wav
        )
        if not transcript:
            return

        reply = await asyncio.get_running_loop().run_in_executor(
            None, _infer, transcript
        )
        if not reply:
            return

        mp3 = await asyncio.get_running_loop().run_in_executor(None, _tts, reply)
        opus = await asyncio.get_running_loop().run_in_executor(
            None, _mp3_to_opus, mp3
        )

        await _send_opus_chunks(websocket, opus)
    except Exception as exc:
        LOGGER.exception("Voice pipeline failed for session; falling back to mock tone")
        try:
            opus = await asyncio.get_running_loop().run_in_executor(None, _load_mock_opus)
            await _send_opus_chunks(websocket, opus)
        except Exception:
            LOGGER.exception("Mock fallback also failed")
            await websocket.send_text(json.dumps({"error": str(exc)}))


async def _send_opus_chunks(websocket: WebSocket, opus: bytes) -> None:
    chunk_size = 1200
    for i in range(0, len(opus), chunk_size):
        await websocket.send_bytes(opus[i : i + chunk_size])
