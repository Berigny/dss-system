# Voice Gateway v0.1

Stateless Signal/Media Gateway for LOAM Voice.

## Scope

- WebRTC negotiation for browser Audio Chat surface.
- Transparent proxy between browser WebRTC and LOAM core WebSocket voice API.
- Session heartbeat.
- No audio decode/encode in gateway — forwards opus payloads and wraps return opus into RTP.

## Browser contract

- `POST {gatewayUrl}/session` with JSON body `{ offer: <SDP> }`
  - Returns `{ session_id: string, answer: <SDP> }`
- `DELETE {gatewayUrl}/session/{session_id}`

## LOAM core contract

- `POST {LOAM_VOICE_API}/v1/voice/session` — allocate a voice session
  - Returns `{ session_id: string }`
- `WS {LOAM_VOICE_API}/v1/voice/stream/{session_id}` — bidirectional opus frames
  - Gateway -> core: opus payloads extracted from browser RTP
  - Core -> gateway: opus payloads to wrap into RTP for the browser
- `DELETE {LOAM_VOICE_API}/v1/voice/session/{session_id}`

## Install

```bash
cd apps/voice-gateway
npm install
```

## Run

```bash
# Proxy to a real LOAM core
LOAM_VOICE_API=http://localhost:8000 npm start

# Standalone mock mode (echoes audio back)
MOCK_CORE=true npm start
```

## Test with audio-surface

```bash
# Terminal 1
cd apps/voice-gateway
MOCK_CORE=true npm start

# Terminal 2
cd apps/audio-surface
python3 -m http.server 8080
```

Open `http://localhost:8080?gateway=http://localhost:3000`, tap connect, and speak. In mock mode your audio is echoed back.

## Environment variables

- `PORT` — gateway HTTP/WebSocket port (default 3000)
- `LOAM_VOICE_API` — base URL for LOAM core voice API (default http://localhost:8000)
- `MOCK_CORE` — set to `true` to run without a real LOAM core and echo audio
