# Audio Chat Surface v0.2

Browser-based, single-channel voice interface to LOAM.

## Modes

The surface supports two runtime modes selected via the `?mode=` query param.

### WebRTC mode (default)

`?gateway=https://your-gateway.example.com`

- Vanilla JS, no framework.
- One main button: tap to connect, tap again to disconnect.
- WebRTC audio capture + Opus 48 kHz / 20 ms.
- CSS-only waveform and audio tones for state feedback.
- Auto-reset error feedback; no modals, no retry button.

Gateway contract expected:
- `POST {gatewayUrl}/session` with JSON body `{ offer: <SDP> }`
  - Returns `{ session_id: string, answer: <SDP> }`
- `DELETE {gatewayUrl}/session/{session_id}`

### Web Speech mode

`?mode=web-speech&chat=https://your-backend.example.com`

- Uses the browser's built-in `SpeechRecognition` and `speechSynthesis`.
- Sends transcribed text to the backend LLM endpoint.
- Backend only needs `POST {chatUrl}/v1/voice/chat`.
- Useful when OpenAI STT/TTS credits are unavailable or when you want to skip the WebRTC gateway.

## Run locally

```bash
cd apps/audio-surface
python3 -m http.server 8080
```

WebRTC:
```
http://localhost:8080?gateway=https://your-gateway.example.com
```

Web Speech:
```
http://localhost:8080?mode=web-speech&chat=https://your-backend.example.com
```

## Build-time config

`build.js` reads these environment variables and writes them to `config.js`:

- `AUDIO_GATEWAY_URL` — default gateway URL for WebRTC mode.
- `AUDIO_CHAT_API_URL` — default backend URL for Web Speech mode.

```bash
AUDIO_GATEWAY_URL=https://voice-gateway.dualsubstrate.com \
AUDIO_CHAT_API_URL=https://audio.dualsubstrate.com \
node build.js
```

## Notes

- Part of the `dss-system` monorepo (root directory `apps/audio-surface`).
- Deployed as a static app (Vercel or equivalent).
- `config.js` is generated at build time and is gitignored.
