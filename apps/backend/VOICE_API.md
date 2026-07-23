# LOAM Voice API Contract v0.1

Base path: `/v1/voice`

## Session lifecycle

```
POST /v1/voice/session        # create session
WS   /v1/voice/stream/{id}    # bidirectional opus stream
DELETE /v1/voice/session/{id} # hard terminate
POST /v1/voice/chat           # text-only inference (browser Web Speech mode)
```

## Endpoints

### `POST /v1/voice/session`

Create a new voice session.

**Response:**
```json
{
  "session_id": "<uuid>",
  "stream_url": "wss://<host>/v1/voice/stream/<uuid>"
}
```

The `stream_url` is a hint the Signal/Media Gateway can use to open the
WebSocket stream. The gateway may construct the same URL from its configured
`LOAM_VOICE_API` base.

### `DELETE /v1/voice/session/{session_id}`

Hard-terminate the session. This:

1. Closes any active WebSocket stream with code `4000` and reason `Session deleted`.
2. Removes the session state from memory.
3. Returns whether the session existed before deletion.

**Response:**
```json
{
  "deleted": true,
  "session_existed": true
}
```

## WebSocket stream

### Path

`WS /v1/voice/stream/{session_id}`

### Connection

- The session must be created via `POST /v1/voice/session` first.
- Unknown session ids are rejected with close code `4004` (`Unknown session`).

### Incoming messages

The server accepts two message types:

1. **Binary opus frames** (preferred)
   - 48 kHz, mono, 20 ms frames.
   - Frames are buffered until ~3 seconds of audio are collected (150 frames),
     then the STT → inference → TTS pipeline runs.

2. **Text control messages**
   - `ping` → server replies `pong`.
   - `heartbeat` → server replies `heartbeat`. Useful for gateway keep-alive.
   - `flush` → forces the buffered opus frames through the pipeline immediately.

### Outgoing messages

- **Binary opus frames** containing the synthesized LOAM response.
- **Text JSON errors** when the pipeline fails and the mock fallback also fails:
  ```json
  {"error": "<message>"}
  ```

## Pipeline

For each utterance:

```
opus frames -> ffmpeg decode -> WAV (16 kHz, mono)
    -> OpenAI Whisper STT
    -> OpenAI chat completion (VOICE_CHAT_MODEL)
    -> OpenAI TTS (mp3)
    -> ffmpeg encode -> opus frames (48 kHz, mono)
```

## Error codes

| Close code | Meaning |
|------------|---------|
| `4004`     | Unknown session id. |
| `4000`     | Session deleted via `DELETE`. |

## Environment variables

See `ENV_VARS.md` for `OPENAI_API_KEY`, `VOICE_CHAT_MODEL`, `VOICE_TTS_VOICE`,
`VOICE_SYSTEM_PROMPT`, and `VOICE_MOCK_MODE`.

### `POST /v1/voice/chat`

Text-only LOAM inference endpoint used by the browser's Web Speech API mode.

**Request:**
```json
{
  "text": "Hello LOAM"
}
```

**Response:**
```json
{
  "text": "Hello. How can I help you?"
}
```

This endpoint shares the same `VOICE_CHAT_MODEL` and `VOICE_SYSTEM_PROMPT` as
the full audio pipeline. When `VOICE_MOCK_MODE=true` it returns a fixed mock
message instead of calling OpenAI.

## Mock mode

When `VOICE_MOCK_MODE=true` (or no OpenAI credit is available and the real
pipeline fails), the server returns a pre-baked 1 kHz test tone (audio stream)
or a fixed mock message (`/chat`) instead of a synthesized response.
